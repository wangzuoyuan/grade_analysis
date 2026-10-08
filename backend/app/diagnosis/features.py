"""P1-B1 诊断特征层（六类指标）服务实现（契约 docs/diagnosis-roadmap/p1-contracts.md §2）。

服务签名（冻结，B2/B3/B4 依赖）::

    student_features(db, scope, person_id: int, academic_year_id: int) -> dict
    class_features(db, scope, academic_year_id: int) -> dict   # 每生 features 列表 + 班级级汇总

``scope`` 是已解析的 WorkspaceContext（app.core.context，与 chat_tools 的
resolve_scope_snapshot 同源机制）；``academic_year_id`` 必须与 scope 解析出的
学年一致（作用域已钉死学年，跨年取数按 422 拒绝）。

口径红线（契约 §0，违反即返工）：
- 成绩取数一律走 ``_queries.readable_facts`` 统一可读事实口径（v2.1/F09）：
  班主任域=本班全科+总分（含经五条件门的教学投影行）；教学域=仅任教学科、
  绝无总分行。缺总体指标输出 ``status: "not_computable"`` + ``missing_reason``，
  绝不跨域取数（教学域绝不借读行政班总分）。
- 缺失纪律：缺考不转 0、不进分母、不残留上次值、不伪造连续。名次解析
  ``definitions.resolve_year_rank``、段位 ``definitions.band_flags``、偏科
  ``definitions.subject_weakness_subjects``、百分位归一化
  ``definitions.normalized_percentile`` 全部复用共享定义，本模块不重新实现。
- 作业行为复用 app/api/homework.py 的事件读取与按天连缺算法
  （整班 ``_person_events_for_members``、单生 ``_person_events`` 与共用
  ``_streak_lines_of`` 等），班主任按学科分线、教学按班单线；无有效批次
  时 trend="无数据"，只给计数特征。
- 教师关注读 ws_student_note（N01 域隔离：只读本域；_human_notes_filter
  排除作业考勤/预警解除/迁移等系统辅助行），空数据如实输出 null。
- 时间窗口（7/30 天）按自然日回溯、窗口边界含当日（§0.7），锚点为
  scope.as_of。

输出形状：§2 JSONC 逐字段实现；每个指标对象额外携带
``status``（"ok" | "not_computable"）与 ``missing_reason``（§0.2 的
not_computable 机制的落地字段），missing_reason 取值：
  no_exam_data / no_main3_row / rank_missing / percentile_missing /
  no_main3_percentile（服务层学年错配另有 422，不进 JSON）。
"""

from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.analysis import definitions as defs
from app.analysis.config import get_band_config
from app.api import _queries as q
from app.api.homework import (
    DEFAULT_TOTAL_TYPE,
    _effective_status,
    _evaluation_tone,
    _forgot_of,
    _is_pure_missing,
    _line_key_of,
    _parse_expected_ids,
    _person_events,
    _person_events_for_members,
    _streak_lines_of,
)
from app.core.context import WorkspaceContext
from app.core.errors import InvalidScopeParam, ResourceOutOfScope
from app.db.workspace_models import ScoreFact, WsStudentNote
from app.diagnosis import thresholds as th

# 教师关注「接触类」档案分类（notes/router.py CATEGORIES 的接触子集；
# 观察/奖惩/其他不构成 last_contact.kind）。
CONTACT_CATEGORIES = ("谈话", "家访", "家长沟通")

# main3 缺失原因（missing_reason 词表，契约 §2 实现要点）
NO_EXAM_DATA = "no_exam_data"
NO_MAIN3_ROW = "no_main3_row"
RANK_MISSING = "rank_missing"
PERCENTILE_MISSING = "percentile_missing"
NO_MAIN3_PERCENTILE = "no_main3_percentile"
MAIN3_ABSENT = "main3_absent"  # 有总分行但 score=NULL（登记缺考；行内残留指标绝不取用）




class _ScopeShim:
    """resolve_scope_snapshot 快照 dict 的属性访问适配。

    与 changes.py 的双载体先例一致：服务函数同时接受 WorkspaceContext
    与 chat 工具链的快照 dict（键同源），内部一律按属性访问。"""

    def __init__(self, snapshot: dict):
        self._data = snapshot

    def __getattr__(self, name: str):
        try:
            value = self._data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc
        if name == "as_of" and isinstance(value, str):
            from datetime import date as _date
            return _date.fromisoformat(value)
        return value


# ────────────────────────────── 服务入口（签名冻结） ──────────────────────────────


