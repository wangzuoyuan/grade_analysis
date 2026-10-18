"""审查问题 2 回归：历史学号消歧/新建身份路径接通。

背景（独立代码审查报告 §2 / 核实说明 §2 / probe.py 2A_declared_new、
2B_reused_id，修复范围以核实说明为准）：

- 2A 历史同号同名候选 + identity_new_aliases 显式新建：确认阶段
  `if alias in declared_new: continue` 后从未把该候选加入 new_students
  → 写入阶段 identity_by_alias[alias] KeyError（500）。
- 2B 历史学年学号被不同姓名的新生复用 + 显式新建：解析阶段把跨学年
  历史命中因姓名不同直接标 conflicts，确认的冲突检查先于 declared_new
  声明执行 → 409 无法消歧。修复后区分「跨学年（非本学年在册）的历史
  同号」与「本学年实际撞号」：前者归 candidates（允许显式新建/接续），
  本学年同号异名仍 409（绝不自动认定同人）。

守卫：不声明新建的候选照旧 409 引导；伪造/未在预览出现的 alias 声明
无效（422）；本学年撞号声明新建也拒绝；确认后历史学生记录不变、
新身份独立入库。teaching_platform.py 预览段同口径。
"""

from datetime import date
from pathlib import Path

from tests.v1.conftest import ALIAS_JIA_H
from tests.v1.test_f05_f06_f07 import (
    _make_history_person,
    _student_row,
    _upload,
    _write_grade23_xlsx,
)

API = "/api/v1"

EXAM_A = "FIX02同号新建考"
EXAM_B = "FIX02异名新建考"
EXAM_CURRENT = "FIX02本年撞号考"
EXAM_TEACH = "FIX02教学异名考"


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _preview_confirm(client, v1_seed, tmp_path, alias, name, exam_name,
                     filename, declared=None, confirmations=None):
    path = _write_grade23_xlsx(
        tmp_path / filename, [_student_row(alias, name, chinese=75, math=76)],
    )
    prev = _upload(
        client, path, "homeroom",
        {"class_id": v1_seed.h6_id, "exam_name": exam_name,
         "exam_date": "2025-11-10"},
    )
    assert prev.status_code == 200, prev.text
    body = {"token": prev.json()["token"]}
    if declared is not None:
        body["identity_new_aliases"] = declared
    if confirmations is not None:
        body["identity_confirmations"] = confirmations
    r = client.post(f"{API}/imports/confirm", json=body)
    return prev.json(), r


# ────────────────────────── 2A：同号同名候选显式新建 ──────────────────────────


