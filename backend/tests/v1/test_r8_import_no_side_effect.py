"""R8：import app.db.models 零建表副作用。

在全新临时 EXAM_TRACKER_DIR 的子进程中仅 import app.db.models，断言：
- 进程退出码 0（import 本身不报错）；
- 数据目录中 db.sqlite 不存在（create_engine 惰性连接，连文件都不建），
  或即使存在也无任何表（sqlite_master 表计数为 0）。
建表职责已收敛到 app.db.schema.ensure_app_schema（启动时显式调用）。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


def test_import_models_creates_no_tables():
    with tempfile.TemporaryDirectory(prefix="v1-r8-import-") as tmp:
        env = dict(os.environ)
        env["EXAM_TRACKER_DIR"] = tmp
        env["EXAM_TRACKER_BACKUP_DIR"] = os.path.join(tmp, "backups")
        proc = subprocess.run(
            [sys.executable, "-c", "import app.db.models"],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, f"import 失败:\n{proc.stdout}\n{proc.stderr}"

        db_path = Path(tmp) / "db.sqlite"
        if not db_path.exists():
            return  # 连库文件都不创建：create_engine 不做惰性之外的连接
        con = sqlite3.connect(str(db_path))
        try:
            count = con.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
        finally:
            con.close()
        assert count == 0, "import 不得创建任何表（R8 零副作用）"
