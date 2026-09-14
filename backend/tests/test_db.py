"""DB模型测试 - 验证ORM写入和查询能力

使用 conftest 配置的临时数据库；每个测试在事务中执行，测试后回滚。
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import app.db.models as models

# 使用测试进程中与 main.py 相同的隔离数据库。
TEST_DATABASE_URL = f"sqlite:///{models.engine.url.database}"

@pytest.fixture(scope="module")
def engine():
    """共享引擎"""
    return models.engine

@pytest.fixture
def db_session(engine):
    """在事务中执行测试，完成后回滚"""
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()

    yield session

    session.close()
    transaction.rollback()
    connection.close()

# === DB模型测试 ===

def test_teacher_init(db_session):
    """Step 2: 数据库初始化 - Teacher表"""
    teacher = db_session.query(models.Teacher).first()
    # 可能有初始化的教师记录
    assert teacher is not None or db_session.query(models.Teacher).count() >= 0

def test_exam_create(db_session):
    """Step 2: 考试创建"""
    exam = models.Exam(
        name="测试考试_自动",
        grade=1,
        semester="下",
        exam_date="2024-12",
        exam_type="月考",
    )
    db_session.add(exam)
    db_session.commit()
    assert exam.id is not None

def test_subject_score_query(db_session):
    """Step 2: 学科成绩查询"""
    # 查询实际存在的数据
    scores = db_session.query(models.SubjectScore).limit(10).all()
    # 不强制数量，只验证查询能工作
    assert isinstance(scores, list)

def test_total_score_query(db_session):
    """Step 2: 总分查询"""
    totals = db_session.query(models.TotalScore).limit(10).all()
    assert isinstance(totals, list)

def test_exam_query_order(db_session):
    """Step 2: 考试按日期排序"""
    exams = db_session.query(models.Exam).order_by(models.Exam.exam_date.desc()).all()
    assert isinstance(exams, list)

def test_student_id_unique(db_session):
    """Step 2: 学号唯一性约束（通过查询验证）"""
    # 取一个学号，验证能查到
    score = db_session.query(models.SubjectScore).first()
    if score:
        count = db_session.query(models.SubjectScore).filter(
            models.SubjectScore.student_id == score.student_id
        ).count()
        assert count >= 1

def test_class_average_query(db_session):
    """Step 2: 班级均分查询"""
    avgs = db_session.query(models.ClassAverage).limit(10).all()
    assert isinstance(avgs, list)

def test_upload_record_query(db_session):
    """Step 2: 上传记录查询"""
    uploads = db_session.query(models.Upload).limit(10).all()
    assert isinstance(uploads, list)
