"""Wave E 回归测试：Codex 2026-09-29 独立审查反馈的正式化（探针转正）。

来源：.test-data/codex-review/test_review_probes.py（审查期定向复现，
已删除）+ 主控补充用例。覆盖（编号对应审查反馈）：

1.  缺考残留指标：score=NULL 行内残留的名次/百分位绝不参与诊断
    （features 当前水平 / B3 变化分解 / C4 复查指标点列）；
2.  教研结局聚合按所选总分口径取数（total:3+3 不再冒充主三门）；
3.  教研队列范围隔离：他班学生的干预建档绝不进入本班队列（含
    subject_scope 他科过滤），也不被误记为「已离班」；
4.  【用户口径裁决 2026-09-29】缺考场次不进名次序列 → 跨缺考的连续
    进步**保留**（300→200→缺考→100 = 连续进步 2 次），本用例反向
    断言钉死该裁决，防止后人「修复」回去；
5.  行动首页应用作业统计豁免（ADR-023）：豁免学生不进优先关注、
    缺交不计入作业摘要，stats_excluded_n 如实展示；
6.  基线口径不符 → pending/baseline_incomparable（改目标的旧基线兜底）；
7.  基线捕获受 start_date 锚点约束：补录过去开始的干预不取干预后成绩；
8.  PATCH 改 target_metric → 自动按新指标重取基线；
9.  due_this_week 按计划复查日（review_date，缺省回落档案日期）；
10. 相关性端点缺省 metric 统一为 total:主三门（与 AI 工具同默认）；
11. B3 学生分解输出 totals 全口径名次变化（主三门/3+3 各读各的）。

样本全部合成；测试数据目录由 conftest 隔离（EXAM_TRACKER_DIR）。
"""

import json
from datetime import date, timedelta

import pytest

from app.db import workspace_models as wm
from app.db.models import Base, SessionLocal, engine
from app.api.students_mgmt import _homeroom_ctx, _teaching_ctx

API = "/api/v1"


@pytest.fixture(scope="module", autouse=True)
def isolated_module_schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def probe(v1_seed):
    db = SessionLocal()
    yield db, v1_seed
    db.rollback()
    db.close()


def add_fact(db, s, name, day, score, rank, pct=0.3, total="主三门", subject=None,
             grade_score=None):
    f = wm.ScoreFact(
        data_domain="homeroom", academic_year_id=s.ay_id, class_ref_id=s.h6_id,
        identity_id=s.jia_h_id, exam_name=name, exam_date=day, score=score,
        xueji_rank=rank, grade_percentile=pct, total_type=total, subject=subject,
        grade_score=grade_score, source="wave-e-test",
    )
    db.add(f)
    db.flush()
    return f


def hctx(db, s):
    return _homeroom_ctx(db, s.ay_id, s.h6_id, None)


def add_note(db, pid, domain="homeroom", **kwargs):
    fields = dict(
        date=date.today(), category="谈话", content="wave-e synthetic note",
        problem="wave-e synthetic problem", status="open",
    )
    fields.update(kwargs)
    n = wm.WsStudentNote(data_domain=domain, person_id=pid, **fields)
    db.add(n)
    db.flush()
    return n


# ── 1. 缺考残留指标 ──────────────────────────────────────────────


def test_absent_residual_metrics_never_used(probe):
    from app.diagnosis.features import student_features
    from app.diagnosis.changes import student_change_decomposition
    from app.diagnosis.review import metric_points

    db, s = probe
    add_fact(db, s, "WaveE A", date(2026, 1, 1), 200, 300, 0.5)
    add_fact(db, s, "WaveE B", date(2026, 2, 1), None, 50, 0.05)  # 缺考但残留名次 50
    ctx = hctx(db, s)
    f = student_features(db, ctx, s.jia_h_id, s.ay_id)
    c = student_change_decomposition(db, ctx, s.jia_h_id, "WaveE A", "WaveE B")
    p = metric_points(db, ctx, s.jia_h_id, "total:主三门")[-1]
    assert f["indicators"]["current_level"]["main3"]["rank"] is None
    assert f["indicators"]["current_level"]["main3"]["missing_reason"] == "main3_absent"
    assert c["main3"]["rank_change"] is None
    assert p["value"] is None
    assert p["missing_reason"] == "absent"


