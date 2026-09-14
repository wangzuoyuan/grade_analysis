"""P6 只读工具注册表测试（契约 docs/contracts/p6-ai-mcp.md §2/§0.1；A01）。

- 注册表 = 契约 8 工具；domains 声明与域投影（teaching 会话不含教学班
  对比工具；homeroom 会话不含全科/总分之外的专属工具）
- 未知 / 越权 / 越界（person/学科）→ 模型可读错误文本，不抛栈
- A01：工具 vs /api/v1 端点同源同果（关键数值断言，同一 service 层）
- 只读证明：执行全部工具前后业务表行数不变
- 单班 teaching 会话调用全部 8 工具，任何输出不含
  T8 学生与 T8 班统计；并集会话两班可见；与页面端点逐字段一致
"""

import json

import pytest

EXAM_E1 = "2025期中"

EXPECTED_TOOLS = {
    "search_students",
    "get_student_profile",
    "get_exam_list",
    "get_exam_stats",
    "get_scores_table",
    "get_homework_summary",
    "get_homework_student",
    "get_class_comparison",
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
    assert len(TOOL_REGISTRY) == 8
    for spec in TOOL_REGISTRY:
        assert spec.domains, spec.name
        assert set(spec.domains) <= {"homeroom", "teaching"}
    by_name = {spec.name: spec for spec in TOOL_REGISTRY}
    # 教学班对比是 teaching 专属工具
    assert by_name["get_class_comparison"].domains == ("teaching",)
    homeroom_tools = {t["name"] for t in tools_for_domain("homeroom")}
    teaching_tools = {t["name"] for t in tools_for_domain("teaching")}
    assert "get_class_comparison" not in homeroom_tools
    assert "get_class_comparison" in teaching_tools
    # homeroom 会话没有专属工具（全科/总分由 homeroom 视角 handler 提供）
    assert homeroom_tools | {"get_class_comparison"} == teaching_tools


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


# ────────────────────── Q01：单班会话绝不扩大成并集 ──────────────────────


def test_q01_single_class_session_all_tools_scoped(v1_seed):
    """T6 单班 teaching 会话：8 工具全调用，任何输出不含 T8 学生与 T8
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

        compare = _run(db, snap, "get_class_comparison", {"exam_name": EXAM_E1})
        assert [c["teaching_class_id"] for c in compare["classes"]] == [v1_seed.t6_id]
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
        _run(db, snap_t, "get_class_comparison", {"exam_name": EXAM_E1})
        db.commit()
        assert counts() == before
    finally:
        db.close()
