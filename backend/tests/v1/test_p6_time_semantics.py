"""P6 时间语义专项测试（跨学年 year_offset / 跨学期 term_offset）。

用户关键需求：AI 要听得懂「上学年/上学期/上上学年/这学期/2026学年第一
学期」——模型只需传相对偏移或直接按名匹配，服务端换算成具体 id。

学期源 = **ws_homework_semester**（学期设置页/作业看板同一事实源；
P1 Term 表真实部署无数据、无维护入口，不再作为学期来源）。term_id 即
作业学期 id。

- ① year_offset=-1 命中早学年数据（get_exam_stats 拿到 2025-2026 学年
  考试统计；本模块把最新学年造成 2026-2027，快照锚点=2026-2027，行政班
  经「未换届自动延续」沿 2025-2026 的 H6，-1 即 2025-2026）
- ② term_offset=-1 跨学年自动落到上一学年第二学期（全学期排平换算）
- ③ id 与 offset 同传 → 可读错误
- ④ 都不传 → 仍为快照学年（与既有行为完全兼容）
- ⑤ get_academic_years 返回 is_current / year_offset / term_offset 标注
  且与库一致（模型由此看到「上学年 = offset -1 = 2025-2026（id=N）」）
- ⑥ 目录出现用户维护风格的学期名（如「2026学年第一学期」），term_id
  直传作业学期 id 可解析
- ⑦ get_student_notes 学期参数按作业学期起止日期过滤档案（学期内留、
  学期外去；不传学期参数=不过滤）
- ⑧ 作业学期挂不存在学年（无对应学年行）→ 目录顶层 unmatched_semesters
  收录，不崩、绝不静默丢弃
- ⑨ 假期条目（暑假/寒假/假期）不参与「上/下学期」偏移：term_offset=-1
  跳过假期落到正确学期；目录中假期标 vacation=true 且 term_offset=null
  （不占编号），term_id 仍可直传查假期数据

学期结构（本模块独立 DB，v1_seed 之上补齐；名称按用户维护风格
「XXXX学年第X学期」，XXXX = 开学年份，挂对应 academic_year_id）：
- 2024-2025：2024学年第一学期 / 2024学年第二学期
- 2025-2026（v1_seed 学年，含 E1 数据）：2025学年第一学期 / 2025学年第二学期
  / 2025学年暑假（假期条目，不占偏移编号）
- 2026-2027（最新学年=锚点）：2026学年第一学期（is_current=1）/ 2026学年第二学期
"""

from datetime import date
from types import SimpleNamespace

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
def time_seed(v1_seed):
    """补齐跨学年学年/作业学期结构（纯目录数据，零业务写入）。

    学期造在 ws_homework_semester（学期设置页同一事实源），挂对应
    academic_year_id；锚点学期「2026学年第一学期」is_current=1 且含今天。"""
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    db = SessionLocal()
    ay_old = wm.AcademicYear(
        name="2024-2025", start_date=date(2024, 9, 1), end_date=date(2025, 7, 15)
    )
    ay_newer = wm.AcademicYear(
        name="2026-2027", start_date=date(2026, 9, 1), end_date=date(2027, 7, 15)
    )
    db.add_all([ay_old, ay_newer])
    db.flush()

    def semester(ay_id, name, start, end, is_current=0):
        row = wm.WsHomeworkSemester(
            academic_year_id=ay_id,
            name=name,
            start_date=start,
            end_date=end,
            is_current=is_current,
            mode="manual",
        )
        db.add(row)
        return row

    semesters = {
        "高一上": semester(
            ay_old.id, "2024学年第一学期", date(2024, 9, 1), date(2025, 1, 31)
        ),
        "高一下": semester(
            ay_old.id, "2024学年第二学期", date(2025, 2, 1), date(2025, 7, 15)
        ),
        "高二上": semester(
            v1_seed.ay_id, "2025学年第一学期", date(2025, 9, 1), date(2026, 1, 31)
        ),
        "高二下": semester(
            v1_seed.ay_id, "2025学年第二学期", date(2026, 2, 1), date(2026, 7, 15)
        ),
        # 假期条目（真实库同款）：保留在目录里，但不参与「上/下学期」偏移
        "暑假": semester(
            v1_seed.ay_id, "2025学年暑假", date(2026, 7, 1), date(2026, 8, 31)
        ),
        "高三上": semester(
            ay_newer.id,
            "2026学年第一学期",
            date(2026, 9, 1),
            date(2027, 1, 31),
            is_current=1,
        ),
        "高三下": semester(
            ay_newer.id, "2026学年第二学期", date(2027, 2, 1), date(2027, 7, 15)
        ),
    }
    db.commit()
    yield SimpleNamespace(
        ay_old_id=ay_old.id,
        ay_newer_id=ay_newer.id,
        **{key: row.id for key, row in semesters.items()},
    )
    db.close()


