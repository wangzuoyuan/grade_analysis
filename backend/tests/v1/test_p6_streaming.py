"""P6 真流式（2026-11-15 方案二）：Anthropic 分支 stream=True 逐字转发。

流式快路径 + 回退结构（chat.py `_stream_anthropic_reply` 头注）：
- 文本增量边生成边发（打字机）；tool_use 参数流内拼装、content_block_stop
  即时执行；
- 网关忽略 stream=True（返回完整响应对象）→ 回退 v1.0.16 路径（既有
  test_p6_sessions 全量守卫，本文件只测流式专属行为）；
- 流中途失败且本轮未发布 → 静默回退非流式重取；已发布 → provider_failed
  收流、半途不落库；
- 事件等待期间 ping 心跳；轮前重验（漂移收流）。
"""

import asyncio
from types import SimpleNamespace

from app.api import chat as chat_module

from tests.v1.test_p6_sessions import _cancel_link, _create_session, _frames


# ────────────────────── 流事件替身 ──────────────────────


class _FakeStream:
    """Anthropic 原始流事件替身：事件序列 + 可选逐事件延迟 / 首事件前延迟 /
    第 N 个事件前抛错 / 事件前回调（注入库层副作用）。"""

    def __init__(self, events, first_delay=0.0, fail_at=None, before=None):
        self.events = events
        self.i = 0
        self.first_delay = first_delay
        self.fail_at = fail_at  # 在取第 fail_at 个事件时抛 RuntimeError
        self.before = before  # callback(self.i) 在取事件前调用
        self.closed = False
        self.cancelled = False
        self.create_kwargs = None  # 由 client 回填

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.first_delay:
            await asyncio.sleep(self.first_delay)
            self.first_delay = 0.0
        if self.before is not None:
            self.before(self.i)
        if self.fail_at is not None and self.i == self.fail_at:
            raise RuntimeError("gateway stream broke")
        if self.i >= len(self.events):
            raise StopAsyncIteration
        try:
            ev = self.events[self.i]
            self.i += 1
            return ev
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def close(self):
        self.closed = True


def _ev_text_start(i=0):
    return SimpleNamespace(
        type="content_block_start", index=i, content_block=SimpleNamespace(type="text")
    )


def _ev_text_delta(i, text):
    return SimpleNamespace(
        type="content_block_delta", index=i, delta=SimpleNamespace(type="text_delta", text=text)
    )


def _ev_tool_start(i, cid, name):
    return SimpleNamespace(
        type="content_block_start",
        index=i,
        content_block=SimpleNamespace(type="tool_use", id=cid, name=name),
    )


def _ev_json_delta(i, chunk):
    return SimpleNamespace(
        type="content_block_delta",
        index=i,
        delta=SimpleNamespace(type="input_json_delta", partial_json=chunk),
    )


def _ev_stop(i):
    return SimpleNamespace(type="content_block_stop", index=i)


def _ev_round_end(stop_reason="end_turn"):
    return [
        SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason=stop_reason)),
        SimpleNamespace(type="message_stop"),
    ]


def _legacy_response(text="回退路径的回答。", stop_reason="end_turn"):
    block = SimpleNamespace(type="text", text=text, input=None, id=None, name=None)
    return SimpleNamespace(content=[block], stop_reason=stop_reason)


class _Messages:
    """create(stream=True) → 流替身；create()（无 stream）→ 完整响应。"""

    def __init__(self, streams, legacy=None):
        self.streams = list(streams)
        self.legacy = legacy or _legacy_response()
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return self.streams.pop(0)
        return self.legacy


def _install(monkeypatch, messages):
    from app.chat.config import ChatConfig

    monkeypatch.setattr(
        "app.api.chat.get_chat_config",
        lambda: ChatConfig(
            provider="anthropic", api_key="test-key", base_url="", model="test-model"
        ),
    )

    class _Client:
        pass

    client = _Client()
    client.messages = messages
    monkeypatch.setattr(
        "app.api.chat.create_async_anthropic_client", lambda config: client
    )
    return messages


