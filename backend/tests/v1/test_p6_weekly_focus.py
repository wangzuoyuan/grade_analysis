"""测试班主任工作台本周关注与名次范围（Weekly Focus & Rank Range）。"""

from datetime import date, timedelta
import pytest
from app.db import workspace_models as wm
from app.db.models import SessionLocal

API = "/api/v1"


def test_homeroom_stats_returns_rank_min_and_rank_max(client, v1_seed):
    """验证 HomeroomTotalStat 正常返回 rank_min 与 rank_max。"""
    db = SessionLocal()
    try:
        fact1 = wm.ScoreFact(
            data_domain="homeroom",
            academic_year_id=v1_seed.ay_id,
            exam_name="2025期中",
            exam_date=date(2025, 11, 15),
            class_ref_id=v1_seed.h6_id,
            identity_id=v1_seed.jia_h_id,
            total_type="主三门",
            score=270.0,
            xueji_rank=15,
        )
        fact2 = wm.ScoreFact(
            data_domain="homeroom",
            academic_year_id=v1_seed.ay_id,
            exam_name="2025期中",
            exam_date=date(2025, 11, 15),
            class_ref_id=v1_seed.h6_id,
            identity_id=v1_seed.yi_h_id,
            total_type="主三门",
            score=210.0,
            xueji_rank=280,
        )
        db.add_all([fact1, fact2])
        db.commit()
    finally:
        db.close()

    r = client.get(f"{API}/homeroom/analysis/exams/2025期中/stats")
    assert r.status_code == 200, r.text
    data = r.json()
    totals = data.get("totals", [])
    main_total = next((t for t in totals if t["total_type"] == "主三门"), None)
    assert main_total is not None
    assert main_total["rank_min"] == 15
    assert main_total["rank_max"] == 280


def test_homeroom_weekly_focus_aggregation(client, v1_seed):
    """验证本周关注接口聚合谈话待办、缺交与考试关注。"""
    db = SessionLocal()
    try:
        note = wm.WsStudentNote(
            data_domain="homeroom",
            person_id=v1_seed.h_person_ids[0],
            date=date.today(),
            category="谈话记录",
            content="与学生交流近期学习状态",
            follow_up="下周一检查错题本",
            follow_up_done=False,
            source=None,
        )
        sys_note = wm.WsStudentNote(
            data_domain="homeroom",
            person_id=v1_seed.h_person_ids[1],
            date=date.today(),
            category="作业考勤",
            content="[迟到] 考勤",
            follow_up="系统自动记录",
            follow_up_done=False,
            source="homework:attendance",
        )
        db.add_all([note, sys_note])
        db.commit()
    finally:
        db.close()

    r = client.get(
        f"{API}/homeroom/analysis/weekly-focus",
        params={
            "academic_year_id": v1_seed.ay_id,
            "class_id": v1_seed.h6_id,
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["class_id"] == v1_seed.h6_id
    assert "start" in data["week"] and "end" in data["week"]

    # 验证第一个学生包含谈话待办标签
    alice_item = next(
        (s for s in data["students"] if s["student_id"] == str(v1_seed.h_person_ids[0])),
        None,
    )
    assert alice_item is not None
    assert any("下周一检查错题本" in r for r in alice_item["reasons"])

    # 验证第二个学生不包含系统内部记录的谈话待办
    bob_item = next(
        (s for s in data["students"] if s["student_id"] == str(v1_seed.h_person_ids[1])),
        None,
    )
    if bob_item:
        assert not any("系统自动记录" in r for r in bob_item["reasons"])


def test_weekly_focus_missing_surge_signal(client, v1_seed):
    """回归：本周缺交激增信号必须真正生效（修复前引用了不存在的列名，
    AttributeError 被静默吞掉导致该信号从未输出）。
    口径：考勤批次、忘带/迟到行不计入缺交激增。"""
    db = SessionLocal()
    today = date.today()
    try:
        def add_assignment(subject, assigned, token):
            a = wm.HomeworkAssignment(
                data_domain="homeroom",
                class_ref_id=v1_seed.h6_id,
                academic_year_id=v1_seed.ay_id,
                subject=subject,
                homework_type="日常作业",
                assigned_date=assigned,
                batch_token=token,
                expected_members_json="[]",
                status="active",
            )
            db.add(a)
            db.flush()
            return a

        def add_missing(a, person_id, evaluation=None):
            db.add(wm.HomeworkSubmission(
                assignment_id=a.id,
                person_id=person_id,
                submission_status="missing",
                evaluation=evaluation,
            ))

        # 甲：3 周前 1 次 + 本周 3 次纯缺交 → 触发激增
        add_missing(add_assignment("数学", today - timedelta(days=25), "wf-surge-old"), v1_seed.jia_h_id)
        for i, subject in enumerate(("语文", "数学", "英语")):
            add_missing(
                add_assignment(subject, today - timedelta(days=2 - i), f"wf-surge-w{i}"),
                v1_seed.jia_h_id,
            )

        # 乙：忘带 + 考勤批次缺勤 → 均不得计入激增
        add_missing(
            add_assignment("物理", today - timedelta(days=1), "wf-surge-forgot"),
            v1_seed.yi_h_id,
            evaluation="忘带",
        )
        add_missing(add_assignment("考勤", today, "wf-surge-att"), v1_seed.yi_h_id)
        db.commit()
    finally:
        db.close()

    r = client.get(
        f"{API}/homeroom/analysis/weekly-focus",
        params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    jia = next(
        (s for s in data["students"] if s["student_id"] == str(v1_seed.jia_h_id)), None
    )
    assert jia is not None, "缺交激增信号未生效：甲（本周 3 次纯缺交）应出现在本周关注"
    assert any("缺交激增" in reason for reason in jia["reasons"])

    yi = next(
        (s for s in data["students"] if s["student_id"] == str(v1_seed.yi_h_id)), None
    )
    if yi is not None:
        assert not any("缺交激增" in reason for reason in yi["reasons"]), \
            "忘带与考勤缺勤不得计入缺交激增"
