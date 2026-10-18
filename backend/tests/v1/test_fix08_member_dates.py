"""审查修复 问题8：成员生效日期显式化。

复现口径（probe.py 8_history_member）：结束学年（end 2026-07-15）手动加学生
写入 valid_from=2026-10-18（今天）——当年名册里晚于学年结束日；且缺省=操作日
导致早于操作日的考试成绩按「考试时不在班」拒绝。

修复语义：
- add_teaching_member / import_teaching_members / sync-from-homeroom /
  班主任 create_student 接受可选 effective_on（ISO 日期），缺省=今天（教学端
  维持原有的「夹到学年内」行为，完全兼容）；
- 校验 ay.start_date <= effective_on <= ay.end_date，越界可读 422 并附合法范围；
  绝不默认回填学年首日；
- create_student 的 valid_from 计算补上限夹紧（历史学年补录不再晚于学年结束日）。
"""

from datetime import date, timedelta

API = "/api/v1"

AY_START = date(2025, 9, 1)
AY_END = date(2026, 7, 15)


def eff_today() -> date:
    return min(max(date.today(), AY_START), AY_END)


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def test_homeroom_create_student_default_and_clamp(client, v1_seed):
    """缺省行为：valid_from=min(max(今天, 学年首), 学年末)——历史学年不再写出界日期。"""
    r = client.post(
        f"{API}/homeroom/students",
        params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
        json={"name": "秦缺省日", "alias": "FIX8-DEFAULT"},
    )
    assert r.status_code == 200, r.text
    person_id = r.json()["person_id"]

    from app.db import workspace_models as wm

    db = _db()
    try:
        enrollment = (
            db.query(wm.Enrollment)
            .filter_by(identity_id=person_id, admin_class_id=v1_seed.h6_id)
            .one()
        )
        assert enrollment.valid_from == eff_today()
    finally:
        db.close()


def test_homeroom_create_student_explicit_effective_on(client, v1_seed):
    """历史日期补录：Enrollment/alias 的 valid_from 用显式日期。"""
    r = client.post(
        f"{API}/homeroom/students",
        params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
        json={"name": "秦补录", "alias": "FIX8-BACKFILL", "effective_on": "2025-09-15"},
    )
    assert r.status_code == 200, r.text
    person_id = r.json()["person_id"]

    from app.db import workspace_models as wm

    db = _db()
    try:
        enrollment = (
            db.query(wm.Enrollment)
            .filter_by(identity_id=person_id, admin_class_id=v1_seed.h6_id)
            .one()
        )
        assert enrollment.valid_from == date(2025, 9, 15)
        alias = (
            db.query(wm.WsStudentAlias)
            .filter_by(identity_id=person_id, data_domain="homeroom")
            .one()
        )
        assert alias.valid_from == date(2025, 9, 15)
    finally:
        db.close()

    # 补录后「当时合法成绩」可读：早于该日的时点不在班、该日起在班（口径门）
    from app.api import _queries as q

    db = _db()
    try:
        before = set(q.members_at(db, "homeroom", [v1_seed.h6_id], date(2025, 9, 14)))
        after = set(q.members_at(db, "homeroom", [v1_seed.h6_id], date(2025, 9, 16)))
    finally:
        db.close()
    assert person_id not in before
    assert person_id in after


def test_homeroom_create_student_rejects_out_of_year_dates(client, v1_seed):
    for bad in ("2025-08-31", "2026-07-16"):
        r = client.post(
            f"{API}/homeroom/students",
            params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
            json={"name": "秦越界", "alias": "FIX8-BAD", "effective_on": bad},
        )
        assert r.status_code == 422, f"{bad}: {r.text}"
        body = r.json()
        # DomainError.to_http 把 details 平铺在响应顶层
        assert body.get("valid_from") == AY_START.isoformat()
        assert body.get("valid_to") == AY_END.isoformat()
    # 非法日期格式：可读 422（detail 为格式说明文本）
    r = client.post(
        f"{API}/homeroom/students",
        params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
        json={"name": "秦坏日期", "alias": "FIX8-BAD2", "effective_on": "not-a-date"},
    )
    assert r.status_code == 422, r.text


