"""跨学年身份解析（app.analysis.identity）单元测试。

完全隔离：每个用例建自己的临时 SQLite 文件引擎（Base.metadata.create_all +
sessionmaker），绝不依赖 EXAM_TRACKER_DIR 或 ~/.exam-tracker。
"""

import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db.models as models
from app.analysis import identity


@pytest.fixture
def db():
    """全新临时文件引擎 + 建表 + session，用完即弃。"""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="identity_test_")
    import os

    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ─────────────────────────── person_ids / identity_of ───────────────────────────


def test_person_ids_degrades_to_singleton_when_no_alias(db):
    """无 alias 时 person_ids 退化为 {student_id}（零配置、零回归）。"""
    sid = "9900601"
    assert identity.person_ids(db, sid) == {sid}


def test_identity_of_none_when_unlinked(db):
    assert identity.identity_of(db, "9900601") is None


def test_identity_of_none_returns_none_for_none(db):
    assert identity.identity_of(db, None) is None


def test_person_ids_none_returns_empty(db):
    assert identity.person_ids(db, None) == set()


# ─────────────────────────── ensure_identity + link_aliases ───────────────────────────


def test_ensure_identity_and_link_two_sids_to_one_identity(db):
    iid = identity.ensure_identity(db, display_name="张三", gender="男")
    assert isinstance(iid, int) and iid > 0

    res = identity.link_aliases(
        db, iid, [("9900601", 1), ("7251601", 2)], "name_confirmed"
    )
    assert res["linked"] == ["9900601", "7251601"]
    assert res["conflicts"] == []
    assert res["skipped"] == []

    # identity_of 命中
    assert identity.identity_of(db, "9900601") == iid
    assert identity.identity_of(db, "7251601") == iid

    # person_ids 返回两个学号
    assert identity.person_ids(db, "9900601") == {"9900601", "7251601"}
    assert identity.person_ids(db, "7251601") == {"9900601", "7251601"}


def test_link_aliases_skip_when_already_same_identity(db):
    iid = identity.ensure_identity(db, display_name="张三")
    identity.link_aliases(db, iid, [("9900601", 1)], "name_confirmed")
    res = identity.link_aliases(db, iid, [("9900601", 1)], "name_confirmed")
    assert res["linked"] == []
    assert res["skipped"] == ["9900601"]
    assert res["conflicts"] == []


def test_link_aliases_conflict_when_sid_on_other_identity(db):
    """某学号已挂在另一个 identity -> 报 conflict 且不覆盖原链接。"""
    iid_a = identity.ensure_identity(db, display_name="张三")
    identity.link_aliases(db, iid_a, [("9900601", 1)], "name_confirmed")

    iid_b = identity.ensure_identity(db, display_name="李四")
    res = identity.link_aliases(db, iid_b, [("9900601", 1)], "manual")
    assert res["linked"] == []
    assert res["skipped"] == []
    assert len(res["conflicts"]) == 1
    conflict = res["conflicts"][0]
    assert conflict["student_id"] == "9900601"
    assert conflict["conflict_identity_id"] == iid_a

    # 原链接保持不变（未覆盖）
    assert identity.identity_of(db, "9900601") == iid_a


# ─────────────────────────── unlink_alias ───────────────────────────


def test_unlink_alias_removes_link_and_degenerates_to_singleton(db):
    iid = identity.ensure_identity(db, display_name="张三")
    identity.link_aliases(db, iid, [("9900601", 1), ("7251601", 2)], "name_confirmed")
    assert identity.identity_of(db, "9900601") == iid

    res = identity.unlink_alias(db, "9900601")
    assert res == {"unlinked": "9900601"}

    # 解链后该学号回到独立人
    assert identity.identity_of(db, "9900601") is None
    assert identity.person_ids(db, "9900601") == {"9900601"}
    # 另一个学号仍挂在 identity 上
    assert identity.identity_of(db, "7251601") == iid


def test_unlink_alias_missing_returns_error(db):
    res = identity.unlink_alias(db, "9999999")
    assert "error" in res


def test_unlink_alias_none_returns_error(db):
    assert "error" in identity.unlink_alias(db, None)


# ─────────────────────────── name_candidates ───────────────────────────


def _seed_exam(db, *, eid, name, grade, exam_date="2024-01"):
    db.add(
        models.Exam(
            id=eid,
            name=name,
            grade=grade,
            semester="上",
            exam_date=exam_date,
            exam_type="月考",
        )
    )


def _seed_subject(db, *, exam_id, student_id, name, class_num, subject="语文"):
    db.add(
        models.SubjectScore(
            exam_id=exam_id,
            student_id=student_id,
            name=name,
            class_num=class_num,
            subject=subject,
            raw_score=80.0,
        )
    )


