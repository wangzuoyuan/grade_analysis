"""应用显式 schema 初始化（幂等、版本感知）。

R8 之后 ``import app.db.models`` 零 DDL 副作用，本库建表职责统一收敛到这里：
只在应用启动（main.py lifespan）或部署迁移时调用，不在任何 import 期执行。

F01 约束：必须先读 alembic 版本再动手——
- 已有版本库一律由迁移链 ``upgrade head`` 演进，禁止先 create_all：
  旧版本库会被最新 metadata 提前建出高版本表，随后 upgrade 重复建表即崩，
  应用无法启动（空库路径掩盖了此缺陷）；
- 只有无版本表的全新库才允许 create_all + stamp；
- 已在 head 时不做任何 DDL，避免 create_all 与迁移链漂移。

Q07 约束：head 判定先比对内置常量 ALEMBIC_HEAD（零文件系统访问）——
已在 head 的库在缺少 alembic/ 迁移目录的最小部署布局下也必须能启动；
只有需要演进（落后/无版本/空库）时才读取迁移目录。
"""

import os

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

# backend/ 绝对路径：alembic.ini 里的 script_location 是相对 cwd 的，
# 程序化调用必须显式改为绝对路径，应用从任意工作目录启动都能解析。
_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_ALEMBIC_INI = os.path.join(_BACKEND_DIR, "alembic.ini")

# head 内置常量（Q07）：仅用于"当前版本 == head"时的零文件系统访问短路——
# 部署镜像可能不携带 alembic/ 迁移目录（最小布局），已 head 的库启动绝不能
# 因此崩溃。漂移约束：只能落后不能超前——超前会把落后库误判成 head 而跳过
# 升级，绝不允许；落后时已 head 库退化为读迁移目录（有目录的环境行为仍
# 正确，无目录环境会响亮失败而非静默错升级）。因此新增迁移必须同步更新
# 本常量，由 tests/v1/test_p7_deploy.py 的常量一致性回归兜底。
ALEMBIC_HEAD = "0017"

# head 形态抽查表（分属 0003/0005 两个建表迁移）：仅用于判定"库里有业务表
# 却没有 alembic_version 表"的历史遗留库是否恰好处于 head 形态——缺任何一张
# 都说明演进状态不明，不得自动建表或 stamp，必须人工核对。
_HEAD_SHAPE_TABLES = (
    "homework_assignment",
    "homework_submission",
    "ws_student_note",
    "ws_homework_semester",
)


def _alembic_config() -> Config:
    cfg = Config(_ALEMBIC_INI)
    cfg.set_main_option("script_location", os.path.join(_BACKEND_DIR, "alembic"))
    return cfg


def _current_revision(engine):
    # 复用应用 engine：env.py 同样以它为连接来源，EXAM_TRACKER_DIR 是唯一事实。
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def _existing_tables(engine) -> set:
    from sqlalchemy import inspect

    return set(inspect(engine).get_table_names())


def ensure_app_schema() -> None:
    """应用显式 schema 初始化（幂等）：import 期零 DDL，仅启动时调用。"""
    # 延迟导入：保证本模块自身 import 也无副作用，且避免模块加载顺序耦合。
    from app.db import workspace_models  # noqa: F401  注册 ws 新表到同一 MetaData
    from app.db.models import Base, engine

    # 先读版本再动手（F01）：任何 create_all 都不得发生在版本判定之前。
    # 版本读取只依赖库本身（alembic_version 表），不依赖迁移目录——这是
    # 无迁移目录环境能安全启动的前提（Q07）。
    current = _current_revision(engine)

    if current == ALEMBIC_HEAD:
        # 已在 head：什么都不做，连 ScriptDirectory 都不读——已 head 库
        # 在没有 alembic/ 目录的最小布局（如精简镜像）下也必须能启动。
        return

    # 走到这才需要迁移目录：落后/无版本/空库的演进都由迁移链负责。
    cfg = _alembic_config()
    head = ScriptDirectory.from_config(cfg).get_current_head()

    if current == head:
        # 常量漂移防御（ALEMBIC_HEAD 落后于真实 head 时可到达）：
        # 库实际已在新 head，仍不做任何 DDL。
        return

    if current is None:
        # 无 alembic_version：区分全新库与历史遗留库。
        existing = _existing_tables(engine)
        if existing:
            missing = sorted(set(_HEAD_SHAPE_TABLES) - existing)
            if missing:
                # 历史遗留库且缺 head 关键表：演进状态不明，绝不能静默
                # stamp 成 head（会把账面标成 head 而结构实际缺表）。
                raise RuntimeError(
                    "数据库中已有业务表但没有 alembic 版本记录，且缺少 head 关键表 "
                    f"{missing}：演进状态不明，禁止自动建表或 stamp head。"
                    "请先按部署文档执行迁移，或人工核对库状态后，再启动应用。"
                )
        # 全新库（或关键表齐全、与 head 形态一致的库）：一次建齐后对齐版本账。
        # create_all 只补缺失的表、不改既有表结构；外键已在 engine 的
        # checkout 监听中真实开启，建表顺序由 MetaData 按依赖排序保证。
        Base.metadata.create_all(bind=engine)
        command.stamp(cfg, head)
        return

    # 有版本且落后 head：演进只归迁移链负责，禁止先建未来表。
    command.upgrade(cfg, "head")