def student_features(db: Session, scope, person_id: int, academic_year_id: int) -> dict:
    if isinstance(scope, dict):
        scope = _ScopeShim(scope)
    """单生六类指标（契约 §2 返回 JSON 的唯一实现，B2/B3/B4 同源）。"""
    _check_year(scope, academic_year_id)
    if person_id not in scope.member_person_ids:
        raise ResourceOutOfScope(
            "person not in current scope", details={"person_id": person_id}
        )
    return _compute_student(db, scope, person_id, get_band_config(db))


def _parse_anchor_date(anchor_date_str):
    """锚点日期字符串 → date；月精度/缺失 → None（该锚点整组回退当前时点）。"""
    if isinstance(anchor_date_str, str):
        try:
            return date.fromisoformat(anchor_date_str)
        except ValueError:
            return None
    return None


def class_features(db: Session, scope, academic_year_id: int, anchor_exam: Optional[str] = None) -> dict:
    """班级特征（单锚点或当前时点）；多锚点批量请用 class_features_at_anchors。"""
    payloads = class_features_at_anchors(
        db, scope, academic_year_id, [anchor_exam] if anchor_exam else []
    )
    return payloads[anchor_exam]


def class_features_at_anchors(
    db: Session, scope, academic_year_id: int, anchor_exams: Optional[List[str]] = None
) -> Dict[Optional[str], dict]:
    """班级特征 · 多锚点一次取数装配（P3 队列清单性能路径）。

    返回 {锚点考试名（或 None=当前时点）: class_features 载荷}。锚点语义
    （契约 p3 §2.1）：考试时间线截断至该场（含），作业/教师关注窗口锚定
    该场考试日期；日期月精度/缺失 → **整组回退当前时点**（成绩不截断、
    行为窗口用 scope.as_of，绝不混合两种时点），timepoint_basis 如实标注
    current_time_point。每生基础数据只取一次，锚点间只做截断与窗口过滤。
    成员为当前名册。
    """
    if isinstance(scope, dict):
        scope = _ScopeShim(scope)
    _check_year(scope, academic_year_id)
    band_config = get_band_config(db)
    anchors: List[Optional[str]] = list(anchor_exams or [])
    if None in anchors:
        raise InvalidScopeParam(
            "anchor_exams must be non-empty exam names",
            details={"param": "anchor_exams"},
        )
    anchor_dates: Dict[str, dict] = {}
    if anchors:
        summaries = q.readable_exam_summaries(db, scope)  # 一次解析全部锚点日期
        by_name = {e["exam_name"]: e["exam_date"] for e in summaries}
        for name in anchors:
            if name not in by_name:
                raise ResourceOutOfScope(
                    "anchor exam not found in current scope",
                    details={"exam_name": name, "data_domain": scope.data_domain},
                )
            date_str = by_name[name]  # None=无日期；"YYYY-MM"=月精度
            anchor_dates[name] = {
                "date_str": date_str,
                "date": _parse_anchor_date(date_str),
            }

    members = sorted(scope.member_person_ids)
    names = q.names_for(db, members)
    buckets: Dict[Optional[str], dict] = {
        name: {"students": [], "type_inputs": []} for name in ([*anchors, None] if anchors else [None])
    }
    # 月精度/缺失日期的锚点：整组回退当前时点（成绩时间线不截断、作业/
    # 关注窗口用 scope.as_of），与 None 桶完全同参——绝不混合「历史成绩 +
    # 当前行为」两种时点。直接复用 None 桶装配，不重复计算。
    unresolved = {name for name, info in anchor_dates.items() if info["date"] is None}
    compute_buckets = [name for name in buckets if name not in unresolved]
    bases = _fetch_class_bases(db, scope, members)
    for person_id in members:
        base = bases[person_id]  # 班内及锚点间共用（一次取数）
        for anchor in compute_buckets:
            info = anchor_dates.get(anchor) if anchor is not None else None
            anchor_date = info["date"] if info else None
            anchor_key = (
                (True, info["date_str"] or "", anchor)
                if info is not None and info["date_str"] is not None
                else None
            )
            features = _compute_student(
                db,
                scope,
                person_id,
                band_config,
                anchor_exam=anchor,
                anchor_key=anchor_key,
                anchor_date=anchor_date,
                base=base,
            )
            buckets[anchor]["students"].append(
                {"person_id": person_id, "name": names.get(person_id), "features": features}
            )
            ind = features["indicators"]
            buckets[anchor]["type_inputs"].append(
                {
                    "person_id": person_id,
                    "valid_exam_count": features["data_quality"]["valid_exam_count"],
                    "bands": ind["current_level"]["bands"],
                    "stability_label": ind["stability"]["label"],
                    "direction_recent": ind["trend"]["direction_recent"],
                    "streak_kind": ind["trend"]["streak"]["kind"],
                    "streak_count": ind["trend"]["streak"]["count"],
                    "imbalance_severe": ind["imbalance"]["severe"],
                    "homework_missing_30d": ind["homework_behavior"]["missing_30d"],
                    "homework_current_streak_days": ind["homework_behavior"]["current_streak_days"],
                }
            )

    for name in unresolved:
        buckets[name] = buckets[None]  # 整组当前时点（同参复用，见上方注释）

    missing_stats = _class_missing_stats(db, scope)  # 锚点无关，一次计算共用

    def _payload(anchor: Optional[str], bucket: dict) -> dict:
        anchor_payload = (
            {
                "exam_name": anchor,
                "exam_date": anchor_dates[anchor]["date_str"],
                "date_resolved": anchor_dates[anchor]["date"] is not None,
                "timepoint_basis": (
                    "exam_anchor" if anchor_dates[anchor]["date"] is not None else "current_time_point"
                ),
            }
            if anchor is not None
            else None
        )
        return {
            "academic_year_id": academic_year_id,
            "calc_version": th.CALC_VERSION,
            "scope_mode": scope.mode,
            "anchor": anchor_payload,
            "student_count": len(members),
            "students": bucket["students"],
            "class_summary": {
                "type_inputs": bucket["type_inputs"],
                "missing_stats": missing_stats,
            },
        }

    return {anchor: _payload(anchor, bucket) for anchor, bucket in buckets.items()}


