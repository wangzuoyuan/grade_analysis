"""R2 回归：跨域投影必须与有效成员交集相交（契约 §1.4.1 条件 4）。

时点口径（契约 p1-api §1.3/§1.4.1 v2.1，F03 修订）：
- 成绩 fact 投影：按【考试时点 fact.exam_date】验证双侧成员；
- 名册行的姓名/座号投影：按【查询时点 ctx.as_of】验证双侧成员；
- 名册响应的共享分数字段（shared_subject_score/shared_conflict）：
  v2.1 起同样逐 fact 过考试时点门——入班日晚于考试日的成绩与冲突
  提示一律不得出现在名册响应中（仅查询时点在班不再 sufficient）。
已确认的 LinkedStudent 映射历史不等于永远允许共享；本文件所有用例
改动成员有效期后均在 finally 恢复，不污染同模块其他用例。
"""

from datetime import date

API = "/api/v1"
EXAM_E1 = "2025期中"
DAY_BEFORE_EXAM = date(2025, 11, 5)
DAY_AFTER_EXAM = date(2025, 11, 10)


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
    return r.json()["students"]


def test_h_enrollment_expired_before_exam_blocks_projection(client, v1_seed):
    """甲 H enrollment 截止在考试前一天：甲的 T 物理 91 绝不进 H /scores。"""
    db = _db()
    try:
        _enrollment(db, v1_seed, v1_seed.jia_h_id).valid_to = DAY_BEFORE_EXAM
        db.commit()

        rows = _homeroom_scores(client, v1_seed)
        # 甲已不在查询时点班级范围：任何行都不得是甲
        assert all(r["person_id"] != v1_seed.jia_h_id for r in rows)
        assert all(r.get("source_domain") != "teaching" for r in rows)
        assert not any(r["score"] == 91.0 for r in rows)
    finally:
        _enrollment(db, v1_seed, v1_seed.jia_h_id).valid_to = None
        db.commit()
        db.close()


def test_t_membership_expired_before_exam_blocks_projection(client, v1_seed):
    """甲乙 T membership 截止在考试前一天：H 侧成绩投影与名册共享同步
    消失；H 本域数据与 T 域本班名册不受影响。"""
    db = _db()
    try:
        for t_id in (v1_seed.jia_t_id, v1_seed.yi_t_id):
            _member(db, v1_seed, t_id).valid_to = DAY_BEFORE_EXAM
        db.commit()

        rows = _homeroom_scores(client, v1_seed)
        assert all(r.get("source_domain") != "teaching" for r in rows)
        for r in rows:
            # 值级断言：91/85 既不作为 score 也不作为冲突提示出现
            assert r["score"] not in (91.0, 85.0)
            assert r.get("shared_conflict") is None
        # 甲乙 H 本域行保留（对方退出不动本域数据）
        jia_phys = [
            r
            for r in rows
            if r["person_id"] == v1_seed.jia_h_id and r.get("subject") == "物理"
        ]
        assert len(jia_phys) == 1 and jia_phys[0]["score"] == 90.0

        # 名册共享分数字段同样按考试时点验证（F03）：T 侧考试日已失效
        # → shared_* 全部消失
        by_pid = {s["person_id"]: s for s in _homeroom_students(client, v1_seed)}
        for h_id in (v1_seed.jia_h_id, v1_seed.yi_h_id):
            assert by_pid[h_id].get("shared_subject_score") is None
            assert by_pid[h_id].get("shared_conflict") is None

        # teaching 域自身：甲乙不在当期成员，T /scores 只剩丁戊己
        r = client.get(
            f"{API}/scores",
            params={
                "mode": "teaching",
                "academic_year_id": v1_seed.ay_id,
                "exam_name": EXAM_E1,
            },
        )
        assert r.status_code == 200
        persons = {row["person_id"] for row in r.json()["rows"]}
        assert persons == {v1_seed.ding_t_id, v1_seed.wu_t_id, v1_seed.ji_t_id}
    finally:
        for t_id in (v1_seed.jia_t_id, v1_seed.yi_t_id):
            _member(db, v1_seed, t_id).valid_to = None
        db.commit()
        db.close()


def test_h_enrollment_after_exam_shares_roster_but_not_old_fact(client, v1_seed):
    """甲考试后才入班（valid_from=考试后一天，v2.1/F03 口径）：查询时点
    在班 → 甲仍在名册（姓名/座号按查询时点投影）；但 E1 考试时点不在
    班 → 该场成绩绝不投影进 /scores、不附冲突提示，名册响应也不得出现
    shared_subject_score/shared_conflict（共享分数一律逐 fact 过考试
    时点门）。"""
    db = _db()
    try:
        _enrollment(db, v1_seed, v1_seed.jia_h_id).valid_from = DAY_AFTER_EXAM
        db.commit()

        rows = _homeroom_scores(client, v1_seed)
        jia_phys = [
            r
            for r in rows
            if r["person_id"] == v1_seed.jia_h_id and r.get("subject") == "物理"
        ]
        assert len(jia_phys) == 1
        assert jia_phys[0]["score"] == 90.0
        assert jia_phys[0].get("shared_conflict") is None
        assert all(
            r.get("source_domain") != "teaching"
            for r in rows
            if r["person_id"] == v1_seed.jia_h_id
        )

        # F03：名册本身按查询时点（甲在班），但共享分数字段按考试时点
        # 门——甲的 T 物理 91（考试日在入班前）不得以任何形式出现
        by_pid = {s["person_id"]: s for s in _homeroom_students(client, v1_seed)}
        assert v1_seed.jia_h_id in by_pid
        assert by_pid[v1_seed.jia_h_id].get("shared_subject_score") is None
        assert by_pid[v1_seed.jia_h_id].get("shared_conflict") is None
    finally:
        _enrollment(db, v1_seed, v1_seed.jia_h_id).valid_from = date(2025, 9, 1)
        db.commit()
        db.close()


def test_empty_membership_intersection_stops_all_projection(client, v1_seed):
    """甲乙双侧均退出（截止在考试前）：成绩与名册共享全部停止；
    LinkedStudent 映射行保留（停止共享 ≠ 删除确认历史）。"""
    db = _db()
    try:
        for h_id in (v1_seed.jia_h_id, v1_seed.yi_h_id):
            _enrollment(db, v1_seed, h_id).valid_to = DAY_BEFORE_EXAM
        for t_id in (v1_seed.jia_t_id, v1_seed.yi_t_id):
            _member(db, v1_seed, t_id).valid_to = DAY_BEFORE_EXAM
        db.commit()

        rows = _homeroom_scores(client, v1_seed)
        assert all(r.get("source_domain") != "teaching" for r in rows)
        assert all(r.get("shared_conflict") is None for r in rows)
        assert {r["person_id"] for r in rows} == {v1_seed.bing_h_id}

        students = _homeroom_students(client, v1_seed)
        assert {s["person_id"] for s in students} == {v1_seed.bing_h_id}

        from app.db import workspace_models as wm

        assert (
            db.query(wm.LinkedStudent).filter_by(link_id=v1_seed.link_id).count() == 2
        )
    finally:
        for h_id in (v1_seed.jia_h_id, v1_seed.yi_h_id):
            _enrollment(db, v1_seed, h_id).valid_to = None
        for t_id in (v1_seed.jia_t_id, v1_seed.yi_t_id):
            _member(db, v1_seed, t_id).valid_to = None
        db.commit()
        db.close()
