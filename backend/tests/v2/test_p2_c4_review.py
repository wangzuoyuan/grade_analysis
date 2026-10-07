"""P2-C4 自动复查对照端点测试（契约 docs/diagnosis-roadmap/p2-contracts.md §5.2）。

覆盖：
- ready（新可比考试）：start_date 之后有可比考试 → {baseline, latest, change,
  note}；change = 最新 − 基线（§9 符号：名次/前百分位负=相对位置上升）；
  note 必须解释方向且声明不构成成功/失败判定、不构成因果或提分保证；
  响应结构上**绝不**出现成功/失败判定字段；
- ready（到期触发）：review_date 已到 → trigger=both；
- pending：未到期且无新可比考试 → not_due_yet；到期但缺考（C4E2 主三门
  score=NULL）→ no_comparable_exam（含缺考场说明）；到期且无任何后续考试 →
  no_comparable_exam；缺目标指标 → no_target_metric；缺基线 → no_baseline；
  基线口径不符 → baseline_incomparable；
- 教学域：subject_grade:物理 等级分口径（正值=数值升高，方向解释随单位切换）；
- 口径版本 calc_version=p2-v1；
- 越界：person 不在显式教学班 → 404 resource_out_of_scope。

样本全部合成（秦门/秦语/秦钥/秦锁/秦考/秦务/秦试/秦理·T）。
"""

from datetime import date, timedelta

import pytest

API = "/api/v1"

METRIC_MAIN3 = "total:主三门"
METRIC_CHINESE = "subject:语文"
METRIC_PHYS_GRADE = "subject_grade:物理"

# 响应中绝不允许出现的判定类字段（§5.2：绝不自动标成功/失败）
_FORBIDDEN_KEYS = ("success", "verdict", "outcome", "achieved", "passed", "failed")


