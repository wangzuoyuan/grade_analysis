"""审查修复 问题7：已关联教学班对新转入学生的三方死锁。

死锁链条（/private/tmp/review-audit-20261018/probe2.py 7_linked_entry、
probe3.py 7_link_pair）：
1. 教学班直接加人 → 409（_reject_direct_linked_roster_change，保留）；
2. shared.add_link_student 要求 teaching_person 已在教学班名册 → 404；
3. sync-from-homeroom 只遍历已有 LinkedStudent 配对的人 → 新人进不来。

修复：扩展 sync-from-homeroom——预览列出「行政班在册、未配对」学生并给出
教学域候选身份（仅提示，绝不自动认定）；确认请求逐生携带决定
（new / pair(teaching_person_id) / skip）+ 生效日期，同一事务内完成
「创建或选定教学身份 → 写 TeachingClassMember → 建 LinkedStudent 配对」，
任一步失败整体回滚零残留。

注：v1_seed 为模块级 fixture（模块内状态跨用例累积），各用例都用
/homeroom/students 新建自己的转入生，保证彼此独立。
"""

from datetime import date

API = "/api/v1"

_seq = iter(range(100))


def _fresh_transferred_student(client, v1_seed) -> dict:
    """造一个「行政班在册、未配对、不在教学班名册」的转入生（死锁主角）。"""
    n = next(_seq)
    r = client.post(
        f"{API}/homeroom/students",
        params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
        json={"name": f"秦转入{n}", "alias": f"FIX7-H-{n:02d}", "effective_on": "2025-09-05"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    return {"person_id": body["person_id"], "name": body["name"], "alias": f"FIX7-H-{n:02d}"}


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def test_sync_preview_lists_unpaired_with_candidates(client, v1_seed):
    """预览：未配对转入生被列出；候选=教学域同名或同学号的既有身份（仅提示）。"""
    student = _fresh_transferred_student(client, v1_seed)

    # 教学域造一个与该生「学号完全一致」的身份（应成候选）；
    # 另造一个不同名不同号的（不应成候选）
    from app.db import workspace_models as wm

    db = _db()
    try:
        cand = wm.WsStudentIdentity(data_domain="teaching", display_name="秦别处")
        noise = wm.WsStudentIdentity(data_domain="teaching", display_name="秦无关")
        db.add_all([cand, noise])
        db.flush()
        db.add(
            wm.WsStudentAlias(
                identity_id=cand.id,
                alias_value=student["alias"],
                data_domain="teaching",
                academic_year_id=v1_seed.ay_id,
                alias_scope=str(v1_seed.ay_id),
                source="synthetic-test",
            )
        )
        db.commit()
        candidate_id = cand.id
    finally:
        db.close()

    preview = client.post(
        f"{API}/teaching/classes/{v1_seed.t6_id}/sync-from-homeroom",
        json={"confirm": False},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    unpaired = body.get("unpaired") or []
    by_h = {row["homeroom_person_id"]: row for row in unpaired}
    # 种子丙与新建转入生都在册未配对 → 均被列出；甲乙已配对不出现
    assert v1_seed.bing_h_id in by_h
    entry = by_h[student["person_id"]]
    assert entry["name"] == student["name"]
    assert entry["alias"] == student["alias"]
    assert [c["person_id"] for c in entry["candidates"]] == [candidate_id]
    assert v1_seed.jia_h_id not in by_h
    assert v1_seed.yi_h_id not in by_h
    # 预览响应附生效日与学年合法范围（问题8 前端提示用）
    assert body.get("valid_from") == "2025-09-01" and body.get("valid_to") == "2026-07-15"

    # 死锁第一环保留：直接加人仍 409
    direct = client.post(
        f"{API}/teaching/classes/{v1_seed.t6_id}/members",
        json={"name": "秦直加", "alias": "FIX7-DIRECT"},
    )
    assert direct.status_code == 409


def test_sync_confirm_new_creates_identity_member_pair_together(client, v1_seed):
    """确认 new：单事务完成 建身份+入班+配对；配对与成员行同生，考试时点可读。"""
    student = _fresh_transferred_student(client, v1_seed)

    confirmed = client.post(
        f"{API}/teaching/classes/{v1_seed.t6_id}/sync-from-homeroom",
        json={
            "confirm": True,
            "effective_on": "2025-10-01",
            "new_students": [
                {"homeroom_person_id": student["person_id"], "action": "new"}
            ],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json().get("paired_count") == 1

    from app.db import workspace_models as wm

    db = _db()
    try:
        pair = (
            db.query(wm.LinkedStudent)
            .filter_by(link_id=v1_seed.link_id, homeroom_identity_id=student["person_id"])
            .one()
        )
        new_t = pair.teaching_identity_id
        # 教学域新身份（同名；学号沿行政班学号在教学域独立登记）
        ident = db.get(wm.WsStudentIdentity, new_t)
        assert ident.data_domain == "teaching"
        assert ident.display_name == student["name"]
        alias = (
            db.query(wm.WsStudentAlias)
            .filter_by(identity_id=new_t, data_domain="teaching")
            .one()
        )
        assert alias.alias_value == student["alias"]
        # 成员行：选定生效日期
        member = (
            db.query(wm.TeachingClassMember)
            .filter_by(teaching_class_id=v1_seed.t6_id, identity_id=new_t)
            .one()
        )
        assert member.valid_from == date(2025, 10, 1)
        assert member.valid_to is None
    finally:
        db.close()

    # 考试时点（2025-11-06，种子考试日）投影门两侧成员均有效 → 配对可读
    from app.api import _queries as q

    db = _db()
    try:
        link = db.get(wm.HomeroomTeachingLink, v1_seed.link_id)
        pairs = q.eligible_linked_pairs(db, link, date(2025, 11, 6))
        assert pairs.get(student["person_id"]) == new_t
    finally:
        db.close()

    # 名册接口可见；再次预览：该生已配对 → 不再出现在 unpaired
    roster = client.get(f"{API}/teaching/classes/{v1_seed.t6_id}/members").json()
    assert new_t in {row["person_id"] for row in roster["active"]}
    preview = client.post(
        f"{API}/teaching/classes/{v1_seed.t6_id}/sync-from-homeroom",
        json={"confirm": False},
    )
    by_h = {row["homeroom_person_id"] for row in preview.json().get("unpaired") or []}
    assert student["person_id"] not in by_h


def test_sync_confirm_pair_uses_existing_identity(client, v1_seed):
    """确认 pair：选定候选身份入班并配对，不新建教学身份。"""
    student = _fresh_transferred_student(client, v1_seed)

    from app.db import workspace_models as wm

    db = _db()
    try:
        cand = wm.WsStudentIdentity(data_domain="teaching", display_name=student["name"])
        db.add(cand)
        db.commit()
        candidate_id = cand.id
        before = (
            db.query(wm.WsStudentIdentity)
            .filter_by(data_domain="teaching")
            .count()
        )
    finally:
        db.close()

    confirmed = client.post(
        f"{API}/teaching/classes/{v1_seed.t6_id}/sync-from-homeroom",
        json={
            "confirm": True,
            "effective_on": "2025-10-05",
            "new_students": [
                {
                    "homeroom_person_id": student["person_id"],
                    "action": "pair",
                    "teaching_person_id": candidate_id,
                }
            ],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    db = _db()
    try:
        after = (
            db.query(wm.WsStudentIdentity)
            .filter_by(data_domain="teaching")
            .count()
        )
        assert after == before  # 没有新建身份
        member = (
            db.query(wm.TeachingClassMember)
            .filter_by(teaching_class_id=v1_seed.t6_id, identity_id=candidate_id)
            .one()
        )
        assert member.valid_from == date(2025, 10, 5)
        pair = (
            db.query(wm.LinkedStudent)
            .filter_by(link_id=v1_seed.link_id, homeroom_identity_id=student["person_id"])
            .one()
        )
        assert pair.teaching_identity_id == candidate_id
    finally:
        db.close()


def test_sync_confirm_rejects_forged_decisions_with_zero_writes(client, v1_seed):
    """伪造/越界/漂移决定一律可读 4xx，且零写入（无半套记录）。"""
    from app.db import workspace_models as wm

    student = _fresh_transferred_student(client, v1_seed)

    def post_decision(payload_action: str, t_id=None, h_id=None):
        h = h_id if h_id is not None else student["person_id"]
        decision = {"homeroom_person_id": h, "action": payload_action}
        if t_id is not None:
            decision["teaching_person_id"] = t_id
        return client.post(
            f"{API}/teaching/classes/{v1_seed.t6_id}/sync-from-homeroom",
            json={"confirm": True, "new_students": [decision]},
        )

    # 伪造不存在的 teaching_person_id
    r = post_decision("pair", t_id=999999)
    assert r.status_code in (404, 422), r.text
    # 传入 homeroom 域身份（跨域越界）
    r = post_decision("pair", t_id=v1_seed.jia_h_id)
    assert r.status_code in (404, 422), r.text
    # 已配对给别人的教学身份（种子里甲的 jia_t）
    r = post_decision("pair", t_id=v1_seed.jia_t_id)
    assert r.status_code in (404, 409, 422), r.text
    # 已配对的行政班学生再决策（甲已配对）
    r = post_decision("new", h_id=v1_seed.jia_h_id)
    assert r.status_code in (404, 409, 422), r.text
    # 不在行政班名册的人（教学域身份 ding_t 不是行政班学生）
    r = post_decision("new", h_id=v1_seed.ding_t_id)
    assert r.status_code in (404, 409, 422), r.text
    # 非法 action
    r = post_decision("merge")
    assert r.status_code == 422, r.text

    # 全部失败请求零残留：该转入生没有配对、没有教学域身份、没有成员行
    db = _db()
    try:
        pair = (
            db.query(wm.LinkedStudent)
            .filter_by(homeroom_identity_id=student["person_id"])
            .first()
        )
        assert pair is None
        teaching_alias = (
            db.query(wm.WsStudentAlias)
            .filter_by(alias_value=student["alias"], data_domain="teaching")
            .first()
        )
        assert teaching_alias is None
        member = (
            db.query(wm.TeachingClassMember)
            .filter_by(identity_id=student["person_id"])
            .first()
        )
        assert member is None
    finally:
        db.close()


def test_sync_confirm_atomic_no_partial_on_second_failure(client, v1_seed):
    """事务性：第一个决定合法、第二个非法 → 整体失败，第一个也不落库。"""
    from app.db import workspace_models as wm

    student = _fresh_transferred_student(client, v1_seed)

    db = _db()
    try:
        before_pairs = db.query(wm.LinkedStudent).count()
        before_members = db.query(wm.TeachingClassMember).count()
        before_ident = db.query(wm.WsStudentIdentity).count()
    finally:
        db.close()

    r = client.post(
        f"{API}/teaching/classes/{v1_seed.t6_id}/sync-from-homeroom",
        json={
            "confirm": True,
            "new_students": [
                {"homeroom_person_id": student["person_id"], "action": "new"},
                {
                    "homeroom_person_id": v1_seed.bing_h_id,
                    "action": "pair",
                    "teaching_person_id": 999999,  # 第二个伪造 → 整体拒绝
                },
            ],
        },
    )
    assert r.status_code in (404, 422), r.text

    db = _db()
    try:
        assert db.query(wm.LinkedStudent).count() == before_pairs
        assert db.query(wm.TeachingClassMember).count() == before_members
        assert db.query(wm.WsStudentIdentity).count() == before_ident
    finally:
        db.close()


def test_sync_confirm_skip_leaves_student_alone(client, v1_seed):
    """skip：既不入班也不配对（教师逐人确认，默认不代作决定）。"""
    student = _fresh_transferred_student(client, v1_seed)
    r = client.post(
        f"{API}/teaching/classes/{v1_seed.t6_id}/sync-from-homeroom",
        json={
            "confirm": True,
            "new_students": [
                {"homeroom_person_id": student["person_id"], "action": "skip"}
            ],
        },
    )
    assert r.status_code == 200, r.text

    from app.db import workspace_models as wm

    db = _db()
    try:
        pair = (
            db.query(wm.LinkedStudent)
            .filter_by(homeroom_identity_id=student["person_id"])
            .first()
        )
        assert pair is None
    finally:
        db.close()
