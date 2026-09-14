"""Phase-4 chat 工具「按人聚合」集成测试（test_chat_tools_union）。

验证 chat/tools.py 里以学生为中心的工具在跨学年身份（StudentAlias 把 g1_101
与 g2_201 链为同一人）下的行为，以及单年级班级工具不被影响。

数据布局（自建自验，隔离库，镜像 test_student_union.py）：
  - Exam 1001 = 高一（grade=1），班号 6
  - Exam 2001 = 高二（grade=2），班号 3
  - 陈一 同一人两个学号：g1_101（高一班 6）/ g2_201（高二班 3）—— 用
    identity.link_aliases 挂到同一 StudentIdentity。
  - G1 同班同学 g1_102（用于班级工具仍按当年班号收口的验证）。

用例覆盖：
  1. student_lookup(name=陈一)：链为一人只返回一行，all_student_ids 含两号，
     person_id 非 None。
  2. student_lookup(student_id=g2_201)：按 person 取并集仍命中两号。
  3. student_trend(g2_201)：主三门 ranks 跨 grade 1+2 两个点。
  4. student_learning_profile(g2_201)：available_exams 跨两个年级，
     student.all_student_ids 含两号，person_id 非 None。
  5. student_notes：挂在 g1_101 的 note 经 g2_201 查询可见（person union）。
  6. student_identity_lookup(name=陈一)：返回 identity_id + aliases 含两号；
     未知姓名返回 {"error": ...}（dict，非异常）。
  7. 单年级班级工具不受影响：focus_list(exam_id=1001) 仍按当年 exam_id 收口，
     含 g1_101 与 g1_102 两个高一学号（不会把高二的 g2_201 算进来）。
"""

import os
import tempfile

# 必须在 import app.* 之前把 EXAM_TRACKER_DIR 钉到临时目录，使
# app.paths / app.db.models 的 engine 绑定隔离库（镜像 test_student_union.py）。
_TMP = tempfile.mkdtemp(prefix="chat_tools_union_test_")
os.environ["EXAM_TRACKER_DIR"] = os.path.join(_TMP, "examdata")
os.makedirs(os.environ["EXAM_TRACKER_DIR"], exist_ok=True)

import pytest

from app.chat import tools as chat_tools
from app.db.models import (
    SessionLocal,
    Exam,
    SubjectScore,
    TotalScore,
    Teacher,
    ClassRoster,
    HomeworkSetting,
    StudentNote,
    StudentIdentity,
    StudentAlias,
)


# ────────────────────────────── fixtures ──────────────────────────────


@pytest.fixture(scope="module", autouse=True)
def _seed_once():
    """模块内 seed 一次；所有用例共享同一隔离库。

    teardown 必须清掉本模块建的 StudentAlias / StudentIdentity，否则会污染
    test_rollover（其 seed() 不清这两张表，残留 alias 会让 g2_201 落到
    inherited 而非 ambiguous）。模块级 fixture 的 finalizer 在本模块最后一个
    用例结束后、下一个模块开始前执行，正好在 test_rollover 之前。
    """
    seed()
    yield
    s = SessionLocal()
    try:
        s.query(StudentAlias).delete()
        s.query(StudentIdentity).delete()
        s.query(StudentNote).delete()
        s.query(ClassRoster).delete()
        s.query(SubjectScore).delete()
        s.query(TotalScore).delete()
        s.query(Exam).delete()
        s.query(Teacher).delete()
        s.commit()
    finally:
        s.close()


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


def _seed_subject(exam_id, student_id, name, class_num, subject="语文",
                  raw=80.0, grade_percentile=None):
    s = SessionLocal()
    s.add(
        SubjectScore(
            exam_id=exam_id,
            student_id=student_id,
            name=name,
            class_num=class_num,
            subject=subject,
            raw_score=raw,
            grade_percentile=grade_percentile,
        )
    )
    s.commit()
    s.close()


def _seed_total(exam_id, student_id, total_type="主三门", total_score=240.0,
                xueji_rank=10, grade_percentile=None):
    s = SessionLocal()
    s.add(
        TotalScore(
            exam_id=exam_id,
            student_id=student_id,
            total_type=total_type,
            total_score=total_score,
            xueji_rank=xueji_rank,
            grade_percentile=grade_percentile,
        )
    )
    s.commit()
    s.close()


