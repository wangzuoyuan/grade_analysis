"""只读 MCP 服务端（Streamable HTTP，stateless JSON，挂载 /mcp）。

作为现有 FastAPI 应用的子挂载运行，供笔记本上的 Hermes 等 MCP 客户端
经公网 HTTPS 调用。班主任版视角：行政班 / 全科 / 总分 / 综合画像 / 作业
与谈话档案。设计约束：

- 单一注册源：tools/list 由 chat/tools.py 的 TOOL_REGISTRY 中带
  read_only=True 元数据的条目派生（旧 20 工具），并追加 P6 ws 四工具
  （chat_tools 注册表的稳定子集，mode 必填，见 §P6 追加段）；tools/call
  一律经 execute_tool() / execute_session_tool() 分发。本模块不 import
  数据库模型、不拼 SQL、不复制业务查询，也不绕开既有安全边界。
- 只读目录语义：未显式标记 read_only 的工具（含未来新增写入/删除工具）
  既不出现在 MCP 目录中，也无法通过 MCP 调用。
- 不污染聊天助手：MCP 元数据只存在于注册表；公开 TOOLS 是投影，
  发往 Anthropic/OpenAI 的 schema 与引入 MCP 前完全一致。
- 命名：本服务端用独立名称 exam-performance-analysis-mcp，不给工具名加
  前缀、不建别名。Hermes 端两个应用（班主任版 homeroom_grade_tracker 与
  任课教师版 grade_tracker）靠连接 key 区分命名空间，那是客户端约定，
  不需要服务端改工具名。
- 认证：独立 Bearer Token（MCP_BEARER_TOKEN），hmac 恒定时间比较，
  401 带 WWW-Authenticate: Bearer。token 只从环境变量读取，绝不写日志、
  绝不出现在任何响应里。
- 传输防护：保留 MCP SDK 的 DNS rebinding / Host / Origin 校验，经
  MCP_ALLOWED_HOSTS / MCP_ALLOWED_ORIGINS 配置（缺省 localhost）。
  非浏览器客户端（Hermes）不带 Origin 头，SDK 语义是放行。
- 公网使用必须 HTTPS：Bearer Token 明文传输只有 TLS 边界保护。

环境变量：
- MCP_ENABLED：默认 false。false 时本模块不被挂载、不导入 mcp SDK，
  应用完全不受影响。
- MCP_BEARER_TOKEN：启用时必填；空白/弱占位符/短于 32 字符 → 启动失败。
- MCP_ALLOWED_HOSTS / MCP_ALLOWED_ORIGINS：逗号分隔；缺省 localhost 系列。
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

MCP_MOUNT_PATH = "/mcp"

# 启用 MCP 时拒绝这些明显是占位符的 token（防止照抄示例导致裸奔）。
_WEAK_TOKEN_PLACEHOLDERS = frozenset({
    "changeme", "change-me", "your_token_here", "your-token-here",
    "yourtoken", "secret", "token", "test", "mcp", "password",
    "123456", "12345678", "1234567890",
    "abcdef", "abcdefg", "abcdefgh", "0123456789", "aaaaaaaaaa",
})


class MCPConfigError(RuntimeError):
    """MCP 配置非法（启用但 token 缺失/弱等）。启动时抛出，fail closed。"""


def mcp_enabled() -> bool:
    return os.environ.get("MCP_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def load_bearer_token() -> str:
    """读取并校验 MCP_BEARER_TOKEN；非法配置抛 MCPConfigError。"""
    token = os.environ.get("MCP_BEARER_TOKEN", "").strip()
    if not token:
        raise MCPConfigError(
            "MCP_ENABLED 已开启，但 MCP_BEARER_TOKEN 缺失或为空白；"
            "拒绝以无认证状态启动。请设置强随机 token（见 DEPLOY.md）。"
        )
    if token.lower() in _WEAK_TOKEN_PLACEHOLDERS:
        raise MCPConfigError(
            "MCP_BEARER_TOKEN 是弱占位符；拒绝以弱 token 启动。"
            "请用 openssl rand -hex 32 生成强随机 token。"
        )
    if len(token) < 32:
        raise MCPConfigError(
            "MCP_BEARER_TOKEN 长度不足 32 字符；拒绝以弱 token 启动。"
            "请用 openssl rand -hex 32 生成强随机 token。"
        )
    return token


def _split_env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_allowed_hosts() -> list[str]:
    """Host 头白名单。缺省 localhost；公网部署必须显式加域名（见 DEPLOY.md）。"""
    configured = _split_env_list("MCP_ALLOWED_HOSTS")
    if configured:
        return configured
    return ["localhost", "127.0.0.1", "[::1]",
            "localhost:*", "127.0.0.1:*", "[::1]:*"]


def load_allowed_origins() -> list[str]:
    """Origin 白名单。缺省 localhost。无 Origin 的非浏览器客户端放行（SDK 语义）。"""
    configured = _split_env_list("MCP_ALLOWED_ORIGINS")
    if configured:
        return configured
    return ["http://localhost", "http://127.0.0.1", "http://[::1]",
            "http://localhost:*", "http://127.0.0.1:*", "http://[::1]:*"]


def _unauthorized() -> JSONResponse:
    # 绝不把 token 或其任何部分放进响应。
    return JSONResponse(
        {"error": "unauthorized", "error_description": "Missing or invalid bearer token"},
        status_code=401,
        headers={"WWW-Authenticate": 'Bearer realm="mcp"'},
    )


class BearerAuthMiddleware:
    """纯 ASGI Bearer 认证中间件：覆盖挂载点内全部路径与方法。

    恒定时间比较；缺失/格式错误/不匹配一律 401。认证先于 SDK 的
    Host/Origin 校验与协议处理，未认证请求不会触达任何工具逻辑。
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token.encode("utf-8")

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            value.strip().encode("utf-8"), self.token
        ):
            logger.warning("MCP request rejected: invalid or missing bearer token")
            await _unauthorized()(scope, receive, send)
            return
        await self.app(scope, receive, send)


