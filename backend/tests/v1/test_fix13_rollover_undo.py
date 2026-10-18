"""问题13 回归：换届撤销误删复用班（13.1）/ 冲突检测收窄（13.2）/
补做换届时点（13.4）。

- 13.1 重放 probe2 的 13B 场景：目标学年先建同名空教学班 → 换届复用 →
  撤销 → 复用班必须保留（旧契约会把换届前已存在的班物理删除）。
- 13.2 正反例：旧学年补录作业、另一工作台档案、日期在旧学年的本域档案
  不再误阻撤销；目标班上的新写入（成绩/作业/档案）仍然阻断。
- 13.4：源学年名册 valid_to 已在学年末截止时，补做换届按学年结束日取样
  不漏人；已离班成员列入 excluded_students。

合成样本全部虚构（秦姓）。每个班主任场景自备一轮 preview→confirm→undo，
前置 _wipe 清理上一轮残留，保证场景独立。
"""

from datetime import date

import pytest

API = "/api/v1"


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


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

    ids = {}
    for key, domain, name in (
        ("jia", "homeroom", "秦撤甲"),
        ("yi", "homeroom", "秦撤乙"),
        ("bing", "homeroom", "秦撤丙"),
        ("bing_t", "teaching", "秦撤丙·T"),
    ):
        obj = wm.WsStudentIdentity(data_domain=domain, display_name=name)
        db.add(obj)
        db.flush()
        ids[key] = obj.id

    for idx, key in enumerate(("jia", "yi", "bing"), start=1):
        db.add(
            wm.Enrollment(
                admin_class_id=h6.id,
                identity_id=ids[key],
                seat_no=idx,
                status="active",
                valid_from=date(2025, 9, 1),
            )
        )
        db.add(
            wm.WsStudentAlias(
                identity_id=ids[key],
                alias_value=f"2025H6-0{idx}",
                data_domain="homeroom",
                academic_year_id=ay1.id,
                alias_scope=str(ay1.id),
                source="manual",
                valid_from=date(2025, 9, 1),
            )
        )
    db.add(
        wm.TeachingClassMember(
            teaching_class_id=tc1.id,
            identity_id=ids["bing_t"],
            valid_from=date(2025, 9, 1),
            source="manual",
        )
    )
    db.add(
        wm.WsStudentAlias(
            identity_id=ids["bing_t"],
            alias_value="2025T-01",
            data_domain="teaching",
            academic_year_id=ay1.id,
            alias_scope=str(ay1.id),
            source="manual",
            valid_from=date(2025, 9, 1),
        )
    )
    db.commit()
    seed = SimpleNamespace(
        ay1=ay1.id, ay2=ay2.id, h6=h6.id, tc1=tc1.id, **ids
    )
    db.close()
    return seed


