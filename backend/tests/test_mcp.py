"""只读 MCP 服务端测试（P0-A2：MCP 全面接入新版工具体系）。

覆盖：
- 未启用时现有应用行为不变（无挂载、无 mcp SDK 导入、/api 正常、/mcp 404）
- 启用但 token 缺失/空白/弱占位符/过短 → fail closed（mount_mcp 抛错）
- 缺 token / 错误 token / 畸形 Authorization → 401 + WWW-Authenticate: Bearer，不回显 token
- 正确 token → initialize / tools/list / tools/call 全走通
- tools/list = chat_tools 注册表全量工具（P0-A2 单一注册源，顺序=注册
  顺序，mode 必填注入 schema，annotations 全只读）；旧 20 工具默认不出现
- 同源性（A01）：同一 scope 下 MCP 调用结果 == 应用内 execute_session_tool
  结果——代表工具全等断言 + 其余工具抽查 + 域投影/越界/时间语义错误路径
  全等断言
- 执行分发证明：monkeypatch execute_session_tool 捕获（参数原样透传，
  mode/scope 参数由服务端消费）
- 旧工具退出主链（P0-A2）：默认目录无旧工具、旧名称不可调用；仅
  MCP_LEGACY_TOOLS=1 显式开启时以 deprecated 兼容入口出现（description
  前缀 + _meta.deprecated），调用走旧 execute_tool 独立路径；旧注册表
  新增条目不进入默认目录（read_only 条目也只在 legacy 开启时出现）
- 新注册表扩展性：chat_tools TOOL_REGISTRY 新增 WsToolSpec 自动进入
  MCP 目录并可调用（以注册表实际内容为准）
- 非法 Host / Origin 被 SDK 防护拒绝（421 / 403）；无 Origin 放行；allowed hosts 可配置
- /mcp/ 在 follow_redirects=False 下正确 token 直接 200、缺 token 直接 401；/mcp 是 307
- 现有 chat TOOLS 恰为 TOOL_REGISTRY 的三键投影：20 个名称/顺序固定、注册表全只读
- 现有 /api/chat 行为不变

不依赖真实 NAS、真实 token、真实 LLM、git 仓库状态；全部使用合成数据。
"""

import json
import sys
from datetime import date

import pytest
from fastapi.testclient import TestClient

# 注册 ws 新表进共享 MetaData（与 tests/v1/conftest.py 同理；tests/conftest.py
# 已先于本模块设置好隔离数据目录，collection 期导入是安全的）：保证
# isolated_module_schema 的 create_all 一开始就建齐全量表——否则库内只有旧
# 表时，应用启动的 ensure_app_schema 会把该状态判为"演进状态不明"拒绝启动。
from app.db import workspace_models  # noqa: F401

STRONG_TOKEN = "f" * 48  # 满足 >=32 且非占位符

# P0-A2：MCP 默认目录 = chat_tools.TOOL_REGISTRY 全量工具（与应用内 AI
# 同一注册表），顺序即注册顺序。此处冻结全部名称与顺序（目录意外回退/漂移
# 即失败）；注册表有意变更时需连同本清单一起更新。P1-B4 追加第 25 个
# get_diagnosis_summary。
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
EXPECTED_TOOLS = set(EXPECTED_TOOL_ORDER)

# 旧 MCP 工具（app/chat/tools.py 旧注册表）：P0-A2 起退出主链——默认目录
# 不含、不可调用；仅 MCP_LEGACY_TOOLS=1 时以 deprecated 兼容入口追加。
LEGACY_TOOL_ORDER = (
    "list_exams", "student_lookup", "student_identity_lookup",
    "student_exam_detail", "student_trend", "student_learning_profile",
    "class_trend", "compare_classes", "focus_list", "subject_weakness",
    "subject_progress_ranking", "multi_exam_progress_ranking", "band_trend",
    "custom_rank_band_trend", "rank_range_filter", "rank_frequency_stat",
    "student_homework_summary", "class_homework_ranking",
    "homework_grade_correlation", "student_notes",
)
LEGACY_TOOLS = set(LEGACY_TOOL_ORDER)

_MCP_DESC_SUFFIX_END = "subject 同时作为工具层学科过滤参数）"


def _set_mcp_env(monkeypatch, enabled="true", token=STRONG_TOKEN,
                 hosts=None, origins=None, legacy=None):
    monkeypatch.setenv("MCP_ENABLED", enabled)
    if token is None:
        monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
    else:
        monkeypatch.setenv("MCP_BEARER_TOKEN", token)
    if hosts is not None:
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", hosts)
    else:
        monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    if origins is not None:
        monkeypatch.setenv("MCP_ALLOWED_ORIGINS", origins)
    else:
        monkeypatch.delenv("MCP_ALLOWED_ORIGINS", raising=False)
    if legacy is None:
        monkeypatch.delenv("MCP_LEGACY_TOOLS", raising=False)
    else:
        monkeypatch.setenv("MCP_LEGACY_TOOLS", legacy)