def _check_year(scope: WorkspaceContext, academic_year_id: int) -> None:
    if academic_year_id != scope.academic_year_id:
        raise InvalidScopeParam(
            "academic_year_id does not match the resolved scope",
            details={
                "academic_year_id": academic_year_id,
                "scope_academic_year_id": scope.academic_year_id,
            },
        )


# ────────────────────────────── 单生计算 ──────────────────────────────


def _exam_sort_key(exams: Dict[str, dict], name: str):
    """与 _ordered_exam_names 完全一致的排序键（锚点截断按同一比较口径）。"""
    display = exams[name]["display_date"]
    return (display is not None, display or "", name)


def _truncate_at_anchor(
    exams: Dict[str, dict], ordered: List[str], anchor_exam: str, anchor_key
) -> List[str]:
    """时间线截断至锚点考试（含该场）：该生有锚点行 → 按位置截断（最稳）；
    没有锚点行（缺考/未导入）→ 按锚点排序键比较截断（只保留不晚于锚点的
    场次；月精度/无日期无法证明先后 → 保守保留，basis 由调用方标注）。"""
    if anchor_exam in exams:
        return ordered[: ordered.index(anchor_exam) + 1]
    if anchor_key is None:
        return ordered
    return [name for name in ordered if _exam_sort_key(exams, name) <= anchor_key]


def _fetch_student_base(db: Session, scope: WorkspaceContext, person_id: int):
    """单生基础数据（成绩分组 + 作业事件轴 + 档案行）。

    锚点间完全共用、只在截断/窗口过滤上分叉——多锚点重算（P3 队列清单）
    一次取数多次装配，避免「每生 × 每场」重复查询。"""
    entries = q.readable_facts(db, scope, member_ids=[person_id])
    exams = _group_exams(entries)
    ordered = _ordered_exam_names(exams)
    events, line_axis_map = _person_events(
        db, scope, person_id, academic_year_id=scope.academic_year_id, all_history=False
    )
    from app.api.students_mgmt import _human_notes_filter

    note_rows = (
        db.query(WsStudentNote)
        .filter(
            WsStudentNote.data_domain == scope.data_domain,
            WsStudentNote.person_id == person_id,
            _human_notes_filter(),
        )
        .all()
    )
    return exams, ordered, events, line_axis_map, note_rows


def _fetch_class_bases(db: Session, scope: WorkspaceContext, members: List[int]):
    """整班基础事实一次读取，按人装配成 _compute_student 的原有入参。"""
    if not members:
        return {}
    facts_by_person = defaultdict(list)
    for entry in q.readable_facts(db, scope, member_ids=members):
        facts_by_person[entry.person_id].append(entry)
    events_by_person = _person_events_for_members(db, scope, members, scope.academic_year_id)

    from app.api.students_mgmt import _human_notes_filter

    notes_by_person = defaultdict(list)
    for start in range(0, len(members), 400):
        notes = db.query(WsStudentNote).filter(
            WsStudentNote.data_domain == scope.data_domain,
            WsStudentNote.person_id.in_(members[start:start + 400]),
            _human_notes_filter(),
        ).all()
        for note in notes:
            notes_by_person[note.person_id].append(note)

    bases = {}
    for pid in members:
        exams = _group_exams(facts_by_person[pid])
        events, line_axis = events_by_person[pid]
        bases[pid] = (exams, _ordered_exam_names(exams), events, line_axis, notes_by_person[pid])
    return bases


