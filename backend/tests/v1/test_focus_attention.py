"""旧班主任版重点关注口径在合并工作台中的回归测试。"""

from datetime import date

from app.db import workspace_models  # noqa: F401

from .conftest import EXAM_E1, SUBJECT, TOTAL_TYPE_MAIN3


API = "/api/v1"


def test_focus_restores_all_legacy_dimensions(client, v1_seed, db_session):
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
    totals[v1_seed.jia_h_id].xueji_rank = 450
    totals[v1_seed.jia_h_id].grade_percentile = 0.30
    totals[v1_seed.yi_h_id].grade_rank = 520  # 学籍名次缺失时沿用旧版年级名次回退
    totals[v1_seed.yi_h_id].grade_percentile = 0.55
    totals[v1_seed.bing_h_id].xueji_rank = 50
    totals[v1_seed.bing_h_id].grade_percentile = 0.10

    jia_physics = (
        db_session.query(wm.ScoreFact)
        .filter(
            wm.ScoreFact.data_domain == "homeroom",
            wm.ScoreFact.exam_name == EXAM_E1,
            wm.ScoreFact.identity_id == v1_seed.jia_h_id,
            wm.ScoreFact.subject == SUBJECT,
            wm.ScoreFact.total_type.is_(None),
        )
        .one()
    )
    jia_physics.grade_percentile = 0.55
    jia_physics.grade_score = 67
    for person_id, percentile, grade_score in (
        (v1_seed.yi_h_id, 0.50, 58),
        (v1_seed.bing_h_id, 0.10, 70),
    ):
        fact = (
            db_session.query(wm.ScoreFact)
            .filter(
                wm.ScoreFact.data_domain == "homeroom",
                wm.ScoreFact.exam_name == EXAM_E1,
                wm.ScoreFact.identity_id == person_id,
                wm.ScoreFact.subject == SUBJECT,
                wm.ScoreFact.total_type.is_(None),
            )
            .one()
        )
        fact.grade_percentile = percentile
        fact.grade_score = grade_score

    def add_history(identity_id, exam_name, exam_date, score, rank):
        db_session.add(
            wm.ScoreFact(
                data_domain="homeroom",
                academic_year_id=v1_seed.ay_id,
                exam_name=exam_name,
                exam_date=exam_date,
                class_ref_id=v1_seed.h6_id,
                identity_id=identity_id,
                subject="总分",
                total_type=TOTAL_TYPE_MAIN3,
                score=score,
                xueji_rank=rank,
                source="synthetic-test",
            )
        )

    # 甲：最近一次前进 80 名且三场极差 160；乙：最近退步 170 名且波动；
    # 丙：连续位于高分段且波动很小，用于覆盖旧版全部关注维度。
    for person_id, ranks in (
        (v1_seed.jia_h_id, (610, 530)),
        (v1_seed.yi_h_id, (250, 350)),
        (v1_seed.bing_h_id, (60, 55)),
    ):
        add_history(person_id, "2025九月考", date(2025, 9, 10), 200.0, ranks[0])
        add_history(person_id, "2025十月考", date(2025, 10, 10), 220.0, ranks[1])
    db_session.commit()

    params = {"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id}
    response = client.get(
        f"{API}/homeroom/analysis/exams/{EXAM_E1}/focus", params=params
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["basis"] == "school_rank_and_grade_percentile"
    assert body["metadata"]["membership_basis"] == "exam"
    assert [row["person_id"] for row in body["students"]] == [
        v1_seed.bing_h_id,
        v1_seed.jia_h_id,
        v1_seed.yi_h_id,
    ]
    by_person = {row["person_id"]: row for row in body["students"]}
    assert by_person[v1_seed.bing_h_id]["issues"] == ["稳定优秀"]
    assert by_person[v1_seed.jia_h_id]["issues"] == [
        "明显进步",
        "波动风险",
        "临界段",
        f"严重偏科（{SUBJECT}）",
    ]
    assert by_person[v1_seed.jia_h_id]["weak_subjects"] == [SUBJECT]
    assert by_person[v1_seed.jia_h_id]["rank_change"] == 80
    assert by_person[v1_seed.jia_h_id]["rank_range"] == 160
    assert by_person[v1_seed.yi_h_id]["issues"] == ["明显退步", "波动风险", "薄弱段"]
    assert by_person[v1_seed.yi_h_id]["rank_change"] == -170
    assert body["config"]["progress_rank_threshold"] == 80
    assert body["config"]["volatility_rank_threshold"] == 120

    bands = client.get(
        f"{API}/homeroom/analysis/bands",
        params={
            **params,
            "exam_name": EXAM_E1,
            "subject": TOTAL_TYPE_MAIN3,
            "metric": "total",
        },
    )
    assert bands.status_code == 200, bands.text
    result = bands.json()
    assert result["total_type"] == TOTAL_TYPE_MAIN3
    assert [row["count"] for row in result["bands"]] == [1, 1, 1]
    assert result["bands"][0]["students"] == [v1_seed.bing_h_id]
    assert result["bands"][1]["students"] == [v1_seed.jia_h_id]
    assert result["bands"][2]["students"] == [v1_seed.yi_h_id]


def test_legacy_exam_detail_rank_sections(client, v1_seed, db_session):
    from app.api.analysis import _distribution_total_types, _rank_metric_options
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
    totals[v1_seed.jia_h_id].xueji_rank = 90
    totals[v1_seed.yi_h_id].xueji_rank = 130
    totals[v1_seed.bing_h_id].xueji_rank = 520
    for fact, percentile, grade_score in (
        (v1_seed.jia_h_id, 0.15, 70),
        (v1_seed.yi_h_id, 0.45, 61),
        (v1_seed.bing_h_id, 0.85, 43),
    ):
        physics = (
            db_session.query(wm.ScoreFact)
            .filter(
                wm.ScoreFact.data_domain == "homeroom",
                wm.ScoreFact.exam_name == EXAM_E1,
                wm.ScoreFact.identity_id == fact,
                wm.ScoreFact.subject == SUBJECT,
                wm.ScoreFact.total_type.is_(None),
            )
            .one()
        )
        physics.grade_percentile = percentile
        physics.grade_score = grade_score
    db_session.commit()

    params = {"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id}
    metrics = client.get(
        f"{API}/homeroom/analysis/rank-metrics",
        params={**params, "mode": "frequency"},
    )
    assert metrics.status_code == 200, metrics.text
    values = [row["value"] for row in metrics.json()["metrics"]]
    assert "subject_grade:物理" in values
    assert "total:主三门" in values and "total:3+3" in values

    distribution = client.get(
        f"{API}/homeroom/analysis/exams/{EXAM_E1}/rank-distribution",
        params=params,
    )
    assert distribution.status_code == 200, distribution.text
    body = distribution.json()
    assert [row["total_type"] for row in body["series"]] == ["主三门", "3+3"]
    main3 = body["series"][0]["counts"]
    assert main3["r81_120"] == 1
    assert main3["r121_160"] == 1
    assert main3["r481_520"] == 1

    frequency = client.get(
        f"{API}/homeroom/analysis/rank-frequency",
        params={**params, "metric": "subject_grade:物理", "exam_names": EXAM_E1},
    )
    assert frequency.status_code == 200, frequency.text
    by_person = {row["person_id"]: row for row in frequency.json()["students"]}
    assert by_person[v1_seed.jia_h_id]["counts"]["g70"] == 1
    assert by_person[v1_seed.bing_h_id]["counts"]["g43"] == 1

    rank_range = client.get(
        f"{API}/homeroom/analysis/exams/{EXAM_E1}/rank-range",
        params={**params, "metric": "total:主三门", "rank_min": 80, "rank_max": 150},
    )
    assert rank_range.status_code == 200, rank_range.text
    assert [row["person_id"] for row in rank_range.json()["students"]] == [
        v1_seed.jia_h_id,
        v1_seed.yi_h_id,
    ]

    # 高一沿用旧页三总分图例；高二/高三为主三门与 3+3。
    assert _distribution_total_types(1) == ("主三门", "五门", "九门")
    assert _distribution_total_types(2) == ("主三门", "3+3")
    # 高一排名频次沿用旧页的主三门/五门指标。
    assert [row["value"] for row in _rank_metric_options(1, "frequency") if row["kind"] == "total_rank"] == [
        "total:主三门",
        "total:五门",
    ]
