"""P3-D1 教研统计（队列回看与结局聚合）服务实现（契约 docs/diagnosis-roadmap/p3-contracts.md §2）。

服务签名::

    def cohort_list(db, ctx, academic_year_id: int) -> dict        # §2.1 可用队列清单
    def outcome_aggregate(db, ctx, cohort, from_exam, to_exam, metric, academic_year_id) -> dict

产品目标：回答「此前关注的学生后来怎样」——**回顾性队列、无对照设计，
响应一律携带 limitations 固定文案，绝不输出干预效果因果结论**（铁律 4）。

同源红线（契约 §2 / 铁律 1，违反即返工）：
- 每人变化一律复用 B3 ``changes.student_change_decomposition``（禁止另算
  第二口径）；三段计数的阈值复用 B1 ``thresholds.TREND_DIRECTION_MIN_CHANGE``
  同一常量（不自带副本）；
- 类型队列复用 B2 ``types.classify_student``（输入 = B1 ``features`` 单生
  特征，与 types 端点/行动首页同一管道：``class_features`` 每生行 +
  ``classify_student``）；
- 干预队列复查摘要复用 C4 ``review.review_contrast``（ready/pending 只
  计数，不复制对照逻辑）；
- 指标解析复用共享 ``definitions.metric_options``（经 review.metric_meta_or_422
  唯一口径），成绩读取只经 ``app.api._queries.readable_facts``。

类型队列的「考试锚点」（2026-09-29 升级）：按队列所标考试时点重算——
B1 ``class_features(anchor_exam=…)`` 将考试时间线截断至该场（含）、作业与
教师关注窗口锚定到该场考试日期；考试日期仅有月精度或缺失时无法证明
时点，回退当前时点口径并以 TYPE_COHORT_LIMITATION 如实标注
（membership_basis 区分 exam_anchor / current_time_point）。成员仍为
当前名册（离班学生本就不在当前名册，跨名册回看属后续能力）。

干预队列范围（N01 域隔离）：只读本域（data_domain 与 ctx 一致）且
status 非空（C4 扩展列：NULL=旧档案/非干预记录）的 ``ws_student_note`` 行；
档案行本身不带班级列，故在人的维度收口——只统计「本班当前名册 ∪ 本班
学年内合法历史成员」（Enrollment / TeachingClassMember 有效期与本学年
窗口重叠）：他班学生即使在同域建档也绝不进入本班队列（既不算成员也
不算「已离班」）；教学域另按 subject_scope 过滤（非空且 ≠ 任教学科的
行属其他学科教学线，不进本班队列）。
"""

import statistics
from datetime import date
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.analysis import definitions as defs
from app.api import _queries as q
from app.core.context import WorkspaceContext
from app.core.errors import InvalidScopeParam, ResourceOutOfScope
from app.db.workspace_models import AcademicYear, Enrollment, TeachingClassMember, WsStudentNote
from app.diagnosis import thresholds as th
from app.diagnosis.changes import (
    CALC_VERSION as CHANGES_CALC_VERSION,
    student_change_decomposition,
)
from app.diagnosis.changes import _ensure_exams_readable, _exam_date_of
from app.diagnosis.features import class_features
from app.diagnosis.review import (
    CALC_VERSION as REVIEW_CALC_VERSION,
    metric_meta_or_422,
    review_contrast,
    unit_of_metric,
)
from app.diagnosis.types import classify_student

# 本波口径版本（p3-contracts.md 头部：本波版本号 p3-v1）
CALC_VERSION = "p3-v1"

# 干预记录状态词表（与 review.py / C4 迁移同源；NULL=非干预记录）
INTERVENTION_STATUSES = ("open", "done", "dismissed")

# 类型队列锚点说明（2026-09-29 升级为考试时点重算后的固定文案）
TYPE_COHORT_NOTE = (
    "类型队列按队列所标考试的时点重算：考试时间线截断至该场（含），"
    "作业与教师关注窗口锚定该场考试日期；成员为当前名册。"
)
# 考试日期不可解析（月精度/缺失）时的回退局限文案
TYPE_COHORT_LIMITATION = (
    "该场考试日期仅有月精度或缺失，无法证明时点，本队列回退为按当前时点"
    "口径判定（B1 特征按查询时点计算）；考试名仅作队列标识。"
)

