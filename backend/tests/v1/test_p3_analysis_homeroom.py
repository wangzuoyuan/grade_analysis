"""P3 分析端点——homeroom 域（契约 docs/contracts/p3-imports-analysis.md §2.1 + §2.3）。

在 tests/v1/conftest.py 合成样本（v1_seed）之上 ORM 直种分析场景，
不依赖导入链路。覆盖验收：
- E01：同一组分数两域端点读到一致值；teaching 响应无 total_type/其他学科键。
- E02：0 分计入均分与最值，NULL 计 missing_count 不进分母。
- E03：trends 按学年分组、响应无跨年同比字段；越界 person 404。
- bands（v2.1/F10）：阈值口径为年级名次、域内无名次事实 → 一律 409。
- 空态：他班考试（域内存在、本班无行）200 空态；域内不存在考试 404。
- 冲突标注：linked 学生任教学科两域同场值不同 → shared_conflicts（E1 样本）。
"""

from datetime import date
from types import SimpleNamespace
from urllib.parse import quote

import pytest

API = "/api/v1"

# 场景考试名（v1_seed 的 2025期中 之外由本模块补种）
EXAM_BASE = "2025期中"  # v1_seed 原样：两域物理值故意不同（90/91、84/85）
EXAM_SAME = "2025期末"  # E01：两域同值样本
EXAM_ZERO_NULL = "2025月考1"  # E02：0 与 NULL 并存
EXAM_BANDS = "2025段位考"  # bands：一律 409（v2.1/F10，无名次事实）
EXAM_OLD = "2024期末"  # E03：旧学年事实
EXAM_OTHER_CLASS = "2025他班考"  # 空态：H9 的考试，本班无行
EXAM_MISSING = "2025不存在"  # 404：域内完全不存在