def test_name_candidates_finds_same_name_in_different_classes(db):
    """grade=1 下两个不同班的同名学生都应被返回，附 class_num /
    latest_exam_name / already_linked 标记。"""
    _seed_exam(db, eid=10, name="高一月考", grade=1, exam_date="2024-09")
    # 两个同名（不同班、不同学号）
    _seed_subject(db, exam_id=10, student_id="9900601", name="王五", class_num=6)
    _seed_subject(db, exam_id=10, student_id="9900602", name="王五", class_num=7)
    # 一个不同名（不应出现）
    _seed_subject(db, exam_id=10, student_id="9900603", name="赵六", class_num=6)
    db.commit()

    cands = identity.name_candidates(db, "王五", target_grade=1)
    assert len(cands) == 2
    sids = {c["student_id"] for c in cands}
    assert sids == {"9900601", "9900602"}
    for c in cands:
        assert c["name"] == "王五"
        assert c["class_num"] in {6, 7}
        assert c["latest_exam_name"] == "高一月考"
        assert c["already_linked"] is False


def test_name_candidates_marks_already_linked(db):
    _seed_exam(db, eid=11, name="高一期中", grade=1, exam_date="2024-10")
    _seed_subject(db, exam_id=11, student_id="7250701", name="孙七", class_num=7)
    db.commit()

    iid = identity.ensure_identity(db, display_name="孙七")
    identity.link_aliases(db, iid, [("7250701", 1)], "name_confirmed")

    cands = identity.name_candidates(db, "孙七", target_grade=1)
    assert len(cands) == 1
    assert cands[0]["already_linked"] is True


def test_name_candidates_zero_hit_returns_empty(db):
    _seed_exam(db, eid=12, name="高一月考", grade=1)
    _seed_subject(db, exam_id=12, student_id="9900601", name="王五", class_num=6)
    db.commit()
    assert identity.name_candidates(db, "查无此名", target_grade=1) == []


def test_name_candidates_scoped_to_target_grade(db):
    """target_grade=1 时不应命中 grade=2 的同名记录。"""
    _seed_exam(db, eid=20, name="高二月考", grade=2, exam_date="2024-09")
    _seed_subject(db, exam_id=20, student_id="7251601", name="周八", class_num=3)
    db.commit()
    assert identity.name_candidates(db, "周八", target_grade=1) == []
    assert len(identity.name_candidates(db, "周八", target_grade=2)) == 1


def test_name_candidates_empty_name_returns_empty(db):
    assert identity.name_candidates(db, "", target_grade=1) == []


# ─────────────────────────── import_crosswalk ───────────────────────────


def test_import_crosswalk_both_new_creates_one_identity_two_aliases(db):
    res = identity.import_crosswalk(
        db,
        [{"g1_sid": "9900601", "g2_sid": "7251601", "name": "吴九"}],
    )
    assert res == {"linked": 1, "conflict": 0, "skipped": 0}
    iid = identity.identity_of(db, "9900601")
    assert iid is not None
    assert identity.identity_of(db, "7251601") == iid
    assert identity.person_ids(db, "9900601") == {"9900601", "7251601"}


def test_import_crosswalk_one_existing_joins(db):
    # 预先给 g1 一个 identity
    iid = identity.ensure_identity(db, display_name="吴九")
    identity.link_aliases(db, iid, [("9900601", 1)], "name_confirmed")

    res = identity.import_crosswalk(
        db,
        [{"g1_sid": "9900601", "g2_sid": "7251601", "name": "吴九"}],
    )
    assert res == {"linked": 1, "conflict": 0, "skipped": 0}
    assert identity.identity_of(db, "7251601") == iid
    assert identity.identity_of(db, "9900601") == iid


def test_import_crosswalk_same_identity_skipped(db):
    iid = identity.ensure_identity(db, display_name="吴九")
    identity.link_aliases(db, iid, [("9900601", 1), ("7251601", 2)], "crosswalk")

    res = identity.import_crosswalk(
        db,
        [{"g1_sid": "9900601", "g2_sid": "7251601", "name": "吴九"}],
    )
    assert res == {"linked": 0, "conflict": 0, "skipped": 1}


def test_import_crosswalk_two_different_identities_conflict(db):
    iid_a = identity.ensure_identity(db, display_name="吴九")
    identity.link_aliases(db, iid_a, [("9900601", 1)], "name_confirmed")
    iid_b = identity.ensure_identity(db, display_name="吴九B")
    identity.link_aliases(db, iid_b, [("7251601", 2)], "name_confirmed")

    res = identity.import_crosswalk(
        db,
        [{"g1_sid": "9900601", "g2_sid": "7251601", "name": "吴九"}],
    )
    assert res == {"linked": 0, "conflict": 1, "skipped": 0}
    # 两个 identity 都未被合并/覆盖
    assert identity.identity_of(db, "9900601") == iid_a
    assert identity.identity_of(db, "7251601") == iid_b


def test_import_crosswalk_grade3_uses_previous_and_target_grades(db):
    result = identity.import_crosswalk(
        db,
        [{"g1_sid": "g2-old", "g2_sid": "g3-new", "name": "高三生"}],
        target_grade=3,
    )
    assert result["linked"] == 1
    iid = identity.identity_of(db, "g3-new")
    grades = {alias.student_id: alias.grade for alias in identity.aliases_of(db, iid)}
    assert grades == {"g2-old": 2, "g3-new": 3}
