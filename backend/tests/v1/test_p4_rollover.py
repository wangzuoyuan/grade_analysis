"""P4 契约用例：换届 preview→confirm→undo 全流程（I01）+ R4 token 语义。

本文件用例按定义顺序共享 v1_seed 的模块级数据；confirm 用例会把
active_grade 临时切到 3 验证新学年名册，用例收尾恢复为 2（后续 preview
都以旧学年为起点）。STATE 字典承载跨用例的 confirm token。
"""

from datetime import date

API = "/api/v1"
STATE: dict = {}


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _set_active_grade(db, value):
    from app.db.models import HomeworkSetting

    row = db.query(HomeworkSetting).filter_by(key="active_grade").one()
    row.value = str(value)
    db.commit()


def _set_high3_binding(db, class_num):
    from app.db.models import Teacher

    teacher = db.query(Teacher).first()
    teacher.target_class_high3 = class_num
    db.commit()


def _to_year_id(db):
    from app.db import workspace_models as wm

    return db.query(wm.AcademicYear).filter_by(name="2026-2027").one().id


def _new_class(db, to_year_id):
    from app.db import workspace_models as wm

    return (
        db.query(wm.AdministrativeClass)
        .filter_by(academic_year_id=to_year_id, grade=3, class_num=6)
        .one_or_none()
    )