# ────────────────────── ① year_offset 命中早学年数据 ──────────────────────


def test_year_offset_minus_one_hits_previous_year_stats(v1_seed, time_seed, client):
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        # 锚点 = 最新学年 2026-2027（行政班经延续沿 2025-2026 的 H6）
        assert snap["academic_year_id"] == time_seed.ay_newer_id
        assert snap["class_ids"] == [v1_seed.h6_id]

        tool = _run(
            db, snap, "get_exam_stats", {"exam_name": "2025期中", "year_offset": -1}
        )
    finally:
        db.close()
    # A01：与显式传 2025-2026 学年 id 的页面端点逐字段一致
    api = client.get(
        "/api/v1/homeroom/analysis/exams/2025期中/stats",
        params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
    ).json()
    assert tool == api
    # 关键数值确为早学年数据（主三门：275/236/207）
    main3 = next(t for t in tool["totals"] if t["total_type"] == "主三门")
    assert main3["avg"] == 239.33

    # 对照（跨学年精确定位语义，随 _exam_year_id_of 更新）：不传 year
    # 参数（锚点 2026-2027）时，完整精确名「2025期中」在快照学年零候选、
    # 且全学年唯一可答 → 服务端直接定位 2025-2026，与 offset=-1 同数据
    # （响应数据正确性优先）；「offset 真正切年」仍由 get_exam_list
    # 缺省空 / -1 有数据、-2 最早学年可读拒绝共同证明。
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        default = _run(db, snap, "get_exam_stats", {"exam_name": "2025期中"})
        older = _run(
            db, snap, "get_exam_stats",
            {"exam_name": "2025期中", "year_offset": -2},
        )
        exams_default = _run(db, snap, "get_exam_list", {})
        exams_prev = _run(db, snap, "get_exam_list", {"year_offset": -1})
    finally:
        db.close()
    assert default == tool
    # 2024-2025 为最早学年：无本班行（未换届延续只向更晚学年投影）→
    # 服务端以「未配置」可读拒绝，绝不猜测/退化到其他班级
    assert older["error"] == "workspace_not_configured"
    assert [e["exam_name"] for e in exams_default["exams"]] == []
    assert any(e["exam_name"] == "2025期中" for e in exams_prev["exams"])


def test_year_offset_out_of_range_readable_error(v1_seed, time_seed):
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        result = _run(
            db, snap, "get_exam_stats", {"exam_name": "2025期中", "year_offset": -5}
        )
    finally:
        db.close()
    assert result["error"] == "invalid_scope_param"
    assert "year_offset=-5" in result["detail"]
    # 错误列出可用学年名称（模型可自我纠正）
    assert "2024-2025" in result["detail"]
    assert "2025-2026" in result["detail"]
    assert "2026-2027" in result["detail"]


