"""P5 作业两域单账本（2026-10-24 引入，2026-10-25 收紧冲突配对口径）：
录入跨侧冲突阻断 + 连缺/展示跨域合并。

- 录入阻断：current_subject_homework 共享开启时，对侧关联班同日同学科
  **同作业种类**已有 active 批次 → preview 422（details.conflict_batches），
  confirm 对已签发 token 复查（关掉 preview TTL 竞态）；不同种类 = 两份
  不同作业（同日两份作业的真实案例（匿名）：班主任侧《日常作业》+ 教学侧《周末卷订正》），
  放行不阻断；同侧同日重复维持软警告；共享关闭时两侧互不可见，不阻断。
- 连缺合并：班主任按学科、教学按单线，跨域批次并成一条线；同日**同种类**
  一侧 missing + 另一侧 submitted = 冲突日 → 按已交处理（打断连缺）、缺交行
  不进 missing_count/recent_missing、单列 conflicts；不同种类 = 真实缺交
  照实计数、不判冲突；两侧都缺 = missing；其他学科不受同学科冲突影响。
- 存量两本账经直插库造（API 已被阻断造不出来），对应生产先于本口径
  落库的历史数据。
"""

import json
from datetime import date as date_cls

from app.db import workspace_models as wm

BASE = "/api/v1"


def _set_scope(client, seed, categories):
    resp = client.post(
        f"{BASE}/shared/links/{seed.link_id}/share-scope",
        json={"share_categories": categories},
    )
    assert resp.status_code == 200, resp.text


def _preview(client, seed, *, mode, day, homework_type="练习册", subject="物理",
             kind="full", rows=None):
    payload = {
        "mode": mode,
        "academic_year_id": seed.ay_id,
        "subject": subject,
        "homework_type": homework_type,
        "assigned_date": day,
        "input": {"kind": kind, **({"rows": rows} if rows else {})},
    }
    payload["teaching_class_id" if mode == "teaching" else "class_id"] = (
        seed.t6_id if mode == "teaching" else seed.h6_id
    )
    return client.post(f"{BASE}/homework/preview", json=payload)


def _confirm(client, token):
    return client.post(f"{BASE}/homework/confirm", json={"token": token})


def _insert_batch(db, *, domain, class_id, ay_id, subject, homework_type, day,
                  expected_ids, rows):
    """直插批次（绕过 preview 阻断，模拟先于本口径的存量两本账）。"""
    a = wm.HomeworkAssignment(
        data_domain=domain,
        class_ref_id=class_id,
        academic_year_id=ay_id,
        subject=subject,
        homework_type=homework_type,
        assigned_date=date_cls.fromisoformat(day),
        batch_token=f"single-ledger:{domain}:{day}:{homework_type}",
        expected_members_json=json.dumps(expected_ids),
        revision=1,
        status="active",
    )
    db.add(a)
    db.flush()
    for pid, status in rows:
        db.add(wm.HomeworkSubmission(
            assignment_id=a.id, person_id=pid,
            submission_status=status, revision=1,
        ))
    db.commit()
    return a


def _warnings(client, seed, *, mode, **params):
    query = {"mode": mode, "min_missing": 1, "academic_year_id": seed.ay_id, **params}
    query["teaching_class_id" if mode == "teaching" else "class_id"] = (
        seed.t6_id if mode == "teaching" else seed.h6_id
    )
    resp = client.get(f"{BASE}/homework/warnings", params=query)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _student_row(body, person_id):
    return next(s for s in body["students"] if s["person_id"] == person_id)


def _profile(client, seed, person_id, *, mode):
    query = {"mode": mode, "academic_year_id": seed.ay_id}
    query["teaching_class_id" if mode == "teaching" else "class_id"] = (
        seed.t6_id if mode == "teaching" else seed.h6_id
    )
    resp = client.get(f"{BASE}/homework/students/{person_id}", params=query)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ───────────────────────── 录入阻断 ─────────────────────────


