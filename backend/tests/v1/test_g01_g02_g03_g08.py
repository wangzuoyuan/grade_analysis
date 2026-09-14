""" 二轮审核 G01/G02/G03/G08 + expected_count 债务回归
（契约 docs/contracts/p5-homework.md §0 v2 / §2 v2 / §4 v2）。

- G01 事件时点门：跨域共享按 assignment.assigned_date 逐批次核验
  （link 事件时点有效期 + share_history_from 下限 + 双侧成员覆盖
  assigned_date 的交集）；授权日前批次、历史授权收紧后的更早批次、
  作业后入班成员的入班前记录，对侧一律不可见、不可写（404/422）。
- G02 跨域撤销边界：跨域 DELETE 仅作用于共享成员事实；批次含本工作台
  无权管理的成员 → 409 且不泄露源域学生姓名；全部成员共享且无冲突才
  允许撤销；源域 DELETE 保持全批次依赖检查。
- G03 跨域可写集 = 事件时点授权成员 ∩ 批次 expected_members_json 快照；
  快照外的人 PATCH → 422 零写入，提交率不被暗中扩人。
- G08 相关性成绩侧走 readable_facts 统一投影：T 本域无物理成绩时，
  合法 H 投影样本仍进入 pairs（非 n=0）。
- expected_count：看板 groups 返回组内批次应交快照真实合计（不再恒 0）。

注意模块内共享同一物理库、用例按定义顺序推进（link 授权状态由各用例
自行声明），不得调整顺序。
"""

import json
from datetime import date

from app.db import workspace_models as wm

BASE = "/api/v1"
# 作业共享类别全开（含 current_subject_score，G08 投影成绩也要它）
HW_CATEGORIES = "roster,current_subject_score,current_subject_homework"

# 跨用例共享的批次 id（模块内定义顺序执行，先建后用）
_STATE: dict = {}


def _set_link(db_session, seed, **fields):
    """直接声明 link 的授权状态（有效期/历史下限/类别）：G01 的门输入。"""
    link = (
        db_session.query(wm.HomeroomTeachingLink)
        .filter(wm.HomeroomTeachingLink.id == seed.link_id)
        .one()
    )
    for key, value in fields.items():
        setattr(link, key, value)
    db_session.commit()


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


def _h_detail(client, seed, aid):
    return client.get(
        f"{BASE}/homework/assignments/{aid}",
        params={"mode": "homeroom", "class_id": seed.h6_id,
                "academic_year_id": seed.ay_id},
    )


def _t_detail(client, seed, aid):
    return client.get(
        f"{BASE}/homework/assignments/{aid}",
        params={"mode": "teaching", "teaching_class_id": seed.t6_id,
                "academic_year_id": seed.ay_id},
    )


def _t_patch(client, seed, aid, **body):
    return client.patch(
        f"{BASE}/homework/assignments/{aid}",
        params={"mode": "teaching", "teaching_class_id": seed.t6_id,
                "academic_year_id": seed.ay_id},
        json=body,
    )


def _t_delete(client, seed, aid):
    return client.delete(
        f"{BASE}/homework/assignments/{aid}",
        params={"mode": "teaching", "teaching_class_id": seed.t6_id,
                "academic_year_id": seed.ay_id},
    )


def _synth_h_assignment(db_session, seed, token, assigned, expected_ids, rows):
    """合成 H 域批次（expected_members 快照与逐人行），模拟"批次后新增
    配对/成员"等无法经现役 API 造出的历史快照状态（同 历史复现口径）。"""
    a = wm.HomeworkAssignment(
        data_domain="homeroom",
        class_ref_id=seed.h6_id,
        academic_year_id=seed.ay_id,
        subject="物理",
        homework_type="练习册",
        assigned_date=assigned,
        batch_token=token,
        expected_members_json=json.dumps(expected_ids),
    )
    db_session.add(a)
    db_session.flush()
    for pid, status in rows:
        db_session.add(
            wm.HomeworkSubmission(
                assignment_id=a.id, person_id=pid, submission_status=status
            )
        )
    db_session.commit()
    return a.id


# ────────────────────── G01 事件时点门 ──────────────────────


