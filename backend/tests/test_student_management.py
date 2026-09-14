"""学生管理（/api/manage）集成测试。

镜像 test_rollover.py 的隔离方式（模块级 EXAM_TRACKER_DIR + 自建 seed）。
覆盖：
  - 作用域：列表只含当前绑定年级班级；已归档学生默认隐藏、可选展示
  - 创建：仅姓名（临时学号+主档+alias）、显式学号、同名拒绝、学号占用拒绝、
    成绩库姓名不符拒绝
  - 编辑：规范姓名写主档并同步花名册展示名，SubjectScore.name 快照不动，
    考试详情展示名走主档覆盖
  - 纠正录错学号：单事务迁移全部引用（成绩/总分/作业/特殊/档案/花名册/别名），
    目标占用 / 同场考试冲突 / 跨班越权拒绝且零落库
  - 新学年学号：只加 alias + 新学年花名册行，旧学号与历史数据保留；越权班级 409
  - 删除：影响预览计数、无数据直接删、有数据需确认且自动备份、身份按需保留；
    干净学生 confirm=false 直删、有跨学年别名仍需确认
  - 合并：预览冲突（同场考试双方有成绩拒绝自动合并）、事务性合并、需确认、自合并拒绝；
    双方无主档时合并建主档且重复学号保留为历史别名
  - 身份回填：幂等、同名学生各建独立主档（绝不按姓名合并）
  - 变更日志：操作类型 / 前后摘要落库可查；只返回当前绑定作用域的留痕
  - 编辑：未提交字段保持原值、显式 null 才清空（区分二者）；列表回显主档
    note/gender；在班状态随编辑同请求单事务提交
  - 新学年学号：目标班已有同号行时按身份校验（同身份幂等 / 无 alias 同名
    接续 / 异名或他身份拒绝）
"""

import os
import tempfile

# 必须在 import app.main 之前把 EXAM_TRACKER_DIR 钉到临时目录，使
# app.paths / app.db.models 的 engine 绑定隔离库。
_TMP = tempfile.mkdtemp(prefix="student_management_test_")
os.environ["EXAM_TRACKER_DIR"] = os.path.join(_TMP, "examdata")
os.makedirs(os.environ["EXAM_TRACKER_DIR"], exist_ok=True)

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.models import (
    SessionLocal,
    Exam,
    HomeworkRecord,
    HomeworkSetting,
    ImportedHistory,
    SpecialRecord,
    StudentAlias,
    StudentChangeLog,
    StudentIdentity,
    StudentNote,
    SubjectScore,
    Teacher,
    ClassRoster,
    TotalScore,
)
from app.analysis.identity import aliases_of, ensure_identity, identity_of, link_aliases


