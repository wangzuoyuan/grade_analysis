"""「写入名册」导入撤销（undo_roster_import）：按批次快照逆向还原。

覆盖：仅姓名建册的撤销、占位替换（记录/alias 迁移）的逆向、届命名空间
迁移的逆向、重复撤销拒绝、导入后学生已被使用时保守跳过。快照模式与
rollover_confirm_batch 的 undo 同一套安全口径。
"""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="roster-undo-test-")
os.environ["EXAM_TRACKER_DIR"] = os.path.join(_TMP, "examdata")
os.makedirs(os.environ["EXAM_TRACKER_DIR"], exist_ok=True)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.db.models import (  # noqa: E402
    SessionLocal, Teacher, Exam, SubjectScore, ClassRoster,
    HomeworkRecord, SpecialRecord, StudentNote, StudentAlias,
    StudentIdentity, RosterImportBatch, StudentChangeLog,
)
from app.analysis.identity import ensure_identity, link_aliases, person_ids  # noqa: E402


@pytest.fixture
def fresh():
    """每个用例从空库 + 教师绑定（高1=6班、高2=6班）开始。"""
    s = SessionLocal()
    for m in (StudentChangeLog, RosterImportBatch, StudentNote, SpecialRecord,
              HomeworkRecord, StudentAlias, StudentIdentity, ClassRoster,
              SubjectScore, Exam, Teacher):
        s.query(m).delete()
    s.commit()
    s.merge(Teacher(id=1, target_class_high1=6, target_class_high2=6))
    s.commit()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client():
    return TestClient(app)


def _import(client, rows):
    r = client.post("/api/rollover/roster", json={"grade": 2, "class_num": 6, "rows": rows})
    assert r.status_code == 200, r.text
    return r.json()


def test_undo_name_only_import_removes_created_rows(client, fresh):
    s = fresh
    body = _import(client, [{"name": "张三"}, {"name": "李四"}])
    assert body["created"] == 2
    assert body["batch_id"]
    assert s.query(ClassRoster).filter(ClassRoster.grade == 2).count() == 2

    r = client.post(f"/api/rollover/roster/{body['batch_id']}/undo")
    assert r.status_code == 200, r.text
    out = r.json()
    assert sorted(out["removed_rows"]) == ["TMP-2-6-张三", "TMP-2-6-李四"]
    assert s.query(ClassRoster).filter(ClassRoster.grade == 2).count() == 0

    # 二次撤销 → 拒绝
    r2 = client.post(f"/api/rollover/roster/{body['batch_id']}/undo")
    assert r2.status_code == 409


def test_undo_placeholder_replace_restores_old_row_refs_and_alias(client, fresh):
    s = fresh
    # 先粘姓名建占位行 + 挂身份链 + 记作业/档案（替换后这些都要能迁回）
    _import(client, [{"name": "王五"}])
    iid = ensure_identity(s, display_name="王五")
    link_aliases(s, iid, [("TMP-2-6-王五", 2)], "name_confirmed")
    s.add(HomeworkRecord(student_id="TMP-2-6-王五", date="2026-09-01", subject="数学"))
    s.add(StudentNote(student_id="TMP-2-6-王五", date="2026-09-02",
                      category="谈话", content="x"))
    s.commit()

    # 正式号替换
    body = _import(client, [{"student_id": "20250601", "name": "王五"}])
    assert body["replaced"] == 1
    assert s.query(ClassRoster).filter(ClassRoster.student_id == "TMP-2-6-王五").count() == 0
    assert s.query(HomeworkRecord).filter(
        HomeworkRecord.student_id == "20250601").count() == 1

    # 撤销 → 一切还原
    r = client.post(f"/api/rollover/roster/{body['batch_id']}/undo")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["restored_rows"] == ["TMP-2-6-王五"]
    assert out["moved_back_refs"] == 2

    s.expire_all()
    assert s.query(ClassRoster).filter(ClassRoster.student_id == "20250601").count() == 0
    old = s.query(ClassRoster).filter(ClassRoster.student_id == "TMP-2-6-王五").one()
    assert old.name == "王五"
    assert s.query(HomeworkRecord).filter(
        HomeworkRecord.student_id == "TMP-2-6-王五").count() == 1
    assert s.query(StudentNote).filter(
        StudentNote.student_id == "TMP-2-6-王五").count() == 1
    alias = s.query(StudentAlias).filter(
        StudentAlias.student_id == "TMP-2-6-王五").one()
    assert alias.identity_id == iid
    assert person_ids(s, "TMP-2-6-王五") == {"TMP-2-6-王五"}


def test_undo_reverses_namespace_migration(client, fresh):
    s = fresh
    # 高一撞号数据（成绩 + 花名册）
    s.add(Exam(id=101, name="高一期末", grade=1, semester="下",
               exam_date="2025-06", exam_type="期末"))
    s.add(SubjectScore(exam_id=101, student_id="9900601", name="赵六",
                       class_num=6, subject="语文", raw_score=90.0))
    s.add(ClassRoster(student_id="9900601", name="赵六", grade=1, class_num=6))
    s.commit()

    # 高二粘贴同号名册 → 守门把高一前缀化
    body = _import(client, [{"student_id": "9900601", "name": "新人"}])
    assert s.query(ClassRoster).filter(
        ClassRoster.student_id == "G1::9900601", ClassRoster.grade == 1).count() == 1

    # 撤销 → 高一还原为裸号，高二新行消失
    r = client.post(f"/api/rollover/roster/{body['batch_id']}/undo")
    assert r.status_code == 200, r.text
    assert r.json()["renamed_reversed"] == 1

    s.expire_all()
    assert s.query(ClassRoster).filter(
        ClassRoster.student_id == "9900601", ClassRoster.grade == 1).count() == 1
    assert s.query(ClassRoster).filter(ClassRoster.grade == 2).count() == 0
    assert s.query(SubjectScore).filter(
        SubjectScore.student_id == "9900601").count() == 1
    assert s.query(SubjectScore).filter(
        SubjectScore.student_id.like("G1::%")).count() == 0


def test_undo_skips_created_row_used_afterwards(client, fresh):
    s = fresh
    body = _import(client, [{"name": "孙七"}])
    # 导入后学生立刻被使用：挂身份 + 记作业
    iid = ensure_identity(s, display_name="孙七")
    link_aliases(s, iid, [("TMP-2-6-孙七", 2)], "manual")
    s.add(HomeworkRecord(student_id="TMP-2-6-孙七", date="2026-09-03", subject="物理"))
    s.commit()

    r = client.post(f"/api/rollover/roster/{body['batch_id']}/undo")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["removed_rows"] == []
    assert any("保留" in item["reason"] for item in out["skipped"])
    assert s.query(ClassRoster).filter(
        ClassRoster.student_id == "TMP-2-6-孙七").count() == 1


def test_last_import_and_scope_guard(client, fresh):
    s = fresh
    assert client.get("/api/rollover/roster/last-import").json() == {"batch_id": None}

    body = _import(client, [{"name": "周九"}])
    last = client.get("/api/rollover/roster/last-import").json()
    assert last["batch_id"] == body["batch_id"]
    assert last["summary"]["created"] == 1

    # 撤销后 last-import 变空
    client.post(f"/api/rollover/roster/{body['batch_id']}/undo")
    assert client.get("/api/rollover/roster/last-import").json() == {"batch_id": None}

    # 越权防呆：批次不存在 → 404
    assert client.post("/api/rollover/roster/nonexistent/undo").status_code == 404
