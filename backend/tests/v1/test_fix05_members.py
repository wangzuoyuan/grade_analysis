"""审查修复 问题5：教学班成员同日移除边界与恢复语义。

复现口径（/private/tmp/review-audit-20261018/probe.py 5_same_day_reimport、
probe3.py 13A_same_day_membership）：
- 同日加入再移除后 valid_from==valid_to==同日，members_at 按 [from,to]
  闭区间判断 → 当天仍算在班；
- 再次导入确认 added_count=0（把「已有行」当跳过，无恢复语义）。

修复后语义：
- 移除一律 valid_to = effective_on 前一天（同日入班者得到空区间，
  当天即不在班；更早入班者行为与原实现一致）；
- 导入/同步遇已结束成员行 → 恢复（restored 计数），同日行原地重开
  （不撞 uq_teaching_member 唯一约束），隔日恢复新开一段、保留空档；
- 反复移除/恢复不产生重叠或重复行。
"""

from datetime import date

API = "/api/v1"

# 种子学年 2025-09-01 ~ 2026-07-15；今天（真实日期）夹到学年内的默认生效日。
AY_START = date(2025, 9, 1)
AY_END = date(2026, 7, 15)


def eff_today() -> date:
    return min(max(date.today(), AY_START), AY_END)


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _members_at_teaching(class_id: int, at: date) -> set:
    from app.api import _queries as q

    db = _db()
    try:
        return set(q.members_at(db, "teaching", [class_id], at))
    finally:
        db.close()


def test_same_day_remove_is_not_member_same_day(client, v1_seed):
    """同日移除：当天即不在班（原缺陷：valid_to==当天，闭区间仍算在班）。"""
    added = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members",
        json={"name": "秦同日", "alias": "FIX5-SAME-DAY"},
    )
    assert added.status_code == 200, added.text
    person_id = added.json()["person_id"]
    eff = eff_today()

    removed = client.delete(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members/{person_id}"
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["valid_to"] == (eff - __import__("datetime").timedelta(days=1)).isoformat()

    # 名册管理页口径（list 接口）与 members_at 一致：当天已不在当期成员
    roster = client.get(f"{API}/teaching/classes/{v1_seed.t8_id}/members").json()
    assert person_id not in {row["person_id"] for row in roster["active"]}
    assert person_id in {row["person_id"] for row in roster["left"]}
    assert person_id not in _members_at_teaching(v1_seed.t8_id, eff)


def test_same_day_reimport_restores_in_place(client, v1_seed):
    """同日移除后重导入：原地重开同一行（restored 计数），不新建行。"""
    added = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members",
        json={"name": "秦再入", "alias": "FIX5-REIMPORT"},
    )
    person_id = added.json()["person_id"]
    client.delete(f"{API}/teaching/classes/{v1_seed.t8_id}/members/{person_id}")

    preview = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members/import",
        json={"text": "FIX5-REIMPORT 秦再入"},
    )
    assert preview.status_code == 200, preview.text
    assert [row["kind"] for row in preview.json()["lines"]] == ["match"]

    confirmed = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members/import",
        json={"token": preview.json()["token"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    # 恢复而不是「跳过」（原缺陷 added_count=0 且无任何恢复）
    assert body.get("restored_count") == 1
    assert body.get("added_count") == 0

    from app.db import workspace_models as wm

    db = _db()
    try:
        rows = (
            db.query(wm.TeachingClassMember)
            .filter_by(teaching_class_id=v1_seed.t8_id, identity_id=person_id)
            .all()
        )
        assert len(rows) == 1  # 原地重开，不新建行
        assert rows[0].valid_to is None
    finally:
        db.close()
    assert person_id in _members_at_teaching(v1_seed.t8_id, eff_today())

    # 恢复后再移除：回到空区间，当天又不在班；再导入又恢复——始终一行
    client.delete(f"{API}/teaching/classes/{v1_seed.t8_id}/members/{person_id}")
    assert person_id not in _members_at_teaching(v1_seed.t8_id, eff_today())
    preview = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members/import",
        json={"text": "FIX5-REIMPORT 秦再入"},
    )
    again = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members/import",
        json={"token": preview.json()["token"]},
    )
    assert again.status_code == 200, again.text
    assert again.json().get("restored_count") == 1
    db = _db()
    try:
        rows = (
            db.query(wm.TeachingClassMember)
            .filter_by(teaching_class_id=v1_seed.t8_id, identity_id=person_id)
            .all()
        )
        assert len(rows) == 1
    finally:
        db.close()


def test_restore_after_gap_opens_second_period(client, v1_seed):
    """隔日恢复：新开一段 [恢复日, ∞)，空档期间考试不算成员、行不重叠。"""
    from app.db import workspace_models as wm

    db = _db()
    try:
        identity = wm.WsStudentIdentity(data_domain="teaching", display_name="秦隔期")
        db.add(identity)
        db.flush()
        db.add(
            wm.WsStudentAlias(
                identity_id=identity.id,
                alias_value="FIX5-GAP",
                data_domain="teaching",
                academic_year_id=v1_seed.ay_id,
                alias_scope=str(v1_seed.ay_id),
                source="synthetic-test",
            )
        )
        db.add(
            wm.TeachingClassMember(
                teaching_class_id=v1_seed.t8_id,
                identity_id=identity.id,
                valid_from=date(2025, 9, 1),
                valid_to=date(2025, 10, 1),
                source="synthetic-test",
            )
        )
        db.commit()
        person_id = identity.id
    finally:
        db.close()

    eff = eff_today()
    # 空档期间（如 2025-10-15）不在班
    assert person_id not in _members_at_teaching(v1_seed.t8_id, date(2025, 10, 15))

    preview = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members/import",
        json={"text": "FIX5-GAP 秦隔期"},
    )
    assert preview.status_code == 200, preview.text
    confirmed = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members/import",
        json={"token": preview.json()["token"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json().get("restored_count") == 1

    db = _db()
    try:
        rows = (
            db.query(wm.TeachingClassMember)
            .filter_by(teaching_class_id=v1_seed.t8_id, identity_id=person_id)
            .order_by(wm.TeachingClassMember.valid_from.asc())
            .all()
        )
        assert len(rows) == 2  # 两段学籍，不重叠不重复
        assert (rows[0].valid_from, rows[0].valid_to) == (date(2025, 9, 1), date(2025, 10, 1))
        assert (rows[1].valid_from, rows[1].valid_to) == (eff, None)
    finally:
        db.close()
    # 空档期仍不在班；恢复日起在班
    assert person_id not in _members_at_teaching(v1_seed.t8_id, date(2025, 10, 15))
    assert person_id in _members_at_teaching(v1_seed.t8_id, eff)


def test_earlier_entrant_remove_keeps_prior_day_membership(client, v1_seed):
    """早于移除日入班者：valid_to=前一天（既有语义保留），前一天仍在班、当天不在。"""
    added = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members",
        json={"name": "秦早期", "alias": "FIX5-EARLY", "effective_on": "2025-09-10"},
    )
    assert added.status_code == 200, added.text
    person_id = added.json()["person_id"]

    removed = client.delete(f"{API}/teaching/classes/{v1_seed.t8_id}/members/{person_id}")
    eff = eff_today()
    assert removed.status_code == 200
    assert removed.json()["valid_to"] == (eff - __import__("datetime").timedelta(days=1)).isoformat()
    assert person_id in _members_at_teaching(v1_seed.t8_id, date(2025, 9, 10))
    assert person_id not in _members_at_teaching(v1_seed.t8_id, eff)
