"""03 期「按人聚合」读端集成测试（test_student_union）。

验证 Phase-3 改造：学生画像 / 列表 / 档案 / 作业看板在跨学年身份（
StudentAlias 把 g1_101 与 g2_201 链为同一人）下的行为。

数据布局（自建自验，隔离库）：
  - Exam 1001 = 高一（grade=1），班号 6
  - Exam 2001 = 高二（grade=2），班号 3
  - 陈一 同一人两个学号：g1_101（高一班 6）/ g2_201（高二班 3）—— 用
    identity.link_aliases 挂到同一 StudentIdentity。
  - G1 同班同学 g1_102（用于班级排名计算）。
  - G2 同班同学 g2_202（用于班级排名计算）。
  - ImportedHistory：该 identity 一行主三门 total（grade=3 旧学期）+ 一行 subject。
  - G1 / G2 作业花名册各一份；active_grade=2。

用例覆盖：
  1. GET /api/students/{g2_201}：main_total_trend 跨 grade 1+2，imported 字段齐，
     class_by_grade {1:6,2:3}，class_num==3（最高年级），identity.aliases 双号，
     imported_history 点 imported==True，class_rank 每场考试存在。
  2. GET /api/students/{g1_101}（旧号）返回同一 union（person_ids 对称）。
  3. GET /api/students（复数）：链为一人只一行，代表是高二 sid 班 3，history 列高一 sid。
  4. class_rank 按各场考试的 class_num 同班同窗分别计算（高一班 6 内排名、高二班 3 内排名）。
  5. 档案 union：挂在 g1_101 的 note 经 GET /api/notes/{g2_201} 可见。
  6. 作业看板（kpi/rankings）按 active_grade=2 单年级收口，只统计高二名册。
"""

import os
import tempfile

# 必须在 import app.main 之前把 EXAM_TRACKER_DIR 钉到临时目录，使
# app.paths / app.db.models 的 engine 绑定隔离库（镜像 test_rollover.py）。
_TMP = tempfile.mkdtemp(prefix="student_union_test_")
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
    HomeworkSemester,
    HomeworkSetting,
    ImportedHistory,
    StudentNote,
    StudentIdentity,
)


# ────────────────────────────── fixtures ──────────────────────────────


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


# ────────────────────────────── seed helpers ──────────────────────────────


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


