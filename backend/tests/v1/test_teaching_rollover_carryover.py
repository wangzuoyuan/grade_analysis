"""教学班换届（preview→confirm→undo）+ 未换届学年延续（双工作台）。

合成样本（模块级，定义顺序即执行顺序）：
- 学年 2025-2026（ay1）：行政班 H6（教师绑定高二 6，成员甲乙）、
  教学班 物A1（物理，成员丙丁，教学域学号）。
- 学年 2026-2027（ay2）：建学年但【无任何班级】——延续口径的目标学年。
延续用例先跑（此时 ay2 无班）；换届用例后跑，confirm 会把 物A1 升入
ay2，顺带验证「换届后延续自动退出、目录展示本学年自己的班」。
"""

from datetime import date

import pytest

API = "/api/v1"
STATE: dict = {}


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _seed():
    """模块级种子：返回 SimpleNamespace(ay1, ay2, ac1, tc1, p1, p2, t1, t2)。"""
    from types import SimpleNamespace

    from app.db import workspace_models as wm
    from app.db.models import HomeworkSetting, SessionLocal, Teacher

    db = SessionLocal()
    db.merge(Teacher(id=1, name="测试班主任", target_class_high2=6))
    db.merge(HomeworkSetting(key="active_grade", value="2"))

    ay1 = wm.AcademicYear(name="2025-2026", start_date=date(2025, 9, 1), end_date=date(2026, 7, 15))
    ay2 = wm.AcademicYear(name="2026-2027", start_date=date(2026, 9, 1), end_date=date(2027, 7, 15))
    db.add_all([ay1, ay2])
    db.flush()

    ac1 = wm.AdministrativeClass(academic_year_id=ay1.id, grade=2, class_num=6, label="高二6班")
    db.add(ac1)
    db.flush()

    tc1 = wm.TeachingClass(academic_year_id=ay1.id, subject="物理", label="物A1", sort_order=0)
    db.add(tc1)
    db.flush()

    def identity(domain, name):
        obj = wm.WsStudentIdentity(data_domain=domain, display_name=name)
        db.add(obj)
        db.flush()
        return obj.id

    p1 = identity("homeroom", "延续甲")
    p2 = identity("homeroom", "延续乙")
    t1 = identity("teaching", "延续丙")
    t2 = identity("teaching", "延续丁")

    for pid, seat in ((p1, 1), (p2, 2)):
        db.add(
            wm.Enrollment(
                admin_class_id=ac1.id,
                identity_id=pid,
                seat_no=seat,
                status="active",
                valid_from=date(2025, 9, 1),
            )
        )
        db.add(
            wm.WsStudentAlias(
                identity_id=pid,
                alias_value=f"2025H6-0{seat}",
                data_domain="homeroom",
                academic_year_id=ay1.id,
                alias_scope=str(ay1.id),
                source="manual",
                valid_from=date(2025, 9, 1),
            )
        )
    for tid, sid in ((t1, 1), (t2, 2)):
        db.add(
            wm.TeachingClassMember(
                teaching_class_id=tc1.id,
                identity_id=tid,
                valid_from=date(2025, 9, 1),
                source="manual",
            )
        )
        db.add(
            wm.WsStudentAlias(
                identity_id=tid,
                alias_value=f"2025T-0{sid}",
                data_domain="teaching",
                academic_year_id=ay1.id,
                alias_scope=str(ay1.id),
                source="manual",
                valid_from=date(2025, 9, 1),
            )
        )
    db.commit()
    seed = SimpleNamespace(ay1=ay1.id, ay2=ay2.id, ac1=ac1.id, tc1=tc1.id, p1=p1, p2=p2, t1=t1, t2=t2)
    db.close()
    return seed


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture(scope="module")
def seed(isolated_module_schema):
    return _seed()


# ────────────────────────────── 学年延续（未换届） ──────────────────────────────


