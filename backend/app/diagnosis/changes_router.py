"""P1-B3 变化分解 HTTP 端点（契约 docs/diagnosis-roadmap/p1-contracts.md §5）。

路由形状::

    GET /api/v1/homeroom/diagnosis/changes/student?person_id=&from_exam=&to_exam=
    GET /api/v1/homeroom/diagnosis/changes/class?from_exam=&to_exam=
    GET /api/v1/teaching/diagnosis/changes/student?person_id=&from_exam=&to_exam=
    GET /api/v1/teaching/diagnosis/changes/class?from_exam=&to_exam=

作用域解析与 v1 既有端点同源（app.core.context.resolve_workspace_context；
homeroom 走绑定班/class_id 校验，teaching 走 build_teaching_params），
计算全部委托 app.diagnosis.changes 的服务函数（契约冻结签名）。
域前缀与既有 /api/v1 路由一致；错误契约沿用 app.api.domain_endpoint
（DomainError → {"error": code, "detail": ...}）。

scope 参数（可选，与 analysis 端点一致的既有模式）：
- homeroom：academic_year_id / class_id / term_id；
- teaching：teaching_class_id / academic_year_id / term_id。

响应为纯 dict（结构见 changes.py 模块注释）：携带 calc_version="p1-v1"、
direction_note（百分比/名次变化方向语义的强制文字解释字段）、显式的
missing_reason——数据不足/缺考一律 null + 原因，绝不编造。
"""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import current_teacher_id, domain_endpoint
from app.api._queries import build_teaching_params, check_admin_class_exists
from app.core.context import resolve_workspace_context
from app.db.models import get_db
from app.diagnosis.changes import class_change_decomposition, student_change_decomposition

router = APIRouter(tags=["diagnosis-changes"])


# ────────────────────────────── homeroom 域 ──────────────────────────────


@router.get("/homeroom/diagnosis/changes/student")
@domain_endpoint
def homeroom_student_change_decomposition(
    person_id: int,
    from_exam: str,
    to_exam: str,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """班主任域单生变化分解：本班全科+总分口径（main3=主三门名次变化）。"""
    teacher_id = current_teacher_id(db)
    if class_id is not None:
        check_admin_class_exists(db, class_id)
    ctx = resolve_workspace_context(
        db,
        teacher_id,
        "homeroom",
        {"academic_year_id": academic_year_id, "term_id": term_id, "class_id": class_id},
    )
    return student_change_decomposition(db, ctx, person_id, from_exam, to_exam)


@router.get("/homeroom/diagnosis/changes/class")
@domain_endpoint
def homeroom_class_change_decomposition(
    from_exam: str,
    to_exam: str,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """班主任域班级变化分解：可比集合=两场都有效的学生交集；基期
    percentile_bin/学校段位分组；组内总分变化均值 + 名次变化描述 +
    person_id 下钻名单；excluded 与 comparable_n 显式输出。"""
    teacher_id = current_teacher_id(db)
    if class_id is not None:
        check_admin_class_exists(db, class_id)
    ctx = resolve_workspace_context(
        db,
        teacher_id,
        "homeroom",
        {"academic_year_id": academic_year_id, "term_id": term_id, "class_id": class_id},
    )
    return class_change_decomposition(db, ctx, from_exam, to_exam)


# ────────────────────────────── teaching 域 ──────────────────────────────


@router.get("/teaching/diagnosis/changes/student")
@domain_endpoint
def teaching_student_change_decomposition(
    person_id: int,
    from_exam: str,
    to_exam: str,
    teaching_class_id: Optional[int] = None,
    academic_year_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """教学域单生变化分解：仅任教学科（范围隔离），无总分/名次特征。"""
    teacher_id = current_teacher_id(db)
    params, _subject, _class_ids = build_teaching_params(
        db, academic_year_id, teaching_class_id, None, term_id
    )
    ctx = resolve_workspace_context(db, teacher_id, "teaching", params)
    return student_change_decomposition(db, ctx, person_id, from_exam, to_exam)


@router.get("/teaching/diagnosis/changes/class")
@domain_endpoint
def teaching_class_change_decomposition(
    from_exam: str,
    to_exam: str,
    teaching_class_id: Optional[int] = None,
    academic_year_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """教学域班级变化分解：任教学科口径；段位分组不可用并如实注明。"""
    teacher_id = current_teacher_id(db)
    params, _subject, _class_ids = build_teaching_params(
        db, academic_year_id, teaching_class_id, None, term_id
    )
    ctx = resolve_workspace_context(db, teacher_id, "teaching", params)
    return class_change_decomposition(db, ctx, from_exam, to_exam)
