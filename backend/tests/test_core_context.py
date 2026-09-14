"""app/core/context.py 最小单测：作用域解析、域边界与空态语义。

使用既有 conftest 的隔离机制（独立 EXAM_TRACKER_DIR + 每模块重建 schema）；
模块导入 app.db.workspace_models 使新表注册进 Base.metadata，由 conftest
的 autouse fixture 一并建出。
"""

from datetime import date

import pytest

import app.db.workspace_models as wm  # noqa: F401  注册新表
from app.core.context import resolve_workspace_context
from app.core.errors import (
    InvalidScopeParam,
    ResourceOutOfScope,
    WorkspaceNotConfigured,
)
from app.db.models import HomeworkSetting, SessionLocal, Teacher

AS_OF = "2025-11-15"  # 固定观测日，避免依赖真实今天


@pytest.fixture()
def seed(db_session):
    """教师绑定高二 3 班 + 学年两个 + 行政班 + 三种 enrollment 状态。"""
    teacher = Teacher(name="测试班主任", target_class_high2=3)
    db_session.add(teacher)
    db_session.add(
        HomeworkSetting(key="active_grade", value="2")
    )  # homeroom 默认年级来源
    db_session.flush()

    ay_old = wm.AcademicYear(
        name="2024-2025", start_date=date(2024, 9, 1), end_date=date(2025, 6, 30)
    )
    ay = wm.AcademicYear(
        name="2025-2026", start_date=date(2025, 9, 1), end_date=date(2026, 6, 30)
    )
    db_session.add_all([ay_old, ay])
    db_session.flush()

    admin = wm.AdministrativeClass(
        academic_year_id=ay.id, grade=2, class_num=3, label="高二3班"
    )
    other_admin = wm.AdministrativeClass(
        academic_year_id=ay.id, grade=2, class_num=4, label="高二4班"
    )
    db_session.add_all([admin, other_admin])
    db_session.flush()

    in_class = wm.WsStudentIdentity(data_domain="homeroom", display_name="在班生")
    left_class = wm.WsStudentIdentity(data_domain="homeroom", display_name="已离班")
    transferred = wm.WsStudentIdentity(data_domain="homeroom", display_name="转班生")
    db_session.add_all([in_class, left_class, transferred])
    db_session.flush()

    db_session.add_all(
        [
            wm.Enrollment(
                admin_class_id=admin.id,
                identity_id=in_class.id,
                seat_no=1,
                valid_from=date(2025, 9, 1),
            ),
            wm.Enrollment(  # 已过有效期
                admin_class_id=admin.id,
                identity_id=left_class.id,
                valid_from=date(2025, 9, 1),
                valid_to=date(2025, 10, 1),
            ),
            wm.Enrollment(  # 状态非 active
                admin_class_id=admin.id,
                identity_id=transferred.id,
                valid_from=date(2025, 9, 1),
                status="transferred",
            ),
        ]
    )

    t_identity = wm.WsStudentIdentity(data_domain="teaching", display_name="教学班学生")
    db_session.add(t_identity)
    db_session.flush()

    tc = wm.TeachingClass(
        academic_year_id=ay.id, subject="物理", label="高二物理3班"
    )
    tc_empty = wm.TeachingClass(
        academic_year_id=ay.id, subject="物理", label="高二物理选考班"
    )
    tc_other_year = wm.TeachingClass(
        academic_year_id=ay_old.id, subject="物理", label="旧学年物理班"
    )
    db_session.add_all([tc, tc_empty, tc_other_year])
    db_session.flush()

    db_session.add(
        wm.TeachingClassMember(
            teaching_class_id=tc.id,
            identity_id=t_identity.id,
            valid_from=date(2025, 9, 1),
        )
    )
    db_session.flush()
    return {
        "teacher": teacher,
        "ay": ay,
        "ay_old": ay_old,
        "admin": admin,
        "other_admin": other_admin,
        "tc": tc,
        "tc_empty": tc_empty,
        "tc_other_year": tc_other_year,
        "t_identity": t_identity,
    }


def test_homeroom_resolves_bound_class_and_members(db_session, seed):
    ctx = resolve_workspace_context(
        db_session, seed["teacher"].id, "homeroom", {"as_of": AS_OF, "grade": 2}
    )
    assert ctx.mode == "homeroom"
    assert ctx.data_domain == "homeroom"
    assert ctx.grade == 2
    assert ctx.class_ids == (seed["admin"].id,)
    assert ctx.member_person_ids  # 在班生解析到
    # 已离班 / 转班不在当期成员
    enrollments = db_session.query(wm.Enrollment).all()
    assert ctx.member_person_ids == tuple(
        sorted(e.identity_id for e in enrollments if e.status == "active" and e.valid_to is None)
    )
    assert ctx.academic_year_id == seed["ay"].id
    assert ctx.link_id is None and ctx.link_version is None


def test_homeroom_default_grade_from_active_grade(db_session, seed):
    ctx = resolve_workspace_context(
        db_session, seed["teacher"].id, "homeroom", {"as_of": AS_OF}
    )
    assert ctx.grade == 2  # homework_setting.active_grade
    assert ctx.class_ids == (seed["admin"].id,)


