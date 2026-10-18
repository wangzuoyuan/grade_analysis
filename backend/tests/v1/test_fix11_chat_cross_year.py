"""fix11 专项：AI 聊天工具跨学年查询三处失败（独立代码审查报告 §11）。

换届后新学年的行政班是新行（AdministrativeClass 每年一行，id 不同），
「本班」跨学年的稳定身份是教师绑定班号。三处失败：
① 十几个 homeroom 工具写死快照 class_ids[0] 配目标学年 → 核心解析
   ResourceOutOfScope（换届后跨年查询 100% 报错）；
② get_diagnosis_summary 把本学年快照与跨年 year_id 一起传 diagnosis →
   _check_year 422「academic_year_id does not match the resolved scope」；
③ search_students 跨年查出目标学年名册后又用本学年快照成员过滤 →
   历史学年学生搜不到。

修复（chat_tools.py，作用域服务端解析、绝不猜）：
- _homeroom_year_class：按教师绑定班号解析目标学年行政班（①当届年级+
  绑定班号精确 → ②换届回看该学年班号唯一实例 → ③未换届延续 carryover），
  解析不出/歧义 → 可读中文错误（歧义列候选班）；
- _year_scope_snapshot：跨年重建目标学年同源快照（diagnosis _check_year
  的硬要求；成员口径 = 该学年本班名册）；
- search_students 跨年成员收敛改用目标学年名册同款服务端口径，
  范围限制不放宽（非绑定班学生绝不出现）。

样本（全部合成姓名）：v1_seed（2025-2026 高二6班，教师绑定 high2=6、
active_grade=2）之上手工补学年班级，模拟真实换届后的结构：
- 2024-2025 高一6班（甲乙丙在班 + 考试「2024期末」）——②换届回看路径；
- 2026-2027 高三6班（甲乙丙+新转入「秦庚」在班 + 考试「2026开学考」）
  ——快照学年（最新学年），行政班是新行；
- 「秦巳」仅 2025-2026 在班（换届前离班）——跨年搜索回归样本；
- 「秦旁」在非绑定班 高二9班——范围限制守护样本；
- 2023-2024 学年存在但无任何班级行——无班可读错误样本。
"""

from datetime import date

import pytest


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _snap(db, mode, **kw):
    from app.api.chat_tools import resolve_scope_snapshot

    return resolve_scope_snapshot(db, 1, mode, **kw)


def _run(db, snapshot, name, args=None):
    from app.api.chat_tools import execute_session_tool

    return execute_session_tool(db, snapshot, name, args or {})


@pytest.fixture(scope="module")
def rollover_seed(v1_seed):
    """v1_seed 之上补两学年两班 + 跨年成员/事实（纯模型写入，合成数据）。

    结构等价于「2024-2025 高一 → 2025-2026 高二（v1_seed）→ 换届 →
    2026-2027 高三」：班号 6 沿三个学年，每年一行新 id；active_grade 与
    教师绑定保持 v1_seed 原样（高二 6 班）。"""
    from types import SimpleNamespace

    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    db = SessionLocal()

    def year(name, start, end):
        row = wm.AcademicYear(name=name, start_date=start, end_date=end)
        db.add(row)
        db.flush()
        return row

    y_pre = year("2023-2024", date(2023, 9, 1), date(2024, 7, 15))
    y_old = year("2024-2025", date(2024, 9, 1), date(2025, 7, 15))
    y_new = year("2026-2027", date(2026, 9, 1), date(2027, 7, 15))

    h_old = wm.AdministrativeClass(
        academic_year_id=y_old.id, grade=1, class_num=6, label="高一6班"
    )
    h_new = wm.AdministrativeClass(
        academic_year_id=y_new.id, grade=3, class_num=6, label="高三6班"
    )
    db.add_all([h_old, h_new])
    db.flush()

    def identity(name):
        row = wm.WsStudentIdentity(data_domain="homeroom", display_name=name)
        db.add(row)
        db.flush()
        return row

    si_h = identity("秦巳")  # 仅 2025-2026 在班（换届前离班）
    geng_h = identity("秦庚")  # 换届后新转入（仅 2026-2027 在班）
    pang_h = identity("秦旁")  # 非绑定班（高二9班）学生，范围守护样本

    def enrollment(cls_id, ident_id, valid_from):
        db.add(
            wm.Enrollment(
                admin_class_id=cls_id,
                identity_id=ident_id,
                status="active",
                valid_from=valid_from,
            )
        )

    # 2024-2025 高一6班：甲乙丙
    for pid in (v1_seed.jia_h_id, v1_seed.yi_h_id, v1_seed.bing_h_id):
        enrollment(h_old.id, pid, date(2024, 9, 1))
    # 2026-2027 高三6班：甲乙丙 + 新转入秦庚
    for pid in (v1_seed.jia_h_id, v1_seed.yi_h_id, v1_seed.bing_h_id, geng_h.id):
        enrollment(h_new.id, pid, date(2026, 9, 1))
    # 2025-2026 高二6班补秦巳（v1_seed 已有甲乙丙）；高二9班收秦旁
    enrollment(v1_seed.h6_id, si_h.id, date(2025, 9, 1))
    enrollment(v1_seed.h9_id, pang_h.id, date(2025, 9, 1))

    def fact(year_id, cls_obj, ident_id, exam_name, exam_day, subject, total_type, score):
        db.add(
            wm.ScoreFact(
                data_domain="homeroom",
                academic_year_id=year_id,
                exam_name=exam_name,
                exam_date=exam_day,
                class_ref_id=cls_obj.id,
                identity_id=ident_id,
                subject=subject,
                total_type=total_type,
                score=score,
                source="synthetic-test",
            )
        )

    # 2024-2025「2024期末」（回看最早年也能查）
    fact(y_old.id, h_old, v1_seed.jia_h_id, "2024期末", date(2025, 1, 15), "语文", None, 84.0)
    fact(y_old.id, h_old, v1_seed.yi_h_id, "2024期末", date(2025, 1, 15), "语文", None, 74.0)
    # 2026-2027「2026开学考」（新学年新班的当期数据）
    fact(y_new.id, h_new, v1_seed.jia_h_id, "2026开学考", date(2026, 10, 5), "语文", None, 89.0)
    fact(y_new.id, h_new, v1_seed.yi_h_id, "2026开学考", date(2026, 10, 5), "语文", None, 78.0)
    fact(y_new.id, h_new, geng_h.id, "2026开学考", date(2026, 10, 5), "语文", None, 71.0)
    db.commit()

    yield SimpleNamespace(
        y_pre_id=y_pre.id,
        y_old_id=y_old.id,
        y_new_id=y_new.id,
        h_old_id=h_old.id,
        h_new_id=h_new.id,
        si_h_id=si_h.id,
        geng_h_id=geng_h.id,
        pang_h_id=pang_h.id,
    )
    db.close()


