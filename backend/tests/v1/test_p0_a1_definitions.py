"""P0-A1 统一分析口径回归测试。

覆盖三个维度（对照清单 docs/diagnosis-roadmap/p0-definitions.md）：
1. 共享定义单测——进退步/波动/偏科/段位/班内名次/分箱在新旧两条路径
   共用 backend/app/analysis/definitions.py 的同一实现；
2. 名次缺失语义——无真实学籍/年级名次时名次不可得（null），
   绝不按「百分位 × 人数」推算名次（rank-range 两路径 + 趋势点）；
3. 缺考处理——缺考不转 0、不残留上次百分位/名次、不伪造连续性。

样本全部为合成姓名（秦甲/秦乙/秦丙/秦丁/秦戊），不使用任何真实学生数据。
旧路径用例直接构造旧库 Exam/SubjectScore/TotalScore 行（fresh schema）。
"""

from datetime import date

import pytest

from .conftest import EXAM_E1, SUBJECT, TOTAL_TYPE_MAIN3


API = "/api/v1"


# ────────────────────────── 1. 共享定义单测 ──────────────────────────


def test_resolve_year_rank_priority_and_missing():
    from app.analysis.definitions import resolve_year_rank

    assert resolve_year_rank(12, 30) == 12  # 学籍名次优先
    assert resolve_year_rank(None, 30) == 30  # 缺学籍名次回退年级名次
    assert resolve_year_rank(None, None) is None  # 名次不可得
    assert resolve_year_rank(0, 30) == 30  # 非正值视为无效数据
    assert resolve_year_rank(-1, None) is None


def test_progress_issue_thresholds():
    from app.analysis.definitions import ISSUE_PROGRESS, ISSUE_REGRESSION, progress_issue

    assert progress_issue(80) == ISSUE_PROGRESS  # 正数=名次数值变小=进步
    assert progress_issue(79) is None
    assert progress_issue(-80) == ISSUE_REGRESSION
    assert progress_issue(-79) is None
    assert progress_issue(None) is None  # 任一场名次缺失不判


def test_volatility_issue_range_and_min_points():
    from app.analysis.definitions import ISSUE_VOLATILE, volatility_issue

    # 3 场、极差恰 120 → 判波动
    assert volatility_issue([100, 220, 100]) == ISSUE_VOLATILE
    # 极差 119 → 不判（与新路径 focus 端点同一临界）
    assert volatility_issue([100, 219, 100]) is None
    # 不足 3 场不判，哪怕极差巨大
    assert volatility_issue([1, 999]) is None
    # None 名次（缺考场）不参与极差
    assert volatility_issue([100, None, 220, 100]) == ISSUE_VOLATILE
    assert volatility_issue([100, None, 100]) is None


def test_subject_weakness_threshold_and_missing():
    from app.analysis.definitions import subject_weakness_subjects

    pairs = [("物理", 0.55), ("化学", 0.40), ("生物", None)]
    # 主三门百分位 0.30：物理差 0.25 命中、化学差 0.10 不命中、生物缺失不判
    assert subject_weakness_subjects(pairs, 0.30) == ["物理"]
    # 恰好等于阈值 0.20 命中（>=）
    assert subject_weakness_subjects([("物理", 0.50)], 0.30) == ["物理"]
    assert subject_weakness_subjects([("物理", 0.49)], 0.30) == []
    # 主三门百分位缺失 → 全部不判（缺考不残留）
    assert subject_weakness_subjects(pairs, None) == []
    # 百分数形态（0–100）与 0–1 同一口径
    assert subject_weakness_subjects([("物理", 55)], 30) == ["物理"]


def test_band_flags_missing_rank_never_banded():
    from app.analysis.definitions import band_flags

    config = {"high_score_max": 80, "critical_min": 400, "critical_max": 500, "weak_min": 501}
    assert band_flags(None, config) == {
        "high_score": False,
        "critical": False,
        "weak": False,
    }
    assert band_flags(1, config)["high_score"] is True
    assert band_flags(400, config)["critical"] is True
    assert band_flags(501, config)["weak"] is True
    assert band_flags(9999, config)["weak"] is True