def _import_fresh_main():
    """重新 import app.main，让 MCP_ENABLED 在导入期生效。"""
    for mod in list(sys.modules):
        if mod == "app.main" or mod.startswith("app.mcp_server"):
            del sys.modules[mod]
    import app.main  # noqa: F401
    return sys.modules["app.main"]


_PROXY_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                   "http_proxy", "https_proxy", "all_proxy", "no_proxy")


def _clear_proxy_env(monkeypatch):
    """本机若设了 SOCKS/HTTP 代理，httpx 会对非 localhost 主机走代理并剥离
    凭据，干扰 Host 校验测试；统一清掉。"""
    for var in _PROXY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _no_host_auth_headers():
    """非 localhost base_url 的客户端：不显式传 Host（显式 Host 与 URL host
    不一致时 httpx 会剥离 Authorization），由 httpx 生成正确 Host。"""
    h = _auth_headers()
    h.pop("Host", None)
    return h


def _auth_headers(extra=None):
    h = {
        "Authorization": "Bearer " + STRONG_TOKEN,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Host": "localhost:8000",
    }
    if extra:
        h.update(extra)
    return h


def _post(client, body, headers):
    # 规范 URL 用 /mcp/（带尾斜杠）：/mcp 会被 FastAPI mount 307 重定向，
    # TestClient 默认跟随会掩盖真实响应，因此测试一律直接打 /mcp/。
    return client.post("/mcp/", json=body, headers=headers)


def _ok(resp):
    assert resp.status_code == 200, (resp.status_code, resp.text)
    return resp.json()


# ─────────────────────── 未启用：完全不影响现有应用 ───────────────────────

def test_disabled_no_mount_and_apis_healthy(monkeypatch):
    _set_mcp_env(monkeypatch, enabled="")
    for m in [k for k in sys.modules if k == "mcp" or k.startswith("mcp.")]:
        del sys.modules[m]
    main = _import_fresh_main()
    from starlette.routing import Mount
    assert not any(isinstance(r, Mount) and r.path == "/mcp" for r in main.app.routes)
    assert not any(k == "mcp" or k.startswith("mcp.") for k in sys.modules)
    with TestClient(main.app) as c:
        assert c.get("/api/health").json()["ok"] is True
        r = c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 404
        r2 = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    follow_redirects=False)
        assert r2.status_code == 404


# ─────────────────────── 启用但配置非法：fail closed ───────────────────────

def test_enabled_missing_token_fails_closed(monkeypatch):
    _set_mcp_env(monkeypatch, token=None)
    from app import mcp_server
    with pytest.raises(mcp_server.MCPConfigError):
        mcp_server.mount_mcp()


def test_enabled_blank_or_weak_token_fails_closed(monkeypatch):
    from app import mcp_server
    for bad in ["   ", "changeme", "your_token_here", "secret", "12345678",
                "short-token-1234", "fffffffffffffffffffffffffffffff"]:
        _set_mcp_env(monkeypatch, token=bad)
        with pytest.raises(mcp_server.MCPConfigError):
            mcp_server.mount_mcp()


def test_main_import_fails_closed_when_enabled_without_token(monkeypatch):
    """端到端 fail closed：MCP_ENABLED 开着但 token 缺失时，app.main 在
    导入（= uvicorn 启动）阶段就抛 MCPConfigError，而不是带病服务。"""
    import pytest as _pytest

    _set_mcp_env(monkeypatch, token=None)
    with _pytest.raises(Exception) as excinfo:
        _import_fresh_main()
    assert "MCP_BEARER_TOKEN" in str(excinfo.value)


def test_validate_fails_closed_when_catalog_drifts_from_registry(monkeypatch):
    """目录一致性校验：ws_tool_catalog 与注册表名称序列不一致 → 启动失败
    （防止目录生成被意外过滤/回退，P0-A2 单一注册源 fail closed）。"""
    _set_mcp_env(monkeypatch)
    import app.api.chat_tools as chat_tools_mod
    from app import mcp_server

    original = mcp_server.ws_tool_catalog
    monkeypatch.setattr(
        mcp_server, "ws_tool_catalog",
        lambda: original()[:-1],  # 模拟目录生成意外丢一个工具
    )
    with pytest.raises(mcp_server.MCPConfigError):
        mcp_server.validate_required_tools()


@pytest.fixture()
def mcp_app(monkeypatch):
    _set_mcp_env(monkeypatch)
    return _import_fresh_main().app


