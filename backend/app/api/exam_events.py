"""Canonical exam identity. No score, membership or sharing permissions live here."""
from sqlalchemy import or_, cast, String
from app.core.errors import InvalidScopeParam, LinkVersionConflict
from app.db.workspace_models import WorkspaceExamEvent, ScoreFact, TeachingExamScope, WorkspaceClassAverage, AdministrativeClass
from app.db.models import Teacher


def canonical_id(db, event_id):
    seen = set()
    while event_id:
        if event_id in seen:
            raise InvalidScopeParam("考试关联存在循环，请先修复关联")
        seen.add(event_id)
        event = db.get(WorkspaceExamEvent, event_id)
        if event is None:
            raise InvalidScopeParam("考试标识不存在")
        if not event.merged_into_id:
            return event.id
        event_id = event.merged_into_id
    return None


def event_ids(db, event_id):
    root = canonical_id(db, event_id)
    event = db.get(WorkspaceExamEvent, root)
    return [e.id for e in db.query(WorkspaceExamEvent).filter_by(academic_year_id=event.academic_year_id)
            if canonical_id(db, e.id) == root]


def fact_key(db, fact, roots=None):
    if fact.exam_event_id:
        root = roots[fact.exam_event_id] if roots is not None else canonical_id(db, fact.exam_event_id)
        return f"event:{root}"
    return f"legacy:{fact.exam_name}|{fact.exam_date or fact.source_exam_date or ''}"


def filter_exam(query, db, model, selector):
    if not selector:
        return query
    if selector.startswith("event:"):
        try:
            ids = event_ids(db, int(selector[6:]))
        except (ValueError, TypeError):
            raise InvalidScopeParam("无效考试标识")
        return query.filter(model.exam_event_id.in_(ids))
    if selector.startswith("legacy:"):
        name, day = selector[7:].rsplit("|", 1)
        query = query.filter(model.exam_name == name, model.exam_event_id.is_(None))
        if not day:
            return query.filter(model.exam_date.is_(None))
        predicate = cast(model.exam_date, String) == day
        if hasattr(model, "source_exam_date"):
            predicate = or_(predicate, model.exam_date.is_(None) & (model.source_exam_date == day))
        return query.filter(predicate)
    return query.filter(model.exam_name == selector)


def candidates(db, ctx, name, grade, day):
    # This single-teacher app shares event metadata across workspaces, never score rows.
    # Homeroom class ownership still follows the teacher's explicit bindings.
    teacher = db.get(Teacher, ctx.teacher_id)
    h_classes = [c.id for c in db.query(AdministrativeClass).filter_by(academic_year_id=ctx.academic_year_id)
                 if c.class_num == getattr(teacher, f"target_class_high{c.grade}", None)]
    if ctx.mode == "homeroom":
        h_classes = list(set(h_classes).union(ctx.class_ids))
    owned = db.query(ScoreFact).filter(ScoreFact.academic_year_id == ctx.academic_year_id,
        or_(ScoreFact.data_domain == "teaching",
            (ScoreFact.data_domain == "homeroom") & ScoreFact.class_ref_id.in_(h_classes)))
    visible = {canonical_id(db, f.exam_event_id) for f in owned if f.exam_event_id}
    visible.update(canonical_id(db, e.exam_event_id) for e in db.query(TeachingExamScope).filter_by(
        academic_year_id=ctx.academic_year_id) if e.exam_event_id)
    visible.update(canonical_id(db, e.exam_event_id) for e in db.query(WorkspaceClassAverage).filter_by(
        academic_year_id=ctx.academic_year_id) if e.exam_event_id)
    result = []
    for event in db.query(WorkspaceExamEvent).filter_by(academic_year_id=ctx.academic_year_id, exam_name=name, merged_into_id=None):
        if event.id not in visible or (grade is not None and event.grade not in (None, grade)):
            continue
        ids = event_ids(db, event.id)
        dates, domains = set(), set()
        for model in (ScoreFact, TeachingExamScope, WorkspaceClassAverage):
            for row in db.query(model).filter(model.exam_event_id.in_(ids)):
                if row.exam_date:
                    dates.add(row.exam_date.isoformat())
                domains.add(getattr(row, "data_domain", "teaching"))
        result.append(dict(id=event.id, exam_name=event.exam_name, grade=event.grade,
                           dates=sorted(dates), sources=sorted(domains), revision=event.data_revision))
    exact = [e for e in result if grade is not None and e["grade"] == grade and day and e["dates"] == [str(day)]]
    auto = exact[0]["id"] if len(exact) == 1 and len(result) == 1 else None
    legacy = db.query(ScoreFact).filter_by(academic_year_id=ctx.academic_year_id, data_domain=ctx.mode,
        exam_name=name, exam_event_id=None).all()
    legacy_date_mismatch = any(str(f.exam_date or "") != str(day or "") for f in legacy)
    requires_choice = bool(result) and auto is None or not day or grade is None or legacy_date_mismatch
    return dict(candidates=result, selected=auto, requires_choice=requires_choice,
                message="将归入已有考试" if auto else "请选择已有考试或另建考试" if requires_choice else "将新建考试")


def resolve(db, ctx, name, grade, day, preview, choice=None):
    current = candidates(db, ctx, name, grade, day)
    if preview is not None and current != preview:
        raise LinkVersionConflict("考试候选或来源已变化，请重新预览")
    selected = current["selected"] if choice is None else choice
    if selected is None and current["requires_choice"]:
        raise LinkVersionConflict("考试存在同名、日期缺失或年级不明，请明确选择已有考试或另建考试",
                                  details={"exam_resolution": current})
    if selected in (None, "new"):
        return None
    if selected not in [e["id"] for e in current["candidates"]]:
        raise InvalidScopeParam("所选考试不在本次候选范围")
    return canonical_id(db, selected)


def create_event(db, year, grade, name):
    event = WorkspaceExamEvent(academic_year_id=year, grade=grade, exam_name=name)
    db.add(event)
    db.flush()
    return event.id


def field_conflicts(left, right):
    return {field: {"own": getattr(left, field), "other": getattr(right, field)}
            for field in ("score", "grade_score", "grade_percentile")
            if getattr(left, field) != getattr(right, field)}


def exam_predicate(db, model, selector):
    return filter_exam(db.query(model), db, model, selector).whereclause


def ensure_grade(db, event_id, grade):
    event = db.get(WorkspaceExamEvent, canonical_id(db, event_id))
    if grade is not None and event.grade is None:
        event.grade = grade
        event.data_revision += 1


def canonical_map(db, year):
    """Request-local resolution: one metadata read, no persistent/stale cache."""
    rows = {e.id: e.merged_into_id for e in db.query(WorkspaceExamEvent).filter_by(academic_year_id=year)}
    roots = {}
    for eid in rows:
        path, current = set(), eid
        while rows.get(current) is not None:
            if current in path or rows[current] not in rows:
                raise InvalidScopeParam("考试关联存在循环或越学年指向")
            path.add(current)
            current = rows[current]
        roots[eid] = current
    return roots


def exam_label(db, selector):
    if selector and selector.startswith("event:"):
        return db.get(WorkspaceExamEvent, canonical_id(db, int(selector[6:]))).exam_name
    if selector and selector.startswith("legacy:"):
        return selector[7:].rsplit("|", 1)[0]
    return selector
