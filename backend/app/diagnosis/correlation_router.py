"""P2-C1 作业×成绩相关性 HTTP 端点（契约 docs/diagnosis-roadmap/p2-contracts.md §2）。

路由形状::

    GET /api/v1/homeroom/diagnosis/correlation?exam_name=&window_days=14|30&metric=
    GET /api/v1/teaching/diagnosis/correlation?exam_name=&window_days=14|30&metric=

作用域解析与 v1 既有端点同源（app.core.context.resolve_workspace_context；
homeroom 走 students_mgmt._homeroom_ctx，teaching 走 _teaching_ctx），
计算全部委托 app.diagnosis.correlation.exam_homework_correlation（契约
冻结签名，绝不在路由层另算）。错误契约沿用 app.api.domain_endpoint。

metric：沿用 definitions.metric_meta 词表（如 total:主三门 / subject:语文，
y 取对应「分数」口径）。homeroom 缺省 total:主三门（与 AI 工具同默认，
2026-09-29 统一）；teaching 缺省钉住
任教学科（subject:{任教学科}，传其他值 422——教学域仅任教学科，绝不跨域）。
window_days 仅允许 14|30（契约 HTTP 形状）。响应携带 calc_version="p2-v1"、
window_days、窗口起止日、sample（各层 n 与排除数）、分层 r/rho 与
note（方向与指标含义 + 「相关性不构成因果或提分保证」）。
"""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import domain_endpoint
from app.api.students_mgmt import _homeroom_ctx, _teaching_ctx
from app.core.errors import InvalidScopeParam
from app.db.models import get_db
from app.diagnosis.correlation import exam_homework_correlation

router = APIRouter(tags=["diagnosis-correlation"])

VALID_WINDOW_DAYS = (14, 30)


def _check_window_days(window_days: int) -> int:
    if window_days not in VALID_WINDOW_DAYS:
        raise InvalidScopeParam(
            "window_days must be 14 or 30",
            details={"param": "window_days", "window_days": window_days},
        )
    return window_days


# ────────────────────────────── homeroom 域 ──────────────────────────────


@router.get("/homeroom/diagnosis/correlation")
@domain_endpoint
def homeroom_diagnosis_correlation(
    exam_name: str,
    window_days: int = 14,
    metric: Optional[str] = None,
    subject: Optional[str] = None,
    homework_type: Optional[str] = None,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    """班主任域：X=窗口内作业提交率（缺省全科+关联学科；subject 可选过滤
    哪科作业），Y=metric 分数；分层=该场学校段位（高分/临界/薄弱）+ 全班。"""
    _check_window_days(window_days)
    ctx = _homeroom_ctx(db, academic_year_id, class_id, term_id)
    return exam_homework_correlation(
        db,
        ctx,
        exam_name,
        window_days=window_days,
        metric=metric or "total:主三门",
        homework_subject=subject,
        homework_type=homework_type,
    )


# ────────────────────────────── teaching 域 ──────────────────────────────


@router.get("/teaching/diagnosis/correlation")
@domain_endpoint
def teaching_diagnosis_correlation(
    exam_name: str,
    window_days: int = 14,
    metric: Optional[str] = None,
    subject: Optional[str] = None,
    homework_type: Optional[str] = None,
    academic_year_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    """教学域：X=窗口内任教学科作业提交率（subject 参数忽略，恒钉任教学科），
    Y=任教学科单科分数（范围隔离，metric 钉住任教学科）；无总分行，段位层
    如实标不可用。"""
    _check_window_days(window_days)
    ctx = _teaching_ctx(db, academic_year_id, teaching_class_id, term_id, subject)
    return exam_homework_correlation(
        db,
        ctx,
        exam_name,
        window_days=window_days,
        metric=metric or f"subject:{ctx.subject}",
        homework_type=homework_type,
    )
