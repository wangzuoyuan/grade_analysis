"""/api/v1/shared 子路由：配置、学年班级、作用域解析、班级关联生命周期。

关联生命周期（契约 §1.2 v2 / v2.1）：
- preview 零业务写入（仅 import_batch 台账行）；both 只列出同对班已存在
  link 时【已确认 LinkedStudent】对应的双侧成员——同名同号只作为 warning
  文本列出，绝不自动配对；homeroom_only/teaching_only 须扣除已配对成员
  （候选集合与计数一致）；
- preview 在目标班对已有 link（任意状态）时把 link_id/link_version/
  link_status 写入快照绑定：confirm 校验绑定与当前库一致——已取消或
  版本变化（含 share-scope 修改递增 version）→ 409，未消费旧 token
  不得复活已取消关联，全新配对（无既有 link）无此绑定；
- confirm 仅接受 pending token：不存在/过期/已消费（confirmed）→ 409
  link_version_conflict，已消费 token 不得再次触发任何状态变化（含
  cancel 后复活）；双侧成员与 preview 快照漂移同样 409，要求重新预览；
- cancel 即时生效（status='cancelled' + version+1），域内原生数据保留；
  复活只能经新 preview token（version+1）。
"""

import json
import secrets
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api import _queries as q
from app.api import current_teacher_id, domain_endpoint
from app.api.schemas import (
    AcademicYearBrief,
    HomeroomClassInfo,
    LinkConfirmResponse,
    LinkPreviewResponse,
    LinkShareScopeRequest,
    LinkShareScopeResponse,
    LinkStudentDeleteResponse,
    LinkStudentsCreateRequest,
    LinkStudentsCreateResponse,
    LinkStudentsListResponse,
    LinkedStudentPair,
    LinkSummary,
    LinkCancelResponse,
    LinkListResponse,
    RosterDiff,
    ScopeResponse,
    SharedClassesResponse,
    SharedConfigResponse,
    StudentBrief,
    TeachingClassInfo,
    TeachingConfig,
    TeacherInfo,
    HomeroomConfig,
)
from app.core.context import VALID_MODES, resolve_workspace_context
from app.core.errors import (
    DomainError,
    InvalidScopeParam,
    LinkVersionConflict,
    ResourceOutOfScope,
)
from app.db.models import get_db
from app.db.workspace_models import (
    AcademicYear,
    AdministrativeClass,
    HomeroomTeachingLink,
    ImportBatch,
    LinkedStudent,
    TeachingClass,
)

router = APIRouter(tags=["shared"])

# 共享白名单全集（契约 §1.2.2）：share-scope 写入口的校验依据；
# 读侧解析仍统一走 q.share_categories_of，两处集合保持一致
ALLOWED_SHARE_CATEGORIES = ("roster", "current_subject_score", "current_subject_homework")


# ────────────────────────────── 1.1 配置与范围 ──────────────────────────────


def _homeroom_binding(db: Session):
    """教师行政班绑定（读法与 core.context 一致：active_grade → 绑定列）。"""
    from app.db.models import Teacher
    from app.rollover.service import get_active_grade

    teacher = db.query(Teacher).first()
    grade = int(get_active_grade(db))
    if teacher is None:
        return None, grade, None
    field = {1: "target_class_high1", 2: "target_class_high2", 3: "target_class_high3"}.get(grade)
    class_num = getattr(teacher, field) if field else None
    return teacher, grade, class_num


def _link_summaries(db: Session, academic_year_id: Optional[int] = None) -> list:
    query = db.query(HomeroomTeachingLink, AcademicYear).join(
        AcademicYear, AcademicYear.id == HomeroomTeachingLink.academic_year_id
    )
    if academic_year_id is not None:
        query = query.filter(HomeroomTeachingLink.academic_year_id == academic_year_id)
    rows = query.order_by(HomeroomTeachingLink.id.asc()).all()

    def _categories_in_order(link: HomeroomTeachingLink) -> list:
        # 保持存库顺序展示（与 q.share_categories_of 的集合语义一致，仅不排序）
        return [part.strip() for part in (link.share_categories or "").split(",") if part.strip()]

    return [
        LinkSummary(
            id=link.id,
            admin_class_id=link.admin_class_id,
            teaching_class_id=link.teaching_class_id,
            academic_year_id=link.academic_year_id,
            academic_year_name=year.name,
            subject=link.subject,
            status=link.status,
            version=link.version,
            valid_from=link.valid_from.isoformat() if link.valid_from else None,
            valid_to=link.valid_to.isoformat() if link.valid_to else None,
            share_categories=_categories_in_order(link),
            share_history_from=(
                link.share_history_from.isoformat() if link.share_history_from else None
            ),
        )
        for link, year in rows
    ]


