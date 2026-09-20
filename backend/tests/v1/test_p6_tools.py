"""P6 只读工具注册表测试（契约 docs/contracts/p6-ai-mcp.md §2/§0.1；A01）。

- 注册表 = 契约 24 工具（既有 9 + 新增 15）；domains 声明与域投影
  （teaching 会话不含班主任专属工具；homeroom 会话不含教学班对比工具）
- 未知 / 越权 / 越界（person/学科）→ 模型可读错误文本，不抛栈
- A01：工具 vs /api/v1 端点同源同果（关键数值断言，同一 service 层），
  新增 15 工具逐个纳入
- 只读证明：执行全部工具前后业务表行数不变
- 单班 teaching 会话调用全部工具，任何输出不含 T8 学生与 T8 班统计；
  并集会话两班可见；与页面端点逐字段一致
"""

import json

import pytest

EXAM_E1 = "2025期中"

EXPECTED_TOOLS = {
    # 既有 9 工具
    "search_students",
    "get_student_profile",
    "get_exam_list",
    "get_exam_stats",
    "get_scores_table",
    "get_homework_summary",
    "get_homework_student",
    "get_homework_correlation",
    "get_class_comparison",
    # 新增 15 工具（跨学年时间语义批次）
    "get_class_averages",
    "get_weekly_focus",
    "get_exam_focus",
    "get_student_trends",
    "get_rank_metrics",
    "get_rank_frequency",
    "get_rank_range",
    "get_rank_distribution",
    "get_score_bands",
    "get_homework_warnings",
    "get_homework_assignments",
    "get_homework_assignment_detail",
    "get_student_notes",
    "get_academic_years",
    "get_exam_students",
}

# 班主任专属工具（teaching 会话不可见）
HOMEROOM_ONLY_TOOLS = {
    "get_class_averages",
    "get_weekly_focus",
    "get_exam_focus",
    "get_student_trends",
    "get_rank_metrics",
    "get_rank_frequency",
    "get_rank_range",
    "get_rank_distribution",
    "get_score_bands",
    "get_exam_students",
}


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _snap(db, mode, **kw):
    from app.api.chat_tools import resolve_scope_snapshot

    return resolve_scope_snapshot(db, 1, mode, **kw)


def _run(db, snapshot, name, args=None):
    from app.api.chat_tools import execute_session_tool

    return execute_session_tool(db, snapshot, name, args or {})


# ────────────────────── 注册表与域投影 ──────────────────────


def test_registry_matches_contract(v1_seed):
    from app.api.chat_tools import TOOL_REGISTRY, tools_for_domain

    assert {spec.name for spec in TOOL_REGISTRY} == EXPECTED_TOOLS
    assert len(TOOL_REGISTRY) == 24
    for spec in TOOL_REGISTRY:
        assert spec.domains, spec.name
        assert set(spec.domains) <= {"homeroom", "teaching"}
    by_name = {spec.name: spec for spec in TOOL_REGISTRY}
    # 教学班对比是 teaching 专属工具；班主任专属工具 domains=("homeroom",)
    assert by_name["get_class_comparison"].domains == ("teaching",)
    for name in HOMEROOM_ONLY_TOOLS:
        assert by_name[name].domains == ("homeroom",), name
    homeroom_tools = {t["name"] for t in tools_for_domain("homeroom")}
    teaching_tools = {t["name"] for t in tools_for_domain("teaching")}
    # 显式集合差（域投影关系式）：
    # teaching = 全集 − 班主任专属 {get_class_averages, get_weekly_focus,
    #   get_exam_focus, get_student_trends, get_rank_metrics,
    #   get_rank_frequency, get_rank_range, get_rank_distribution,
    #   get_score_bands, get_exam_students}
    # homeroom = teaching − {get_class_comparison}（教学班对比工具唯一
    #   teaching 专属）
    assert teaching_tools == EXPECTED_TOOLS - HOMEROOM_ONLY_TOOLS
    assert homeroom_tools == EXPECTED_TOOLS - {"get_class_comparison"}
    assert "get_class_comparison" in teaching_tools
    assert "get_class_comparison" not in homeroom_tools
    # 新增班主任专属工具确实不在 teaching 会话出现
    assert not (HOMEROOM_ONLY_TOOLS & teaching_tools)
    assert HOMEROOM_ONLY_TOOLS <= homeroom_tools


# ────────────────────── 越权 / 越界 / 未知工具 ──────────────────────


def test_unknown_tool_rejected_text(v1_seed):
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        result = _run(db, snap, "write_scores", {"person_id": 1})
        assert result["error"] == "tool_not_in_scope"
        assert "不在当前会话" in result["detail"]
    finally:
        db.close()


def test_homeroom_session_cannot_call_teaching_only_tool(v1_seed):
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        result = _run(db, snap, "get_class_comparison", {"exam_name": EXAM_E1})
        assert result["error"] == "tool_not_in_scope"
        assert "不在当前会话" in result["detail"]
    finally:
        db.close()


