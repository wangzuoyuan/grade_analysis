"""作业预警退出机制测试：
- 连续负面评价人工解除（POST /api/v1/homework/warnings/dismiss）
- 解除后再次出现连续差评重新唤醒
- 忘带预警近 30 天滑动窗口自动冲刷
"""

from datetime import date
import json
from app.db import workspace_models as wm

API = "/api/v1"


def _preview_confirm(client, seed, day, rows, subject="物理", homework_type="课后作业"):
    preview = client.post(
        f"{API}/homework/preview",
        json={
            "mode": "homeroom",
            "class_id": seed.h6_id,
            "academic_year_id": seed.ay_id,
            "subject": subject,
            "homework_type": homework_type,
            "assigned_date": day,
            "input": {"kind": "detailed", "rows": rows},
        },
    )
    assert preview.status_code == 200, preview.text
    token = preview.json()["token"]
    confirm = client.post(f"{API}/homework/confirm", json={"token": token})
    assert confirm.status_code == 200, confirm.text
    return confirm.json()


def test_quality_warning_dismissal_and_reactivation(client, v1_seed):
    """测试连续负面评价人工解除及后续新产生负面的重新激活机制。"""
    # 1. 产生连续 2 次负面评价（使用规范的负面评价词：潦草、错误率高）
    _preview_confirm(
        client,
        v1_seed,
        "2025-10-01",
        [{"name_or_alias": "秦甲", "status": "submitted", "evaluation": "潦草"}],
    )
    _preview_confirm(
        client,
        v1_seed,
        "2025-10-02",
        [{"name_or_alias": "秦甲", "status": "submitted", "evaluation": "错误率高"}],
    )

    # 查预警：秦甲在 quality 预警中
    warn_resp = client.get(
        f"{API}/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "subject": "物理"},
    )
    assert warn_resp.status_code == 200
    quality_list = warn_resp.json()["quality"]
    assert any(q["person_id"] == v1_seed.jia_h_id for q in quality_list)

    # 2. 老师人工解除该预警
    dismiss_resp = client.post(
        f"{API}/homework/warnings/dismiss",
        json={
            "mode": "homeroom",
            "class_id": v1_seed.h6_id,
            "academic_year_id": v1_seed.ay_id,
            "subject": "物理",
            "person_id": v1_seed.jia_h_id,
            "warning_kind": "quality",
            "dismiss_date": "2025-10-03",
        },
    )
    assert dismiss_resp.status_code == 200, dismiss_resp.text
    assert dismiss_resp.json()["ok"] is True

    # 3. 再次查询预警：秦甲已退出连续负面预警
    warn_resp2 = client.get(
        f"{API}/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "subject": "物理"},
    )
    assert warn_resp2.status_code == 200
    assert not any(q["person_id"] == v1_seed.jia_h_id for q in warn_resp2.json()["quality"])

    # 4. 秦甲之后只有 1 次差评：不足 2 次，不重新报警
    _preview_confirm(
        client,
        v1_seed,
        "2025-10-05",
        [{"name_or_alias": "秦甲", "status": "submitted", "evaluation": "马虎"}],
    )
    warn_resp3 = client.get(
        f"{API}/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "subject": "物理"},
    )
    assert not any(q["person_id"] == v1_seed.jia_h_id for q in warn_resp3.json()["quality"])

    # 5. 秦甲再次连续出现第 2 次差评：重新唤醒报警
    _preview_confirm(
        client,
        v1_seed,
        "2025-10-06",
        [{"name_or_alias": "秦甲", "status": "submitted", "evaluation": "不合格"}],
    )
    warn_resp4 = client.get(
        f"{API}/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "subject": "物理"},
    )
    reactivated = next(
        q for q in warn_resp4.json()["quality"] if q["person_id"] == v1_seed.jia_h_id
    )
    assert reactivated["count"] == 2
    assert "2025-10-06" in reactivated["dates"]


