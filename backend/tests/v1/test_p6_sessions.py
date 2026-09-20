"""P6 会话生命周期测试（契约 docs/contracts/p6-ai-mcp.md §1/§3/§0.1；A02）。

- 创建会话：服务端解析并冻结 scope；对外投影不含成员明单（含 cohort_size）
- 无模型 Key → 409 workspace_not_configured 且不半开（库中无 ChatSession 行）
- GET 快照 / close 幂等 / close 后发消息 409
- SSE messages：帧可解析（text + done，TestClient streaming）
- A02：撤销关联 / 成员漂移 → 流前 JSON 409 link_version_conflict（非事件流）
- Q02：教学会话绑定关联版本集合；cancel/share 收紧/配对删除 → 409；
  多轮工具执行期间失效 → 范围失效帧并中止（模型不再拿到旧范围数据）
- 配对指纹是排序后的身份对集合——删除两对再交叉新增
  两对（数量不变）→ 旧会话发消息 409，新会话以新配对为基准正常
- 最后一次模型调用（最终轮、无工具调用）期间撤销关联
  → 旧答复不发布（只有范围失效帧）、本轮连同 user 消息不落历史；
  Anthropic / OpenAI 两 provider 分支各覆盖一例（替身注入点不同）
- Q03：服务端历史——两轮后 GET messages 返回 u/a/u/a；第二次模型请求
  组装含第一轮；漂移会话历史只读可拉、发消息 409

样本沿用 tests/v1/conftest.py 的 v1_seed（H6↔T6 关联、物理教学班、2025期中）。
"""

import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

EXAM_E1 = "2025期中"


def _create_session(client, body):
    return client.post("/api/v1/chat/sessions", json=body)


def _frames(text: str) -> list:
    """解析 SSE 帧（data JSON 行 + 空行）。"""
    frames = []
    for chunk in text.split("\n\n"):
        chunk = chunk.strip()
        if chunk.startswith("data: "):
            frames.append(json.loads(chunk[len("data: ") :]))
    return frames


@pytest.fixture()
def no_model_key(monkeypatch):
    """伪造"未配置 Key"：探测函数返回空 api_key（不依赖真实 .env/环境变量）。"""
    from app.chat.config import ChatConfig

    monkeypatch.setattr(
        "app.api.chat.get_chat_config",
        lambda: ChatConfig(provider="anthropic", api_key="", base_url="", model="test-model"),
    )


@pytest.fixture()
def fake_model(monkeypatch):
    """伪造已配置的 Anthropic 客户端：不触网；SSE 帧循环走真实代码。"""
    from app.chat.config import ChatConfig

    monkeypatch.setattr(
        "app.api.chat.get_chat_config",
        lambda: ChatConfig(provider="anthropic", api_key="test-key", base_url="", model="test-model"),
    )

    class _Block:
        type = "text"
        text = "模拟回答：请以工具查询结果为准。"

    class _Response:
        content = [_Block()]
        stop_reason = "end_turn"

    class _Messages:
        async def create(self, **kwargs):
            return _Response()

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(
        "app.api.chat.create_async_anthropic_client", lambda config: _Client()
    )


@pytest.fixture()
def scripted_model(monkeypatch):
    """可编排替身模型（ new-repro 手法）：按 create 次序返回脚本化
    响应，捕获每次请求的 messages 入参（Q03），并支持 on_call(index) 钩子
    在第 N 轮 create 时机注入库层副作用（Q02 多轮中途失效）。
    文本项可带 stop_reason（如 "max_tokens"）验证截断提示分支。"""
    from app.chat.config import ChatConfig

    monkeypatch.setattr(
        "app.api.chat.get_chat_config",
        lambda: ChatConfig(provider="anthropic", api_key="test-key", base_url="", model="test-model"),
    )
    holder: dict = {"responses": [], "calls": [], "on_call": None}

    def _text(text, stop_reason=None):
        item = {"kind": "text", "text": text}
        if stop_reason:
            item["stop_reason"] = stop_reason
        return item

    def _tool(call_id, name, args):
        return {"kind": "tool", "id": call_id, "name": name, "input": args}

    class _Messages:
        async def create(self, **kwargs):
            index = len(holder["calls"])
            if holder["on_call"] is not None:
                holder["on_call"](index)
            holder["calls"].append(kwargs)
            blocks = []
            stop_reason = "end_turn"
            for item in holder["responses"][index]:
                block = SimpleNamespace()
                if item["kind"] == "text":
                    block.type, block.text = "text", item["text"]
                    stop_reason = item.get("stop_reason", "end_turn")
                else:
                    block.type = "tool_use"
                    block.id, block.name, block.input = item["id"], item["name"], item["input"]
                blocks.append(block)
            return SimpleNamespace(content=blocks, stop_reason=stop_reason)

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(
        "app.api.chat.create_async_anthropic_client", lambda config: _Client()
    )
    return SimpleNamespace(holder=holder, text=_text, tool=_tool)


