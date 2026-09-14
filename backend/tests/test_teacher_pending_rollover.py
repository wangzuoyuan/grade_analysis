"""班主任换届待办信号的班级与年级隔离测试。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.models import (
    ClassRoster,
    StudentAlias,
    StudentIdentity,
    Teacher,
)


@pytest.fixture(autouse=True)
def clean_pending_rollover_data(db_session):
    db_session.query(StudentAlias).delete()
    db_session.query(StudentIdentity).delete()
    db_session.query(ClassRoster).delete()
    db_session.query(Teacher).delete()
    db_session.commit()
    yield


def _link(db, student_id: str, grade: int, name: str) -> None:
    identity = StudentIdentity(display_name=name)
    db.add(identity)
    db.flush()
    db.add(
        StudentAlias(
            identity_id=identity.id,
            student_id=student_id,
            grade=grade,
            link_source="manual",
        )
    )


def test_pending_rollover_is_scoped_to_highest_bound_grade_and_class(db_session):
    db_session.add(
        Teacher(
            id=1,
            name="测试班主任",
            target_class_high1=6,
            target_class_high2=3,
            target_class_high3=8,
        )
    )

    # 已完成的高一→高二身份不应压制高三待办。
    _link(db_session, "g1-linked", 1, "已接续学生")
    _link(db_session, "g2-linked", 2, "已接续学生")
    db_session.add_all(
        [
            ClassRoster(
                student_id="g3-unlinked",
                name="本班未接续",
                grade=3,
                class_num=8,
            ),
            ClassRoster(
                student_id="g3-other-unlinked",
                name="外班未接续",
                grade=3,
                class_num=9,
            ),
        ]
    )
    db_session.commit()

    client = TestClient(app)
    assert client.get("/api/teacher").json()["has_pending_rollover"] is True

    # 当前高三8班全部建立当年级 alias 后，外班未接续不影响本班。
    _link(db_session, "g3-unlinked", 3, "本班未接续")
    db_session.commit()
    assert client.get("/api/teacher").json()["has_pending_rollover"] is False


def test_pending_rollover_supports_grade_two_when_grade_three_has_no_data(db_session):
    teacher = Teacher(
        id=1,
        name="测试班主任",
        target_class_high2=3,
        target_class_high3=8,
    )
    db_session.add(teacher)
    db_session.add(
        ClassRoster(
            student_id="g2-unlinked",
            name="高二未接续",
            grade=2,
            class_num=3,
        )
    )
    db_session.commit()

    client = TestClient(app)
    assert client.get("/api/teacher").json()["has_pending_rollover"] is True

    _link(db_session, "g2-unlinked", 2, "高二未接续")
    db_session.commit()
    assert client.get("/api/teacher").json()["has_pending_rollover"] is False