def test_forgot_warning_auto_flush_30_days(client, v1_seed, db_session):
    """测试忘带预警按近 30 天滑动窗口自动冲刷。"""
    # 模拟 50 天前的 3 次忘带记录（应该被自动冲刷）
    old_dates = [date(2025, 8, 1), date(2025, 8, 2), date(2025, 8, 3)]
    for d in old_dates:
        db_session.add(
            wm.WsStudentNote(
                data_domain="homeroom",
                person_id=v1_seed.bing_h_id,
                date=d,
                category="谈话",
                content="[忘带] 作业本忘带",
                source="migration:test",
            )
        )
    db_session.commit()

    # 在 2025-10-01 产生一个作业批次，anchor_date 落在 2025-10-01
    _preview_confirm(
        client,
        v1_seed,
        "2025-10-01",
        [{"name_or_alias": "秦丙", "status": "submitted"}],
    )

    warn_resp = client.get(
        f"{API}/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "subject": "物理"},
    )
    assert warn_resp.status_code == 200
    # 8 月份的 3 次忘带已超过 30 天，自动冲刷，不触发预警
    assert not any(f["person_id"] == v1_seed.bing_h_id for f in warn_resp.json()["forgot"])

    # 在近 30 天内（9 月 20 日之后）新增 3 次忘带
    recent_dates = [date(2025, 9, 25), date(2025, 9, 28), date(2025, 9, 30)]
    for d in recent_dates:
        db_session.add(
            wm.WsStudentNote(
                data_domain="homeroom",
                person_id=v1_seed.bing_h_id,
                date=d,
                category="谈话",
                content="[忘带] 练习册未带",
                source="migration:test",
            )
        )
    db_session.commit()

    warn_resp2 = client.get(
        f"{API}/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "subject": "物理"},
    )
    assert warn_resp2.status_code == 200
    bing_forgot = next(
        f for f in warn_resp2.json()["forgot"] if f["person_id"] == v1_seed.bing_h_id
    )
    # 近 30 天累计 3 次，正确触发预警
    assert bing_forgot["count"] == 3


def test_attendance_and_forgot_excluded_from_submitted_and_streaks(client, v1_seed):
    """测试：迟到、没来、忘带不计入已交，且不计入连续缺交统计（双工作台同步）。"""
    # 班主任工作台：录入 4 天作业
    # 2025-10-10: 秦甲 迟到
    # 2025-10-11: 秦甲 纯缺交 (missing)
    # 2025-10-12: 秦甲 忘带
    # 2025-10-13: 秦甲 没来
    conf1 = _preview_confirm(
        client, v1_seed, "2025-10-10",
        [{"name_or_alias": "秦甲", "status": "missing", "attendance": "迟到"}],
    )
    assert conf1["submitted"] == 2  # 3人班级，秦甲迟到不计入已交，已交仅2人
    assert conf1["missing"] == 1

    _preview_confirm(
        client, v1_seed, "2025-10-11",
        [{"name_or_alias": "秦甲", "status": "missing"}],
    )
    conf3 = _preview_confirm(
        client, v1_seed, "2025-10-12",
        [{"name_or_alias": "秦甲", "status": "missing", "evaluation": "忘带"}],
    )
    assert conf3["submitted"] == 2
    assert conf3["missing"] == 1

    _preview_confirm(
        client, v1_seed, "2025-10-13",
        [{"name_or_alias": "秦甲", "status": "missing", "attendance": "没来"}],
    )

    # 查预警：秦甲只有 10-11 是纯缺交，迟到、忘带、没来均不累加连续缺交
    warn_h = client.get(
        f"{API}/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "subject": "物理", "min_missing": 2},
    )
    assert warn_h.status_code == 200
    # min_missing=2 时，秦甲纯缺交只有 1 次，不应该出现在连续缺交预警名单中
    assert not any(s["person_id"] == v1_seed.jia_h_id for s in warn_h.json()["students"])

    # min_missing=1 时查画像/预警，秦甲连续缺交仅为 1
    warn_h1 = client.get(
        f"{API}/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "subject": "物理", "min_missing": 1},
    )
    jia_s = next(s for s in warn_h1.json()["students"] if s["person_id"] == v1_seed.jia_h_id)
    assert jia_s["current_streak"] == 1


