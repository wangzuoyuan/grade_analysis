"""P6 心跳与止损守卫（2026-11-15 连接中断修复）。

背景：公网入口反代对空闲 SSE 流按超时掐线（当日两次「连接中断」），根因
= 非流式模型调用等待期间链路零字节 + 工具循环拉长静默窗口。修复三件套：
①等模型期间周期发 {"type":"ping"} 心跳帧；②生成器被关闭（客户端断开）时
取消在途模型调用；③同工具同参数第 3 次起拦截并喂纠正性错误结果。
"""

import asyncio
import json
from types import SimpleNamespace

from app.api import chat as chat_module

from tests.v1.test_p6_sessions import _create_session, _frames


# ────────────────────── ① 心跳帧 ──────────────────────


def _slow_anthropic(monkeypatch, delay: float):
    from app.chat.config import ChatConfig

    monkeypatch.setattr(
        "app.api.chat.get_chat_config",
        lambda: ChatConfig(
            provider="anthropic", api_key="test-key", base_url="", model="test-model"
        ),
    )

    class _Block:
        type = "text"
        text = "慢模型的完整回答。"

    class _Response:
        content = [_Block()]
        stop_reason = "end_turn"

    class _Messages:
        async def create(self, **kwargs):
            await asyncio.sleep(delay)
            return _Response()

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(
        "app.api.chat.create_async_anthropic_client", lambda config: _Client()
    )


def test_ping_frames_while_model_slow_anthropic(v1_seed, client, monkeypatch):
    """模型响应慢于心跳间隔时，SSE 流中周期出现 ping 帧；最终帧序列不受
    影响（text + done 收尾），ping 不落历史。"""
    monkeypatch.setattr(chat_module, "_CHAT_PING_INTERVAL", 0.05)
    _slow_anthropic(monkeypatch, delay=0.22)

    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    r = client.post(
        f"/api/v1/chat/sessions/{sid}/messages", json={"content": "慢一点没关系"}
    )
    assert r.status_code == 200, r.text
    frames = _frames(r.text)

    pings = [f for f in frames if f["type"] == "ping"]
    texts = [f for f in frames if f["type"] == "text"]
    assert len(pings) >= 2, f"慢模型期间应发多帧 ping，实得 {len(pings)}"
    assert all(set(p.keys()) == {"type"} and p["type"] == "ping" for p in pings)
    assert texts and texts[-1]["delta"] == "慢模型的完整回答。"
    assert frames[-1]["type"] == "done"
    # ping 不落历史（刷新恢复不依赖浏览器，历史里不得有心跳噪声）
    hist = client.get(f"/api/v1/chat/sessions/{sid}/messages").json()["messages"]
    assert all("ping" not in (m.get("content") or "") for m in hist)


def test_ping_frames_while_model_slow_openai(v1_seed, client, monkeypatch):
    """OpenAI 分支同款心跳路径（wrapper 两分支共用，此处守住接线）。"""
    from app.chat.config import ChatConfig

    monkeypatch.setattr(chat_module, "_CHAT_PING_INTERVAL", 0.05)
    monkeypatch.setattr(
        "app.api.chat.get_chat_config",
        lambda: ChatConfig(
            provider="openai", api_key="test-key", base_url="", model="test-model"
        ),
    )

    class _Msg:
        content = "慢模型的完整回答。"
        tool_calls = None

    class _Choice:
        message = _Msg()
        finish_reason = "stop"

    class _Response:
        choices = [_Choice()]

    class _Completions:
        async def create(self, **kwargs):
            await asyncio.sleep(0.22)
            return _Response()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(
        "app.api.chat.create_async_openai_client", lambda config: _Client()
    )

    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    r = client.post(
        f"/api/v1/chat/sessions/{sid}/messages", json={"content": "慢一点没关系"}
    )
    assert r.status_code == 200, r.text
    frames = _frames(r.text)
    assert len([f for f in frames if f["type"] == "ping"]) >= 2
    assert frames[-1]["type"] == "done"


def test_instant_model_no_ping_frames(v1_seed, client, monkeypatch):
    """模型即时返回时不发任何 ping——既有帧序列零扰动（存量测试同口径）。"""
    _slow_anthropic(monkeypatch, delay=0.0)

    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    r = client.post(
        f"/api/v1/chat/sessions/{sid}/messages", json={"content": "在吗"}
    )
    frames = _frames(r.text)
    assert not any(f["type"] == "ping" for f in frames)
    assert frames[-1]["type"] == "done"


# ────────────────────── ② 断开即取消在途调用 ──────────────────────


def test_wrapper_cancels_inflight_model_task_on_close(monkeypatch):
    """生成器被 aclose（客户端断开 → Starlette 关闭生成器链）时，在途模型
    调用立即被取消——单元级直接驱动 _model_call_with_pings。"""
    monkeypatch.setattr(chat_module, "_CHAT_PING_INTERVAL", 0.01)
    cancelled = {"flag": False}

    async def _slow_call():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled["flag"] = True
            raise

    async def _scenario():
        box: dict = {}
        agen = chat_module._model_call_with_pings(_slow_call, box)
        # 等到第一帧 ping（模型仍在途）
        first = await asyncio.wait_for(agen.__anext__(), timeout=1.0)
        assert '"ping"' in first
        await agen.aclose()
        # aclose 返回即在途任务已完成取消收尾
        assert cancelled["flag"], "aclose 后在途模型调用必须已被取消"
        assert "result" not in box and "error" not in box, "取消路径不得填结果"

    asyncio.run(_scenario())