def test_teaching_session_rejects_non_teaching_subject(v1_seed):
    """teaching 会话请求全科/总分口径（他学科成绩表）→ 拒绝文本。"""
    db = _db()
    try:
        snap = _snap(db, "teaching")
        result = _run(db, snap, "get_scores_table", {"exam_name": EXAM_E1, "subject": "语文"})
        assert result["error"] == "resource_out_of_scope"
        assert "不在当前会话范围" in result["detail"]
    finally:
        db.close()


def test_out_of_scope_person_rejected_text(v1_seed):
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        # 戊只在教学域（T8），不在 homeroom 会话成员集合
        profile = _run(db, snap, "get_student_profile", {"person_id": v1_seed.wu_t_id})
        assert profile["error"] == "resource_out_of_scope"
        assert "该学生不在当前会话范围" in profile["detail"]
        homework = _run(db, snap, "get_homework_student", {"person_id": v1_seed.wu_t_id})
        assert "该学生不在当前会话范围" in homework["detail"]
        # teaching 会话越界到行政班-only 学生（丙）
        snap_t = _snap(db, "teaching")
        result = _run(db, snap_t, "get_student_profile", {"person_id": v1_seed.bing_h_id})
        assert "该学生不在当前会话范围" in result["detail"]
    finally:
        db.close()


# ────────────────────── 域语义 smoke ──────────────────────


def test_search_students_domain_semantics(v1_seed):
    db = _db()
    try:
        snap_h = _snap(db, "homeroom")
        result = _run(db, snap_h, "search_students", {"q": "秦甲"})
        names = [item["name"] for item in result["students"]]
        # 教学域同名「秦甲」（戊）不可见于 homeroom 会话
        assert names == ["秦甲"]
        assert result["cohort_size"] == 3
        empty = _run(db, snap_h, "search_students", {"q": "丁"})
        assert empty["students"] == []
    finally:
        db.close()


def test_exam_list_domain_semantics(v1_seed):
    db = _db()
    try:
        exams_h = _run(db, _snap(db, "homeroom"), "get_exam_list")
        e1 = next(e for e in exams_h["exams"] if e["exam_name"] == EXAM_E1)
        assert set(e1["subjects"]) == {"语文", "数学", "英语", "物理"}
        exams_t = _run(db, _snap(db, "teaching"), "get_exam_list")
        e1t = next(e for e in exams_t["exams"] if e["exam_name"] == EXAM_E1)
        assert e1t["subjects"] == ["物理"]  # 教学侧仅任教学科
    finally:
        db.close()


def test_profile_domain_semantics(v1_seed):
    db = _db()
    try:
        # homeroom 画像：全科 + 总分；甲的物理取 H 域本域值 90（T 值 91
        # 仅作冲突提示，绝不替换）
        profile = _run(db, _snap(db, "homeroom"), "get_student_profile", {"person_id": v1_seed.jia_h_id})
        subjects = {g["subject"]: g["exams"] for g in profile["subjects"]}
        physics = [e for e in subjects["物理"] if e["exam_name"] == EXAM_E1]
        assert physics[0]["score"] == 90.0
        assert physics[0]["source_domain"] == "homeroom"
        totals = {g["total_type"]: g["exams"] for g in profile["totals"]}
        assert totals["主三门"][0]["score"] == 275.0
        # teaching 画像：仅任教学科、无 totals 键
        profile_t = _run(db, _snap(db, "teaching"), "get_student_profile", {"person_id": v1_seed.jia_t_id})
        assert "totals" not in profile_t
        assert {g["subject"] for g in profile_t["subjects"]} == {"物理"}
    finally:
        db.close()


def test_homework_summary_basic(v1_seed):
    db = _db()
    try:
        result = _run(
            db,
            _snap(db, "homeroom"),
            "get_homework_summary",
            {"from": "2025-01-01", "to": "2026-12-31"},
        )
        assert result["range"] == {"from": "2025-01-01", "to": "2026-12-31"}
        assert result["groups"] == []  # 种子无作业数据：合法空态
    finally:
        db.close()


# ────────────────────── A01：工具 vs /api/v1 端点同源同果 ──────────────────────


def test_a01_exam_stats_matches_homeroom_endpoint(v1_seed, client):
    db = _db()
    try:
        tool = _run(db, _snap(db, "homeroom"), "get_exam_stats", {"exam_name": EXAM_E1})
    finally:
        db.close()
    resp = client.get(f"/api/v1/homeroom/analysis/exams/{EXAM_E1}/stats")
    assert resp.status_code == 200, resp.text
    api = resp.json()
    assert tool["subjects"] == api["subjects"]  # 全科均分/最值/人数逐项一致
    assert tool["totals"] == api["totals"]  # 总分口径一致
    assert tool["cohort_size"] == api["cohort_size"]
    assert tool["small_sample"] == api["small_sample"]


