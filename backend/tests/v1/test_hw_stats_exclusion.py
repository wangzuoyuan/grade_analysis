"""ADR-023 作业统计排除：缺交不计入看板/排行/预警；相关性与个人明细保留。

语义对照老教学版 ClassRoster.excluded（respect_excluded）：记录永不删除，
只影响聚合展示；排除按班登记，不跨班、不跨域传播。
"""

import pytest


def _preview(client, seed, **over):
    payload = {
        "mode": "homeroom",
        "class_id": seed.h6_id,
        "academic_year_id": seed.ay_id,
        "subject": "语文",
        "homework_type": "日常作业",
        "assigned_date": "2025-09-10",
        "input": {"kind": "full"},
    }
    payload.update(over)
    return client.post("/api/v1/homework/preview", json=payload)


def _confirm(client, token):
    resp = client.post("/api/v1/homework/confirm", json={"token": token})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture(scope="module")
def hw_seed(client, v1_seed):
    """两个行政班批次：语文（甲乙缺交）、数学（甲缺交）。"""
    r1 = _preview(
        client, v1_seed,
        input={"kind": "full", "exceptions": [
            {"name_or_alias": "秦甲", "status": "missing"},
            {"name_or_alias": "秦乙", "status": "missing"},
        ]},
    )
    assert r1.status_code == 200, r1.text
    _confirm(client, r1.json()["token"])
    r2 = _preview(
        client, v1_seed, subject="数学", assigned_date="2025-09-11",
        input={"kind": "full", "exceptions": [
            {"name_or_alias": "秦甲", "status": "missing"},
        ]},
    )
    assert r2.status_code == 200, r2.text
    _confirm(client, r2.json()["token"])
    return v1_seed


def _put_exclusion(client, seed, person_id, excluded):
    return client.put("/api/v1/homework/stats-exclusion", json={
        "mode": "homeroom",
        "class_id": seed.h6_id,
        "academic_year_id": seed.ay_id,
        "person_id": person_id,
        "excluded": excluded,
    })


def _month_group(client, seed):
    resp = client.get("/api/v1/homework/dashboard", params={
        "mode": "homeroom", "class_id": seed.h6_id,
        "academic_year_id": seed.ay_id, "group_by": "month",
    })
    assert resp.status_code == 200, resp.text
    groups = resp.json()["groups"]
    assert groups, "应至少有一个月度分组"
    return groups[0]


def test_exclusion_off_baseline(client, hw_seed):
    """未排除基线：看板 expected=6/missing=3；预警含甲乙两人。"""
    g = _month_group(client, hw_seed)
    assert (g["assignments"], g["expected_count"], g["submitted"], g["missing"]) == (2, 6, 3, 3)
    resp = client.get("/api/v1/homework/warnings", params={
        "mode": "homeroom", "class_id": hw_seed.h6_id,
        "academic_year_id": hw_seed.ay_id, "min_missing": 1,
    })
    assert resp.status_code == 200, resp.text
    names = [s["name"] for s in resp.json()["students"]]
    assert set(names) == {"秦甲", "秦乙"}


def test_dashboard_and_warnings_exclude(client, hw_seed):
    """排除甲：看板聚合与其缺交都消失；预警/排行只剩乙；关闭后恢复。"""
    seed = hw_seed
    resp = _put_exclusion(client, seed, seed.jia_h_id, True)
    assert resp.status_code == 200, resp.text
    entries = {e["person_id"]: e["excluded"] for e in resp.json()["entries"]}
    assert entries[seed.jia_h_id] is True and entries[seed.yi_h_id] is False

    g = _month_group(client, seed)
    assert (g["assignments"], g["expected_count"], g["submitted"], g["missing"]) == (2, 4, 3, 1)

    resp = client.get("/api/v1/homework/warnings", params={
        "mode": "homeroom", "class_id": seed.h6_id,
        "academic_year_id": seed.ay_id, "min_missing": 1,
    })
    names = [s["name"] for s in resp.json()["students"]]
    assert names == ["秦乙"], f"甲不应出现在预警排行：{names}"

    # 恢复：幂等关闭后聚合回到基线
    assert _put_exclusion(client, seed, seed.jia_h_id, False).status_code == 200
    assert _put_exclusion(client, seed, seed.jia_h_id, False).status_code == 200
    g = _month_group(client, seed)
    assert (g["expected_count"], g["missing"]) == (6, 3)