@pytest.fixture()
def client(mcp_app):
    with TestClient(mcp_app, base_url="http://localhost:8000") as c:
        yield c


_WS_EXAM = "2099期中"


@pytest.fixture(scope="module")
def ws_seed():
    """最小合成样本（同源性/发现→调用全流程用）：教师绑定高二6班 + 2 名
    学生 + 一场考试的语文/主三门总分。在班有效期取 2025-09-01（早于 as_of
    =当天，快照成员可解析）；学年/考试日期用 2099 合成值。ws 表已随本模块
    顶层导入注册，isolated_module_schema 建齐。"""
    from app.db import workspace_models as wm
    from app.db.models import HomeworkSetting, SessionLocal, Teacher
    db = SessionLocal()
    db.merge(Teacher(id=1, name="测试班主任", target_class_high2=6))
    db.merge(HomeworkSetting(key="active_grade", value="2"))
    ay = wm.AcademicYear(
        name="2099-2100",
        start_date=date(2099, 9, 1),
        end_date=date(2100, 7, 15),
    )
    db.add(ay)
    db.flush()
    h6 = wm.AdministrativeClass(
        academic_year_id=ay.id, grade=2, class_num=6, label="测试高二6班"
    )
    db.add(h6)
    db.flush()
    idents = []
    for display_name in ("测试甲", "测试乙"):
        ident = wm.WsStudentIdentity(data_domain="homeroom", display_name=display_name)
        db.add(ident)
        idents.append(ident)
    db.flush()
    for ident, seat in zip(idents, (1, 2)):
        db.add(
            wm.Enrollment(
                admin_class_id=h6.id,
                identity_id=ident.id,
                status="active",
                valid_from=date(2025, 9, 1),
                seat_no=seat,
            )
        )
    for ident, chinese, total in ((idents[0], 88.0, 275.0), (idents[1], 76.0, 236.0)):
        db.add(
            wm.ScoreFact(
                data_domain="homeroom",
                academic_year_id=ay.id,
                exam_name=_WS_EXAM,
                exam_date=date(2099, 11, 6),
                class_ref_id=h6.id,
                identity_id=ident.id,
                subject="语文",
                score=chinese,
                source="synthetic-test",
            )
        )
        db.add(
            wm.ScoreFact(
                data_domain="homeroom",
                academic_year_id=ay.id,
                exam_name=_WS_EXAM,
                exam_date=date(2099, 11, 6),
                class_ref_id=h6.id,
                identity_id=ident.id,
                total_type="主三门",
                score=total,
                source="synthetic-test",
            )
        )
    db.commit()
    yield {"exam_name": _WS_EXAM, "jia_id": idents[0].id, "yi_id": idents[1].id}
    db.close()


# ─────────────────────── 认证：401 / 放行 ───────────────────────

def test_missing_token_401_with_challenge(client):
    r = _post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
              {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "Host": "localhost:8000"})
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")
    assert STRONG_TOKEN not in r.text


def test_wrong_token_401_with_challenge(client):
    r = _post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
              _auth_headers({"Authorization": "Bearer totally-wrong-token-aaaa"}))
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")
    assert STRONG_TOKEN not in r.text


def test_malformed_authorization_header_401(client):
    for bad in ["Basic abc", "Bearer", "bearer ", STRONG_TOKEN]:
        r = _post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                  _auth_headers({"Authorization": bad}))
        assert r.status_code == 401, bad


# ─────────────────────── 协议：initialize / list / call ───────────────────────

def test_initialize_ok(client):
    r = _ok(_post(client, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "t", "version": "0"}},
    }, _auth_headers()))
    assert r["result"]["serverInfo"]["name"] == "exam-performance-analysis-mcp"
    assert r["result"]["protocolVersion"]


def test_tools_list_equals_registry_exactly(client):
    """P0-A2 单一注册源：目录 = chat_tools 注册表全量工具，名称与顺序
    逐项一致；旧工具一个都不出现；每个工具 mode 必填注入 schema、
    annotations 全只读、description = 注册表原文 + MCP 调用说明。"""
    from app.api.chat_tools import TOOL_REGISTRY
    r = _ok(_post(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, _auth_headers()))
    tools = r["result"]["tools"]
    names = [t["name"] for t in tools]
    assert names == list(EXPECTED_TOOL_ORDER)
    assert [spec.name for spec in TOOL_REGISTRY] == names  # 与注册表实况一致
    assert not (set(names) & LEGACY_TOOLS), "旧工具不得出现在默认目录"
    by_name = {t["name"]: t for t in tools}
    for spec in TOOL_REGISTRY:
        t = by_name[spec.name]
        ann = t["annotations"]
        assert ann["readOnlyHint"] is True
        assert ann["destructiveHint"] is False
        assert ann["idempotentHint"] is True
        assert ann["openWorldHint"] is False
        schema = t["inputSchema"]
        assert schema["required"][0] == "mode"
        assert schema["properties"]["mode"]["enum"] == ["homeroom", "teaching"]
        assert "必填" in schema["properties"]["mode"]["description"]
        assert t["description"] == spec.description + t["description"][
            len(spec.description):]
        assert t["description"].startswith(spec.description)
        assert t["description"].endswith(_MCP_DESC_SUFFIX_END)
        assert "mode" in t["description"]


