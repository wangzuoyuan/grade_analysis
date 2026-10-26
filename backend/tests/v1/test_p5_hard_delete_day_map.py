"""P5 作业批次硬删除 + 请假/出勤异常按天映射（2026-10-26 用户拍板）。

- 硬删除：DELETE 物理删除批次与逐人行（及其衍生的忘带/出勤溯源笔记），
  不再保留 status='revoked' 撤销记录；409 编辑冲突护栏与 G02 跨域边界
  保持不变；存量 revoked 行保留在库但列表不再下发。
- 按天映射：当天任何可见批次登记的请假（excused）与出勤异常（迟到/
  没来等，含考勤批次缺勤行）按人映射到当天其他可见批次——请假 → 非考勤
  批次一律按请假（显式已交行除外）；出勤异常 → 缺交行补注出勤文本
  （缺交计数保留、连缺跳过）。可见范围 = 本域班 + 经作业共享门的关联班
  批次（请假跨域生效），读取即映射、零数据迁移，考勤批次只作来源不作
  映射目标。计数/详情/列表 id 数组/预警/连缺/画像/确认回执/预览装饰
  同一口径。
"""

import json
from datetime import date as date_cls

from app.db import workspace_models as wm

BASE = "/api/v1"
DAY = "2025-10-20"
# v1_seed 为模块级夹具：同库跨用例复用，batch_token 唯一约束需要自增后缀
_TOKEN_SEQ = iter(range(10000))
# _seed_day 的跨用例缓存（v1_seed 模块级共享库）
_SEED_DAY_IDS: dict = {}


def _token_suffix() -> str:
    return f":seq{next(_TOKEN_SEQ)}"


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
                  expected_ids, rows, status="active", token_suffix=""):
    """直插批次；rows = [(person_id, status, evaluation|None)]。"""
    a = wm.HomeworkAssignment(
        data_domain=domain,
        class_ref_id=class_id,
        academic_year_id=ay_id,
        subject=subject,
        homework_type=homework_type,
        assigned_date=date_cls.fromisoformat(day),
        batch_token=f"hard-delete-daymap:{domain}:{day}:{homework_type}{token_suffix}",
        expected_members_json=json.dumps(expected_ids),
        revision=1,
        status=status,
    )
    db.add(a)
    db.flush()
    for pid, status_, evaluation in rows:
        db.add(wm.HomeworkSubmission(
            assignment_id=a.id, person_id=pid,
            submission_status=status_, evaluation=evaluation, revision=1,
        ))
    db.commit()
    return a


def _list_items(client, seed, *, mode, day=DAY):
    query = {"mode": mode, "academic_year_id": seed.ay_id,
             "from_date": day, "to_date": day}
    query["teaching_class_id" if mode == "teaching" else "class_id"] = (
        seed.t6_id if mode == "teaching" else seed.h6_id
    )
    resp = client.get(f"{BASE}/homework/assignments", params=query)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _item(body, subject, homework_type):
    return next(i for i in body["items"]
                if i["subject"] == subject and i["homework_type"] == homework_type)


def _detail(client, seed, aid, *, mode):
    params = {"mode": mode, "academic_year_id": seed.ay_id}
    params["teaching_class_id" if mode == "teaching" else "class_id"] = (
        seed.t6_id if mode == "teaching" else seed.h6_id
    )
    return client.get(f"{BASE}/homework/assignments/{aid}", params=params)


