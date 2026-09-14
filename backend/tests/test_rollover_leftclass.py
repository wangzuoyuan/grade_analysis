"""left_class 跨学号空间回归测试。

复现并锁死 rollover.classify() 的 left_class 缺陷：学号跨学年会换号段
（高一 72460xx，高二 72563xx），两套学号空间永不相交。旧逻辑
`if sid in sid_set: continue`（sid 为高一学号，sid_set 为高二学号集合）
使得**没有任何**高一学号命中，于是上学年整班都会被误报为「离班」——
包括同名结转的、以及已 link 身份的学生。

新逻辑只在两条信号都不满足时才判「离班」：
  1) 身份已链接且其任一学号出现在新班；
  2) 姓名出现在新班学生姓名中（同名待确认）。

本测试用模块级独立 EXAM_TRACKER_DIR，绝不触碰 ~/.exam-tracker。
"""

import os
import tempfile

# 必须在 import app.main 之前钉住隔离库目录。
_TMP = tempfile.mkdtemp(prefix="rollover_leftclass_test_")
os.environ["EXAM_TRACKER_DIR"] = os.path.join(_TMP, "examdata")
os.makedirs(os.environ["EXAM_TRACKER_DIR"], exist_ok=True)

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.models import (
    SessionLocal,
    Exam,
    SubjectScore,
    Teacher,
    ClassRoster,
    HomeworkSetting,
    ImportedHistory,
    StudentIdentity,
    StudentAlias,
)
from app.rollover.service import classify
from app.analysis.identity import ensure_identity, link_aliases


# ── 跨号段学号 ──────────────────────────────────────────────
# 高一 class 6：号段 72460xx
G1_CHEN = "7246001"   # 陈一 —— 高二同名 ambiguous 结转，不算离班
G1_MA = "7246002"     # 马二 —— 高二无同名、无身份链 -> 真正离班
G1_WANG = "7246003"   # 王五 —— 高二同名 ambiguous 结转，不算离班
G1_ZHAO = "7246004"   # 赵六 —— 高二改名，靠身份 link 结转，不算离班

# 高二 class 3：号段 72563xx（与高一学号空间完全不相交）
G2_CHEN = "7256301"   # 陈一（同名）
G2_WANG = "7256303"   # 王五（同名）
G2_ZHAO = "7256304"   # 赵小六（改名，需身份 link）


def _seed_exam(eid, name, grade, exam_date):
    s = SessionLocal()
    s.add(
        Exam(id=eid, name=name, grade=grade, semester="上",
             exam_date=exam_date, exam_type="月考")
    )
    s.commit()
    s.close()


def _seed_subject(exam_id, student_id, name, class_num, subject="语文"):
    s = SessionLocal()
    s.add(
        SubjectScore(exam_id=exam_id, student_id=student_id, name=name,
                     class_num=class_num, subject=subject, raw_score=80.0)
    )
    s.commit()
    s.close()


def seed():
    s = SessionLocal()
    for m in (SubjectScore, Exam, Teacher, ClassRoster, HomeworkSetting,
              ImportedHistory, StudentAlias, StudentIdentity):
        s.query(m).delete()
    s.commit()
    s.close()

    _seed_exam(1001, "高一入学测", 1, "2024-09")
    _seed_exam(1002, "高二开学测", 2, "2025-09")

    # 高一 class 6：四名学生
    _seed_subject(1001, G1_CHEN, "陈一", 6)
    _seed_subject(1001, G1_MA, "马二", 6)
    _seed_subject(1001, G1_WANG, "王五", 6)
    _seed_subject(1001, G1_ZHAO, "赵六", 6)

    # 高二 class 3：三名学生（马二没跟过来）
    _seed_subject(1002, G2_CHEN, "陈一", 3)      # 同名 -> ambiguous
    _seed_subject(1002, G2_WANG, "王五", 3)      # 同名 -> ambiguous
    _seed_subject(1002, G2_ZHAO, "赵小六", 3)    # 改名，需 link

    # 班主任绑定 高一->6，高二->3
    s = SessionLocal()
    s.merge(Teacher(id=1, target_class_high1=6, target_class_high2=3))
    s.commit()
    s.close()


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


def test_left_class_excludes_same_name_carryover(client, db):
    """link 前 left_class 只含真正走人的（马二 + 尚未 link 的改名赵六），
    绝不是整个高一班——同名结转的陈一/王五必须排除。

    旧逻辑（`高一学号 in 高二学号集合`）跨号段无一命中，会把 G1 全体 4 人
    都塞进 left_class；新逻辑靠「同名在新班」信号排除陈一、王五。
    """
    result = classify(db, target_grade=2, class_num=3)

    left = result["left_class"]
    left_sids = {x["student_id"] for x in left}
    left_names = {x["name"] for x in left}

    # 决定性断言：同名结转的陈一/王五 不得被误报为离班（旧逻辑会误报）。
    assert G1_CHEN not in left_sids, f"陈一被误报离班：{left_names}"
    assert G1_WANG not in left_sids, f"王五被误报离班：{left_names}"

    # 此刻真正走人的：马二（无同名、无链）+ 赵六（改名、尚未 link）。
    # 绝非整班 4 人。
    assert left_sids == {G1_MA, G1_ZHAO}, f"实得 left_class={left_names}"
    assert result["summary"]["left_class"] == 2
    # 马二一定在其中
    assert G1_MA in left_sids


def test_carried_by_same_name_are_ambiguous_not_left(client, db):
    """同名结转的高二学生进入 ambiguous，其高一同名号不在 left_class。"""
    result = classify(db, target_grade=2, class_num=3)
    amb_sids = {a["student_id"] for a in result["ambiguous"]}
    # 高二陈一、王五应作为同名候选待消歧
    assert G2_CHEN in amb_sids
    assert G2_WANG in amb_sids
    left_sids = {x["student_id"] for x in result["left_class"]}
    assert G1_CHEN not in left_sids
    assert G1_WANG not in left_sids


def test_linked_student_not_in_left_class(client, db):
    """把高二 赵小六 link 到高一 赵六（跨号段身份），赵六不再被判离班。

    link 前：赵六改了名，新班无同名、无身份 -> 落在 left_class。
    link 后：identity 的高一号 G1_ZHAO 通过 person_sids ∩ sid_set 命中，
    应从 left_class 移除。
    """
    # link 前：赵六在 left_class
    before = classify(db, target_grade=2, class_num=3)
    before_left = {x["student_id"] for x in before["left_class"]}
    assert G1_ZHAO in before_left, "改名未 link 前，赵六应先被判离班"

    # 建 identity，把跨号段两学号挂到同一人
    iid = ensure_identity(db, display_name="赵六")
    link_aliases(db, iid, [(G1_ZHAO, 1), (G2_ZHAO, 2)], "manual")

    # link 后：赵六不再离班
    after = classify(db, target_grade=2, class_num=3)
    after_left = {x["student_id"] for x in after["left_class"]}
    assert G1_ZHAO not in after_left, "身份 link 后赵六不应再被判离班"

    # 且高二 赵小六 现应落 inherited（而非 left/ambiguous/new）
    inh_sids = {x["student_id"] for x in after["inherited"]}
    assert G2_ZHAO in inh_sids

    # 最终 left_class 仍只剩真正离开的马二
    assert after_left == {G1_MA}
    assert after["summary"]["left_class"] == 1