def test_correlation_keeps_excluded(client, hw_seed):
    """用户裁定（2026-09-15）：相关性不排除——排除学生的提交率样本保留。"""
    seed = hw_seed
    assert _put_exclusion(client, seed, seed.jia_h_id, True).status_code == 200
    try:
        resp = client.get("/api/v1/homework/correlation", params={
            "mode": "homeroom", "class_id": seed.h6_id,
            "academic_year_id": seed.ay_id,
            "subject": "语文", "exam_name": "2025期中",
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        pids = [p["person_id"] for p in body["pairs"]]
        assert seed.jia_h_id in pids, "排除学生仍应参与相关性"
        jia = next(p for p in body["pairs"] if p["person_id"] == seed.jia_h_id)
        assert jia["x"] == 0.0, "甲语文两次缺交，提交率应为 0"
    finally:
        _put_exclusion(client, seed, seed.jia_h_id, False)


def test_student_events_and_detail_keeps_excluded(client, hw_seed):
    """个人明细与批次明细保留（老版语义：指定具体学生查询不排除）。"""
    seed = hw_seed
    assert _put_exclusion(client, seed, seed.jia_h_id, True).status_code == 200
    try:
        resp = client.get(
            f"/api/v1/homework/students/{seed.jia_h_id}",
            params={"mode": "homeroom", "class_id": seed.h6_id,
                    "academic_year_id": seed.ay_id},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["person_id"] == seed.jia_h_id
        assert len(resp.json()["events"]) == 2, "甲的两条缺交事件应保留"

        resp = client.get("/api/v1/homework/assignments", params={
            "mode": "homeroom", "class_id": seed.h6_id,
            "academic_year_id": seed.ay_id,
        })
        first = resp.json()["items"][0]
        detail = client.get(
            f"/api/v1/homework/assignments/{first['assignment_id']}",
            params={"mode": "homeroom", "class_id": seed.h6_id,
                    "academic_year_id": seed.ay_id},
        ).json()
        pids = [s["person_id"] for s in detail["submissions"]]
        assert seed.jia_h_id in pids, "批次明细仍应包含被排除学生"
    finally:
        _put_exclusion(client, seed, seed.jia_h_id, False)


def test_toggle_validation(client, hw_seed):
    seed = hw_seed
    # 名册外 person → 422 点名 person_id
    resp = _put_exclusion(client, seed, 99999, True)
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_scope_param"
    assert resp.json()["person_id"] == 99999
    # teaching 并集缺省 → 422 引导先选班
    resp = client.get("/api/v1/homework/stats-exclusion", params={
        "mode": "teaching", "academic_year_id": seed.ay_id,
    })
    assert resp.status_code == 422
    assert resp.json()["param"] == "teaching_class_id"
    # 非法 mode → 422
    resp = client.get("/api/v1/homework/stats-exclusion", params={"mode": "other"})
    assert resp.status_code == 422


def test_teaching_exclusion_is_class_scoped(client, v1_seed):
    """排除按班登记：T6 排除甲·T 不影响 T8 的预警；跨域共享批次在班主任
    看板同样按写域排除生效。"""
    seed = v1_seed
    # 打开作业共享类别，T6 物理批次才能投影进班主任看板
    resp = client.post(
        f"/api/v1/shared/links/{seed.link_id}/share-scope",
        json={"share_categories": ["roster", "current_subject_score",
                                   "current_subject_homework"]},
    )
    assert resp.status_code == 200, resp.text
    for tc_id, missing_name, date in (
        (seed.t6_id, "秦甲·T", "2025-09-10"),
        (seed.t8_id, "秦甲", "2025-09-10"),
    ):
        r = client.post("/api/v1/homework/preview", json={
            "mode": "teaching", "teaching_class_id": tc_id,
            "academic_year_id": seed.ay_id, "subject": "物理",
            "homework_type": "练习册", "assigned_date": date,
            "input": {"kind": "full", "exceptions": [
                {"name_or_alias": missing_name, "status": "missing"},
            ]},
        })
        assert r.status_code == 200, r.text
        _confirm(client, r.json()["token"])

    resp = client.put("/api/v1/homework/stats-exclusion", json={
        "mode": "teaching", "teaching_class_id": seed.t6_id,
        "academic_year_id": seed.ay_id, "person_id": seed.jia_t_id,
        "excluded": True,
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["class_ref_id"] == seed.t6_id

    # T6 预警无甲·T；T8 预警仍有秦甲（戊，同名样本）
    for tc_id, expect in ((seed.t6_id, []), (seed.t8_id, ["秦甲"])):
        resp = client.get("/api/v1/homework/warnings", params={
            "mode": "teaching", "teaching_class_id": tc_id,
            "academic_year_id": seed.ay_id, "min_missing": 1,
        })
        assert resp.status_code == 200, resp.text
        names = [s["name"] for s in resp.json()["students"]]
        assert names == expect, f"tc={tc_id}: {names}"

    # 班主任经共享投影读 T6 物理批次：甲被写域排除，看板不再计其缺交
    resp = client.get("/api/v1/homework/dashboard", params={
        "mode": "homeroom", "class_id": seed.h6_id,
        "academic_year_id": seed.ay_id, "group_by": "month", "subject": "物理",
    })
    assert resp.status_code == 200, resp.text
    groups = resp.json()["groups"]
    assert groups, "应看到共享的物理批次"
    assert groups[0]["missing"] == 0, "甲·T 的缺交应被排除"
    assert groups[0]["expected_count"] == 1, "投影应交只剩乙"
