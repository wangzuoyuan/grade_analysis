"""G04 回归：换届撤销的快照一致性核验（契约 p4-students.md §2.2 v2）。

undo 不得只凭 created_at 检测新写入——confirm 之后任何对新建/收尾行的后续
编辑（改座号、追加学号、离班、离班又恢复）都必须让该生进入 conflicted 保留
现状，绝不删除已被编辑过的行。全部场景走正式 API：preview → confirm →
（PATCH / alias / archive）→ undo。

用例按定义顺序共享模块级数据；每个场景自备一轮 preview→confirm→undo。
"""

from datetime import date

API = "/api/v1"
STATE: dict = {}


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _set_active_grade(db, value):
    """切换工作年级：换届 preview/confirm 用 2（旧学年绑定），改后续编辑
    要命中新班学籍须临时切 3（作用域按 active_grade 推导绑定班）。"""
    from app.db.models import HomeworkSetting

    row = db.query(HomeworkSetting).filter_by(key="active_grade").one()
    row.value = str(value)
    db.commit()


def _set_high3_binding(db, class_num):
    from app.db.models import Teacher

    teacher = db.query(Teacher).first()
    teacher.target_class_high3 = class_num
    db.commit()


def _new_class(db, to_year_id):
    from app.db import workspace_models as wm

    return (
        db.query(wm.AdministrativeClass)
        .filter_by(academic_year_id=to_year_id, grade=3, class_num=6)
        .one_or_none()
    )


def _old_alias(db, seed, pid, value):
    """旧学年（v1_seed 学年）alias 行：v2.2/G06 起种子 alias 已规范标注
    本学年（不再是 NULL），按 identity + 值 + seed 学年唯一定位。"""
    from app.db import workspace_models as wm

    return (
        db.query(wm.WsStudentAlias)
        .filter(
            wm.WsStudentAlias.identity_id == pid,
            wm.WsStudentAlias.alias_value == value,
            wm.WsStudentAlias.academic_year_id == seed.ay_id,
        )
        .one()
    )


