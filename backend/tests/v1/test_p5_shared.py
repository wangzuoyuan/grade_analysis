"""P5 H04 双向共享 + H05 跨学年历史（契约 docs/contracts/p5-homework.md §2）。

- 共享门：无 current_subject_homework 类别时 teaching 看不到 H 批次；
  开类别后同一 assignment_id 经 LinkedStudent 交集投影（甲乙各 2 人）。
- T8 与 H6 无关联，其批次对 homeroom 永不可见。
- 双向 PATCH：跨域行按读域名字解析、映射回发起域 person_id（同一事实）；
  revision 乐观锁跨域一致。
- 关闭类别后跨域批次立即从两侧列表/事件流消失。
- H05：事件流跟人不跟学年（跨学年 active 批次聚合）；streaks 按日期升序
  重算。person 不在作用域名册 → 404。

注意模块内测试共享 DB 且用例顺序即状态推进（开门 → 双向写 → 关门 →
历史 → 隔离），不得调整顺序。
"""

import json
from datetime import date

from app.db import workspace_models as wm

BASE = "/api/v1"


def _h_preview(client, seed, **over):
    payload = {
        "mode": "homeroom",
        "class_id": seed.h6_id,
        "academic_year_id": seed.ay_id,
        "subject": "物理",
        "homework_type": "练习册",
        "assigned_date": "2025-09-10",
        "input": {"kind": "full"},
    }
    payload.update(over)
    return client.post(f"{BASE}/homework/preview", json=payload)


def _t_preview(client, seed, tc_id, **over):
    payload = {
        "mode": "teaching",
        "teaching_class_id": tc_id,
        "academic_year_id": seed.ay_id,
        "subject": "物理",
        "homework_type": "练习册",
        "assigned_date": "2025-09-11",
        "input": {"kind": "full"},
    }
    payload.update(over)
    return client.post(f"{BASE}/homework/preview", json=payload)


def _confirm(client, token):
    return client.post(f"{BASE}/homework/confirm", json={"token": token})


def _set_scope(client, seed, categories):
    resp = client.post(
        f"{BASE}/shared/links/{seed.link_id}/share-scope",
        json={"share_categories": categories},
    )
    assert resp.status_code == 200, resp.text


def _list(client, seed, mode, **params):
    query = {"mode": mode, "academic_year_id": seed.ay_id, **params}
    return client.get(f"{BASE}/homework/assignments", params=query)


def test_h04_teaching_cannot_see_before_category(client, v1_seed):
    """未开 current_subject_homework：teaching 列表/事件流无 H 批次。"""
    p = _h_preview(client, v1_seed)
    assert p.status_code == 200, p.text
    h_aid = _confirm(client, p.json()["token"]).json()["assignment_id"]

    listing = _list(
        client, v1_seed, "teaching", teaching_class_id=v1_seed.t6_id,
        from_date="2025-09-01", to_date="2025-09-30",
    )
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 0

    events = client.get(
        f"{BASE}/homework/students/{v1_seed.jia_t_id}",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t6_id},
    ).json()
    assert events["events"] == []


def test_h04_open_scope_projects_h_batch_to_teaching(client, v1_seed):
    """开类别后：teaching 看到同一 assignment_id，期望/提交按 LinkedStudent
    交集投影为甲乙 2 人；T8 批次对 homeroom 永不可见（无关联班）。"""
    _set_scope(client, v1_seed, ["roster", "current_subject_score",
                                 "current_subject_homework"])

    # 造 T8 批次（与 H6 无关联）
    p8 = _t_preview(client, v1_seed, v1_seed.t8_id, assigned_date="2025-09-12")
    assert p8.status_code == 200, p8.text
    t8_aid = _confirm(client, p8.json()["token"]).json()["assignment_id"]

    listing = _list(
        client, v1_seed, "teaching", teaching_class_id=v1_seed.t6_id,
        from_date="2025-09-01", to_date="2025-09-30",
    )
    items = listing.json()["items"]
    assert [i["assignment_id"] for i in items] != []
    h_item = next(i for i in items if i["assigned_date"] == "2025-09-10")
    assert all(i["assignment_id"] != t8_aid for i in items)  # T6 视角不见 T8

    detail = client.get(
        f"{BASE}/homework/assignments/{h_item['assignment_id']}",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t6_id,
                "academic_year_id": v1_seed.ay_id},
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    # 投影分母 = LinkedStudent 交集（甲乙），丙不在交集
    assert body["expected_count"] == 2
    assert body["submission_rate"] == 1.0
    assert {s["person_id"] for s in body["submissions"]} == {
        v1_seed.jia_t_id, v1_seed.yi_t_id,
    }
    assert {m["person_id"] for m in body["expected_members"]} == {
        v1_seed.jia_t_id, v1_seed.yi_t_id,
    }

    # homeroom 列表永不见 T8 批次
    h_list = _list(
        client, v1_seed, "homeroom", class_id=v1_seed.h6_id,
        from_date="2025-09-01", to_date="2025-09-30",
    ).json()
    assert all(i["assignment_id"] != t8_aid for i in h_list["items"])


