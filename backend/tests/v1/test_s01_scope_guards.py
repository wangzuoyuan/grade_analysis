"""S01 作用域守卫：未配置 409 / 非法参数 422 / 越界 404 / 空成员空态 200。

契约先行：app 导入全部延迟到用例内；本文件不依赖 v1_seed，自造最小数据
（教师绑定 + 学年 + 按需教学班），保证每个用例独立可运行。
"""

from types import SimpleNamespace

import pytest

API = "/api/v1"
AY_NAME = "2025-2026"  # 与 tests/v1/conftest.py 合成样本一致


def _set_first(obj, candidates, value):
    """v1/conftest.set_first_column 的本地副本（conftest 不做模块导入）。"""
    for name in candidates:
        if name in obj.__table__.columns:
            setattr(obj, name, value)
            return


def test_contract_import_surface():
    """契约导入面：模块路径与表类名必须与 P1 契约完全一致。"""
    import inspect

    from app.core.context import WorkspaceContext, resolve_workspace_context
    from app.core.errors import (
        InvalidScopeParam,
        LinkVersionConflict,
        ResourceOutOfScope,
        WorkspaceNotConfigured,
    )
    from app.db import workspace_models as wm

    for symbol in (
        WorkspaceContext,
        WorkspaceNotConfigured,
        InvalidScopeParam,
        ResourceOutOfScope,
        LinkVersionConflict,
    ):
        assert inspect.isclass(symbol), symbol
    assert callable(resolve_workspace_context)

    for table in (
        "AcademicYear",
        "Term",
        "Cohort",
        "StudentIdentity",
        "StudentAlias",
        "AdministrativeClass",
        "Enrollment",
        "TeachingClass",
        "TeachingClassMember",
        "HomeroomTeachingLink",
        "LinkedStudent",
        "SourceMap",
        "MigrationRun",
        "ScoreFact",
        "ImportBatch",
    ):
        assert hasattr(wm, table), table


@pytest.fixture()
def s01_base():
    """S01 专用最小数据：教师绑定高二 6 班 + 一个学年，无任何教学班。

    幂等：本文件用例共用同学年，第二次进入直接复用已存在的
    '2025-2026'（academic_year.name 全局唯一），避免撞 UNIQUE。
    """
    from datetime import datetime

    from app.db import workspace_models as wm
    from app.db.models import HomeworkSetting, SessionLocal, Teacher

    db = SessionLocal()
    db.merge(Teacher(id=1, name="测试班主任", target_class_high2=6))
    db.merge(HomeworkSetting(key="active_grade", value="2"))
    ay = db.query(wm.AcademicYear).filter_by(name=AY_NAME).first()
    if ay is None:
        ay = wm.AcademicYear(
            name=AY_NAME,
            start_date=datetime(2025, 9, 1).date(),
            end_date=datetime(2026, 7, 15).date(),
        )
        db.add(ay)
    db.flush()
    ay_id = ay.id
    db.commit()
    try:
        yield SimpleNamespace(db=db, ay_id=ay_id)
    finally:
        db.close()


def test_teaching_scope_unconfigured_returns_409(client, s01_base):
    """教学学科未配置：teaching scope 一律 409，绝不回退行政班或全年级。"""
    r = client.get(
        f"{API}/shared/scope",
        params={
            "mode": "teaching",
            "academic_year_id": s01_base.ay_id,
            "teaching_class_id": 999999,
        },
    )
    assert r.status_code == 409
    assert r.json().get("error") == "workspace_not_configured"


def test_scope_invalid_params_return_422(client, s01_base):
    """mode 非法 / homeroom 缺 class_id：422，且错误码可识别。"""
    r = client.get(
        f"{API}/shared/scope",
        params={
            "mode": "principal",  # 不存在的 mode：mode 不是权限凭证
            "academic_year_id": s01_base.ay_id,
            "class_id": 1,
        },
    )
    assert r.status_code == 422
    assert r.json().get("error") == "invalid_scope_param"

    # 缺 class_id：不得回退教师默认班（FastAPI 参数校验 422 也算命中）
    r2 = client.get(
        f"{API}/shared/scope",
        params={"mode": "homeroom", "academic_year_id": s01_base.ay_id},
    )
    assert r2.status_code == 422


def test_scope_nonexistent_class_returns_404(client, s01_base):
    """格式合法但不存在的 class id：404 resource_out_of_scope，而非空态。"""
    r = client.get(
        f"{API}/shared/scope",
        params={
            "mode": "homeroom",
            "academic_year_id": s01_base.ay_id,
            "class_id": 999999,
        },
    )
    assert r.status_code == 404
    assert r.json().get("error") == "resource_out_of_scope"


def test_scope_empty_teaching_class_returns_empty_not_grade(client, s01_base):
    """无成员教学班：200 空态，member_person_ids==[]，绝不退化全年级人数。"""
    from app.db import workspace_models as wm

    t_empty = wm.TeachingClass(
        academic_year_id=s01_base.ay_id, subject="物理", label="高二6班(教·空)"
    )
    _set_first(t_empty, ("teacher_id",), 1)
    s01_base.db.add(t_empty)
    s01_base.db.commit()

    r = client.get(
        f"{API}/shared/scope",
        params={
            "mode": "teaching",
            "academic_year_id": s01_base.ay_id,
            "teaching_class_id": t_empty.id,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "teaching"
    assert body["member_person_ids"] == []
    assert body.get("cohort_size") in (0, None)
