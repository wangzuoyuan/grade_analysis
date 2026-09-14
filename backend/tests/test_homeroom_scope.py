"""班主任当前范围解析契约。"""

import inspect

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.chat import tools
from app.db.models import Base, ClassRoster, HomeworkRecord, HomeworkSetting, Teacher
from app.homework import router, service
from app.ingest.router import detect_class_from_students


class _TeacherQuery:
    def __init__(self, teacher):
        self.teacher = teacher

    def first(self):
        return self.teacher


class _TeacherDb:
    def __init__(self, teacher):
        self.teacher = teacher

    def query(self, _model):
        return _TeacherQuery(self.teacher)


def test_active_class_comes_from_grade_binding():
    teacher = Teacher(target_class_high1=4, target_class_high2=11, target_class_high3=None)
    db = _TeacherDb(teacher)

    assert service.get_active_class_num(db, grade=1) == 4
    assert service.get_active_class_num(db, grade=2) == 11
    assert service.get_active_class_num(db, grade=3) is None


def test_class_scoped_entrypoints_have_no_numeric_default():
    assert inspect.signature(router.hw_kpi).parameters['class_num'].default is None
    assert inspect.signature(router.hw_warnings).parameters['class_num'].default is None
    assert inspect.signature(router.hw_correlation).parameters['class_num'].default is None
    assert inspect.signature(router.hw_correlation_subjects).parameters['class_num'].default is None
    assert inspect.signature(router.weekly_focus).parameters['class_num'].default is None
    assert inspect.signature(tools.class_homework_ranking).parameters['class_num'].default is None
    assert inspect.signature(tools.homework_grade_correlation).parameters['class_num'].default is None


def test_upload_detection_does_not_invent_class_six():
    assert detect_class_from_students([]) is None
    assert detect_class_from_students([{'class_num': 9}, {'class_num': 9}, {'class_num': 3}]) == 9


def test_dashboard_homework_aggregates_filter_active_grade_and_bound_class():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        db.add(Teacher(target_class_high1=4))
        db.add(HomeworkSetting(key="active_grade", value="1"))
        db.add_all([
            ClassRoster(student_id="g1-c4", name="本班学生", class_num=4, grade=1, excluded=0),
            ClassRoster(student_id="g1-c5", name="同年级他班", class_num=5, grade=1, excluded=0),
            ClassRoster(student_id="g2-c4", name="跨年级同班号", class_num=4, grade=2, excluded=0),
        ])
        db.add_all([
            HomeworkRecord(student_id="g1-c4", date="2026-03-01", subject="数学"),
            HomeworkRecord(student_id="g1-c4", date="2026-03-02", subject="数学"),
            HomeworkRecord(student_id="g1-c5", date="2026-03-01", subject="语文"),
            HomeworkRecord(student_id="g1-c5", date="2026-03-02", subject="语文"),
            HomeworkRecord(student_id="g1-c5", date="2026-03-03", subject="语文"),
            HomeworkRecord(student_id="g2-c4", date="2026-03-01", subject="英语"),
        ])
        db.commit()

        result = service.kpi(db, "2026-03-01", "2026-03-31", class_num=4)
        warning_result = service.warnings(db, "2026-03-01", "2026-03-31", class_num=4)

        assert result["total_misses"] == 2
        assert result["worst_subject"] == {"name": "数学", "count": 2}
        assert warning_result["counts"] == {"serious": 0, "warning": 1, "students": 1}
        assert warning_result["warning"][0]["student_id"] == "g1-c4"
        assert router._validated_scope_class_num(db, 4) == 4
        with pytest.raises(HTTPException) as exc_info:
            router._validated_scope_class_num(db, 5)
        assert exc_info.value.status_code == 409
    finally:
        db.close()
        engine.dispose()
