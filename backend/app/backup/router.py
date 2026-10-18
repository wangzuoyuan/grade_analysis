"""数据备份 / 恢复（/api/backup …）。

备份目录放在 DATA_DIR 之外（~/.exam-tracker-backups），避免被 `run.py init`
的清空操作一并删除。备份内容：db.sqlite + homework_exports/。

db.sqlite 一律经 SQLite 备份接口生成一致性快照（而非直接打包磁盘文件）：
无论库处于 DELETE 还是 WAL 日志模式，快照都包含全部已提交事务（WAL 中
尚未合并回主文件的帧也会被合并进快照），且产物是干净的单文件数据库。
"""

import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

router = APIRouter(tags=["backup"])

from app.paths import DATA_DIR, BACKUP_DIR

DB_PATH = os.path.join(DATA_DIR, "db.sqlite")
EXPORT_DIR = os.path.join(DATA_DIR, "homework_exports")

# 备份校验用的标志表：legacy 根表 + workspace 核心事实表。
# 应用生成的任何备份都应同时具备；缺失说明文件不是本应用的有效库
# （老 schema 备份也会因缺 workspace 表被拒——这类库恢复后应用启动时
# 会被 ensure_app_schema 以「演进状态不明」拒绝，提前在恢复口拦下更好）。
CORE_TABLES = ("teacher", "score_fact")


