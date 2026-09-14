"""跨届撞号防护测试：启动迁移（改写 + 按姓名建链）、成绩上传防呆、
花名册录学号防呆。

场景：高一 9900629=林一舟（历史成绩），高二分班重新编号后 9900629=周子墨。
花名册行是学号当前属主（一人一行），成绩中与花名册姓名不符的历史行属于
别人，需改写为 g<年级>- 前缀并按姓名链到本名下的花名册行。
"""

import tempfile
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db.models as models
from app.analysis.identity import person_ids
from app.db.migrate_student_ids import (
    migrate_colliding_student_ids,
    strip_id_namespace,
)


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="collision_test_")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def seed(db):
    db.add_all([
        models.Exam(id=1, name="高一期末", grade=1, semester="下", exam_date="2025-06-20", exam_type="期末"),
        models.Exam(id=2, name="高二月考", grade=2, semester="上", exam_date="2026-09-01", exam_type="月考"),
        models.SubjectScore(exam_id=1, student_id="9900629", subject="物理", name="林一舟", raw_score=80),
        models.SubjectScore(exam_id=1, student_id="9900629", subject="化学", name="林一舟", raw_score=75),
        models.SubjectScore(exam_id=2, student_id="9900629", subject="物理", name="周子墨", raw_score=90),
        models.TotalScore(exam_id=1, student_id="9900629", total_type="主三门", total_score=240),
        # 花名册：9900629 当前属主周子墨（高二）；林一舟高二在新号/TMP 行上
        models.ClassRoster(student_id="9900629", name="周子墨", grade=2, class_num=6, excluded=0),
        models.ClassRoster(student_id="TMP-2-6-林一舟", name="林一舟", grade=2, class_num=6, excluded=0),
    ])
    db.commit()


def score_rows(db, sid):
    return sorted(
        (r.subject, r.name) for r in db.query(models.SubjectScore).filter(
            models.SubjectScore.student_id == sid
        ).all()
    )


def test_rekey_and_auto_link(db):
    seed(db)
    stats = migrate_colliding_student_ids(db)
    assert stats["collisions"] == 1
    # 林一舟历史行改写为命名空间学号，周子墨行保留原号
    assert score_rows(db, "g1-9900629") == [("化学", "林一舟"), ("物理", "林一舟")]
    assert score_rows(db, "9900629") == [("物理", "周子墨")]
    # 旧总分行同批考试一并改指
    assert db.query(models.TotalScore).filter(
        models.TotalScore.student_id == "g1-9900629"
    ).count() == 1
    # 按姓名自动建链：g1-9900629 ↔ TMP-2-6-林一舟（花名册同名占位行）
    ids = person_ids(db, "TMP-2-6-林一舟")
    assert ids == {"TMP-2-6-林一舟", "g1-9900629"}
    assert stats["linked"] == 1


def test_idempotent(db):
    seed(db)
    migrate_colliding_student_ids(db)
    again = migrate_colliding_student_ids(db)
    assert again["collisions"] == 0
    assert again["rekeyed_rows"] == 0
    assert again["linked"] == 0


def test_member_mismatch_rekeys_all_rows(db):
    """高二成绩未导入时序：成绩里 9900629 只有林一舟，花名册属主周子墨。
    全部历史行改写，学号名下清零，画像不再串用他人成绩。"""
    seed(db)
    db.query(models.SubjectScore).filter(models.SubjectScore.exam_id == 2).delete()
    db.commit()
    stats = migrate_colliding_student_ids(db)
    assert stats["collisions"] == 1
    assert score_rows(db, "g1-9900629") == [("化学", "林一舟"), ("物理", "林一舟")]
    assert db.query(models.SubjectScore).filter(
        models.SubjectScore.student_id == "9900629"
    ).count() == 0
    assert person_ids(db, "TMP-2-6-林一舟") == {"TMP-2-6-林一舟", "g1-9900629"}


def test_matching_names_untouched(db):
    """成绩姓名与花名册属主一致 → 完全不动。"""
    seed(db)
    db.query(models.SubjectScore).filter(models.SubjectScore.exam_id == 2).delete()
    db.query(models.ClassRoster).filter(
        models.ClassRoster.student_id == "9900629"
    ).update({"name": "林一舟"}, synchronize_session=False)
    db.commit()
    stats = migrate_colliding_student_ids(db)
    assert stats["collisions"] == 0
    assert score_rows(db, "9900629") == [("化学", "林一舟"), ("物理", "林一舟")]


def test_orphan_history_untouched(db):
    """学号不在花名册（纯历史数据）→ 不动。"""
    seed(db)
    db.query(models.ClassRoster).delete()
    db.commit()
    stats = migrate_colliding_student_ids(db)
    assert stats["collisions"] == 0
    assert len(score_rows(db, "9900629")) == 3


def test_ambiguous_and_missing_candidates_go_pending(db):
    seed(db)
    # 高二无同名行 → no_candidate
    db.query(models.ClassRoster).filter(
        models.ClassRoster.name == "林一舟"
    ).delete()
    db.commit()
    stats = migrate_colliding_student_ids(db)
    assert stats["linked"] == 0
    assert stats["pending"][0]["reason"] == "no_candidate"
    # 改写本身仍完成
    assert score_rows(db, "g1-9900629") == [("化学", "林一舟"), ("物理", "林一舟")]


def test_strip_namespace():
    assert strip_id_namespace("g1-9900629") == "9900629"
    assert strip_id_namespace("9900629") == "9900629"
    assert strip_id_namespace("TMP-2-6-张三") == "TMP-2-6-张三"
    assert strip_id_namespace("") == ""


# ─────────────────────── 上传 / 花名册录学号防呆 ───────────────────────


def test_detect_sid_name_conflicts():
    from app.ingest.router import _detect_sid_name_conflicts

    class _Q:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *a, **k):
            return self

        def distinct(self):
            return self

        def all(self):
            return self._rows

    class _DB:
        def __init__(self, rows):
            self._q = _Q(rows)

        def query(self, *a, **k):
            return self._q

    # 本文件内同学号多姓名
    msg = _detect_sid_name_conflicts(
        _DB([]),
        [
            {"student_id": "9900629", "name": "林一舟"},
            {"student_id": "9900629", "name": "周子墨"},
        ],
    )
    assert msg and "文件内同一学号" in msg

    # 与库内冲突（跨届撞号形态）
    msg = _detect_sid_name_conflicts(
        _DB([("9900629", "林一舟")]),
        [{"student_id": "9900629", "name": "周子墨"}],
    )
    assert msg and "历史数据姓名不一致" in msg and "林一舟" in msg and "周子墨" in msg

    # 同名重传 / 无冲突 → 放行
    assert _detect_sid_name_conflicts(
        _DB([("9900629", "林一舟")]),
        [{"student_id": "9900629", "name": "林一舟"}],
    ) is None
    assert _detect_sid_name_conflicts(_DB([]), []) is None