@pytest.fixture()
def scripted_openai_model(monkeypatch):
    """OpenAI 形状的可编排替身（V02：两 provider 分支各覆盖一例，注入点
    为 chat.completions.create）：捕获 create 入参，支持 on_call 在第 N 轮
    create 时机注入库层副作用。responses 存最终文本字符串，或含
    content/finish_reason 的 dict（验证 finish_reason="length" 截断提示）。"""
    from app.chat.config import ChatConfig

    monkeypatch.setattr(
        "app.api.chat.get_chat_config",
        lambda: ChatConfig(provider="openai", api_key="test-key", base_url="", model="test-model"),
    )
    holder: dict = {"responses": [], "calls": [], "on_call": None}

    class _Completions:
        async def create(self, **kwargs):
            index = len(holder["calls"])
            if holder["on_call"] is not None:
                holder["on_call"](index)
            holder["calls"].append(kwargs)
            item = holder["responses"][index]
            if isinstance(item, dict):
                content = item.get("content", "")
                finish_reason = item.get("finish_reason", "stop")
            else:
                content, finish_reason = item, "stop"
            message = SimpleNamespace(content=content, tool_calls=None)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
            )

    class _Client:
        chat = SimpleNamespace(completions=_Completions())

    monkeypatch.setattr(
        "app.api.chat.create_async_openai_client", lambda config: _Client()
    )
    return SimpleNamespace(holder=holder)


def _cancel_link(link_id: int) -> None:
    """直接置 cancelled + version+1（与 /shared/links/{id}/cancel 同语义），
    供替身模型在多轮 create() 之间触发范围失效（TestClient 流内不可重入
    发请求，走库层触发与  new-repro 一致）。"""
    from app.db.models import SessionLocal
    from app.db.workspace_models import HomeroomTeachingLink

    db = SessionLocal()
    try:
        link = db.get(HomeroomTeachingLink, link_id)
        link.status = "cancelled"
        link.version = (link.version or 1) + 1
        link.cancelled_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


def _restore_link(link_id: int) -> None:
    """恢复为种子时的 active/version=1（替身注入失效后的同模块去污染）。"""
    from app.db.models import SessionLocal
    from app.db.workspace_models import HomeroomTeachingLink

    db = SessionLocal()
    try:
        link = db.get(HomeroomTeachingLink, link_id)
        link.status = "active"
        link.version = 1
        link.cancelled_at = None
        db.commit()
    finally:
        db.close()


# ────────────────────── 创建：无 Key 409 不半开（最先执行，库为空） ──────────────────────


def test_create_session_without_model_key_409_no_half_open(v1_seed, client, no_model_key):
    r = _create_session(client, {"mode": "homeroom"})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error"] == "workspace_not_configured"
    assert "ANTHROPIC_API_KEY" in body["detail"]

    from app.db.models import SessionLocal
    from app.db.workspace_models import ChatSession

    db = SessionLocal()
    try:
        assert db.query(ChatSession).count() == 0  # 不半开
    finally:
        db.close()


def test_create_session_invalid_mode_422(v1_seed, client):
    r = _create_session(client, {"mode": "admin"})
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_scope_param"


def test_create_session_cross_mode_params_422(v1_seed, client):
    r = _create_session(client, {"mode": "homeroom", "teaching_class_id": v1_seed.t6_id})
    assert r.status_code == 422
    r2 = _create_session(client, {"mode": "teaching", "class_id": v1_seed.h6_id})
    assert r2.status_code == 422


# ────────────────────── 创建：快照冻结 + 对外投影 ──────────────────────


def test_create_homeroom_session_freezes_scope(v1_seed, client, fake_model):
    r = _create_session(client, {"mode": "homeroom"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] > 0
    scope = body["scope"]
    assert scope["mode"] == "homeroom"
    assert scope["data_domain"] == "homeroom"
    assert scope["class_ids"] == [v1_seed.h6_id]
    assert scope["subject"] == "物理"  # 关联推导，客户端无关
    assert scope["link_id"] == v1_seed.link_id
    assert scope["link_version"] == 1
    assert scope["cohort_size"] == 3
    assert "member_person_ids" not in scope  # 不泄露成员明单


def test_create_teaching_session_union(v1_seed, client, fake_model):
    r = _create_session(client, {"mode": "teaching"})
    assert r.status_code == 200, r.text
    scope = r.json()["scope"]
    assert scope["mode"] == "teaching"
    assert scope["data_domain"] == "teaching"
    assert scope["subject"] == "物理"
    assert sorted(scope["class_ids"]) == sorted(
        [v1_seed.t6_id, v1_seed.t8_id, v1_seed.t_empty_id]
    )  # 缺省 = 该学年该学科全部所教班（含无成员的空班）
    assert scope["cohort_size"] == 5  # 空班不贡献成员
    assert "member_person_ids" not in scope


def test_get_session_snapshot_and_404(v1_seed, client, fake_model):
    miss = client.get("/api/v1/chat/sessions/99999")
    assert miss.status_code == 404
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    got = client.get(f"/api/v1/chat/sessions/{sid}")
    assert got.status_code == 200
    scope = got.json()["scope"]
    assert scope["cohort_size"] == 3
    assert "member_person_ids" not in scope


def test_close_session_idempotent(v1_seed, client, fake_model):
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    r1 = client.post(f"/api/v1/chat/sessions/{sid}/close")
    assert r1.status_code == 200
    assert r1.json() == {"session_id": sid, "status": "closed"}
    r2 = client.post(f"/api/v1/chat/sessions/{sid}/close")
    assert r2.status_code == 200
    assert r2.json()["status"] == "closed"


# ────────────────────── messages：close 后 409 / SSE happy path ──────────────────────


def test_message_after_close_409_json_not_stream(v1_seed, client, fake_model):
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    client.post(f"/api/v1/chat/sessions/{sid}/close")
    r = client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "在吗"})
    assert r.status_code == 409
    assert r.json()["error"] == "link_version_conflict"
    assert "text/event-stream" not in (r.headers.get("content-type") or "")