# ────────────────────── 文本逐字转发 ──────────────────────


def test_streaming_text_deltas_forwarded_and_persisted(v1_seed, client, monkeypatch):
    """文本增量逐帧转发（打字机）；落库文本 = 增量拼接。"""
    msgs = _install(
        monkeypatch,
        _Messages(
            streams=[
                _FakeStream(
                    [
                        SimpleNamespace(type="message_start"),
                        _ev_text_start(0),
                        _ev_text_delta(0, "第一段"),
                        _ev_text_delta(0, "第二段"),
                        _ev_stop(0),
                        *_ev_round_end(),
                    ]
                )
            ]
        ),
    )
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    r = client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "打个比方"})
    assert r.status_code == 200, r.text
    frames = _frames(r.text)
    texts = [f for f in frames if f["type"] == "text"]
    assert [t["delta"] for t in texts] == ["第一段", "第二段"], "增量逐帧、不合并"
    assert frames[-1]["type"] == "done"
    assert msgs.calls[0].get("stream") is True
    hist = client.get(f"/api/v1/chat/sessions/{sid}/messages").json()["messages"]
    assert hist[-1]["content"] == "第一段第二段"


# ────────────────────── 流内工具执行 ──────────────────────


def test_streaming_tool_json_assembled_and_executed(v1_seed, client, monkeypatch):
    """tool_use 参数分片到达（input_json_delta×2）→ 拼装解析后执行；
    第二轮请求体含 tool_use + tool_result；两轮文本都落库。"""
    msgs = _install(
        monkeypatch,
        _Messages(
            streams=[
                _FakeStream(
                    [
                        _ev_tool_start(0, "t1", "search_students"),
                        _ev_json_delta(0, '{"q": '),
                        _ev_json_delta(0, '"张"}'),
                        _ev_stop(0),
                        _ev_text_start(1),
                        _ev_text_delta(1, "我先查一下。"),
                        _ev_stop(1),
                        *_ev_round_end(),
                    ]
                ),
                _FakeStream(
                    [
                        _ev_text_start(0),
                        _ev_text_delta(0, "最终回答。"),
                        _ev_stop(0),
                        *_ev_round_end(),
                    ]
                ),
            ]
        ),
    )
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    r = client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "查张同学"})
    frames = _frames(r.text)
    calls = [f for f in frames if f["type"] == "tool_call"]
    assert len(calls) == 1 and calls[0]["input"] == {"q": "张"}, "参数分片拼装成完整 JSON"
    assert any(f["type"] == "tool_result" for f in frames)
    texts = [f["delta"] for f in frames if f["type"] == "text"]
    assert texts == ["我先查一下。", "最终回答。"], "中间轮叙述也随流发布"
    # 第二轮请求体：assistant(tool_use+text) + user(tool_result)
    round2 = msgs.calls[1]["messages"]
    assert round2[-2]["content"][0]["type"] == "tool_use"
    assert round2[-1]["content"][0]["type"] == "tool_result"
    hist = client.get(f"/api/v1/chat/sessions/{sid}/messages").json()["messages"]
    assert hist[-1]["content"] == "我先查一下。最终回答。"


# ────────────────────── 心跳：首事件前慢 ──────────────────────


def test_streaming_ping_before_first_event(v1_seed, client, monkeypatch):
    monkeypatch.setattr(chat_module, "_CHAT_PING_INTERVAL", 0.05)
    _install(
        monkeypatch,
        _Messages(
            streams=[
                _FakeStream(
                    [_ev_text_start(0), _ev_text_delta(0, "来了"), _ev_stop(0), *_ev_round_end()],
                    first_delay=0.22,
                )
            ]
        ),
    )
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    r = client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "在吗"})
    frames = _frames(r.text)
    assert len([f for f in frames if f["type"] == "ping"]) >= 2
    assert frames[-1]["type"] == "done"


# ────────────────────── 网关 keepalive 洪泛（2026-11-15 线上根因回归）──────────────────────