def test_h04_teaching_batch_projects_to_homeroom_and_event_streams(client, v1_seed):
    """反向：teaching 建 T6 批次，homeroom 经映射可见；两侧学生事件流
    均含两域批次且按日期升序。"""
    p = _t_preview(client, v1_seed, v1_seed.t6_id)
    assert p.status_code == 200, p.text
    t_aid = _confirm(client, p.json()["token"]).json()["assignment_id"]

    h_list = _list(
        client, v1_seed, "homeroom", class_id=v1_seed.h6_id,
        from_date="2025-09-01", to_date="2025-09-30",
    ).json()
    assert any(i["assignment_id"] == t_aid for i in h_list["items"])

    h_events = client.get(
        f"{BASE}/homework/students/{v1_seed.jia_h_id}",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id},
    ).json()
    assert [e["assigned_date"] for e in h_events["events"]] == [
        "2025-09-10", "2025-09-11",
    ]
    assert all(e["status"] == "submitted" for e in h_events["events"])

    t_events = client.get(
        f"{BASE}/homework/students/{v1_seed.jia_t_id}",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t6_id},
    ).json()
    assert [e["assigned_date"] for e in t_events["events"]] == [
        "2025-09-10", "2025-09-11",
    ]


def test_h04_bidirectional_patch_and_optimistic_lock(client, v1_seed):
    """跨域 PATCH：按读域名字解析后映射回发起域 person_id；revision
    递增；旧 revision 409；两侧看到同一状态（同一事实，不复制）。"""
    listing = _list(
        client, v1_seed, "teaching", teaching_class_id=v1_seed.t6_id,
        from_date="2025-09-10", to_date="2025-09-10",
    ).json()
    h_aid = listing["items"][0]["assignment_id"]
    listing_t = _list(
        client, v1_seed, "homeroom", class_id=v1_seed.h6_id,
        from_date="2025-09-11", to_date="2025-09-11",
    ).json()
    t_aid = listing_t["items"][0]["assignment_id"]

    # teaching 侧改 H 批次的甲·T（读域=teaching，写回 homeroom init id）
    patch_t = client.patch(
        f"{BASE}/homework/assignments/{h_aid}",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t6_id,
                "academic_year_id": v1_seed.ay_id},
        json={"revision": 1,
              "rows": [{"name_or_alias": "秦甲·T", "status": "missing"}]},
    )
    assert patch_t.status_code == 200, patch_t.text
    assert (patch_t.json()["revision"], patch_t.json()["updated"]) == (2, 1)

    stale = client.patch(
        f"{BASE}/homework/assignments/{h_aid}",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t6_id,
                "academic_year_id": v1_seed.ay_id},
        json={"revision": 1,
              "rows": [{"name_or_alias": "秦甲·T", "status": "submitted"}]},
    )
    assert stale.status_code == 409
    assert stale.json()["error"] == "link_version_conflict"

    # homeroom 侧看同一批次：甲 missing（投影回 jia_h）
    h_detail = client.get(
        f"{BASE}/homework/assignments/{h_aid}",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "academic_year_id": v1_seed.ay_id},
    ).json()
    by_pid = {s["person_id"]: s["status"] for s in h_detail["submissions"]}
    assert by_pid[v1_seed.jia_h_id] == "missing"

    # 反向：homeroom 侧改 T 批次的秦乙（读域=homeroom → yi_t）
    patch_h = client.patch(
        f"{BASE}/homework/assignments/{t_aid}",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "academic_year_id": v1_seed.ay_id},
        json={"revision": 1,
              "rows": [{"name_or_alias": "秦乙", "status": "excused"}]},
    )
    assert patch_h.status_code == 200, patch_h.text
    assert patch_h.json()["revision"] == 2

    t_detail = client.get(
        f"{BASE}/homework/assignments/{t_aid}",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t6_id,
                "academic_year_id": v1_seed.ay_id},
    ).json()
    by_pid_t = {s["person_id"]: s["status"] for s in t_detail["submissions"]}
    assert by_pid_t[v1_seed.yi_t_id] == "excused"