def test_message_empty_content_422(v1_seed, client, fake_model):
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    r = client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "  "})
    assert r.status_code == 422


def test_message_sse_happy_path(v1_seed, client, fake_model):
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    r = client.post(
        f"/api/v1/chat/sessions/{sid}/messages", json={"content": "总结最近一次考试"}
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    frames = _frames(r.text)
    assert frames, "至少一帧"
    assert frames[-1]["type"] == "done"
    assert any(
        f["type"] == "text" and "模拟回答" in f.get("delta", "") for f in frames
    )


# ────────────────────── A02：快照漂移 → 流前 JSON 409 ──────────────────────


def test_a02_message_after_link_cancel_409(v1_seed, client, fake_model):
    """撤销关联后旧会话发消息 → 409 JSON（非事件流），旧上下文不得继续作答。"""
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]

    from app.db.models import SessionLocal
    from app.db.workspace_models import HomeroomTeachingLink

    db = SessionLocal()
    link = db.get(HomeroomTeachingLink, v1_seed.link_id)
    original_status, original_version, original_cancelled_at = (
        link.status,
        link.version,
        link.cancelled_at,
    )
    try:
        link.status = "cancelled"
        link.version = (link.version or 1) + 1
        link.cancelled_at = datetime.utcnow()
        db.commit()

        r = client.post(
            f"/api/v1/chat/sessions/{sid}/messages", json={"content": "还在吗"}
        )
        assert r.status_code == 409, r.text
        body = r.json()
        assert body["error"] == "link_version_conflict"
        assert "text/event-stream" not in (r.headers.get("content-type") or "")
    finally:
        link = db.get(HomeroomTeachingLink, v1_seed.link_id)
        link.status = original_status
        link.version = original_version
        link.cancelled_at = original_cancelled_at
        db.commit()
        db.close()


def test_a02_message_after_member_drift_409(v1_seed, client, fake_model):
    """成员漂移（一人离班）后旧会话发消息 → 409 link_version_conflict。"""
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]

    from app.db.models import SessionLocal
    from app.db.workspace_models import Enrollment

    db = SessionLocal()
    enrollment = (
        db.query(Enrollment)
        .filter(
            Enrollment.admin_class_id == v1_seed.h6_id,
            Enrollment.identity_id == v1_seed.bing_h_id,
        )
        .one()
    )
    original_valid_to = enrollment.valid_to
    try:
        # valid_to 早于今天 → 当期成员集合比快照少一人
        enrollment.valid_to = date.today() - timedelta(days=1)
        db.commit()

        r = client.post(
            f"/api/v1/chat/sessions/{sid}/messages", json={"content": "班级情况如何"}
        )
        assert r.status_code == 409, r.text
        body = r.json()
        assert body["error"] == "link_version_conflict"
        drift = body.get("drift") or {}
        assert "member_person_ids" in drift
    finally:
        enrollment = db.get(Enrollment, enrollment.id)
        enrollment.valid_to = original_valid_to
        db.commit()
        db.close()


# ────────────────────── Q02：会话绑定关联版本集合 ──────────────────────


def test_q02_teaching_session_link_cancel_409(v1_seed, client, fake_model):
    """Q02 核心反例：T6 教学会话 → 正式 cancel link → 发消息 409（H 侧
    取消不能代表 T 侧；快照冻结的可见关联集合随重放变化）。"""
    body = _create_session(
        client, {"mode": "teaching", "teaching_class_id": v1_seed.t6_id, "subject": "物理"}
    ).json()
    # 快照冻结 T6 的 active 关联
    assert body["scope"]["links"] == [
        {
            "link_id": v1_seed.link_id,
            "version": 1,
            "status": "active",
            "linked_pairs": 2,
        }
    ]

    r_cancel = client.post(f"/api/v1/shared/links/{v1_seed.link_id}/cancel")
    assert r_cancel.status_code == 200, r_cancel.text
    try:
        r = client.post(
            f"/api/v1/chat/sessions/{body['session_id']}/messages",
            json={"content": "还在吗"},
        )
        assert r.status_code == 409, r.text
        assert r.json()["error"] == "link_version_conflict"
        assert "text/event-stream" not in (r.headers.get("content-type") or "")
        drift = r.json().get("drift") or {}
        assert "links" in drift  # 关联集合漂移可见
    finally:
        from app.db.models import SessionLocal
        from app.db.workspace_models import HomeroomTeachingLink

        db = SessionLocal()
        link = db.get(HomeroomTeachingLink, v1_seed.link_id)
        link.status = "active"
        link.version = 1
        link.cancelled_at = None
        db.commit()
        db.close()