def test_exact_exam_name_locates_across_years_without_year_param(
    v1_seed, time_seed
):
    """跨学年精确定位（学年行 _exam_year_id_of）：快照学年（2026-2027）
    无任何考试、唯一可答学年 = 2025-2026 时，完整精确名不带 year 参数
    直查即命中该学年（响应数据正确性优先；精确名不产生 exam_resolved
    注记）。显式指到无考试的学年仍钉定该学年走既有零命中错误；模糊名
    「期中」也绝不跨年猜——绝不擅自改指用户未指的学年。"""
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        assert snap["academic_year_id"] == time_seed.ay_newer_id
        hit = _run(db, snap, "get_exam_stats", {"exam_name": "2025期中"})
        by_id = _run(
            db, snap, "get_exam_stats",
            {"exam_name": "2025期中", "academic_year_id": v1_seed.ay_id},
        )
        pinned = _run(
            db, snap, "get_exam_stats",
            {"exam_name": "2025期中", "academic_year_id": time_seed.ay_newer_id},
        )
        fuzzy = _run(db, snap, "get_exam_stats", {"exam_name": "期中"})
    finally:
        db.close()
    # 跨学年唯一定位：与显式传 2025-2026 学年 id 完全同数据，无注记
    assert "error" not in hit
    assert "exam_resolved" not in hit
    assert hit == by_id
    main3 = next(t for t in hit["totals"] if t["total_type"] == "主三门")
    assert main3["avg"] == 239.33
    # 显式指快照学年（无考试）：用户自己圈定范围 → 零命中可读报错
    assert pinned["error"] == "resource_out_of_scope"
    # 模糊名仍钉快照学年做子串匹配：零命中，不跨年猜
    assert fuzzy["error"] == "resource_out_of_scope"


# ────────────────────── ② term_offset 跨学年换算 ──────────────────────


def test_term_offset_minus_one_falls_into_previous_year_term2(
    v1_seed, time_seed
):
    """非假期学期按 start_date 排平、以 is_current=1（2026学年第一学期，
    亦含 as_of）为锚：term_offset=-1 跳过假期条目「2025学年暑假」，落到
    上一学年（2025-2026）第二学期——假期不占编号。"""
    from app.api.chat_tools import _resolve_term_arg

    db = _db()
    try:
        snap = _snap(db, "homeroom")
        assert _resolve_term_arg(db, snap, {"term_offset": 0}) == time_seed.高三上
        assert _resolve_term_arg(db, snap, {"term_offset": -1}) == time_seed.高二下
        assert _resolve_term_arg(db, snap, {"term_offset": -2}) == time_seed.高二上
        assert _resolve_term_arg(db, snap, {"term_offset": -3}) == time_seed.高一下
        assert _resolve_term_arg(db, snap, {"term_offset": -4}) == time_seed.高一上
        assert _resolve_term_arg(db, snap, {"term_offset": 1}) == time_seed.高三下
        assert _resolve_term_arg(db, snap, {"term_id": time_seed.高二上}) == (
            time_seed.高二上
        )
        # 越界：可读错误并列出可用学期名称
        overflow = _resolve_term_arg(db, snap, {"term_offset": -9})
    finally:
        db.close()
    assert isinstance(overflow, dict)
    assert overflow["error"] == "invalid_scope_param"
    assert "term_offset=-9" in overflow["detail"]
    assert "2025学年第二学期" in overflow["detail"]


def test_term_offset_via_tool_get_student_notes(v1_seed, time_seed):
    """工具路径：get_student_notes 用 term_offset=-1（上一学年第二学期）
    解析成功；term 入参非法类型 → 可读错误。"""
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        ok = _run(
            db, snap,
            "get_student_notes",
            {"person_id": v1_seed.jia_h_id, "term_offset": -1},
        )
        bad = _run(
            db, snap,
            "get_student_notes",
            {"person_id": v1_seed.jia_h_id, "term_offset": "上一学期"},
        )
    finally:
        db.close()
    assert "error" not in ok
    assert ok["notes"] == []  # 种子无档案：合法空态
    assert bad["error"] == "invalid_scope_param"
    assert "term_offset 必须是整数" in bad["detail"]


# ────────────────────── ③ id 与 offset 同传 → 可读错误 ──────────────────────


