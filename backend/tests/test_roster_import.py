"""升级换届粘贴名册（仅姓名 / 学号+姓名）与旧缺陷行修复的集成测试。

覆盖：
  - 仅姓名导入：临时学号 TMP-{grade}-{class}-{name}、正确写 name/grade/class_num、幂等
  - 旧缺陷行收编：grade=2、class_num/name 为 NULL、student_id=姓名 的行修复到目标作用域，
    依赖数据（作业/特殊/档案）随迁；未粘贴姓名的缺陷行与高一数据绝不动；
    两侧身份别名冲突时整批拒绝，同一身份才收编且不留孤儿 alias
  - 正式学号补录：唯一占位行替换（精确匹配 temp_sid，绝不信 TMP- 前缀），
    作业/特殊/档案/身份别名事务性迁移，属性保留；直接建册同样统一冲突校验
  - 从成绩派生：复用同一替换事务，先建册后出分的学生只剩正式学号一行
  - 冲突拒绝与整批回滚：学号被占用、成绩库姓名不符、跨班同名
  - 作用域校验：未绑定 / 班级不一致 → 409；行校验错误 → 422
  - 学生检索/画像：roster-only 学生可见、可搜索、详情不 404；已关联身份的
    roster-only 学生并入同一人并以高二为当前代表；roster-only 仅纳入绑定班
镜像 test_rollover.py 的 env 钉扎 + TestClient 模式，自建自验。
"""

import os
import tempfile

# 必须在 import app.main 之前把 EXAM_TRACKER_DIR 钉到临时目录，使
# app.paths / app.db.models 的 engine 绑定隔离库。
_TMP = tempfile.mkdtemp(prefix="roster_import_test_")
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
    HomeworkRecord,
    SpecialRecord,
    StudentNote,
    StudentAlias,
)

TMP_ZHANG = "TMP-2-6-张三"
TMP_LI = "TMP-2-6-李四"
TMP_ZHAO = "TMP-2-6-赵六"
TMP_SUN = "TMP-2-6-孙七"
TMP_ZHOU = "TMP-2-6-周九"
TMP_WANG = "TMP-2-6-王五"


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
    """数据布局：高一 6 班（张三/李四已有成绩），高二 6 班为粘贴目标。

    - 旧缺陷行：张三/李四/王五 三条（student_id=姓名、grade=2、class_num/name 为空）
    - 张三的缺陷行上挂了一条作业记录（验证收编时随迁）
    - 20250209 已被「别人」占用（正式学号冲突用）；20250210 在成绩库属于「路人」
    - 高一正常 roster 行 g1_001（验证高一数据不被触碰）
    """
    s = SessionLocal()
    for m in (StudentNote, SpecialRecord, HomeworkRecord, StudentAlias,
              ClassRoster, SubjectScore, Exam, Teacher):
        s.query(m).delete()
    s.commit()

    s.add(Exam(id=101, name="高一期末", grade=1, semester="下",
               exam_date="2025-06", exam_type="期末"))
    s.add(Exam(id=201, name="高二开学测", grade=2, semester="上",
               exam_date="2025-09", exam_type="月考"))
    s.commit()

    # 高一 6 班成绩（同名候选来源）
    for sid, name in (("g1_001", "张三"), ("g1_002", "李四")):
        s.add(SubjectScore(exam_id=101, student_id=sid, name=name,
                           class_num=6, subject="语文", raw_score=90.0))
    # 高二成绩：20250210 属于「路人」（替换预检用）
    s.add(SubjectScore(exam_id=201, student_id="20250210", name="路人",
                       class_num=6, subject="语文", raw_score=80.0))

    # 高一正常 roster 行（不能被任何高二操作触碰）
    s.add(ClassRoster(student_id="g1_001", name="张三", grade=1, class_num=6))
    # 高二既有正常行：正式学号占用
    s.add(ClassRoster(student_id="20250209", name="别人", grade=2, class_num=6))
    # 旧缺陷行（线上 41 条的形态；name 用空串兼容测试库的 NOT NULL 约束）
    for nm in ("张三", "李四", "王五"):
        s.add(ClassRoster(student_id=nm, name="", grade=2, class_num=None))
    # 缺陷行上的孤儿作业记录（收编时必须随迁）
    s.add(HomeworkRecord(student_id="张三", date="2025-08-30",
                         subject="数学", content="练习册", remark=None))

    s.merge(Teacher(id=1, target_class_high1=6, target_class_high2=6))
    s.commit()
    s.close()


