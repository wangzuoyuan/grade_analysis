"""P5 H03：读取侧分母 / 名单漂移 / 未知状态 + 看板与事件流
（契约 docs/contracts/p5-homework.md §1.3/§2）。

- 仅缺交历史、无可靠分母的批次 → rate null + rate_unavailable（不推断全交）。
- preview 之后成员漂移 → confirm 409 零写入。
- unknown 学生 streak 置 null（不冒充连续）；submitted 打断连续。
"""

from datetime import date

from app.db import workspace_models as wm


def _preview(client, seed, **over):
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
    return client.post("/api/v1/homework/preview", json=payload)


def _confirm(client, token):
    return client.post("/api/v1/homework/confirm", json={"token": token})


def test_h03_missing_only_batch_rate_unavailable(client, v1_seed):
    """空名册教学班（T-empty）建批次：快照为空 → 分母不可用，绝不推断全交。"""
    p = client.post(
        "/api/v1/homework/preview",
        json={
            "mode": "teaching",
            "teaching_class_id": v1_seed.t_empty_id,
            "academic_year_id": v1_seed.ay_id,
            "subject": "物理",
            "homework_type": "练习册",
            "assigned_date": "2025-09-10",
            "input": {"kind": "full"},
        },
    )
    assert p.status_code == 200, p.text
    assert p.json()["assignment"]["expected_members"] == []

    c = _confirm(client, p.json()["token"])
    assert c.status_code == 200, c.text
    counts = c.json()
    assert (counts["submitted"], counts["missing"], counts["excused"], counts["unknown"]) == (
        0, 0, 0, 0,
    )
    aid = counts["assignment_id"]

    listing = client.get(
        "/api/v1/homework/assignments",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t_empty_id,
                "academic_year_id": v1_seed.ay_id},
    )
    assert listing.status_code == 200
    items = [i for i in listing.json()["items"] if i["assignment_id"] == aid]
    assert len(items) == 1
    assert items[0]["expected_count"] == 0
    assert items[0]["submission_rate"] is None
    assert items[0]["rate_unavailable"] is True

    board = client.get(
        "/api/v1/homework/dashboard",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t_empty_id,
                "academic_year_id": v1_seed.ay_id, "group_by": "week"},
    ).json()
    assert board["basis"] == "day"
    assert board["groups"][0]["submission_rate"] is None
    assert board["groups"][0]["rate_unavailable"] is True


def test_h03_member_drift_after_preview_confirms_409(client, v1_seed, db_session):
    """preview 后名册漂移（新成员入班）→ confirm 409 零写入。"""
    p = _preview(client, v1_seed, assigned_date="2025-09-15")
    token = p.json()["token"]

    newcomer = wm.WsStudentIdentity(data_domain="homeroom", display_name="秦新")
    db_session.add(newcomer)
    db_session.flush()
    db_session.add(
        wm.Enrollment(
            admin_class_id=v1_seed.h6_id,
            identity_id=newcomer.id,
            status="active",
            valid_from=date(2025, 9, 1),
        )
    )
    db_session.commit()

    resp = _confirm(client, token)
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"] == "link_version_conflict"
    assert "member_person_ids" in body["drift"]

    listing = client.get(
        "/api/v1/homework/assignments",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "from_date": "2025-09-15", "to_date": "2025-09-15"},
    ).json()
    assert listing["total"] == 0  # 漂移拒绝零写入


def test_h03_unknown_breaks_current_streak(client, v1_seed):
    """missing-unknown-missing：current 置 null 且 basis='unknown'，
    longest 只计两段真实的连续缺交（各 1），不跨 unknown 冒充连续。"""
    rows = [
        ("2025-09-01", "missing"),
        ("2025-09-02", "unknown"),
        ("2025-09-03", "missing"),
    ]
    for day, status in rows:
        p = _preview(
            client, v1_seed,
            assigned_date=day,
            input={"kind": "detailed",
                   "rows": [{"name_or_alias": "秦甲", "status": status}]},
        )
        assert _confirm(client, p.json()["token"]).status_code == 200

    resp = client.get(
        f"/api/v1/homework/students/{v1_seed.jia_h_id}",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [e["assigned_date"] for e in body["events"]] == [
        "2025-09-01", "2025-09-02", "2025-09-03",
    ]
    assert [e["status"] for e in body["events"]] == ["missing", "unknown", "missing"]
    streaks = body["streaks"]
    assert streaks["current_missing_streak"] is None
    assert streaks["streak_basis"] == "unknown"
    assert streaks["longest_missing_streak"] == 1


def test_h03_submitted_breaks_and_counts_streak(client, v1_seed):
    """对照：连续 missing 计数；submitted 打断（current 归零）。"""
    for day, status in (
        ("2025-09-05", "missing"),
        ("2025-09-06", "missing"),
        ("2025-09-07", "submitted"),
    ):
        p = _preview(
            client, v1_seed,
            assigned_date=day,
            input={"kind": "detailed",
                   "rows": [{"name_or_alias": "秦丙", "status": status}]},
        )
        assert _confirm(client, p.json()["token"]).status_code == 200

    body = client.get(
        f"/api/v1/homework/students/{v1_seed.bing_h_id}",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id},
    ).json()
    assert body["streaks"]["current_missing_streak"] == 0
    assert body["streaks"]["streak_basis"] == "events"
    assert body["streaks"]["longest_missing_streak"] == 2


