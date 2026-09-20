"""/api/v1 分析端点（契约 docs/contracts/p3-imports-analysis.md §2，已冻结）。

读取 ScoreFact 做班内统计；作用域解析/metadata/空态沿用 P1 既有规则：
- 统一可读事实口径（v2.1/F09）：全部端点经 _queries.readable_facts 取数
  （本域事实 + 经 §1.4.1 五条件门的对侧投影行 + 冲突注记），不得自建
  第二套投影/过滤逻辑。
- 考试维度成员口径（v2.1/F08）：exams/{exam_name}/stats|students、bands、
  class-compare 按考试发生时名册（members_at）解析成员；metadata 注明
  membership_basis="exam"。教师可访问历史班的校验仍由 ctx（绑定/任课）
  完成；dashboard/看板/预警类沿用查询时点（membership_basis="current"）。
- homeroom：resolve_workspace_context(绑定班)；全科与总分本域已有，
  linked 学生任教学科经统一口径进入统计/学生表——仅本域无同场事实时
  投影（T-only 科目进 subjects），两域同场值不同保留本域值并附
  shared_conflicts，绝不投影对方值。
- teaching：build_teaching_params（teaching_class_id 缺省=全部所教班并集）；
  成员事实 = teaching 域该班行 + 经 §1.4.1 投影门的 H 域反向投影行
  （两者都算分母/名次，投影行标 source_domain）。
- 计量红线（§2.3）：缺考 NULL 不进均分/名次分母、单独 missing_count、
  绝不转 0；同分同名次（min-rank，1,2,2,4）且只在班内成员计算；
  valid_count < 5 → small_sample；未知字段缺省不编造；空成员 200 空态。
- bands（v2.1/F10）：AnalysisConfig 的 400/500 是年级名次阈值；仅总分
  行有合法年级名次时计算，否则返回 409，绝不做分数镜像比较。
- 越界：域内（同 data_domain+学年）完全不存在该考试 → 404
  resource_out_of_scope；存在但本班无行 → 200 空态，绝不回退全年级。
"""