@pytest.fixture(scope="module")
def client():
    seed()
    return TestClient(app)


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def seed():
    """数据布局（active_grade=2，教师绑定高二 6 班，高一/高三也绑 6 班）：

    - 20250201 张三：花名册（座号1）+ 两场考试成绩 + 主三门总分 + 作业/特殊/档案，无主档
    - 20250202 / 20250205 两个同名「李四」：花名册无任何数据、无主档（回填同名不合并）
    - 20250203 王五：花名册 + 主档 identity（别名 g1_003 高一学号）+ 高一成绩 + 导入历史
    - 20250204 赵六：花名册 status=transferred（归档隐藏）+ 作业记录
    - 20250299 误建：花名册行，无任何关联数据
    - 20250305 孙七：仅成绩（高二6班），无花名册无主档
    - TMP-2-6-周九：仅花名册临时学号
    - 20250999 别人：花名册行（学号占用拒绝用）
    - 20250210 路人：高二6班成绩（成绩库姓名不符拒绝用）
    - 20250888 路人乙：高二7班成绩（跨班越权拒绝用）
    - 20250220/20250221：前者花名册+201成绩，后者 201 成绩（纠正同场冲突用）
    - 20250230 王小主（主档A）/20250231 张小三（主档B）：无同场重叠（合并成功用）
    - 20250240/20250241：同场 201 都有成绩（合并冲突用）
    """
    s = SessionLocal()
    for m in (StudentChangeLog, ImportedHistory, StudentNote, SpecialRecord,
              HomeworkRecord, StudentAlias, StudentIdentity, ClassRoster,
              SubjectScore, TotalScore, Exam, HomeworkSetting, Teacher):
        s.query(m).delete()
    s.commit()

    s.add(Exam(id=101, name="高一期末", grade=1, semester="下",
               exam_date="2025-06", exam_type="期末"))
    s.add(Exam(id=201, name="高二开学测", grade=2, semester="上",
               exam_date="2025-09", exam_type="月考"))
    s.add(Exam(id=202, name="高二期中", grade=2, semester="上",
               exam_date="2025-11", exam_type="期中"))
    s.commit()

    def score(eid, sid, name, cls, subj, raw, pct=None):
        s.add(SubjectScore(exam_id=eid, student_id=sid, name=name,
                           class_num=cls, subject=subj, raw_score=raw,
                           grade_percentile=pct))

    # 张三：两场成绩 + 总分 + 作业/特殊/档案
    score(201, "20250201", "张三", 6, "语文", 110.0, 0.85)
    score(202, "20250201", "张三", 6, "语文", 105.0, 0.80)
    s.add(TotalScore(exam_id=201, student_id="20250201", total_type="主三门",
                     total_score=261.0, xueji_rank=12, grade_percentile=0.85))
    s.add(HomeworkRecord(student_id="20250201", date="2025-09-10",
                         subject="数学", content="练习册"))
    s.add(SpecialRecord(student_id="20250201", date="2025-09-11", type="迟到"))
    s.add(StudentNote(student_id="20250201", date="2025-09-12",
                      category="谈话", content="近期状态波动，已沟通"))
    # 路人（成绩库姓名不符）/ 路人乙（跨班）
    score(201, "20250210", "路人", 6, "语文", 100.0)
    score(201, "20250888", "路人乙", 7, "语文", 95.0)
    # 王五高一成绩（别名 g1_003）
    score(101, "g1_003", "王五", 6, "语文", 108.0)
    # 纠正冲突对：20250220 与 20250221 同场 201
    score(201, "20250220", "钱一", 6, "语文", 99.0)
    score(201, "20250221", "钱一", 6, "数学", 99.0)
    # 合并成功对：20250230（202 成绩）/ 20250231（201 成绩），无重叠
    score(202, "20250230", "王小主", 6, "语文", 112.0)
    score(201, "20250231", "张小三", 6, "语文", 106.0)
    s.add(HomeworkRecord(student_id="20250231", date="2025-09-20",
                         subject="英语", content="默写"))
    # 合并冲突对：20250240 / 20250241 同场 201 都有语文
    score(201, "20250240", "孙二", 6, "语文", 100.0)
    score(201, "20250241", "孙二", 6, "语文", 101.0)
    # 孙七：仅成绩
    score(201, "20250305", "孙七", 6, "语文", 96.0)

    # 花名册（高二 6 班）
    roster_rows = [
        ("20250201", "张三", 1, "男", None),
        ("20250202", "李四", 2, "男", None),
        ("20250205", "李四", 5, "女", None),
        ("20250203", "王五", 3, "男", None),
        ("20250204", "赵六", 4, "男", "transferred"),
        ("20250299", "误建", None, None, None),
        ("TMP-2-6-周九", "周九", None, None, None),
        ("20250999", "别人", None, None, None),
        ("20250220", "钱一", None, None, None),
        ("20250230", "王小主", None, None, None),
        ("20250231", "张小三", None, None, None),
        ("20250240", "孙二", None, None, None),
        ("20250241", "孙二", None, None, None),
    ]
    for sid, name, seat, gender, status in roster_rows:
        s.add(ClassRoster(student_id=sid, name=name, class_num=6, grade=2,
                          seat_no=seat, gender=gender, status=status))
    # 高一 roster（王五旧学号），验证跨年保留
    s.add(ClassRoster(student_id="g1_003", name="王五", class_num=6, grade=1))

    # 王五主档：identity + 双别名 + 导入历史
    iid = ensure_identity(s, display_name="王五", gender="男")
    link_aliases(s, iid, [("20250203", 2), ("g1_003", 1)], "name_confirmed")
    s.add(ImportedHistory(identity_id=iid, grade=1, exam_label="高一上期末",
                          kind="total", total_type="主三门", raw_score=255.0))

    # 合并对主档：A=20250230 王小主 / B=20250231 张小三
    iid_a = ensure_identity(s, display_name="王小主", gender="男")
    link_aliases(s, iid_a, [("20250230", 2)], "manual")
    iid_b = ensure_identity(s, display_name="张小三", gender="男")
    link_aliases(s, iid_b, [("20250231", 2)], "manual")

    s.merge(Teacher(id=1, target_class_high1=6, target_class_high2=6,
                    target_class_high3=6))
    s.merge(HomeworkSetting(key="active_grade", value="2"))
    s.commit()
    s.close()


def _counts(client, sid):
    r = client.get(f"/api/manage/students/{sid}/delete-preview")
    return r


# ─────────────────────────── 列表与作用域 ───────────────────────────


def test_list_scoped_and_hides_archived(client):
    body = client.get("/api/manage/students").json()
    sids = {row["student_id"] for row in body}
    # 本班学生全在
    assert "20250201" in sids and "20250305" in sids and "TMP-2-6-周九" in sids
    # 他班 / 已归档默认不在
    assert "20250888" not in sids
    assert "20250204" not in sids
    # 归档学生可通过 include_archived 查看
    body_all = client.get("/api/manage/students", params={"include_archived": "true"}).json()
    sids_all = {row["student_id"] for row in body_all}
    assert "20250204" in sids_all
    zhao = next(r for r in body_all if r["student_id"] == "20250204")
    assert zhao["status"] == "transferred"
    # 孙七是成绩在册、未建花名册
    sun = next(r for r in body if r["student_id"] == "20250305")
    assert sun["in_roster"] is False
    assert sun["name"] == "孙七"