def test_homeroom_rejects_unbound_class(db_session, seed):
    with pytest.raises(ResourceOutOfScope) as exc:
        resolve_workspace_context(
            db_session,
            seed["teacher"].id,
            "homeroom",
            {"as_of": AS_OF, "grade": 2, "class_id": seed["other_admin"].id},
        )
    assert exc.value.to_http()[0] == 404


def test_homeroom_unbound_grade_not_configured(db_session, seed):
    # 教师未绑定高三班级
    with pytest.raises(WorkspaceNotConfigured):
        resolve_workspace_context(
            db_session, seed["teacher"].id, "homeroom", {"as_of": AS_OF, "grade": 3}
        )


def test_homeroom_bad_academic_year_out_of_scope(db_session, seed):
    with pytest.raises(ResourceOutOfScope):
        resolve_workspace_context(
            db_session,
            seed["teacher"].id,
            "homeroom",
            {"as_of": AS_OF, "grade": 2, "academic_year_id": 99999},
        )


def test_homeroom_fills_active_link(db_session, seed):
    tc = seed["tc"]
    link = wm.HomeroomTeachingLink(
        admin_class_id=seed["admin"].id,
        teaching_class_id=tc.id,
        academic_year_id=seed["ay"].id,
        subject="物理",
        valid_from=date(2025, 9, 1),
        version=3,
    )
    # 已撤销的关联（不同教学班，避开唯一约束）不应被采用
    cancelled = wm.HomeroomTeachingLink(
        admin_class_id=seed["admin"].id,
        teaching_class_id=seed["tc_empty"].id,
        academic_year_id=seed["ay"].id,
        subject="物理",
        valid_from=date(2025, 9, 1),
        status="cancelled",
        version=1,
    )
    db_session.add_all([link, cancelled])
    db_session.flush()

    ctx = resolve_workspace_context(
        db_session, seed["teacher"].id, "homeroom", {"as_of": AS_OF, "grade": 2}
    )
    assert ctx.link_id == link.id
    assert ctx.link_version == 3
    assert ctx.subject == "物理"


def test_teaching_resolves_members(db_session, seed):
    ctx = resolve_workspace_context(
        db_session,
        seed["teacher"].id,
        "teaching",
        {
            "as_of": AS_OF,
            "teaching_class_id": [seed["tc"].id],
            "subject": "物理",
        },
    )
    assert ctx.mode == "teaching"
    assert ctx.data_domain == "teaching"
    assert ctx.subject == "物理"
    assert ctx.class_ids == (seed["tc"].id,)
    assert ctx.member_person_ids == (seed["t_identity"].id,)


def test_teaching_empty_members_is_valid_empty_scope(db_session, seed):
    """空成员是合法空态：context 正常返回，绝不回退全年级。"""
    ctx = resolve_workspace_context(
        db_session,
        seed["teacher"].id,
        "teaching",
        {
            "as_of": AS_OF,
            "teaching_class_id": [seed["tc_empty"].id],
            "subject": "物理",
        },
    )
    assert ctx.member_person_ids == ()
    assert ctx.class_ids == (seed["tc_empty"].id,)


def test_teaching_rejects_class_from_other_academic_year(db_session, seed):
    with pytest.raises(ResourceOutOfScope):
        resolve_workspace_context(
            db_session,
            seed["teacher"].id,
            "teaching",
            {
                "as_of": AS_OF,
                "teaching_class_id": [seed["tc_other_year"].id],
                "subject": "物理",
            },
        )


def test_teaching_without_subject_not_configured(db_session, seed):
    with pytest.raises(WorkspaceNotConfigured):
        resolve_workspace_context(
            db_session,
            seed["teacher"].id,
            "teaching",
            {"as_of": AS_OF, "teaching_class_id": [seed["tc"].id]},
        )


def test_teaching_missing_class_id_invalid_param(db_session, seed):
    with pytest.raises(InvalidScopeParam):
        resolve_workspace_context(
            db_session, seed["teacher"].id, "teaching", {"as_of": AS_OF, "subject": "物理"}
        )


def test_invalid_mode_rejected(db_session, seed):
    with pytest.raises(InvalidScopeParam):
        resolve_workspace_context(db_session, seed["teacher"].id, "admin", {})
    # mode 不是权限凭证：字符串 'all' 之类一律拒绝
    with pytest.raises(InvalidScopeParam):
        resolve_workspace_context(db_session, seed["teacher"].id, "all", {})


def test_unknown_teacher_not_configured(db_session, seed):
    with pytest.raises(WorkspaceNotConfigured):
        resolve_workspace_context(db_session, 424242, "homeroom", {"as_of": AS_OF, "grade": 2})


def test_foreign_keys_enabler_scoped_and_reversible():
    """enable_sqlite_foreign_keys() 显式开启后，借出的每个连接 FK=ON；
    测试结束后注销监听并重建连接池，避免遗留班主任版模块在本进程中
    继承 FK 开关（其批量删除依赖 FK 关闭，后续波次再修复写入顺序）。"""
    from sqlalchemy import event

    from app.db.models import engine

    wm.enable_sqlite_foreign_keys(engine)
    try:
        assert event.contains(engine, "checkout", wm._enable_sqlite_foreign_keys)
        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    finally:
        event.remove(engine, "checkout", wm._enable_sqlite_foreign_keys)
        engine.dispose()
