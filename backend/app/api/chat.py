"""/api/v1 AI 会话端点（P6，契约 docs/contracts/p6-ai-mcp.md）。

骨架由集成者预建，已由 P6-BE 填充实现：
- §1 会话生命周期（POST/GET/close + SSE messages；快照漂移 409，A02）
- §2 工具注册表（见 chat_tools.py；只读、按会话域投影，薄封装 /api/v1
  service 层，不另写查询逻辑绕过投影门）
- §3 模型配置探测（无 key 409 workspace_not_configured，不半开；流内
  首帧输出错误事件并终止，会话保持可重试）

SSE 帧格式沿用旧 H 版（POST /api/chat，该端点已于 2026-10-23 下线）的
data JSON 帧循环模式，本模块独立实现：
  {"type":"text","delta"} | {"type":"tool_call",call_id,name,input}
  | {"type":"tool_result",call_id,name,output}
  | {"type":"tool_error",call_id,name,error}
  | {"type":"error","message","code"?} | {"type":"done"}
  | {"type":"ping"}（2026-11-15 心跳帧：等模型事件期间周期发送，防公网
    入口反代按空闲超时掐线；前端对未知类型静默忽略，不落历史）
Anthropic 分支 2026-11-15（方案二）起 stream=True 真流式：text 增量逐帧
转发、tool_use 流内拼装即时执行；网关不支持流式或流中断且未发布 → 自动
回退非流式路径（本文件 `_stream_anthropic_reply` 头注）。
error 帧可选 code（前端优先认 code，文字匹配仅兜底）：scope_drift /
key_not_configured / provider_failed / tool_rounds_exceeded。
同工具+同参数第 3 次起不再执行、改喂纠正性错误结果（重复取数止损）。

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
import time
from contextlib import suppress
from datetime import date, datetime
from typing import Any, Dict, List, Optional

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
# 2026-11-15 连接中断修复：等待模型完整响应期间 SSE 链路完全静默（非流式
# 调用 + 多轮工具循环），公网入口反向代理按空闲超时掐线（当日 21:34/21:36
# 两次，Mac caddy "reading: context canceled"）。每 _CHAT_PING_INTERVAL 秒
# 发一帧 ping 心跳保持链路活跃；前端对未知帧类型静默忽略，零前端改动。
_CHAT_PING_INTERVAL = 10.0
# 同工具+同参数重复调用止损：第 _CHAT_DUPE_CALL_LIMIT 次起不再执行，改喂
# 纠正性错误结果（当日故障里模型对 get_homework_warnings 连调 14 次，多为
# 重复取数烧轮次）；同参数重复取数结果不会变化，拦截不损失信息。
_CHAT_DUPE_CALL_LIMIT = 3
_DUPE_CALL_MESSAGE = (
    "同一工具与相同参数已连续调用多次，结果不会变化。"
    "请直接基于已有工具结果作答，或改用不同参数/工具。"
)
# Q02/V02 范围失效帧文案（沿用既有文案：工具执行前/模型返回后/落库前共用）
_SCOPE_DRIFT_FRAME = (
    "会话范围已变化（关联撤销/版本变化/成员变化），本轮流已终止，请新建会话"
)


def sse(payload: dict) -> str:
    """H 版帧格式：data JSON 行 + 空行。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _model_call_with_pings(factory, box: Dict[str, Any]):
    """带心跳与取消清理的模型调用（2026-11-15 连接中断修复）。

    非流式模型调用要等完整响应，期间对客户端零输出——多轮工具循环下静默
    窗口可达分钟级，公网入口反代会按空闲掐线。本包装在等待期间每
    _CHAT_PING_INTERVAL 秒 yield 一帧 {"type":"ping"}（前端忽略未知类型）。

    factory：每次尝试返回新协程对象（重试不可复用已消费协程）；沿用既有
    _CHAT_PROVIDER_RETRIES 次重试 + 0.4*n 退避语义。结果写入调用方传入的
    box：box["result"]=response 成功 / box["error"]=last_exc 重试耗尽；
    两者皆缺 = 在途被取消（客户端已断开），调用方应静默收束。

    生成器被关闭/取消（客户端断开时 Starlette 会 aclose 整条生成器链）时，
    在途模型调用立即 cancel 并等待收尾——浏览器已断线就不再白烧轮次；
    GeneratorExit/CancelledError 原样上抛，绝不吞取消信号。
    """
    last_exc: Optional[Exception] = None
    for attempt in range(_CHAT_PROVIDER_RETRIES):
        task: Optional[asyncio.Task] = None
        try:
            task = asyncio.ensure_future(factory())
        except Exception as exc:  # factory 同步抛错与 await 抛错同路重试
            last_exc = exc
        if task is not None:
            try:
                while True:
                    done, _ = await asyncio.wait({task}, timeout=_CHAT_PING_INTERVAL)
                    if done:
                        break
                    yield sse({"type": "ping"})
            except BaseException:
                # 本生成器被关闭/取消（典型：客户端断开 → 消费方 aclose）：
                # 取消在途调用后原样上抛，让取消语义沿生成器链继续传播
                if not task.done():
                    task.cancel()
                    with suppress(BaseException):
                        await task
                raise
            if task.cancelled():  # 防御：仅外都 cancel 才会到（上面已上抛）
                return
            exc = task.exception()
            if exc is None:
                box["result"] = task.result()
                return
            last_exc = exc
        if attempt < _CHAT_PROVIDER_RETRIES - 1:
            await asyncio.sleep(0.4 * (attempt + 1))
    box["error"] = last_exc
    return


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
    rule_economy = (
        "8. 工具用量（2026-11-15）：全班或多学生的汇总问题（缺交/预警/分布等）"
        "优先一次调用覆盖全班的汇总类工具（get_homework_warnings / "
        "get_exam_students / get_diagnosis_summary 等），按学科细分用 subject / "
        "homework_type 参数或在汇总结果上自行分组，**不要**对每个学生逐一调用"
        "单人工具（get_homework_student / get_homework_student_subjects）——逐人"
        "批量调用会耗尽回答长度上限，导致最终回答被截断。\n"
    )
    return (
        header
        + anchor_line
        + "共同规则：\n"
        + "1. 引用任何数字必须先经工具查询，绝不凭空编造；工具报错或越界时如实告知，不得改用猜测值。\n"
        + "2. 学生名单不预置：需要定位学生时先用 search_students 查询 person_id。\n"
        + "3. 只分析当前会话范围内的班级与学生；跨范围请求一律拒绝并说明原因。\n"
        + "4. 数字口径：\n"
        "   a) 缺考为空值（null），绝不转 0；排名只在本班人群内解释。\n"
        "   b) 年级百分位（percentile / grade_percentile，0–1）=「年级前百分之几」，越小越靠前，不得当作越大越好；需要「越大越好」的口径用 100 − 百分位×100 换算。\n"
        "   c) 名次口径（2026-10-24 起）：学籍=1（闵中学籍）的学生名次取「学籍排名」（缺时用年级排名补），学籍非1（借读类）只取「年级排名」，学籍未采集取「学籍排名」；名次缺失显示空值。排名分布/排名频次/名次区间/段位分布按此名次统计全部学生，回答名次类数字时带上这条口径。\n"
        "   d) 班级均分表（get_class_averages）里选科均分为 0 表示该班无人选该科，不是真实分数，不得计入平均、比较或当最低分；total_ranks 是班型内排名（平行班/实验班分组各排），不是全年级名次。\n"
        + "5. 分析作业与成绩的关系（如“缺交多的学生成绩是否更差”）用 "
        "get_homework_correlation（皮尔逊相关，仅描述统计关联、不构成因果，"
        "回答时必须带上这条口径）。\n"
        "问某个学生时先查person_id并传给get_homework_correlation，使用个人多次考试分析；"
        "高一年级百分位、高二高三等级分，不能用班级相关系数推断个人。\n"
        + rule_time
        + rule_auto
        + rule_economy
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
        try:
            async for frame in frames:
                yield frame
        finally:
            # 客户端断开时 Starlette 只 aclose 本生成器；async-for 不会自动
            # 关闭被迭代生成器，须显式传导（分支内再到 _model_call_with_pings，
            # 取消在途模型调用，止损白烧）
            await frames.aclose()
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
    """Anthropic 分支（2026-11-15 方案二起真流式）。

    每轮优先 stream=True 真流式：text 增量边生成边转发（打字机效果）、
    tool_use 块流内拼装参数并在 content_block_stop 即时执行；事件等待期间
    继续发 ping 心跳。网关不支持流式（create 返回完整响应对象）或流中途
    失败且本轮尚未发布任何帧 → 自动回退到 v1.0.16 非流式路径（语义不变，
    既有测试替身天然走回退路径）；已发布后中断 → provider_failed 收流，
    半途轮次不落库。范围校验两道闸：轮前重验（真流式文本边生成边发布，
    2026-11-15 用户拍板豁免「生成中途漂移」——成员/关联不会在提问进行中
    变化）+ 落库前终验（_stream_session_reply）；回退路径另保留模型返回后
    的发布前重验（V02 原语义）。
    """
    client = create_async_anthropic_client(config)
    chat_messages = list(messages)
    call_counts: Dict[str, int] = {}
    all_text_parts: List[str] = []  # 已随流发布的文本（流式路径跨轮累积，落库用）

    def _run_tool(name: str, call_id: str, block_id, args: dict, tool_results: List[dict]):
        """执行单个 tool_use 并产出待发帧（去重止损→执行→结果/错误帧）。
        返回 (frames, drifted)：drifted=True 表示范围失效（帧含失效帧+done，
        调用方立即收流）。"""
        frames: List[str] = [
            sse({"type": "tool_call", "call_id": call_id, "name": name, "input": args})
        ]
        dupe_key = f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)}"
        dupe_count = call_counts.get(dupe_key, 0) + 1
        call_counts[dupe_key] = dupe_count
        if dupe_count >= _CHAT_DUPE_CALL_LIMIT:
            # 重复调用止损：不执行，喂纠正性错误结果让模型改道/收束
            sink["tool_events"].append({"name": name, "status": "error"})
            frames.append(
                sse(
                    {
                        "type": "tool_error",
                        "call_id": call_id,
                        "name": name,
                        "error": _DUPE_CALL_MESSAGE,
                    }
                )
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block_id,
                    "content": _DUPE_CALL_MESSAGE,
                    "is_error": True,
                }
            )
            return frames, False
        try:
            result = execute_session_tool(db, snapshot, name, args)
        except ScopeDriftError:
            # Q02：工具执行期间范围失效 → 失效帧 + 收流，旧上下文绝不继续
            return (
                [
                    sse({"type": "error", "code": "scope_drift", "message": _SCOPE_DRIFT_FRAME}),
                    sse({"type": "done"}),
                ],
                True,
            )
        if result.get("error"):
            sink["tool_events"].append({"name": name, "status": "error"})
            frames.append(
                sse(
                    {
                        "type": "tool_error",
                        "call_id": call_id,
                        "name": name,
                        "error": str(result.get("detail") or result["error"]),
                    }
                )
            )
        else:
            sink["tool_events"].append({"name": name, "status": "ok"})
            frames.append(
                sse({"type": "tool_result", "call_id": call_id, "name": name, "output": result})
            )
        tool_results.append(
            {
                "type": "tool_result",
                "tool_use_id": block_id,
                "content": json.dumps(result, ensure_ascii=False, default=str),
            }
        )
        return frames, False

    for _ in range(CHAT_MAX_TOOL_ROUNDS):
        # 轮前重验（两路径共用；真流式下文本边生成边发布，这里是发布前的
        # 主闸——漂移即收流，本轮不发起模型调用）
        try:
            _assert_snapshot_fresh(db, snapshot, None)
        except ScopeDriftError:
            yield sse({"type": "error", "code": "scope_drift", "message": _SCOPE_DRIFT_FRAME})
            yield sse({"type": "done"})
            return

        # 模型调用（create 本身经心跳包装重试；stream=True 由网关支持时
        # 返回异步事件流，否则返回完整响应对象 → 回退路径）
        box: Dict[str, Any] = {}
        agen = _model_call_with_pings(
            lambda: client.messages.create(
                model=config.model,
                max_tokens=config.max_tokens,
                system=system,
                messages=chat_messages,
                tools=tools,
                stream=True,
            ),
            box,
        )
        try:
            async for ping_frame in agen:
                yield ping_frame
        finally:
            await agen.aclose()
        if "result" not in box:
            if "error" in box:
                yield sse(
                    {
                        "type": "error",
                        "code": "provider_failed",
                        "message": f"模型调用失败（已重试）：{box['error']}",
                    }
                )
                yield sse({"type": "done"})
            return
        stream_obj = box["result"]

        assistant_content: List[dict] = []
        tool_results: List[dict] = []
        final_text_parts: List[str] = []
        stop_reason: Optional[str] = None

        if hasattr(stream_obj, "__aiter__"):
            # ── 真流式路径：事件消费（ping 交织 + 流内工具执行）──
            block_text: Dict[int, str] = {}
            block_tool: Dict[int, dict] = {}
            published = False  # 本轮是否已发过任何帧（决定中断后可否回退）
            pending: Optional[asyncio.Task] = None
            stream_error: Optional[Exception] = None
            try:
                event_iter = stream_obj.__aiter__()
                # 心跳以「客户端多久没收到字节」为准，而非「多久没收到模型
                # 事件」——智谱网关在生成期间持续发 keepalive 事件（每条都会
                # 重置 asyncio.wait 的计时器），事件视角永不超时，而客户端
                # 侧是纯静默（2026-11-15 线上实测：4520 次 wait 零 timeout，
                # 公网 60s 被掐）。凡本轮事件未产生任何客户端帧且距上次
                # 下发 ≥ _CHAT_PING_INTERVAL，即补一帧 ping。
                last_forward = time.monotonic()
                while True:
                    if pending is None:
                        pending = asyncio.ensure_future(event_iter.__anext__())
                    done, _ = await asyncio.wait({pending}, timeout=_CHAT_PING_INTERVAL)
                    if not done:
                        yield sse({"type": "ping"})
                        last_forward = time.monotonic()
                        continue
                    task, pending = pending, None
                    try:
                        event = task.result()
                    except StopAsyncIteration:
                        break
                    forwarded = False
                    etype = getattr(event, "type", "")
                    if etype == "content_block_start":
                        cbt = getattr(event.content_block, "type", "")
                        if cbt == "tool_use":
                            block_tool[event.index] = {
                                "id": event.content_block.id,
                                "name": event.content_block.name,
                                "json": "",
                            }
                        elif cbt == "text":
                            block_text[event.index] = ""
                        # 其他块类型（thinking 等）：不转发，聚合时跳过
                    elif etype == "content_block_delta":
                        dtype = getattr(event.delta, "type", "")
                        if dtype == "text_delta" and event.index in block_text:
                            block_text[event.index] += event.delta.text
                            published = True
                            forwarded = True
                            yield sse({"type": "text", "delta": event.delta.text})
                        elif dtype == "input_json_delta" and event.index in block_tool:
                            block_tool[event.index]["json"] += event.delta.partial_json
                    elif etype == "content_block_stop":
                        if event.index in block_tool:
                            spec = block_tool[event.index]
                            try:
                                args = json.loads(spec["json"] or "{}")
                            except ValueError:
                                args = {}
                            spec["args"] = args
                            published = True
                            forwarded = True
                            frames, drifted = _run_tool(
                                spec["name"], str(spec["id"]), spec["id"], args, tool_results
                            )
                            for frame in frames:
                                yield frame
                            if drifted:
                                return  # 失效帧+done 已在 frames 里发出
                    elif etype == "message_delta":
                        stop_reason = getattr(event.delta, "stop_reason", None)
                    elif etype == "error":
                        raise RuntimeError(f"模型流内错误：{getattr(event, 'error', event)}")
                    # message_start / ping / message_stop：不产生客户端帧
                    if forwarded:
                        last_forward = time.monotonic()
                    elif time.monotonic() - last_forward >= _CHAT_PING_INTERVAL:
                        # 网关 keepalive 事件流：客户端侧已静默 ≥ 心跳间隔
                        yield sse({"type": "ping"})
                        last_forward = time.monotonic()
            except Exception as exc:
                stream_error = exc
            finally:
                if pending is not None and not pending.done():
                    pending.cancel()
                if pending is not None:
                    with suppress(BaseException):
                        await pending
                with suppress(BaseException):
                    await stream_obj.close()
            if stream_error is not None:
                if published:
                    # 已发布过内容，无法安全重试/回退（会造成重复文本）：
                    # 中断收流，半途轮次不落库
                    yield sse(
                        {
                            "type": "error",
                            "code": "provider_failed",
                            "message": f"模型流式中断：{stream_error}",
                        }
                    )
                    yield sse({"type": "done"})
                    return
                # 未发布任何帧 → 回退到非流式路径重取本轮
                fallback_box: Dict[str, Any] = {}
                fagen = _model_call_with_pings(
                    lambda: client.messages.create(
                        model=config.model,
                        max_tokens=config.max_tokens,
                        system=system,
                        messages=chat_messages,
                        tools=tools,
                    ),
                    fallback_box,
                )
                try:
                    async for ping_frame in fagen:
                        yield ping_frame
                finally:
                    await fagen.aclose()
                if "result" not in fallback_box:
                    if "error" in fallback_box:
                        yield sse(
                            {
                                "type": "error",
                                "code": "provider_failed",
                                "message": f"模型调用失败（已重试）：{fallback_box['error']}",
                            }
                        )
                        yield sse({"type": "done"})
                    return
                response = fallback_box["result"]
            else:
                # 流式轮正常结束：按块序聚合 assistant_content（下一轮请求体）
                for idx in sorted(set(block_text) | set(block_tool)):
                    if idx in block_tool:
                        spec = block_tool[idx]
                        assistant_content.append(
                            {
                                "type": "tool_use",
                                "id": spec["id"],
                                "name": spec["name"],
                                "input": spec["args"],
                            }
                        )
                    elif block_text[idx].strip():
                        # 空文本块不入请求体（空 text 块会被 Anthropic 400 拒绝）
                        assistant_content.append({"type": "text", "text": block_text[idx]})
                round_text = "".join(block_text[i] for i in sorted(block_text))
                all_text_parts.append(round_text)
                if tool_results:
                    chat_messages.append({"role": "assistant", "content": assistant_content})
                    chat_messages.append({"role": "user", "content": tool_results})
                    continue
                # 最终轮：文本已随流发布，截断提示补发增量后收流
                text = "".join(all_text_parts)
                if stop_reason == "max_tokens":
                    # 截断保险丝：明确告知被截断，而非无声截尾
                    hint = "\n\n（回答达到长度上限被截断，可继续追问让我接着说。）"
                    text += hint
                    yield sse({"type": "text", "delta": hint})
                sink["final_text"] = text if text.strip() else None
                yield sse({"type": "done"})
                return
        else:
            # ── 回退路径：完整响应（网关忽略 stream=True / 旧测试替身）──
            response = stream_obj

        # 回退路径的块处理（v1.0.16 语义：V02 发布前重验 + 文本轮末一次性发）
        try:
            _assert_snapshot_fresh(db, snapshot, None)
        except ScopeDriftError:
            yield sse({"type": "error", "code": "scope_drift", "message": _SCOPE_DRIFT_FRAME})
            yield sse({"type": "done"})
            return
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
                frames, drifted = _run_tool(block.name, call_id, block.id, args, tool_results)
                for frame in frames:
                    yield frame
                if drifted:
                    return
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
        # 请求 400（历史毒化），宁可本轮连同 user 消息不进历史。混流回退
        # （此前轮已流式发布过文本）时把已发布部分并入落库文本
        full_text = "".join(all_text_parts) + text
        sink["final_text"] = full_text if full_text.strip() else None
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
    call_counts: Dict[str, int] = {}
    for _ in range(CHAT_MAX_TOOL_ROUNDS):
        # 兼容端点偶发瞬时错误（401/429/5xx/网络抖动），读操作可安全重试
        #（与 Anthropic 分支同款重试；OpenAI 通道不传 max_tokens，不设上限）；
        # 等待期间周期发 ping 心跳，客户端断开时在途调用即被取消
        box: Dict[str, Any] = {}
        agen = _model_call_with_pings(
            lambda: client.chat.completions.create(
                model=config.model,
                messages=chat_messages,
                tools=oai_tools,
            ),
            box,
        )
        try:
            async for ping_frame in agen:
                yield ping_frame
        finally:
            await agen.aclose()
        if "result" not in box:
            if "error" in box:
                yield sse({"type": "error", "code": "provider_failed", "message": f"模型调用失败：{box['error']}"})
                yield sse({"type": "done"})
            return
        response = box["result"]
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
                dupe_key = f"{tc.function.name}:{json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)}"
                dupe_count = call_counts.get(dupe_key, 0) + 1
                call_counts[dupe_key] = dupe_count
                if dupe_count >= _CHAT_DUPE_CALL_LIMIT:
                    # 重复调用止损（同 Anthropic 分支）：不执行，喂纠正性错误结果
                    sink["tool_events"].append({"name": tc.function.name, "status": "error"})
                    yield sse(
                        {
                            "type": "tool_error",
                            "call_id": call_id,
                            "name": tc.function.name,
                            "error": _DUPE_CALL_MESSAGE,
                        }
                    )
                    chat_messages.append(
                        {"role": "tool", "tool_call_id": tc.id, "content": _DUPE_CALL_MESSAGE}
                    )
                    continue
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