def test_list_counts_and_latest_main(client):
    rows = {r["student_id"]: r for r in client.get("/api/manage/students").json()}
    zs = rows["20250201"]
    assert zs["counts"]["subject_score"] == 2
    assert zs["counts"]["homework"] == 1
    assert zs["counts"]["special"] == 1
    assert zs["counts"]["note"] == 1
    assert zs["latest_main_score"] == 261
    assert zs["latest_main_rank"] == 12
    assert zs["latest_exam_name"] == "高二开学测"


def test_list_search(client):
    rows = client.get("/api/manage/students", params={"search": "张三"}).json()
    assert {r["student_id"] for r in rows} == {"20250201"}


# ─────────────────────────── 创建 ───────────────────────────


def test_create_student_without_sid(client, db):
    r = client.post("/api/manage/students", json={"name": "新同学", "gender": "女", "seat_no": 30})
    assert r.status_code == 200, r.text
    sid = r.json()["student_id"]
    assert sid == "TMP-2-6-新同学"
    row = db.query(ClassRoster).filter(ClassRoster.student_id == sid).first()
    assert row is not None and row.grade == 2 and row.class_num == 6
    iid = identity_of(db, sid)
    assert iid is not None
    ident = db.query(StudentIdentity).filter(StudentIdentity.id == iid).first()
    assert ident.display_name == "新同学" and ident.gender == "女"
    alias = db.query(StudentAlias).filter(StudentAlias.student_id == sid).first()
    assert alias is not None and alias.identity_id == iid and alias.grade == 2


def test_create_rejects_same_name_without_sid(client):
    r = client.post("/api/manage/students", json={"name": "李四"})
    assert r.status_code == 422
    assert "同名" in r.json()["detail"]


def test_create_rejects_occupied_or_mismatched_sid(client, db):
    before = db.query(ClassRoster).count()
    # 学号被花名册占用
    r1 = client.post("/api/manage/students", json={"name": "甲", "student_id": "20250999"})
    assert r1.status_code == 422
    # 学号已关联身份
    r2 = client.post("/api/manage/students", json={"name": "甲", "student_id": "g1_003"})
    assert r2.status_code == 422
    # 成绩库姓名不符
    r3 = client.post("/api/manage/students", json={"name": "甲", "student_id": "20250210"})
    assert r3.status_code == 422
    assert db.query(ClassRoster).count() == before


def test_create_with_explicit_sid(client, db):
    r = client.post("/api/manage/students", json={"name": "钱新", "student_id": "20250288"})
    assert r.status_code == 200, r.text
    assert identity_of(db, "20250288") is not None


# ─────────────────────────── 编辑 ───────────────────────────


def test_update_writes_identity_and_syncs_roster_keeps_snapshot(client, db):
    r = client.put("/api/manage/students/20250201", json={
        "name": "张三丰", "gender": "男", "seat_no": 1, "note": "体委员",
    })
    assert r.status_code == 200, r.text
    iid = identity_of(db, "20250201")
    ident = db.query(StudentIdentity).filter(StudentIdentity.id == iid).first()
    assert ident.display_name == "张三丰" and ident.note == "体委员"
    # 花名册展示名同步
    roster = db.query(ClassRoster).filter(ClassRoster.student_id == "20250201").first()
    assert roster.name == "张三丰"
    # 成绩快照不改写
    snap = db.query(SubjectScore).filter(
        SubjectScore.student_id == "20250201", SubjectScore.exam_id == 201
    ).first()
    assert snap.name == "张三"
    # 考试详情展示名走主档覆盖
    exam = client.get("/api/exams/201").json()
    student = next(s for s in exam["students"] if s["student_id"] == "20250201")
    assert student["name"] == "张三丰"


def test_update_out_of_scope_404(client):
    r = client.put("/api/manage/students/20250888", json={"name": "x"})
    assert r.status_code == 404


# ─────────────────────────── 纠正录错学号 ───────────────────────────


def test_correct_sid_migrates_all_refs_in_one_transaction(client, db):
    r = client.post("/api/manage/students/20250201/correct-sid",
                    json={"new_student_id": "20250215"})
    assert r.status_code == 200, r.text

    # 成绩/总分/作业/特殊/档案全部随迁
    assert db.query(SubjectScore).filter(SubjectScore.student_id == "20250201").count() == 0
    assert db.query(SubjectScore).filter(SubjectScore.student_id == "20250215").count() == 2
    assert db.query(TotalScore).filter(TotalScore.student_id == "20250215").count() == 1
    assert db.query(HomeworkRecord).filter(HomeworkRecord.student_id == "20250215").count() == 1
    assert db.query(SpecialRecord).filter(SpecialRecord.student_id == "20250215").count() == 1
    assert db.query(StudentNote).filter(StudentNote.student_id == "20250215").count() == 1

    # 花名册行原位换号，座号保留；旧号无残留
    roster = db.query(ClassRoster).filter(ClassRoster.student_id == "20250215").first()
    assert roster is not None and roster.seat_no == 1 and roster.grade == 2
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "20250201").count() == 0

    # 别名随迁且身份不变
    iid = identity_of(db, "20250215")
    assert iid is not None
    assert db.query(StudentAlias).filter(StudentAlias.student_id == "20250201").count() == 0

    # 变更日志
    log = db.query(StudentChangeLog).filter(
        StudentChangeLog.op_type == "correct_sid",
        StudentChangeLog.student_id == "20250215",
    ).first()
    assert log is not None
    assert log.before_summary["student_id"] == "20250201"
    assert log.detail["moved"]["subject_score"] == 2