def test_a01_exam_stats_matches_teaching_endpoint(v1_seed, client):
    db = _db()
    try:
        tool = _run(db, _snap(db, "teaching"), "get_exam_stats", {"exam_name": EXAM_E1})
    finally:
        db.close()
    api = client.get(f"/api/v1/teaching/analysis/exams/{EXAM_E1}/stats").json()
    assert tool["subject"] == api["subject"] == "物理"
    assert tool["avg"] == api["avg"]
    assert tool["valid_count"] == api["valid_count"]
    assert tool["missing_count"] == api["missing_count"]
    assert tool["cohort_size"] == api["cohort_size"]


def test_a01_scores_table_matches_scores_endpoint(v1_seed, client):
    db = _db()
    try:
        tool = _run(db, _snap(db, "homeroom"), "get_scores_table", {"exam_name": EXAM_E1})
    finally:
        db.close()
    api = client.get(
        "/api/v1/scores", params={"mode": "homeroom", "exam_name": EXAM_E1}
    ).json()
    assert tool["rows"] == api["rows"]  # 逐人逐科分数（含冲突注记）完全一致


def test_a01_class_comparison_matches_teaching_endpoint(v1_seed, client):
    db = _db()
    try:
        tool = _run(db, _snap(db, "teaching"), "get_class_comparison", {"exam_name": EXAM_E1})
    finally:
        db.close()
    api = client.get(f"/api/v1/teaching/analysis/class-compare", params={"exam_name": EXAM_E1}).json()
    assert tool["classes"] == api["classes"]
    assert tool["small_sample"] == api["small_sample"]


# ────────────────────── A01：新增 15 工具 vs /api/v1 端点同源同果 ──────────────────────


def test_a01_class_averages_matches_endpoint(v1_seed, client):
    db = _db()
    try:
        tool = _run(
            db, _snap(db, "homeroom"), "get_class_averages", {"exam_name": EXAM_E1}
        )
    finally:
        db.close()
    api = client.get(
        f"/api/v1/homeroom/analysis/exams/{EXAM_E1}/class-averages"
    ).json()
    assert tool == api  # 种子无班级均分表：双方同为空态（rows=[]），逐字段一致
    assert tool["rows"] == []


def test_a01_weekly_focus_matches_endpoint(v1_seed, client):
    db = _db()
    try:
        tool = _run(db, _snap(db, "homeroom"), "get_weekly_focus", {})
    finally:
        db.close()
    api = client.get("/api/v1/homeroom/analysis/weekly-focus").json()
    assert tool == api


def test_a01_exam_focus_matches_endpoint(v1_seed, client):
    db = _db()
    try:
        tool = _run(
            db, _snap(db, "homeroom"), "get_exam_focus", {"exam_name": EXAM_E1}
        )
    finally:
        db.close()
    api = client.get(f"/api/v1/homeroom/analysis/exams/{EXAM_E1}/focus").json()
    assert tool == api


def test_a01_student_trends_matches_endpoint(v1_seed, client):
    db = _db()
    try:
        tool = _run(
            db,
            _snap(db, "homeroom"),
            "get_student_trends",
            {"person_id": v1_seed.jia_h_id},
        )
    finally:
        db.close()
    api = client.get(
        "/api/v1/homeroom/analysis/trends",
        params={"person_id": v1_seed.jia_h_id, "class_id": v1_seed.h6_id},
    ).json()
    assert tool == api
    # 关键数值：跨学年分段结构含 2025-2026 学年的主三门/全科点
    assert tool["name"] == "秦甲"
    assert tool["years"][0]["totals"]["主三门"][0]["score"] == 275.0


def test_a01_rank_metrics_matches_endpoint(v1_seed, client):
    db = _db()
    try:
        tool = _run(db, _snap(db, "homeroom"), "get_rank_metrics", {})
    finally:
        db.close()
    api = client.get(
        "/api/v1/homeroom/analysis/rank-metrics", params={"mode": "frequency"}
    ).json()
    assert tool == api
    metric_values = {m["value"] for m in tool["metrics"]}
    assert "total:主三门" in metric_values  # 高二 frequency 模式含主三门名次


def test_a01_rank_frequency_matches_endpoint(v1_seed, client):
    db = _db()
    try:
        tool = _run(
            db,
            _snap(db, "homeroom"),
            "get_rank_frequency",
            {"metric": "total:主三门", "exam_names": EXAM_E1},
        )
    finally:
        db.close()
    api = client.get(
        "/api/v1/homeroom/analysis/rank-frequency",
        params={"metric": "total:主三门", "exam_names": EXAM_E1},
    ).json()
    assert tool == api  # 种子无名次事实：合法空态且双方逐字段一致
    assert tool["students"] == []


def test_a01_rank_range_matches_endpoint(v1_seed, client):
    db = _db()
    try:
        tool = _run(
            db,
            _snap(db, "homeroom"),
            "get_rank_range",
            {"exam_name": EXAM_E1, "metric": "total:主三门"},
        )
    finally:
        db.close()
    api = client.get(
        f"/api/v1/homeroom/analysis/exams/{EXAM_E1}/rank-range",
        params={"metric": "total:主三门"},
    ).json()
    assert tool == api


