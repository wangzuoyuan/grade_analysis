"""P2-C4 自动复查对照端点（契约 docs/diagnosis-roadmap/p2-contracts.md §5.2）。

``GET /api/v1/{mode}/diagnosis/review-contrast?follow_up_id=``

- 计算一律在 ``app.diagnosis.review.review_contrast``；本路由只做作用域
  解析、干预行定位与响应装配，绝不内嵌第二套对照逻辑。
- 作用域：与既有 v1 诊断路由同源（types_router._resolve_ctx 同一解析）；
  干预行定位复用 notes 路由的 ``_note_or_404``（域不符/越界/系统行 → 404，
  绝不向其他 mode 泄露存在性，N01 红线）。
- 缺考/无可比考试 → ``{"status": "pending", "reason": ...}``；响应**绝不**
  含成功/失败判定（契约 §5.2 红线）。
"""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import current_teacher_id, domain_endpoint
from app.api._queries import build_teaching_params, check_admin_class_exists
from app.api.students_mgmt import _note_or_404
from app.core.context import WorkspaceContext, resolve_workspace_context
from app.db.models import get_db
from app.diagnosis.review import review_contrast

router = APIRouter(tags=["diagnosis-review"])


def _resolve_ctx(
    db: Session,
    teacher_id: int,
    mode: str,
    academic_year_id: Optional[int],
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    subject: Optional[str] = None,
) -> WorkspaceContext:
    """与 types_router._resolve_ctx 同源的作用域解析（复制保形，见契约
    §1 并行边界：diagnosis 各 router 文件互不依赖）。"""
    if mode == "homeroom":
        params: dict = {"academic_year_id": academic_year_id, "term_id": term_id}
        if class_id is not None:
            check_admin_class_exists(db, class_id)
            params["class_id"] = class_id
    else:
        params, _subject, _ids = build_teaching_params(
            db, academic_year_id, teaching_class_id, subject, term_id
        )
    return resolve_workspace_context(db, teacher_id, mode, params)


@router.get("/homeroom/diagnosis/review-contrast")
@domain_endpoint
def homeroom_review_contrast(
    follow_up_id: int,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    """班主任域复查对照（本班学生干预；全科+总分口径可读事实）。"""
    teacher_id = current_teacher_id(db)
    ctx = _resolve_ctx(
        db, teacher_id, "homeroom", academic_year_id,
        class_id=class_id, term_id=term_id,
    )
    return _contrast_payload(db, ctx, "homeroom", follow_up_id)


@router.get("/teaching/diagnosis/review-contrast")
@domain_endpoint
def teaching_review_contrast(
    follow_up_id: int,
    academic_year_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    subject: Optional[str] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    """教学域复查对照（仅任教学科可读事实；绝不跨域取数）。"""
    teacher_id = current_teacher_id(db)
    ctx = _resolve_ctx(
        db, teacher_id, "teaching", academic_year_id,
        term_id=term_id, teaching_class_id=teaching_class_id, subject=subject,
    )
    return _contrast_payload(db, ctx, "teaching", follow_up_id)


def _contrast_payload(
    db: Session, ctx: WorkspaceContext, mode: str, follow_up_id: int
) -> dict:
    note = _note_or_404(db, mode, follow_up_id, ctx)
    return review_contrast(db, ctx, note)
