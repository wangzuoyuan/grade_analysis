"""2026-10-22 用户拍板的学科/总分列序规则 + 档案总分行校名次。

列序规则（退役旧 sorted() 字母序——「化学、历史、地理、政治、数学…」）：
- 学科：语文、数学、英语、物理、化学、生物、政治、历史、地理（仅列有数据的）
- 总分：高一 = 主三门、五门、九门；高二/高三 = +3、主三门、3+3
- 年级不可得（legacy 事实无考试事件）→ 总分保持既有字母序回退
- 档案画像/打印报告总分行带校名次（学籍名次优先，resolve_year_rank），
  单科行恒 None。

覆盖码点：order_subjects/order_totals 单元行为、homeroom_exam_stats、
homeroom_exam_students（键序）、homeroom 画像、homeroom student_report。
"""

from datetime import date
from types import SimpleNamespace
from urllib.parse import quote

import pytest

API = "/api/v1"

# 场景考试名（v1_seed 的 2025期中 之外由本模块补种）
EXAM_ORDER = "2025排序考"  # 挂事件 grade=2 → 规范列序
EXAM_LEGACY = "2025旧排序考"  # 无事件（legacy）→ 总分字母序回退

EXPECTED_SUBJECTS = ["语文", "数学", "英语", "物理", "化学", "地理"]
EXPECTED_TOTALS_G2 = ["+3", "主三门", "3+3"]


@pytest.fixture(scope="module")
def order_seed(v1_seed):
    """在 v1_seed（高二6班，甲乙丙）之上补种排序场景（ORM 直种合成数据）。

    - 排序考（event grade=2）：甲乙丙各 语数英+物化地 六科 + 三种总分；
      总分行名次覆盖 学籍名次优先 / grade_rank 兜底 / 双双缺失 三种形态。
    - 旧排序考（legacy 无事件，日期更晚）：甲的三种总分 → 回退字母序。
    """
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    s = v1_seed
    db = SessionLocal()

    event = wm.WorkspaceExamEvent(
        academic_year_id=s.ay_id, grade=2, exam_name=EXAM_ORDER
    )
    db.add(event)
    db.flush()

    def fact(ident_id, exam, exam_date_iso, subject, total_type, score,
             xueji_rank=None, grade_rank=None, event_id=None):
        f = wm.ScoreFact()
        f.data_domain = "homeroom"
        f.academic_year_id = s.ay_id
        f.exam_name = exam
        f.exam_event_id = event_id
        f.exam_date = date.fromisoformat(exam_date_iso)
        f.class_ref_id = s.h6_id
        f.identity_id = ident_id
        f.subject = subject
        f.total_type = total_type
        f.score = score
        f.xueji_rank = xueji_rank
        f.grade_rank = grade_rank
        f.source = "order-rule-test"
        db.add(f)
        return f

    subjects_scores = {
        s.jia_h_id: {"语文": 88.0, "数学": 92.0, "英语": 95.0, "物理": 90.0, "化学": 85.0, "地理": 80.0},
        s.yi_h_id: {"语文": 76.0, "数学": 81.0, "英语": 79.0, "物理": 84.0, "化学": 78.0, "地理": 74.0},
        s.bing_h_id: {"语文": 65.0, "数学": 70.0, "英语": 72.0, "物理": 68.0, "化学": 66.0, "地理": 63.0},
    }
    # 总分行名次形态：甲 3+3=学籍名次 12、主三门=grade_rank 58 兜底、+3 无名次；
    # 乙 主三门=学籍名次 88；丙 双双缺失。
    totals_rows = [
        (s.jia_h_id, "+3", 348.0, None, None),
        (s.jia_h_id, "主三门", 275.0, None, 58),
        (s.jia_h_id, "3+3", 436.0, 12, None),
        (s.yi_h_id, "+3", 312.0, None, None),
        (s.yi_h_id, "主三门", 236.0, 88, None),
        (s.yi_h_id, "3+3", 398.0, 34, None),
        (s.bing_h_id, "+3", 280.0, None, None),
        (s.bing_h_id, "主三门", 207.0, None, None),
        (s.bing_h_id, "3+3", 342.0, 56, None),
    ]
    for ident_id, by_subject in subjects_scores.items():
        for subject, score in by_subject.items():
            fact(ident_id, EXAM_ORDER, "2025-12-01", subject, None, score, event_id=event.id)
    for ident_id, total_type, score, xueji_rank, grade_rank in totals_rows:
        fact(ident_id, EXAM_ORDER, "2025-12-01", None, total_type, score,
             xueji_rank=xueji_rank, grade_rank=grade_rank, event_id=event.id)

    # legacy 考试（无事件、日期更晚）：证明年级不可得时总分回退字母序
    fact(s.jia_h_id, EXAM_LEGACY, "2025-12-15", "语文", None, 90.0)
    fact(s.jia_h_id, EXAM_LEGACY, "2025-12-15", "物理", None, 86.0)
    fact(s.jia_h_id, EXAM_LEGACY, "2025-12-15", None, "+3", 350.0)
    fact(s.jia_h_id, EXAM_LEGACY, "2025-12-15", None, "主三门", 276.0)
    fact(s.jia_h_id, EXAM_LEGACY, "2025-12-15", None, "3+3", 438.0)

    db.commit()
    yield SimpleNamespace(event_id=event.id)
    db.close()


