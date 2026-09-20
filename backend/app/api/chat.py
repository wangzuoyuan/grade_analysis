"""/api/v1 AI 会话端点（P6，契约 docs/contracts/p6-ai-mcp.md）。

骨架由集成者预建，已由 P6-BE 填充实现：
- §1 会话生命周期（POST/GET/close + SSE messages；快照漂移 409，A02）
- §2 工具注册表（见 chat_tools.py；只读、按会话域投影，薄封装 /api/v1
  service 层，不另写查询逻辑绕过投影门）
- §3 模型配置探测（无 key 409 workspace_not_configured，不半开；流内
  首帧输出错误事件并终止，会话保持可重试）

SSE 帧格式沿用 H 版（app/chat/session.py）的 data JSON 帧循环模式，本
模块独立实现、不 import 旧 session 模块的全局状态：
  {"type":"text","delta"} | {"type":"tool_call",call_id,name,input}
  | {"type":"tool_result",call_id,name,output}
  | {"type":"tool_error",call_id,name,error}
  | {"type":"error","message","code"?} | {"type":"done"}
error 帧可选 code（前端优先认 code，文字匹配仅兜底）：scope_drift /
key_not_configured / provider_failed / tool_rounds_exceeded。

模型调用走异步客户端（AsyncAnthropic / AsyncOpenAI），事件循环不再被
同步请求阻塞；回答上限取 ChatConfig.max_tokens（CHAT_MAX_TOKENS，缺省
16384，Anthropic 接口 max_tokens 必填；OpenAI 分支不传该参数、不设上
限），命中上限截断时在 text 帧末尾追加截断提示。空最终文本不落库，
历史组装时防御过滤空 content 行（旧缺陷毒化行不再发给模型）。

服务端会话历史——messages 端点组装"历史（最近 20 条）
+ 本次输入"请求模型；流正常完成后把本轮 user 消息与 assistant 最终文本
（含 tool_events 摘要 JSON）落 ChatMessage；GET /chat/sessions/{id}/messages
返回历史（刷新恢复不依赖浏览器；漂移/关闭会话历史只读保留）。
多轮工具执行期间每轮工具调用前重验快照，失效即发
type=error 帧（注明范围失效）并收流，旧上下文绝不继续作答。
重验覆盖整轮流——每个 provider 分支在【模型返回后、
输出任何文本/工具帧之前】重验一次（慢生成期间撤销 → 旧范围答复不发布），
_persist_chat_round 之前再验一次（失效则本轮连同 user 消息一并不落历史，
避免半轮上下文成为后续对话依据）。
"""

import asyncio
import json
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api import current_teacher_id, domain_endpoint
from app.api.chat_schemas import (
    ChatCloseResponse,
    ChatHistoryMessage,
    ChatHistoryResponse,
    ChatMessageRequest,
    ChatScopeInfo,
    ChatSessionCreateRequest,
    ChatSessionResponse,
)
from app.api.chat_tools import (
    ScopeDriftError,
    _assert_snapshot_fresh,
    detect_scope_drift,
    dump_scope_snapshot,
    execute_session_tool,
    resolve_scope_snapshot,
    scope_public_view,
    tools_for_domain,
)
from app.chat.config import get_chat_config
from app.chat.tools import (
    create_async_anthropic_client,
    create_async_openai_client,
    to_openai_tools,
)
from app.core.errors import (
    InvalidScopeParam,
    LinkVersionConflict,
    ResourceOutOfScope,
    WorkspaceNotConfigured,
)
from app.db.models import SessionLocal, get_db
from app.db.workspace_models import ChatMessage, ChatSession

router = APIRouter(tags=["chat"])

CHAT_MAX_TOOL_ROUNDS = 8
CHAT_HISTORY_LIMIT = 20  # Q03：组装给模型的历史截断（最近 20 条）
_CHAT_PROVIDER_RETRIES = 3
# Q02/V02 范围失效帧文案（沿用既有文案：工具执行前/模型返回后/落库前共用）
_SCOPE_DRIFT_FRAME = (
    "会话范围已变化（关联撤销/版本变化/成员变化），本轮流已终止，请新建会话"
)