def test_preview_blocked_when_teaching_has_same_day_batch(client, v1_seed):
    """共享开启：T6 已录 10-20 物理 → H6 同日同学科 preview 422，details
    带 conflict_batches（对侧批次 id/种类/侧名）；错误码 invalid_scope_param。"""
    _set_scope(client, v1_seed, ["roster", "current_subject_homework"])
    ok = _preview(client, v1_seed, mode="teaching", day="2025-10-20")
    assert ok.status_code == 200, ok.text
    t_aid = _confirm(client, ok.json()["token"]).json()["assignment_id"]

    blocked = _preview(client, v1_seed, mode="homeroom", day="2025-10-20")
    assert blocked.status_code == 422, blocked.text
    body = blocked.json()
    assert body["error"] == "invalid_scope_param"
    assert body["conflict_batches"][0]["assignment_id"] == t_aid
    assert body["conflict_batches"][0]["side"] == "教学侧"
    assert "教学侧" in body["detail"]


def test_preview_blocked_reverse_direction(client, v1_seed):
    """反向对称：H6 已录 10-21 → T6 同日物理 preview 422（班主任侧）。"""
    ok = _preview(client, v1_seed, mode="homeroom", day="2025-10-21")
    assert ok.status_code == 200, ok.text
    _confirm(client, ok.json()["token"])

    blocked = _preview(client, v1_seed, mode="teaching", day="2025-10-21")
    assert blocked.status_code == 422, blocked.text
    body = blocked.json()
    assert body["conflict_batches"][0]["side"] == "班主任侧"


def test_preview_passes_different_day_and_other_subject(client, v1_seed):
    """不同日（10-22）放行；同日但非关联学科（语文，link.subject=物理）
    放行——阻断只作用于两域共享的那门学科。"""
    ok = _preview(client, v1_seed, mode="homeroom", day="2025-10-22")
    assert ok.status_code == 200, ok.text
    lit = _preview(
        client, v1_seed, mode="homeroom", day="2025-10-21",
        subject="语文", homework_type="默写",
    )
    assert lit.status_code == 200, lit.text


def test_same_side_same_day_duplicate_stays_soft_warning(client, v1_seed):
    """同侧同日同科同种类重复：维持既有软警告（warnings 列表），不 422。"""
    first = _preview(client, v1_seed, mode="homeroom", day="2025-10-23")
    assert first.status_code == 200, first.text
    _confirm(client, first.json()["token"])

    second = _preview(client, v1_seed, mode="homeroom", day="2025-10-23")
    assert second.status_code == 200, second.text
    assert any("同日同科同种类" in w for w in second.json()["assignment"]["warnings"])


def test_confirm_rejects_cross_side_appeared_after_preview(client, v1_seed):
    """竞态关死：H6 preview 成功（当时无对侧批次）→ T6 同日录批次 →
    H6 旧 token confirm 422，零写入。"""
    pending = _preview(client, v1_seed, mode="homeroom", day="2025-10-24")
    assert pending.status_code == 200, pending.text
    token = pending.json()["token"]

    t_ok = _preview(client, v1_seed, mode="teaching", day="2025-10-24")
    assert t_ok.status_code == 200, t_ok.text
    _confirm(client, t_ok.json()["token"])

    late = _confirm(client, token)
    assert late.status_code == 422, late.text
    assert "conflict_batches" in late.json()


def test_no_block_when_share_scope_closed(client, v1_seed):
    """共享关闭（无 current_subject_homework）：两域互不可见，同日同学科
    双录不阻断（也就无冲突合并——各算各的，隔离语义保持）。"""
    _set_scope(client, v1_seed, ["roster"])
    ok = _preview(client, v1_seed, mode="teaching", day="2025-10-25")
    assert ok.status_code == 200, ok.text
    _confirm(client, ok.json()["token"])
    h_ok = _preview(client, v1_seed, mode="homeroom", day="2025-10-25")
    assert h_ok.status_code == 200, h_ok.text
    # 恢复共享，供后续用例
    _set_scope(client, v1_seed, ["roster", "current_subject_homework"])


# ───────────────────── 连缺跨域合并（存量两本账，直插库） ─────────────────────