# ────────────────────────────── 单元行为 ──────────────────────────────


def test_order_subjects_unit():
    from app.analysis.definitions import order_subjects

    # 规范序：语数英在前、六选科在后（仅列有数据的）
    assert order_subjects({"化学", "地理", "数学", "物理", "语文", "英语"}) == EXPECTED_SUBJECTS
    assert order_subjects(["生物", "语文", "政治"]) == ["语文", "生物", "政治"]
    # 未知学科排在已知之后（按名称排序保证确定性）
    assert order_subjects(["日语", "化学", "物理"]) == ["物理", "化学", "日语"]
    assert order_subjects([]) == []


def test_order_totals_unit():
    from app.analysis.definitions import order_totals

    # 高二/高三：+3、主三门、3+3；未知口径排已知之后
    assert order_totals({"3+3", "主三门", "+3"}, 2) == EXPECTED_TOTALS_G2
    assert order_totals(["主三门", "3+3"], 3) == ["主三门", "3+3"]
    assert order_totals({"3+3", "主三门", "总总分"}, 2) == ["主三门", "3+3", "总总分"]
    # 高一：主三门、五门、九门
    assert order_totals({"九门", "主三门", "五门"}, 1) == ["主三门", "五门", "九门"]
    # 年级不可得（None/未知年级）→ 保持既有 sorted() 字母序行为
    assert order_totals({"3+3", "主三门", "+3"}, None) == ["+3", "3+3", "主三门"]
    assert order_totals({"主三门", "五门"}, 9) == ["主三门", "五门"]


# ────────────────────────────── student_report（打印） ──────────────────────────────