def _ensure_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def _snapshot_database(dest_path: str) -> None:
    """用 SQLite 备份接口把 DB_PATH 的一致性快照写入 dest_path。

    优先只读（mode=ro）打开源库；若只读通道读不了（如 WAL 库缺 -shm
    的边角场景），退回普通连接并置 query_only 防写。备份接口对源库只
    读页数据，不会把源库写大。
    """
    try:
        src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    except sqlite3.Error:
        src = None
    if src is not None:
        try:
            dst = sqlite3.connect(dest_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
            return
        except sqlite3.Error:
            pass  # 只读通道失败：退回普通连接重试
        finally:
            src.close()
    fallback = sqlite3.connect(DB_PATH)
    try:
        fallback.execute("PRAGMA query_only=ON")
        dst = sqlite3.connect(dest_path)
        try:
            fallback.backup(dst)
        finally:
            dst.close()
    finally:
        fallback.close()


def create_backup(prefix: str = "backup") -> str:
    """打包当前数据库 + 作业导出到带时间戳 zip，返回文件名。"""
    _ensure_dir()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{prefix}-{stamp}.zip"
    path = os.path.join(BACKUP_DIR, filename)
    snapshot = None
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            if os.path.exists(DB_PATH):
                # 一致性快照：DELETE/WAL 两种日志模式下都拿到完整已提交状态。
                fd, snapshot = tempfile.mkstemp(suffix=".snapshot", dir=BACKUP_DIR)
                os.close(fd)
                _snapshot_database(snapshot)
                zf.write(snapshot, "db.sqlite")
                os.unlink(snapshot)
                snapshot = None
            if os.path.isdir(EXPORT_DIR):
                for root, _, files in os.walk(EXPORT_DIR):
                    for f in files:
                        full = os.path.join(root, f)
                        arc = os.path.join("homework_exports", os.path.relpath(full, EXPORT_DIR))
                        zf.write(full, arc)
    except Exception:
        if snapshot and os.path.exists(snapshot):
            os.unlink(snapshot)
        if os.path.exists(path):
            os.unlink(path)
        raise
    return filename


@router.post("/backup")
async def backup():
    filename = create_backup()
    size = os.path.getsize(os.path.join(BACKUP_DIR, filename))
    return {"success": True, "filename": filename, "size": size}


@router.get("/backups")
async def list_backups():
    _ensure_dir()
    rows = []
    for name in os.listdir(BACKUP_DIR):
        if not name.endswith(".zip"):
            continue
        full = os.path.join(BACKUP_DIR, name)
        st = os.stat(full)
        rows.append({
            "filename": name,
            "size": st.st_size,
            "created": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        })
    rows.sort(key=lambda r: r["filename"], reverse=True)
    return rows


@router.get("/backup/{filename}/download")
async def download_backup(filename: str):
    path = os.path.join(BACKUP_DIR, os.path.basename(filename))
    if not os.path.exists(path):
        raise HTTPException(404, "备份不存在")
    return FileResponse(path, filename=filename, media_type="application/zip")


class RestorePayload(BaseModel):
    filename: str


def _stage_db_from_zip(zip_path: str) -> str:
    """把备份 zip 内的 db.sqlite 解包到临时文件（BACKUP_DIR 同盘），返回路径。"""
    _ensure_dir()
    fd, staged = tempfile.mkstemp(suffix=".restore", dir=BACKUP_DIR)
    os.close(fd)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            if "db.sqlite" not in zf.namelist():
                raise KeyError("db.sqlite")
            with zf.open("db.sqlite") as src, open(staged, "wb") as dst:
                shutil.copyfileobj(src, dst)
    except Exception:
        if os.path.exists(staged):
            os.unlink(staged)
        raise
    return staged


def _validate_staged_db(staged: str) -> None:
    """恢复前校验备份库：integrity_check 通过 + 标志表齐全，否则抛可读错误。"""
    con = sqlite3.connect(staged)
    try:
        row = con.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            detail = row[0] if row else "无结果"
            raise HTTPException(400, f"备份校验未通过：完整性检查失败（{detail}），已保持当前数据库不变")
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = [t for t in CORE_TABLES if t not in tables]
        if missing:
            raise HTTPException(
                400,
                "备份校验未通过：缺少核心数据表（" + "、".join(missing)
                + "），不是本应用的有效备份，已保持当前数据库不变",
            )
    except sqlite3.DatabaseError as exc:
        raise HTTPException(400, f"备份校验未通过：db.sqlite 不是有效的 SQLite 数据库（{exc}），已保持当前数据库不变")
    finally:
        con.close()


@router.post("/restore")
async def restore(payload: RestorePayload):
    path = os.path.join(BACKUP_DIR, os.path.basename(payload.filename))
    if not os.path.exists(path):
        raise HTTPException(404, "备份不存在")

    # 1) 备份校验：先解包并验证，损坏/缺表一律 400，绝不触碰现有库。
    try:
        staged = _stage_db_from_zip(path)
    except (zipfile.BadZipFile, KeyError, OSError):
        raise HTTPException(400, "备份文件无法读取（损坏或缺少 db.sqlite），已保持当前数据库不变")
    try:
        _validate_staged_db(staged)
    except Exception:
        os.unlink(staged)
        raise

    # 2) 安全备份：恢复前自动备份当前库，误操作可回退。
    safety = create_backup(prefix="before-restore")

    # 3) 受控替换：释放连接 → 清掉旧库伴随的 -wal/-shm（属旧库持久状态，
    #    残留会被回放到新库上造成损坏）→ 临时文件 os.replace 原子覆盖。
    try:
        try:
            from app.db.models import engine
            engine.dispose()
        except Exception:
            pass
        for sidecar in (DB_PATH + "-wal", DB_PATH + "-shm"):
            try:
                os.remove(sidecar)
            except FileNotFoundError:
                pass
        os.replace(staged, DB_PATH)
    except Exception as exc:
        # 4) 失败恢复：os.replace 原子性保证主文件不会被半写——失败时
        #    当前库仍是原文件；清理暂存文件后返回可读 500。
        if os.path.exists(staged):
            os.unlink(staged)
        raise HTTPException(500, f"恢复失败（{exc}）：当前数据库未被改动，可重试或先下载安全备份 {safety}")

    return {
        "success": True,
        "restored_from": payload.filename,
        "safety_backup": safety,
        "restart_required": True,
        "note": "已恢复数据库。建议执行 run.py stop && start 重启以确保所有连接刷新。",
    }