# ─────────────────────────── 作用域与行校验 ───────────────────────────


def test_scope_rejects_unbound_or_mismatched_class(client):
    r = client.post("/api/rollover/roster", json={
        "grade": 3, "class_num": 6,
        "rows": [{"name": "张三"}],
    })
    assert r.status_code == 409
    assert "尚未绑定" in r.json()["detail"]

    r2 = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 9,
        "rows": [{"name": "张三"}],
    })
    assert r2.status_code == 409
    assert "不一致" in r2.json()["detail"]


def test_row_validation_rejects_bad_rows_without_writing(client, db):
    before = db.query(ClassRoster).count()
    cases = [
        # 空姓名
        [{"student_id": "20250201", "name": "  "}],
        # 同批同名
        [{"name": "张三"}, {"name": "张三"}],
        # 同批同学号
        [{"student_id": "20250201", "name": "甲"}, {"student_id": "20250201", "name": "乙"}],
        # 学号姓名双空
        [{"student_id": None, "name": None}],
    ]
    for rows in cases:
        r = client.post("/api/rollover/roster", json={
            "grade": 2, "class_num": 6, "rows": rows,
        })
        assert r.status_code == 422, (rows, r.text)
        assert "行" in r.json()["detail"]
    assert db.query(ClassRoster).count() == before


# ─────────────────────────── 仅姓名导入 + 旧缺陷行收编 ───────────────────────────


def test_name_only_import_creates_temp_roster_and_repairs_legacy(client, db):
    r = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6,
        "rows": [{"name": "张三"}, {"name": "李四"}, {"name": "赵六"}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 3
    assert body["repaired"] == 2  # 张三、李四两条缺陷行被收编；王五未粘贴不动
    # total = 张三/李四/赵六 + 既有「别人」
    assert body["total"] == 4

    zhang = db.query(ClassRoster).filter(ClassRoster.student_id == TMP_ZHANG).one()
    assert (zhang.name, zhang.grade, zhang.class_num) == ("张三", 2, 6)

    # 缺陷行消失；未粘贴的王五缺陷行原样保留
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "张三").count() == 0
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "李四").count() == 0
    wang = db.query(ClassRoster).filter(ClassRoster.student_id == "王五").one()
    assert wang.class_num is None  # 未粘贴 → 缺陷行原样保留，等待老师下次粘贴收编

    # 缺陷行上的作业记录已随迁到临时学号
    rec = db.query(HomeworkRecord).filter(
        HomeworkRecord.student_id == TMP_ZHANG).one()
    assert rec.subject == "数学"

    # 高一正常 roster 行与成绩不受影响
    g1 = db.query(ClassRoster).filter(ClassRoster.student_id == "g1_001").one()
    assert (g1.grade, g1.class_num) == (1, 6)


