"""/api/v1 班主任学生管理 / 换届 / 档案（P4，契约 docs/contracts/p4-students.md）。

- §2.1 名册 CRUD：作用域 = 绑定行政班 + 学年（resolve_workspace_context，
  class_id 可选缺省绑定班）。建档/换号绝不物理删除；离班改当期 Enrollment
  行的 status+valid_to（恢复 active 清 valid_to），关联共享由读侧既有门即时生效。
- §2.2 换届：preview 零业务写入（仅 import_batch 台账）；confirm/undo 的
  token 生命周期复用 shared._load_preview_batch（R4 同语义：pending/未过期/
  成员无漂移，已消费一律 409）。confirm 单事务建新学年行政班 + 每生
  Enrollment + 新学段 alias（旧 alias 收尾为新学年 start_date 前一日）；
  undo 按 confirm 快照精确回滚，换届后已有新写入（成绩/作业/档案）的学生
  逐人列为 conflicted 跳过撤销，绝不静默覆盖后续合法数据。
- §2.3 学生报告：画像取数复用 _queries 与 students.py 画像同一套门/冲突规则
  （R1 只取目标人）；档案摘要只统计 homeroom 域（N01）。
- §4 档案 notes：ws_student_note，N01 域隔离红线——各 mode 只读写本域
  note，person 必须在该 mode 当前作用域（越界一律 404）。
"""

import hashlib
import json
import secrets
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, not_, or_
from sqlalchemy.orm import Session

from app.api import _queries as q
from app.api import current_teacher_id, domain_endpoint
from app.api.schemas import SubjectGroup, TotalGroup
from app.api.shared import _homeroom_binding, _load_preview_batch
from app.api.students import _exam_entry, _metadata as _profile_metadata
from app.api.students_mgmt_schemas import (
    AliasItem,
    AliasesResponse,
    NoteCreateRequest,
    NoteDeleteResponse,
    NoteItem,
    NotePatchRequest,
    NotesResponse,
    NotesSummary,
    ReportPerson,
    ReportRoster,
    RolloverConfirmRequest,
    RolloverConfirmResponse,
    RolloverConflictedStudent,
    RolloverPreviewResponse,
    RolloverPreviewStudent,
    RolloverUndoResponse,
    RolloverYearInfo,
    NOTE_CATEGORIES,
    NOTE_FOLLOW_UP_STATUSES,
    NOTE_INTERVENTION_FIELDS,
    StudentAliasAppendRequest,
    StudentArchiveRequest,
    StudentArchiveResponse,
    StudentCreateRequest,
    StudentMutationResponse,
    StudentPatchRequest,
    StudentReportResponse,
    TeachingMemberImportRequest,
    TeachingMemberItem,
    TeachingMembersResponse,
    TeachingRolloverClassResult,
    TeachingRolloverConfirmResponse,
    TeachingSyncRequest,
)
from app.core.context import WorkspaceContext, resolve_workspace_context
from app.core.errors import (
    DomainError,
    InvalidScopeParam,
    LinkVersionConflict,
    ResourceOutOfScope,
    WorkspaceNotConfigured,
)
from app.db.models import get_db
from app.db.workspace_models import (
    AcademicYear,
    AdministrativeClass,
    Enrollment,
    HomeworkAssignment,
    HomeworkSubmission,
    HomeroomTeachingLink,
    ImportBatch,
    LinkedStudent,
    ScoreFact,
    TeachingClass,
    TeachingClassMember,
    WsStudentAlias,
    WsStudentIdentity,
    WsStudentNote,
)

router = APIRouter(tags=["students-mgmt"])

# 离班状态枚举（契约 §2.1）：active 表示恢复在班
ARCHIVE_STATUSES = ("transferred", "graduated", "active")
_GRADE_LABELS = {1: "高一", 2: "高二", 3: "高三"}


def _human_notes_filter():
    """只查询教师手动填写的成长/谈话档案，排除系统预警及历史特殊记录等内部辅助标记。"""
    return or_(
        WsStudentNote.source.is_(None),
        not_(
            WsStudentNote.source.like("homework:%")
            | WsStudentNote.source.like("warning_dismissal:%")
            | WsStudentNote.source.like("migration:%")
        ),
    )


# ────────────────────────────── 通用助手 ──────────────────────────────


def _exam_date_before(exam_date: str, anchor: date) -> bool:
    """考试日期字符串是否严格早于锚点（月精度/不可解析 → False，
    与 review._is_strictly_before 同一纪律）。"""
    try:
        return date.fromisoformat(exam_date) < anchor
    except (ValueError, TypeError):
        return False