def test_year_id_and_offset_both_rejected(v1_seed, time_seed):
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        result = _run(
            db, snap, "get_exam_stats",
            {
                "exam_name": "2025期中",
                "academic_year_id": time_seed.ay_old_id,
                "year_offset": -1,
            },
        )
        # academic_year_id 直传可用：显式指到有数据的 2025-2026 学年
        by_id_prev = _run(
            db, snap, "get_exam_list", {"academic_year_id": v1_seed.ay_id}
        )
        # 显式指到最早学年：与 offset -2 同样被服务端可读拒绝
        by_id_oldest = _run(
            db, snap, "get_exam_list", {"academic_year_id": time_seed.ay_old_id}
        )
    finally:
        db.close()
    assert result["error"] == "invalid_scope_param"
    assert "academic_year_id 与 year_offset 只能提供一个" in result["detail"]
    assert any(e["exam_name"] == "2025期中" for e in by_id_prev["exams"])
    assert "error" in by_id_oldest


def test_term_id_and_offset_both_rejected(v1_seed, time_seed):
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        result = _run(
            db, snap,
            "get_student_notes",
            {
                "person_id": v1_seed.jia_h_id,
                "term_id": time_seed.高二上,
                "term_offset": -1,
            },
        )
    finally:
        db.close()
    assert result["error"] == "invalid_scope_param"
    assert "term_id 与 term_offset 只能提供一个" in result["detail"]


# ────────────────────── ④ 都不传 → 快照学年（行为兼容） ──────────────────────


def test_no_year_args_defaults_to_snapshot_year(v1_seed, time_seed):
    from app.api.chat_tools import _resolve_year_arg

    db = _db()
    try:
        snap = _snap(db, "homeroom")
        assert snap["academic_year_id"] == time_seed.ay_newer_id
        # 解析层：无参 → None（调用方取快照学年）
        assert _resolve_year_arg(db, snap, {}) is None
        # 工具层：不传 / offset=0 / 显式 id 三者同果
        default = _run(db, snap, "get_exam_list", {})
        zero = _run(db, snap, "get_exam_list", {"year_offset": 0})
        explicit = _run(
            db, snap, "get_exam_list", {"academic_year_id": snap["academic_year_id"]}
        )
    finally:
        db.close()
    assert default == zero == explicit


# ────────────────────── ⑤ get_academic_years 锚点标注 ──────────────────────


def test_get_academic_years_annotations_match_db(v1_seed, time_seed, client):
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        tool = _run(db, snap, "get_academic_years", {})
    finally:
        db.close()

    assert tool["current_academic_year_id"] == time_seed.ay_newer_id
    years = tool["years"]
    assert [y["name"] for y in years] == ["2024-2025", "2025-2026", "2026-2027"]
    assert [y["year_offset"] for y in years] == [-2, -1, 0]
    assert [y["is_current"] for y in years] == [False, False, True]

    by_name = {y["name"]: y for y in years}
    # 上学年 = offset -1 = 2025-2026（id 一眼可见）
    assert by_name["2025-2026"]["year_offset"] == -1
    assert by_name["2025-2026"]["id"] == v1_seed.ay_id

    terms_flat = {
        term["name"]: term
        for year in years
        for term in year["terms"]
    }
    assert terms_flat["2026学年第一学期"]["term_offset"] == 0
    assert terms_flat["2026学年第一学期"]["is_current"] is True
    # 跨学年：-1 落到上一学年第二学期
    assert terms_flat["2025学年第二学期"]["term_offset"] == -1
    assert terms_flat["2025学年第二学期"]["is_current"] is False
    assert terms_flat["2025学年第一学期"]["term_offset"] == -2
    assert terms_flat["2024学年第二学期"]["term_offset"] == -3
    assert terms_flat["2024学年第一学期"]["term_offset"] == -4
    assert terms_flat["2026学年第二学期"]["term_offset"] == 1
    assert tool["current_term_id"] == terms_flat["2026学年第一学期"]["id"]
    # 目录无挂不上学年的孤儿学期 → unmatched_semesters 为空
    assert tool["unmatched_semesters"] == []

    # 与目录端点同源：学年 id/名称/起止一致
    api_years = client.get("/api/v1/shared/academic-years").json()["years"]
    api_by_id = {y["id"]: y for y in api_years}
    for year in years:
        row = api_by_id[year["id"]]
        assert (year["name"], year["start_date"], year["end_date"]) == (
            row["name"],
            row["start_date"],
            row["end_date"],
        )
    # 学期源 = ws_homework_semester（学期设置页 /homework/semesters 同一
    # 事实源）；P1 Term 表不再作为学期来源
    prev_semesters = client.get(
        "/api/v1/homework/semesters", params={"academic_year_id": v1_seed.ay_id}
    ).json()["semesters"]
    assert {
        (t["id"], t["name"], t["start_date"], t["end_date"])
        for t in by_name["2025-2026"]["terms"]
    } == {
        (s["id"], s["name"], s["start_date"], s["end_date"])
        for s in prev_semesters
    }