def test_name_only_import_idempotent(client, db):
    r = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6,
        "rows": [{"name": "张三"}, {"name": "李四"}, {"name": "赵六"}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 0
    assert body["updated"] == 3
    assert body["total"] == 4
    # 重复导入不产生重复行
    assert db.query(ClassRoster).filter(
        ClassRoster.grade == 2, ClassRoster.class_num == 6,
        ClassRoster.name == "张三").count() == 1


# ─────────────────────────── 作业/档案立即可用 ───────────────────────────


def test_homework_roster_and_recording_work_with_temp_sid(client, db):
    assert client.patch("/api/rollover/active-grade", json={"grade": 2}).status_code == 200

    roster = client.get("/api/homework/roster").json()
    names = {row["name"]: row for row in roster}
    assert "张三" in names and "赵六" in names
    assert names["张三"]["record_count"] == 1  # 缺陷行随迁的记录

    # 标记一次排除（验证后续正式学号替换时 excluded 保留）
    r = client.put(f"/api/homework/roster/{TMP_ZHANG}/toggle-excluded")
    assert r.status_code == 200 and r.json()["excluded"] == 1

    # 智能录入按姓名在当前班解析 → 写到临时学号
    r2 = client.post("/api/homework/records", json={
        "raw_text": "李四：数学练习册", "date": "2026-09-01", "mode": "by_student",
    })
    assert r2.status_code == 200, r2.text
    rec = db.query(HomeworkRecord).filter(
        HomeworkRecord.student_id == TMP_LI,
        HomeworkRecord.date == "2026-09-01").one()
    assert rec.subject == "数学"

    # 档案也按临时学号写入
    r3 = client.post("/api/notes", json={
        "student_id": TMP_LI, "date": "2026-09-02",
        "category": "谈话", "content": "开学谈话",
    })
    assert r3.status_code == 200, r3.text


# ─────────────────────────── 预览按姓名匹配、绝不自动合并 ───────────────────────────


def test_preview_matches_roster_names_without_auto_link(client, db):
    body = client.get("/api/rollover/preview?grade=2&class_num=6").json()
    amb = {row["name"]: row for row in body["ambiguous"]}
    unmatched = {row["name"] for row in body["unmatched"]}
    # 张三/李四在高一 6 班有同名成绩 → 同名待确认（候选来自高一年级）
    assert "张三" in amb
    zhang_cands = {c["student_id"] for c in amb["张三"]["candidates"]}
    assert "g1_001" in zhang_cands
    assert "李四" in amb
    # 赵六高一没有同名 → 无成绩（待补学号）桶
    assert "赵六" in unmatched
    # 绝不自动建身份链接
    assert db.query(StudentAlias).count() == 0


# ─────────────────────────── 正式学号补录：替换 + 全量迁移 ───────────────────────────


def test_official_sid_replaces_placeholder_and_migrates_everything(client, db):
    # 先给张三建跨学年身份（教师逐人确认），替换后 alias 必须随学号迁移
    link = client.post("/api/rollover/link", json={
        "g2_student_id": TMP_ZHANG, "name": "张三",
        "g1_student_id": "g1_001", "grade": 2,
    })
    assert link.status_code == 200, link.text
    iid = link.json()["identity_id"]

    # 张三的档案挂在临时学号上
    assert client.post("/api/notes", json={
        "student_id": TMP_ZHANG, "date": "2026-09-03",
        "category": "观察", "content": "课堂观察",
    }).status_code == 200
    s = SessionLocal()
    s.add(SpecialRecord(student_id=TMP_ZHANG, date="2026-09-03", type="迟到", note=None))
    s.commit()
    s.close()

    r = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6,
        "rows": [{"student_id": "20250201", "name": "张三"}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["replaced"] == 1
    assert body["created"] == 0

    row = db.query(ClassRoster).filter(ClassRoster.student_id == "20250201").one()
    assert (row.name, row.grade, row.class_num) == ("张三", 2, 6)
    assert row.excluded == 1  # 排除标记保留
    assert db.query(ClassRoster).filter(ClassRoster.student_id == TMP_ZHANG).count() == 0

    # 作业 / 特殊 / 档案 / 身份别名全部迁移，无孤儿
    assert db.query(HomeworkRecord).filter(HomeworkRecord.student_id == TMP_ZHANG).count() == 0
    assert db.query(HomeworkRecord).filter(HomeworkRecord.student_id == "20250201").count() == 1
    assert db.query(SpecialRecord).filter(SpecialRecord.student_id == "20250201").count() == 1
    assert db.query(StudentNote).filter(StudentNote.student_id == "20250201").count() == 1
    alias = db.query(StudentAlias).filter(StudentAlias.student_id == "20250201").one()
    assert alias.identity_id == iid
    assert db.query(StudentAlias).filter(StudentAlias.student_id == TMP_ZHANG).count() == 0

    # 跨学年身份自然接续：person_ids 覆盖新旧学号
    from app.analysis.identity import person_ids
    assert person_ids(db, "20250201") == {"20250201", "g1_001"}


# ─────────────────────────── 冲突拒绝与整批回滚 ───────────────────────────


def test_official_conflicts_rejected_and_batch_rolls_back(client, db):
    # 整批：第二行占用「别人」的学号 → 422，且第一行不得落库（原子回滚）
    r = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6,
        "rows": [
            {"student_id": "20250202", "name": "李四"},
            {"student_id": "20250209", "name": "赵六"},
        ],
    })
    assert r.status_code == 422
    assert "别人" in r.json()["detail"] or "不一致" in r.json()["detail"]
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "20250202").count() == 0
    assert db.query(ClassRoster).filter(ClassRoster.student_id == TMP_LI).count() == 1

    # 学号在成绩库属于他人 → 拒绝替换
    r2 = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6,
        "rows": [{"student_id": "20250210", "name": "李四"}],
    })
    assert r2.status_code == 422
    assert "路人" in r2.json()["detail"]
    assert db.query(ClassRoster).filter(ClassRoster.student_id == TMP_LI).count() == 1

    # 占位判定精确匹配系统临时学号：只有 TMP-2-6-李四 被替换，
    # 其它 TMP- 前缀行（TMP-2-6-李四-2，非系统占位 ID）不受影响
    s = SessionLocal()
    s.add(ClassRoster(student_id=TMP_LI + "-2", name="李四", grade=2, class_num=6))
    s.commit()
    s.close()
    r3 = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6,
        "rows": [{"student_id": "20250202", "name": "李四"}],
    })
    assert r3.status_code == 200, r3.text
    assert r3.json()["replaced"] == 1
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "20250202").count() == 1
    assert db.query(ClassRoster).filter(ClassRoster.student_id == TMP_LI).count() == 0
    dup = db.query(ClassRoster).filter(ClassRoster.student_id == TMP_LI + "-2").one()
    assert dup.class_num == 6  # 非系统占位行不被触碰

    # 跨班同名：高一/他班同名行绝不合并；本班已有正式学号行时拒绝重复建册
    s = SessionLocal()
    s.add(ClassRoster(student_id="TMP-2-9-张三", name="张三", grade=2, class_num=9))
    s.commit()
    s.close()
    r4 = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6,
        "rows": [{"student_id": "20250299", "name": "张三"}],
    })
    assert r4.status_code == 422
    assert "20250201" in r4.json()["detail"]
    other = db.query(ClassRoster).filter(ClassRoster.student_id == "TMP-2-9-张三").one()
    assert other.class_num == 9  # 他班行未被触碰


