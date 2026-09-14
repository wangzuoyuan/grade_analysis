"""/api/v1 学生端点：域隔离读 + 受控关联投影（契约 §1.3 + §1.4.1）。

- homeroom students：本班 enrollment 成员（姓名/座号按查询时点）；共享
  分数字段（shared_subject_score/shared_conflict）经统一可读事实口径逐
  fact 过五条件门（v2.1/F03：成员交集按考试时点验证——入班日晚于考试
  日的成绩绝不出现）。最近一场通过门的 T 事实与 H 域同学科同场事实
  不冲突时设 shared_subject_score，冲突（值不同，含一方缺考）时改报
  shared_conflict（R5，绝不静默取 teaching 值）。
- teaching students：本教学班成员；roster 已授权且查询时点双侧成员
  有效时，关联成员的 name/seat_no 从 H 名册投影（R2/R3）。
- 画像：homeroom 全科+总分，事实只取目标人（R1）；teaching 仅任教学科、
  无 totals；跨域投影经统一可读事实口径（readable_facts，F09）+
  冲突规则（§1.4.1）；越界 person → 404 resource_out_of_scope。
"""

from datetime import date
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import _queries as q
from app.api import current_teacher_id, domain_endpoint
from app.api.schemas import (
    Metadata,
    PersonInfo,
    ProfileExam,
    ScopeInfo,
    SharedSubjectScore,
    StudentEntry,
    StudentProfileResponse,
    StudentsResponse,
    SubjectGroup,
    TeachingStudentProfileResponse,
    TotalGroup,
)
from app.core.context import WorkspaceContext, resolve_workspace_context
from app.core.errors import InvalidScopeParam, ResourceOutOfScope
from app.db.models import get_db

router = APIRouter(tags=["students"])


def _metadata(
    ctx: WorkspaceContext,
    teaching_class_id: Optional[int] = None,
    fact_revisions=(),
) -> Metadata:
    revisions = [ctx.link_version or 0]
    revisions.extend(int(rev or 0) for rev in fact_revisions)
    return Metadata(
        mode=ctx.mode,
        # 契约/测试口径：homeroom 响应 subject 恒为 null（任教学科只在
        # 关联与投影字段上体现）；teaching 为当前任教学科。
        subject=ctx.subject if ctx.mode == "teaching" else None,
        scope=ScopeInfo(
            academic_year_id=ctx.academic_year_id,
            term_id=ctx.term_id,
            class_id=ctx.class_ids[0] if ctx.mode == "homeroom" else None,
            teaching_class_id=teaching_class_id,
            link_id=ctx.link_id,
            link_version=ctx.link_version,
        ),
        cohort_size=len(ctx.member_person_ids),
        data_revision=max(revisions) if revisions else 0,
    )


def _shared_scores_for_roster(
    db, ctx: WorkspaceContext, roster_ids: List[int]
) -> Dict[int, Tuple[Optional[SharedSubjectScore], Optional[dict]]]:
    """全班交集成员的任教学科共享（契约 §1.3 v2.1/F03 + §1.4.1）。

    所有共享分数字段经统一可读事实口径（readable_facts）逐 fact 过
    五条件门：成员交集按【考试时点 fact.exam_date】验证——入班日/入班
    有效期不覆盖考试日的事实绝不出现（名册行的姓名/座号仍按查询时点）。
    每人取最近一场通过门的 T 事实：与本域同场事实值不同（readable_facts
    已标 conflict）→ 不设 shared_subject_score，改报 shared_conflict；
    本域无该场或值相同（投影行）→ shared_subject_score 取该场（R5，
    绝不静默取 teaching 值）。
    返回 {h_id: (shared_subject_score | None, shared_conflict | None)}。"""
    roster_set = set(roster_ids)
    projected_by_h: Dict[int, list] = {}
    conflict_by_h: Dict[int, list] = {}
    for entry in q.readable_facts(db, ctx):
        if entry.person_id not in roster_set:
            continue
        if entry.projected:
            if entry.source_domain == "teaching":
                projected_by_h.setdefault(entry.person_id, []).append(entry.fact)
        elif entry.conflict_fact is not None:
            conflict_by_h.setdefault(entry.person_id, []).append(entry.conflict_fact)

    result: Dict[int, Tuple[Optional[SharedSubjectScore], Optional[dict]]] = {}
    for h_id in set(projected_by_h) | set(conflict_by_h):
        # (fact, is_conflict)：取最近一场（latest_fact 口径同名册共享）
        candidates = [(f, True) for f in conflict_by_h.get(h_id, [])]
        candidates += [(f, False) for f in projected_by_h.get(h_id, [])]
        latest, is_conflict = max(
            candidates,
            key=lambda pair: (
                pair[0].exam_date is not None,
                pair[0].exam_date or date.min,
                pair[0].id,
            ),
        )
        if is_conflict:
            # 值不同（含一方缺考）：保留本域值，仅提示冲突分数
            result[h_id] = (None, {"teaching_score": latest.score})
        else:
            result[h_id] = (
                SharedSubjectScore(
                    subject=latest.subject,
                    score=latest.score,  # 缺考保持 null，绝不转 0
                    exam_name=latest.exam_name,
                    source_domain="teaching",
                ),
                None,
            )
    return result


