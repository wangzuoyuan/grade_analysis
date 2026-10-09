"""P1-B2 类型引擎端点（契约 docs/diagnosis-roadmap/p1-contracts.md §3）。

``GET /api/v1/{homeroom|teaching}/diagnosis/types?person_id=&academic_year_id=``

- 判定逻辑一律在 ``app.diagnosis.types.classify_student``（纯函数）；本
  路由只负责作用域解析、取数与响应装配，绝不内嵌第二套规则。
- 作用域：与既有 v1 路由同源——homeroom 走 resolve_workspace_context
  （class_id 缺省教师绑定班，显式非绑定班 404）；teaching 走
  build_teaching_params（仅任教学科，绝不跨域取数）。person 不在当期
  名册 → 404。
- 特征取数：B1 的 ``app.diagnosis.features.student_features``（六类指标
  唯一实现）；B2 开发期的最小特征提取已在 Wave B 合并时由主控删除，
  不保留第二口径。
"""

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api import _queries as q
from app.api import current_teacher_id, domain_endpoint
from app.core.context import WorkspaceContext, resolve_workspace_context
from app.core.errors import ResourceOutOfScope
from app.db.models import get_db
from app.diagnosis.features import student_features
from app.diagnosis.types import classify_student

router = APIRouter(tags=["diagnosis-types"])

HOMEWORK_WINDOW_DAYS = 30  # 契约 §2：作业行为 7/30 天窗口（自然日回溯，含当日）

DIRECTION_PROGRESS = "进步"
DIRECTION_DECLINE = "退步"
DIRECTION_FLAT = "持平"
DIRECTION_INSUFFICIENT = "数据不足"
STABILITY_LABEL_INSUFFICIENT = "数据不足"


# ────────────────────────── 响应模型（契约 §3 输出形状） ──────────────────────────


class DiagnosisEvidence(BaseModel):
    type: str
    basis: str


class StudentTypesResponse(BaseModel):
    person_id: int
    calc_version: str
    main_type: Optional[str] = None
    secondary_tags: List[str] = []
    evidence: List[DiagnosisEvidence] = []
    classification_status: str


# ────────────────────────── 作用域解析（与既有 v1 路由同源） ──────────────────────────


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
    if mode == "homeroom":
        params: dict = {"academic_year_id": academic_year_id, "term_id": term_id}
        if class_id is not None:
            q.check_admin_class_exists(db, class_id)
            params["class_id"] = class_id
    else:
        params, _subject, _ids = q.build_teaching_params(
            db, academic_year_id, teaching_class_id, subject, term_id
        )
    return resolve_workspace_context(db, teacher_id, mode, params)


def _types_payload(db: Session, ctx: WorkspaceContext, person_id: int) -> dict:
    if person_id not in ctx.member_person_ids:
        raise ResourceOutOfScope(
            "person not in current workspace scope", details={"person_id": person_id}
        )
    features = student_features(db, ctx, person_id, ctx.academic_year_id)
    result = classify_student(features)
    result["person_id"] = person_id
    return result


@router.get("/homeroom/diagnosis/types", response_model=StudentTypesResponse)
@domain_endpoint
def homeroom_diagnosis_types(
    person_id: int,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    """班主任域学生类型（本班全科+总分口径，绝不跨域取数）。"""
    teacher_id = current_teacher_id(db)
    ctx = _resolve_ctx(
        db, teacher_id, "homeroom", academic_year_id,
        class_id=class_id, term_id=term_id,
    )
    return _types_payload(db, ctx, person_id)


@router.get("/teaching/diagnosis/types", response_model=StudentTypesResponse)
@domain_endpoint
def teaching_diagnosis_types(
    person_id: int,
    academic_year_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    subject: Optional[str] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    """教学域学生类型（仅任教学科口径；总体/名次类指标缺失时如实输出
    insufficient_data 或作业风险，绝不回退全科）。"""
    teacher_id = current_teacher_id(db)
    ctx = _resolve_ctx(
        db, teacher_id, "teaching", academic_year_id,
        term_id=term_id, teaching_class_id=teaching_class_id, subject=subject,
    )
    return _types_payload(db, ctx, person_id)
