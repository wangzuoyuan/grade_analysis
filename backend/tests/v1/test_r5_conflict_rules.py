"""R5 回归（2026-10 口径变更）：两域成绩不互通，各域只读本域事实。

样本天然冲突：甲 H 物理 90 / T 物理 91、乙 H 物理 84 / T 物理 85。
2026-10 两域成绩不互通：readable_facts 不再做任何跨域成绩投影，
- homeroom 侧：保留 H 值、不附 shared_conflict（冲突标记不再产生）；
- teaching 侧：保留 T 值、无 H 反向投影；T 域事实删除后该生无行；
- H 域无该场考试的 T 事实（构造 E2）→ H 侧不可见，名册共享分数字段为空。
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


def test_homeroom_rows_keep_own_value_without_cross_domain_marker(client, v1_seed):
    """H 物理 90 / T 物理 91：H 行保留 90；2026-10 两域成绩不互通——
    不再附 shared_conflict，91 不得出现为行；行数不放大。"""
    rows = _scores(client, v1_seed, "homeroom", EXAM_E1)

    jia_phys = [
        r
        for r in rows
        if r["person_id"] == v1_seed.jia_h_id and r.get("subject") == "物理"
    ]
    assert len(jia_phys) == 1
    assert jia_phys[0]["score"] == 90.0
    assert jia_phys[0]["source_domain"] == "homeroom"
    # 2026-10 两域成绩不互通：T 域 91 不再投影/不再产生冲突标记
    assert jia_phys[0].get("shared_conflict") is None
    assert not any(
        r["person_id"] == v1_seed.jia_h_id and r["score"] == 91.0 for r in rows
    )

    yi_phys = [
        r
        for r in rows
        if r["person_id"] == v1_seed.yi_h_id and r.get("subject") == "物理"
    ]
    assert yi_phys[0]["score"] == 84.0
    # 2026-10 两域成绩不互通：乙同样只有本域值、无冲突提示
    assert yi_phys[0].get("shared_conflict") is None

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


def test_profile_isolated_on_both_sides(client, v1_seed):
    """画像隔离语义（2026-10 口径变更）：H 侧物理条目保留 90、无冲突
    提示；T 侧物理条目保留 91、不附字段。"""
    r = client.get(f"{API}/homeroom/students/{v1_seed.jia_h_id}")
    assert r.status_code == 200
    physics_h = next(
        g for g in r.json()["subjects"] if g["subject"] == "物理"
    )["exams"]
    assert len(physics_h) == 1
    assert physics_h[0]["score"] == 90.0
    assert physics_h[0]["source_domain"] == "homeroom"
    # 2026-10 两域成绩不互通：画像不再并入教学域冲突提示
    assert physics_h[0].get("shared_conflict") is None

    r = client.get(f"{API}/teaching/students/{v1_seed.jia_t_id}")
    assert r.status_code == 200
    physics_t = next(
        g for g in r.json()["subjects"] if g["subject"] == "物理"
    )["exams"]
    assert len(physics_t) == 1
    assert physics_t[0]["score"] == 91.0
    assert physics_t[0]["source_domain"] == "teaching"
    assert physics_t[0].get("shared_conflict") is None


def test_t_fact_removed_leaves_no_teaching_row_no_projection(client, v1_seed):
    """删除甲的 T 物理 91（2026-10 口径变更）：H 值 90 不再反向投影——
    T /scores 的甲无物理行、T 画像物理组缺失；乙/丁本域行不受影响；
    H 侧本域 90 照常可读且无冲突提示。"""
    db = _db()
    try:
        fact = _jia_t_fact(db, v1_seed)
        db.delete(fact)
        db.commit()

        rows = _scores(client, v1_seed, "teaching", EXAM_E1)
        # 2026-10 两域成绩不互通：H 值 90 不再投影进教学域
        jia = [r for r in rows if r["person_id"] == v1_seed.jia_t_id]
        assert jia == []
        # 乙/丁的本域教学行不受影响
        assert v1_seed.yi_t_id in {r["person_id"] for r in rows}
        assert v1_seed.ding_t_id in {r["person_id"] for r in rows}

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
        # 2026-10 两域成绩不互通：T 画像物理组缺失（无 H 投影条目）
        assert r.json()["subjects"] == []
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


def test_t_only_exam_not_visible_in_homeroom(client, v1_seed):
    """构造 E2（H 域无该场物理事实，T 域甲 93）（2026-10 口径变更）：
    H /scores 不出现 T-only 投影行（甲物理只剩 E1 本域 90）；
    teaching 侧本域两场齐全；名册 shared_subject_score 恒空、
    linked_teaching_class_id 仍在（名册/关联共享不受影响）。"""
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
        # 2026-10 两域成绩不互通：T-only 的 E2 不投影进 H——甲物理只剩 E1 的 90
        assert len(jia_phys) == 1
        assert jia_phys[0]["score"] == 90.0
        assert jia_phys[0]["source_domain"] == "homeroom"
        assert jia_phys[0].get("shared_conflict") is None
        assert not any(r["score"] == 93.0 for r in rows)

        # teaching 侧本域两场齐全，无 H 投影行
        t_rows = _scores(client, v1_seed, "teaching")
        jia_t = [r for r in t_rows if r["person_id"] == v1_seed.jia_t_id]
        assert {r["score"] for r in jia_t} == {91.0, 93.0}
        assert all(r["source_domain"] == "teaching" for r in jia_t)

        # 名册：成绩共享字段恒空（不再取对侧最近一场），关联标记保留
        r = client.get(
            f"{API}/homeroom/students",
            params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
        )
        assert r.status_code == 200
        jia = next(
            s for s in r.json()["students"] if s["person_id"] == v1_seed.jia_h_id
        )
        # 2026-10 两域成绩不互通：名册不再共享任教学科分数（含最近一场）
        assert jia.get("shared_subject_score") is None
        assert jia.get("shared_conflict") is None
        assert jia.get("linked_teaching_class_id") == v1_seed.t6_id
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