# ────────────────────── 只读目录（由单一注册源派生） ──────────────────────

# 班主任版当前注册表中的全部 20 个只读工具（成绩 16 + 作业 3 + 档案 1）。
# 防止注册表意外回退导致 MCP 目录缺工具；新增只读工具不必改这里。
REQUIRED_READONLY_TOOLS: tuple[str, ...] = (
    "list_exams",
    "student_lookup",
    "student_identity_lookup",
    "student_exam_detail",
    "student_trend",
    "student_learning_profile",
    "class_trend",
    "compare_classes",
    "focus_list",
    "subject_weakness",
    "subject_progress_ranking",
    "multi_exam_progress_ranking",
    "band_trend",
    "custom_rank_band_trend",
    "rank_range_filter",
    "rank_frequency_stat",
    "student_homework_summary",
    "class_homework_ranking",
    "homework_grade_correlation",
    "student_notes",
)


def mcp_tool_catalog() -> list[dict[str, Any]]:
    """MCP 工具目录：TOOL_REGISTRY 中 read_only 条目的 MCP 形状视图。

    description / input_schema 原样沿用注册表，不改写。每次调用都重新
    读取注册表，因此注册表变化（含测试 monkeypatch）即时生效。
    """
    from mcp.types import ToolAnnotations

    from app.chat.tools import readonly_tool_catalog

    return [
        {
            "name": entry["name"],
            "description": entry.get("description", ""),
            "inputSchema": entry.get("input_schema", {"type": "object", "properties": {}}),
            "annotations": ToolAnnotations(
                read_only_hint=True,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=False,
            ),
        }
        for entry in readonly_tool_catalog()
    ]


def validate_required_tools() -> None:
    """确保班主任版必须暴露的 20 个只读工具都在目录中（防止注册表意外回退）。"""
    names = {t["name"] for t in mcp_tool_catalog()}
    missing = [n for n in REQUIRED_READONLY_TOOLS if n not in names]
    if missing:
        raise MCPConfigError(
            "MCP 工具目录缺少必须暴露的只读工具: " + ", ".join(missing)
        )


