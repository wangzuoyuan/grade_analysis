"""R10：作业批次契约的关键数据库约束（模型 + 连接层外键）。

契约 §2.1（docs/contracts/p1-api.md）冻结的最小作业契约逐项验证：
batch_token 幂等唯一、同人同批次唯一、外键真实拒绝孤儿行、
状态白名单 CHECK、合法全字段路径可写可读。
完整录入/预警逻辑属 P5，不在此文件测试。
"""

import json
from datetime import date, datetime

import pytest

# 学年名需模块内互不相同（AcademicYear.name 全局唯一），
# 以标签区分各用例自建的父行。
_YEAR_LABELS = iter(range(1, 99))


def _mk_year(db):
    """建一个用例专属学年，返回对象（已 flush 出 id）。"""
    from app.db import workspace_models as wm

    ay = wm.AcademicYear(
        name=f"2025-2026-r10-{next(_YEAR_LABELS)}",
        start_date=date(2025, 9, 1),
        end_date=date(2026, 6, 30),
    )
    db.add(ay)
    db.flush()
    return ay


def _mk_identity(db, display_name):
    from app.db import workspace_models as wm

    ident = wm.WsStudentIdentity(data_domain="homeroom", display_name=display_name)
    db.add(ident)
    db.flush()
    return ident


def _mk_assignment(db, ay_id, token, expected="[]", domain="homeroom"):
    """class_ref_id 为多态引用（无数据库外键，同 ScoreFact），
    用裸整数即可构造；非法域走 domain 参数。"""
    from app.db import workspace_models as wm

    a = wm.HomeworkAssignment(
        data_domain=domain,
        class_ref_id=101,
        academic_year_id=ay_id,
        subject="物理",
        homework_type="练习册",
        assigned_date=date(2025, 9, 10),
        due_date=date(2025, 9, 11),
        batch_token=token,
        expected_members_json=expected,
    )
    db.add(a)
    return a


def test_duplicate_batch_token_rejected(db_session):
    """batch_token UNIQUE：重试同 token 不得新增批次。"""
    from sqlalchemy.exc import IntegrityError

    db = db_session
    ay = _mk_year(db)
    _mk_assignment(db, ay.id, "r10-token-dup")
    db.commit()

    _mk_assignment(db, ay.id, "r10-token-dup", expected='[7]')
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_duplicate_submission_pair_rejected(db_session):
    """UNIQUE (assignment_id, person_id)：同人同批次只有一条逐人状态。"""
    from sqlalchemy.exc import IntegrityError

    from app.db import workspace_models as wm

    db = db_session
    ay = _mk_year(db)
    ident = _mk_identity(db, "R10 同批次学生")
    a = _mk_assignment(db, ay.id, "r10-token-pair", expected=json.dumps([ident.id]))
    db.flush()
    db.add(
        wm.HomeworkSubmission(
            assignment_id=a.id, person_id=ident.id, submission_status="submitted"
        )
    )
    db.commit()

    db.add(
        wm.HomeworkSubmission(
            assignment_id=a.id, person_id=ident.id, submission_status="missing"
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_orphan_submission_fk_rejected(db_session):
    """外键真实开启：person_id / assignment_id 指向不存在行必须被拒。"""
    from sqlalchemy.exc import IntegrityError

    from app.db import workspace_models as wm

    db = db_session
    ay = _mk_year(db)
    ident = _mk_identity(db, "R10 外键学生")
    a = _mk_assignment(db, ay.id, "r10-token-fk", expected=json.dumps([ident.id]))
    db.flush()

    # person_id 指向不存在的身份
    db.add(
        wm.HomeworkSubmission(
            assignment_id=a.id, person_id=999999, submission_status="submitted"
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    # assignment_id 指向不存在的批次
    db.add(
        wm.HomeworkSubmission(
            assignment_id=999999, person_id=ident.id, submission_status="submitted"
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_invalid_status_values_rejected(db_session):
    """CHECK 白名单：submission_status 与 data_domain 的非法值落库即拒。"""
    from sqlalchemy.exc import IntegrityError

    from app.db import workspace_models as wm

    db = db_session
    ay = _mk_year(db)
    ident = _mk_identity(db, "R10 状态学生")
    a = _mk_assignment(db, ay.id, "r10-token-status", expected=json.dumps([ident.id]))
    db.flush()

    # submission_status='done' 不在契约四值白名单内
    db.add(
        wm.HomeworkSubmission(
            assignment_id=a.id, person_id=ident.id, submission_status="done"
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    # data_domain='other' 不是合法分区
    _mk_assignment(db, ay.id, "r10-token-bad-domain", domain="other")
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_valid_full_roundtrip(db_session):
    """合法路径：学年+身份+assignment（成员快照）+submission 全字段
    写入成功并读回（含默认值 revision=1 / status='active'）。"""
    from app.db import workspace_models as wm

    db = db_session
    ay = _mk_year(db)
    ident = _mk_identity(db, "R10 合法学生")
    expected_json = json.dumps([ident.id])
    a = _mk_assignment(db, ay.id, "r10-token-ok", expected=expected_json)
    db.flush()
    submitted_at = datetime(2025, 9, 10, 21, 30, 0)
    db.add(
        wm.HomeworkSubmission(
            assignment_id=a.id,
            person_id=ident.id,
            submission_status="submitted",
            evaluation="完成质量良好",
            submitted_at=submitted_at,
        )
    )
    db.commit()

    db.expire_all()
    got_a = db.query(wm.HomeworkAssignment).filter_by(batch_token="r10-token-ok").one()
    assert got_a.data_domain == "homeroom"
    assert got_a.class_ref_id == 101
    assert got_a.academic_year_id == ay.id
    assert got_a.subject == "物理"
    assert got_a.homework_type == "练习册"
    assert got_a.assigned_date == date(2025, 9, 10)
    assert got_a.due_date == date(2025, 9, 11)
    assert got_a.expected_members_json == expected_json
    assert got_a.revision == 1
    assert got_a.status == "active"
    assert got_a.created_at is not None

    got_s = (
        db.query(wm.HomeworkSubmission)
        .filter_by(assignment_id=got_a.id, person_id=ident.id)
        .one()
    )
    assert got_s.submission_status == "submitted"
    assert got_s.evaluation == "完成质量良好"
    assert got_s.submitted_at == submitted_at
    assert got_s.revision == 1
    assert got_s.created_at is not None
