"""本周关注须把无考试作为正常空态，且保留其他信号。"""

import json
import logging
from datetime import date, timedelta

import pytest

from app.db import workspace_models as wm
from app.db.models import SessionLocal


@pytest.mark.parametrize("exam_kind", ["none", "subject-only", "total", "empty-class"])
def test_weekly_focus_exam_availability(client, v1_seed, caplog, exam_kind):
    """已有其他班/学年的考试不可兜底；200 也必须没有被吞掉的计算异常。"""
    today = date.today()
    with SessionLocal() as db:
        year = wm.AcademicYear(
            name=f"weekly-focus-{exam_kind}",
            start_date=today - timedelta(days=90),
            end_date=today + timedelta(days=90),
        )
        db.add(year)
        db.flush()
        classroom = wm.AdministrativeClass(
            academic_year_id=year.id, grade=2, class_num=6,
        )
        person = wm.WsStudentIdentity(
            data_domain="homeroom", display_name="合成关注学生",
        )
        db.add_all([classroom, person])
        db.flush()
        year_id, class_id, person_id = year.id, classroom.id, person.id
        if exam_kind != "empty-class":
            db.add(wm.Enrollment(
                admin_class_id=class_id, identity_id=person_id,
                valid_from=today - timedelta(days=30), status="active",
            ))
            db.add(wm.WsStudentNote(
                data_domain="homeroom", person_id=person_id, date=today,
                category="谈话记录", content="合成观察", follow_up="复查学习安排",
                follow_up_done=False,
            ))
            for offset in (1, 0):
                assignment = wm.HomeworkAssignment(
                    data_domain="homeroom", class_ref_id=class_id,
                    academic_year_id=year_id, subject="数学", homework_type="练习",
                    assigned_date=today - timedelta(days=offset),
                    batch_token=f"weekly-focus-{exam_kind}-{offset}",
                    expected_members_json=json.dumps([person_id]), status="active",
                )
                db.add(assignment)
                db.flush()
                db.add(wm.HomeworkSubmission(
                    assignment_id=assignment.id, person_id=person_id,
                    submission_status="missing",
                ))
        if exam_kind in ("subject-only", "total"):
            db.add(wm.ScoreFact(
                data_domain="homeroom", academic_year_id=year_id,
                class_ref_id=class_id, identity_id=person_id,
                exam_name="合成关注考试", exam_date=today,
                subject="总分" if exam_kind == "total" else "数学",
                total_type="主三门" if exam_kind == "total" else None,
                score=240.0 if exam_kind == "total" else 80.0,
                xueji_rank=450 if exam_kind == "total" else None,
            ))
        db.commit()

    with caplog.at_level(logging.ERROR, logger="app.api.analysis"):
        response = client.get(
            "/api/v1/homeroom/analysis/weekly-focus",
            params={"academic_year_id": year_id, "class_id": class_id},
        )
    assert response.status_code == 200, response.text
    assert not [
        record for record in caplog.records
        if record.name == "app.api.analysis" and record.levelno >= logging.ERROR
    ], "本周关注不能用 200 响应掩盖计算异常"
    result = response.json()
    assert result["class_id"] == class_id
    if exam_kind == "empty-class":
        assert result["students"] == []
        assert result["total_count"] == 0
        return

    assert result["total_count"] == 1
    student = result["students"][0]
    assert student["student_id"] == str(person_id)
    assert "谈话跟进待办：复查学习安排" in student["reasons"]
    assert any("连续缺交2次" in reason for reason in student["reasons"])
    if exam_kind == "total":
        assert "临界段" in student["reasons"]
        assert student["score"] == 5
    else:
        assert len(student["reasons"]) == 2
        assert student["score"] == 4