def _compute_student(
    db: Session,
    scope: WorkspaceContext,
    person_id: int,
    band_config: dict,
    anchor_exam: Optional[str] = None,
    anchor_key=None,
    anchor_date: Optional[date] = None,
    base=None,
) -> dict:
    as_of = anchor_date if anchor_date is not None else scope.as_of
    if base is None:
        base = _fetch_student_base(db, scope, person_id)
    exams, full_ordered, events, line_axis_map, note_rows = base
    ordered = full_ordered
    if anchor_exam is not None:
        ordered = _truncate_at_anchor(exams, full_ordered, anchor_exam, anchor_key)

    current_level, notes = _current_level_indicator(exams, ordered, band_config)
    trend = _trend_indicator(exams, ordered)
    stability = _stability_indicator(exams, ordered)
    imbalance = _imbalance_indicator(exams, ordered)
    homework = _homework_from(scope.mode, events, line_axis_map, as_of)
    attention = _attention_from(note_rows, as_of)
    notes.extend(_data_quality_notes(exams, ordered))

    valid_exam_count = len(ordered)
    return {
        "person_id": person_id,
        "academic_year_id": scope.academic_year_id,
        "calc_version": th.CALC_VERSION,
        "indicators": {
            "current_level": current_level,
            "trend": trend,
            "stability": stability,
            "imbalance": imbalance,
            "homework_behavior": homework,
            "teacher_attention": attention,
        },
        "data_quality": {"valid_exam_count": valid_exam_count, "notes": notes},
    }


def _group_exams(entries) -> Dict[str, dict]:
    """可读事实行 → {exam_name: {"display_date", "subjects", "totals"}}。
    每生每场每口径至多一行（readable_facts 已按本域优先消解冲突）。"""
    exams: Dict[str, dict] = {}
    for entry in entries:
        fact = entry.fact
        bucket = exams.setdefault(
            fact.exam_name,
            {"display_date": None, "subjects": {}, "totals": {}},
        )
        shown = q.display_exam_date(fact)
        if shown is not None and (
            bucket["display_date"] is None or shown > bucket["display_date"]
        ):
            bucket["display_date"] = shown
        if fact.total_type is None:
            bucket["subjects"].setdefault(fact.subject, fact)
        else:
            bucket["totals"].setdefault(fact.total_type, fact)
    return exams


def _ordered_exam_names(exams: Dict[str, dict]) -> List[str]:
    """学年内考试时间线：有日期在前按日期升序，无确切日期视为更早
    （与共享 latest_fact 的「无日期不冒充最新」语义一致），同名兜底按名称。"""
    return sorted(
        exams,
        key=lambda name: (
            exams[name]["display_date"] is not None,
            exams[name]["display_date"] or "",
            name,
        ),
    )


def _not_computable(reason: str) -> Tuple[str, str]:
    return "not_computable", reason


# ── 指标 1：当前水平 ──


def _current_level_indicator(exams: Dict[str, dict], ordered: List[str], band_config: dict):
    notes: List[str] = []
    if not ordered:
        return (
            {
                "status": "not_computable",
                "missing_reason": NO_EXAM_DATA,
                "exam_name": None,
                "as_of": None,
                "main3": {
                    "rank": None,
                    "percentile": None,
                    "basis": None,
                    "missing_reason": NO_EXAM_DATA,
                },
                "subjects": [],
                "bands": defs.band_flags(None, band_config),
            },
            notes,
        )
    latest = ordered[-1]
    bucket = exams[latest]
    main3_row = bucket["totals"].get(DEFAULT_TOTAL_TYPE)
    absent = main3_row is not None and main3_row.score is None
    # 缺考纪律：score=NULL 的总分行，行内残留名次/百分位绝不取用
    rank = (
        defs.resolve_year_rank(main3_row.xueji_rank, main3_row.grade_rank)
        if main3_row is not None and not absent
        else None
    )
    percentile = (
        defs.normalized_percentile(main3_row.grade_percentile)
        if main3_row is not None and not absent
        else None
    )
    if main3_row is None:
        missing_reason = NO_MAIN3_ROW
    elif absent:
        missing_reason = MAIN3_ABSENT
        notes.append("最近一场主三门缺考（score 为空），名次/百分位不取残留值")
    elif rank is None:
        missing_reason = RANK_MISSING
        notes.append("最近一场主三门名次不可得（缺考或未导入），未落段")
    elif percentile is None:
        missing_reason = PERCENTILE_MISSING
    else:
        missing_reason = None
    subjects = []
    for subject, fact in sorted(bucket["subjects"].items()):
        if not subject:
            continue
        if fact.score is None:
            # 缺考科目：行保留、值一律 null（行内残留百分位/等级分绝不取）
            subjects.append(
                {"subject": subject, "percentile": None, "grade_score": None}
            )
            continue
        subject_pct = defs.normalized_percentile(fact.grade_percentile)
        subjects.append(
            {
                "subject": subject,
                "percentile": round(subject_pct, 4) if subject_pct is not None else None,
                "grade_score": getattr(fact, "grade_score", None),
            }
        )
    return (
        {
            "status": "ok",
            "missing_reason": None,
            "exam_name": latest,
            "as_of": bucket["display_date"],
            "main3": {
                "rank": rank,
                "percentile": round(percentile, 4) if percentile is not None else None,
                "basis": "school" if (rank is not None or percentile is not None) else None,
                "missing_reason": missing_reason,
            },
            "subjects": subjects,
            "bands": defs.band_flags(rank, band_config),
        },
        notes,
    )


