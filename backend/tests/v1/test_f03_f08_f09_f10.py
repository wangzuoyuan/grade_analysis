"""审核 F03/F08/F09/F10 回归（读侧口径统一）。

- F03：名册响应的共享分数字段逐 fact 过【考试时点】五条件门——入班日
  晚于考试日时 shared_subject_score/shared_conflict 一律不出现（名册
  行本身仍按查询时点）。
- F08：考试维度端点（exams/{name}/stats|students、class-compare）按
  【考试发生时名册】解析成员，考后离班/考后入班不改写历史人群；
  metadata 注明 membership_basis="exam"。
- F09：统一可读事实口径——H-only 事实从 teaching 可读（考试下拉/
  统计），T-only 事实从 homeroom 可读；取消关联后全部消失。
  readable_exam_summaries 为 /shared/exams 的接线入口（集成者接线，
  本文件直接对 _queries 断言）。
- F10：bands 段位阈值口径为年级名次，域内无名次事实 → 一律 409。

在 tests/v1/conftest.py 合成样本（v1_seed）之上 ORM 直种场景；所有
成员有效期/关联状态改动均在 finally 恢复，不污染同模块其他用例。
"""

from datetime import date
from urllib.parse import quote

import pytest

API = "/api/v1"
EXAM_E1 = "2025期中"  # v1_seed 原样：exam_date=2025-11-06
DAY_AFTER_EXAM = date(2025, 11, 10)
LEFT_AFTER_EXAM = date(2026, 8, 1)  # 早于运行时“今天”，晚于样本考试

EXAM_H_ONLY = "F09T投影考"  # 仅 H 域有物理事实（teaching 经反向投影可读）
EXAM_T_ONLY = "F09H投影考"  # 仅 T 域有物理事实（homeroom 经正向投影可读）
BANDS_DETAIL = "段位阈值口径为年级名次，当前范围无名次数据，不可计算"


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _enrollment(db, seed, h_id):
    from app.db import workspace_models as wm

    return (
        db.query(wm.Enrollment)
        .filter_by(admin_class_id=seed.h6_id, identity_id=h_id)
        .one()
    )


def _member(db, seed, t_id):
    from app.db import workspace_models as wm

    return (
        db.query(wm.TeachingClassMember)
        .filter_by(teaching_class_id=seed.t6_id, identity_id=t_id)
        .one()
    )


def _link(db, seed):
    from app.db import workspace_models as wm

    return db.get(wm.HomeroomTeachingLink, seed.link_id)


@pytest.fixture(scope="module")
def f09_seed(v1_seed):
    """补种 F09 场景：H-only 与 T-only 的单科物理事实（考试名唯一）。"""
    from app.db import workspace_models as wm

    s = v1_seed
    db = _db()
    db.add(
        wm.ScoreFact(
            data_domain="homeroom",
            academic_year_id=s.ay_id,
            exam_name=EXAM_H_ONLY,
            exam_date=date(2026, 1, 10),
            class_ref_id=s.h6_id,
            identity_id=s.jia_h_id,
            subject="物理",
            score=66.0,
            source="f03-f08-f09-f10-test",
        )
    )
    db.add(
        wm.ScoreFact(
            data_domain="teaching",
            academic_year_id=s.ay_id,
            exam_name=EXAM_T_ONLY,
            exam_date=date(2026, 2, 10),
            class_ref_id=s.t6_id,
            identity_id=s.jia_t_id,
            subject="物理",
            score=77.0,
            source="f03-f08-f09-f10-test",
        )
    )
    db.commit()
    yield
    db.close()


def _homeroom_ctx(seed):
    from app.core.context import resolve_workspace_context

    db = _db()
    ctx = resolve_workspace_context(
        db, 1, "homeroom", {"academic_year_id": seed.ay_id, "class_id": seed.h6_id}
    )
    return db, ctx


def _teaching_ctx(seed):
    from app.api import _queries as q
    from app.core.context import resolve_workspace_context

    db = _db()
    params, _subject, _ids = q.build_teaching_params(db, seed.ay_id, seed.t6_id, None)
    ctx = resolve_workspace_context(db, 1, "teaching", params)
    return db, ctx


# ────────────────────────────── F03 名册共享分数的考试时点门 ──────────────────────────────


def test_f03_roster_hides_shared_fields_when_enrolled_after_exam(client, v1_seed):
    """甲入班日 2025-11-10（考试 11-06）：名册行本身在（查询时点在班），
    但 shared_subject_score/shared_conflict 一律不得出现。"""
    db = _db()
    try:
        _enrollment(db, v1_seed, v1_seed.jia_h_id).valid_from = DAY_AFTER_EXAM
        db.commit()

        r = client.get(
            f"{API}/homeroom/students",
            params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
        )
        assert r.status_code == 200
        by_pid = {s["person_id"]: s for s in r.json()["students"]}
        assert v1_seed.jia_h_id in by_pid  # 名册按查询时点：甲在班
        assert by_pid[v1_seed.jia_h_id].get("shared_subject_score") is None
        assert by_pid[v1_seed.jia_h_id].get("shared_conflict") is None
        assert by_pid[v1_seed.yi_h_id].get("shared_conflict") == {
            "teaching_score": 85.0
        }  # 乙考试时点在班：冲突提示照常
    finally:
        _enrollment(db, v1_seed, v1_seed.jia_h_id).valid_from = date(2025, 9, 1)
        db.commit()
        db.close()


