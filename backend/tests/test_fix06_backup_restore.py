"""修复 6：页面备份/恢复的 WAL 一致性与受控替换（合成数据，绝不触碰真实库）。

覆盖：
- create_backup 用 SQLite 备份接口做一致性快照：WAL 模式下已提交但未
  checkpoint 的数据也进入备份（修复前裸拷主文件会得到 0 条）；
- DELETE（默认）日志模式同样完整；
- homework_exports 打包行为不回归；
- restore 四要素：备份校验（损坏 zip/缺 db.sqlite/非 SQLite/缺核心表/
  页损坏 → 400 且原库不动）、before-restore 安全备份、engine.dispose 后
  清理 -wal/-shm 并原子替换、替换失败时原库完好且返回可读 500。
"""
import os
import sqlite3
import zipfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.backup import router as br


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """把 backup 路由指向独立临时目录：合成库 + 独立备份目录。"""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    paths = SimpleNamespace(
        db=tmp_path / "data" / "db.sqlite",
        backup_dir=backup_dir,
        scratch=tmp_path,
    )
    paths.db.parent.mkdir()
    monkeypatch.setattr(br, "DB_PATH", str(paths.db))
    monkeypatch.setattr(br, "BACKUP_DIR", str(backup_dir))
    return paths


def _make_app_db(path, journal_mode=None, marker_row=False, bulk_rows=0):
    """造一个带核心标志表（teacher/score_fact）的合成应用库，返回连接。"""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE teacher (id INTEGER PRIMARY KEY, name TEXT)")
    con.execute("CREATE TABLE score_fact (id INTEGER PRIMARY KEY, score REAL, note TEXT)")
    con.commit()
    if journal_mode:
        con.execute(f"PRAGMA journal_mode={journal_mode}")
    if bulk_rows:
        con.executemany("INSERT INTO score_fact (score, note) VALUES (?, ?)",
                        [(float(i), "合成填充行-" + "x" * 80) for i in range(bulk_rows)])
        con.commit()
    if marker_row:
        con.execute("INSERT INTO score_fact (score) VALUES (88.5)")
        con.commit()
    return con


def _zip_db(backup_dir, zip_name, db_file):
    with zipfile.ZipFile(backup_dir / zip_name, "w") as zf:
        zf.write(db_file, "db.sqlite")


def _snapshot_table_count(backup_dir, filename, table="score_fact"):
    """从备份 zip 解出 db.sqlite 快照，返回指定表行数。"""
    extracted = backup_dir / "extracted-for-audit.sqlite"
    with zipfile.ZipFile(backup_dir / filename) as zf:
        assert "db.sqlite" in zf.namelist()
        with zf.open("db.sqlite") as src, open(extracted, "wb") as dst:
            dst.write(src.read())
    con = sqlite3.connect(extracted)
    try:
        return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        con.close()
        extracted.unlink()


def _db_score_rows(path):
    """直接读当前库文件（不触发任何修复逻辑），返回 score_fact 行数。"""
    if not os.path.exists(path):
        return None
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return con.execute("SELECT count(*) FROM score_fact").fetchone()[0]
    finally:
        con.close()


# ───────────────────────── create_backup：一致性快照 ─────────────────────────

def test_backup_wal_mode_includes_committed_uncheckpointed_rows(paths):
    """WAL 库：已提交未 checkpoint 的数据必须进入备份（修复前为 0 条）。"""
    con = _make_app_db(paths.db, journal_mode="WAL", marker_row=True)
    try:
        # 提交事务仍留在 -wal 中（连接未关闭，也不会触发 checkpoint）。
        assert os.path.exists(str(paths.db) + "-wal")
        assert os.path.getsize(str(paths.db) + "-wal") > 0
        filename = br.create_backup("wal-audit")
        assert _snapshot_table_count(paths.backup_dir, filename) == 1
    finally:
        con.close()


def test_backup_delete_journal_mode_complete(paths):
    con = _make_app_db(paths.db, journal_mode="DELETE", marker_row=True)
    try:
        filename = br.create_backup("delete-audit")
        assert _snapshot_table_count(paths.backup_dir, filename) == 1
    finally:
        con.close()


def test_backup_still_packages_homework_exports(paths, monkeypatch):
    export_dir = paths.db.parent / "homework_exports"
    export_dir.mkdir()
    (export_dir / "note.txt").write_text("合成导出", encoding="utf-8")
    monkeypatch.setattr(br, "EXPORT_DIR", str(export_dir))
    con = _make_app_db(paths.db, marker_row=True)
    try:
        filename = br.create_backup("export-audit")
        with zipfile.ZipFile(paths.backup_dir / filename) as zf:
            assert "homework_exports/note.txt" in zf.namelist()
    finally:
        con.close()


# ───────────────────────── restore：正常路径 ─────────────────────────

