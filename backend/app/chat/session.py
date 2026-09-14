import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.chat.config import get_chat_config
from app.chat.tools import create_anthropic_client, create_openai_client, to_openai_tools

router = APIRouter(prefix="/chat", tags=["chat"])
CHAT_MAX_TOKENS = 4096
CHAT_MAX_CONTINUATIONS = 2

SYSTEM_PROMPT = """你是高中班主任的成绩分析助手。

核心规则：
1. 跨学年趋势只能用主三门和语数英原始分；高一到高二禁止用九门或+3比
2. 引用任何数字必须先经过工具查询；不准凭空给数据
3. 其他班是参照系不是报告对象
4. 受众是班主任本人，可直呼学生姓名
4.1 “加三学科”是物理、化学、生物、政治、历史、地理六科的统称；“+3/选考三科”才表示学生实际选择参加的三门
5. 用户问”最近两次/最近几次/多场考试谁进步最大或退步最大”、且问题包含多个单科或总分口径时，优先调用 multi_exam_progress_ranking；用户说”最近两次”就传 recent_count=2，用户说”几次趋势”就按语义传 recent_count 和 min_points
6. 用户只问某年级某一门学科进步最大/退步最大/提升最多时，可调用 subject_progress_ranking
7. 用户问”这个同学/某学生整体情况/学习情况/优劣势/建议”时，优先调用 student_learning_profile；如果当前页面上下文有 student_id，就直接使用它
7.1 用户问高分段/临界段/薄弱段人数随时间/历次/最近几次的变化、走势、有没有增减时，调用 band_trend；只问某次考试的段位分布则用 focus_list 或考试详情。段位区间口径以下方"当前重点关注段位口径"为准
7.2 用户给出临时排名阈值或排名区间（如“350名以内/前350名/300-450名之间/年级排名小于等于350”）并问人数、变化或趋势时，调用 custom_rank_band_trend。这个工具直接统计学生已有 xueji_rank/grade_rank，不受高分段/临界段/薄弱段固定口径限制
7.3 用户问某次考试某指标的排名区间筛选名单（如“数学前100名有哪些”“主三门300到350名有哪些学生”）时，调用 rank_range_filter。用户问多场考试排名频次/前20%次数/40名一档次数/+3等级分频次时，调用 rank_frequency_stat；+3等级分频次必须按70、67、64、61、58、55、52、49、46、43、40这些精确等级分统计，不按分数区间汇总
8. 描述成绩**趋势和进退步**时，严格按以下规则选择指标，**禁止用”分数从X升到Y””提升/下降Z分”来描述趋势走势**：
   - 总分趋势：用学籍排名（xueji_rank）；无学籍排名时用年级百分位（grade_percentile）
   - 高一所有单科：用年级百分位（grade_percentile）；百分位降低=进步，升高=退步
   - 高二/高三 语数英单科：用年级百分位（grade_percentile）
   - 高二/高三 +3选考单科：用等级分（grade_score）；不用原始分，不用百分位
   - raw_score 只允许出现在”该次考试原始分为X”的单点描述中，不得用于计算进退步幅度
9. 工具返回 available=false 或 raw_score/grade_score 均为空的单科，表示未参考或无有效成绩；不得把残留百分位当作真实成绩，也不得把有 raw_score 的高一期中/期末小科误写成“—”
10. 涉及作业、缺交、欠交、完成情况时调用作业工具：单个学生用 student_homework_summary，全班缺交排行用 class_homework_ranking，要把缺交和成绩联系起来（如“缺交多是否成绩差”）用 homework_grade_correlation。重要口径：作业数据只记录缺交、请假、迟到等负面信号，不包含作业完成质量或评分，措辞上不要把“缺交少”等同于“作业认真/质量高”
11. 用户要“结合最近谈话/家访”“准备和某某的谈话提纲”“写给某某家长的沟通稿”时，调用 student_notes 读取该生成长/谈话档案，并结合 student_learning_profile（成绩）与 student_homework_summary（缺交）综合起草。谈话档案是班主任私密记录，措辞必须稳妥、尊重学生
12. 学生可能跨学年换过学号；以学生为中心的查询（学生画像/趋势/档案/作业）已自动合并同一人的多个学号；班级口径（关注名单/段位/对比）仍按当年行政班

口径文档参考：exam-score-analysis/references/metric-definitions.md"""


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_tools_list():
    from app.chat.tools import TOOLS

    return TOOLS


