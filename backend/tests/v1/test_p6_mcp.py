"""P6/P0-A2 MCP ws 工具测试（契约 docs/contracts/p6-ai-mcp.md §4/§0.1；A01）。

- mode 缺省 / 非法 → 错误结果（422 invalid_scope_param 语义，模型可读）
- 带 mode 调用 → 服务端重新解析 scope，走 chat_tools 同一注册表：
  与 /api/v1 端点同数值；会话落 ChatSession(type='mcp')
- 同源性（A01/P0-A2）：同一 scope 下 MCP 调用结果 == 应用内
  execute_session_tool 结果（teaching/homeroom 两域代表工具 + 域投影）
- P0-A2：tools/list = chat_tools 注册表全量工具（mode 必填注入；P1-B4 起含
  第 25 个 get_diagnosis_summary）；
  旧 20 工具退出主链（默认不可见不可调用，仅 MCP_LEGACY_TOOLS=1 显式
  开启时以 deprecated 兼容入口出现）
"""

import json
import os
import sys

import pytest

STRONG_TOKEN = "e" * 48  # >= 32 且非占位符
EXAM_E1 = "2025期中"

# P0-A2：MCP 默认目录 = chat_tools.TOOL_REGISTRY 全量工具（与应用内 AI
# 同一注册表，顺序即注册顺序；tests/test_mcp.py 冻结同一清单；P1-B4 起
# 含第 25 个 get_diagnosis_summary）。
EXPECTED_TOOL_ORDER = (
    "search_students",
    "get_student_profile",
    "get_exam_list",
    "get_exam_stats",
    "get_scores_table",
    "get_homework_summary",
    "get_homework_student",
    "get_homework_correlation",
    "get_class_comparison",
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
    # P1-B4 诊断摘要（第 25 个工具，契约 docs/diagnosis-roadmap/p1-contracts.md §6.3）
    "get_diagnosis_summary",
)

# 旧 MCP 工具（chat/tools.py 旧注册表）：默认退出主链
LEGACY_TOOL_ORDER = (
    "list_exams", "student_lookup", "student_identity_lookup",
    "student_exam_detail", "student_trend", "student_learning_profile",
    "class_trend", "compare_classes", "focus_list", "subject_weakness",
    "subject_progress_ranking", "multi_exam_progress_ranking", "band_trend",
    "custom_rank_band_trend", "rank_range_filter", "rank_frequency_stat",
    "student_homework_summary", "class_homework_ranking",
    "homework_grade_correlation", "student_notes",
)


def _auth_headers():
    return {
        "Authorization": "Bearer " + STRONG_TOKEN,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Host": "localhost:8000",
    }


def _post(client, body):
    return client.post("/mcp/", json=body, headers=_auth_headers())