def test_teaching_session_year_offset_scoped(v1_seed, time_seed):
    """teaching 会话跨年：-1 解析 2025-2026 的同学科教学班（T6/T8 仍在
    该学年）→ E1 可见；锚点学年无教学班数据 → 空列表而非报错。"""
    db = _db()
    try:
        snap = _snap(db, "teaching", teaching_class_id=v1_seed.t6_id, subject="物理")
        prev = _run(db, snap, "get_exam_list", {"year_offset": -1})
        current = _run(db, snap, "get_exam_list", {})
    finally:
        db.close()
    assert any(e["exam_name"] == "2025期中" for e in prev["exams"])
    assert prev["exams"][0]["subjects"] == ["物理"]  # 钉任教学科，绝不变宽
    assert current["exams"] == []


# ────────────────────── ⑥ 目录用户风格学期名 + term_id 直传 ──────────────────────


def test_catalog_user_facing_names_and_term_id_direct(v1_seed, time_seed):
    """⑥ get_academic_years 目录出现用户维护风格的学期名（如「2026学年第一
    学期」），模型可按名匹配；term_id 直传 = ws_homework_semester.id（学期
    设置页同一 id 体系）可解析，get_student_notes 返回生效学期区间。"""
    from app.api.chat_tools import _resolve_term_arg

    db = _db()
    try:
        snap = _snap(db, "homeroom")
        catalog = _run(db, snap, "get_academic_years", {})
        resolved = _resolve_term_arg(db, snap, {"term_id": time_seed.高三上})
        via_tool = _run(
            db, snap, "get_student_notes",
            {"person_id": v1_seed.jia_h_id, "term_id": time_seed.高三上},
        )
    finally:
        db.close()
    names = [t["name"] for year in catalog["years"] for t in year["terms"]]
    assert "2026学年第一学期" in names
    assert "2025学年第二学期" in names
    assert resolved == time_seed.高三上
    assert "error" not in via_tool
    assert via_tool["term"]["id"] == time_seed.高三上
    assert via_tool["term"]["name"] == "2026学年第一学期"
    assert via_tool["term"]["start_date"] == "2026-09-01"
    assert via_tool["term"]["end_date"] == "2027-01-31"
    # term_id 指向不存在的学期 → 可读错误并列出可用学期名
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        missing = _run(
            db, snap, "get_student_notes",
            {"person_id": v1_seed.jia_h_id, "term_id": 987654321},
        )
    finally:
        db.close()
    assert missing["error"] == "invalid_scope_param"
    assert "term_id=987654321 不存在" in missing["detail"]
    assert "2026学年第一学期" in missing["detail"]


# ────────────────────── ⑦ 档案按学期起止日期过滤 ──────────────────────


