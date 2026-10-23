"""旧版聊天工具函数的班级作用域契约。

旧版 AI 聊天 HTTP 端点（POST /api/chat、GET /api/chat/config，app/chat/session.py）
已于 2026-10-23 下线；app/chat/tools.py 的作用域过滤逻辑仍被
MCP 旧工具兼容路径使用，以下直接测工具函数（不经 HTTP）的用例保留。
"""

import pytest

from app.chat import tools
from app.db.models import ClassRoster, HomeworkRecord, SpecialRecord


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
