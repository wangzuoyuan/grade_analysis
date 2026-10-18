"""问题9 回归：未换届延续期（carryover）写入在正式换届后的承接与回迁。

场景重放（对应审查 probe2 的 9_carryover）：
- 学年 2025-2026（ay1，结束 2026-07-15）→ 2026-2027（ay2）未换届，工作台
  延续使用旧班（ctx.academic_year_id=ay2、class_ids=旧班）。
- 延续期写入：开学考成绩（ay2 + 旧班 class_ref_id）、作业批次（同签名）、
  延续期新建学生（create_student 把 Enrollment 写进旧班、valid_from 落在 ay2）。
- 旧契约：换届建新班后这些行在新旧两学年都查不到（readable 1 → 0）。
- 新契约：preview 列出承接清单 → confirm 同事务迁入新班（academic_year_id
  不动）→ undo 时班级删除即回迁来源班（回到延续态可读）；班级保留则迁移
  保留。旧学年正常数据（ay1 + 旧班）一律不动。

合成样本全部虚构（秦姓），延续期新建学生走真实 API 路径验证污染形态。
"""

from datetime import date

import pytest

API = "/api/v1"
STATE: dict = {}


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _roster_as_of():
    """与实现同口径的期望取样时点：min(今天, ay1 结束日 2026-07-15)。"""
    today = date.today()
    end = date(2026, 7, 15)
    return min(today, end)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture(scope="module")
def seed(isolated_module_schema):
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

    h6 = wm.AdministrativeClass(academic_year_id=ay1.id, grade=2, class_num=6, label="高二6班")
    db.add(h6)
    db.flush()
    tc1 = wm.TeachingClass(academic_year_id=ay1.id, subject="物理", label="物A1", sort_order=0)
    db.add(tc1)
    db.flush()

    jia = wm.WsStudentIdentity(data_domain="homeroom", display_name="秦延甲")
    db.add(jia)
    db.flush()
    db.add(
        wm.Enrollment(
            admin_class_id=h6.id,
            identity_id=jia.id,
            seat_no=1,
            status="active",
            valid_from=date(2025, 9, 1),
        )
    )
    db.add(
        wm.WsStudentAlias(
            identity_id=jia.id,
            alias_value="2025H6-01",
            data_domain="homeroom",
            academic_year_id=ay1.id,
            alias_scope=str(ay1.id),
            source="manual",
            valid_from=date(2025, 9, 1),
        )
    )
    bing_t = wm.WsStudentIdentity(data_domain="teaching", display_name="秦延丙")
    db.add(bing_t)
    db.flush()
    db.add(
        wm.TeachingClassMember(
            teaching_class_id=tc1.id,
            identity_id=bing_t.id,
            valid_from=date(2025, 9, 1),
            source="manual",
        )
    )
    db.add(
        wm.WsStudentAlias(
            identity_id=bing_t.id,
            alias_value="2025T-01",
            data_domain="teaching",
            academic_year_id=ay1.id,
            alias_scope=str(ay1.id),
            source="manual",
            valid_from=date(2025, 9, 1),
        )
    )

    # ── 延续期写入（carryover 签名：ay2 + 旧班 class_ref_id）──
    db.add(
        wm.ScoreFact(
            data_domain="homeroom",
            academic_year_id=ay2.id,
            exam_name="合成开学考",
            exam_date=date(2026, 10, 1),
            class_ref_id=h6.id,
            identity_id=jia.id,
            subject="语文",
            score=83.0,
            source="fix09-test",
        )
    )
    db.add(
        wm.HomeworkAssignment(
            data_domain="homeroom",
            academic_year_id=ay2.id,
            class_ref_id=h6.id,
            subject="语文",
            homework_type="练习册",
            assigned_date=date(2026, 10, 2),
            batch_token="fix09-h-carryover",
            expected_members_json=json_members([jia.id]),
            status="active",
        )
    )
    db.add(
        wm.ScoreFact(
            data_domain="teaching",
            academic_year_id=ay2.id,
            exam_name="合成教学开学考",
            exam_date=date(2026, 10, 3),
            class_ref_id=tc1.id,
            identity_id=bing_t.id,
            subject="物理",
            score=91.0,
            source="fix09-test",
        )
    )
    db.add(
        wm.HomeworkAssignment(
            data_domain="teaching",
            academic_year_id=ay2.id,
            class_ref_id=tc1.id,
            subject="物理",
            homework_type="练习册",
            assigned_date=date(2026, 10, 4),
            batch_token="fix09-t-carryover",
            expected_members_json=json_members([bing_t.id]),
            status="active",
        )
    )
    # ── 旧学年正常数据（绝不迁移的对照行）──
    db.add(
        wm.ScoreFact(
            data_domain="homeroom",
            academic_year_id=ay1.id,
            exam_name="合成旧年考",
            exam_date=date(2026, 5, 1),
            class_ref_id=h6.id,
            identity_id=jia.id,
            subject="语文",
            score=80.0,
            source="fix09-test",
        )
    )
    db.commit()

    # 延续期新建学生：真实 API 路径（academic_year_id=ay2 → 延续解析到旧班），
    # 验证 create_student 的延续期写入形态：Enrollment 落旧班、valid_from >= ay2.start。
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    r = c.post(
        f"{API}/homeroom/students",
        json={"name": "秦延新", "alias": "2026H6-09", "seat_no": 9},
        params={"academic_year_id": ay2.id},
    )
    assert r.status_code == 200, r.text
    stray_id = r.json()["person_id"]

    seed = SimpleNamespace(
        ay1=ay1.id,
        ay2=ay2.id,
        h6=h6.id,
        tc1=tc1.id,
        jia=jia.id,
        bing_t=bing_t.id,
        stray=stray_id,
    )
    db.close()
    return seed