def _homeroom_preview(client, seed):
    r = client.get(
        f"{API}/homeroom/rollover/preview",
        params={"from_academic_year_id": seed.ay1},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _homeroom_confirm(client, seed):
    body = _homeroom_preview(client, seed)
    r = client.post(f"{API}/homeroom/rollover", json={"token": body["token"]})
    assert r.status_code == 200, r.text
    return body["token"], r.json()


def _wipe_rollover_state(db, seed):
    """场景间清理：删上一轮新班及其残留（学籍/目标学年 alias/挂在班上的
    成绩与作业及其提交/目标学年档案），恢复 seed alias 开放。"""
    from app.db import workspace_models as wm

    for cls in (
        db.query(wm.AdministrativeClass)
        .filter_by(academic_year_id=seed.ay2, grade=3, class_num=6)
        .all()
    ):
        for fact in (
            db.query(wm.ScoreFact).filter_by(class_ref_id=cls.id, data_domain="homeroom").all()
        ):
            db.delete(fact)
        for assignment in (
            db.query(wm.HomeworkAssignment)
            .filter_by(class_ref_id=cls.id, data_domain="homeroom")
            .all()
        ):
            db.query(wm.HomeworkSubmission).filter_by(
                assignment_id=assignment.id
            ).delete(synchronize_session=False)
            db.delete(assignment)
        db.query(wm.Enrollment).filter_by(admin_class_id=cls.id).delete(
            synchronize_session=False
        )
        db.delete(cls)
    db.query(wm.WsStudentAlias).filter_by(
        academic_year_id=seed.ay2, data_domain="homeroom"
    ).delete(synchronize_session=False)
    db.query(wm.WsStudentNote).filter(
        wm.WsStudentNote.data_domain == "homeroom",
        wm.WsStudentNote.date >= date(2026, 9, 1),
    ).delete(synchronize_session=False)
    for alias in (
        db.query(wm.WsStudentAlias)
        .filter_by(academic_year_id=seed.ay1, data_domain="homeroom")
        .all()
    ):
        alias.valid_to = None
    db.commit()


# ────────────────────── 13.1 撤销不得删除复用班 ──────────────────────


def test_undo_keeps_preexisting_reused_teaching_class(client, seed):
    """重放 probe2 13B：目标学年先建同名空教学班 → 换届复用 → 撤销 →
    换届前已存在的班级保留（旧契约物理删除）。"""
    from app.db import workspace_models as wm

    db = _db()
    try:
        pre = wm.TeachingClass(
            academic_year_id=seed.ay2, subject="物理", label="物A1", sort_order=0
        )
        db.add(pre)
        db.commit()
        pre_id = pre.id
    finally:
        db.close()

    r = client.get(
        f"{API}/teaching/rollover/preview",
        params={"from_academic_year_id": seed.ay1},
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    r = client.post(f"{API}/teaching/rollover", json={"token": token})
    assert r.status_code == 200, r.text
    assert r.json()["classes"][0]["class_created"] is False  # 复用预先建的班

    r = client.post(f"{API}/teaching/rollover/{token}/undo")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["undone"] == 1 and body["conflicted"] == []
    assert body["class_removed"] is False
    assert body["kept_reused_class_labels"] == ["物A1"]

    db = _db()
    try:
        # 换届前已存在的班未被删除；成员行已回滚
        assert db.get(wm.TeachingClass, pre_id) is not None
        assert (
            db.query(wm.TeachingClassMember).filter_by(teaching_class_id=pre_id).count()
            == 0
        )
        # 收尾清理：删掉预置班，恢复 seed 学号开放（供后续场景独立）
        db.delete(db.get(wm.TeachingClass, pre_id))
        alias = (
            db.query(wm.WsStudentAlias)
            .filter_by(identity_id=seed.bing_t, academic_year_id=seed.ay1)
            .one()
        )
        alias.valid_to = None
        db.commit()
    finally:
        db.close()


# ────────────────────── 13.2 冲突检测收窄 ──────────────────────


def test_old_year_and_cross_domain_writes_do_not_block_undo(client, seed):
    """换届后在【旧学年】补录作业、写另一工作台档案、补记日期在旧学年的
    本域档案——均不依赖被回滚的目标班行，不再误阻撤销。"""
    token, result = _homeroom_confirm(client, seed)
    new_class_id = result["class_id"]

    from app.db import workspace_models as wm

    db = _db()
    try:
        old_year_assignment = wm.HomeworkAssignment(
            data_domain="homeroom",
            academic_year_id=seed.ay1,
            class_ref_id=seed.h6,
            subject="数学",
            homework_type="订正",
            assigned_date=date(2026, 6, 1),
            batch_token="fix13-old-year-makeup",
            expected_members_json="[]",
            status="active",
        )
        db.add(old_year_assignment)
        db.flush()
        db.add(
            wm.HomeworkSubmission(
                assignment_id=old_year_assignment.id,
                person_id=seed.jia,
                submission_status="missing",
            )
        )
        # 另一工作台的档案（teaching 域，person 指向 homeroom 身份）
        db.add(
            wm.WsStudentNote(
                data_domain="teaching",
                person_id=seed.yi,
                date=date(2026, 10, 20),
                category="谈话",
                content="合成跨域档案",
            )
        )
        # 本域档案但日期在旧学年（补记历史谈话）
        db.add(
            wm.WsStudentNote(
                data_domain="homeroom",
                person_id=seed.bing,
                date=date(2026, 6, 15),
                category="谈话",
                content="合成旧学年补记",
            )
        )
        db.commit()
    finally:
        db.close()

    r = client.post(f"{API}/homeroom/rollover/{token}/undo")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["conflicted"] == []
    assert body["undone"] == 3
    assert body["class_removed"] is True

    db = _db()
    try:
        _wipe_rollover_state(db, seed)
        # 旧学年补录行原样保留
        assert (
            db.query(wm.HomeworkAssignment)
            .filter_by(batch_token="fix13-old-year-makeup")
            .count()
            == 1
        )
    finally:
        db.close()


def test_target_class_writes_still_block_undo(client, seed):
    """目标班上的真实新写入（成绩/作业/档案）仍然阻断撤销（保护语义）。"""
    token, result = _homeroom_confirm(client, seed)
    new_class_id = result["class_id"]

    from app.db import workspace_models as wm

    db = _db()
    try:
        # 丙：目标班上的新成绩
        db.add(
            wm.ScoreFact(
                data_domain="homeroom",
                academic_year_id=seed.ay2,
                exam_name="合成新学年考",
                exam_date=date(2026, 11, 5),
                class_ref_id=new_class_id,
                identity_id=seed.bing,
                subject="物理",
                score=88.0,
                source="fix13-test",
            )
        )
        # 甲：挂在目标班作业批次上的新提交
        assignment = wm.HomeworkAssignment(
            data_domain="homeroom",
            academic_year_id=seed.ay2,
            class_ref_id=new_class_id,
            subject="语文",
            homework_type="练习册",
            assigned_date=date(2026, 10, 20),
            batch_token="fix13-new-class-hw",
            expected_members_json="[]",
            status="active",
        )
        db.add(assignment)
        db.flush()
        db.add(
            wm.HomeworkSubmission(
                assignment_id=assignment.id,
                person_id=seed.jia,
                submission_status="missing",
            )
        )
        # 乙：本域、日期在目标学年的新档案
        db.add(
            wm.WsStudentNote(
                data_domain="homeroom",
                person_id=seed.yi,
                date=date(2026, 11, 1),
                category="谈话",
                content="合成新学年谈话",
            )
        )
        db.commit()
    finally:
        db.close()

    r = client.post(f"{API}/homeroom/rollover/{token}/undo")
    assert r.status_code == 200, r.text
    body = r.json()
    conflicted = {c["person_id"]: c["reason"] for c in body["conflicted"]}
    assert set(conflicted) == {seed.jia, seed.yi, seed.bing}
    assert "作业" in conflicted[seed.jia]
    assert "档案" in conflicted[seed.yi]
    assert "成绩" in conflicted[seed.bing]
    assert body["undone"] == 0
    assert body["class_removed"] is False

    db = _db()
    try:
        _wipe_rollover_state(db, seed)
    finally:
        db.close()


# ────────────────────── 13.4 补做换届时点 ──────────────────────


def test_sampling_at_source_year_end_keeps_capped_members(client, seed):
    """源学年 valid_to 已在学年末截止（2026-07-15）→ 今天已过学年结束，
    按 min(今天, 学年结束日) 取样：截止成员不漏；届前离班者列出排除。"""
    from app.db import workspace_models as wm

    db = _db()
    try:
        # 甲乙：学年末截止（如结业批处理写的 valid_to=学年结束日）
        for pid in (seed.jia, seed.yi):
            enr = (
                db.query(wm.Enrollment)
                .filter_by(admin_class_id=seed.h6, identity_id=pid)
                .one()
            )
            enr.valid_to = date(2026, 7, 15)
        # 丙：届前已离班
        enr = (
            db.query(wm.Enrollment)
            .filter_by(admin_class_id=seed.h6, identity_id=seed.bing)
            .one()
        )
        enr.valid_to = date(2026, 6, 30)
        db.commit()
    finally:
        db.close()

    body = _homeroom_preview(client, seed)
    expected_as_of = min(date.today(), date(2026, 7, 15))
    assert body["roster_as_of"] == expected_as_of.isoformat()
    assert "学年结束日" in body["roster_basis"]
    # 旧契约按今天取样：甲乙 valid_to=07-15 < 今天 → 0 人；新契约 2 人
    assert sorted(s["person_id"] for s in body["students"]) == [seed.jia, seed.yi]
    excluded = {s["person_id"]: s for s in body["excluded_students"]}
    assert set(excluded) == {seed.bing}
    assert "离班" in excluded[seed.bing]["note"]

    token = body["token"]
    r = client.post(f"{API}/homeroom/rollover", json={"token": token})
    assert r.status_code == 200, r.text
    assert r.json()["rolled_over"] == 2

    r = client.post(f"{API}/homeroom/rollover/{token}/undo")
    assert r.status_code == 200, r.text
    assert r.json()["undone"] == 2 and r.json()["class_removed"] is True

    db = _db()
    try:
        _wipe_rollover_state(db, seed)
        # 恢复开放学籍供潜在后续场景
        for pid in (seed.jia, seed.yi, seed.bing):
            enr = (
                db.query(wm.Enrollment)
                .filter_by(admin_class_id=seed.h6, identity_id=pid)
                .one()
            )
            enr.valid_to = None
        db.commit()
    finally:
        db.close()
