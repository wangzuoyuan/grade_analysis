"""UX-HW02：出勤/评价正交、旧批次时间轴与教学班生命周期行为回归。"""

from datetime import date

from app.db import workspace_models as wm


API = "/api/v1"


def _preview_confirm(client, seed, day, rows, *, mode="homeroom", class_id=None):
    teaching = mode == "teaching"
    payload = {
        "mode": mode,
        "academic_year_id": seed.ay_id,
        "subject": "物理",
        "homework_type": "练习册",
        "assigned_date": day,
        "input": {"kind": "detailed", "rows": rows},
    }
    payload["teaching_class_id" if teaching else "class_id"] = class_id or (
        seed.t8_id if teaching else seed.h6_id
    )
    preview = client.post(f"{API}/homework/preview", json=payload)
    assert preview.status_code == 200, preview.text
    confirmed = client.post(f"{API}/homework/confirm", json={"token": preview.json()["token"]})
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()["assignment_id"]


def test_attendance_is_independent_and_default_status_is_submitted(client, v1_seed):
    aid = _preview_confirm(
        client,
        v1_seed,
        "2025-10-10",
        [{"name_or_alias": "秦甲", "status": "submitted", "attendance": "没来", "evaluation": "差"}],
    )
    params = {"mode": "homeroom", "class_id": v1_seed.h6_id}
    detail = client.get(f"{API}/homework/assignments/{aid}", params=params).json()
    row = next(item for item in detail["submissions"] if item["person_id"] == v1_seed.jia_h_id)
    assert (row["status"], row["attendance"], row["quality_negative"]) == ("submitted", "没来", True)
    assert (detail["submitted"], detail["missing"], detail["excused"]) == (3, 0, 0)
    assert "unknown" not in detail
    listed = client.get(f"{API}/homework/assignments", params=params).json()["items"]
    list_row = next(item for item in listed if item["assignment_id"] == aid)
    assert (list_row["attendance_count"], list_row["negative_count"]) == (1, 1)

    cleared = client.patch(
        f"{API}/homework/assignments/{aid}",
        params=params,
        json={
            "revision": detail["revision"],
            "rows": [{"person_id": v1_seed.jia_h_id, "status": "submitted", "attendance": None, "evaluation": "差"}],
        },
    )
    assert cleared.status_code == 200, cleared.text
    detail2 = client.get(f"{API}/homework/assignments/{aid}", params=params).json()
    row2 = next(item for item in detail2["submissions"] if item["person_id"] == v1_seed.jia_h_id)
    assert (row2["status"], row2["attendance"], row2["quality_negative"]) == ("submitted", None, True)
    assert detail2["submitted"] == 3


def test_quality_streak_independent_and_attendance_defaults_to_submitted(client, v1_seed):
    for day, evaluation in (("2025-10-11", "作业乱"), ("2025-10-12", "错误率高")):
        _preview_confirm(client, v1_seed, day, [{"name_or_alias": "秦乙", "status": "submitted", "evaluation": evaluation}])
    for day, row in (
        ("2025-10-13", {"name_or_alias": "秦丙", "status": "missing"}),
        ("2025-10-14", {"name_or_alias": "秦丙", "status": "unknown", "attendance": "迟到"}),
        ("2025-10-15", {"name_or_alias": "秦丙", "status": "missing"}),
    ):
        _preview_confirm(client, v1_seed, day, [row])

    warning = client.get(
        f"{API}/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "subject": "物理", "min_missing": 2},
    )
    assert warning.status_code == 200, warning.text
    body = warning.json()
    quality = next(item for item in body["quality"] if item["person_id"] == v1_seed.yi_h_id)
    assert quality["count"] == 2
    bing = next(item for item in body["students"] if item["person_id"] == v1_seed.bing_h_id)
    assert bing["current_streak"] == 1


