"""P5 H01/H02：录入解析与 token 幂等（契约 docs/contracts/p5-homework.md §1）。

- H01：full（全交台账）+ 个人例外；detailed 两种行顺序结果一致；请求内
  同人两行矛盾 → 422；姓名歧义 422 列候选、学号消歧优先。
- H02：confirm 重试同 token 幂等不增；同日同科同种类两份各自独立
  （existing_batches 提示、revoked 不算）；预警 recent_missing 按日期排序。
"""

import json
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


def _subs_by_id(body):
    return {
        item["person_id"]: (item["status"], item.get("evaluation"))
        for item in body["assignment"]["submissions"]
    }


def test_h01_full_with_exception_and_counts(client, v1_seed):
    """full 展开全员 submitted 再应用例外（先全量后例外，行顺序无关）。"""
    resp = _preview(
        client,
        v1_seed,
        input={
            "kind": "full",
            "exceptions": [{"name_or_alias": "秦乙", "status": "excused"}],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [m["person_id"] for m in body["assignment"]["expected_members"]] == sorted(
        v1_seed.h_person_ids
    )
    subs = _subs_by_id(body)
    assert subs[v1_seed.jia_h_id] == ("submitted", None)
    assert subs[v1_seed.yi_h_id] == ("excused", None)
    assert subs[v1_seed.bing_h_id] == ("submitted", None)
    assert body["existing_batches"] == []

    confirmed = _confirm(client, body["token"])
    assert confirmed.status_code == 200, confirmed.text
    counts = confirmed.json()
    assert (counts["submitted"], counts["missing"], counts["excused"], counts["unknown"]) == (
        2, 0, 1, 0,
    )

    detail = client.get(
        f"/api/v1/homework/assignments/{counts['assignment_id']}",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id},
    )
    assert detail.status_code == 200, detail.text
    dbody = detail.json()
    assert dbody["status"] == "active"
    # 分母 = 快照 3 − excused 1 = 2，两人提交 → rate 1.0
    assert dbody["expected_count"] == 3
    assert dbody["submission_rate"] == 1.0
    assert dbody["rate_unavailable"] is False


def test_h01_detailed_row_order_equivalent(client, v1_seed):
    """detailed 两种行顺序解析结果完全一致（H01 红线）。"""
    rows_a = [
        {"name_or_alias": "秦甲", "status": "missing"},
        {"name_or_alias": "秦乙", "status": "submitted", "evaluation": "工整"},
    ]
    rows_b = list(reversed(rows_a))
    p1 = _preview(
        client, v1_seed,
        assigned_date="2025-09-11", homework_type="试卷订正",
        input={"kind": "detailed", "rows": rows_a},
    )
    p2 = _preview(
        client, v1_seed,
        assigned_date="2025-09-11", homework_type="试卷订正",
        input={"kind": "detailed", "rows": rows_b},
    )
    assert p1.status_code == 200 and p2.status_code == 200
    # 第二份 preview 提示第一份尚在（pending 不算 existing，确认后才算）
    assert p2.json()["existing_batches"] == []
    assert _subs_by_id(p1.json()) == _subs_by_id(p2.json())

    confirmed = _confirm(client, p1.json()["token"])
    assert confirmed.status_code == 200
    counts = confirmed.json()
    assert (counts["submitted"], counts["missing"]) == (1, 1)


def test_h01_conflicting_rows_same_person_rejected(client, v1_seed):
    """请求内同人两行不同状态 → 422（API 层同人矛盾；DB 唯一键由 R10 测）。"""
    rows = [
        {"name_or_alias": "秦甲", "status": "missing"},
        {"name_or_alias": "秦甲", "status": "submitted"},
    ]
    resp = _preview(
        client, v1_seed,
        assigned_date="2025-09-12", homework_type="课堂练习",
        input={"kind": "detailed", "rows": rows},
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_scope_param"

    # full 模式的例外同样互相矛盾 → 422
    resp_full = _preview(
        client, v1_seed,
        assigned_date="2025-09-12", homework_type="课堂练习",
        input={
            "kind": "full",
            "exceptions": [
                {"name_or_alias": "秦甲", "status": "excused"},
                {"name_or_alias": "秦甲", "status": "missing"},
            ],
        },
    )
    assert resp_full.status_code == 422

    # 相同人完全相同的行 → 去重不报错
    resp_dup = _preview(
        client, v1_seed,
        assigned_date="2025-09-12", homework_type="课堂练习",
        input={
            "kind": "detailed",
            "rows": [
                {"name_or_alias": "秦甲", "status": "missing"},
                {"name_or_alias": "秦甲", "status": "missing"},
            ],
        },
    )
    assert resp_dup.status_code == 200
    assert len(resp_dup.json()["assignment"]["submissions"]) == 1


def test_h01_patch_conflict_and_upsert(client, v1_seed):
    """PATCH：逐行 upsert + revision 递增；请求内同人矛盾 422；旧版 409。"""
    p = _preview(client, v1_seed, assigned_date="2025-09-13", homework_type="背诵默写")
    aid = _confirm(client, p.json()["token"]).json()["assignment_id"]
    url = f"/api/v1/homework/assignments/{aid}"
    params = {"mode": "homeroom", "class_id": v1_seed.h6_id}

    resp = client.patch(
        url,
        params=params,
        json={"revision": 1, "rows": [{"name_or_alias": "秦丙", "status": "missing"}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["revision"] == 2

    # 同人两行矛盾 → 422
    bad = client.patch(
        url,
        params=params,
        json={
            "revision": 2,
            "rows": [
                {"name_or_alias": "秦甲", "status": "missing"},
                {"name_or_alias": "秦甲", "status": "excused"},
            ],
        },
    )
    assert bad.status_code == 422
    assert bad.json()["error"] == "invalid_scope_param"

    # 旧 revision → 409 乐观锁冲突
    stale = client.patch(
        url,
        params=params,
        json={"revision": 1, "rows": [{"name_or_alias": "秦甲", "status": "missing"}]},
    )
    assert stale.status_code == 409
    assert stale.json()["error"] == "link_version_conflict"

    detail = client.get(url, params=params).json()
    by_pid = {s["person_id"]: s["status"] for s in detail["submissions"]}
    assert by_pid[v1_seed.bing_h_id] == "missing"


def test_h02_confirm_token_idempotent(client, v1_seed, db_session):
    """同 token 重试 confirm 幂等返回既有批次统计，不新增（H02）。"""
    p = _preview(client, v1_seed, assigned_date="2025-09-20", homework_type="周末作业")
    token = p.json()["token"]
    first = _confirm(client, token)
    assert first.status_code == 200
    aid_first = first.json()["assignment_id"]

    retry = _confirm(client, token)
    assert retry.status_code == 200, retry.text
    assert retry.json()["assignment_id"] == aid_first
    assert retry.json() == first.json()

    rows = (
        db_session.query(wm.HomeworkAssignment)
        .filter(wm.HomeworkAssignment.batch_token == token)
        .all()
    )
    assert len(rows) == 1

    # 已消费的 token 不能当作其他用途再次消费（R4 语义不回退）
    list_resp = client.get(
        "/api/v1/homework/assignments",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "from_date": "2025-09-20", "to_date": "2025-09-20"},
    )
    assert list_resp.json()["total"] == 1


def test_h02_same_day_same_type_two_batches_independent(client, v1_seed):
    """同日同科同种类两份各自独立成批次；existing_batches 提示且 revoked 不算。"""
    d1 = _preview(client, v1_seed, assigned_date="2025-09-21", homework_type="练习册")
    aid1 = _confirm(client, d1.json()["token"]).json()["assignment_id"]

    d2 = _preview(client, v1_seed, assigned_date="2025-09-21", homework_type="练习册")
    body2 = d2.json()
    assert [b["assignment_id"] for b in body2["existing_batches"]] == [aid1]
    assert any("不自动叠加" in w for w in body2["assignment"]["warnings"])
    confirmed2 = _confirm(client, body2["token"])
    assert confirmed2.status_code == 200
    aid2 = confirmed2.json()["assignment_id"]
    assert aid2 != aid1

    list_resp = client.get(
        "/api/v1/homework/assignments",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "from_date": "2025-09-21", "to_date": "2025-09-21"},
    ).json()
    assert list_resp["total"] == 2
    assert {i["assignment_id"] for i in list_resp["items"]} == {aid1, aid2}

    # 撤销第一份后：existing_batches 只剩 active 的 aid2（revoked 不算）
    revoked = client.delete(
        f"/api/v1/homework/assignments/{aid1}",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id},
    )
    assert revoked.status_code == 200, revoked.text
    d3 = _preview(client, v1_seed, assigned_date="2025-09-21", homework_type="练习册")
    assert [b["assignment_id"] for b in d3.json()["existing_batches"]] == [aid2]


def test_h02_warnings_recent_missing_sorted_by_date(client, v1_seed):
    """预警 recent_missing 按 assigned_date 降序（最近在前）。
    模块内共享 DB：甲另有 2025-09-11（earlier 用例）的缺交事件。"""
    for day in ("2025-09-30", "2025-10-01"):
        p = _preview(
            client, v1_seed,
            assigned_date=day, homework_type="预习作业",
            input={"kind": "detailed",
                   "rows": [{"name_or_alias": "秦甲", "status": "missing"}]},
        )
        assert _confirm(client, p.json()["token"]).status_code == 200

    resp = client.get(
        "/api/v1/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "min_missing": 2, "subject": "物理"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["basis"] == "events"
    jia = next(s for s in body["students"] if s["person_id"] == v1_seed.jia_h_id)
    dates = [e["assigned_date"] for e in jia["recent_missing"]]
    assert dates == sorted(dates, reverse=True)
    assert dates[:2] == ["2025-10-01", "2025-09-30"]
    assert jia["missing_count"] == 3


def test_h01_name_ambiguity_and_alias_disambiguation(client, v1_seed, db_session):
    """同班同名 → 422 列候选；学号（alias）与 person_id 精确消歧。
    本用例改动 T6 名册（加同名成员），置于模块最后执行。"""
    dup = wm.WsStudentIdentity(data_domain="teaching", display_name="秦甲·T")
    db_session.add(dup)
    db_session.flush()
    db_session.add(
        wm.TeachingClassMember(
            teaching_class_id=v1_seed.t6_id,
            identity_id=dup.id,
            valid_from=date(2025, 9, 1),
        )
    )
    db_session.commit()

    teach_payload = {
        "mode": "teaching",
        "teaching_class_id": v1_seed.t6_id,
        "academic_year_id": v1_seed.ay_id,
        "subject": "物理",
        "homework_type": "练习册",
        "assigned_date": "2025-09-25",
    }
    ambiguous = client.post(
        "/api/v1/homework/preview",
        json={
            **teach_payload,
            "input": {"kind": "detailed",
                      "rows": [{"name_or_alias": "秦甲·T", "status": "missing"}]},
        },
    )
    assert ambiguous.status_code == 422
    body = ambiguous.json()
    assert body["error"] == "invalid_scope_param"
    assert len(body["candidates"]) == 2

    # 学号消歧优先：裸号 01 在 T6 唯一命中甲·T
    by_alias = client.post(
        "/api/v1/homework/preview",
        json={
            **teach_payload,
            "input": {"kind": "detailed",
                      "rows": [{"name_or_alias": "2025T6-01", "status": "missing"}]},
        },
    )
    assert by_alias.status_code == 200, by_alias.text
    subs = by_alias.json()["assignment"]["submissions"]
    assert [(s["person_id"], s["status"]) for s in subs] == [
        (v1_seed.jia_t_id, "missing")
    ]

    # person_id 直达同样合法
    by_pid = client.post(
        "/api/v1/homework/preview",
        json={
            **teach_payload,
            "input": {"kind": "detailed",
                      "rows": [{"person_id": v1_seed.yi_t_id, "status": "excused"}]},
        },
    )
    assert by_pid.status_code == 200
    assert by_pid.json()["assignment"]["submissions"][0]["status"] == "excused"
