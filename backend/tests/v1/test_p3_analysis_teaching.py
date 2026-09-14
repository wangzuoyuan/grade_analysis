"""P3 分析端点——teaching 域（契约 docs/contracts/p3-imports-analysis.md §2.2 + §2.3）。

在 tests/v1/conftest.py 合成样本（v1_seed）之上 ORM 直种分析场景，
不依赖导入链路。覆盖验收：
- E02：0 分计入均分/名次；NULL 计 missing_count 不进分母；
  同分同名次（min-rank）且下一名跳号。
- 反向投影：H 域行经 §1.4.1 投影门参与 teaching 名次并标 source_domain；
  T 域已有同场事实时不投影、不重复计人。
- E04：class-compare 全部行 source='estimated'；单班有效人数 <5 →
  small_sample=true。
- 作用域：teaching_class_id 缺省=全部所教班并集；指定单班时其他班
  （T8）不可见；空教学班（T-empty）为合法 200 空态。
- 越界：teaching 域内不存在该考试 → 404。
"""

from datetime import date
from urllib.parse import quote

import pytest

API = "/api/v1"

EXAM_BASE = "2025期中"  # v1_seed 原样：T6 甲91 乙85 丁NULL；H 域 90/84（同场不投影）
EXAM_ZERO_NULL = "2025月考1"  # E02：0 与 NULL 并存
EXAM_TIE = "2025月考2"  # E02：两人同分 + 下一名跳号
EXAM_PROJECTED = "2025投影考"  # 仅 H 域有行的考试（反向投影参与名次）
EXAM_SAME = "2025期末"  # E04 class-compare
EXAM_MISSING = "2025没有"