def sse(payload: dict) -> str:
    """H 版帧格式：data JSON 行 + 空行。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _key_name_of(config) -> str:
    return "OPENAI_API_KEY" if config.provider == "openai" else "ANTHROPIC_API_KEY"


def build_system_prompt(snapshot: dict, time_anchor: Optional[dict] = None) -> str:
    """系统提示按域生成（契约 §2）：homeroom 全科班主任视角 / teaching
    单科教师视角；只含范围描述，不含成员明单（按需经工具查询）。

    time_anchor（可选）：{"current_date", "academic_year_name", "term_name",
    "current_grade"}，由 _stream_session_reply 用 db 现查后传入；为 None
    （如旧调用方/锚点现查失败）时不输出锚点行，函数保持可用、规则文本
    不变。current_grade 仅班主任会话可能有（教学会话无行政班恒 None），
    缺失时锚点行不写年级、年级换算规则整条省略，其余时间规则仍写。"""
    if snapshot.get("mode") == "teaching":
        header = (
            "你是高中任课教师工作台的单科成绩分析助手，只服务当前会话冻结的教学班范围。\n"
            f"任教学科：{snapshot.get('subject')}；"
            f"教学班数量：{len(snapshot.get('class_ids') or [])}；"
            f"学生总数（cohort_size）：{snapshot.get('cohort_size')}。\n"
            "你只能看到任教学科的数据：全科成绩、总分、其他班级名册一律不可见，也不得推测。\n"
        )
    else:
        if snapshot.get("link_id") is not None:
            link_part = (
                f"本行政班存在生效关联（关联任教学科：{snapshot.get('subject')}），"
                "关联班的该学科数据经授权投影，仅供参考并注明来源。"
            )
        else:
            link_part = "本行政班暂无生效关联，只有本班全科与总分数据。"
        header = (
            "你是高中班主任工作台的全科成绩分析助手，只服务当前会话冻结的行政班。\n"
            f"班级人数（cohort_size）：{snapshot.get('cohort_size')}。{link_part}\n"
        )
    anchor_line = ""
    if time_anchor:
        grade_part = (
            f"；当前年级：{time_anchor['current_grade']}"
            if time_anchor.get("current_grade")
            else ""
        )
        anchor_line = (
            "\n时间锚点：今天是 {date}；当前学年：{year}；当前学期：{term}{grade}。\n"
        ).format(
            date=time_anchor.get("current_date") or "未知",
            year=time_anchor.get("academic_year_name") or "未知",
            term=time_anchor.get("term_name") or "未分学期",
            grade=grade_part,
        )
    rule_time = (
        "6. 时间语义：\n"
        + "   a) 学年命名约定：2026学年 = 2026 年 9 月开学的 2026-2027 学年"
        "（同理 2025学年 = 2025-2026 学年）。\n"
        + "   b) 相对偏移：用户说“上学年/上学期/上上学年/这学期/第一学期”等相对时间时，"
        "换算成 year_offset/term_offset 传参（0=本学年，-1=上一个，-2=上上一个）；"
        "跨学期的作业区间查询用 get_academic_years 返回的学期起止日期换算 from/to。"
        "目录中的假期条目（暑假/寒假）不参与“上/下学期”偏移换算；"
        "查假期作业时按学期名或 term_id 直达。\n"
    )
    if time_anchor and time_anchor.get("current_grade"):
        grade = time_anchor["current_grade"]
        seq = {"高一": 1, "高二": 2, "高三": 3}.get(grade)

        def _offset_text(value: int) -> str:
            return str(value) if value <= 0 else f"+{value}"

        if seq is not None:
            rule_time += (
                f"   c) 年级→学年换算（当前年级 {grade}）：用户说“高X”时按当前年级换算学年——"
                f"当前是{grade}，则高一=year_offset {_offset_text(1 - seq)}、"
                f"高二=year_offset {_offset_text(2 - seq)}、"
                f"高三=year_offset {_offset_text(3 - seq)}"
                "（如“高一第二学期”这类带年级的学期名：先按届差定学年，"
                "再按学期名在 get_academic_years 目录中匹配）。\n"
            )
    rule_time += (
        "   d) 学期名直接匹配：get_academic_years 返回的学期名形如“2026学年第一学期”，"
        "用户原话即可按名称在目录中匹配，取其起止日期换算 from/to。\n"
    )
    # 第 7 条「自主分析」恒定注入（不依赖 time_anchor）：释放"拿表自算"能力——
    # 没有专门工具的分析问题不拒绝，用既有数据工具拿全量真实数据后自行计算。
    rule_auto = (
        "7. 自主分析：没有专门工具的分析问题（进步/退步对比、排序、分布、两场考试对比等），"
        "先用 get_scores_table / get_exam_students / get_homework_assignments 等"
        "拿全量真实数据，再自行计算分析，绝不以“没有对应工具”为由拒绝回答：\n"
        + "   a) 计算纪律：缺考 null 不当 0（不计入分母与均值）；数字多时先整理成"
        "表格逐步计算并给出关键中间结果，避免心算出错。\n"
        + "   b) 用户没说清哪场考试时，先用 get_exam_list 确认最近一场或列出候选"
        "让用户挑选，绝不瞎猜；考试名支持部分名称模糊匹配（唯一命中自动解析，"
        "多命中会返回候选清单）。\n"
    )
    return (
        header
        + anchor_line
        + "共同规则：\n"
        + "1. 引用任何数字必须先经工具查询，绝不凭空编造；工具报错或越界时如实告知，不得改用猜测值。\n"
        + "2. 学生名单不预置：需要定位学生时先用 search_students 查询 person_id。\n"
        + "3. 只分析当前会话范围内的班级与学生；跨范围请求一律拒绝并说明原因。\n"
        + "4. 缺考为空值（null），绝不转 0；排名只在本班人群内解释。\n"
        + "5. 分析作业与成绩的关系（如“缺交多的学生成绩是否更差”）用 "
        "get_homework_correlation（皮尔逊相关，仅描述统计关联、不构成因果，"
        "回答时必须带上这条口径）。\n"
        + rule_time
        + rule_auto
    )


def _time_anchor_of(db: Session, snapshot: dict) -> Optional[dict]:
    """系统提示时间锚点：现查当前学年/学期/年级名称。学期名读作业学期表
    ws_homework_semester（学期设置页/作业看板同一事实源，is_current 显式
    标记优先，无则取含快照 as_of 的学期；P1 Term 表真实部署无数据且无维护
    入口，弃用）。年级锚点仅班主任会话：快照行政班的 grade 转中文（延续
    班/越界查不到 → None，提示省略年级行）。锚点是提示增强信息，任何失败
    都降级为 None（不影响会话与工具执行）。"""
    try:
        from app.db.workspace_models import (
            AcademicYear,
            AdministrativeClass,
            WsHomeworkSemester,
        )

        ay = db.get(AcademicYear, snapshot.get("academic_year_id"))
        as_of = date.fromisoformat(snapshot["as_of"])
        term = (
            db.query(WsHomeworkSemester)
            .filter(WsHomeworkSemester.is_current == 1)
            .order_by(WsHomeworkSemester.id.desc())
            .first()
        )
        if term is None:
            term = (
                db.query(WsHomeworkSemester)
                .filter(
                    WsHomeworkSemester.start_date <= as_of,
                    WsHomeworkSemester.end_date >= as_of,
                )
                .order_by(WsHomeworkSemester.start_date.asc())
                .first()
            )
        grade_name = None
        if snapshot.get("mode") == "homeroom":
            class_ids = snapshot.get("class_ids") or []
            admin = db.get(AdministrativeClass, class_ids[0]) if class_ids else None
            grade_name = {1: "高一", 2: "高二", 3: "高三"}.get(
                admin.grade if admin is not None else None
            )
        return {
            "current_date": snapshot.get("as_of"),
            "academic_year_name": ay.name if ay is not None else None,
            "term_name": term.name if term is not None else None,
            "current_grade": grade_name,
        }
    except Exception:
        return None


def _load_session(db: Session, session_id: int) -> ChatSession:
    session = db.get(ChatSession, session_id)
    if session is None:
        # 404 不区分"不存在/他运会话"，避免泄露会话存在性
        raise ResourceOutOfScope("chat session not found", details={"session_id": session_id})
    return session


def _session_scope_info(session: ChatSession) -> ChatScopeInfo:
    snapshot = json.loads(session.scope_json or "{}")
    return ChatScopeInfo(**scope_public_view(snapshot))


# ────────────────────── §1 会话生命周期 ──────────────────────


@router.post("/chat/sessions", response_model=ChatSessionResponse)
@domain_endpoint
def create_chat_session(req: ChatSessionCreateRequest, db: Session = Depends(get_db)):
    """创建会话：服务端解析 scope 并冻结入库；无模型 key → 409 不半开。"""
    teacher_id = current_teacher_id(db)
    snapshot = resolve_scope_snapshot(
        db,
        teacher_id,
        req.mode,
        academic_year_id=req.academic_year_id,
        class_id=req.class_id,
        teaching_class_id=req.teaching_class_id,
        subject=req.subject,
    )
    config = get_chat_config()
    if not config.is_configured:
        # 契约 §3：无 key 409 workspace_not_configured，detail 引导配置，
        # 绝不创建半开会话
        raise WorkspaceNotConfigured(
            f"AI 助手尚未配置模型 Key：请在 backend/.env 设置 {_key_name_of(config)}"
            "（按 CHAT_PROVIDER 对应变量）后重试",
            details={"provider": config.provider, "env": _key_name_of(config)},
        )
    session = ChatSession(
        type="chat", scope_json=dump_scope_snapshot(snapshot), status="open"
    )
    db.add(session)
    db.commit()
    return ChatSessionResponse(
        session_id=session.id, scope=ChatScopeInfo(**scope_public_view(snapshot))
    )


@router.get("/chat/sessions/{session_id}", response_model=ChatSessionResponse)
@domain_endpoint
def get_chat_session(session_id: int, db: Session = Depends(get_db)):
    """会话快照核对（对外投影：含 cohort_size，不含成员明单）。"""
    session = _load_session(db, session_id)
    return ChatSessionResponse(
        session_id=session.id, scope=_session_scope_info(session)
    )


@router.post("/chat/sessions/{session_id}/close", response_model=ChatCloseResponse)
@domain_endpoint
def close_chat_session(session_id: int, db: Session = Depends(get_db)):
    """显式关闭（撤销关联后的清理路径）；重复关闭幂等。"""
    session = _load_session(db, session_id)
    if session.status != "closed":
        session.status = "closed"
        session.closed_at = datetime.utcnow()
        db.commit()
    return ChatCloseResponse(session_id=session.id, status=session.status)


@router.post("/chat/sessions/{session_id}/messages")
@domain_endpoint
def post_chat_message(session_id: int, req: ChatMessageRequest, db: Session = Depends(get_db)):
    """发消息 → SSE 流。流前先做快照校验：会话必须 open；服务端重新解析
    当前 scope 与快照逐项比对（mode/学年/班级/学科/关联/成员集合），任一
    漂移 → 409 JSON 而非流（A02：撤销关联/换班后旧会话立即失效）。"""
    session = _load_session(db, session_id)
    if session.status != "open":
        # 契约未定义独立错误码：沿用 409 link_version_conflict 表达
        # "旧上下文不可继续作答"的冲突语义
        raise LinkVersionConflict(
            "会话已关闭，请新建会话",
            details={"session_id": session.id, "status": session.status},
        )
    if not (req.content or "").strip():
        raise InvalidScopeParam(
            "content must be a non-empty string", details={"param": "content"}
        )
    try:
        snapshot = json.loads(session.scope_json or "{}")
    except ValueError:
        raise LinkVersionConflict(
            "会话快照损坏，请新建会话", details={"session_id": session.id}
        )
    drift = detect_scope_drift(db, current_teacher_id(db), snapshot)
    if drift:
        raise LinkVersionConflict(
            "会话作用域已漂移（关联撤销/版本变化/成员变化），旧会话立即失效，请新建会话",
            details={"drift": drift},
        )
    return StreamingResponse(
        _stream_session_reply(session.id, snapshot, req.content.strip()),
        media_type="text/event-stream",
    )


@router.get(
    "/chat/sessions/{session_id}/messages", response_model=ChatHistoryResponse
)
@domain_endpoint
def get_chat_messages(session_id: int, db: Session = Depends(get_db)):
    """会话历史（Q03）：刷新恢复语义——前端以此重建消息列表。open 与
    closed/漂移失效会话都可只读拉取（范围失效只禁止继续作答，历史保留
    只读）；不存在的会话 404 不区分他运会话。"""
    session = _load_session(db, session_id)
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.id.asc())
        .all()
    )
    return ChatHistoryResponse(
        messages=[
            ChatHistoryMessage(
                id=row.id,
                role=row.role,
                content=row.content,
                created_at=row.created_at.isoformat() if row.created_at else "",
            )
            for row in rows
        ]
    )


# ────────────────────── Q03：服务端会话历史 ──────────────────────


def _history_messages(db: Session, session_id: int) -> List[dict]:
    """最近 CHAT_HISTORY_LIMIT 条历史，按时间正序组装为模型 messages
    （user/assistant 纯文本；工具事件以摘要 JSON 附在 assistant 轮）。
    防御过滤：跳过 content 为空（或纯空白）的行——旧缺陷曾把空 assistant
    文本落库，Anthropic 会因空 content 400 拒绝整个请求，毒化行绝不再发。"""
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.desc())
        .limit(CHAT_HISTORY_LIMIT)
        .all()
    )
    return [
        {"role": row.role, "content": row.content}
        for row in reversed(rows)
        if (row.content or "").strip()
    ]


def _persist_chat_round(
    db: Session, session_id: int, user_content: str, final_text: str, tool_events: list
) -> None:
    """流正常完成后落本轮 user 消息与 assistant 最终文本（tool_events
    为摘要 JSON：工具名 + 结果/错误状态）。模型失败/范围中止/轮次超限
    等异常收流不落库——半途轮次不构成可复用的对话上下文。"""
    db.add(ChatMessage(session_id=session_id, role="user", content=user_content))
    db.add(
        ChatMessage(
            session_id=session_id,
            role="assistant",
            content=final_text,
            tool_events_json=json.dumps(tool_events, ensure_ascii=False, default=str),
        )
    )
    db.commit()


# ────────────────────── §3 模型流式生成（H 版帧循环模式） ──────────────────────


async def _stream_session_reply(session_id: int, snapshot: dict, content: str):
    """SSE 生成器：无 key/模型不可用 → 流内首帧错误事件并终止（会话保持）；
    工具执行用独立 SessionLocal（请求级依赖在流式响应期间不保证存活）。
    Q03：模型请求 = 服务端历史（最近 20 条）+ 本次输入；流正常完成后
    本轮 user/assistant 落 ChatMessage（刷新恢复/多轮指代追问的依据）。"""
    config = get_chat_config()
    if not config.is_configured:
        yield sse(
            {
                "type": "error",
                "code": "key_not_configured",
                "message": "模型 Key 未配置或已失效，请配置后重试（会话仍保留）",
            }
        )
        yield sse({"type": "done"})
        return
    tools = tools_for_domain(snapshot["data_domain"])
    db = SessionLocal()
    try:
        # 时间锚点：db 现查当前学年/学期名后传入系统提示（失败降级 None，
        # 不阻断作答）；锚点只在流内现查，绝不写进快照
        system = build_system_prompt(snapshot, _time_anchor_of(db, snapshot))
        history = _history_messages(db, session_id)
        messages = [*history, {"role": "user", "content": content}]
        # sink 由具体流填充：final_text=None 表示本轮未正常完成（不落库）
        sink = {"final_text": None, "tool_events": []}
        if config.provider == "openai":
            frames = _stream_openai_reply(
                config, system, tools, messages, db, snapshot, content, sink
            )
        else:
            frames = _stream_anthropic_reply(
                config, system, tools, messages, db, snapshot, content, sink
            )
        async for frame in frames:
            yield frame
        if sink["final_text"] is None:
            return
        # V02：落库前最后一道重验——文本帧发布后到写历史前关联再被撤销时，
        # 本轮连同 user 消息一并不落历史（半途轮次不构成可复用的对话上下文）；
        # 此分支只在流 done 之后的极窄窗口触发，故只补发失效帧不重复 done
        try:
            _assert_snapshot_fresh(db, snapshot, None)
        except ScopeDriftError:
            yield sse({"type": "error", "code": "scope_drift", "message": _SCOPE_DRIFT_FRAME})
            return
        _persist_chat_round(
            db, session_id, content, sink["final_text"], sink["tool_events"]
        )
    finally:
        db.close()


async def _stream_anthropic_reply(config, system, tools, messages, db, snapshot, content, sink):
    client = create_async_anthropic_client(config)
    chat_messages = list(messages)
    for _ in range(CHAT_MAX_TOOL_ROUNDS):
        # 兼容端点偶发瞬时错误（401/429/5xx/网络抖动），读操作可安全重试
        response = None
        last_exc = None
        for attempt in range(_CHAT_PROVIDER_RETRIES):
            try:
                response = await client.messages.create(
                    model=config.model,
                    max_tokens=config.max_tokens,
                    system=system,
                    messages=chat_messages,
                    tools=tools,
                )
                break
            except Exception as exc:
                last_exc = exc
                if attempt < _CHAT_PROVIDER_RETRIES - 1:
                    await asyncio.sleep(0.4 * (attempt + 1))
        if response is None:
            yield sse(
                {
                    "type": "error",
                    "code": "provider_failed",
                    "message": f"模型调用失败（已重试）：{last_exc}",
                }
            )
            yield sse({"type": "done"})
            return

        # V02：模型返回后、发布任何文本/工具帧之前重验快照——慢生成期间
        # 关联被撤销时，旧范围的答复与工具帧绝不发布（final_text 保持
        # None，本轮连同 user 消息不落历史）
        try:
            _assert_snapshot_fresh(db, snapshot, None)
        except ScopeDriftError:
            yield sse({"type": "error", "code": "scope_drift", "message": _SCOPE_DRIFT_FRAME})
            yield sse({"type": "done"})
            return

        assistant_content: List[dict] = []
        tool_results: List[dict] = []
        final_text_parts: List[str] = []
        for block in response.content:
            if block.type == "text":
                assistant_content.append({"type": "text", "text": block.text})
                final_text_parts.append(block.text)
            elif block.type == "tool_use":
                args = block.input or {}
                call_id = str(block.id)
                assistant_content.append(
                    {"type": "tool_use", "id": block.id, "name": block.name, "input": args}
                )
                yield sse({"type": "tool_call", "call_id": call_id, "name": block.name, "input": args})
                try:
                    result = execute_session_tool(db, snapshot, block.name, args)
                except ScopeDriftError:
                    # Q02：多轮工具执行期间范围失效 → 发范围失效帧并收流，
                    # 旧上下文绝不继续执行后续轮
                    yield sse({"type": "error", "code": "scope_drift", "message": _SCOPE_DRIFT_FRAME})
                    yield sse({"type": "done"})
                    return
                if result.get("error"):
                    sink["tool_events"].append({"name": block.name, "status": "error"})
                    yield sse(
                        {
                            "type": "tool_error",
                            "call_id": call_id,
                            "name": block.name,
                            "error": str(result.get("detail") or result["error"]),
                        }
                    )
                else:
                    sink["tool_events"].append({"name": block.name, "status": "ok"})
                    yield sse(
                        {
                            "type": "tool_result",
                            "call_id": call_id,
                            "name": block.name,
                            "output": result,
                        }
                    )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )
        if tool_results:
            chat_messages.append({"role": "assistant", "content": assistant_content})
            chat_messages.append({"role": "user", "content": tool_results})
            continue

        text = "".join(final_text_parts)
        if getattr(response, "stop_reason", None) == "max_tokens":
            # 截断保险丝：明确告知被截断，而非无声截尾
            text += "\n\n（回答达到长度上限被截断，可继续追问让我接着说。）"
        if text:
            yield sse({"type": "text", "delta": text})
        # Q03：正常完成才落库（异常收流路径 final_text 保持 None）；空白
        # 最终文本视同未完成不落库——空 assistant 行会让下一轮 Anthropic
        # 请求 400（历史毒化），宁可本轮连同 user 消息不进历史
        sink["final_text"] = text if text.strip() else None
        yield sse({"type": "done"})
        return

    yield sse(
        {
            "type": "error",
            "code": "tool_rounds_exceeded",
            "message": "工具调用轮次过多，已停止。请缩小问题范围后重试。",
        }
    )
    yield sse({"type": "done"})


async def _stream_openai_reply(config, system, tools, messages, db, snapshot, content, sink):
    client = create_async_openai_client(config)
    oai_tools = to_openai_tools(tools)
    chat_messages: List[dict] = [{"role": "system", "content": system}, *messages]
    for _ in range(CHAT_MAX_TOOL_ROUNDS):
        # 兼容端点偶发瞬时错误（401/429/5xx/网络抖动），读操作可安全重试
        #（与 Anthropic 分支同款重试；OpenAI 通道不传 max_tokens，不设上限）
        response = None
        last_exc = None
        for attempt in range(_CHAT_PROVIDER_RETRIES):
            try:
                response = await client.chat.completions.create(
                    model=config.model,
                    messages=chat_messages,
                    tools=oai_tools,
                )
                break
            except Exception as exc:
                last_exc = exc
                if attempt < _CHAT_PROVIDER_RETRIES - 1:
                    await asyncio.sleep(0.4 * (attempt + 1))
        if response is None:
            yield sse({"type": "error", "code": "provider_failed", "message": f"模型调用失败：{last_exc}"})
            yield sse({"type": "done"})
            return
        # V02：模型返回后、发布任何文本/工具帧之前重验快照（同 Anthropic
        # 分支——慢生成期间撤销 → 旧答复不发布、本轮不落历史）
        try:
            _assert_snapshot_fresh(db, snapshot, None)
        except ScopeDriftError:
            yield sse({"type": "error", "code": "scope_drift", "message": _SCOPE_DRIFT_FRAME})
            yield sse({"type": "done"})
            return
        choice = response.choices[0]
        msg = choice.message
        tool_calls = getattr(msg, "tool_calls", None) or []
        if tool_calls:
            chat_messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments or "{}",
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except ValueError:
                    args = {}
                call_id = str(tc.id)
                yield sse({"type": "tool_call", "call_id": call_id, "name": tc.function.name, "input": args})
                try:
                    result = execute_session_tool(db, snapshot, tc.function.name, args)
                except ScopeDriftError:
                    # Q02：范围失效帧 + 收流（同 Anthropic 分支）
                    yield sse({"type": "error", "code": "scope_drift", "message": _SCOPE_DRIFT_FRAME})
                    yield sse({"type": "done"})
                    return
                if result.get("error"):
                    sink["tool_events"].append({"name": tc.function.name, "status": "error"})
                    yield sse(
                        {
                            "type": "tool_error",
                            "call_id": call_id,
                            "name": tc.function.name,
                            "error": str(result.get("detail") or result["error"]),
                        }
                    )
                else:
                    sink["tool_events"].append({"name": tc.function.name, "status": "ok"})
                    yield sse(
                        {
                            "type": "tool_result",
                            "call_id": call_id,
                            "name": tc.function.name,
                            "output": result,
                        }
                    )
                chat_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )
            continue

        text = msg.content or ""
        if getattr(choice, "finish_reason", None) == "length":
            # 截断保险丝（同 Anthropic 分支）
            text += "\n\n（回答达到长度上限被截断，可继续追问让我接着说。）"
        if text:
            yield sse({"type": "text", "delta": text})
        # 空白最终文本视同未完成不落库（防历史毒化，同 Anthropic 分支）
        sink["final_text"] = text if text.strip() else None
        yield sse({"type": "done"})
        return

    yield sse(
        {
            "type": "error",
            "code": "tool_rounds_exceeded",
            "message": "工具调用轮次过多，已停止。请缩小问题范围后重试。",
        }
    )
    yield sse({"type": "done"})
