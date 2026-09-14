"""升级换届（rollover）REST 路由集成测试。

镜像 test_homework_router.py / test_notes_router.py 的 TestClient 风格，但用
模块级 EXAM_TRACKER_DIR 指向全新临时目录，让 app.main 的 engine 绑定到隔离
库；所有数据自建自验，绝不触碰 ~/.exam-tracker。
"""

import os
import sqlite3
import tempfile

# 必须在 import app.main 之前把 EXAM_TRACKER_DIR 钉到临时目录，使
# app.paths / app.db.models 的 engine 绑定隔离库。
_TMP = tempfile.mkdtemp(prefix="rollover_test_")
os.environ["EXAM_TRACKER_DIR"] = os.path.join(_TMP, "examdata")
os.makedirs(os.environ["EXAM_TRACKER_DIR"], exist_ok=True)

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.models import (
    SessionLocal,
    Exam,
    SubjectScore,
    TotalScore,
    Teacher,
    ClassRoster,
    HomeworkSetting,
    ImportedHistory,
)


@pytest.fixture(scope="module")
def client():
    """单 TestClient，模块内共享；数据在 module 级 seed 一次。"""
    seed()
    return TestClient(app)


@pytest.fixture
def db():
    """直接读 DB 验证落库（与 client 同一 engine/库）。"""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _seed_exam(eid, name, grade, exam_date):
    s = SessionLocal()
    s.add(
        Exam(
            id=eid,
            name=name,
            grade=grade,
            semester="上",
            exam_date=exam_date,
            exam_type="月考",
        )
    )
    s.commit()
    s.close()


def _seed_subject(exam_id, student_id, name, class_num, subject="语文"):
    s = SessionLocal()
    s.add(
        SubjectScore(
            exam_id=exam_id,
            student_id=student_id,
            name=name,
            class_num=class_num,
            subject=subject,
            raw_score=80.0,
        )
    )
    s.commit()
    s.close()


def _seed_total(exam_id, student_id, total_type="主三门", total_score=240.0, xueji_rank=10):
    s = SessionLocal()
    s.add(
        TotalScore(
            exam_id=exam_id,
            student_id=student_id,
            total_type=total_type,
            total_score=total_score,
            xueji_rank=xueji_rank,
        )
    )
    s.commit()
    s.close()


def seed():
    """建两场考试 + 若干学生。

    数据布局（class_num 6=高一班, 3=高二班）：
      - 同名「陈一」横跨 G1(class6 sid 101) 与 G2(class3 sid 201) -> ambiguous
      - G1 还有「马二」(class6 sid 102) -> left_class 候选
      - G2 独有「刘三」(class3 sid 203) -> new
    班主任绑定 G1->6, G2->3。
    """
    s = SessionLocal()
    # 清空隔离库已有残留（保险，多重跑安全）
    for m in (SubjectScore, TotalScore, Exam, Teacher, ClassRoster,
              HomeworkSetting, ImportedHistory):
        s.query(m).delete()
    s.commit()
    s.close()

    _seed_exam(101, "高一入学测", 1, "2024-09")
    _seed_exam(201, "高二开学测", 2, "2025-09")

    # G1 / class 6
    _seed_subject(101, "g1_101", "陈一", 6)
    _seed_subject(101, "g1_102", "马二", 6)
    # G2 / class 3
    _seed_subject(201, "g2_201", "陈一", 3)   # 与 G1 同名 -> ambiguous
    _seed_subject(201, "g2_203", "刘三", 3)   # G2 独有 -> new

    # 主三门总分（供 inherited 后 prev_aliases 附加信息用）
    _seed_total(101, "g1_101", "主三门", 240.0, 10)
    _seed_total(201, "g2_201", "主三门", 260.0, 5)
    _seed_total(201, "g2_203", "主三门", 250.0, 20)

    # 班主任绑定班号
    s = SessionLocal()
    s.merge(Teacher(id=1, target_class_high1=6, target_class_high2=3))
    s.commit()
    s.close()


# ─────────────────────────── preview 四态 ───────────────────────────


def test_preview_returns_buckets_and_summary(client):
    r = client.get("/api/rollover/preview?grade=2&class_num=3")
    assert r.status_code == 200
    body = r.json()
    for key in ("inherited", "ambiguous", "new", "unmatched", "left_class", "summary"):
        assert key in body
    summ = body["summary"]
    assert set(summ.keys()) >= {"inherited", "ambiguous", "new", "unmatched", "left_class", "total"}

    # 同名陈一（g2_201）应落在 ambiguous
    amb_sids = {a["student_id"] for a in body["ambiguous"]}
    assert "g2_201" in amb_sids
    amb = next(a for a in body["ambiguous"] if a["student_id"] == "g2_201")
    assert amb["name"] == "陈一"
    # 候选指向 G1 同名「陈一」g1_101
    cand_ids = {c["student_id"] for c in amb["candidates"]}
    assert "g1_101" in cand_ids

    # 刘三（g2_203）G2 独有 -> new
    new_sids = {n["student_id"] for n in body["new"]}
    assert "g2_203" in new_sids


