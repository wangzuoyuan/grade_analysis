"""P1-B1 诊断特征层端点（契约 docs/diagnosis-roadmap/p1-contracts.md §2）。

域前缀跟随现有 /api/v1 路由的 mode 机制（同一 router 按 homeroom/teaching
分域，模式与 app/api/analysis.py 一致）：班主任域=本班全科+总分；教学域=
仅任教学科。作用域解析复用 students_mgmt 的 _homeroom_ctx/_teaching_ctx
（resolve_workspace_context 同源）；服务实现见 app/diagnosis/features.py，
单生响应即 ``student_features`` 的返回 dict（B4 三处同源的数据源）。
"""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.students_mgmt import _homeroom_ctx, _teaching_ctx
from app.db.models import get_db
from app.api import domain_endpoint
from app.diagnosis.features import class_features, student_features

router = APIRouter(tags=["diagnosis"])


@router.get("/homeroom/diagnosis/features")
@domain_endpoint
def homeroom_diagnosis_features(
    person_id: int,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    """班主任域单生诊断特征（全科+总分；越界 person 404）。"""
    ctx = _homeroom_ctx(db, academic_year_id, class_id, term_id)
    return student_features(db, ctx, person_id, ctx.academic_year_id)


@router.get("/teaching/diagnosis/features")
@domain_endpoint
def teaching_diagnosis_features(
    person_id: int,
    academic_year_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    subject: Optional[str] = None,
    db: Session = Depends(get_db),
) -> dict:
    """教学域单生诊断特征（仅任教学科；总体类指标按缺失输出，不跨域取数）。"""
    ctx = _teaching_ctx(db, academic_year_id, teaching_class_id, term_id, subject)
    return student_features(db, ctx, person_id, ctx.academic_year_id)


@router.get("/homeroom/diagnosis/features/class")
@domain_endpoint
def homeroom_diagnosis_class_features(
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    """班主任域班级特征汇总（每生 features + type_inputs + 缺失统计）。"""
    ctx = _homeroom_ctx(db, academic_year_id, class_id, term_id)
    return class_features(db, ctx, ctx.academic_year_id)


@router.get("/teaching/diagnosis/features/class")
@domain_endpoint
def teaching_diagnosis_class_features(
    academic_year_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    subject: Optional[str] = None,
    db: Session = Depends(get_db),
) -> dict:
    """教学域班级特征汇总（所教班并集，仅任教学科口径）。"""
    ctx = _teaching_ctx(db, academic_year_id, teaching_class_id, term_id, subject)
    return class_features(db, ctx, ctx.academic_year_id)