# ─────────────────────────── 学生检索 / 画像 roster-only ───────────────────────────


def test_students_list_and_detail_include_roster_only(client, db):
    body = client.get("/api/students").json()
    zhao = next(row for row in body if row["name"] == "赵六")
    assert zhao["current_grade"] == 2
    assert zhao["class_num"] == 6
    assert zhao["latest_exam_name"] is None
    assert zhao["latest_main_score"] is None
    assert zhao["latest_main_rank"] is None
    # 未收编的缺陷行（王五）不进入学生列表
    assert all(row["name"] != "王五" for row in body)
    # 张三（已链接身份、正式学号已替换但高二尚无成绩）：roster 学号与高一
    # 成绩学号并入同一人，当前代表是高二学号、current_grade=2，高一进 history
    zhang = next(row for row in body if row["name"] == "张三")
    assert zhang["student_id"] == "20250201"
    assert zhang["current_grade"] == 2
    assert zhang["class_num"] == 6
    assert {h["student_id"] for h in zhang["history"]} == {"g1_001"}

    # 按姓名搜索
    searched = client.get("/api/students", params={"search": "赵六"}).json()
    assert {row["name"] for row in searched} == {"赵六"}

    # 画像：roster-only 学生不 404，姓名/班级来自花名册，趋势为空
    detail = client.get(f"/api/students/{TMP_ZHAO}")
    assert detail.status_code == 200, detail.text
    profile = detail.json()
    assert profile["name"] == "赵六"
    assert profile["class_num"] == 6
    assert profile["main_total_trend"] == []

    # 正式学号学生的画像姓名正确、跨学年链接未错挂到他人；年级集合合并
    # 高一成绩年级与高二花名册年级，顶层班级取最高年级（高二）作用域
    linked = client.get("/api/students/20250201").json()
    assert linked["name"] == "张三"
    assert linked["grades"] == [1, 2]
    assert linked["has_cross_year"] is True
    assert linked["class_num"] == 6
    assert linked["class_by_grade"] == {"1": 6, "2": 6}


