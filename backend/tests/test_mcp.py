"""只读 MCP 服务端测试（班主任版）。

覆盖：
- 未启用时现有应用行为不变（无挂载、无 mcp SDK 导入、/api 正常、/mcp 404）
- 启用但 token 缺失/空白/弱占位符/过短 → fail closed（mount_mcp 抛错）
- 缺 token / 错误 token / 畸形 Authorization → 401 + WWW-Authenticate: Bearer，不回显 token
- 正确 token → initialize / tools/list / tools/call 全走通
- tools/list = 旧 20 个只读工具（顺序固定）+ P6 ws 四工具（Q09，追加在后，
  mode 必填注入 schema），共 24 个，annotations 全只读
- tools/call 经 execute_tool 分发（monkeypatch 证明），参数不被改写
- 非目录工具（写入/不存在/render_chart）不可调用
- 新增只读注册项自动出现在 MCP；写入项/未标记项默认不出现（扩展性 + 安全）
- ws 工具发现→调用全流程（list 找到 → 缺 mode isError → 带 mode 与 API 同数值）
- 非法 Host / Origin 被 SDK 防护拒绝（421 / 403）；无 Origin 放行；allowed hosts 可配置
- /mcp/ 在 follow_redirects=False 下正确 token 直接 200、缺 token 直接 401；/mcp 是 307
- 现有 chat TOOLS 恰为 TOOL_REGISTRY 的三键投影：20 个名称/顺序固定、注册表全只读
- 现有 /api/chat 行为不变

不依赖真实 NAS、真实 token、真实 LLM、git 仓库状态。
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

# 班主任版注册表当前必须暴露的全部只读工具（成绩 16 + 作业 3 + 档案 1），
# 顺序即对外契约（TOOLS 投影、MCP 目录都保持这一顺序）。
REQUIRED_TOOL_ORDER = (
    "list_exams", "student_lookup", "student_identity_lookup",
    "student_exam_detail", "student_trend", "student_learning_profile",
    "class_trend", "compare_classes", "focus_list", "subject_weakness",
    "subject_progress_ranking", "multi_exam_progress_ranking", "band_trend",
    "custom_rank_band_trend", "rank_range_filter", "rank_frequency_stat",
    "student_homework_summary", "class_homework_ranking",
    "homework_grade_correlation", "student_notes",
)
REQUIRED_TOOLS = set(REQUIRED_TOOL_ORDER)

# P6 ws 四工具（Q09）：追加在旧 20 之后，顺序 = mcp_server.P6_WS_TOOL_NAMES
WS_TOOL_ORDER = ("search_students", "get_student_profile", "get_exam_stats", "get_scores_table")
WS_TOOLS = set(WS_TOOL_ORDER)


def _set_mcp_env(monkeypatch, enabled="true", token=STRONG_TOKEN,
                 hosts=None, origins=None):
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
    """最小合成样本（Q09 发现→调用全流程用）：教师绑定高二6班 + 2 名
    学生 + 一场考试的语文/主三门总分。ws 表已随本模块顶层导入注册，
    isolated_module_schema 建齐。"""
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
                valid_from=date(2099, 9, 1),
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
    yield {"exam_name": _WS_EXAM}
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


def test_tools_list_equals_readonly_registry_exactly(client):
    """目录 = 旧 20 只读工具（顺序固定）+ P6 ws 四工具（追加在后，Q09）：
    旧条目一个不少、别的一个不多；ws 四工具 mode 必填注入 schema。"""
    from app.chat.tools import readonly_tool_catalog
    r = _ok(_post(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, _auth_headers()))
    tools = r["result"]["tools"]
    names = [t["name"] for t in tools]
    expected = [e["name"] for e in readonly_tool_catalog()]
    assert names[: len(expected)] == expected
    assert set(names) == REQUIRED_TOOLS | WS_TOOLS
    assert len(names) == 24
    assert names[len(expected):] == list(WS_TOOL_ORDER)
    by_name = {t["name"]: t for t in tools}
    for t in tools:
        ann = t["annotations"]
        assert ann["readOnlyHint"] is True
        assert ann["destructiveHint"] is False
        assert ann["idempotentHint"] is True
        assert ann["openWorldHint"] is False
    for name in WS_TOOL_ORDER:
        schema = by_name[name]["inputSchema"]
        assert "mode" in schema["required"]
        assert schema["properties"]["mode"]["enum"] == ["homeroom", "teaching"]
        assert "必填" in schema["properties"]["mode"]["description"]
        assert "mode" in by_name[name]["description"]


def test_tools_list_schema_verbatim_from_registry(client):
    """旧 20 工具 schema 原样沿用注册表；ws 四工具 schema = 注册表条目 +
    必填 mode 注入（p6_ws_mcp_catalog 单一来源）。"""
    from app.chat.tools import readonly_tool_catalog

    from app.mcp_server import p6_ws_mcp_catalog
    r = _ok(_post(client, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, _auth_headers()))
    reg = {e["name"]: e for e in readonly_tool_catalog()}
    ws = {e["name"]: e for e in p6_ws_mcp_catalog()}
    for t in r["result"]["tools"]:
        e = reg.get(t["name"]) or ws[t["name"]]
        assert t["description"] == e["description"]
        # 旧目录条目键为 input_schema（chat/tools 惯例），ws 目录条目为
        # inputSchema（MCP 形状），MCP 报文恒为 inputSchema
        assert t["inputSchema"] == (e.get("inputSchema") or e.get("input_schema"))


def test_ws_discovery_then_call_flow_matches_api(client, ws_seed):
    """Q09 发现→调用全流程（正常客户端路径）：tools/list 找到
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


