"""P8-EXEC：班主任历史学年上下文回退（换届后回看低年级班）。

真实迁移数据形态：active_grade=2，2025-2026 的 6班是 grade1 实例、
2026-2027 的是 grade2 实例。修复前按 active_grade 精确匹配会让历史学年
409（高一全年成绩对班主任工作台不可见）。回退仅限教师绑定班号在该学年
的唯一实例，成员范围绝不放宽；歧义（同班号多实例）维持 409。
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

API = "/api/v1"
Y1 = "P8EXEC-HIST-2025-2026"
Y2 = "P8EXEC-HIST-2026-2027"


@pytest.fixture()
def hist_base():
    from app.db import workspace_models as wm
    from app.db.models import HomeworkSetting, SessionLocal, Teacher

    db = SessionLocal()
    db.merge(Teacher(id=1, name="测试班主任", target_class_high1=6, target_class_high2=6))
    db.merge(HomeworkSetting(key="active_grade", value="2"))

    def year(name, start, end):
        row = db.query(wm.AcademicYear).filter_by(name=name).first()
        if row is None:
            row = wm.AcademicYear(name=name, start_date=start, end_date=end)
            db.add(row)
            db.flush()
        return row

    y1 = year(Y1, datetime(2025, 9, 1).date(), datetime(2026, 7, 15).date())
    y2 = year(Y2, datetime(2026, 9, 1).date(), datetime(2027, 7, 15).date())

    def aclass(ay, grade, num):
        row = (
            db.query(wm.AdministrativeClass)
            .filter_by(academic_year_id=ay.id, grade=grade, class_num=num)
            .first()
        )
        if row is None:
            row = wm.AdministrativeClass(academic_year_id=ay.id, grade=grade, class_num=num)
            db.add(row)
            db.flush()
        return row

    c1 = aclass(y1, 1, 6)
    c2 = aclass(y2, 2, 6)

    ident = (
        db.query(wm.WsStudentIdentity)
        .filter_by(data_domain="homeroom", display_name="历史回退学生")
        .first()
    )
    if ident is None:
        ident = wm.WsStudentIdentity(data_domain="homeroom", display_name="历史回退学生")
        db.add(ident)
        db.flush()
    if (
        db.query(wm.Enrollment)
        .filter_by(admin_class_id=c1.id, identity_id=ident.id)
        .first()
        is None
    ):
        db.add(
            wm.Enrollment(
                admin_class_id=c1.id,
                identity_id=ident.id,
                seat_no=1,
                status="active",
                valid_from=y1.start_date,
            )
        )
    db.commit()
    try:
        yield SimpleNamespace(db=db, y1=y1, y2=y2, c1=c1, c2=c2, ident=ident)
    finally:
        db.close()


def test_homeroom_context_historical_year_falls_back_to_years_class(client, hist_base):
    from app.core.context import resolve_workspace_context

    ctx = resolve_workspace_context(
        hist_base.db, 1, "homeroom", {"academic_year_id": hist_base.y1.id}
    )
    assert ctx.class_ids == (hist_base.c1.id,)
    assert ctx.grade == 1
    assert ctx.member_person_ids == (hist_base.ident.id,)


def test_homeroom_context_current_year_exact_match_unchanged(client, hist_base):
    from app.core.context import resolve_workspace_context

    ctx = resolve_workspace_context(
        hist_base.db, 1, "homeroom", {"academic_year_id": hist_base.y2.id}
    )
    assert ctx.class_ids == (hist_base.c2.id,)
    assert ctx.grade == 2
    assert ctx.member_person_ids == ()


def test_shared_exams_historical_year_200(client, hist_base):
    r = client.get(
        f"{API}/shared/exams",
        params={"mode": "homeroom", "academic_year_id": hist_base.y1.id},
    )
    assert r.status_code == 200
    assert r.json()["exams"] == []


def test_homeroom_context_ambiguous_history_still_409(client, hist_base):
    from app.core.context import resolve_workspace_context
    from app.core.errors import WorkspaceNotConfigured
    from app.db import workspace_models as wm

    db = hist_base.db
    # y1 已有 (grade1,6)；再放一个 (grade3,6) → 精确 (2,6) 未命中且绑定班号
    # 出现两个实例 → 歧义，必须维持 409。
    extra = wm.AdministrativeClass(
        academic_year_id=hist_base.y1.id, grade=3, class_num=6
    )
    db.add(extra)
    db.commit()
    try:
        with pytest.raises(WorkspaceNotConfigured):
            resolve_workspace_context(db, 1, "homeroom", {"academic_year_id": hist_base.y1.id})
    finally:
        db.delete(extra)
        db.commit()