def test_a01_rank_distribution_matches_endpoint(v1_seed, client):
    db = _db()
    try:
        tool = _run(
            db,
            _snap(db, "homeroom"),
            "get_rank_distribution",
            {"exam_name": EXAM_E1},
        )
    finally:
        db.close()
    api = client.get(
        f"/api/v1/homeroom/analysis/exams/{EXAM_E1}/rank-distribution"
    ).json()
    assert tool == api


def test_a01_score_bands_error_same_as_endpoint(v1_seed, client):
    """种子无名次事实 → 段位不可计算（409）：工具与端点同错误码同文案，
    绝不把名次阈值当分数比较。"""
    db = _db()
    try:
        tool = _run(
            db,
            _snap(db, "homeroom"),
            "get_score_bands",
            {"exam_name": EXAM_E1, "subject": "主三门"},
        )
    finally:
        db.close()
    resp = client.get(
        "/api/v1/homeroom/analysis/bands",
        params={"exam_name": EXAM_E1, "subject": "主三门", "metric": "total"},
    )
    assert resp.status_code == 409
    assert tool == resp.json()


def test_a01_homework_warnings_matches_endpoint(v1_seed, client):
    db = _db()
    try:
        tool = _run(db, _snap(db, "homeroom"), "get_homework_warnings", {})
    finally:
        db.close()
    api = client.get(
        "/api/v1/homework/warnings",
        params={
            "mode": "homeroom",
            "class_id": v1_seed.h6_id,
            "academic_year_id": v1_seed.ay_id,
        },
    ).json()
    assert tool == api  # 种子无作业：合法空态
    assert tool["students"] == []


def test_a01_homework_assignments_matches_endpoint(v1_seed, client):
    db = _db()
    try:
        tool = _run(db, _snap(db, "homeroom"), "get_homework_assignments", {})
    finally:
        db.close()
    api = client.get(
        "/api/v1/homework/assignments",
        params={
            "mode": "homeroom",
            "class_id": v1_seed.h6_id,
            "academic_year_id": v1_seed.ay_id,
        },
    ).json()
    assert tool == api
    assert tool["total"] == 0


def test_a01_student_notes_matches_endpoint(v1_seed, client):
    db = _db()
    try:
        tool = _run(
            db,
            _snap(db, "homeroom"),
            "get_student_notes",
            {"person_id": v1_seed.jia_h_id},
        )
    finally:
        db.close()
    api = client.get(
        f"/api/v1/homeroom/students/{v1_seed.jia_h_id}/notes",
        params={"class_id": v1_seed.h6_id, "academic_year_id": v1_seed.ay_id},
    ).json()
    assert tool == api
    assert tool["notes"] == []


def test_a01_academic_years_matches_directory(v1_seed, client):
    """学年/学期目录：学年与 /shared/academic-years 同源、学期与
    /homework/semesters（ws_homework_semester，学期设置页同一事实源）同源，
    并带 is_current / year_offset / term_offset 标注（时间语义锚点）。"""
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        tool = _run(db, snap, "get_academic_years", {})
    finally:
        db.close()
    years = client.get("/api/v1/shared/academic-years").json()["years"]
    assert tool["current_academic_year_id"] == v1_seed.ay_id
    assert [y["id"] for y in tool["years"]] == sorted(
        y["id"] for y in [y for y in years]
    )
    by_id = {y["id"]: y for y in tool["years"]}
    for year in years:
        item = by_id[year["id"]]
        assert (item["name"], item["start_date"], item["end_date"]) == (
            year["name"],
            year["start_date"],
            year["end_date"],
        )
        semesters_api = client.get(
            "/api/v1/homework/semesters", params={"academic_year_id": year["id"]}
        ).json()["semesters"]
        # 学期源 = ws_homework_semester；该学年无落库学期时端点回退推导
        # 条目（id=null），目录只收真实作业学期行
        assert [t["id"] for t in item["terms"]] == [
            s["id"] for s in semesters_api if s["id"] is not None
        ]
    current = by_id[v1_seed.ay_id]
    assert current["is_current"] is True
    assert current["year_offset"] == 0
    assert [y["year_offset"] for y in tool["years"]] == sorted(
        y["year_offset"] for y in tool["years"]
    )


def test_a01_exam_students_matches_endpoint(v1_seed, client):
    db = _db()
    try:
        tool = _run(
            db, _snap(db, "homeroom"), "get_exam_students", {"exam_name": EXAM_E1}
        )
    finally:
        db.close()
    api = client.get(
        f"/api/v1/homeroom/analysis/exams/{EXAM_E1}/students"
    ).json()
    assert tool == api
    # 关键数值：甲的宽表行（全科 + 主三门总分）
    jia = next(s for s in tool["students"] if s["person_id"] == v1_seed.jia_h_id)
    assert jia["scores"]["物理"] == 90.0
    assert jia["totals"]["主三门"] == 275.0
    # subject 收窄视图：只保留该科（不含总分键）
    db = _db()
    try:
        narrowed = _run(
            db,
            _snap(db, "homeroom"),
            "get_exam_students",
            {"exam_name": EXAM_E1, "subject": "语文"},
        )
    finally:
        db.close()
    jia_narrowed = next(
        s for s in narrowed["students"] if s["person_id"] == v1_seed.jia_h_id
    )
    assert set(jia_narrowed["scores"].keys()) == {"语文"}
    assert jia_narrowed["scores"]["语文"] == 88.0