def test_get_student_notes_filters_by_semester_dates(v1_seed, time_seed):
    """⑦ get_student_notes 学期参数按 ws_homework_semester 起止日期过滤档案
    date（学期内留、学期外去；不传学期参数=不过滤，与既有行为兼容）。
    service list_notes 的 P1 term_id 真实无数据，不再透传；term_id 直传
    假期条目（term_offset 到不了）可查假期区间档案。"""
    from app.db import workspace_models as wm

    db = _db()
    try:
        db.add_all(
            [
                wm.WsStudentNote(
                    data_domain="homeroom",
                    person_id=v1_seed.jia_h_id,
                    date=date(2026, 10, 5),
                    category="谈话",
                    content="本学期谈话",
                ),
                wm.WsStudentNote(
                    data_domain="homeroom",
                    person_id=v1_seed.jia_h_id,
                    date=date(2026, 3, 18),
                    category="谈话",
                    content="上学期谈话",
                ),
                wm.WsStudentNote(
                    data_domain="homeroom",
                    person_id=v1_seed.jia_h_id,
                    date=date(2026, 8, 20),
                    category="观察",
                    content="暑假观察",
                ),
                wm.WsStudentNote(
                    data_domain="homeroom",
                    person_id=v1_seed.jia_h_id,
                    date=date(2026, 7, 20),  # 暑假期内、高二下学期（~07-15）外
                    category="观察",
                    content="暑假补账",
                ),
            ]
        )
        db.commit()
        snap = _snap(db, "homeroom")
        current = _run(
            db, snap, "get_student_notes",
            {"person_id": v1_seed.jia_h_id, "term_offset": 0},
        )
        vacation = _run(
            db, snap, "get_student_notes",
            {"person_id": v1_seed.jia_h_id, "term_id": time_seed.暑假},
        )
        previous = _run(
            db, snap, "get_student_notes",
            {"person_id": v1_seed.jia_h_id, "term_id": time_seed.高二下},
        )
        unfiltered = _run(
            db, snap, "get_student_notes", {"person_id": v1_seed.jia_h_id}
        )
    finally:
        db.query(wm.WsStudentNote).filter(
            wm.WsStudentNote.person_id == v1_seed.jia_h_id
        ).delete(synchronize_session=False)
        db.commit()
        db.close()
    # 学期偏移 0 = is_current 的 2026学年第一学期：只留学期内 note
    assert [n["content"] for n in current["notes"]] == ["本学期谈话"]
    assert current["term"]["name"] == "2026学年第一学期"
    assert current["term"]["vacation"] is False
    # term_id 直传假期条目：只留假期区间内 note（偏移到不了，直传可达）
    assert [n["content"] for n in vacation["notes"]] == ["暑假观察", "暑假补账"]
    assert vacation["term"]["name"] == "2025学年暑假"
    assert vacation["term"]["vacation"] is True
    # term_id 直传上一学年第二学期：只留该学期内 note
    assert [n["content"] for n in previous["notes"]] == ["上学期谈话"]
    assert previous["term"]["name"] == "2025学年第二学期"
    # 不传学期参数：全部保留且无 term 键（既有行为兼容；service 按日期降序）
    assert [n["content"] for n in unfiltered["notes"]] == [
        "本学期谈话",
        "暑假观察",
        "暑假补账",
        "上学期谈话",
    ]
    assert "term" not in unfiltered


# ────────────────────── ⑨ 假期条目不占偏移编号 ──────────────────────


