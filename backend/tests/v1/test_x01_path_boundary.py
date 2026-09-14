"""X01（ 终审）：回退演练脚本的数据目录边界校验。

反例（ path-boundary.log 复现）：--root 指向演练根 A，EXAM_TRACKER_DIR
指向根外的合法合成 SQLite 库 B——旧实现退出 0 并在 B 内建目录/写入/覆盖。
修复后必须：退出 2、stderr 含「红线」、B 目录与库**零变化**（不建 raw/、
不改 db 内容/mtime）。符号链接逃逸经 realpath 解析后同样拒绝。
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
_SCRIPT = REPO / "scripts" / "migration" / "rehearse_backup_restore.py"


def _snapshot_tree(base: Path) -> dict:
    out = {}
    for p in sorted(base.rglob("*")):
        rel = str(p.relative_to(base))
        out[rel] = (p.is_dir(), p.stat().st_mtime_ns if p.is_file() else None)
    return out


def test_x01_outside_root_data_dir_rejected_zero_change():
    with tempfile.TemporaryDirectory(prefix="v1-x01-") as tmp:
        root = Path(tmp) / "requested-root"
        outside = Path(tmp) / "outside-requested-root"
        root.mkdir()
        outside.mkdir()
        # 根外目录放一个"合法"合成库（有表有数据，足以通过旧实现的库存在检查）
        db = outside / "db.sqlite"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE t (x INTEGER)")
        con.execute("INSERT INTO t VALUES (42)")
        con.commit()
        con.close()
        before_tree = _snapshot_tree(outside)
        before_bytes = db.read_bytes()

        env = dict(os.environ)
        env["EXAM_TRACKER_DIR"] = str(outside)
        env["EXAM_TRACKER_BACKUP_DIR"] = str(outside / "backups")
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT), "--root", str(root)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert proc.returncode == 2, f"根外数据目录必须 exit 2，实际 {proc.returncode}"
        assert "红线" in proc.stderr, f"stderr 应含红线提示：{proc.stderr}"
        # 零变化：目录树与库字节完全一致（raw/ 未建、db 未被碰）
        assert _snapshot_tree(outside) == before_tree, "根外目录必须零变化"
        assert db.read_bytes() == before_bytes, "根外数据库必须零变化"


def test_x01_symlink_escape_rejected():
    with tempfile.TemporaryDirectory(prefix="v1-x01-sym-") as tmp:
        root = Path(tmp) / "root"
        real_outside = Path(tmp) / "real-outside"
        root.mkdir()
        real_outside.mkdir()
        (real_outside / "db.sqlite").write_bytes(b"")  # 占位（空文件即视为库存在）
        # 根内符号链接指向根外——realpath 解析后落在根外，必须拒绝
        link_dir = root / "escaped"
        link_dir.symlink_to(real_outside, target_is_directory=True)

        env = dict(os.environ)
        env["EXAM_TRACKER_DIR"] = str(link_dir)
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT), "--root", str(root)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        # 符号链接路径经 realpath 后在根外 → exit 2（而非把链接目标当根内目录执行）
        assert proc.returncode == 2, f"符号链接逃逸必须 exit 2，实际 {proc.returncode}"
        assert "红线" in proc.stderr
