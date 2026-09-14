"""R3 回归：共享类别白名单与历史日期边界（契约 §1.2.2 + §1.4.1 条件 2/3）。

- share_categories 不含 current_subject_score → 成绩零投影（含冲突提示），
  roster 名册投影不受影响；
- 考试日期 < share_history_from ?? valid_from → 不投影：
  valid_from 晚于考试即阻断；share_history_from 设为考试当日可恢复授权
  （>= 含等号），设为考试次日再次阻断；
- exam_date 为 NULL 的事实一律不共享（未知日期不得默认放开）。
"""

from datetime import date

API = "/api/v1"
EXAM_E1 = "2025期中"
EXAM_E1_DATE = date(2025, 11, 6)
LINK_VALID_FROM = date(2025, 9, 1)
DEFAULT_CATEGORIES = "roster,current_subject_score"


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _link(db, seed):
    from app.db import workspace_models as wm

    link = db.get(wm.HomeroomTeachingLink, seed.link_id)
    assert link is not None
    return link


def _homeroom_scores(client, seed):
    r = client.get(
        f"{API}/scores",
        params={"mode": "homeroom", "academic_year_id": seed.ay_id, "exam_name": EXAM_E1},
    )
    assert r.status_code == 200
    return r.json()["rows"]


def _homeroom_students(client, seed):
    r = client.get(
        f"{API}/homeroom/students",
        params={"academic_year_id": seed.ay_id, "class_id": seed.h6_id},
    )
    assert r.status_code == 200
    return {s["person_id"]: s for s in r.json()["students"]}


def test_roster_only_disables_score_projection(client, v1_seed):
    """share_categories='roster'：成绩零投影、零冲突提示；
    name/seat 名册投影保留（roster 仍授权）。"""
    db = _db()
    try:
        _link(db, v1_seed).share_categories = "roster"
        db.commit()

        rows = _homeroom_scores(client, v1_seed)
        assert all(r.get("source_domain") != "teaching" for r in rows)
        assert all(r.get("shared_conflict") is None for r in rows)
        assert not any(r["score"] in (91.0, 85.0) for r in rows)

        by_pid = _homeroom_students(client, v1_seed)
        for h_id in (v1_seed.jia_h_id, v1_seed.yi_h_id):
            assert by_pid[h_id].get("shared_subject_score") is None
            assert by_pid[h_id].get("shared_conflict") is None

        # roster 名册投影不受成绩类别关闭影响：T6 名册仍显示 H 域名
        r = client.get(
            f"{API}/teaching/students",
            params={
                "academic_year_id": v1_seed.ay_id,
                "teaching_class_id": v1_seed.t6_id,
            },
        )
        assert r.status_code == 200
        t_by_pid = {s["person_id"]: s for s in r.json()["students"]}
        assert t_by_pid[v1_seed.jia_t_id]["name"] == "秦甲"
    finally:
        _link(db, v1_seed).share_categories = DEFAULT_CATEGORIES
        db.commit()
        db.close()


def test_link_valid_from_after_exam_blocks_history(client, v1_seed):
    """valid_from 改为今天：样本考试（2025-11-06）早于关联生效 →
    成绩与名册成绩共享全部不投影（关联前历史默认关闭）。"""
    db = _db()
    try:
        _link(db, v1_seed).valid_from = date.today()
        db.commit()

        rows = _homeroom_scores(client, v1_seed)
        assert all(r.get("source_domain") != "teaching" for r in rows)
        assert all(r.get("shared_conflict") is None for r in rows)
        jia_phys = [
            r
            for r in rows
            if r["person_id"] == v1_seed.jia_h_id and r.get("subject") == "物理"
        ]
        assert len(jia_phys) == 1 and jia_phys[0]["score"] == 90.0

        by_pid = _homeroom_students(client, v1_seed)
        assert by_pid[v1_seed.jia_h_id].get("shared_subject_score") is None
        assert by_pid[v1_seed.jia_h_id].get("shared_conflict") is None
    finally:
        _link(db, v1_seed).valid_from = LINK_VALID_FROM
        db.commit()
        db.close()


def test_share_history_from_boundary(client, v1_seed):
    """share_history_from=考试当日 → 恰好恢复授权（>= 含等号）；
    设为考试次日 → 再次阻断。"""
    db = _db()
    try:
        # 次日授权：边界外一天即不共享
        _link(db, v1_seed).share_history_from = date(2025, 11, 7)
        db.commit()
        rows = _homeroom_scores(client, v1_seed)
        assert all(r.get("shared_conflict") is None for r in rows)
        assert not any(r["score"] in (91.0, 85.0) for r in rows)

        # 当日授权：恢复共享（冲突按 §1.4.1 报提示，不静默取值）
        _link(db, v1_seed).share_history_from = EXAM_E1_DATE
        db.commit()
        rows = _homeroom_scores(client, v1_seed)
        jia_phys = [
            r
            for r in rows
            if r["person_id"] == v1_seed.jia_h_id and r.get("subject") == "物理"
        ]
        assert len(jia_phys) == 1
        assert jia_phys[0]["score"] == 90.0
        assert jia_phys[0].get("shared_conflict") == {"teaching_score": 91.0}
    finally:
        _link(db, v1_seed).share_history_from = None
        db.commit()
        db.close()


def test_null_exam_date_fact_never_shared(client, v1_seed):
    """T 域事实 exam_date 置 NULL：未知考试日期不得默认放开，
    成绩投影与名册成绩共享一律不出现。"""
    db = _db()
    try:
        from app.db import workspace_models as wm

        facts = (
            db.query(wm.ScoreFact)
            .filter_by(
                data_domain="teaching",
                academic_year_id=v1_seed.ay_id,
                exam_name=EXAM_E1,
                subject="物理",
            )
            .filter(
                wm.ScoreFact.identity_id.in_(
                    [v1_seed.jia_t_id, v1_seed.yi_t_id, v1_seed.ding_t_id]
                )
            )
            .all()
        )
        assert len(facts) == 3
        for f in facts:
            f.exam_date = None
        db.commit()

        rows = _homeroom_scores(client, v1_seed)
        assert all(r.get("source_domain") != "teaching" for r in rows)
        assert all(r.get("shared_conflict") is None for r in rows)

        by_pid = _homeroom_students(client, v1_seed)
        assert by_pid[v1_seed.jia_h_id].get("shared_subject_score") is None
        assert by_pid[v1_seed.jia_h_id].get("shared_conflict") is None
    finally:
        from app.db import workspace_models as wm

        facts = (
            db.query(wm.ScoreFact)
            .filter_by(
                data_domain="teaching",
                academic_year_id=v1_seed.ay_id,
                exam_name=EXAM_E1,
                subject="物理",
            )
            .filter(
                wm.ScoreFact.identity_id.in_(
                    [v1_seed.jia_t_id, v1_seed.yi_t_id, v1_seed.ding_t_id]
                )
            )
            .all()
        )
        for f in facts:
            f.exam_date = EXAM_E1_DATE
        db.commit()
        db.close()
