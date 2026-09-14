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
- bands（v2.1/F10）：AnalysisConfig 的 400/500 是年级名次阈值，当前 ws
  域无名次事实 → 一律 409 invalid_scope_param，绝不做分数镜像比较。
- 越界：域内（同 data_domain+学年）完全不存在该考试 → 404
  resource_out_of_scope；存在但本班无行 → 200 空态，绝不回退全年级。
"""

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
    HomeroomAnalysisStudent,
    HomeroomExamStatsResponse,
    HomeroomExamStudentsResponse,
    HomeroomSubjectStat,
    HomeroomTotalStat,
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
    HomeroomTeachingLink,
    ScoreFact,
    TeachingClass,
)

router = APIRouter(tags=["analysis"])


class BandsNotComputable(DomainError):
    """段位分布不可计算（契约 §2.1 v2.1/F10）：段位阈值口径为年级名次，
    当前 ws 域不存名次事实。契约明确该情形返回 409 invalid_scope_param
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
        totals.append(
            HomeroomTotalStat(
                total_type=total_type,
                avg=_avg(valid),
                max=max(valid) if valid else None,
                min=min(valid) if valid else None,
                valid_count=len(valid),
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

    def _point(f) -> TrendExamPoint:
        return TrendExamPoint(
            exam_name=f.exam_name,
            exam_date=q.display_exam_date(f),
            score=f.score,
            grade_score=_grade_score_of(f),
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
    400/500 是名次阈值），当前 ws 域不存年级名次事实 → 一律 409，
    绝不把名次阈值镜像成原始分比较（那会把 90 分的物理全员判成薄弱）。
    参数/作用域校验沿用既有规则（422/404），通过后不可计算即 409。
    待引入合法名次来源或独立的分数段配置后才恢复计算。"""
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
    raise BandsNotComputable(
        "段位阈值口径为年级名次，当前范围无名次数据，不可计算"
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