def test_gateway_keepalive_flood_still_pings_client(v1_seed, client, monkeypatch):
    """智谱网关生成期间持续发 keepalive 事件（SSE ping 等），事件视角永不
    超时（每条都重置等待计时器）而客户端纯静默——心跳必须按「客户端静默
    时长」触发，而不是「无事件时长」（v1.0.17 线上 4520 次 wait 零 timeout、
    公网 60s 被掐的根因）。"""
    import asyncio as _asyncio

    monkeypatch.setattr(chat_module, "_CHAT_PING_INTERVAL", 0.05)

    class _KeepaliveFloodStream:
        def __init__(self):
            self.i = 0
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.i == 0:
                self.i += 1
                return SimpleNamespace(type="message_start")
            if self.i <= 40:  # 0.01s 一个 keepalive，共 ~0.4s 洪泛
                self.i += 1
                await _asyncio.sleep(0.01)
                return SimpleNamespace(type="ping")  # 网关心跳事件：无客户端帧
            if self.i == 41:
                self.i += 1
                return _ev_text_start(0)
            if self.i == 42:
                self.i += 1
                return _ev_text_delta(0, "终于来了")
            if self.i == 43:
                self.i += 1
                return _ev_stop(0)
            raise StopAsyncIteration

        async def close(self):
            self.closed = True

    class _Messages3:
        async def create(self, **kwargs):
            return _KeepaliveFloodStream()

    class _Client3:
        messages = _Messages3()

    monkeypatch.setattr(
        "app.api.chat.create_async_anthropic_client", lambda config: _Client3()
    )

    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    r = client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "慢思考"})
    frames = _frames(r.text)
    pings = [f for f in frames if f["type"] == "ping"]
    texts = [f for f in frames if f["type"] == "text"]
    assert len(pings) >= 3, f"keepalive 洪泛期间必须照发客户端心跳，实得 {len(pings)}"
    assert texts and texts[-1]["delta"] == "终于来了"
    assert frames[-1]["type"] == "done"
    # ping 必须出现在 text 之前（静默期先于心跳之后的正文）
    assert frames.index(pings[0]) < frames.index(texts[0])


# ────────────────────── 回退两态 ──────────────────────


def test_streaming_failure_before_publish_falls_back(v1_seed, client, monkeypatch):
    """流在发布任何帧之前断（首个事件就抛错）→ 静默回退非流式重取本轮，
    用户无感知。"""
    msgs = _install(
        monkeypatch,
        _Messages(
            streams=[_FakeStream([], fail_at=0)],
            legacy=_legacy_response("回退后的完整回答。"),
        ),
    )
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    r = client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "总结"})
    frames = _frames(r.text)
    assert not any(f["type"] == "ping" for f in frames)
    assert [f["delta"] for f in frames if f["type"] == "text"] == ["回退后的完整回答。"]
    assert frames[-1]["type"] == "done"
    # 第二次 create 是非流式（无 stream=True）
    assert len(msgs.calls) == 2 and not msgs.calls[1].get("stream")
    hist = client.get(f"/api/v1/chat/sessions/{sid}/messages").json()["messages"]
    assert hist[-1]["content"] == "回退后的完整回答。"


def test_streaming_failure_after_publish_stops(v1_seed, client, monkeypatch):
    """已发布文本后流断 → provider_failed 收流；半途轮不落库。"""
    _install(
        monkeypatch,
        _Messages(
            streams=[
                _FakeStream(
                    [
                        _ev_text_start(0),
                        _ev_text_delta(0, "说到一半"),
                        _ev_stop(0),
                    ],
                    fail_at=3,
                )
            ]
        ),
    )
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    r = client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "长篇大论"})
    frames = _frames(r.text)
    texts = [f["delta"] for f in frames if f["type"] == "text"]
    assert texts == ["说到一半"], "已发布部分保留"
    errors = [f for f in frames if f["type"] == "error"]
    assert errors and errors[-1]["code"] == "provider_failed"
    assert frames[-1]["type"] == "done"
    hist = client.get(f"/api/v1/chat/sessions/{sid}/messages").json()["messages"]
    assert hist == [], "半途轮不落库"


