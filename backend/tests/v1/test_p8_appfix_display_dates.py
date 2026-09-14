"""P8 application follow-up: truthful month display without sharing expansion."""

from app.api import _queries as q


API = "/api/v1"
MONTH_EXAM = "P8月精度展示"


def _add_month_fact(v1_seed, exam_name=MONTH_EXAM):
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    db = SessionLocal()
    fact = wm.ScoreFact(
        data_domain="homeroom",
        academic_year_id=v1_seed.ay_id,
        exam_name=exam_name,
        exam_date=None,
        source_exam_date="2025-10",
        exam_date_precision="month",
        class_ref_id=v1_seed.h6_id,
        identity_id=v1_seed.jia_h_id,
        subject="物理",
        total_type=None,
        score=77,
        source="p8-appfix-test",
        data_revision=1,
    )
    db.add(fact)
    db.commit()
    db.refresh(fact)
    db.close()
    return fact


def test_month_precision_is_displayed_but_still_blocked_by_share_gate(client, v1_seed):
    fact = _add_month_fact(v1_seed)

    own = client.get(
        f"{API}/shared/exams",
        params={
            "mode": "homeroom",
            "academic_year_id": v1_seed.ay_id,
            "class_id": v1_seed.h6_id,
        },
    )
    assert own.status_code == 200, own.text
    item = next(row for row in own.json()["exams"] if row["exam_name"] == MONTH_EXAM)
    assert item["exam_date"] == "2025-10"

    # ADR-017 remains intact: a display-only month cannot prove an exam day,
    # so the active H6/T6 link must not project this fact to teaching.
    other = client.get(
        f"{API}/shared/exams",
        params={
            "mode": "teaching",
            "academic_year_id": v1_seed.ay_id,
            "teaching_class_id": v1_seed.t6_id,
        },
    )
    assert other.status_code == 200, other.text
    assert MONTH_EXAM not in {row["exam_name"] for row in other.json()["exams"]}
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    db = SessionLocal()
    try:
        link = db.get(wm.HomeroomTeachingLink, v1_seed.link_id)
        stored_fact = db.get(wm.ScoreFact, fact.id)
        assert q.gate_fact(link, stored_fact) is False
    finally:
        db.close()


def test_month_precision_reaches_profile_and_trends(client, v1_seed):
    exam_name = f"{MONTH_EXAM}-画像"
    _add_month_fact(v1_seed, exam_name)

    profile = client.get(
        f"{API}/homeroom/students/{v1_seed.jia_h_id}",
        params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
    )
    assert profile.status_code == 200, profile.text
    physics = next(group for group in profile.json()["subjects"] if group["subject"] == "物理")
    point = next(row for row in physics["exams"] if row["exam_name"] == exam_name)
    assert point["exam_date"] == "2025-10"

    trends = client.get(
        f"{API}/homeroom/analysis/trends",
        params={
            "person_id": v1_seed.jia_h_id,
            "academic_year_id": v1_seed.ay_id,
            "class_id": v1_seed.h6_id,
        },
    )
    assert trends.status_code == 200, trends.text
    points = [
        row
        for year in trends.json()["years"]
        for row in year["subjects"].get("物理", [])
        if row["exam_name"] == exam_name
    ]
    assert points == [
        {"exam_name": exam_name, "exam_date": "2025-10", "score": 77.0, "grade_score": None}
    ]


def test_invalid_source_month_is_not_exposed(v1_seed):
    fact = _add_month_fact(v1_seed, f"{MONTH_EXAM}-非法")
    fact.source_exam_date = "2025-13"
    assert q.display_exam_date(fact) is None