def test_q02_share_scope_tighten_409(v1_seed, client, fake_model):
    """share-scope 收紧（version+1）→ 旧会话发消息 409。"""
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    r = client.post(
        f"/api/v1/shared/links/{v1_seed.link_id}/share-scope",
        json={"share_categories": ["roster"]},
    )
    assert r.status_code == 200, r.text
    try:
        msg = client.post(
            f"/api/v1/chat/sessions/{sid}/messages", json={"content": "历史成绩如何"}
        )
        assert msg.status_code == 409, msg.text
        assert msg.json()["error"] == "link_version_conflict"
    finally:
        from app.db.models import SessionLocal
        from app.db.workspace_models import HomeroomTeachingLink

        db = SessionLocal()
        link = db.get(HomeroomTeachingLink, v1_seed.link_id)
        link.share_categories = "roster,current_subject_score"
        link.share_history_from = None
        link.version = 1
        db.commit()
        db.close()


def test_q02_pair_delete_409(v1_seed, client, fake_model):
    """配对删除（不改 version）→ 成员映射指纹变化 → 409。"""
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]

    from app.db.models import SessionLocal
    from app.db.workspace_models import LinkedStudent

    db = SessionLocal()
    pair = (
        db.query(LinkedStudent).filter(LinkedStudent.link_id == v1_seed.link_id).first()
    )
    snapshot_pair = {
        "link_id": pair.link_id,
        "homeroom_identity_id": pair.homeroom_identity_id,
        "teaching_identity_id": pair.teaching_identity_id,
        "confirm_basis": pair.confirm_basis,
    }
    db.delete(pair)
    db.commit()
    try:
        msg = client.post(
            f"/api/v1/chat/sessions/{sid}/messages", json={"content": "名单对齐吗"}
        )
        assert msg.status_code == 409, msg.text
        assert msg.json()["error"] == "link_version_conflict"
    finally:
        db.add(LinkedStudent(**snapshot_pair))
        db.commit()
        db.close()


def test_q02_pair_swap_same_count_409(v1_seed, client, fake_model):
    """V01（审核）反例照抄：T6 会话 → 删除原两对 → 正式 POST 配对
    API 交叉重配（甲h↔乙t、乙h↔甲t，全 200）——班级/成员/对数全部不变
    （修复前指纹只看对数 → 无漂移 → 旧会话沿用旧身份映射继续作答），冻结
    配对集合指纹后旧会话发消息必须 409。"""
    body = _create_session(
        client, {"mode": "teaching", "teaching_class_id": v1_seed.t6_id, "subject": "物理"}
    ).json()
    listed = client.get(f"/api/v1/shared/links/{v1_seed.link_id}/students")
    assert listed.status_code == 200, listed.text
    pairs = listed.json()["pairs"]
    assert len(pairs) == 2

    # 删除原两对（正式 DELETE API；配对增删不递增 link.version）
    for pair in pairs:
        r_del = client.delete(
            f"/api/v1/shared/links/{v1_seed.link_id}/students/{pair['linked_id']}"
        )
        assert r_del.status_code == 200, r_del.text
    try:
        # 正式 POST 配对 API 交叉重配：两对全 200，对数与成员集合不变
        r_add = client.post(
            f"/api/v1/shared/links/{v1_seed.link_id}/students",
            json={
                "pairs": [
                    {
                        "homeroom_person_id": v1_seed.jia_h_id,
                        "teaching_person_id": v1_seed.yi_t_id,
                    },
                    {
                        "homeroom_person_id": v1_seed.yi_h_id,
                        "teaching_person_id": v1_seed.jia_t_id,
                    },
                ]
            },
        )
        assert r_add.status_code == 200, r_add.text
        assert r_add.json()["created"] == 2

        msg = client.post(
            f"/api/v1/chat/sessions/{body['session_id']}/messages",
            json={"content": "名单对齐吗"},
        )
        assert msg.status_code == 409, msg.text
        assert msg.json()["error"] == "link_version_conflict"
        assert "links" in (msg.json().get("drift") or {})

        # 新会话以替换后的配对为基准：快照与现状一致，正常作答（指纹不是
        # 一律 409，只在快照与现状不一致时失效）
        fresh = _create_session(
            client,
            {"mode": "teaching", "teaching_class_id": v1_seed.t6_id, "subject": "物理"},
        ).json()
        ok = client.post(
            f"/api/v1/chat/sessions/{fresh['session_id']}/messages",
            json={"content": "新会话正常作答"},
        )
        assert ok.status_code == 200, ok.text
        frames = _frames(ok.text)
        assert frames[-1]["type"] == "done"
        assert not any(f["type"] == "error" for f in frames)
    finally:
        # 恢复种子两对，避免污染同模块其他用例
        from app.db.models import SessionLocal
        from app.db.workspace_models import LinkedStudent

        db = SessionLocal()
        try:
            db.query(LinkedStudent).filter(
                LinkedStudent.link_id == v1_seed.link_id
            ).delete()
            db.add_all(
                LinkedStudent(
                    link_id=v1_seed.link_id,
                    homeroom_identity_id=h_id,
                    teaching_identity_id=t_id,
                    confirm_basis="synthetic-test",
                )
                for h_id, t_id in (
                    (v1_seed.jia_h_id, v1_seed.jia_t_id),
                    (v1_seed.yi_h_id, v1_seed.yi_t_id),
                )
            )
            db.commit()
        finally:
            db.close()