def json_members(ids):
    import json

    return json.dumps(ids)


# ────────────────────── 班主任换届承接 ──────────────────────


def test_carryover_writes_visible_before_rollover(seed):
    """重放 probe2 前半段：延续态下开学考可读（readable_before == 1），
    且 create_student 的学籍确实落在旧班（污染形态查证）。"""
    from app.core.context import resolve_workspace_context

    db = _db()
    try:
        from app.db import workspace_models as wm

        ctx = resolve_workspace_context(db, 1, "homeroom", {"academic_year_id": seed.ay2})
        assert ctx.carried_from_academic_year_id == seed.ay1
        assert list(ctx.class_ids) == [seed.h6]
        stray_enr = (
            db.query(wm.Enrollment)
            .filter_by(admin_class_id=seed.h6, identity_id=seed.stray)
            .one()
        )
        assert stray_enr.valid_from >= date(2026, 9, 1)  # 落在目标学年区间

        from app.api import _queries as q

        facts = q.readable_facts(db, ctx, "合成开学考", member_ids=[seed.jia])
        assert len(facts) == 1 and facts[0].fact.score == 83.0
    finally:
        db.close()


def test_homeroom_preview_lists_carryover_plan(client, seed):
    r = client.get(
        f"{API}/homeroom/rollover/preview",
        params={"from_academic_year_id": seed.ay1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    STATE["token"] = body["token"]

    # 取样时点（13.4）：源学年已结束 → 按学年结束日取样
    assert body["roster_as_of"] == _roster_as_of().isoformat()
    assert "取样" in body["roster_basis"]

    # 名册：甲在册；延续期新建学籍不在名册（valid_from 晚于取样时点）
    assert [s["person_id"] for s in body["students"]] == [seed.jia]
    excluded = {s["person_id"]: s for s in body["excluded_students"]}
    assert seed.stray in excluded
    assert "承接" in excluded[seed.stray]["note"]

    # 承接清单（只读影响预览）
    plan = body["carryover"]
    assert plan["score_facts"] == 1
    assert plan["homework_assignments"] == 1
    assert plan["stray_enrollments"] == 1
    assert plan["exam_names"] == ["合成开学考"]
    assert plan["stray_student_names"] == ["秦延新"]


def test_homeroom_confirm_migrates_carryover_writes(client, seed):
    r = client.post(f"{API}/homeroom/rollover", json={"token": STATE["token"]})
    assert r.status_code == 200, r.text
    body = r.json()
    new_class_id = body["class_id"]
    STATE["new_class_id"] = new_class_id
    assert body["class_created"] is True
    assert body["rolled_over"] == 1
    assert body["carryover_moved"] == 3  # 成绩 + 作业 + 延续期学籍

    from app.core.context import resolve_workspace_context

    db = _db()
    try:
        from app.db import workspace_models as wm

        # 迁移：class_ref_id / admin_class_id 归位新班，academic_year_id 不动
        fact = db.query(wm.ScoreFact).filter_by(exam_name="合成开学考").one()
        assert fact.class_ref_id == new_class_id and fact.academic_year_id == seed.ay2
        assignment = (
            db.query(wm.HomeworkAssignment).filter_by(batch_token="fix09-h-carryover").one()
        )
        assert assignment.class_ref_id == new_class_id
        stray_enr = db.query(wm.Enrollment).filter_by(identity_id=seed.stray).one()
        assert stray_enr.admin_class_id == new_class_id

        # 旧学年正常数据不动
        old_fact = db.query(wm.ScoreFact).filter_by(exam_name="合成旧年考").one()
        assert old_fact.class_ref_id == seed.h6 and old_fact.academic_year_id == seed.ay1

        # 换届后可读（probe2 断言 readable_after == 1）
        after_ctx = resolve_workspace_context(
            db, 1, "homeroom", {"academic_year_id": seed.ay2, "class_id": new_class_id}
        )
        assert after_ctx.carried_from_academic_year_id is None
        from app.api import _queries as q

        facts = q.readable_facts(db, after_ctx, "合成开学考", member_ids=[seed.jia])
        assert len(facts) == 1 and facts[0].fact.score == 83.0
        # 延续期新生随承接进入新班名册
        roster_pids = {item["person_id"] for item in q.homeroom_roster(db, new_class_id, date.today())}
        assert seed.stray in roster_pids
    finally:
        db.close()


def test_homeroom_undo_restores_carryover_writes(client, seed):
    r = client.post(f"{API}/homeroom/rollover/{STATE['token']}/undo")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["undone"] == 1 and body["conflicted"] == []
    assert body["class_removed"] is True
    assert body["carryover_restored"] == 3
    assert body["carryover_kept"] == 0

    from app.core.context import resolve_workspace_context

    db = _db()
    try:
        from app.db import workspace_models as wm

        # 回迁来源班：数据回到延续态（新学年上下文再次解析到旧班）
        fact = db.query(wm.ScoreFact).filter_by(exam_name="合成开学考").one()
        assert fact.class_ref_id == seed.h6
        assignment = (
            db.query(wm.HomeworkAssignment).filter_by(batch_token="fix09-h-carryover").one()
        )
        assert assignment.class_ref_id == seed.h6
        stray_enr = db.query(wm.Enrollment).filter_by(identity_id=seed.stray).one()
        assert stray_enr.admin_class_id == seed.h6

        ctx = resolve_workspace_context(db, 1, "homeroom", {"academic_year_id": seed.ay2})
        assert list(ctx.class_ids) == [seed.h6]
        from app.api import _queries as q

        assert len(q.readable_facts(db, ctx, "合成开学考", member_ids=[seed.jia])) == 1
    finally:
        db.close()


def test_kept_class_keeps_migration_on_partial_undo(client, seed):
    """换届后目标班出现新写入 → 撤销保留班级，迁移保留（不回迁，数据
    始终挂在存活班级上仍可读）。"""
    r = client.get(
        f"{API}/homeroom/rollover/preview",
        params={"from_academic_year_id": seed.ay1},
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    r = client.post(f"{API}/homeroom/rollover", json={"token": token})
    assert r.status_code == 200, r.text
    new_class_id = r.json()["class_id"]

    db = _db()
    try:
        from app.db import workspace_models as wm

        db.add(
            wm.ScoreFact(
                data_domain="homeroom",
                academic_year_id=seed.ay2,
                exam_name="合成换届后考",
                exam_date=date(2026, 11, 1),
                class_ref_id=new_class_id,
                identity_id=seed.jia,
                subject="数学",
                score=90.0,
                source="fix09-test",
            )
        )
        db.commit()
    finally:
        db.close()

    r = client.post(f"{API}/homeroom/rollover/{token}/undo")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [c["person_id"] for c in body["conflicted"]] == [seed.jia]
    assert body["class_removed"] is False
    assert body["carryover_restored"] == 0
    assert body["carryover_kept"] == 3

    db = _db()
    try:
        from app.db import workspace_models as wm

        fact = db.query(wm.ScoreFact).filter_by(exam_name="合成开学考").one()
        assert fact.class_ref_id == new_class_id  # 迁移保留
        from app.core.context import resolve_workspace_context

        ctx = resolve_workspace_context(
            db, 1, "homeroom", {"academic_year_id": seed.ay2}
        )
        from app.api import _queries as q

        assert len(q.readable_facts(db, ctx, "合成开学考", member_ids=[seed.jia])) == 1
        # 清理：撤销后残留的新班 + 换届后新写入，避免影响后续教学域用例
        db.query(wm.ScoreFact).filter_by(exam_name="合成换届后考").delete()
        db.query(wm.Enrollment).filter_by(admin_class_id=new_class_id).delete()
        db.query(wm.WsStudentAlias).filter_by(
            academic_year_id=seed.ay2, data_domain="homeroom"
        ).delete()
        db.delete(db.get(wm.AdministrativeClass, new_class_id))
        db.query(wm.WsStudentAlias).filter_by(
            identity_id=seed.jia, academic_year_id=seed.ay1
        ).one().valid_to = None
        db.commit()
    finally:
        db.close()


# ────────────────────── 教学班换届承接 ──────────────────────


def test_teaching_carryover_preview_confirm_and_undo(client, seed):
    from app.core.context import resolve_workspace_context

    r = client.get(
        f"{API}/teaching/rollover/preview",
        params={"from_academic_year_id": seed.ay1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    plan = body["carryover"]
    assert plan["score_facts"] == 1
    assert plan["homework_assignments"] == 1
    assert plan["stray_enrollments"] == 0  # 教学域成员无延续期错位（查证结论）
    assert plan["exam_names"] == ["合成教学开学考"]
    token = body["token"]

    # 延续态可读（承接前）
    db = _db()
    try:
        ctx = resolve_workspace_context(
            db,
            1,
            "teaching",
            {"academic_year_id": seed.ay2, "teaching_class_id": seed.tc1, "subject": "物理"},
        )
        assert ctx.carried_from_academic_year_id == seed.ay1
        from app.api import _queries as q

        assert (
            len(q.readable_facts(db, ctx, "合成教学开学考", member_ids=[seed.bing_t])) == 1
        )
    finally:
        db.close()

    r = client.post(f"{API}/teaching/rollover", json={"token": token})
    assert r.status_code == 200, r.text
    body = r.json()
    new_class_id = body["classes"][0]["class_id"]
    assert body["classes"][0]["class_created"] is True
    assert body["carryover_moved"] == 2

    db = _db()
    try:
        from app.db import workspace_models as wm

        fact = db.query(wm.ScoreFact).filter_by(exam_name="合成教学开学考").one()
        assert fact.class_ref_id == new_class_id and fact.academic_year_id == seed.ay2
        assignment = (
            db.query(wm.HomeworkAssignment).filter_by(batch_token="fix09-t-carryover").one()
        )
        assert assignment.class_ref_id == new_class_id

        after_ctx = resolve_workspace_context(
            db,
            1,
            "teaching",
            {"academic_year_id": seed.ay2, "teaching_class_id": new_class_id, "subject": "物理"},
        )
        from app.api import _queries as q

        assert (
            len(q.readable_facts(db, after_ctx, "合成教学开学考", member_ids=[seed.bing_t]))
            == 1
        )
    finally:
        db.close()

    # 撤销：新班删除 → 延续期写入回迁来源班，回到延续态可读
    r = client.post(f"{API}/teaching/rollover/{token}/undo")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["undone"] == 1 and body["conflicted"] == []
    assert body["class_removed"] is True
    assert body["carryover_restored"] == 2
    assert body["kept_reused_class_labels"] == []

    db = _db()
    try:
        from app.db import workspace_models as wm

        fact = db.query(wm.ScoreFact).filter_by(exam_name="合成教学开学考").one()
        assert fact.class_ref_id == seed.tc1
        assignment = (
            db.query(wm.HomeworkAssignment).filter_by(batch_token="fix09-t-carryover").one()
        )
        assert assignment.class_ref_id == seed.tc1

        ctx = resolve_workspace_context(
            db,
            1,
            "teaching",
            {"academic_year_id": seed.ay2, "teaching_class_id": seed.tc1, "subject": "物理"},
        )
        assert ctx.carried_from_academic_year_id == seed.ay1
        from app.api import _queries as q

        assert (
            len(q.readable_facts(db, ctx, "合成教学开学考", member_ids=[seed.bing_t])) == 1
        )
    finally:
        db.close()