def _seed_subject(
    exam_id,
    student_id,
    name,
    class_num,
    subject="语文",
    raw=80.0,
    grade_score=None,
    grade_percentile=None,
):
    s = SessionLocal()
    s.add(
        SubjectScore(
            exam_id=exam_id,
            student_id=student_id,
            name=name,
            class_num=class_num,
            subject=subject,
            raw_score=raw,
            grade_score=grade_score,
            grade_percentile=grade_percentile,
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
    """建两场考试 + 跨学年同一人 + 同班同学 + 历史导入行。"""
    s = SessionLocal()
    # 清空隔离库残留（多重跑安全）
    for m in (
        StudentNote,
        HomeworkRecord,
        HomeworkSetting,
        ClassRoster,
        ImportedHistory,
        SubjectScore,
        TotalScore,
        Exam,
        Teacher,
        StudentIdentity,
    ):
        s.query(m).delete()
    s.commit()
    s.close()

    _seed_exam(1001, "高一入学测", 1, "2024-09")
    _seed_exam(2001, "高二开学测", 2, "2025-09")

    # ── 高一（grade=1）班 6 ──
    _seed_subject(1001, "g1_101", "陈一", 6, raw=85.0)
    _seed_subject(1001, "g1_102", "李四", 6, raw=70.0)  # 同班同学，排名基准
    _seed_total(1001, "g1_101", "主三门", 250.0, 8)
    _seed_total(1001, "g1_102", "主三门", 220.0, 15)
    _seed_total(1001, "g1_101", "九门", 660.0, 6)

    # ── 高二（grade=2）班 3 ──
    _seed_subject(2001, "g2_201", "陈一", 3, raw=88.0)
    _seed_subject(2001, "g2_202", "王五", 3, raw=90.0)  # 同班同学，排名基准
    _seed_subject(
        2001,
        "g2_201",
        "陈一",
        3,
        subject="物理",
        raw=78.0,
        grade_score=64.0,
    )
    _seed_total(2001, "g2_201", "主三门", 270.0, 5)
    _seed_total(2001, "g2_202", "主三门", 280.0, 3)

    # 班主任绑定班号（高一 6 / 高二 3）
    s = SessionLocal()
    s.merge(Teacher(id=1, target_class_high1=6, target_class_high2=3))
    s.commit()
    s.close()

    # ── 把 g1_101 与 g2_201 链为同一人 ──
    from app.analysis.identity import ensure_identity, link_aliases

    s = SessionLocal()
    iid = ensure_identity(s, display_name="陈一")
    link_aliases(s, iid, [("g1_101", 1), ("g2_201", 2)], "manual")
    s.close()

    # ── ImportedHistory：旧学期（grade=3 模拟初中）主三门 total + 一科 subject ──
    s = SessionLocal()
    s.add(
        ImportedHistory(
            identity_id=iid,
            grade=0,  # 早于高一，展示用
            exam_label="初中期末",
            exam_seq=1,
            kind="total",
            total_type="主三门",
            raw_score=300.0,
            xueji_rank=20,
        )
    )
    s.add(
        ImportedHistory(
            identity_id=iid,
            grade=0,
            exam_label="初中期末",
            exam_seq=1,
            kind="subject",
            subject="数学",
            raw_score=110.0,
            grade_score=67.0,
        )
    )
    s.commit()
    s.close()


# ────────────────────────────── GET /students/{g2_201} ──────────────────────────────


def test_get_student_union_from_g2_id(client):
    """高二学号打开画像：main_total_trend 跨 grade 1+2，imported 齐全，class_by_grade 正确。"""
    r = client.get("/api/students/g2_201")
    assert r.status_code == 200, r.text
    body = r.json()

    # 跨年级
    assert body["has_cross_year"] is True
    assert sorted(body["grades"]) == [1, 2]

    # 标量班级取最高年级（高二班 3）
    assert body["class_num"] == 3
    # 班级按年级分别取众数（JSON 序列化后 key 为字符串）
    assert body["class_by_grade"] == {"1": 6, "2": 3}

    main_trend = body["main_total_trend"]
    # 含高一 + 高二 + 导入 = 至少 3 个点
    grades_in_trend = [p["grade"] for p in main_trend]
    assert 1 in grades_in_trend and 2 in grades_in_trend
    # 真实点 imported==False，导入点 imported==True
    real_pts = [p for p in main_trend if p.get("imported") is False]
    imp_pts = [p for p in main_trend if p.get("imported") is True]
    assert len(real_pts) >= 2  # 高一 + 高二
    assert len(imp_pts) == 1   # 一条主三门历史导入行
    # 每个点都有 imported 字段
    assert all("imported" in p for p in main_trend)

    # identity + aliases
    ident = body["identity"]
    assert ident["id"] is not None
    alias_sids = {a["student_id"] for a in ident["aliases"]}
    assert {"g1_101", "g2_201"} <= alias_sids
    # aliases 带派生 class_num
    alias_by_sid = {a["student_id"]: a for a in ident["aliases"]}
    assert alias_by_sid["g1_101"]["class_num"] == 6
    assert alias_by_sid["g2_201"]["class_num"] == 3


def test_get_student_imported_history_subject_point(client):
    """ImportedHistory 的 subject 行进 subject_trend，imported==True。"""
    body = client.get("/api/students/g2_201").json()
    subj_imp = [p for p in body["subject_trend"] if p.get("imported") is True]
    assert any(p.get("subject") == "数学" and p.get("raw_score") == 110.0 for p in subj_imp)


def test_student_profile_returns_stage_specific_metrics(client):
    """画像返回高一九门趋势，并保留高二选科与手工导入历史的等级分。"""
    body = client.get("/api/students/g2_201").json()

    assert any(
        point["exam_id"] == 1001 and point["total_score"] == 660.0
        for point in body["nine_trend"]
    )

    physics = [
        point
        for point in body["subject_trend"]
        if point["subject"] == "物理" and point["exam_id"] == 2001
    ]
    assert physics and physics[0]["grade_score"] == 64.0

    imported_math = [
        point
        for point in body["subject_trend"]
        if point.get("imported") is True and point["subject"] == "数学"
    ]
    assert imported_math and imported_math[0]["grade_score"] == 67.0


def test_get_student_class_rank_per_exam(client):
    """每场考试 class_rank 按本班同窗分别计算，存在且非 None。"""
    body = client.get("/api/students/g2_201").json()
    real_pts = [p for p in body["main_total_trend"] if p.get("imported") is False]
    # 按 grade 取高一/高二两个真实点
    g1_pt = next(p for p in real_pts if p["grade"] == 1)
    g2_pt = next(p for p in real_pts if p["grade"] == 2)
    # 陈一 g1_101 主三门 250 > 李四 220 -> 班内第 1
    assert g1_pt["class_rank"] == 1
    # 陈一 g2_201 主三门 270 < 王五 280 -> 班内第 2
    assert g2_pt["class_rank"] == 2


# ────────────────────────────── GET /students/{g1_101}（旧号）──────────────────────────────


def test_get_student_union_symmetric_from_old_id(client):
    """用高一旧学号 g1_101 查询，应返回同一人的全部数据（person_ids 对称）。"""
    r = client.get("/api/students/g1_101")
    assert r.status_code == 200, r.text
    body = r.json()
    # 同一人：class_by_grade / 趋势点数应一致（JSON key 为字符串）
    assert body["class_by_grade"] == {"1": 6, "2": 3}
    assert body["class_num"] == 3  # 仍取最高年级
    grades = [p["grade"] for p in body["main_total_trend"]]
    assert 1 in grades and 2 in grades
    alias_sids = {a["student_id"] for a in body["identity"]["aliases"]}
    assert {"g1_101", "g2_201"} <= alias_sids


# ────────────────────────────── GET /students（复数）──────────────────────────────


def test_list_students_dedups_linked_person(client):
    """链为同一人的两个学号在列表里只占一行，代表是高二年学号。"""
    r = client.get("/api/students")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert isinstance(rows, list)
    # 所有 student_id 不应同时出现 g1_101 与 g2_201（去重）
    sids = {row["student_id"] for row in rows}
    assert not ({"g1_101", "g2_201"} <= sids), "链为同一人应去重为一行"

    # 找到代表那一行
    rep = next(row for row in rows if row["student_id"] == "g2_201")
    assert rep["current_grade"] == 2
    assert rep["class_num"] == 3
    # history 列出高一旧学号
    hist_sids = {h["student_id"] for h in rep["history"]}
    assert "g1_101" in hist_sids


# ────────────────────────────── 档案 union ──────────────────────────────


def test_notes_union_across_linked_ids(client):
    """挂在 g1_101（高一学号）的档案，经高二学号 g2_201 查询可见（person union）。"""
    # 用 g1_101 建一条档案
    r = client.post(
        "/api/notes",
        json={
            "student_id": "g1_101",
            "category": "谈话",
            "content": "高一档案：跨学年可见性测试",
        },
    )
    assert r.status_code == 200, r.text
    nid = r.json()["id"]

    try:
        # 经 g2_201 查询应能看到
        rows = client.get("/api/notes/g2_201").json()
        assert any(n["id"] == nid for n in rows), "高二学号应能看到高一学号的档案"
        # 经 g1_101 查询也应看到
        rows2 = client.get("/api/notes/g1_101").json()
        assert any(n["id"] == nid for n in rows2)
    finally:
        client.delete(f"/api/notes/{nid}")


# ────────────────────────────── 作业看板单年级收口 ──────────────────────────────


def _seed_hw_rosters_and_records():
    """建高一/高二两份花名册 + 两边各一条缺交记录，active_grade 设为 2。"""
    s = SessionLocal()
    # 花名册：高一班 6 / 高二班 3
    s.merge(ClassRoster(student_id="g1_101", name="陈一", class_num=6, grade=1, excluded=0))
    s.merge(ClassRoster(student_id="g1_102", name="李四", class_num=6, grade=1, excluded=0))
    s.merge(ClassRoster(student_id="g2_201", name="陈一", class_num=3, grade=2, excluded=0))
    s.merge(ClassRoster(student_id="g2_202", name="王五", class_num=3, grade=2, excluded=0))
    # 显式种入当前学期，避免测试依赖 get_semester 的按日推算窗口；
    # 缺交记录日期必须落在该学期区间内，否则看板按学期过滤会排除它们
    s.merge(HomeworkSemester(name="测试学期", start_date="2026-02-17", end_date="2026-07-04", is_current=1))
    s.add(HomeworkRecord(student_id="g1_101", date="2026-03-10", subject="数学", content="缺"))
    s.add(HomeworkRecord(student_id="g2_201", date="2026-03-10", subject="英语", content="缺"))
    s.add(HomeworkRecord(student_id="g2_202", date="2026-03-10", subject="英语", content="缺"))
    # active_grade=2
    s.merge(HomeworkSetting(key="active_grade", value="2"))
    s.commit()
    s.close()


def test_homework_boards_single_year_active_grade(client):
    """看板（kpi/rankings）按 active_grade=2 收口，只统计高二名册缺交（2 条），不含高一。"""
    _seed_hw_rosters_and_records()

    kpi = client.get("/api/homework/kpi").json()
    # 高二有 2 条缺交（g2_201 + g2_202），高一 1 条不应计入
    assert kpi["total_misses"] == 2

    rankings = client.get("/api/homework/rankings").json()
    # 排行里的姓名应只含高二学生
    names = set(rankings["names"])
    assert {"陈一", "王五"} <= names  # 高二名册
    assert "李四" not in names          # 高一名册，不应出现
