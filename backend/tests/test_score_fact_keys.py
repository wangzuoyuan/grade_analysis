"""ScoreFact subject_key/total_key 自动同步与自然唯一键（返工补充）。

契约：业务代码只写 subject/total_type；key 列由 ORM 同步
（@validates 设值即同步，flush 时以 subject/total_type 为准强制覆写）。
同一学生同场考试的多科行 + 总分行不得互相顶掉；真正重复行必须被
唯一键 (data_domain, academic_year_id, exam_name, identity_id,
subject_key, total_key) 拒绝。
"""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

import app.db.workspace_models as wm  # noqa: F401  注册新表
from app.db.models import SessionLocal


@pytest.fixture()
def seed(db_session):
    # 模块内多测试共享库：学年/行政班按唯一键复用，避免重复创建冲突
    ay = (
        db_session.query(wm.AcademicYear)
        .filter(wm.AcademicYear.name == "2025-2026")
        .one_or_none()
    )
    if ay is None:
        ay = wm.AcademicYear(
            name="2025-2026", start_date=date(2025, 9, 1), end_date=date(2026, 6, 30)
        )
        db_session.add(ay)
        db_session.flush()
    admin = (
        db_session.query(wm.AdministrativeClass)
        .filter(
            wm.AdministrativeClass.academic_year_id == ay.id,
            wm.AdministrativeClass.grade == 2,
            wm.AdministrativeClass.class_num == 3,
        )
        .one_or_none()
    )
    if admin is None:
        admin = wm.AdministrativeClass(
            academic_year_id=ay.id, grade=2, class_num=3, label="高二3班"
        )
        db_session.add(admin)
    ident = wm.WsStudentIdentity(data_domain="homeroom", display_name="甲")
    db_session.add(ident)
    db_session.flush()
    db_session.commit()
    db_session.expire_all()
    return {"ay": ay, "admin": admin, "ident": ident}


def _fact(seed, subject, total_type=None, score=None, exam="期中考试", **overrides):
    f = wm.ScoreFact(
        data_domain="homeroom",
        academic_year_id=seed["ay"].id,
        exam_name=exam,
        exam_date=date(2025, 11, 6),
        class_ref_id=seed["admin"].id,
        identity_id=seed["ident"].id,
        subject=subject,
        total_type=total_type,
        score=score,
    )
    for key, value in overrides.items():
        setattr(f, key, value)
    return f


def test_multi_subject_and_total_rows_auto_sync_keys(db_session, seed):
    """只写 subject/total_type：多科行 + 总分行全部插入成功，key 列同步。"""
    db_session.add_all(
        [
            _fact(seed, "语文", score=110.0),
            _fact(seed, "数学", score=135.0),
            _fact(seed, "物理", score=88.0),
            _fact(seed, None, total_type="主三门", score=333.0),  # 总分行
        ]
    )
    # 显式写错的 key 也会被同步覆写（以 subject/total_type 为准）
    db_session.add(_fact(seed, "化学", score=90.0, subject_key="WRONG"))
    db_session.commit()

    rows = {r.subject: r for r in db_session.query(wm.ScoreFact).all()}
    assert set(rows) == {"语文", "数学", "物理", "化学", None}
    assert rows["语文"].subject_key == "语文" and rows["语文"].total_key == ""
    assert rows["数学"].subject_key == "数学"
    assert rows["物理"].subject_key == "物理"
    assert rows["化学"].subject_key == "化学"  # 覆写 WRONG
    total_row = rows[None]
    assert total_row.subject_key == "" and total_row.total_key == "主三门"


def test_update_resyncs_keys(db_session, seed):
    db_session.add(_fact(seed, "物理", score=80.0))
    db_session.commit()
    row = (
        db_session.query(wm.ScoreFact)
        .filter(wm.ScoreFact.identity_id == seed["ident"].id)
        .one()
    )
    row.subject = "化学"
    db_session.commit()
    db_session.expire_all()
    row2 = (
        db_session.query(wm.ScoreFact)
        .filter(wm.ScoreFact.identity_id == seed["ident"].id)
        .one()
    )
    assert (row2.subject, row2.subject_key) == ("化学", "化学")


def test_duplicate_row_rejected_by_unique_key(db_session, seed):
    db_session.add(_fact(seed, "物理", score=80.0))
    db_session.commit()
    db_session.add(_fact(seed, "物理", score=95.0))  # 真正重复
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    mine = db_session.query(wm.ScoreFact).filter(
        wm.ScoreFact.identity_id == seed["ident"].id
    )
    assert mine.count() == 1


def test_same_exam_other_domain_or_exam_name_coexist(db_session, seed):
    """teaching 域同科同考试、或不同考试名：不构成重复，均应可插入。"""
    t_ident = wm.WsStudentIdentity(data_domain="teaching", display_name="甲T")
    db_session.add(t_ident)
    db_session.flush()
    db_session.add_all(
        [
            _fact(seed, "物理", score=80.0),
            _fact(seed, "物理", score=80.0, exam="期末考试"),
            wm.ScoreFact(
                data_domain="teaching",
                academic_year_id=seed["ay"].id,
                exam_name="期中考试",
                identity_id=t_ident.id,
                subject="物理",
            ),
        ]
    )
    db_session.commit()
    mine = db_session.query(wm.ScoreFact).filter(
        wm.ScoreFact.identity_id.in_([seed["ident"].id, t_ident.id])
    )
    assert mine.count() == 3


def test_sessionlocal_factory_still_works():
    """共享 SessionLocal 体系未被破坏（规格要求复用既有体系）。"""
    db = SessionLocal()
    try:
        assert db.query(wm.AcademicYear).count() >= 0
    finally:
        db.close()