def test_forgot_and_attendance_warning_combined_and_dismissal(client, v1_seed):
    """测试：出勤异常（迟到、没来等）与忘带合并计入预警，累计3次提醒，支持人工解除。"""
    # 2025-10-20: 秦丙 迟到
    _preview_confirm(
        client, v1_seed, "2025-10-20",
        [{"name_or_alias": "秦丙", "status": "missing", "attendance": "迟到"}],
    )
    # 2025-10-21: 秦丙 忘带作业本
    _preview_confirm(
        client, v1_seed, "2025-10-21",
        [{"name_or_alias": "秦丙", "status": "missing", "evaluation": "忘带作业本"}],
    )
    # 2025-10-22: 秦丙 没来
    _preview_confirm(
        client, v1_seed, "2025-10-22",
        [{"name_or_alias": "秦丙", "status": "missing", "attendance": "没来"}],
    )

    warn_resp = client.get(
        f"{API}/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "subject": "物理"},
    )
    assert warn_resp.status_code == 200
    forgot_list = warn_resp.json()["forgot"]
    bing_warn = next(f for f in forgot_list if f["person_id"] == v1_seed.bing_h_id)
    assert bing_warn["count"] >= 3
    assert len(bing_warn["dates"]) >= 3

    # 人工解除忘带与出勤预警
    dismiss_resp = client.post(
        f"{API}/homework/warnings/dismiss",
        json={
            "mode": "homeroom",
            "class_id": v1_seed.h6_id,
            "academic_year_id": v1_seed.ay_id,
            "subject": "物理",
            "person_id": v1_seed.bing_h_id,
            "warning_kind": "forgot",
            "dismiss_date": "2025-10-23",
        },
    )
    assert dismiss_resp.status_code == 200
    assert dismiss_resp.json()["ok"] is True

    # 解除后查预警：已退出
    warn_after = client.get(
        f"{API}/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "subject": "物理"},
    )
    assert not any(f["person_id"] == v1_seed.bing_h_id for f in warn_after.json()["forgot"])


def test_teaching_mode_attendance_and_forgot_rules(client, v1_seed):
    """测试：教学工作台下迟到、没来、忘带规则完全对称生效。"""
    # 教学班 t6 (任教物理)，学生为 秦甲·T
    # 2025-11-01: 秦甲·T 迟到 (不计入已交)
    p1 = client.post(
        f"{API}/homework/preview",
        json={
            "mode": "teaching",
            "teaching_class_id": v1_seed.t6_id,
            "academic_year_id": v1_seed.ay_id,
            "subject": "物理",
            "homework_type": "校本作业",
            "assigned_date": "2025-11-01",
            "input": {
                "kind": "detailed",
                "rows": [{"name_or_alias": "秦甲·T", "status": "missing", "attendance": "迟到"}],
            },
        },
    )
    assert p1.status_code == 200, p1.text
    c1 = client.post(f"{API}/homework/confirm", json={"token": p1.json()["token"]}).json()
    assert c1["submitted"] == 2  # 教学班3人，秦甲·T迟到为missing，已交仅2人
    assert c1["missing"] == 1

    # 2025-11-02: 秦甲·T 纯缺交 (missing)
    p2 = client.post(
        f"{API}/homework/preview",
        json={
            "mode": "teaching",
            "teaching_class_id": v1_seed.t6_id,
            "academic_year_id": v1_seed.ay_id,
            "subject": "物理",
            "homework_type": "校本作业",
            "assigned_date": "2025-11-02",
            "input": {
                "kind": "detailed",
                "rows": [{"name_or_alias": "秦甲·T", "status": "missing"}],
            },
        },
    )
    client.post(f"{API}/homework/confirm", json={"token": p2.json()["token"]})

    # 2025-11-03: 秦甲·T 忘带
    p3 = client.post(
        f"{API}/homework/preview",
        json={
            "mode": "teaching",
            "teaching_class_id": v1_seed.t6_id,
            "academic_year_id": v1_seed.ay_id,
            "subject": "物理",
            "homework_type": "校本作业",
            "assigned_date": "2025-11-03",
            "input": {
                "kind": "detailed",
                "rows": [{"name_or_alias": "秦甲·T", "status": "missing", "evaluation": "忘带"}],
            },
        },
    )
    client.post(f"{API}/homework/confirm", json={"token": p3.json()["token"]})

    # 教学工作台查询预警：秦甲在连续缺交中只记 1 次纯缺交，不因迟到/忘带累加到 3
    warn_t = client.get(
        f"{API}/homework/warnings",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t6_id, "subject": "物理", "min_missing": 2},
    )
    assert warn_t.status_code == 200
    assert not any(s["person_id"] == v1_seed.jia_t_id for s in warn_t.json()["students"])