def test_correct_sid_rejects_conflicts_and_rolls_back(client, db):
    # 目标学号被占用
    r1 = client.post("/api/manage/students/20250215/correct-sid",
                     json={"new_student_id": "20250999"})
    assert r1.status_code == 422
    # 同场考试双方都有成绩
    r2 = client.post("/api/manage/students/20250220/correct-sid",
                     json={"new_student_id": "20250221"})
    assert r2.status_code == 422
    assert "同场考试" in r2.json()["detail"]
    # 跨班越权（高二成绩在 7 班）
    r3 = client.post("/api/manage/students/20250215/correct-sid",
                     json={"new_student_id": "20250888"})
    assert r3.status_code == 422
    # 自身相同
    r4 = client.post("/api/manage/students/20250215/correct-sid",
                     json={"new_student_id": "20250215"})
    assert r4.status_code == 422

    # 连续失败后零落库：原学号数据完好
    assert db.query(SubjectScore).filter(SubjectScore.student_id == "20250215").count() == 2
    assert db.query(SubjectScore).filter(SubjectScore.student_id == "20250220").count() == 1
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "20250215").count() == 1
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "20250220").count() == 1


# ─────────────────────────── 新学年学号 ───────────────────────────


def test_new_year_sid_adds_alias_and_roster_keeps_history(client, db):
    r = client.post("/api/manage/students/20250203/new-year-sid",
                    json={"new_student_id": "20250301", "grade": 3, "class_num": 6})
    assert r.status_code == 200, r.text
    iid = identity_of(db, "20250203")
    # 新旧学号同属一个身份
    assert identity_of(db, "20250301") == iid
    # 新学年花名册行
    row = db.query(ClassRoster).filter(ClassRoster.student_id == "20250301").first()
    assert row is not None and row.grade == 3 and row.class_num == 6
    # 旧学号与高一历史原样保留
    assert db.query(SubjectScore).filter(SubjectScore.student_id == "g1_003").count() == 1
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "20250203").count() == 1
    assert {a.student_id for a in aliases_of(db, iid)} >= {"20250203", "g1_003", "20250301"}


def test_new_year_sid_rejects_unbound_or_mismatched_class(client):
    r1 = client.post("/api/manage/students/20250203/new-year-sid",
                     json={"new_student_id": "20250302", "grade": 3, "class_num": 7})
    assert r1.status_code == 409
    # 与当前班同学年班级 → 指引走纠正
    r2 = client.post("/api/manage/students/20250203/new-year-sid",
                     json={"new_student_id": "20250303", "grade": 2, "class_num": 6})
    assert r2.status_code == 422


# ─────────────────────────── 删除 ───────────────────────────


def test_delete_clean_student_without_any_data(client, db):
    # 创建 → 无数据 → 预览为 0 → 直接删除（身份一并清理）
    r = client.post("/api/manage/students", json={"name": "待删", "student_id": "20250277"})
    assert r.status_code == 200
    iid = identity_of(db, "20250277")
    assert iid is not None

    preview = client.get("/api/manage/students/20250277/delete-preview").json()
    assert preview["total_refs"] == 0 and preview["requires_confirm"] is False

    r = client.request("DELETE", "/api/manage/students/20250277", json={"confirm": True})
    assert r.status_code == 200
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "20250277").count() == 0
    assert db.query(StudentIdentity).filter(StudentIdentity.id == iid).count() == 0


