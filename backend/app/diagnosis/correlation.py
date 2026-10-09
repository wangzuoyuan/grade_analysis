"""P2-C1 作业×成绩相关性（Pearson + Spearman，契约 docs/diagnosis-roadmap/p2-contracts.md §2）。

服务函数（契约冻结签名；``homework_subject``/``homework_type`` 为保留
chat 工具既有「作业学科/种类」对外语义追加的可选 kwargs，默认 None=
不过滤，冻结调用形状行为不变——偏差已登记交付说明）::

    def exam_homework_correlation(db, scope, exam_name, window_days=14,
                                  metric="total:主三门",
                                  homework_subject=None, homework_type=None) -> dict

HTTP 端点见 correlation_router（GET /api/v1/{homeroom|teaching}/diagnosis/correlation）。

统计实现声明（契约 §2.2 二选一）：``backend/requirements-lock.txt`` 无
scipy（本波已核对），本模块用**纯 Python 自实现** Pearson r 与 Spearman
rho，不引入任何第三方统计依赖；Spearman 对并列值取平均秩（并列秩处理），
对非线性单调关系比 Pearson 更稳健。

口径要点（§0 通用规则 + §2）：
- 样本构造：该场考试有有效成绩（metric 对应分数非 NULL，缺考不转 0）×
  其在 ``[exam_date − window_days, exam_date)`` 窗口内有有效批次的作业
  提交率（缺交数/应交数）。窗口**不含考试当日与考后作业**（P1 §0.7）。
- 例外登记口径：应交快照内无逐人行 = 已交（既有口径）；显式缺交计缺交；
  请假（excused）/忘带/出勤异常行**不计入分子也不计入分母**；分母未知
  （批次无应交快照）的批次整批剔除并计数；窗口内无可计批次（或例外剔除
  后分母为 0）→ 该生不入样本，绝不默认已交、绝不编造提交率。
- X 取数完全复用 app/api/homework.py 既有查询与状态语义
  （``_visible_assignments``/``_projected_submissions``/``_projected_expected``
  /``_effective_status``/``_attendance_of``/``_forgot_of``），本模块不另建
  第二套作业口径；Y 取数走 ``_queries.readable_facts``（F09 统一可读事实），
  考试成员按 F08 ``members_at`` 考试时点解析。
- metric 沿用 ``definitions.metric_meta``：homeroom 按班级年级校验
  （subject_percentile→单科分数、subject_grade_score→等级分、total_rank→
  总分）；teaching 域仅任教学科（范围隔离 §0.2），metric 钉住任教学科
  单科分数，传其他值 422。
- 分层：该场考试学校段位（high/critical/weak，``definitions.band_flags``
  + AnalysisConfig 阈值，名次解析 ``resolve_year_rank``，与 B1 段位同源）
  + 全班整体；主三门名次不可得的学生只进「全班」层（不落段，绝不按哨兵
  名次落段）；teaching 域无总分行，段位层如实标不可用。
- 每层不可计算原因（§2.3）：n<8（``n_too_small``）/ 零方差
  （``zero_variance``）/ 分母未知占比>50%（``denominator_unknown_over_half``，
  该段有成绩学生中因作业分母不可得被排除者过半）/ 段位不可用
  （``band_unavailable``）。不可计算层 r/rho 一律 null，绝不编造。
- 方向语义（y=分数，非名次）：r/rho>0 = 提交率越高成绩越高；
  <0 = 提交率越高成绩越低。``direction`` 取值 submit_up_score_up /
  submit_up_score_down（chat 工具旧键保留，值域随 y 改为分数口径）。
- 响应必含（§2.4）：window_days、窗口起止日、sample（各层 n 与排除数）、
  note（方向与指标含义 + 「相关性不构成因果或提分保证」）、
  calc_version="p2-v1"；另附旧键兼容字段 metadata/pairs/n/r/direction/
  caveats（pairs.y 由此前的名次改为分数，见交付说明）。
- 顶层 status="not_computable" 仅用于结构性不可算（考试日期缺失/该
  metric 无任何有效成绩）；分层级不可算在 layers[*].status/reason 表达。
"""