# ── 指标 2：趋势（学年内，主三门名次序列） ──


def _main3_rank_points(exams: Dict[str, dict], ordered: List[str]) -> List[Tuple[str, int]]:
    """有效名次点：缺考（score=NULL，残留名次不取）/未导入名次/无总分行
    的那一场不进序列（不转 0、不残留）。"""
    points = []
    for name in ordered:
        row = exams[name]["totals"].get(DEFAULT_TOTAL_TYPE)
        if row is None or row.score is None:
            continue
        rank = defs.resolve_year_rank(row.xueji_rank, row.grade_rank)
        if rank is not None:
            points.append((name, rank))
    return points


def _trend_indicator(exams: Dict[str, dict], ordered: List[str]) -> dict:
    has_main3_rows = any(
        exams[name]["totals"].get(DEFAULT_TOTAL_TYPE) is not None for name in ordered
    )
    if not has_main3_rows:
        return {
            "status": "not_computable",
            "missing_reason": NO_MAIN3_ROW,
            "last_change": None,
            "direction_recent": "数据不足",
            "streak": {"kind": None, "count": 0},
            "long_term": "数据不足",
            "valid_exam_count": 0,
        }
    points = _main3_rank_points(exams, ordered)
    # 名次差 = 上一次名次 − 本次名次（共享口径：正数 = 进步）——仅供下方
    # 方向/连击/长期计算；导出字段 rank_change 按路线图全局规则取反
    # （本次−上次，负值 = 名次数值变小 = 相对位置上升）。
    changes = [
        points[i - 1][1] - points[i][1] for i in range(1, len(points))
    ]
    last_change = (
        {
            "from": points[-2][0],
            "to": points[-1][0],
            "rank_change": -changes[-1] if changes else None,
        }
        if len(points) >= 2
        else None
    )
    if changes:
        window = changes[-th.TREND_DIRECTION_RECENT_N :]
        net = sum(window)
        if net >= th.TREND_DIRECTION_MIN_CHANGE:
            direction = "进步"
        elif net <= -th.TREND_DIRECTION_MIN_CHANGE:
            direction = "退步"
        else:
            direction = "持平"
    else:
        direction = "数据不足"
    streak_kind, streak_count = _streak_of(changes)
    if len(points) >= 2:
        span = points[-1][1] - points[0][1]  # 末 − 首；负 = 名次变小 = 上升
        if span <= -th.TREND_DIRECTION_MIN_CHANGE:
            long_term = "上升"
        elif span >= th.TREND_DIRECTION_MIN_CHANGE:
            long_term = "下降"
        else:
            long_term = "平稳"
    else:
        long_term = "数据不足"
    return {
        "status": "ok",
        "missing_reason": None,
        "last_change": last_change,
        "direction_recent": direction,
        "streak": {"kind": streak_kind, "count": streak_count},
        "long_term": long_term,
        "valid_exam_count": len(points),
    }


def _streak_of(changes: List[int]) -> Tuple[Optional[str], int]:
    """尾部连续同向变化的（kind, count）；单次变化 < TREND_DIRECTION_MIN_CHANGE
    不构成方向、中断连续。"""
    if not changes:
        return None, 0

    def _direction(change: int) -> Optional[str]:
        if change >= th.TREND_DIRECTION_MIN_CHANGE:
            return "进步"
        if change <= -th.TREND_DIRECTION_MIN_CHANGE:
            return "退步"
        return None

    kind = _direction(changes[-1])
    if kind is None:
        return None, 0
    count = 1
    for change in reversed(changes[:-1]):
        if _direction(change) != kind:
            break
        count += 1
    return kind, count


# ── 指标 3：稳定性（主三门百分位极差） ──