def test_tools_list_schema_injected_mode_only(client):
    """schema 注入仅限必填 mode：其余 properties/required 与注册表条目逐项
    一致（应用内 AI 的 schema 不被 MCP 改写，注册表原样沿用）。"""
    from app.api.chat_tools import TOOL_REGISTRY
    r = _ok(_post(client, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, _auth_headers()))
    by_name = {t["name"]: t for t in r["result"]["tools"]}
    for spec in TOOL_REGISTRY:
        schema = by_name[spec.name]["inputSchema"]
        registry = spec.input_schema
        expected_required = ["mode", *list(registry.get("required") or [])]
        assert schema["required"] == expected_required
        rest = {k: v for k, v in schema["properties"].items() if k != "mode"}
        assert rest == (registry.get("properties") or {})
        assert schema["type"] == registry.get("type", "object")


def _mcp_call(client, name, arguments, req_id):
    r = _ok(_post(client, {
        "jsonrpc": "2.0", "id": req_id, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }, _auth_headers()))
    return r["result"]


def _mcp_payload(client, name, arguments, req_id):
    result = _mcp_call(client, name, arguments, req_id)
    assert result.get("isError") is not True, result
    return json.loads(result["content"][0]["text"])


def _mcp_payload_any(client, name, arguments, req_id):
    """tools/call 结果体（不预设成功/失败），供错误路径同源断言。"""
    result = _mcp_call(client, name, arguments, req_id)
    return json.loads(result["content"][0]["text"]), result.get("isError") is True


def _in_app_tool_result(name, tool_args, mode="homeroom"):
    """应用内 AI 同一受控路径的结果（同一注册表 execute_session_tool）：
    服务端解析快照（同 MCP 的 scope 语义）后执行同一工具。"""
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


# 同源性代表工具（A01：同 scope 下 MCP 调用结果 == 应用内工具结果）
_REPRESENTATIVE_TOOLS = (
    ("search_students", {"q": "测试"}),
    ("get_exam_list", {}),
    ("get_exam_stats", {"exam_name": _WS_EXAM}),
    ("get_scores_table", {"exam_name": _WS_EXAM}),
    ("get_academic_years", {}),
)
# 其余工具抽查（同样的全等断言，覆盖不同 handler 家族）
_SPOT_CHECK_TOOLS = (
    ("get_weekly_focus", {}),
    ("get_rank_metrics", {}),
    ("get_homework_summary", {}),
    ("get_homework_warnings", {}),
    ("get_homework_assignments", {}),
    ("get_homework_correlation", {"exam_name": _WS_EXAM, "subject": "语文"}),
    ("get_class_averages", {"exam_name": _WS_EXAM}),
    ("get_exam_students", {"exam_name": _WS_EXAM}),
    ("get_exam_focus", {"exam_name": _WS_EXAM}),
)
# 错误路径同源（域投影/越界/时间语义：MCP 与应用内同样逐字全等）
_ERROR_PATH_TOOLS = (
    ("get_class_comparison", {"exam_name": _WS_EXAM}),  # teaching 专属，homeroom 域投影拒绝
    ("get_student_profile", {"person_id": 999999}),     # 成员越界
    ("get_scores_table", {"exam_name": _WS_EXAM, "year_offset": -1}),  # 学年偏移越界
    ("get_scores_table", {"exam_name": "2099摸底考"}),  # 考试不在范围
)


def test_mcp_call_equals_in_app_tool_result(client, ws_seed):
    """核心同源性（A01）：同一 scope（同 mode、同服务端解析快照）下，
    MCP tools/call 的结果体与应用内 execute_session_tool 结果逐字全等。
    代表工具全等 + 其余工具抽查 + 错误路径（域投影/越界/时间语义）全等。"""
    for req_id, (name, tool_args) in enumerate(
        _REPRESENTATIVE_TOOLS + _SPOT_CHECK_TOOLS, start=100
    ):
        payload = _mcp_payload(client, name, {"mode": "homeroom", **tool_args}, req_id)
        expected = _in_app_tool_result(name, tool_args)
        assert payload == expected, f"{name} {tool_args} MCP 与应用内结果不一致"

    for req_id, (name, tool_args) in enumerate(_ERROR_PATH_TOOLS, start=150):
        payload, is_error = _mcp_payload_any(
            client, name, {"mode": "homeroom", **tool_args}, req_id
        )
        expected = _in_app_tool_result(name, tool_args)
        assert payload == expected, f"{name} {tool_args} MCP 与应用内错误结果不一致"
        assert is_error and "error" in expected, f"{name} 应为错误路径"