def _preview(client, seed):
    r = client.get(
        f"{API}/homeroom/rollover/preview",
        params={"from_academic_year_id": seed.ay_id},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _write_counts(db, seed, to_year_id):
    """新班学籍数 + 目标学年 alias 数（零写入断言用）。"""
    from app.db import workspace_models as wm

    cls = _new_class(db, to_year_id)
    enr = (
        db.query(wm.Enrollment).filter_by(admin_class_id=cls.id).count()
        if cls is not None
        else 0
    )
    als = (
        db.query(wm.WsStudentAlias)
        .filter_by(academic_year_id=to_year_id, data_domain="homeroom")
        .count()
    )
    return enr, als


def _old_alias(db, seed, pid, value):
    """旧学年（v1_seed 学年）alias 行。

    v2.2/G06 起 v1_seed 的 alias 已规范标注本学年（不再是 NULL），旧别名
    行按 identity + 值 + seed 学年定位；redo 后同值行会跨学年重复，该
    过滤同时保证唯一定位到旧行。
    """
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


def test_preview_requires_existing_next_year(client, v1_seed):
    # 无新学年 → 409 workspace_not_configured（提示先建学年）
    r = client.get(
        f"{API}/homeroom/rollover/preview",
        params={"from_academic_year_id": v1_seed.ay_id},
    )
    assert r.status_code == 409
    assert r.json()["error"] == "workspace_not_configured"
    assert "学年" in r.json()["detail"]

    # 参数缺失 → 422；不存在的学年 → 404
    assert client.get(f"{API}/homeroom/rollover/preview").status_code == 422
    r = client.get(
        f"{API}/homeroom/rollover/preview", params={"from_academic_year_id": 999999}
    )
    assert r.status_code == 404
    assert r.json()["error"] == "resource_out_of_scope"


def test_preview_suggests_keeping_old_alias(client, v1_seed):
    # 先经 §1 学年管理端点建新学年（2026-2027）
    r = client.post(
        f"{API}/shared/academic-years",
        json={
            "name": "2026-2027",
            "start_date": "2026-09-01",
            "end_date": "2027-07-15",
        },
    )
    assert r.status_code == 200
    to_year_id = r.json()["id"]

    body = _preview(client, v1_seed)
    assert body["from_year"]["id"] == v1_seed.ay_id
    assert body["to_year"]["id"] == to_year_id
    assert len(body["students"]) == 3
    # 保守建议：next_alias = 旧 alias 原样
    assert {s["current_alias"] for s in body["students"]} == {
        "2025H6-01",
        "2025H6-02",
        "2025H6-03",
    }
    for stu in body["students"]:
        assert stu["next_alias"] == stu["current_alias"]

    # 零业务写入：只有 import_batch 台账行（pending）
    from app.db import workspace_models as wm

    db = _db()
    try:
        batch = db.query(wm.ImportBatch).filter_by(token=body["token"]).one()
        assert batch.status == "pending"
        assert '"kind": "rollover_preview"' in batch.scope_json.replace(" ", "") or (
            "rollover_preview" in batch.scope_json
        )
    finally:
        db.close()


def test_confirm_creates_class_enrollments_aliases(client, v1_seed):
    db = _db()
    try:
        _set_high3_binding(db, 6)
    finally:
        db.close()

    body = _preview(client, v1_seed)
    token = body["token"]
    to_year_id = body["to_year"]["id"]
    STATE["confirm_token"] = token

    # 丙不传 aliases → 沿用建议（旧学号）
    r = client.post(
        f"{API}/homeroom/rollover",
        json={
            "token": token,
            "aliases": {
                str(v1_seed.jia_h_id): "2026H6-01",
                str(v1_seed.yi_h_id): "2026H6-02",
            },
        },
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["class_created"] is True
    assert result["academic_year_id"] == to_year_id
    assert result["rolled_over"] == 3

    from app.db import workspace_models as wm

    db = _db()
    try:
        cls = _new_class(db, to_year_id)
        assert cls is not None and cls.label == "高三6班"
        enrs = db.query(wm.Enrollment).filter_by(admin_class_id=cls.id).all()
        assert {e.identity_id for e in enrs} == {
            v1_seed.jia_h_id,
            v1_seed.yi_h_id,
            v1_seed.bing_h_id,
        }
        assert all(
            e.status == "active" and e.valid_from == date(2026, 9, 1) for e in enrs
        )
        aliases = (
            db.query(wm.WsStudentAlias)
            .filter_by(academic_year_id=to_year_id, data_domain="homeroom")
            .all()
        )
        assert {a.alias_value for a in aliases} == {
            "2026H6-01",
            "2026H6-02",
            "2025H6-03",
        }
        # 旧 alias 收尾：valid_to = 新学年 start_date 前一日
        old_aliases = {
            v1_seed.jia_h_id: "2025H6-01",
            v1_seed.yi_h_id: "2025H6-02",
            v1_seed.bing_h_id: "2025H6-03",
        }
        for pid, value in old_aliases.items():
            old = _old_alias(db, v1_seed, pid, value)
            assert old.valid_to == date(2026, 8, 31)
        batch = db.query(wm.ImportBatch).filter_by(token=token).one()
        assert batch.status == "confirmed"
    finally:
        db.close()

    # 切到高三绑定 → 新学年名册可见（换届后教师工作口径；
    # P1 名册端点要求显式 class_id）
    db = _db()
    try:
        _set_active_grade(db, 3)
        cls = _new_class(db, to_year_id)
        new_class_id = cls.id
    finally:
        db.close()
    r = client.get(
        f"{API}/homeroom/students",
        params={"academic_year_id": to_year_id, "class_id": new_class_id},
    )
    assert r.status_code == 200
    students = r.json()["students"]
    assert len(students) == 3
    jia = next(s for s in students if s["person_id"] == v1_seed.jia_h_id)
    # 注：P1 homeroom_roster→aliases_for 不带学年参数，名册 alias 固定回退
    # 最早学号（关联问题见交付报告）；新学号落库已由上面 ORM 断言覆盖，
    # 此处验证换届后成员出现在新学年名册即可
    assert jia is not None and jia["status"] == "active"

    # 恢复 active_grade=2：后续 preview 以旧学年为起点
    db = _db()
    try:
        _set_active_grade(db, 2)
    finally:
        db.close()


def test_confirm_token_replay_409_zero_side_effect(client, v1_seed):
    token = STATE["confirm_token"]
    from app.db import workspace_models as wm

    db = _db()
    try:
        to_year_id = _to_year_id(db)
        before = _write_counts(db, v1_seed, to_year_id)
    finally:
        db.close()

    # 重复确认同 token → 409，不得再次触发任何状态变化
    r = client.post(f"{API}/homeroom/rollover", json={"token": token})
    assert r.status_code == 409
    assert r.json()["error"] == "link_version_conflict"

    db = _db()
    try:
        to_year_id = _to_year_id(db)
        assert _write_counts(db, v1_seed, to_year_id) == before
        assert db.query(wm.ImportBatch).filter_by(token=token).one().status == "confirmed"
    finally:
        db.close()


def test_undo_lists_conflicted_and_rolls_back_others(client, v1_seed):
    token = STATE["confirm_token"]
    from app.db import workspace_models as wm

    # 换届后给丙的新学号种一条成绩（created_at 晚于 confirm 时点）
    db = _db()
    try:
        to_year_id = _to_year_id(db)
        cls = _new_class(db, to_year_id)
        db.add(
            wm.ScoreFact(
                data_domain="homeroom",
                academic_year_id=to_year_id,
                exam_name="2026期中",
                exam_date=date(2026, 11, 5),
                class_ref_id=cls.id,
                identity_id=v1_seed.bing_h_id,
                subject="物理",
                score=88.0,
                source="p4-test",
            )
        )
        db.commit()
    finally:
        db.close()

    r = client.post(f"{API}/homeroom/rollover/{token}/undo")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["undone"] == 2
    assert body["class_removed"] is False  # 丙的学籍仍在新班，班级不能删
    assert len(body["conflicted"]) == 1
    conflicted = body["conflicted"][0]
    assert conflicted["person_id"] == v1_seed.bing_h_id
    assert "成绩" in conflicted["reason"]

    db = _db()
    try:
        to_year_id = _to_year_id(db)
        cls = _new_class(db, to_year_id)
        # 甲乙：学籍/新 alias 全部回滚，旧 alias 恢复 valid_to=NULL
        old_values = {v1_seed.jia_h_id: "2025H6-01", v1_seed.yi_h_id: "2025H6-02"}
        for pid, value in old_values.items():
            assert (
                db.query(wm.Enrollment)
                .filter_by(admin_class_id=cls.id, identity_id=pid)
                .count()
                == 0
            )
            assert (
                db.query(wm.WsStudentAlias)
                .filter_by(identity_id=pid, academic_year_id=to_year_id)
                .count()
                == 0
            )
            old = _old_alias(db, v1_seed, pid, value)
            assert old.valid_to is None
        # 丙（conflicted）：换届结果保留现状
        assert (
            db.query(wm.Enrollment)
            .filter_by(admin_class_id=cls.id, identity_id=v1_seed.bing_h_id)
            .count()
            == 1
        )
        kept = (
            db.query(wm.WsStudentAlias)
            .filter_by(
                identity_id=v1_seed.bing_h_id, academic_year_id=to_year_id
            )
            .all()
        )
        assert [a.alias_value for a in kept] == ["2025H6-03"]
        old_bing = _old_alias(db, v1_seed, v1_seed.bing_h_id, "2025H6-03")
        assert old_bing.valid_to == date(2026, 8, 31)  # 收尾不恢复
        # token 单次消费：confirmed_undo 终态
        assert db.query(wm.ImportBatch).filter_by(token=token).one().status == (
            "confirmed_undo"
        )
    finally:
        db.close()


def test_undo_repeat_409(client, v1_seed):
    r = client.post(f"{API}/homeroom/rollover/{STATE['confirm_token']}/undo")
    assert r.status_code == 409
    assert r.json()["error"] == "link_version_conflict"
    from app.db import workspace_models as wm

    db = _db()
    try:
        batch = db.query(wm.ImportBatch).filter_by(token=STATE["confirm_token"]).one()
        assert batch.status == "confirmed_undo"
    finally:
        db.close()


def test_redo_after_undo_reuses_class(client, v1_seed):
    """撤销后再走：新 preview → confirm 复用既有班级（绑定一致），零重复行。"""
    body = _preview(client, v1_seed)
    to_year_id = body["to_year"]["id"]
    r = client.post(f"{API}/homeroom/rollover", json={"token": body["token"]})
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["class_created"] is False  # 复用上次新建的班级
    assert result["rolled_over"] == 3

    from app.db import workspace_models as wm

    db = _db()
    try:
        cls = _new_class(db, to_year_id)
        enrs = db.query(wm.Enrollment).filter_by(admin_class_id=cls.id).all()
        assert {e.identity_id for e in enrs} == {
            v1_seed.jia_h_id,
            v1_seed.yi_h_id,
            v1_seed.bing_h_id,
        }
        aliases = (
            db.query(wm.WsStudentAlias)
            .filter_by(academic_year_id=to_year_id, data_domain="homeroom")
            .all()
        )
        # 甲乙重建建议号行；丙（conflicted 保留）幂等跳过
        assert {a.alias_value for a in aliases} == {
            "2025H6-01",
            "2025H6-02",
            "2025H6-03",
        }
        # 甲乙旧 alias 再次收尾（redo 后同值行跨学年重复，按"值+年为空"定位旧行）
        redo_values = {v1_seed.jia_h_id: "2025H6-01", v1_seed.yi_h_id: "2025H6-02"}
        for pid, value in redo_values.items():
            old = _old_alias(db, v1_seed, pid, value)
            assert old.valid_to == date(2026, 8, 31)
    finally:
        db.close()


def test_confirm_alias_collision_409_zero_write(client, v1_seed):
    from app.db import workspace_models as wm

    # 库内他人（另行建档）已占用目标学号
    db = _db()
    try:
        to_year_id = _to_year_id(db)
        outsider = wm.WsStudentIdentity(data_domain="homeroom", display_name="秦外")
        db.add(outsider)
        db.flush()
        outsider_id = outsider.id  # close 前取出，避免 DetachedInstance
        db.add(
            wm.WsStudentAlias(
                identity_id=outsider_id,
                alias_value="2026H6-88",
                data_domain="homeroom",
                academic_year_id=to_year_id,
                alias_scope=str(to_year_id),
                source="p4-test",
            )
        )
        db.commit()
        before = _write_counts(db, v1_seed, to_year_id)
    finally:
        db.close()

    token = _preview(client, v1_seed)["token"]
    r = client.post(
        f"{API}/homeroom/rollover",
        json={"token": token, "aliases": {str(v1_seed.jia_h_id): "2026H6-88"}},
    )
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "link_version_conflict"
    assert any(
        c["person_id"] == outsider_id and c["name"] == "秦外"
        for c in body["conflicts"]
    )

    # 整批零写入，token 保持 pending 可重试
    db = _db()
    try:
        to_year_id = _to_year_id(db)
        assert _write_counts(db, v1_seed, to_year_id) == before
        assert db.query(wm.ImportBatch).filter_by(token=token).one().status == "pending"
    finally:
        db.close()

    # 非本批成员的 alias 覆盖 → 422
    r = client.post(
        f"{API}/homeroom/rollover",
        json={"token": token, "aliases": {"999999": "X"}},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_scope_param"


def test_confirm_member_drift_409(client, v1_seed):
    from app.db import workspace_models as wm

    token = _preview(client, v1_seed)["token"]

    # preview 后旧学年成员漂移（乙离班）
    db = _db()
    try:
        enr = (
            db.query(wm.Enrollment)
            .filter_by(
                admin_class_id=v1_seed.h6_id, identity_id=v1_seed.yi_h_id
            )
            .one()
        )
        enr.valid_to = date(2026, 9, 10)
        db.commit()
    finally:
        db.close()

    try:
        r = client.post(f"{API}/homeroom/rollover", json={"token": token})
        assert r.status_code == 409
        assert r.json()["error"] == "link_version_conflict"
        assert "重新预览" in r.json()["detail"]

        db = _db()
        try:
            to_year_id = _to_year_id(db)
            batch = db.query(wm.ImportBatch).filter_by(token=token).one()
            assert batch.status == "pending"  # 拒绝路径零写入、token 未消费
        finally:
            db.close()
    finally:
        # 恢复成员，避免污染同模块后续用例
        db = _db()
        try:
            enr = (
                db.query(wm.Enrollment)
                .filter_by(
                    admin_class_id=v1_seed.h6_id, identity_id=v1_seed.yi_h_id
                )
                .one()
            )
            enr.valid_to = None
            db.commit()
        finally:
            db.close()


def test_confirm_reuses_bound_class_and_keeps_link(client, v1_seed):
    """已存在同班且属绑定关系 → 复用（不重复建行）；换届不动 LinkedStudent/link。"""
    from app.db import workspace_models as wm

    body = _preview(client, v1_seed)
    to_year_id = body["to_year"]["id"]
    r = client.post(f"{API}/homeroom/rollover", json={"token": body["token"]})
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["class_created"] is False
    assert result["rolled_over"] == 3

    db = _db()
    try:
        classes = (
            db.query(wm.AdministrativeClass)
            .filter_by(academic_year_id=to_year_id, grade=3, class_num=6)
            .all()
        )
        assert len(classes) == 1  # 全程只此一行
        # 换届不动关联与学生配对（按人，架构 §7）
        link = db.get(wm.HomeroomTeachingLink, v1_seed.link_id)
        assert link.status == "active"
        assert (
            db.query(wm.LinkedStudent).filter_by(link_id=v1_seed.link_id).count() == 2
        )
    finally:
        db.close()


def test_confirm_existing_unbound_class_422(client, v1_seed):
    """存在同班号班级但教师未绑定该班（非绑定同班）→ 422 拒绝并入。"""
    from app.db import workspace_models as wm

    db = _db()
    try:
        _set_high3_binding(db, None)  # 解除高三绑定 → 目标班号回退沿用旧班号
    finally:
        db.close()

    try:
        token = _preview(client, v1_seed)["token"]
        r = client.post(f"{API}/homeroom/rollover", json={"token": token})
        assert r.status_code == 422
        assert r.json()["error"] == "invalid_scope_param"
        assert "class_id" in r.json()
        # 零写入：token 保持 pending
        db = _db()
        try:
            assert (
                db.query(wm.ImportBatch).filter_by(token=token).one().status
                == "pending"
            )
        finally:
            db.close()
    finally:
        db = _db()
        try:
            _set_high3_binding(db, 6)
        finally:
            db.close()