def test_class_num_regression_written_from_target_scope(client, db):
    """行级 class_num=null（Pydantic model_dump）绝不能写出 NULL 班级行。"""
    r = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6,
        "rows": [{"student_id": "20250205", "name": "钱七", "class_num": None}],
    })
    assert r.status_code == 200, r.text
    row = db.query(ClassRoster).filter(ClassRoster.student_id == "20250205").one()
    assert row.class_num == 6
    assert row.grade == 2
    # 全库不允许再出现 class_num 为 NULL 的名册行（王五缺陷行除外，未被粘贴）
    broken = db.query(ClassRoster).filter(ClassRoster.class_num.is_(None)).all()
    assert [b.student_id for b in broken] == ["王五"]


# ─────────────── P1-1 已关联身份的 roster-only 学生仍显示为高二 ───────────────


def test_linked_roster_only_student_shows_as_current_class(client, db):
    """先姓名建册 → 逐人关联高一学号 → 学生列表/画像仍是高二当前班视角。

    高一 5 班（他班生源）的孙七进入本班：roster 学号与高一成绩学号并入
    同一人，高二 roster-only 学号成为当前代表，高一学号进 history。
    """
    s = SessionLocal()
    s.add(SubjectScore(exam_id=101, student_id="g1_007", name="孙七",
                       class_num=5, subject="语文", raw_score=85.0))
    s.add(TotalScore(exam_id=101, student_id="g1_007",
                     total_type="主三门", total_score=242.0))
    s.commit()
    s.close()

    r = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6, "rows": [{"name": "孙七"}],
    })
    assert r.status_code == 200, r.text

    link = client.post("/api/rollover/link", json={
        "g2_student_id": TMP_SUN, "name": "孙七",
        "g1_student_id": "g1_007", "grade": 2,
    })
    assert link.status_code == 200, link.text

    body = client.get("/api/students").json()
    sun = next(row for row in body if row["name"] == "孙七")
    assert sun["student_id"] == TMP_SUN  # 高二 roster-only 学号是当前代表
    assert sun["current_grade"] == 2
    assert sun["class_num"] == 6
    assert sun["latest_main_score"] is None  # 高二尚无成绩 → 「—」而非高一分数
    assert sun["history"] == [
        {"student_id": "g1_007", "grade": 1, "class_num": 5},
    ]

    # 按姓名 / 临时学号都能搜到
    assert {row["student_id"] for row in client.get(
        "/api/students", params={"search": "孙七"}).json()} == {TMP_SUN}
    assert {row["student_id"] for row in client.get(
        "/api/students", params={"search": TMP_SUN}).json()} == {TMP_SUN}

    detail = client.get(f"/api/students/{TMP_SUN}").json()
    assert detail["name"] == "孙七"
    assert detail["grades"] == [1, 2]           # 合法花名册年级并入
    assert detail["has_cross_year"] is True
    assert detail["class_num"] == 6             # 顶层取最高年级（高二）作用域
    assert detail["class_by_grade"] == {"1": 5, "2": 6}


# ─────────────── P1-2 从成绩派生复用占位替换，不产生第二条 ───────────────


def test_from_scores_replaces_placeholder_and_migrates(client, db):
    """姓名占位已有作业 → 高二出分 → 从成绩派生 → 只剩正式学号一行，记录随迁。"""
    r = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6, "rows": [{"name": "周九"}],
    })
    assert r.status_code == 200, r.text
    assert client.post("/api/homework/records", json={
        "raw_text": "周九：物理练习册", "date": "2026-09-05", "mode": "by_student",
    }).status_code == 200

    s = SessionLocal()
    s.add(SubjectScore(exam_id=201, student_id="20250230", name="周九",
                       class_num=6, subject="物理", raw_score=70.0))
    s.commit()
    s.close()

    r2 = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6, "from_scores": True,
    })
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["replaced"] == 1   # 周九占位替换
    assert body["created"] == 1    # 路人（20250210）从成绩新入册

    rows9 = db.query(ClassRoster).filter(ClassRoster.name == "周九").all()
    assert [x.student_id for x in rows9] == ["20250230"]  # 绝无第二条重复行
    assert db.query(ClassRoster).filter(ClassRoster.student_id == TMP_ZHOU).count() == 0
    assert db.query(HomeworkRecord).filter(
        HomeworkRecord.student_id == "20250230").count() == 1
    assert db.query(HomeworkRecord).filter(
        HomeworkRecord.student_id == TMP_ZHOU).count() == 0


# ─────────────── P1-3 正式学号直接建册也统一冲突校验 ───────────────