def test_preview_left_class_contains_g1_student_not_in_g2(client):
    r = client.get("/api/rollover/preview?grade=2&class_num=3")
    body = r.json()
    left_sids = {l["student_id"] for l in body["left_class"]}
    # 马二（g1_102）在 G1 班 6，不在 G2 班 3 -> left_class
    assert "g1_102" in left_sids


def test_preview_roster_is_scoped_to_requested_class(client, db):
    db.merge(ClassRoster(student_id="other-class", name="外班生", grade=2, class_num=9))
    db.merge(ClassRoster(student_id="this-class", name="本班生", grade=2, class_num=3))
    db.commit()
    body = client.get("/api/rollover/preview?grade=2&class_num=3").json()
    visible = {
        row["student_id"]
        for bucket in ("inherited", "ambiguous", "new", "unmatched")
        for row in body[bucket]
    }
    assert "this-class" in visible
    assert "other-class" not in visible


# ─────────────────────────── link ───────────────────────────


def test_link_then_inherited(client):
    """把 g2_201 链到 g1_101 后再 preview，g2_201 进入 inherited。"""
    r = client.post(
        "/api/rollover/link",
        json={
            "g2_student_id": "g2_201",
            "name": "陈一",
            "g1_student_id": "g1_101",
            "gender": "男",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    iid = body["identity_id"]
    assert iid is not None
    alias_sids = {a["student_id"] for a in body["aliases"]}
    assert {"g2_201", "g1_101"} <= alias_sids

    # re-preview：g2_201 现在应 in inherited，不在 ambiguous
    r2 = client.get("/api/rollover/preview?grade=2&class_num=3")
    p = r2.json()
    inh_sids = {x["student_id"] for x in p["inherited"]}
    amb_sids = {x["student_id"] for x in p["ambiguous"]}
    assert "g2_201" in inh_sids
    assert "g2_201" not in amb_sids
    inh = next(x for x in p["inherited"] if x["student_id"] == "g2_201")
    assert inh["identity_id"] == iid
    # prev_aliases 应含 G1 的 g1_101
    prev_ids = {a["student_id"] for a in inh["prev_aliases"]}
    assert "g1_101" in prev_ids


def test_unlink_alias_endpoint(client):
    """删链后学号回到独立人。用刚 link 过的 g2_201。"""
    r = client.delete("/api/rollover/link/g2_201")
    assert r.status_code == 200
    assert r.json() == {"unlinked": "g2_201"}
    # 再删一次 -> 404
    r2 = client.delete("/api/rollover/link/g2_201")
    assert r2.status_code == 404


# ─────────────────────────── crosswalk ───────────────────────────


def test_crosswalk_bulk_link(client, db):
    """bulk 名册导入：两个全新组合 link 成功，返回计数。

    故意的「假链」挂在隔离学号 g2_299（无成绩、不入花名册派生）上：
    正式学号导入现在会统一校验别名归属，若假链挂在有成绩的学号上，
    后续 from_scores 派生会按契约整批拒绝。
    """
    r = client.post(
        "/api/rollover/crosswalk",
        json={
            "rows": [
                {"g1_sid": "g1_101", "g2_sid": "g2_201", "name": "陈一"},
                {"g1_sid": "g1_102", "g2_sid": "g2_299", "name": "同名假链"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"linked", "conflict", "skipped"}
    assert body["linked"] >= 1


def test_grade3_link_and_crosswalk_record_dynamic_alias_grades(client, db):
    _seed_exam(301, "高三开学测", 3, "2026-09")
    _seed_subject(301, "g3_dynamic", "高三生", 8)
    response = client.post(
        "/api/rollover/link",
        json={
            "g2_student_id": "g3_dynamic",
            "g1_student_id": "g2_dynamic",
            "name": "高三生",
            "grade": 3,
        },
    )
    assert response.status_code == 200, response.text
    grades = {row["student_id"]: row["grade"] for row in response.json()["aliases"]}
    assert grades == {"g2_dynamic": 2, "g3_dynamic": 3}

    response2 = client.post(
        "/api/rollover/crosswalk",
        json={
            "target_grade": 3,
            "rows": [{"g1_sid": "g2_cross", "g2_sid": "g3_cross", "name": "对照生"}],
        },
    )
    assert response2.status_code == 200, response2.text
    from app.analysis.identity import aliases_of, identity_of
    iid = identity_of(db, "g3_cross")
    aliases = {row.student_id: row.grade for row in aliases_of(db, iid)}
    assert aliases == {"g2_cross": 2, "g3_cross": 3}


def test_unbind_class_accepts_explicit_null(client):
    bound = client.post("/api/teacher/bind-class", json={"grade": 3, "class_num": 8})
    assert bound.status_code == 200
    unbound = client.post("/api/teacher/bind-class", json={"grade": 3, "class_num": None})
    assert unbound.status_code == 200, unbound.text
    assert unbound.json()["bound_class"] is None
    assert client.get("/api/teacher").json()["target_class_high3"] is None


# ─────────────────────────── import-history ───────────────────────────


def test_import_history_writes_rows(client, db):
    # 自建一个独立 identity，不依赖前面用例的落库状态
    from app.analysis.identity import ensure_identity, link_aliases

    iid = ensure_identity(db, display_name="历史生测试")
    link_aliases(db, iid, [("hist_sid_test", 1)], "manual")

    r = client.post(
        "/api/rollover/import-history",
        json={
            "identity_id": iid,
            "rows": [
                {
                    "exam_label": "初三月考",
                    "kind": "subject",
                    "subject": "数学",
                    "raw_score": 110.0,
                    "grade": 3,
                },
                {
                    "exam_label": "初三月考",
                    "kind": "total",
                    "total_type": "主三门",
                    "raw_score": 330.0,
                    "xueji_rank": 12,
                    "grade": 3,
                },
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["identity_id"] == iid
    assert body["imported"] == 2

    # 落库验证
    db.expire_all()
    rows = db.query(ImportedHistory).filter(ImportedHistory.identity_id == iid).all()
    kinds = {r.kind for r in rows}
    assert "subject" in kinds and "total" in kinds


def test_grade3_history_import_links_current_alias_and_keeps_grade2_history(client, db):
    response = client.post(
        "/api/rollover/import-history",
        json={
            "student_id": "g3-history",
            "name": "历史高三生",
            "target_grade": 3,
            "rows": [{
                "exam_label": "高二期末",
                "kind": "subject",
                "subject": "数学",
                "raw_score": 99,
                "grade": 2,
            }],
        },
    )
    assert response.status_code == 200, response.text
    from app.analysis.identity import aliases_of
    iid = response.json()["identity_id"]
    aliases = {row.student_id: row.grade for row in aliases_of(db, iid)}
    assert aliases["g3-history"] == 3
    history = db.query(ImportedHistory).filter_by(identity_id=iid).one()
    assert history.grade == 2


# ─────────────────────────── active-grade ───────────────────────────


def test_active_grade_sets_homework_setting(client, db):
    r = client.patch("/api/rollover/active-grade", json={"grade": 2})
    assert r.status_code == 200
    assert r.json() == {"active_grade": 2}

    row = db.query(HomeworkSetting).filter_by(key="active_grade").first()
    assert row is not None
    assert int(row.value) == 2


def test_active_grade_rejects_unbound_grade(client):
    r = client.patch("/api/rollover/active-grade", json={"grade": 3})
    assert r.status_code == 409
    assert "尚未绑定行政班" in r.json()["detail"]


def test_active_grade_rejects_invalid_grade(client):
    r = client.patch("/api/rollover/active-grade", json={"grade": 4})
    assert r.status_code == 422


# ─────────────────────────── roster 结转 ───────────────────────────


def test_roster_from_scores_builds_grade2_roster(client, db):
    r = client.post(
        "/api/rollover/roster",
        json={"grade": 2, "class_num": 3, "from_scores": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] >= 1

    rows = db.query(ClassRoster).filter(ClassRoster.grade == 2).all()
    assert rows, "grade=2 花名册为空"
    roster_sids = {r.student_id for r in rows}
    # G2 班 3 的成绩学号都应入册
    assert {"g2_201", "g2_203"} <= roster_sids
    # 全部行 grade 必须是 2
    assert all(r.grade == 2 for r in rows)


def test_homework_migrate_uses_active_grade_for_identity_and_roster(tmp_path, monkeypatch):
    """迁移必须按当前年级消歧同名学生，并保留全局 active_grade。"""
    from app.homework import migrate as migrate_module

    source = tmp_path / "homework.db"
    connection = sqlite3.connect(source)
    connection.executescript(
        """
        CREATE TABLE students (
            id INTEGER PRIMARY KEY,
            student_no TEXT,
            name TEXT,
            gender TEXT,
            excluded INTEGER DEFAULT 0
        );
        CREATE TABLE records (
            student_id INTEGER,
            date TEXT,
            subject TEXT,
            content TEXT,
            remark TEXT
        );
        CREATE TABLE special_records (
            student_id INTEGER,
            date TEXT,
            type TEXT,
            note TEXT
        );
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO students VALUES (1, '1', '陈一', '女', 0);
        INSERT INTO records VALUES (1, '2026-07-01', '数学', '练习册', '');
        INSERT INTO settings VALUES ('semester_name', '高二上');
        """
    )
    connection.commit()
    connection.close()

    def isolated_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(migrate_module, "get_db", isolated_get_db)
    migrate_module.migrate(str(source))

    verify = SessionLocal()
    try:
        roster = verify.query(ClassRoster).one()
        assert roster.student_id == "g2_201"
        assert roster.class_num == 3
        assert roster.grade == 2
        assert verify.get(HomeworkSetting, "active_grade").value == "2"
        assert verify.get(HomeworkSetting, "semester_name").value == "高二上"
    finally:
        verify.close()
