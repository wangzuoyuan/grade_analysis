"""R7：应用进程（非迁移进程）的 SQLite 外键必须真实开启。

models.py 为共享 engine 注册了 checkout 监听（PRAGMA foreign_keys=ON），
任何从应用 Session 借出的连接都不得接受孤儿行；同时正常的插入-回滚
事务路径不受影响（外键开启不产生误伤）。
"""

from datetime import date

import pytest


def test_app_session_rejects_orphan_enrollment():
    from sqlalchemy.exc import IntegrityError

    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    db = SessionLocal()
    try:
        # admin_class_id / identity_id 均指向不存在的父行：外键真实开启时
        # commit 必须以 IntegrityError 拒绝，而不是静默落库成孤儿行。
        db.add(
            wm.Enrollment(
                admin_class_id=999999,
                identity_id=999999,
                valid_from=date(2025, 9, 1),
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_app_session_normal_insert_rollback_unaffected():
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    db = SessionLocal()
    try:
        ay = wm.AcademicYear(
            name="2025-2026-r7",
            start_date=date(2025, 9, 1),
            end_date=date(2026, 6, 30),
        )
        db.add(ay)
        db.flush()
        admin_class = wm.AdministrativeClass(
            academic_year_id=ay.id, grade=2, class_num=6
        )
        identity = wm.WsStudentIdentity(data_domain="homeroom", display_name="R7 学生")
        db.add_all([admin_class, identity])
        db.flush()
        db.add(
            wm.Enrollment(
                admin_class_id=admin_class.id,
                identity_id=identity.id,
                valid_from=date(2025, 9, 1),
            )
        )
        db.flush()  # 合法引用的 INSERT 在外键开启下照常成功
        db.rollback()
        assert db.query(wm.Enrollment).count() == 0  # 回滚语义不变
    finally:
        db.close()