def test_g01_batch_before_valid_from_invisible_and_writes_rejected(
    client, v1_seed, db_session
):
    """授权日前批次：对侧列表/详情不可见，PATCH/DELETE 404；双向各一例。"""
    _set_link(db_session, v1_seed, valid_from=date(2025, 11, 1),
              share_history_from=None, share_categories=HW_CATEGORIES)

    # H 建 2025-09-10 批次（早于 link.valid_from 2025-11-01）
    p = _h_preview(client, v1_seed, assigned_date="2025-09-10",
                   homework_type="G01练习")
    assert p.status_code == 200, p.text
    aid_h = _confirm(client, p.json()["token"]).json()["assignment_id"]
    _STATE["aid_h"] = aid_h

    listing = client.get(
        f"{BASE}/homework/assignments",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t6_id,
                "academic_year_id": v1_seed.ay_id,
                "from_date": "2025-09-01", "to_date": "2025-09-30"},
    )
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 0  # 授权日前批次对侧列表不可见

    assert _t_detail(client, v1_seed, aid_h).status_code == 404
    stale_patch = _t_patch(
        client, v1_seed, aid_h, revision=1,
        rows=[{"name_or_alias": "秦甲·T", "status": "missing"}],
    )
    assert stale_patch.status_code == 404, stale_patch.text
    assert _t_delete(client, v1_seed, aid_h).status_code == 404

    # 源域直读不受影响
    assert _h_detail(client, v1_seed, aid_h).status_code == 200

    # 双向：T 建 2025-08-15 批次（同样早于 valid_from），H 侧不可见
    tp = _t_preview(client, v1_seed, v1_seed.t6_id,
                    assigned_date="2025-08-15", homework_type="G01练习T")
    assert tp.status_code == 200, tp.text
    aid_t = _confirm(client, tp.json()["token"]).json()["assignment_id"]

    h_listing = client.get(
        f"{BASE}/homework/assignments",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "academic_year_id": v1_seed.ay_id,
                "from_date": "2025-08-01", "to_date": "2025-08-31"},
    ).json()
    assert h_listing["total"] == 0
    assert _h_detail(client, v1_seed, aid_t).status_code == 404


def test_g01_share_history_from_tightened_hides_earlier_batches(
    client, v1_seed, db_session
):
    """share_history_from 收紧后：更早的历史批次对侧立即消失，
    下限之后的批次照常可见（读门在每次请求时生效）。"""
    _set_link(db_session, v1_seed, valid_from=date(2025, 9, 1),
              share_history_from=date(2025, 10, 1),
              share_categories=HW_CATEGORIES)
    aid_h = _STATE["aid_h"]  # 2025-09-10 批次（早于新下限）

    assert _t_detail(client, v1_seed, aid_h).status_code == 404
    listing = client.get(
        f"{BASE}/homework/assignments",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t6_id,
                "academic_year_id": v1_seed.ay_id,
                "from_date": "2025-09-01", "to_date": "2025-09-30"},
    ).json()
    assert listing["total"] == 0

    # 下限之后的批次照常投影
    p = _h_preview(client, v1_seed, assigned_date="2025-10-15",
                   homework_type="G01练习")
    assert p.status_code == 200, p.text
    aid_h2 = _confirm(client, p.json()["token"]).json()["assignment_id"]

    detail = _t_detail(client, v1_seed, aid_h2)
    assert detail.status_code == 200, detail.text
    oct_listing = client.get(
        f"{BASE}/homework/assignments",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t6_id,
                "academic_year_id": v1_seed.ay_id,
                "from_date": "2025-10-01", "to_date": "2025-10-31"},
    ).json()
    assert [i["assignment_id"] for i in oct_listing["items"]] == [aid_h2]


def test_g01_member_joined_after_assignment_has_no_earlier_records(
    client, v1_seed, db_session
):
    """作业后入班成员：入班前批次的记录对侧不可见、不可写；有效期覆盖
    之后批次的记录照常投影（事件时点交集，非一刀切屏蔽）。"""
    _set_link(db_session, v1_seed, valid_from=date(2025, 9, 1),
              share_history_from=None, share_categories=HW_CATEGORIES)

    # 新 T 学生「秦晚·T」2025-09-20 入 T6，并与 H 域丙配对
    late = wm.WsStudentIdentity(data_domain="teaching", display_name="秦晚·T")
    db_session.add(late)
    db_session.flush()
    db_session.add(
        wm.TeachingClassMember(
            teaching_class_id=v1_seed.t6_id, identity_id=late.id,
            valid_from=date(2025, 9, 20),
        )
    )
    db_session.add(
        wm.LinkedStudent(
            link_id=v1_seed.link_id,
            homeroom_identity_id=v1_seed.bing_h_id,
            teaching_identity_id=late.id,
            confirm_basis="g01-test",
        )
    )
    db_session.commit()
    _STATE["late_t_id"] = late.id

    aid_h = _STATE["aid_h"]  # 2025-09-10 full 批次（甲乙丙均 submitted）
    detail = _t_detail(client, v1_seed, aid_h)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    # 事件时点（09-10）丙未入 T6 → 投影交集只有甲乙；丙的行对侧不可见
    assert body["expected_count"] == 2
    assert {s["person_id"] for s in body["submissions"]} == {
        v1_seed.jia_t_id, v1_seed.yi_t_id,
    }

    # 入班前批次：晚·T 事件流无该批记录
    events = client.get(
        f"{BASE}/homework/students/{late.id}",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t6_id},
    ).json()
    assert all(e["assigned_date"] != "2025-09-10" for e in events["events"])
    # 有效期覆盖后的批次（10-15，丙已入班）照常出现
    assert any(e["assigned_date"] == "2025-10-15" for e in events["events"])

    # 不可写：给入班前批次补晚·T 的行 → 422（不在事件时点授权交集内）
    resp = _t_patch(
        client, v1_seed, aid_h, revision=1,
        rows=[{"name_or_alias": "秦晚·T", "status": "missing"}],
    )
    assert resp.status_code == 422, resp.text