# ────────────────────── 轮前漂移与工具期漂移 ──────────────────────


def test_streaming_tool_drift_aborts(v1_seed, client, monkeypatch):
    """流式轮工具执行期间撤销关联（content_block_stop 前注入库层取消）→
    scope_drift 帧 + 收流，不发布后续内容。"""

    def _before(index, link_id=v1_seed.link_id):
        if index == 2:  # tool 块 content_block_stop 前（工具即将执行）
            _cancel_link(link_id)

    _install(
        monkeypatch,
        _Messages(
            streams=[
                _FakeStream(
                    [
                        _ev_tool_start(0, "t1", "search_students"),
                        _ev_json_delta(0, "{}"),
                        _ev_stop(0),
                        _ev_text_start(1),
                        _ev_text_delta(1, "不应发布"),
                    ],
                    before=_before,
                )
            ]
        ),
    )
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    r = client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "查名单"})
    frames = _frames(r.text)
    errors = [f for f in frames if f["type"] == "error"]
    assert errors and errors[-1]["code"] == "scope_drift"
    assert not any(f["type"] == "text" for f in frames), "失效后不再发布文本"
    assert frames[-1]["type"] == "done"

    # 恢复关联，避免污染同模块其他用例
    from app.db.models import SessionLocal
    from app.db.workspace_models import HomeroomTeachingLink

    db = SessionLocal()
    link = db.get(HomeroomTeachingLink, v1_seed.link_id)
    link.status = "active"
    link.version = 1
    link.cancelled_at = None
    db.commit()
    db.close()


# ────────────────────── 流中取消（客户端断开）─────────────────────


def test_streaming_teardown_cancels_pending(monkeypatch):
    """生成器被 aclose（客户端断开）时：在途 __anext__ 任务被取消、流被
    关闭——单元级直接驱动分支生成器。"""
    monkeypatch.setattr(chat_module, "_CHAT_PING_INTERVAL", 0.02)
    monkeypatch.setattr(chat_module, "_assert_snapshot_fresh", lambda *a, **k: None)

    stream = _FakeStream(
        [_ev_text_start(0), _ev_text_delta(0, "开头"), _ev_stop(0), *_ev_round_end()]
    )

    original_next = stream.__anext__

    async def _next():
        if stream.i == 1:
            # 第二个事件（text 增量）前长睡，制造可取消的在途窗口
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                stream.cancelled = True
                raise
        return await original_next()

    stream.__anext__ = _next  # type: ignore[method-assign]

    class _Messages2:
        async def create(self, **kwargs):
            return stream

    class _Client:
        messages = _Messages2()

    monkeypatch.setattr(
        "app.api.chat.create_async_anthropic_client", lambda config: _Client()
    )

    sink = {"final_text": None, "tool_events": []}
    config = SimpleNamespace(model="m", max_tokens=10)
    agen = chat_module._stream_anthropic_reply(
        config, "sys", [], [{"role": "user", "content": "hi"}], None, {}, "hi", sink
    )

    async def _scenario():
        # 消费到第一条 ping（只在等待事件期间出现 → 此刻必有在途任务）
        got_ping = False
        async for frame in agen:
            if '"ping"' in frame:
                got_ping = True
                break
        assert got_ping
        await agen.aclose()
        assert stream.cancelled, "aclose 后在途事件等待必须被取消"
        assert stream.closed, "流对象必须被关闭"

    asyncio.run(_scenario())


# ────────────────────── 系统提示词：工具用量规则 ──────────────────────


def test_system_prompt_has_tool_economy_rule(monkeypatch):
    """E2E 发现逐人 fan-out 耗尽 token 预算 → 提示词加规则 8（守卫防回退）。"""
    monkeypatch.setattr(chat_module, "_time_anchor_of", lambda db, s: None)
    prompt = chat_module.build_system_prompt(
        {"mode": "homeroom", "data_domain": "homeroom"}, None
    )
    assert "工具用量" in prompt
    assert "不要**对每个学生逐一调用" in prompt
    assert "get_homework_student_subjects" in prompt
    assert "8. 工具用量" in prompt