# limitations 固定文案（契约 §2.2：回顾性队列、无对照组，前后变化不构成
# 干预效果因果结论——铁律 4，任何响应必含，绝不输出因果效果结论）
LIMITATIONS_RETROSPECTIVE = (
    "回顾性队列、无对照组：以上聚合是对既有档案与成绩数据的前后变化描述，"
    "前后变化不构成干预效果因果结论，也不能证明任何措施有效或无效。"
)

# 方向语义说明（符号沿用 P1 §9 裁决：变化值 = 本次 − 上次；名次/年级前
# 百分位越小越好 → 负值 = 相对位置上升；等级分越大越好 → 正值 = 提高）
_DIRECTION_NOTE_RANK = (
    "变化值 = 本次考试 − 上次考试（学籍/年级名次口径）：负值 = 名次数值变小 = "
    "相对位置上升（进步），正值 = 相对位置下降（退步）。"
)
_DIRECTION_NOTE_PERCENTILE = (
    "变化值 = 本次考试 − 上次考试（年级前百分位，单位百分点）：负值 = 相对位置"
    "上升（进步），正值 = 相对位置下降（退步）。"
)
_DIRECTION_NOTE_GRADE_SCORE = (
    "变化值 = 本次考试 − 上次考试（选考等级分）：正值 = 数值升高（进步），"
    "负值 = 数值降低（退步）。"
)
_NO_CAUSAL_NOTE = (
    "以上为既有数据的描述性聚合（回顾性队列、无对照组），不构成干预效果因果结论。"
)

# 聚合方向词表（与 B1 trend 方向词同源）
DIRECTION_PROGRESS = "进步"
DIRECTION_FLAT = "持平"
DIRECTION_DECLINE = "退步"

# 排除类目（契约 §2.2：缺考/无数据/已离班）
EXCLUDE_MISSING_EXAM = "missing_exam"      # 缺考或指标字段缺失（有可读事实但不可比）
EXCLUDE_NO_DATA = "no_data"                # 两场均无可读事实
EXCLUDE_TRANSFERRED_OUT = "transferred_out"  # 已离班（建档时在班、查询时点已不在当期名册）

_B2_TYPE_ORDER = (
    "持续进步型",
    "短期下滑型",
    "持续下滑型",
    "稳定优秀型",
    "高位波动型",
    "稳定临界型",
    "临界上升型",
    "临界下滑型",
    "明显偏科型",
    "综合风险型",
    "作业风险型",
)


# ────────────────────────── 小工具 ──────────────────────────


def _check_year(ctx: WorkspaceContext, academic_year_id: int) -> None:
    """学年一致性校验（与 features._check_year 同语义：作用域已钉死学年，
    跨年取数按 422 拒绝——跨学年不存在 404 由路由层 scope 解析给出）。"""
    if academic_year_id != ctx.academic_year_id:
        raise InvalidScopeParam(
            "academic_year_id does not match the resolved scope",
            details={
                "academic_year_id": academic_year_id,
                "scope_academic_year_id": ctx.academic_year_id,
            },
        )


def rules_version_combined() -> str:
    """rules_version 组合标注（契约 §2.2 必含）：各层口径版本按
    「特征/类型(p1) → 复查(p2) → 教研聚合(p3)」串联。"""
    return f"{th.CALC_VERSION}/{REVIEW_CALC_VERSION}/{CALC_VERSION}"


def _metric_direction_note(unit: str) -> str:
    base = {
        "rank": _DIRECTION_NOTE_RANK,
        "percentile": _DIRECTION_NOTE_PERCENTILE,
        "grade_score": _DIRECTION_NOTE_GRADE_SCORE,
    }.get(unit, _DIRECTION_NOTE_PERCENTILE)
    return (
        base
        + f"三段计数的持平阈值复用趋势方向常量 TREND_DIRECTION_MIN_CHANGE="
        f"{th.TREND_DIRECTION_MIN_CHANGE}（按指标自身单位计，|变化| 低于阈值计为持平）。"
        + _NO_CAUSAL_NOTE
    )


def _smaller_is_better(unit: str) -> bool:
    return unit in ("rank", "percentile")