def _warnings(client, seed, *, mode):
    query = {"mode": mode, "min_missing": 1, "academic_year_id": seed.ay_id}
    query["teaching_class_id" if mode == "teaching" else "class_id"] = (
        seed.t6_id if mode == "teaching" else seed.h6_id
    )
    resp = client.get(f"{BASE}/homework/warnings", params=query)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _profile(client, seed, person_id, *, mode):
    query = {"mode": mode, "academic_year_id": seed.ay_id}
    query["teaching_class_id" if mode == "teaching" else "class_id"] = (
        seed.t6_id if mode == "teaching" else seed.h6_id
    )
    resp = client.get(f"{BASE}/homework/students/{person_id}", params=query)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _seed_day(client, seed, db):
    """同日三批次：教学物理（请假/迟到来源）+ 班主任数学卷子 + 班主任考勤。
    关联对：jia_h↔jia_t、yi_h↔yi_t；bing_h 不关联。v1_seed 是模块级夹具
    （同一内存库跨用例复用），重复调用时按缓存 id 复用已插入的批次。"""
    _set_scope(client, seed, ["roster", "current_subject_homework"])
    if _SEED_DAY_IDS.get("done"):
        rows = {
            a.id: a
            for a in db.query(wm.HomeworkAssignment).filter(
                wm.HomeworkAssignment.id.in_(list(_SEED_DAY_IDS["ids"]))
            ).all()
        }
        return rows[_SEED_DAY_IDS["physics"]], rows[_SEED_DAY_IDS["math"]], rows[_SEED_DAY_IDS["attend"]]
    physics = _insert_batch(
        db, domain="teaching", class_id=seed.t6_id, ay_id=seed.ay_id,
        subject="物理", homework_type="动量定理", day=DAY,
        expected_ids=seed.t6_person_ids,
        rows=[
            (seed.jia_t_id, "excused", None),          # 请假来源
            (seed.yi_t_id, "missing", "迟到"),          # 出勤异常来源
            (seed.ding_t_id, "submitted", None),
        ],
        token_suffix=_token_suffix(),
    )
    math = _insert_batch(
        db, domain="homeroom", class_id=seed.h6_id, ay_id=seed.ay_id,
        subject="数学", homework_type="卷子", day=DAY,
        expected_ids=seed.h_person_ids,
        rows=[
            (seed.jia_h_id, "missing", None),           # → 映射为请假
            (seed.yi_h_id, "missing", None),            # → 映射补注迟到
            (seed.bing_h_id, "missing", None),          # → 考勤来源补注缺席
        ],
        token_suffix=_token_suffix(),
    )
    attend = _insert_batch(
        db, domain="homeroom", class_id=seed.h6_id, ay_id=seed.ay_id,
        subject="考勤", homework_type="日报", day=DAY,
        expected_ids=seed.h_person_ids,
        rows=[(seed.bing_h_id, "missing", None)],       # 考勤缺勤：作来源不作目标
        token_suffix=_token_suffix(),
    )
    _SEED_DAY_IDS.update(done=True, ids=[physics.id, math.id, attend.id],
                         physics=physics.id, math=math.id, attend=attend.id)
    return physics, math, attend


# ───────────────────────── 硬删除 ─────────────────────────


