"""共享测试隔离。

必须在任何测试模块导入 ``app`` 前设置独立数据目录，避免测试读写教师真实的
``~/.exam-tracker`` 数据库，也避免其他产品版本留下的扩展列污染本仓库 schema。
"""

import os
import tempfile

import pytest

_TEST_DATA_DIR = tempfile.mkdtemp(prefix="exam-tracker-tests-")
os.environ["EXAM_TRACKER_DIR"] = _TEST_DATA_DIR
os.environ["EXAM_TRACKER_BACKUP_DIR"] = os.path.join(_TEST_DATA_DIR, "backups")


@pytest.fixture(scope="module", autouse=True)
def isolated_module_schema():
    """每个测试模块从空 schema 开始，禁止依赖模块执行顺序。"""
    from app.db.models import Base, engine

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield

@pytest.fixture
def db_session():
    # 测试模块会在导入 app 之前设置独立 EXAM_TRACKER_DIR；这里必须延迟导入，
    # 否则 conftest 收集阶段会先把全局 engine 绑定到用户默认数据库。
    from app.db.models import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