# ────────────────────────────── F08 考试维度按考试时点名册 ──────────────────────────────


def test_f08_left_after_exam_does_not_rewrite_history_stats(client, v1_seed):
    """甲考后离班（valid_to=2026-08-01）：E1 历史统计的人群与均分不变，
    membership_basis=exam；/scores（查询时点口径）则不再有甲的行。"""
    db = _db()
    try:
        _enrollment(db, v1_seed, v1_seed.jia_h_id).valid_to = LEFT_AFTER_EXAM
        db.commit()

        r = client.get(
            f"{API}/homeroom/analysis/exams/{quote(EXAM_E1)}/stats",
            params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["metadata"]["membership_basis"] == "exam"
        assert body["cohort_size"] == 3  # 甲实际参加了该场考试
        chinese = next(x for x in body["subjects"] if x["subject"] == "语文")
        assert chinese["avg"] == 76.33 and chinese["valid_count"] == 3

        # 非考试维度（/scores 用查询时点成员）：甲的行消失，历史统计不变
        r2 = client.get(
            f"{API}/scores",
            params={
                "mode": "homeroom",
                "academic_year_id": v1_seed.ay_id,
                "exam_name": EXAM_E1,
            },
        )
        assert r2.status_code == 200
        assert all(
            row["person_id"] != v1_seed.jia_h_id for row in r2.json()["rows"]
        )
    finally:
        _enrollment(db, v1_seed, v1_seed.jia_h_id).valid_to = None
        db.commit()
        db.close()


def test_f08_joined_after_exam_not_in_history(client, v1_seed):
    """丙考后入班（valid_from=2025-11-10）：E1 统计与学生表都按考试时点
    名册——丙不进历史考试的人群，无成绩成员也不编造。"""
    db = _db()
    try:
        _enrollment(db, v1_seed, v1_seed.bing_h_id).valid_from = DAY_AFTER_EXAM
        db.commit()

        r = client.get(
            f"{API}/homeroom/analysis/exams/{quote(EXAM_E1)}/stats",
            params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["cohort_size"] == 2
        chinese = next(x for x in body["subjects"] if x["subject"] == "语文")
        assert chinese["avg"] == 82.0 and chinese["valid_count"] == 2

        r2 = client.get(
            f"{API}/homeroom/analysis/exams/{quote(EXAM_E1)}/students",
            params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
        )
        assert r2.status_code == 200
        persons = {row["person_id"] for row in r2.json()["students"]}
        assert persons == {v1_seed.jia_h_id, v1_seed.yi_h_id}
    finally:
        _enrollment(db, v1_seed, v1_seed.bing_h_id).valid_from = date(2025, 9, 1)
        db.commit()
        db.close()


def test_f08_teaching_stats_uses_exam_time_members(client, v1_seed):
    """甲 T membership 考后失效：teaching E1 统计仍按考试时点 3 人
    （甲乙丁），均分 88 不被重算；metadata 注明 exam 口径。"""
    db = _db()
    try:
        _member(db, v1_seed, v1_seed.jia_t_id).valid_to = LEFT_AFTER_EXAM
        db.commit()

        r = client.get(
            f"{API}/teaching/analysis/exams/{quote(EXAM_E1)}/stats",
            params={
                "academic_year_id": v1_seed.ay_id,
                "teaching_class_id": v1_seed.t6_id,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["metadata"]["membership_basis"] == "exam"
        assert body["cohort_size"] == 3
        assert body["valid_count"] == 2 and body["avg"] == 88.0
    finally:
        _member(db, v1_seed, v1_seed.jia_t_id).valid_to = None
        db.commit()
        db.close()


# ────────────────────────────── F09 统一可读事实口径 ──────────────────────────────


def test_f09_h_only_exam_readable_from_teaching(client, v1_seed, f09_seed):
    """H-only 物理事实：teaching readable_exam_summaries 列出该考试，
    teaching 统计含该科（反向投影计入均分/分母）。"""
    from app.api import _queries as q

    s = v1_seed
    db, ctx = _teaching_ctx(s)
    try:
        summaries = q.readable_exam_summaries(db, ctx)
        names = [e["exam_name"] for e in summaries]
        assert EXAM_H_ONLY in names  # H-only 考试出现在 teaching 考试清单
        entry = next(e for e in summaries if e["exam_name"] == EXAM_H_ONLY)
        assert entry["subjects"] == ["物理"] and entry["row_count"] == 1
    finally:
        db.close()

    r = client.get(
        f"{API}/teaching/analysis/exams/{quote(EXAM_H_ONLY)}/stats",
        params={"academic_year_id": s.ay_id, "teaching_class_id": s.t6_id},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["valid_count"] == 1 and body["avg"] == 66.0
    assert body["cohort_size"] == 3  # 考试时点 T6 成员甲乙丁


def test_f09_t_only_exam_readable_from_homeroom(client, v1_seed, f09_seed):
    """T-only 物理事实：homeroom readable_exam_summaries 列出该考试，
    homeroom 统计 subjects 含该科，/scores 出现 teaching 来源投影行。"""
    from app.api import _queries as q

    s = v1_seed
    db, ctx = _homeroom_ctx(s)
    try:
        summaries = q.readable_exam_summaries(db, ctx)
        names = [e["exam_name"] for e in summaries]
        assert EXAM_T_ONLY in names  # T-only 考试出现在 homeroom 考试清单
        entry = next(e for e in summaries if e["exam_name"] == EXAM_T_ONLY)
        assert entry["subjects"] == ["物理"] and entry["row_count"] == 1
        # 冲突事实不放大行数：E1 仍为本域 15 行（甲乙 T 值只作冲突提示）
        e1 = next(e for e in summaries if e["exam_name"] == EXAM_E1)
        assert e1["row_count"] == 15
    finally:
        db.close()

    r = client.get(
        f"{API}/homeroom/analysis/exams/{quote(EXAM_T_ONLY)}/stats",
        params={"academic_year_id": s.ay_id, "class_id": s.h6_id},
    )
    assert r.status_code == 200
    body = r.json()
    assert [x["subject"] for x in body["subjects"]] == ["物理"]
    assert body["subjects"][0]["avg"] == 77.0 and body["subjects"][0]["valid_count"] == 1

    r2 = client.get(
        f"{API}/scores",
        params={
            "mode": "homeroom",
            "academic_year_id": s.ay_id,
            "exam_name": EXAM_T_ONLY,
        },
    )
    assert r2.status_code == 200
    rows = r2.json()["rows"]
    jia = next(
        row
        for row in rows
        if row["person_id"] == s.jia_h_id and row["subject"] == "物理"
    )
    assert jia["score"] == 77.0 and jia["source_domain"] == "teaching"


def test_f09_cancel_link_removes_all_projections(client, v1_seed, f09_seed):
    """取消关联：H-only/T-only 考试在对方域的清单与统计中全部消失。"""
    from app.api import _queries as q

    s = v1_seed
    db = _db()
    try:
        _link(db, s).status = "cancelled"
        db.commit()
    finally:
        pass

    try:
        db2, h_ctx = _homeroom_ctx(s)
        try:
            h_names = [
                e["exam_name"] for e in q.readable_exam_summaries(db2, h_ctx)
            ]
            assert EXAM_T_ONLY not in h_names
            assert EXAM_H_ONLY in h_names  # H 本域事实保留
        finally:
            db2.close()

        db3, t_ctx = _teaching_ctx(s)
        try:
            t_names = [
                e["exam_name"] for e in q.readable_exam_summaries(db3, t_ctx)
            ]
            assert EXAM_H_ONLY not in t_names
            assert EXAM_T_ONLY in t_names  # T 本域事实保留
        finally:
            db3.close()

        # homeroom 统计：域内完全不存在该考试 → 404 越界（不投影）
        r = client.get(
            f"{API}/homeroom/analysis/exams/{quote(EXAM_T_ONLY)}/stats",
            params={"academic_year_id": s.ay_id, "class_id": s.h6_id},
        )
        assert r.status_code == 404
        assert r.json()["error"] == "resource_out_of_scope"

        # teaching 统计：H-only 考试仍存在于域内（H 域事实），但无投影行 → 200 空态
        r2 = client.get(
            f"{API}/teaching/analysis/exams/{quote(EXAM_H_ONLY)}/stats",
            params={
                "academic_year_id": s.ay_id,
                "teaching_class_id": s.t6_id,
            },
        )
        assert r2.status_code == 200
        assert r2.json()["valid_count"] == 0
    finally:
        db4 = _db()
        try:
            _link(db4, s).status = "active"
            db4.commit()
        finally:
            db4.close()


# ────────────────────────────── F10 bands 名次阈值不可镜像 ──────────────────────────────


def test_f10_bands_returns_409_with_contract_detail(client, v1_seed):
    """F10：名次阈值 400/500 不允许当分数比较 → bands 一律 409，
    detail 为契约冻结文案。"""
    s = v1_seed
    for metric, subject in (("score", "物理"), ("total", "主三门")):
        r = client.get(
            f"{API}/homeroom/analysis/bands",
            params={
                "exam_name": EXAM_E1,
                "subject": subject,
                "metric": metric,
                "academic_year_id": s.ay_id,
                "class_id": s.h6_id,
            },
        )
        assert r.status_code == 409, r.text
        body = r.json()
        assert body["error"] == "invalid_scope_param"
        assert body["detail"] == BANDS_DETAIL