def test_a01_homework_assignment_detail_matches_endpoint(v1_seed, client, db_session):
    """作业批次逐人明细：经 preview/confirm 造一个 H6 物理批次后，工具与
    端点逐字段一致；单班 T6 teaching 会话查该 H6 批次 → 越界文本。"""
    p = client.post(
        "/api/v1/homework/preview",
        json={
            "mode": "homeroom",
            "class_id": v1_seed.h6_id,
            "academic_year_id": v1_seed.ay_id,
            "subject": "物理",
            "homework_type": "工具明细测",
            "assigned_date": "2025-10-10",
            "input": {
                "kind": "detailed",
                "rows": [
                    {"name_or_alias": "秦甲", "status": "submitted"},
                    {"name_or_alias": "秦乙", "status": "missing"},
                    {"name_or_alias": "秦丙", "status": "excused"},
                ],
            },
        },
    )
    assert p.status_code == 200, p.text
    confirmed = client.post(
        "/api/v1/homework/confirm", json={"token": p.json()["token"]}
    )
    assert confirmed.status_code == 200, confirmed.text
    assignment_id = confirmed.json()["assignment_id"]

    db = _db()
    try:
        tool = _run(
            db,
            _snap(db, "homeroom"),
            "get_homework_assignment_detail",
            {"assignment_id": assignment_id},
        )
        # 越界：单班 T6 teaching 会话不可读 H6 批次
        snap_t6 = _snap(db, "teaching", teaching_class_id=v1_seed.t6_id, subject="物理")
        leak = _run(
            db, snap_t6, "get_homework_assignment_detail", {"assignment_id": assignment_id}
        )
        # 越界 person：档案工具同样拦截
        notes_leak = _run(
            db,
            snap_t6,
            "get_student_notes",
            {"person_id": v1_seed.jia_h_id},  # 甲只挂 H6，不在 T6 成员集合
        )
    finally:
        db.close()
    api = client.get(
        f"/api/v1/homework/assignments/{assignment_id}",
        params={
            "mode": "homeroom",
            "class_id": v1_seed.h6_id,
            "academic_year_id": v1_seed.ay_id,
        },
    ).json()
    assert tool == api
    assert tool["submitted"] == 1 and tool["missing"] == 1 and tool["excused"] == 1
    assert "不在当前会话范围" in leak["detail"]
    assert "该学生不在当前会话范围" in notes_leak["detail"]


# ────────────────────── Q01：单班会话绝不扩大成并集 ──────────────────────


def test_q01_single_class_session_all_tools_scoped(v1_seed):
    """T6 单班 teaching 会话：9 工具全调用，任何输出不含 T8 学生与 T8
    班统计（聚合前限制班级，而非取回后裁剪）。"""
    db = _db()
    try:
        snap = _snap(
            db, "teaching", teaching_class_id=v1_seed.t6_id, subject="物理"
        )
        assert sorted(snap["class_ids"]) == [v1_seed.t6_id]
        forbidden = {v1_seed.wu_t_id, v1_seed.ji_t_id}

        found = _run(db, snap, "search_students", {"q": ""})
        assert sorted(s["person_id"] for s in found["students"]) == sorted(
            v1_seed.t6_person_ids
        )

        profile_ok = _run(db, snap, "get_student_profile", {"person_id": v1_seed.jia_t_id})
        assert profile_ok["person"]["person_id"] == v1_seed.jia_t_id
        profile_leak = _run(db, snap, "get_student_profile", {"person_id": v1_seed.wu_t_id})
        assert "该学生不在当前会话范围" in profile_leak["detail"]

        exams = _run(db, snap, "get_exam_list")
        assert any(e["exam_name"] == EXAM_E1 for e in exams["exams"])

        stats = _run(db, snap, "get_exam_stats", {"exam_name": EXAM_E1})
        assert stats["cohort_size"] == 3  # 不是并集的 5
        assert stats["valid_count"] == 2  # 甲91/乙85，丁缺考

        table = _run(db, snap, "get_scores_table", {"exam_name": EXAM_E1})
        row_ids = {r["person_id"] for r in table["rows"]}
        assert row_ids == set(v1_seed.t6_person_ids)
        assert not (row_ids & forbidden)
        assert table["metadata"]["cohort_size"] == 3

        summary = _run(db, snap, "get_homework_summary", {"from": "2025-01-01", "to": "2026-12-31"})
        assert summary["groups"] == []  # 种子无作业：合法空态且无越界行

        hw_ok = _run(db, snap, "get_homework_student", {"person_id": v1_seed.ding_t_id})
        assert hw_ok["person_id"] == v1_seed.ding_t_id
        hw_leak = _run(db, snap, "get_homework_student", {"person_id": v1_seed.ji_t_id})
        assert "该学生不在当前会话范围" in hw_leak["detail"]

        # 种子无作业：correlation 在单班会话合法空态（r=null 绝不编造），
        # 不传 subject 也钉住任教学科物理
        corr = _run(db, snap, "get_homework_correlation", {"exam_name": EXAM_E1})
        assert corr["n"] == 0
        assert corr["r"] is None
        assert corr["pairs"] == []
        assert any("不可计算" in c for c in corr["caveats"])

        compare = _run(db, snap, "get_class_comparison", {"exam_name": EXAM_E1})
        assert [c["teaching_class_id"] for c in compare["classes"]] == [v1_seed.t6_id]
    finally:
        db.close()