def _seed_double_ledger(client, seed, db_session):
    """两本账式存量（真实案例匿名化）：H6 物理 11-01 甲默认已交（屏障）、
    11-03 甲缺《卷二》+ T6 同日同种类《卷二》甲·T 默认已交（**同种类**
    冲突日，2026-10-25 口径下两侧批次种类必须一致才判冲突）、11-04 甲缺；
    H6 语文 11-03 甲缺（同学科冲突不殃及其他学科）。模块内 DB 共享，
    幂等：已造过即跳过。"""
    _set_scope(client, seed, ["roster", "current_subject_homework"])
    exists = (
        db_session.query(wm.HomeworkAssignment)
        .filter(
            wm.HomeworkAssignment.batch_token
            == "single-ledger:homeroom:2025-11-03:卷二"
        )
        .one_or_none()
    )
    if exists is not None:
        return
    _insert_batch(
        db_session, domain="homeroom", class_id=seed.h6_id, ay_id=seed.ay_id,
        subject="物理", homework_type="练习册", day="2025-11-01",
        expected_ids=[seed.jia_h_id, seed.yi_h_id], rows=[],
    )
    _insert_batch(
        db_session, domain="homeroom", class_id=seed.h6_id, ay_id=seed.ay_id,
        subject="物理", homework_type="卷二", day="2025-11-03",
        expected_ids=[seed.jia_h_id, seed.yi_h_id],
        rows=[(seed.jia_h_id, "missing")],
    )
    _insert_batch(
        db_session, domain="teaching", class_id=seed.t6_id, ay_id=seed.ay_id,
        subject="物理", homework_type="卷二", day="2025-11-03",
        expected_ids=[seed.jia_t_id, seed.yi_t_id], rows=[],
    )
    _insert_batch(
        db_session, domain="homeroom", class_id=seed.h6_id, ay_id=seed.ay_id,
        subject="物理", homework_type="日常作业", day="2025-11-04",
        expected_ids=[seed.jia_h_id, seed.yi_h_id],
        rows=[(seed.jia_h_id, "missing")],
    )
    _insert_batch(
        db_session, domain="homeroom", class_id=seed.h6_id, ay_id=seed.ay_id,
        subject="语文", homework_type="默写", day="2025-11-03",
        expected_ids=[seed.jia_h_id, seed.yi_h_id],
        rows=[(seed.jia_h_id, "missing")],
    )


def test_conflict_day_breaks_streak_and_excluded_from_count(
    client, v1_seed, db_session
):
    """教学侧读：冲突日 11-03 按已交处理 → current=1（合并前两本账
    各算各会得 2）；missing_count 剔除冲突行；conflicts 单列；
    recent_missing 只剩 11-04。"""
    _seed_double_ledger(client, v1_seed, db_session)

    row = _student_row(_warnings(client, v1_seed, mode="teaching"), v1_seed.jia_t_id)
    assert row["current_streak"] == 1
    assert row["missing_count"] == 1
    assert [c["date"] for c in row["conflicts"]] == ["2025-11-03"]
    assert [r["assigned_date"] for r in row["recent_missing"]] == ["2025-11-04"]


def test_conflict_day_profile_conflicts_and_shared_rows(client, v1_seed, db_session):
    """画像端点：conflicts 含两侧批次与状态；events 带 data_domain，
    跨域共享行（教学端读到的 homeroom 批次）可辨识。"""
    _seed_double_ledger(client, v1_seed, db_session)

    body = _profile(client, v1_seed, v1_seed.jia_t_id, mode="teaching")
    assert body["streaks"]["current_missing_streak"] == 1
    conflict = next(c for c in body["conflicts"] if c["date"] == "2025-11-03")
    assert conflict["subject"] == "物理"
    by_domain = {b["data_domain"]: b for b in conflict["batches"]}
    assert by_domain["homeroom"]["status"] == "missing"
    assert by_domain["teaching"]["status"] == "submitted"

    events = {(e["assigned_date"], e["data_domain"]): e for e in body["events"]}
    shared = events[("2025-11-03", "homeroom")]
    assert shared["status"] == "missing"  # 共享投影行，前端标「班主任侧」
    own = events[("2025-11-03", "teaching")]
    assert own["status"] == "submitted"


def test_homeroom_side_symmetric_merge(client, v1_seed, db_session):
    """班主任侧读同一份数据：物理线同样合并（冲突日打断 → current=1）；
    语文 11-03 的缺交不受物理冲突影响（missing_count 含语文行；预警单行
    胜出线=物理，平手按最后事件日 11-04 > 语文 11-03）。"""
    _seed_double_ledger(client, v1_seed, db_session)

    row = _student_row(_warnings(client, v1_seed, mode="homeroom"), v1_seed.jia_h_id)
    assert row["streak_subject"] == "物理"
    assert row["current_streak"] == 1
    assert [c["date"] for c in row["conflicts"]] == ["2025-11-03"]
    # 11-04 物理 + 11-03 语文（11-03 物理卷二已被冲突剔除）
    assert row["missing_count"] == 2
    assert {r["assigned_date"] for r in row["recent_missing"]} == {
        "2025-11-04", "2025-11-03",
    }