def _stats(client, exam, **params):
    r = client.get(f"{API}/teaching/analysis/exams/{quote(exam)}/stats", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _students(client, exam, **params):
    r = client.get(f"{API}/teaching/analysis/exams/{quote(exam)}/students", params=params)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def p3_seed(v1_seed):
    """在 v1_seed 基础上补种教学分析场景（ORM 直种，不走导入链路）。"""
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    s = v1_seed
    db = SessionLocal()

    def fact(domain, cls_id, ident_id, exam, exam_date_iso, score):
        f = wm.ScoreFact()
        f.data_domain = domain
        f.academic_year_id = s.ay_id
        f.exam_name = exam
        f.exam_date = date.fromisoformat(exam_date_iso)
        f.class_ref_id = cls_id
        f.identity_id = ident_id
        f.subject = "物理"
        f.total_type = None
        f.score = score
        f.source = "p3-analysis-test"
        db.add(f)
        return f

    # E02：0 分（甲）计入均分/名次；丁 NULL 不进分母
    fact("teaching", s.t6_id, s.jia_t_id, EXAM_ZERO_NULL, "2025-10-15", 0.0)
    fact("teaching", s.t6_id, s.yi_t_id, EXAM_ZERO_NULL, "2025-10-15", 80.0)
    fact("teaching", s.t6_id, s.ding_t_id, EXAM_ZERO_NULL, "2025-10-15", None)

    # E02：甲乙同分 80 → 并列第 1，丁 70 跳号为第 3
    fact("teaching", s.t6_id, s.jia_t_id, EXAM_TIE, "2025-10-25", 80.0)
    fact("teaching", s.t6_id, s.yi_t_id, EXAM_TIE, "2025-10-25", 80.0)
    fact("teaching", s.t6_id, s.ding_t_id, EXAM_TIE, "2025-10-25", 70.0)

    # 反向投影样本：仅 H 域有甲的行，T6 无该场 → 投影参与 teaching 名次
    fact("homeroom", s.h6_id, s.jia_h_id, EXAM_PROJECTED, "2025-12-05", 95.0)

    # E04 class-compare 样本
    fact("teaching", s.t6_id, s.jia_t_id, EXAM_SAME, "2025-12-20", 88.0)
    fact("teaching", s.t6_id, s.yi_t_id, EXAM_SAME, "2025-12-20", 72.0)
    fact("teaching", s.t6_id, s.ding_t_id, EXAM_SAME, "2025-12-20", None)

    db.commit()
    yield
    db.close()


# ────────────────────────────── E02 计量红线 ──────────────────────────────


def test_e02_zero_in_avg_and_null_missing(client, p3_seed, v1_seed):
    s = v1_seed
    stats = _stats(
        client, EXAM_ZERO_NULL, academic_year_id=s.ay_id, teaching_class_id=s.t6_id
    )
    # 0 分计入均分与最值；NULL 单独计 missing_count，不进分母、绝不转 0
    assert stats["avg"] == 40.0
    assert stats["max"] == 80.0 and stats["min"] == 0.0
    assert stats["valid_count"] == 2 and stats["missing_count"] == 1
    assert stats["cohort_size"] == 3


def test_e02_min_rank_tie_and_jump(client, p3_seed, v1_seed):
    s = v1_seed
    body = _students(
        client, EXAM_TIE, academic_year_id=s.ay_id, teaching_class_id=s.t6_id
    )
    ranks = {row["person_id"]: row["rank"] for row in body["students"]}
    # 同分同名次（1,1），下一名跳号为 3（min-rank，如 1,2,2,4）
    assert ranks[s.jia_t_id] == 1 and ranks[s.yi_t_id] == 1
    assert ranks[s.ding_t_id] == 3
    # 名次只在 T6 成员内计算，丁的第 3 名即全班最大名次
    stats = _stats(client, EXAM_TIE, academic_year_id=s.ay_id, teaching_class_id=s.t6_id)
    assert stats["rank_min"] == 1 and stats["rank_max"] == 3


# ────────────────────────────── 反向投影 ──────────────────────────────


def test_reverse_projection_participates_in_rank(client, p3_seed, v1_seed):
    """EXAM_PROJECTED 仅 H 域有甲的行：经 §1.4.1 投影门进入 teaching
    响应（source_domain='homeroom'），参与名次与分母。"""
    s = v1_seed
    body = _students(
        client, EXAM_PROJECTED, academic_year_id=s.ay_id, teaching_class_id=s.t6_id
    )
    assert len(body["students"]) == 1
    row = body["students"][0]
    assert row["person_id"] == s.jia_t_id  # person_id 仍为响应所在域身份
    assert row["score"] == 95.0 and row["rank"] == 1
    assert row["source_domain"] == "homeroom"
    assert row["class_label"] == "高二6班(教)"
    assert "grade_score" in row  # 列在途时缺省 null，键结构按契约存在

    stats = _stats(
        client, EXAM_PROJECTED, academic_year_id=s.ay_id, teaching_class_id=s.t6_id
    )
    assert stats["valid_count"] == 1 and stats["missing_count"] == 0
    assert stats["avg"] == 95.0
    assert stats["rank_min"] == 1 and stats["rank_max"] == 1
    assert stats["cohort_size"] == 3  # 乙丁本场无行，不推断、不编造
    assert stats["small_sample"] is True


def test_teaching_row_kept_when_h_domain_has_same_exam(client, p3_seed, v1_seed):
    """E1：T6 已有甲乙同场行（91/85），H 域 90/84 同场 → 不投影、不附
    字段、不重复计人（每人每场恰一行）。"""
    s = v1_seed
    body = _students(client, EXAM_BASE, academic_year_id=s.ay_id, teaching_class_id=s.t6_id)
    by_id = {row["person_id"]: row for row in body["students"]}
    assert set(by_id) == {s.jia_t_id, s.yi_t_id, s.ding_t_id}
    assert by_id[s.jia_t_id]["score"] == 91.0
    assert by_id[s.jia_t_id]["source_domain"] == "teaching"
    assert by_id[s.ding_t_id]["score"] is None
    stats = _stats(client, EXAM_BASE, academic_year_id=s.ay_id, teaching_class_id=s.t6_id)
    assert stats["valid_count"] == 2 and stats["avg"] == 88.0


# ────────────────────────────── E04 班级对比 ──────────────────────────────


def test_e04_class_compare_estimated_and_small_sample(client, p3_seed, v1_seed):
    s = v1_seed
    r = client.get(
        f"{API}/teaching/analysis/class-compare",
        params={"exam_name": EXAM_SAME, "academic_year_id": s.ay_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    by_id = {row["teaching_class_id"]: row for row in body["classes"]}
    # 全部所教班（含 T8、T-empty）各占一行，本班样本均分恒标 estimated
    assert set(by_id) == {s.t6_id, s.t8_id, s.t_empty_id}
    assert all(row["source"] == "estimated" for row in body["classes"])
    t6 = by_id[s.t6_id]
    assert t6["class_label"] == "高二6班(教)"
    # member_count = 参与该场考试的有效成员数（非 NULL），非名册人数
    assert t6["member_count"] == 2 and t6["subject_avg"] == 80.0
    assert t6["score_basis"] == "raw"
    for tc in (s.t8_id, s.t_empty_id):
        assert by_id[tc]["member_count"] == 0 and by_id[tc]["subject_avg"] is None
    # 单班有效人数 <5 → small_sample
    assert body["small_sample"] is True


# ────────────────────────────── 作用域与空态 ──────────────────────────────


def test_teaching_empty_class_is_legal_empty_state(client, p3_seed, v1_seed):
    s = v1_seed
    stats = _stats(
        client, EXAM_BASE, academic_year_id=s.ay_id, teaching_class_id=s.t_empty_id
    )
    assert stats["avg"] is None and stats["max"] is None and stats["min"] is None
    assert stats["valid_count"] == 0 and stats["missing_count"] == 0
    assert stats["rank_min"] is None and stats["rank_max"] is None
    assert stats["cohort_size"] == 0 and stats["small_sample"] is True
    body = _students(
        client, EXAM_BASE, academic_year_id=s.ay_id, teaching_class_id=s.t_empty_id
    )
    assert body["students"] == []  # 空成员绝不回退全年级


def test_teaching_union_default_and_single_class_scope(client, p3_seed, v1_seed):
    s = v1_seed
    # teaching_class_id 缺省 → 全部所教班（T6+T8）成员并集
    stats = _stats(client, EXAM_BASE, academic_year_id=s.ay_id)
    assert stats["cohort_size"] == 5
    assert stats["valid_count"] == 4  # 甲91 乙85 戊77 己82；丁 NULL
    assert stats["missing_count"] == 1
    assert stats["avg"] == 83.75
    assert stats["rank_min"] == 1 and stats["rank_max"] == 4
    assert stats["metadata"]["scope"]["teaching_class_id"] is None
    persons = {row["person_id"] for row in _students(client, EXAM_BASE, academic_year_id=s.ay_id)["students"]}
    assert persons == set(s.t6_person_ids + s.t8_person_ids)

    # 指定单班 T6：T8（戊己）不可见
    single = _students(
        client, EXAM_BASE, academic_year_id=s.ay_id, teaching_class_id=s.t6_id
    )
    assert {row["person_id"] for row in single["students"]} == set(s.t6_person_ids)
    assert all(row["class_label"] == "高二6班(教)" for row in single["students"])


def test_teaching_exam_not_in_domain_returns_404(client, p3_seed, v1_seed):
    s = v1_seed
    for path in ("stats", "students"):
        r = client.get(
            f"{API}/teaching/analysis/exams/{quote(EXAM_MISSING)}/{path}",
            params={"academic_year_id": s.ay_id, "teaching_class_id": s.t6_id},
        )
        assert r.status_code == 404, path
        assert r.json()["error"] == "resource_out_of_scope"
    r = client.get(
        f"{API}/teaching/analysis/class-compare",
        params={"exam_name": EXAM_MISSING, "academic_year_id": s.ay_id},
    )
    assert r.status_code == 404
    assert r.json()["error"] == "resource_out_of_scope"