def test_q01_single_class_new_dual_tools_scoped(v1_seed):
    """新增双域工具同样受单班范围约束：T6 会话拿不到 H6 的作业/档案数据。"""
    db = _db()
    try:
        snap = _snap(db, "teaching", teaching_class_id=v1_seed.t6_id, subject="物理")
        warnings = _run(db, snap, "get_homework_warnings", {})
        assert warnings["students"] == []  # 共享范围未含作业 → 无投影预警行
        assignments = _run(db, snap, "get_homework_assignments", {})
        assert assignments["total"] == 0
        notes_ok = _run(db, snap, "get_student_notes", {"person_id": v1_seed.jia_t_id})
        assert notes_ok["notes"] == []
        years = _run(db, snap, "get_academic_years", {})
        assert years["current_academic_year_id"] == snap["academic_year_id"]
        assert years["current_term_id"] is None  # 本模块种子无学期
    finally:
        db.close()


def test_q01_single_class_matches_page_endpoints(v1_seed, client):
    """Q01 + A01 重跑：单班会话工具输出与显式同范围的页面端点逐字段一致。"""
    db = _db()
    try:
        snap = _snap(db, "teaching", teaching_class_id=v1_seed.t6_id, subject="物理")
        stats = _run(db, snap, "get_exam_stats", {"exam_name": EXAM_E1})
        compare = _run(db, snap, "get_class_comparison", {"exam_name": EXAM_E1})
        table = _run(db, snap, "get_scores_table", {"exam_name": EXAM_E1})
    finally:
        db.close()

    api_stats = client.get(
        f"/api/v1/teaching/analysis/exams/{EXAM_E1}/stats",
        params={"teaching_class_id": v1_seed.t6_id},
    ).json()
    assert stats == api_stats  # 全字段（含 metadata）逐项一致

    union_compare = client.get(
        "/api/v1/teaching/analysis/class-compare", params={"exam_name": EXAM_E1}
    ).json()
    assert compare["classes"] == [
        c for c in union_compare["classes"] if c["teaching_class_id"] == v1_seed.t6_id
    ]

    union_table = client.get(
        "/api/v1/scores", params={"mode": "teaching", "exam_name": EXAM_E1}
    ).json()
    assert table["rows"] == [
        r for r in union_table["rows"] if r["person_id"] in set(v1_seed.t6_person_ids)
    ]
    assert table["metadata"]["cohort_size"] == 3


def test_a01_teaching_union_matches_page_endpoints(v1_seed, client):
    """并集会话（缺省全部所教班）：工具输出与页面端点逐字段一致（Q01
    收窄不改变并集语义）。"""
    db = _db()
    try:
        snap = _snap(db, "teaching")
        table = _run(db, snap, "get_scores_table", {"exam_name": EXAM_E1})
        compare = _run(db, snap, "get_class_comparison", {"exam_name": EXAM_E1})
    finally:
        db.close()
    api_table = client.get(
        "/api/v1/scores", params={"mode": "teaching", "exam_name": EXAM_E1}
    ).json()
    assert table == api_table
    api_compare = client.get(
        "/api/v1/teaching/analysis/class-compare", params={"exam_name": EXAM_E1}
    ).json()
    assert compare == api_compare
    # 并集会话两班都可见（T6+T8）
    row_ids = {r["person_id"] for r in table["rows"]}
    assert set(v1_seed.t6_person_ids) <= row_ids
    assert set(v1_seed.t8_person_ids) <= row_ids
    assert {c["teaching_class_id"] for c in compare["classes"]} == {
        v1_seed.t6_id,
        v1_seed.t8_id,
        v1_seed.t_empty_id,
    }


# ────────────────────── 考试名模糊匹配（_resolve_exam_name 统一解析） ──────────────────────