def _direction_of(change: float, unit: str) -> str:
    """三段计数判定（阈值 = TREND_DIRECTION_MIN_CHANGE 同一常量，按指标
    自身单位；方向含义随「越小越好/越大越好」切换）。"""
    t = th.TREND_DIRECTION_MIN_CHANGE
    improved_is = change <= -t if _smaller_is_better(unit) else change >= t
    declined_is = change >= t if _smaller_is_better(unit) else change <= -t
    if improved_is:
        return DIRECTION_PROGRESS
    if declined_is:
        return DIRECTION_DECLINE
    return DIRECTION_FLAT


def _metric_options(db: Session, ctx: WorkspaceContext) -> List[dict]:
    """指标选项（共享 definitions.metric_options 唯一口径；教学域 ctx.grade
    可为 None，按 active_grade 回落——与 review.metric_meta_or_422 同法）。"""
    grade = ctx.grade
    if grade is None:
        from app.rollover.service import get_active_grade

        grade = int(get_active_grade(db))
    return defs.metric_options(int(grade))


def _class_history_person_ids(
    db: Session, ctx: WorkspaceContext, candidates: Set[int]
) -> Set[int]:
    """候选人中的「本班学年内合法历史成员」：Enrollment（homeroom）/
    TeachingClassMember（teaching）有效期与本学年窗口重叠（F08 同源
    口径：历史时点只看有效期覆盖，不按当前 status 过滤）。
    学年无法解析 → 空集（保守：宁可不认历史成员，绝不放宽到他班）。"""
    if not candidates or not ctx.class_ids or ctx.academic_year_id is None:
        return set()
    year = db.get(AcademicYear, ctx.academic_year_id)
    if year is None:
        return set()
    if ctx.mode == "homeroom":
        rows = (
            db.query(Enrollment.identity_id)
            .filter(
                Enrollment.admin_class_id.in_(list(ctx.class_ids)),
                Enrollment.identity_id.in_(sorted(candidates)),
                Enrollment.valid_from <= year.end_date,
                or_(
                    Enrollment.valid_to.is_(None),
                    Enrollment.valid_to >= year.start_date,
                ),
            )
            .distinct()
            .all()
        )
    else:
        rows = (
            db.query(TeachingClassMember.identity_id)
            .filter(
                TeachingClassMember.teaching_class_id.in_(list(ctx.class_ids)),
                TeachingClassMember.identity_id.in_(sorted(candidates)),
                TeachingClassMember.valid_from <= year.end_date,
                or_(
                    TeachingClassMember.valid_to.is_(None),
                    TeachingClassMember.valid_to >= year.start_date,
                ),
            )
            .distinct()
            .all()
        )
    return {row[0] for row in rows}


def _intervention_notes(db: Session, ctx: WorkspaceContext) -> List[WsStudentNote]:
    """本班范围内的干预建档行（C4 扩展列 status 非空；NULL=旧档案/非干预
    记录，与模型注释同一判定）。

    N01 收口（档案行无班级列，按人的班级归属约束）：
    - 域一致（data_domain == ctx.data_domain）；
    - 人 ∈ 本班当前名册 ∪ 本班学年内合法历史成员（他班学生即使同域
      建档也不进入，且绝不被误记为「已离班」）；
    - 教学域另过滤 subject_scope：非空且 ≠ 任教学科的行属其他学科
      教学线（该生可能在多个教学班，此行不属于本班学科）。"""
    notes = (
        db.query(WsStudentNote)
        .filter(
            WsStudentNote.data_domain == ctx.data_domain,
            WsStudentNote.status.in_(INTERVENTION_STATUSES),
        )
        .order_by(WsStudentNote.date.asc(), WsStudentNote.id.asc())
        .all()
    )
    member_set = set(ctx.member_person_ids)
    candidates = {note.person_id for note in notes} - member_set
    qualified = member_set | _class_history_person_ids(db, ctx, candidates)
    if ctx.mode == "teaching":
        return [
            note
            for note in notes
            if note.person_id in qualified
            and not (note.subject_scope and ctx.subject and note.subject_scope != ctx.subject)
        ]
    return [note for note in notes if note.person_id in qualified]