def test_q02_drift_during_tool_rounds_aborts_stream(v1_seed, client, scripted_model):
    """多轮工具执行期间取消关联：第二轮工具执行前重验失效 → 范围失效
    error 帧 + 收流，模型未再收到旧范围数据（Q02 每轮重验核心场景）。"""
    session_id = _create_session(
        client, {"mode": "teaching", "teaching_class_id": v1_seed.t6_id, "subject": "物理"}
    ).json()["session_id"]
    model = scripted_model
    model.holder["responses"] = [
        [model.tool("b1", "search_students", {"q": ""})],
        [model.tool("b2", "get_exam_list", {})],
    ]
    # 第一次 create 时（第一轮工具尚未执行）库状态正常；第二次 create
    # 返回前撤销关联——随后的第二轮工具执行必须被重验拦下
    model.holder["on_call"] = (
        lambda index: _cancel_link(v1_seed.link_id) if index == 1 else None
    )

    r = client.post(
        f"/api/v1/chat/sessions/{session_id}/messages",
        json={"content": "先查名单再看考试"},
    )
    assert r.status_code == 200, r.text
    frames = _frames(r.text)

    tool_results = [f for f in frames if f["type"] == "tool_result"]
    tool_errors = [f for f in frames if f["type"] == "tool_error"]
    errors = [f for f in frames if f["type"] == "error"]
    # 第一轮工具正常返回；第二轮工具调用未产生结果帧（重验拦下）
    assert [f["name"] for f in tool_results] == ["search_students"]
    assert all(f["name"] != "get_exam_list" for f in tool_results + tool_errors)
    assert errors, "必须有范围失效帧"
    assert "范围已变化" in errors[-1]["message"]
    assert errors[-1]["code"] == "scope_drift"
    assert frames[-1]["type"] == "done"
    assert not any(f["type"] == "text" for f in frames), "不得再用旧上下文作答"

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


def test_q02_final_round_drift_anthropic_no_publish(v1_seed, client, scripted_model):
    """V02（审核）Anthropic 分支：最后一次模型调用（本轮即最终轮、
    无工具调用）期间撤销关联——模型返回后、发布文本前重验失效 → 请求仍
    SSE 但只有范围失效帧，旧答复不发布，本轮（含 user 消息）不落历史。"""
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    model = scripted_model
    model.holder["responses"] = [[model.text("旧范围的最终回答")]]
    # 第一次（唯一一次）create 执行期间撤销关联、随后才返回最终文本：
    # 模拟慢生成期间另一会话取消（TestClient 流内不可重入，走库层触发）
    model.holder["on_call"] = (
        lambda index: _cancel_link(v1_seed.link_id) if index == 0 else None
    )

    r = client.post(
        f"/api/v1/chat/sessions/{sid}/messages", json={"content": "总结一下"}
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    frames = _frames(r.text)
    errors = [f for f in frames if f["type"] == "error"]
    assert errors, "必须有范围失效帧"
    assert "范围已变化" in errors[-1]["message"]
    assert errors[-1]["code"] == "scope_drift"
    assert not any(
        f["type"] == "text" for f in frames
    ), "撤销后的旧范围答复不得发布"
    assert frames[-1]["type"] == "done"

    history = client.get(f"/api/v1/chat/sessions/{sid}/messages")
    assert history.status_code == 200, history.text
    assert history.json()["messages"] == [], "失效轮次（含 user 消息）不得落历史"

    _restore_link(v1_seed.link_id)


def test_q02_final_round_drift_openai_no_publish(v1_seed, client, scripted_openai_model):
    """V02（审核）OpenAI 分支：同上，替身注入点为
    chat.completions.create（最终轮无工具调用期间撤销 → 只有失效帧、
    不发布旧答复、不落历史）。"""
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    model = scripted_openai_model
    model.holder["responses"] = ["旧范围的最终回答"]
    model.holder["on_call"] = (
        lambda index: _cancel_link(v1_seed.link_id) if index == 0 else None
    )

    r = client.post(
        f"/api/v1/chat/sessions/{sid}/messages", json={"content": "总结一下"}
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    frames = _frames(r.text)
    errors = [f for f in frames if f["type"] == "error"]
    assert errors, "必须有范围失效帧"
    assert "范围已变化" in errors[-1]["message"]
    assert errors[-1]["code"] == "scope_drift"
    assert not any(
        f["type"] == "text" for f in frames
    ), "撤销后的旧范围答复不得发布"
    assert frames[-1]["type"] == "done"

    history = client.get(f"/api/v1/chat/sessions/{sid}/messages")
    assert history.status_code == 200, history.text
    assert history.json()["messages"] == [], "失效轮次（含 user 消息）不得落历史"

    _restore_link(v1_seed.link_id)


# ────────────────────── Q03：服务端会话历史 ──────────────────────


def test_q03_two_rounds_persisted_and_served(v1_seed, client, scripted_model):
    """两轮对话后 GET messages 返回 4 条（u/a/u/a），内容与顺序一致。"""
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    model = scripted_model
    model.holder["responses"] = [
        [model.text("第一轮：本班均分情况如下。")],
        [model.text("第二轮：秦甲与秦乙的差异如下。")],
    ]
    r1 = client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "总结上次考试"})
    assert r1.status_code == 200
    r2 = client.post(
        f"/api/v1/chat/sessions/{sid}/messages", json={"content": "再说说他们的差异"}
    )
    assert r2.status_code == 200

    history = client.get(f"/api/v1/chat/sessions/{sid}/messages")
    assert history.status_code == 200, history.text
    messages = history.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[0]["content"] == "总结上次考试"
    assert messages[1]["content"] == "第一轮：本班均分情况如下。"
    assert messages[2]["content"] == "再说说他们的差异"
    assert messages[3]["content"] == "第二轮：秦甲与秦乙的差异如下。"
    assert all(m["id"] > 0 for m in messages)
    assert [m["id"] for m in messages] == sorted(m["id"] for m in messages)
    assert all(m["created_at"] for m in messages)
    assert "tool_events" not in messages[0]  # user 行无工具摘要投影


