"""Scope and transaction boundary for teaching attention."""
from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.api import domain_endpoint
from app.api.students_mgmt import _teaching_ctx
from app.db.models import get_db
from app.diagnosis.attention import (
    AttentionSettings, FollowUpRequest, FinishRequest, attention_summary,
    save_settings, create_attention_followup, finish_attention_followup,
)

router = APIRouter(prefix="/teaching/diagnosis/attention", tags=["teaching-attention"])


def context(academic_year_id: Optional[int] = None, teaching_class_id: Optional[int] = None,
            ):
    # Resolve inside domain_endpoint, so out-of-scope resources return the normal 404 body.
    return academic_year_id, teaching_class_id


def _resolve(db, args):
    return _teaching_ctx(db, args[0], args[1], None, None)


@router.get("")
@domain_endpoint
def summary(args=Depends(context), db: Session = Depends(get_db)):
    return attention_summary(db, _resolve(db, args))


@router.put("/settings")
@domain_endpoint
def settings(req: AttentionSettings, args=Depends(context), db: Session = Depends(get_db)):
    return save_settings(db, _resolve(db, args), req)


def _write_context(db, args):
    # Re-read membership under the write lock; no stale authorization or duplicate creation.
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    return _resolve(db, args)


@router.post("/{person_id}/follow-ups")
@domain_endpoint
def create(person_id: int, req: FollowUpRequest, args=Depends(context), db: Session = Depends(get_db)):
    return create_attention_followup(db, _write_context(db, args), person_id, req)


@router.post("/follow-ups/{note_id}/finish")
@domain_endpoint
def finish(note_id: int, req: FinishRequest, args=Depends(context), db: Session = Depends(get_db)):
    return finish_attention_followup(db, _write_context(db, args), note_id, req)