def test_legacy_empty_snapshot_uses_event_date_roster_for_default_submitted(client, v1_seed, db_session):
    assignment = wm.HomeworkAssignment(
        data_domain="homeroom",
        class_ref_id=v1_seed.h6_id,
        academic_year_id=v1_seed.ay_id,
        subject="物理",
        homework_type="legacy",
        assigned_date=date(2025, 10, 20),
        batch_token="migration:h:test-empty-expected",
        expected_members_json="[]",
        status="active",
    )
    db_session.add(assignment)
    db_session.commit()

    params = {"mode": "homeroom", "class_id": v1_seed.h6_id}
    detail = client.get(f"{API}/homework/assignments/{assignment.id}", params=params)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["expected_count"] == 3
    assert body["submitted"] == 3
    assert body["submission_rate"] == 1.0 and body["rate_unavailable"] is False
    assert {row["person_id"] for row in body["submissions"]} == set(v1_seed.h_person_ids)

    patched = client.patch(
        f"{API}/homework/assignments/{assignment.id}",
        params=params,
        json={"revision": body["revision"], "rows": [{"person_id": v1_seed.jia_h_id, "status": "submitted"}]},
    )
    assert patched.status_code == 200, patched.text
    after = client.get(f"{API}/homework/assignments/{assignment.id}", params=params).json()
    assert after["expected_count"] == 3 and after["rate_unavailable"] is False
    assert next(row for row in after["submissions"] if row["person_id"] == v1_seed.jia_h_id)["status"] == "submitted"


def test_legacy_full_collection_breaks_homeroom_streak(client, v1_seed, db_session):
    def legacy(day, token, status=None):
        assignment = wm.HomeworkAssignment(
            data_domain="homeroom", class_ref_id=v1_seed.h6_id,
            academic_year_id=v1_seed.ay_id, subject="化学", homework_type="legacy",
            assigned_date=date.fromisoformat(day), batch_token=token,
            expected_members_json="[]", status="active",
        )
        db_session.add(assignment); db_session.flush()
        if status:
            db_session.add(wm.HomeworkSubmission(
                assignment_id=assignment.id, person_id=v1_seed.jia_h_id,
                submission_status=status,
            ))

    legacy("2025-11-01", "migration:h:legacy-miss-1", "missing")
    legacy("2025-11-02", "migration:h:collection:test-timeline")
    legacy("2025-11-03", "migration:h:legacy-miss-2", "missing")
    db_session.commit()
    body = client.get(
        f"{API}/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "subject": "化学", "min_missing": 2},
    ).json()
    row = next(item for item in body["students"] if item["person_id"] == v1_seed.jia_h_id)
    assert row["current_streak"] == 1
    assert row["streak_basis"] == "legacy_events"


def test_other_student_missing_advances_homeroom_collection_axis(client, v1_seed, db_session):
    for index, (day, person_id) in enumerate((
        ("2025-11-11", v1_seed.jia_h_id),
        ("2025-11-12", v1_seed.yi_h_id),
        ("2025-11-13", v1_seed.jia_h_id),
    ), start=1):
        assignment = wm.HomeworkAssignment(
            data_domain="homeroom", class_ref_id=v1_seed.h6_id,
            academic_year_id=v1_seed.ay_id, subject="英语", homework_type="legacy",
            assigned_date=date.fromisoformat(day), batch_token=f"migration:h:individual:{index}",
            expected_members_json="[]", status="active",
        )
        db_session.add(assignment); db_session.flush()
        db_session.add(wm.HomeworkSubmission(
            assignment_id=assignment.id, person_id=person_id, submission_status="missing",
        ))
    db_session.commit()
    body = client.get(
        f"{API}/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "subject": "英语", "min_missing": 2},
    ).json()
    row = next(item for item in body["students"] if item["person_id"] == v1_seed.jia_h_id)
    # 旧 H 只存缺交者：同班同学科当日有人缺交即证明收过作业，甲未缺交，
    # 因而 11-12 会中断甲的连续段（旧源码 homework/service.py 293-346）。
    assert row["current_streak"] == 1


