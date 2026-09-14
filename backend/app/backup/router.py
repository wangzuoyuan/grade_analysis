"""数据备份 / 恢复（/api/backup …）。

备份目录放在 DATA_DIR 之外（~/.exam-tracker-backups），避免被 `run.py init`
的清空操作一并删除。备份内容：db.sqlite + homework_exports/。
"""

import os
import shutil
import zipfile
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

router = APIRouter(tags=["backup"])

from app.paths import DATA_DIR, BACKUP_DIR

DB_PATH = os.path.join(DATA_DIR, "db.sqlite")
EXPORT_DIR = os.path.join(DATA_DIR, "homework_exports")


def _ensure_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def create_backup(prefix: str = "backup") -> str:
    """打包当前数据库 + 作业导出到带时间戳 zip，返回文件名。"""
    _ensure_dir()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{prefix}-{stamp}.zip"
    path = os.path.join(BACKUP_DIR, filename)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(DB_PATH):
            zf.write(DB_PATH, "db.sqlite")
        if os.path.isdir(EXPORT_DIR):
            for root, _, files in os.walk(EXPORT_DIR):
                for f in files:
                    full = os.path.join(root, f)
                    arc = os.path.join("homework_exports", os.path.relpath(full, EXPORT_DIR))
                    zf.write(full, arc)
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


@router.post("/restore")
async def restore(payload: RestorePayload):
    path = os.path.join(BACKUP_DIR, os.path.basename(payload.filename))
    if not os.path.exists(path):
        raise HTTPException(404, "备份不存在")
    with zipfile.ZipFile(path) as zf:
        if "db.sqlite" not in zf.namelist():
            raise HTTPException(400, "备份内缺少 db.sqlite")

    # 恢复前先自动备份当前库，避免误操作不可逆
    safety = create_backup(prefix="before-restore")

    # 释放数据库连接后再覆盖文件（SQLite 文件级替换）
    try:
        from app.db.models import engine
        engine.dispose()
    except Exception:
        pass

    with zipfile.ZipFile(path) as zf:
        with zf.open("db.sqlite") as src, open(DB_PATH, "wb") as dst:
            shutil.copyfileobj(src, dst)

    return {
        "success": True,
        "restored_from": payload.filename,
        "safety_backup": safety,
        "restart_required": True,
        "note": "已恢复数据库。建议执行 run.py stop && start 重启以确保所有连接刷新。",
    }