# ── 2 + 11. 教研按所选口径取数 / B3 totals 全口径分解 ────────────


def test_research_selected_total_metric_used(probe):
    from app.diagnosis.research import outcome_aggregate

    db, s = probe
    for name, day, m3_rank, all_rank in [
        ("WaveE A", date(2026, 1, 1), 300, 100),
        ("WaveE B", date(2026, 2, 1), 200, 300),
    ]:
        add_fact(db, s, name, day, 250, m3_rank, 0.3, total="主三门")
        add_fact(db, s, name, day, 450, all_rank, 0.3, total="3+3")
    add_note(db, s.jia_h_id)
    result = outcome_aggregate(db, hctx(db, s), "interventions", "WaveE A", "WaveE B",
                               "total:3+3", s.ay_id)
    assert result["students"][0]["change"] == 200  # 3+3：100→300（退步 200 名）
    # 对照：主三门口径应为 300→200 = −100（进步）
    m3 = outcome_aggregate(db, hctx(db, s), "interventions", "WaveE A", "WaveE B",
                           "total:主三门", s.ay_id)
    assert m3["students"][0]["change"] == -100


def test_b3_totals_breakdown_covers_all_total_types(probe):
    from app.diagnosis.changes import student_change_decomposition

    db, s = probe
    for name, day, m3_rank, all_rank in [
        ("WaveE A", date(2026, 1, 1), 300, 100),
        ("WaveE B", date(2026, 2, 1), 200, 300),
    ]:
        add_fact(db, s, name, day, 250, m3_rank, 0.3, total="主三门")
        add_fact(db, s, name, day, 450, all_rank, 0.3, total="3+3")
    c = student_change_decomposition(db, hctx(db, s), s.jia_h_id, "WaveE A", "WaveE B")
    by_type = {t["total_type"]: t["rank_change"] for t in c["totals"]}
    assert by_type == {"3+3": 200, "主三门": -100}
    assert c["main3"]["rank_change"] == -100  # 既有键不回归


# ── 3. 教研队列范围隔离 ──────────────────────────────────────────


def test_research_cohorts_do_not_expose_other_class_notes(probe):
    from app.diagnosis.research import cohort_list

    db, s = probe
    # 吴·T 是 T8 成员（非 T6）；在 T6（物理）视角下其干预建档绝不出现
    add_note(db, s.wu_t_id, "teaching")  # 无 subject_scope
    add_note(db, s.wu_t_id, "teaching", subject_scope="化学")  # 他科教学线
    # T6 自己的成员正常出现（秦甲·T 在 T6）
    add_note(db, s.jia_t_id, "teaching", subject_scope="物理", problem="物理下滑")
    ctx = _teaching_ctx(db, s.ay_id, s.t6_id, None, "物理")
    result = cohort_list(db, ctx, s.ay_id)
    interventions = result["cohorts"][0]
    all_pids = [pid for g in interventions["groups"] for pid in g["person_ids"]]
    assert s.wu_t_id not in all_pids
    assert s.wu_t_id not in interventions["transferred_out"]["person_ids"]  # 不误标转出
    assert s.jia_t_id in all_pids


def test_research_cohorts_transferred_out_keeps_legal_history(probe):
    """学年内曾在本班、现已跨出名册的学生：计入已离班而非消失/误报。"""
    from app.diagnosis.research import cohort_list

    db, s = probe
    # 新建一个历史成员：本学年开学入册、期中转走（valid_to 收口）
    gone = wm.WsStudentIdentity(data_domain="homeroom", display_name="秦转·E")
    db.add(gone)
    db.flush()
    year = db.get(wm.AcademicYear, s.ay_id)
    db.add(wm.Enrollment(
        admin_class_id=s.h6_id, identity_id=gone.id,
        valid_from=year.start_date, valid_to=year.start_date + timedelta(days=90),
        status="transferred",
    ))
    add_note(db, gone.id, "homeroom", problem="历史干预")
    result = cohort_list(db, hctx(db, s), s.ay_id)
    interventions = result["cohorts"][0]
    assert gone.id in interventions["transferred_out"]["person_ids"]
    assert all(gone.id not in g["person_ids"] for g in interventions["groups"])


