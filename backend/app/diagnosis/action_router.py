"""P2-C2 教师行动首页端点（契约 docs/diagnosis-roadmap/p2-contracts.md §3）。

路由形状::

    GET /api/v1/homeroom/diagnosis/action-summary?academic_year_id=&class_id=
    GET /api/v1/teaching/diagnosis/action-summary?academic_year_id=&teaching_class_id=...

- 摘要计算一律在 ``app.diagnosis.action.action_summary``（B1 class_features
  + B2 classify_student 的同源装配）；本路由只负责作用域解析与响应透传，
  绝不内嵌任何第二套计算。
- 作用域解析与既有 v1 路由同源（app.api.students_mgmt 的
  _homeroom_ctx/_teaching_ctx）：班主任域=本班全科+总分；教学域=仅任教学科，
  无总分/名次 → 结构计数如实为 0，绝不跨域取数。
- 错误契约沿用 app.api.domain_endpoint（DomainError → {"error": code, "detail": ...}）。
"""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import domain_endpoint
from app.api.students_mgmt import _homeroom_ctx, _teaching_ctx
from app.db.models import get_db
from app.diagnosis.action import action_summary

router = APIRouter(tags=["diagnosis-action"])


# ────────────────────────────── homeroom 域 ──────────────────────────────


@router.get("/homeroom/diagnosis/action-summary")
@domain_endpoint
def homeroom_action_summary(
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    """班主任域行动首页摘要（本班全科+总分口径；优先关注+四段摘要）。"""
    ctx = _homeroom_ctx(db, academic_year_id, class_id, term_id)
    return action_summary(db, ctx, ctx.academic_year_id)


# ────────────────────────────── teaching 域 ──────────────────────────────


@router.get("/teaching/diagnosis/action-summary")
@domain_endpoint
def teaching_action_summary(
    academic_year_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    subject: Optional[str] = None,
    db: Session = Depends(get_db),
) -> dict:
    """教学域行动首页摘要（仅任教学科口径；结构类计数按缺失如实为 0）。"""
    ctx = _teaching_ctx(db, academic_year_id, teaching_class_id, term_id, subject)
    return action_summary(db, ctx, ctx.academic_year_id)