def test_daily_forgot_entry_is_counted_once_and_can_be_cleared(client, v1_seed):
    assignment_ids = []
    for day in ("2025-10-16", "2025-10-17", "2025-10-18"):
        assignment_ids.append(_preview_confirm(
            client, v1_seed, day,
            [{"name_or_alias": "秦甲", "status": "submitted", "evaluation": "忘带作业本"}],
        ))
    params = {"mode": "homeroom", "class_id": v1_seed.h6_id, "subject": "物理", "min_missing": 1}
    before = client.get(f"{API}/homework/warnings", params=params).json()
    row = next(item for item in before["forgot"] if item["person_id"] == v1_seed.jia_h_id)
    assert row["count"] == 3

    detail = client.get(
        f"{API}/homework/assignments/{assignment_ids[-1]}",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id},
    ).json()
    cleared = client.patch(
        f"{API}/homework/assignments/{assignment_ids[-1]}",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id},
        json={"revision": detail["revision"], "rows": [
            {"person_id": v1_seed.jia_h_id, "status": "submitted", "evaluation": None}
        ]},
    )
    assert cleared.status_code == 200, cleared.text
    after = client.get(f"{API}/homework/warnings", params=params).json()
    assert all(item["person_id"] != v1_seed.jia_h_id for item in after["forgot"])


def test_semester_bounds_apply_to_all_warnings_and_private_notes_do_not_count(client, v1_seed, db_session):
    db_session.add(wm.WsHomeworkSemester(
        academic_year_id=v1_seed.ay_id, name="测试第一学期",
        start_date=date(2025, 10, 1), end_date=date(2025, 10, 31),
        is_current=1, mode="manual",
    ))
    for index, day in enumerate(("2025-10-02", "2025-10-03", "2025-11-01"), start=1):
        _preview_confirm(client, v1_seed, day, [{"name_or_alias": "秦丙", "status": "submitted", "evaluation": "差"}])
    for index, day in enumerate(("2025-10-04", "2025-10-05", "2025-10-06"), start=1):
        db_session.add(wm.WsStudentNote(
            data_domain="homeroom", person_id=v1_seed.yi_h_id,
            date=date.fromisoformat(day), category="其他", content=f"[忘带] 第{index}次",
            source="migration:h",
        ))
    for index, day in enumerate(("2025-10-07", "2025-10-08", "2025-10-09"), start=1):
        db_session.add(wm.WsStudentNote(
            data_domain="homeroom", person_id=v1_seed.jia_h_id,
            date=date.fromisoformat(day), category="谈话", content=f"谈话提到忘带第{index}次",
            source="manual",
        ))
    db_session.commit()

    body = client.get(
        f"{API}/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "subject": "物理", "min_missing": 1},
    ).json()
    assert body["quality"], body
    quality_row = next(item for item in body["quality"] if item["person_id"] == v1_seed.bing_h_id)
    assert quality_row["count"] == 2
    assert "2025-11-01" not in quality_row["dates"]
    assert [item["person_id"] for item in body["forgot"]] == [v1_seed.yi_h_id]
    assert body["forgot"][0]["count"] == 3
    dashboard = client.get(
        f"{API}/homework/dashboard",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "subject": "物理", "group_by": "month"},
    )
    assert dashboard.status_code == 200, dashboard.text
    assert {group["label"] for group in dashboard.json()["groups"]} == {"2025-10-01"}


def test_other_batch_defaults_to_submitted_and_breaks_teaching_streak(client, v1_seed, db_session):
    def event(day, token, person_id, status):
        assignment = wm.HomeworkAssignment(
            data_domain="teaching", class_ref_id=v1_seed.t8_id,
            academic_year_id=v1_seed.ay_id, subject="物理", homework_type="专题练习",
            assigned_date=date.fromisoformat(day), batch_token=token,
            expected_members_json="[]", status="active",
        )
        db_session.add(assignment); db_session.flush()
        db_session.add(wm.HomeworkSubmission(
            assignment_id=assignment.id, person_id=person_id,
            submission_status=status,
        ))

    event("2025-10-21", "migration:t:own-miss-1", v1_seed.wu_t_id, "missing")
    event("2025-10-22", "migration:t:other-submit", v1_seed.ji_t_id, "submitted")
    event("2025-10-23", "migration:t:own-miss-2", v1_seed.wu_t_id, "missing")
    db_session.commit()
    body = client.get(
        f"{API}/homework/warnings",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t8_id,
                "academic_year_id": v1_seed.ay_id, "subject": "物理",
                "homework_type": "专题练习", "min_missing": 2},
    ).json()
    row = next(item for item in body["students"] if item["person_id"] == v1_seed.wu_t_id)
    assert row["current_streak"] == 1