def build_mcp_server():
    """构建 low-level MCP Server（list/call 均走注册表与 execute_tool）。"""
    from mcp.server import Server
    from mcp.types import (
        CallToolRequestParams,
        CallToolResult,
        ListToolsResult,
        TextContent,
        Tool,
    )

    from app.chat import tools as chat_tools

    async def list_tools(ctx, params):
        # tools/list 必须发布 P6 ws 四工具（完整
        # name/description/inputSchema，mode 必填），正常 MCP 客户端才能
        # 发现并使用合并版能力；旧 20 工具的目录形状零改动。
        tools = [
            Tool(
                name=t["name"],
                description=t["description"],
                input_schema=t["inputSchema"],
                annotations=t["annotations"],
            )
            for t in mcp_tool_catalog() + p6_ws_mcp_catalog()
        ]
        return ListToolsResult(tools=tools)

    async def call_tool(ctx, params: CallToolRequestParams) -> CallToolResult:
        # ── P6 追加分支（契约 docs/contracts/p6-ai-mcp.md §4）：ws 域工具
        #    走 chat_tools 同一注册表（mode 必传，服务端重新解析 scope，
        #    会话落 ChatSession(type='mcp')）；本分支之下的既有只读目录
        #    分发逻辑零改动。──
        if params.name in P6_WS_TOOL_NAMES:
            return _p6_call_ws_tool(params.name, dict(params.arguments or {}))
        allowed = {t["name"] for t in mcp_tool_catalog()}
        if params.name not in allowed:
            # 未列入只读目录的工具一律不可经 MCP 调用（含未来新增写工具）。
            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text=json.dumps(
                        {"error": "未知或不可经 MCP 调用的工具: " + params.name},
                        ensure_ascii=False,
                    ),
                )],
                is_error=True,
            )
        # 参数原样透传给 execute_tool —— MCP 层不改写、不过滤。
        args = dict(params.arguments or {})
        result = chat_tools.execute_tool(params.name, args)
        if isinstance(result, (str, int, float, bool)):
            text = json.dumps({"result": result}, ensure_ascii=False, default=str)
        else:
            text = json.dumps(result, ensure_ascii=False, default=str)
        return CallToolResult(content=[TextContent(type="text", text=text)])

    return Server(
        "exam-performance-analysis-mcp",
        version="1.0.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


class MCPMount:
    """挂载结果：ASGI 子应用 + 需要宿主 lifespan 管理的 session manager。"""

    def __init__(self, app: ASGIApp, server) -> None:
        self.app = app
        self.server = server

    def session_manager(self):
        # streamable_http_app() 调用后 server.session_manager 才存在
        return self.server.session_manager


def mount_mcp() -> MCPMount:
    """构建完整 MCP 挂载（认证 + 传输防护 + 只读目录校验）。

    任何配置错误（token 缺失/弱、必须工具缺失）在此抛出，应用启动失败。
    挂载后规范 URL 为 /mcp/（FastAPI mount 会把 /mcp 以 307 重定向到 /mcp/）。
    """
    from mcp.server.transport_security import TransportSecuritySettings

    token = load_bearer_token()
    validate_required_tools()
    server = build_mcp_server()
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=load_allowed_hosts(),
        allowed_origins=load_allowed_origins(),
    )
    inner = server.streamable_http_app(
        streamable_http_path="/",   # 挂载前缀即完整路径 /mcp
        json_response=True,        # 每个 POST 回单个 JSON（客户端友好）
        stateless_http=True,       # 无会话；不落任何会话状态
        transport_security=security,
    )
    return MCPMount(BearerAuthMiddleware(inner, token), server)


# ══════════ P6 追加：ws 域只读工具（契约 docs/contracts/p6-ai-mcp.md §4/§0.1） ══════════
#
# 与既有 20 个旧表只读工具的关系：
# - 不动旧工具、旧 20 工具的目录条目零改动；ws 四工具经 tools/list 发布
#   （Q09：正常 MCP 客户端必须能发现），追加在旧目录之后。
# - ws 工具清单 = app/api/chat_tools.py 注册表的稳定子集（契约 §4 首版
#   四个）；每个调用必传 mode（缺省 422 invalid_scope_param 语义）。
# - 服务端以 mode + scope 参数重新解析 WorkspaceContext（绝不信任客户端
#   成员/学科），会话落 ChatSession(type='mcp')，执行走 execute_session_tool
#   与 AI 聊天完全同源（A01 同查询同结果）。

# MCP 稳定子集（契约 §4 首版；写操作工具一律不注册，域投影在
# execute_session_tool 内仍按会话域裁剪）
P6_WS_TOOL_NAMES: tuple[str, ...] = (
    "search_students",
    "get_student_profile",
    "get_exam_stats",
    "get_scores_table",
)

# mode 参数的 JSON Schema（Q09：tools/list 必须注明 mode 必填与语义）
_WS_MODE_SCHEMA: dict[str, Any] = {
    "type": "string",
    "enum": ["homeroom", "teaching"],
    "description": (
        "必填：数据域。homeroom=班主任行政班视角（全科+总分）；"
        "teaching=任课教师教学班视角（仅任教学科）。成员/学科/班级范围"
        "由服务端解析，客户端不可提交成员名单。"
    ),
}