def test_carryover_shared_classes(client, seed):
    r = client.get(f"{API}/shared/classes", params={"academic_year_id": seed.ay2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["homeroom"]["class_id"] == seed.ac1
    assert body["homeroom"]["carried_from_academic_year_name"] == "2025-2026"
    assert [c["class_id"] for c in body["teaching"]] == [seed.tc1]
    assert body["teaching"][0]["carried_from_academic_year_name"] == "2025-2026"

    # 来源学年自身：正常目录、无延续标记
    r = client.get(f"{API}/shared/classes", params={"academic_year_id": seed.ay1})
    body = r.json()
    assert body["homeroom"]["carried_from_academic_year_id"] is None
    assert body["teaching"][0]["carried_from_academic_year_id"] is None


def test_carryover_scope_both_modes(client, seed):
    r = client.get(
        f"{API}/shared/scope",
        params={
            "mode": "homeroom",
            "academic_year_id": seed.ay2,
            "class_id": seed.ac1,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cohort_size"] == 2
    assert body["carried_from_academic_year_name"] == "2025-2026"

    r = client.get(
        f"{API}/shared/scope",
        params={
            "mode": "teaching",
            "academic_year_id": seed.ay2,
            "teaching_class_id": seed.tc1,
            "subject": "物理",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cohort_size"] == 2
    assert body["carried_from_academic_year_name"] == "2025-2026"

    # 来源学年自身无延续标记
    r = client.get(
        f"{API}/shared/scope",
        params={
            "mode": "teaching",
            "academic_year_id": seed.ay1,
            "teaching_class_id": seed.tc1,
            "subject": "物理",
        },
    )
    assert r.status_code == 200
    assert r.json()["carried_from_academic_year_id"] is None

    # 不存在的班仍 404，延续不放宽越界
    r = client.get(
        f"{API}/shared/scope",
        params={
            "mode": "teaching",
            "academic_year_id": seed.ay2,
            "teaching_class_id": 999999,
            "subject": "物理",
        },
    )
    assert r.status_code == 404


def test_carryover_teaching_class_list(client, seed):
    r = client.get(f"{API}/teaching/classes", params={"academic_year_id": seed.ay2})
    assert r.status_code == 200, r.text
    classes = r.json()["classes"]
    assert [c["class_id"] for c in classes] == [seed.tc1]
    assert classes[0]["carried_from_academic_year_name"] == "2025-2026"


def test_teaching_rollover_preview_requires_source_classes(client, seed):
    # ay2 尚无教学班 → 以它为来源的换届无可换届名册（409）
    r = client.get(
        f"{API}/teaching/rollover/preview",
        params={"from_academic_year_id": seed.ay2},
    )
    assert r.status_code == 409
    assert r.json()["error"] == "workspace_not_configured"


# ────────────────────────────── 教学班换届 ──────────────────────────────


def test_teaching_rollover_preview(client, seed):
    r = client.get(
        f"{API}/teaching/rollover/preview",
        params={"from_academic_year_id": seed.ay1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    STATE["token"] = body["token"]
    assert body["from_year"]["name"] == "2025-2026"
    assert body["to_year"]["name"] == "2026-2027"
    assert {(s["person_id"], s["class_label"]) for s in body["students"]} == {
        (seed.t1, "物A1"),
        (seed.t2, "物A1"),
    }
    # 保守建议：沿用旧学号
    by_pid = {s["person_id"]: s for s in body["students"]}
    assert by_pid[seed.t1]["next_alias"] == "2025T-01"


def test_teaching_rollover_confirm(client, seed):
    r = client.post(
        f"{API}/teaching/rollover",
        json={"token": STATE["token"], "aliases": {}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rolled_over"] == 2
    assert body["academic_year_id"] == seed.ay2
    assert body["classes"][0]["class_created"] is True
    new_class_id = body["classes"][0]["class_id"]
    STATE["new_class_id"] = new_class_id

    from app.db import workspace_models as wm

    db = _db()
    try:
        tc = db.get(wm.TeachingClass, new_class_id)
        assert tc.academic_year_id == seed.ay2 and tc.label == "物A1"
        members = (
            db.query(wm.TeachingClassMember)
            .filter_by(teaching_class_id=new_class_id)
            .all()
        )
        assert {m.identity_id for m in members} == {seed.t1, seed.t2}
        assert all(m.valid_from == date(2026, 9, 1) for m in members)
        # 新学段 alias 写入、旧 alias 收尾到新学年开始前一天
        new_aliases = (
            db.query(wm.WsStudentAlias)
            .filter_by(academic_year_id=seed.ay2, data_domain="teaching")
            .all()
        )
        assert {a.alias_value for a in new_aliases} == {"2025T-01", "2025T-02"}
        old = (
            db.query(wm.WsStudentAlias)
            .filter_by(academic_year_id=seed.ay1, data_domain="teaching")
            .all()
        )
        assert all(a.valid_to == date(2026, 8, 31) for a in old)
        # 换届后延续自动退出：ay2 目录展示本学年自己的班、无延续标记
    finally:
        db.close()

    r = client.get(f"{API}/shared/classes", params={"academic_year_id": seed.ay2})
    body = r.json()
    assert body["teaching"][0]["class_id"] == new_class_id
    assert body["teaching"][0]["carried_from_academic_year_id"] is None

    # 同 token 重复确认 → 409
    r = client.post(f"{API}/teaching/rollover", json={"token": STATE["token"], "aliases": {}})
    assert r.status_code == 409


def test_teaching_rollover_undo_conflicted(client, seed):
    """换届后丙已有新学年成绩 → 撤销时丙保留现状、丁正常回滚。"""
    from app.db import workspace_models as wm

    db = _db()
    try:
        db.add(
            wm.ScoreFact(
                data_domain="teaching",
                academic_year_id=seed.ay2,
                exam_name="2026月考1",
                class_ref_id=STATE["new_class_id"],
                identity_id=seed.t1,
                subject="物理",
                score=88.0,
                source="test",
            )
        )
        db.commit()
    finally:
        db.close()

    r = client.post(f"{API}/teaching/rollover/{STATE['token']}/undo")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["undone"] == 1
    assert [c["person_id"] for c in body["conflicted"]] == [seed.t1]
    assert "成绩" in body["conflicted"][0]["reason"]
    assert body["class_removed"] is False  # 丙的成员行仍在 → 新班保留

    db = _db()
    try:
        members = (
            db.query(wm.TeachingClassMember)
            .filter_by(teaching_class_id=STATE["new_class_id"])
            .all()
        )
        assert {m.identity_id for m in members} == {seed.t1}
        # 丙的新 alias 保留、旧 alias 仍收尾；丁的新 alias 删除、旧 alias 恢复开放
        t1_old = (
            db.query(wm.WsStudentAlias)
            .filter_by(identity_id=seed.t1, academic_year_id=seed.ay1)
            .one()
        )
        assert t1_old.valid_to == date(2026, 8, 31)
        t2_old = (
            db.query(wm.WsStudentAlias)
            .filter_by(identity_id=seed.t2, academic_year_id=seed.ay1)
            .one()
        )
        assert t2_old.valid_to is None
        t2_new = (
            db.query(wm.WsStudentAlias)
            .filter_by(identity_id=seed.t2, academic_year_id=seed.ay2)
            .first()
        )
        assert t2_new is None
    finally:
        db.close()

    # token 单次消费：重复撤销 409
    r = client.post(f"{API}/teaching/rollover/{STATE['token']}/undo")
    assert r.status_code == 409


def test_teaching_rollover_undo_clean_roundtrip(client, seed):
    """无后续写入的完整撤销：成员/alias/新建班全部回滚。"""
    from app.db import workspace_models as wm

    # 清理上一用例为制造 conflicted 留下的合成痕迹（成绩行 + 丙保留的
    # 新学年成员/学号），恢复「无后续写入、无残留」的干净前提
    db = _db()
    try:
        db.query(wm.ScoreFact).filter_by(
            data_domain="teaching",
            academic_year_id=seed.ay2,
            identity_id=seed.t1,
        ).delete()
        db.query(wm.TeachingClassMember).filter_by(
            teaching_class_id=STATE["new_class_id"],
            identity_id=seed.t1,
        ).delete()
        db.query(wm.WsStudentAlias).filter_by(
            identity_id=seed.t1,
            academic_year_id=seed.ay2,
            data_domain="teaching",
        ).delete()
        db.commit()
    finally:
        db.close()

    r = client.get(
        f"{API}/teaching/rollover/preview",
        params={"from_academic_year_id": seed.ay1},
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    r = client.post(f"{API}/teaching/rollover", json={"token": token, "aliases": {}})
    assert r.status_code == 200, r.text
    new_class_id = r.json()["classes"][0]["class_id"]
    assert new_class_id == STATE["new_class_id"]  # 复用既有新学年同标签班

    r = client.post(f"{API}/teaching/rollover/{token}/undo")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["undone"] == 2
    assert body["conflicted"] == []
    assert body["class_removed"] is True  # 成员/成绩/关联/作业均无残留

    db = _db()
    try:
        assert db.get(wm.TeachingClass, new_class_id) is None
        members = (
            db.query(wm.TeachingClassMember)
            .filter_by(teaching_class_id=new_class_id)
            .count()
        )
        assert members == 0
    finally:
        db.close()