def test_wrapper_returns_result_and_retries(monkeypatch):
    """正常路径：前两次尝试抛错重试、第三次成功 → box["result"]；错误
    路径重试耗尽 → box["error"]（_CHAT_PROVIDER_RETRIES 次数语义不变）。"""
    monkeypatch.setattr(chat_module, "_CHAT_PING_INTERVAL", 0.05)
    attempts = {"n": 0}

    async def _flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("gateway hiccup")
        return "final-response"

    async def _scenario():
        box: dict = {}
        agen = chat_module._model_call_with_pings(_flaky, box)
        frames = []
        try:
            async for frame in agen:
                frames.append(frame)
        finally:
            await agen.aclose()
        return box, frames

    box, frames = asyncio.run(_scenario())
    assert attempts["n"] == 3
    assert box["result"] == "final-response"
    assert not frames, "快速失败重试期间（0.4s×2 退避）不足以触发心跳，应无 ping"

    # 重试耗尽路径
    attempts["n"] = 0

    async def _always_fail():
        attempts["n"] += 1
        raise RuntimeError("gateway down")

    async def _scenario2():
        box: dict = {}
        agen = chat_module._model_call_with_pings(_always_fail, box)
        try:
            async for frame in agen:
                pass
        finally:
            await agen.aclose()
        return box

    box = asyncio.run(_scenario2())
    assert attempts["n"] == 3  # 既有 _CHAT_PROVIDER_RETRIES 次数语义不变
    assert isinstance(box["error"], RuntimeError)


# ────────────────────── ③ 同工具同参数重复调用止损 ──────────────────────


def _anthropic_scripted(monkeypatch):
    from app.chat.config import ChatConfig

    monkeypatch.setattr(
        "app.api.chat.get_chat_config",
        lambda: ChatConfig(
            provider="anthropic", api_key="test-key", base_url="", model="test-model"
        ),
    )
    holder = {"responses": [], "calls": []}

    class _Messages:
        async def create(self, **kwargs):
            index = len(holder["calls"])
            holder["calls"].append(kwargs)
            blocks = []
            for item in holder["responses"][index]:
                b = SimpleNamespace()
                if item["kind"] == "text":
                    b.type, b.text = "text", item["text"]
                else:
                    b.type = "tool_use"
                    b.id, b.name, b.input = item["id"], item["name"], item["input"]
                blocks.append(b)
            return SimpleNamespace(content=blocks, stop_reason="end_turn")

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(
        "app.api.chat.create_async_anthropic_client", lambda config: _Client()
    )
    return holder


def test_duplicate_identical_calls_suppressed_anthropic(v1_seed, client, monkeypatch):
    """同工具同参数：前 2 次照常执行，第 3 次起拦截（tool_error 帧 +
    纠正性 tool_result），模型下一轮收到纠正文本后仍能正常收束作答。"""
    holder = _anthropic_scripted(monkeypatch)
    same_args = {"q": ""}

    def _tool(cid):
        return {"kind": "tool", "id": cid, "name": "search_students", "input": same_args}

    holder["responses"] = [
        [_tool("c1")],
        [_tool("c2"), _tool("c3"), _tool("c4")],
        [{"kind": "text", "text": "基于已有结果的收束回答。"}],
    ]

    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    r = client.post(
        f"/api/v1/chat/sessions/{sid}/messages", json={"content": "查名单"}
    )
    assert r.status_code == 200, r.text
    frames = _frames(r.text)

    results = [f for f in frames if f["type"] == "tool_result" and f["name"] == "search_students"]
    errors = [f for f in frames if f["type"] == "tool_error"]
    assert [f["call_id"] for f in results] == ["c1", "c2"], "前两次照常执行"
    assert [f["call_id"] for f in errors] == ["c3", "c4"], "第 3 次起全部拦截"
    assert all("同一工具与相同参数" in f["error"] for f in errors)
    texts = [f for f in frames if f["type"] == "text"]
    assert texts and "收束回答" in texts[-1]["delta"]
    assert frames[-1]["type"] == "done"

    # 模型在第三轮请求里收到了纠正文本（tool_result 内容），可据此改道
    third_round_messages = json.dumps(holder["calls"][2]["messages"], ensure_ascii=False)
    assert "同一工具与相同参数" in third_round_messages

    # 落库：本轮 user + assistant 均在，工具事件含 2 ok + 2 error
    hist = client.get(f"/api/v1/chat/sessions/{sid}/messages").json()["messages"]
    assert [m["role"] for m in hist] == ["user", "assistant"]


def test_different_args_not_suppressed(v1_seed, client, monkeypatch):
    """参数不同（如按学科逐个查询）不拦截——止损只针对完全相同的重复取数。"""
    holder = _anthropic_scripted(monkeypatch)

    def _tool(cid, q):
        return {"kind": "tool", "id": cid, "name": "search_students", "input": {"q": q}}

    holder["responses"] = [
        [_tool("c1", "张"), _tool("c2", "李"), _tool("c3", "王"), _tool("c4", "赵")],
        [{"kind": "text", "text": "四个不同参数全部执行。"}],
    ]

    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    r = client.post(
        f"/api/v1/chat/sessions/{sid}/messages", json={"content": "分别查四个人"}
    )
    frames = _frames(r.text)
    results = [f for f in frames if f["type"] == "tool_result"]
    errors = [f for f in frames if f["type"] == "tool_error"]
    assert len(results) == 4 and not errors
    assert frames[-1]["type"] == "done"