def test_dashboard_groups_week_and_month(client, v1_seed):
    """看板按月（月首）/按周（ISO 周一）聚合，rate 按批次聚合分母。"""
    for day, homework_type in (("2025-09-10", "试卷订正"), ("2025-10-10", "试卷订正")):
        p = _preview(client, v1_seed, assigned_date=day, homework_type=homework_type)
        assert _confirm(client, p.json()["token"]).status_code == 200

    month = client.get(
        "/api/v1/homework/dashboard",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "group_by": "month", "homework_type": "试卷订正"},
    ).json()
    labels = [g["label"] for g in month["groups"]]
    assert labels == ["2025-09-01", "2025-10-01"]
    assert all(g["assignments"] == 1 for g in month["groups"])
    assert all(g["submission_rate"] == 1.0 for g in month["groups"])
    assert all(g["rate_unavailable"] is False for g in month["groups"])

    week = client.get(
        "/api/v1/homework/dashboard",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "group_by": "week", "homework_type": "试卷订正"},
    ).json()
    # 2025-09-10 为周三 → ISO 周一 2025-09-08；10-10 为周五 → 2025-10-06
    assert [g["label"] for g in week["groups"]] == ["2025-09-08", "2025-10-06"]


def test_assignment_list_filters(client, v1_seed):
    """列表筛选：subject / homework_type / from_date / to_date。"""
    p1 = _preview(client, v1_seed, assigned_date="2025-09-10", homework_type="练习册")
    p2 = _preview(client, v1_seed, assigned_date="2025-11-10", homework_type="周末作业")
    for p in (p1, p2):
        assert _confirm(client, p.json()["token"]).status_code == 200

    base = {"mode": "homeroom", "class_id": v1_seed.h6_id}
    only_weekend = client.get(
        "/api/v1/homework/assignments", params={**base, "homework_type": "周末作业"}
    ).json()
    assert {i["homework_type"] for i in only_weekend["items"]} == {"周末作业"}

    november = client.get(
        "/api/v1/homework/assignments",
        params={**base, "from_date": "2025-11-01", "to_date": "2025-11-30"},
    ).json()
    assert [i["assigned_date"] for i in november["items"]] == ["2025-11-10"]

    math_none = client.get(
        "/api/v1/homework/assignments", params={**base, "subject": "数学"}
    ).json()
    assert math_none["total"] == 0


def test_detail_patch_due_date_revoke_flow(client, v1_seed):
    """详情 → PATCH due_date（批次级编辑不产生行依赖）→ 撤销成功；
    撤销后 detail status=revoked，existing_batches 不再计入（指标即时重算）。
    （行级评价编辑的撤销阻塞见 test_revoke_conflict_lists_edited_submissions。）"""
    p = _preview(client, v1_seed, assigned_date="2025-09-18", homework_type="背诵默写")
    aid = _confirm(client, p.json()["token"]).json()["assignment_id"]
    params = {"mode": "homeroom", "class_id": v1_seed.h6_id}

    patched = client.patch(
        f"/api/v1/homework/assignments/{aid}",
        params=params,
        json={"revision": 1, "due_date": "2025-09-21"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["revision"] == 2

    detail = client.get(f"/api/v1/homework/assignments/{aid}", params=params).json()
    assert detail["due_date"] == "2025-09-21"

    revoked = client.delete(f"/api/v1/homework/assignments/{aid}", params=params)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"

    detail = client.get(f"/api/v1/homework/assignments/{aid}", params=params).json()
    assert detail["status"] == "revoked"

    again = _preview(client, v1_seed, assigned_date="2025-09-18", homework_type="背诵默写")
    assert again.json()["existing_batches"] == []


def test_revoke_conflict_lists_edited_submissions(client, v1_seed):
    """撤销前存在晚于批次创建的评价编辑 → 409 列冲突清单，不覆盖。"""
    p = _preview(client, v1_seed, assigned_date="2025-09-19", homework_type="练习册")
    aid = _confirm(client, p.json()["token"]).json()["assignment_id"]
    params = {"mode": "homeroom", "class_id": v1_seed.h6_id}

    edited = client.patch(
        f"/api/v1/homework/assignments/{aid}",
        params=params,
        json={
            "revision": 1,
            "rows": [{"name_or_alias": "秦甲", "status": "submitted",
                      "evaluation": "补交"}],
        },
    )
    assert edited.status_code == 200

    resp = client.delete(f"/api/v1/homework/assignments/{aid}", params=params)
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"] == "link_version_conflict"
    conflicts = body["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["person_id"] == v1_seed.jia_h_id
    assert conflicts[0]["name"] == "秦甲"