def _parse_iso_date(value, field_name: str) -> date:
    """ISO 日期解析；失败抛 422，保持统一错误 JSON 形态（不走 FastAPI 校验）。"""
    if not isinstance(value, str):
        raise InvalidScopeParam(
            f"{field_name} must be an ISO date (YYYY-MM-DD)", details={"param": field_name}
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidScopeParam(
            f"{field_name} must be an ISO date (YYYY-MM-DD)", details={"param": field_name}
        ) from exc


def _homeroom_ctx(
    db: Session,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
) -> WorkspaceContext:
    """班主任侧作用域：class_id 缺省 = 绑定班（core.context 口径）。"""
    teacher_id = current_teacher_id(db)
    params: dict = {}
    if academic_year_id is not None:
        params["academic_year_id"] = academic_year_id
    if term_id is not None:
        params["term_id"] = term_id
    if class_id is not None:
        params["class_id"] = class_id
    return resolve_workspace_context(db, teacher_id, "homeroom", params)


def _teaching_ctx(
    db: Session,
    academic_year_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    subject: Optional[str] = None,
) -> WorkspaceContext:
    teacher_id = current_teacher_id(db)
    params, _subject, _ids = q.build_teaching_params(
        db, academic_year_id, teaching_class_id, subject, term_id
    )
    return resolve_workspace_context(db, teacher_id, "teaching", params)


def _mode_ctx(db: Session, mode: str, **params) -> WorkspaceContext:
    """§4 notes 的 mode 分派：mode 只选解析路径，权限由绑定/成员推导。"""
    if mode == "homeroom":
        return _homeroom_ctx(
            db, params.get("academic_year_id"), params.get("class_id"), params.get("term_id")
        )
    if mode == "teaching":
        return _teaching_ctx(
            db,
            params.get("academic_year_id"),
            params.get("teaching_class_id"),
            params.get("term_id"),
            params.get("subject"),
        )
    raise InvalidScopeParam("mode must be 'homeroom' or 'teaching'", details={"param": "mode"})


def _strict_current_enrollment(
    db: Session, admin_class_id: int, person_id: int, as_of: date
) -> Optional[Enrollment]:
    """当期在班行：active 且有效期覆盖 as_of（PATCH/报告的成员口径）。"""
    return (
        db.query(Enrollment)
        .filter(
            Enrollment.admin_class_id == admin_class_id,
            Enrollment.identity_id == person_id,
            Enrollment.status == "active",
            Enrollment.valid_from <= as_of,
            or_(Enrollment.valid_to.is_(None), Enrollment.valid_to >= as_of),
        )
        .order_by(Enrollment.valid_from.desc(), Enrollment.id.desc())
        .first()
    )


def _current_enrollment(
    db: Session, admin_class_id: int, person_id: int, as_of: date
) -> Optional[Enrollment]:
    """archive 目标行：优先当期覆盖行；无覆盖行取最近一行（恢复在班场景）。"""
    row = (
        db.query(Enrollment)
        .filter(
            Enrollment.admin_class_id == admin_class_id,
            Enrollment.identity_id == person_id,
            Enrollment.valid_from <= as_of,
            or_(Enrollment.valid_to.is_(None), Enrollment.valid_to >= as_of),
        )
        .order_by(Enrollment.valid_from.desc(), Enrollment.id.desc())
        .first()
    )
    if row is not None:
        return row
    return (
        db.query(Enrollment)
        .filter(
            Enrollment.admin_class_id == admin_class_id,
            Enrollment.identity_id == person_id,
        )
        .order_by(Enrollment.valid_from.desc(), Enrollment.id.desc())
        .first()
    )


def _require_bound_class_person(db: Session, ctx: WorkspaceContext, person_id: int) -> None:
    """person 必须属于绑定行政班（任意时期学籍行，含已离班），越界 404。"""
    owned = (
        db.query(Enrollment.id)
        .filter(
            Enrollment.admin_class_id == ctx.class_ids[0],
            Enrollment.identity_id == person_id,
        )
        .first()
    )
    if owned is None:
        raise ResourceOutOfScope(
            "person not in bound homeroom class", details={"person_id": person_id}
        )


def _alias_conflicts(
    db: Session, domain: str, alias_value: str, owner_identity_id: int, at_year: AcademicYear
) -> List:
    """同域同号已属他人（at_year 当学年或更早历史，含未标学年登记）的行列表。
    同一 identity 的重复登记不算冲突（由唯一约束/幂等守卫处理）。"""
    rows = (
        db.query(WsStudentAlias, AcademicYear)
        .outerjoin(AcademicYear, AcademicYear.id == WsStudentAlias.academic_year_id)
        .filter(
            WsStudentAlias.data_domain == domain,
            WsStudentAlias.alias_value == alias_value,
            WsStudentAlias.identity_id != owner_identity_id,
        )
        .all()
    )
    return [
        (alias, year)
        for alias, year in rows
        if year is None or year.start_date <= at_year.start_date
    ]


def _conflict_details(db: Session, conflicts: List) -> List[dict]:
    names = q.names_for(db, [alias.identity_id for alias, _ in conflicts])
    return [
        {
            "person_id": alias.identity_id,
            "name": names.get(alias.identity_id),
            "alias": alias.alias_value,
        }
        for alias, _ in conflicts
    ]


def _year_containing(db: Session, on_date: date, fallback_year_id: int) -> AcademicYear:
    """valid_from 落入的学年（start<=d<=end，取最近）；无覆盖学年回退当期。"""
    ay = (
        db.query(AcademicYear)
        .filter(AcademicYear.start_date <= on_date, AcademicYear.end_date >= on_date)
        .order_by(AcademicYear.start_date.desc(), AcademicYear.id.desc())
        .first()
    )
    if ay is not None:
        return ay
    return db.get(AcademicYear, fallback_year_id)


def _aliases_response(db: Session, person_id: int) -> AliasesResponse:
    rows = (
        db.query(WsStudentAlias)
        .filter(
            WsStudentAlias.identity_id == person_id,
            WsStudentAlias.data_domain == "homeroom",
        )
        .order_by(WsStudentAlias.id.asc())
        .all()
    )
    return AliasesResponse(
        aliases=[
            AliasItem(
                id=row.id,
                alias_value=row.alias_value,
                valid_from=row.valid_from.isoformat() if row.valid_from else None,
                valid_to=row.valid_to.isoformat() if row.valid_to else None,
            )
            for row in rows
        ]
    )


def _note_item(note: WsStudentNote) -> NoteItem:
    return NoteItem(
        id=note.id,
        person_id=note.person_id,
        date=note.date.isoformat(),
        category=note.category,
        content=note.content,
        follow_up=note.follow_up,
        follow_up_done=note.follow_up_done,
        # ── P2-C4 干预扩展（全可空；普通/旧档案一律 null） ──
        problem=note.problem,
        subject_scope=note.subject_scope,
        measures=note.measures,
        target_metric=note.target_metric,
        baseline_value=note.baseline_value,
        start_date=note.start_date.isoformat() if note.start_date else None,
        review_date=note.review_date.isoformat() if note.review_date else None,
        status=note.status,
        created_at=note.created_at.isoformat() if note.created_at else None,
    )


# ── P2-C4 轻量干预（契约 docs/diagnosis-roadmap/p2-contracts.md §5） ──


class DuplicateFollowUp(DomainError):
    """同人同科已有未关闭干预（契约 §5.1 防重复录入提示）。

    非阻断式红线：教师确认知情后带 ``force=true`` 重发即可仍建；
    existing 携带未关闭干预明细供前端提示卡展示。"""

    status_code = 409
    code = "duplicate_follow_up"


def _is_intervention_request(req: NoteCreateRequest) -> bool:
    """创建请求是否按干预建档：出现任一干预扩展列（显式传入，含 null）即算。"""
    sent = req.model_fields_set
    return any(name in sent for name in NOTE_INTERVENTION_FIELDS)


def _open_duplicate_interventions(
    db: Session, ctx: WorkspaceContext, person_id: int, subject_scope: Optional[str]
) -> List[WsStudentNote]:
    """同人同科（subject_scope 逐字相等，None=None 视为同科）未关闭干预。"""
    rows = (
        db.query(WsStudentNote)
        .filter(
            WsStudentNote.data_domain == ctx.mode,
            WsStudentNote.person_id == person_id,
            WsStudentNote.status == "open",
            _human_notes_filter(),
        )
        .order_by(WsStudentNote.date.desc(), WsStudentNote.id.desc())
        .all()
    )
    return [n for n in rows if n.subject_scope == subject_scope]


def _sync_follow_up_close(note: WsStudentNote) -> None:
    """干预行 status 与 follow_up_done 的收口同步（契约 §5.4 不碰旧列语义）。

    - status 变更后调用：done/dismissed 为关闭态 → follow_up_done=1；
      open → follow_up_done=0（B1 teacher_attention 只读 follow_up_done，
      同步保证「未关闭干预」在特征层不重复计数）。
    - 仅对干预行（status 非空）生效；旧档案（status=NULL）行为一律不变。
    """
    if note.status is None:
        return
    note.follow_up_done = 0 if note.status == "open" else 1


def _apply_follow_up_done(note: WsStudentNote, value: int) -> None:
    """旧关闭路径（follow_up_done 0/1）在干预行上的状态镜像。

    follow_up_done 语义本身不变：1=跟进已关闭、0=未关闭。干预行额外把
    status 镜像到 done/open，保证两条关闭路径殊途同归；旧档案不镜像。"""
    note.follow_up_done = 1 if value else 0
    if note.status is None:
        return
    if value and note.status == "open":
        note.status = "done"
    elif not value and note.status in ("done", "dismissed"):
        note.status = "open"


# ────────────────────────────── §3 教学班成员 ──────────────────────────────


def _teaching_class_scope(
    db: Session, teaching_class_id: int
) -> tuple[TeachingClass, AcademicYear, WorkspaceContext, date]:
    """解析单个教学班并把写入日期夹在所属学年内。"""
    tc = db.get(TeachingClass, teaching_class_id)
    if tc is None:
        raise ResourceOutOfScope(
            "teaching class not found", details={"teaching_class_id": teaching_class_id}
        )
    ctx = _teaching_ctx(db, tc.academic_year_id, tc.id, subject=tc.subject)
    ay = db.get(AcademicYear, tc.academic_year_id)
    effective_on = min(max(ctx.as_of, ay.start_date), ay.end_date)
    return tc, ay, ctx, effective_on


def _active_teaching_link(
    db: Session, tc: TeachingClass, effective_on: date
) -> Optional[HomeroomTeachingLink]:
    return q.active_link_for_teaching_class(
        db, tc.id, tc.academic_year_id, effective_on
    )


def _reject_direct_linked_roster_change(
    db: Session, tc: TeachingClass, effective_on: date
) -> None:
    link = _active_teaching_link(db, tc, effective_on)
    if link is not None:
        raise LinkVersionConflict(
            "关联班名册由行政班和已确认配对共同维护；请到班主任侧添加学生或更新配对",
            details={"link_id": link.id, "teaching_class_id": tc.id},
        )


def _teaching_member_item(
    member: TeachingClassMember,
    identity: WsStudentIdentity,
    alias: Optional[str],
) -> TeachingMemberItem:
    return TeachingMemberItem(
        person_id=member.identity_id,
        name=identity.display_name,
        alias=alias,
        valid_from=member.valid_from.isoformat() if member.valid_from else None,
        valid_to=member.valid_to.isoformat() if member.valid_to else None,
        source=member.source,
    )


def _active_teaching_member_ids(
    db: Session, teaching_class_id: int, effective_on: date
) -> set[int]:
    return {
        row[0]
        for row in db.query(TeachingClassMember.identity_id)
        .filter(
            TeachingClassMember.teaching_class_id == teaching_class_id,
            TeachingClassMember.valid_from <= effective_on,
            or_(
                TeachingClassMember.valid_to.is_(None),
                TeachingClassMember.valid_to >= effective_on,
            ),
        )
        .distinct()
        .all()
    }


@router.get(
    "/teaching/classes/{teaching_class_id}/members",
    response_model=TeachingMembersResponse,
)
@domain_endpoint
def list_teaching_members(teaching_class_id: int, db: Session = Depends(get_db)):
    """返回该教学班完整成员史；前端按 active/left 明确分组。"""
    tc, ay, _ctx, effective_on = _teaching_class_scope(db, teaching_class_id)
    rows = (
        db.query(TeachingClassMember, WsStudentIdentity)
        .join(WsStudentIdentity, WsStudentIdentity.id == TeachingClassMember.identity_id)
        .filter(
            TeachingClassMember.teaching_class_id == tc.id,
            WsStudentIdentity.data_domain == "teaching",
        )
        .order_by(TeachingClassMember.valid_from.asc(), TeachingClassMember.id.asc())
        .all()
    )
    aliases = q.aliases_for(
        db, [member.identity_id for member, _ in rows], "teaching", tc.academic_year_id
    )
    active: list[TeachingMemberItem] = []
    left: list[TeachingMemberItem] = []
    for member, identity in rows:
        item = _teaching_member_item(member, identity, aliases.get(member.identity_id))
        # 本接口没有独立 status 列；按契约以 valid_to 是否为空分列。
        target = active if member.valid_to is None else left
        target.append(item)
    return TeachingMembersResponse(active=active, left=left)


@router.post("/teaching/classes/{teaching_class_id}/members")
@domain_endpoint
def create_teaching_member(
    teaching_class_id: int,
    req: StudentCreateRequest,
    db: Session = Depends(get_db),
):
    tc, ay, _ctx, effective_on = _teaching_class_scope(db, teaching_class_id)
    _reject_direct_linked_roster_change(db, tc, effective_on)
    name = (req.name or "").strip()
    if not name:
        raise InvalidScopeParam("name must be a non-empty string", details={"param": "name"})
    alias = (req.alias or "").strip() or None
    if alias is not None:
        conflicts = _alias_conflicts(db, "teaching", alias, 0, ay)
        if conflicts:
            raise InvalidScopeParam(
                "学号在本学年已属于其他教学域学生",
                details={"conflicts": _conflict_details(db, conflicts)},
            )
    identity = WsStudentIdentity(data_domain="teaching", display_name=name)
    db.add(identity)
    db.flush()
    if alias is not None:
        db.add(
            WsStudentAlias(
                identity_id=identity.id,
                alias_value=alias,
                data_domain="teaching",
                academic_year_id=ay.id,
                alias_scope=str(ay.id),
                source="manual",
                valid_from=effective_on,
            )
        )
    db.add(
        TeachingClassMember(
            teaching_class_id=tc.id,
            identity_id=identity.id,
            valid_from=effective_on,
            source="manual",
        )
    )
    db.commit()
    return {"person_id": identity.id, "name": name, "alias": alias, "status": "active"}


@router.delete("/teaching/classes/{teaching_class_id}/members/{person_id}")
@domain_endpoint
def remove_teaching_member(
    teaching_class_id: int, person_id: int, db: Session = Depends(get_db)
):
    tc, ay, _ctx, effective_on = _teaching_class_scope(db, teaching_class_id)
    _reject_direct_linked_roster_change(db, tc, effective_on)
    member = (
        db.query(TeachingClassMember)
        .filter(
            TeachingClassMember.teaching_class_id == tc.id,
            TeachingClassMember.identity_id == person_id,
            TeachingClassMember.valid_from <= effective_on,
            or_(
                TeachingClassMember.valid_to.is_(None),
                TeachingClassMember.valid_to >= effective_on,
            ),
        )
        .order_by(TeachingClassMember.valid_from.desc(), TeachingClassMember.id.desc())
        .first()
    )
    if member is None:
        raise ResourceOutOfScope(
            "person not in current teaching roster", details={"person_id": person_id}
        )
    member.valid_to = (
        effective_on - timedelta(days=1)
        if member.valid_from < effective_on
        else effective_on
    )
    db.commit()
    identity = db.get(WsStudentIdentity, person_id)
    alias = q.aliases_for(db, [person_id], "teaching", ay.id).get(person_id)
    return {
        "person_id": person_id,
        "name": identity.display_name if identity else None,
        "alias": alias,
        "status": "left",
        "valid_to": member.valid_to.isoformat(),
    }


def _parse_teaching_import_lines(
    db: Session, text: str, ay: AcademicYear
) -> list[dict]:
    parsed: list[dict] = []
    seen_aliases: set[str] = set()
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split()
        alias = parts[0] if len(parts) > 1 else None
        name = " ".join(parts[1:]).strip() if alias else parts[0]
        item = {"raw": raw, "name": name, "alias": alias, "kind": "new"}
        if not name:
            item.update(kind="invalid", reason="姓名为空")
        elif alias and alias in seen_aliases:
            item.update(kind="invalid", reason="导入文本内学号重复")
        elif alias:
            seen_aliases.add(alias)
            conflicts = _alias_conflicts(db, "teaching", alias, 0, ay)
            if len(conflicts) == 1:
                existing_alias, _year = conflicts[0]
                identity = db.get(WsStudentIdentity, existing_alias.identity_id)
                if identity is not None and identity.display_name == name:
                    item.update(kind="match", person_id=identity.id)
                else:
                    item.update(kind="invalid", reason="学号已属于其他姓名")
            elif len(conflicts) > 1:
                item.update(kind="invalid", reason="学号对应多个历史身份，需人工处理")
        parsed.append(item)
    return parsed


@router.post("/teaching/classes/{teaching_class_id}/members/import")
@domain_endpoint
def import_teaching_members(
    teaching_class_id: int,
    req: TeachingMemberImportRequest,
    db: Session = Depends(get_db),
):
    tc, ay, _ctx, effective_on = _teaching_class_scope(db, teaching_class_id)
    _reject_direct_linked_roster_change(db, tc, effective_on)
    has_text = req.text is not None
    has_token = req.token is not None
    if has_text == has_token:
        raise InvalidScopeParam(
            "request must contain exactly one of text or token",
            details={"params": ["text", "token"]},
        )

    if has_text:
        text = (req.text or "").strip()
        if not text:
            raise InvalidScopeParam("text must not be empty", details={"param": "text"})
        lines = _parse_teaching_import_lines(db, text, ay)
        if not lines:
            raise InvalidScopeParam("text contains no student rows", details={"param": "text"})
        token = secrets.token_hex(16)
        expires_at = datetime.utcnow() + timedelta(minutes=q.PREVIEW_TTL_MINUTES)
        snapshot = {
            "kind": "teaching_member_import",
            "teaching_class_id": tc.id,
            "academic_year_id": ay.id,
            "subject": tc.subject,
            "effective_on": effective_on.isoformat(),
            "member_ids": sorted(_active_teaching_member_ids(db, tc.id, effective_on)),
            "lines": lines,
        }
        db.add(
            ImportBatch(
                token=token,
                data_domain="teaching",
                scope_json=json.dumps(snapshot, ensure_ascii=False),
                content_digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                status="pending",
                expires_at=expires_at,
            )
        )
        db.commit()
        return {"token": token, "expires_at": expires_at.isoformat(), "lines": lines}

    batch = _load_preview_batch(db, req.token or "")
    snapshot = json.loads(batch.scope_json or "{}")
    if batch.data_domain != "teaching" or snapshot.get("kind") != "teaching_member_import":
        raise LinkVersionConflict("token is not a teaching member import preview")
    if (
        snapshot.get("teaching_class_id") != tc.id
        or snapshot.get("academic_year_id") != ay.id
        or snapshot.get("subject") != tc.subject
        or snapshot.get("effective_on") != effective_on.isoformat()
    ):
        raise LinkVersionConflict("教学班作用域已变化，请重新预览")
    current_ids = sorted(_active_teaching_member_ids(db, tc.id, effective_on))
    if current_ids != snapshot.get("member_ids"):
        raise LinkVersionConflict(
            "教学班成员已变化，请重新预览",
            details={"snapshot_member_ids": snapshot.get("member_ids"), "current_member_ids": current_ids},
        )
    # 预览后新建关联时，确认也必须拒绝，不能绕过 H 权威名册规则。
    _reject_direct_linked_roster_change(db, tc, effective_on)

    added_count = 0
    invalid_count = 0
    for line in snapshot.get("lines") or []:
        if line.get("kind") == "invalid":
            invalid_count += 1
            continue
        identity = None
        if line.get("kind") == "match":
            identity = db.get(WsStudentIdentity, line.get("person_id"))
            if identity is None or identity.data_domain != "teaching":
                raise LinkVersionConflict("预览匹配的学生身份已变化，请重新预览")
        else:
            alias = line.get("alias")
            if alias and _alias_conflicts(db, "teaching", alias, 0, ay):
                raise LinkVersionConflict("学号归属已变化，请重新预览", details={"alias": alias})
            identity = WsStudentIdentity(
                data_domain="teaching", display_name=line.get("name")
            )
            db.add(identity)
            db.flush()
            if alias:
                db.add(
                    WsStudentAlias(
                        identity_id=identity.id,
                        alias_value=alias,
                        data_domain="teaching",
                        academic_year_id=ay.id,
                        alias_scope=str(ay.id),
                        source="text_import",
                        valid_from=effective_on,
                    )
                )
        if identity.id not in current_ids:
            db.add(
                TeachingClassMember(
                    teaching_class_id=tc.id,
                    identity_id=identity.id,
                    valid_from=effective_on,
                    source="text_import",
                )
            )
            current_ids.append(identity.id)
            added_count += 1
    batch.status = "confirmed"
    db.commit()
    return {"added_count": added_count, "invalid_count": invalid_count}


def _sync_diff(
    db: Session,
    tc: TeachingClass,
    link: HomeroomTeachingLink,
    effective_on: date,
) -> tuple[list[dict], list[dict]]:
    pairs = db.query(LinkedStudent).filter(LinkedStudent.link_id == link.id).all()
    if not pairs:
        return [], []
    active_h_ids = {
        row[0]
        for row in db.query(Enrollment.identity_id)
        .filter(
            Enrollment.admin_class_id == link.admin_class_id,
            Enrollment.identity_id.in_([pair.homeroom_identity_id for pair in pairs]),
            Enrollment.status == "active",
            Enrollment.valid_from <= effective_on,
            or_(Enrollment.valid_to.is_(None), Enrollment.valid_to >= effective_on),
        )
        .all()
    }
    eligible_t_ids = [
        pair.teaching_identity_id
        for pair in pairs
        if pair.homeroom_identity_id in active_h_ids
    ]
    active_t_ids = _active_teaching_member_ids(db, tc.id, effective_on)
    names = q.names_for(db, eligible_t_ids)
    aliases = q.aliases_for(db, eligible_t_ids, "teaching", tc.academic_year_id)
    to_add: list[dict] = []
    already: list[dict] = []
    for person_id in eligible_t_ids:
        item = {"person_id": person_id, "name": names.get(person_id), "alias": aliases.get(person_id)}
        (already if person_id in active_t_ids else to_add).append(item)
    return to_add, already


@router.post("/teaching/classes/{teaching_class_id}/sync-from-homeroom")
@domain_endpoint
def sync_teaching_members_from_homeroom(
    teaching_class_id: int,
    req: TeachingSyncRequest,
    db: Session = Depends(get_db),
):
    tc, _ay, _ctx, effective_on = _teaching_class_scope(db, teaching_class_id)
    link = _active_teaching_link(db, tc, effective_on)
    if link is None:
        raise LinkVersionConflict(
            "该教学班没有生效中的行政班关联，不能同步",
            details={"teaching_class_id": tc.id},
        )
    to_add, already = _sync_diff(db, tc, link, effective_on)
    if not req.confirm:
        return {"to_add": to_add, "already_synced": already, "link_version": link.version}
    for item in to_add:
        db.add(
            TeachingClassMember(
                teaching_class_id=tc.id,
                identity_id=item["person_id"],
                valid_from=effective_on,
                source="link_projection",
            )
        )
    db.commit()
    return {"added_count": len(to_add), "link_version": link.version}


# ────────────────────────────── §2.1 名册 CRUD ──────────────────────────────


@router.post("/homeroom/students", response_model=StudentMutationResponse)
@domain_endpoint
def create_student(
    req: StudentCreateRequest,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """新建学生：identity(homeroom) + alias(本学年) + Enrollment(active)。
    alias 同学年同域已属他人 → 422 列冲突人（绝不自动改名合并）。"""
    ctx = _homeroom_ctx(db, academic_year_id, class_id)
    name = (req.name or "").strip()
    if not name:
        raise InvalidScopeParam("name must be a non-empty string", details={"param": "name"})
    alias = (req.alias or "").strip() or None
    ay = db.get(AcademicYear, ctx.academic_year_id)

    if alias is not None:
        # 与 §2.1 追加学号同口径：当学年或更早（含未标学年登记）已属他人
        # 即冲突——不能只看"同学年精确匹配"，种子/历史行可能不带学年
        conflicts = _alias_conflicts(db, "homeroom", alias, 0, ay)
        if conflicts:
            raise InvalidScopeParam(
                "学号在本学年已属于其他学生",
                details={"conflicts": _conflict_details(db, conflicts)},
            )

    # 学籍/学号生效日 = 学年 start_date 与今天较晚者（与导入口径一致）
    valid_from = max(ay.start_date, ctx.as_of)
    identity = WsStudentIdentity(data_domain="homeroom", display_name=name)
    db.add(identity)
    db.flush()
    if alias is not None:
        db.add(
            WsStudentAlias(
                identity_id=identity.id,
                alias_value=alias,
                data_domain="homeroom",
                academic_year_id=ay.id,
                alias_scope=str(ay.id),
                source="manual",
                valid_from=valid_from,
            )
        )
    db.add(
        Enrollment(
            admin_class_id=ctx.class_ids[0],
            identity_id=identity.id,
            seat_no=req.seat_no,
            status="active",
            valid_from=valid_from,
        )
    )
    db.commit()
    return StudentMutationResponse(
        person_id=identity.id, name=name, alias=alias, seat_no=req.seat_no, status="active"
    )


@router.patch("/homeroom/students/{person_id}", response_model=StudentMutationResponse)
@domain_endpoint
def patch_student(
    person_id: int,
    req: StudentPatchRequest,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """仅本班当期在班成员可改（越界 404）；字段缺省保持原值。"""
    ctx = _homeroom_ctx(db, academic_year_id, class_id)
    enrollment = _strict_current_enrollment(db, ctx.class_ids[0], person_id, ctx.as_of)
    if enrollment is None:
        raise ResourceOutOfScope(
            "person not in current homeroom roster", details={"person_id": person_id}
        )
    identity = db.get(WsStudentIdentity, person_id)
    fields = req.model_fields_set or set()

    if "name" in fields and req.name is not None:
        stripped = req.name.strip()
        if not stripped:
            raise InvalidScopeParam("name must be a non-empty string", details={"param": "name"})
        identity.display_name = stripped
    if "seat_no" in fields:
        enrollment.seat_no = req.seat_no
    db.commit()

    alias_map = q.aliases_for(db, [person_id], "homeroom", ctx.academic_year_id)
    return StudentMutationResponse(
        person_id=person_id,
        name=identity.display_name,
        alias=alias_map.get(person_id),
        seat_no=enrollment.seat_no,
        status=enrollment.status,
    )


@router.post("/homeroom/students/{person_id}/archive", response_model=StudentArchiveResponse)
@domain_endpoint
def archive_student(
    person_id: int,
    req: StudentArchiveRequest,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """离班/恢复：改当期 Enrollment 行的 status + valid_to，绝不物理删除；
    active 恢复清空 valid_to。共享影响由读侧既有门即时生效，无需额外处理。"""
    if req.status not in ARCHIVE_STATUSES:
        raise InvalidScopeParam(
            "status must be one of transferred/graduated/active",
            details={"param": "status", "status": req.status},
        )
    ctx = _homeroom_ctx(db, academic_year_id, class_id)
    enrollment = _current_enrollment(db, ctx.class_ids[0], person_id, ctx.as_of)
    if enrollment is None:
        raise ResourceOutOfScope(
            "person not in bound homeroom class", details={"person_id": person_id}
        )

    valid_to = None
    if req.status == "active":
        # 恢复在班：清空 valid_to（契约 §2.1）
        enrollment.status = "active"
        enrollment.valid_to = None
    else:
        if req.valid_to is None:
            raise InvalidScopeParam(
                "valid_to is required when archiving", details={"param": "valid_to"}
            )
        valid_to = _parse_iso_date(req.valid_to, "valid_to")
        if enrollment.valid_from is not None and valid_to < enrollment.valid_from:
            raise InvalidScopeParam(
                "valid_to must not be earlier than valid_from",
                details={"param": "valid_to", "valid_to": req.valid_to},
            )
        enrollment.status = req.status
        enrollment.valid_to = valid_to
    db.commit()
    return StudentArchiveResponse(
        person_id=person_id,
        status=enrollment.status,
        valid_to=valid_to.isoformat() if valid_to else None,
    )


@router.post("/homeroom/students/{person_id}/alias", response_model=AliasesResponse)
@domain_endpoint
def append_student_alias(
    person_id: int,
    req: StudentAliasAppendRequest,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """追加新学号（换号接续，S08）：identity 不变；旧 alias 行收尾
    valid_to=valid_from 前一日；同号新号已属他人（同域同学年或历史）→ 422。"""
    ctx = _homeroom_ctx(db, academic_year_id, class_id)
    _require_bound_class_person(db, ctx, person_id)
    if db.get(WsStudentIdentity, person_id) is None:
        raise ResourceOutOfScope("person not found", details={"person_id": person_id})
    alias_value = (req.alias or "").strip()
    if not alias_value:
        raise InvalidScopeParam("alias must be a non-empty string", details={"param": "alias"})
    valid_from = _parse_iso_date(req.valid_from, "valid_from")
    new_year = _year_containing(db, valid_from, ctx.academic_year_id)

    conflicts = _alias_conflicts(db, "homeroom", alias_value, person_id, new_year)
    if conflicts:
        raise InvalidScopeParam(
            "新学号已属于其他学生（同学年或历史）",
            details={"conflicts": _conflict_details(db, conflicts)},
        )
    # 同人同学年重复登记同号会被唯一键拒绝，提前给 422 而非 500
    duplicate = (
        db.query(WsStudentAlias)
        .filter(
            WsStudentAlias.identity_id == person_id,
            WsStudentAlias.data_domain == "homeroom",
            WsStudentAlias.alias_value == alias_value,
            WsStudentAlias.academic_year_id == new_year.id,
        )
        .first()
    )
    if duplicate is not None:
        raise InvalidScopeParam(
            "该学号在本学年已登记过", details={"alias": alias_value, "alias_id": duplicate.id}
        )

    open_rows = (
        db.query(WsStudentAlias)
        .filter(
            WsStudentAlias.identity_id == person_id,
            WsStudentAlias.data_domain == "homeroom",
            WsStudentAlias.valid_to.is_(None),
        )
        .all()
    )
    for row in open_rows:
        if row.valid_from is not None and row.valid_from >= valid_from:
            raise InvalidScopeParam(
                "valid_from 必须晚于现有学号的生效日",
                details={"param": "valid_from", "valid_from": req.valid_from},
            )
        row.valid_to = valid_from - timedelta(days=1)
    db.add(
        WsStudentAlias(
            identity_id=person_id,
            alias_value=alias_value,
            data_domain="homeroom",
            academic_year_id=new_year.id,
            alias_scope=str(new_year.id),
            source="manual",
            valid_from=valid_from,
            valid_to=None,
        )
    )
    db.commit()
    return _aliases_response(db, person_id)


@router.get("/homeroom/students/{person_id}/aliases", response_model=AliasesResponse)
@domain_endpoint
def list_student_aliases(
    person_id: int,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    ctx = _homeroom_ctx(db, academic_year_id, class_id)
    _require_bound_class_person(db, ctx, person_id)
    return _aliases_response(db, person_id)


# ────────────────────────────── §2.2 换届 ──────────────────────────────


def _iso_or_none(value) -> Optional[str]:
    """日期/时间字段的快照序列化口径（undo 比对两侧统一走同一转换）。"""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _enrollment_current(enr: Enrollment) -> Dict[str, Optional[str]]:
    """快照核验口径：座号/状态/有效期 + updated_at。

    updated_at 是兜底信号：像"离班又恢复"这类把字段值改回原样的路径，
    字段逐项比对发现不了，但行确实被后续操作改写过（onupdate 必然推进），
    必须列为 conflicted，不能静默回滚。"""
    return {
        "seat_no": _iso_or_none(enr.seat_no),
        "status": enr.status,
        "valid_from": _iso_or_none(enr.valid_from),
        "valid_to": _iso_or_none(enr.valid_to),
        "updated_at": _iso_or_none(enr.updated_at),
    }


def _alias_current(row: WsStudentAlias) -> Dict[str, Optional[str]]:
    """alias 快照口径：收尾日被"追加新学号"等后续操作改动即视为漂移。"""
    return {
        "alias_value": row.alias_value,
        "valid_from": _iso_or_none(row.valid_from),
        "valid_to": _iso_or_none(row.valid_to),
    }


def _row_drifted(current: Dict[str, Optional[str]], snap: Dict) -> bool:
    """逐项比对 confirm 快照记录过的字段；快照没有的字段不参与核验。"""
    for key, expected in snap.items():
        if key == "identity_id":
            continue
        if current.get(key) != expected:
            return True
    return False


@router.get("/homeroom/rollover/preview", response_model=RolloverPreviewResponse)
@domain_endpoint
def rollover_preview(
    from_academic_year_id: Optional[int] = None, db: Session = Depends(get_db)
):
    """换届预览：零业务写入（仅 import_batch 台账）。next_alias 建议 = 旧
    alias 原样保留（保守：confirm 时可逐人覆盖）；无新学年 → 409 提示先建。"""
    current_teacher_id(db)
    if from_academic_year_id is None:
        raise InvalidScopeParam(
            "from_academic_year_id is required",
            details={"param": "from_academic_year_id"},
        )
    _teacher, grade, class_num = _homeroom_binding(db)
    if class_num is None:
        raise WorkspaceNotConfigured(
            "homeroom class binding not configured", details={"grade": grade}
        )
    from_year = db.get(AcademicYear, from_academic_year_id)
    if from_year is None:
        raise ResourceOutOfScope(
            "academic year not found", details={"academic_year_id": from_academic_year_id}
        )
    from_class = (
        db.query(AdministrativeClass)
        .filter(
            AdministrativeClass.academic_year_id == from_year.id,
            AdministrativeClass.grade == grade,
            AdministrativeClass.class_num == class_num,
        )
        .one_or_none()
    )
    if from_class is None:
        raise WorkspaceNotConfigured(
            "绑定学年没有教师绑定的行政班，无可换届名册",
            details={"academic_year_id": from_year.id, "grade": grade, "class_num": class_num},
        )
    # 新学年 = start_date 年份 +1 的学年（最早者）；无 → 409 提示先建学年
    to_year = (
        db.query(AcademicYear)
        .filter(
            func.strftime("%Y", AcademicYear.start_date)
            == str(from_year.start_date.year + 1)
        )
        .order_by(AcademicYear.start_date.asc(), AcademicYear.id.asc())
        .first()
    )
    if to_year is None:
        raise WorkspaceNotConfigured(
            "未找到下一学年，请先在学年管理中创建新学年",
            details={"from_academic_year_id": from_year.id},
        )

    roster = q.homeroom_roster(db, from_class.id, date.today())
    students = [
        RolloverPreviewStudent(
            person_id=item["person_id"],
            name=item["name"],
            current_alias=item["alias"],
            next_alias=item["alias"],
            note=None if item["alias"] else "暂无学号，可在确认时补填",
        )
        for item in roster
    ]
    snapshot = {
        "kind": "rollover_preview",
        "from_academic_year_id": from_year.id,
        "to_academic_year_id": to_year.id,
        "from_class_id": from_class.id,
        "grade": grade,
        "class_num": class_num,
        "member_ids": [item["person_id"] for item in roster],
        "member_aliases": {str(item["person_id"]): item["alias"] for item in roster},
    }
    token = secrets.token_hex(16)
    expires_at = datetime.utcnow() + timedelta(minutes=q.PREVIEW_TTL_MINUTES)
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
    return RolloverPreviewResponse(
        token=token,
        expires_at=expires_at.isoformat(),
        from_year=RolloverYearInfo(id=from_year.id, name=from_year.name),
        to_year=RolloverYearInfo(id=to_year.id, name=to_year.name),
        students=students,
    )


@router.post("/homeroom/rollover", response_model=RolloverConfirmResponse)
@domain_endpoint
def rollover_confirm(req: RolloverConfirmRequest, db: Session = Depends(get_db)):
    """换届确认（R4 全套校验 + 单事务写入）：
    token pending/未过期（_load_preview_batch 复用）→ 范围/绑定/旧学年成员
    无漂移 → 新 alias 撞他人整批 409 零写入 → 新学年行政班（沿用绑定班号）
    + 每生 Enrollment + 新学段 alias + 旧 alias 收尾。重复确认同 token 409。"""
    teacher_id = current_teacher_id(db)
    batch = _load_preview_batch(db, req.token)  # R4：不存在/过期/已消费一律 409
    snapshot = json.loads(batch.scope_json or "{}")
    if snapshot.get("kind") != "rollover_preview":
        raise LinkVersionConflict("token is not a rollover preview", details={"token": req.token})

    # 范围一致性：来源班级/目标学年/教师绑定任一变化 → 409
    from_class = db.get(AdministrativeClass, snapshot.get("from_class_id"))
    if from_class is None or from_class.academic_year_id != snapshot.get("from_academic_year_id"):
        raise LinkVersionConflict("来源班级范围已变化，请重新预览")
    to_year = db.get(AcademicYear, snapshot.get("to_academic_year_id"))
    if to_year is None:
        raise LinkVersionConflict("目标学年已不存在，请重新预览")
    _teacher, grade, class_num = _homeroom_binding(db)
    if (grade, class_num) != (snapshot.get("grade"), snapshot.get("class_num")):
        raise LinkVersionConflict("教师班级绑定已变化，请重新预览")

    # 旧学年成员漂移校验（R4 校验 3）
    current_ids = {
        item["person_id"] for item in q.homeroom_roster(db, from_class.id, date.today())
    }
    if current_ids != set(snapshot.get("member_ids") or []):
        raise LinkVersionConflict(
            "旧学年成员已变化，请重新预览",
            details={
                "snapshot_member_ids": sorted(snapshot.get("member_ids") or []),
                "current_member_ids": sorted(current_ids),
            },
        )

    member_ids: List[int] = list(snapshot.get("member_ids") or [])
    suggested: Dict[str, Optional[str]] = snapshot.get("member_aliases") or {}
    aliases: Dict[int, Optional[str]] = {}
    for key, value in (req.aliases or {}).items():
        try:
            pid = int(key)
        except (TypeError, ValueError) as exc:
            raise InvalidScopeParam(
                "aliases keys must be person_id strings", details={"param": "aliases"}
            ) from exc
        if pid not in member_ids:
            raise InvalidScopeParam(
                "aliases 覆盖了非本次换届成员", details={"param": "aliases", "person_id": pid}
            )
        cleaned = (value or "").strip()
        if not cleaned:
            raise InvalidScopeParam(
                "alias must be a non-empty string", details={"param": "aliases", "person_id": pid}
            )
        aliases[pid] = cleaned
    # 缺省条目沿用 preview 建议值（旧学号）
    for pid in member_ids:
        aliases.setdefault(pid, suggested.get(str(pid)))

    # 新 alias 撞他人（同域目标学年或历史）→ 整批 409 零写入
    conflicts: List = []
    for pid in member_ids:
        value = aliases.get(pid)
        if value:
            conflicts.extend(_alias_conflicts(db, "homeroom", value, pid, to_year))
    if conflicts:
        raise LinkVersionConflict(
            "新学号已属于其他学生，整批拒绝（零写入）",
            details={"conflicts": _conflict_details(db, conflicts)},
        )

    # 目标行政班：绑定 grade+1、班号沿用绑定；绑定缺省时沿用旧班号。
    # 已存在同班：属绑定关系 → 复用；绑定缺省（非绑定同班）→ 422 拒绝并入。
    new_grade = (snapshot.get("grade") or 0) + 1
    if new_grade > 3:
        raise InvalidScopeParam(
            "毕业年级无可换届的目标年级", details={"param": "grade", "grade": new_grade}
        )
    from app.db.models import Teacher

    teacher = db.get(Teacher, teacher_id)
    bound_num = getattr(teacher, f"target_class_high{new_grade}", None)
    target_num = bound_num if bound_num is not None else from_class.class_num
    existing_class = (
        db.query(AdministrativeClass)
        .filter(
            AdministrativeClass.academic_year_id == to_year.id,
            AdministrativeClass.grade == new_grade,
            AdministrativeClass.class_num == target_num,
        )
        .one_or_none()
    )
    if existing_class is not None and bound_num is None:
        raise InvalidScopeParam(
            "新学年已存在同班号班级但教师未绑定该班，拒绝并入",
            details={"class_id": existing_class.id, "grade": new_grade, "class_num": target_num},
        )
    class_created = existing_class is None
    if class_created:
        new_class = AdministrativeClass(
            academic_year_id=to_year.id,
            grade=new_grade,
            class_num=target_num,
            label=f"{_GRADE_LABELS.get(new_grade, '')}{target_num}班",
        )
        db.add(new_class)
    else:
        new_class = existing_class
    db.flush()

    # 单事务写入：Enrollment + 新学段 alias + 旧 alias 收尾；
    # 幂等守卫（ redo 场景）：完全相同的行已存在则跳过，绝不重复插入
    created_enrollment_ids: List[int] = []
    created_alias_ids: List[int] = []
    closed_alias_ids: List[int] = []
    # G04：每张新建/收尾行的可比较快照——undo 前逐项核验当前行是否被
    # 后续操作改写（改座号/改号/离班恢复等），漂移者列入 conflicted 保留现状
    enrollment_snapshots: Dict[str, dict] = {}
    created_alias_snapshots: Dict[str, dict] = {}
    closed_alias_snapshots: Dict[str, dict] = {}
    valid_from = to_year.start_date
    close_to = valid_from - timedelta(days=1)
    for pid in member_ids:
        enr = (
            db.query(Enrollment)
            .filter(
                Enrollment.admin_class_id == new_class.id,
                Enrollment.identity_id == pid,
                Enrollment.valid_from == valid_from,
            )
            .one_or_none()
        )
        if enr is None:
            enr = Enrollment(
                admin_class_id=new_class.id,
                identity_id=pid,
                status="active",
                valid_from=valid_from,
            )
            db.add(enr)
            db.flush()
            created_enrollment_ids.append(enr.id)
            enrollment_snapshots[str(enr.id)] = {
                **_enrollment_current(enr),
                "identity_id": pid,
            }
        # 先收尾旧 alias（旧学年 + 历史开放行，排除目标学年行），再插入
        # 新学段行——顺序保证刚建的新行不会被自己收尾；redo 场景下目标
        # 学年已保留的行（conflicted 跳过撤销者）同样不受影响
        for old in (
            db.query(WsStudentAlias)
            .filter(
                WsStudentAlias.identity_id == pid,
                WsStudentAlias.data_domain == "homeroom",
                WsStudentAlias.valid_to.is_(None),
                or_(
                    WsStudentAlias.academic_year_id.is_(None),
                    WsStudentAlias.academic_year_id != to_year.id,
                ),
            )
            .all()
        ):
            old.valid_to = close_to
            closed_alias_ids.append(old.id)
            closed_alias_snapshots[str(old.id)] = {
                "identity_id": pid,
                "valid_to": close_to.isoformat(),
            }
        value = aliases.get(pid)
        if value:
            dup_alias = (
                db.query(WsStudentAlias)
                .filter(
                    WsStudentAlias.identity_id == pid,
                    WsStudentAlias.data_domain == "homeroom",
                    WsStudentAlias.alias_value == value,
                    WsStudentAlias.academic_year_id == to_year.id,
                )
                .first()
            )
            if dup_alias is None:
                row = WsStudentAlias(
                    identity_id=pid,
                    alias_value=value,
                    data_domain="homeroom",
                    academic_year_id=to_year.id,
                    alias_scope=str(to_year.id),
                    source="rollover",
                    valid_from=valid_from,
                )
                db.add(row)
                db.flush()
                created_alias_ids.append(row.id)
                created_alias_snapshots[str(row.id)] = {
                    **_alias_current(row),
                    "identity_id": pid,
                }

    # confirm 快照落台账：undo 按此精确回滚（created_* 只含本次新建行）
    snapshot["confirmed_at"] = datetime.utcnow().isoformat()
    snapshot["new_class_id"] = new_class.id
    snapshot["class_created"] = class_created
    snapshot["created_enrollment_ids"] = created_enrollment_ids
    snapshot["created_alias_ids"] = created_alias_ids
    snapshot["closed_alias_ids"] = closed_alias_ids
    snapshot["enrollment_snapshots"] = enrollment_snapshots
    snapshot["created_alias_snapshots"] = created_alias_snapshots
    snapshot["closed_alias_snapshots"] = closed_alias_snapshots
    batch.scope_json = json.dumps(snapshot, ensure_ascii=False)
    batch.status = "confirmed"
    db.commit()
    return RolloverConfirmResponse(
        rolled_over=len(member_ids),
        class_id=new_class.id,
        academic_year_id=to_year.id,
        class_created=class_created,
    )


@router.post("/homeroom/rollover/{token}/undo", response_model=RolloverUndoResponse)
@domain_endpoint
def rollover_undo(token: str, db: Session = Depends(get_db)):
    """撤销本次换届（按 confirm 快照回滚）：删本次新建 Enrollment/alias、
    恢复被收尾 alias 的 valid_to、本次新建且已无其他数据的班级删行。
    换届后已有新写入（成绩/作业/档案）或学籍/学号行被后续编辑（G04 快照
    核验：改座号/追加学号/离班又恢复）的学生列入 conflicted 跳过撤销；
    token 单次消费：撤销后 status=confirmed_undo，重复撤销 409。
    换届不动 LinkedStudent / link（按人，架构 §7）。"""
    current_teacher_id(db)
    batch = db.query(ImportBatch).filter(ImportBatch.token == token).first()
    if batch is None:
        raise ResourceOutOfScope("token not found", details={"token": token})
    snapshot = json.loads(batch.scope_json or "{}")
    if snapshot.get("kind") != "rollover_preview":
        raise LinkVersionConflict("token is not a rollover token", details={"token": token})
    if batch.status == "confirmed_undo":
        raise LinkVersionConflict(
            "换届已撤销，token 已消费", details={"token": token, "status": batch.status}
        )
    if batch.status != "confirmed":
        raise LinkVersionConflict(
            "仅已确认的换届可撤销", details={"token": token, "status": batch.status}
        )

    confirmed_at = (
        datetime.fromisoformat(snapshot["confirmed_at"])
        if snapshot.get("confirmed_at")
        else None
    )
    to_year_id = snapshot.get("to_academic_year_id")
    new_class_id = snapshot.get("new_class_id")
    member_ids: List[int] = list(snapshot.get("member_ids") or [])
    names = q.names_for(db, member_ids)
    # G04：confirm 时每张新建/收尾行的关键字段快照
    enrollment_snaps: Dict[str, dict] = snapshot.get("enrollment_snapshots") or {}
    created_alias_snaps: Dict[str, dict] = snapshot.get("created_alias_snapshots") or {}
    closed_alias_snaps: Dict[str, dict] = snapshot.get("closed_alias_snapshots") or {}

    conflicted: List[RolloverConflictedStudent] = []
    undone = 0
    for pid in member_ids:
        # 换届后新写入检查：以 confirm 时点为界，新 alias/新班上的合法后续
        # 数据绝不静默覆盖（契约 §2.2 undo）
        reasons: List[str] = []
        if confirmed_at is not None:
            if (
                db.query(ScoreFact)
                .filter(
                    ScoreFact.identity_id == pid,
                    ScoreFact.academic_year_id == to_year_id,
                    ScoreFact.created_at >= confirmed_at,
                )
                .first()
                is not None
            ):
                reasons.append("成绩")
            if (
                db.query(HomeworkSubmission)
                .filter(
                    HomeworkSubmission.person_id == pid,
                    HomeworkSubmission.created_at >= confirmed_at,
                )
                .first()
                is not None
            ):
                reasons.append("作业")
            if (
                db.query(WsStudentNote)
                .filter(
                    WsStudentNote.person_id == pid,
                    WsStudentNote.created_at >= confirmed_at,
                    _human_notes_filter(),
                )
                .first()
                is not None
            ):
                reasons.append("档案")
        # G04 快照核验：confirm 创建/收尾的每一行与当前行逐字段比对——
        # 改座号、追加学号、离班（含离班又恢复）等任一后续编辑都会让该生
        # 进入 conflicted，保留现状，绝不删除已被编辑过的行。
        # LinkedStudent 不在核验范围：换届不写关联行（按人，架构 §7），无行可回滚。
        for eid, snap in enrollment_snaps.items():
            if snap.get("identity_id") != pid:
                continue
            enr = db.get(Enrollment, int(eid))
            if enr is not None and _row_drifted(_enrollment_current(enr), snap):
                reasons.append("学籍")
        for aid, snap in created_alias_snaps.items():
            if snap.get("identity_id") != pid:
                continue
            row = db.get(WsStudentAlias, int(aid))
            if row is not None and _row_drifted(_alias_current(row), snap):
                reasons.append("学号")
        for aid, snap in closed_alias_snaps.items():
            if snap.get("identity_id") != pid:
                continue
            row = db.get(WsStudentAlias, int(aid))
            if row is not None and _row_drifted(_alias_current(row), snap):
                reasons.append("学号")
        if reasons:
            conflicted.append(
                RolloverConflictedStudent(
                    person_id=pid,
                    name=names.get(pid),
                    reason="换届后已有新写入或学籍/学号被后续编辑（"
                    + "、".join(reasons)
                    + "），跳过撤销保留现状",
                )
            )
            continue
        for eid in snapshot.get("created_enrollment_ids") or []:
            enr = db.get(Enrollment, eid)
            if enr is not None and enr.identity_id == pid:
                db.delete(enr)
        for aid in snapshot.get("created_alias_ids") or []:
            row = db.get(WsStudentAlias, aid)
            if row is not None and row.identity_id == pid:
                db.delete(row)
        for aid in snapshot.get("closed_alias_ids") or []:
            row = db.get(WsStudentAlias, aid)
            if row is not None and row.identity_id == pid:
                row.valid_to = None
        undone += 1

    # 本次新建的行政班：无任何其他数据（残留学籍/成绩/关联/作业）才删行
    class_removed = False
    if snapshot.get("class_created") and new_class_id is not None:
        leftover = (
            db.query(Enrollment.id)
            .filter(Enrollment.admin_class_id == new_class_id)
            .first()
            or db.query(ScoreFact.id)
            .filter(
                ScoreFact.data_domain == "homeroom",
                ScoreFact.class_ref_id == new_class_id,
            )
            .first()
            or db.query(HomeroomTeachingLink.id)
            .filter(HomeroomTeachingLink.admin_class_id == new_class_id)
            .first()
            or db.query(HomeworkAssignment.id)
            .filter(
                HomeworkAssignment.data_domain == "homeroom",
                HomeworkAssignment.class_ref_id == new_class_id,
            )
            .first()
        )
        if leftover is None:
            cls = db.get(AdministrativeClass, new_class_id)
            if cls is not None:
                db.delete(cls)
                class_removed = True

    batch.status = "confirmed_undo"
    db.commit()
    return RolloverUndoResponse(
        success=True, undone=undone, conflicted=conflicted, class_removed=class_removed
    )


# ────────────────────────────── §2.2 教学班换届 ──────────────────────────────
# 与班主任换届同语义（R4 token 生命周期 + G04 快照回滚），作用对象换成
# 教学班：来源学年任教学科的全部 active 教学班整批升入新学年（同标签建
# 新学年教学班 + 成员有效期 + 教学域新学段 alias）。换届不是每学年必须
# 操作——未换届时读侧按学年延续旧班（_queries.carryover_*），本组端点只
# 在教师显式确认时写入。


def _teaching_source_classes(db: Session, from_year: AcademicYear):
    """来源学年任教学科口径下的 active 教学班（目录序）。
    无班 → 409 无可换届名册；多学科 → 422 要求先明确学科（首版一师一科）。"""
    active = {
        row[0]
        for row in db.query(TeachingClass.subject)
        .filter(
            TeachingClass.academic_year_id == from_year.id,
            TeachingClass.status == "active",
        )
        .distinct()
        .all()
    }
    if not active:
        raise WorkspaceNotConfigured(
            "来源学年没有教学班，无可换届名册",
            details={"from_academic_year_id": from_year.id},
        )
    if len(active) != 1:
        raise InvalidScopeParam(
            "来源学年有多个任教学科，请逐学科换届",
            details={"param": "subject", "subjects": sorted(active)},
        )
    subject = next(iter(active))
    classes = (
        db.query(TeachingClass)
        .filter(
            TeachingClass.academic_year_id == from_year.id,
            TeachingClass.subject == subject,
            TeachingClass.status == "active",
        )
        .order_by(TeachingClass.sort_order.asc(), TeachingClass.id.asc())
        .all()
    )
    return subject, classes


def _next_year_of(db: Session, from_year: AcademicYear) -> AcademicYear:
    """新学年 = start_date 年份 +1 的学年（最早者）；无 → 409 提示先建学年。"""
    to_year = (
        db.query(AcademicYear)
        .filter(
            func.strftime("%Y", AcademicYear.start_date)
            == str(from_year.start_date.year + 1)
        )
        .order_by(AcademicYear.start_date.asc(), AcademicYear.id.asc())
        .first()
    )
    if to_year is None:
        raise WorkspaceNotConfigured(
            "未找到下一学年，请先在学年管理中创建新学年",
            details={"from_academic_year_id": from_year.id},
        )
    return to_year


def _member_current(member: TeachingClassMember) -> Dict[str, Optional[str]]:
    """教学班成员快照口径（G04 同语义：后续编辑即漂移）。"""
    return {
        "valid_from": _iso_or_none(member.valid_from),
        "valid_to": _iso_or_none(member.valid_to),
        "updated_at": _iso_or_none(member.updated_at),
    }


@router.get("/teaching/rollover/preview", response_model=RolloverPreviewResponse)
@domain_endpoint
def teaching_rollover_preview(
    from_academic_year_id: Optional[int] = None, db: Session = Depends(get_db)
):
    """教学班换届预览：零业务写入（仅 import_batch 台账）。逐班列出成员，
    next_alias 保守建议沿用旧学号；students 带 class_label 供前端分班展示。"""
    current_teacher_id(db)
    if from_academic_year_id is None:
        raise InvalidScopeParam(
            "from_academic_year_id is required",
            details={"param": "from_academic_year_id"},
        )
    from_year = db.get(AcademicYear, from_academic_year_id)
    if from_year is None:
        raise ResourceOutOfScope(
            "academic year not found", details={"academic_year_id": from_academic_year_id}
        )
    subject, from_classes = _teaching_source_classes(db, from_year)
    to_year = _next_year_of(db, from_year)

    pairs: List[dict] = []
    students: List[RolloverPreviewStudent] = []
    for tc in from_classes:
        for item in q.teaching_roster(db, [tc.id], date.today(), from_year.id):
            pairs.append({"class_id": tc.id, "person_id": item["person_id"]})
            students.append(
                RolloverPreviewStudent(
                    person_id=item["person_id"],
                    name=item["name"],
                    current_alias=item["alias"],
                    next_alias=item["alias"],
                    class_label=tc.label,
                    note=None if item["alias"] else "暂无学号，可在确认时补填",
                )
            )
    snapshot = {
        "kind": "teaching_rollover_preview",
        "from_academic_year_id": from_year.id,
        "to_academic_year_id": to_year.id,
        "subject": subject,
        "classes": [
            {"class_id": tc.id, "label": tc.label, "sort_order": tc.sort_order}
            for tc in from_classes
        ],
        "pairs": pairs,
        "member_aliases": {str(s.person_id): s.current_alias for s in students},
    }
    token = secrets.token_hex(16)
    expires_at = datetime.utcnow() + timedelta(minutes=q.PREVIEW_TTL_MINUTES)
    db.add(
        ImportBatch(
            token=token,
            data_domain="teaching",
            scope_json=json.dumps(snapshot, ensure_ascii=False),
            status="pending",
            expires_at=expires_at,
        )
    )
    db.commit()
    return RolloverPreviewResponse(
        token=token,
        expires_at=expires_at.isoformat(),
        from_year=RolloverYearInfo(id=from_year.id, name=from_year.name),
        to_year=RolloverYearInfo(id=to_year.id, name=to_year.name),
        students=students,
    )


@router.post("/teaching/rollover", response_model=TeachingRolloverConfirmResponse)
@domain_endpoint
def teaching_rollover_confirm(req: RolloverConfirmRequest, db: Session = Depends(get_db)):
    """教学班换届确认（R4 全套校验 + 单事务写入）：token/范围/成员无漂移 →
    新学号撞他人整批 409 零写入 → 新学年教学班（同标签）+ 每成员有效期行 +
    教学域新学段 alias + 旧 alias 收尾。新学年已有同标签班 → 复用并入。"""
    current_teacher_id(db)
    batch = _load_preview_batch(db, req.token)
    snapshot = json.loads(batch.scope_json or "{}")
    if snapshot.get("kind") != "teaching_rollover_preview":
        raise LinkVersionConflict(
            "token is not a teaching rollover preview", details={"token": req.token}
        )

    from_year = db.get(AcademicYear, snapshot.get("from_academic_year_id"))
    to_year = db.get(AcademicYear, snapshot.get("to_academic_year_id"))
    if from_year is None or to_year is None:
        raise LinkVersionConflict("来源/目标学年已不存在，请重新预览")
    subject = snapshot.get("subject")
    from_classes: List[TeachingClass] = []
    for item in snapshot.get("classes") or []:
        tc = db.get(TeachingClass, item.get("class_id"))
        if (
            tc is None
            or tc.academic_year_id != from_year.id
            or tc.label != item.get("label")
            or tc.status != "active"
        ):
            raise LinkVersionConflict("来源教学班已变化，请重新预览")
        from_classes.append(tc)

    # 成员漂移校验（R4 校验 3）：逐班重算与快照 pairs 全等才放行
    current_pairs: List[dict] = []
    for tc in from_classes:
        for item in q.teaching_roster(db, [tc.id], date.today(), from_year.id):
            current_pairs.append({"class_id": tc.id, "person_id": item["person_id"]})
    if current_pairs != (snapshot.get("pairs") or []):
        raise LinkVersionConflict("来源学年教学班成员已变化，请重新预览")

    member_person_ids = sorted({p["person_id"] for p in current_pairs})
    suggested: Dict[str, Optional[str]] = snapshot.get("member_aliases") or {}
    aliases: Dict[int, Optional[str]] = {}
    for key, value in (req.aliases or {}).items():
        try:
            pid = int(key)
        except (TypeError, ValueError) as exc:
            raise InvalidScopeParam(
                "aliases keys must be person_id strings", details={"param": "aliases"}
            ) from exc
        if pid not in member_person_ids:
            raise InvalidScopeParam(
                "aliases 覆盖了非本次换届成员", details={"param": "aliases", "person_id": pid}
            )
        cleaned = (value or "").strip()
        if not cleaned:
            raise InvalidScopeParam(
                "alias must be a non-empty string", details={"param": "aliases", "person_id": pid}
            )
        aliases[pid] = cleaned
    for pid in member_person_ids:
        aliases.setdefault(pid, suggested.get(str(pid)))

    # 新 alias 撞他人（教学域目标学年或历史）→ 整批 409 零写入
    conflicts: List = []
    for pid in member_person_ids:
        value = aliases.get(pid)
        if value:
            conflicts.extend(_alias_conflicts(db, "teaching", value, pid, to_year))
    if conflicts:
        raise LinkVersionConflict(
            "新学号已属于其他学生，整批拒绝（零写入）",
            details={"conflicts": _conflict_details(db, conflicts)},
        )

    valid_from = to_year.start_date
    close_to = valid_from - timedelta(days=1)
    created_class_ids: List[int] = []
    target_class_ids: List[int] = []
    created_member_ids: List[int] = []
    created_alias_ids: List[int] = []
    closed_alias_ids: List[int] = []
    member_snapshots: Dict[str, dict] = {}
    created_alias_snapshots: Dict[str, dict] = {}
    closed_alias_snapshots: Dict[str, dict] = {}
    class_results: List[TeachingRolloverClassResult] = []

    for tc in from_classes:
        existing = (
            db.query(TeachingClass)
            .filter(
                TeachingClass.academic_year_id == to_year.id,
                TeachingClass.subject == subject,
                TeachingClass.label == tc.label,
            )
            .one_or_none()
        )
        if existing is None:
            new_tc = TeachingClass(
                academic_year_id=to_year.id,
                subject=subject,
                label=tc.label,
                sort_order=tc.sort_order,
                status="active",
            )
            db.add(new_tc)
            db.flush()
            created_class_ids.append(new_tc.id)
            class_created = True
        else:
            new_tc = existing
            class_created = False
        target_class_ids.append(new_tc.id)
        class_results.append(
            TeachingRolloverClassResult(
                class_id=new_tc.id, label=new_tc.label, class_created=class_created
            )
        )
        # 幂等守卫（redo 场景）：完全相同的成员行已存在则跳过，绝不重复插入
        for pid in [p["person_id"] for p in current_pairs if p["class_id"] == tc.id]:
            member = (
                db.query(TeachingClassMember)
                .filter(
                    TeachingClassMember.teaching_class_id == new_tc.id,
                    TeachingClassMember.identity_id == pid,
                    TeachingClassMember.valid_from == valid_from,
                )
                .one_or_none()
            )
            if member is None:
                member = TeachingClassMember(
                    teaching_class_id=new_tc.id,
                    identity_id=pid,
                    valid_from=valid_from,
                    source="rollover",
                )
                db.add(member)
                db.flush()
                created_member_ids.append(member.id)
                member_snapshots[str(member.id)] = {
                    **_member_current(member),
                    "identity_id": pid,
                }

    # 学号：逐人先收尾旧教学段 alias，再插入新学年行（顺序防自收尾）
    for pid in member_person_ids:
        for old in (
            db.query(WsStudentAlias)
            .filter(
                WsStudentAlias.identity_id == pid,
                WsStudentAlias.data_domain == "teaching",
                WsStudentAlias.valid_to.is_(None),
                or_(
                    WsStudentAlias.academic_year_id.is_(None),
                    WsStudentAlias.academic_year_id != to_year.id,
                ),
            )
            .all()
        ):
            old.valid_to = close_to
            closed_alias_ids.append(old.id)
            closed_alias_snapshots[str(old.id)] = {
                "identity_id": pid,
                "valid_to": close_to.isoformat(),
            }
        value = aliases.get(pid)
        if value:
            dup_alias = (
                db.query(WsStudentAlias)
                .filter(
                    WsStudentAlias.identity_id == pid,
                    WsStudentAlias.data_domain == "teaching",
                    WsStudentAlias.alias_value == value,
                    WsStudentAlias.academic_year_id == to_year.id,
                )
                .first()
            )
            if dup_alias is None:
                row = WsStudentAlias(
                    identity_id=pid,
                    alias_value=value,
                    data_domain="teaching",
                    academic_year_id=to_year.id,
                    alias_scope=str(to_year.id),
                    source="rollover",
                    valid_from=valid_from,
                )
                db.add(row)
                db.flush()
                created_alias_ids.append(row.id)
                created_alias_snapshots[str(row.id)] = {
                    **_alias_current(row),
                    "identity_id": pid,
                }

    # confirm 快照落台账：undo 按此精确回滚（created_* 只含本次新建行）
    snapshot["confirmed_at"] = datetime.utcnow().isoformat()
    snapshot["created_class_ids"] = created_class_ids
    snapshot["target_class_ids"] = target_class_ids
    snapshot["created_member_ids"] = created_member_ids
    snapshot["created_alias_ids"] = created_alias_ids
    snapshot["closed_alias_ids"] = closed_alias_ids
    snapshot["member_snapshots"] = member_snapshots
    snapshot["created_alias_snapshots"] = created_alias_snapshots
    snapshot["closed_alias_snapshots"] = closed_alias_snapshots
    batch.scope_json = json.dumps(snapshot, ensure_ascii=False)
    batch.status = "confirmed"
    db.commit()
    return TeachingRolloverConfirmResponse(
        rolled_over=len(member_person_ids),
        academic_year_id=to_year.id,
        classes=class_results,
    )


@router.post("/teaching/rollover/{token}/undo", response_model=RolloverUndoResponse)
@domain_endpoint
def teaching_rollover_undo(token: str, db: Session = Depends(get_db)):
    """撤销本次教学班换届（按 confirm 快照回滚）：删本次新建成员/alias、
    恢复被收尾 alias 的 valid_to、本次新建且已无其他数据的教学班删行。
    换届后已有新写入（成绩/作业/档案）或成员/学号被后续编辑的学生列入
    conflicted 跳过撤销；token 单次消费，重复撤销 409。"""
    current_teacher_id(db)
    batch = db.query(ImportBatch).filter(ImportBatch.token == token).first()
    if batch is None:
        raise ResourceOutOfScope("token not found", details={"token": token})
    snapshot = json.loads(batch.scope_json or "{}")
    if snapshot.get("kind") != "teaching_rollover_preview":
        raise LinkVersionConflict("token is not a teaching rollover token", details={"token": token})
    if batch.status == "confirmed_undo":
        raise LinkVersionConflict(
            "换届已撤销，token 已消费", details={"token": token, "status": batch.status}
        )
    if batch.status != "confirmed":
        raise LinkVersionConflict(
            "仅已确认的换届可撤销", details={"token": token, "status": batch.status}
        )

    confirmed_at = (
        datetime.fromisoformat(snapshot["confirmed_at"])
        if snapshot.get("confirmed_at")
        else None
    )
    to_year_id = snapshot.get("to_academic_year_id")
    pairs = snapshot.get("pairs") or []
    member_person_ids = sorted({p["person_id"] for p in pairs})
    names = q.names_for(db, member_person_ids)
    member_snaps: Dict[str, dict] = snapshot.get("member_snapshots") or {}
    created_alias_snaps: Dict[str, dict] = snapshot.get("created_alias_snapshots") or {}
    closed_alias_snaps: Dict[str, dict] = snapshot.get("closed_alias_snapshots") or {}

    conflicted: List[RolloverConflictedStudent] = []
    undone = 0
    for pid in member_person_ids:
        reasons: List[str] = []
        if confirmed_at is not None:
            if (
                db.query(ScoreFact)
                .filter(
                    ScoreFact.data_domain == "teaching",
                    ScoreFact.identity_id == pid,
                    ScoreFact.academic_year_id == to_year_id,
                    ScoreFact.created_at >= confirmed_at,
                )
                .first()
                is not None
            ):
                reasons.append("成绩")
            if (
                db.query(HomeworkSubmission)
                .filter(
                    HomeworkSubmission.person_id == pid,
                    HomeworkSubmission.created_at >= confirmed_at,
                )
                .first()
                is not None
            ):
                reasons.append("作业")
            if (
                db.query(WsStudentNote)
                .filter(
                    WsStudentNote.data_domain == "teaching",
                    WsStudentNote.person_id == pid,
                    WsStudentNote.created_at >= confirmed_at,
                    _human_notes_filter(),
                )
                .first()
                is not None
            ):
                reasons.append("档案")
        # G04 快照核验：confirm 创建/收尾的每一行与当前行逐字段比对
        for mid, snap in member_snaps.items():
            if snap.get("identity_id") != pid:
                continue
            member = db.get(TeachingClassMember, int(mid))
            if member is not None and _row_drifted(_member_current(member), snap):
                reasons.append("成员")
        for aid, snap in created_alias_snaps.items():
            if snap.get("identity_id") != pid:
                continue
            row = db.get(WsStudentAlias, int(aid))
            if row is not None and _row_drifted(_alias_current(row), snap):
                reasons.append("学号")
        for aid, snap in closed_alias_snaps.items():
            if snap.get("identity_id") != pid:
                continue
            row = db.get(WsStudentAlias, int(aid))
            if row is not None and _row_drifted(_alias_current(row), snap):
                reasons.append("学号")
        if reasons:
            conflicted.append(
                RolloverConflictedStudent(
                    person_id=pid,
                    name=names.get(pid),
                    reason="换届后已有新写入或成员/学号被后续编辑（"
                    + "、".join(reasons)
                    + "），跳过撤销保留现状",
                )
            )
            continue
        for mid in snapshot.get("created_member_ids") or []:
            member = db.get(TeachingClassMember, mid)
            if member is not None and member.identity_id == pid:
                db.delete(member)
        for aid in snapshot.get("created_alias_ids") or []:
            row = db.get(WsStudentAlias, aid)
            if row is not None and row.identity_id == pid:
                db.delete(row)
        for aid in snapshot.get("closed_alias_ids") or []:
            row = db.get(WsStudentAlias, aid)
            if row is not None and row.identity_id == pid:
                row.valid_to = None
        undone += 1

    # 本次 touching 的新学年教学班（新建或复用同标签班）：撤销后无任何
    # 其他数据（残留成员/成绩/关联/作业）才删行——完整撤销后不留空壳班，
    # 学年延续视图得以恢复；有残留（含他人成员）一律保留。
    class_removed = False
    touched_class_ids = list(
        dict.fromkeys(
            [*(snapshot.get("created_class_ids") or []), *(snapshot.get("target_class_ids") or [])]
        )
    )
    for cid in touched_class_ids:
        leftover = (
            db.query(TeachingClassMember.id)
            .filter(TeachingClassMember.teaching_class_id == cid)
            .first()
            or db.query(ScoreFact.id)
            .filter(
                ScoreFact.data_domain == "teaching",
                ScoreFact.class_ref_id == cid,
            )
            .first()
            or db.query(HomeroomTeachingLink.id)
            .filter(HomeroomTeachingLink.teaching_class_id == cid)
            .first()
            or db.query(HomeworkAssignment.id)
            .filter(
                HomeworkAssignment.data_domain == "teaching",
                HomeworkAssignment.class_ref_id == cid,
            )
            .first()
        )
        if leftover is None:
            cls = db.get(TeachingClass, cid)
            if cls is not None:
                db.delete(cls)
                class_removed = True

    batch.status = "confirmed_undo"
    db.commit()
    return RolloverUndoResponse(
        success=True, undone=undone, conflicted=conflicted, class_removed=class_removed
    )


# ────────────────────────────── §2.3 学生报告 ──────────────────────────────


@router.get("/homeroom/students/{person_id}/report", response_model=StudentReportResponse)
@domain_endpoint
def student_report(
    person_id: int,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """打印数据源：画像（与 students.py 画像同一取数/投影门/冲突规则，R1）
    + 当期名册位 + 别名史 + homeroom 域档案摘要（N01：不含 teaching 域）。"""
    ctx = _homeroom_ctx(db, academic_year_id, class_id, term_id)
    if person_id not in ctx.member_person_ids:
        raise ResourceOutOfScope(
            "person not in current homeroom scope", details={"person_id": person_id}
        )
    names = q.names_for(db, [person_id])
    alias_rows = (
        db.query(WsStudentAlias)
        .filter(
            WsStudentAlias.identity_id == person_id,
            WsStudentAlias.data_domain == "homeroom",
        )
        .order_by(WsStudentAlias.id.asc())
        .all()
    )

    facts = q.homeroom_facts(db, ctx, identity_ids=[person_id])
    subject_facts = [f for f in facts if f.total_type is None]
    total_facts = [f for f in facts if f.total_type is not None]

    # 关联学科投影（§1.4.1，R5，与画像同规则）：H 无该场 → 加 teaching 条目；
    # 值不同 → 保留 H 条目并附 shared_conflict；值相同 → 不重复添加
    conflicts: Dict = {}
    teaching_extra: List = []
    if ctx.link_id is not None:
        link = db.get(HomeroomTeachingLink, ctx.link_id)
        for t_fact, _h_id in q.gated_teaching_facts_for_homeroom(
            db, ctx, link, h_ids=[person_id]
        ):
            h_match = next(
                (
                    f
                    for f in subject_facts
                    if f.subject == t_fact.subject and f.exam_name == t_fact.exam_name
                ),
                None,
            )
            if h_match is None:
                teaching_extra.append(t_fact)
            elif h_match.score != t_fact.score:
                conflicts[(t_fact.subject, t_fact.exam_name)] = t_fact.score

    grouped: dict = {}
    for fact in sorted(
        subject_facts, key=lambda f: (f.exam_date is None, f.exam_date or date.min, f.id)
    ):
        entry = _exam_entry(fact, "homeroom")
        conflict_key = (fact.subject, fact.exam_name)
        if conflict_key in conflicts:
            entry.shared_conflict = {"teaching_score": conflicts[conflict_key]}
        grouped.setdefault(fact.subject, []).append(entry)
    for t_fact in sorted(
        teaching_extra, key=lambda f: (f.exam_date is None, f.exam_date or date.min, f.id)
    ):
        grouped.setdefault(t_fact.subject, []).append(_exam_entry(t_fact, "teaching"))
    subjects = [
        SubjectGroup(subject=subject, exams=exams)
        for subject, exams in sorted(grouped.items())
    ]
    totals_grouped: dict = {}
    for fact in sorted(
        total_facts, key=lambda f: (f.exam_date is None, f.exam_date or date.min, f.id)
    ):
        totals_grouped.setdefault(fact.total_type, []).append(_exam_entry(fact, "homeroom"))
    totals = [
        TotalGroup(total_type=total_type, exams=exams)
        for total_type, exams in sorted(totals_grouped.items())
    ]

    enrollment = _strict_current_enrollment(db, ctx.class_ids[0], person_id, ctx.as_of)
    alias_map = q.aliases_for(db, [person_id], "homeroom", ctx.academic_year_id)
    bound_class = db.get(AdministrativeClass, ctx.class_ids[0])
    bound_year = db.get(AcademicYear, ctx.academic_year_id)
    roster = ReportRoster(
        class_id=ctx.class_ids[0],
        seat_no=enrollment.seat_no if enrollment else None,
        status=enrollment.status if enrollment else None,
        alias=alias_map.get(person_id),
        class_label=bound_class.label if bound_class else None,
        academic_year_name=bound_year.name if bound_year else None,
    )

    # 档案摘要：只统计 homeroom 域（N01 红线，teaching 档案绝不出现；排除系统自动记录）
    note_rows = (
        db.query(WsStudentNote)
        .filter(
            WsStudentNote.data_domain == "homeroom",
            WsStudentNote.person_id == person_id,
            _human_notes_filter(),
        )
        .order_by(WsStudentNote.date.desc(), WsStudentNote.id.desc())
        .all()
    )
    notes_summary = NotesSummary(
        count=len(note_rows),
        recent=[_note_item(note) for note in note_rows[:5]],
    )

    return StudentReportResponse(
        metadata=_profile_metadata(ctx),
        person=ReportPerson(
            person_id=person_id,
            name=names.get(person_id),
            domain="homeroom",
            aliases=[
                AliasItem(
                    id=row.id,
                    alias_value=row.alias_value,
                    valid_from=row.valid_from.isoformat() if row.valid_from else None,
                    valid_to=row.valid_to.isoformat() if row.valid_to else None,
                )
                for row in alias_rows
            ],
        ),
        roster=roster,
        subjects=subjects,
        totals=totals,
        notes_summary=notes_summary,
    )


# ────────────────────────────── §4 档案 notes（N01 域隔离红线） ──────────────────────────────


def _require_mode_person(
    db: Session, mode: str, person_id: int, **params
) -> WorkspaceContext:
    """person 必须在该 mode 当前作用域（当期成员），越界一律 404——
    homeroom 的人经 teaching 路径读取即 404（N01），反向亦然。"""
    ctx = _mode_ctx(db, mode, **params)
    if person_id not in ctx.member_person_ids:
        raise ResourceOutOfScope(
            "person not in current scope", details={"mode": mode, "person_id": person_id}
        )
    return ctx


def _note_or_404(
    db: Session, mode: str, note_id: int, ctx: WorkspaceContext
) -> WsStudentNote:
    """note 域不符或归属人越界 → 404（绝不向其他 mode 泄露存在性）。
    系统内部辅助 note（作业出勤/忘带同步、预警解除、迁移标记）禁止通过档案 API 篡改。"""
    note = db.get(WsStudentNote, note_id)
    if note is None or note.data_domain != mode:
        raise ResourceOutOfScope(
            "note not found in this domain", details={"note_id": note_id, "mode": mode}
        )
    if note.person_id not in ctx.member_person_ids:
        raise ResourceOutOfScope(
            "person not in current scope", details={"note_id": note_id}
        )
    if note.source and (
        note.source.startswith("homework:")
        or note.source.startswith("warning_dismissal:")
        or note.source.startswith("migration:")
    ):
        raise ResourceOutOfScope(
            "system note cannot be accessed via student notes API", details={"note_id": note_id}
        )
    return note


@router.get("/{mode}/students/{person_id}/notes", response_model=NotesResponse)
@domain_endpoint
def list_notes(
    mode: str,
    person_id: int,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    subject: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """按 person 聚合其本域全部教师手动填写的档案，按 date 降序。
    只返回 data_domain=该域 且排除系统内部辅助标记（作业考勤/预警解除/迁移）的行。"""
    ctx = _require_mode_person(
        db,
        mode,
        person_id,
        academic_year_id=academic_year_id,
        class_id=class_id,
        teaching_class_id=teaching_class_id,
        term_id=term_id,
        subject=subject,
    )
    rows = (
        db.query(WsStudentNote)
        .filter(
            WsStudentNote.data_domain == mode,
            WsStudentNote.person_id == person_id,
            _human_notes_filter(),
        )
        .order_by(WsStudentNote.date.desc(), WsStudentNote.id.desc())
        .all()
    )
    return NotesResponse(notes=[_note_item(note) for note in rows])


def _validate_manual_baseline(
    db: Session, ctx, baseline: Optional[dict], metric: Optional[str]
) -> None:
    """手填数字基线的单位范围校验（与复查对照 _numeric_baseline_value 同口径）：
    percentile 以 0–1 小数存储（前 40% = 0.4，不是 40）、rank 不小于 1；
    越界 → 422，把「百分数当小数入库」挡在写入前。非数字/无指标不拦
    （复查对照自然 pending/baseline_incomparable）。"""
    if not isinstance(baseline, dict) or not metric:
        return
    value = baseline.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    from app.diagnosis.review import metric_meta_or_422, unit_of_metric

    unit = unit_of_metric(metric_meta_or_422(db, ctx, metric))
    if unit == "percentile" and not 0.0 <= float(value) <= 1.0:
        raise InvalidScopeParam(
            "baseline_value.value for percentile metrics must be a decimal fraction "
            "in [0,1] (前 40% = 0.4，不是 40)",
            details={"param": "baseline_value", "unit": unit, "value": value},
        )
    if unit == "rank" and float(value) < 1:
        raise InvalidScopeParam(
            "baseline_value.value for rank metrics must be >= 1",
            details={"param": "baseline_value", "unit": unit, "value": value},
        )
    if unit == "rank" and float(value) != int(float(value)):
        # 名次为整数：1.5 这类小数在写入前拦下（Codex 复审 #3）
        raise InvalidScopeParam(
            "baseline_value.value for rank metrics must be an integer（名次为整数，如 300）",
            details={"param": "baseline_value", "unit": unit, "value": value},
        )


@router.post("/{mode}/students/{person_id}/notes", response_model=NoteItem)
@domain_endpoint
def create_note(
    mode: str,
    person_id: int,
    req: NoteCreateRequest,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    subject: Optional[str] = None,
    db: Session = Depends(get_db),
):
    ctx = _require_mode_person(
        db,
        mode,
        person_id,
        academic_year_id=academic_year_id,
        class_id=class_id,
        teaching_class_id=teaching_class_id,
        term_id=term_id,
        subject=subject,
    )
    if req.category not in NOTE_CATEGORIES:
        raise InvalidScopeParam(
            "category must be one of 谈话/观察/家访/家长沟通/奖惩/其他",
            details={"param": "category", "category": req.category},
        )
    note_date = _parse_iso_date(req.date, "date")
    content = (req.content or "").strip()
    if not content:
        raise InvalidScopeParam("content must be a non-empty string", details={"param": "content"})
    # ── P2-C4 干预建档（契约 §5.1）：防重复录入 + target_metric 校验 +
    # 基线自动捕获（复用 app.diagnosis.review 服务，成绩口径同源）。──
    start_date = (
        _parse_iso_date(req.start_date, "start_date") if req.start_date is not None else None
    )
    review_date = (
        _parse_iso_date(req.review_date, "review_date") if req.review_date is not None else None
    )
    if review_date is not None and start_date is not None and review_date < start_date:
        raise InvalidScopeParam(
            "review_date must not be earlier than start_date",
            details={"param": "review_date", "review_date": req.review_date},
        )
    baseline_value = None
    target_metric = req.target_metric.strip() if req.target_metric else None
    if _is_intervention_request(req):
        if target_metric:
            from app.diagnosis.review import capture_baseline, metric_meta_or_422

            metric_meta_or_422(db, ctx, target_metric)  # 不支持的指标 → 422（唯一口径解析）
            if req.baseline_value is not None:
                # 教师手填基线：单位范围先校验（percentile 0–1 小数等，见
                # _validate_manual_baseline），原样入库并标注来源；口径不符
                # 由复查对照兜底（pending/baseline_incomparable，绝不硬算）。
                _validate_manual_baseline(db, ctx, req.baseline_value, target_metric)
                baseline_value = dict(req.baseline_value)
                baseline_value.setdefault("metric", target_metric)
                baseline_value.setdefault("source", "teacher")
            else:
                # 基线锚点 = 干预开始日（缺省回落档案日期）：只取锚点**之前**的
                # 可比成绩——补录过去开始的干预不会把干预后成绩当基线。
                anchor = start_date or note_date
                baseline_value = capture_baseline(db, ctx, person_id, target_metric, anchor=anchor)
        elif req.baseline_value is not None:
            baseline_value = dict(req.baseline_value)
            baseline_value.setdefault("source", "teacher")
        if not req.force:
            duplicates = _open_duplicate_interventions(db, ctx, person_id, req.subject_scope)
            if duplicates:
                raise DuplicateFollowUp(
                    "该生已有未关闭的同科干预，请先关闭或选择继续创建",
                    details={"existing": [_note_item(n).model_dump() for n in duplicates]},
                )
    note = WsStudentNote(
        data_domain=mode,
        person_id=person_id,
        date=note_date,
        category=req.category,
        content=content,
        follow_up=req.follow_up,
        follow_up_done=0,
        problem=req.problem,
        subject_scope=req.subject_scope,
        measures=req.measures,
        target_metric=target_metric,
        baseline_value=baseline_value,
        start_date=start_date,
        review_date=review_date,
        status="open" if _is_intervention_request(req) else None,
    )
    db.add(note)
    db.commit()
    return _note_item(note)


@router.patch("/{mode}/notes/{note_id}", response_model=NoteItem)
@domain_endpoint
def patch_note(
    mode: str,
    note_id: int,
    req: NotePatchRequest,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    subject: Optional[str] = None,
    db: Session = Depends(get_db),
):
    ctx = _mode_ctx(
        db,
        mode,
        academic_year_id=academic_year_id,
        class_id=class_id,
        teaching_class_id=teaching_class_id,
        term_id=term_id,
        subject=subject,
    )
    note = _note_or_404(db, mode, note_id, ctx)
    fields = req.model_fields_set or set()

    if "category" in fields and req.category is not None:
        if req.category not in NOTE_CATEGORIES:
            raise InvalidScopeParam(
                "category must be one of 谈话/观察/家访/家长沟通/奖惩/其他",
                details={"param": "category", "category": req.category},
            )
        note.category = req.category
    if "date" in fields and req.date is not None:
        note.date = _parse_iso_date(req.date, "date")
    if "content" in fields and req.content is not None:
        stripped = req.content.strip()
        if not stripped:
            raise InvalidScopeParam(
                "content must be a non-empty string", details={"param": "content"}
            )
        note.content = stripped
    if "follow_up" in fields:
        note.follow_up = req.follow_up
    if "follow_up_done" in fields and req.follow_up_done is not None:
        if req.follow_up_done not in (0, 1):
            raise InvalidScopeParam(
                "follow_up_done must be 0 or 1",
                details={"param": "follow_up_done", "follow_up_done": req.follow_up_done},
            )
        _apply_follow_up_done(note, req.follow_up_done)
    # ── P2-C4 干预扩展（契约 §5.1/§5.4）：status 关闭路径与旧列同步，
    # 其余扩展列按编辑原样落库；旧档案（status=NULL）不受镜像影响。──
    if "status" in fields and req.status is not None:
        if req.status not in NOTE_FOLLOW_UP_STATUSES:
            raise InvalidScopeParam(
                "status must be one of open/done/dismissed",
                details={"param": "status", "status": req.status},
            )
        note.status = req.status
        _sync_follow_up_close(note)
    if "problem" in fields:
        note.problem = req.problem
    if "subject_scope" in fields:
        note.subject_scope = req.subject_scope
    if "measures" in fields:
        note.measures = req.measures
    metric_changed = False
    if "target_metric" in fields:
        new_metric = req.target_metric.strip() if req.target_metric else None
        metric_changed = new_metric != note.target_metric
        if new_metric and note.status in ("open", "done", "dismissed"):
            from app.diagnosis.review import metric_meta_or_422

            metric_meta_or_422(db, ctx, new_metric)  # 与创建同口径：不支持 → 422
        note.target_metric = new_metric
    if "baseline_value" in fields:
        # 手填基线单位校验与创建同口径（此时 target_metric 已更新为本请求值）
        if (
            req.baseline_value is not None
            and note.target_metric
            and note.status in ("open", "done", "dismissed")
        ):
            _validate_manual_baseline(db, ctx, req.baseline_value, note.target_metric)
        note.baseline_value = req.baseline_value
    if "start_date" in fields or "review_date" in fields:
        # 生效值 = 请求值（None/空串=清空），先算出两侧结果、校验
        # start<=review，再统一落库（部分更新时另一侧取现值）。
        if "start_date" in fields:
            new_start = _parse_iso_date(req.start_date, "start_date") if req.start_date else None
        else:
            new_start = note.start_date
        if "review_date" in fields:
            new_review = (
                _parse_iso_date(req.review_date, "review_date") if req.review_date else None
            )
        else:
            new_review = note.review_date
        if new_start is not None and new_review is not None and new_review < new_start:
            raise InvalidScopeParam(
                "review_date must not be earlier than start_date",
                details={"param": "review_date", "review_date": req.review_date,
                         "start_date": req.start_date},
            )
        note.start_date = new_start
        note.review_date = new_review
    # ── 基线自动重取（干预行，C4 §5.1）：目标指标变更（旧基线口径不符）
    # 或锚点（start_date，缺省回落档案 date）因 start_date/date 变更而移动、
    # 基线不再早于新锚点时，按当前指标与锚点重取；重取不到 → None（复查
    # 对照如实 pending/no_baseline，教师可手填）。同请求显式手填
    # baseline_value 的以手填为准，不覆盖。旧档案（status=NULL）不动。──
    if note.status in ("open", "done", "dismissed") and note.target_metric:
        anchor = note.start_date or note.date
        # start_date 为空时锚点随档案 date 移动：改 date 同样可能使既有
        # 基线落在锚点之后（如补录过去开始的干预），须一并检查。
        anchor_moved = "start_date" in fields or (
            note.start_date is None and "date" in fields
        )
        needs_recapture = metric_changed
        if not needs_recapture and anchor_moved:
            baseline = (
                dict(note.baseline_value) if isinstance(note.baseline_value, dict) else {}
            )
            exam_date = baseline.get("exam_date")
            if baseline and (exam_date is None or not _exam_date_before(exam_date, anchor)):
                needs_recapture = True
        if needs_recapture and not (
            "baseline_value" in fields and req.baseline_value is not None
        ):
            from app.diagnosis.review import capture_baseline

            note.baseline_value = capture_baseline(
                db, ctx, note.person_id, note.target_metric, anchor=anchor
            )
    db.commit()
    return _note_item(note)


@router.delete("/{mode}/notes/{note_id}", response_model=NoteDeleteResponse)
@domain_endpoint
def delete_note(
    mode: str,
    note_id: int,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    subject: Optional[str] = None,
    db: Session = Depends(get_db),
):
    ctx = _mode_ctx(
        db,
        mode,
        academic_year_id=academic_year_id,
        class_id=class_id,
        teaching_class_id=teaching_class_id,
        term_id=term_id,
        subject=subject,
    )
    note = _note_or_404(db, mode, note_id, ctx)
    db.delete(note)
    db.commit()
    return NoteDeleteResponse(success=True)