def test_mcp_member_scoped_tools_match_in_app(client, ws_seed):
    """成员守卫类工具同源：在册成员（快照 member_person_ids 内）正向可查、
    越界成员拒绝，两路径结果全等。"""
    for req_id, (name, tool_args) in enumerate(
        (
            ("search_students", {"q": "测试甲"}),
            ("get_student_profile", {"person_id": ws_seed["jia_id"]}),
            ("get_homework_student", {"person_id": ws_seed["jia_id"]}),
            ("get_student_notes", {"person_id": ws_seed["jia_id"]}),
            ("get_student_trends", {"person_id": ws_seed["jia_id"]}),
        ),
        start=200,
    ):
        payload = _mcp_payload(client, name, {"mode": "homeroom", **tool_args}, req_id)
        expected = _in_app_tool_result(name, tool_args)
        assert payload == expected, f"{name} {tool_args} MCP 与应用内结果不一致"
        assert "error" not in payload, f"{name} 应为正向数据路径"


def test_ws_discovery_then_call_flow_matches_api(client, ws_seed):
    """发现→调用全流程（正常客户端路径）：tools/list 找到
    get_exam_stats → 缺 mode 调用 isError → 带 mode 调用与 /api/v1
    端点同数值（同一 service 层，A01）。"""
    r = _ok(_post(client, {"jsonrpc": "2.0", "id": 30, "method": "tools/list"}, _auth_headers()))
    spec = next(t for t in r["result"]["tools"] if t["name"] == "get_exam_stats")
    assert "mode" in spec["inputSchema"]["required"]

    missing = _ok(_post(client, {
        "jsonrpc": "2.0", "id": 31, "method": "tools/call",
        "params": {"name": "get_exam_stats",
                   "arguments": {"exam_name": ws_seed["exam_name"]}},
    }, _auth_headers()))
    assert missing["result"]["isError"] is True
    payload = json.loads(missing["result"]["content"][0]["text"])
    assert payload["error"] == "invalid_scope_param"
    assert payload["status"] == 422

    ok = _ok(_post(client, {
        "jsonrpc": "2.0", "id": 32, "method": "tools/call",
        "params": {"name": "get_exam_stats",
                   "arguments": {"mode": "homeroom", "exam_name": ws_seed["exam_name"]}},
    }, _auth_headers()))
    assert ok["result"].get("isError") is not True
    body = json.loads(ok["result"]["content"][0]["text"])
    api = client.get(f"/api/v1/homeroom/analysis/exams/{ws_seed['exam_name']}/stats").json()
    assert body["subjects"] == api["subjects"]
    assert body["totals"] == api["totals"]
    assert body["cohort_size"] == api["cohort_size"] == 2


def test_call_dispatches_through_execute_session_tool_untouched(client, ws_seed, monkeypatch):
    """分发证明：MCP 调用走与应用内 AI 同一 execute_session_tool；工具参数
    原样透传（mode/scope 类参数由服务端消费，不进入工具参数）。"""
    import app.api.chat_tools as chat_tools_mod
    captured = {}

    def fake(db, snapshot, name, args, teacher_id=None):
        captured["name"] = name
        captured["args"] = args
        captured["domain"] = snapshot["data_domain"]
        return {"echo": True, "got": args}

    monkeypatch.setattr(chat_tools_mod, "execute_session_tool", fake)
    result = _mcp_call(client, "search_students",
                       {"mode": "homeroom", "q": "关键字", "extra": "keep-me"}, 4)
    assert captured["name"] == "search_students"
    assert captured["args"] == {"q": "关键字", "extra": "keep-me"}
    assert captured["domain"] == "homeroom"
    assert json.loads(result["content"][0]["text"]) == {
        "echo": True, "got": {"q": "关键字", "extra": "keep-me"}
    }
    assert result.get("isError") is not True


def test_mcp_call_lands_chat_session_of_type_mcp(client, ws_seed):
    """契约 §4：MCP 调用同样落 ChatSession(type='mcp')（快照入库可审计）。"""
    from app.db.models import SessionLocal
    from app.db.workspace_models import ChatSession

    _mcp_payload(client, "get_exam_list", {"mode": "homeroom"}, 41)
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
        assert scope["cohort_size"] == 2
    finally:
        db.close()