def block_to_message_content(block) -> dict:
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return {}


def _band_config_line() -> str:
    """把用户自定义的段位口径拼成一行，注入系统提示，保证 AI 文字表述与页面/工具一致。"""
    try:
        from app.analysis.config import get_band_config

        c = get_band_config()
        return (
            "\n\n当前重点关注段位口径（按学籍排名，用户可自定义，引用时一律以此为准）："
            f"高分段 1-{c['high_score_max']}名、"
            f"临界段 {c['critical_min']}-{c['critical_max']}名、"
            f"薄弱段 第 {c['weak_min']} 名及以后。"
        )
    except Exception:
        return ""


def build_system_prompt(context: dict | None = None) -> str:
    prompt = SYSTEM_PROMPT + _band_config_line()

    if not context:
        return prompt

    safe_context = {
        key: context.get(key)
        for key in ("page", "student_id", "exam_id", "grade", "class_num")
        if context.get(key) is not None
    }
    if not safe_context:
        return prompt

    return (
        prompt
        + "\n\n当前班级作用域：只能分析该 grade + class_num 对应的班级；其他班级只能作为对比参照，不得作为报告对象。\n"
        + "当前页面上下文（仅用于理解“这个学生/本次考试”等指代，不能替代工具查询数字）："
        + json.dumps(safe_context, ensure_ascii=False)
    )


def resolve_chat_scope(context: dict | None) -> dict:
    """在建立 SSE 之前校验前端声明的年级和班级确属当前教师。"""
    from app.db.models import SessionLocal, Teacher

    if not isinstance(context, dict):
        raise HTTPException(status_code=409, detail="尚未配置班级作用域，请先绑定班级")
    try:
        grade = int(context.get("grade"))
        class_num = int(context.get("class_num"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=409, detail="尚未配置班级作用域，请先绑定班级")
    if grade not in (1, 2, 3) or class_num <= 0:
        raise HTTPException(status_code=409, detail="班级作用域无效，请重新选择")

    db = SessionLocal()
    try:
        from app.rollover.service import get_active_grade

        teacher = db.query(Teacher).first()
        bound_class = getattr(teacher, f"target_class_high{grade}", None) if teacher else None
        if bound_class is None:
            raise HTTPException(status_code=409, detail=f"高{grade}尚未绑定班级，请先完成班级配置")
        if int(bound_class) != class_num:
            raise HTTPException(status_code=409, detail="请求班级与教师已绑定班级不一致")
        if int(get_active_grade(db)) != grade:
            raise HTTPException(status_code=409, detail="请求年级与教师当前选择的年级不一致")
    finally:
        db.close()

    return {**context, "grade": grade, "class_num": class_num}


def _anthropic_messages_to_openai(messages: list, system_prompt: str) -> list[dict]:
    """把 Anthropic 风格历史消息转成 OpenAI chat.completions 格式。"""
    out: list[dict] = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            continue
        if role == "assistant":
            text_parts = []
            tool_calls = []
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get("id"),
                            "type": "function",
                            "function": {
                                "name": block.get("name"),
                                "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                            },
                        }
                    )
            entry: dict = {"role": "assistant", "content": "".join(text_parts) or None}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
        elif role == "user":
            text_parts = []
            tool_results = []
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_result":
                    tool_results.append(block)
            if text_parts:
                out.append({"role": "user", "content": "".join(text_parts)})
            for tr in tool_results:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": tr.get("tool_use_id"),
                        "content": tr.get("content", ""),
                    }
                )
    return out