def test_restore_replaces_db_and_clears_stale_wal_sidecars(client, paths):
    """正常恢复：换库成功、before-restore 自动备份、旧 -wal/-shm 不残留。"""
    source = _make_app_db(paths.scratch / "source.sqlite", marker_row=True)
    source.close()
    _zip_db(paths.backup_dir, "good.zip", paths.scratch / "source.sqlite")
    # 当前库是另一份（0 行 marker），预埋旧库伴随文件模拟 WAL 残留。
    current = _make_app_db(paths.db)
    current.close()
    wal, shm = str(paths.db) + "-wal", str(paths.db) + "-shm"
    open(wal, "wb").write(b"stale-wal-of-old-db")
    open(shm, "wb").write(b"stale-shm")

    r = client.post("/api/restore", json={"filename": "good.zip"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True and body["restart_required"] is True
    assert body["safety_backup"].startswith("before-restore-")
    assert (paths.backup_dir / body["safety_backup"]).exists()
    # 旧库伴随文件必须被清掉：残留 WAL 会被回放到新库上造成损坏。
    # （此后 SQLite 可按需为恢复库自建新伴随文件，属正常现象。）
    assert not os.path.exists(wal) and not os.path.exists(shm)
    assert _db_score_rows(paths.db) == 1
    con = sqlite3.connect(paths.db)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()


def test_restore_wal_mode_backup_roundtrip(client, paths, monkeypatch):
    """WAL 库的备份恢复到另一份库：数据完整、无伴随文件残留。"""
    wal_source = paths.scratch / "wal-source.sqlite"
    con = _make_app_db(wal_source, journal_mode="WAL", marker_row=True)
    try:
        monkeypatch.setattr(br, "DB_PATH", str(wal_source))  # 备份的是 WAL 源库
        filename = br.create_backup("wal-source")
    finally:
        con.close()
        monkeypatch.setattr(br, "DB_PATH", str(paths.db))
    current = _make_app_db(paths.db)
    current.close()
    r = client.post("/api/restore", json={"filename": filename})
    assert r.status_code == 200, r.text
    # 恢复完成瞬间不得残留旧伴随文件（此后 SQLite 可按库头自建新的，属正常）。
    assert not os.path.exists(str(paths.db) + "-wal")
    assert not os.path.exists(str(paths.db) + "-shm")
    assert _db_score_rows(paths.db) == 1


# ───────────────────────── restore：备份校验拒绝路径 ─────────────────────────

def _assert_restore_rejected(client, paths, detail_part=None):
    before = _db_score_rows(paths.db)
    r = client.post("/api/restore", json={"filename": "broken.zip"})
    assert r.status_code == 400, r.text
    if detail_part:
        assert detail_part in r.json()["detail"]
    assert _db_score_rows(paths.db) == before  # 原库分毫未动


def _seed_current_db(paths):
    current = _make_app_db(paths.db, marker_row=True)
    current.close()


def test_restore_rejects_not_a_zip(client, paths):
    _seed_current_db(paths)
    (paths.backup_dir / "broken.zip").write_bytes(b"this is definitely not a zip file")
    _assert_restore_rejected(client, paths, "无法读取")


def test_restore_rejects_zip_without_db_sqlite(client, paths):
    _seed_current_db(paths)
    with zipfile.ZipFile(paths.backup_dir / "broken.zip", "w") as zf:
        zf.writestr("readme.txt", "合成占位")
    _assert_restore_rejected(client, paths)


def test_restore_rejects_non_sqlite_payload(client, paths):
    _seed_current_db(paths)
    with zipfile.ZipFile(paths.backup_dir / "broken.zip", "w") as zf:
        zf.writestr("db.sqlite", os.urandom(4096))
    _assert_restore_rejected(client, paths, "不是有效的 SQLite")


def test_restore_rejects_valid_sqlite_missing_core_tables(client, paths):
    _seed_current_db(paths)
    stray = paths.scratch / "unrelated.sqlite"
    con = sqlite3.connect(stray)
    con.execute("CREATE TABLE unrelated (id INTEGER)")
    con.commit()
    con.close()
    _zip_db(paths.backup_dir, "broken.zip", stray)
    _assert_restore_rejected(client, paths, "核心数据表")


def test_restore_corrupt_pages_fail_integrity_check(client, paths):
    """页结构被改坏的库（页头/单元格指针损坏）→ 400，原库不动。

    只改 payload 文本不会破坏 B 树结构，integrity_check 仍 ok；因此这里
    破坏第 2、3 页的页头与指针区，确保结构性损坏被校验发现。
    """
    _seed_current_db(paths)
    valid = paths.scratch / "big.sqlite"
    con = _make_app_db(valid, bulk_rows=600)
    con.close()
    data = bytearray(valid.read_bytes())
    assert len(data) > 3 * 4096
    for base in (4096, 8192):          # 第 2、3 页的页头 + 单元格指针数组
        for offset in range(base, base + 120):
            data[offset] ^= 0xFF
    corrupt = paths.scratch / "corrupt.sqlite"
    corrupt.write_bytes(bytes(data))
    _zip_db(paths.backup_dir, "broken.zip", corrupt)
    _assert_restore_rejected(client, paths, "校验未通过")


# ───────────────────────── restore：替换失败恢复 ─────────────────────────

def test_restore_failure_keeps_original_db(client, paths, monkeypatch):
    """替换阶段失败：主文件不被半写、原库完好、500 可读、暂存清理。"""
    source = _make_app_db(paths.scratch / "good2.sqlite", marker_row=True)
    source.close()
    _zip_db(paths.backup_dir, "good2.zip", paths.scratch / "good2.sqlite")
    current = _make_app_db(paths.db, bulk_rows=5)
    current.close()
    before = _db_score_rows(paths.db)

    real_replace = os.replace

    def failing_replace(src, dst, *args, **kwargs):
        if str(dst) == str(paths.db):
            raise OSError("synthetic replace failure")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", failing_replace)
    r = client.post("/api/restore", json={"filename": "good2.zip"})
    assert r.status_code == 500, r.text
    assert "恢复失败" in r.json()["detail"]
    assert _db_score_rows(paths.db) == before  # 原库未被改动、无半写
    leftovers = [p for p in os.listdir(paths.backup_dir) if p.endswith(".restore")]
    assert leftovers == []


def test_restore_missing_file_404(client, paths):
    r = client.post("/api/restore", json={"filename": "no-such-backup.zip"})
    assert r.status_code == 404
