"""关注回看真实服务/HTTP 验证；仅使用独立测试库与合成种子。"""
from datetime import date
from dataclasses import replace
from pathlib import Path
import importlib.util
import json

import pytest


@pytest.fixture(scope="module")
def demo(isolated_module_schema):
    from app.db.models import SessionLocal
    script = Path(__file__).resolve().parents[3] / "scripts/seed_research_demo.py"
    spec = importlib.util.spec_from_file_location("focus_demo", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with SessionLocal() as db:
        return module.seed_demo(db)


@pytest.fixture
def sample(demo):
    from app.db.models import SessionLocal
    from app.core.context import resolve_workspace_context
    with SessionLocal() as db:
        ctx = resolve_workspace_context(db, 1, "homeroom", {
            "academic_year_id": demo["academic_year_id"], "class_id": demo["class_id"],
            "as_of": "2026-10-07",
        })
        yield db, ctx, demo
        db.rollback()


def outcome(db, ctx, metric="problem:imbalance"):
    from app.diagnosis.focus import focus_outcome
    return focus_outcome(db, ctx, "focus:明显偏科型@九月月考", "九月月考", "阶段复查", metric)


def test_fixed_start_keeps_improved_students_and_secondary_tags(sample):
    from app.diagnosis.focus import focus_cohorts
    db, ctx, demo = sample
    cohorts = focus_cohorts(db, ctx)["cohorts"]
    historical = next(c for c in cohorts if c["cohort"] == "focus:明显偏科型@九月月考")
    current = next(c for c in cohorts if c["cohort"] == "current-focus:明显偏科型")
    assert historical["student_count"] == 6
    assert demo["person_ids"]["示例甲"] not in current["person_ids"]
    result = outcome(db, ctx)
    row = next(r for r in result["students"] if r["name"] == "示例甲")
    assert row["direction"] == "短板有所改善"
    secondary = next(r for r in result["students"] if r["name"] == "示例丁")
    assert secondary["is_secondary"] and secondary["main_type"] == "短期下滑型"
    assert "数学" in secondary["reason"]


def test_gap_shrink_does_not_claim_improvement_when_subject_declines(sample):
    db, ctx, _ = sample
    row = next(r for r in outcome(db, ctx)["students"] if r["name"] == "示例乙")
    assert row["direction"] == "需核查：差距缩小但单科退步"
    math = row["imbalance"][0]
    assert (math["from_percentile"], math["to_percentile"]) == (66, 70)
    assert (math["from_gap"], math["to_gap"]) == (35, 20)


def test_missing_score_remnant_not_used_and_exclusions_retained(sample):
    db, ctx, _ = sample
    result = outcome(db, ctx)
    assert (result["selected_n"], result["comparable_n"], result["excluded_n"]) == (6, 4, 2)
    missing = {r["name"]: r for r in result["excluded_students"]}
    assert missing["示例戊"]["imbalance"][0]["to_percentile"] is None
    assert missing["示例己"]["imbalance"][0]["to_overall_percentile"] is None
    assert all(r["missing_reason"] and r["direction"] == "暂不可比" for r in missing.values())


def test_scalar_values_units_and_changes_match_b3(sample):
    from app.diagnosis.changes import student_change_decomposition
    db, ctx, _ = sample
    for metric, unit, threshold, field in (("total:主三门", "rank", 20, "rank_change"),
                                          ("subject:数学", "percentile", 5, "percentile_change")):
        result = outcome(db, ctx, metric)
        assert (result["metric_unit"], result["threshold"]) == (unit, threshold)
        for row in result["students"]:
            b3 = student_change_decomposition(db, ctx, row["person_id"], "九月月考", "阶段复查")
            source = b3["main3"] if unit == "rank" else next(r for r in b3["subjects"] if r["subject"] == "数学")
            assert row["change"] == source[field]
            assert row["change"] == pytest.approx(row["to_value"] - row["from_value"])
        if unit == "percentile":
            improved = next(r for r in result["students"] if r["name"] == "示例甲")
            assert improved["change"] == -24 and improved["direction"] == "进步"


@pytest.mark.parametrize("change,unit,direction", [(-20,"rank","进步"),(19,"rank","未达变化阈值"),
    (5,"percentile","退步"),(-4.9,"percentile","未达变化阈值"),(3,"grade_score","进步"),(-3,"grade_score","退步")])
def test_display_thresholds_keep_different_units(change, unit, direction):
    from app.diagnosis.focus import _direction
    assert _direction(change, unit) == direction


def test_month_only_date_is_current_only_and_historical_rejected(sample):
    from app.db.workspace_models import ScoreFact
    from app.diagnosis.focus import focus_cohorts
    from app.core.errors import InvalidScopeParam
    db, ctx, _ = sample
    for row in db.query(ScoreFact).filter(ScoreFact.exam_name == "九月月考"):
        row.exam_date = None
        row.source_exam_date = "2026-09"
        row.exam_date_precision = "month"
    db.flush()
    result = focus_cohorts(db, ctx)
    exam = next(e for e in result["exams"] if e["exam_name"] == "九月月考")
    assert not exam["historical_available"]
    assert not any(c["exam_name"] == "九月月考" for c in result["cohorts"])
    assert any(c["membership_basis"] == "current_time_point" for c in result["cohorts"])
    with pytest.raises(InvalidScopeParam):
        outcome(db, ctx)


def test_homework_equal_windows_coverage_and_no_automatic_success(sample):
    from app.diagnosis.focus import _homework_compare
    db, ctx, demo = sample
    result = _homework_compare(db, ctx, demo["person_ids"]["示例乙"], date(2026,9,15), date(2026,10,1))
    assert result["comparable"]
    assert (result["from"]["missing"], result["to"]["missing"]) == (4,1)
    assert result["from"]["valid_batches"] == result["to"]["valid_batches"] == 7
    assert "覆盖" in result["reason"] and "改善" not in result.get("direction", "")


def test_homework_no_batches_and_legacy_cannot_imply_all_submitted(sample):
    from app.db.workspace_models import HomeworkAssignment, HomeworkSubmission
    from app.diagnosis.focus import _homework_compare
    db, ctx, demo = sample
    pid = demo["person_ids"]["示例乙"]
    empty = _homework_compare(db, ctx, pid, date(2025,9,15), date(2025,10,1))
    assert not empty["comparable"] and empty["from"]["valid_batches"] == 0
    legacy = HomeworkAssignment(data_domain="homeroom", academic_year_id=ctx.academic_year_id,
        class_ref_id=demo["class_id"], subject="数学", homework_type="旧记录", assigned_date=date(2026,9,30), batch_token="legacy-focus-test", expected_members_json="[]")
    db.add(legacy); db.flush()
    db.add(HomeworkSubmission(assignment_id=legacy.id, person_id=pid, submission_status="missing")); db.flush()
    result = _homework_compare(db, ctx, pid, date(2026,9,15), date(2026,10,1))
    assert not result["comparable"] and result["to"]["legacy_batches"] == 1


def test_other_person_and_other_class_batches_not_counted(sample):
    from app.db.workspace_models import HomeworkAssignment
    from app.diagnosis.focus import _homework_compare
    db, ctx, demo = sample
    for class_id, expected in ((demo["class_id"], [demo["person_ids"]["示例甲"]]), (999, [demo["person_ids"]["示例乙"]])):
        db.add(HomeworkAssignment(data_domain="homeroom", academic_year_id=ctx.academic_year_id,
            class_ref_id=class_id, subject="数学", homework_type="他人作业", assigned_date=date(2026,9,30),
            batch_token=f"other-{class_id}", expected_members_json=json.dumps(expected)))
    db.flush()
    result = _homework_compare(db, ctx, demo["person_ids"]["示例乙"], date(2026,9,15), date(2026,10,1))
    assert result["comparable"] and result["to"]["legacy_batches"] == 0 and result["to"]["valid_batches"] == 7


def test_followups_use_each_record_baseline_pending_and_closed_separate(sample):
    from app.diagnosis.focus import follow_ups
    from app.diagnosis.review import review_contrast
    from app.db.workspace_models import WsStudentNote
    db, ctx, _ = sample
    result = follow_ups(db, ctx)
    assert result["summary"] == {"total_n":6,"due_n":4,"ready_n":3,"pending_n":2}
    rows = {r["name"]: r for r in result["records"]}
    assert rows["示例戊"]["contrast"]["reason"] == "no_comparable_exam"
    assert rows["示例己"]["contrast"]["reason"] == "no_baseline"
    assert rows["示例辛"]["record_status"] == "done" and rows["示例辛"]["contrast"]["status"] == "ready"
    assert rows["示例庚"]["contrast"]["baseline"]["value"] == 460 and not rows["示例庚"]["due"]
    assert rows["示例甲"]["contrast"]["baseline"]["value"] == .66
    for row in result["records"]:
        assert row["contrast"] == review_contrast(db, ctx, db.get(WsStudentNote, row["id"]))


def test_empty_members_do_not_widen_to_grade(sample):
    from app.diagnosis.focus import focus_cohorts, follow_ups
    db, ctx, _ = sample
    empty = replace(ctx, class_ids=(), member_person_ids=())
    assert all(c["student_count"] == 0 for c in focus_cohorts(db, empty)["cohorts"])
    assert follow_ups(db, empty)["records"] == []


def test_other_class_followup_does_not_become_transferred_record(sample):
    from app.db.workspace_models import AdministrativeClass, WsStudentIdentity, Enrollment, WsStudentNote
    from app.diagnosis.focus import follow_ups
    db, ctx, _ = sample
    other_class = AdministrativeClass(academic_year_id=ctx.academic_year_id, grade=1, class_num=9)
    other_person = WsStudentIdentity(data_domain="homeroom", display_name="虚拟他班学生")
    db.add_all([other_class,other_person]); db.flush()
    db.add(Enrollment(admin_class_id=other_class.id, identity_id=other_person.id, valid_from=date(2026,8,20), status="active"))
    db.add(WsStudentNote(data_domain="homeroom",person_id=other_person.id,date=date(2026,9,16),
                        category="谈话",content="他班合成跟进",problem="他班问题",status="open"))
    db.flush()
    assert len(follow_ups(db, ctx)["records"]) == 6
    assert all(r["person_id"] != other_person.id for r in follow_ups(db, ctx)["records"])


def test_http_invalid_anchor_exam_year_and_class_rejected(client, demo):
    query = {"academic_year_id":demo["academic_year_id"],"class_id":demo["class_id"]}
    root = "/api/v1/homeroom/diagnosis/research/focus"
    assert client.get(root+"/cohorts", params=query).status_code == 200
    for bad in ({"academic_year_id":999}, {"class_id":999}):
        assert client.get(root+"/cohorts", params={**query,**bad}).status_code == 404
    for change in ({"from_exam":"开学诊断"},{"to_exam":"开学诊断"}, {"metric":"problem:homework"}):
        resp = client.get(root+"/outcome", params={**query,"cohort":"focus:明显偏科型@九月月考",
            "from_exam":"九月月考","to_exam":"阶段复查","metric":"problem:imbalance",**change})
        assert resp.status_code == 422, resp.text
