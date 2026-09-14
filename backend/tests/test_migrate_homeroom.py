"""班主任版数据层迁移（migrate_homeroom.migrate）测试。

完全隔离：每个用例建自己的临时 SQLite 文件引擎，绝不触碰
~/.exam-tracker 或 EXAM_TRACKER_DIR 指向的真实库。
"""
import os
import tempfile

import pytest
from sqlalchemy import create_engine, inspect, text

from app.db.models import Base, HomeworkSetting
from app.db.migrate_homeroom import migrate


def _fresh_engine():
    """返回一个绑定到全新临时文件的 SQLite engine（文件路径全随机）。"""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="migrate_test_")
    os.close(fd)
    return create_engine(f"sqlite:///{path}")


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": name},
    ).scalar()
    return bool(row)


def _columns(conn, table: str) -> list:
    return [r[1] for r in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()]


# ───────────────────────── 测试用例 ─────────────────────────


def test_migrate_creates_tables_and_grade_column():
    """fresh boot 场景：先 create_all（模拟 app 启动建表），再 migrate，
    新增的 student_identity / student_alias / imported_history 存在，
    class_roster 有 grade 列。"""
    engine = _fresh_engine()
    Base.metadata.create_all(engine)

    summary = migrate(engine)

    with engine.connect() as conn:
        assert _table_exists(conn, "student_identity")
        assert _table_exists(conn, "student_alias")
        assert _table_exists(conn, "imported_history")
        cols = _columns(conn, "class_roster")
        assert "grade" in cols
    # create_all 已建表，migrate 不应再新建
    assert "student_identity" not in summary.get("created", [])


def test_migrate_idempotent():
    """migrate 可安全重复调用，grade 列只出现一次，不抛错。"""
    engine = _fresh_engine()
    Base.metadata.create_all(engine)

    migrate(engine)
    summary2 = migrate(engine)  # 第二次：必须无异常

    with engine.connect() as conn:
        cols = _columns(conn, "class_roster")
        # grade 恰好出现一次（idempotent：不会重复 ALTER）
        assert cols.count("grade") == 1
    # 第二次跑不应再报告“新增了 grade 列”
    assert summary2.get("added_grade_column") is False


def test_migrate_active_grade_default():
    """fresh engine 迁移后：homework_setting 有 active_grade 行且值可解析为 int，
    schema_version == 'homeroom_v1'。"""
    engine = _fresh_engine()
    Base.metadata.create_all(engine)

    migrate(engine)

    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        ag = db.query(HomeworkSetting).filter_by(key="active_grade").first()
        assert ag is not None
        assert ag.value is not None
        # 必须可解析为 int
        assert int(ag.value) >= 1

        sv = db.query(HomeworkSetting).filter_by(key="schema_version").first()
        assert sv is not None
        assert sv.value == "homeroom_v1"
    finally:
        db.close()


def test_migrate_adds_status_column_to_legacy_db():
    """旧库升级场景：class_roster 没有 status 列时幂等补列，已有行保持
    NULL（按在班处理），重复 migrate 不重复加列。"""
    engine = _fresh_engine()

    # 模拟 status 列引入前的旧库（只有 grade 列，含一行在班学生）
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE class_roster ("
            "student_id TEXT PRIMARY KEY, name TEXT NOT NULL, class_num INTEGER, "
            "grade INTEGER, seat_no INTEGER, gender TEXT, excluded INTEGER DEFAULT 0)"
        ))
        conn.execute(text(
            "INSERT INTO class_roster (student_id, name, class_num, grade) "
            "VALUES ('9900601', '张三', 6, 2)"
        ))

    Base.metadata.create_all(engine)  # 对已存在表为 no-op
    summary = migrate(engine)

    with engine.connect() as conn:
        cols = _columns(conn, "class_roster")
        assert "status" in cols
        # 旧行仍在且 status 为 NULL（按在班处理）
        row = conn.execute(text(
            "SELECT name, status FROM class_roster WHERE student_id='9900601'"
        )).fetchone()
        assert row == ("张三", None)

    assert summary.get("added_status_column") is True

    # 幂等：第二次迁移不再加列
    summary2 = migrate(engine)
    with engine.connect() as conn:
        assert _columns(conn, "class_roster").count("status") == 1
    assert summary2.get("added_status_column") is False


