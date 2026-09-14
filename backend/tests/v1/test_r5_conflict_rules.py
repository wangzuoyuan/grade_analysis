"""R5 回归：两域冲突绝不静默取对方值；仅一侧有事实时按门投影（§1.4.1）。

样本天然冲突：甲 H 物理 90 / T 物理 91、乙 H 物理 84 / T 物理 85。
- homeroom 侧：保留 H 值并附 shared_conflict={"teaching_score": ...}，
  91/85 不得成为独立行；
- teaching 侧：保留 T 值、不附字段；T 域事实删除后 H 值 90 反向投影进来；
- H 域无该场考试的 T 事实（构造 E2）→ 正向投影为 teaching 来源行。
"""

from datetime import date

API = "/api/v1"
EXAM_E1 = "2025期中"
EXAM_E2 = "2025期末"
EXAM_E2_DATE = date(2026, 1, 15)


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _scores(client, seed, mode, exam_name=None):
    params = {"mode": mode, "academic_year_id": seed.ay_id}
    if exam_name:
        params["exam_name"] = exam_name
    r = client.get(f"{API}/scores", params=params)
    assert r.status_code == 200
    return r.json()["rows"]


def _jia_t_fact(db, seed):
    from app.db import workspace_models as wm

    return (
        db.query(wm.ScoreFact)
        .filter_by(
            data_domain="teaching",
            academic_year_id=seed.ay_id,
            exam_name=EXAM_E1,
            identity_id=seed.jia_t_id,
            subject="物理",
        )
        .one()
    )


def test_homeroom_conflict_keeps_own_value_with_marker(client, v1_seed):
    """H 物理 90 / T 物理 91：H 行保留 90 并附 shared_conflict；
    91 不得出现为行（替换式投影被禁止）；行数不放大。"""
    rows = _scores(client, v1_seed, "homeroom", EXAM_E1)

    jia_phys = [
        r
        for r in rows
        if r["person_id"] == v1_seed.jia_h_id and r.get("subject") == "物理"
    ]
    assert len(jia_phys) == 1
    assert jia_phys[0]["score"] == 90.0
    assert jia_phys[0]["source_domain"] == "homeroom"
    assert jia_phys[0]["shared_conflict"] == {"teaching_score": 91.0}
    assert not any(
        r["person_id"] == v1_seed.jia_h_id and r["score"] == 91.0 for r in rows
    )

    yi_phys = [
        r
        for r in rows
        if r["person_id"] == v1_seed.yi_h_id and r.get("subject") == "物理"
    ]
    assert yi_phys[0]["score"] == 84.0
    assert yi_phys[0]["shared_conflict"] == {"teaching_score": 85.0}

    # 丙未关联：无冲突提示；总行数恰 3 人 × (4 科 + 1 总分)
    bing_phys = [
        r
        for r in rows
        if r["person_id"] == v1_seed.bing_h_id and r.get("subject") == "物理"
    ]
    assert bing_phys[0].get("shared_conflict") is None
    assert len(rows) == 15


def test_teaching_rows_keep_own_value_without_conflict_field(client, v1_seed):
    """T /scores：甲 91 保留、行内无 shared_conflict 键；T 域已有同场
    事实 → H 值 90 绝不投影进来（无 homeroom 来源行）。"""
    rows = _scores(client, v1_seed, "teaching", EXAM_E1)

    jia = [r for r in rows if r["person_id"] == v1_seed.jia_t_id]
    assert len(jia) == 1
    assert jia[0]["subject"] == "物理"
    assert jia[0]["score"] == 91.0
    assert jia[0]["source_domain"] == "teaching"
    assert "shared_conflict" not in jia[0]
    assert all(r.get("source_domain") == "teaching" for r in rows)


def test_profile_conflict_semantics_on_both_sides(client, v1_seed):
    """画像冲突语义：H 侧物理条目保留 90 + shared_conflict；
    T 侧物理条目保留 91、不附字段。"""
    r = client.get(f"{API}/homeroom/students/{v1_seed.jia_h_id}")
    assert r.status_code == 200
    physics_h = next(
        g for g in r.json()["subjects"] if g["subject"] == "物理"
    )["exams"]
    assert len(physics_h) == 1
    assert physics_h[0]["score"] == 90.0
    assert physics_h[0]["source_domain"] == "homeroom"
    assert physics_h[0]["shared_conflict"] == {"teaching_score": 91.0}

    r = client.get(f"{API}/teaching/students/{v1_seed.jia_t_id}")
    assert r.status_code == 200
    physics_t = next(
        g for g in r.json()["subjects"] if g["subject"] == "物理"
    )["exams"]
    assert len(physics_t) == 1
    assert physics_t[0]["score"] == 91.0
    assert physics_t[0]["source_domain"] == "teaching"
    assert physics_t[0].get("shared_conflict") is None