def _intervention_person_sets(
    db: Session, ctx: WorkspaceContext
) -> Tuple[List[int], List[int]]:
    """干预队列的（在册成员, 已离班成员）：在册 = 当前名册内有干预建档；
    已离班 = 学年内合法历史成员（含跨出当前名册者）。他班人员两个集合
    都不进。"""
    notes = _intervention_notes(db, ctx)
    member_set = set(ctx.member_person_ids)
    base = {note.person_id for note in notes}
    in_scope = sorted(pid for pid in base if pid in member_set)
    transferred = sorted(pid for pid in base if pid not in member_set)
    return in_scope, transferred


def _exam_names_of_scope(db: Session, ctx: WorkspaceContext) -> List[dict]:
    """本范围可读考试清单（F09 统一口径：readable_exam_summaries）。"""
    return [
        {"exam_name": item["exam_name"], "exam_date": item["exam_date"]}
        for item in q.readable_exam_summaries(db, ctx)
    ]


# ────────────────────────── §2.1 可用队列清单 ──────────────────────────


def cohort_list(db: Session, ctx: WorkspaceContext, academic_year_id: int) -> dict:
    """可用队列清单（契约 §2.1）：interventions + type:<名>@<考试>。"""
    _check_year(ctx, academic_year_id)

    cohorts: List[dict] = []

    # ── interventions：有过干预建档的学生（按 subject_scope/problem 分组）──
    notes = _intervention_notes(db, ctx)  # 已按本班成员/历史成员 + 教学学科线收口
    in_scope, transferred = _intervention_person_sets(db, ctx)
    groups: Dict[Tuple[Optional[str], Optional[str]], dict] = {}
    in_scope_set = set(in_scope)
    for note in notes:
        if note.person_id not in in_scope_set:
            continue  # 已离班成员只进 transferred_out 桶，不进可答队列分组
        key = (note.subject_scope, note.problem)
        bucket = groups.setdefault(
            key,
            {
                "subject_scope": note.subject_scope,
                "problem": note.problem,
                "person_ids": [],
                "n": 0,
                "start_date": None,
                "end_date": None,
                "status_counts": {status: 0 for status in INTERVENTION_STATUSES},
            },
        )
        if note.person_id not in bucket["person_ids"]:
            bucket["person_ids"].append(note.person_id)
            bucket["n"] += 1
        bucket["status_counts"][note.status if note.status in INTERVENTION_STATUSES else "open"] += 1
        # 起止：start = 最早开始日（缺省回落档案日期）；end = 最晚计划复查日
        # （缺省回落开始日/档案日期）——只陈述既有字段，绝不推算。
        start = note.start_date or note.date
        end = note.review_date or start
        if bucket["start_date"] is None or start < bucket["start_date"]:
            bucket["start_date"] = start
        if bucket["end_date"] is None or end > bucket["end_date"]:
            bucket["end_date"] = end

    cohorts.append(
        {
            "cohort": "interventions",
            "kind": "interventions",
            "student_count": len(in_scope),
            "groups": [
                {
                    **bucket,
                    "person_ids": sorted(bucket["person_ids"]),
                    "start_date": bucket["start_date"].isoformat()
                    if bucket["start_date"]
                    else None,
                    "end_date": bucket["end_date"].isoformat() if bucket["end_date"] else None,
                }
                for bucket in sorted(
                    groups.values(),
                    key=lambda b: (
                        b["subject_scope"] or "",
                        b["problem"] or "",
                    ),
                )
            ],
            "transferred_out": {
                "n": len(transferred),
                "person_ids": sorted(transferred),
            },
        }
    )

    # ── type:<类型名>@<考试名>：B2 类型队列（按该场考试锚点重算，见模块注释）──
    exams = _exam_names_of_scope(db, ctx)
    # 多锚点一次取数装配（每生基础数据只查一次，锚点间只做截断/窗口过滤）
    from app.diagnosis.features import class_features_at_anchors

    anchored = class_features_at_anchors(
        db, ctx, academic_year_id, [exam["exam_name"] for exam in exams]
    )
    type_entries: List[dict] = []
    for exam in exams:
        cls = anchored[exam["exam_name"]]
        anchor_info = cls.get("anchor") or {}
        basis = anchor_info.get("timepoint_basis") or "current_time_point"
        fallback_limitation = None if anchor_info.get("date_resolved") else TYPE_COHORT_LIMITATION
        members_by_type: Dict[str, List[int]] = {}
        for row in cls.get("students") or []:
            features = row.get("features")
            if not isinstance(features, dict):
                continue  # 无 features 的行不编造（缺失纪律）
            main_type = classify_student(features).get("main_type")
            if main_type:
                members_by_type.setdefault(main_type, []).append(row.get("person_id"))
        for type_name in _B2_TYPE_ORDER:
            pids = sorted(members_by_type.get(type_name, []))
            if not pids:
                continue  # 空队列不进清单（outcome 对空队列仍如实返回空聚合）
            type_entries.append(
                {
                    "cohort": f"type:{type_name}@{exam['exam_name']}",
                    "kind": "type",
                    "type_name": type_name,
                    "exam_name": exam["exam_name"],
                    "student_count": len(pids),
                    "person_ids": pids,
                    "membership_basis": basis,
                    "limitation": fallback_limitation,
                }
            )
    cohorts.extend(type_entries)

    return {
        "calc_version": CALC_VERSION,
        "rules_version": rules_version_combined(),
        "academic_year_id": ctx.academic_year_id,
        "scope_mode": ctx.mode,
        "as_of": ctx.as_of.isoformat() if isinstance(ctx.as_of, date) else ctx.as_of,
        "exams": exams,
        "metric_options": _metric_options(db, ctx),
        "cohorts": cohorts,
        "type_cohort_note": TYPE_COHORT_NOTE,
        "limitations": [LIMITATIONS_RETROSPECTIVE, TYPE_COHORT_NOTE],
    }


