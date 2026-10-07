"""由测试启动器停止服务后调用：生产只读快照、暂存迁移、备份及替换。"""
from datetime import datetime
from pathlib import Path
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path('/Users/xiaomeng/code/学情追踪/deploy/data/db.sqlite')


def snapshot(source: Path, target: Path) -> None:
    src = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    target.chmod(0o600)


def validate(path: Path) -> None:
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise RuntimeError('副本完整性检查失败，未替换测试数据库')
        if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise RuntimeError('副本外键检查失败，未替换测试数据库')
    finally:
        db.close()


def migrate(stage: Path) -> None:
    env = {**os.environ, 'EXAM_TRACKER_DIR': str(stage),
           'EXAM_TRACKER_BACKUP_DIR': str(stage / 'backups'),
           'PYTHONPATH': str(ROOT / 'backend')}
    subprocess.run([sys.executable, '-c',
                    'from app.db.schema import ensure_app_schema; ensure_app_schema()'],
                   cwd=ROOT / 'backend', env=env, check=True)
    # 原子替换只安装主文件，先合并 WAL 并切回单文件模式。
    db = sqlite3.connect(stage / 'db.sqlite')
    try:
        if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] != 0:
            raise RuntimeError('迁移副本仍被使用，无法合并 WAL')
        if db.execute('PRAGMA journal_mode=DELETE').fetchone()[0] != 'delete':
            raise RuntimeError('迁移副本无法转换为单文件数据库')
    finally:
        db.close()


def refresh(source: Path, data: Path, backups: Path, migrator=migrate) -> Path | None:
    target = data / 'db.sqlite'
    if source.resolve() == target.resolve():
        raise RuntimeError('源数据库与目标相同，拒绝同步')
    data.mkdir(parents=True, exist_ok=True)
    backups.mkdir(parents=True, exist_ok=True)
    data.chmod(0o700)
    backups.chmod(0o700)
    stage = Path(tempfile.mkdtemp(prefix='.refresh-', dir=data.parent))
    saved = None
    try:
        candidate = stage / 'db.sqlite'
        snapshot(source, candidate)
        validate(candidate)
        migrator(stage)
        validate(candidate)
        if target.exists():
            saved = backups / ('pre-refresh-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite')
            snapshot(target, saved)
            validate(saved)
        # 服务已停，旧副本已备份；旧 WAL/SHM 不可用于新库。
        for suffix in ('-wal', '-shm', '-journal'):
            Path(str(target) + suffix).unlink(missing_ok=True)
        os.replace(candidate, target)
        (data / 'raw').mkdir(exist_ok=True)
        (data / 'last-refresh.txt').write_text(
            datetime.now().astimezone().isoformat() + '\n', encoding='utf-8')
        return saved
    finally:
        shutil.rmtree(stage)


if __name__ == '__main__':
    os.umask(0o077)
    saved = refresh(SOURCE, ROOT / '.test-data/dev/exam-tracker', ROOT / '.test-data/dev/backups')
    print('• 已同步最新快照，并通过迁移、完整性和外键校验。')
    if saved:
        print(f'• 上次测试数据已备份：{saved}')