# ────────────────────────────── 列表 ──────────────────────────────


@router.get("/homeroom/students", response_model=StudentsResponse)
@domain_endpoint
def homeroom_students(
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    teacher_id = current_teacher_id(db)
    if class_id is None:
        raise InvalidScopeParam(
            "class_id is required", details={"param": "class_id"}
        )
    q.check_admin_class_exists(db, class_id)
    ctx = resolve_workspace_context(
        db,
        teacher_id,
        "homeroom",
        {"academic_year_id": academic_year_id, "term_id": term_id, "class_id": class_id},
    )

    roster = q.homeroom_roster(
        db, ctx.class_ids[0], ctx.as_of, ctx.academic_year_id
    )
    shared_by_h = {}
    linked_map = {}
    link = None
    if ctx.link_id is not None:
        from app.db.workspace_models import HomeroomTeachingLink

        link = db.get(HomeroomTeachingLink, ctx.link_id)
        linked_map = q.linked_map_for_link(db, link.id)
        shared_by_h = _shared_scores_for_roster(
            db, ctx, [item["person_id"] for item in roster]
        )

    students = []
    for item in roster:
        entry = StudentEntry(
            person_id=item["person_id"],
            name=item["name"],
            seat_no=item["seat_no"],
            alias=item["alias"],
            status=item["status"],
        )
        if link is not None and item["person_id"] in linked_map:
            entry.linked_teaching_class_id = link.teaching_class_id
            shared, conflict = shared_by_h.get(item["person_id"], (None, None))
            if shared is not None:
                entry.shared_subject_score = shared
            if conflict is not None:
                entry.shared_conflict = conflict
        students.append(entry)

    return StudentsResponse(
        metadata=_metadata(ctx),
        students=students,
    )


@router.get("/teaching/students", response_model=StudentsResponse)
@domain_endpoint
def teaching_students(
    academic_year_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    teacher_id = current_teacher_id(db)
    if teaching_class_id is None:
        raise InvalidScopeParam(
            "teaching_class_id is required", details={"param": "teaching_class_id"}
        )
    params, _, class_ids = q.build_teaching_params(
        db, academic_year_id, teaching_class_id, None, term_id
    )
    ctx = resolve_workspace_context(db, teacher_id, "teaching", params)

    roster = q.teaching_roster(db, class_ids, ctx.as_of, ctx.academic_year_id)
    # 名册投影（name/seat）门（R3）：roster 已授权 + 查询时点双侧成员交集
    link = q.active_link_for_teaching_class(db, class_ids[0], ctx.academic_year_id, ctx.as_of)
    homeroom_projection = {}
    if link is not None and "roster" in q.share_categories_of(link):
        eligible = q.eligible_linked_pairs(db, link, ctx.as_of)
        if eligible:
            h_roster = {
                item["person_id"]: item
                for item in q.homeroom_roster(db, link.admin_class_id, ctx.as_of)
            }
            for h_id, t_id in eligible.items():
                h_item = h_roster.get(h_id)
                if h_item is not None:
                    homeroom_projection[t_id] = h_item

    students = []
    for item in roster:
        projected = homeroom_projection.get(item["person_id"])
        students.append(
            StudentEntry(
                person_id=item["person_id"],
                name=projected["name"] if projected else item["name"],
                seat_no=projected["seat_no"] if projected else item["seat_no"],
                alias=item["alias"],
                status=item["status"],
            )
        )
    return StudentsResponse(
        metadata=_metadata(ctx, teaching_class_id=class_ids[0]),
        students=students,
    )


# ────────────────────────────── 画像 ──────────────────────────────


def _exam_entry(fact, source_domain: str) -> ProfileExam:
    return ProfileExam(
        exam_name=fact.exam_name,
        exam_date=q.display_exam_date(fact),
        score=fact.score,
        grade_score=fact.grade_score,
        source_domain=source_domain,
    )


def _group_exams(facts, source_domain: str):
    grouped: dict = {}
    for fact in sorted(
        facts,
        key=lambda f: (q.display_exam_date(f) is None, q.display_exam_date(f) or "", f.id),
    ):
        grouped.setdefault(fact.subject, []).append(_exam_entry(fact, source_domain))
    return grouped


@router.get("/homeroom/students/{person_id}", response_model=StudentProfileResponse)
@domain_endpoint
def homeroom_student_profile(
    person_id: int,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    teacher_id = current_teacher_id(db)
    if class_id is not None:
        q.check_admin_class_exists(db, class_id)
    ctx = resolve_workspace_context(
        db,
        teacher_id,
        "homeroom",
        {"academic_year_id": academic_year_id, "term_id": term_id, "class_id": class_id},
    )
    if person_id not in ctx.member_person_ids:
        raise ResourceOutOfScope(
            "person not in current homeroom scope", details={"person_id": person_id}
        )

    names = q.names_for(db, [person_id])
    # R1：画像事实只取目标人（统一可读事实口径 F09：本域事实 + 经门
    # 投影行 + 冲突注记；成员限定目标人，绝不混入他人成绩）
    entries = q.readable_facts(db, ctx, member_ids=[person_id])

    subject_facts = [
        e.fact for e in entries if e.fact.total_type is None and not e.projected
    ]
    total_facts = [e.fact for e in entries if e.fact.total_type is not None]
    # 关联学科投影（§1.4.1，R5）：通过门的 T fact 绝不替换 H 条目——
    # H 域无该场 → 投影行（source_domain=teaching）；值不同 → 保留 H
    # 条目并附 shared_conflict；值相同 → H 条目已是规范值，不重复添加。
    conflicts: Dict[Tuple[str, str], float] = {
        (e.fact.subject, e.fact.exam_name): e.conflict["teaching_score"]
        for e in entries
        if e.conflict is not None
    }
    teaching_facts: List = [e.fact for e in entries if e.projected]

    grouped: dict = {}
    for fact in sorted(
        subject_facts,
        key=lambda f: (q.display_exam_date(f) is None, q.display_exam_date(f) or "", f.id),
    ):
        entry = _exam_entry(fact, "homeroom")
        conflict_key = (fact.subject, fact.exam_name)
        if conflict_key in conflicts:
            entry.shared_conflict = {"teaching_score": conflicts[conflict_key]}
        grouped.setdefault(fact.subject, []).append(entry)
    for t_fact in sorted(
        teaching_facts,
        key=lambda f: (q.display_exam_date(f) is None, q.display_exam_date(f) or "", f.id),
    ):
        grouped.setdefault(t_fact.subject, []).append(_exam_entry(t_fact, "teaching"))

    subjects = [SubjectGroup(subject=subject, exams=exams) for subject, exams in grouped.items()]
    totals_grouped: dict = {}
    for fact in sorted(
        total_facts,
        key=lambda f: (q.display_exam_date(f) is None, q.display_exam_date(f) or "", f.id),
    ):
        totals_grouped.setdefault(fact.total_type, []).append(
            _exam_entry(fact, "homeroom")
        )
    totals = [
        TotalGroup(total_type=total_type, exams=exams)
        for total_type, exams in totals_grouped.items()
    ]
    return StudentProfileResponse(
        metadata=_metadata(ctx),
        person=PersonInfo(person_id=person_id, name=names.get(person_id), domain="homeroom"),
        subjects=sorted(subjects, key=lambda group: group.subject),
        totals=sorted(totals, key=lambda group: group.total_type),
    )


@router.get("/teaching/students/{person_id}", response_model=TeachingStudentProfileResponse)
@domain_endpoint
def teaching_student_profile(
    person_id: int,
    academic_year_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    teacher_id = current_teacher_id(db)
    params, subject, class_ids = q.build_teaching_params(
        db, academic_year_id, teaching_class_id, None, term_id
    )
    ctx = resolve_workspace_context(db, teacher_id, "teaching", params)
    if person_id not in ctx.member_person_ids:
        raise ResourceOutOfScope(
            "person not in current teaching scope", details={"person_id": person_id}
        )

    names = q.names_for(db, [person_id])
    # 统一可读事实口径（F09）：T 本域事实 + linked 学生 H 域同学科事实
    # 经同一投影门反向投影；T 域已有同场事实（值同或值异均然）保留 T
    # 条目、不投影、不附字段——teaching 侧不携带 shared_conflict。
    entries = q.readable_facts(db, ctx, member_ids=[person_id])
    facts = [e.fact for e in entries if not e.projected]
    projected_facts: List = [e.fact for e in entries if e.projected]

    grouped = _group_exams(facts, "teaching")
    for h_fact in sorted(
        projected_facts,
        key=lambda f: (q.display_exam_date(f) is None, q.display_exam_date(f) or "", f.id),
    ):
        grouped.setdefault(h_fact.subject, []).append(_exam_entry(h_fact, "homeroom"))
    subjects = [SubjectGroup(subject=s, exams=e) for s, e in grouped.items()]
    return TeachingStudentProfileResponse(
        metadata=_metadata(ctx, teaching_class_id=class_ids[0] if teaching_class_id is not None else None),
        person=PersonInfo(person_id=person_id, name=names.get(person_id), domain="teaching"),
        subjects=subjects,
    )
