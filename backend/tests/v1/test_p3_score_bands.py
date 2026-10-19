"""P3 教学分数段分析（/teaching/analysis/exams/{exam}/score-bands）。

官方《分数段分析》表同构的本班口径，2026-10-18 新增。在 v1_seed 之上
ORM 直种，覆盖：
- 段口左闭右开：90 入顶段 [90,+∞)、89.5 入 [80,90)、0 入 [0,10)
  （0 分计入段与均分，E02）；累计自高分段向下、比例分母=valid_count；
- 缺考 NULL 不进任何段与计量，单独 missing_count；
- 汇总行：人数/最高/最低/均分/总体标准差 σ；
- 段宽 5/10/20 变体与非法段宽 422；
- 作用域：缺省=全部所教班（含 T-empty 合法空态），单班时其余班不可见、
  merged=该班自身；H 域反向投影行参与段统计（F09）；
- 越界考试 404。
"""

from datetime import date
from urllib.parse import quote

import pytest

API = "/api/v1"

EXAM_BANDS = "2025分数段考"  # T6: 甲95 乙90 丁NULL；T8: 戊89.5 己0
EXAM_PROJ_BANDS = "2025段投影考"  # 仅 H 域甲 95（反向投影）
EXAM_MISSING = "2025没有段"


@pytest.fixture(scope="module")
def bands_seed(v1_seed):
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
        f.source = "p3-score-bands-test"
        db.add(f)

    fact("teaching", s.t6_id, s.jia_t_id, EXAM_BANDS, "2025-11-01", 95.0)
    fact("teaching", s.t6_id, s.yi_t_id, EXAM_BANDS, "2025-11-01", 90.0)
    fact("teaching", s.t6_id, s.ding_t_id, EXAM_BANDS, "2025-11-01", None)
    fact("teaching", s.t8_id, s.wu_t_id, EXAM_BANDS, "2025-11-01", 89.5)
    fact("teaching", s.t8_id, s.ji_t_id, EXAM_BANDS, "2025-11-01", 0.0)

    fact("homeroom", s.h6_id, s.jia_h_id, EXAM_PROJ_BANDS, "2025-11-10", 95.0)

    db.commit()
    yield
    db.close()


def _bands(client, exam, **params):
    r = client.get(
        f"{API}/teaching/analysis/exams/{quote(exam)}/score-bands", params=params
    )
    assert r.status_code == 200, r.text
    return r.json()


def _band(col, low):
    """按段下界取段行（顶段 low=90 等）。"""
    return next(b for b in col["bands"] if b["low"] == low)


def test_score_bands_edges_cumulative_and_summary(client, bands_seed, v1_seed):
    s = v1_seed
    body = _bands(client, EXAM_BANDS, academic_year_id=s.ay_id)
    assert body["subject"] == "物理"
    assert body["band_width"] == 10 and body["score_basis"] == "raw"
    assert body["small_sample"] is True  # 各列 valid_count 均 <5

    by_id = {c["teaching_class_id"]: c for c in body["classes"]}
    assert set(by_id) == {s.t6_id, s.t8_id, s.t_empty_id}

    t6 = by_id[s.t6_id]
    assert t6["class_label"] == "高二6班(教)"
    # 左闭右开：95 与 90 都入顶段 [90,+∞)
    top = _band(t6, 90)
    assert top["high"] is None and top["count"] == 2
    assert top["cumulative"] == 2 and top["cumulative_pct"] == 100.0
    assert all(b["count"] == 0 and b["cumulative"] == 2 for b in t6["bands"] if b["low"] != 90)
    assert t6["valid_count"] == 2 and t6["missing_count"] == 1  # 丁 NULL 不进段
    assert t6["max"] == 95.0 and t6["min"] == 90.0 and t6["avg"] == 92.5
    assert t6["stddev"] == 2.5  # 总体标准差 σ（非样本 s）

    t8 = by_id[s.t8_id]
    assert _band(t8, 90)["count"] == 0
    # 89.5 入 [80,90)（左闭右开）；0 入 [0,10) 且计入均分（E02）
    b80 = _band(t8, 80)
    assert b80["high"] == 90.0 and b80["count"] == 1
    assert b80["cumulative"] == 1 and b80["cumulative_pct"] == 50.0
    b0 = _band(t8, 0)
    assert b0["high"] == 10.0 and b0["count"] == 1
    assert b0["cumulative"] == 2 and b0["cumulative_pct"] == 100.0
    assert t8["valid_count"] == 2 and t8["missing_count"] == 0
    assert t8["max"] == 89.5 and t8["min"] == 0.0 and t8["avg"] == 44.75
    assert t8["stddev"] == 44.75
    # 段计数总和 = 有效人数
    assert sum(b["count"] for b in t8["bands"]) == t8["valid_count"]

    # 空教学班为合法空态：无段计数、比例 None、不编造计量
    empty = by_id[s.t_empty_id]
    assert empty["valid_count"] == 0 and all(b["count"] == 0 for b in empty["bands"])
    assert all(b["cumulative_pct"] is None for b in empty["bands"])
    assert empty["avg"] is None and empty["stddev"] is None

    # merged（当前范围合并）：顶段 2 人 50%，[80,90) 后 75%，[0,10) 后 100%
    merged = body["merged"]
    assert merged["teaching_class_id"] is None
    assert merged["valid_count"] == 4 and merged["missing_count"] == 1
    assert _band(merged, 90)["cumulative_pct"] == 50.0
    assert _band(merged, 80)["cumulative_pct"] == 75.0
    assert _band(merged, 0)["cumulative_pct"] == 100.0
    assert abs(merged["avg"] - 68.63) < 0.01  # (95+90+89.5+0)/4