def _seed_extra_exam_fact(
    db, ay_id, class_id, identity_id, exam_name, exam_date, subject="语文", score=80.0
):
    """直插一条 homeroom 域 ScoreFact 造一场新考试（与 v1_seed 造数同款，
    ORM 直写、key 列由 validates 同步）。返回该行供用例结束清理。"""
    from app.db.workspace_models import ScoreFact

    fact = ScoreFact(
        data_domain="homeroom",
        academic_year_id=ay_id,
        exam_name=exam_name,
        exam_date=exam_date,
        class_ref_id=class_id,
        identity_id=identity_id,
        subject=subject,
        total_type=None,
        score=score,
        source="synthetic-test",
    )
    db.add(fact)
    db.commit()
    return fact


def test_exam_name_fuzzy_unique_substring_resolved(v1_seed):
    """唯一子串命中：片段「期中」自动解析为 2025期中，响应顶层 exam_resolved
    注明全名，数值与精确调用完全一致；精确命中无注记（基线不变）。
    （精确名基线本身由既有 A01 同源用例覆盖，此处不重复断言端点等值。）"""
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        fuzzy = _run(db, snap, "get_exam_stats", {"exam_name": "期中"})
        exact = _run(db, snap, "get_exam_stats", {"exam_name": EXAM_E1})
        table = _run(db, snap, "get_scores_table", {"exam_name": "期中"})
    finally:
        db.close()
    assert fuzzy["exam_resolved"] == EXAM_E1
    assert fuzzy["subjects"] == exact["subjects"]  # 数值与精确调用一致
    assert fuzzy["totals"] == exact["totals"]
    assert fuzzy["cohort_size"] == exact["cohort_size"]
    assert "exam_resolved" not in exact  # 精确命中：无注记，既有行为不变
    assert table["exam_resolved"] == EXAM_E1  # 成绩表同链路生效
    assert len(table["rows"]) > 0


def test_exam_name_fuzzy_multiple_matches_lists_candidates(v1_seed):
    """多场子串命中 → invalid_scope_param 候选清单：含两场名字与日期、按日期
    降序、提示选一场后重试，绝不抛栈。补插考试用后清理。"""
    from datetime import date

    from app.db.workspace_models import ScoreFact

    db = _db()
    extra = None
    try:
        extra = _seed_extra_exam_fact(
            db,
            v1_seed.ay_id,
            v1_seed.h6_id,
            v1_seed.jia_h_id,
            "2025期中补充卷",
            date(2025, 11, 20),
        )
        snap = _snap(db, "homeroom")
        result = _run(db, snap, "get_exam_stats", {"exam_name": "期中"})
        exact = _run(db, snap, "get_exam_stats", {"exam_name": EXAM_E1})
    finally:
        if extra is not None:
            db.query(ScoreFact).filter(ScoreFact.id == extra.id).delete()
            db.commit()
        db.close()
    assert result["error"] == "invalid_scope_param"
    assert "2025期中补充卷" in result["detail"]
    assert EXAM_E1 in result["detail"]
    assert "请选一场" in result["detail"]
    # 按日期降序：补充卷（11-20）排在期中（11-06）之前（「2025期中（」锚定
    # 完整名单行，避免命中「2025期中补充卷」前缀）
    assert result["detail"].index("2025期中补充卷") < result["detail"].index(
        f"{EXAM_E1}（"
    )
    assert "Traceback" not in result["detail"]
    assert "exam_resolved" not in exact  # 精确名在多考试环境仍直接命中


def test_exam_name_fuzzy_zero_match_lists_available(v1_seed):
    """零命中 → 不在范围可读错误并列出该范围可用考试名（模型一步自纠）。"""
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        result = _run(db, snap, "get_exam_stats", {"exam_name": "不存在的考试"})
    finally:
        db.close()
    assert result["error"] == "resource_out_of_scope"
    assert "不在当前会话范围" in result["detail"]
    assert EXAM_E1 in result["detail"]  # 列出可用考试
    assert "Traceback" not in result["detail"]


def test_rank_frequency_exam_names_itemwise_resolution(v1_seed):
    """rank_frequency 逐项解析：一项模糊一项精确都成功时响应顶层
    exam_resolved 注明逐项解析结果；某项零命中报错并注明「第 N 项」。"""
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        ok = _run(
            db,
            snap,
            "get_rank_frequency",
            {"metric": "total:主三门", "exam_names": f"期中,{EXAM_E1}"},
        )
        bad = _run(
            db,
            snap,
            "get_rank_frequency",
            {"metric": "total:主三门", "exam_names": f"{EXAM_E1},不存在的一场"},
        )
    finally:
        db.close()
    assert ok["exam_resolved"] == f"{EXAM_E1},{EXAM_E1}"
    assert ok["students"] == []  # 种子无名次事实：合法空态
    assert bad["error"] == "resource_out_of_scope"
    assert "第 2 项" in bad["detail"]
    assert "不存在的一场" in bad["detail"]
    assert EXAM_E1 in bad["detail"]  # 错误同时列出可用考试