def test_vacation_term_skipped_in_offset_and_flagged(v1_seed, time_seed):
    """⑨ 假期条目（暑假/寒假/假期）不参与「上/下学期」偏移换算：
    term_offset=-1 从 2026学年第一学期跳过「2025学年暑假」落到
    2025学年第二学期；目录中假期条目 vacation=True 且 term_offset=null
    （不占编号）、非假期编号连续；term_id 直传仍可直指假期条目。"""
    from app.api.chat_tools import _resolve_term_arg

    db = _db()
    try:
        snap = _snap(db, "homeroom")
        offset_zero = _resolve_term_arg(db, snap, {"term_offset": 0})
        offset_minus1 = _resolve_term_arg(db, snap, {"term_offset": -1})
        offset_plus1 = _resolve_term_arg(db, snap, {"term_offset": 1})
        vacation_direct = _resolve_term_arg(db, snap, {"term_id": time_seed.暑假})
        catalog = _run(db, snap, "get_academic_years", {})
    finally:
        db.close()

    # ① 偏移跳过假期：-1 落到 2025学年第二学期，而非暑假作业真空区间
    assert offset_zero == time_seed.高三上
    assert offset_minus1 == time_seed.高二下
    assert offset_plus1 == time_seed.高三下
    # term_id 直传不受影响：可直指假期条目查假期数据
    assert vacation_direct == time_seed.暑假

    # ② 目录：假期条目仍列出，vacation=True 且 term_offset=null（不占编号）
    by_name = {
        term["name"]: term
        for year in catalog["years"]
        for term in year["terms"]
    }
    vacation = by_name["2025学年暑假"]
    assert vacation["vacation"] is True
    assert vacation["term_offset"] is None
    assert vacation["is_current"] is False
    assert vacation["start_date"] == "2026-07-01"
    assert vacation["end_date"] == "2026-08-31"
    # 非假期条目 vacation=False、编号连续无空洞
    assert by_name["2024学年第一学期"]["term_offset"] == -4
    assert by_name["2024学年第二学期"]["term_offset"] == -3
    assert by_name["2025学年第一学期"]["term_offset"] == -2
    assert by_name["2025学年第二学期"]["term_offset"] == -1
    assert by_name["2026学年第一学期"]["term_offset"] == 0
    assert by_name["2026学年第一学期"]["is_current"] is True
    assert by_name["2026学年第二学期"]["term_offset"] == 1
    assert all(
        term["vacation"] is False
        for name, term in by_name.items()
        if name != "2025学年暑假"
    )


# ────────────────────── ⑧ 孤儿作业学期 → unmatched_semesters ──────────────────────


def test_unmatched_semesters_surfaced_not_dropped(v1_seed, time_seed):
    """⑧ 作业学期挂不存在学年（academic_year_id 无对应学年行）时，
    get_academic_years 顶层 unmatched_semesters 收录该学期：不崩、绝不
    静默丢弃；is_current 锚点与正常 term_offset 解析不受孤儿行影响。"""
    import os
    import sqlite3

    from app.paths import DATA_DIR

    db_path = os.path.join(DATA_DIR, "db.sqlite")
    # FK 拦不住造「挂不存在学年」的孤儿行：sqlite3 直连默认关外键，
    # 模拟真实部署可能出现的目录对不上学期（迁移/历史库遗留）
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO ws_homework_semester"
            " (academic_year_id, name, start_date, end_date, is_current, mode)"
            " VALUES (999999, '幽灵学期', '2030-09-01', '2031-01-31', 0, 'manual')"
        )
        conn.commit()
    finally:
        conn.close()

    db = _db()
    try:
        snap = _snap(db, "homeroom")
        catalog = _run(db, snap, "get_academic_years", {})
        anchor_check = _run(
            db, snap, "get_student_notes",
            {"person_id": v1_seed.jia_h_id, "term_offset": 0},
        )
    finally:
        db.close()
    # 先清理孤儿行（无论断言成败），避免污染同模块其他用例
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "DELETE FROM ws_homework_semester WHERE academic_year_id = 999999"
        )
        conn.commit()
    finally:
        conn.close()

    unmatched = catalog["unmatched_semesters"]
    assert [s["name"] for s in unmatched] == ["幽灵学期"]
    assert unmatched[0]["academic_year_id"] == 999999
    assert unmatched[0]["start_date"] == "2030-09-01"
    assert all(
        s["id"] not in {t["id"] for y in catalog["years"] for t in y["terms"]}
        for s in unmatched
    )
    # 锚点仍钉 is_current 学期，正常学期偏移不受孤儿行影响
    assert anchor_check["term"]["name"] == "2026学年第一学期"
    assert catalog["current_term_id"] == time_seed.高三上
    assert catalog["current_term_id"] != unmatched[0]["id"]