import math
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.analysis import definitions as defs
from app.core.context import WorkspaceContext
from app.core.errors import InvalidScopeParam, ResourceOutOfScope

# ── P2-C1 版本化常量（本模块自有；不改 P1 thresholds.py） ──────────────
CALC_VERSION = "p2-v1"
MIN_PAIRS_N = 8                     # 分层最小样本（契约 §2.3：n<8 不可计算）
DEFAULT_WINDOW_DAYS = 14            # 契约 HTTP 形状缺省窗口
VALID_WINDOW_DAYS = (14, 30)        # 契约 HTTP 形状允许 14|30
DENOMINATOR_UNKNOWN_SHARE_MAX = 0.5  # 分母未知占比 > 50% → 层不可计算

BAND_LAYER_ORDER = ("all", "high_score", "critical", "weak")
BAND_LAYER_LABELS = {
    "all": "全班",
    "high_score": "高分段",
    "critical": "临界段",
    "weak": "薄弱段",
}

REASON_N_TOO_SMALL = "n_too_small"
REASON_ZERO_VARIANCE = "zero_variance"
REASON_DENOMINATOR_OVER_HALF = "denominator_unknown_over_half"
REASON_BAND_UNAVAILABLE = "band_unavailable"

NOTE_CAUSAL = "相关性仅描述统计关联，不构成因果结论，也不构成提分保证。"


# ────────────────────────────── scope 与统计基础 ──────────────────────────────


def _as_context(scope) -> WorkspaceContext:
    """服务层接受 WorkspaceContext 或 chat_tools.resolve_scope_snapshot 的
    快照 dict（同源机制的两种载体，与 B1 _ScopeShim / B3 _as_context
    先例一致），统一还原为不可变上下文。"""
    if isinstance(scope, WorkspaceContext):
        return scope
    if isinstance(scope, dict):
        from datetime import datetime as _dt

        as_of = scope.get("as_of")
        if isinstance(as_of, str):
            as_of = _dt.fromisoformat(as_of).date() if "T" in as_of else date.fromisoformat(as_of)
        elif isinstance(as_of, _dt):
            as_of = as_of.date()
        return WorkspaceContext(
            teacher_id=int(scope.get("teacher_id") or 0),
            mode=scope["mode"],
            data_domain=scope.get("data_domain") or scope["mode"],
            academic_year_id=scope.get("academic_year_id"),
            class_ids=tuple(scope.get("class_ids") or ()),
            subject=scope.get("subject"),
            link_id=scope.get("link_id"),
            link_version=scope.get("link_version"),
            member_person_ids=tuple(scope.get("member_person_ids") or ()),
            as_of=as_of if isinstance(as_of, date) else date.today(),
        )
    raise InvalidScopeParam(
        "scope must be a WorkspaceContext or a resolved scope snapshot dict",
        details={"param": "scope"},
    )


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    """样本 Pearson 积矩相关系数（纯 Python 自实现，见模块 docstring 声明）。
    n<2 或任一侧零方差 → None（调用方转不可计算原因）。"""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return None
    return cov / math.sqrt(vx * vy)


