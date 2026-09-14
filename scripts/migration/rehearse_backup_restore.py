"""D01 合成回退演练：一致性备份 → 完整恢复 → 查询核对 → 增量清单 → 只读回退。

Q06 修订（ 三轮审核）：
- 备份用 SQLite backup API（``Connection.backup``）产出一致性快照——已提交
  但仍在 WAL 的数据必然包含（旧实现直接 zip 主文件，WAL 已提交数据丢失；
   反例：备份恢复后 1→0）。快照时刻取复制完成之后，作为增量边界的
  时间上界；边界竞态仅在"边备份边写入"时存在，现场回退前必须停写。
- 资产全集：db.sqlite + 数据目录内全部普通文件（raw/ 上传原件、
  homework_exports/ 导出、backups/ 应用内备份等，存在即纳入）+ manifest
  （逐文件 sha256）；恢复按清单逐项双重核对摘要（zip 流 + 落盘回读）。
- 增量以备份点时间戳为界（manifest.backup_taken_at，UTC、与 SQLite
  DateTime 存储同构的空格格式），对全部含 created_at 的业务表导出
  created_at/updated_at 越界行（新增+修改），并以主键清单列出"备份点有、
  当前无"的删除行——不依赖演示专用 source 字符串。迁移台账
  （migration_run/source_map/import_batch）是工具簿记，不属业务事实，排除。

范围声明：对**合成目标库**执行，绝不触碰 ~/.exam-tracker；本脚本不 import
应用代码，全程 sqlite3 原生访问 + 文件复制 + hashlib 摘要。

输出：<root>/reports/backup_restore.json、<root>/reports/incremental.json、
备份 zip 存 <root>/backups/。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_ROOT = os.path.join(REPO_ROOT, ".test-data", "migration-rehearsal")

DB_BASENAME = "db.sqlite"
# 库侧伴生文件绝不随备份/恢复走：主库由 backup API 一致性复制，旧 WAL/SHM/
# journal 配替换后的新库文件必然损坏（回退工具的经典翻车点）
_DB_SIDECARS = (DB_BASENAME + "-wal", DB_BASENAME + "-shm", DB_BASENAME + "-journal")
# 增量清单排除：alembic 版本账 + 迁移工具自身台账（非业务事实）
_INCREMENTAL_EXCLUDED = {"alembic_version", "migration_run", "source_map", "import_batch"}

# 抽样核对查询：覆盖学年/身份/关联/成绩事实等 ws 核心表
SPOT_QUERIES = {
    "academic_year": "SELECT COUNT(*) FROM academic_year",
    "term": "SELECT COUNT(*) FROM term",
    "ws_student_identity_homeroom": "SELECT COUNT(*) FROM ws_student_identity WHERE data_domain='homeroom'",
    "ws_student_identity_teaching": "SELECT COUNT(*) FROM ws_student_identity WHERE data_domain='teaching'",
    "administrative_class": "SELECT COUNT(*) FROM administrative_class",
    "teaching_class": "SELECT COUNT(*) FROM teaching_class",
    "enrollment": "SELECT COUNT(*) FROM enrollment",
    "score_fact_homeroom": "SELECT COUNT(*) FROM score_fact WHERE data_domain='homeroom'",
    "score_fact_teaching": "SELECT COUNT(*) FROM score_fact WHERE data_domain='teaching'",
    "homeroom_teaching_link": "SELECT COUNT(*) FROM homeroom_teaching_link WHERE status='active'",
    "linked_student": "SELECT COUNT(*) FROM linked_student",
    "source_map": "SELECT COUNT(*) FROM source_map",
    "migration_run": "SELECT COUNT(*) FROM migration_run",
}


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _utc_stamp() -> str:
    """UTC 时间戳，空格分隔 + 微秒——与 SQLite DateTime 列的存储格式同构，
    可直接参与 SQL 字符串比较（增量边界 `created_at > ?`）。"""
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")


def _sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _create_consistent_snapshot(db_path: str, snapshot_path: str) -> None:
    """SQLite backup API 复制一致性快照。

    源连接用默认（可写）打开而非 mode=ro：活跃 -wal/-shm 场景下只读连接
    需要额外目录写权限且对锁时序更敏感；backup API 的源连接只读页数据、
    不改业务内容。已提交但仍在 WAL 的数据经该 API 必然进入快照。
    """
    src = sqlite3.connect(db_path, timeout=30)
    try:
        dst = sqlite3.connect(snapshot_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _data_assets(data_dir: str) -> list:
    """数据目录内全部普通文件（相对 arcname, 绝对路径），排除库伴生文件。
    raw/、homework_exports/、backups/ 等目录资产"存在即纳入"，与
    app/backup/router.py 的文件级备份口径一致且更完整。"""
    assets = []
    for root, _dirs, files in os.walk(data_dir):
        for name in sorted(files):
            if name == DB_BASENAME or name in _DB_SIDECARS:
                continue
            full = os.path.join(root, name)
            assets.append((os.path.relpath(full, data_dir), full))
    return assets


def read_manifest(zip_path: str) -> dict:
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open("manifest.json") as f:
            return json.load(f)


def create_backup(db_path: str, backup_dir: str, prefix: str) -> str:
    """一致性备份：backup API 快照 + 数据目录资产 + manifest（逐文件
    sha256）打包 zip，返回 zip 路径。"""
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(backup_dir, f"{prefix}-{stamp}.zip")
    # taken_at 取快照复制完成之后：它是快照内容的时间上界，任何进快照的
    # 行 created_at/updated_at 都不晚于它（增量边界因此不重不漏）
    staging_fd, snapshot = tempfile.mkstemp(prefix="rehearsal-snap-", suffix=".sqlite")
    os.close(staging_fd)
    try:
        _create_consistent_snapshot(db_path, snapshot)
        taken_at = _utc_stamp()
        entries = [(DB_BASENAME, snapshot)]
        entries.extend(_data_assets(os.path.dirname(db_path)))
        manifest = {
            "tool": "rehearse_backup_restore",
            "backup_taken_at": taken_at,
            "taken_at_note": "UTC，SQLite DateTime 同构格式；停写前提下是快照内容的时间上界",
            "source_db": os.path.abspath(db_path),
            "files": [
                {"arcname": arc, "sha256": _sha256_file(full), "size": os.path.getsize(full)}
                for arc, full in entries
            ],
        }
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for arc, full in entries:
                zf.write(full, arc)
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    finally:
        os.unlink(snapshot)
    return path


def restore_to(zip_path: str, dest_dir: str) -> str:
    """按 manifest 恢复全部资产到 dest_dir，逐文件双重核对 sha256
    （zip 解流时一次 + 落盘回读一次），返回恢复后的 db 路径。

    恢复前清除 dest 内旧库伴生文件：被替换的主库配旧 WAL/SHM 会损坏。"""
    os.makedirs(dest_dir, exist_ok=True)
    manifest = read_manifest(zip_path)
    listed = {entry["arcname"]: entry["sha256"] for entry in manifest["files"]}
    # 防路径穿越：arcname 规范化后必须仍落在 dest 内
    for arc in listed:
        target = os.path.abspath(os.path.join(dest_dir, arc))
        if not target.startswith(os.path.abspath(dest_dir) + os.sep):
            raise RuntimeError(f"manifest 内出现越界路径：{arc}")

    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.filename == "manifest.json":
                continue
            if info.filename not in listed:
                raise RuntimeError(f"zip 内有清单外文件：{info.filename}")
            dest = os.path.join(dest_dir, info.filename)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            h = hashlib.sha256()
            with zf.open(info) as src, open(dest, "wb") as dst:
                while True:
                    block = src.read(1 << 20)
                    if not block:
                        break
                    h.update(block)
                    dst.write(block)
            if h.hexdigest() != listed[info.filename]:
                raise RuntimeError(f"恢复校验失败（zip 流）：{info.filename}")

    # 双重核对：落盘文件回读摘要必须与清单一致
    for arc, expected in listed.items():
        actual = _sha256_file(os.path.join(dest_dir, arc))
        if actual != expected:
            raise RuntimeError(f"恢复校验失败（落盘回读）：{arc}")

    for sidecar in _DB_SIDECARS:
        stale = os.path.join(dest_dir, sidecar)
        if os.path.exists(stale):
            os.unlink(stale)
    return os.path.join(dest_dir, DB_BASENAME)


def _table_shape(conn: sqlite3.Connection, table: str) -> tuple:
    """(有 created_at?, updated_at 列名或 None, 单列整型主键列名或 None)。"""
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    names = [c["name"] for c in cols]
    created = "created_at" in names
    updated = "updated_at" if "updated_at" in names else None
    pk_cols = [c["name"] for c in cols if c["pk"]]
    pk = pk_cols[0] if len(pk_cols) == 1 and pk_cols[0] == "id" else None
    return created, updated, pk


def _business_tables(conn: sqlite3.Connection) -> list:
    """全部含 created_at 的业务表（排除系统表与迁移台账）。"""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    out = []
    for (name,) in rows:
        if name in _INCREMENTAL_EXCLUDED:
            continue
        created, _updated, _pk = _table_shape(conn, name)
        if created:
            out.append(name)
    return sorted(out)


def export_incremental(db_path: str, backup_zip: str) -> dict:
    """导出「备份点之后」的业务差异清单（Q06：时间戳边界，不依赖 source）。

    - upserts：created_at > 边界（新增）或 updated_at > 边界（修改）的整行；
    - deletes：备份点快照有、当前库没有的主键清单。

    边界取自备份 zip 的 manifest.backup_taken_at，绝不由调用方口头传入。"""
    manifest = read_manifest(backup_zip)
    boundary = manifest["backup_taken_at"]

    snapshot_fd, snapshot = tempfile.mkstemp(prefix="rehearsal-base-", suffix=".sqlite")
    os.close(snapshot_fd)
    os.unlink(snapshot)  # restore 需要目标路径不存在
    try:
        with zipfile.ZipFile(backup_zip) as zf:
            with zf.open(DB_BASENAME) as src, open(snapshot, "wb") as dst:
                shutil.copyfileobj(src, dst)

        conn = _connect(db_path)
        try:
            tables = _business_tables(conn)
            upserts, deletes, counts = {}, {}, {}
            for table in tables:
                created, updated, pk = _table_shape(conn, table)
                where = "created_at > ?"
                params = [boundary]
                if updated:
                    where += f" OR {updated} > ?"
                    params.append(boundary)
                rows = [
                    dict(r)
                    for r in conn.execute(
                        f"SELECT * FROM {table} WHERE {where}", params
                    )
                ]
                removed = []
                if pk:
                    snap = _connect(snapshot)
                    try:
                        backup_ids = {
                            r[0] for r in snap.execute(f"SELECT {pk} FROM {table}")
                        }
                    finally:
                        snap.close()
                    current_ids = {r[0] for r in conn.execute(f"SELECT {pk} FROM {table}")}
                    removed = sorted(backup_ids - current_ids)
                if rows:
                    upserts[table] = rows
                if removed:
                    deletes[table] = removed
                if rows or removed:
                    counts[table] = {"upserts": len(rows), "deletes": len(removed)}
            total_up = sum(c["upserts"] for c in counts.values())
            total_del = sum(c["deletes"] for c in counts.values())
            return {
                "captured_at": _utc_stamp(),
                "boundary": boundary,
                "boundary_note": "备份点（UTC，SQLite DateTime 同构格式），取自备份 manifest",
                "tables_scanned": tables,
                "upserts": upserts,
                "deletes": deletes,
                "counts": counts,
                "totals": {"upserts": total_up, "deletes": total_del,
                           "tables_changed": len(counts)},
                "note": (
                    "备份点之后新增/修改/删除的业务差异；只读回退后这些行不在"
                    "回退库中——增量待回放，不宣称无损"
                ),
            }
        finally:
            conn.close()
    finally:
        os.unlink(snapshot)


def spot_counts(db_path: str) -> dict:
    conn = _connect(db_path)
    try:
        out = {}
        for name, sql in SPOT_QUERIES.items():
            out[name] = conn.execute(sql).fetchone()[0]
        # link 抽样：关联对与共享科目可解析
        out["link_sample"] = [
            dict(r)
            for r in conn.execute(
                "SELECT htl.subject, ac.grade, ac.class_num, tc.label "
                "FROM homeroom_teaching_link htl "
                "JOIN administrative_class ac ON ac.id = htl.admin_class_id "
                "JOIN teaching_class tc ON tc.id = htl.teaching_class_id "
                "WHERE htl.status='active'"
            )
        ]
        return out
    finally:
        conn.close()


def _ensure_demo_assets(data_dir: str) -> list:
    """确保数据目录内有可核对的上传原件/导出资产（存在则不动），
    返回本次保障存在的资产相对路径。"""
    demo = [
        ("raw", "rehearsal-upload.txt", "演练上传原件（合成）\n"),
        ("homework_exports", "rehearsal-export.csv", "科目,分数\n物理,90\n"),
    ]
    made = []
    for sub, name, content in demo:
        sub_dir = os.path.join(data_dir, sub)
        os.makedirs(sub_dir, exist_ok=True)
        target = os.path.join(sub_dir, name)
        if not os.path.exists(target):
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            made.append(os.path.join(sub, name))
    return made


def main() -> int:
    parser = argparse.ArgumentParser(description="D01 合成回退演练（备份/恢复/增量）")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="演练根目录")
    args = parser.parse_args()

    root = os.path.realpath(os.path.abspath(args.root))
    if ".exam-tracker" in root or ".sources" in root.split(os.sep):
        print("[红线] 拒绝的演练根目录", file=sys.stderr)
        return 2
    data_dir = os.environ.get("EXAM_TRACKER_DIR") or os.path.join(root, "target", "data")
    data_dir = os.path.realpath(os.path.abspath(data_dir))
    # X01（ 终审）：realpath/commonpath 边界校验——数据/恢复目标必须落在
    # 演练根之内才允许建目录、造文件或连接数据库；符号链接逃逸经 realpath
    # 解析后同样落在根外而被拒绝（与 run_rehearsal/build_synthetic_sources
    # 三脚本同等根约束，契约 p7 v2 §4.1）。
    try:
        if os.path.commonpath([root, data_dir]) != root:
            raise ValueError("outside")
    except ValueError:
        print(
            f"[红线] 数据目录在演练根之外，拒绝执行：data={data_dir} root={root}",
            file=sys.stderr,
        )
        return 2
    db_path = os.path.join(data_dir, DB_BASENAME)
    if not os.path.exists(db_path):
        print(f"目标库不存在：{db_path}（先跑 run_rehearsal.py）", file=sys.stderr)
        return 2
    backup_dir = os.path.join(root, "backups")
    reports_dir = os.path.join(root, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    result = {"db_path": db_path}
    demo_assets = _ensure_demo_assets(data_dir)

    # ── 场景 A：一致性备份 → 恢复全部资产 → 抽样核对 + 摘要核对 ──
    backup_a = create_backup(db_path, backup_dir, "rehearsal")
    manifest = read_manifest(backup_a)
    result["backup"] = os.path.relpath(backup_a, root)
    result["manifest_summary"] = {
        "backup_taken_at": manifest["backup_taken_at"],
        "files": len(manifest["files"]),
        "asset_files": len(manifest["files"]) - 1,
        "demo_assets_created": demo_assets,
    }
    restored_dir = os.path.join(root, "restored", "data")
    if os.path.exists(restored_dir):
        shutil.rmtree(restored_dir)
    restored_db = restore_to(backup_a, restored_dir)
    before_counts, after_counts = spot_counts(db_path), spot_counts(restored_db)
    mismatch = {k: (before_counts[k], after_counts[k])
                for k in SPOT_QUERIES if before_counts[k] != after_counts[k]}
    result["restore_check"] = {
        "restored_path": os.path.relpath(restored_db, root),
        "spot_queries": len(SPOT_QUERIES),
        "mismatch": mismatch,
        "link_sample": after_counts["link_sample"],
        "assets_restored": len(manifest["files"]) - 1,
        "assets_sha256_verified": len(manifest["files"]),
        "ok": not mismatch,
    }
    if mismatch:
        result["restore_check"]["note"] = "恢复后抽样计数不一致——回退演练失败"
    else:
        result["restore_check"]["note"] = (
            "恢复后 ws 表与 link 抽样计数逐项一致；资产按 manifest 逐文件 sha256 双重核对通过"
        )

    # ── 场景 B：模拟上线后已有新写入 → 导出增量 → 只读回退旧库 ──
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # 场景 B 内按列名取值
    conn.execute("PRAGMA foreign_keys=ON")
    # WAL 演练（Q06  反例场景）：切换 WAL 后写入并保持连接打开——
    # 已提交数据留在 -wal，不落主文件；备份必须经 backup API 捕获
    conn.execute("PRAGMA journal_mode=WAL")
    ay_id = conn.execute("SELECT id FROM academic_year ORDER BY id LIMIT 1").fetchone()[0]
    person = conn.execute(
        "SELECT id FROM ws_student_identity WHERE data_domain='teaching' ORDER BY id LIMIT 1"
    ).fetchone()[0]
    tclass = conn.execute("SELECT id FROM teaching_class ORDER BY id LIMIT 1").fetchone()[0]
    now = _utc_stamp()
    cur = conn.execute(
        "INSERT INTO score_fact (data_domain, academic_year_id, exam_name, exam_date, "
        "class_ref_id, identity_id, subject, subject_key, total_key, score, source, "
        "data_revision, created_at, updated_at) VALUES ('teaching', ?, '2026开学摸底', "
        "'2026-09-01', ?, ?, '物理', '物理', '', 79.0, 'post-cutover-write', 1, ?, ?)",
        (ay_id, tclass, person, now, now),
    )
    inserted_id = cur.lastrowid
    # 旧行修改（成绩订正）：raw SQL 不触发 ORM onupdate，显式 bump updated_at
    old_row = conn.execute(
        "SELECT id, score FROM score_fact WHERE source LIKE 'migration:%' "
        "ORDER BY id LIMIT 1"
    ).fetchone()
    updated_id, old_score = old_row["id"], old_row["score"]
    new_score = float(old_score) + 1 if old_score is not None else 59.0
    conn.execute(
        "UPDATE score_fact SET score=?, updated_at=? WHERE id=?", (new_score, now, updated_id)
    )
    # 作业编辑：改一条作业提交状态与评价（updated_at 越界 → 进增量清单）
    hw_row = conn.execute("SELECT id FROM homework_submission ORDER BY id LIMIT 1").fetchone()
    hw_id = hw_row["id"] if hw_row else None
    if hw_id is not None:
        conn.execute(
            "UPDATE homework_submission SET evaluation='回退演练编辑', updated_at=? WHERE id=?",
            (now, hw_id),
        )
    # 删除一条备份点已有的行（回退后必须恢复）
    del_row = conn.execute(
        "SELECT id, score FROM score_fact WHERE source LIKE 'migration:%' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    deleted_id, deleted_score = del_row["id"], del_row["score"]
    conn.execute("DELETE FROM score_fact WHERE id=?", (deleted_id,))
    conn.commit()

    # 备份点 A 之后的新写入已提交、连接保持打开（数据仍在 -wal）：
    # backup API 一致性快照必须包含它们（ 反例 1→0 在此变 1→1）
    backup_b = create_backup(db_path, backup_dir, "post-write")
    wal_check_dir = os.path.join(root, "restored", "wal-check", "data")
    if os.path.exists(wal_check_dir):
        shutil.rmtree(wal_check_dir)
    wal_db = restore_to(backup_b, wal_check_dir)
    wal_conn = _connect(wal_db)
    try:
        wal_has_new_row = wal_conn.execute(
            "SELECT COUNT(*) FROM score_fact WHERE id=?", (inserted_id,)
        ).fetchone()[0] == 1
    finally:
        wal_conn.close()
    result["rollback"] = {"wal_committed_data_in_backup": wal_has_new_row}

    incremental = export_incremental(db_path, backup_a)
    with open(os.path.join(reports_dir, "incremental.json"), "w", encoding="utf-8") as f:
        json.dump(incremental, f, ensure_ascii=False, indent=2, default=str)

    result["rollback"].update({
        "restored_from": os.path.relpath(backup_a, root),
        "post_write_backup": os.path.relpath(backup_b, root),
        "incremental_report": "reports/incremental.json",
        "incremental_pending_replay": incremental["totals"],
        "post_write_changes": {
            "inserted_score_fact_id": inserted_id,
            "updated_score_fact_id": updated_id,
            "updated_homework_submission_id": hw_id,
            "deleted_score_fact_id": deleted_id,
        },
    })

    conn.close()
    # 只读回退：用「切换时备份」覆盖目标库（restore_to 清理 WAL 伴生文件）
    restore_to(backup_a, data_dir)
    after_rollback = spot_counts(db_path)
    rollback_ok = all(after_rollback[k] == before_counts[k] for k in SPOT_QUERIES)
    # 逐值核对：新写入行消失、被改行复原、被删行回归
    conn = _connect(db_path)
    try:
        post_cutover_gone = conn.execute(
            "SELECT COUNT(*) FROM score_fact WHERE source='post-cutover-write'"
        ).fetchone()[0] == 0
        restored_old = conn.execute(
            "SELECT score FROM score_fact WHERE id=?", (updated_id,)
        ).fetchone()[0] == old_score
        restored_deleted = conn.execute(
            "SELECT score FROM score_fact WHERE id=?", (deleted_id,)
        ).fetchone()[0] == deleted_score
    finally:
        conn.close()
    rollback_ok = rollback_ok and post_cutover_gone and restored_old and restored_deleted
    result["rollback"].update({
        "target_matches_backup_point": rollback_ok,
        "post_cutover_rows_gone": post_cutover_gone,
        "edited_row_value_restored": restored_old,
        "deleted_row_restored": restored_deleted,
        "declaration": "增量待回放，不宣称无损（docs/planning/04-migration.md 回退原则）",
    })

    with open(os.path.join(reports_dir, "backup_restore.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
