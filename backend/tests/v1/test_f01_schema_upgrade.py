"""F01 回归：ensure_app_schema 必须版本感知，已有版本库启动只走迁移演进。

审核反例：旧实现先 create_all 再 upgrade，停在 0001 的已有版本库
启动时被最新 metadata 提前建出 0003/0005 的新表，随后 upgrade 建表即崩
（``table homework_assignment already exists``），应用无法启动；空库路径
掩盖了此缺陷。本文件全部用独立临时 EXAM_TRACKER_DIR 跑子进程（参照
test_m01_migration_repeat.py 的 sys.executable 模式），绝不触碰默认
~/.exam-tracker。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

# tests/v1/ 向上两级才是 backend/（与 test_m01_migration_repeat.py 同口径）
BACKEND_DIR = Path(__file__).resolve().parents[2]

# head 形态关键表（与 schema.py 的抽查口径一致，分属 0003/0005 建表迁移）
HEAD_KEY_TABLES = {
    "homework_assignment",
    "homework_submission",
    "ws_student_note",
    "ws_homework_semester",
}


def _alembic_head() -> str:
    """进程内读取迁移链 head，避免测试硬编码版本号随迁移追加而腐化。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _subprocess_env(data_dir: str) -> dict:
    env = dict(os.environ)
    env["EXAM_TRACKER_DIR"] = data_dir
    env["EXAM_TRACKER_BACKUP_DIR"] = str(Path(data_dir) / "backups")
    # python -c 场景显式给 PYTHONPATH，不依赖 cwd 推断
    env["PYTHONPATH"] = str(BACKEND_DIR)
    return env


def _run(args, data_dir: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=BACKEND_DIR,
        env=_subprocess_env(data_dir),
        capture_output=True,
        text=True,
        timeout=600,
    )


def _run_alembic(data_dir: str, *argv: str) -> subprocess.CompletedProcess:
    return _run([sys.executable, "-m", "alembic", *argv], data_dir)


def _run_ensure_schema(data_dir: str) -> subprocess.CompletedProcess:
    # 与审核反例同一路径：子进程内直接调用 main lifespan 的初始化入口
    return _run(
        [sys.executable, "-c", "from app.db.schema import ensure_app_schema; ensure_app_schema()"],
        data_dir,
    )


def _db_path(data_dir: str) -> Path:
    return Path(data_dir) / "db.sqlite"


def _run_sql(db_path: Path, sql: str) -> None:
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute(sql)
        con.commit()
    finally:
        con.close()


def _fetchall(db_path: Path, sql: str):
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _table_names(db_path: Path) -> set:
    return {
        row[0]
        for row in _fetchall(
            db_path, "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _version_rows(db_path: Path):
    return _fetchall(db_path, "SELECT version_num FROM alembic_version")


def test_f01_start_from_0001_upgrades_via_migrations_and_keeps_data():
    """停在 0001 的带数据版本库：启动只迁移演进，不先建未来表、不丢原记录。"""
    with tempfile.TemporaryDirectory(prefix="f01-from-0001-") as data_dir:
        head = _alembic_head()
        proc = _run_alembic(data_dir, "upgrade", "0001")
        assert proc.returncode == 0, proc.stdout + proc.stderr

        db_path = _db_path(data_dir)
        # 升级前落一行业务数据：结构演进绝不能吃掉原记录
        _run_sql(
            db_path,
            "INSERT INTO academic_year (name, start_date, end_date) "
            "VALUES ('2025-2026', '2025-09-01', '2026-06-30')",
        )

        # 审核反例场景：旧实现在此退出 1（homework_assignment already exists）
        proc = _run_ensure_schema(data_dir)
        assert proc.returncode == 0, proc.stdout + proc.stderr

        assert _version_rows(db_path) == [(head,)]
        assert _fetchall(db_path, "SELECT name FROM academic_year") == [
            ("2025-2026",)
        ]
        # 迁移链负责把后续新表补齐，且不是由 create_all 抢先建出
        assert HEAD_KEY_TABLES <= _table_names(db_path)


def test_f01_start_from_0003_upgrades_and_keeps_data():
    """停在 0003 的版本库：升级成功，0003 期已有作业数据原样保留。"""
    with tempfile.TemporaryDirectory(prefix="f01-from-0003-") as data_dir:
        head = _alembic_head()
        proc = _run_alembic(data_dir, "upgrade", "0003")
        assert proc.returncode == 0, proc.stdout + proc.stderr

        db_path = _db_path(data_dir)
        _run_sql(
            db_path,
            "INSERT INTO academic_year (name, start_date, end_date) "
            "VALUES ('2025-2026', '2025-09-01', '2026-06-30')",
        )
        _run_sql(
            db_path,
            "INSERT INTO homework_assignment (data_domain, class_ref_id, "
            "academic_year_id, subject, homework_type, assigned_date, "
            "batch_token, expected_members_json) VALUES "
            "('teaching', 1, 1, '物理', '日常作业', '2025-11-01', "
            "'f01-token-0003', '[]')",
        )

        proc = _run_ensure_schema(data_dir)
        assert proc.returncode == 0, proc.stdout + proc.stderr

        assert _version_rows(db_path) == [(head,)]
        assert _fetchall(
            db_path, "SELECT batch_token FROM homework_assignment"
        ) == [("f01-token-0003",)]
        assert HEAD_KEY_TABLES <= _table_names(db_path)


def test_f01_ensure_schema_idempotent_called_twice():
    """重复启动（连续两次 ensure_app_schema）：幂等，版本不叠加、无异常。"""
    with tempfile.TemporaryDirectory(prefix="f01-idempotent-") as data_dir:
        head = _alembic_head()
        first = _run_ensure_schema(data_dir)
        assert first.returncode == 0, first.stdout + first.stderr

        second = _run_ensure_schema(data_dir)
        assert second.returncode == 0, second.stdout + second.stderr

        db_path = _db_path(data_dir)
        assert _version_rows(db_path) == [(head,)]


def test_f01_empty_db_still_creates_and_stamps_head():
    """空库直接启动（原路径回归）：建齐全量表并 stamp head。"""
    with tempfile.TemporaryDirectory(prefix="f01-empty-") as data_dir:
        head = _alembic_head()
        proc = _run_ensure_schema(data_dir)
        assert proc.returncode == 0, proc.stdout + proc.stderr

        db_path = _db_path(data_dir)
        assert _version_rows(db_path) == [(head,)]
        assert HEAD_KEY_TABLES <= _table_names(db_path)


def test_f01_legacy_tables_without_version_table_refuse_to_stamp():
    """有业务表但无版本表且缺 head 关键表：必须报错，绝不静默 stamp。"""
    with tempfile.TemporaryDirectory(prefix="f01-legacy-") as data_dir:
        db_path = _db_path(data_dir)
        # 手工造历史遗留库：只有旧域业务表，没有 alembic_version
        _run_sql(db_path, "CREATE TABLE teacher (id INTEGER PRIMARY KEY, name TEXT)")

        proc = _run_ensure_schema(data_dir)
        assert proc.returncode != 0, "演进状态不明的遗留库不得被自动初始化"
        # 报错必须指向迁移/人工核对，而不是一句裸异常
        assert "迁移" in proc.stderr
        assert "人工核对" in proc.stderr
        # 绝不静默 stamp：版本表不应被建出，业务表也不该被顺手动掉
        assert "alembic_version" not in _table_names(db_path)
        assert "teacher" in _table_names(db_path)