def test_delete_with_data_requires_confirm_and_backs_up(client, db):
    from app.paths import BACKUP_DIR

    before_backups = set(os.listdir(BACKUP_DIR)) if os.path.isdir(BACKUP_DIR) else set()

    # 未确认 → 409 + 影响计数
    r = client.request("DELETE", "/api/manage/students/20250215", json={"confirm": False})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["requires_confirm"] is True
    assert detail["counts"]["subject_score"] == 2
    assert detail["counts"]["note"] == 1
    # 未确认时零落库
    assert db.query(SubjectScore).filter(SubjectScore.student_id == "20250215").count() == 2

    # 确认 → 删除 + 自动备份
    r = client.request("DELETE", "/api/manage/students/20250215", json={"confirm": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["backup_file"] is not None
    after_backups = set(os.listdir(BACKUP_DIR))
    assert body["backup_file"] in after_backups - before_backups

    assert db.query(SubjectScore).filter(SubjectScore.student_id == "20250215").count() == 0
    assert db.query(HomeworkRecord).filter(HomeworkRecord.student_id == "20250215").count() == 0
    assert db.query(StudentNote).filter(StudentNote.student_id == "20250215").count() == 0
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "20250215").count() == 0
    # 身份无残留别名 → 一并删除
    assert db.query(StudentAlias).filter(StudentAlias.student_id == "20250215").count() == 0
    log = db.query(StudentChangeLog).filter(
        StudentChangeLog.op_type == "delete",
        StudentChangeLog.student_id == "20250215",
    ).first()
    assert log is not None and log.detail["backup_file"] == body["backup_file"]


def test_delete_keeps_identity_when_other_aliases_remain(client, db):
    preview = client.get("/api/manage/students/20250203/delete-preview").json()
    assert preview["requires_confirm"] is True
    assert "g1_003" in preview["other_aliases_kept"]
    assert preview["imported_history_kept"] == 1

    r = client.request("DELETE", "/api/manage/students/20250203", json={"confirm": True})
    assert r.status_code == 200
    # 身份与其余学号、导入历史保留（经 g1_003 别名仍指向同一身份）
    iid = identity_of(db, "g1_003")
    assert iid is not None
    assert db.query(StudentAlias).filter(StudentAlias.student_id == "g1_003").count() == 1
    assert db.query(ImportedHistory).filter(ImportedHistory.identity_id == iid).count() == 1


def test_delete_out_of_scope_404(client):
    r = client.request("DELETE", "/api/manage/students/20250888", json={"confirm": True})
    assert r.status_code == 404


# ─────────────────────────── 归档 ───────────────────────────


def test_archive_and_restore(client, db):
    r = client.post("/api/manage/students/20250240/archive", json={"status": "transferred"})
    assert r.status_code == 200
    db.expire_all()  # 端点在另一会话提交，先过期本地缓存再读
    row = db.query(ClassRoster).filter(ClassRoster.student_id == "20250240").first()
    assert row.status == "transferred"
    # 数据不动，仅默认列表隐藏
    assert db.query(SubjectScore).filter(SubjectScore.student_id == "20250240").count() == 1
    assert "20250240" not in {x["student_id"] for x in client.get("/api/manage/students").json()}

    r = client.post("/api/manage/students/20250240/archive", json={"status": "active"})
    assert r.status_code == 200
    db.expire_all()
    row = db.query(ClassRoster).filter(ClassRoster.student_id == "20250240").first()
    assert row.status is None
    assert "20250240" in {x["student_id"] for x in client.get("/api/manage/students").json()}


# ─────────────────────────── 合并 ───────────────────────────


def test_merge_preview_and_conflict_block(client):
    # 无重叠：可合并
    ok = client.post("/api/manage/students/merge-preview", json={
        "primary_student_id": "20250230", "duplicate_student_id": "20250231",
    }).json()
    assert ok["mergeable"] is True and ok["conflicts"] == []
    assert ok["duplicate"]["counts"]["homework"] == 1

    # 同场考试双方有成绩：冲突拒绝
    conflict = client.post("/api/manage/students/merge-preview", json={
        "primary_student_id": "20250240", "duplicate_student_id": "20250241",
    })
    assert conflict.status_code == 200
    body = conflict.json()
    assert body["mergeable"] is False
    assert body["conflicts"][0]["exam_id"] == 201

    r = client.post("/api/manage/students/merge", json={
        "primary_student_id": "20250240", "duplicate_student_id": "20250241",
        "confirm": True,
    })
    assert r.status_code == 409
    assert r.json()["detail"]["conflicts"][0]["exam_id"] == 201


def test_merge_requires_confirm(client):
    r = client.post("/api/manage/students/merge", json={
        "primary_student_id": "20250230", "duplicate_student_id": "20250231",
        "confirm": False,
    })
    assert r.status_code == 409
    assert r.json()["detail"]["requires_confirm"] is True


def test_merge_transactional_success(client, db):
    iid_a_before = identity_of(db, "20250230")
    iid_b_before = identity_of(db, "20250231")
    assert iid_a_before is not None and iid_b_before is not None
    assert iid_a_before != iid_b_before

    r = client.post("/api/manage/students/merge", json={
        "primary_student_id": "20250230", "duplicate_student_id": "20250231",
        "confirm": True,
    })
    assert r.status_code == 200, r.text

    # 数据全部并入主学号（原 202 一场 + 迁入 201 一场）
    assert db.query(SubjectScore).filter(SubjectScore.student_id == "20250231").count() == 0
    assert db.query(SubjectScore).filter(SubjectScore.student_id == "20250230").count() == 2
    assert db.query(HomeworkRecord).filter(HomeworkRecord.student_id == "20250230").count() == 1
    # 重复花名册行删除，主行保留
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "20250231").count() == 0
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "20250230").count() == 1
    # 身份合并：B 并入 A 并删除；重复学号保留为 A 的历史学号
    assert identity_of(db, "20250231") == iid_a_before
    assert db.query(StudentIdentity).filter(StudentIdentity.id == iid_b_before).count() == 0
    alias = db.query(StudentAlias).filter(StudentAlias.student_id == "20250231").first()
    assert alias is not None and alias.identity_id == iid_a_before
    # 日志
    log = db.query(StudentChangeLog).filter(
        StudentChangeLog.op_type == "merge",
        StudentChangeLog.student_id == "20250230",
    ).first()
    assert log is not None