def test_min_ranks_ties_and_missing():
    from app.analysis.definitions import min_ranks

    pairs = [(1, 100.0), (2, 98.0), (3, 98.0), (4, 95.0), (5, None)]
    ranks = min_ranks(pairs)
    assert [ranks[1], ranks[2], ranks[3], ranks[4], ranks[5]] == [1, 2, 2, 4, None]


def test_bins_missing_values_never_binned():
    from app.analysis.definitions import grade_score_bin, percentile_bin, rank_bin

    assert percentile_bin(None) is None  # 缺考/缺失不入百分位箱
    assert percentile_bin(0.15) == "p0_20"
    assert percentile_bin(15) == "p0_20"  # 百分数形态归一
    assert rank_bin(None) is None
    assert rank_bin(0) is None
    assert rank_bin(41) == "r41_80"
    assert grade_score_bin(None) is None
    assert grade_score_bin(66) is None  # 非标准档位不入箱
    assert grade_score_bin(67) == "g67"


def test_metric_options_identical_between_paths():
    """排名指标选项：旧 /api 与新 /api/v1 必须同一份（共享定义）。"""
    from app.analysis.rank_metrics import rank_metric_options
    from app.api.analysis import _rank_metric_options

    for grade in (1, 2, 3):
        for mode in ("frequency", "range"):
            assert rank_metric_options(grade, mode) == _rank_metric_options(grade, mode)


def test_compute_student_trend_unified_criteria(db_session):
    """旧 chat 工具底层 compute_student_trend 与新路径 focus 同口径：
    相邻两场进退步（±80）、极差波动（≥120 且 ≥3 场）、缺考场不入序列。"""
    from app.analysis.trends import compute_student_trend
    from app.db.models import Exam, TotalScore

    exam_ids = []
    for name in ("2026趋势一", "2026趋势二", "2026趋势三"):
        exam = Exam(name=name, grade=2, semester="上", exam_date="2026-04-01", exam_type="月考")
        db_session.add(exam)
        db_session.flush()
        exam_ids.append(exam.id)
    # 秦庚：相邻差 10、极差 80 → 正常波动（改动前「首末差 80」会误判明显进步）
    for exam_id, rank in zip(exam_ids, (100, 30, 20)):
        db_session.add(TotalScore(exam_id=exam_id, student_id="syn-trend-g",
                                  total_type=TOTAL_TYPE_MAIN3, total_score=240.0,
                                  xueji_rank=rank))
    # 秦辛：相邻退 120、极差 120 → 波动优先于进退步
    for exam_id, rank in zip(exam_ids, (100, 100, 220)):
        db_session.add(TotalScore(exam_id=exam_id, student_id="syn-trend-x",
                                  total_type=TOTAL_TYPE_MAIN3, total_score=200.0,
                                  xueji_rank=rank))
    # 秦壬：中间场缺考（无名次行）→ 不入序列，相邻两场 100→20 差 80 判进步
    for exam_id, rank in ((exam_ids[0], 100), (exam_ids[2], 20)):
        db_session.add(TotalScore(exam_id=exam_id, student_id="syn-trend-r",
                                  total_type=TOTAL_TYPE_MAIN3, total_score=230.0,
                                  xueji_rank=rank))
    db_session.commit()

    g = compute_student_trend("syn-trend-g", TOTAL_TYPE_MAIN3, exam_ids, db_session)
    assert g["trend_label"] == "正常波动"
    assert g["rank_change"] == 10
    assert g["volatility"] == 80

    x = compute_student_trend("syn-trend-x", TOTAL_TYPE_MAIN3, exam_ids, db_session)
    assert x["trend_label"] == "波动较大"  # 波动优先，不因退步 120 双重报退步
    assert x["rank_change"] == -120
    assert x["volatility"] == 120

    r = compute_student_trend("syn-trend-r", TOTAL_TYPE_MAIN3, exam_ids, db_session)
    assert r["trend_label"] == "明显进步"
    assert [exam_id for exam_id, _rank in r["ranks"]] == [exam_ids[0], exam_ids[2]]