def test_non_registry_tools_not_callable(client):
    """非注册表名称一律不可调用（P0-A2 默认含旧工具）：旧只读工具、写工具、
    不存在名称都返回 isError。"""
    targets = ("list_exams", "student_lookup", "render_chart", "no_such_tool_xyz")
    for name in targets:
        result = _mcp_call(client, name, {"mode": "homeroom"}, 5)
        assert result["isError"] is True, name
        payload = json.loads(result["content"][0]["text"])
        assert "未知或不可经 MCP 调用的工具" in payload["error"], name


# ─────────────────────── 旧工具退出主链（P0-A2） ───────────────────────

def test_legacy_tools_absent_from_default_catalog(client):
    """旧 20 工具退出主链：默认目录不含任何旧工具名称（tools/list 与
    非注册表拒绝双保险）。"""
    r = _ok(_post(client, {"jsonrpc": "2.0", "id": 6, "method": "tools/list"}, _auth_headers()))
    names = {t["name"] for t in r["result"]["tools"]}
    assert names == EXPECTED_TOOLS
    assert not (names & LEGACY_TOOLS)


def test_legacy_tool_call_rejected_by_default(client):
    """默认（无 MCP_LEGACY_TOOLS）：旧名称调用一律拒绝（旧工具退出主链）。"""
    result = _mcp_call(client, "list_exams", {"grade": 2}, 7)
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert "未知或不可经 MCP 调用的工具" in payload["error"]


def test_legacy_compat_opt_in_explicitly_deprecated(client, monkeypatch):
    """兼容入口显式开启（MCP_LEGACY_TOOLS=1）：旧 20 工具追加在全部新工具
    之后，条目显式标注 deprecated（description 前缀 + _meta.deprecated），
    调用走旧 execute_tool 独立路径、参数原样透传、无需 mode。"""
    import app.chat.tools as legacy_chat_tools

    monkeypatch.setenv("MCP_LEGACY_TOOLS", "true")
    r = _ok(_post(client, {"jsonrpc": "2.0", "id": 8, "method": "tools/list"}, _auth_headers()))
    tools = r["result"]["tools"]
    names = [t["name"] for t in tools]
    assert names[: len(EXPECTED_TOOL_ORDER)] == list(EXPECTED_TOOL_ORDER)
    assert names[len(EXPECTED_TOOL_ORDER):] == list(LEGACY_TOOL_ORDER)
    by_name = {t["name"]: t for t in tools}
    for name in LEGACY_TOOL_ORDER:
        t = by_name[name]
        assert t["description"].startswith("[deprecated] "), name
        assert t["_meta"]["deprecated"] is True, name
        assert t["annotations"]["readOnlyHint"] is True, name
        assert "mode" not in (t["inputSchema"].get("properties") or {}), name

    captured = {}

    def fake_execute_tool(name, args):
        captured["name"] = name
        captured["args"] = args
        return {"echo": True, "got": args}

    monkeypatch.setattr(legacy_chat_tools, "execute_tool", fake_execute_tool)
    result = _mcp_call(client, "list_exams", {"grade": 2, "extra": "keep-me"}, 9)
    assert result.get("isError") is not True
    assert captured == {"name": "list_exams", "args": {"grade": 2, "extra": "keep-me"}}
    assert json.loads(result["content"][0]["text"]) == {
        "echo": True, "got": {"grade": 2, "extra": "keep-me"}
    }


def test_legacy_opt_out_value_keeps_default_rejection(client, monkeypatch):
    """MCP_LEGACY_TOOLS 显式置 false 与缺省同语义：旧工具不可见也不可调用。"""
    monkeypatch.setenv("MCP_LEGACY_TOOLS", "false")
    r = _ok(_post(client, {"jsonrpc": "2.0", "id": 10, "method": "tools/list"}, _auth_headers()))
    names = {t["name"] for t in r["result"]["tools"]}
    assert not (names & LEGACY_TOOLS)
    result = _mcp_call(client, "list_exams", {}, 11)
    assert result["isError"] is True