def test_both_sides_missing_same_day_stays_missing(client, v1_seed, db_session):
    """两侧同日都缺 → 该天 missing（不是冲突），连缺成链 current=2；
    11-05 全交屏障日隔离前序用例遗留缺交（模块内 DB 共享）。"""
    _set_scope(client, v1_seed, ["roster", "current_subject_homework"])
    _insert_batch(
        db_session, domain="homeroom", class_id=v1_seed.h6_id, ay_id=v1_seed.ay_id,
        subject="物理", homework_type="练习册", day="2025-11-05",
        expected_ids=[v1_seed.jia_h_id], rows=[],
    )
    _insert_batch(
        db_session, domain="homeroom", class_id=v1_seed.h6_id, ay_id=v1_seed.ay_id,
        subject="物理", homework_type="练习册", day="2025-11-06",
        expected_ids=[v1_seed.jia_h_id],
        rows=[(v1_seed.jia_h_id, "missing")],
    )
    _insert_batch(
        db_session, domain="teaching", class_id=v1_seed.t6_id, ay_id=v1_seed.ay_id,
        subject="物理", homework_type="实验册", day="2025-11-06",
        expected_ids=[v1_seed.jia_t_id],
        rows=[(v1_seed.jia_t_id, "missing")],
    )
    _insert_batch(
        db_session, domain="homeroom", class_id=v1_seed.h6_id, ay_id=v1_seed.ay_id,
        subject="物理", homework_type="日常作业", day="2025-11-07",
        expected_ids=[v1_seed.jia_h_id],
        rows=[(v1_seed.jia_h_id, "missing")],
    )

    row = _student_row(_warnings(client, v1_seed, mode="teaching"), v1_seed.jia_t_id)
    assert row["current_streak"] == 2
    # 两侧都缺的 11-06 不是冲突日（模块内前序用例的 11-03 冲突仍在）
    assert all(c["date"] != "2025-11-06" for c in row["conflicts"])


def test_different_type_same_day_is_two_assignments_not_conflict(
    client, v1_seed, db_session
):
    """2026-10-25 用户裁决（同日两份作业的真实案例（匿名））：同日同学科**不同种类** = 两份
    不同作业——①录入放行（旧口径 422 拦截、逼老师改日期）；②缺交照实
    计数（进 missing_count/recent_missing），不判冲突、无琥珀提示。
    11-19 全交屏障 + 11-20 H《日常作业》甲缺 + T《周末卷订正》全交。"""
    _set_scope(client, v1_seed, ["roster", "current_subject_homework"])
    barrier = _preview(client, v1_seed, mode="homeroom", day="2025-11-19")
    assert barrier.status_code == 200, barrier.text
    _confirm(client, barrier.json()["token"])
    # H6 先录《日常作业》（甲缺），T6 同日再录《周末卷订正》→ 放行
    h_miss = _preview(
        client, v1_seed, mode="homeroom", day="2025-11-20",
        homework_type="日常作业", kind="detailed",
        rows=[{"person_id": v1_seed.jia_h_id, "status": "missing"}],
    )
    assert h_miss.status_code == 200, h_miss.text
    _confirm(client, h_miss.json()["token"])
    t_ok = _preview(
        client, v1_seed, mode="teaching", day="2025-11-20",
        homework_type="周末卷订正",
    )
    assert t_ok.status_code == 200, t_ok.text
    _confirm(client, t_ok.json()["token"])

    row = _student_row(_warnings(client, v1_seed, mode="teaching"), v1_seed.jia_t_id)
    assert row["current_streak"] == 1  # 11-20 真缺计入；11-19 全交屏障打断
    assert all(c["date"] != "2025-11-20" for c in row["conflicts"])
    assert row["recent_missing"][0]["assigned_date"] == "2025-11-20"

    profile = _profile(client, v1_seed, v1_seed.jia_t_id, mode="teaching")
    assert all(c["date"] != "2025-11-20" for c in profile["conflicts"])
    day_events = [
        e for e in profile["events"] if e["assigned_date"] == "2025-11-20"
    ]
    assert {(e["data_domain"], e["status"]) for e in day_events} == {
        ("homeroom", "missing"),
        ("teaching", "submitted"),
    }


