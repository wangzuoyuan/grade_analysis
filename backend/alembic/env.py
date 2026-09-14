"""Alembic 环境（合并版，P1）。

- target_metadata 复用 app.db.models 的 Base.metadata；导入
  app.db.workspace_models 把 P1 新表注册进同一 MetaData。
- 数据库连接直接复用 app.db.models 的 engine（其 URL 由
  EXAM_TRACKER_DIR 决定），alembic.ini 中不写死 sqlalchemy.url。
- import app.db.models 已无任何建表副作用（R8）：旧 H 表由应用启动时的
  显式初始化（app.db.schema.ensure_app_schema，main.py lifespan 调用）
  或部署迁移负责建出。本迁移链只管辖工作台新表
  （见 versions/0001_workspace_tables.py），旧启动迁移不再随 import 执行
  （app.main.run_legacy_startup_migrations 仅隔离源副本适用）。
"""

from logging.config import fileConfig

from alembic import context

from app.db import workspace_models  # noqa: F401  注册新表到 Base.metadata
from app.db.models import Base, engine

# 迁移进程内真实开启 SQLite 外键（升降级的 DROP 顺序依赖它；
# 这是合并版 P1 的显式开启点之一，见 workspace_models 注释）
workspace_models.enable_sqlite_foreign_keys(engine)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL。URL 取自应用 engine，保证与在线同库。"""
    context.configure(
        url=str(engine.url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：复用应用 engine（EXAM_TRACKER_DIR 决定库文件路径）。"""
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
