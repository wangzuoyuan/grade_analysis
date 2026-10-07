"""P2-C4 迁移 0017 演练：可升级、旧数据兼容、可回退（契约 §5.4 迁移纪律）。

在独立临时 EXAM_TRACKER_DIR 中以子进程运行 alembic（与部署同一路径），
不触碰测试共享库。断言：
- upgrade head：8 个干预扩展列就位 + idx_ws_note_status 索引；
- 旧列（follow_up/follow_up_done 等）语义不动：升级前后旧行原样可读；
- 既有约束 ck_ws_note_domain 在 upgrade/downgrade 往返后仍在（不重建表）；
- downgrade 0016：扩展列移除、旧行数据保留；
- 再升级 / downgrade base 往返可重复执行。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]

_INTERVENTION_COLUMNS = (
    "problem",
    "subject_scope",
    "measures",
    "target_metric",
    "baseline_value",
    "start_date",
    "review_date",
    "status",
)

_LEGACY_COLUMNS = ("follow_up", "follow_up_done", "source", "created_at", "updated_at")


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


def _columns(db_path: Path, table: str) -> set:
    con = sqlite3.connect(str(db_path))
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        con.close()


def _index_names(db_path: Path) -> set:
    con = sqlite3.connect(str(db_path))
    try:
        return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        con.close()


def _table_ddl(db_path: Path, table: str) -> str:
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
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


def _legacy_row(db_path: Path):
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(
            "SELECT content, follow_up, follow_up_done FROM ws_student_note WHERE id=1"
        ).fetchone()
    finally:
        con.close()


def test_migration_0017_upgrade_downgrade_roundtrip():
    with tempfile.TemporaryDirectory(prefix="exam-tracker-c4-mig-") as tmp:
        db_path = Path(tmp) / "db.sqlite"

        # 升级到 0017（head）：扩展列 + 索引就位
        _run_alembic(tmp, "upgrade", "0017")
        cols = _columns(db_path, "ws_student_note")
        assert set(_INTERVENTION_COLUMNS) <= cols
        assert set(_LEGACY_COLUMNS) <= cols  # 旧列一个不少
        assert "idx_ws_note_status" in _index_names(db_path)
        # 既有约束未被表重建抹掉
        assert "ck_ws_note_domain" in _table_ddl(db_path, "ws_student_note")

        # 旧式档案行（只用旧列）+ 干预行共存：旧数据完全兼容
        _run_sql(
            db_path,
            "INSERT INTO ws_student_identity (data_domain, display_name) "
            "VALUES ('homeroom', '迁移演练学生')",
        )
        _run_sql(
            db_path,
            "INSERT INTO ws_student_note (data_domain, person_id, date, category, "
            "content, follow_up, follow_up_done) "
            "VALUES ('homeroom', 1, '2026-09-01', '谈话', '旧档案内容', '旧跟进', 0)",
        )
        _run_sql(
            db_path,
            "INSERT INTO ws_student_note (data_domain, person_id, date, category, "
            "content, follow_up, follow_up_done, problem, subject_scope, status, "
            "start_date, review_date) "
            "VALUES ('homeroom', 1, '2026-09-02', '谈话', '干预记录', '复查跟进', 0, "
            "'主三门名次下滑', '数学', 'open', '2026-09-02', '2026-10-02')",
        )
        assert _legacy_row(db_path) == ("旧档案内容", "旧跟进", 0)

        # downgrade 0016：扩展列移除，旧列与旧行数据保留
        _run_alembic(tmp, "downgrade", "0016")
        cols = _columns(db_path, "ws_student_note")
        assert not (set(_INTERVENTION_COLUMNS) & cols)
        assert set(_LEGACY_COLUMNS) <= cols
        assert "idx_ws_note_status" not in _index_names(db_path)
        assert _legacy_row(db_path) == ("旧档案内容", "旧跟进", 0)
        assert "ck_ws_note_domain" in _table_ddl(db_path, "ws_student_note")

        # 再次升级：可重复执行，干预行仍在（扩展列值随 downgrade 移除属预期）
        _run_alembic(tmp, "upgrade", "head")
        assert set(_INTERVENTION_COLUMNS) <= _columns(db_path, "ws_student_note")
        assert _legacy_row(db_path) == ("旧档案内容", "旧跟进", 0)

        # downgrade base：ws_student_note 随建表迁移逆序删除
        _run_alembic(tmp, "downgrade", "base")
        con = sqlite3.connect(str(db_path))
        try:
            tables = {
                row[0]
                for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            con.close()
        assert "ws_student_note" not in tables


def test_alembic_head_constant_matches_chain():
    """schema.py ALEMBIC_HEAD 与迁移链一致（P2-C4 新增 0017 后必须同步更新）。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.db import schema

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    assert schema.ALEMBIC_HEAD == head