def test_official_direct_create_validates_conflicts(client, db):
    from app.analysis.identity import ensure_identity, link_aliases, import_crosswalk

    # 学号已有「别人」的成绩 → 拒绝整批回滚（第一行也不得落库）
    r = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6,
        "rows": [
            {"student_id": "20250260", "name": "郑一"},
            {"student_id": "20250210", "name": "郑二"},
        ],
    })
    assert r.status_code == 422
    assert "路人" in r.json()["detail"]
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "20250260").count() == 0

    # 学号已挂别人的跨学年身份（alias + display_name 证据）→ 拒绝
    iid_wang = ensure_identity(db, display_name="王五")
    link_aliases(db, iid_wang, [("20250261", 2)], "manual")
    r2 = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6,
        "rows": [{"student_id": "20250261", "name": "郑一"}],
    })
    assert r2.status_code == 422
    assert "王五" in r2.json()["detail"]

    # 同名且目标作用域一致 → 安全接续：crosswalk 过的正式学号直接建册成功
    s = SessionLocal()
    s.add(SubjectScore(exam_id=101, student_id="g1_003", name="郑一",
                       class_num=6, subject="数学", raw_score=88.0))
    s.commit()
    s.close()
    assert import_crosswalk(
        db, [{"g1_sid": "g1_003", "g2_sid": "20250250", "name": "郑一"}], 2
    )["linked"] == 1
    r3 = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6,
        "rows": [{"student_id": "20250250", "name": "郑一"}],
    })
    assert r3.status_code == 200, r3.text
    row = db.query(ClassRoster).filter(ClassRoster.student_id == "20250250").one()
    assert (row.name, row.grade, row.class_num) == ("郑一", 2, 6)


# ─────────────── P1-4 旧缺陷行收编的身份别名冲突 ───────────────


def test_absorb_legacy_row_checks_identity_conflict(client, db):
    from app.analysis.identity import ensure_identity, link_aliases, unlink_alias

    # 前置：王五缺陷行挂 identity A；本班 TMP 王五行挂 identity B
    iid_a = ensure_identity(db, display_name="王五")
    link_aliases(db, iid_a, [("王五", 2)], "manual")
    iid_b = ensure_identity(db, display_name="另一个人")
    s = SessionLocal()
    s.add(ClassRoster(student_id=TMP_WANG, name="王五", grade=2, class_num=6))
    s.add(HomeworkRecord(student_id="王五", date="2026-09-06",
                         subject="语文", content="练习册", remark=None))
    s.commit()
    s.close()
    link_aliases(db, iid_b, [(TMP_WANG, 2)], "manual")

    # 两侧 alias 指向不同 identity → 整批拒绝：不迁记录、不删旧行
    r = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6, "rows": [{"name": "王五"}],
    })
    assert r.status_code == 422
    assert "身份" in r.json()["detail"]
    broken = db.query(ClassRoster).filter(ClassRoster.student_id == "王五").one()
    assert broken.class_num is None  # 缺陷行原样保留
    assert db.query(HomeworkRecord).filter(
        HomeworkRecord.student_id == "王五").count() == 1
    assert db.query(HomeworkRecord).filter(
        HomeworkRecord.student_id == TMP_WANG).count() == 0

    # 同一 identity → 安全收编：业务记录随迁，缺陷学号 alias 删除不留孤儿
    unlink_alias(db, TMP_WANG)
    link_aliases(db, iid_a, [(TMP_WANG, 2)], "manual")
    r2 = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6, "rows": [{"name": "王五"}],
    })
    assert r2.status_code == 200, r2.text
    assert r2.json()["repaired"] == 1
    assert db.query(ClassRoster).filter(ClassRoster.student_id == "王五").count() == 0
    assert db.query(HomeworkRecord).filter(
        HomeworkRecord.student_id == TMP_WANG).count() == 1
    assert {a.student_id for a in db.query(StudentAlias).filter(
        StudentAlias.identity_id == iid_a).all()} == {TMP_WANG}


# ─────────────── P1-5 真实 TMP- 前缀学号绝不被当占位行 ───────────────