def _preview(client, seed):
    r = client.get(
        f"{API}/homeroom/rollover/preview",
        params={"from_academic_year_id": seed.ay_id},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _confirm(client, seed):
    """一轮 preview→confirm（aliases 全部沿用建议值）。"""
    body = _preview(client, seed)
    r = client.post(f"{API}/homeroom/rollover", json={"token": body["token"]})
    assert r.status_code == 200, r.text
    return body["token"], r.json()


def _old_aliases(seed):
    return {
        seed.jia_h_id: "2025H6-01",
        seed.yi_h_id: "2025H6-02",
        seed.bing_h_id: "2025H6-03",
    }


def _wipe_rollover_state(db, seed, to_year_id):
    """场景间清理：把上一轮换届写入（含 conflicted 保留的行与追加的
    alias）清回换届前状态。conflicted 行按设计被撤销保留，若不清掉，
    下一轮 confirm 会因幂等守卫跳过重建、该行不进新快照，场景就不再
    独立。每个漂移场景开头调用，保证 confirm 新建全部三人的行。"""
    from app.db import workspace_models as wm

    cls = _new_class(db, to_year_id)
    if cls is not None:
        db.query(wm.Enrollment).filter_by(admin_class_id=cls.id).delete(
            synchronize_session=False
        )
        db.delete(cls)
    db.query(wm.WsStudentAlias).filter_by(
        academic_year_id=to_year_id, data_domain="homeroom"
    ).delete(synchronize_session=False)
    for pid, value in _old_aliases(seed).items():
        _old_alias(db, seed, pid, value).valid_to = None
    db.commit()


def test_setup_and_clean_undo_rolls_back_everything(client, v1_seed):
    """无任何后续编辑 → undo 正常回滚（快照完全一致，零 conflicted）。"""
    # 建下一学年（2026-2027）供换届
    r = client.post(
        f"{API}/shared/academic-years",
        json={"name": "2026-2027", "start_date": "2026-09-01", "end_date": "2027-07-15"},
    )
    assert r.status_code == 200
    STATE["to_year_id"] = r.json()["id"]

    db = _db()
    try:
        _set_high3_binding(db, 6)
    finally:
        db.close()

    token, result = _confirm(client, v1_seed)
    assert result["rolled_over"] == 3 and result["class_created"] is True

    # confirm → undo 之间不做任何编辑
    r = client.post(f"{API}/homeroom/rollover/{token}/undo")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["conflicted"] == []
    assert body["undone"] == 3
    assert body["class_removed"] is True

    db = _db()
    try:
        to_year_id = STATE["to_year_id"]
        # 新班已删：学籍零残留
        assert _new_class(db, to_year_id) is None
        # 目标学年 alias 全部回滚；旧 alias 恢复 valid_to=NULL
        from app.db import workspace_models as wm

        assert (
            db.query(wm.WsStudentAlias)
            .filter_by(academic_year_id=to_year_id, data_domain="homeroom")
            .count()
            == 0
        )
        for pid, value in _old_aliases(v1_seed).items():
            assert _old_alias(db, v1_seed, pid, value).valid_to is None
    finally:
        db.close()


def test_undo_conflicts_when_seat_no_edited_after_confirm(client, v1_seed):
    """换届 → 改新学年座号 → undo：该生 conflicted，改过的学籍行保留。"""
    to_year_id = STATE["to_year_id"]
    db = _db()
    try:
        _wipe_rollover_state(db, v1_seed, to_year_id)
    finally:
        db.close()
    token, result = _confirm(client, v1_seed)
    assert result["class_created"] is True  # 清理后班级重建，三人的行都进快照

    # 正式 API 改座号：工作口径切到高三（active_grade=3）才能命中新班学籍
    db = _db()
    try:
        _set_active_grade(db, 3)
    finally:
        db.close()
    r = client.patch(
        f"{API}/homeroom/students/{v1_seed.jia_h_id}",
        json={"seat_no": 99},
        params={"academic_year_id": to_year_id},
    )
    assert r.status_code == 200, r.text
    db = _db()
    try:
        _set_active_grade(db, 2)
    finally:
        db.close()

    r = client.post(f"{API}/homeroom/rollover/{token}/undo")
    assert r.status_code == 200, r.text
    body = r.json()
    conflicted_ids = [c["person_id"] for c in body["conflicted"]]
    assert conflicted_ids == [v1_seed.jia_h_id]
    assert "学籍" in body["conflicted"][0]["reason"]
    assert body["undone"] == 2
    assert body["class_removed"] is False  # 甲的学籍保留在新班

    db = _db()
    try:
        from app.db import workspace_models as wm

        cls = _new_class(db, to_year_id)
        # 甲：被改过的学籍行原样保留（seat_no=99），不删除不回改
        kept = (
            db.query(wm.Enrollment)
            .filter_by(admin_class_id=cls.id, identity_id=v1_seed.jia_h_id)
            .one()
        )
        assert kept.seat_no == 99 and kept.status == "active"
        # 甲的新学段 alias 同样保留（_confirm 沿用建议值=旧号，未动 alias）
        kept_alias = (
            db.query(wm.WsStudentAlias)
            .filter_by(identity_id=v1_seed.jia_h_id, academic_year_id=to_year_id)
            .all()
        )
        assert [a.alias_value for a in kept_alias] == ["2025H6-01"]
        # 甲的旧 alias 收尾不恢复
        assert _old_alias(db, v1_seed, v1_seed.jia_h_id, "2025H6-01").valid_to == date(2026, 8, 31)
        # 乙丙：正常回滚
        for pid in (v1_seed.yi_h_id, v1_seed.bing_h_id):
            assert (
                db.query(wm.Enrollment)
                .filter_by(admin_class_id=cls.id, identity_id=pid)
                .count()
                == 0
            )
            assert _old_alias(db, v1_seed, pid, _old_aliases(v1_seed)[pid]).valid_to is None
    finally:
        db.close()


def test_undo_conflicts_when_alias_appended_after_confirm(client, v1_seed):
    """换届 → 追加新学号（换届 alias 被收尾）→ undo：该生 conflicted。"""
    to_year_id = STATE["to_year_id"]
    db = _db()
    try:
        _wipe_rollover_state(db, v1_seed, to_year_id)
    finally:
        db.close()
    token, _result = _confirm(client, v1_seed)

    db = _db()
    try:
        _set_active_grade(db, 3)
    finally:
        db.close()
    # 追加学号会把换届新建的开放 alias 行收尾（valid_to=前一日）→ 快照漂移
    r = client.post(
        f"{API}/homeroom/students/{v1_seed.yi_h_id}/alias",
        json={"alias": "2026H6-02X", "valid_from": "2026-10-01"},
        params={"academic_year_id": to_year_id},
    )
    assert r.status_code == 200, r.text
    db = _db()
    try:
        _set_active_grade(db, 2)
    finally:
        db.close()

    r = client.post(f"{API}/homeroom/rollover/{token}/undo")
    assert r.status_code == 200, r.text
    body = r.json()
    conflicted = {c["person_id"]: c["reason"] for c in body["conflicted"]}
    assert set(conflicted) == {v1_seed.yi_h_id}
    assert "学号" in conflicted[v1_seed.yi_h_id]

    db = _db()
    try:
        from app.db import workspace_models as wm

        cls = _new_class(db, to_year_id)
        # 乙：换届 alias 行（建议值=旧号）保留且保持被追加学号收尾后的状态；
        # 追加的行不删
        rows = (
            db.query(wm.WsStudentAlias)
            .filter_by(identity_id=v1_seed.yi_h_id, academic_year_id=to_year_id)
            .all()
        )
        by_value = {a.alias_value: a for a in rows}
        assert set(by_value) == {"2025H6-02", "2026H6-02X"}
        assert by_value["2025H6-02"].valid_to == date(2026, 9, 30)
        assert by_value["2026H6-02X"].valid_to is None
        # 乙的学籍仍在（未列入撤销）
        assert (
            db.query(wm.Enrollment)
            .filter_by(admin_class_id=cls.id, identity_id=v1_seed.yi_h_id)
            .count()
            == 1
        )
    finally:
        db.close()


def test_undo_conflicts_when_archived_or_restored_after_confirm(client, v1_seed):
    """换届 → 甲离班不恢复 / 丙离班又恢复 → undo：两人均 conflicted。

    丙的字段值被"恢复在班"改回快照原样（active/valid_to=NULL），只有
    updated_at 能证明该行被后续改写——这正是 G04 要求快照核验的反例。"""
    to_year_id = STATE["to_year_id"]
    db = _db()
    try:
        _wipe_rollover_state(db, v1_seed, to_year_id)
    finally:
        db.close()
    token, _result = _confirm(client, v1_seed)

    db = _db()
    try:
        _set_active_grade(db, 3)
    finally:
        db.close()
    r = client.post(
        f"{API}/homeroom/students/{v1_seed.jia_h_id}/archive",
        json={"status": "graduated", "valid_to": "2026-12-31"},
        params={"academic_year_id": to_year_id},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"{API}/homeroom/students/{v1_seed.bing_h_id}/archive",
        json={"status": "transferred", "valid_to": "2026-12-31"},
        params={"academic_year_id": to_year_id},
    )
    assert r.status_code == 200, r.text
    # 丙恢复在班：status/valid_to 回到快照值，但行已被改写
    r = client.post(
        f"{API}/homeroom/students/{v1_seed.bing_h_id}/archive",
        json={"status": "active"},
        params={"academic_year_id": to_year_id},
    )
    assert r.status_code == 200, r.text
    db = _db()
    try:
        _set_active_grade(db, 2)
    finally:
        db.close()

    r = client.post(f"{API}/homeroom/rollover/{token}/undo")
    assert r.status_code == 200, r.text
    body = r.json()
    conflicted = {c["person_id"]: c["reason"] for c in body["conflicted"]}
    assert set(conflicted) == {v1_seed.jia_h_id, v1_seed.bing_h_id}
    assert all("学籍" in reason for reason in conflicted.values())
    assert body["undone"] == 1  # 只有乙回滚

    db = _db()
    try:
        from app.db import workspace_models as wm

        cls = _new_class(db, to_year_id)
        # 甲：离班状态原样保留，不被撤销删除
        jia = (
            db.query(wm.Enrollment)
            .filter_by(admin_class_id=cls.id, identity_id=v1_seed.jia_h_id)
            .one()
        )
        assert jia.status == "graduated" and str(jia.valid_to) == "2026-12-31"
        # 丙：恢复在班后的现状保留（active/valid_to=NULL）
        bing = (
            db.query(wm.Enrollment)
            .filter_by(admin_class_id=cls.id, identity_id=v1_seed.bing_h_id)
            .one()
        )
        assert bing.status == "active" and bing.valid_to is None
        # token 终态
        assert (
            db.query(wm.ImportBatch).filter_by(token=token).one().status
            == "confirmed_undo"
        )
    finally:
        db.close()