def _main3_percentile_points(exams: Dict[str, dict], ordered: List[str]) -> List[float]:
    points = []
    for name in ordered:
        row = exams[name]["totals"].get(DEFAULT_TOTAL_TYPE)
        if row is None or row.score is None:
            continue  # 缺考行残留百分位不取
        percentile = defs.normalized_percentile(row.grade_percentile)
        if percentile is not None:
            points.append(percentile)
    return points


def _stability_indicator(exams: Dict[str, dict], ordered: List[str]) -> dict:
    has_main3_rows = any(
        exams[name]["totals"].get(DEFAULT_TOTAL_TYPE) is not None for name in ordered
    )
    if not has_main3_rows:
        status, reason = _not_computable(NO_MAIN3_ROW)
        return _stability_payload(status, reason, None, "数据不足")
    points = _main3_percentile_points(exams, ordered)
    if not points:
        status, reason = _not_computable(PERCENTILE_MISSING)
        return _stability_payload(status, reason, None, "数据不足")
    window = points[-th.STABILITY_WINDOW_N :]
    if len(window) < th.STABILITY_MIN_POINTS:
        # 有效样本 < min_points：波动结论不稳定，如实输出「数据不足」
        return _stability_payload("ok", None, None, "数据不足")
    value = round(max(window) - min(window), 4)
    return _stability_payload("ok", None, value, th.stability_label(value))


def _stability_payload(status: str, reason: Optional[str], value: Optional[float], label: str) -> dict:
    return {
        "status": status,
        "missing_reason": reason,
        "window_n": th.STABILITY_WINDOW_N,
        "statistic": th.STABILITY_STATISTIC,
        "value": value,
        "label": label,
        "min_points": th.STABILITY_MIN_POINTS,
    }


# ── 指标 4：偏科（同场可比，复用 subject_weakness_subjects 口径） ──


def _imbalance_indicator(exams: Dict[str, dict], ordered: List[str]) -> dict:
    if not ordered:
        return _imbalance_payload("not_computable", NO_MAIN3_ROW, [], [])
    latest = ordered[-1]
    bucket = exams[latest]
    main3_row = bucket["totals"].get(DEFAULT_TOTAL_TYPE)
    if main3_row is None:
        return _imbalance_payload("not_computable", NO_MAIN3_ROW, [], [])
    if main3_row.score is None:
        return _imbalance_payload("not_computable", MAIN3_ABSENT, [], [])
    base = defs.normalized_percentile(main3_row.grade_percentile)
    if base is None:
        return _imbalance_payload("not_computable", NO_MAIN3_PERCENTILE, [], [])
    subjects = []
    for subject, fact in sorted(bucket["subjects"].items()):
        if not subject or fact.score is None:
            continue  # 缺考/无百分位：该科本场不可比，不判不残留
        percentile = defs.normalized_percentile(fact.grade_percentile)
        if percentile is None:
            continue  # 缺考/无百分位：该科本场不可比，不判不残留
        diff_pct_point = round((percentile - base) * 100, 2)
        consecutive = _consecutive_weak_exams(
            exams, ordered, subject, percentile, base
        )
        subjects.append(
            {
                "subject": subject,
                "diff_pct_point": diff_pct_point,
                "consecutive_exams": consecutive,
            }
        )
    severe = sorted(
        entry["subject"]
        for entry in subjects
        if entry["consecutive_exams"] >= th.IMBALANCE_MIN_CONSECUTIVE
    )
    return _imbalance_payload("ok", None, subjects, severe)


def _consecutive_weak_exams(
    exams: Dict[str, dict],
    ordered: List[str],
    subject: str,
    latest_subject_pct: float,
    latest_main_pct: float,
) -> int:
    """从最近一场向前数的连续偏科场数：偏科判定复用共享
    subject_weakness_subjects（≥ SUBJECT_WEAKNESS_PCT_DIFF）；任一侧百分位
    缺失（缺考/未导入）即断——绝不伪造连续。"""
    consecutive = 0
    subject_pct: Optional[float] = latest_subject_pct
    main_pct: Optional[float] = latest_main_pct
    idx = len(ordered) - 1
    while idx >= 0 and subject_pct is not None and main_pct is not None:
        # 偏科判定复用共享定义（≥ SUBJECT_WEAKNESS_PCT_DIFF）
        if not defs.subject_weakness_subjects([(subject, subject_pct)], main_pct):
            break
        consecutive += 1
        idx -= 1
        if idx < 0:
            break
        bucket = exams[ordered[idx]]
        earlier_main = bucket["totals"].get(DEFAULT_TOTAL_TYPE)
        earlier_subject = bucket["subjects"].get(subject)
        if (
            earlier_main is None
            or earlier_subject is None
            or earlier_main.score is None
            or earlier_subject.score is None
        ):
            break  # 任一侧缺失/缺考即断：缺考不伪造连续
        main_pct = defs.normalized_percentile(earlier_main.grade_percentile)
        subject_pct = defs.normalized_percentile(earlier_subject.grade_percentile)
    return consecutive


