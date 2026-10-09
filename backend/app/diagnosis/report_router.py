"""P2-C3 诊断版学生报告端点（契约 docs/diagnosis-roadmap/p2-contracts.md §4）。

路由形状::

    GET /api/v1/homeroom/diagnosis/report?person_id=&academic_year_id=&exam_name=
    GET /api/v1/teaching/diagnosis/report?person_id=&academic_year_id=&exam_name=

- 报告全部内容在 ``app.diagnosis.report.student_report`` 生成（B1/B2 同源，
  三层分节标注 + 1–3 条建议）；本路由只做作用域解析与转发，绝不内嵌
  第二套口径。
- 作用域与既有 v1 路由同源：homeroom 走 resolve_workspace_context
  （class_id 缺省教师绑定班，显式非绑定班 404）；teaching 走
  build_teaching_params（仅任教学科，绝不跨域取数）。person 不在当期
  名册 → 404 resource_out_of_scope；exam_name 在该生可读事实中不存在
  → 422 invalid_scope_param。
- 教师对建议的编辑只存在于前端本地（不入库），本路由无任何写入端点。
"""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import current_teacher_id, domain_endpoint
from app.api._queries import build_teaching_params, check_admin_class_exists
from app.core.context import resolve_workspace_context
from app.db.models import get_db
from app.diagnosis.report import student_report

router = APIRouter(tags=["diagnosis-report"])


@router.get("/homeroom/diagnosis/report")
@domain_endpoint
def homeroom_diagnosis_report(
    person_id: int,
    academic_year_id: Optional[int] = None,
    exam_name: Optional[str] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    """班主任域诊断版学生报告（本班全科+总分口径，绝不跨域取数）。"""
    teacher_id = current_teacher_id(db)
    if class_id is not None:
        check_admin_class_exists(db, class_id)
    ctx = resolve_workspace_context(
        db,
        teacher_id,
        "homeroom",
        {"academic_year_id": academic_year_id, "term_id": term_id, "class_id": class_id},
    )
    return student_report(db, ctx, person_id, ctx.academic_year_id, exam_name)


@router.get("/teaching/diagnosis/report")
@domain_endpoint
def teaching_diagnosis_report(
    person_id: int,
    academic_year_id: Optional[int] = None,
    exam_name: Optional[str] = None,
    teaching_class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    subject: Optional[str] = None,
    db: Session = Depends(get_db),
) -> dict:
    """教学域诊断版学生报告（仅任教学科口径；总体类指标缺失时如实输出）。"""
    teacher_id = current_teacher_id(db)
    params, _subject, _class_ids = build_teaching_params(
        db, academic_year_id, teaching_class_id, subject, term_id
    )
    ctx = resolve_workspace_context(db, teacher_id, "teaching", params)
    return student_report(db, ctx, person_id, ctx.academic_year_id, exam_name)