def test_all_taught_classes_keep_separate_legacy_streak_axes(client, v1_seed, db_session):
    def event(day, token, class_id, person_id):
        assignment = wm.HomeworkAssignment(
            data_domain="teaching", class_ref_id=class_id,
            academic_year_id=v1_seed.ay_id, subject="物理", homework_type="周末练习",
            assigned_date=date.fromisoformat(day), batch_token=token,
            expected_members_json="[]", status="active",
        )
        db_session.add(assignment); db_session.flush()
        db_session.add(wm.HomeworkSubmission(
            assignment_id=assignment.id, person_id=person_id, submission_status="missing",
        ))

    event("2025-10-26", "migration:t:t8-miss-1", v1_seed.t8_id, v1_seed.wu_t_id)
    event("2025-10-27", "migration:t:t6-unrelated", v1_seed.t6_id, v1_seed.jia_t_id)
    event("2025-10-28", "migration:t:t8-miss-2", v1_seed.t8_id, v1_seed.wu_t_id)
    db_session.commit()
    body = client.get(
        f"{API}/homework/warnings",
        params={"mode": "teaching", "academic_year_id": v1_seed.ay_id,
                "subject": "物理", "homework_type": "周末练习", "min_missing": 2},
    ).json()
    row = next(item for item in body["students"] if item["person_id"] == v1_seed.wu_t_id)
    assert row["current_streak"] == 2


def test_teaching_class_management_keeps_inactive_history_readable(client, v1_seed):
    created = client.post(
        f"{API}/teaching/classes",
        json={"academic_year_id": v1_seed.ay_id, "subject": "物理", "label": "高二新教学班"},
    )
    assert created.status_code == 200, created.text
    assert any(item["label"] == "高二新教学班" for item in created.json()["classes"])
    aid = _preview_confirm(
        client,
        v1_seed,
        "2025-10-25",
        [{"name_or_alias": "秦甲", "status": "submitted"}],
        mode="teaching",
        class_id=v1_seed.t8_id,
    )
    renamed = client.patch(
        f"{API}/teaching/classes/{v1_seed.t8_id}",
        json={"label": "高二8班（历史）"},
    )
    assert renamed.status_code == 200, renamed.text
    stopped = client.patch(
        f"{API}/teaching/classes/{v1_seed.t8_id}",
        json={"status": "inactive"},
    )
    assert stopped.status_code == 200, stopped.text
    assert next(item for item in stopped.json()["classes"] if item["class_id"] == v1_seed.t8_id)["status"] == "inactive"

    default_catalog = client.get(f"{API}/shared/classes", params={"academic_year_id": v1_seed.ay_id}).json()
    assert v1_seed.t8_id not in {item["class_id"] for item in default_catalog["teaching"]}
    history = client.get(
        f"{API}/homework/assignments/{aid}",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t8_id, "academic_year_id": v1_seed.ay_id, "subject": "物理"},
    )
    assert history.status_code == 200, history.text

    restored = client.patch(f"{API}/teaching/classes/{v1_seed.t8_id}", json={"status": "active"})
    assert restored.status_code == 200, restored.text


def test_last_inactive_class_still_has_management_and_explicit_history(client, v1_seed):
    managed = client.get(
        f"{API}/teaching/classes",
        params={"academic_year_id": v1_seed.ay_id, "subject": "物理"},
    ).json()["classes"]
    for item in managed:
        response = client.patch(
            f"{API}/teaching/classes/{item['class_id']}", json={"status": "inactive"},
        )
        assert response.status_code == 200, response.text

    catalog = client.get(f"{API}/shared/classes", params={"academic_year_id": v1_seed.ay_id})
    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["teaching"] == []
    management = client.get(f"{API}/teaching/classes", params={"academic_year_id": v1_seed.ay_id})
    assert management.status_code == 200, management.text
    assert all(item["status"] == "inactive" for item in management.json()["classes"])
    history = client.get(
        f"{API}/homework/assignments",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t8_id,
                "academic_year_id": v1_seed.ay_id, "subject": "物理"},
    )
    assert history.status_code == 200, history.text

    for item in management.json()["classes"]:
        restored = client.patch(
            f"{API}/teaching/classes/{item['class_id']}", json={"status": "active"},
        )
        assert restored.status_code == 200, restored.text
