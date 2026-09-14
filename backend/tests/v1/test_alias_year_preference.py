"""名册学年别名回归（v2.1/名册学年别名）。

WsStudentAlias 按 (identity, alias, domain, 学年) 登记。名册取别名时
academic_year_id 缺省保持旧行为（最早一条回退）；显式指定学年时优先
该学年登记，无登记再回退最早。两学年并存时同一个人在不同学年可以
有不同的学号展示。

注意：v1_seed 的别名已规范标注本学年（v2.2/G06 起种子不再用 NULL 学年，
alias_scope=str(ay.id)），第二学年的新登记同样同步 alias_scope=str(ay.id)
（非空冗余列参与唯一约束，见模型注释）。本模块会新建学年，因此所有
API 调用均显式传 academic_year_id，不依赖"缺省取最新学年"的解析。
"""

from datetime import date
from types import SimpleNamespace

import pytest

API = "/api/v1"
AS_OF = date(2026, 9, 11)


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


@pytest.fixture(scope="module")
def alias_seed(v1_seed):
    """补种第二学年 + 甲（H/T 双域）在该学年的学号登记。"""
    from app.db import workspace_models as wm

    s = v1_seed
    db = _db()
    ay2 = wm.AcademicYear(
        name="2026-2027", start_date=date(2026, 9, 1), end_date=date(2027, 7, 15)
    )
    db.add(ay2)
    db.flush()
    for ident_id, value, domain in (
        (s.jia_h_id, "2026H6-01", "homeroom"),
        (s.jia_t_id, "2026T6-01", "teaching"),
    ):
        db.add(
            wm.WsStudentAlias(
                identity_id=ident_id,
                alias_value=value,
                data_domain=domain,
                source="alias-year-test",
                academic_year_id=ay2.id,
                alias_scope=str(ay2.id),  # 冗余列与学年同步（唯一约束）
            )
        )
    db.commit()
    yield SimpleNamespace(ay2_id=ay2.id)
    db.close()


def _roster_alias(roster, person_id):
    return next(item["alias"] for item in roster if item["person_id"] == person_id)


def test_homeroom_roster_prefers_given_year_alias(v1_seed, alias_seed):
    """指定学年 → 显示该学年学号；该学年无登记或缺省 → 回退最早一条。"""
    from app.api import _queries as q

    s = v1_seed
    db = _db()
    try:
        roster_ay2 = q.homeroom_roster(db, s.h6_id, AS_OF, alias_seed.ay2_id)
        assert _roster_alias(roster_ay2, s.jia_h_id) == "2026H6-01"

        # 甲在旧学年无登记 → 回退最早（v1_seed 的 None-学年别名）
        roster_ay1 = q.homeroom_roster(db, s.h6_id, AS_OF, s.ay_id)
        assert _roster_alias(roster_ay1, s.jia_h_id) == "2025H6-01"

        # 缺省学年保持旧行为：最早一条
        roster_default = q.homeroom_roster(db, s.h6_id, AS_OF)
        assert _roster_alias(roster_default, s.jia_h_id) == "2025H6-01"
    finally:
        db.close()


def test_teaching_roster_prefers_given_year_alias(v1_seed, alias_seed):
    """teaching_roster 同口径：指定学年优先该学年登记。"""
    from app.api import _queries as q

    s = v1_seed
    db = _db()
    try:
        roster_ay2 = q.teaching_roster(db, [s.t6_id], AS_OF, alias_seed.ay2_id)
        assert _roster_alias(roster_ay2, s.jia_t_id) == "2026T6-01"

        roster_default = q.teaching_roster(db, [s.t6_id], AS_OF)
        assert _roster_alias(roster_default, s.jia_t_id) == "2025T6-01"
    finally:
        db.close()


def test_students_api_alias_unchanged_for_current_year(client, v1_seed, alias_seed):
    """API 级（students.py 传 ctx.academic_year_id）：单学年数据行为
    不变——当前学年查询仍显示历史登记别名。"""
    s = v1_seed
    r = client.get(
        f"{API}/homeroom/students",
        params={"academic_year_id": s.ay_id, "class_id": s.h6_id},
    )
    assert r.status_code == 200
    by_pid = {item["person_id"]: item for item in r.json()["students"]}
    assert by_pid[s.jia_h_id]["alias"] == "2025H6-01"
    assert by_pid[s.yi_h_id]["alias"] == "2025H6-02"

    r2 = client.get(
        f"{API}/teaching/students",
        params={"academic_year_id": s.ay_id, "teaching_class_id": s.t6_id},
    )
    assert r2.status_code == 200
    t_by_pid = {item["person_id"]: item for item in r2.json()["students"]}
    assert t_by_pid[s.jia_t_id]["alias"] == "2025T6-01"