def test_delete_is_physical_rows_notes_and_list_gone(client, v1_seed, db_session):
    """删除后批次/逐人行/溯源笔记全部消失；列表不再返回；同日同科同种类
    可直接重录（不再有撤销记录挡路）。"""
    p = _preview(
        client, v1_seed, mode="homeroom", day=DAY, homework_type="背诵默写",
        kind="detailed",
        rows=[{"name_or_alias": "秦甲", "status": "missing", "evaluation": "迟到"}],
    )
    assert p.status_code == 200, p.text
    aid = _confirm(client, p.json()["token"]).json()["assignment_id"]
    note = db_session.query(wm.WsStudentNote).filter(
        wm.WsStudentNote.source == f"homework:{aid}"
    ).count()
    assert note == 1  # 迟到已同步为独立溯源事实

    resp = client.delete(
        f"{BASE}/homework/assignments/{aid}",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "academic_year_id": v1_seed.ay_id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "deleted"

    assert _detail(client, v1_seed, aid, mode="homeroom").status_code == 404
    assert db_session.query(wm.HomeworkAssignment).filter(
        wm.HomeworkAssignment.id == aid).count() == 0
    assert db_session.query(wm.HomeworkSubmission).filter(
        wm.HomeworkSubmission.assignment_id == aid).count() == 0
    assert db_session.query(wm.WsStudentNote).filter(
        wm.WsStudentNote.source == f"homework:{aid}"
    ).count() == 0
    assert _list_items(client, v1_seed, mode="homeroom")["total"] == 0

    # 同日同科同种类可直接重录
    again = _preview(
        client, v1_seed, mode="homeroom", day=DAY, homework_type="背诵默写"
    )
    assert again.status_code == 200, again.text
    assert again.json()["existing_batches"] == []


def test_legacy_revoked_batches_not_listed(client, v1_seed, db_session):
    """存量 revoked 行保留在库但列表不再下发（不做破坏性迁移）。"""
    _insert_batch(
        db_session, domain="homeroom", class_id=v1_seed.h6_id,
        ay_id=v1_seed.ay_id, subject="英语", homework_type="旧撤销", day=DAY,
        expected_ids=v1_seed.h_person_ids,
        rows=[(v1_seed.jia_h_id, "missing", None)],
        status="revoked", token_suffix=_token_suffix(),
    )
    body = _list_items(client, v1_seed, mode="homeroom")
    assert all(i["status"] != "revoked" for i in body["items"])
    assert not any(i["homework_type"] == "旧撤销" for i in body["items"])


# ───────────────────────── 按天映射 ─────────────────────────


def test_day_leave_and_attendance_map_across_domains(client, v1_seed, db_session):
    """教学物理批次登记的请假/出勤异常映射到同日班主任数学批次：jia 缺交行
    →请假；yi 缺交行→补注迟到；bing 缺交行←考勤缺勤→补注缺席；考勤批次
    自身不被映射；教学侧读数不变。"""
    _seed_day(client, v1_seed, db_session)

    body = _list_items(client, v1_seed, mode="homeroom")
    math = _item(body, "数学", "卷子")
    assert set(math["excused_ids"]) == {v1_seed.jia_h_id}
    assert set(math["missing_ids"]) == {v1_seed.yi_h_id, v1_seed.bing_h_id}
    assert set(math["attendance_ids"]) == {v1_seed.yi_h_id, v1_seed.bing_h_id}
    assert math["excused"] == 1 and math["missing"] == 2 and math["submitted"] == 0

    attend = _item(body, "考勤", "日报")
    # 考勤批次是出勤事实本身：jia 的请假不写入考勤，bing 缺勤照常
    assert v1_seed.jia_h_id not in attend["excused_ids"]
    assert attend["excused_ids"] == []
    assert set(attend["attendance_ids"]) == {v1_seed.bing_h_id}
    assert attend["missing_ids"] == []  # 考勤缺勤不进学科缺交名单

    detail = _detail(client, v1_seed, math["assignment_id"], mode="homeroom").json()
    rows = {s["person_id"]: s for s in detail["submissions"]}
    assert rows[v1_seed.jia_h_id]["status"] == "excused"
    assert rows[v1_seed.jia_h_id]["attendance"] is None
    assert rows[v1_seed.yi_h_id]["status"] == "missing"
    assert rows[v1_seed.yi_h_id]["attendance"] == "迟到"
    assert rows[v1_seed.bing_h_id]["status"] == "missing"
    assert rows[v1_seed.bing_h_id]["attendance"] == "缺席"

    # 教学侧视角：物理批次读数照旧（映射不回写、不越 link.subject）
    t_body = _list_items(client, v1_seed, mode="teaching")
    physics = _item(t_body, "物理", "动量定理")
    assert set(physics["excused_ids"]) == {v1_seed.jia_t_id}
    assert set(physics["attendance_ids"]) == {v1_seed.yi_t_id}


def test_day_map_counts_warnings_streaks_and_rates(client, v1_seed, db_session):
    """映射贯通预警/连缺/画像/看板/确认回执：请假不进缺交计数、不打断
    也不累加连缺；出勤异常缺交不计纯缺交；分母扣除请假。"""
    _seed_day(client, v1_seed, db_session)
    # 阳性对照：次日 bing 纯缺交（当天无任何请假/出勤来源）应照常进预警
    _insert_batch(
        db_session, domain="homeroom", class_id=v1_seed.h6_id, ay_id=v1_seed.ay_id,
        subject="数学", homework_type="卷子", day="2025-10-21",
        expected_ids=v1_seed.h_person_ids,
        rows=[
            (v1_seed.jia_h_id, "submitted", None),
            (v1_seed.yi_h_id, "submitted", None),
            (v1_seed.bing_h_id, "missing", None),
        ],
        token_suffix=_token_suffix(),
    )

    warn = _warnings(client, v1_seed, mode="homeroom")
    by_pid = {s["person_id"]: s for s in warn["students"]}
    # jia 缺交被映射为请假 → 不进缺交计数；yi/bing 缺交行带出勤注记 → 非纯缺交
    assert v1_seed.jia_h_id not in by_pid
    assert v1_seed.yi_h_id not in by_pid
    assert by_pid[v1_seed.bing_h_id]["missing_count"] == 1
    assert [r["assigned_date"] for r in by_pid[v1_seed.bing_h_id]["recent_missing"]] == ["2025-10-21"]

    profile = _profile(client, v1_seed, v1_seed.jia_h_id, mode="homeroom")
    events = {
        (e["subject"], e["homework_type"]): e
        for e in profile["events"] if e["assigned_date"] == DAY
    }
    assert events[("数学", "卷子")]["status"] == "excused"
    assert events[("物理", "动量定理")]["status"] == "excused"
    assert profile["streaks"]["current_missing_streak"] == 0

    dash = client.get(
        f"{BASE}/homework/dashboard",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "academic_year_id": v1_seed.ay_id, "group_by": "day"},
    )
    assert dash.status_code == 200, dash.text
    group = next(g for g in dash.json()["groups"] if g["label"] == DAY)
    # 跨批次求和：物理投影应交 2（ding 未关联不可见）+ 数学 3 + 考勤 3；
    # excused = 物理 jia + 数学 jia；missing = 物理 yi + 数学 yi/bing + 考勤 bing
    assert group["expected_count"] == 8
    # submitted = 考勤批次里 jia/yi 的默认已交（考勤不作映射目标）
    assert group["excused"] == 2 and group["missing"] == 4 and group["submitted"] == 2

    # 确认回执同口径：full 模式新建批次（无行），jia 按映射显示请假
    p = _preview(client, v1_seed, mode="homeroom", day=DAY,
                 homework_type="错题集", subject="数学")
    assert p.status_code == 200, p.text
    preview_rows = {s["person_id"]: s for s in p.json()["assignment"]["submissions"]}
    assert preview_rows[v1_seed.jia_h_id]["status"] == "excused"
    assert preview_rows[v1_seed.yi_h_id]["status"] == "submitted"  # 出勤注记只落缺交行
    assert any("请假" in w for w in p.json()["assignment"]["warnings"])
    confirmed = _confirm(client, p.json()["token"]).json()
    assert confirmed["excused"] == 1 and confirmed["submitted"] == 2