# ────────────── ① 换届后跨年考试目录/统计/逐人矩阵（probe 11A 回归） ──────────────


def test_exam_catalog_and_stats_cross_year_after_rollover(
    v1_seed, rollover_seed, client
):
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        # 换届后：快照学年=最新学年 2026-2027，行政班=新行（id 不同于旧学年）
        assert snap["academic_year_id"] == rollover_seed.y_new_id
        assert snap["class_ids"] == [rollover_seed.h_new_id]
        assert rollover_seed.h_new_id != v1_seed.h6_id

        # 修复前：新班 id 配旧学年 → ResourceOutOfScope「…不在当前会话范围」
        prev = _run(db, snap, "get_exam_list", {"year_offset": -1})
        older = _run(db, snap, "get_exam_list", {"year_offset": -2})
        current = _run(db, snap, "get_exam_list", {})
    finally:
        db.close()
    assert "error" not in prev
    assert [e["exam_name"] for e in prev["exams"]] == ["2025期中"]
    assert [e["exam_name"] for e in older["exams"]] == ["2024期末"]
    assert [e["exam_name"] for e in current["exams"]] == ["2026开学考"]

    # A01：跨年统计与显式「学年+班级」的页面端点逐字段一致
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        tool = _run(
            db, snap, "get_exam_stats", {"exam_name": "2025期中", "year_offset": -1}
        )
        students = _run(
            db, snap, "get_exam_students", {"exam_name": "2025期中", "year_offset": -1}
        )
    finally:
        db.close()
    assert "error" not in tool
    api = client.get(
        "/api/v1/homeroom/analysis/exams/2025期中/stats",
        params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
    ).json()
    assert tool == api
    main3 = next(t for t in tool["totals"] if t["total_type"] == "主三门")
    assert main3["avg"] == 239.33
    # 逐人矩阵按【考试时点成员】解析：2025-2026 高二6班 = 甲乙丙巳 四人
    assert {s["person_id"] for s in students["students"]} == {
        v1_seed.jia_h_id,
        v1_seed.yi_h_id,
        v1_seed.bing_h_id,
        rollover_seed.si_h_id,
    }


# ────────────── ② 跨年诊断摘要不再 422（probe 11B 回归） ──────────────


def test_diagnosis_summary_cross_year_no_422(v1_seed, rollover_seed):
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        # 修复前：InvalidScopeParam「academic_year_id does not match the
        # resolved scope」（本学年快照 + 跨年 year_id 一起传 diagnosis）
        student = _run(
            db,
            snap,
            "get_diagnosis_summary",
            {"academic_year_id": v1_seed.ay_id, "person_id": v1_seed.jia_h_id},
        )
        klass = _run(db, snap, "get_diagnosis_summary", {"year_offset": -1})
        # 新转入学生（仅 2026-2027 在班）不在 2025-2026 名册 → 可读说明
        outsider = _run(
            db,
            snap,
            "get_diagnosis_summary",
            {"academic_year_id": v1_seed.ay_id, "person_id": rollover_seed.geng_h_id},
        )
    finally:
        db.close()
    assert "error" not in student
    assert student["summary_kind"] == "student"
    assert student["academic_year_id"] == v1_seed.ay_id
    assert "error" not in klass
    assert klass["summary_kind"] == "class"
    assert klass["academic_year_id"] == v1_seed.ay_id
    assert outsider["error"] == "resource_out_of_scope"
    assert "2025-2026" in outsider["detail"]