def test_report_column_order_and_total_rank(client, order_seed, v1_seed):
    s = v1_seed
    r = client.get(
        f"{API}/homeroom/students/{s.jia_h_id}/report",
        params={"academic_year_id": s.ay_id, "class_id": s.h6_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 学科规范序（语数英 + 物化地）；总分按高二口径序（年级=最新一场
    # 可定位年级的考试事件，排序考 grade=2；legacy 考试无事件不参与定级）
    assert [g["subject"] for g in body["subjects"]] == EXPECTED_SUBJECTS
    assert [g["total_type"] for g in body["totals"]] == EXPECTED_TOTALS_G2

    # 总分行校名次：学籍名次优先（3+3=12）、grade_rank 兜底（主三门=58）、
    # 来源无名次（+3=None，高二 '+3' 明细本就无排名列）
    by_total = {g["total_type"]: g["exams"] for g in body["totals"]}
    order_exam = next(e for e in by_total["主三门"] if e["exam_name"] == EXAM_ORDER)
    assert order_exam["rank"] == 58
    plus3 = next(e for e in by_total["+3"] if e["exam_name"] == EXAM_ORDER)
    assert plus3["rank"] is None
    total3 = next(e for e in by_total["3+3"] if e["exam_name"] == EXAM_ORDER)
    assert total3["rank"] == 12
    # legacy 总分行（无事件）名次照常缺失
    legacy = next(e for e in by_total["3+3"] if e["exam_name"] == EXAM_LEGACY)
    assert legacy["rank"] is None

    # 单科行恒无校名次
    for group in body["subjects"]:
        assert all(e["rank"] is None for e in group["exams"]), group["subject"]


# ────────────────────────────── 画像 ──────────────────────────────


def test_profile_column_order_and_total_rank(client, order_seed, v1_seed):
    s = v1_seed
    r = client.get(
        f"{API}/homeroom/students/{s.jia_h_id}",
        params={"academic_year_id": s.ay_id, "class_id": s.h6_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [g["subject"] for g in body["subjects"]] == EXPECTED_SUBJECTS
    assert [g["total_type"] for g in body["totals"]] == EXPECTED_TOTALS_G2
    by_total = {g["total_type"]: g["exams"] for g in body["totals"]}
    assert next(e for e in by_total["3+3"] if e["exam_name"] == EXAM_ORDER)["rank"] == 12
    assert all(e["rank"] is None for g in body["subjects"] for e in g["exams"])


# ────────────────────────────── stats / students 端点 ──────────────────────────────


def test_stats_column_order_g2(client, order_seed, v1_seed):
    s = v1_seed
    r = client.get(
        f"{API}/homeroom/analysis/exams/{quote(EXAM_ORDER)}/stats",
        params={"academic_year_id": s.ay_id, "class_id": s.h6_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [row["subject"] for row in body["subjects"]] == EXPECTED_SUBJECTS
    assert [row["total_type"] for row in body["totals"]] == EXPECTED_TOTALS_G2
    # 总分行 rank_min/rank_max 沿用 resolve_year_rank（学籍名次优先）
    totals = {row["total_type"]: row for row in body["totals"]}
    assert totals["3+3"]["rank_min"] == 12 and totals["3+3"]["rank_max"] == 56
    assert totals["主三门"]["rank_min"] == 58 and totals["主三门"]["rank_max"] == 88


def test_stats_legacy_exam_keeps_alpha_fallback(client, order_seed, v1_seed):
    """legacy 考试（无事件、年级不可得）→ 总分保持既有字母序回退。"""
    s = v1_seed
    r = client.get(
        f"{API}/homeroom/analysis/exams/{quote(EXAM_LEGACY)}/stats",
        params={"academic_year_id": s.ay_id, "class_id": s.h6_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [row["subject"] for row in body["subjects"]] == ["语文", "物理"]
    assert [row["total_type"] for row in body["totals"]] == ["+3", "3+3", "主三门"]


def test_students_endpoint_key_order(client, order_seed, v1_seed):
    """单场学生表：scores/percentiles/totals/ranks 的键序同样按规范序输出。"""
    s = v1_seed
    r = client.get(
        f"{API}/homeroom/analysis/exams/{quote(EXAM_ORDER)}/students",
        params={"academic_year_id": s.ay_id, "class_id": s.h6_id},
    )
    assert r.status_code == 200, r.text
    by_id = {row["person_id"]: row for row in r.json()["students"]}
    jia = by_id[s.jia_h_id]
    assert list(jia["scores"]) == EXPECTED_SUBJECTS
    assert list(jia["totals"]) == EXPECTED_TOTALS_G2
    assert list(jia["ranks"]) == EXPECTED_TOTALS_G2
    assert list(jia["percentiles"]) == EXPECTED_SUBJECTS + EXPECTED_TOTALS_G2
    assert jia["ranks"]["3+3"] == 12 and jia["ranks"]["主三门"] == 58
    assert jia["ranks"]["+3"] is None
