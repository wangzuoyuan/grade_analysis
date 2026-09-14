"""统一的数据目录配置。

本地（Mac，`run.py`）默认落在 `~/.exam-tracker`；Docker 部署时通过环境变量
`EXAM_TRACKER_DIR` 指向挂载卷（如 `/data`），让 SQLite 与导出文件持久化到 NAS
共享文件夹。备份目录默认在数据目录之外（`~/.exam-tracker-backups`），Docker 里
用 `EXAM_TRACKER_BACKUP_DIR` 覆盖（如 `/data/backups`）。
"""

import os

DATA_DIR = os.environ.get(
    "EXAM_TRACKER_DIR", os.path.expanduser("~/.exam-tracker")
)
BACKUP_DIR = os.environ.get(
    "EXAM_TRACKER_BACKUP_DIR", os.path.expanduser("~/.exam-tracker-backups")
)