def _imbalance_payload(status: str, reason: Optional[str], subjects: list, severe: list) -> dict:
    return {
        "status": status,
        "missing_reason": reason,
        "subjects": subjects,
        "severe": severe,
    }


# ── 指标 5：作业行为（复用既有作业查询与按天连缺算法） ──


def _window_start(as_of: date, days: int) -> date:
    """自然日回溯 days 天、边界含当日 → 窗口起点。"""
    return as_of - timedelta(days=days - 1)


def _homework_from(mode: str, events, line_axis_map, as_of: date) -> dict:
    """作业行为指标（纯装配：吃 _fetch_student_base 的共用事件轴）。"""
    # as_of 时点纪律：晚于 as_of 的批次（预录的未来作业）不参与任何统计，
    # 考试锚点重算（P3）据此回看历史时点；考勤批次不参与缺交统计
    # （与 homework_student / homework_warnings 同口径）。
    events = [(a, s) for a, s in events if a.assigned_date <= as_of]
    line_axis_map = {
        key: [a for a in axis if a.assigned_date <= as_of]
        for key, axis in line_axis_map.items()
    }
    streak_events = [(a, s) for a, s in events if a.subject != "考勤"]
    legacy_ids = {
        a.id
        for axis in line_axis_map.values()
        for a in axis
        if not _parse_expected_ids(a.expected_members_json)
    }
    lines = _streak_lines_of(mode, streak_events, line_axis_map, legacy_ids)
    current_streak_days = max((line[0] or 0) for line in lines) if lines else 0

    start7 = _window_start(as_of, th.HOMEWORK_WINDOW_7D)
    start30 = _window_start(as_of, th.HOMEWORK_WINDOW_30D)
    prev7_start, prev7_end = _window_start(as_of, 14), as_of - timedelta(days=th.HOMEWORK_WINDOW_7D)

    win30 = [(a, s) for a, s in streak_events if start30 <= a.assigned_date <= as_of]
    win7 = [(a, s) for a, s in win30 if a.assigned_date >= start7]
    prev7 = [
        (a, s)
        for a, s in streak_events
        if prev7_start <= a.assigned_date <= prev7_end
    ]
    missing_7d = sum(1 for _a, s in win7 if _is_pure_missing(s))
    missing_30d = sum(1 for _a, s in win30 if _is_pure_missing(s))
    missing_by_subject: Dict[str, int] = defaultdict(int)
    for a, s in win30:
        if _is_pure_missing(s):
            missing_by_subject[a.subject] += 1
    forgot_30d = sum(1 for _a, s in win30 if _forgot_of(s.evaluation))
    negative_notes_30d = sum(
        1
        for _a, s in win30
        if _effective_status(s) == "submitted" and _evaluation_tone(s.evaluation) == "negative"
    )

    # trend：近 7 天 vs 前 7 天（天 8–14）纯缺交次数；本生连缺线范围内
    # 近 14 天无任何收交批次 → 无数据（不拿 0 冒充持平）。
    own_line_keys = {_line_key_of(mode, a) for a, _s in streak_events}
    batch_dates = {a.assigned_date for a, _s in streak_events}
    batch_dates |= {
        a.assigned_date
        for key, axis in line_axis_map.items()
        if key in own_line_keys
        for a in axis
    }
    if not any(prev7_start <= d <= as_of for d in batch_dates):
        trend = "无数据"
    else:
        prev_missing = sum(1 for _a, s in prev7 if _is_pure_missing(s))
        if missing_7d > prev_missing:
            trend = "恶化"
        elif missing_7d < prev_missing:
            trend = "改善"
        else:
            trend = "持平"
    return {
        "status": "ok",
        "missing_reason": None,
        "missing_7d": missing_7d,
        "missing_30d": missing_30d,
        "current_streak_days": current_streak_days,
        "trend": trend,
        "forgot_30d": forgot_30d,
        "negative_notes_30d": negative_notes_30d,
        "missing_by_subject": dict(sorted(missing_by_subject.items())),
    }


# ── 指标 6：教师关注（follow_up / note，域内） ──