@router.get("/shared/config", response_model=SharedConfigResponse)
@domain_endpoint
def get_shared_config(db: Session = Depends(get_db)):
    teacher_id = current_teacher_id(db)
    from app.db.models import Teacher

    teacher = db.query(Teacher).first()
    _, grade, class_num = _homeroom_binding(db)
    homeroom = HomeroomConfig(
        configured=class_num is not None,
        grade=grade if class_num is not None else None,
        class_num=class_num,
    )

    teaching = TeachingConfig(configured=False, subject=None)
    current_ay = (
        db.query(AcademicYear)
        .order_by(AcademicYear.start_date.desc(), AcademicYear.id.desc())
        .first()
    )
    if current_ay is not None:
        try:
            subject = q.teaching_subject_for_year(db, current_ay.id, None)
            teaching = TeachingConfig(configured=True, subject=subject)
        except DomainError:
            pass  # 学年无教学班：教学侧未配置是合法展示态

    return SharedConfigResponse(
        teacher=TeacherInfo(id=teacher_id, name=teacher.name if teacher else None),
        homeroom=homeroom,
        teaching=teaching,
        current_academic_year=(
            AcademicYearBrief(id=current_ay.id, name=current_ay.name)
            if current_ay is not None
            else None
        ),
        links=_link_summaries(db),
    )


@router.get("/shared/classes", response_model=SharedClassesResponse)
@domain_endpoint
def get_shared_classes(
    academic_year_id: Optional[int] = None, db: Session = Depends(get_db)
):
    """关联配置向导的班级下拉数据源（契约 §1.1 补丁）。"""
    current_teacher_id(db)
    if academic_year_id is None:
        raise InvalidScopeParam(
            "academic_year_id is required", details={"param": "academic_year_id"}
        )
    ay = db.get(AcademicYear, academic_year_id)
    if ay is None:
        # 本端点口径：学年 ID 非法（缺失/不存在）→ 422 invalid_scope_param
        raise InvalidScopeParam(
            "academic year not found", details={"academic_year_id": academic_year_id}
        )

    _, grade, class_num = _homeroom_binding(db)
    homeroom_info = None
    if class_num is not None:
        admin_class = (
            db.query(AdministrativeClass)
            .filter(
                AdministrativeClass.academic_year_id == ay.id,
                AdministrativeClass.grade == grade,
                AdministrativeClass.class_num == class_num,
            )
            .one_or_none()
        )
        if admin_class is not None:
            homeroom_info = HomeroomClassInfo(
                class_id=admin_class.id,
                grade=admin_class.grade,
                class_num=admin_class.class_num,
                label=admin_class.label,
            )

    teaching_list = [
        TeachingClassInfo(class_id=tc.id, label=tc.label, subject=tc.subject)
        for tc in (
            db.query(TeachingClass)
            .filter(
                TeachingClass.academic_year_id == ay.id,
                TeachingClass.subject == q.teaching_subject_for_year(db, ay.id, None),
            )
            .order_by(TeachingClass.sort_order.asc(), TeachingClass.id.asc())
            .all()
        )
    ]
    return SharedClassesResponse(
        academic_year_id=ay.id,
        academic_year_name=ay.name,
        homeroom=homeroom_info,
        teaching=teaching_list,
    )