def _stats(client, exam, **params):
    r = client.get(f"{API}/homeroom/analysis/exams/{quote(exam)}/stats", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _students(client, exam, **params):
    r = client.get(f"{API}/homeroom/analysis/exams/{quote(exam)}/students", params=params)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def p3_seed(v1_seed):
    """在 v1_seed 基础上补种分析场景（ORM 直种，不走导入链路）。"""
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    s = v1_seed
    db = SessionLocal()

    def fact(domain, ay_id, cls_id, ident_id, exam, exam_date_iso, subject, total_type, score):
        f = wm.ScoreFact()
        f.data_domain = domain
        f.academic_year_id = ay_id
        f.exam_name = exam
        f.exam_date = date.fromisoformat(exam_date_iso)
        f.class_ref_id = cls_id
        f.identity_id = ident_id
        f.subject = subject
        f.total_type = total_type
        f.score = score
        f.source = "p3-analysis-test"
        db.add(f)
        return f

    # E01 同值样本：甲乙（linked）物理两域同值；丙仅 H 域且本场无行；丁 T 域缺考
    fact("homeroom", s.ay_id, s.h6_id, s.jia_h_id, EXAM_SAME, "2025-12-20", "物理", None, 88.0)
    fact("homeroom", s.ay_id, s.h6_id, s.yi_h_id, EXAM_SAME, "2025-12-20", "物理", None, 72.0)
    fact("teaching", s.ay_id, s.t6_id, s.jia_t_id, EXAM_SAME, "2025-12-20", "物理", None, 88.0)
    fact("teaching", s.ay_id, s.t6_id, s.yi_t_id, EXAM_SAME, "2025-12-20", "物理", None, 72.0)
    fact("teaching", s.ay_id, s.t6_id, s.ding_t_id, EXAM_SAME, "2025-12-20", "物理", None, None)

    # E02：0 分（甲）与 NULL（丙）并存，0 计入计量、NULL 不进分母
    fact("homeroom", s.ay_id, s.h6_id, s.jia_h_id, EXAM_ZERO_NULL, "2025-10-15", "物理", None, 0.0)
    fact("homeroom", s.ay_id, s.h6_id, s.yi_h_id, EXAM_ZERO_NULL, "2025-10-15", "物理", None, 80.0)
    fact("homeroom", s.ay_id, s.h6_id, s.bing_h_id, EXAM_ZERO_NULL, "2025-10-15", "物理", None, None)

    # bands：单科（含 NULL）+ 两种 total_type（跨段值与 NULL）
    fact("homeroom", s.ay_id, s.h6_id, s.jia_h_id, EXAM_BANDS, "2025-12-01", "物理", None, 90.0)
    fact("homeroom", s.ay_id, s.h6_id, s.yi_h_id, EXAM_BANDS, "2025-12-01", "物理", None, None)
    fact("homeroom", s.ay_id, s.h6_id, s.bing_h_id, EXAM_BANDS, "2025-12-01", "物理", None, 85.0)
    fact("homeroom", s.ay_id, s.h6_id, s.jia_h_id, EXAM_BANDS, "2025-12-01", None, "主三门", 550.0)
    fact("homeroom", s.ay_id, s.h6_id, s.yi_h_id, EXAM_BANDS, "2025-12-01", None, "主三门", 450.0)
    fact("homeroom", s.ay_id, s.h6_id, s.bing_h_id, EXAM_BANDS, "2025-12-01", None, "主三门", 380.0)
    fact("homeroom", s.ay_id, s.h6_id, s.jia_h_id, EXAM_BANDS, "2025-12-01", None, "九科", 700.0)
    fact("homeroom", s.ay_id, s.h6_id, s.yi_h_id, EXAM_BANDS, "2025-12-01", None, "九科", 300.0)
    fact("homeroom", s.ay_id, s.h6_id, s.bing_h_id, EXAM_BANDS, "2025-12-01", None, "九科", None)

    # E03：旧学年 + 旧行政班下的甲的历史事实（trends 按学年分组）
    ay2 = wm.AcademicYear(name="2024-2025", start_date=date(2024, 9, 1), end_date=date(2025, 7, 15))
    db.add(ay2)
    db.flush()
    old_cls = wm.AdministrativeClass(
        academic_year_id=ay2.id, grade=1, class_num=6, label="高一6班"
    )
    db.add(old_cls)
    db.flush()
    old_physics = fact("homeroom", ay2.id, old_cls.id, s.jia_h_id, EXAM_OLD, "2025-01-10", "物理", None, 78.0)
    old_physics.grade_percentile = 0.12
    fact("homeroom", ay2.id, old_cls.id, s.jia_h_id, EXAM_OLD, "2025-01-10", "语文", None, 88.0)

    # 空态样本：H9（非绑定班）的考试——homeroom 域内存在、本班（H6）无行
    geng = wm.WsStudentIdentity(data_domain="homeroom", display_name="秦庚")
    db.add(geng)
    db.flush()
    db.add(
        wm.Enrollment(
            admin_class_id=s.h9_id,
            identity_id=geng.id,
            status="active",
            valid_from=date(2025, 9, 1),
            seat_no=1,
        )
    )
    fact("homeroom", s.ay_id, s.h9_id, geng.id, EXAM_OTHER_CLASS, "2025-11-20", "语文", None, 60.0)

    db.commit()
    yield SimpleNamespace(ay2_id=ay2.id, geng_id=geng.id)
    db.close()


# ────────────────────────────── E01 一致性 ──────────────────────────────


def test_e01_stats_consistent_across_domains(client, p3_seed, v1_seed):
    s = v1_seed
    h = _stats(client, EXAM_SAME, academic_year_id=s.ay_id, class_id=s.h6_id)
    physics = [row for row in h["subjects"] if row["subject"] == "物理"]
    assert len(physics) == 1
    row = physics[0]
    assert row["avg"] == 80.0 and row["max"] == 88.0 and row["min"] == 72.0
    assert row["valid_count"] == 2 and row["missing_count"] == 0
    assert row["score_basis"] == "raw"

    r = client.get(
        f"{API}/teaching/analysis/exams/{quote(EXAM_SAME)}/stats",
        params={"academic_year_id": s.ay_id, "teaching_class_id": s.t6_id},
    )
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["subject"] == "物理"
    # 同一组分数两域读到一致值（E01）
    assert t["avg"] == row["avg"] and t["max"] == row["max"] and t["min"] == row["min"]
    assert t["valid_count"] == row["valid_count"]
    # 成员口径差异如实体现：T6 多一名缺考丁（NULL）
    assert t["missing_count"] == 1
    assert t["rank_min"] == 1 and t["rank_max"] == 2
    assert t["cohort_size"] == 3 and t["score_basis"] == "raw"


def test_e01_students_consistent_and_teaching_keys_clean(client, p3_seed, v1_seed):
    s = v1_seed
    h = _students(client, EXAM_SAME, academic_year_id=s.ay_id, class_id=s.h6_id)
    by_id = {row["person_id"]: row for row in h["students"]}
    assert by_id[s.jia_h_id]["scores"]["物理"] == 88.0
    assert by_id[s.yi_h_id]["scores"]["物理"] == 72.0
    # 丙本场无行：合法 null（显示"—"），不编造
    assert by_id[s.bing_h_id]["scores"]["物理"] is None

    r = client.get(
        f"{API}/teaching/analysis/exams/{quote(EXAM_SAME)}/students",
        params={"academic_year_id": s.ay_id, "teaching_class_id": s.t6_id},
    )
    assert r.status_code == 200, r.text
    t = r.json()
    t_by_id = {row["person_id"]: row for row in t["students"]}
    assert t_by_id[s.jia_t_id]["score"] == by_id[s.jia_h_id]["scores"]["物理"]
    assert t_by_id[s.yi_t_id]["score"] == by_id[s.yi_h_id]["scores"]["物理"]
    # E01：teaching 行结构无 total_type/subject 键（键本身缺席）
    for row in t["students"]:
        assert "total_type" not in row and "subject" not in row
        assert set(row) <= {
            "person_id", "name", "class_label", "score", "grade_score", "rank", "source_domain",
        }
    # stats 响应同样无 totals/subjects（多科）键
    stats = client.get(
        f"{API}/teaching/analysis/exams/{quote(EXAM_SAME)}/stats",
        params={"academic_year_id": s.ay_id, "teaching_class_id": s.t6_id},
    ).json()
    assert "totals" not in stats and "subjects" not in stats


# ────────────────────────────── E02 0/NULL 红线 ──────────────────────────────


def test_e02_zero_counts_null_separated(client, p3_seed, v1_seed):
    s = v1_seed
    h = _stats(client, EXAM_ZERO_NULL, academic_year_id=s.ay_id, class_id=s.h6_id)
    row = next(r for r in h["subjects"] if r["subject"] == "物理")
    # 0 计入均分与最值；NULL 只计 missing_count，不进分母、绝不转 0
    assert row["avg"] == 40.0
    assert row["max"] == 80.0 and row["min"] == 0.0
    assert row["valid_count"] == 2 and row["missing_count"] == 1

    st = _students(client, EXAM_ZERO_NULL, academic_year_id=s.ay_id, class_id=s.h6_id)
    by_id = {row["person_id"]: row for row in st["students"]}
    assert by_id[s.jia_h_id]["scores"]["物理"] == 0.0  # 0 分保持 0
    assert by_id[s.bing_h_id]["scores"]["物理"] is None  # 缺考保持 null


# ────────────────────────────── 冲突标注（§1.4.1 复用门） ──────────────────────────────


def test_homeroom_students_conflict_flagged(client, p3_seed, v1_seed):
    """E1 样本：甲乙物理两域值不同（90/91、84/85）→ shared_conflicts
    提示待人工核对；丙非 linked 学生无提示。"""
    s = v1_seed
    h = _students(client, EXAM_BASE, academic_year_id=s.ay_id, class_id=s.h6_id)
    by_id = {row["person_id"]: row for row in h["students"]}
    # H 域本域值保留
    assert by_id[s.jia_h_id]["scores"]["物理"] == 90.0
    assert by_id[s.jia_h_id]["shared_conflicts"] == {"teaching_score": 91.0}
    assert by_id[s.yi_h_id]["scores"]["物理"] == 84.0
    assert by_id[s.yi_h_id]["shared_conflicts"] == {"teaching_score": 85.0}
    assert by_id[s.bing_h_id]["shared_conflicts"] is None
    # 全科 + 总分齐全（E1 样本四科 + 主三门）
    assert by_id[s.jia_h_id]["totals"]["主三门"] == 275.0
    assert set(by_id[s.jia_h_id]["scores"]) == {"语文", "数学", "英语", "物理"}


def test_homeroom_stats_e1_full(client, p3_seed, v1_seed):
    s = v1_seed
    h = _stats(client, EXAM_BASE, academic_year_id=s.ay_id, class_id=s.h6_id)
    subjects = {row["subject"]: row for row in h["subjects"]}
    assert subjects["语文"]["avg"] == 76.33
    assert subjects["数学"]["avg"] == 81.0
    assert subjects["英语"]["avg"] == 82.0
    assert subjects["物理"]["avg"] == 80.67
    assert all(row["valid_count"] == 3 for row in subjects.values())
    assert h["totals"][0]["total_type"] == "主三门"
    assert h["totals"][0]["avg"] == 239.33
    assert h["totals"][0]["valid_count"] == 3
    assert h["cohort_size"] == 3
    assert h["small_sample"] is True  # 所有行 valid_count < 5
    meta = h["metadata"]
    assert meta["mode"] == "homeroom" and meta["scope"]["class_id"] == s.h6_id


def test_homeroom_scope_defaults_to_bound_class(client, p3_seed, v1_seed):
    """class_id 缺省 → 教师绑定班（H6），作用域由后端解析。"""
    h = _stats(client, EXAM_BASE, academic_year_id=v1_seed.ay_id)
    assert h["metadata"]["scope"]["class_id"] == v1_seed.h6_id
    assert len(h["subjects"]) == 4


# ────────────────────────────── E03 跨学年趋势 ──────────────────────────────


def test_e03_trends_grouped_by_year_without_cross_year_fields(client, p3_seed, v1_seed):
    s = v1_seed
    r = client.get(
        f"{API}/homeroom/analysis/trends",
        params={"person_id": s.jia_h_id, "academic_year_id": s.ay_id, "class_id": s.h6_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 结构红线：无任何跨学年同比字段（顶层仅 person_id/name/years）
    assert set(body) == {"person_id", "name", "years"}
    years = body["years"]
    assert {y["academic_year_id"] for y in years} == {s.ay_id, p3_seed.ay2_id}
    # 按学年 start_date 升序（时间线）
    assert [y["academic_year_id"] for y in years] == [p3_seed.ay2_id, s.ay_id]
    for y in years:
        assert set(y) == {"academic_year_id", "academic_year_name", "subjects", "totals"}
    old = years[0]
    assert old["academic_year_name"] == "2024-2025"
    assert [(p["exam_name"], p["score"]) for p in old["subjects"]["物理"]] == [(EXAM_OLD, 78.0)]
    assert [(p["exam_name"], p["score"]) for p in old["subjects"]["语文"]] == [(EXAM_OLD, 88.0)]
    # 当前学年组：E1 的 H 域 90 保留（T 域 91 同场值不同不投影不覆盖）
    current = years[1]
    e1_point = next(p for p in current["subjects"]["物理"] if p["exam_name"] == EXAM_BASE)
    assert e1_point["score"] == 90.0
    same_point = next(p for p in current["subjects"]["物理"] if p["exam_name"] == EXAM_SAME)
    assert same_point["score"] == 88.0
    # 趋势点保留原始分数供详情，图表改用来源中的年级排名百分位；
    # 缺排名就保持 null，不从绝对分数推算。
    assert set(e1_point) == {"exam_name", "exam_date", "score", "grade_score", "rank", "rank_basis"}
    assert e1_point["rank"] is None
    old_point = old["subjects"]["物理"][0]
    assert old_point["rank"] == 12.0
    assert old_point["rank_basis"] == "grade_percentile"


def test_e03_trends_person_out_of_scope_404(client, p3_seed, v1_seed):
    r = client.get(
        f"{API}/homeroom/analysis/trends",
        params={"person_id": v1_seed.wu_t_id, "academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
    )
    assert r.status_code == 404
    assert r.json()["error"] == "resource_out_of_scope"


# ────────────────────────────── bands 段位分布（v2.1/F10：一律 409） ──────────────────────────────


def _bands_response(client, exam, subject, metric, **params):
    return client.get(
        f"{API}/homeroom/analysis/bands",
        params={"exam_name": exam, "subject": subject, "metric": metric, **params},
    )


def test_bands_without_rank_data_returns_409(client, p3_seed, v1_seed):
    """F10：AnalysisConfig 的 400/500 是【年级名次】阈值，当前 ws 域不存
    名次事实 → bands 一律 409 invalid_scope_param，绝不做分数镜像比较
    （把名次阈值当分数会让物理 90 分全员判薄弱）。score/total 两种
    metric、有分/缺考样本同此口径。"""
    s = v1_seed
    for subject, metric in (("物理", "score"), ("主三门", "total"), ("九科", "total")):
        r = _bands_response(
            client, EXAM_BANDS, subject, metric,
            academic_year_id=s.ay_id, class_id=s.h6_id,
        )
        assert r.status_code == 409, (subject, metric, r.text)
        body = r.json()
        assert body["error"] == "invalid_scope_param"
        assert body["detail"] == "段位阈值口径为年级名次，当前范围无名次数据，不可计算"


def test_bands_rejects_bad_metric(client, p3_seed, v1_seed):
    r = client.get(
        f"{API}/homeroom/analysis/bands",
        params={
            "exam_name": EXAM_BANDS, "subject": "物理", "metric": "percentile",
            "academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id,
        },
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_scope_param"


# ────────────────────────────── 空态 / 越界 ──────────────────────────────


def test_exam_not_in_domain_returns_404(client, p3_seed, v1_seed):
    s = v1_seed
    for path in ("stats", "students"):
        r = client.get(
            f"{API}/homeroom/analysis/exams/{quote(EXAM_MISSING)}/{path}",
            params={"academic_year_id": s.ay_id, "class_id": s.h6_id},
        )
        assert r.status_code == 404, path
        assert r.json()["error"] == "resource_out_of_scope"
    r = client.get(
        f"{API}/homeroom/analysis/bands",
        params={
            "exam_name": EXAM_MISSING, "subject": "物理", "metric": "score",
            "academic_year_id": s.ay_id, "class_id": s.h6_id,
        },
    )
    assert r.status_code == 404
    assert r.json()["error"] == "resource_out_of_scope"


def test_other_class_exam_is_legal_empty_state(client, p3_seed, v1_seed):
    """域内存在、本班无行 → 200 空态，绝不回退全年级（庚/H9 的行不可见）。"""
    s = v1_seed
    h = _stats(client, EXAM_OTHER_CLASS, academic_year_id=s.ay_id, class_id=s.h6_id)
    assert h["subjects"] == [] and h["totals"] == [] and h["cohort_size"] == 3
    assert h["metadata"]["membership_basis"] == "exam"  # v2.1/F08 口径标注
    st = _students(client, EXAM_OTHER_CLASS, academic_year_id=s.ay_id, class_id=s.h6_id)
    assert st["students"] == []
    # bands 在参数/作用域校验通过后仍不可计算（F10）
    r = _bands_response(
        client, EXAM_OTHER_CLASS, "语文", "score", academic_year_id=s.ay_id, class_id=s.h6_id
    )
    assert r.status_code == 409
    assert r.json()["error"] == "invalid_scope_param"