def test_q03_second_model_request_includes_history(v1_seed, client, scripted_model):
    """第二轮模型请求 = 历史 + 本次输入（捕获替身入参：messages 长度>1，
    含第一轮 user/assistant 与第二句 user）。"""
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    model = scripted_model
    model.holder["responses"] = [
        [model.text("第一轮回答")],
        [model.text("第二轮回答")],
    ]
    client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "只比较秦甲和秦乙"})
    client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "再说说他们的差异"})

    first_call, second_call = model.holder["calls"][0], model.holder["calls"][1]
    assert first_call["messages"] == [{"role": "user", "content": "只比较秦甲和秦乙"}]
    assert len(second_call["messages"]) == 3
    assert second_call["messages"][0] == {"role": "user", "content": "只比较秦甲和秦乙"}
    assert second_call["messages"][1] == {"role": "assistant", "content": "第一轮回答"}
    assert second_call["messages"][2] == {"role": "user", "content": "再说说他们的差异"}


def test_q03_tool_events_summary_in_history(v1_seed, client, scripted_model):
    """assistant 行落 tool_events 摘要 JSON（工具名 + 结果状态）。"""
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    model = scripted_model
    model.holder["responses"] = [
        [model.tool("b1", "search_students", {"q": "秦"})],
        [model.text("名单里有秦甲、秦乙、秦丙。")],
    ]
    r = client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "班里有谁"})
    assert r.status_code == 200

    from app.db.models import SessionLocal
    from app.db.workspace_models import ChatMessage

    db = SessionLocal()
    try:
        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == sid)
            .order_by(ChatMessage.id.asc())
            .all()
        )
        assert [row.role for row in rows] == ["user", "assistant"]
        events = json.loads(rows[1].tool_events_json)
        assert events == [{"name": "search_students", "status": "ok"}]
    finally:
        db.close()


def test_q03_drifted_session_history_readonly(v1_seed, client, fake_model):
    """漂移后：GET messages 200 只读（历史保留），发消息仍 409。"""
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    r = client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "总结一下"})
    assert r.status_code == 200

    from app.db.models import SessionLocal
    from app.db.workspace_models import HomeroomTeachingLink

    db = SessionLocal()
    link = db.get(HomeroomTeachingLink, v1_seed.link_id)
    original_status, original_version, original_cancelled_at = (
        link.status,
        link.version,
        link.cancelled_at,
    )
    try:
        link.status = "cancelled"
        link.version = (link.version or 1) + 1
        link.cancelled_at = datetime.utcnow()
        db.commit()

        history = client.get(f"/api/v1/chat/sessions/{sid}/messages")
        assert history.status_code == 200, history.text
        messages = history.json()["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant"]

        blocked = client.post(
            f"/api/v1/chat/sessions/{sid}/messages", json={"content": "继续"}
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"] == "link_version_conflict"
    finally:
        link = db.get(HomeroomTeachingLink, v1_seed.link_id)
        link.status = original_status
        link.version = original_version
        link.cancelled_at = original_cancelled_at
        db.commit()
        db.close()


# ────────────────────── 回归：空回答不落库 / 毒化自愈 / 上限参数 / 截断提示 ──────────────────────


def test_empty_final_text_not_persisted_and_session_survives(
    v1_seed, client, scripted_model
):
    """模型最终轮返回空文本：流正常 done、无 error 帧；本轮（含 user 消息）
    不落历史；下一轮仍正常作答（会话未被空 content 毒化）。"""
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    model = scripted_model
    model.holder["responses"] = [
        [model.text("")],
        [model.text("第二轮正常回答")],
    ]
    r = client.post(
        f"/api/v1/chat/sessions/{sid}/messages", json={"content": "总结一下"}
    )
    assert r.status_code == 200, r.text
    frames = _frames(r.text)
    assert frames[-1]["type"] == "done"
    assert not any(f["type"] == "error" for f in frames)
    assert not any(f["type"] == "text" for f in frames)

    history = client.get(f"/api/v1/chat/sessions/{sid}/messages")
    assert history.status_code == 200, history.text
    assert history.json()["messages"] == [], "空回答轮（含 user 消息）不得落库"

    r2 = client.post(
        f"/api/v1/chat/sessions/{sid}/messages", json={"content": "再总结一次"}
    )
    assert r2.status_code == 200, r2.text
    frames2 = _frames(r2.text)
    assert frames2[-1]["type"] == "done"
    assert any(
        f["type"] == "text" and "第二轮正常回答" in f.get("delta", "") for f in frames2
    ), "后续轮次必须仍正常作答"
    history2 = client.get(f"/api/v1/chat/sessions/{sid}/messages")
    messages = history2.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "再总结一次"


def test_history_blank_content_rows_filtered_from_model_messages(
    v1_seed, client, scripted_model
):
    """历史毒化自愈：库里已有空 content 行（旧缺陷落库）时组装给模型的
    messages 不含空 content 条目，正常轮次照常成功。"""
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]

    from app.db.models import SessionLocal
    from app.db.workspace_models import ChatMessage

    db = SessionLocal()
    try:
        db.add(ChatMessage(session_id=sid, role="assistant", content=""))
        db.add(ChatMessage(session_id=sid, role="user", content="上一轮的问题"))
        db.commit()
    finally:
        db.close()

    model = scripted_model
    model.holder["responses"] = [[model.text("本轮回答")]]
    r = client.post(
        f"/api/v1/chat/sessions/{sid}/messages", json={"content": "本轮问题"}
    )
    assert r.status_code == 200, r.text
    frames = _frames(r.text)
    assert frames[-1]["type"] == "done"
    assert not any(f["type"] == "error" for f in frames)

    call = model.holder["calls"][0]
    sent = call["messages"]
    assert all(
        (m.get("content") or "").strip() for m in sent
    ), "发给模型的 messages 不得含空 content 条目"
    assert {"role": "user", "content": "上一轮的问题"} in sent
    assert {"role": "user", "content": "本轮问题"} in sent
    assert not any(
        m["role"] == "assistant" and not (m["content"] or "").strip() for m in sent
    )