def _rank_average(values: List[float]) -> List[float]:
    """并列秩处理：升序排名，同值取平均秩（1 起）。如 [10, 20, 20, 30] →
    [1, 2.5, 2.5, 4]。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def _spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    """Spearman 秩相关系数（纯 Python 自实现）：x/y 各取平均秩后求
    Pearson。任一侧零方差（含全部并列）→ None。"""
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    return _pearson(_rank_average(xs), _rank_average(ys))


def _round4(value: Optional[float]) -> Optional[float]:
    return round(value, 4) if value is not None else None


# ────────────────────────────── 考试与 Y 侧取数 ──────────────────────────────


def _ensure_exam_readable(db: Session, ctx: WorkspaceContext, exam_name: str) -> None:
    """考试必须在当前域可读（与既有 homework_correlation 端点同一规则，
    错误语义保持）：homeroom 查本域；teaching 本域或经生效关联可读的
    homeroom 域。完全不存在 → 404。"""
    from app.api.homework import _exam_exists_in_domain

    if ctx.mode == "homeroom":
        ok = _exam_exists_in_domain(db, "homeroom", ctx.academic_year_id, exam_name)
    else:
        ok = _exam_exists_in_domain(
            db, "teaching", ctx.academic_year_id, exam_name
        ) or _exam_exists_in_domain(db, "homeroom", ctx.academic_year_id, exam_name)
    if not ok:
        raise ResourceOutOfScope(
            f"exam not found in {ctx.mode} domain", details={"exam_name": exam_name}
        )


def _month_end(value: str) -> Optional[date]:
    """月份精度文本（"2026-05" 或 "2026-5"）→ 该月最后一天。解析失败 → None。"""
    import calendar

    parts = value.strip().split("-")
    if len(parts) != 2:
        return None
    try:
        year, month = int(parts[0]), int(parts[1])
        return date(year, month, calendar.monthrange(year, month)[1])
    except ValueError:
        return None


def _exam_anchor(db: Session, ctx: WorkspaceContext, exam_name: str):
    """考试窗口锚点与精度：exam_date（日精度）→ source_exam_date（月精度，
    锚定该月最后一天）→ None。

    月份精度是显式标注的近似（响应 caveats 注明 exam_date_month_precision），
    不是伪造日期；日精度数据出现后自动回到精确窗口。"""
    day = _exam_date_of(db, ctx, exam_name)
    if day is not None:
        return day, "day"
    from app.db.workspace_models import ScoreFact

    rows = (
        db.query(ScoreFact.source_exam_date)
        .filter(
            ScoreFact.data_domain == ctx.data_domain,
            ScoreFact.academic_year_id == ctx.academic_year_id,
            ScoreFact.exam_name == exam_name,
        )
        .all()
    )
    months = [row[0] for row in rows if row[0]]
    if not months:
        other = "teaching" if ctx.data_domain == "homeroom" else "homeroom"
        months = [
            row[0]
            for row in db.query(ScoreFact.source_exam_date)
            .filter(
                ScoreFact.data_domain == other,
                ScoreFact.academic_year_id == ctx.academic_year_id,
                ScoreFact.exam_name == exam_name,
            )
            .all()
            if row[0]
        ]
    latest = max(months) if months else None
    if latest is None:
        return None, None
    anchor = _month_end(str(latest))
    return (anchor, "month") if anchor is not None else (None, None)


def _exam_date_of(db: Session, ctx: WorkspaceContext, exam_name: str) -> Optional[date]:
    """按考试名解析考试日期（B3 changes.py 同一回退口径：本域本班 →
    本域全域 → 对侧域全域；全未知 → None，绝不伪造日期）。"""
    from app.db.workspace_models import ScoreFact

    def _max(rows) -> Optional[date]:
        dates = [row[0] for row in rows if row[0] is not None]
        return max(dates) if dates else None

    direct = _max(
        db.query(ScoreFact.exam_date)
        .filter(
            ScoreFact.data_domain == ctx.data_domain,
            ScoreFact.academic_year_id == ctx.academic_year_id,
            ScoreFact.exam_name == exam_name,
            ScoreFact.class_ref_id.in_(list(ctx.class_ids)),
        )
        .all()
    )
    if direct is not None:
        return direct
    domain_wide = _max(
        db.query(ScoreFact.exam_date)
        .filter(
            ScoreFact.data_domain == ctx.data_domain,
            ScoreFact.academic_year_id == ctx.academic_year_id,
            ScoreFact.exam_name == exam_name,
        )
        .all()
    )
    if domain_wide is not None:
        return domain_wide
    other = "teaching" if ctx.data_domain == "homeroom" else "homeroom"
    return _max(
        db.query(ScoreFact.exam_date)
        .filter(
            ScoreFact.data_domain == other,
            ScoreFact.academic_year_id == ctx.academic_year_id,
            ScoreFact.exam_name == exam_name,
        )
        .all()
    )


def _homeroom_grade(db: Session, ctx: WorkspaceContext) -> Optional[int]:
    from app.db.workspace_models import AdministrativeClass

    if not ctx.class_ids:
        return None
    return (
        db.query(AdministrativeClass.grade)
        .filter(AdministrativeClass.id == ctx.class_ids[0])
        .scalar()
    )


def _resolve_metric(db: Session, ctx: WorkspaceContext, metric: str) -> dict:
    """metric 解析（契约 §2.2：metric 沿用 definitions.metric_meta）。

    - homeroom：按班级年级经 defs.metric_meta 校验（ValueError → 422，
      与 analysis 端点同一错误语义）；kind 决定 y 取哪一列分数。
    - teaching：范围隔离（§0.2），metric 钉住任教学科单科分数；显式传入
      其他 metric → 422（绝不跨域取总分/他科）。
    """
    if ctx.mode == "teaching":
        pinned = f"subject:{ctx.subject}"
        if (metric or "").strip() != pinned:
            raise InvalidScopeParam(
                "teaching domain metric is pinned to the taught subject",
                details={"metric": metric, "pinned": pinned},
            )
        return {
            "value": pinned,
            "label": f"{ctx.subject}分数",
            "kind": "subject_score",
            "source": "subject",
            "key": ctx.subject,
        }
    grade = _homeroom_grade(db, ctx)
    try:
        meta = defs.metric_meta(grade, metric)
    except ValueError:
        raise InvalidScopeParam(
            "unsupported rank metric for grade",
            details={"metric": metric, "grade": grade},
        )
    kind = meta["kind"]
    if kind not in ("subject_percentile", "subject_grade_score", "total_rank"):
        raise InvalidScopeParam(
            "unsupported metric kind for correlation",
            details={"metric": metric, "kind": kind},
        )
    # y 一律取「分数」口径（契约 §2.2 单科分数或总分）：等级分指标取
    # grade_score 列，其余取 score 列。
    label = meta["label"] + ("等级分" if kind == "subject_grade_score" else "分数")
    return {**meta, "label": label}


def _y_by_person(db, ctx: WorkspaceContext, exam_name: str, roster: List[int], meta: dict) -> Dict[int, float]:
    """Y = metric 对应的分数（该场考试；缺考/缺列不转 0、不进样本）。
    经 readable_facts（F09 统一可读事实）取数，绝不自建投影。"""
    from app.api import _queries as q

    kind = meta["kind"]
    key = meta["key"]
    entries = q.readable_facts(db, ctx, exam_name, member_ids=roster)
    values: Dict[int, float] = {}
    for entry in entries:
        fact = entry.fact
        value = None
        if kind == "total_rank":
            if fact.total_type == key and fact.score is not None:
                value = float(fact.score)
        elif kind == "subject_grade_score":
            if fact.total_type is None and fact.subject == key:
                grade_score = getattr(fact, "grade_score", None)
                if grade_score is not None:
                    value = float(grade_score)
        else:  # subject_percentile / subject_score（teaching 钉住任教学科）
            if fact.total_type is None and fact.subject == key and fact.score is not None:
                value = float(fact.score)
        if value is not None:
            # 同一人同一口径多行（历史冲突保留行）取先到值：readable_facts
            # 已按域内优先消解，此处仅防御性去重
            values.setdefault(entry.person_id, value)
    return values


def _main3_rank_by_person(db, ctx: WorkspaceContext, exam_name: str, roster: List[int]) -> Dict[int, int]:
    """该场考试主三门名次（段位分层用；与 B1 current_level 同源：
    resolve_year_rank + 主三门总分行）。名次不可得的学生不落段。"""
    from app.api import _queries as q
    from app.api.homework import DEFAULT_TOTAL_TYPE

    ranks: Dict[int, int] = {}
    for entry in q.readable_facts(db, ctx, exam_name, member_ids=roster):
        fact = entry.fact
        if fact.total_type != DEFAULT_TOTAL_TYPE:
            continue
        rank = defs.resolve_year_rank(fact.xueji_rank, fact.grade_rank)
        if rank is not None:
            ranks.setdefault(entry.person_id, rank)
    return ranks


# ────────────────────────────── X 侧：窗口提交率 ──────────────────────────────


def _window_rates(
    db: Session,
    ctx: WorkspaceContext,
    window_start: date,
    window_end_exclusive: date,
    homework_subject: Optional[str],
    homework_type: Optional[str],
) -> Tuple[Dict[int, Tuple[int, int]], int]:
    """窗口 ``[window_start, window_end_exclusive)`` 内每人 (提交数, 应交数)
    （例外登记口径，见模块 docstring）与被剔除的「分母未知」批次个数。

    复用 homework 模块既有查询与状态语义：
    - 应交快照为空的批次整批剔除（分母未知），绝不按其默认已交；
    - 应交快照内无逐人行 = 已交（既有例外登记默认）；
    - 显式缺交 → 只进分母；请假/忘带/出勤异常行 → 分子分母都不进。
    """
    from app.api.homework import (
        _attendance_of,
        _effective_status,
        _forgot_of,
        _parse_expected_ids,
        _projected_expected,
        _projected_submissions,
        _visible_assignments,
    )

    numer: Dict[int, int] = {}
    denom: Dict[int, int] = {}
    excluded_batches = 0
    for a, mapping in _visible_assignments(db, ctx, active_only=True):
        if not (window_start <= a.assigned_date < window_end_exclusive):
            continue  # 窗口不含考试当日与考后批次，也不含窗口前批次
        if homework_subject is not None and a.subject != homework_subject:
            continue
        if homework_type is not None and a.homework_type != homework_type:
            continue
        if not _parse_expected_ids(a.expected_members_json):
            excluded_batches += 1  # 分母未知：整批剔除，绝不默认已交
            continue
        expected_reader = _projected_expected(db, a, mapping)
        rows = _projected_submissions(db, a, mapping)
        row_by_reader = {rid: s for s, rid in rows}
        for rid in expected_reader:
            status = _effective_status(row_by_reader[rid]) if rid in row_by_reader else "submitted"
            if status == "excused":
                continue  # 请假：不计分子分母
            row = row_by_reader.get(rid)
            if row is not None and (_attendance_of(row.evaluation) or _forgot_of(row.evaluation)):
                continue  # 出勤/忘带：不计分子分母
            denom[rid] = denom.get(rid, 0) + 1
            if status != "missing":
                numer[rid] = numer.get(rid, 0) + 1
    rates = {pid: (numer.get(pid, 0), denom[pid]) for pid in denom if denom[pid] > 0}
    return rates, excluded_batches


# ────────────────────────────── 分层与响应装配 ──────────────────────────────


def _layer_key_of(rank: Optional[int], band_config: dict) -> Optional[str]:
    """该场学校段位 → 层 key（band_flags 同源）；名次不可得或不在段 →
    None（只进「全班」层）。"""
    flags = defs.band_flags(rank, band_config)
    for key in ("high_score", "critical", "weak"):
        if flags[key]:
            return key
    return None


def _layer_payload(
    key: str,
    eligible: List[Tuple[int, float, float]],  # (person_id, x, y)
    eligible_n: int,
    excluded_no_homework: int,
    band_available: bool,
) -> dict:
    """单层计算：n<8 / 零方差 / 分母未知占比>50% → 不可计算 + 原因，
    r/rho 一律 null（绝不编造）。reason 按契约 §2.3 列举顺序择先。"""
    payload = {
        "key": key,
        "label": BAND_LAYER_LABELS[key],
        "eligible_n": eligible_n,
        "n": len(eligible),
        "excluded_no_homework": excluded_no_homework,
        "denominator_unknown_share": (
            round(excluded_no_homework / eligible_n, 4) if eligible_n > 0 else None
        ),
        "r": None,
        "rho": None,
        "status": "not_computable",
        "reason": None,
    }
    if key != "all" and not band_available:
        payload["reason"] = REASON_BAND_UNAVAILABLE
        return payload
    n = len(eligible)
    if n < MIN_PAIRS_N:
        payload["reason"] = REASON_N_TOO_SMALL
        return payload
    share = payload["denominator_unknown_share"]
    xs = [x for _pid, x, _y in eligible]
    ys = [y for _pid, _x, y in eligible]
    r = _pearson(xs, ys)
    rho = _spearman(xs, ys)
    if r is None:
        payload["reason"] = REASON_ZERO_VARIANCE
        return payload
    if share is not None and share > DENOMINATOR_UNKNOWN_SHARE_MAX:
        payload["reason"] = REASON_DENOMINATOR_OVER_HALF
        return payload
    payload["r"] = _round4(r)
    payload["rho"] = _round4(rho)
    payload["status"] = "ok"
    return payload


def _metadata_of(ctx: WorkspaceContext) -> dict:
    """与 homework 相关端点 metadata 同形状（旧工具返回体键保持）：
    homeroom subject 恒 null；teaching 为任教学科；teaching_class_id 沿用
    旧端点缺省 None。"""
    return {
        "mode": ctx.mode,
        "subject": ctx.subject if ctx.mode == "teaching" else None,
        "scope": {
            "academic_year_id": ctx.academic_year_id,
            "term_id": None,
            "class_id": ctx.class_ids[0] if ctx.mode == "homeroom" and ctx.class_ids else None,
            "teaching_class_id": None,
            "link_id": ctx.link_id,
            "link_version": ctx.link_version,
        },
        "cohort_size": len(ctx.member_person_ids),
        "data_revision": ctx.link_version or 0,
    }


def _base_response(ctx: WorkspaceContext, exam_name: str, window_days: int, meta: dict) -> dict:
    """结构骨架（结构性不可算时也返回完整形状，绝不缺键）。"""
    return {
        "calc_version": CALC_VERSION,
        "metadata": _metadata_of(ctx),
        "exam_name": exam_name,
        "exam_date": None,
        "window_days": window_days,
        "window_start": None,
        "window_end": None,
        "metric": meta["value"],
        "metric_label": meta["label"],
        "metric_kind": meta["kind"],
        "homework_subject": None,
        "status": "ok",
        "missing_reason": None,
        "n": 0,
        "r": None,
        "rho": None,
        "direction": None,
        "pairs": [],
        "layers": {},
        "sample": {
            "roster_n": 0,
            "exam_score_n": 0,
            "paired_n": 0,
            "excluded_no_exam_score": 0,
            "excluded_no_homework": 0,
            "excluded_batches_denominator_unknown": 0,
            "layers": {},
        },
        "caveats": [],
        "note": "",
    }


def _build_note(metric_label: str, window_days: int, band_available: bool) -> str:
    band_text = (
        "分层按该场考试的学校段位（高分/临界/薄弱，AnalysisConfig 阈值）+ 全班整体"
        if band_available
        else "教学域无总分行，学校段位分层不可用，仅输出全班整体"
    )
    return (
        f"指标含义：x = 考前 {window_days} 天窗口（不含考试当日与考后作业）内的"
        f"作业提交率（0–1，缺交数/应交数；忘带/请假/出勤等例外登记不计入分子分母，"
        f"分母未知或无有效批次的学生不入样本）；y = {metric_label}（分数，缺考不转 0）。"
        f"方向：r/rho 为正表示提交率越高的学生该指标分数越高，为负表示越低；"
        f"rho 为 Spearman 秩相关（并列值取平均秩），对非线性单调关系更稳健。"
        f"{band_text}；n<8、零方差或分母未知占比>50% 的层不输出系数。"
        f"{NOTE_CAUSAL}"
    )


# ────────────────────────────── 服务入口（契约冻结签名） ──────────────────────────────


def exam_homework_correlation(
    db: Session,
    scope,
    exam_name: str,
    window_days: int = DEFAULT_WINDOW_DAYS,
    metric: str = "total:主三门",  # 2026-09-29 与 AI 工具/前端统一默认（原 subject:语文）
    homework_subject: Optional[str] = None,
    homework_type: Optional[str] = None,
) -> dict:
    """单场考试「作业提交率 × 成绩」Pearson + Spearman 分层相关性（契约 §2）。

    - X：窗口内作业提交率（缺交数/应交数，例外登记口径）；
    - Y：metric 对应分数（definitions.metric_meta 口径；teaching 钉任教学科）；
    - 分层：该场学校段位（high/critical/weak）+ 全班；每层 n/r/rho/不可算原因。
    结构性不可算（考试日期缺失/无有效成绩）→ status="not_computable" + 原因，
    完整形状照返，绝不编造任何系数。
    """
    ctx = _as_context(scope)
    exam_name = (exam_name or "").strip()
    if not exam_name:
        raise InvalidScopeParam(
            "exam_name must be a non-empty exam name", details={"param": "exam_name"}
        )
    if not isinstance(window_days, int) or isinstance(window_days, bool) or window_days <= 0:
        raise InvalidScopeParam(
            "window_days must be a positive integer (14 or 30)",
            details={"param": "window_days", "window_days": window_days},
        )
    homework_subject = (homework_subject or "").strip() or None
    homework_type = (homework_type or "").strip() or None
    if ctx.mode == "teaching":
        # 范围隔离（§0.2）：教学域作业只有任教学科，X 侧钉住会话学科
        homework_subject = ctx.subject

    meta = _resolve_metric(db, ctx, metric)
    band_available = ctx.mode == "homeroom"
    response = _base_response(ctx, exam_name, window_days, meta)
    response["homework_subject"] = homework_subject
    response["note"] = _build_note(meta["label"], window_days, band_available)

    _ensure_exam_readable(db, ctx, exam_name)
    exam_date, date_precision = _exam_anchor(db, ctx, exam_name)
    if date_precision == "month":
        response["caveats"].append(
            "exam_date_month_precision：该场考试日期仅有年月精度，窗口以该月最后一天为锚点的近似值"
        )
    if exam_date is None:
        # 缺失纪律：无确切考试日期就没有可信窗口，绝不伪造起止日
        response["status"] = "not_computable"
        response["missing_reason"] = "exam_date_missing"
        response["caveats"].append("该场考试无确切日期（旧数据可能只有年月），窗口不可构造，相关性不可计算")
        response["note"] += "（当前：考试日期缺失，整体不可计算。）"
        for key in BAND_LAYER_ORDER:
            response["layers"][key] = _layer_payload(
                key, [], 0, 0, band_available
            )
            # 整体结构性不可算：所有层统一原因（不与 n_too_small 混淆）
            response["layers"][key]["reason"] = "exam_date_missing"
            response["sample"]["layers"][key] = _layer_sample(response["layers"][key])
        return response

    from app.api import _queries as q

    roster = q.members_at(db, ctx.mode, ctx.class_ids, exam_date) or list(
        ctx.member_person_ids
    )
    window_start = exam_date - timedelta(days=window_days)
    window_end = exam_date - timedelta(days=1)  # 最后一个计入的自然日（不含考后）
    response["exam_date"] = exam_date.isoformat()
    response["window_start"] = window_start.isoformat()
    response["window_end"] = window_end.isoformat()

    band_config = None
    if band_available:
        from app.analysis.config import get_band_config

        band_config = get_band_config(db)

    y_values = _y_by_person(db, ctx, exam_name, roster, meta)
    ranks = _main3_rank_by_person(db, ctx, exam_name, roster) if band_available else {}
    rates, excluded_batches = _window_rates(
        db, ctx, window_start, exam_date, homework_subject, homework_type
    )

    paired: List[Tuple[int, float, float, Optional[str]]] = []
    excluded_no_homework = 0
    for pid in sorted(y_values):
        rate = rates.get(pid)
        if rate is None:
            excluded_no_homework += 1  # 无有效批次或分母未知：绝不默认已交
            continue
        numer, denom = rate
        band_key = _layer_key_of(ranks.get(pid), band_config) if band_available else None
        paired.append((pid, numer / denom, y_values[pid], band_key))

    exam_score_n = len(y_values)
    sample = response["sample"]
    sample.update(
        {
            "roster_n": len(roster),
            "exam_score_n": exam_score_n,
            "paired_n": len(paired),
            "excluded_no_exam_score": max(len(roster) - exam_score_n, 0),
            "excluded_no_homework": excluded_no_homework,
            "excluded_batches_denominator_unknown": excluded_batches,
        }
    )

    names = q.names_for(db, [pid for pid, _x, _y, _b in paired])
    response["pairs"] = [
        {
            "person_id": pid,
            "name": names.get(pid),
            "x": round(x, 4),
            "y": y,
            "band": band_key,
        }
        for pid, x, y, band_key in paired
    ]
    response["n"] = len(paired)

    if exam_score_n == 0:
        response["status"] = "not_computable"
        response["missing_reason"] = "no_valid_exam_score"
        response["caveats"].append(
            "该场考试在当前范围内没有该指标的有效成绩（缺考或未导入），相关性不可计算"
        )
    elif excluded_batches:
        response["caveats"].append(
            f"{excluded_batches} 个窗口内作业批次因应交快照为空（分母未知）被整批剔除，"
            "未计入任何人的提交率"
        )

    # 分层：全班 = 有成绩的全体；段位层 = 该段有成绩的学生
    eligible_by_layer: Dict[str, List[Tuple[int, float, float]]] = {key: [] for key in BAND_LAYER_ORDER}
    excluded_by_layer: Dict[str, int] = {key: 0 for key in BAND_LAYER_ORDER}
    for pid, x, y, band_key in paired:
        eligible_by_layer["all"].append((pid, x, y))
        if band_key is not None:
            eligible_by_layer[band_key].append((pid, x, y))
    # 被作业侧排除者的段位（有成绩、有名次才可归段）
    for pid in sorted(y_values):
        if rates.get(pid) is not None:
            continue
        band_key = _layer_key_of(ranks.get(pid), band_config) if band_available else None
        excluded_by_layer["all"] += 1
        if band_key is not None:
            excluded_by_layer[band_key] += 1

    all_layer = None
    for key in BAND_LAYER_ORDER:
        if key == "all":
            layer_eligible_n = sample["exam_score_n"]
        else:
            layer_eligible_n = len(eligible_by_layer[key]) + excluded_by_layer[key]
        layer = _layer_payload(
            key,
            eligible_by_layer[key],
            layer_eligible_n,
            excluded_by_layer[key],
            band_available,
        )
        response["layers"][key] = layer
        response["sample"]["layers"][key] = _layer_sample(layer)
        if key == "all":
            all_layer = layer

    response["r"] = all_layer["r"]
    response["rho"] = all_layer["rho"]
    if all_layer["status"] != "ok":
        reason_text = {
            REASON_N_TOO_SMALL: f"配对样本数 n={all_layer['n']} < {MIN_PAIRS_N}，全班层 r/rho 不可计算",
            REASON_ZERO_VARIANCE: "提交率或成绩零方差，全班层 r/rho 不可计算",
            REASON_DENOMINATOR_OVER_HALF: "分母未知（无有效作业批次）的学生过半，全班层 r/rho 不可计算",
            REASON_BAND_UNAVAILABLE: "全班层不可计算",
            "exam_date_missing": "考试日期缺失，不可计算",
            "no_valid_exam_score": "无有效成绩，不可计算",
        }.get(all_layer["reason"] or "", "全班层不可计算")
        response["caveats"].append(f"不可计算：{reason_text}")
    elif all_layer["r"] is not None:
        if all_layer["r"] > 0:
            response["direction"] = "submit_up_score_up"
        elif all_layer["r"] < 0:
            response["direction"] = "submit_up_score_down"
        else:
            response["caveats"].append("提交率与成绩无线性相关（r=0），方向不判定")
    return response


def _layer_sample(layer: dict) -> dict:
    """sample.layers[*] 的紧凑形状（各层 n 与排除数，契约 §2.4）。"""
    return {
        "eligible_n": layer["eligible_n"],
        "n": layer["n"],
        "excluded_no_homework": layer["excluded_no_homework"],
        "status": layer["status"],
        "reason": layer["reason"],
    }
