"""只读 MCP 服务端（Streamable HTTP，stateless JSON，挂载 /mcp）。

作为现有 FastAPI 应用的子挂载运行，供笔记本上的 Hermes 等 MCP 客户端
经公网 HTTPS 调用。P0-A2 起 MCP 与应用内 AI 共用同一注册表与同一受控
执行路径，设计约束：

- 单一注册源（P0-A2）：tools/list 与 tools/call 只认
  app/api/chat_tools.py 的 TOOL_REGISTRY（24 个 WsToolSpec，与应用内 AI
  完全同一注册表，目录以注册表实际内容为准）。每个 MCP 调用必传 mode
  （homeroom|teaching），服务端以 mode + scope 参数重新解析
  WorkspaceContext（绝不信任客户端成员/学科），会话落
  ChatSession(type='mcp')，执行走 execute_session_tool —— 与应用内 AI
  同一作用域解析、同一范围校验、同一结果结构（同范围请求同结果）。
  本模块不 import 数据库模型、不拼 SQL、不复制业务查询，也不绕开既有
  安全边界。
- 旧工具退出主链（P0-A2）：旧 app/chat/tools.py 的 20 工具默认不出现在
  MCP 目录、也无法经 MCP 调用。确需兼容时以 MCP_LEGACY_TOOLS=1 显式
  开启，目录条目显式标注 deprecated（description 前缀 + _meta.deprecated），
  调用仍走旧 execute_tool（独立兼容路径，绝不混入新注册表链路）。
- 只读语义：TOOL_REGISTRY 本身全只读（handler 只做 /api/v1 service 薄
  封装，不触发表写入）；目录与调用只认 TOOL_REGISTRY，其余名称一律
  拒绝（默认含旧工具）。
- 不污染应用内 AI：mode 注入只发生在本模块的 MCP 目录视图里，
  chat_tools.TOOL_REGISTRY 原样不动，发往应用内模型的 schema 与引入
  MCP 前完全一致。
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
- MCP_LEGACY_TOOLS：默认 false。true 时旧 20 工具以 deprecated 兼容入口
  追加进 tools/list（追加在 24 个新工具之后），并可经旧路径调用；false
  （默认）时旧工具不可见也不可调用——绝不作为默认暴露路径。
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


def legacy_tools_enabled() -> bool:
    """旧工具 deprecated 兼容入口开关（默认关闭）。只有显式
    MCP_LEGACY_TOOLS=1 才把旧 20 工具追加进目录并允许旧路径调用。"""
    return os.environ.get("MCP_LEGACY_TOOLS", "").strip().lower() in ("1", "true", "yes", "on")


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


# ────────────────────── 只读目录（由 chat_tools 注册表派生，P0-A2） ──────────────────────

# mode 参数的 JSON Schema（每个 MCP 工具调用必传 mode；tools/list 的
# inputSchema 注入必填 mode 与语义说明）
_WS_MODE_SCHEMA: dict[str, Any] = {
    "type": "string",
    "enum": ["homeroom", "teaching"],
    "description": (
        "必填：数据域。homeroom=班主任行政班视角（全科+总分）；"
        "teaching=任课教师教学班视角（仅任教学科）。成员/学科/班级范围"
        "由服务端解析，客户端不可提交成员名单。"
    ),
}

# 追加到每个工具 description 的 MCP 调用说明（Q09：tools/list 注明 mode
# 必填与 scope 参数语义）
_MCP_DESC_SUFFIX = (
    "（MCP 调用必传 mode=homeroom|teaching；可选 academic_year_id/"
    "class_id/teaching_class_id/subject 由服务端重新解析作用域，"
    "subject 同时作为工具层学科过滤参数）"
)


def ws_tool_catalog() -> list[dict[str, Any]]:
    """MCP 默认工具目录：chat_tools.TOOL_REGISTRY 全量 WsToolSpec 的 MCP
    形状视图（P0-A2 起与应用内 AI 唯一注册表，目录以注册表实际内容为准）。

    - description = 注册表 description 原文 + MCP mode/scope 调用说明；
    - inputSchema = 注册表 schema 深拷贝后注入必填 mode，其余原样；
    - 每次调用都重新读取注册表，注册表变化（含测试 monkeypatch）即时生效。
    """
    from mcp.types import ToolAnnotations

    from app.api.chat_tools import TOOL_REGISTRY

    catalog = []
    for spec in TOOL_REGISTRY:
        schema = json.loads(json.dumps(spec.input_schema))  # 深拷贝注册表条目
        properties = {"mode": dict(_WS_MODE_SCHEMA)}
        properties.update(schema.get("properties") or {})
        schema["properties"] = properties
        schema["required"] = ["mode", *list(schema.get("required") or [])]
        catalog.append(
            {
                "name": spec.name,
                "description": spec.description + _MCP_DESC_SUFFIX,
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


def legacy_tool_catalog() -> list[dict[str, Any]]:
    """旧工具 deprecated 兼容目录（仅 MCP_LEGACY_TOOLS=1 时由
    mcp_tool_catalog 追加）：app/chat/tools.py 旧注册表中 read_only=True
    的条目，显式标注 deprecated（description 前缀 + deprecated 标记，
    list_tools 落为 _meta.deprecated）。"""
    from mcp.types import ToolAnnotations

    from app.chat.tools import readonly_tool_catalog

    catalog = []
    for entry in readonly_tool_catalog():
        catalog.append(
            {
                "name": entry["name"],
                "description": (
                    "[deprecated] " + entry.get("description", "")
                    + "（旧版工具体系兼容入口，仅 MCP_LEGACY_TOOLS=1 时暴露，"
                    "P7 前下线；新集成请使用同目录的 chat_tools 注册表工具）"
                ),
                "inputSchema": entry.get(
                    "input_schema", {"type": "object", "properties": {}}
                ),
                "annotations": ToolAnnotations(
                    read_only_hint=True,
                    destructive_hint=False,
                    idempotent_hint=True,
                    open_world_hint=False,
                ),
                "deprecated": True,
            }
        )
    return catalog


def mcp_tool_catalog() -> list[dict[str, Any]]:
    """tools/list 的完整目录：默认 = ws_tool_catalog()（24 个新注册表
    工具，与应用内 AI 同一注册表）；仅当 MCP_LEGACY_TOOLS=1 时追加
    legacy_tool_catalog()（deprecated 兼容入口，绝不进入默认暴露路径）。"""
    catalog = ws_tool_catalog()
    if legacy_tools_enabled():
        catalog = catalog + legacy_tool_catalog()
    return catalog


def validate_required_tools() -> None:
    """确保 MCP 默认目录与应用内 AI 注册表（chat_tools.TOOL_REGISTRY）
    完全一致（名称与顺序逐项相等，P0-A2 单一注册源）——防止目录生成
    被意外过滤/回退导致 MCP 缺工具或漂移。不一致即 MCPConfigError，
    启动失败 fail closed。"""
    from app.api.chat_tools import TOOL_NAMES

    names = [t["name"] for t in ws_tool_catalog()]
    if names != list(TOOL_NAMES):
        raise MCPConfigError(
            "MCP 工具目录必须与应用内 AI 注册表（chat_tools.TOOL_REGISTRY）"
            "完全一致：目录=" + ", ".join(names)
            + "；注册表=" + ", ".join(TOOL_NAMES)
        )


def build_mcp_server():
    """构建 low-level MCP Server（list/call 均走注册表派生目录）。"""
    from mcp.server import Server
    from mcp.types import (
        CallToolRequestParams,
        CallToolResult,
        ListToolsResult,
        TextContent,
        Tool,
    )

    async def list_tools(ctx, params):
        # tools/list 发布 chat_tools 注册表全量工具（name/description/
        # inputSchema，mode 必填注入），正常 MCP 客户端即可发现并使用与
        # 应用内 AI 完全一致的能力；MCP_LEGACY_TOOLS=1 时追加 deprecated
        # 旧工具（显式标注）。
        tools = [
            Tool(
                name=t["name"],
                description=t["description"],
                input_schema=t["inputSchema"],
                annotations=t["annotations"],
                **({"meta": {"deprecated": True}} if t.get("deprecated") else {}),
            )
            for t in mcp_tool_catalog()
        ]
        return ListToolsResult(tools=tools)

    async def call_tool(ctx, params: CallToolRequestParams) -> CallToolResult:
        from app.api.chat_tools import TOOL_REGISTRY

        # 每次调用重新读取注册表（与目录同一来源，注册表变化即时生效）。
        ws_names = {spec.name for spec in TOOL_REGISTRY}
        # ── 新注册表主链（P0-A2）：chat_tools 注册表工具一律走与应用内
        #    AI 相同的受控路径（mode 必传 → 服务端重新解析 scope → 落
        #    ChatSession(type='mcp') → execute_session_tool，作用域解析/
        #    范围校验/结果结构一致）。──
        if params.name in ws_names:
            return _call_ws_tool(params.name, dict(params.arguments or {}))
        # ── deprecated 兼容入口（仅 MCP_LEGACY_TOOLS=1）：旧工具走旧
        #    execute_tool 独立路径，绝不混入新注册表链路。──
        if legacy_tools_enabled():
            legacy_names = {t["name"] for t in legacy_tool_catalog()}
            if params.name in legacy_names:
                return _call_legacy_tool(params.name, dict(params.arguments or {}))
        # 未列入目录的工具一律不可经 MCP 调用（默认含旧工具与未来新增
        # 的任何非注册表名称）。
        return _error_result(
            {"error": "未知或不可经 MCP 调用的工具: " + params.name}
        )

    return Server(
        "exam-performance-analysis-mcp",
        version="1.0.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def _error_result(payload: dict[str, Any]):
    from mcp.types import CallToolResult, TextContent

    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, default=str))],
        is_error=True,
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
    """构建完整 MCP 挂载（认证 + 传输防护 + 目录一致性校验）。

    任何配置错误（token 缺失/弱、目录与注册表不一致）在此抛出，应用
    启动失败。挂载后规范 URL 为 /mcp/（FastAPI mount 会把 /mcp 以 307
    重定向到 /mcp/）。
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