def test_t_fact_removed_homeroom_value_projects_into_teaching(client, v1_seed):
    """删除甲的 T 物理 91：T /scores 与 T 画像的甲物理变 H 投影 90
    （source_domain="homeroom"）；H 侧冲突提示随对方事实消失而消失。"""
    db = _db()
    try:
        fact = _jia_t_fact(db, v1_seed)
        db.delete(fact)
        db.commit()

        rows = _scores(client, v1_seed, "teaching", EXAM_E1)
        jia = [r for r in rows if r["person_id"] == v1_seed.jia_t_id]
        assert len(jia) == 1
        assert jia[0]["score"] == 90.0
        assert jia[0]["source_domain"] == "homeroom"

        h_rows = _scores(client, v1_seed, "homeroom", EXAM_E1)
        jia_h = [
            r
            for r in h_rows
            if r["person_id"] == v1_seed.jia_h_id and r.get("subject") == "物理"
        ]
        assert len(jia_h) == 1
        assert jia_h[0]["score"] == 90.0
        assert jia_h[0].get("shared_conflict") is None

        r = client.get(f"{API}/teaching/students/{v1_seed.jia_t_id}")
        assert r.status_code == 200
        physics_t = next(
            g for g in r.json()["subjects"] if g["subject"] == "物理"
        )["exams"]
        assert len(physics_t) == 1
        assert physics_t[0]["score"] == 90.0
        assert physics_t[0]["source_domain"] == "homeroom"
    finally:
        # 恢复甲的 T 域事实（自然键与被删行一致，重建即还原样本）
        from app.db import workspace_models as wm

        db.add(
            wm.ScoreFact(
                data_domain="teaching",
                academic_year_id=v1_seed.ay_id,
                exam_name=EXAM_E1,
                exam_date=date(2025, 11, 6),
                class_ref_id=v1_seed.t6_id,
                identity_id=v1_seed.jia_t_id,
                subject="物理",
                score=91.0,
                source="synthetic-test",
            )
        )
        db.commit()
        db.close()


def test_t_only_exam_projects_into_homeroom(client, v1_seed):
    """构造 E2（H 域无该场物理事实，T 域甲 93）：H /scores 出现
    (甲, 物理, 93, source_domain="teaching") 投影行；E1 冲突行不变；
    名册最近一场变为 E2 → shared_subject_score=93 且无冲突。"""
    db = _db()
    try:
        from app.db import workspace_models as wm

        db.add(
            wm.ScoreFact(
                data_domain="teaching",
                academic_year_id=v1_seed.ay_id,
                exam_name=EXAM_E2,
                exam_date=EXAM_E2_DATE,
                class_ref_id=v1_seed.t6_id,
                identity_id=v1_seed.jia_t_id,
                subject="物理",
                score=93.0,
                source="synthetic-test",
            )
        )
        db.commit()

        rows = _scores(client, v1_seed, "homeroom")
        jia_phys = [
            r
            for r in rows
            if r["person_id"] == v1_seed.jia_h_id and r.get("subject") == "物理"
        ]
        by_exam = {r["score"]: r for r in jia_phys}
        # E1 冲突：保留 90 + 提示 91；E2 仅 T 域有：投影 93
        assert len(jia_phys) == 2
        assert by_exam[90.0]["shared_conflict"] == {"teaching_score": 91.0}
        assert by_exam[93.0]["source_domain"] == "teaching"
        assert by_exam[93.0].get("shared_conflict") is None

        # teaching 侧本域两场齐全，无 H 投影行
        t_rows = _scores(client, v1_seed, "teaching")
        jia_t = [r for r in t_rows if r["person_id"] == v1_seed.jia_t_id]
        assert {r["score"] for r in jia_t} == {91.0, 93.0}
        assert all(r["source_domain"] == "teaching" for r in jia_t)

        # 名册共享取最近一场（E2 无冲突）→ shared_subject_score
        r = client.get(
            f"{API}/homeroom/students",
            params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
        )
        assert r.status_code == 200
        jia = next(
            s for s in r.json()["students"] if s["person_id"] == v1_seed.jia_h_id
        )
        shared = jia.get("shared_subject_score")
        assert shared is not None
        assert shared["score"] == 93.0
        assert shared["exam_name"] == EXAM_E2
        assert shared["source_domain"] == "teaching"
        assert jia.get("shared_conflict") is None
    finally:
        from app.db import workspace_models as wm

        (
            db.query(wm.ScoreFact)
            .filter_by(
                data_domain="teaching",
                academic_year_id=v1_seed.ay_id,
                exam_name=EXAM_E2,
                identity_id=v1_seed.jia_t_id,
            )
            .delete()
        )
        db.commit()
        db.close()
