"""Alembic v1（0001_workspace_tables）迁移演练：可升级、幂等重复、可回退。

在独立临时 EXAM_TRACKER_DIR 中以子进程运行 alembic（与应用部署同一路径），
不触碰测试共享库，也不访问默认 ~/.exam-tracker。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]

# 0001 新建的工作台表（含 ws_ 前缀身份表）
WS_TABLES = {
    "academic_year",
    "term",
    "cohort",
    "ws_student_identity",
    "ws_student_alias",
    "administrative_class",
    "enrollment",
    "teaching_class",
    "teaching_class_member",
    "homeroom_teaching_link",
    "linked_student",
    "source_map",
    "migration_run",
    "score_fact",
    "import_batch",
}

# 既有 H 表（0001 不管辖）：import app.db.models 已零建表副作用（R8），
# 空库 alembic upgrade head 后旧表必须不存在——这是 R8 的验收断言。
LEGACY_TABLE = "teacher"


def _run_alembic(data_dir: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["EXAM_TRACKER_DIR"] = data_dir
    env["EXAM_TRACKER_BACKUP_DIR"] = str(Path(data_dir) / "backups")
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"alembic {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}"
    return proc


def _table_names(db_path: Path) -> set:
    con = sqlite3.connect(str(db_path))
    try:
        return {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        con.close()


def _run_sql(db_path: Path, sql: str) -> None:
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute(sql)
        con.commit()
    finally:
        con.close()


def test_alembic_v1_upgrade_idempotent_and_reversible():
    with tempfile.TemporaryDirectory(prefix="exam-tracker-alembic-v1-") as tmp:
        db_path = Path(tmp) / "db.sqlite"

        # 首次升级：新表 + alembic_version 全部就位
        _run_alembic(tmp, "upgrade", "head")
        tables = _table_names(db_path)
        assert "alembic_version" in tables
        assert WS_TABLES <= tables
        # R8：import 无建表副作用，旧 H 表不得被建出
        assert LEGACY_TABLE not in tables

        # 幂等重复升级：无报错、无残留差异
        _run_alembic(tmp, "upgrade", "head")
        assert WS_TABLES <= _table_names(db_path)

        # 外键真实开启：先建合法父行，非法 academic_year_id 的写入被拒绝
        _run_sql(
            db_path,
            "INSERT INTO academic_year (name, start_date, end_date) "
            "VALUES ('2025-2026', '2025-09-01', '2026-06-30')",
        )
        _run_sql(
            db_path,
            "INSERT INTO ws_student_identity (data_domain, display_name) "
            "VALUES ('teaching', 'FK 演练学生')",
        )
        valid_ay = 1  # sqlite 自增从 1 起（上方首条插入）
        _run_sql(
            db_path,
            f"INSERT INTO score_fact (data_domain, academic_year_id, exam_name, "
            f"identity_id, subject_key, total_key) VALUES "
            f"('teaching', {valid_ay}, '期中', 1, '物理', '')",
        )
        try:
            _run_sql(
                db_path,
                "INSERT INTO score_fact (data_domain, academic_year_id, exam_name, "
                "identity_id, subject_key, total_key) VALUES "
                "('teaching', 9999, '期中2', 1, '物理', '')",
            )
            raise AssertionError("foreign key constraint not enforced on score_fact")
        except sqlite3.IntegrityError:
            pass

        # 回退到 base：只删工作台新表（旧 H 表本就不存在，R8）
        _run_alembic(tmp, "downgrade", "base")
        tables = _table_names(db_path)
        assert not (WS_TABLES & tables)
        assert LEGACY_TABLE not in tables
        assert "alembic_version" in tables  # alembic 版本表自身保留（空）

        # 再次升级：可重复执行
        _run_alembic(tmp, "upgrade", "head")
        assert WS_TABLES <= _table_names(db_path)