def test_merge_self_rejected(client):
    r = client.post("/api/manage/students/merge", json={
        "primary_student_id": "20250230", "duplicate_student_id": "20250230",
        "confirm": True,
    })
    assert r.status_code == 422


# ─────────────────────────── 身份回填 ───────────────────────────


def test_backfill_idempotent_and_never_merges_same_names(client, db):
    preview = client.get("/api/manage/backfill-preview").json()
    pending = {row["student_id"] for row in preview["pending"]}
    assert "20250202" in pending and "20250205" in pending
    assert "20250305" in pending and "TMP-2-6-周九" in pending
    # 已有主档的不在列
    assert "20250203" not in pending and "20250230" not in pending

    r = client.post("/api/manage/backfill-identities")
    assert r.status_code == 200, r.text
    assert r.json()["created"] == len(pending)

    # 同名两个李四 → 两张独立主档（绝不按姓名合并）
    iid_2 = identity_of(db, "20250202")
    iid_5 = identity_of(db, "20250205")
    assert iid_2 is not None and iid_5 is not None and iid_2 != iid_5
    ident_2 = db.query(StudentIdentity).filter(StudentIdentity.id == iid_2).first()
    ident_5 = db.query(StudentIdentity).filter(StudentIdentity.id == iid_5).first()
    assert ident_2.display_name == "李四" and ident_5.display_name == "李四"

    # 幂等：再跑一遍 created=0
    r2 = client.post("/api/manage/backfill-identities")
    assert r2.status_code == 200
    assert r2.json()["created"] == 0


# ─────────────────────────── 变更日志 ───────────────────────────


def test_change_log_listing_and_filter(client):
    rows = client.get("/api/manage/change-log").json()
    assert len(rows) >= 5
    op_types = {r["op_type"] for r in rows}
    assert {"create", "update", "correct_sid", "delete", "merge", "backfill"} <= op_types
    # 时间倒序
    created = [r["created_at"] for r in rows]
    assert created == sorted(created, reverse=True)

    filtered = client.get("/api/manage/change-log",
                          params={"student_id": "20250230"}).json()
    assert filtered
    assert all(r["student_id"] == "20250230" for r in filtered)


# ─────────────────── 编辑：未提交 vs 显式 null / 单事务 ───────────────────


def test_update_list_returns_main_archive_note_and_gender(client, db):
    """列表必须回显主档 note 与规范 gender（不能每次显示空备注）。"""
    r = client.post("/api/manage/students", json={
        "name": "编辑生", "student_id": "20250252",
        "gender": "女", "seat_no": 33, "note": "初始备注",
    })
    assert r.status_code == 200, r.text

    rows = {r["student_id"]: r for r in client.get("/api/manage/students").json()}
    row = rows["20250252"]
    assert row["note"] == "初始备注"
    assert row["gender"] == "女"
    detail = client.get("/api/manage/students/20250252").json()
    assert detail["note"] == "初始备注" and detail["gender"] == "女"


def test_update_missing_fields_keep_values_explicit_null_clears(client, db):
    """只提交的字段才生效；显式 null 才清空——二者语义不同。"""
    client.post("/api/manage/students", json={
        "name": "清空生", "student_id": "20250253",
        "gender": "女", "seat_no": 34, "note": "初始备注",
    })

    # 只提交 name：gender/seat_no/note 保持原值（未提交 ≠ 清空）
    r = client.put("/api/manage/students/20250253", json={"name": "清空生二号"})
    assert r.status_code == 200, r.text
    detail = client.get("/api/manage/students/20250253").json()
    assert detail["name"] == "清空生二号"
    assert detail["gender"] == "女" and detail["seat_no"] == 34 and detail["note"] == "初始备注"

    # 显式 null：清空 gender / seat_no / note（性别/座号/备注都能明确清掉）
    r = client.put("/api/manage/students/20250253",
                   json={"gender": None, "seat_no": None, "note": None})
    assert r.status_code == 200, r.text
    db.expire_all()
    detail = client.get("/api/manage/students/20250253").json()
    assert detail["gender"] is None and detail["seat_no"] is None and detail["note"] is None
    iid = identity_of(db, "20250253")
    ident = db.query(StudentIdentity).filter(StudentIdentity.id == iid).first()
    assert ident.gender is None and ident.note is None
    roster = db.query(ClassRoster).filter(ClassRoster.student_id == "20250253").first()
    assert roster.seat_no is None and roster.gender is None