# ────────────────────── G02 跨域撤销边界 ──────────────────────


def test_g02_cross_delete_with_nonshared_member_rejected_without_name_leak(
    client, v1_seed, db_session
):
    """H 批次含非共享成员丙且丙已被 H 编辑：T DELETE 409、错误不含丙姓名
    （不反向泄露源域私有明细）；源域 DELETE 仍做全批依赖检查并列出丙。"""
    p = _h_preview(client, v1_seed, assigned_date="2025-09-17",
                   homework_type="G02练习")
    assert p.status_code == 200, p.text
    aid = _confirm(client, p.json()["token"]).json()["assignment_id"]

    # H 编辑丙（revision 递增 → 源域撤销依赖存在）
    edited = client.patch(
        f"{BASE}/homework/assignments/{aid}",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "academic_year_id": v1_seed.ay_id},
        json={"revision": 1,
              "rows": [{"name_or_alias": "秦丙", "status": "missing",
                        "evaluation": "情况说明"}]},
    )
    assert edited.status_code == 200, edited.text

    resp = _t_delete(client, v1_seed, aid)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "link_version_conflict"
    assert "无权管理" in resp.json()["detail"]
    assert "秦丙" not in resp.text  # 不泄露源域私有学生姓名
    assert "conflicts" not in resp.json()
    # 批次未被撤销
    assert _h_detail(client, v1_seed, aid).json()["status"] == "active"

    # 源域 DELETE：全批次依赖检查保留，冲突清单可见丙
    h_del = client.delete(
        f"{BASE}/homework/assignments/{aid}",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "academic_year_id": v1_seed.ay_id},
    )
    assert h_del.status_code == 409, h_del.text
    conflicts = h_del.json()["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["person_id"] == v1_seed.bing_h_id
    assert conflicts[0]["name"] == "秦丙"


def test_g02_cross_delete_all_shared_no_conflict_allowed(
    client, v1_seed, db_session
):
    """全部成员均为共享（快照仅甲乙）且无后续编辑：T DELETE 允许撤销
    （此时源域独有成员不存在，整批 revoked 合法）。"""
    aid = _synth_h_assignment(
        db_session, v1_seed, "g02-all-shared", date(2025, 9, 18),
        [v1_seed.jia_h_id, v1_seed.yi_h_id],
        [(v1_seed.jia_h_id, "submitted"), (v1_seed.yi_h_id, "submitted")],
    )

    resp = _t_delete(client, v1_seed, aid)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "revoked"

    h_body = _h_detail(client, v1_seed, aid).json()
    assert h_body["status"] == "revoked"
    assert h_body["revision"] == 2


# ────────────────────── G03 跨域可写集 ∩ 快照 ──────────────────────


def test_g03_cross_patch_outside_snapshot_422_zero_writes(
    client, v1_seed, db_session
):
    """快照仅甲、当前甲乙均配对：T 给乙 upsert → 422 零写入（批次 revision
    与逐人行都不变）；给甲 upsert → 200 且提交率不超过 1。"""
    aid = _synth_h_assignment(
        db_session, v1_seed, "g03-snap-jia", date(2025, 9, 19),
        [v1_seed.jia_h_id],
        [(v1_seed.jia_h_id, "missing")],
    )

    # 乙·T 在事件时点授权交集内，但不在该批次快照内 → 拒绝
    outside = _t_patch(
        client, v1_seed, aid, revision=1,
        rows=[{"name_or_alias": "秦乙·T", "status": "submitted"}],
    )
    assert outside.status_code == 422, outside.text
    assert outside.json()["error"] == "invalid_scope_param"

    # 零写入：批次 revision 不变、无乙的行
    h_body = _h_detail(client, v1_seed, aid).json()
    assert h_body["revision"] == 1
    assert h_body["expected_count"] == 1
    assert [(s["person_id"], s["status"]) for s in h_body["submissions"]] == [
        (v1_seed.jia_h_id, "missing")
    ]
    rows = (
        db_session.query(wm.HomeworkSubmission)
        .filter(wm.HomeworkSubmission.assignment_id == aid)
        .all()
    )
    assert [(r.person_id, r.submission_status) for r in rows] == [
        (v1_seed.jia_h_id, "missing")
    ]

    # 快照内的甲：允许 upsert；提交率 = 1/1 = 1.0（不被暗中扩人）
    inside = _t_patch(
        client, v1_seed, aid, revision=1,
        rows=[{"name_or_alias": "秦甲·T", "status": "submitted"}],
    )
    assert inside.status_code == 200, inside.text
    assert (inside.json()["revision"], inside.json()["updated"]) == (2, 1)

    t_body = _t_detail(client, v1_seed, aid).json()
    assert t_body["expected_count"] == 1
    assert t_body["submitted"] == 1
    assert t_body["submission_rate"] == 1.0
    assert t_body["rate_unavailable"] is False


# ────────────────────── G08 相关性统一投影 ──────────────────────


def test_g08_correlation_uses_unified_projection_for_teaching(
    client, v1_seed, db_session
):
    """清空 T 本域「2025期中」物理成绩后：teaching 相关性仍经
    readable_facts 拿到合法 H 投影样本（pairs 非空、n=2），而非直连
    本域 facts 得到 pairs=[]、n=0。"""
    p = _h_preview(
        client, v1_seed, assigned_date="2025-09-15", homework_type="G08练习",
        input={"kind": "detailed",
               "rows": [{"name_or_alias": "秦甲", "status": "submitted"},
                        {"name_or_alias": "秦乙", "status": "missing"}]},
    )
    assert p.status_code == 200, p.text
    assert _confirm(client, p.json()["token"]).status_code == 200

    deleted = (
        db_session.query(wm.ScoreFact)
        .filter(
            wm.ScoreFact.data_domain == "teaching",
            wm.ScoreFact.exam_name == "2025期中",
            wm.ScoreFact.class_ref_id == v1_seed.t6_id,
        )
        .delete(synchronize_session=False)
    )
    assert deleted > 0
    db_session.commit()

    resp = client.get(
        f"{BASE}/homework/correlation",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t6_id,
                "academic_year_id": v1_seed.ay_id,
                "subject": "物理", "homework_type": "G08练习",
                "exam_name": "2025期中"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    pair_ids = {pr["person_id"]: pr for pr in body["pairs"]}
    # 甲乙的成绩全部来自 H 域投影（T 本域已清空），X 来自共享作业批次
    assert set(pair_ids) == {v1_seed.jia_t_id, v1_seed.yi_t_id}
    assert pair_ids[v1_seed.jia_t_id]["y"] == 1  # 90 分班内第一（投影值）
    assert pair_ids[v1_seed.jia_t_id]["x"] == 1.0
    assert pair_ids[v1_seed.yi_t_id]["y"] == 2
    assert pair_ids[v1_seed.yi_t_id]["x"] == 0.0
    assert body["n"] == 2
    assert body["r"] is None
    assert any("n=" in c for c in body["caveats"])


# ────────────────────── expected_count 真实值 ──────────────────────


def test_dashboard_expected_count_is_real_sum(client, v1_seed):
    """看板 groups.expected_count = 组内各批次应交快照人数的真实合计
    （两组批次各自聚合：1 批 → 3，同周 2 批 → 6），不再恒 0。"""
    for day in ("2025-09-24", "2025-10-08", "2025-10-10"):
        p = _h_preview(client, v1_seed, assigned_date=day,
                       homework_type="G10练习")
        assert p.status_code == 200, p.text
        assert _confirm(client, p.json()["token"]).status_code == 200

    params = {"mode": "homeroom", "class_id": v1_seed.h6_id,
              "academic_year_id": v1_seed.ay_id,
              "homework_type": "G10练习"}

    week = client.get(f"{BASE}/homework/dashboard",
                      params={**params, "group_by": "week"}).json()
    assert [(g["label"], g["assignments"], g["expected_count"])
            for g in week["groups"]] == [
        ("2025-09-22", 1, 3), ("2025-10-06", 2, 6),
    ]
    assert all(g["submission_rate"] == 1.0 for g in week["groups"])

    month = client.get(f"{BASE}/homework/dashboard",
                       params={**params, "group_by": "month"}).json()
    assert [(g["label"], g["assignments"], g["expected_count"])
            for g in month["groups"]] == [
        ("2025-09-01", 1, 3), ("2025-10-01", 2, 6),
    ]
