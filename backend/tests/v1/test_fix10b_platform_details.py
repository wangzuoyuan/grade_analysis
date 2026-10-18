"""审查问题 10b 回归：教学平台归并考试明细按单一日期过滤漏学生。

背景（独立代码审查报告 §10 / 核实说明 §10 / probe2.py 10B_multi_day）：
不同日期分科考试归并进同一事件后（ScoreFact 保留各自 exam_date），
teaching_platform.student_rows 在按 teaching_exam_id/exam_event_id 匹配
之外硬编码 `exam_date == exam.exam_date` → 次日学生明细消失
（readable_facts 3 人、明细 2 人）。

修复：按事件/考试 id 匹配时不再钉死单一 exam_date；每条 fact 用它自己
的 exam_date 做考试当日成员有效期与共享权限检查（铁律不放松——
不在该日期名册上的学生绝不出现）。
"""

from datetime import date

from app.db import workspace_models as wm
from tests.v1.test_exam_management import world  # noqa: F401  (events_world 的依赖)
from tests.v1.test_exam_events import events_world  # noqa: F401  (fixture 组合)


def test_multiday_merged_event_details_complete(client, events_world):
    """次日分科考归并后：明细含当日 2 人 + 次日 2 人（含次日才入班的
    学生）；修复前实测明细仅 2 人（次日事实被日期过滤丢弃）。"""
    w = events_world
    db = w.db

    # 次日考试：独立事件 + 独立 TeachingExamScope，事实保自己的日期
    second_event = wm.WorkspaceExamEvent(
        academic_year_id=w.ay, grade=2, exam_name="FIX10B次日科考",
    )
    db.add(second_event)
    db.flush()
    second_exam = wm.TeachingExamScope(
        academic_year_id=w.ay, grade=2, subject="物理",
        exam_name=second_event.exam_name, exam_date=date(2025, 11, 22),
        exam_event_id=second_event.id,
    )
    db.add(second_exam)
    db.flush()

    both_day = wm.WsStudentIdentity(data_domain="teaching", display_name="FIX10B全期生")
    next_day = wm.WsStudentIdentity(data_domain="teaching", display_name="FIX10B次日生")
    db.add_all([both_day, next_day])
    db.flush()
    db.add(wm.TeachingClassMember(
        teaching_class_id=w.ta, identity_id=both_day.id, valid_from=date(2025, 9, 1)))
    # 次日才入班：11-21 当日不是成员，11-22 是
    db.add(wm.TeachingClassMember(
        teaching_class_id=w.ta, identity_id=next_day.id, valid_from=date(2025, 11, 22)))
    db.add(wm.ScoreFact(
        data_domain="teaching", academic_year_id=w.ay, class_ref_id=w.ta,
        identity_id=both_day.id, exam_name=second_event.exam_name,
        exam_event_id=second_event.id, exam_date=second_exam.exam_date,
        subject="物理", score=88, teaching_exam_id=second_exam.id))
    db.add(wm.ScoreFact(
        data_domain="teaching", academic_year_id=w.ay, class_ref_id=w.ta,
        identity_id=next_day.id, exam_name=second_event.exam_name,
        exam_event_id=second_event.id, exam_date=second_exam.exam_date,
        subject="物理", score=92, teaching_exam_id=second_exam.id))
    db.commit()

    p = client.post("/api/v1/teaching/exams/merge-preview", json={
        "academic_year_id": w.ay,
        "source_event_id": second_event.id,
        "target_event_id": w.teaching_event,
    })
    assert p.status_code == 200, p.text
    r = client.post("/api/v1/teaching/exams/merge-confirm", json={
        "token": p.json()["token"],
        "confirm_name": p.json()["target"]["exam_name"],
        "accept_conflicts": True,
    })
    assert r.status_code == 200, r.text
    db.expire_all()

    detail = client.get(f"/api/v1/teaching/platform/exams/{w.exam}/students")
    assert detail.status_code == 200, detail.text
    students = detail.json()["students"]
    ids = {s["person_id"] for s in students}
    # 当日 ta/tb 两人 + 次日两人（全期生 + 次日才入班生）都可见
    assert ids == {w.people["ta"], w.people["tb"], both_day.id, next_day.id}, students
    scores = {s["person_id"]: s["score"] for s in students}
    assert scores[both_day.id] == 88 and scores[next_day.id] == 92


def test_multiday_membership_gate_not_loosened(client, events_world):
    """铁律不放松：学生只在 11-22 有事实、且 11-22 当日不在名册（11-23
    才入班）→ 该 fact 仍被成员门拦下，绝不因多日归并而混入。"""
    w = events_world
    db = w.db

    second_event = wm.WorkspaceExamEvent(
        academic_year_id=w.ay, grade=2, exam_name="FIX10B门禁考",
    )
    db.add(second_event)
    db.flush()
    second_exam = wm.TeachingExamScope(
        academic_year_id=w.ay, grade=2, subject="物理",
        exam_name=second_event.exam_name, exam_date=date(2025, 11, 22),
        exam_event_id=second_event.id,
    )
    db.add(second_exam)
    db.flush()
    late_joiner = wm.WsStudentIdentity(data_domain="teaching", display_name="FIX10B晚到生")
    db.add(late_joiner)
    db.flush()
    db.add(wm.TeachingClassMember(
        teaching_class_id=w.ta, identity_id=late_joiner.id, valid_from=date(2025, 11, 23)))
    db.add(wm.ScoreFact(
        data_domain="teaching", academic_year_id=w.ay, class_ref_id=w.ta,
        identity_id=late_joiner.id, exam_name=second_event.exam_name,
        exam_event_id=second_event.id, exam_date=date(2025, 11, 22),
        subject="物理", score=99, teaching_exam_id=second_exam.id))
    db.commit()

    p = client.post("/api/v1/teaching/exams/merge-preview", json={
        "academic_year_id": w.ay,
        "source_event_id": second_event.id,
        "target_event_id": w.teaching_event,
    })
    assert p.status_code == 200, p.text
    r = client.post("/api/v1/teaching/exams/merge-confirm", json={
        "token": p.json()["token"],
        "confirm_name": p.json()["target"]["exam_name"],
        "accept_conflicts": True,
    })
    assert r.status_code == 200, r.text
    db.expire_all()

    detail = client.get(f"/api/v1/teaching/platform/exams/{w.exam}/students")
    assert detail.status_code == 200, detail.text
    students = detail.json()["students"]
    ids = {s["person_id"] for s in students}
    assert ids == {w.people["ta"], w.people["tb"]}, students
    assert late_joiner.id not in ids