def test_update_note_only_on_identity_less_student(client, db):
    """无主档学生只编辑 note：补建主档并写入备注，绝不允许 TypeError。"""
    # 裸花名册行（无 identity、无成绩）
    db.add(ClassRoster(student_id="20250254", name="裸档生", class_num=6, grade=2))
    db.commit()

    r = client.put("/api/manage/students/20250254", json={"note": "只补备注"})
    assert r.status_code == 200, r.text
    db.expire_all()
    iid = identity_of(db, "20250254")
    assert iid is not None
    ident = db.query(StudentIdentity).filter(StudentIdentity.id == iid).first()
    assert ident.note == "只补备注"
    assert ident.display_name == "裸档生"
    alias = db.query(StudentAlias).filter(StudentAlias.student_id == "20250254").first()
    assert alias is not None and alias.identity_id == iid


def test_update_status_in_same_single_request(client, db):
    """在班状态随同一次编辑请求原子提交：基本信息 + 状态单事务落库。"""
    client.post("/api/manage/students", json={"name": "原子生", "student_id": "20250256"})

    r = client.put("/api/manage/students/20250256",
                   json={"note": "转班前备注", "status": "transferred"})
    assert r.status_code == 200, r.text
    changed = r.json()["changed"]
    assert changed["status"]["after"] == "transferred"
    assert changed["note"]["after"] == "转班前备注"

    db.expire_all()
    roster = db.query(ClassRoster).filter(ClassRoster.student_id == "20250256").first()
    assert roster.status == "transferred"
    ident = db.query(StudentIdentity).filter(
        StudentIdentity.id == identity_of(db, "20250256")
    ).first()
    assert ident.note == "转班前备注"
    # 归档学生默认列表隐藏
    assert "20250256" not in {
        r["student_id"] for r in client.get("/api/manage/students").json()
    }

    # 恢复在班同样随编辑提交
    r2 = client.put("/api/manage/students/20250256", json={"status": "active"})
    assert r2.status_code == 200
    db.expire_all()
    roster = db.query(ClassRoster).filter(ClassRoster.student_id == "20250256").first()
    assert roster.status is None


# ─────────────────── 新学年学号：既有行的身份校验 ───────────────────


def test_new_year_sid_idempotent_only_for_same_identity(client, db):
    """目标班已有同号行且属同一身份：幂等成功，绝不重复建行/建别名。"""
    client.post("/api/manage/students", json={"name": "幂等生", "student_id": "20250257"})
    r1 = client.post("/api/manage/students/20250257/new-year-sid",
                     json={"new_student_id": "20250312", "grade": 3, "class_num": 6})
    assert r1.status_code == 200, r1.text
    assert r1.json()["created"] is True
    iid = identity_of(db, "20250257")

    r2 = client.post("/api/manage/students/20250257/new-year-sid",
                     json={"new_student_id": "20250312", "grade": 3, "class_num": 6})
    assert r2.status_code == 200, r2.text
    assert r2.json()["created"] is False
    assert identity_of(db, "20250312") == iid
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "20250312").count() == 1
    assert db.query(StudentAlias).filter(StudentAlias.student_id == "20250312").count() == 1


def test_new_year_sid_attaches_unlinked_same_name_roster_row(client, db):
    """目标班已有无 alias 的同号行：教师显式输入 + 姓名一致 + 冲突检查通过
    才挂到当前身份（否则换届时同名行会掩盖占用）。"""
    db.add(ClassRoster(student_id="20250258", name="接续生", class_num=6, grade=2))
    db.add(ClassRoster(student_id="20250322", name="接续生", class_num=6, grade=3))
    db.commit()

    r = client.post("/api/manage/students/20250258/new-year-sid",
                    json={"new_student_id": "20250322", "grade": 3, "class_num": 6})
    assert r.status_code == 200, r.text
    assert r.json()["created"] is True
    iid = identity_of(db, "20250258")
    assert iid is not None
    assert identity_of(db, "20250322") == iid
    # 花名册行不重复
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "20250322").count() == 1


def test_new_year_sid_rejects_other_name_or_other_identity(client, db):
    """目标班同号行属另一姓名 / 已挂其他身份：一律拒绝、零落库。"""
    db.add(ClassRoster(student_id="20250259", name="另册生", class_num=6, grade=2))
    db.add(ClassRoster(student_id="20250332", name="别人丁", class_num=6, grade=3))
    db.commit()

    # 目标学号在目标班属于别的姓名 → 拒绝
    r1 = client.post("/api/manage/students/20250259/new-year-sid",
                     json={"new_student_id": "20250332", "grade": 3, "class_num": 6})
    assert r1.status_code == 422
    assert "别人丁" in r1.json()["detail"]
    # 目标学号已挂其他跨学年身份 → 拒绝（g1_003 属王五）
    r2 = client.post("/api/manage/students/20250259/new-year-sid",
                     json={"new_student_id": "g1_003", "grade": 1, "class_num": 6})
    assert r2.status_code == 422

    db.expire_all()
    assert identity_of(db, "20250259") is None
    row = db.query(ClassRoster).filter(ClassRoster.student_id == "20250332").first()
    assert row is not None and row.name == "别人丁"