import logging
import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import _queries as q
from app.api import current_teacher_id, domain_endpoint
from app.api.analysis_schemas import (
    AnalysisMetadata,
    BandsResponse,
    ClassCompareEntry,
    ClassCompareResponse,
    FocusBandConfig,
    HomeroomAnalysisStudent,
    HomeroomClassAverageRow,
    HomeroomClassAveragesResponse,
    HomeroomExamStatsResponse,
    HomeroomExamStudentsResponse,
    HomeroomFocusResponse,
    HomeroomFocusStudent,
    HomeroomRankFrequencyBin,
    HomeroomRankFrequencyExam,
    HomeroomRankFrequencyResponse,
    HomeroomRankFrequencyStudent,
    HomeroomRankDistributionResponse,
    HomeroomRankDistributionSeries,
    HomeroomRankMetric,
    HomeroomRankMetricsResponse,
    HomeroomRankRangeResponse,
    HomeroomRankRangeStudent,
    HomeroomSubjectStat,
    HomeroomTotalStat,
    HomeroomWeeklyFocusResponse,
    WeeklyFocusStudent,
    TeachingAnalysisStudent,
    TeachingExamStatsResponse,
    TeachingExamStudentsResponse,
    TrendExamPoint,
    TrendYearGroup,
    TrendsResponse,
)
from app.api.students import _metadata
from app.core.context import WorkspaceContext, resolve_workspace_context
from app.core.errors import DomainError, InvalidScopeParam, ResourceOutOfScope
from app.db.models import get_db
from app.db.workspace_models import (
    AcademicYear,
    AdministrativeClass,
    HomeroomTeachingLink,
    HomeworkAssignment,
    HomeworkSubmission,
    ScoreFact,
    WsStudentNote,
    TeachingClass,
    WorkspaceClassAverage,
)
from app.analysis.config import (
    PROGRESS_RANK_THRESHOLD,
    SUBJECT_WEAKNESS_PCT_DIFF,
    VOLATILITY_RANK_THRESHOLD,
    get_band_config,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analysis"])

BASE_SUBJECTS = ["语文", "数学", "英语"]
ELECTIVE_SUBJECTS = ["物理", "化学", "生物", "政治", "历史", "地理"]
ALL_SUBJECTS = BASE_SUBJECTS + ELECTIVE_SUBJECTS
PERCENTILE_BINS = [
    ("p0_20", "前20%", 0.0, 0.2),
    ("p20_40", "20%-40%", 0.2, 0.4),
    ("p40_60", "40%-60%", 0.4, 0.6),
    ("p60_80", "60%-80%", 0.6, 0.8),
    ("p80_100", "后20%", 0.8, 1.0),
]
GRADE_SCORE_VALUES = [70, 67, 64, 61, 58, 55, 52, 49, 46, 43, 40]
GRADE_SCORE_SEPARATOR_AFTER = {67, 58, 49, 43}


class BandsNotComputable(DomainError):
    """段位分布不可计算（契约 §2.1 v2.1/F10）：段位阈值口径为年级名次，
    当前范围没有名次事实时返回 409 invalid_scope_param
    （特例覆盖 InvalidScopeParam 的通用 422 映射）。"""

    status_code = 409
    code = "invalid_scope_param"


# ────────────────────────────── 公共计量 ──────────────────────────────


def _analysis_metadata(
    ctx: WorkspaceContext,
    membership_basis: str,
    teaching_class_id: Optional[int] = None,
    fact_revisions=(),
) -> AnalysisMetadata:
    """分析端点元数据：在公共 Metadata 上补 membership_basis（契约 §1.4.1
    v2.1：考试维度=exam，看板/查询时点=current）。"""
    base = _metadata(ctx, teaching_class_id=teaching_class_id, fact_revisions=fact_revisions)
    return AnalysisMetadata(**base.model_dump(), membership_basis=membership_basis)


def _exam_member_ids(db: Session, ctx: WorkspaceContext, exam_name: str) -> List[int]:
    """考试维度成员口径（F08）：按该场考试 exam_date 时点解析本班成员；
    考试日期未知（事实均无日期）→ 回退查询时点成员。教师可访问历史班
    的校验仍由 ctx（绑定/任课）完成，这里只做成员时点解析。"""
    exam_date = q.exam_date_for(db, ctx, exam_name)
    if exam_date is None:
        return list(ctx.member_person_ids)
    return q.members_at(db, ctx.mode, ctx.class_ids, exam_date)


def _grade_score_of(fact) -> Optional[float]:
    """契约 §1.3 的 grade_score 列由并行波次（迁移 0004）落地；
    列未就绪时缺省 None，不编造。"""
    return getattr(fact, "grade_score", None)


def _avg(values: List[float]) -> Optional[float]:
    """均分：NULL 不进分母（调用方已过滤）；无可计值 → None。
    统一 round(2)，保证同组分数在两域端点读到完全一致的值（E01）。"""
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _rank_map(pairs: List[Tuple[int, Optional[float]]]) -> Dict[int, Optional[int]]:
    """min-rank 同分同名次（如 1,2,2,4）：名次 = 1 + 严格更高分人数。
    NULL 不参与名次（该 key 的 rank 为 None）；只对传入的成员集合计算，
    绝不外扩到年级。"""
    scores = [s for _, s in pairs if s is not None]
    return {
        key: (None if s is None else 1 + sum(1 for other in scores if other > s))
        for key, s in pairs
    }


def _exam_exists_in_domain(
    db: Session, data_domain: str, academic_year_id: int, exam_name: str
) -> bool:
    """该域该学年是否存在该考试（不限班级）：完全不存在 → 调用方 404；
    存在但本班无行由调用方按 200 空态处理。"""
    return (
        db.query(ScoreFact.id)
        .filter(
            ScoreFact.data_domain == data_domain,
            ScoreFact.academic_year_id == academic_year_id,
            ScoreFact.exam_name == exam_name,
        )
        .limit(1)
        .first()
        is not None
    )


def _exam_exists_for_teaching(
    db: Session, academic_year_id: int, exam_name: str
) -> bool:
    """teaching 端点的考试存在性：教学侧计量含经 §1.4.1 反向投影的
    H 域行（两者都算分母/名次），故放宽为任一域存在该考试；两域皆无
    该考试才是越界 404。"""
    return _exam_exists_in_domain(
        db, "teaching", academic_year_id, exam_name
    ) or _exam_exists_in_domain(db, "homeroom", academic_year_id, exam_name)


def _exam_exists_for_homeroom(db: Session, ctx, exam_name: str) -> bool:
    """homeroom 端点的考试存在性（F09 统一可读事实口径）：本域存在即
    可读；本域无该考试但有生效关联（ctx.link_id 只在 status='active'
    且有效期内解析出）且对侧教学域存在 → 对侧行可投影进统计，同样算
    可读。取消关联后回退严格本域口径（对侧考试即越界 404）。"""
    if _exam_exists_in_domain(db, "homeroom", ctx.academic_year_id, exam_name):
        return True
    if ctx.link_id is None:
        return False
    return _exam_exists_in_domain(db, "teaching", ctx.academic_year_id, exam_name)


def _class_labels(db: Session, class_ids) -> Dict[int, str]:
    rows = (
        db.query(TeachingClass.id, TeachingClass.label)
        .filter(TeachingClass.id.in_(list(class_ids)))
        .all()
    )
    return {row[0]: row[1] for row in rows}


def _teaching_member_rows(
    db: Session,
    ctx: WorkspaceContext,
    class_ids,
    subject: str,
    exam_name: Optional[str],
    member_ids: Optional[Sequence[int]] = None,
) -> List[Tuple[int, Optional[float], Optional[float], str, Optional[str]]]:
    """教学班成员事实（stats/students/class-compare 共用口径）：
    teaching 域指定班行 + 经 §1.4.1 投影门的 H 域反向投影行，统一走
    readable_facts（F09）。member_ids 缺省查询时点成员；考试维度端点
    传考试时点成员（F08）。

    返回 [(person_id, score, grade_score, source_domain, class_label)]，
    person_id 为 teaching 域身份；T 域已有同场事实（值同或值异均然）
    时保留 T 行、不投影、不附字段（teaching 侧不携带 shared_conflict）。
    空成员 → 空列表（合法空态）。"""
    if member_ids is None:
        member_ids = ctx.member_person_ids
    labels = _class_labels(db, class_ids)
    rows: List[Tuple[int, Optional[float], Optional[float], str, Optional[str]]] = []
    for e in q.readable_facts(
        db, ctx, exam_name, member_ids=member_ids, class_ids=class_ids
    ):
        rows.append(
            (
                e.person_id,
                e.fact.score,
                _grade_score_of(e.fact),
                e.source_domain,
                labels.get(e.class_ref_id),
            )
        )
    return rows


def _resolve_homeroom_ctx(db: Session, teacher_id: int, academic_year_id, class_id, term_id):
    """homeroom 分析端点的作用域解析（与 students.py 画像同模式：
    class_id 可选，缺省取教师绑定班；非绑定班 404）。"""
    if class_id is not None:
        q.check_admin_class_exists(db, class_id)
    return resolve_workspace_context(
        db,
        teacher_id,
        "homeroom",
        {"academic_year_id": academic_year_id, "term_id": term_id, "class_id": class_id},
    )


def _homeroom_grade(db: Session, ctx: WorkspaceContext) -> int:
    row = (
        db.query(AdministrativeClass.grade)
        .filter(AdministrativeClass.id.in_(list(ctx.class_ids)))
        .first()
    )
    if row is None:
        raise ResourceOutOfScope("homeroom class not found")
    return int(row[0])


def _rank_metric_options(grade: int, mode: str) -> List[dict]:
    if grade == 1:
        return [
            *[
                {"value": f"subject:{subject}", "label": subject, "kind": "subject_percentile"}
                for subject in ALL_SUBJECTS
            ],
            *[
                {"value": f"total:{total_type}", "label": f"{total_type}总分", "kind": "total_rank"}
                for total_type in ("主三门", "五门")
            ],
        ]
    options = [
        {"value": f"subject:{subject}", "label": subject, "kind": "subject_percentile"}
        for subject in BASE_SUBJECTS
    ]
    if mode == "frequency":
        options.extend(
            {
                "value": f"subject_grade:{subject}",
                "label": f"{subject}等级分",
                "kind": "subject_grade_score",
            }
            for subject in ELECTIVE_SUBJECTS
        )
    options.extend(
        {"value": f"total:{total_type}", "label": f"{total_type}总分", "kind": "total_rank"}
        for total_type in ("主三门", "3+3")
    )
    return options


def _rank_metric_meta(grade: int, metric: str, mode: str) -> dict:
    for option in _rank_metric_options(grade, mode):
        if option["value"] == metric:
            source, key = metric.split(":", 1)
            return {**option, "source": source, "key": key}
    raise InvalidScopeParam(
        "unsupported rank metric for grade", details={"metric": metric, "grade": grade}
    )


def _normalized_percentile(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    number = float(value)
    if number > 1:
        number /= 100
    return min(max(number, 0), 1)


def _percentile_bin(value: Optional[float]) -> Optional[str]:
    percentile = _normalized_percentile(value)
    if percentile is None:
        return None
    for key, _label, lower, upper in PERCENTILE_BINS:
        if percentile <= upper and (percentile > lower or lower == 0):
            return key
    return PERCENTILE_BINS[-1][0]


def _rank_bin(rank: Optional[int]) -> Optional[str]:
    if rank is None or rank < 1:
        return None
    start = ((int(rank) - 1) // 40) * 40 + 1
    return f"r{start}_{start + 39}"


def _rank_bin_label(key: str) -> str:
    start, end = key.removeprefix("r").split("_")
    return f"{start}–{end}名"


def _distribution_total_types(grade: int) -> Tuple[str, ...]:
    return ("主三门", "五门", "九门") if grade == 1 else ("主三门", "3+3")


# ────────────────────── homeroom：单场统计（§2.1） ──────────────────────


@router.get(
    "/homeroom/analysis/exams/{exam_name}/stats",
    response_model=HomeroomExamStatsResponse,
)
@domain_endpoint
def homeroom_exam_stats(
    exam_name: str,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> HomeroomExamStatsResponse:
    teacher_id = current_teacher_id(db)
    ctx = _resolve_homeroom_ctx(db, teacher_id, academic_year_id, class_id, term_id)
    if not _exam_exists_for_homeroom(db, ctx, exam_name):
        raise ResourceOutOfScope(
            "exam not found in homeroom domain", details={"exam_name": exam_name}
        )
    # F08：按考试发生时名册解析成员（考后离班/入班不改写历史人群）；
    # F09：统一可读事实口径（本域事实 + 经门投影行；冲突保留本域值，
    # T-only 科目经投影进入 subjects）。
    member_ids = _exam_member_ids(db, ctx, exam_name)
    entries = q.readable_facts(db, ctx, exam_name, member_ids=member_ids)
    facts = [e.fact for e in entries]
    by_subject: Dict[str, List] = {}
    by_total: Dict[str, List] = {}
    for f in facts:
        if f.total_type is None:
            by_subject.setdefault(f.subject, []).append(f)
        else:
            by_total.setdefault(f.total_type, []).append(f)

    subjects = []
    totals = []
    small_sample = False
    for subject in sorted(by_subject):
        valid = [f.score for f in by_subject[subject] if f.score is not None]
        if len(valid) < 5:
            small_sample = True
        subjects.append(
            HomeroomSubjectStat(
                subject=subject,
                avg=_avg(valid),
                max=max(valid) if valid else None,
                min=min(valid) if valid else None,
                valid_count=len(valid),
                missing_count=len(by_subject[subject]) - len(valid),
                score_basis="raw",
            )
        )
    for total_type in sorted(by_total):
        valid = [f.score for f in by_total[total_type] if f.score is not None]
        if len(valid) < 5:
            small_sample = True
        ranks = [
            f.xueji_rank if f.xueji_rank is not None else f.grade_rank
            for f in by_total[total_type]
            if f.score is not None
        ]
        ranks = [r for r in ranks if r is not None]
        totals.append(
            HomeroomTotalStat(
                total_type=total_type,
                avg=_avg(valid),
                max=max(valid) if valid else None,
                min=min(valid) if valid else None,
                valid_count=len(valid),
                rank_min=min(ranks) if ranks else None,
                rank_max=max(ranks) if ranks else None,
            )
        )
    return HomeroomExamStatsResponse(
        metadata=_analysis_metadata(
            ctx,
            "exam",
            fact_revisions=[f.data_revision for f in facts],
        ),
        subjects=subjects,
        totals=totals,
        cohort_size=len(member_ids),
        small_sample=small_sample,
    )


@router.get(
    "/homeroom/analysis/exams/{exam_name}/class-averages",
    response_model=HomeroomClassAveragesResponse,
)
@domain_endpoint
def homeroom_class_averages(
    exam_name: str,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> HomeroomClassAveragesResponse:
    """读取随考试导入的全年级班级均分表，不用本班学生成绩反推。"""
    ctx = _resolve_homeroom_ctx(
        db, current_teacher_id(db), academic_year_id, class_id, term_id
    )
    admin_class = db.get(AdministrativeClass, ctx.class_ids[0])
    records = (
        db.query(WorkspaceClassAverage)
        .filter(
            WorkspaceClassAverage.data_domain == "homeroom",
            WorkspaceClassAverage.academic_year_id == ctx.academic_year_id,
            WorkspaceClassAverage.exam_name == exam_name,
            WorkspaceClassAverage.grade == admin_class.grade,
        )
        .all()
    )

    subject_keys = {
        key for record in records for key in (record.subject_averages or {}).keys()
    }
    total_keys = {
        key for record in records for key in (record.total_averages or {}).keys()
    }
    preferred_subjects = ALL_SUBJECTS + [
        f"{subject}_{suffix}"
        for subject in ELECTIVE_SUBJECTS
        for suffix in ("原始", "等级")
    ]
    subjects = [key for key in preferred_subjects if key in subject_keys]
    subjects.extend(sorted(subject_keys - set(subjects)))
    preferred_totals = ["主三门", "五门", "九门", "+3", "3+3"]
    total_types = [key for key in preferred_totals if key in total_keys]
    total_types.extend(sorted(total_keys - set(total_types)))

    ranks_by_total = {}
    for total_type in total_types:
        pairs = [
            (record.id, (record.total_averages or {}).get(total_type))
            for record in records
        ]
        # 旧均分表会用整列 0 表示该口径本场不可用。保留原始 0 供核对，
        # 但不把所有班都编造成并列第 1。
        if not any(value is not None and value > 0 for _, value in pairs):
            ranks_by_total[total_type] = {record_id: None for record_id, _ in pairs}
        else:
            ranks_by_total[total_type] = _rank_map(pairs)
    class_type_order = {"平行班": 0, "平行": 0, "实验班": 1, "实验": 1}
    records.sort(
        key=lambda record: (
            class_type_order.get(record.class_type or "", 9),
            record.class_type or "",
            record.class_num,
        )
    )
    return HomeroomClassAveragesResponse(
        metadata=_analysis_metadata(
            ctx,
            "exam",
            fact_revisions=[record.data_revision for record in records],
        ),
        exam_name=exam_name,
        grade=admin_class.grade,
        subjects=subjects,
        total_types=total_types,
        current_class_num=admin_class.class_num,
        rows=[
            HomeroomClassAverageRow(
                class_type=record.class_type,
                class_num=record.class_num,
                teacher_name=record.teacher_name,
                subjects={key: (record.subject_averages or {}).get(key) for key in subjects},
                totals={key: (record.total_averages or {}).get(key) for key in total_types},
                total_ranks={
                    key: ranks_by_total[key].get(record.id) for key in total_types
                },
            )
            for record in records
        ],
    )


# ────────────────────── homeroom：单场学生表（§2.1） ──────────────────────


@router.get(
    "/homeroom/analysis/exams/{exam_name}/students",
    response_model=HomeroomExamStudentsResponse,
)
@domain_endpoint
def homeroom_exam_students(
    exam_name: str,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> HomeroomExamStudentsResponse:
    teacher_id = current_teacher_id(db)
    ctx = _resolve_homeroom_ctx(db, teacher_id, academic_year_id, class_id, term_id)
    if not _exam_exists_for_homeroom(db, ctx, exam_name):
        raise ResourceOutOfScope(
            "exam not found in homeroom domain", details={"exam_name": exam_name}
        )
    # F08：行集合（成员）按考试发生时名册解析；姓名/别名仍是展示投影，
    # 不受成员时点影响。F09：事实走统一可读口径（冲突注记随本域行返回）。
    member_ids = _exam_member_ids(db, ctx, exam_name)
    entries = q.readable_facts(db, ctx, exam_name, member_ids=member_ids)
    # 空态：该场本班无任何可读行 → students=[]（契约 §2.1/任务书）
    if not entries:
        return HomeroomExamStudentsResponse(metadata=_analysis_metadata(ctx, "exam"), students=[])

    subject_keys = sorted(
        {e.fact.subject for e in entries if e.fact.total_type is None}
    )
    total_keys = sorted(
        {e.fact.total_type for e in entries if e.fact.total_type is not None}
    )
    by_person: Dict[int, Dict[str, Optional[float]]] = {}
    totals_by_person: Dict[int, Dict[str, Optional[float]]] = {}
    conflicts_by_person: Dict[int, dict] = {}
    for e in entries:
        if e.fact.total_type is None:
            by_person.setdefault(e.person_id, {})[e.fact.subject] = e.fact.score
        else:
            totals_by_person.setdefault(e.person_id, {})[e.fact.total_type] = e.fact.score
        if e.conflict is not None:
            conflicts_by_person[e.person_id] = e.conflict

    alias_map = q.aliases_for(db, member_ids, "homeroom", ctx.academic_year_id)
    name_map = q.names_for(db, member_ids)
    students = []
    for person_id in member_ids:
        entry = HomeroomAnalysisStudent(
            person_id=person_id,
            name=name_map.get(person_id),
            alias=alias_map.get(person_id),
            scores={
                subject: by_person.get(person_id, {}).get(subject)
                for subject in subject_keys
            },
            totals={
                total_type: totals_by_person.get(person_id, {}).get(total_type)
                for total_type in total_keys
            },
        )
        if person_id in conflicts_by_person:
            entry.shared_conflicts = conflicts_by_person[person_id]
        students.append(entry)

    return HomeroomExamStudentsResponse(
        metadata=_analysis_metadata(
            ctx,
            "exam",
            fact_revisions=[e.fact.data_revision for e in entries],
        ),
        students=students,
    )


@router.get(
    "/homeroom/analysis/rank-metrics",
    response_model=HomeroomRankMetricsResponse,
)
@domain_endpoint
def homeroom_rank_metrics(
    mode: str = "frequency",
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> HomeroomRankMetricsResponse:
    if mode not in {"frequency", "range"}:
        raise InvalidScopeParam("mode must be frequency or range", details={"mode": mode})
    ctx = _resolve_homeroom_ctx(
        db, current_teacher_id(db), academic_year_id, class_id, term_id
    )
    grade = _homeroom_grade(db, ctx)
    return HomeroomRankMetricsResponse(
        metadata=_analysis_metadata(ctx, "current"),
        grade=grade,
        metrics=[HomeroomRankMetric(**row) for row in _rank_metric_options(grade, mode)],
    )


@router.get(
    "/homeroom/analysis/rank-frequency",
    response_model=HomeroomRankFrequencyResponse,
)
@domain_endpoint
def homeroom_rank_frequency(
    metric: str,
    exam_names: str,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> HomeroomRankFrequencyResponse:
    ctx = _resolve_homeroom_ctx(
        db, current_teacher_id(db), academic_year_id, class_id, term_id
    )
    grade = _homeroom_grade(db, ctx)
    meta = _rank_metric_meta(grade, metric, "frequency")
    selected_names = list(dict.fromkeys(name.strip() for name in exam_names.split(",") if name.strip()))
    if not selected_names:
        raise InvalidScopeParam("at least one exam is required")

    if meta["kind"] == "subject_grade_score":
        bins = [
            HomeroomRankFrequencyBin(
                key=f"g{score}",
                label=f"{score}分",
                separator_after=score in GRADE_SCORE_SEPARATOR_AFTER,
            )
            for score in GRADE_SCORE_VALUES
        ]
    elif meta["kind"] == "subject_percentile":
        bins = [
            HomeroomRankFrequencyBin(key=key, label=label)
            for key, label, _lower, _upper in PERCENTILE_BINS
        ]
    else:
        bins = []

    rows: Dict[int, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    person_ids = set()
    revisions = []
    exam_rows = []
    rank_keys = set()
    for exam_name in selected_names:
        if not _exam_exists_for_homeroom(db, ctx, exam_name):
            raise ResourceOutOfScope(
                "exam not found in homeroom domain", details={"exam_name": exam_name}
            )
        member_ids = _exam_member_ids(db, ctx, exam_name)
        entries = q.readable_facts(db, ctx, exam_name, member_ids=member_ids)
        exam_rows.append(
            HomeroomRankFrequencyExam(
                exam_name=exam_name,
                exam_date=next(
                    (q.display_exam_date(entry.fact) for entry in entries if q.display_exam_date(entry.fact)),
                    None,
                ),
            )
        )
        for entry in entries:
            fact = entry.fact
            key = None
            if meta["kind"] == "total_rank" and fact.total_type == meta["key"]:
                rank = fact.xueji_rank if fact.xueji_rank is not None else fact.grade_rank
                key = _rank_bin(rank)
                if key:
                    rank_keys.add(key)
            elif meta["kind"] == "subject_percentile" and fact.subject == meta["key"]:
                key = _percentile_bin(fact.grade_percentile)
            elif meta["kind"] == "subject_grade_score" and fact.subject == meta["key"]:
                grade_score = _grade_score_of(fact)
                rounded = int(round(grade_score)) if grade_score is not None else None
                key = f"g{rounded}" if rounded in GRADE_SCORE_VALUES else None
            if key is None:
                continue
            rows[entry.person_id][key] += 1
            person_ids.add(entry.person_id)
            revisions.append(fact.data_revision)

    if meta["kind"] == "total_rank":
        bins = [
            HomeroomRankFrequencyBin(key=key, label=_rank_bin_label(key))
            for key in sorted(rank_keys, key=lambda value: int(value.split("_")[0][1:]))
        ]
    names = q.names_for(db, person_ids)
    aliases = q.aliases_for(db, person_ids, "homeroom", ctx.academic_year_id)
    students = [
        HomeroomRankFrequencyStudent(
            person_id=person_id,
            name=names.get(person_id),
            alias=aliases.get(person_id),
            counts={bin_row.key: counts.get(bin_row.key, 0) for bin_row in bins},
            total_count=sum(counts.values()),
        )
        for person_id, counts in rows.items()
    ]
    students.sort(
        key=lambda student: (
            -sum((index + 1) * student.counts.get(bin_row.key, 0) for index, bin_row in enumerate(bins)),
            student.name or "",
        )
    )
    exam_rows.sort(key=lambda exam: (exam.exam_date is not None, exam.exam_date or "", exam.exam_name))
    return HomeroomRankFrequencyResponse(
        metadata=_analysis_metadata(ctx, "exam", fact_revisions=revisions),
        metric=metric,
        metric_label=meta["label"],
        metric_kind=meta["kind"],
        exams=exam_rows,
        bins=bins,
        students=students,
        metric_note="单科按年级百分位五等分；选考科目按精确等级分统计；总分按学籍/年级名次每40名一档统计。",
    )


@router.get(
    "/homeroom/analysis/exams/{exam_name}/rank-range",
    response_model=HomeroomRankRangeResponse,
)
@domain_endpoint
def homeroom_rank_range(
    exam_name: str,
    metric: str,
    rank_min: int = 1,
    rank_max: int = 100,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> HomeroomRankRangeResponse:
    if rank_min < 1 or rank_max < rank_min:
        raise InvalidScopeParam(
            "invalid rank range", details={"rank_min": rank_min, "rank_max": rank_max}
        )
    ctx = _resolve_homeroom_ctx(
        db, current_teacher_id(db), academic_year_id, class_id, term_id
    )
    if not _exam_exists_for_homeroom(db, ctx, exam_name):
        raise ResourceOutOfScope(
            "exam not found in homeroom domain", details={"exam_name": exam_name}
        )
    meta = _rank_metric_meta(_homeroom_grade(db, ctx), metric, "range")
    member_ids = _exam_member_ids(db, ctx, exam_name)
    entries = q.readable_facts(db, ctx, exam_name, member_ids=member_ids)
    cohort_size = max(
        (
            fact.xueji_rank if fact.xueji_rank is not None else fact.grade_rank
            for fact in (entry.fact for entry in entries)
            if fact.total_type == "主三门"
            and (fact.xueji_rank is not None or fact.grade_rank is not None)
        ),
        default=None,
    )
    matched = []
    scores = []
    for entry in entries:
        fact = entry.fact
        if meta["kind"] == "total_rank":
            if fact.total_type != meta["key"]:
                continue
            year_rank = fact.xueji_rank if fact.xueji_rank is not None else fact.grade_rank
        else:
            if fact.subject != meta["key"]:
                continue
            year_rank = fact.grade_rank
            if year_rank is None and cohort_size:
                percentile = _normalized_percentile(fact.grade_percentile)
                year_rank = max(1, math.ceil(percentile * cohort_size)) if percentile is not None else None
        scores.append((entry.person_id, fact.score))
        if year_rank is not None and rank_min <= year_rank <= rank_max:
            matched.append((entry.person_id, fact.score, year_rank))
    class_ranks = _rank_map(scores)
    names = q.names_for(db, member_ids)
    aliases = q.aliases_for(db, member_ids, "homeroom", ctx.academic_year_id)
    students = [
        HomeroomRankRangeStudent(
            person_id=person_id,
            name=names.get(person_id),
            alias=aliases.get(person_id),
            score=score,
            class_rank=class_ranks.get(person_id),
            year_rank=year_rank,
        )
        for person_id, score, year_rank in matched
    ]
    students.sort(key=lambda student: (student.year_rank or 10**9, student.name or ""))
    return HomeroomRankRangeResponse(
        metadata=_analysis_metadata(
            ctx, "exam", fact_revisions=[entry.fact.data_revision for entry in entries]
        ),
        exam_name=exam_name,
        metric=metric,
        metric_label=meta["label"],
        metric_kind=meta["kind"],
        rank_min=rank_min,
        rank_max=rank_max,
        students=students,
        metric_note="总分使用已有学籍/年级名次；单科使用年级百分位按该场主三门名次范围换算。",
    )


@router.get(
    "/homeroom/analysis/exams/{exam_name}/rank-distribution",
    response_model=HomeroomRankDistributionResponse,
)
@domain_endpoint
def homeroom_rank_distribution(
    exam_name: str,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> HomeroomRankDistributionResponse:
    ctx = _resolve_homeroom_ctx(
        db, current_teacher_id(db), academic_year_id, class_id, term_id
    )
    if not _exam_exists_for_homeroom(db, ctx, exam_name):
        raise ResourceOutOfScope(
            "exam not found in homeroom domain", details={"exam_name": exam_name}
        )
    grade = _homeroom_grade(db, ctx)
    total_types = _distribution_total_types(grade)
    member_ids = _exam_member_ids(db, ctx, exam_name)
    entries = q.readable_facts(db, ctx, exam_name, member_ids=member_ids)
    counts = {total_type: defaultdict(int) for total_type in total_types}
    max_rank = 0
    for entry in entries:
        fact = entry.fact
        if fact.total_type not in counts:
            continue
        rank = fact.xueji_rank if fact.xueji_rank is not None else fact.grade_rank
        key = _rank_bin(rank)
        if key is None:
            continue
        counts[fact.total_type][key] += 1
        max_rank = max(max_rank, int(rank))
    bin_rows = []
    if max_rank > 0:
        for start in range(1, ((max_rank - 1) // 40 + 1) * 40 + 1, 40):
            key = f"r{start}_{start + 39}"
            bin_rows.append(HomeroomRankFrequencyBin(key=key, label=f"{start}–{start + 39}名"))
    return HomeroomRankDistributionResponse(
        metadata=_analysis_metadata(
            ctx, "exam", fact_revisions=[entry.fact.data_revision for entry in entries]
        ),
        exam_name=exam_name,
        grade=grade,
        bins=bin_rows,
        series=[
            HomeroomRankDistributionSeries(
                total_type=total_type,
                counts={bin_row.key: counts[total_type].get(bin_row.key, 0) for bin_row in bin_rows},
            )
            for total_type in total_types
        ],
        metric_note="按学籍/年级名次每40名一档；缺少真实名次的总分口径保留图例但不生成柱子。",
    )


@router.get(
    "/homeroom/analysis/exams/{exam_name}/focus",
    response_model=HomeroomFocusResponse,
)
@domain_endpoint
def homeroom_exam_focus(
    exam_name: str,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> HomeroomFocusResponse:
    """恢复旧班主任版完整重点关注：主三门学籍名次判定进退步、
    波动、临界/薄弱与稳定优秀；单科百分位判定严重偏科。"""
    teacher_id = current_teacher_id(db)
    ctx = _resolve_homeroom_ctx(db, teacher_id, academic_year_id, class_id, term_id)
    if not _exam_exists_for_homeroom(db, ctx, exam_name):
        raise ResourceOutOfScope(
            "exam not found in homeroom domain", details={"exam_name": exam_name}
        )

    member_ids = _exam_member_ids(db, ctx, exam_name)
    entries = q.readable_facts(db, ctx, exam_name, member_ids=member_ids)
    history_entries = q.readable_facts(db, ctx, None, member_ids=member_ids)
    facts_by_person: Dict[int, List[ScoreFact]] = {}
    for entry in entries:
        facts_by_person.setdefault(entry.person_id, []).append(entry.fact)

    band = get_band_config(db)
    names = q.names_for(db, member_ids)
    aliases = q.aliases_for(db, member_ids, "homeroom", ctx.academic_year_id)
    focus: List[HomeroomFocusStudent] = []
    for person_id in member_ids:
        facts = facts_by_person.get(person_id, [])
        total = next((f for f in facts if f.total_type == "主三门"), None)
        if total is None:
            continue
        rank = total.xueji_rank if total.xueji_rank is not None else total.grade_rank
        issues: List[str] = []

        historical_totals = {
            e.fact.exam_name: e.fact
            for e in history_entries
            if e.person_id == person_id and e.fact.total_type == "主三门"
        }
        ordered_totals = sorted(
            historical_totals.values(),
            key=lambda fact: (
                q.display_exam_date(fact) is not None,
                q.display_exam_date(fact) or "",
                fact.exam_name,
            ),
        )
        selected_index = next(
            (i for i, fact in enumerate(ordered_totals) if fact.exam_name == exam_name),
            None,
        )
        if selected_index is not None:
            ordered_totals = ordered_totals[: selected_index + 1]
        ranks = [
            fact.xueji_rank if fact.xueji_rank is not None else fact.grade_rank
            for fact in ordered_totals
        ]
        ranks = [value for value in ranks if value is not None]
        previous_rank = ranks[-2] if len(ranks) >= 2 else None
        rank_change = previous_rank - rank if previous_rank is not None and rank is not None else None
        rank_range = max(ranks) - min(ranks) if ranks else None

        if rank_change is not None and rank_change >= PROGRESS_RANK_THRESHOLD:
            issues.append("明显进步")
        if rank_change is not None and rank_change <= -PROGRESS_RANK_THRESHOLD:
            issues.append("明显退步")
        if (
            len(ranks) >= 3
            and rank_range is not None
            and rank_range >= VOLATILITY_RANK_THRESHOLD
        ):
            issues.append("波动风险")
        if rank is not None and band["critical_min"] <= rank <= band["critical_max"]:
            issues.append("临界段")
        if rank is not None and rank >= band["weak_min"]:
            issues.append("薄弱段")

        weak_subjects: List[str] = []
        if total.grade_percentile is not None:
            weak_subjects = sorted(
                {
                    f.subject
                    for f in facts
                    if f.subject
                    and f.total_type is None
                    and f.grade_percentile is not None
                    and f.grade_percentile - total.grade_percentile
                    >= SUBJECT_WEAKNESS_PCT_DIFF
                }
            )
        issues.extend(f"严重偏科（{subject}）" for subject in weak_subjects)
        if (
            rank is not None
            and rank <= band["high_score_max"]
            and (rank_range is None or rank_range < VOLATILITY_RANK_THRESHOLD)
        ):
            issues.append("稳定优秀")
        if issues:
            focus.append(
                HomeroomFocusStudent(
                    person_id=person_id,
                    name=names.get(person_id),
                    alias=aliases.get(person_id),
                    total_score=total.score,
                    xueji_rank=rank,
                    issues=issues,
                    weak_subjects=weak_subjects,
                    previous_rank=previous_rank,
                    rank_change=rank_change,
                    rank_range=rank_range,
                    exam_count=len(ranks),
                )
            )

    focus.sort(
        key=lambda row: (
            row.xueji_rank is None,
            row.xueji_rank if row.xueji_rank is not None else 999999,
            row.name or "",
        )
    )
    return HomeroomFocusResponse(
        metadata=_analysis_metadata(
            ctx,
            "exam",
            fact_revisions=[entry.fact.data_revision for entry in entries],
        ),
        exam_name=exam_name,
        config=FocusBandConfig(
            high_score_max=band["high_score_max"],
            critical_min=band["critical_min"],
            critical_max=band["critical_max"],
            weak_min=band["weak_min"],
            subject_weakness_diff=SUBJECT_WEAKNESS_PCT_DIFF,
            progress_rank_threshold=PROGRESS_RANK_THRESHOLD,
            volatility_rank_threshold=VOLATILITY_RANK_THRESHOLD,
        ),
        students=focus,
    )


# ────────────────────── homeroom：跨学年趋势（§2.1，E03） ──────────────────────


@router.get("/homeroom/analysis/trends", response_model=TrendsResponse)
@domain_endpoint
def homeroom_trends(
    person_id: int,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> TrendsResponse:
    """跨学年分段趋势（E03）：person 必须在当前 homeroom 作用域；
    读取该人 H 域事实 + 经 §1.4.1 投影门的 teaching 投影行（仅当前
    学年、H 域无该场才补条目；值不同保留 H 值不投影），按学年分组输出。
    响应结构按学年分组即阻断跨年连算，不输出任何跨学年同比字段。"""
    teacher_id = current_teacher_id(db)
    ctx = _resolve_homeroom_ctx(db, teacher_id, academic_year_id, class_id, term_id)
    if person_id not in ctx.member_person_ids:
        raise ResourceOutOfScope(
            "person not in current homeroom scope", details={"person_id": person_id}
        )

    # 按人取 H 域全部学年事实（个人成绩史；identity 唯一锁定本人，无他人数据）
    facts = (
        db.query(ScoreFact)
        .filter(
            ScoreFact.data_domain == "homeroom",
            ScoreFact.identity_id == person_id,
        )
        .all()
    )
    projected: List[ScoreFact] = []
    if ctx.link_id is not None:
        link = db.get(HomeroomTeachingLink, ctx.link_id)
        h_keys = {(f.subject, f.exam_name) for f in facts if f.total_type is None}
        for t_fact, _h_id in q.gated_teaching_facts_for_homeroom(
            db, ctx, link, h_ids=[person_id]
        ):
            # H 域已有同场（值同或值异）→ 保留 H 条目，不重复/不替换
            if (t_fact.subject, t_fact.exam_name) in h_keys:
                continue
            projected.append(t_fact)

    def _point(f: ScoreFact) -> TrendExamPoint:
        if f.total_type is not None:
            rank = f.xueji_rank if f.xueji_rank is not None else f.grade_rank
            rank_basis = "school" if rank is not None else None
        else:
            percentile = _normalized_percentile(f.grade_percentile)
            rank = round(percentile * 100, 2) if percentile is not None else None
            rank_basis = "grade_percentile" if rank is not None else None
        return TrendExamPoint(
            exam_name=f.exam_name,
            exam_date=q.display_exam_date(f),
            score=f.score,
            grade_score=_grade_score_of(f),
            rank=rank,
            rank_basis=rank_basis,
        )

    years_raw: Dict[int, dict] = {}
    for f in facts:
        group = years_raw.setdefault(f.academic_year_id, {"subjects": {}, "totals": {}})
        if f.total_type is None:
            group["subjects"].setdefault(f.subject, []).append(_point(f))
        else:
            group["totals"].setdefault(f.total_type, []).append(_point(f))
    for f in projected:  # 投影行归属当前学年（gated 门按 ctx 学年取数）
        group = years_raw.setdefault(ctx.academic_year_id, {"subjects": {}, "totals": {}})
        group["subjects"].setdefault(f.subject, []).append(_point(f))

    ay_rows = (
        db.query(AcademicYear)
        .filter(AcademicYear.id.in_(list(years_raw)))
        .order_by(AcademicYear.start_date.asc(), AcademicYear.id.asc())
        .all()
    )
    years = [
        TrendYearGroup(
            academic_year_id=ay.id,
            academic_year_name=ay.name,
            subjects=years_raw[ay.id]["subjects"],
            totals=years_raw[ay.id]["totals"],
        )
        for ay in ay_rows
    ]
    return TrendsResponse(
        person_id=person_id,
        name=q.names_for(db, [person_id]).get(person_id),
        years=years,
    )


# ────────────────────── homeroom：段位分布（§2.1，v2.1/F10） ──────────────────────


@router.get("/homeroom/analysis/bands", response_model=BandsResponse)
@domain_endpoint
def homeroom_bands(
    exam_name: str,
    subject: Optional[str] = None,
    metric: str = "score",
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> BandsResponse:
    """段位分布（v2.1/F10）：段位阈值口径为年级名次（AnalysisConfig 的
    400/500 是名次阈值）。旧班主任总分事实带学籍/年级名次时按名次计算；
    单科或当前范围没有名次事实时返回 409，绝不把名次阈值当分数比较。"""
    if metric not in ("score", "total"):
        raise InvalidScopeParam(
            "metric must be 'score' or 'total'", details={"param": "metric"}
        )
    if subject is None or not subject.strip():
        raise InvalidScopeParam(
            "subject is required (score 模式为学科名，total 模式为 total_type)",
            details={"param": "subject"},
        )
    subject = subject.strip()
    teacher_id = current_teacher_id(db)
    ctx = _resolve_homeroom_ctx(db, teacher_id, academic_year_id, class_id, term_id)
    if not _exam_exists_for_homeroom(db, ctx, exam_name):
        raise ResourceOutOfScope(
            "exam not found in homeroom domain", details={"exam_name": exam_name}
        )
    if metric != "total":
        raise BandsNotComputable(
            "段位阈值口径为年级名次，当前范围无名次数据，不可计算"
        )

    member_ids = _exam_member_ids(db, ctx, exam_name)
    entries = q.readable_facts(db, ctx, exam_name, member_ids=member_ids)
    ranked = []
    for entry in entries:
        fact = entry.fact
        if fact.total_type != subject:
            continue
        rank = fact.xueji_rank if fact.xueji_rank is not None else fact.grade_rank
        if rank is not None:
            ranked.append((entry.person_id, rank))
    if not ranked:
        raise BandsNotComputable(
            "段位阈值口径为年级名次，当前范围无名次数据，不可计算"
        )

    config = get_band_config(db)
    definitions = [
        (f"高分段（1–{config['high_score_max']}名）", lambda rank: rank <= config["high_score_max"]),
        (
            f"临界段（{config['critical_min']}–{config['critical_max']}名）",
            lambda rank: config["critical_min"] <= rank <= config["critical_max"],
        ),
        (f"薄弱段（{config['weak_min']}名起）", lambda rank: rank >= config["weak_min"]),
    ]
    bands = [
        {
            "label": label,
            "count": len(students),
            "students": students,
        }
        for label, matches in definitions
        for students in [[person_id for person_id, rank in ranked if matches(rank)]]
    ]
    return BandsResponse(
        metadata=_analysis_metadata(
            ctx,
            "exam",
            fact_revisions=[entry.fact.data_revision for entry in entries],
        ),
        total_type=subject,
        bands=bands,
    )


# ────────────────────── teaching：单场统计（§2.2） ──────────────────────


@router.get(
    "/teaching/analysis/exams/{exam_name}/stats",
    response_model=TeachingExamStatsResponse,
)
@domain_endpoint
def teaching_exam_stats(
    exam_name: str,
    teaching_class_id: Optional[int] = None,
    academic_year_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> TeachingExamStatsResponse:
    teacher_id = current_teacher_id(db)
    params, subject, class_ids = q.build_teaching_params(
        db, academic_year_id, teaching_class_id, None, term_id
    )
    ctx = resolve_workspace_context(db, teacher_id, "teaching", params)
    if not _exam_exists_for_teaching(db, ctx.academic_year_id, exam_name):
        raise ResourceOutOfScope(
            "exam not found in teaching domain", details={"exam_name": exam_name}
        )
    member_ids = _exam_member_ids(db, ctx, exam_name)
    rows = _teaching_member_rows(db, ctx, class_ids, subject, exam_name, member_ids)
    valid = [score for _, score, _, _, _ in rows if score is not None]
    rank_values = [
        rank
        for rank in _rank_map([(pid, score) for pid, score, _, _, _ in rows]).values()
        if rank is not None
    ]
    return TeachingExamStatsResponse(
        metadata=_analysis_metadata(
            ctx,
            "exam",
            teaching_class_id=class_ids[0] if teaching_class_id is not None else None,
        ),
        subject=subject,
        avg=_avg(valid),
        max=max(valid) if valid else None,
        min=min(valid) if valid else None,
        valid_count=len(valid),
        missing_count=len(rows) - len(valid),
        rank_min=min(rank_values) if rank_values else None,
        rank_max=max(rank_values) if rank_values else None,
        score_basis="raw",
        cohort_size=len(member_ids),
        small_sample=len(valid) < 5,
    )


# ────────────────────── teaching：单场学生表（§2.2） ──────────────────────


@router.get(
    "/teaching/analysis/exams/{exam_name}/students",
    response_model=TeachingExamStudentsResponse,
)
@domain_endpoint
def teaching_exam_students(
    exam_name: str,
    teaching_class_id: Optional[int] = None,
    academic_year_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> TeachingExamStudentsResponse:
    teacher_id = current_teacher_id(db)
    params, subject, class_ids = q.build_teaching_params(
        db, academic_year_id, teaching_class_id, None, term_id
    )
    ctx = resolve_workspace_context(db, teacher_id, "teaching", params)
    if not _exam_exists_for_teaching(db, ctx.academic_year_id, exam_name):
        raise ResourceOutOfScope(
            "exam not found in teaching domain", details={"exam_name": exam_name}
        )
    member_ids = _exam_member_ids(db, ctx, exam_name)
    rows = _teaching_member_rows(db, ctx, class_ids, subject, exam_name, member_ids)
    ranks = _rank_map([(pid, score) for pid, score, _, _, _ in rows])
    name_map = q.names_for(db, [pid for pid, _, _, _, _ in rows])
    students = [
        TeachingAnalysisStudent(
            person_id=pid,
            name=name_map.get(pid),
            class_label=class_label,
            score=score,
            grade_score=grade_score,
            rank=ranks[pid],
            source_domain=source_domain,
        )
        for pid, score, grade_score, source_domain, class_label in rows
    ]
    students.sort(key=lambda s: (s.rank is None, s.rank or 0, s.person_id))
    return TeachingExamStudentsResponse(
        metadata=_analysis_metadata(
            ctx,
            "exam",
            teaching_class_id=class_ids[0] if teaching_class_id is not None else None,
        ),
        students=students,
    )


# ────────────────────── teaching：班级对比（§2.2，E04） ──────────────────────


@router.get("/teaching/analysis/class-compare", response_model=ClassCompareResponse)
@domain_endpoint
def teaching_class_compare(
    exam_name: str,
    academic_year_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> ClassCompareResponse:
    """本班样本均分恒标 estimated（E04）：官方班均表未入库前不提供
    official，绝不拿班内样本冒充年级/官方口径。member_count 为该班
    参与该场考试的有效成员数（非 NULL）。成员按考试时点名册解析
    （F08 考试维度口径；响应为契约冻结结构、无 metadata 键）。"""
    teacher_id = current_teacher_id(db)
    params, subject, class_ids = q.build_teaching_params(
        db, academic_year_id, None, None, term_id
    )
    ctx = resolve_workspace_context(db, teacher_id, "teaching", params)
    if not _exam_exists_for_teaching(db, ctx.academic_year_id, exam_name):
        raise ResourceOutOfScope(
            "exam not found in teaching domain", details={"exam_name": exam_name}
        )
    member_ids = _exam_member_ids(db, ctx, exam_name)
    labels = _class_labels(db, class_ids)
    classes = []
    small_sample = False
    for tc_id in class_ids:
        rows = _teaching_member_rows(db, ctx, [tc_id], subject, exam_name, member_ids)
        valid = [score for _, score, _, _, _ in rows if score is not None]
        if len(valid) < 5:
            small_sample = True
        classes.append(
            ClassCompareEntry(
                teaching_class_id=tc_id,
                class_label=labels.get(tc_id, ""),
                member_count=len(valid),
                subject_avg=_avg(valid),
                score_basis="raw",
                source="estimated",
            )
        )
    return ClassCompareResponse(classes=classes, small_sample=small_sample)


@router.get(
    "/homeroom/analysis/weekly-focus",
    response_model=HomeroomWeeklyFocusResponse,
)
@domain_endpoint
def homeroom_weekly_focus(
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> HomeroomWeeklyFocusResponse:
    """班主任工作台本周关注名单（四维信号加权驱动）：
    1. 连续缺交预警（streak >= 2，权重 3 或 2）；
    2. 本周缺交激增（近 7 天缺交 >= 3 次且 >= 2 倍周均，权重 2）；
    3. 最近一次考试关注（临界段、薄弱段、严重偏科，权重 1）；
    4. 谈话跟进待办（未完成跟进事项，权重 2）。
    主要由缺交与待办信号驱动，无新考试也每天实时更新。
    """
    from app.api.homework import _is_pure_missing, _stats_excluded_ids, homework_warnings

    teacher_id = current_teacher_id(db)
    ctx = _resolve_homeroom_ctx(db, teacher_id, academic_year_id, class_id, term_id)
    class_id_val = ctx.class_ids[0]
    member_ids = list(ctx.member_person_ids)
    names = q.names_for(db, member_ids)

    today = date.today()
    week_start = today - timedelta(days=6)
    week_start_str = week_start.isoformat()
    today_str = today.isoformat()

    reasons_by_person: Dict[int, List[Dict[str, any]]] = defaultdict(list)

    def add_signal(pid: int, tag: str, weight: int):
        if pid in ctx.member_person_ids:
            reasons_by_person[pid].append({"tag": tag, "weight": weight})

    # ① 连续缺交预警
    try:
        hw_warn = homework_warnings(
            mode="homeroom",
            class_id=class_id_val,
            academic_year_id=ctx.academic_year_id,
            min_missing=1,
            min_streak=2,
            db=db,
        )
        for s in hw_warn.students:
            streak = s.current_streak or 0
            if streak >= 2:
                weight = 3 if streak >= 3 else 2
                subj = s.streak_subject or s.streak_homework_type or "作业"
                add_signal(s.person_id, f"连续缺交{streak}次（{subj}）", weight)
    except Exception:
        logger.exception("weekly-focus 连续缺交信号计算失败")

    # ② 本周缺交激增：只有纯缺交计入——考勤批次、迟到/没来、忘带行
    # 均不算缺交（与 homework_warnings 的 _is_pure_missing 同口径）。
    try:
        excluded_ids = _stats_excluded_ids(db, "homeroom", ctx.class_ids)
        submissions = (
            db.query(HomeworkSubmission, HomeworkAssignment)
            .join(HomeworkAssignment, HomeworkSubmission.assignment_id == HomeworkAssignment.id)
            .filter(
                HomeworkAssignment.data_domain == "homeroom",
                HomeworkAssignment.class_ref_id == class_id_val,
                HomeworkAssignment.academic_year_id == ctx.academic_year_id,
                HomeworkAssignment.status == "active",
                HomeworkAssignment.subject != "考勤",
                HomeworkSubmission.person_id.in_(member_ids),
                HomeworkSubmission.submission_status == "missing",
            )
            .all()
        )
        total_misses_by_pid: Dict[int, int] = defaultdict(int)
        week_misses_by_pid: Dict[int, int] = defaultdict(int)
        all_dates: List[date] = []
        for sub, assign in submissions:
            if sub.person_id in excluded_ids or not _is_pure_missing(sub):
                continue
            total_misses_by_pid[sub.person_id] += 1
            all_dates.append(assign.assigned_date)
            if week_start <= assign.assigned_date <= today:
                week_misses_by_pid[sub.person_id] += 1

        if all_dates:
            min_d = min(all_dates)
            max_d = max(today, max(all_dates))
            weeks_elapsed = max(1.0, (max_d - min_d).days / 7.0)
            for pid, wk in week_misses_by_pid.items():
                avg = total_misses_by_pid[pid] / weeks_elapsed
                if wk >= 3 and wk >= 2 * max(avg, 0.5):
                    add_signal(pid, f"本周缺交激增（{wk}次）", 2)
    except Exception:
        logger.exception("weekly-focus 本周缺交激增信号计算失败")

    # ③ 最近一次考试临界/薄弱/偏科
    try:
        latest_exam_fact = (
            db.query(ScoreFact.exam_name)
            .filter(
                ScoreFact.data_domain == "homeroom",
                ScoreFact.academic_year_id == ctx.academic_year_id,
                ScoreFact.class_ref_id == class_id_val,
            )
            .order_by(ScoreFact.exam_date.desc(), ScoreFact.id.desc())
            .first()
        )
        if latest_exam_fact and latest_exam_fact[0]:
            exam_name = latest_exam_fact[0]
            focus_res = homeroom_exam_focus(
                exam_name=exam_name,
                academic_year_id=ctx.academic_year_id,
                class_id=class_id_val,
                term_id=term_id,
                db=db,
            )
        for fs in focus_res.students:
            if fs.issues:
                for issue in fs.issues:
                    add_signal(fs.person_id, issue, 1)
    except Exception:
        logger.exception("weekly-focus 最近考试关注信号计算失败")

    # ④ 谈话跟进待办
    try:
        from app.api.students_mgmt import _human_notes_filter
        from sqlalchemy import or_

        notes = (
            db.query(WsStudentNote)
            .filter(
                WsStudentNote.data_domain == "homeroom",
                WsStudentNote.person_id.in_(member_ids),
                WsStudentNote.follow_up.isnot(None),
                WsStudentNote.follow_up != "",
                or_(
                    WsStudentNote.follow_up_done == False,
                    WsStudentNote.follow_up_done.is_(None),
                ),
                _human_notes_filter(),
            )
            .all()
        )
        for note in notes:
            add_signal(note.person_id, f"谈话跟进待办：{note.follow_up}", 2)
    except Exception:
        logger.exception("weekly-focus 谈话跟进待办信号计算失败")

    out_students: List[WeeklyFocusStudent] = []
    for pid, items in reasons_by_person.items():
        total_score = sum(i["weight"] for i in items)
        sorted_items = sorted(items, key=lambda x: -x["weight"])
        seen_tags = set()
        unique_tags = []
        for it in sorted_items:
            t = it["tag"]
            if t not in seen_tags:
                seen_tags.add(t)
                unique_tags.append(t)
        out_students.append(
            WeeklyFocusStudent(
                student_id=str(pid),
                name=names.get(pid, f"学生{pid}"),
                score=total_score,
                reasons=unique_tags,
            )
        )

    out_students.sort(key=lambda s: s.score, reverse=True)

    return HomeroomWeeklyFocusResponse(
        metadata=_analysis_metadata(ctx, "current"),
        class_id=class_id_val,
        week={"start": week_start_str, "end": today_str},
        students=out_students,
        total_count=len(out_students),
        note="合并连续缺交预警、本周缺交激增、最近考试临界/薄弱/偏科、谈话跟进待办。由缺交和待办信号驱动，无新考试也每天更新。",
    )
