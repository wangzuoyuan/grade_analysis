"""P3-D1 教研统计 HTTP 端点（契约 docs/diagnosis-roadmap/p3-contracts.md §2）。

路由形状::

    GET /api/v1/{homeroom|teaching}/diagnosis/research/cohorts?academic_year_id=
    GET /api/v1/{homeroom|teaching}/diagnosis/research/outcome?cohort=&from_exam=&to_exam=&academic_year_id=&metric=

计算全部委托 ``app.diagnosis.research`` 服务（本文件零业务逻辑）；作用域
解析与 review_router._resolve_ctx 同源（复制保形，见契约 §1 并行边界：
diagnosis 各 router 文件互不依赖）。错误契约沿用 app.api.domain_endpoint
（DomainError → {"error": code, "detail": ...}）：跨学年不存在 → 404、
作用域参数不符 → 422、考试不在范围 → 404。响应为纯 dict，携带
rules_version 组合标注与 limitations 固定文案（回顾性队列、无对照组，
绝不输出因果结论）。
"""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import current_teacher_id, domain_endpoint
from app.api._queries import build_teaching_params, check_admin_class_exists
from app.core.context import WorkspaceContext, resolve_workspace_context
from app.db.models import get_db
from app.diagnosis.research import cohort_list, outcome_aggregate
from app.diagnosis.focus import focus_cohorts, focus_outcome, follow_ups

router = APIRouter(tags=["diagnosis-research"])


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
    """与 types_router/review_router._resolve_ctx 同源的作用域解析（复制保形，
    见契约 §1 并行边界：diagnosis 各 router 文件互不依赖）。"""
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


# ────────────────────────── homeroom 域 ──────────────────────────


@router.get("/homeroom/diagnosis/research/cohorts")
@domain_endpoint
def homeroom_research_cohorts(
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    """班主任域可用队列清单（干预建档分组 + B2 类型回算队列，本班全科+总分口径）。"""
    teacher_id = current_teacher_id(db)
    ctx = _resolve_ctx(
        db, teacher_id, "homeroom", academic_year_id,
        class_id=class_id, term_id=term_id,
    )
    return cohort_list(db, ctx, ctx.academic_year_id)


@router.get("/homeroom/diagnosis/research/outcome")
@domain_endpoint
def homeroom_research_outcome(
    cohort: str,
    from_exam: str,
    to_exam: str,
    metric: str,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    """班主任域队列结局聚合（每人变化复用 B3；干预队列附 C4 复查摘要）。"""
    teacher_id = current_teacher_id(db)
    ctx = _resolve_ctx(
        db, teacher_id, "homeroom", academic_year_id,
        class_id=class_id, term_id=term_id,
    )
    return outcome_aggregate(
        db, ctx, cohort, from_exam, to_exam, metric, ctx.academic_year_id
    )


# ────────────────────────── teaching 域 ──────────────────────────


@router.get("/teaching/diagnosis/research/cohorts")
@domain_endpoint
def teaching_research_cohorts(
    academic_year_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    subject: Optional[str] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    """教学域可用队列清单（仅任教学科口径；绝不跨域取数）。"""
    teacher_id = current_teacher_id(db)
    ctx = _resolve_ctx(
        db, teacher_id, "teaching", academic_year_id,
        term_id=term_id, teaching_class_id=teaching_class_id, subject=subject,
    )
    return cohort_list(db, ctx, ctx.academic_year_id)


@router.get("/teaching/diagnosis/research/outcome")
@domain_endpoint
def teaching_research_outcome(
    cohort: str,
    from_exam: str,
    to_exam: str,
    metric: str,
    academic_year_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    subject: Optional[str] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    """教学域队列结局聚合（任教学科口径；总分/名次类指标不可得时如实排除）。"""
    teacher_id = current_teacher_id(db)
    ctx = _resolve_ctx(
        db, teacher_id, "teaching", academic_year_id,
        term_id=term_id, teaching_class_id=teaching_class_id, subject=subject,
    )
    return outcome_aggregate(
        db, ctx, cohort, from_exam, to_exam, metric, ctx.academic_year_id
    )

# 关注回看独立 API；原 P3 两个端点继续兼容旧调用。


@router.get('/homeroom/diagnosis/research/focus/cohorts')
@domain_endpoint
def homeroom_focus_cohorts(academic_year_id: Optional[int] = None, class_id: Optional[int] = None,
                          db: Session = Depends(get_db)) -> dict:
    ctx = _resolve_ctx(db, current_teacher_id(db), 'homeroom', academic_year_id, class_id=class_id)
    return focus_cohorts(db, ctx)


@router.get('/homeroom/diagnosis/research/focus/outcome')
@domain_endpoint
def homeroom_focus_outcome(cohort: str, from_exam: str, to_exam: str, metric: str,
                          academic_year_id: Optional[int] = None, class_id: Optional[int] = None,
                          db: Session = Depends(get_db)) -> dict:
    ctx = _resolve_ctx(db, current_teacher_id(db), 'homeroom', academic_year_id, class_id=class_id)
    return focus_outcome(db, ctx, cohort, from_exam, to_exam, metric)


@router.get('/homeroom/diagnosis/research/follow-ups')
@domain_endpoint
def homeroom_research_follow_ups(academic_year_id: Optional[int] = None, class_id: Optional[int] = None,
                                db: Session = Depends(get_db)) -> dict:
    ctx = _resolve_ctx(db, current_teacher_id(db), 'homeroom', academic_year_id, class_id=class_id)
    return follow_ups(db, ctx)


@router.get('/teaching/diagnosis/research/follow-ups')
@domain_endpoint
def teaching_research_follow_ups(academic_year_id: Optional[int] = None, teaching_class_id: Optional[int] = None,
                                subject: Optional[str] = None, db: Session = Depends(get_db)) -> dict:
    ctx = _resolve_ctx(db, current_teacher_id(db), 'teaching', academic_year_id,
                       teaching_class_id=teaching_class_id, subject=subject)
    return follow_ups(db, ctx)