# ── 4.【用户口径裁决】缺考跳过 → 连续进步保留 ────────────────────


def test_absent_exam_keeps_consecutive_progress_by_user_ruling(probe):
    """用户 2026-09-29 裁决：缺考场次不进名次序列，跨缺考的连续进步**算数**。

    300→200→缺考→100：缺考被跳过，进步 2 次连续成立（streak 进步×2）。
    本断言钉死该口径——这不是 bug，是产品决策；如需改动须先过用户。
    """
    from app.diagnosis.features import student_features

    db, s = probe
    for i, rank in enumerate([300, 200, None, 100], 1):
        add_fact(db, s, f"WaveE {i}", date(2026, i, 1),
                 250 if rank is not None else None, rank,
                 0.3 if rank is not None else None)
    f = student_features(db, hctx(db, s), s.jia_h_id, s.ay_id)
    streak = f["indicators"]["trend"]["streak"]
    assert streak == {"kind": "进步", "count": 2}


# ── 5. 行动首页统计豁免 ──────────────────────────────────────────


def test_action_summary_honors_homework_stats_exclusion(probe):
    from app.diagnosis.action import action_summary

    db, s = probe
    a = wm.HomeworkAssignment(
        data_domain="homeroom", academic_year_id=s.ay_id, class_ref_id=s.h6_id,
        subject="数学", homework_type="review", assigned_date=date.today(),
        status="active", expected_members_json=json.dumps([s.jia_h_id]),
        revision=1, batch_token="wave-e-batch",
    )
    db.add(a)
    db.flush()
    db.add(wm.HomeworkSubmission(
        assignment_id=a.id, person_id=s.jia_h_id, submission_status="missing"
    ))
    db.add(wm.HomeworkStatsExclusion(
        data_domain="homeroom", class_ref_id=s.h6_id, identity_id=s.jia_h_id
    ))
    db.flush()
    result = action_summary(db, hctx(db, s), s.ay_id)
    homework = result["sections"]["homework"]
    assert homework["missing_30d_total"] == 0
    assert homework["stats_excluded_n"] == 1
    assert all(p["person_id"] != s.jia_h_id for p in result["priority_persons"])


# ── 6. 基线口径不符 → pending ────────────────────────────────────


def test_baseline_metric_change_must_not_reuse_other_subject(probe):
    from app.diagnosis.review import review_contrast

    db, s = probe
    add_fact(db, s, "WaveE Math", date.today() - timedelta(days=1), 85, None,
             0.6, total=None, subject="数学")
    n = add_note(
        db, s.jia_h_id, target_metric="subject:数学",
        baseline_value={"metric": "subject:语文", "unit": "percentile", "value": 0.3},
        start_date=date.today() - timedelta(days=10), review_date=date.today(),
    )
    result = review_contrast(db, hctx(db, s), n)
    assert result["status"] == "pending"
    assert result["reason"] == "baseline_incomparable"


# ── 7. 基线捕获锚点 ──────────────────────────────────────────────


def test_baseline_capture_respects_start_date(probe):
    from app.api.students_mgmt import create_note
    from app.api.students_mgmt_schemas import NoteCreateRequest
    from app.diagnosis.review import review_contrast

    db, s = probe
    add_fact(db, s, "WaveE Before", date.today() - timedelta(days=60), 240, 300)
    add_fact(db, s, "WaveE After", date.today() - timedelta(days=10), 260, 100)
    req = NoteCreateRequest(
        date=date.today().isoformat(), category="谈话", content="wave-e backfill",
        problem="补录历史干预", target_metric="total:主三门",
        start_date=(date.today() - timedelta(days=30)).isoformat(),
    )
    result = create_note(
        mode="homeroom", person_id=s.jia_h_id, req=req,
        academic_year_id=s.ay_id, class_id=s.h6_id, term_id=None,
        teaching_class_id=None, subject=None, db=db,
    )
    # 基线 = 干预开始**之前**的那场（300 名），不是 10 天前的 100 名
    assert result.baseline_value["exam_name"] == "WaveE Before"
    assert result.baseline_value["value"] == 300.0
    note = db.get(wm.WsStudentNote, result.id)
    contrast = review_contrast(db, hctx(db, s), note)
    assert contrast["status"] == "ready"
    assert contrast["change"]["value"] == 100 - 300  # 最新 100 − 基线 300