def p6_ws_mcp_catalog() -> list[dict[str, Any]]:
    """tools/list 用的 ws 工具目录（Q09）：chat_tools 注册表条目 + 必填
    mode 注入 inputSchema、调用说明并入 description。返回形状与
    mcp_tool_catalog() 一致（name/description/inputSchema/annotations）。"""
    from mcp.types import ToolAnnotations

    catalog = []
    for entry in p6_ws_tool_catalog():
        schema = json.loads(json.dumps(entry["input_schema"]))  # 深拷贝注册表条目
        properties = {"mode": dict(_WS_MODE_SCHEMA)}
        properties.update(schema.get("properties") or {})
        schema["properties"] = properties
        schema["required"] = ["mode", *list(schema.get("required") or [])]
        catalog.append(
            {
                "name": entry["name"],
                "description": (
                    entry["description"]
                    + "（MCP 调用必传 mode=homeroom|teaching；可选 academic_year_id/"
                    "class_id/teaching_class_id/subject 由服务端重新解析作用域）"
                ),
                "inputSchema": schema,
                "annotations": ToolAnnotations(
                    read_only_hint=True,
                    destructive_hint=False,
                    idempotent_hint=True,
                    open_world_hint=False,
                ),
            }
        )
    return catalog


def p6_ws_tool_catalog() -> list[dict[str, Any]]:
    """P6 ws 工具目录（供调用侧/测试发现；注册表缺失即配置错误 fail closed）。"""
    from app.api.chat_tools import TOOL_REGISTRY as WS_REGISTRY

    by_name = {spec.name: spec for spec in WS_REGISTRY}
    catalog = []
    for name in P6_WS_TOOL_NAMES:
        spec = by_name.get(name)
        if spec is None:
            raise MCPConfigError(f"P6 ws 工具在 chat_tools 注册表中缺失: {name}")
        catalog.append(
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "domains": list(spec.domains),
            }
        )
    return catalog


def _p6_ws_error_result(payload: dict[str, Any]):
    from mcp.types import CallToolResult, TextContent

    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, default=str))],
        is_error=True,
    )


def _p6_call_ws_tool(name: str, args: dict[str, Any]):
    """ws 工具调用入口：mode 必传（缺省 422 语义错误结果）→ 服务端重新
    解析 scope → 落 ChatSession(type='mcp') → 走 chat_tools 注册表执行。"""
    from mcp.types import CallToolResult, TextContent

    from app.api import current_teacher_id
    from app.api.chat_tools import (
        ScopeDriftError,
        execute_session_tool,
        resolve_scope_snapshot,
    )
    from app.core.context import VALID_MODES
    from app.core.errors import DomainError
    from app.db.models import SessionLocal
    from app.db.workspace_models import ChatSession

    mode = args.get("mode")
    if not mode:
        return _p6_ws_error_result(
            {
                "error": "invalid_scope_param",
                "status": 422,
                "detail": "ws 工具调用必须携带 mode（homeroom|teaching）",
            }
        )
    if mode not in VALID_MODES:
        return _p6_ws_error_result(
            {
                "error": "invalid_scope_param",
                "status": 422,
                "detail": "mode 必须是 'homeroom' 或 'teaching'",
            }
        )

    # scope 类参数只用于服务端重新解析作用域；其余参数透传给工具层
    scope_keys = ("academic_year_id", "class_id", "teaching_class_id", "subject")
    scope_args = {key: args.get(key) for key in scope_keys}
    tool_args = {key: value for key, value in args.items() if key != "mode" and key not in scope_keys}

    db = SessionLocal()
    try:
        try:
            teacher_id = current_teacher_id(db)
            snapshot = resolve_scope_snapshot(db, teacher_id, mode, **scope_args)
        except DomainError as exc:
            status, payload = exc.to_http()
            return _p6_ws_error_result(
                {"error": exc.code, "status": status, **payload}
            )
        session = ChatSession(
            type="mcp",
            scope_json=json.dumps(snapshot, ensure_ascii=False),
            status="open",
        )
        db.add(session)
        db.commit()
        try:
            result = execute_session_tool(db, snapshot, name, tool_args, teacher_id)
        except ScopeDriftError:
            # Q02：MCP 单次调用路径同样受每轮重验保护（本入口刚解析的
            # 快照正常不会漂移；防御并发状态竞态，统一转范围失效错误结果）
            return _p6_ws_error_result(
                {
                    "error": "scope_drifted",
                    "detail": "会话范围已变化（关联撤销/版本变化/成员变化），请重新发起调用",
                }
            )
        db.commit()  # 工具全部只读；此处仅归位事务，便于同会话复用连接
        body: dict[str, Any] = {"session_id": session.id}
        if isinstance(result, dict):
            body.update(result)
        else:
            body["result"] = result
        return CallToolResult(
            content=[
                TextContent(type="text", text=json.dumps(body, ensure_ascii=False, default=str))
            ],
            is_error=isinstance(result, dict) and "error" in result,
        )
    finally:
        db.close()
