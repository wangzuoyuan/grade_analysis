"""Teaching-class deletion: preview, name confirmation and atomic scoped cleanup."""
import hashlib
import json
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import update, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api import current_teacher_id, domain_endpoint
from app.api.shared import _load_preview_batch, _managed_teaching_classes
from app.core.errors import InvalidScopeParam, LinkVersionConflict, ResourceOutOfScope
from app.db.models import get_db
from app.db.workspace_models import (
    AcademicYear, ChatSession, HomeworkAssignment, HomeworkStatsExclusion,
    HomeworkSubmission, HomeroomTeachingLink, ImportBatch, LinkedStudent,
    ScoreFact, SourceMap, SourceProjectionMap, TeachingClass, TeachingClassMember,
    TeachingExamScope, TeachingComparisonRoster,
)

router = APIRouter(tags=["teaching-classes"])
KIND = "teaching_class_delete"


class DeleteClassConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str
    confirm_name: str


def _row(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def _json(value):
    return json.dumps(value, default=str, ensure_ascii=False, sort_keys=True)


def _class_references(value):
    """Only inspect typed class references, never unrelated numeric/student IDs."""
    if isinstance(value, list):
        return set().union(*(_class_references(v) for v in value))
    if not isinstance(value, dict):
        return set()
    ids = set()
    for key, item in value.items():
        if key in {"teaching_class_id", "class_id", "class_ref_id", "from_class_id", "to_class_id"} and isinstance(item, int):
            ids.add(item)
        if key in {"class_ids", "ctx_class_ids", "teaching_class_ids", "created_class_ids", "target_class_ids"} and isinstance(item, list):
            ids.update(i for i in item if isinstance(i, int))
        if isinstance(item, (dict, list)):
            ids.update(_class_references(item))
    return ids


def _references_class(value, class_id):
    return class_id in _class_references(value)


def _parse(value):
    try:
        result = json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def _plan(db, class_id):
    teacher_id = current_teacher_id(db)
    # Single-teacher application: all managed subjects/years belong to this teacher.
    cls = db.get(TeachingClass, class_id)
    if cls is None:
        raise ResourceOutOfScope("教学班不存在或已删除")
    year = db.get(AcademicYear, cls.academic_year_id)
    rows = {
        "teaching_class_member": db.query(TeachingClassMember).filter_by(teaching_class_id=cls.id).order_by(TeachingClassMember.id).all(),
        "score_fact": db.query(ScoreFact).filter_by(data_domain="teaching", class_ref_id=cls.id).order_by(ScoreFact.id).all(),
        "homework_assignment": db.query(HomeworkAssignment).filter_by(data_domain="teaching", class_ref_id=cls.id).order_by(HomeworkAssignment.id).all(),
        "homework_stats_exclusion": db.query(HomeworkStatsExclusion).filter_by(data_domain="teaching", class_ref_id=cls.id).order_by(HomeworkStatsExclusion.id).all(),
        "homeroom_teaching_link": db.query(HomeroomTeachingLink).filter_by(teaching_class_id=cls.id).order_by(HomeroomTeachingLink.id).all(),
    }
    rows["homework_submission"] = db.query(HomeworkSubmission).filter(HomeworkSubmission.assignment_id.in_(
        [r.id for r in rows["homework_assignment"]])).order_by(HomeworkSubmission.id).all()
    link_ids = [r.id for r in rows["homeroom_teaching_link"]]
    rows["linked_student"] = db.query(LinkedStudent).filter(LinkedStudent.link_id.in_(link_ids)).order_by(LinkedStudent.id).all()
    # Official grade-wide statistics remain; detach their management pointer.
    references = []
    for model, field in ((TeachingExamScope, "class_snapshot"), (TeachingComparisonRoster, "classes")):
        for row in db.query(model).filter_by(academic_year_id=cls.academic_year_id, subject=cls.subject).order_by(model.id):
            if any(c.get("managed_class_id") == cls.id for c in (getattr(row, field) or [])):
                references.append((row, field))
    # Confirmed imports remain audit records. A confirmed rollover's undo must not
    # restore deleted members/facts into a missing or subsequently reused class.
    batches = []
    for batch in db.query(ImportBatch).filter(ImportBatch.status.in_(["pending", "confirmed"])).order_by(ImportBatch.id):
        s = _parse(batch.scope_json)
        if s.get("kind") == KIND:
            continue
        teaching = batch.data_domain == "teaching"
        explicit = _references_class(s, cls.id) if teaching else s.get("teaching_class_id") == cls.id
        broad = teaching and not _class_references(s) and s.get("academic_year_id") == cls.academic_year_id and s.get("subject") in (None, cls.subject)
        linked = s.get("link_id", s.get("ctx_link_id")) in link_ids
        if batch.status == "pending" and (explicit or broad or linked):
            batches.append(batch)
        elif batch.status == "confirmed" and teaching and s.get("kind") == "teaching_rollover_preview" and explicit:
            batches.append(batch)
    chats = []
    for chat in db.query(ChatSession).filter_by(status="open").order_by(ChatSession.id):
        scope = _parse(chat.scope_json)
        if ((scope.get("mode") == "teaching" or scope.get("data_domain") == "teaching") and _references_class(scope, cls.id)) or scope.get("link_id") in link_ids:
            chats.append(chat)
    rows["teaching_class"] = [cls]
    maps, projections = [], []
    for table, targets in rows.items():
        ids = [r.id for r in targets]
        if ids:
            maps.extend(db.query(SourceMap).filter(SourceMap.target_table == table, SourceMap.target_id.in_(ids)).order_by(SourceMap.id))
            projections.extend(db.query(SourceProjectionMap).filter(SourceProjectionMap.target_table == table, SourceProjectionMap.target_id.in_(ids)).order_by(SourceProjectionMap.id))
    snapshot = {table: [_row(r) for r in targets] for table, targets in rows.items()}
    snapshot.update(source_map=[_row(r) for r in maps], source_projection_map=[_row(r) for r in projections],
                    invalidated_batches=[_row(r) for r in batches], closed_sessions=[_row(r) for r in chats],
                    detached_references=[dict(table=r.__tablename__, row=_row(r)) for r, _ in references])
    fingerprint = hashlib.sha256(_json({"teacher_id": teacher_id, "rows": snapshot}).encode()).hexdigest()
    impact = dict(class_id=cls.id, label=cls.label, subject=cls.subject,
        academic_year_id=cls.academic_year_id, academic_year_name=year.name,
        member_count=len({r.identity_id for r in rows["teaching_class_member"]}),
        membership_count=len(rows["teaching_class_member"]), score_count=len(rows["score_fact"]),
        homework_count=len(rows["homework_assignment"]), submission_count=len(rows["homework_submission"]),
        exclusion_count=len(rows["homework_stats_exclusion"]), link_count=len(link_ids),
        pair_count=len(rows["linked_student"]), invalidated_operations=len(batches), closed_sessions=len(chats),
        reference_count=len(references))
    return cls, rows, batches, chats, maps, projections, references, snapshot, fingerprint, impact


@router.post("/teaching/classes/{class_id}/delete-preview")
@domain_endpoint
def preview_class_delete(class_id: int, db: Session = Depends(get_db)):
    *_, fingerprint, impact = _plan(db, class_id)
    token = secrets.token_hex(24)
    expires = datetime.utcnow() + timedelta(minutes=30)
    db.add(ImportBatch(token=token, data_domain="teaching", scope_json=_json({
        "kind": KIND, "class_id": class_id, "label": impact["label"], "fingerprint": fingerprint,
    }), content_digest=fingerprint, expires_at=expires))
    db.commit()
    return dict(token=token, expires_at=expires.isoformat(), impact=impact)


@router.post("/teaching/classes/{class_id}/delete-confirm")
@domain_endpoint
def confirm_class_delete(class_id: int, req: DeleteClassConfirm, db: Session = Depends(get_db)):
    batch = _load_preview_batch(db, req.token)
    s = _parse(batch.scope_json)
    if batch.data_domain != "teaching" or s.get("kind") != KIND or s.get("class_id") != class_id:
        raise LinkVersionConflict("删除凭据不属于该教学班，请重新预览")
    if req.confirm_name != s.get("label"):
        raise InvalidScopeParam("请输入完整教学班名称确认删除")
    try:
        # Acquire the SQLite writer lock before rereading the entire deletion plan.
        locked = db.execute(update(ImportBatch).where(ImportBatch.id == batch.id,
            ImportBatch.status == "pending").values(status="applying"))
        if locked.rowcount != 1:
            raise LinkVersionConflict("删除凭据已使用，请刷新列表")
        db.expire_all()
        try:
            cls, rows, batches, chats, maps, projections, references, snapshot, fingerprint, impact = _plan(db, class_id)
        except ResourceOutOfScope:
            raise LinkVersionConflict("教学班已变化，请重新预览") from None
        if fingerprint != s["fingerprint"]:
            raise LinkVersionConflict("班级数据或关联已变化，请重新预览后删除")
        # Reserve the highest issued class ID before SQLite can recycle a rowid.
        db.execute(text("INSERT INTO homework_setting (key, value) VALUES "
            "('teaching_class_id_high_watermark', (SELECT MAX(id) FROM teaching_class)) "
            "ON CONFLICT(key) DO UPDATE SET value = MAX(CAST(value AS INTEGER), CAST(excluded.value AS INTEGER))"))
        for b in batches:
            b.status = "invalidated"
        for chat in chats:
            chat.status, chat.closed_at = "closed", datetime.utcnow()
        for m in maps:
            m.active = 0
        for m in projections:
            m.status, m.reason = "deleted", KIND
        for reference, field in references:
            setattr(reference, field, [{**c, "managed_class_id": None} if c.get("managed_class_id") == class_id else c
                                       for c in getattr(reference, field)])
        for table in ("homework_submission", "linked_student", "score_fact", "homework_stats_exclusion",
                      "homework_assignment", "teaching_class_member", "homeroom_teaching_link", "teaching_class"):
            for row in rows[table]:
                db.delete(row)
            db.flush()
        batch = db.get(ImportBatch, batch.id)
        batch.status = "confirmed"
        batch.scope_json = _json({**s, "deleted_at": datetime.utcnow(), "deleted_rows": snapshot, "impact": impact})
        remaining = _managed_teaching_classes(db, impact["academic_year_id"], impact["subject"])
        db.commit()
        return dict(success=True, classes=remaining.classes, impact=impact)
    except OperationalError as exc:
        db.rollback()
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise LinkVersionConflict("数据正在更新，请重新预览后删除") from None
        raise
    except Exception:
        db.rollback()
        raise