def test_h04_close_scope_hides_cross_batches(client, v1_seed):
    """关类别：跨域批次立即从两侧列表与事件流消失（门在读取时生效）。"""
    _set_scope(client, v1_seed, ["roster", "current_subject_score"])

    t_list = _list(
        client, v1_seed, "teaching", teaching_class_id=v1_seed.t6_id,
        from_date="2025-09-01", to_date="2025-09-30",
    ).json()
    # 本域 T 批次保留，跨域 H 批次（09-10）消失
    assert {i["assigned_date"] for i in t_list["items"]} == {"2025-09-11"}

    h_list = _list(
        client, v1_seed, "homeroom", class_id=v1_seed.h6_id,
        from_date="2025-09-01", to_date="2025-09-30",
    ).json()
    assert {i["assigned_date"] for i in h_list["items"]} == {"2025-09-10"}

    h_events = client.get(
        f"{BASE}/homework/students/{v1_seed.jia_h_id}",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id},
    ).json()
    assert [e["assigned_date"] for e in h_events["events"]] == ["2025-09-10"]

    t_events = client.get(
        f"{BASE}/homework/students/{v1_seed.jia_t_id}",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t6_id},
    ).json()
    assert [e["assigned_date"] for e in t_events["events"]] == ["2025-09-11"]


def test_h05_cross_year_history_follows_person(client, v1_seed, db_session):
    """H05：事件流跟人跟全部学年——上一学年（2024-2025）的缺交与本届
    批次聚合（含跨域写回的本域行）；streaks 按 assigned_date 升序重算。
    （关门后 T 批次事件不可见，见上一用例。）"""
    ay2 = wm.AcademicYear()
    ay2.name = "2024-2025"
    ay2.start_date = date(2024, 9, 1)
    ay2.end_date = date(2025, 7, 15)
    db_session.add(ay2)
    db_session.flush()
    assignment = wm.HomeworkAssignment(
        data_domain="homeroom",
        class_ref_id=v1_seed.h6_id,
        academic_year_id=ay2.id,
        subject="物理",
        homework_type="练习册",
        assigned_date=date(2024, 10, 8),
        batch_token="p5-h05-ay2",
        expected_members_json=json.dumps([v1_seed.jia_h_id]),
    )
    db_session.add(assignment)
    db_session.flush()
    db_session.add(
        wm.HomeworkSubmission(
            assignment_id=assignment.id,
            person_id=v1_seed.jia_h_id,
            submission_status="missing",
            revision=1,
        )
    )
    # 学号别名随学年换号（2024H6-01）：验证可落库、不与 2025 号冲突
    alias = wm.WsStudentAlias()
    alias.identity_id = v1_seed.jia_h_id
    alias.alias_value = "2024H6-01"
    alias.data_domain = "homeroom"
    alias.link_source = "p5-test"
    db_session.add(alias)
    db_session.commit()

    resp = client.get(
        f"{BASE}/homework/students/{v1_seed.jia_h_id}",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 关门状态（上一用例已收窄共享范围）：本域直读跨全部学年——
    # 2024 缺交 + 2025-09-10（teaching PATCH 写回的本域行，missing）
    assert [e["assigned_date"] for e in body["events"]] == [
        "2024-10-08", "2025-09-10",
    ]
    assert [e["status"] for e in body["events"]] == ["missing", "missing"]
    streaks = body["streaks"]
    assert streaks["current_missing_streak"] == 2
    assert streaks["longest_missing_streak"] == 2
    assert streaks["streak_basis"] == "events"


def test_h04_scope_isolation_rejects_foreign_persons(client, v1_seed):
    """隔离红线：teaching 域 person 不在 homeroom 名册（404）、T8 成员
    不在 T6 名册（404）——不因同名或关联存在而放行。"""
    resp_h = client.get(
        f"{BASE}/homework/students/{v1_seed.ding_t_id}",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id},
    )
    assert resp_h.status_code == 404, resp_h.text
    assert resp_h.json()["error"] == "resource_out_of_scope"

    resp_t = client.get(
        f"{BASE}/homework/students/{v1_seed.wu_t_id}",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t6_id},
    )
    assert resp_t.status_code == 404, resp_t.text
    assert resp_t.json()["error"] == "resource_out_of_scope"