def test_anthropic_uses_config_max_tokens_default_16384(
    v1_seed, client, scripted_model
):
    """Anthropic 分支 max_tokens 取 ChatConfig.max_tokens（缺省 16384，
    不再是旧的硬编码 4096）。"""
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    model = scripted_model
    model.holder["responses"] = [[model.text("回答")]]
    r = client.post(
        f"/api/v1/chat/sessions/{sid}/messages", json={"content": "总结一下"}
    )
    assert r.status_code == 200, r.text
    assert model.holder["calls"][0]["max_tokens"] == 16384


def test_openai_omits_max_tokens_param(v1_seed, client, scripted_openai_model):
    """OpenAI 分支完全不传 max_tokens（用户要求 OpenAI 通道不设上限）。"""
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    model = scripted_openai_model
    model.holder["responses"] = ["回答"]
    r = client.post(
        f"/api/v1/chat/sessions/{sid}/messages", json={"content": "总结一下"}
    )
    assert r.status_code == 200, r.text
    assert "max_tokens" not in model.holder["calls"][0]


def test_anthropic_max_tokens_stop_reason_appends_truncation_notice(
    v1_seed, client, scripted_model
):
    """stop_reason == "max_tokens" → text 帧末尾追加截断提示，不再无声截尾。"""
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    model = scripted_model
    model.holder["responses"] = [
        [model.text("一段很长很长的回答", stop_reason="max_tokens")]
    ]
    r = client.post(
        f"/api/v1/chat/sessions/{sid}/messages", json={"content": "长篇分析"}
    )
    assert r.status_code == 200, r.text
    frames = _frames(r.text)
    text_frames = [f for f in frames if f["type"] == "text"]
    assert text_frames, "必须有文本帧"
    assert any("被截断" in f["delta"] for f in text_frames), "必须有截断提示"
    assert frames[-1]["type"] == "done"
    assert not any(f["type"] == "error" for f in frames)


def test_openai_length_finish_reason_appends_truncation_notice(
    v1_seed, client, scripted_openai_model
):
    """finish_reason == "length" → 同款截断提示（OpenAI 分支）。"""
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    model = scripted_openai_model
    model.holder["responses"] = [
        {"content": "一段很长很长的回答", "finish_reason": "length"}
    ]
    r = client.post(
        f"/api/v1/chat/sessions/{sid}/messages", json={"content": "长篇分析"}
    )
    assert r.status_code == 200, r.text
    frames = _frames(r.text)
    text_frames = [f for f in frames if f["type"] == "text"]
    assert text_frames, "必须有文本帧"
    assert any("被截断" in f["delta"] for f in text_frames), "必须有截断提示"
    assert frames[-1]["type"] == "done"
    assert not any(f["type"] == "error" for f in frames)


# ────────────────────── 系统提示：时间锚点与相对时间翻译规则 ──────────────────────