# ────────────────────────── §2.2 结局聚合 ──────────────────────────


def _parse_cohort(db: Session, ctx: WorkspaceContext, cohort: str) -> dict:
    """队列标识解析：``interventions`` 或 ``type:<类型名>@<考试名>``。
    类型名不在 B2 词表 → 422；锚点考试不在本范围可读考试内 → 404。"""
    if not cohort or not cohort.strip():
        raise InvalidScopeParam(
            "cohort is required",
            details={"param": "cohort", "expected": "interventions | type:<类型名>@<考试名>"},
        )
    text = cohort.strip()
    if text == "interventions":
        return {"kind": "interventions"}
    if text.startswith("type:") and "@" in text:
        type_name, exam_name = text[len("type:"):].rsplit("@", 1)
        if type_name not in _B2_TYPE_ORDER:
            raise InvalidScopeParam(
                "unknown type name for cohort",
                details={"param": "cohort", "type_name": type_name},
            )
        exam_names = {item["exam_name"] for item in _exam_names_of_scope(db, ctx)}
        if exam_name not in exam_names:
            raise ResourceOutOfScope(
                "cohort exam not found in current scope",
                details={"exam_name": exam_name, "data_domain": ctx.data_domain},
            )
        return {"kind": "type", "type_name": type_name, "exam_name": exam_name}
    raise InvalidScopeParam(
        "cohort must be 'interventions' or 'type:<类型名>@<考试名>'",
        details={"param": "cohort", "cohort": cohort},
    )


def _members_of(db: Session, ctx: WorkspaceContext, parsed: dict, academic_year_id: int) -> Tuple[List[int], List[int], dict]:
    """队列成员（在册）与已离班成员（学年内合法历史成员且已跨出当前名册，
    仅干预队列可能非空；他班人员绝不进任一集合）。第三个返回值为队列
    附加信息（类型队列 = 锚点解析结果，供 basis/局限标注，避免二次计算）。"""
    member_set = set(ctx.member_person_ids)
    if parsed["kind"] == "interventions":
        in_scope, transferred = _intervention_person_sets(db, ctx)
        return in_scope, transferred, {}

    # 类型队列：B1 class_features（考试锚点重算）+ B2 classify_student
    # （与 types 端点同管道），成员 = 主类型命中者；日期不可解析时
    # class_features 回退当前时点（basis 如实标注）
    cls = class_features(db, ctx, academic_year_id, anchor_exam=parsed["exam_name"])
    members: List[int] = []
    for row in cls.get("students") or []:
        features = row.get("features")
        if not isinstance(features, dict):
            continue
        if row.get("person_id") in member_set and classify_student(features).get(
            "main_type"
        ) == parsed["type_name"]:
            members.append(row["person_id"])
    return sorted(members), [], {"anchor": cls.get("anchor") or {}}


