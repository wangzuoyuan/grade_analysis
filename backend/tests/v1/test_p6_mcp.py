"""P6 MCP ws 工具测试（契约 docs/contracts/p6-ai-mcp.md §4/§0.1；A01）。

- mode 缺省 / 非法 → 错误结果（422 invalid_scope_param 语义，模型可读）
- 带 mode 调用 → 服务端重新解析 scope，走 chat_tools 同一注册表：
  与 /api/v1 端点同数值；会话落 ChatSession(type='mcp')
- 域投影/越界在 MCP 路径同样生效（越界 person → 拒绝文本）
- Q09：tools/list 发布 ws 四工具（完整 name/description/inputSchema，
  mode 必填 + enum），目录 = 旧 20 + ws 4 = 24；发现→调用全流程可走通
"""

import json
import os
import sys

import pytest

STRONG_TOKEN = "e" * 48  # >= 32 且非占位符
EXAM_E1 = "2025期中"

# 契约 §4 首版稳定子集（顺序 = mcp_server.P6_WS_TOOL_NAMES，即目录追加顺序）
P6_WS_TOOLS = ("search_students", "get_student_profile", "get_exam_stats", "get_scores_table")


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

    from app.db.models import SessionLocal
    from app.db.workspace_models import ChatSession

    db = SessionLocal()
    try:
        row = db.get(ChatSession, payload["session_id"])
        assert row is not None
        assert row.type == "mcp"
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


# ────────────────────── 域投影 / 越界在 MCP 路径同样生效 ──────────────────────


def test_ws_out_of_scope_person_rejected_text(mcp_client, v1_seed):
    result = _call(
        mcp_client, "get_student_profile", {"mode": "homeroom", "person_id": v1_seed.wu_t_id}
    )
    assert result["isError"] is True
    payload = _payload(result)
    assert "该学生不在当前会话范围" in payload["detail"]


def test_ws_tool_outside_stable_subset_rejected(mcp_client, v1_seed):
    """子集之外的注册表工具（教学班对比）不可经 MCP 调用。"""
    result = _call(
        mcp_client, "get_class_comparison", {"mode": "teaching", "exam_name": EXAM_E1}
    )
    assert result["isError"] is True
    payload = _payload(result)
    assert "error" in payload


# ────────────────────── Q09：tools/list 发布 ws 工具 ──────────────────────

# 旧 20 个只读工具（顺序即 tests/test_mcp.py 冻结契约）
LEGACY_TOOLS = (
    "list_exams", "student_lookup", "student_identity_lookup",
    "student_exam_detail", "student_trend", "student_learning_profile",
    "class_trend", "compare_classes", "focus_list", "subject_weakness",
    "subject_progress_ranking", "multi_exam_progress_ranking", "band_trend",
    "custom_rank_band_trend", "rank_range_filter", "rank_frequency_stat",
    "student_homework_summary", "class_homework_ranking",
    "homework_grade_correlation", "student_notes",
)


def _list_tools(client) -> list:
    resp = _post(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert resp.status_code == 200
    return resp.json()["result"]["tools"]


def test_catalog_exposes_ws_tools_after_legacy_20(mcp_client):
    """Q09：目录 = 旧 20（顺序不变）+ ws 4；ws 条目 schema 注入必填 mode。"""
    tools = _list_tools(mcp_client)
    names = [t["name"] for t in tools]
    assert names[:20] == list(LEGACY_TOOLS)  # 旧目录条目与顺序零改动
    assert names[20:] == list(P6_WS_TOOLS)
    assert len(names) == 24
    by_name = {t["name"]: t for t in tools}
    for name in P6_WS_TOOLS:
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


def test_legacy_tool_still_callable_without_mode(mcp_client, monkeypatch):
    """旧只读工具不受 P6 分支影响：无需 mode、参数原样透传
    （fake execute_tool 证明分发路径与 test_mcp 时期一致）。"""
    from app.chat import tools as chat_tools

    captured = {}

    def fake_execute_tool(name, args):
        captured["name"] = name
        captured["args"] = args
        return {"echo": True}

    monkeypatch.setattr(chat_tools, "execute_tool", fake_execute_tool)
    result = _call(mcp_client, "list_exams", {"grade": 2, "extra": "keep-me"})
    assert result.get("isError") is not True
    assert captured["name"] == "list_exams"
    assert captured["args"] == {"grade": 2, "extra": "keep-me"}