# ─────────────────── 合并：双方无主档时也建主档保号 ───────────────────


def test_merge_without_identities_creates_identity_and_keeps_both_sids(client, db):
    """双方都没有 identity：合并后以主学号规范姓名建主档，primary 与
    duplicate 两个学号都保留为该身份的 alias（绝不丢重复学号）。"""
    db.add(ClassRoster(student_id="20250272", name="合并新丁", class_num=6, grade=2, seat_no=41))
    db.add(ClassRoster(student_id="20250273", name="合并新丁", class_num=6, grade=2, seat_no=42))
    db.add(SubjectScore(exam_id=201, student_id="20250273", name="合并新丁",
                        class_num=6, subject="语文", raw_score=100.0))
    db.commit()
    assert identity_of(db, "20250272") is None
    assert identity_of(db, "20250273") is None

    r = client.post("/api/manage/students/merge", json={
        "primary_student_id": "20250272", "duplicate_student_id": "20250273",
        "confirm": True,
    })
    assert r.status_code == 200, r.text
    iid = r.json()["identity_id"]
    assert iid is not None

    db.expire_all()
    ident = db.query(StudentIdentity).filter(StudentIdentity.id == iid).first()
    assert ident.display_name == "合并新丁"
    assert identity_of(db, "20250272") == iid
    assert identity_of(db, "20250273") == iid  # 重复学号保留为历史学号
    assert db.query(SubjectScore).filter(SubjectScore.student_id == "20250272").count() == 1
    assert db.query(SubjectScore).filter(SubjectScore.student_id == "20250273").count() == 0


# ─────────────────── 删除：确认门控与契约一致 ───────────────────


def test_delete_clean_student_allows_confirm_false(client, db):
    """干净误建学生（预览 requires_confirm=False）：confirm=false 直接删除。"""
    r = client.post("/api/manage/students", json={"name": "手滑生", "student_id": "20250262"})
    assert r.status_code == 200
    preview = client.get("/api/manage/students/20250262/delete-preview").json()
    assert preview["requires_confirm"] is False and preview["total_refs"] == 0

    r = client.request("DELETE", "/api/manage/students/20250262", json={"confirm": False})
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "20250262").count() == 0
    assert identity_of(db, "20250262") is None


def test_delete_with_cross_year_link_requires_confirm(client, db):
    """无业务数据但有其他学号别名：仍需确认（防切断主档连续性）。"""
    db.add(ClassRoster(student_id="20250264", name="双号甲", class_num=6, grade=2))
    db.add(ClassRoster(student_id="20250265", name="双号甲", class_num=6, grade=1))
    iid = ensure_identity(db, display_name="双号甲")
    link_aliases(db, iid, [("20250264", 2), ("20250265", 1)], "manual")
    db.commit()

    preview = client.get("/api/manage/students/20250264/delete-preview").json()
    assert preview["requires_confirm"] is True and preview["total_refs"] == 0
    assert preview["other_aliases_kept"] == ["20250265"]

    r = client.request("DELETE", "/api/manage/students/20250264", json={"confirm": False})
    assert r.status_code == 409
    assert r.json()["detail"]["requires_confirm"] is True
    # 什么都没删
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "20250264").count() == 1

    r = client.request("DELETE", "/api/manage/students/20250264", json={"confirm": True})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "20250264").count() == 0
    # 身份与另一学号保留
    assert identity_of(db, "20250265") == iid


# ─────────────────── 变更日志作用域 ───────────────────


def test_change_log_scoped_to_current_class(client, db):
    """日志只返回当前绑定作用域的留痕：他班与预览版无作用域旧日志不外泄；
    带 student_id 查询同样受作用域约束。"""
    from datetime import datetime

    db.add(StudentChangeLog(op_type="update", student_id="7250777",
                            grade=2, class_num=7,
                            before_summary={"name": "他班"},
                            after_summary={"name": "他班改"},
                            created_at=datetime.utcnow()))
    # 预览版本遗留：无作用域的旧日志同样不进入当前班视图
    db.add(StudentChangeLog(op_type="update", student_id="20250257",
                            created_at=datetime.utcnow()))
    db.commit()

    rows = client.get("/api/manage/change-log").json()
    assert rows
    assert all(r["grade"] == 2 and r["class_num"] == 6 for r in rows)
    assert all(r["student_id"] != "7250777" for r in rows)

    # 他班学号查不到任何日志（不越权）
    leaked = client.get("/api/manage/change-log",
                        params={"student_id": "7250777"}).json()
    assert leaked == []
    # 本班学号：只回本班作用域的留痕（无作用域旧记录被过滤）
    mine = client.get("/api/manage/change-log",
                      params={"student_id": "20250257"}).json()
    assert mine
    assert all(r["grade"] == 2 and r["class_num"] == 6 for r in mine)