def test_teaching_add_member_effective_on(client, v1_seed):
    r = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members",
        json={"name": "秦教补", "alias": "FIX8-T-BACK", "effective_on": "2025-10-01"},
    )
    assert r.status_code == 200, r.text
    person_id = r.json()["person_id"]

    from app.db import workspace_models as wm

    db = _db()
    try:
        member = (
            db.query(wm.TeachingClassMember)
            .filter_by(teaching_class_id=v1_seed.t8_id, identity_id=person_id)
            .one()
        )
        assert member.valid_from == date(2025, 10, 1)
    finally:
        db.close()

    # 越界拒绝（含合法范围）
    r = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members",
        json={"name": "秦教越", "alias": "FIX8-T-BAD", "effective_on": "2026-08-01"},
    )
    assert r.status_code == 422, r.text
    body = r.json()
    # DomainError.to_http 把 details 平铺在响应顶层
    assert body.get("valid_from") == AY_START.isoformat()
    assert body.get("valid_to") == AY_END.isoformat()


def test_teaching_import_effective_on(client, v1_seed):
    preview = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members/import",
        json={"text": "FIX8-IMP-01 秦导入补\nFIX8-IMP-02 秦导入新", "effective_on": "2025-09-20"},
    )
    assert preview.status_code == 200, preview.text
    confirmed = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members/import",
        json={"token": preview.json()["token"], "effective_on": "2025-09-20"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json().get("added_count") == 2

    from app.db import workspace_models as wm

    db = _db()
    try:
        rows = (
            db.query(wm.TeachingClassMember)
            .filter(wm.TeachingClassMember.teaching_class_id == v1_seed.t8_id)
            .all()
        )
        fresh = [m for m in rows if m.valid_from == date(2025, 9, 20)]
        assert len(fresh) == 2
    finally:
        db.close()

    # 日期漂移：预览与确认的 effective_on 不一致 → 可读 409 重新预览
    preview = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members/import",
        json={"text": "FIX8-IMP-03 秦漂日", "effective_on": "2025-09-21"},
    )
    drift = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members/import",
        json={"token": preview.json()["token"]},
    )
    assert drift.status_code == 409, drift.text

    # 越界 422
    r = client.post(
        f"{API}/teaching/classes/{v1_seed.t8_id}/members/import",
        json={"text": "FIX8-IMP-04 秦越界", "effective_on": str(AY_END + timedelta(days=1))},
    )
    assert r.status_code == 422, r.text


def test_teaching_sync_effective_on(client, v1_seed):
    """同步（含新成员决策）接受生效日期：成员行 valid_from 用该日期。"""
    confirmed = client.post(
        f"{API}/teaching/classes/{v1_seed.t6_id}/sync-from-homeroom",
        json={
            "confirm": True,
            "effective_on": "2025-10-10",
            "new_students": [
                {"homeroom_person_id": v1_seed.bing_h_id, "action": "new"}
            ],
        },
    )
    assert confirmed.status_code == 200, confirmed.text

    from app.db import workspace_models as wm

    db = _db()
    try:
        pair = (
            db.query(wm.LinkedStudent)
            .filter_by(link_id=v1_seed.link_id, homeroom_identity_id=v1_seed.bing_h_id)
            .one()
        )
        member = (
            db.query(wm.TeachingClassMember)
            .filter_by(teaching_class_id=v1_seed.t6_id, identity_id=pair.teaching_identity_id)
            .one()
        )
        assert member.valid_from == date(2025, 10, 10)
    finally:
        db.close()

    # 越界 422
    r = client.post(
        f"{API}/teaching/classes/{v1_seed.t6_id}/sync-from-homeroom",
        json={"confirm": False, "effective_on": "2025-08-01"},
    )
    assert r.status_code == 422, r.text