def seed():
    """建两场考试 + 跨学年同一人 + 同班同学 + 一条高一学号的档案。"""
    s = SessionLocal()
    # 清空隔离库残留（多重跑安全）
    for m in (
        StudentNote,
        HomeworkSetting,
        ClassRoster,
        StudentAlias,
        StudentIdentity,
        SubjectScore,
        TotalScore,
        Exam,
        Teacher,
    ):
        s.query(m).delete()
    s.commit()
    s.close()

    _seed_exam(1001, "高一入学测", 1, "2024-09")
    _seed_exam(2001, "高二开学测", 2, "2025-09")

    # ── 高一（grade=1）班 6 ──
    # total.grade_percentile=0.10，subject.grade_percentile=0.90 -> 差 0.80 >= 0.20，
    # 触发 focus_list 的「严重偏科」，使学生稳定进入关注名单（与段位阈值无关）。
    _seed_subject(1001, "g1_101", "陈一", 6, raw=85.0, grade_percentile=0.90)
    _seed_subject(1001, "g1_102", "李四", 6, raw=70.0, grade_percentile=0.90)  # 同班同学
    _seed_total(1001, "g1_101", "主三门", 250.0, 8, grade_percentile=0.10)
    _seed_total(1001, "g1_102", "主三门", 220.0, 15, grade_percentile=0.10)

    # ── 高二（grade=2）班 3 ──
    _seed_subject(2001, "g2_201", "陈一", 3, raw=88.0, grade_percentile=0.50)
    _seed_total(2001, "g2_201", "主三门", 270.0, 5, grade_percentile=0.20)

    # ── 作业花名册（student_notes 按 ClassRoster 解析学号）──
    s = SessionLocal()
    s.merge(ClassRoster(student_id="g1_101", name="陈一", class_num=6, grade=1, excluded=0))
    s.merge(ClassRoster(student_id="g1_102", name="李四", class_num=6, grade=1, excluded=0))
    s.merge(ClassRoster(student_id="g2_201", name="陈一", class_num=3, grade=2, excluded=0))
    s.commit()
    s.close()

    # 把 g1_101 与 g2_201 链为同一人
    from app.analysis.identity import ensure_identity, link_aliases

    s = SessionLocal()
    iid = ensure_identity(s, display_name="陈一")
    link_aliases(s, iid, [("g1_101", 1), ("g2_201", 2)], "manual")
    s.close()


# ────────────────────────────── student_lookup ──────────────────────────────


def test_student_lookup_merges_linked_ids_by_name():
    """链为同一人的两个学号按姓名查只返回一行，person_id 非 None，all_student_ids 含两号。"""
    rows = chat_tools.student_lookup(name="陈一")
    assert isinstance(rows, list)
    assert len(rows) == 1, "链为同一人应去重为一行"
    row = rows[0]
    assert row["name"] == "陈一"
    assert row["person_id"] is not None
    assert {"g1_101", "g2_201"} <= set(row["all_student_ids"])


def test_student_lookup_by_either_id_returns_same_person():
    """用任一学号查都应解析到同一人并集。"""
    for sid in ("g1_101", "g2_201"):
        rows = chat_tools.student_lookup(student_id=sid)
        assert len(rows) == 1, f"{sid} 应解析到唯一一人"
        assert {"g1_101", "g2_201"} <= set(rows[0]["all_student_ids"])
        assert rows[0]["person_id"] is not None


# ────────────────────────────── student_trend ──────────────────────────────


def test_student_trend_spans_both_grades():
    """跨学年调用主三门 trend：ranks 含高一 1001 + 高二 2001 两个点。"""
    result = chat_tools.student_trend(student_id="g2_201", total_type="主三门")
    assert "error" not in result
    ranks = result.get("ranks")
    assert isinstance(ranks, list)
    exam_ids = {r[0] for r in ranks}
    assert {1001, 2001} <= exam_ids, "趋势应跨高一/高二两个年级"


# ────────────────────────────── student_learning_profile ──────────────────────────────


def test_student_learning_profile_unions_both_grades():
    """学习画像跨年级合并：available_exams 含两个年级，all_student_ids 含两号。"""
    result = chat_tools.student_learning_profile(student_id="g2_201")
    assert "error" not in result
    student = result["student"]
    assert {"g1_101", "g2_201"} <= set(student["all_student_ids"])
    assert student["person_id"] is not None

    exam_grades = {ex["grade"] for ex in result["available_exams"]}
    assert {1, 2} <= exam_grades, "available_exams 应跨高一/高二"

    # 主三门趋势也应跨两个年级
    trend_grades = {pt["exam"]["grade"] for pt in result["main_total_trend"]}
    assert {1, 2} <= trend_grades


def test_student_learning_profile_symmetric_from_old_id():
    """用高一旧学号查画像，同样合并到同一人。"""
    result = chat_tools.student_learning_profile(student_id="g1_101")
    assert "error" not in result
    assert {"g1_101", "g2_201"} <= set(result["student"]["all_student_ids"])
    exam_grades = {ex["grade"] for ex in result["available_exams"]}
    assert {1, 2} <= exam_grades


# ────────────────────────────── student_notes ──────────────────────────────