def _metric_change_of(decomposition: dict, meta: dict, unit: str) -> Tuple[Optional[float], Optional[str]]:
    """从 B3 单生分解结果提取目标指标的变化值（只读字段，绝不复算）。

    total:{T} → ``totals`` 里口径为 {T} 的名次变化（主三门兼容读既有
    ``main3`` 键；五门/3+3 等口径各读各的，绝不再冒充主三门）；
    subject:{S} → 该科 percentile_change；subject_grade:{S} → 该科
    grade_score_change。变化不可得 → (None, 原因)。"""
    if unit == "rank":
        key = meta.get("key")
        row = next(
            (
                item
                for item in decomposition.get("totals") or []
                if isinstance(item, dict) and item.get("total_type") == key
            ),
            None,
        )
        if row is None and key == "主三门":
            row = decomposition.get("main3") or None  # 旧响应兼容（仅主三门）
        if row is None:
            return None, f"该生两场考试均无 {key} 总分成绩行"
        change = row.get("rank_change")
        if change is None:
            return None, row.get("missing_reason") or f"{key}名次变化不可得"
        return float(change), None
    key = meta.get("key")
    row = next(
        (
            item
            for item in decomposition.get("subjects") or []
            if isinstance(item, dict) and item.get("subject") == key
        ),
        None,
    )
    if row is None:
        return None, "该科在两场考试均无可读成绩行"
    field = "grade_score_change" if unit == "grade_score" else "percentile_change"
    change = row.get(field)
    if change is None:
        return None, row.get("missing_reason") or "该指标在两场考试间不可比"
    return float(change), None


def _has_readable_fact(db: Session, ctx: WorkspaceContext, exam_name: str, person_id: int) -> bool:
    """该生在该场是否有任何本范围可读事实（区分「缺考/字段缺失」与
    「两场均无数据」；只做读取判断，不做任何计算）。"""
    return bool(
        q.readable_facts(db, ctx, exam_name, member_ids=[person_id])
    )


def _review_summary(db: Session, ctx: WorkspaceContext, member_ids: List[int]) -> dict:
    """干预队列复查摘要（契约 §2.2：ready 条数/pending 条数）——复用 C4
    ``review.review_contrast`` 逐条对照，只计数，绝不复制对照逻辑。"""
    member_set = set(member_ids)
    ready = pending = 0
    by_reason: Dict[str, int] = {}
    for note in _intervention_notes(db, ctx):
        if note.person_id not in member_set:
            continue
        contrast = review_contrast(db, ctx, note)
        if contrast.get("status") == "ready":
            ready += 1
        else:
            pending += 1
            reason = contrast.get("reason") or "unknown"
            by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "ready_n": ready,
        "pending_n": pending,
        "total_n": ready + pending,
        "pending_reasons": dict(sorted(by_reason.items())),
    }