def _call(client, name, arguments) -> dict:
    """tools/call 并返回 result 对象。"""
    resp = _post(
        client,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


def _payload(result) -> dict:
    return json.loads(result["content"][0]["text"])


def _in_app_tool_result(name, tool_args, mode):
    """应用内 AI 同一受控路径的结果（同一注册表 execute_session_tool）。"""
    from app.api import current_teacher_id
    from app.api.chat_tools import execute_session_tool, resolve_scope_snapshot
    from app.db.models import SessionLocal

    db = SessionLocal()
    try:
        teacher_id = current_teacher_id(db)
        snapshot = resolve_scope_snapshot(db, teacher_id, mode)
        return execute_session_tool(db, snapshot, name, dict(tool_args), teacher_id)
    finally:
        db.close()


@pytest.fixture(scope="module")
def mcp_client(v1_seed):
    """启用 MCP 的 fresh app（模块级：env 手工设置并在退出时恢复）。"""
    keys = ("MCP_ENABLED", "MCP_BEARER_TOKEN", "MCP_ALLOWED_HOSTS", "MCP_ALLOWED_ORIGINS")
    original = {key: os.environ.get(key) for key in keys}
    os.environ["MCP_ENABLED"] = "true"
    os.environ["MCP_BEARER_TOKEN"] = STRONG_TOKEN
    os.environ.pop("MCP_ALLOWED_HOSTS", None)
    os.environ.pop("MCP_ALLOWED_ORIGINS", None)
    for mod in list(sys.modules):
        if mod == "app.main" or mod.startswith("app.mcp_server"):
            del sys.modules[mod]
    try:
        import app.main  # noqa: F401

        from fastapi.testclient import TestClient

        with TestClient(app.main.app, base_url="http://localhost:8000") as client:
            yield client
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for mod in list(sys.modules):
            if mod == "app.main" or mod.startswith("app.mcp_server"):
                del sys.modules[mod]


# ────────────────────── mode 必传（缺省 422 语义） ──────────────────────


def test_ws_tool_without_mode_rejected_422(mcp_client):
    result = _call(mcp_client, "get_exam_stats", {"exam_name": EXAM_E1})
    assert result["isError"] is True
    payload = _payload(result)
    assert payload["error"] == "invalid_scope_param"
    assert payload["status"] == 422
    assert "mode" in payload["detail"]


def test_ws_tool_with_invalid_mode_rejected_422(mcp_client):
    result = _call(mcp_client, "search_students", {"mode": "admin", "q": "秦"})
    assert result["isError"] is True
    payload = _payload(result)
    assert payload["error"] == "invalid_scope_param"
    assert payload["status"] == 422


# ────────────────────── 带 mode：与端点同源同果 + 会话落库 ──────────────────────


def test_ws_exam_stats_homeroom_matches_api_and_lands_session(mcp_client):
    result = _call(mcp_client, "get_exam_stats", {"mode": "homeroom", "exam_name": EXAM_E1})
    assert result.get("isError") is not True
    payload = _payload(result)
    api = mcp_client.get(f"/api/v1/homeroom/analysis/exams/{EXAM_E1}/stats").json()
    assert payload["subjects"] == api["subjects"]
    assert payload["totals"] == api["totals"]
    assert payload["cohort_size"] == api["cohort_size"]

    # 契约 §4：MCP 会话同样存 chat_session（type='mcp'），结果体本身与应用
    # 内工具结果同构（无 MCP 侧注入键）；会话行按最新一条核对。
    from app.db.models import SessionLocal
    from app.db.workspace_models import ChatSession

    db = SessionLocal()
    try:
        row = (
            db.query(ChatSession)
            .filter(ChatSession.type == "mcp")
            .order_by(ChatSession.id.desc())
            .first()
        )
        assert row is not None
        assert row.status == "open"
        scope = json.loads(row.scope_json)
        assert scope["mode"] == "homeroom"
        assert scope["cohort_size"] == 3
    finally:
        db.close()


def test_ws_exam_stats_teaching_matches_api(mcp_client):
    result = _call(mcp_client, "get_exam_stats", {"mode": "teaching", "exam_name": EXAM_E1})
    payload = _payload(result)
    api = mcp_client.get(f"/api/v1/teaching/analysis/exams/{EXAM_E1}/stats").json()
    assert payload["subject"] == api["subject"] == "物理"
    assert payload["avg"] == api["avg"]
    assert payload["valid_count"] == api["valid_count"]


def test_ws_scores_table_matches_api(mcp_client):
    result = _call(mcp_client, "get_scores_table", {"mode": "homeroom", "exam_name": EXAM_E1})
    payload = _payload(result)
    api = mcp_client.get(
        "/api/v1/scores", params={"mode": "homeroom", "exam_name": EXAM_E1}
    ).json()
    assert payload["rows"] == api["rows"]


def test_ws_search_students_domain_semantics(mcp_client, v1_seed):
    result = _call(mcp_client, "search_students", {"mode": "homeroom", "q": "秦甲"})
    payload = _payload(result)
    # teaching 域同名「秦甲」（戊）不可见于 homeroom 会话
    assert [item["name"] for item in payload["students"]] == ["秦甲"]
    assert payload["cohort_size"] == 3


# ────────────────────── 同源性（A01）：MCP == 应用内工具结果 ──────────────────────


def test_mcp_equals_in_app_result_both_modes(mcp_client, v1_seed):
    """P0-A2 核心同源性：同一 scope 下 MCP 调用结果 == 应用内
    execute_session_tool 结果（homeroom/teaching 两域代表工具，含成员守卫
    正向路径与 homeroom subject 过滤参数透传）。"""
    cases = (
        ("homeroom", "search_students", {"q": "秦甲"}),
        ("homeroom", "search_students", {}),
        ("homeroom", "get_exam_list", {}),
        ("homeroom", "get_exam_stats", {"exam_name": EXAM_E1}),
        ("homeroom", "get_scores_table", {"exam_name": EXAM_E1}),
        ("homeroom", "get_scores_table", {"exam_name": EXAM_E1, "subject": "语文"}),
        ("homeroom", "get_exam_students", {"exam_name": EXAM_E1}),
        ("homeroom", "get_class_averages", {"exam_name": EXAM_E1}),
        ("homeroom", "get_student_profile", {"person_id": v1_seed.jia_h_id}),
        ("teaching", "search_students", {}),
        ("teaching", "get_exam_stats", {"exam_name": EXAM_E1}),
        ("teaching", "get_scores_table", {"exam_name": EXAM_E1}),
        ("teaching", "get_student_profile", {"person_id": v1_seed.jia_t_id}),
    )
    for req_id, (mode, name, tool_args) in enumerate(cases, start=50):
        result = _call(mcp_client, name, {"mode": mode, **tool_args})
        assert result.get("isError") is not True, (mode, name, tool_args, result)
        assert _payload(result) == _in_app_tool_result(name, tool_args, mode), (
            mode, name, tool_args, "MCP 与应用内结果不一致"
        )


def test_class_comparison_domain_projection_matches_in_app(mcp_client, v1_seed):
    """域投影同源（Q01/P0-A2）：get_class_comparison 为 teaching 专属——
    teaching 会话可调且与应用内同果；homeroom 会话拒绝且错误文本与
    应用内逐字一致。"""
    ok = _call(mcp_client, "get_class_comparison", {"mode": "teaching", "exam_name": EXAM_E1})
    assert ok.get("isError") is not True
    assert _payload(ok) == _in_app_tool_result(
        "get_class_comparison", {"exam_name": EXAM_E1}, "teaching"
    )

    denied = _call(mcp_client, "get_class_comparison", {"mode": "homeroom", "exam_name": EXAM_E1})
    assert denied["isError"] is True
    assert _payload(denied) == _in_app_tool_result(
        "get_class_comparison", {"exam_name": EXAM_E1}, "homeroom"
    )
    assert "tool_not_in_scope" == _payload(denied)["error"]


def test_ws_out_of_scope_person_rejected_text(mcp_client, v1_seed):
    result = _call(
        mcp_client, "get_student_profile", {"mode": "homeroom", "person_id": v1_seed.wu_t_id}
    )
    assert result["isError"] is True
    payload = _payload(result)
    assert "该学生不在当前会话范围" in payload["detail"]


# ────────────────────── Q09/P0-A2：tools/list 发布全量注册表 ──────────────────────


def _list_tools(client) -> list:
    resp = _post(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert resp.status_code == 200
    return resp.json()["result"]["tools"]


def test_catalog_is_full_registry_without_legacy(mcp_client):
    """P0-A2：目录 = chat_tools 注册表全量工具（与应用内 AI 同一注册表，
    顺序即注册顺序），旧 20 工具默认不出现；每个条目 mode 必填注入。"""
    tools = _list_tools(mcp_client)
    names = [t["name"] for t in tools]
    assert names == list(EXPECTED_TOOL_ORDER)
    assert not (set(names) & set(LEGACY_TOOL_ORDER))
    by_name = {t["name"]: t for t in tools}
    for name in EXPECTED_TOOL_ORDER:
        schema = by_name[name]["inputSchema"]
        assert "mode" in schema["required"]
        assert schema["properties"]["mode"]["enum"] == ["homeroom", "teaching"]
        assert "必填" in schema["properties"]["mode"]["description"]
        assert "mode" in by_name[name]["description"]
        ann = by_name[name]["annotations"]
        assert ann["readOnlyHint"] is True and ann["destructiveHint"] is False


def test_ws_discovery_then_call_flow_matches_api(mcp_client):
    """Q09 发现→调用全流程：list 找到 get_exam_stats → 带 mode 调用 →
    结果与 /api/v1 端点一致；缺 mode → isError（正常客户端路径）。"""
    tools = _list_tools(mcp_client)
    spec = next(t for t in tools if t["name"] == "get_exam_stats")
    assert "mode" in spec["inputSchema"]["required"]

    result = _call(mcp_client, spec["name"], {"mode": "homeroom", "exam_name": EXAM_E1})
    assert result.get("isError") is not True
    payload = _payload(result)
    api = mcp_client.get(f"/api/v1/homeroom/analysis/exams/{EXAM_E1}/stats").json()
    assert payload["subjects"] == api["subjects"]
    assert payload["totals"] == api["totals"]
    assert payload["cohort_size"] == api["cohort_size"]


# ────────────────────── P0-A2：旧工具退出主链 ──────────────────────


def test_legacy_tool_not_callable_by_default(mcp_client):
    """旧只读工具退出主链：默认（未设 MCP_LEGACY_TOOLS）不可见也不可调用。"""
    result = _call(mcp_client, "list_exams", {"grade": 2})
    assert result["isError"] is True
    assert "未知或不可经 MCP 调用的工具" in _payload(result)["error"]


def test_legacy_compat_opt_in_deprecated_and_callable(mcp_client, monkeypatch):
    """显式开启 MCP_LEGACY_TOOLS=1：旧 20 工具以 deprecated 兼容入口追加在
    全部新工具之后（description 前缀 + _meta.deprecated），调用走旧
    execute_tool 独立路径、参数原样透传、无需 mode。"""
    import app.chat.tools as legacy_chat_tools

    monkeypatch.setenv("MCP_LEGACY_TOOLS", "true")
    tools = _list_tools(mcp_client)
    names = [t["name"] for t in tools]
    assert names[: len(EXPECTED_TOOL_ORDER)] == list(EXPECTED_TOOL_ORDER)
    assert names[len(EXPECTED_TOOL_ORDER):] == list(LEGACY_TOOL_ORDER)
    by_name = {t["name"]: t for t in tools}
    for name in LEGACY_TOOL_ORDER:
        assert by_name[name]["description"].startswith("[deprecated] "), name
        assert by_name[name]["_meta"]["deprecated"] is True, name

    captured = {}

    def fake_execute_tool(name, args):
        captured["name"] = name
        captured["args"] = args
        return {"echo": True}

    monkeypatch.setattr(legacy_chat_tools, "execute_tool", fake_execute_tool)
    result = _call(mcp_client, "list_exams", {"grade": 2, "extra": "keep-me"})
    assert result.get("isError") is not True
    assert captured == {"name": "list_exams", "args": {"grade": 2, "extra": "keep-me"}}
