"""学期管理测试：自动推算 / 当前学期切换 / 新增与编辑校验。

使用独立内存 SQLite，不触碰真实 ~/.exam-tracker/db.sqlite。
"""

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.models as models
from app.homework import service


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


# === derive_semester 自动推算 ===

def test_derive_autumn_september():
    d = service.derive_semester(date(2026, 9, 1))
    assert d["semester_start"] == "2026-09-01"
    assert d["semester_end"] == "2027-01-31"
    assert d["semester_name"] == "2026学年第一学期"
    assert d["auto"] is True


def test_derive_january_belongs_to_previous_autumn():
    d = service.derive_semester(date(2027, 1, 15))
    assert d["semester_start"] == "2026-09-01"
    assert d["semester_end"] == "2027-01-31"
    assert d["semester_name"] == "2026学年第一学期"


def test_derive_spring():
    d = service.derive_semester(date(2026, 3, 10))
    assert d["semester_start"] == "2026-02-01"
    assert d["semester_end"] == "2026-06-30"
    assert d["semester_name"] == "2025学年第二学期"


def test_derive_summer_vacation_keeps_ended_spring():
    for day in (date(2026, 7, 5), date(2026, 8, 20)):
        d = service.derive_semester(day)
        assert d["semester_start"] == "2026-02-01"
        assert d["semester_end"] == "2026-06-30"
        assert d["semester_name"] == "2025学年第二学期"


# === get_semester 回落与切换 ===

def test_get_semester_falls_back_to_derived(db):
    sem = service.get_semester(db)
    assert sem["auto"] is True
    assert sem["semester_id"] is None
    items = service.list_semesters(db)
    assert items[0]["is_current"] is True
    assert items[0]["auto"] is True
    assert items[0]["id"] is None


def test_add_and_switch_semester(db):
    service.add_semester(db, "2025学年第二学期", "2026-02-17", "2026-07-04")
    # 未设当前 → 仍走推算
    assert service.get_semester(db)["auto"] is True
    row = db.query(models.HomeworkSemester).order_by(models.HomeworkSemester.id).first()
    assert service.set_current_semester(db, row.id) is True
    sem = service.get_semester(db)
    assert sem["auto"] is False
    assert sem["semester_start"] == "2026-02-17"
    assert sem["semester_id"] == row.id
    items = service.list_semesters(db)
    assert items[0]["is_current"] is True and items[0]["auto"] is False
    assert service.set_current_semester(db, 9999) is False


def test_add_semester_rejects_duplicate_and_inverted_dates(db):
    service.add_semester(db, "2025学年第二学期", "2026-02-17", "2026-07-04")
    with pytest.raises(ValueError):
        service.add_semester(db, "2025学年第二学期", "2026-02-17", "2026-07-04")
    with pytest.raises(ValueError):
        service.add_semester(db, "倒置学期", "2026-07-04", "2026-02-17")


def test_add_semester_make_current_clears_old(db):
    first = db.query(models.HomeworkSemester).filter_by(id=-1).first()
    assert first is None
    service.add_semester(db, "2025学年第二学期", "2026-02-17", "2026-07-04", make_current=True)
    service.add_semester(db, "2026学年第一学期", "2026-09-01", "2027-01-31", make_current=True)
    currents = db.query(models.HomeworkSemester).filter_by(is_current=1).all()
    assert len(currents) == 1
    assert currents[0].name == "2026学年第一学期"


def test_set_semester_creates_current_row(db):
    sem = service.set_semester(db, {
        "semester_start": "2026-09-01",
        "semester_end": "2027-01-31",
        "semester_name": "2026学年第一学期",
    })
    assert sem["auto"] is False
    assert sem["semester_name"] == "2026学年第一学期"
    with pytest.raises(ValueError):
        service.set_semester(db, {"semester_start": "2027-01-31", "semester_end": "2026-09-01"})