def test_exam_name_fuzzy_year_restricted_candidates(v1_seed):
    """候选限定所选学年：显式 academic_year_id 指到无「期中」的学年时零命中
    且可用清单只含该学年考试；指回种子学年照常唯一命中——绝不串学年。
    补插学年与考试事实用后清理。"""
    from datetime import date

    from app.db import workspace_models as wm
    from app.db.workspace_models import ScoreFact

    db = _db()
    ay_new = None
    extra = None
    try:
        ay_new = wm.AcademicYear(
            name="2026-2027",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 7, 15),
        )
        db.add(ay_new)
        db.flush()
        extra = _seed_extra_exam_fact(
            db, ay_new.id, v1_seed.h6_id, v1_seed.jia_h_id,
            "2026开学考", date(2026, 9, 10),
        )
        snap = _snap(db, "homeroom")
        other = _run(
            db,
            snap,
            "get_exam_stats",
            {"exam_name": "期中", "academic_year_id": ay_new.id},
        )
        hit = _run(
            db,
            snap,
            "get_exam_stats",
            {"exam_name": "开学", "academic_year_id": ay_new.id},
        )
        current = _run(
            db,
            snap,
            "get_exam_stats",
            {"exam_name": "期中", "academic_year_id": v1_seed.ay_id},
        )
    finally:
        if extra is not None:
            db.query(ScoreFact).filter(ScoreFact.id == extra.id).delete()
        if ay_new is not None:
            db.query(wm.AcademicYear).filter(wm.AcademicYear.id == ay_new.id).delete()
        db.commit()
        db.close()
    # 指到 2026-2027：「期中」在该学年零命中，可用清单只含该学年考试
    assert other["error"] == "resource_out_of_scope"
    assert "2026开学考" in other["detail"]
    assert EXAM_E1 not in other["detail"]
    # 「开学」在该学年唯一子串命中
    assert hit["exam_resolved"] == "2026开学考"
    # 指回 2025-2026：「期中」照常唯一命中，不串入其他学年考试
    assert current["exam_resolved"] == EXAM_E1


# ────────────────────── 只读证明 ──────────────────────


def test_tools_are_readonly_row_counts_unchanged(v1_seed):
    db = _db()
    try:
        from app.db.workspace_models import (
            ChatSession,
            HomeworkAssignment,
            HomeworkSubmission,
            ScoreFact,
        )

        def counts():
            return (
                db.query(ScoreFact).count(),
                db.query(HomeworkAssignment).count(),
                db.query(HomeworkSubmission).count(),
                db.query(ChatSession).count(),
            )

        before = counts()
        snap_h = _snap(db, "homeroom")
        snap_t = _snap(db, "teaching")
        _run(db, snap_h, "search_students", {"q": "秦"})
        _run(db, snap_h, "get_student_profile", {"person_id": v1_seed.jia_h_id})
        _run(db, snap_h, "get_exam_list")
        _run(db, snap_h, "get_exam_stats", {"exam_name": EXAM_E1})
        _run(db, snap_h, "get_scores_table", {"exam_name": EXAM_E1})
        _run(db, snap_h, "get_homework_summary", {"from": "2025-01-01", "to": "2026-12-31"})
        _run(db, snap_h, "get_homework_student", {"person_id": v1_seed.jia_h_id})
        _run(db, snap_h, "get_homework_correlation", {
            "exam_name": EXAM_E1, "subject": "物理"})
        _run(db, snap_t, "get_class_comparison", {"exam_name": EXAM_E1})
        # 新增 15 工具全部纳入只读证明
        _run(db, snap_h, "get_class_averages", {"exam_name": EXAM_E1})
        _run(db, snap_h, "get_weekly_focus", {})
        _run(db, snap_h, "get_exam_focus", {"exam_name": EXAM_E1})
        _run(db, snap_h, "get_student_trends", {"person_id": v1_seed.jia_h_id})
        _run(db, snap_h, "get_rank_metrics", {})
        _run(db, snap_h, "get_rank_frequency",
             {"metric": "total:主三门", "exam_names": EXAM_E1})
        _run(db, snap_h, "get_rank_range",
             {"exam_name": EXAM_E1, "metric": "total:主三门"})
        _run(db, snap_h, "get_rank_distribution", {"exam_name": EXAM_E1})
        _run(db, snap_h, "get_score_bands",
             {"exam_name": EXAM_E1, "subject": "主三门"})
        _run(db, snap_h, "get_homework_warnings", {})
        _run(db, snap_h, "get_homework_assignments", {})
        assignment_id = db.query(HomeworkAssignment.id).first()
        if assignment_id is not None:
            _run(db, snap_h, "get_homework_assignment_detail",
                 {"assignment_id": assignment_id[0]})
        _run(db, snap_h, "get_student_notes", {"person_id": v1_seed.jia_h_id})
        _run(db, snap_h, "get_academic_years", {})
        _run(db, snap_h, "get_exam_students", {"exam_name": EXAM_E1})
        # 双域新工具 teaching 会话同样只读
        _run(db, snap_t, "get_homework_warnings", {})
        _run(db, snap_t, "get_homework_assignments", {})
        _run(db, snap_t, "get_academic_years", {})
        db.commit()
        assert counts() == before
    finally:
        db.close()