def _join_continuation(parts: list[str]) -> str:
    text = ""
    for part in parts:
        if not part:
            continue
        if text and not text.endswith(("\n", " ", "　")) and not part.startswith(("\n", " ", "，", "。", "；", "：", "、", "）", ")", "]")):
            text += "\n"
        text += part
    return text


def _continuation_prompt() -> str:
    return "请从上一句后继续完成回答，不要重复已经写过的内容；如果已经完整结束，只回复空内容。"


async def _stream_openai(config, messages: list, context: dict | None):
    from app.chat.tools import execute_tool

    client = create_openai_client(config)
    tools = to_openai_tools(build_tools_list())
    system_prompt = build_system_prompt(context)
    chat_messages = _anthropic_messages_to_openai(messages, system_prompt)

    for _ in range(8):
        try:
            response = client.chat.completions.create(
                model=config.model,
                messages=chat_messages,
                tools=tools,
                max_tokens=CHAT_MAX_TOKENS,
            )
        except Exception as exc:
            yield sse({"type": "text", "delta": f"对话接口调用失败：{exc}"})
            yield sse({"type": "done"})
            return

        choice = response.choices[0]
        msg = choice.message
        tool_calls = getattr(msg, "tool_calls", None) or []

        if tool_calls:
            assistant_entry: dict = {
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
            chat_messages.append(assistant_entry)
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                call_id = str(tc.id)
                yield sse({"type": "tool_call", "call_id": call_id, "name": tc.function.name, "input": args})
                try:
                    result = execute_tool(tc.function.name, args, context)
                except Exception as exc:
                    result = {"error": str(exc)}
                if isinstance(result, dict) and result.get("error"):
                    yield sse({
                        "type": "tool_error",
                        "call_id": call_id,
                        "name": tc.function.name,
                        "error": str(result["error"]),
                    })
                else:
                    yield sse({
                        "type": "tool_result",
                        "call_id": call_id,
                        "name": tc.function.name,
                        "output": result,
                    })
                chat_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            continue

        text_parts = [msg.content or ""]
        finish_reason = getattr(choice, "finish_reason", None)
        for _ in range(CHAT_MAX_CONTINUATIONS):
            if finish_reason != "length" or not text_parts[-1]:
                break
            chat_messages.append({"role": "assistant", "content": text_parts[-1]})
            chat_messages.append({"role": "user", "content": _continuation_prompt()})
            try:
                continuation = client.chat.completions.create(
                    model=config.model,
                    messages=chat_messages,
                    max_tokens=CHAT_MAX_TOKENS,
                )
            except Exception:
                break
            continuation_choice = continuation.choices[0]
            continuation_text = continuation_choice.message.content or ""
            if not continuation_text:
                break
            text_parts.append(continuation_text)
            finish_reason = getattr(continuation_choice, "finish_reason", None)

        text = _join_continuation(text_parts)
        if finish_reason == "length":
            text += "\n\n（回答仍达到长度上限，已尽量保留完整内容；如需更长明细请继续追问。）"
        if text:
            yield sse({"type": "text", "delta": text})
        yield sse({"type": "done"})
        return

    yield sse({"type": "text", "delta": "工具调用轮次过多，已停止。请缩小问题范围后重试。"})
    yield sse({"type": "done"})


async def stream_chat(messages: list, context: dict | None = None):
    """SSE 对话。支持 Claude / OpenAI 兼容接口 tool-use，并把工具调用元数据推给前端。"""
    config = get_chat_config()
    if not config.is_configured:
        key_name = "OPENAI_API_KEY" if config.provider == "openai" else "ANTHROPIC_API_KEY"
        yield sse({"type": "text", "delta": f"未设置有效的 {key_name}，对话助手暂不可用。"})
        yield sse({"type": "done"})
        return

    if config.provider == "openai":
        async for chunk in _stream_openai(config, messages, context):
            yield chunk
        return

    from app.chat.tools import execute_tool

    client = create_anthropic_client(config)
    tools = build_tools_list()
    chat_messages = list(messages)

    for _ in range(8):
        # MiniMax 等兼容端点偶发瞬时错误（401/429/5xx/网络抖动），读操作可安全重试
        response = None
        last_exc = None
        for attempt in range(3):
            try:
                response = client.messages.create(
                    model=config.model,
                    max_tokens=CHAT_MAX_TOKENS,
                    system=build_system_prompt(context),
                    messages=chat_messages,
                    tools=tools,
                )
                break
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(0.6 * (attempt + 1))
        if response is None:
            yield sse({"type": "text", "delta": f"对话接口调用失败（已重试）：{last_exc}"})
            yield sse({"type": "done"})
            return

        tool_results = []
        assistant_content = []
        final_text_parts = []

        for block in response.content:
            if block.type == "text":
                assistant_content.append(block_to_message_content(block))
                final_text_parts.append(block.text)
            elif block.type == "tool_use":
                assistant_content.append(block_to_message_content(block))
                args = block.input or {}
                call_id = str(block.id)
                yield sse({"type": "tool_call", "call_id": call_id, "name": block.name, "input": args})
                try:
                    result = execute_tool(block.name, args, context)
                except Exception as exc:
                    result = {"error": str(exc)}
                if isinstance(result, dict) and result.get("error"):
                    yield sse({
                        "type": "tool_error",
                        "call_id": call_id,
                        "name": block.name,
                        "error": str(result["error"]),
                    })
                else:
                    yield sse({
                        "type": "tool_result",
                        "call_id": call_id,
                        "name": block.name,
                        "output": result,
                    })
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        if tool_results:
            chat_messages.append({"role": "assistant", "content": assistant_content})
            chat_messages.append({"role": "user", "content": tool_results})
            continue

        text_parts = ["".join(final_text_parts)]
        stop_reason = getattr(response, "stop_reason", None)
        for _ in range(CHAT_MAX_CONTINUATIONS):
            if stop_reason != "max_tokens" or not text_parts[-1]:
                break
            chat_messages.append({"role": "assistant", "content": [{"type": "text", "text": text_parts[-1]}]})
            chat_messages.append({"role": "user", "content": _continuation_prompt()})

            continuation = None
            last_exc = None
            for attempt in range(3):
                try:
                    continuation = client.messages.create(
                        model=config.model,
                        max_tokens=CHAT_MAX_TOKENS,
                        system=build_system_prompt(context),
                        messages=chat_messages,
                    )
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt < 2:
                        await asyncio.sleep(0.6 * (attempt + 1))
            if continuation is None:
                text_parts.append(f"\n\n（继续生成失败：{last_exc}）")
                break

            continuation_text = "".join(
                block.text for block in continuation.content if block.type == "text"
            )
            if not continuation_text:
                break
            text_parts.append(continuation_text)
            stop_reason = getattr(continuation, "stop_reason", None)

        text = _join_continuation(text_parts)
        if stop_reason == "max_tokens":
            text += "\n\n（回答仍达到长度上限，已尽量保留完整内容；如需更长明细请继续追问。）"
        if text:
            yield sse({"type": "text", "delta": text})
        yield sse({"type": "done"})
        return

    yield sse({"type": "text", "delta": "工具调用轮次过多，已停止。请缩小问题范围后重试。"})
    yield sse({"type": "done"})


@router.post("")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    context = resolve_chat_scope(body.get("context", {}))

    return StreamingResponse(
        stream_chat(messages, context),
        media_type="text/event-stream",
    )


@router.get("/config")
async def chat_config():
    config = get_chat_config()
    return {
        "provider": config.provider,
        "model": config.model,
        "configured": config.is_configured,
        "base_url_configured": bool(config.base_url),
    }
