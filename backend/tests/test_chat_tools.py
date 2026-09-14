import pytest

from app.chat.tools import (
    TOOL_FUNCTIONS,
    custom_rank_band_trend,
    multi_exam_progress_ranking,
    rank_frequency_stat_tool,
    rank_range_filter_tool,
    student_learning_profile,
    subject_progress_ranking,
)


def test_subject_progress_ranking_registered():
    assert TOOL_FUNCTIONS["subject_progress_ranking"] is subject_progress_ranking
    assert TOOL_FUNCTIONS["student_learning_profile"] is student_learning_profile
    assert TOOL_FUNCTIONS["multi_exam_progress_ranking"] is multi_exam_progress_ranking
    assert TOOL_FUNCTIONS["custom_rank_band_trend"] is custom_rank_band_trend
    assert TOOL_FUNCTIONS["rank_range_filter"] is rank_range_filter_tool
    assert TOOL_FUNCTIONS["rank_frequency_stat"] is rank_frequency_stat_tool


def test_subject_progress_ranking_for_high2_chinese():
    result = subject_progress_ranking(grade=2, subject="语文", limit=5)
    if result.get("error"):
        pytest.skip(result["error"])

    assert result["subject"] == "语文"
    assert result["start_exam"]["id"] != result["end_exam"]["id"]
    assert 1 <= len(result["rows"]) <= 5
    first = result["rows"][0]
    assert {"student_id", "name", "percentile_change", "raw_score_change"} <= set(first)


def test_student_learning_profile_for_existing_student():
    from app.db.models import SessionLocal, SubjectScore

    db = SessionLocal()
    row = db.query(SubjectScore).first()
    db.close()
    if row is None:
        pytest.skip("no students in local tracker database")

    result = student_learning_profile(student_id=row.student_id)

    assert result["student"]["student_id"] == row.student_id
    assert isinstance(result["main_total_trend"], list)
    assert isinstance(result["latest_subjects"], list)
    assert isinstance(result["exam_history"], list)
    assert "subject_scope_note" in result
    assert "metric_note" in result


def test_student_learning_profile_keeps_valid_high1_elective_scores():
    result = student_learning_profile(student_id="9900639", subject_limit=9)
    if result.get("error"):
        pytest.skip(result["error"])

    exam_history = result.get("exam_history") or []
    by_name = {row["exam"]["name"]: row for row in exam_history}
    required_exams = ["高一第一学期期中考试", "高一第一学期期末考试", "高一第一学期9月月考"]
    if not all(name in by_name for name in required_exams):
        pytest.skip("local database does not contain the screenshot regression exams")

    for exam_name in ["高一第一学期期中考试", "高一第一学期期末考试"]:
        subjects = by_name[exam_name]["subjects"]
        for subject in ["生物", "政治", "历史", "地理"]:
            assert subjects[subject]["available"] is True
            assert subjects[subject]["raw_score"] is not None

    september_subjects = by_name["高一第一学期9月月考"]["subjects"]
    for subject in ["物理", "化学", "生物", "政治", "历史", "地理"]:
        assert september_subjects[subject]["available"] is False
        assert september_subjects[subject]["grade_percentile"] is None