# ── 8. PATCH 改目标 → 重取基线 ───────────────────────────────────


def test_patch_target_metric_recaptures_baseline(client, v1_seed):
    s = v1_seed
    # 经 API 创建：主三门干预（基线自动捕获）
    resp = client.post(f"{API}/homeroom/students/{s.jia_h_id}/notes", json={
        "date": date.today().isoformat(), "category": "谈话", "content": "wave-e",
        "problem": "patch 基线重取", "target_metric": "total:主三门",
        "start_date": (date.today() - timedelta(days=30)).isoformat(),
        "force": True,  # 前用例已为甲建同科干预
    })
    assert resp.status_code == 200, resp.text
    note = resp.json()
    # 改目标为 subject:语文 → 基线应自动重取为语文口径（metric 键同步）
    resp2 = client.patch(f"{API}/homeroom/notes/{note['id']}", json={
        "target_metric": "subject:语文"})
    assert resp2.status_code == 200, resp2.text
    patched = resp2.json()
    if patched["baseline_value"] is not None:
        assert patched["baseline_value"]["metric"] == "subject:语文"
    # 复查对照不再跨口径硬算：metric 一致或 pending
    resp3 = client.get(f"{API}/homeroom/diagnosis/review-contrast",
                       params={"follow_up_id": note["id"],
                               "academic_year_id": s.ay_id, "class_id": s.h6_id})
    assert resp3.status_code == 200, resp3.text
    body = resp3.json()
    assert body["metric"] == "subject:语文"
    if body["status"] == "ready":
        assert body["baseline"]["unit"] == "percentile"


# ── 9. due_this_week 按复查日 ────────────────────────────────────


def test_due_this_week_counts_by_review_date(client, v1_seed):
    s = v1_seed
    # 甲：上周建档、复查日在本周 → 计入（旧口径按建档日期则不计）
    r1 = client.post(f"{API}/homeroom/students/{s.jia_h_id}/notes", json={
        "date": (date.today() - timedelta(days=10)).isoformat(),
        "category": "谈话", "content": "wave-e due",
        "follow_up": "复查：本周", "problem": "due 测试",
        "target_metric": "total:主三门",
        "start_date": (date.today() - timedelta(days=10)).isoformat(),
        "review_date": date.today().isoformat(),
        "force": True,  # 前用例已为甲建同科干预
    })
    assert r1.status_code == 200, r1.text
    resp = client.get(f"{API}/homeroom/diagnosis/action-summary",
                      params={"academic_year_id": s.ay_id, "class_id": s.h6_id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["sections"]["follow_ups"]["due_this_week"] >= 1


# ── 10. 相关性缺省口径统一 ───────────────────────────────────────


def test_correlation_default_metric_is_main3(client, v1_seed):
    s = v1_seed
    # 造一场有日期的考试 + 主三座行，保证端点可返回（样本不足也带 metric 字段）
    db = SessionLocal()
    try:
        add_fact(db, s, "WaveE Corr", date.today() - timedelta(days=3), 250, 300)
        db.commit()
    finally:
        db.close()
    resp = client.get(f"{API}/homeroom/diagnosis/correlation", params={
        "exam_name": "WaveE Corr", "window_days": 14,
        "academic_year_id": s.ay_id, "class_id": s.h6_id,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["metric"] == "total:主三门"  # 与 AI 工具同默认（2026-09-29 统一）