def test_day_leave_overrides_submitted_rows(client, v1_seed, db_session):
    """请假按天无条件生效：full 台账展开的已交行同样翻转为请假
    （请假了就不可能有已交；认为确已交付应修正请假记录本身）。"""
    _set_scope(client, v1_seed, ["roster", "current_subject_homework"])
    _insert_batch(
        db_session, domain="teaching", class_id=v1_seed.t6_id, ay_id=v1_seed.ay_id,
        subject="物理", homework_type="动量定理", day=DAY,
        expected_ids=v1_seed.t6_person_ids,
        rows=[(v1_seed.jia_t_id, "excused", None)],
        token_suffix=_token_suffix(),
    )
    math = _insert_batch(
        db_session, domain="homeroom", class_id=v1_seed.h6_id, ay_id=v1_seed.ay_id,
        subject="数学", homework_type="卷子", day=DAY,
        expected_ids=v1_seed.h_person_ids,
        rows=[(v1_seed.jia_h_id, "submitted", None)],  # full 台账式已交行
        token_suffix=_token_suffix(),
    )
    body = _list_items(client, v1_seed, mode="homeroom")
    item = _item(body, "数学", "卷子")
    assert v1_seed.jia_h_id in item["excused_ids"]
    assert v1_seed.jia_h_id not in item["missing_ids"]
    detail = _detail(client, v1_seed, math.id, mode="homeroom").json()
    row = next(s for s in detail["submissions"] if s["person_id"] == v1_seed.jia_h_id)
    assert row["status"] == "excused"


def test_day_map_needs_active_share_gate(client, v1_seed, db_session):
    """共享关闭时教学批次的请假不再映射到班主任批次（隔离口径不变）。"""
    _physics, math, _attend = _seed_day(client, v1_seed, db_session)
    _set_scope(client, v1_seed, ["roster"])  # 关闭作业共享
    body = _list_items(client, v1_seed, mode="homeroom")
    item = next(i for i in body["items"] if i["assignment_id"] == math.id)
    assert v1_seed.jia_h_id in item["missing_ids"]
    assert item["excused_ids"] == []