def test_old_registry_new_entry_never_enters_default_catalog(client, monkeypatch):
    """旧注册表新增条目（即使 read_only）也不进默认目录；仅 legacy 开启时
    以 deprecated 形态出现；写入条目（read_only=False）连 legacy 开启也不
    出现——旧体系绝不经由 MCP 回到主链。"""
    import app.chat.tools as legacy_chat_tools

    ro_probe = {
        "name": "_tmp_legacy_ro_probe",
        "read_only": True,
        "description": "旧注册表只读探针",
        "input_schema": {"type": "object", "properties": {"x": {"type": "integer"}}},
    }
    write_probe = {
        "name": "_tmp_legacy_write_probe",
        "read_only": False,
        "description": "旧注册表写探针",
        "input_schema": {"type": "object", "properties": {}},
    }
    monkeypatch.setattr(
        legacy_chat_tools, "TOOL_REGISTRY",
        [*legacy_chat_tools.TOOL_REGISTRY, ro_probe, write_probe],
    )
    monkeypatch.setattr(
        legacy_chat_tools, "TOOL_FUNCTIONS",
        {**legacy_chat_tools.TOOL_FUNCTIONS,
         ro_probe["name"]: (lambda **kw: {"ok": kw}),
         write_probe["name"]: (lambda **kw: {"wrote": True})},
    )
    r = _ok(_post(client, {"jsonrpc": "2.0", "id": 12, "method": "tools/list"}, _auth_headers()))
    names = {t["name"] for t in r["result"]["tools"]}
    assert names == EXPECTED_TOOLS  # 默认目录零变化

    monkeypatch.setenv("MCP_LEGACY_TOOLS", "true")
    r2 = _ok(_post(client, {"jsonrpc": "2.0", "id": 13, "method": "tools/list"}, _auth_headers()))
    names2 = {t["name"] for t in r2["result"]["tools"]}
    assert ro_probe["name"] in names2
    deprecated_entry = next(
        t for t in r2["result"]["tools"] if t["name"] == ro_probe["name"]
    )
    assert deprecated_entry["description"].startswith("[deprecated] ")
    assert deprecated_entry["_meta"]["deprecated"] is True
    assert write_probe["name"] not in names2

    result = _mcp_call(client, write_probe["name"], {}, 14)
    assert result["isError"] is True


# ─────────────────────── 新注册表扩展性（以注册表实况为准） ───────────────────────

def test_ws_registry_new_entry_auto_appears_and_callable(client, ws_seed, monkeypatch):
    """chat_tools 注册表新增 WsToolSpec 即自动进入 MCP 目录（mode 注入）
    并可经同一受控路径调用——目录以注册表实际内容为准，不是凑数。"""
    import app.api.chat_tools as chat_tools_mod
    from app.api.chat_tools import WsToolSpec

    probe = WsToolSpec(
        name="_tmp_ws_probe",
        description="新注册表临时探针",
        input_schema={
            "type": "object",
            "properties": {"q": {"type": "string"}},
        },
        domains=("homeroom", "teaching"),
        handler=lambda db, snapshot, args: {
            "ok": True,
            "q": args.get("q"),
            "domain": snapshot["data_domain"],
        },
    )
    monkeypatch.setattr(
        chat_tools_mod, "TOOL_REGISTRY", [*chat_tools_mod.TOOL_REGISTRY, probe]
    )
    monkeypatch.setattr(
        chat_tools_mod, "_REGISTRY_BY_NAME",
        {**chat_tools_mod._REGISTRY_BY_NAME, probe.name: probe},
    )

    r = _ok(_post(client, {"jsonrpc": "2.0", "id": 15, "method": "tools/list"}, _auth_headers()))
    names = [t["name"] for t in r["result"]["tools"]]
    assert names == [*list(EXPECTED_TOOL_ORDER), "_tmp_ws_probe"]
    probe_entry = r["result"]["tools"][-1]
    assert "mode" in probe_entry["inputSchema"]["required"]

    payload = _mcp_payload(client, "_tmp_ws_probe", {"mode": "homeroom", "q": "x"}, 16)
    assert payload == {"ok": True, "q": "x", "domain": "homeroom"}


# ─────────────────────── SDK Host / Origin 防护 ───────────────────────

def test_invalid_host_421(mcp_app, monkeypatch):
    """非法 Host（经 base_url 控制）→ 421。清代理 env 防本机代理干扰。"""
    _clear_proxy_env(monkeypatch)
    with TestClient(mcp_app, base_url="http://evil.example.com:8000") as c:
        r = _post(c, {"jsonrpc": "2.0", "id": 11, "method": "tools/list"},
                  _no_host_auth_headers())
        assert r.status_code == 421


def test_invalid_origin_403(client):
    r = _post(client, {"jsonrpc": "2.0", "id": 12, "method": "tools/list"},
              _auth_headers({"Origin": "https://evil.example"}))
    assert r.status_code == 403


def test_no_origin_browserless_client_ok(client):
    """Hermes 等非浏览器客户端不带 Origin：正常放行。"""
    r = _post(client, {"jsonrpc": "2.0", "id": 13, "method": "tools/list"}, _auth_headers())
    assert r.status_code == 200


def test_allowed_hosts_configurable(monkeypatch):
    """MCP_ALLOWED_HOSTS 配置公网域名后：该域名放行。"""
    _clear_proxy_env(monkeypatch)
    _set_mcp_env(monkeypatch,
                 hosts="grade.zuoyuan.wang,grade.zuoyuan.wang:*,localhost:8000")
    main = _import_fresh_main()
    with TestClient(main.app, base_url="https://grade.zuoyuan.wang") as c:
        r = c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                   headers=_no_host_auth_headers())
        assert r.status_code == 200