def outcome_aggregate(
    db: Session,
    ctx: WorkspaceContext,
    cohort: str,
    from_exam: str,
    to_exam: str,
    metric: str,
    academic_year_id: int,
) -> dict:
    """队列结局聚合（契约 §2.2）：成员固定可比集合（两场均有效）；每人变化
    复用 B3 student_change_decomposition；聚合 improved/flat/declined（阈值 =
    TREND_DIRECTION_MIN_CHANGE 同口径）、median_change 与 excluded；
    干预队列另附 C4 review 摘要；响应必含 rules_version 组合标注与
    limitations 固定文案。"""
    _check_year(ctx, academic_year_id)
    parsed = _parse_cohort(db, ctx, cohort)
    if not from_exam or not to_exam:
        raise InvalidScopeParam(
            "from_exam/to_exam must be non-empty exam names",
            details={"param": "from_exam,to_exam"},
        )
    if from_exam == to_exam:
        raise InvalidScopeParam(
            "from_exam and to_exam must differ",
            details={"from_exam": from_exam, "to_exam": to_exam},
        )
    # 两场考试都必须在当前作用域可读（复用 B3 同一校验；跨学年 → 404）
    _ensure_exams_readable(db, ctx, [from_exam, to_exam])
    meta = metric_meta_or_422(db, ctx, metric)  # 非法指标 → 422（唯一口径）
    unit = unit_of_metric(meta)

    members, transferred, extra = _members_of(db, ctx, parsed, academic_year_id)
    names = q.names_for(db, sorted(set(members) | set(transferred)))

    improved = flat = declined = 0
    changes: List[float] = []
    students: List[dict] = []
    excluded: Dict[str, dict] = {
        EXCLUDE_MISSING_EXAM: {"n": 0, "person_ids": []},
        EXCLUDE_NO_DATA: {"n": 0, "person_ids": []},
        EXCLUDE_TRANSFERRED_OUT: {"n": len(transferred), "person_ids": list(transferred)},
    }

    for pid in members:
        decomposition = student_change_decomposition(db, ctx, pid, from_exam, to_exam)  # B3 唯一变化口径
        change, reason = _metric_change_of(decomposition, meta, unit)
        if change is None:
            # 两场均无可读事实 → 无数据；有事实但缺考/指标字段缺失 → 缺考
            has_any = _has_readable_fact(
                db, ctx, from_exam, pid
            ) or _has_readable_fact(db, ctx, to_exam, pid)
            bucket = EXCLUDE_MISSING_EXAM if has_any else EXCLUDE_NO_DATA
            excluded[bucket]["person_ids"].append(pid)
            excluded[bucket]["n"] += 1
            continue
        direction = _direction_of(change, unit)
        if direction == DIRECTION_PROGRESS:
            improved += 1
        elif direction == DIRECTION_DECLINE:
            declined += 1
        else:
            flat += 1
        changes.append(change)
        students.append(
            {
                "person_id": pid,
                "name": names.get(pid),
                "change": round(change, 2),
                "direction": direction,
                "missing_reason": reason,
            }
        )

    # 明细排序：进步在前（变化越小越靠前，随指标方向），同值按 person_id
    students.sort(
        key=lambda item: (
            {DIRECTION_PROGRESS: 0, DIRECTION_FLAT: 1, DIRECTION_DECLINE: 2}[item["direction"]],
            item["change"] if _smaller_is_better(unit) else -item["change"],
            item["person_id"],
        )
    )

    is_intervention = parsed["kind"] == "interventions"
    limitations = [LIMITATIONS_RETROSPECTIVE]
    membership_basis = "current_roster"
    if not is_intervention:
        # 类型队列：按锚点考试重算（_members_of 已解析锚点；日期不可解析
        # 时其内部回退当前时点，此处按同一结果标注 basis 与局限）
        anchor_info = extra.get("anchor") or {}
        if anchor_info.get("date_resolved"):
            membership_basis = "exam_anchor"
            limitations.append(TYPE_COHORT_NOTE)
        else:
            membership_basis = "current_time_point"
            limitations.append(TYPE_COHORT_LIMITATION)

    from_date = _exam_date_of(db, ctx, from_exam)
    to_date = _exam_date_of(db, ctx, to_exam)
    return {
        "calc_version": CALC_VERSION,
        "rules_version": rules_version_combined(),
        "rules_version_detail": {
            "features": th.CALC_VERSION,
            "types": th.CALC_VERSION,
            "changes": CHANGES_CALC_VERSION,
            "review": REVIEW_CALC_VERSION,
            "research": CALC_VERSION,
        },
        "academic_year_id": ctx.academic_year_id,
        "scope_mode": ctx.mode,
        "cohort": cohort.strip(),
        "cohort_kind": parsed["kind"],
        "type_name": parsed.get("type_name"),
        "cohort_exam_name": parsed.get("exam_name"),
        "membership_basis": membership_basis,
        "metric": metric,
        "metric_unit": unit,
        "from_exam": from_exam,
        "from_exam_date": from_date.isoformat() if from_date else None,
        "to_exam": to_exam,
        "to_exam_date": to_date.isoformat() if to_date else None,
        "direction_note": _metric_direction_note(unit),
        "threshold": th.TREND_DIRECTION_MIN_CHANGE,
        "status": "ok" if members else "empty",
        "missing_reason": None if members else "该队列在本范围内没有成员（空队列如实返回零计数）",
        "comparable_n": len(changes),
        "improved_n": improved,
        "flat_n": flat,
        "declined_n": declined,
        "median_change": round(float(statistics.median(changes)), 2) if changes else None,
        "students": students,
        "excluded": excluded,
        "review_summary": _review_summary(db, ctx, members) if is_intervention else None,
        "limitations": limitations,
    }