# ────────────── ③ 跨年搜索找得到旧学年学生、本学年行为不变 ──────────────


def test_search_students_cross_year_member_convergence(v1_seed, rollover_seed):
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        # 修复前：跨年名册被本学年快照成员截断（秦巳 2025-2026 在班、
        # 换届前离班 → 搜不到）
        prev = _run(db, snap, "search_students", {"q": "秦巳", "year_offset": -1})
        cur = _run(db, snap, "search_students", {"q": "秦巳"})
        prev_all = _run(db, snap, "search_students", {"year_offset": -1})
        cur_all = _run(db, snap, "search_students", {})
        # 范围限制不放宽：非绑定班（高二9班）学生绝不出现
        pang = _run(db, snap, "search_students", {"q": "秦旁", "year_offset": -1})
    finally:
        db.close()
    assert [s["person_id"] for s in prev["students"]] == [rollover_seed.si_h_id]
    assert cur["students"] == []  # 本学年（换届后）名册没有秦巳：行为不变
    assert {s["person_id"] for s in prev_all["students"]} == {
        v1_seed.jia_h_id,
        v1_seed.yi_h_id,
        v1_seed.bing_h_id,
        rollover_seed.si_h_id,
    }
    assert {s["person_id"] for s in cur_all["students"]} == {
        v1_seed.jia_h_id,
        v1_seed.yi_h_id,
        v1_seed.bing_h_id,
        rollover_seed.geng_h_id,
    }
    assert pang["students"] == []


# ────────────── teaching 跨年既有实现不被破坏 ──────────────


def test_teaching_cross_year_still_works(v1_seed, rollover_seed):
    db = _db()
    try:
        snap = _snap(db, "teaching")
        assert snap["subject"] == "物理"
        prev = _run(db, snap, "get_exam_list", {"year_offset": -1})
        search = _run(db, snap, "search_students", {"q": "秦丁", "year_offset": -1})
        diag = _run(
            db,
            snap,
            "get_diagnosis_summary",
            {"academic_year_id": v1_seed.ay_id, "person_id": v1_seed.ding_t_id},
        )
    finally:
        db.close()
    assert "error" not in prev
    assert [e["exam_name"] for e in prev["exams"]] == ["2025期中"]
    assert [s["person_id"] for s in search["students"]] == [v1_seed.ding_t_id]
    assert "error" not in diag
    assert diag["summary_kind"] == "student"


# ────────────── 无班学年 / 歧义学年 → 可读错误（绝不猜） ──────────────


def test_cross_year_without_class_readable_error(v1_seed, rollover_seed):
    """2023-2024 无任何班级行：绑定班号 6 在该学年无班、也无更早延续班
    → 可读中文错误（错误类与核心解析同款 workspace_not_configured，
    detail 带学年名与绑定班号，不再是裸英文栈）。"""
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        result = _run(db, snap, "get_exam_list", {"year_offset": -3})
        search = _run(db, snap, "search_students", {"year_offset": -3})
        diag = _run(db, snap, "get_diagnosis_summary", {"year_offset": -3})
    finally:
        db.close()
    assert result["error"] == "workspace_not_configured"
    assert "2023-2024" in result["detail"]
    assert "没有本人行政班" in result["detail"]
    assert search["error"] == "workspace_not_configured"
    assert diag["error"] == "workspace_not_configured"


def test_cross_year_ambiguous_class_lists_candidates(v1_seed, rollover_seed):
    """目标学年按绑定班号匹配到多个班级且无映射可辨 → 可读错误列出
    候选班，绝不猜（本用例临时加行，finally 清理还原）。"""
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    db = SessionLocal()
    extra1 = wm.AdministrativeClass(
        academic_year_id=rollover_seed.y_pre_id,
        grade=1,
        class_num=6,
        label="高一6班(歧义)",
    )
    extra3 = wm.AdministrativeClass(
        academic_year_id=rollover_seed.y_pre_id,
        grade=3,
        class_num=6,
        label="高三6班(歧义)",
    )
    db.add_all([extra1, extra3])
    db.commit()
    try:
        snap = _snap(db, "homeroom")
        result = _run(db, snap, "get_exam_list", {"year_offset": -3})
    finally:
        db.delete(extra1)
        db.delete(extra3)
        db.commit()
        db.close()
    assert result["error"] == "workspace_not_configured"
    assert "匹配到 2 个行政班" in result["detail"]
    assert "高一6班(歧义)" in result["detail"]
    assert "高三6班(歧义)" in result["detail"]
