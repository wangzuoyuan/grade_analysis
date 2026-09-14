"""P4 §3 教学班成员管理：查询、直接维护、两段导入与关联班同步。"""

from datetime import date


API = "/api/v1"


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def test_teaching_members_lists_active_roster(client, v1_seed):
    response = client.get(f"{API}/teaching/classes/{v1_seed.t6_id}/members")
    assert response.status_code == 200, response.text
    body = response.json()
    assert {row["person_id"] for row in body["active"]} == set(v1_seed.t6_person_ids)
    assert body["left"] == []
    assert all(row["source"] for row in body["active"])


def test_linked_class_rejects_direct_roster_changes(client, v1_seed):
    add = client.post(
        f"{API}/teaching/classes/{v1_seed.t6_id}/members",
        json={"name": "不应直加", "alias": "T-LINK-99"},
    )
    assert add.status_code == 409
    assert add.json()["error"] == "link_version_conflict"

    imported = client.post(
        f"{API}/teaching/classes/{v1_seed.t6_id}/members/import",
        json={"text": "T-LINK-98 不应直导"},
    )
    assert imported.status_code == 409

    removed = client.delete(
        f"{API}/teaching/classes/{v1_seed.t6_id}/members/{v1_seed.jia_t_id}"
    )
    assert removed.status_code == 409


def test_unlinked_class_manual_add_remove_and_two_phase_import(client, v1_seed):
    added = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members",
        json={"name": "秦新教", "alias": "2025T8-09"},
    )
    assert added.status_code == 200, added.text
    person_id = added.json()["person_id"]

    roster = client.get(f"{API}/teaching/classes/{v1_seed.t8_id}/members").json()
    assert person_id in {row["person_id"] for row in roster["active"]}

    removed = client.delete(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members/{person_id}"
    )
    assert removed.status_code == 200, removed.text
    roster = client.get(f"{API}/teaching/classes/{v1_seed.t8_id}/members").json()
    assert person_id in {row["person_id"] for row in roster["left"]}

    preview = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members/import",
        json={"text": "2025T8-10 秦导入甲\n秦导入乙"},
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert [row["kind"] for row in preview_body["lines"]] == ["new", "new"]

    confirmed = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members/import",
        json={"token": preview_body["token"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json() == {"added_count": 2, "invalid_count": 0}

    replay = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members/import",
        json={"token": preview_body["token"]},
    )
    assert replay.status_code == 409


def test_import_confirm_rejects_roster_drift(client, v1_seed):
    preview = client.post(
        f"{API}/teaching/classes/{v1_seed.t_empty_id}/members/import",
        json={"text": "T-DRIFT-01 秦漂移"},
    )
    assert preview.status_code == 200, preview.text

    from app.db import workspace_models as wm

    db = _db()
    try:
        identity = wm.WsStudentIdentity(data_domain="teaching", display_name="并发新增")
        db.add(identity)
        db.flush()
        db.add(
            wm.TeachingClassMember(
                teaching_class_id=v1_seed.t_empty_id,
                identity_id=identity.id,
                valid_from=date(2025, 9, 1),
                source="synthetic-test",
            )
        )
        db.commit()
    finally:
        db.close()

    confirmed = client.post(
        f"{API}/teaching/classes/{v1_seed.t_empty_id}/members/import",
        json={"token": preview.json()["token"]},
    )
    assert confirmed.status_code == 409
    assert "成员已变化" in confirmed.json()["detail"]


def test_linked_class_sync_uses_confirmed_pairs_only(client, v1_seed):
    from app.db import workspace_models as wm

    db = _db()
    try:
        member = (
            db.query(wm.TeachingClassMember)
            .filter_by(
                teaching_class_id=v1_seed.t6_id,
                identity_id=v1_seed.jia_t_id,
            )
            .one()
        )
        member.valid_to = date(2026, 7, 14)
        db.commit()
    finally:
        db.close()

    preview = client.post(
        f"{API}/teaching/classes/{v1_seed.t6_id}/sync-from-homeroom",
        json={"confirm": False},
    )
    assert preview.status_code == 200, preview.text
    assert [row["person_id"] for row in preview.json()["to_add"]] == [v1_seed.jia_t_id]

    confirmed = client.post(
        f"{API}/teaching/classes/{v1_seed.t6_id}/sync-from-homeroom",
        json={"confirm": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["added_count"] == 1

    roster = client.get(f"{API}/teaching/classes/{v1_seed.t6_id}/members").json()
    assert v1_seed.jia_t_id in {row["person_id"] for row in roster["active"]}