@router.get("/shared/scope", response_model=ScopeResponse)
@domain_endpoint
def get_shared_scope(
    mode: Optional[str] = None,
    academic_year_id: Optional[int] = None,
    term_id: Optional[int] = None,
    class_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    subject: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if mode is None or mode not in VALID_MODES:
        raise InvalidScopeParam(
            "mode must be 'homeroom' or 'teaching'", details={"param": "mode"}
        )
    teacher_id = current_teacher_id(db)
    if mode == "homeroom":
        # 契约 §1.1：homeroom 必须显式 class_id，不得回退教师默认班
        if class_id is None:
            raise InvalidScopeParam(
                "class_id is required for homeroom mode", details={"param": "class_id"}
            )
        q.check_admin_class_exists(db, class_id)
        params = {
            "academic_year_id": academic_year_id,
            "term_id": term_id,
            "class_id": class_id,
        }
    else:
        params, _, _ = q.build_teaching_params(
            db, academic_year_id, teaching_class_id, subject, term_id
        )
    ctx = resolve_workspace_context(db, teacher_id, mode, params)
    return ScopeResponse(
        mode=ctx.mode,
        data_domain=ctx.data_domain,
        subject=ctx.subject if ctx.mode == "teaching" else None,
        member_person_ids=list(ctx.member_person_ids),
        cohort_size=len(ctx.member_person_ids),
        link_id=ctx.link_id,
        link_version=ctx.link_version,
        as_of=ctx.as_of.isoformat(),
    )


# ────────────────────────────── 1.2 班级关联 ──────────────────────────────


@router.get("/shared/links", response_model=LinkListResponse)
@domain_endpoint
def list_links(academic_year_id: Optional[int] = None, db: Session = Depends(get_db)):
    if academic_year_id is not None and db.get(AcademicYear, academic_year_id) is None:
        raise ResourceOutOfScope(
            "academic year not found", details={"academic_year_id": academic_year_id}
        )
    return LinkListResponse(links=_link_summaries(db, academic_year_id))


class LinkPreviewRequest(BaseModel):
    admin_class_id: int
    teaching_class_id: int
    academic_year_id: int
    subject: str


def _roster_warning(homeroom_briefs: list, teaching_briefs: list) -> str:
    """同名同号候选提示：仅供人工确认，绝不据此自动配对（both 只投影
    已确认 LinkedStudent，见 preview_link）。"""
    candidates = []
    for h in homeroom_briefs:
        for t in teaching_briefs:
            same_name = h.name and t.name and h.name == t.name
            same_suffix = (
                q.bare_alias_suffix(h.alias)
                and q.bare_alias_suffix(h.alias) == q.bare_alias_suffix(t.alias)
            )
            if same_name or same_suffix:
                candidates.append(
                    f"{h.name}({h.alias}) ↔ {t.name}({t.alias})"
                )
    if candidates:
        return (
            "同名同号候选（仅供人工确认，绝不自动配对）："
            + "；".join(candidates)
        )
    return "预览不自动配对：同名同号不作为关联依据，确认后需人工建立学生对应关系。"


@router.post("/shared/links/preview", response_model=LinkPreviewResponse)
@domain_endpoint
def preview_link(req: LinkPreviewRequest, db: Session = Depends(get_db)):
    teacher_id = current_teacher_id(db)
    q.check_admin_class_exists(db, req.admin_class_id)
    # 借作用域解析校验：行政班必须属于教师绑定（越界 404）、学年合法
    ctx = resolve_workspace_context(
        db,
        teacher_id,
        "homeroom",
        {"academic_year_id": req.academic_year_id, "class_id": req.admin_class_id},
    )
    tc = db.get(TeachingClass, req.teaching_class_id)
    if tc is None or tc.academic_year_id != req.academic_year_id:
        raise ResourceOutOfScope(
            "teaching class not found in this academic year",
            details={"teaching_class_id": req.teaching_class_id},
        )
    subject = (req.subject or "").strip()
    if subject != tc.subject:
        raise InvalidScopeParam(
            "subject does not match teaching class",
            details={"teaching_class_id": req.teaching_class_id, "subject": subject},
        )

    homeroom_roster = q.homeroom_roster(db, ctx.class_ids[0], ctx.as_of)
    teaching_roster = q.teaching_roster(db, [tc.id], ctx.as_of)
    homeroom_briefs = [
        StudentBrief(person_id=item["person_id"], name=item["name"], alias=item["alias"])
        for item in homeroom_roster
    ]
    teaching_briefs = [
        StudentBrief(person_id=item["person_id"], name=item["name"], alias=item["alias"])
        for item in teaching_roster
    ]

    # both（契约 §1.2 v2）：同对班已存在 link 时，列出已确认 LinkedStudent
    # 对应的双侧成员。每条已确认配对输出一条 homeroom 侧 StudentBrief
    # （契约只要求 both 列出双侧成员，不展开两侧各一份）；按当前名册交集
    # 过滤——任一侧已不在当期名册的配对不算交集成员。同名同号绝不自动
    # 生成配对，此处只投影显式确认过的 LinkedStudent。
    both_briefs = []
    # 已确认配对成员全集（v2.1 候选集合）：从 homeroom_only/teaching_only
    # 中扣除，保证"未配对候选"不再含已配对的甲乙（集合与计数一致）
    paired_h_ids: set = set()
    paired_t_ids: set = set()
    existing_link = (
        db.query(HomeroomTeachingLink)
        .filter(
            HomeroomTeachingLink.admin_class_id == req.admin_class_id,
            HomeroomTeachingLink.teaching_class_id == tc.id,
            HomeroomTeachingLink.academic_year_id == req.academic_year_id,
            HomeroomTeachingLink.subject == subject,
        )
        .one_or_none()
    )
    if existing_link is not None:
        h_roster_index = {item["person_id"]: item for item in homeroom_roster}
        t_member_ids = {item["person_id"] for item in teaching_roster}
        for ls in q.linked_students_for_link(db, existing_link.id):
            paired_h_ids.add(ls.homeroom_identity_id)
            paired_t_ids.add(ls.teaching_identity_id)
            h_item = h_roster_index.get(ls.homeroom_identity_id)
            if h_item is not None and ls.teaching_identity_id in t_member_ids:
                both_briefs.append(
                    StudentBrief(
                        person_id=h_item["person_id"],
                        name=h_item["name"],
                        alias=h_item["alias"],
                    )
                )

    token = secrets.token_hex(16)
    expires_at = datetime.utcnow() + timedelta(minutes=q.PREVIEW_TTL_MINUTES)
    snapshot = {
        "kind": "link_preview",
        "admin_class_id": req.admin_class_id,
        "teaching_class_id": req.teaching_class_id,
        "academic_year_id": req.academic_year_id,
        "subject": subject,
        "as_of": ctx.as_of.isoformat(),
        "homeroom_member_ids": [item["person_id"] for item in homeroom_roster],
        "teaching_member_ids": [item["person_id"] for item in teaching_roster],
    }
    if existing_link is not None:
        # v2.1（F02）绑定：目标班对已有 link（任意状态）时记录其
        # id/版本/状态，confirm 据此识别"预览之后发生 cancel / 版本变化"，
        # 未消费旧 token 不得复活已取消关联
        snapshot["link_id"] = existing_link.id
        snapshot["link_version"] = int(existing_link.version or 1)
        snapshot["link_status"] = existing_link.status
    db.add(
        ImportBatch(
            token=token,
            data_domain="homeroom",
            scope_json=json.dumps(snapshot, ensure_ascii=False),
            status="pending",
            expires_at=expires_at,
        )
    )
    db.commit()
    return LinkPreviewResponse(
        token=token,
        expires_at=expires_at.isoformat(),
        roster_diff=RosterDiff(
            both=both_briefs,
            homeroom_only=[
                b for b in homeroom_briefs if b.person_id not in paired_h_ids
            ],
            teaching_only=[
                b for b in teaching_briefs if b.person_id not in paired_t_ids
            ],
        ),
        warning=_roster_warning(homeroom_briefs, teaching_briefs),
    )


class LinkConfirmRequest(BaseModel):
    token: str


def _load_preview_batch(db: Session, token: str) -> ImportBatch:
    batch = db.query(ImportBatch).filter(ImportBatch.token == token).first()
    if batch is None:
        raise LinkVersionConflict("token not found", details={"token": token})
    if batch.expires_at is not None and batch.expires_at < datetime.utcnow():
        raise LinkVersionConflict("token expired", details={"token": token})
    if batch.status != "pending":
        # 契约 §1.2 校验 1（v2）：已消费（confirmed）token 一律 409，
        # 不得再次触发任何状态变化——幂等读语义不存在，重试必须重新预览
        raise LinkVersionConflict(
            "token already consumed",
            details={"token": token, "status": batch.status},
        )
    return batch


@router.post("/shared/links/confirm", response_model=LinkConfirmResponse)
@domain_endpoint
def confirm_link(req: LinkConfirmRequest, db: Session = Depends(get_db)):
    teacher_id = current_teacher_id(db)
    batch = _load_preview_batch(db, req.token)
    snapshot = json.loads(batch.scope_json or "{}")
    if snapshot.get("kind") != "link_preview":
        raise LinkVersionConflict("token is not a link preview", details={"token": req.token})

    try:
        # 范围与当前库一致性：班级/学年/学科/任一绑定变化都拒绝
        admin_class = q.check_admin_class_exists(db, snapshot["admin_class_id"])
        ctx = resolve_workspace_context(
            db,
            teacher_id,
            "homeroom",
            {
                "academic_year_id": snapshot["academic_year_id"],
                "class_id": snapshot["admin_class_id"],
            },
        )
        tc = db.get(TeachingClass, snapshot["teaching_class_id"])
        if (
            tc is None
            or tc.academic_year_id != snapshot["academic_year_id"]
            or tc.subject != snapshot["subject"]
        ):
            raise LinkVersionConflict("teaching class scope changed")
        # 成员漂移校验（契约 §1.2 校验 3）：用当前名册重算双侧成员集合，
        # 与 preview 快照不一致（任一侧增/减）→ 409 零写入，要求重新预览
        current_h = {
            item["person_id"] for item in q.homeroom_roster(db, admin_class.id, ctx.as_of)
        }
        current_t = {
            item["person_id"] for item in q.teaching_roster(db, [tc.id], ctx.as_of)
        }
        if (
            current_h != set(snapshot.get("homeroom_member_ids") or [])
            or current_t != set(snapshot.get("teaching_member_ids") or [])
        ):
            raise LinkVersionConflict(
                "成员已变化，请重新预览",
                details={
                    "homeroom_member_ids": sorted(current_h),
                    "teaching_member_ids": sorted(current_t),
                },
            )
        # 目标班对当前 link（若存在）：既用于 v2.1 绑定校验，也供下方
        # 创建/复活决策复用，避免重复查询
        link = (
            db.query(HomeroomTeachingLink)
            .filter(
                HomeroomTeachingLink.admin_class_id == admin_class.id,
                HomeroomTeachingLink.teaching_class_id == tc.id,
                HomeroomTeachingLink.academic_year_id == snapshot["academic_year_id"],
                HomeroomTeachingLink.subject == snapshot["subject"],
            )
            .one_or_none()
        )
        # v2.1（F02）绑定校验：preview 时目标班对已有 link 的，快照记录
        # 其 id/版本/状态；当前库不一致（已取消 / 版本变化，含 share-scope
        # 修改递增的 version）说明预览之后状态已漂移 → 409 零写入，必须
        # 重新预览。快照无绑定（全新配对）跳过本检查。
        if "link_id" in snapshot and (
            link is None
            or link.id != snapshot["link_id"]
            or int(link.version or 1) != snapshot.get("link_version")
            or link.status != snapshot.get("link_status")
        ):
            raise LinkVersionConflict(
                "关联状态或版本已变化，请重新预览",
                details={
                    "snapshot_link_id": snapshot.get("link_id"),
                    "snapshot_link_version": snapshot.get("link_version"),
                    "snapshot_link_status": snapshot.get("link_status"),
                },
            )
        # 首版约束：同学年同学科，一个行政班只能 active 关联一个教学班
        conflicting = (
            db.query(HomeroomTeachingLink)
            .filter(
                HomeroomTeachingLink.admin_class_id == admin_class.id,
                HomeroomTeachingLink.academic_year_id == snapshot["academic_year_id"],
                HomeroomTeachingLink.subject == snapshot["subject"],
                HomeroomTeachingLink.status == "active",
                HomeroomTeachingLink.teaching_class_id != tc.id,
            )
            .first()
        )
        if conflicting is not None:
            raise LinkVersionConflict(
                "another teaching class is already actively linked",
                details={"link_id": conflicting.id},
            )
    except DomainError as exc:
        if isinstance(exc, LinkVersionConflict):
            raise
        # 契约：confirm 一切失败统一 link_version_conflict 409
        raise LinkVersionConflict(
            f"preview scope no longer valid: {exc.message}", details=exc.details
        ) from exc

    if link is None:
        link = HomeroomTeachingLink(
            admin_class_id=admin_class.id,
            teaching_class_id=tc.id,
            academic_year_id=snapshot["academic_year_id"],
            subject=snapshot["subject"],
            valid_from=ctx.as_of,
            valid_to=None,
            status="active",
            version=1,
        )
        db.add(link)
    elif link.status != "active":
        # 同一对班撤销后重新确认：复活原行（唯一键占位），版本 +1
        link.status = "active"
        link.version = (link.version or 1) + 1
        link.valid_from = ctx.as_of
        link.valid_to = None
        link.cancelled_at = None
    db.flush()

    linked_count = len(q.linked_students_for_link(db, link.id))
    snapshot["link_id"] = link.id
    batch.scope_json = json.dumps(snapshot, ensure_ascii=False)
    batch.status = "confirmed"
    db.commit()
    return LinkConfirmResponse(link_id=link.id, version=link.version, linked_count=linked_count)


@router.post("/shared/links/{link_id}/cancel", response_model=LinkCancelResponse)
@domain_endpoint
def cancel_link(link_id: int, db: Session = Depends(get_db)):
    current_teacher_id(db)
    link = db.get(HomeroomTeachingLink, link_id)
    if link is None:
        raise ResourceOutOfScope("link not found", details={"link_id": link_id})
    if link.status != "cancelled":
        link.status = "cancelled"
        link.version = (link.version or 1) + 1
        link.cancelled_at = datetime.utcnow()
        db.commit()
    return LinkCancelResponse(success=True, status="cancelled")


# ─────────────────── 1.2.1 学生配对 / 1.2.2 共享范围（v2——R6） ───────────────────


def _pair_entries(db: Session, rows: list) -> list:
    """LinkedStudent 行 → 响应配对条目（名字用 q.names_for，缺名为 null）。"""
    names = q.names_for(
        db,
        [r.homeroom_identity_id for r in rows] + [r.teaching_identity_id for r in rows],
    )
    return [
        LinkedStudentPair(
            linked_id=row.id,
            homeroom_person_id=row.homeroom_identity_id,
            teaching_person_id=row.teaching_identity_id,
            homeroom_name=names.get(row.homeroom_identity_id),
            teaching_name=names.get(row.teaching_identity_id),
            confirm_basis=row.confirm_basis,
        )
        for row in rows
    ]


@router.get("/shared/links/{link_id}/students", response_model=LinkStudentsListResponse)
@domain_endpoint
def list_link_students(link_id: int, db: Session = Depends(get_db)):
    current_teacher_id(db)
    if db.get(HomeroomTeachingLink, link_id) is None:
        raise ResourceOutOfScope("link not found", details={"link_id": link_id})
    return LinkStudentsListResponse(
        link_id=link_id,
        pairs=_pair_entries(db, q.linked_students_for_link(db, link_id)),
    )


def _active_link_or_conflict(db: Session, link_id: int, as_of: date) -> HomeroomTeachingLink:
    """配对写入的前置门：link 存在且 active 且有效期覆盖 as_of。"""
    link = db.get(HomeroomTeachingLink, link_id)
    if link is None:
        raise ResourceOutOfScope("link not found", details={"link_id": link_id})
    if link.status != "active" or link.valid_from > as_of or (
        link.valid_to is not None and link.valid_to < as_of
    ):
        raise LinkVersionConflict(
            "link is not active", details={"link_id": link_id, "status": link.status}
        )
    return link


@router.post(
    "/shared/links/{link_id}/students", response_model=LinkStudentsCreateResponse
)
@domain_endpoint
def create_link_students(
    link_id: int, req: LinkStudentsCreateRequest, db: Session = Depends(get_db)
):
    """显式确认学生配对（契约 §1.2.1）：只接受 person_id 对，同名同号绝不
    自动配对。逐对校验，任一失败整批 422/404 且零写入；与既有完全相同的
    对幂等跳过。"""
    current_teacher_id(db)
    if not req.pairs:
        raise InvalidScopeParam(
            "pairs must contain at least one pair", details={"param": "pairs"}
        )
    as_of = date.today()
    link = _active_link_or_conflict(db, link_id, as_of)

    # 请求内一一映射校验（同 h 配不同 t / 同 t 配不同 h → 422）；
    # 完全相同的重复对在请求内去重，只处理一次
    unique_pairs: list = []
    seen_request: set = set()
    h_to_t: dict = {}
    t_to_h: dict = {}
    for pair in req.pairs:
        key = (pair.homeroom_person_id, pair.teaching_person_id)
        if key in seen_request:
            continue
        seen_request.add(key)
        prev_t = h_to_t.get(pair.homeroom_person_id)
        if prev_t is not None and prev_t != pair.teaching_person_id:
            raise InvalidScopeParam(
                "homeroom person mapped to multiple teaching persons",
                details={"homeroom_person_id": pair.homeroom_person_id},
            )
        prev_h = t_to_h.get(pair.teaching_person_id)
        if prev_h is not None and prev_h != pair.homeroom_person_id:
            raise InvalidScopeParam(
                "teaching person mapped to multiple homeroom persons",
                details={"teaching_person_id": pair.teaching_person_id},
            )
        h_to_t[pair.homeroom_person_id] = pair.teaching_person_id
        t_to_h[pair.teaching_person_id] = pair.homeroom_person_id
        unique_pairs.append(pair)

    # 双侧当期成员（复用名册查询：H 侧 status='active'、T 侧 membership
    # 均含有效期过滤），越界 person → 404
    h_member_ids = {
        item["person_id"] for item in q.homeroom_roster(db, link.admin_class_id, as_of)
    }
    t_member_ids = {
        item["person_id"]
        for item in q.teaching_roster(db, [link.teaching_class_id], as_of)
    }
    existing = q.linked_students_for_link(db, link.id)
    existing_pairs = {
        (ls.homeroom_identity_id, ls.teaching_identity_id) for ls in existing
    }
    existing_h = {ls.homeroom_identity_id for ls in existing}
    existing_t = {ls.teaching_identity_id for ls in existing}

    basis = req.confirm_basis or f"manual_confirm:{as_of.isoformat()}"
    to_create: list = []
    skipped = 0
    for pair in unique_pairs:
        if pair.homeroom_person_id not in h_member_ids:
            raise ResourceOutOfScope(
                "homeroom person not in linked class",
                details={"homeroom_person_id": pair.homeroom_person_id},
            )
        if pair.teaching_person_id not in t_member_ids:
            raise ResourceOutOfScope(
                "teaching person not in linked class",
                details={"teaching_person_id": pair.teaching_person_id},
            )
        if (pair.homeroom_person_id, pair.teaching_person_id) in existing_pairs:
            skipped += 1  # 幂等：完全相同的对跳过不报错
            continue
        if (
            pair.homeroom_person_id in existing_h
            or pair.teaching_person_id in existing_t
        ):
            # 到这里必是"同 h 不同 t / 同 t 不同 h"（完全相同的对已 continue）
            raise InvalidScopeParam(
                "person already linked to a different counterpart",
                details={
                    "homeroom_person_id": pair.homeroom_person_id,
                    "teaching_person_id": pair.teaching_person_id,
                },
            )
        to_create.append(pair)

    # 全部通过后一次性写入（失败路径在此之前均已 raise，零写入）
    for pair in to_create:
        db.add(
            LinkedStudent(
                link_id=link.id,
                homeroom_identity_id=pair.homeroom_person_id,
                teaching_identity_id=pair.teaching_person_id,
                confirm_basis=basis,
            )
        )
    db.commit()

    rows_by_pair = {
        (ls.homeroom_identity_id, ls.teaching_identity_id): ls
        for ls in q.linked_students_for_link(db, link.id)
    }
    return LinkStudentsCreateResponse(
        created=len(to_create),
        skipped=skipped,
        pairs=_pair_entries(
            db,
            [rows_by_pair[(p.homeroom_person_id, p.teaching_person_id)] for p in unique_pairs],
        ),
    )


@router.delete(
    "/shared/links/{link_id}/students/{linked_id}",
    response_model=LinkStudentDeleteResponse,
)
@domain_endpoint
def delete_link_student(link_id: int, linked_id: int, db: Session = Depends(get_db)):
    """撤销配对：即时停止该生跨域共享；不存在或属其他 link → 404。"""
    current_teacher_id(db)
    if db.get(HomeroomTeachingLink, link_id) is None:
        raise ResourceOutOfScope("link not found", details={"link_id": link_id})
    ls = (
        db.query(LinkedStudent)
        .filter(LinkedStudent.id == linked_id, LinkedStudent.link_id == link_id)
        .one_or_none()
    )
    if ls is None:
        raise ResourceOutOfScope(
            "linked student not found for this link",
            details={"link_id": link_id, "linked_id": linked_id},
        )
    db.delete(ls)
    db.commit()
    return LinkStudentDeleteResponse(success=True)


@router.post("/shared/links/{link_id}/share-scope", response_model=LinkShareScopeResponse)
@domain_endpoint
def update_link_share_scope(
    link_id: int, req: LinkShareScopeRequest, db: Session = Depends(get_db)
):
    """共享范围收紧/放宽（契约 §1.2.2）：仅 active link 可改；收紧即时
    生效（version+1 使缓存失效）。share_history_from 为 ISO 日期或 null
    （null 撤销历史授权）；字段缺省保持原值。"""
    current_teacher_id(db)
    link = db.get(HomeroomTeachingLink, link_id)
    if link is None:
        raise ResourceOutOfScope("link not found", details={"link_id": link_id})
    if link.status != "active":
        raise LinkVersionConflict(
            "link is not active", details={"link_id": link_id, "status": link.status}
        )

    if req.share_categories is not None:
        deduped: list = []
        for category in req.share_categories:
            if category not in ALLOWED_SHARE_CATEGORIES:
                raise InvalidScopeParam(
                    "unknown share category",
                    details={"share_categories": req.share_categories},
                )
            if category not in deduped:
                deduped.append(category)
        if not deduped:
            raise InvalidScopeParam(
                "share_categories must not be empty", details={"param": "share_categories"}
            )
        link.share_categories = ",".join(deduped)

    if req.share_history_from is not None:
        try:
            parsed = date.fromisoformat(req.share_history_from)
        except ValueError as exc:
            raise InvalidScopeParam(
                "share_history_from must be an ISO date (YYYY-MM-DD) or null",
                details={"share_history_from": req.share_history_from},
            ) from exc
        link.share_history_from = parsed
    elif "share_history_from" in (req.model_fields_set or set()):
        # 显式 null：撤销历史授权，回到 valid_from 下限
        link.share_history_from = None

    link.version = (link.version or 1) + 1
    db.commit()
    return LinkShareScopeResponse(
        link_id=link.id,
        version=link.version,
        share_categories=[
            part.strip()
            for part in (link.share_categories or "").split(",")
            if part.strip()
        ],
        share_history_from=(
            link.share_history_from.isoformat() if link.share_history_from else None
        ),
    )


# ────────────────────────────── §1 学年 / 学期管理（P4 契约补齐） ──────────────────────────────
# 本段为纯追加实现（docs/contracts/p4-students.md §1，P1 遗留项）：学年/学期
# 的基础 CRUD。import 置于段首而非文件头，保持对既有代码零改动（并行协作约束）。

from app.api.students_mgmt_schemas import (  # noqa: E402
    AcademicYearCreateRequest,
    AcademicYearItem,
    AcademicYearPatchRequest,
    AcademicYearsResponse,
    TermCreateRequest,
    TermItem,
    TermsResponse,
)


def _p4_parse_iso_date(value, field_name: str) -> date:
    """ISO 日期解析；失败抛 422 invalid_scope_param，保持统一错误 JSON 形态。"""
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InvalidScopeParam(
            f"{field_name} must be an ISO date (YYYY-MM-DD)",
            details={"param": field_name},
        ) from exc


@router.get("/shared/academic-years", response_model=AcademicYearsResponse)
@domain_endpoint
def list_academic_years(db: Session = Depends(get_db)):
    current_teacher_id(db)
    rows = (
        db.query(AcademicYear)
        .order_by(AcademicYear.start_date.desc(), AcademicYear.id.desc())
        .all()
    )
    return AcademicYearsResponse(
        years=[
            AcademicYearItem(
                id=ay.id,
                name=ay.name,
                start_date=ay.start_date.isoformat(),
                end_date=ay.end_date.isoformat(),
            )
            for ay in rows
        ]
    )


@router.post("/shared/academic-years", response_model=AcademicYearItem)
@domain_endpoint
def create_academic_year(req: AcademicYearCreateRequest, db: Session = Depends(get_db)):
    """建年后不自动建行政班/教学班（契约 §1）。"""
    current_teacher_id(db)
    name = (req.name or "").strip()
    if not name:
        raise InvalidScopeParam("name must be a non-empty string", details={"param": "name"})
    start = _p4_parse_iso_date(req.start_date, "start_date")
    end = _p4_parse_iso_date(req.end_date, "end_date")
    if start > end:
        raise InvalidScopeParam(
            "start_date must not be after end_date",
            details={"param": "start_date", "start_date": req.start_date, "end_date": req.end_date},
        )
    duplicate = db.query(AcademicYear).filter(AcademicYear.name == name).first()
    if duplicate is not None:
        raise InvalidScopeParam(
            "学年名称已存在", details={"name": name, "academic_year_id": duplicate.id}
        )
    ay = AcademicYear(name=name, start_date=start, end_date=end)
    db.add(ay)
    db.commit()
    return AcademicYearItem(
        id=ay.id, name=ay.name, start_date=ay.start_date.isoformat(), end_date=ay.end_date.isoformat()
    )


@router.patch("/shared/academic-years/{year_id}", response_model=AcademicYearItem)
@domain_endpoint
def patch_academic_year(
    year_id: int, req: AcademicYearPatchRequest, db: Session = Depends(get_db)
):
    """改名始终允许；已有业务数据引用（行政班/教学班/成绩事实）时改起止
    日期 → 422（防期间错乱，契约 §1）。"""
    current_teacher_id(db)
    ay = db.get(AcademicYear, year_id)
    if ay is None:
        raise ResourceOutOfScope("academic year not found", details={"academic_year_id": year_id})
    fields = req.model_fields_set or set()

    if "name" in fields and req.name is not None:
        new_name = req.name.strip()
        if not new_name:
            raise InvalidScopeParam("name must be a non-empty string", details={"param": "name"})
        taken = (
            db.query(AcademicYear)
            .filter(AcademicYear.name == new_name, AcademicYear.id != year_id)
            .first()
        )
        if taken is not None:
            raise InvalidScopeParam(
                "学年名称已存在", details={"name": new_name, "academic_year_id": taken.id}
            )
        ay.name = new_name

    date_change = ("start_date" in fields and req.start_date is not None) or (
        "end_date" in fields and req.end_date is not None
    )
    if date_change:
        # 查引用计数：任一业务表引用该学年 → 拒绝改日期（422）
        from app.db.workspace_models import ScoreFact, TeachingClass

        referenced = (
            db.query(AdministrativeClass.id)
            .filter(AdministrativeClass.academic_year_id == ay.id)
            .first()
            or db.query(TeachingClass.id)
            .filter(TeachingClass.academic_year_id == ay.id)
            .first()
            or db.query(ScoreFact.id)
            .filter(ScoreFact.academic_year_id == ay.id)
            .first()
        )
        if referenced is not None:
            raise InvalidScopeParam(
                "该学年已有班级/成绩数据引用，禁止修改起止日期",
                details={"academic_year_id": ay.id},
            )

    if "start_date" in fields and req.start_date is not None:
        ay.start_date = _p4_parse_iso_date(req.start_date, "start_date")
    if "end_date" in fields and req.end_date is not None:
        ay.end_date = _p4_parse_iso_date(req.end_date, "end_date")
    if ay.start_date > ay.end_date:
        raise InvalidScopeParam(
            "start_date must not be after end_date",
            details={"academic_year_id": ay.id},
        )
    db.commit()
    return AcademicYearItem(
        id=ay.id, name=ay.name, start_date=ay.start_date.isoformat(), end_date=ay.end_date.isoformat()
    )


@router.get("/shared/terms", response_model=TermsResponse)
@domain_endpoint
def list_terms(academic_year_id: Optional[int] = None, db: Session = Depends(get_db)):
    from app.db.workspace_models import Term

    current_teacher_id(db)
    if academic_year_id is None:
        raise InvalidScopeParam(
            "academic_year_id is required", details={"param": "academic_year_id"}
        )
    ay = q.resolve_year(db, academic_year_id)  # 不存在 → 404 resource_out_of_scope
    rows = (
        db.query(Term)
        .filter(Term.academic_year_id == ay.id)
        .order_by(Term.start_date.asc(), Term.id.asc())
        .all()
    )
    return TermsResponse(
        terms=[
            TermItem(
                id=t.id,
                academic_year_id=t.academic_year_id,
                name=t.name,
                start_date=t.start_date.isoformat(),
                end_date=t.end_date.isoformat(),
            )
            for t in rows
        ]
    )


@router.post("/shared/terms", response_model=TermItem)
@domain_endpoint
def create_term(req: TermCreateRequest, db: Session = Depends(get_db)):
    from app.db.workspace_models import Term

    current_teacher_id(db)
    ay = q.resolve_year(db, req.academic_year_id)
    name = (req.name or "").strip()
    if not name:
        raise InvalidScopeParam("name must be a non-empty string", details={"param": "name"})
    start = _p4_parse_iso_date(req.start_date, "start_date")
    end = _p4_parse_iso_date(req.end_date, "end_date")
    if start > end:
        raise InvalidScopeParam(
            "start_date must not be after end_date",
            details={"param": "start_date", "start_date": req.start_date, "end_date": req.end_date},
        )
    duplicate = (
        db.query(Term).filter(Term.academic_year_id == ay.id, Term.name == name).first()
    )
    if duplicate is not None:
        # 同一学年下学期重名 → 422（不同学年同名合法）
        raise InvalidScopeParam(
            "该学年下学期名称已存在",
            details={"academic_year_id": ay.id, "name": name, "term_id": duplicate.id},
        )
    term = Term(academic_year_id=ay.id, name=name, start_date=start, end_date=end)
    db.add(term)
    db.commit()
    return TermItem(
        id=term.id,
        academic_year_id=term.academic_year_id,
        name=term.name,
        start_date=term.start_date.isoformat(),
        end_date=term.end_date.isoformat(),
    )