def test_student_notes_union_across_linked_ids(db):
    """挂在 g1_101 的档案，经 g2_201 查询可见（person union）。"""
    note = StudentNote(
        student_id="g1_101",
        date="2025-10-01",
        category="谈话",
        content="高一学号下的档案：跨学年可见性",
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    nid = note.id
    try:
        result = chat_tools.student_notes(student_id="g2_201")
        assert "error" not in result
        ids = {n["content"] for n in result["notes"]}
        assert "高一学号下的档案：跨学年可见性" in ids
        # 反向也可见
        result2 = chat_tools.student_notes(student_id="g1_101")
        assert "error" not in result2
        ids2 = {n["content"] for n in result2["notes"]}
        assert "高一学号下的档案：跨学年可见性" in ids2
    finally:
        db.delete(note)
        db.commit()


# ────────────────────────────── student_identity_lookup ──────────────────────────────


def test_student_identity_lookup_by_id_returns_merged_aliases():
    """链为一人时按学号查返回 identity_id + aliases 含两个学号。"""
    result = chat_tools.student_identity_lookup(student_id="g2_201")
    assert "error" not in result
    assert result["identity_id"] is not None
    alias_sids = {a["student_id"] for a in result["aliases"]}
    assert {"g1_101", "g2_201"} <= alias_sids
    # 学号履历应带年级
    grades = {a["grade"] for a in result["aliases"]}
    assert {1, 2} <= grades
    assert result["display_name"] == "陈一"


def test_student_identity_lookup_by_old_id_returns_merged_aliases():
    """用高一旧学号查同样解析到同一人的两个学号（对称）。"""
    result = chat_tools.student_identity_lookup(student_id="g1_101")
    assert "error" not in result
    assert result["identity_id"] is not None
    alias_sids = {a["student_id"] for a in result["aliases"]}
    assert {"g1_101", "g2_201"} <= alias_sids


def test_student_identity_lookup_by_name_disambiguates():
    """按姓名查时，name_candidates 跨年级各命中一次（高一 g1_101 / 高二 g2_201），
    返回「匹配到多个学生」候选（同名必须人工消歧，绝不按姓名自动合并）。"""
    result = chat_tools.student_identity_lookup(name="陈一")
    assert isinstance(result, dict)
    # 两个学号同名 -> 必须人工指定学号，不能自动合并
    assert "error" in result
    assert "匹配到多个" in result["error"]
    cand_sids = {c["student_id"] for c in result.get("candidates", [])}
    assert {"g1_101", "g2_201"} <= cand_sids


def test_student_identity_lookup_unknown_name_returns_error_dict():
    """未知姓名应返回 dict（中文 error），不能抛异常。"""
    result = chat_tools.student_identity_lookup(name="查无此名张三丰")
    assert isinstance(result, dict)
    assert "error" in result
    assert "未找到学生" in result["error"] or "匹配到多个" in result["error"] or "未找到" in result["error"]


def test_student_identity_lookup_unknown_id_returns_error_dict():
    """未知学号应返回 dict（中文 error），不能抛异常。"""
    result = chat_tools.student_identity_lookup(student_id="no_such_sid_999")
    assert isinstance(result, dict)
    assert "error" in result


def test_student_identity_lookup_registered_in_all_three_sites():
    """新工具在 TOOL_FUNCTIONS / TOOLS / 函数定义三处都已注册。"""
    from app.chat.tools import TOOL_FUNCTIONS, TOOLS, student_identity_lookup

    assert TOOL_FUNCTIONS["student_identity_lookup"] is student_identity_lookup
    names = {t["name"] for t in TOOLS}
    assert "student_identity_lookup" in names


# ────────────────────────────── 单年级班级工具不受影响 ──────────────────────────────


def test_focus_list_unaffected_keys_off_exam_id():
    """单年级班级工具 focus_list 仍按 exam_id 收口：高一 1001 含 g1_101/g1_102，
    不会把高二的 g2_201 算进来（两个高一学号主三门排名都落在薄弱段口径内）。。
    focus_list 本身不按 class_num 过滤，但它按 exam_id 限定单场考试，因此
    天然按当年行政班收口。"""
    rows = chat_tools.focus_list(exam_id=1001)
    sids = {r["student_id"] for r in rows}
    # g1_101 / g1_102 都在 1001 这场高一考试里
    assert {"g1_101", "g1_102"} <= sids
    # 高二学号绝不在高一考试里出现
    assert "g2_201" not in sids


def test_class_trend_unaffected_keys_off_class_num():
    """class_trend 仍按 class_num 收口：查班 6 的时间序列只匹配高一班 6 的
    ClassAverage（本隔离库未 seed ClassAverage，应返回空 series，但绝不报错
    或把高二三班的数据混进来）。"""
    series = chat_tools.class_trend(class_num=6, metric="主三门")
    assert isinstance(series, list)
    # 隔离库未建 ClassAverage，series 为空是正常；关键是按 class_num 收口、无异常
    # （这里若 seed 了 ClassAverage 也只会是高一班 6 的记录）


def test_subject_weakness_unaffected_keys_off_class_num():
    """subject_weakness 仍按 class_num + exam_id 收口：高一班 6 / 考试 1001，
    只统计高一班 6 的单科记录，不含高二 g2_201。"""
    rows = chat_tools.subject_weakness(class_num=6, exam_id=1001)
    sids = {r["student_id"] for r in rows}
    assert "g2_201" not in sids
    # g1_101 / g1_102 都是高一班 6 的学号（是否进薄弱取决于百分位差，但至少不应抛异常）
    assert sids <= {"g1_101", "g1_102"}
