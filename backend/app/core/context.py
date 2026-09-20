"""WorkspaceContext：每个请求的不可变作用域（P1）。

所有作用域一律在后端解析：客户端只提交 mode 与所选资源 ID，
教师绑定、学年、成员、学科、关联状态全部由本模块从数据库推导。
空成员范围是合法空态（member_person_ids=[]），绝不回退全年级；
mode 不是权限凭证，越界资源引用抛 ``ResourceOutOfScope``。

homeroom：绑定班来自既有 Teacher 表（target_class_high1/2/3 +
active_grade，读法与 app.main 的 GET /api/teacher 一致），
行政班/成员来自新表 administrative_class / enrollment。
teaching：教学班列表由调用方提交（须属于当前学年），任教学科
首版由调用方传入并在此固定（未传视为未配置）。
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import or_

from app.core.errors import InvalidScopeParam, ResourceOutOfScope, WorkspaceNotConfigured
from app.db.workspace_models import (
    AdministrativeClass,
    Enrollment,
    HomeroomTeachingLink,
    Term,
    TeachingClass,
    TeachingClassMember,
)

VALID_MODES = ("homeroom", "teaching")
_HOMEROOM_BINDING_FIELDS = {1: "target_class_high1", 2: "target_class_high2", 3: "target_class_high3"}


@dataclass(frozen=True)
class WorkspaceContext:
    """一次请求的不可变作用域快照。

    class_ids / member_person_ids 用 tuple 保持真正的不可变
    （frozen dataclass 持有 list 仍可被原地修改）。
    """

    teacher_id: int
    mode: str  # 'homeroom' | 'teaching'
    data_domain: str  # 与 mode 同值，查询侧显式携带
    link_id: int | None = None
    link_version: int | None = None
    academic_year_id: int | None = None
    term_id: int | None = None
    grade: int | None = None
    class_ids: tuple[int, ...] = field(default_factory=tuple)
    subject: str | None = None
    member_person_ids: tuple[int, ...] = field(default_factory=tuple)
    as_of: date = field(default_factory=date.today)
    # 未换届自动延续：class_ids 实际来自更早学年（目标学年尚无本班）时记录
    # 来源学年 id；供 /shared/scope 与界面提示「延续自 X 学年」。
    carried_from_academic_year_id: int | None = None


def _as_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidScopeParam(f"{name} must be an integer", details={"param": name})
    return value


def _parse_as_of(params: dict[str, Any]) -> date:
    raw = params.get("as_of")
    if raw is None or isinstance(raw, date):
        return raw if isinstance(raw, date) and not isinstance(raw, datetime) else date.today()
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise InvalidScopeParam("as_of must be an ISO date (YYYY-MM-DD)") from exc
    raise InvalidScopeParam("as_of must be an ISO date (YYYY-MM-DD)")


def _resolve_academic_year(db, params: dict[str, Any]):
    """academic_year_id 显式传入则校验存在；否则取最新学年（start_date
    最大者，并列时取 id 较大者）。库中无任何学年 → WorkspaceNotConfigured。"""
    from app.db.workspace_models import AcademicYear

    raw = params.get("academic_year_id")
    if raw is not None:
        ay_id = _as_int(raw, "academic_year_id")
        ay = db.get(AcademicYear, ay_id)
        if ay is None:
            raise ResourceOutOfScope("academic_year not found", details={"academic_year_id": ay_id})
        return ay
    ay = (
        db.query(AcademicYear)
        .order_by(AcademicYear.start_date.desc(), AcademicYear.id.desc())
        .first()
    )
    if ay is None:
        raise WorkspaceNotConfigured("no academic year configured")
    return ay


def _resolve_term_id(db, params: dict[str, Any]) -> int | None:
    raw = params.get("term_id")
    if raw is None:
        return None
    term_id = _as_int(raw, "term_id")
    if db.get(Term, term_id) is None:
        raise ResourceOutOfScope("term not found", details={"term_id": term_id})
    return term_id


def _current_member_ids(db, model, class_id_column, class_ids, as_of: date) -> tuple[int, ...]:
    """当期成员：valid_from <= as_of 且（valid_to 为空或 >= as_of），
    且在 class_ids 集合内。返回去重、排序后的 identity id。"""
    rows = (
        db.query(model.identity_id)
        .filter(
            class_id_column.in_(class_ids),
            model.valid_from <= as_of,
            or_(model.valid_to.is_(None), model.valid_to >= as_of),
        )
        .distinct()
        .all()
    )
    return tuple(sorted({row[0] for row in rows}))


def _active_link_for(db, admin_class_id: int, academic_year_id: int, as_of: date):
    """行政班在当期的 active 关联（同班多关联时取 id 最小者，保持确定性）。
    首版一位教师一个任教学科，正常只有一条。"""
    return (
        db.query(HomeroomTeachingLink)
        .filter(
            HomeroomTeachingLink.admin_class_id == admin_class_id,
            HomeroomTeachingLink.academic_year_id == academic_year_id,
            HomeroomTeachingLink.status == "active",
            HomeroomTeachingLink.valid_from <= as_of,
            or_(HomeroomTeachingLink.valid_to.is_(None), HomeroomTeachingLink.valid_to >= as_of),
        )
        .order_by(HomeroomTeachingLink.id.asc())
        .first()
    )


def _resolve_homeroom(db, teacher_id: int, params: dict[str, Any], as_of: date) -> WorkspaceContext:
    from app.db.models import Teacher

    teacher = db.get(Teacher, teacher_id)
    if teacher is None:
        raise WorkspaceNotConfigured("teacher not found", details={"teacher_id": teacher_id})

    raw_grade = params.get("grade")
    if raw_grade is None:
        from app.rollover.service import get_active_grade

        grade = int(get_active_grade(db))
    else:
        grade = _as_int(raw_grade, "grade")
        if grade not in _HOMEROOM_BINDING_FIELDS:
            raise InvalidScopeParam("grade must be 1, 2, or 3", details={"param": "grade"})

    class_num = getattr(teacher, _HOMEROOM_BINDING_FIELDS[grade])
    if class_num is None:
        raise WorkspaceNotConfigured(
            "homeroom class binding not configured",
            details={"grade": grade},
        )

    ay = _resolve_academic_year(db, params)
    carried_from: int | None = None
    admin_class = (
        db.query(AdministrativeClass)
        .filter(
            AdministrativeClass.academic_year_id == ay.id,
            AdministrativeClass.grade == grade,
            AdministrativeClass.class_num == class_num,
        )
        .one_or_none()
    )
    if admin_class is None:
        # 换届后回看历史学年：当年的班是低年级（如 grade2 查上学年的 grade1 班）。
        # 回退仅当「教师绑定的班号」在该学年恰有唯一实例；多个实例（歧义）或
        # 无实例仍按未建立处理，成员范围绝不放宽、绝不退化到全年级。
        bound_nums = {
            getattr(teacher, field)
            for field in _HOMEROOM_BINDING_FIELDS.values()
            if getattr(teacher, field) is not None
        }
        alt_rows = (
            db.query(AdministrativeClass)
            .filter(
                AdministrativeClass.academic_year_id == ay.id,
                AdministrativeClass.class_num.in_(bound_nums),
            )
            .all()
        )
        if len(alt_rows) == 1 and alt_rows[0].grade in _HOMEROOM_BINDING_FIELDS:
            admin_class = alt_rows[0]
            grade = alt_rows[0].grade
        if admin_class is None:
            # 未换届自动延续：目标学年还没有建立本班时，沿教师绑定对取最近
            # 一个更早学年的班级继续使用（零写入）；无候选仍按未建立处理。
            from app.api import _queries as q

            carried_class = q.carryover_homeroom_class(db, teacher, ay.id)
            if carried_class is not None:
                admin_class = carried_class
                grade = carried_class.grade
                carried_from = carried_class.academic_year_id
        if admin_class is None:
            raise WorkspaceNotConfigured(
                "administrative class not established for this academic year",
                details={"grade": grade, "class_num": class_num, "academic_year_id": ay.id},
            )

    raw_class_id = params.get("class_id")
    if raw_class_id is not None:
        class_id = _as_int(raw_class_id, "class_id")
        if class_id != admin_class.id:
            # 非绑定班（含其他教师的班、其他学年同班号）→ 域边界违规
            raise ResourceOutOfScope(
                "class is not bound to this homeroom workspace",
                details={"class_id": class_id},
            )

    # 当期在班成员：status='active' 且有效期覆盖 as_of；空结果即合法空态
    member_rows = (
        db.query(Enrollment.identity_id)
        .filter(
            Enrollment.admin_class_id == admin_class.id,
            Enrollment.status == "active",
            Enrollment.valid_from <= as_of,
            or_(Enrollment.valid_to.is_(None), Enrollment.valid_to >= as_of),
        )
        .distinct()
        .all()
    )
    member_ids = tuple(sorted({row[0] for row in member_rows}))

    link = _active_link_for(db, admin_class.id, ay.id, as_of)
    return WorkspaceContext(
        teacher_id=teacher.id,
        mode="homeroom",
        data_domain="homeroom",
        link_id=link.id if link else None,
        link_version=link.version if link else None,
        academic_year_id=ay.id,
        term_id=_resolve_term_id(db, params),
        grade=grade,
        class_ids=(admin_class.id,),
        subject=link.subject if link else None,
        member_person_ids=member_ids,
        as_of=as_of,
        carried_from_academic_year_id=carried_from,
    )


def _resolve_teaching(db, teacher_id: int, params: dict[str, Any], as_of: date) -> WorkspaceContext:
    from app.db.models import Teacher

    teacher = db.get(Teacher, teacher_id)
    if teacher is None:
        raise WorkspaceNotConfigured("teacher not found", details={"teacher_id": teacher_id})

    ay = _resolve_academic_year(db, params)

    raw_ids = params.get("teaching_class_id")
    if raw_ids is None:
        raise InvalidScopeParam("teaching_class_id is required", details={"param": "teaching_class_id"})
    if isinstance(raw_ids, (list, tuple)):
        class_ids = [_as_int(v, "teaching_class_id") for v in raw_ids]
    else:
        class_ids = [_as_int(raw_ids, "teaching_class_id")]
    if not class_ids:
        raise InvalidScopeParam("teaching_class_id must not be empty", details={"param": "teaching_class_id"})

    seen: set[int] = set()
    ordered_ids: list[int] = []
    for cid in class_ids:
        if cid not in seen:
            seen.add(cid)
            ordered_ids.append(cid)
    carried_from: int | None = None
    for cid in ordered_ids:
        tc = db.get(TeachingClass, cid)
        if tc is not None and tc.academic_year_id != ay.id:
            # 未换届自动延续：目标学年该学科还没有教学班时，接受最近一个
            # 更早学年的班（零写入）；其余跨年提交仍按越界拒绝。
            from app.api import _queries as q

            cy = q.carryover_teaching_year(db, tc.subject, ay.id)
            if cy is None or tc.academic_year_id != cy.id:
                tc = None
            else:
                carried_from = cy.id
        if tc is None:
            raise ResourceOutOfScope(
                "teaching class not found in this academic year",
                details={"teaching_class_id": cid, "academic_year_id": ay.id},
            )

    member_ids = _current_member_ids(
        db, TeachingClassMember, TeachingClassMember.teaching_class_id, ordered_ids, as_of
    )

    subject = params.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        # 首版一位教师一个任教学科：由调用方声明并在此固定
        raise WorkspaceNotConfigured("teaching subject not configured")

    raw_grade = params.get("grade")
    grade = None if raw_grade is None else _as_int(raw_grade, "grade")

    return WorkspaceContext(
        teacher_id=teacher.id,
        mode="teaching",
        data_domain="teaching",
        link_id=None,
        link_version=None,
        academic_year_id=ay.id,
        term_id=_resolve_term_id(db, params),
        grade=grade,
        class_ids=tuple(ordered_ids),
        subject=subject.strip(),
        member_person_ids=member_ids,
        as_of=as_of,
        carried_from_academic_year_id=carried_from,
    )


def resolve_workspace_context(
    db,
    teacher_id: int,
    mode: str,
    params: dict[str, Any] | None = None,
) -> WorkspaceContext:
    """从数据库解析当前请求的不可变 WorkspaceContext。

    - mode 只接受 'homeroom' | 'teaching'（mode 不是权限凭证，仅选择
      解析路径；所有权限由绑定/成员/关联推导）。
    - 成员为空是合法空态，正常返回 member_person_ids=()，绝不回退全年级。
    - 抛 InvalidScopeParam(422) / ResourceOutOfScope(404) /
      WorkspaceNotConfigured(409)，见 app/core/errors.py。
    """
    params = dict(params or {})
    if mode not in VALID_MODES:
        raise InvalidScopeParam(
            "mode must be 'homeroom' or 'teaching'", details={"param": "mode"}
        )
    teacher_id = _as_int(teacher_id, "teacher_id")
    as_of = _parse_as_of(params)
    if mode == "homeroom":
        return _resolve_homeroom(db, teacher_id, params, as_of)
    return _resolve_teaching(db, teacher_id, params, as_of)
