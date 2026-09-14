"""AI 班级作用域和工具 SSE 状态契约。"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.chat import session, tools
from app.db.models import ClassRoster, HomeworkRecord, SpecialRecord, Teacher
from app.main import app


def _events(chunks):
    return [json.loads(chunk.removeprefix("data: ").strip()) for chunk in chunks]


def test_scope_is_validated_against_teacher_binding(db_session):
    db_session.query(Teacher).delete()
    db_session.add(Teacher(target_class_high1=4))
    db_session.commit()

    resolved = session.resolve_chat_scope({"grade": 1, "class_num": 4, "page": {"pathname": "/"}})
    assert resolved["grade"] == 1
    assert resolved["class_num"] == 4

    with pytest.raises(HTTPException, match="已绑定班级不一致") as mismatch:
        session.resolve_chat_scope({"grade": 1, "class_num": 5})
    assert mismatch.value.status_code == 409

    with pytest.raises(HTTPException, match="尚未配置班级作用域"):
        session.resolve_chat_scope({})


def test_chat_endpoint_rejects_missing_scope_before_streaming(db_session):
    db_session.query(Teacher).delete()
    db_session.add(Teacher(target_class_high1=4))
    db_session.commit()

    response = TestClient(app).post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "分析班级"}], "context": {}},
    )
    assert response.status_code == 409
    assert "尚未配置班级作用域" in response.json()["detail"]


def test_system_prompt_contains_verified_scope():
    prompt = session.build_system_prompt({"grade": 2, "class_num": 11})
    assert "当前班级作用域" in prompt
    assert '"grade": 2' in prompt
    assert '"class_num": 11' in prompt


def test_execute_tool_overrides_model_scope(monkeypatch):
    captured = {}

    def fake_tool(grade=None, class_num=None):
        captured.update(grade=grade, class_num=class_num)
        return {"ok": True}

    monkeypatch.setitem(tools.TOOL_FUNCTIONS, "band_trend", fake_tool)
    result = tools.execute_tool(
        "band_trend",
        {"grade": 3, "class_num": 99},
        {"grade": 1, "class_num": 4},
    )
    assert result == {"ok": True}
    assert captured == {"grade": 1, "class_num": 4}


def test_execute_tool_filters_students_outside_scope(db_session, monkeypatch):
    db_session.query(ClassRoster).delete()
    db_session.add_all(
        [
            ClassRoster(student_id="inside", name="本班", grade=1, class_num=4),
            ClassRoster(student_id="outside", name="外班", grade=1, class_num=5),
        ]
    )
    db_session.commit()
    monkeypatch.setitem(
        tools.TOOL_FUNCTIONS,
        "student_lookup",
        lambda **_kwargs: [
            {"student_id": "inside", "name": "本班"},
            {"student_id": "outside", "name": "外班"},
        ],
    )

    result = tools.execute_tool("student_lookup", {"name": "同名"}, {"grade": 1, "class_num": 4})
    assert result == [{"student_id": "inside", "name": "本班"}]


def test_homework_ranking_forces_verified_class_and_excludes_other_class(db_session):
    db_session.query(HomeworkRecord).delete()
    db_session.query(ClassRoster).delete()
    db_session.add_all(
        [
            ClassRoster(student_id="inside", name="本班学生", grade=1, class_num=4),
            ClassRoster(student_id="outside", name="外班学生", grade=1, class_num=5),
            HomeworkRecord(student_id="inside", date="2026-03-01", subject="数学"),
            HomeworkRecord(student_id="outside", date="2026-03-01", subject="数学"),
            HomeworkRecord(student_id="outside", date="2026-03-02", subject="数学"),
            HomeworkRecord(student_id="outside", date="2026-03-03", subject="数学"),
        ]
    )
    db_session.commit()

    result = tools.execute_tool(
        "class_homework_ranking",
        {"class_num": 5, "start_date": "2026-01-01", "end_date": "2026-12-31"},
        {"grade": 1, "class_num": 4},
    )

    assert result["class_num"] == 4
    assert result["rankings"] == [{"name": "本班学生", "miss_count": 1}]


@pytest.mark.parametrize(
    "tool_name",
    sorted(tools._PRIVATE_STUDENT_TOOLS),
)
def test_every_private_student_tool_resolves_name_inside_scope(
    db_session, monkeypatch, tool_name
):
    # 外键开启：homework/special 子行指向 class_roster，须先清子行再清父行
    db_session.query(SpecialRecord).delete()
    db_session.query(HomeworkRecord).delete()
    db_session.query(ClassRoster).delete()
    db_session.add_all(
        [
            ClassRoster(student_id="inside", name="同名学生", grade=1, class_num=4),
            ClassRoster(student_id="outside", name="同名学生", grade=1, class_num=5),
        ]
    )
    db_session.commit()
    captured = {}

    def fake_private_tool(**kwargs):
        captured.update(kwargs)
        return {"student_id": kwargs["student_id"], "private": "本班数据"}

    monkeypatch.setitem(tools.TOOL_FUNCTIONS, tool_name, fake_private_tool)
    result = tools.execute_tool(
        tool_name,
        {"name": "同名学生"},
        {"grade": 1, "class_num": 4},
    )

    assert result["student_id"] == "inside"
    assert captured["student_id"] == "inside"
    assert "name" not in captured


def test_private_student_tool_rejects_outside_name_before_execution(db_session, monkeypatch):
    # 外键开启：homework/special 子行指向 class_roster，须先清子行再清父行
    db_session.query(SpecialRecord).delete()
    db_session.query(HomeworkRecord).delete()
    db_session.query(ClassRoster).delete()
    db_session.add(ClassRoster(student_id="outside", name="外班学生", grade=1, class_num=5))
    db_session.commit()
    called = False

    def fake_notes(**_kwargs):
        nonlocal called
        called = True
        return {"student": {"student_id": "outside"}, "notes": ["外班私密档案"]}

    monkeypatch.setitem(tools.TOOL_FUNCTIONS, "student_notes", fake_notes)
    result = tools.execute_tool(
        "student_notes",
        {"name": "外班学生"},
        {"grade": 1, "class_num": 4},
    )

    assert result == {"error": "该学生不属于当前班级作用域"}
    assert called is False
    assert "notes" not in result


def test_private_student_tool_rejects_entire_nested_foreign_result(db_session, monkeypatch):
    # 外键开启：homework/special 子行指向 class_roster，须先清子行再清父行
    db_session.query(SpecialRecord).delete()
    db_session.query(HomeworkRecord).delete()
    db_session.query(ClassRoster).delete()
    db_session.add_all(
        [
            ClassRoster(student_id="inside", name="本班学生", grade=1, class_num=4),
            ClassRoster(student_id="outside", name="外班学生", grade=1, class_num=5),
        ]
    )
    db_session.commit()
    monkeypatch.setitem(
        tools.TOOL_FUNCTIONS,
        "student_learning_profile",
        lambda **_kwargs: {
            "student": {"student_id": "inside", "name": "本班学生"},
            "profile": {
                "related_student": {"student_id": "outside", "name": "外班学生"},
                "notes": ["不应保留的外班私密内容"],
            },
        },
    )

    result = tools.execute_tool(
        "student_learning_profile",
        {"student_id": "inside"},
        {"grade": 1, "class_num": 4},
    )

    assert result == {"error": "该学生不属于当前班级作用域"}
    assert "profile" not in result


def test_openai_stream_emits_real_tool_result(monkeypatch):
    tool_call = SimpleNamespace(
        id="openai-1",
        function=SimpleNamespace(name="band_trend", arguments='{"grade": 3}'),
    )
    responses = iter(
        [
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tool_call]), finish_reason="tool_calls")]
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="分析完成", tool_calls=[]), finish_reason="stop")]
            ),
        ]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: next(responses)))
    )
    monkeypatch.setattr(session, "create_openai_client", lambda _config: client)
    monkeypatch.setattr(tools, "execute_tool", lambda name, args, scope: {"rows": [1], "scope": scope})

    config = SimpleNamespace(model="test")
    async def collect():
        return [chunk async for chunk in session._stream_openai(config, [{"role": "user", "content": "test"}], {"grade": 1, "class_num": 4})]

    chunks = asyncio.run(collect())
    events = _events(chunks)
    assert [event["type"] for event in events] == ["tool_call", "tool_result", "text", "done"]
    assert events[1]["call_id"] == "openai-1"
    assert events[1]["output"]["scope"] == {"grade": 1, "class_num": 4}


def test_anthropic_stream_emits_real_tool_error(monkeypatch):
    class ToolBlock:
        type = "tool_use"
        id = "anthropic-1"
        name = "student_notes"
        input = {"student_id": "outside"}

    class TextBlock:
        type = "text"
        text = "工具失败"

    responses = iter(
        [
            SimpleNamespace(content=[ToolBlock()], stop_reason="tool_use"),
            SimpleNamespace(content=[TextBlock()], stop_reason="end_turn"),
        ]
    )
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **_kwargs: next(responses)))
    config = SimpleNamespace(
        provider="anthropic",
        model="test",
        is_configured=True,
        api_key="test",
        base_url=None,
    )
    monkeypatch.setattr(session, "get_chat_config", lambda: config)
    monkeypatch.setattr(session, "create_anthropic_client", lambda _config: client)
    monkeypatch.setattr(tools, "execute_tool", lambda name, args, scope: {"error": "该学生不属于当前班级作用域"})

    async def collect():
        return [chunk async for chunk in session.stream_chat([{"role": "user", "content": "test"}], {"grade": 1, "class_num": 4})]

    chunks = asyncio.run(collect())
    events = _events(chunks)
    assert [event["type"] for event in events] == ["tool_call", "tool_error", "text", "done"]
    assert events[1] == {
        "type": "tool_error",
        "call_id": "anthropic-1",
        "name": "student_notes",
        "error": "该学生不属于当前班级作用域",
    }