def test_migrate_adds_changelog_scope_columns_to_preview_table():
    """预览版本可能已建 student_change_log（缺 grade/class_num 列）：
    migrate 幂等补列，已有行保持 NULL，重复 migrate 不重复加列。"""
    engine = _fresh_engine()

    with engine.begin() as conn:
        # 模拟预览版本建出的变更日志表（无作用域列），含一条历史留痕
        conn.execute(text(
            "CREATE TABLE student_change_log ("
            "id INTEGER PRIMARY KEY, op_type TEXT NOT NULL, identity_id INTEGER, "
            "student_id TEXT, before_summary JSON, after_summary JSON, "
            "detail JSON, created_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT INTO student_change_log (op_type, student_id) "
            "VALUES ('create', '9900601')"
        ))
        # class_roster 也一并建出，避免 roster 补列分支报错干扰
        conn.execute(text(
            "CREATE TABLE class_roster ("
            "student_id TEXT PRIMARY KEY, name TEXT NOT NULL, class_num INTEGER, "
            "seat_no INTEGER, gender TEXT, excluded INTEGER DEFAULT 0)"
        ))

    Base.metadata.create_all(engine)  # 对已存在表为 no-op
    summary = migrate(engine)

    with engine.connect() as conn:
        cols = _columns(conn, "student_change_log")
        assert "grade" in cols and "class_num" in cols
        row = conn.execute(text(
            "SELECT student_id, grade, class_num FROM student_change_log"
        )).fetchone()
        assert row == ("9900601", None, None)  # 旧行保留，作用域为 NULL

    assert summary.get("added_changelog_columns") == ["grade", "class_num"]

    # 幂等：第二次迁移不再加列
    summary2 = migrate(engine)
    with engine.connect() as conn:
        log_cols = _columns(conn, "student_change_log")
        assert log_cols.count("grade") == 1 and log_cols.count("class_num") == 1
    assert summary2.get("added_changelog_columns") == []


def test_migrate_messy_db_robust():
    """关键风险测试：脏库（模拟现网 dev DB）下 migrate 不抛错且行为正确。

    脏库构成：
      - class_roster 多出 class_label 列（孤立遗留）
      - 孤立 teaching_class 表（带一行数据）+ teaching_class_member 留痕
      - 预先存在的 student_identity 表（列结构与目标一致）
    预期：migrate 不抛错；class_roster 补上 grade 列（在已有 class_label 之上 ALTER）；
    孤立 teaching_class 不被删除；student_identity 仍存在。
    """
    engine = _fresh_engine()

    # 关键：在 create_all 之前，用裸 DDL 摆出脏库状态
    with engine.begin() as conn:
        # 带多余 class_label 的 class_roster
        conn.execute(text(
            "CREATE TABLE class_roster ("
            "student_id TEXT PRIMARY KEY, name TEXT NOT NULL, class_num INTEGER, "
            "class_label TEXT, seat_no INTEGER, gender TEXT, excluded INTEGER DEFAULT 0)"
        ))
        # 孤立 teaching_class + 一行数据
        conn.execute(text(
            "CREATE TABLE teaching_class (id INTEGER PRIMARY KEY, label TEXT)"
        ))
        conn.execute(text("INSERT INTO teaching_class (id, label) VALUES (1, '实验班')"))
        # 预先存在的 student_identity
        conn.execute(text(
            "CREATE TABLE student_identity ("
            "id INTEGER PRIMARY KEY, display_name TEXT, gender TEXT, "
            "ext_key TEXT, note TEXT, created_at DATETIME)"
        ))

    # 现在 create_all（模拟 app 启动）：对已存在表为 no-op，补建其他表
    Base.metadata.create_all(engine)

    # migrate 必须不抛错
    summary = migrate(engine)

    with engine.connect() as conn:
        roster_cols = _columns(conn, "class_roster")
        # grade 列被补上（即便该表已有多余的 class_label）
        assert "grade" in roster_cols
        # class_label 被保留（不删列）
        assert "class_label" in roster_cols

        # 孤立表 teaching_class 仍在（不被 migrate 删除）
        assert _table_exists(conn, "teaching_class")
        # 且其数据完好
        row = conn.execute(text("SELECT label FROM teaching_class WHERE id=1")).scalar()
        assert row == "实验班"

        # student_identity 仍在
        assert _table_exists(conn, "student_identity")
        # student_alias / imported_history 也被建出
        assert _table_exists(conn, "student_alias")
        assert _table_exists(conn, "imported_history")

    # 摘要里应反映 grade 列确实是这次 ALTER 进去的
    assert summary.get("added_grade_column") is True