def test_system_prompt_carries_time_anchor_and_translation_rule(
    v1_seed, client, scripted_model
):
    """系统提示含当前日期/当前学年锚点（流内 db 现查），以及
    「上学年/year_offset」相对时间翻译规则（时间语义批次新增第 6 条）。"""
    sid = _create_session(client, {"mode": "homeroom"}).json()["session_id"]
    model = scripted_model
    model.holder["responses"] = [[model.text("回答")]]
    r = client.post(
        f"/api/v1/chat/sessions/{sid}/messages", json={"content": "上学年成绩如何"}
    )
    assert r.status_code == 200

    system = model.holder["calls"][0]["system"]
    assert "时间锚点" in system
    assert "今天是" in system  # 当前日期（快照 as_of）
    assert "2025-2026" in system  # 当前学年名称（v1_seed 唯一学年）
    assert "当前学期：未分学期" in system  # 种子未建学期
    # 第 6 条翻译规则
    assert "上学年" in system
    assert "year_offset" in system
    assert "term_offset" in system
    assert "get_academic_years" in system


def test_build_system_prompt_without_anchor_backward_compatible(v1_seed):
    """无锚点参数时函数保持可用（既有调用/测试不破坏）；规则文本不变。"""
    from app.api.chat import build_system_prompt

    prompt = build_system_prompt({"mode": "homeroom", "cohort_size": 3})
    assert "时间锚点" not in prompt
    assert "year_offset" in prompt  # 第 6 条规则恒在
    assert "get_homework_correlation" in prompt  # 既有第 5 条不变

    anchored = build_system_prompt(
        {
            "mode": "homeroom",
            "cohort_size": 3,
            "as_of": "2026-09-19",
        },
        {
            "current_date": "2026-09-19",
            "academic_year_name": "2025-2026",
            "term_name": None,
        },
    )
    assert "今天是 2026-09-19" in anchored
    assert "当前学年：2025-2026" in anchored
    assert "当前学期：未分学期" in anchored


def test_system_prompt_rule7_autonomous_analysis(v1_seed):
    """第 7 条「自主分析」恒定注入（不依赖 time_anchor，教学会话同样生效）：
    拿表自算不以"没有对应工具"拒绝、缺考 null 不当 0、考试名模糊匹配提示。"""
    from app.api.chat import build_system_prompt

    plain = build_system_prompt({"mode": "homeroom", "cohort_size": 3})
    anchored = build_system_prompt(
        {
            "mode": "homeroom",
            "cohort_size": 3,
            "as_of": "2026-09-19",
        },
        {
            "current_date": "2026-09-19",
            "academic_year_name": "2025-2026",
            "term_name": None,
        },
    )
    teaching = build_system_prompt(
        {"mode": "teaching", "cohort_size": 5, "subject": "物理"}
    )
    for prompt in (plain, anchored, teaching):
        assert "7. 自主分析" in prompt
        assert "绝不以“没有对应工具”为由拒绝回答" in prompt
        assert "null 不当 0" in prompt
        assert "get_scores_table" in prompt
        assert "get_exam_list" in prompt
        assert "部分名称模糊匹配" in prompt
    # 规则顺序衔接：第 7 条在第 6 条时间语义之后（不重号）
    assert plain.index("6. 时间语义") < plain.index("7. 自主分析")


def test_time_anchor_reads_homework_semester_and_grade(v1_seed):
    """时间锚点学期名读 ws_homework_semester（is_current=1 优先，学期设置页
    同一事实源）；班主任会话系统提示带「当前年级」锚点与学年命名约定/年级
    换算规则，教学会话（无行政班）无年级锚点但学期名仍注入。"""
    from app.api.chat import _time_anchor_of, build_system_prompt
    from app.api.chat_tools import resolve_scope_snapshot
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    db = SessionLocal()
    try:
        db.add(
            wm.WsHomeworkSemester(
                academic_year_id=v1_seed.ay_id,
                name="2025学年第一学期",
                start_date=date(2025, 9, 1),
                end_date=date(2026, 1, 31),
                is_current=1,
                mode="manual",
            )
        )
        db.commit()
        homeroom_snap = resolve_scope_snapshot(db, 1, "homeroom")
        teaching_snap = resolve_scope_snapshot(
            db, 1, "teaching", teaching_class_id=v1_seed.t6_id, subject="物理"
        )
        homeroom_prompt = build_system_prompt(
            homeroom_snap, _time_anchor_of(db, homeroom_snap)
        )
        teaching_prompt = build_system_prompt(
            teaching_snap, _time_anchor_of(db, teaching_snap)
        )
        db.query(wm.WsHomeworkSemester).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()

    # 学期名来自作业学期表（is_current 优先），不再读 P1 Term 表
    assert "当前学期：2025学年第一学期" in homeroom_prompt
    # 年级锚点：快照行政班 grade=2 → 高二（仅班主任会话）
    assert "当前年级：高二" in homeroom_prompt
    # 规则第 6 条：命名约定 + 年级换算（当前是高二 → 高一=year_offset -1）
    assert "2026学年 = 2026 年 9 月开学" in homeroom_prompt
    assert "当前是高二，则高一=year_offset -1" in homeroom_prompt
    assert "学期名形如“2025学年第一学期”" in homeroom_prompt or (
        "学期名形如“2026学年第一学期”" in homeroom_prompt
    )
    # 教学会话：无行政班 → 无年级锚点（年级换算规则整条省略），学期名保留
    assert "当前年级" not in teaching_prompt
    assert "当前是高二" not in teaching_prompt
    assert "当前学期：2025学年第一学期" in teaching_prompt
    assert "2026学年 = 2026 年 9 月开学" in teaching_prompt