def test_unlisted_host_rejected_when_configured(monkeypatch):
    """同一配置下，未列入白名单的域名仍 421（独立 fresh import：session
    manager 每 app 实例只能 run 一次）。"""
    _clear_proxy_env(monkeypatch)
    _set_mcp_env(monkeypatch,
                 hosts="grade.zuoyuan.wang,grade.zuoyuan.wang:*,localhost:8000")
    main = _import_fresh_main()
    with TestClient(main.app, base_url="http://other.example.com:8080") as c:
        r = c.post("/mcp/", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                   headers=_no_host_auth_headers())
        assert r.status_code == 421


def test_bad_token_rejected_before_host_check(monkeypatch):
    """认证在最外层：错误 token + 非法 Host 也是 401（不泄露 Host 校验细节）。"""
    _clear_proxy_env(monkeypatch)
    _set_mcp_env(monkeypatch)
    main = _import_fresh_main()
    with TestClient(main.app, base_url="http://evil.example.com:8000") as c:
        r = c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                   headers={**_no_host_auth_headers(),
                            "Authorization": "Bearer wrong-token-zzz"})
        assert r.status_code == 401


# ─────────────────────── 规范 URL / 重定向 ───────────────────────

def test_canonical_url_no_redirect_masking(client):
    """/mcp/ 是规范 URL：两个分支都关闭重定向跟随，直接拿到最终响应 ——
    正确 token 直接 200（真实 tools 结果）、缺 token 直接 401，而非 307。"""
    r = client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 21, "method": "tools/list"},
        headers=_auth_headers(),
        follow_redirects=False,
    )
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["tools"]
    assert {t["name"] for t in result["tools"]} == EXPECTED_TOOLS

    r2 = client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 22, "method": "tools/list"},
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "Host": "localhost:8000"},
        follow_redirects=False,
    )
    assert r2.status_code == 401
    assert r2.headers["www-authenticate"].startswith("Bearer")


def test_bare_mcp_path_redirects_to_canonical(client):
    """/mcp（无尾斜杠）是 307 → /mcp/；面向用户的兼容入口，Hermes/curl -L 会跟随。"""
    r = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 23, "method": "tools/list"},
        headers=_auth_headers(),
        follow_redirects=False,
    )
    assert r.status_code == 307
    assert r.headers["location"].endswith("/mcp/")


# ─────────────────────── 现有聊天助手不受影响 ───────────────────────

def test_chat_config_and_chat_endpoint_unchanged(mcp_app, monkeypatch):
    _clear_proxy_env(monkeypatch)
    with TestClient(mcp_app) as c:
        r = c.get("/api/chat/config")
        assert r.status_code == 200
        assert "provider" in r.json()
        # 未绑定班级作用域时 /api/chat 维持既有 409 拒绝（不触达 LLM）。
        r2 = c.post("/api/chat", json={"messages": [], "context": {}})
        assert r2.status_code in (200, 400, 409, 422)


def test_session_build_tools_list_is_public_projection():
    from app.chat.session import build_tools_list
    tools = build_tools_list()
    assert len(tools) == 20
    for t in tools:
        assert list(t.keys()) == ["name", "description", "input_schema"]


# ───────────────── TOOLS 恰为 TOOL_REGISTRY 三键投影 ─────────────────

def test_public_tools_exactly_registry_projection():
    """公开 TOOLS（旧 /api/chat 会话协议输入）恰为旧 TOOL_REGISTRY 的三键
    投影：结构（name/description/input_schema）、顺序、内容逐项一致；注册表
    20 项全只读、名称与顺序固定。旧聊天助手与 MCP 改造互不影响。"""
    from app.chat.tools import TOOLS, TOOL_REGISTRY

    assert len(TOOL_REGISTRY) == 20
    assert len(TOOLS) == 20
    expected = [
        {
            "name": e["name"],
            "description": e["description"],
            "input_schema": e["input_schema"],
        }
        for e in TOOL_REGISTRY
    ]
    assert TOOLS == expected
    for t in TOOLS:
        assert list(t.keys()) == ["name", "description", "input_schema"]
    assert [t["name"] for t in TOOLS] == list(LEGACY_TOOL_ORDER)
    assert [e["name"] for e in TOOL_REGISTRY] == list(LEGACY_TOOL_ORDER)
    assert set(LEGACY_TOOL_ORDER) == LEGACY_TOOLS
    for entry in TOOL_REGISTRY:
        assert entry["read_only"] is True