def test_score_bands_width_variants(client, bands_seed, v1_seed):
    s = v1_seed
    body5 = _bands(client, EXAM_BANDS, academic_year_id=s.ay_id, band_width=5)
    t6 = next(c for c in body5["classes"] if c["teaching_class_id"] == s.t6_id)
    # 顶段下界 = 95；甲 95 入顶段、乙 90 入 [90,95)
    top = _band(t6, 95)
    assert top["high"] is None and top["count"] == 1
    assert _band(t6, 90)["count"] == 1 and _band(t6, 90)["high"] == 95.0
    t8 = next(c for c in body5["classes"] if c["teaching_class_id"] == s.t8_id)
    assert _band(t8, 85)["count"] == 1  # 89.5 入 [85,90)
    assert _band(t8, 0)["count"] == 1 and _band(t8, 0)["high"] == 5.0  # 0 入 [0,5)

    body20 = _bands(client, EXAM_BANDS, academic_year_id=s.ay_id, band_width=20)
    t6_20 = next(c for c in body20["classes"] if c["teaching_class_id"] == s.t6_id)
    top20 = _band(t6_20, 80)
    assert top20["high"] is None and top20["count"] == 2  # 95/90 均入 [80,+∞)
    assert [b["low"] for b in t6_20["bands"]] == [80.0, 60.0, 40.0, 20.0, 0.0]


def test_score_bands_invalid_width_422(client, bands_seed, v1_seed):
    s = v1_seed
    r = client.get(
        f"{API}/teaching/analysis/exams/{quote(EXAM_BANDS)}/score-bands",
        params={"academic_year_id": s.ay_id, "band_width": 7},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_scope_param"


def test_score_bands_single_class_scope_and_merged(client, bands_seed, v1_seed):
    s = v1_seed
    body = _bands(
        client, EXAM_BANDS, academic_year_id=s.ay_id, teaching_class_id=s.t6_id
    )
    # 指定单班：T8/T-empty 不可见；merged 即该班自身
    assert [c["teaching_class_id"] for c in body["classes"]] == [s.t6_id]
    assert body["merged"]["valid_count"] == 2
    assert _band(body["merged"], 90)["count"] == 2
    assert body["metadata"]["scope"]["teaching_class_id"] == s.t6_id


def test_score_bands_reverse_projection_counts(client, bands_seed, v1_seed):
    """2026-10 两域成绩不互通：仅 H 域有甲的行不再投影进教学分数段——
    T6 该场为空态（valid_count=0、各段 0 人、比例 null）。"""
    s = v1_seed
    body = _bands(
        client, EXAM_PROJ_BANDS, academic_year_id=s.ay_id, teaching_class_id=s.t6_id
    )
    t6 = body["classes"][0]
    # 2026-10 两域成绩不互通：H 域 95 分不进 T6 段统计分母
    assert t6["valid_count"] == 0 and t6["max"] is None and t6["avg"] is None
    assert all(band["count"] == 0 for band in t6["bands"])
    assert _band(t6, 90)["count"] == 0
    assert _band(t6, 90)["cumulative_pct"] is None  # 0 人分母 → 比例 null


def test_score_bands_unknown_exam_404(client, bands_seed, v1_seed):
    s = v1_seed
    r = client.get(
        f"{API}/teaching/analysis/exams/{quote(EXAM_MISSING)}/score-bands",
        params={"academic_year_id": s.ay_id},
    )
    assert r.status_code == 404
    assert r.json()["error"] == "resource_out_of_scope"