def test_legacy_line_also_detects_conflicts(client, v1_seed, db_session):
    """legacy 线（9 月迁移批次无快照）同样判冲突：同日**同种类**一侧缺、
    另一侧交 → 冲突日单列、连缺按已交 break（11-12 H/T 均《卷子》）；同日
    不同种类（11-14 H《周末卷》缺 + T《周末卷订正》交）不判冲突、缺交照实
    进明细；班主任侧 conflicts 不再因 legacy 分支漏报（真实两本账案例回归）。
    轴上加入 migration 批次后整条物理线走 legacy 分支，此用例须最后跑
    （前序用例依赖非 legacy 轴）。"""
    _set_scope(client, v1_seed, ["roster", "current_subject_homework"])
    # migration 批次：无 expected 快照 → 物理线整线转 legacy 分支
    _insert_batch(
        db_session, domain="homeroom", class_id=v1_seed.h6_id, ay_id=v1_seed.ay_id,
        subject="物理", homework_type="迁移历史", day="2025-11-10",
        expected_ids=None, rows=[(v1_seed.jia_h_id, "missing")],
    )
    # 11-12：一侧缺（H6）+ 另一侧**同种类**交（T6 默认已交）→ 冲突日
    _insert_batch(
        db_session, domain="homeroom", class_id=v1_seed.h6_id, ay_id=v1_seed.ay_id,
        subject="物理", homework_type="卷子", day="2025-11-12",
        expected_ids=[v1_seed.jia_h_id],
        rows=[(v1_seed.jia_h_id, "missing")],
    )
    _insert_batch(
        db_session, domain="teaching", class_id=v1_seed.t6_id, ay_id=v1_seed.ay_id,
        subject="物理", homework_type="卷子", day="2025-11-12",
        expected_ids=[v1_seed.jia_t_id], rows=[],
    )
    # 11-13：H6 缺（冲突之后的真缺）
    _insert_batch(
        db_session, domain="homeroom", class_id=v1_seed.h6_id, ay_id=v1_seed.ay_id,
        subject="物理", homework_type="日常作业", day="2025-11-13",
        expected_ids=[v1_seed.jia_h_id],
        rows=[(v1_seed.jia_h_id, "missing")],
    )
    # 11-14：同日不同种类（H6《周末卷》缺 + T6《周末卷订正》默认已交）
    # → 不是冲突日，H6 缺交照实保留
    _insert_batch(
        db_session, domain="homeroom", class_id=v1_seed.h6_id, ay_id=v1_seed.ay_id,
        subject="物理", homework_type="周末卷", day="2025-11-14",
        expected_ids=[v1_seed.jia_h_id],
        rows=[(v1_seed.jia_h_id, "missing")],
    )
    _insert_batch(
        db_session, domain="teaching", class_id=v1_seed.t6_id, ay_id=v1_seed.ay_id,
        subject="物理", homework_type="周末卷订正", day="2025-11-14",
        expected_ids=[v1_seed.jia_t_id], rows=[],
    )

    body = _profile(client, v1_seed, v1_seed.jia_h_id, mode="homeroom")
    physics_conflicts = [c for c in body["conflicts"] if c["subject"] == "物理"]
    assert any(c["date"] == "2025-11-12" for c in physics_conflicts), body["conflicts"]
    # 11-14 不同种类 = 两份作业，不得判冲突
    assert all(c["date"] != "2025-11-14" for c in physics_conflicts), body["conflicts"]
    # legacy 轴只数 current：11-20 缺（前序用例）→ 11-19 全交屏障 break，
    # 数值恒 1；语文线（非 legacy）current 同为 1，winner 平手按最后事件日
    # 取物理线，故只断言数值、不断言 basis
    assert body["streaks"]["current_missing_streak"] == 1
    # 11-14 缺交行未被冲突剔除（进最近缺交证据）
    row = _student_row(_warnings(client, v1_seed, mode="homeroom"), v1_seed.jia_h_id)
    assert "2025-11-14" in {r["assigned_date"] for r in row["recent_missing"]}