def test_call_dispatches_through_execute_tool_untouched(client, monkeypatch):
    from app.chat import tools as chat_tools
    captured = {}

    def fake(name, args):
        captured["name"] = name
        captured["args"] = args
        return {"echo": True, "got": args}

    monkeypatch.setattr(chat_tools, "execute_tool", fake)
    r = _ok(_post(client, {
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "list_exams",
                   "arguments": {"grade": 2, "extra": "keep-me"}},
    }, _auth_headers()))
    assert captured["name"] == "list_exams"
    assert captured["args"] == {"grade": 2, "extra": "keep-me"}
    payload = json.loads(r["result"]["content"][0]["text"])
    assert payload["got"] == {"grade": 2, "extra": "keep-me"}
    assert r["result"].get("isError") is not True


def test_non_catalog_tools_not_callable(client):
    from app.chat.tools import TOOL_REGISTRY
    non_ro = [e["name"] for e in TOOL_REGISTRY if not e.get("read_only")]
    targets = (non_ro[:1] or []) + ["no_such_tool_xyz", "render_chart",
                                    "list_my_classes"]
    for name in targets:
        r = _ok(_post(client, {
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": name, "arguments": {}},
        }, _auth_headers()))
        assert r["result"]["isError"] is True, name


# ─────────────────────── 扩展性与安全 ───────────────────────

def test_new_readonly_tool_auto_appears(client, monkeypatch):
    from app.chat import tools as chat_tools
    entry = {
        "name": "_tmp_readonly_probe",
        "read_only": True,
        "description": "临时只读探针",
        "input_schema": {"type": "object", "properties": {"x": {"type": "integer"}}},
    }
    monkeypatch.setattr(chat_tools, "TOOL_REGISTRY", chat_tools.TOOL_REGISTRY + [entry])
    monkeypatch.setattr(
        chat_tools, "TOOL_FUNCTIONS",
        {**chat_tools.TOOL_FUNCTIONS, "_tmp_readonly_probe": lambda **kw: {"ok": kw}},
    )
    r = _ok(_post(client, {"jsonrpc": "2.0", "id": 6, "method": "tools/list"}, _auth_headers()))
    names = {t["name"] for t in r["result"]["tools"]}
    assert "_tmp_readonly_probe" in names

    r2 = _ok(_post(client, {
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "_tmp_readonly_probe", "arguments": {"x": 1}},
    }, _auth_headers()))
    assert json.loads(r2["result"]["content"][0]["text"]) == {"ok": {"x": 1}}


def test_write_tool_not_exposed_and_not_callable(client, monkeypatch):
    from app.chat import tools as chat_tools
    entry = {
        "name": "_tmp_write_probe",
        "read_only": False,
        "description": "临时写探针",
        "input_schema": {"type": "object", "properties": {}},
    }
    monkeypatch.setattr(chat_tools, "TOOL_REGISTRY", chat_tools.TOOL_REGISTRY + [entry])
    monkeypatch.setattr(
        chat_tools, "TOOL_FUNCTIONS",
        {**chat_tools.TOOL_FUNCTIONS, "_tmp_write_probe": lambda **kw: {"wrote": True}},
    )
    r = _ok(_post(client, {"jsonrpc": "2.0", "id": 8, "method": "tools/list"}, _auth_headers()))
    names = {t["name"] for t in r["result"]["tools"]}
    assert "_tmp_write_probe" not in names

    r2 = _ok(_post(client, {
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"name": "_tmp_write_probe", "arguments": {}},
    }, _auth_headers()))
    assert r2["result"]["isError"] is True


def test_registry_entry_without_flag_not_exposed(client, monkeypatch):
    """read_only 缺省（未标记）的条目同样不暴露 —— 默认安全。"""
    from app.chat import tools as chat_tools
    entry = {"name": "_tmp_unflagged_probe", "description": "未标记",
             "input_schema": {"type": "object", "properties": {}}}
    monkeypatch.setattr(chat_tools, "TOOL_REGISTRY", chat_tools.TOOL_REGISTRY + [entry])
    r = _ok(_post(client, {"jsonrpc": "2.0", "id": 10, "method": "tools/list"}, _auth_headers()))
    names = {t["name"] for t in r["result"]["tools"]}
    assert "_tmp_unflagged_probe" not in names


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
    assert {t["name"] for t in result["tools"]} == REQUIRED_TOOLS | WS_TOOLS

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
    """公开 TOOLS（session 聊天协议输入）恰为 TOOL_REGISTRY 的三键投影：
    结构（name/description/input_schema）、顺序、内容逐项一致；注册表
    20 项全只读、名称与顺序固定。不依赖 git 基线自执行。"""
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
    assert [t["name"] for t in TOOLS] == list(REQUIRED_TOOL_ORDER)
    assert [e["name"] for e in TOOL_REGISTRY] == list(REQUIRED_TOOL_ORDER)
    assert set(REQUIRED_TOOL_ORDER) == REQUIRED_TOOLS
    for entry in TOOL_REGISTRY:
        assert entry["read_only"] is True