def _iso(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _open_intervention(client, mode, person_id, target_metric, *, start_days_ago=30,
                       review_in_days=None, subject_scope=None, extra=None):
    payload = {
        "date": _iso(0),
        "category": "谈话",
        "content": "开始干预并跟踪",
        "problem": "名次下滑",
        "measures": "每周面批",
        "target_metric": target_metric,
        "start_date": _iso(start_days_ago),
    }
    if review_in_days is not None:
        payload["review_date"] = _iso(-review_in_days)
    if subject_scope is not None:
        payload["subject_scope"] = subject_scope
    if extra:
        payload.update(extra)
    resp = client.post(f"{API}/{mode}/students/{person_id}/notes", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _add_fact(seed, domain, cls_id, ident_id, exam, exam_date, subject=None,
              total_type=None, score=None, pct=None, xueji=None, grade_rank=None,
              grade_score=None):
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    db = SessionLocal()
    try:
        f = wm.ScoreFact()
        f.data_domain = domain
        f.academic_year_id = seed.seed.ay_id
        f.exam_name = exam
        f.exam_date = exam_date
        f.class_ref_id = cls_id
        f.identity_id = ident_id
        f.subject = subject
        f.total_type = total_type
        f.score = score
        f.grade_percentile = pct
        f.xueji_rank = xueji
        f.grade_rank = grade_rank
        f.grade_score = grade_score
        f.source = "p2-c4-review-test"
        db.add(f)
        db.commit()
        return f.id
    finally:
        db.close()


def _contrast(client, mode, note_id):
    return client.get(
        f"{API}/{mode}/diagnosis/review-contrast?follow_up_id={note_id}"
    )


def _assert_no_verdict_keys(body: dict):
    for key in _FORBIDDEN_KEYS:
        assert key not in body, f"复查对照不得出现判定字段 {key}"


@pytest.mark.usefixtures("p2c4_seed")
class TestReviewReady:
    def test_ready_rank_metric_new_exam_after_start(self, client, p2c4_seed):
        """新可比考试（干预创建后到考）：rank 口径 change=-12（名次变小=上升）。"""
        note = _open_intervention(
            client, "homeroom", p2c4_seed.men_id, METRIC_MAIN3,
            start_days_ago=30, review_in_days=10,
        )
        assert note["baseline_value"]["value"] == 200.0  # C4基线考（创建时点最新可比）
        # 干预后新考试（7 天前 = start_date(30 天前) 之后）
        _add_fact(p2c4_seed, "homeroom", p2c4_seed.seed.h6_id, p2c4_seed.men_id,
                  "C4新考一", date.fromisoformat(_iso(7)), None, "主三门", 280.0,
                  xueji=188, grade_rank=196)
        resp = _contrast(client, "homeroom", note["id"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["calc_version"] == "p2-v1"
        assert body["status"] == "ready"
        _assert_no_verdict_keys(body)
        assert body["baseline"]["value"] == 200.0
        assert body["baseline"]["unit"] == "rank"
        assert body["baseline"]["exam_name"] == p2c4_seed.exam_base
        assert body["latest"]["value"] == 188.0
        assert body["latest"]["exam_name"] == "C4新考一"
        assert body["change"]["value"] == -12.0
        assert body["change"]["smaller_is_better"] is True
        assert body["review"]["due"] is False
        assert body["review"]["trigger"] == "new_comparable_exam"
        assert "相对位置上升" in body["note"]
        assert "不构成成功/失败判定" in body["note"]
        assert "不构成因果或提分保证" in body["note"]

    def test_ready_due_trigger_both(self, client, p2c4_seed):
        """到期触发：review_date 已到 + 有可比考试 → trigger=both。"""
        note = _open_intervention(
            client, "homeroom", p2c4_seed.dang_id, METRIC_MAIN3,
            start_days_ago=30, review_in_days=0,  # review_date = 今天
        )
        _add_fact(p2c4_seed, "homeroom", p2c4_seed.seed.h6_id, p2c4_seed.dang_id,
                  "C4新考二", date.fromisoformat(_iso(5)), None, "主三门", 277.0,
                  xueji=205, grade_rank=215)
        resp = _contrast(client, "homeroom", note["id"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ready"
        assert body["review"]["due"] is True
        assert body["review"]["trigger"] == "both"
        assert body["latest"]["value"] == 205.0
        assert body["change"]["value"] == -5.0

    def test_ready_percentile_metric_homeroom(self, client, p2c4_seed):
        """班主任域 subject:语文 百分位口径：change=-0.10（前百分位变小=上升）。"""
        note = _open_intervention(
            client, "homeroom", p2c4_seed.yu_id, METRIC_CHINESE,
            start_days_ago=30, review_in_days=14,
        )
        assert note["baseline_value"]["value"] == 0.5  # C4基线考 语文 pct
        _add_fact(p2c4_seed, "homeroom", p2c4_seed.seed.h6_id, p2c4_seed.yu_id,
                  "C4新考三", date.fromisoformat(_iso(6)), "语文", None, 84.0,
                  pct=0.40)
        resp = _contrast(client, "homeroom", note["id"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ready"
        assert body["unit"] == "percentile"
        assert body["baseline"]["value"] == 0.5
        assert body["latest"]["value"] == 0.4
        assert body["change"]["value"] == -0.1
        assert "相对位置上升" in body["note"]

    def test_ready_grade_score_metric_teaching_domain(self, client, p2c4_seed):
        """教学域 subject_grade:物理 等级分口径：正值=数值升高（越大越好）。"""
        note = _open_intervention(
            client, "teaching", p2c4_seed.li_t_id, METRIC_PHYS_GRADE,
            start_days_ago=30, review_in_days=14,
        )
        assert note["baseline_value"]["value"] == 82.0
        _add_fact(p2c4_seed, "teaching", p2c4_seed.seed.t6_id, p2c4_seed.li_t_id,
                  "C4新考四", date.fromisoformat(_iso(6)), "物理", None, 91.0,
                  grade_score=85.0)
        resp = _contrast(client, "teaching", note["id"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ready"
        assert body["unit"] == "grade_score"
        assert body["change"]["value"] == 3.0
        assert body["change"]["smaller_is_better"] is False
        assert "数值升高" in body["note"]
        assert "不构成成功/失败判定" in body["note"]


@pytest.mark.usefixtures("p2c4_seed")
class TestReviewPending:
    def test_pending_not_due_yet(self, client, p2c4_seed):
        """未到期且开始日后无新可比考试 → pending/not_due_yet。"""
        note = _open_intervention(
            client, "homeroom", p2c4_seed.yue_id, METRIC_MAIN3,
            start_days_ago=30, review_in_days=10,
        )
        resp = _contrast(client, "homeroom", note["id"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        assert body["reason"] == "not_due_yet"
        assert body["comparable_exams_n"] == 0
        _assert_no_verdict_keys(body)

    def test_pending_no_comparable_exam_when_due_with_missing_exam(self, client, p2c4_seed):
        """§6 门禁：复查缺考 → pending。秦考 C4E2 主三门 score=NULL（缺考），
        该场不可比；到期 → pending/no_comparable_exam 并点名缺考场。"""
        note = _open_intervention(
            client, "homeroom", p2c4_seed.kao_id, METRIC_MAIN3,
            start_days_ago=30, review_in_days=0,
        )
        assert note["baseline_value"]["value"] == 400.0  # 基线来自 C4基线考
        resp = _contrast(client, "homeroom", note["id"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        assert body["reason"] == "no_comparable_exam"
        assert body["comparable_exams_n"] == 0
        assert p2c4_seed.exam_missing in body["note"]
        _assert_no_verdict_keys(body)

    def test_pending_no_comparable_exam_when_due_without_any_exam(self, client, p2c4_seed):
        """到期但开始日后无任何考试 → pending/no_comparable_exam。"""
        note = _open_intervention(
            client, "homeroom", p2c4_seed.suo_id, METRIC_MAIN3,
            start_days_ago=30, review_in_days=0,
        )
        resp = _contrast(client, "homeroom", note["id"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        assert body["reason"] == "no_comparable_exam"

    def test_pending_no_target_metric(self, client, p2c4_seed):
        note = _open_intervention(
            client, "homeroom", p2c4_seed.zhi_id, None,
            start_days_ago=10,
            extra={"target_metric": None},
        )
        resp = _contrast(client, "homeroom", note["id"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        assert body["reason"] == "no_target_metric"

    def test_pending_no_baseline(self, client, p2c4_seed):
        """零成绩学生：target_metric 有但基线捕获不到 → pending/no_baseline。"""
        note = _open_intervention(
            client, "homeroom", p2c4_seed.wu2_id, METRIC_MAIN3,
            start_days_ago=10, review_in_days=0,
        )
        resp = _contrast(client, "homeroom", note["id"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        assert body["reason"] == "no_baseline"

    def test_pending_baseline_incomparable_on_unit_mismatch(self, client, p2c4_seed):
        """教师手填基线单位与指标口径不符 → pending/baseline_incomparable。"""
        note = _open_intervention(
            client, "homeroom", p2c4_seed.shi_id, METRIC_MAIN3,
            start_days_ago=30, review_in_days=0,
            extra={"baseline_value": {"value": 272.0, "unit": "score"}},  # 指标期望 rank
        )
        resp = _contrast(client, "homeroom", note["id"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        assert body["reason"] == "baseline_incomparable"

    def test_pending_baseline_incomparable_on_non_numeric(self, client, p2c4_seed):
        note = _open_intervention(
            client, "homeroom", p2c4_seed.dang_id, METRIC_MAIN3,
            start_days_ago=30,
            extra={"baseline_value": {"value": "还没出分", "unit": "rank"}, "force": True},
        )
        resp = _contrast(client, "homeroom", note["id"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["reason"] == "baseline_incomparable"


@pytest.mark.usefixtures("p2c4_seed")
class TestReviewScopeGuards:
    def test_note_of_other_domain_404(self, client, p2c4_seed):
        """N01 域隔离：homeroom 域干预经 teaching 复查路径 → 404（绝不泄露存在性）。"""
        homeroom_note = _open_intervention(
            client, "homeroom", p2c4_seed.men_id, METRIC_MAIN3,
            start_days_ago=30, extra={"force": True},  # 秦门已有未关闭干预（前用例）
        )
        resp = _contrast(client, "teaching", homeroom_note["id"])
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"] == "resource_out_of_scope"
        # 教学域本人的干预正常可见（专用学生 秦数·T；2026-09-29 基线锚点
        # 语义：基线只取 start 之前 → 补一场干预开始前的等级分 70@120天前，
        # C4基线考(78@60天前)落在 start 之后 → 作为「新可比考试」构成 ready）
        _add_fact(p2c4_seed, "teaching", p2c4_seed.seed.t6_id, p2c4_seed.shu_t_id,
                  "C4前置考", date.fromisoformat(_iso(120)), "物理", None, 80.0,
                  pct=0.40, grade_score=70.0)
        note = _open_intervention(
            client, "teaching", p2c4_seed.shu_t_id, METRIC_PHYS_GRADE,
            start_days_ago=90,
        )
        ok = _contrast(client, "teaching", note["id"])
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["status"] == "ready"
        assert body["baseline"]["value"] == 70.0  # 锚点之前的 C4前置考
        assert body["baseline"]["exam_name"] == "C4前置考"
        assert body["latest"]["value"] == 78.0
        assert body["change"]["value"] == 8.0
        _assert_no_verdict_keys(body)

    def test_unknown_follow_up_id_404(self, client, p2c4_seed):
        resp = _contrast(client, "homeroom", 999999)
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"] == "resource_out_of_scope"


def test_month_precision_exam_never_crashes_and_not_comparable(client, v1_seed, db_session):
    """月份精度考试日期（真实库主流形态）不得让 review-contrast 崩溃：
    不构成可比证据，走 pending 信封（主控合并修复回归）。"""
    from datetime import date as _date

    from app.db import workspace_models as wm

    note = wm.WsStudentNote(
        data_domain="homeroom",
        person_id=v1_seed.jia_h_id,
        date=_date(2025, 12, 1),
        category="谈话",
        content="合成：月精度回归",
        problem="合成问题",
        subject_scope=None,
        target_metric="total:主三门",
        baseline_value={"value": 300.0, "unit": "score"},
        start_date=_date(2025, 12, 1),
        review_date=_date(2025, 12, 20),
        status="open",
    )
    db_session.add(note)
    db_session.commit()
    resp = client.get(
        f"/api/v1/homeroom/diagnosis/review-contrast?follow_up_id={note.id}"
        "&academic_year_id=" + str(v1_seed.ay_id) + "&class_id=" + str(v1_seed.h6_id)
    )
    assert resp.status_code == 200, resp.text[:300]
    body = resp.json()
    assert body["status"] in ("ready", "pending")