def test_real_tmp_prefixed_sid_is_not_placeholder(client, db):
    s = SessionLocal()
    s.add(ClassRoster(student_id="TMP-REAL-777", name="吴十", grade=2, class_num=6))
    s.add(HomeworkRecord(student_id="TMP-REAL-777", date="2026-09-07",
                         subject="化学", content="练习册", remark=None))
    s.commit()
    s.close()

    # 仅姓名粘贴：幂等复用既有唯一同名行，不新建第二条、不动其数据
    r = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6, "rows": [{"name": "吴十"}],
    })
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 0 and r.json()["updated"] == 1
    assert db.query(ClassRoster).filter(ClassRoster.name == "吴十").count() == 1

    # 正式学号粘贴：系统占位（TMP-2-6-吴十）不存在 → 拒绝重复建册，数据不误迁
    r2 = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6,
        "rows": [{"student_id": "20250240", "name": "吴十"}],
    })
    assert r2.status_code == 422
    assert "TMP-REAL-777" in r2.json()["detail"]
    assert db.query(HomeworkRecord).filter(
        HomeworkRecord.student_id == "TMP-REAL-777").count() == 1
    assert db.query(HomeworkRecord).filter(
        HomeworkRecord.student_id == "20250240").count() == 0
    assert db.query(ClassRoster).filter(
        ClassRoster.student_id == "20250240").count() == 0


# ─────────────── P1-6 roster-only 作用域：只纳入教师绑定班 ───────────────


def test_roster_only_rows_scoped_to_bound_class(client, db):
    # 他班（高二 9 班）roster-only 行不出现在学生列表
    body = client.get("/api/students").json()
    assert all(row["student_id"] != "TMP-2-9-张三" for row in body)

    # 他班 roster-only 详情 → 404（不越权展示）
    assert client.get("/api/students/TMP-2-9-张三").status_code == 404

    # 本班 roster-only 详情仍 200；有高一成绩的已关联学生（跨学年画像）不受影响
    assert client.get(f"/api/students/{TMP_ZHAO}").status_code == 200
    assert client.get(f"/api/students/{TMP_SUN}").status_code == 200


# ─────────────────────────── 改名后的占位行替换 ───────────────────────────


def test_renamed_placeholder_replaced_via_tmp_prefix_fallback(client, db):
    """建册后改过名的学生：占位行学号里冻结着建册时的旧名（改名只更新
    name 字段、不动学号），再粘「正式号,新名」时精确匹配必然落空——
    按「本班同名 + TMP- 前缀且唯一」回退识别占位行并事务性替换。
    （线上真实卡点：当初粘贴打成「沈知远」，改名「沈知远」后无法补号。）"""
    # 当初粘贴「沈知远」建占位行
    r = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6,
        "rows": [{"name": "沈知远"}],
    })
    assert r.status_code == 200, r.text

    # 老师后来改名（与 update_student 一致：只改 name、学号不变）
    s = SessionLocal()
    row = (
        s.query(ClassRoster)
        .filter(ClassRoster.student_id == "TMP-2-6-沈知远")
        .one()
    )
    row.name = "沈知远"
    s.commit()
    s.close()

    # 粘贴「正式学号,新名字」→ 必须命中占位行并替换
    r = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6,
        "rows": [{"student_id": "20250301", "name": "沈知远"}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["replaced"] == 1, body
    assert body["created"] == 0

    s = SessionLocal()
    assert (
        s.query(ClassRoster)
        .filter(ClassRoster.student_id == "TMP-2-6-沈知远")
        .count()
        == 0
    )
    row = (
        s.query(ClassRoster).filter(ClassRoster.student_id == "20250301").one()
    )
    assert (row.name, row.grade, row.class_num) == ("沈知远", 2, 6)
    s.close()


def test_renamed_placeholder_fallback_never_touches_real_sid(client, db):
    """回退只认 TMP- 前缀：本班同名的**正式号**行绝不被回退替换
    （多行歧义时保守拒绝，维持原有防重复建册语义）。"""
    # 本班已有同名正式号行（非 TMP）
    r = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6,
        "rows": [{"student_id": "20250302", "name": "钱多多"}],
    })
    assert r.status_code == 200, r.text

    # 再粘同名的另一个正式号 → 无占位行可补，维持拒绝（防重复建册）
    r = client.post("/api/rollover/roster", json={
        "grade": 2, "class_num": 6,
        "rows": [{"student_id": "20250303", "name": "钱多多"}],
    })
    assert r.status_code == 422, r.text
    assert "未找到待补学号占位行" in r.text
