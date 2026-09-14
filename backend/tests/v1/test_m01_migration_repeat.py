"""M01 迁移可重入：alembic upgrade head 两次 + downgrade base 全部成功。

以独立临时 EXAM_TRACKER_DIR 跑子进程（cwd=backend），绝不触碰默认
~/.exam-tracker；用 sqlite3 模块直接开库断言 alembic_version 表存在且
版本行随升降变化。契约先行：alembic.ini 与迁移脚本由 P1 实现方提供。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _version_rows(db_path):
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT version_num FROM alembic_version").fetchall()
    finally:
        con.close()


def test_alembic_repeat_upgrade_then_downgrade_base():
    tmp_dir = tempfile.mkdtemp(prefix="v1-m01-")
    env = os.environ.copy()
    env["EXAM_TRACKER_DIR"] = tmp_dir
    env["EXAM_TRACKER_BACKUP_DIR"] = os.path.join(tmp_dir, "backups")

    def run(*args):
        # 用当前解释器的 -m alembic（同 test_alembic_v1.py 的 _run_alembic），
        # 不写死任何本机虚拟环境路径（R9：.venv311 在 README/CI 环境不存在）。
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )

    db_path = os.path.join(tmp_dir, "db.sqlite")

    # 第一次 upgrade head：建库 + alembic_version 有且仅有一行
    up1 = run("upgrade", "head")
    assert up1.returncode == 0, up1.stdout + up1.stderr
    assert os.path.exists(db_path), "迁移后应存在 SQLite 数据库文件"
    rows = _version_rows(db_path)
    assert len(rows) == 1
    head_version = rows[0][0]
    assert head_version

    # 第二次 upgrade head：幂等，返回码 0，版本行不叠加
    up2 = run("upgrade", "head")
    assert up2.returncode == 0, up2.stdout + up2.stderr
    assert _version_rows(db_path) == [(head_version,)]

    # downgrade base：可完整回退，版本行清空
    down = run("downgrade", "base")
    assert down.returncode == 0, down.stdout + down.stderr
    assert _version_rows(db_path) == []