# ══════════ 主链：chat_tools 注册表工具的受控执行路径（P0-A2） ══════════
#
# MCP 与应用内 AI（app/api/chat.py）共用同一套约定（契约
# docs/contracts/p6-ai-mcp.md §0/§2/§4）：
# - 每个调用必传 mode（缺省 422 invalid_scope_param 语义错误结果）；
# - 服务端以 mode + scope 参数重新解析 WorkspaceContext（绝不信任客户端
#   成员/学科），快照冻结后落 ChatSession(type='mcp')；
# - 执行走 execute_session_tool：域投影校验 + 每轮快照重验（Q02）+ 入参
#   作用域校验 + DomainError/异常兜底转模型可读错误文本，与应用内 AI
#   完全同源（A01 同查询同结果）。
# - 结果体 = 工具结果本身（无 MCP 侧注入键），同范围请求同结果。


def _call_ws_tool(name: str, args: dict[str, Any]):
    """注册表工具调用入口（与应用内 AI 同一受控路径）：mode 必传（缺省
    422 语义错误结果）→ 服务端重新解析 scope → 落 ChatSession(type='mcp')
    → 走 chat_tools 注册表 execute_session_tool 执行。"""
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
        return _error_result(
            {
                "error": "invalid_scope_param",
                "status": 422,
                "detail": "ws 工具调用必须携带 mode（homeroom|teaching）",
            }
        )
    if mode not in VALID_MODES:
        return _error_result(
            {
                "error": "invalid_scope_param",
                "status": 422,
                "detail": "mode 必须是 'homeroom' 或 'teaching'",
            }
        )

    # scope 类参数只用于服务端重新解析作用域；其余参数透传给工具层。
    # subject 双重语义（与应用内 AI 同参同果）：既参与作用域解析
    # （teaching 钉任教学科），又是工具层的学科过滤参数（homeroom 全科
    # 视角按学科收窄输出）——因此同时透传给工具层；academic_year_id/
    # class_id/teaching_class_id 仍为作用域专用（应用内会话锚点语义）。
    scope_keys = ("academic_year_id", "class_id", "teaching_class_id", "subject")
    scope_args = {key: args.get(key) for key in scope_keys}
    tool_args = {key: value for key, value in args.items() if key != "mode" and key not in scope_keys}
    if args.get("subject") is not None:
        tool_args["subject"] = args["subject"]

    db = SessionLocal()
    try:
        try:
            teacher_id = current_teacher_id(db)
            snapshot = resolve_scope_snapshot(db, teacher_id, mode, **scope_args)
        except DomainError as exc:
            status, payload = exc.to_http()
            return _error_result(
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
            return _error_result(
                {
                    "error": "scope_drifted",
                    "detail": "会话范围已变化（关联撤销/版本变化/成员变化），请重新发起调用",
                }
            )
        db.commit()  # 工具全部只读；此处仅归位事务，便于同会话复用连接
        body: dict[str, Any] = dict(result) if isinstance(result, dict) else {
            "result": result
        }
        return CallToolResult(
            content=[
                TextContent(type="text", text=json.dumps(body, ensure_ascii=False, default=str))
            ],
            is_error="error" in body,
        )
    finally:
        db.close()


# ══════════ deprecated 兼容入口：旧工具体系（仅 MCP_LEGACY_TOOLS=1） ══════════
#
# 旧 app/chat/tools.py 20 工具退出 MCP 主链（P0-A2）：默认不暴露、不可
# 调用；显式开启 MCP_LEGACY_TOOLS 后经本独立路径兼容调用（目录条目标注
# deprecated，见 legacy_tool_catalog / list_tools）。旧路径与应用内旧聊天
# 助手（/api/chat）同一 execute_tool 分发，保持既有行为零改动，P7 前下线。


def _call_legacy_tool(name: str, args: dict[str, Any]):
    """旧工具兼容调用：原样透传给 chat/tools.py execute_tool（与 P6 引入
    MCP 前的 MCP 分发完全一致）。"""
    from mcp.types import CallToolResult, TextContent

    from app.chat import tools as chat_tools

    result = chat_tools.execute_tool(name, args)
    if isinstance(result, (str, int, float, bool)):
        text = json.dumps({"result": result}, ensure_ascii=False, default=str)
    else:
        text = json.dumps(result, ensure_ascii=False, default=str)
    return CallToolResult(content=[TextContent(type="text", text=text)])