# ────────────────── 2. 新路径：名次缺失语义（rank-range） ──────────────────


def test_rank_range_subject_metric_requires_real_rank(client, v1_seed, db_session):
    """单科 rank-range（高二语数英走百分位指标）：无真实年级名次 →
    名次不可得，不进名单、不按百分位推算；写入真实 grade_rank 后才可筛。"""
    from app.db import workspace_models as wm

    def chinese_fact(person_id):
        return (
            db_session.query(wm.ScoreFact)
            .filter(
                wm.ScoreFact.data_domain == "homeroom",
                wm.ScoreFact.exam_name == EXAM_E1,
                wm.ScoreFact.identity_id == person_id,
                wm.ScoreFact.subject == "语文",
                wm.ScoreFact.total_type.is_(None),
            )
            .one()
        )

    # 百分位仍在（可靠信号保留），但没有真实名次
    fact_yi = chinese_fact(v1_seed.yi_h_id)
    fact_yi.grade_percentile = 0.55
    fact_yi.grade_rank = None
    db_session.commit()

    params = {"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id}
    response = client.get(
        f"{API}/homeroom/analysis/exams/{EXAM_E1}/rank-range",
        params={**params, "metric": "subject:语文", "rank_min": 1, "rank_max": 10},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # 只有百分位 → 不允许推算名次，名单为空且说明文案标注缺失语义
    assert body["students"] == []
    assert "不按百分位推算名次" in body["metric_note"]

    # 写入真实年级名次 → 该生进入名单，year_rank 用真实值
    fact_yi.grade_rank = 5
    db_session.commit()
    response = client.get(
        f"{API}/homeroom/analysis/exams/{EXAM_E1}/rank-range",
        params={**params, "metric": "subject:语文", "rank_min": 1, "rank_max": 10},
    )
    assert response.status_code == 200, response.text
    students = response.json()["students"]
    assert [row["person_id"] for row in students] == [v1_seed.yi_h_id]
    assert students[0]["year_rank"] == 5
    assert students[0]["score"] == 76.0  # 班内 min-rank 分母里的真实分


def test_rank_range_total_metric_uses_real_rank_only(client, v1_seed, db_session):
    """总分 rank-range：学籍名次优先；无名次的学生即使百分位很高也不出现。"""
    from app.db import workspace_models as wm

    totals = {
        fact.identity_id: fact
        for fact in db_session.query(wm.ScoreFact)
        .filter(
            wm.ScoreFact.data_domain == "homeroom",
            wm.ScoreFact.exam_name == EXAM_E1,
            wm.ScoreFact.total_type == TOTAL_TYPE_MAIN3,
        )
        .all()
    }
    totals[v1_seed.jia_h_id].xueji_rank = 5
    totals[v1_seed.yi_h_id].grade_percentile = 0.95  # 高百分位、无名次
    totals[v1_seed.yi_h_id].grade_rank = None
    totals[v1_seed.bing_h_id].grade_rank = 8
    db_session.commit()

    response = client.get(
        f"{API}/homeroom/analysis/exams/{EXAM_E1}/rank-range",
        params={
            "academic_year_id": v1_seed.ay_id,
            "class_id": v1_seed.h6_id,
            "metric": f"total:{TOTAL_TYPE_MAIN3}",
            "rank_min": 1,
            "rank_max": 10,
        },
    )
    assert response.status_code == 200, response.text
    students = response.json()["students"]
    # 甲走学籍名次 5；丙走年级名次 8；乙只有百分位 → 名次不可得，不进名单
    assert [(row["person_id"], row["year_rank"]) for row in students] == [
        (v1_seed.jia_h_id, 5),
        (v1_seed.bing_h_id, 8),
    ]


# ────────────────── 3. 新路径：缺考不伪造连续性 ──────────────────


def test_trends_absent_exam_point_not_fabricated(client, v1_seed, db_session):
    """缺考（有行无分、无任何名次百分位）：趋势点如实陈列
    score=null / rank=null / rank_basis=null，绝不残留上一场的值。"""
    from app.db import workspace_models as wm

    db_session.add(
        wm.ScoreFact(
            data_domain="homeroom",
            academic_year_id=v1_seed.ay_id,
            exam_name="2025期末",
            exam_date=date(2025, 12, 20),
            class_ref_id=v1_seed.h6_id,
            identity_id=v1_seed.jia_h_id,
            subject="总分",
            total_type=TOTAL_TYPE_MAIN3,
            score=None,  # 缺考：NULL，绝不转 0
            xueji_rank=None,
            grade_rank=None,
            grade_percentile=None,
            source="synthetic-test",
        )
    )
    # 上一场（期中）给甲一个真实名次，确保「不残留」可断言
    main3 = (
        db_session.query(wm.ScoreFact)
        .filter(
            wm.ScoreFact.data_domain == "homeroom",
            wm.ScoreFact.exam_name == EXAM_E1,
            wm.ScoreFact.identity_id == v1_seed.jia_h_id,
            wm.ScoreFact.total_type == TOTAL_TYPE_MAIN3,
        )
        .one()
    )
    main3.xueji_rank = 12
    db_session.commit()

    response = client.get(
        f"{API}/homeroom/analysis/trends",
        params={"person_id": v1_seed.jia_h_id, "academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
    )
    assert response.status_code == 200, response.text
    year = next(
        y for y in response.json()["years"] if y["academic_year_id"] == v1_seed.ay_id
    )
    points = {p["exam_name"]: p for p in year["totals"][TOTAL_TYPE_MAIN3]}
    assert points[EXAM_E1]["rank"] == 12
    assert points[EXAM_E1]["rank_basis"] == "school"
    # 缺考场：分数与名次都如实缺失，rank_basis 为 null（不是 school）
    assert points["2025期末"]["score"] is None
    assert points["2025期末"]["rank"] is None
    assert points["2025期末"]["rank_basis"] is None


def test_rank_frequency_skips_missing_percentile(client, v1_seed, db_session):
    """百分位缺失（缺考形态）的行不入任何分箱，不残留、不按 0 落箱。"""
    from app.db import workspace_models as wm

    for person_id, percentile in (
        (v1_seed.jia_h_id, 0.15),
        (v1_seed.yi_h_id, 0.55),
        (v1_seed.bing_h_id, None),  # 丙：百分位缺失
    ):
        fact = (
            db_session.query(wm.ScoreFact)
            .filter(
                wm.ScoreFact.data_domain == "homeroom",
                wm.ScoreFact.exam_name == EXAM_E1,
                wm.ScoreFact.identity_id == person_id,
                wm.ScoreFact.subject == "语文",
                wm.ScoreFact.total_type.is_(None),
            )
            .one()
        )
        fact.grade_percentile = percentile
    db_session.commit()

    response = client.get(
        f"{API}/homeroom/analysis/rank-frequency",
        params={
            "academic_year_id": v1_seed.ay_id,
            "class_id": v1_seed.h6_id,
            "metric": "subject:语文",
            "exam_names": EXAM_E1,
        },
    )
    assert response.status_code == 200, response.text
    by_person = {row["person_id"]: row for row in response.json()["students"]}
    assert by_person[v1_seed.jia_h_id]["counts"]["p0_20"] == 1
    assert by_person[v1_seed.yi_h_id]["counts"]["p40_60"] == 1  # 0.55 ∈ (0.4, 0.6]
    assert by_person[v1_seed.jia_h_id]["counts"]["p80_100"] == 0
    # 丙缺失百分位：不入后20% 箱，也没有任何计数
    assert v1_seed.bing_h_id not in by_person


# ────────────────── 4. 旧路径：哨兵名次移除与缺名次不落段 ──────────────────


@pytest.fixture()
def old_exam(db_session):
    """旧库合成考试：秦戊（学籍名次 450，临界段）、秦丁（缺名次、偏科）。"""
    from app.db.models import Exam, SubjectScore, TotalScore

    exam = Exam(name="2026合成月考", grade=1, semester="上", exam_date="2026-03-10", exam_type="月考")
    db_session.add(exam)
    db_session.flush()

    # 秦戊：名次 450 → 临界段；物理百分位差 0.25 → 严重偏科
    db_session.add(
        TotalScore(exam_id=exam.id, student_id="syn-oldd-1", total_type=TOTAL_TYPE_MAIN3,
                   total_score=250.0, grade_percentile=0.30, xueji_rank=450)
    )
    db_session.add(SubjectScore(exam_id=exam.id, student_id="syn-oldd-1", class_num=6,
                                name="秦戊", subject=SUBJECT, raw_score=88.0, grade_percentile=0.55))
    # 秦丁：完全无学籍/年级名次（缺考或未导入名次）；有百分位 → 只应报偏科
    db_session.add(
        TotalScore(exam_id=exam.id, student_id="syn-oldd-2", total_type=TOTAL_TYPE_MAIN3,
                   total_score=180.0, grade_percentile=0.30, xueji_rank=None, grade_rank=None)
    )
    db_session.add(SubjectScore(exam_id=exam.id, student_id="syn-oldd-2", class_num=6,
                                name="秦丁", subject=SUBJECT, raw_score=82.0, grade_percentile=0.55))
    db_session.commit()
    return exam.id


def test_old_focus_list_missing_rank_never_weak_band(client, old_exam):
    """旧 /api/focus-list：缺名次学生不再按 9999 哨兵落入薄弱段；
    名次不可得 → xueji_rank=null，仅按偏科入列。"""
    response = client.get(f"/api/focus-list/{old_exam}")
    assert response.status_code == 200, response.text
    rows = {row["student_id"]: row for row in response.json()["focus_list"]}
    assert rows["syn-oldd-1"]["issues"] == ["临界段", f"严重偏科({SUBJECT})"]
    assert rows["syn-oldd-1"]["xueji_rank"] == 450
    # 关键回归：改动前 rank or 9999 → 薄弱段；现在名次不可得 → 不落段
    assert rows["syn-oldd-2"]["issues"] == [f"严重偏科({SUBJECT})"]
    assert rows["syn-oldd-2"]["xueji_rank"] is None
    # 名次不可得者排在有名次者之后
    ids = [row["student_id"] for row in response.json()["focus_list"]]
    assert ids.index("syn-oldd-1") < ids.index("syn-oldd-2")


def test_old_rank_range_real_rank_only(client, old_exam):
    """旧 /api/rank-range：总分按真实名次筛选；单科旧库无名次列 →
    名单为空 + 说明文案（不按百分位推算）。"""
    response = client.get(
        f"/api/rank-range?exam_id={old_exam}&metric=total:{TOTAL_TYPE_MAIN3}&rank_min=1&rank_max=10"
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["rows"] == []  # 唯一名次 450 不在 1–10 区间

    response = client.get(
        f"/api/rank-range?exam_id={old_exam}&metric=total:{TOTAL_TYPE_MAIN3}&rank_min=400&rank_max=500"
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert [row["student_id"] for row in data["rows"]] == ["syn-oldd-1"]
    assert data["rows"][0]["year_rank"] == 450

    response = client.get(
        f"/api/rank-range?exam_id={old_exam}&metric=subject:{SUBJECT}&rank_min=1&rank_max=10"
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["rows"] == []  # 单科仅有百分位，名次不可得 → 不推算、不进名单
    assert "不按百分位推算名次" in data["metric_note"]


def test_old_band_trend_missing_rank_not_counted(client, old_exam):
    """旧 /api/band-trend：缺名次学生不落薄弱段（不按哨兵计数）。"""
    response = client.get("/api/band-trend?grade=1")
    assert response.status_code == 200, response.text
    series = {row["exam_id"]: row for row in response.json()["series"]}
    row = series[old_exam]
    assert row["high_score"] == 0
    assert row["critical"] == 1  # 秦戊 450
    assert row["weak"] == 0  # 秦丁缺名次：不计入薄弱段