def test_homeroom_attendance_batch_standalone(client, v1_seed):
    """测试班主任工作台考勤独立批次（subject='考勤'）两段式录入、确认入库与不计入缺交统计。"""
    # 2025-11-10: 班主任录入「考勤」独立批次：秦甲 迟到，全班其余人全勤（kind=full）
    preview = client.post(
        f"{API}/homework/preview",
        json={
            "mode": "homeroom",
            "class_id": v1_seed.h6_id,
            "academic_year_id": v1_seed.ay_id,
            "subject": "考勤",
            "homework_type": "日常作业",
            "assigned_date": "2025-11-10",
            "input": {
                "kind": "full",
                "exceptions": [{"name_or_alias": "秦甲", "status": "missing", "attendance": "迟到"}],
            },
        },
    )
    assert preview.status_code == 200, preview.text
    token = preview.json()["token"]
    confirm = client.post(f"{API}/homework/confirm", json={"token": token})
    assert confirm.status_code == 200, confirm.text
    body = confirm.json()
    assert body["missing"] == 1
    assert body["submitted"] == 2

    # 查看批次列表：学科为 考勤，迟到人数为 1
    listed = client.get(
        f"{API}/homework/assignments",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id},
    ).json()["items"]
    att_item = next(item for item in listed if item["assignment_id"] == body["assignment_id"])
    assert att_item["subject"] == "考勤"
    assert att_item["attendance_count"] == 1

    # 查询预警：迟到不累加连续缺交（min_missing=2 时秦甲不预警）
    warn_h = client.get(
        f"{API}/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "subject": "考勤", "min_missing": 2},
    )
    assert warn_h.status_code == 200
    assert not any(s["person_id"] == v1_seed.jia_h_id for s in warn_h.json()["students"])


def test_student_events_academic_year_filter_and_all_history(client, v1_seed, db_session):
    """学生事件流接口按显式 academic_year_id 过滤；all_history=True 返回跨学年全历史。"""
    # 创建另一学年 2024-2025 并插入秦甲的旧缺交
    ay_old = wm.AcademicYear(name="2024-2025", start_date=date(2024, 9, 1), end_date=date(2025, 7, 15))
    db_session.add(ay_old)
    db_session.flush()

    old_hw = wm.HomeworkAssignment(
        data_domain="homeroom",
        class_ref_id=v1_seed.h6_id,
        academic_year_id=ay_old.id,
        subject="数学",
        homework_type="旧练习",
        assigned_date=date(2024, 11, 1),
        batch_token="test-old-hw-ay",
        expected_members_json=json.dumps([v1_seed.jia_h_id]),
    )
    db_session.add(old_hw)
    db_session.flush()
    db_session.add(wm.HomeworkSubmission(
        assignment_id=old_hw.id,
        person_id=v1_seed.jia_h_id,
        submission_status="missing",
    ))
    db_session.commit()

    # 1. 指定当前学年 v1_seed.ay_id：不包含 2024 旧学年作业
    resp_cur = client.get(
        f"{API}/homework/students/{v1_seed.jia_h_id}",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "academic_year_id": v1_seed.ay_id},
    )
    assert resp_cur.status_code == 200
    dates_cur = [e["assigned_date"] for e in resp_cur.json()["events"]]
    assert "2024-11-01" not in dates_cur

    # 2. 传 all_history=True：跨学年返回 2024 年旧作业
    resp_all = client.get(
        f"{API}/homework/students/{v1_seed.jia_h_id}",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "academic_year_id": v1_seed.ay_id, "all_history": "true"},
    )
    assert resp_all.status_code == 200
    dates_all = [e["assigned_date"] for e in resp_all.json()["events"]]
    assert "2024-11-01" in dates_all



