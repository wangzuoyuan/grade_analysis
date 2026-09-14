"""学生成长 / 谈话档案 REST 路由（/api/notes）。"""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.db.models import StudentNote, get_db

router = APIRouter(tags=["notes"])

CATEGORIES = ("谈话", "观察", "家访", "家长沟通", "奖惩", "其他")


def _serialize(n: StudentNote) -> dict:
    return {
        "id": n.id,
        "student_id": n.student_id,
        "date": n.date,
        "category": n.category,
        "content": n.content,
        "follow_up": n.follow_up,
        "follow_up_done": bool(n.follow_up_done),
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@router.get("/notes/{student_id}")
async def list_notes(student_id: str):
    db = next(get_db())
    try:
        from app.analysis.identity import person_ids
        ids = person_ids(db, student_id)
        rows = (
            db.query(StudentNote)
            .filter(StudentNote.student_id.in_(ids))
            .order_by(StudentNote.date.desc(), StudentNote.id.desc())
            .all()
        )
        return [_serialize(n) for n in rows]
    finally:
        db.close()


class NotePayload(BaseModel):
    student_id: str
    date: Optional[str] = None
    category: str = "谈话"
    content: str
    follow_up: Optional[str] = None


@router.post("/notes")
async def create_note(payload: NotePayload):
    if not payload.content.strip():
        raise HTTPException(400, "内容不能为空")
    category = payload.category if payload.category in CATEGORIES else "其他"
    db = next(get_db())
    try:
        note = StudentNote(
            student_id=payload.student_id,
            date=payload.date or datetime.now().strftime("%Y-%m-%d"),
            category=category,
            content=payload.content.strip(),
            follow_up=(payload.follow_up or "").strip() or None,
            follow_up_done=0,
        )
        db.add(note)
        db.commit()
        db.refresh(note)
        return _serialize(note)
    finally:
        db.close()


class NoteUpdatePayload(BaseModel):
    date: Optional[str] = None
    category: Optional[str] = None
    content: Optional[str] = None
    follow_up: Optional[str] = None
    follow_up_done: Optional[bool] = None


@router.put("/notes/{note_id}")
async def update_note(note_id: int, payload: NoteUpdatePayload):
    db = next(get_db())
    try:
        note = db.query(StudentNote).filter(StudentNote.id == note_id).first()
        if not note:
            raise HTTPException(404, "记录不存在")
        if payload.date is not None:
            note.date = payload.date
        if payload.category is not None and payload.category in CATEGORIES:
            note.category = payload.category
        if payload.content is not None:
            note.content = payload.content.strip()
        if payload.follow_up is not None:
            note.follow_up = payload.follow_up.strip() or None
        if payload.follow_up_done is not None:
            note.follow_up_done = 1 if payload.follow_up_done else 0
        db.commit()
        db.refresh(note)
        return _serialize(note)
    finally:
        db.close()


@router.delete("/notes/{note_id}")
async def delete_note(note_id: int):
    db = next(get_db())
    try:
        db.query(StudentNote).filter(StudentNote.id == note_id).delete()
        db.commit()
        return {"success": True}
    finally:
        db.close()