def test_2a_history_same_name_declared_new_creates_independent_identity(
    client, v1_seed, tmp_path,
):
    """历史同号同名候选 + identity_new_aliases：200，新身份独立入库，
    历史学生（合成旧生）记录不变。修复前实测 confirm 500（KeyError）。"""
    from app.db import workspace_models as wm

    db = _db()
    try:
        old_pid, old_ay_id = _make_history_person(
            db, "FIX02学年2A", "2024-09-01", "2025-07-15",
            "homeroom", "秦旧二", "SYN-HIST-2A",
        )
    finally:
        db.close()

    prev, ok = _preview_confirm(
        client, v1_seed, tmp_path, "SYN-HIST-2A", "秦旧二", EXAM_A,
        "FIX02同号.xlsx", declared=["SYN-HIST-2A"],
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["students_created"] == 1

    db = _db()
    try:
        facts = db.query(wm.ScoreFact).filter_by(exam_name=EXAM_A).all()
        assert facts, "成绩必须落库"
        new_pid = facts[0].identity_id
        assert new_pid != old_pid  # 新身份独立，绝不动历史 identity
        # 新身份建了档案：本学年 alias + 行政班学籍（F07 生效日规则）
        alias_now = (
            db.query(wm.WsStudentAlias)
            .filter_by(alias_value="SYN-HIST-2A", data_domain="homeroom",
                       academic_year_id=v1_seed.ay_id)
            .one()
        )
        assert alias_now.identity_id == new_pid
        enrollment = (
            db.query(wm.Enrollment)
            .filter_by(admin_class_id=v1_seed.h6_id, identity_id=new_pid)
            .one()
        )
        assert enrollment.valid_from == date(2025, 11, 10)
        # 历史学生记录不变：旧 identity 仍在、没有新学年 alias/学籍
        assert db.get(wm.WsStudentIdentity, old_pid) is not None
        assert (
            db.query(wm.WsStudentAlias)
            .filter_by(identity_id=old_pid, data_domain="homeroom",
                       academic_year_id=v1_seed.ay_id)
            .count() == 0
        )
        assert (
            db.query(wm.Enrollment)
            .filter_by(admin_class_id=v1_seed.h6_id, identity_id=old_pid)
            .count() == 0
        )
    finally:
        db.close()


# ────────────────────────── 2B：历史同号异名显式新建 ──────────────────────────


def test_2b_history_different_name_declared_new_succeeds(
    client, v1_seed, tmp_path,
):
    """历史学年学号被不同姓名的新生复用 + 显式新建：preview 出候选（而非
    撞号警告），confirm 200 建新身份；历史学生记录不变。
    修复前实测 preview 200 带「同学号不同姓名」警告、confirm 409。"""
    from app.db import workspace_models as wm

    db = _db()
    try:
        old_pid, old_ay_id = _make_history_person(
            db, "FIX02学年2B", "2023-09-01", "2024-07-15",
            "homeroom", "秦古二", "SYN-HIST-2B",
        )
    finally:
        db.close()

    prev, ok = _preview_confirm(
        client, v1_seed, tmp_path, "SYN-HIST-2B", "秦新生二", EXAM_B,
        "FIX02异名.xlsx", declared=["SYN-HIST-2B"],
    )
    # 预览层：跨学年同号异名是候选，不是撞号警告
    item = prev["items"][0]
    assert item["identity_candidates"], "跨学年同号异名必须出候选清单"
    assert {c["person_id"] for c in item["identity_candidates"]} == {old_pid}
    assert not any("同学号不同姓名" in w for w in item["warnings"])

    assert ok.status_code == 200, ok.text
    assert ok.json()["students_created"] == 1

    db = _db()
    try:
        facts = db.query(wm.ScoreFact).filter_by(exam_name=EXAM_B).all()
        new_pid = facts[0].identity_id
        assert new_pid != old_pid
        # 新身份用文件姓名建档（不是历史姓名）
        new_ident = db.get(wm.WsStudentIdentity, new_pid)
        assert new_ident.display_name == "秦新生二"
        # 历史学生记录不变
        old_ident = db.get(wm.WsStudentIdentity, old_pid)
        assert old_ident.display_name == "秦古二"
        assert (
            db.query(wm.WsStudentAlias)
            .filter_by(identity_id=old_pid, data_domain="homeroom",
                       academic_year_id=v1_seed.ay_id)
            .count() == 0
        )
    finally:
        db.close()


def test_2b_without_declaration_still_guided_to_confirm(
    client, v1_seed, tmp_path,
):
    """守卫：跨学年同号异名候选不做任何声明 → 仍 409 附候选清单引导
    显式确认/新建，零写入（绝不静默猜）。"""
    from app.db import workspace_models as wm

    db = _db()
    try:
        _make_history_person(
            db, "FIX02学年2C", "2023-09-01", "2024-07-15",
            "homeroom", "秦古三", "SYN-HIST-2C",
        )
    finally:
        db.close()

    prev, denied = _preview_confirm(
        client, v1_seed, tmp_path, "SYN-HIST-2C", "秦新生三", "FIX02未声明考",
        "FIX02未声明.xlsx",
    )
    assert denied.status_code == 409, denied.text
    body = denied.json()
    assert body["error"] == "link_version_conflict"
    assert body["identity_candidates"]  # 候选清单引导，而非撞号冲突

    db = _db()
    try:
        assert db.query(wm.ScoreFact).filter_by(exam_name="FIX02未声明考").count() == 0
    finally:
        db.close()


def test_forged_new_alias_declaration_rejected(client, v1_seed, tmp_path):
    """守卫：伪造/未在预览候选中出现的 alias 声明新建 → 422 无效，
    零写入。"""
    from app.db import workspace_models as wm

    db = _db()
    try:
        _make_history_person(
            db, "FIX02学年2D", "2024-09-01", "2025-07-15",
            "homeroom", "秦旧四", "SYN-HIST-2D",
        )
    finally:
        db.close()

    # 全新学号（无任何命中，本就按新学生建档）+ 伪造的「新建」声明
    prev, r = _preview_confirm(
        client, v1_seed, tmp_path, "SYN-ALL-NEW-2D", "秦全新", "FIX02伪造考",
        "FIX02伪造.xlsx", declared=["SYN-FORGED-2D"],
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"] == "invalid_scope_param"

    db = _db()
    try:
        assert db.query(wm.ScoreFact).filter_by(exam_name="FIX02伪造考").count() == 0
        assert (
            db.query(wm.WsStudentIdentity)
            .filter_by(display_name="秦全新")
            .count() == 0
        )
    finally:
        db.close()


def test_current_year_collision_still_rejected_even_declared_new(
    client, v1_seed, tmp_path,
):
    """守卫：本学年同号异名（秦甲在册）→ 即使声明 identity_new_aliases
    也 409 撞号拒绝，绝不自动认定同人、也不允许借声明绕过撞号。"""
    from app.db import workspace_models as wm

    prev, r = _preview_confirm(
        client, v1_seed, tmp_path, ALIAS_JIA_H, "秦冒名", EXAM_CURRENT,
        "FIX02撞号.xlsx", declared=[ALIAS_JIA_H],
    )
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error"] == "link_version_conflict"
    assert body["conflicts"]  # 撞号冲突，而非候选清单

    db = _db()
    try:
        assert (
            db.query(wm.ScoreFact).filter_by(exam_name=EXAM_CURRENT).count() == 0
        )
    finally:
        db.close()


# ────────────────────── teaching_platform.py 预览段同口径 ──────────────────────


def test_platform_history_different_name_declared_new(client, tmp_path):
    """教学平台导入：历史学年同号异名 → 预览出候选（不再整批 409 撞号），
    confirm identity_new_aliases 新建身份；历史学生记录不变。
    修复前实测 preview 409「学号姓名冲突，整批拒绝」。"""
    from io import BytesIO

    from app.db import workspace_models as wm
    from app.db.models import SessionLocal, Teacher
    from openpyxl import Workbook

    db = SessionLocal()
    try:
        db.merge(Teacher(id=1, name="合成教师"))
        ay = wm.AcademicYear(
            name="FIX02合成学年", start_date=date(2030, 9, 1),
            end_date=date(2031, 8, 31),
        )
        db.add(ay)
        db.flush()
        tc = wm.TeachingClass(
            academic_year_id=ay.id, grade=2, subject="物理", label="FIX02班",
        )
        db.add(tc)
        db.flush()
        old_ay = wm.AcademicYear(
            name="FIX02旧学年", start_date=date(2029, 9, 1),
            end_date=date(2030, 8, 31),
        )
        db.add(old_ay)
        db.flush()
        old_ident = wm.WsStudentIdentity(
            data_domain="teaching", display_name="秦平台旧",
        )
        db.add(old_ident)
        db.flush()
        db.add(
            wm.WsStudentAlias(
                identity_id=old_ident.id, alias_value="SYN-PLAT-OLD",
                data_domain="teaching", academic_year_id=old_ay.id,
                alias_scope=str(old_ay.id), source="synthetic-test",
            )
        )
        db.commit()
        year_id, tc_id, old_pid = ay.id, tc.id, old_ident.id
    finally:
        db.close()

    def detail(alias, name, score):
        wb = Workbook()
        ws = wb.active
        ws.append(["学号", "姓名", "原始分数", "等级分数"])
        ws.append([alias, name, score, None])
        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()

    form = {
        "academic_year_id": str(year_id), "grade": "2", "subject": "物理",
        "teaching_class_id": str(tc_id), "exam_name": EXAM_TEACH,
        "exam_date": "2030-11-01", "operation": "initial",
    }
    files = [("files", ("明细.xlsx", detail("SYN-PLAT-OLD", "秦平台新", 66),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))]
    p = client.post(f"{API}/teaching/platform/imports/preview",
                    data=form, files=files)
    assert p.status_code == 200, p.text
    body = p.json()
    assert {c["person_id"] for c in body["identity_candidates"]} == {old_pid}

    # 不声明 → 409 引导（enforce）
    r = client.post(
        f"{API}/teaching/platform/imports/confirm",
        json={"token": body["token"], "accept_roster_changes": True},
    )
    assert r.status_code == 409, r.text

    # 重新预览（token 单次消费）+ 显式新建 → 200
    p2 = client.post(f"{API}/teaching/platform/imports/preview",
                     data=form, files=files)
    assert p2.status_code == 200, p2.text
    token2 = p2.json()["token"]
    ok = client.post(
        f"{API}/teaching/platform/imports/confirm",
        json={"token": token2, "accept_roster_changes": True,
              "identity_new_aliases": ["SYN-PLAT-OLD"]},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["students_created"] == 1

    db = SessionLocal()
    try:
        fact = db.query(wm.ScoreFact).filter_by(exam_name=EXAM_TEACH).one()
        assert fact.identity_id != old_pid
        assert db.get(wm.WsStudentIdentity, old_pid).display_name == "秦平台旧"
        assert (
            db.query(wm.WsStudentAlias)
            .filter_by(identity_id=old_pid, data_domain="teaching",
                       academic_year_id=year_id)
            .count() == 0
        )
    finally:
        db.close()