def _attention_from(note_rows, as_of: date) -> dict:
    """教师关注指标（纯装配：吃 _fetch_student_base 的共用档案行）。
    as_of 时点纪律：晚于 as_of 的档案不参与（P3 锚点回看）。"""
    rows = [note for note in note_rows if note.date <= as_of]
    contacts = [note for note in rows if note.category in CONTACT_CATEGORIES]
    if contacts:
        last = max(contacts, key=lambda note: (note.date, note.id))
        last_contact = {"kind": last.category, "days_ago": (as_of - last.date).days}
    else:
        last_contact = {"kind": None, "days_ago": None}
    start30 = _window_start(as_of, th.HOMEWORK_WINDOW_30D)

    def _has_follow_up(note: WsStudentNote) -> bool:
        return bool((note.follow_up or "").strip())

    open_follow_ups = sum(
        1
        for note in rows
        if _has_follow_up(note) and (note.follow_up_done or 0) == 0
    )
    done_follow_ups_30d = sum(
        1
        for note in rows
        if _has_follow_up(note) and note.follow_up_done == 1 and start30 <= note.date <= as_of
    )
    return {
        "status": "ok",
        "missing_reason": None,
        "last_contact": last_contact,
        "open_follow_ups": open_follow_ups,
        "done_follow_ups_30d": done_follow_ups_30d,
    }


# ── data_quality 备注 ──


def _data_quality_notes(exams: Dict[str, dict], ordered: List[str]) -> List[str]:
    notes: List[str] = []
    if ordered and exams[ordered[-1]]["display_date"] is None:
        notes.append("最近一场有数据考试缺少确切日期，时间线按考试名兜底排序")
    main3_row_exams = sum(
        1 for name in ordered if exams[name]["totals"].get(DEFAULT_TOTAL_TYPE) is not None
    )
    rank_points = _main3_rank_points(exams, ordered)
    if main3_row_exams > len(rank_points):
        notes.append("部分考试主三门名次缺失（缺考或未导入），未计入趋势名次序列")
    if main3_row_exams == 0:
        notes.append("范围内无主三门总分行，总体类指标按缺失输出（不跨域取数）")
    return notes


# ────────────────────────────── 班级级汇总 ──────────────────────────────


def _class_missing_stats(db: Session, scope: WorkspaceContext) -> dict:
    """缺失统计（F08 考试维度成员口径）：
    - missing_by_subject：考试时点成员中该科无任何行的人数（未导入/未参考）；
    - absent_by_subject：该科 score 为 NULL 的登记缺考行数（缺考不转 0）；
    - missing_main3（仅班主任域）：无主三门总分行的人数。
    考试名发现 = 本域本班事实的去重考试 ∪ 成员可读事实中的考试（含投影行）。"""
    scope_exam_names = {
        row[0]
        for row in (
            db.query(ScoreFact.exam_name)
            .filter(
                ScoreFact.data_domain == scope.data_domain,
                ScoreFact.academic_year_id == scope.academic_year_id,
                ScoreFact.class_ref_id.in_(list(scope.class_ids)),
            )
            .distinct()
            .all()
        )
        if row[0]
    }
    entries_all = q.readable_facts(db, scope)
    exam_names = scope_exam_names | {e.fact.exam_name for e in entries_all}

    exams = []
    for name in sorted(exam_names):
        exam_date = (
            db.query(func.max(ScoreFact.exam_date))
            .filter(
                ScoreFact.data_domain == scope.data_domain,
                ScoreFact.academic_year_id == scope.academic_year_id,
                ScoreFact.class_ref_id.in_(list(scope.class_ids)),
                ScoreFact.exam_name == name,
            )
            .scalar()
        )
        members = (
            q.members_at(db, scope.mode, scope.class_ids, exam_date)
            if exam_date is not None
            else list(scope.member_person_ids)
        )
        entries = q.readable_facts(db, scope, exam_name=name, member_ids=members)
        subject_have: Dict[str, set] = defaultdict(set)
        subject_absent: Dict[str, int] = defaultdict(int)
        main3_have: set = set()
        for entry in entries:
            fact = entry.fact
            if fact.total_type is None:
                if fact.subject:
                    subject_have[fact.subject].add(entry.person_id)
                    if fact.score is None:
                        subject_absent[fact.subject] += 1
            elif fact.total_type == DEFAULT_TOTAL_TYPE:
                main3_have.add(entry.person_id)
        exam_stat = {
            "exam_name": name,
            "exam_date": exam_date.isoformat() if exam_date is not None else None,
            "members_at": len(members),
            "missing_by_subject": {
                subject: sum(1 for pid in members if pid not in subject_have.get(subject, ()))
                for subject in sorted(subject_have)
            },
            "absent_by_subject": dict(sorted(subject_absent.items())),
        }
        if scope.mode == "homeroom":
            exam_stat["missing_main3"] = sum(1 for pid in members if pid not in main3_have)
        exams.append(exam_stat)

    touched = {e.person_id for e in entries_all}
    return {
        "exams": exams,
        "students_without_exam_data": [pid for pid in sorted(scope.member_person_ids) if pid not in touched],
    }
