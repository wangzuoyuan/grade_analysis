"""学生成长/谈话档案路由测试（真实 DB，自建自清理，不留脏数据）。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

SID = "9900636"  # 沈知远（class 6 真实学号）


@pytest.fixture
def client():
    return TestClient(app)


def test_note_crud_and_followup(client):
    # 建
    r = client.post(
        "/api/notes",
        json={"student_id": SID, "category": "谈话", "content": "测试谈话内容", "follow_up": "一周后再谈"},
    )
    assert r.status_code == 200
    note = r.json()
    nid = note["id"]
    assert note["category"] == "谈话"
    assert note["follow_up_done"] is False

    # 查
    rows = client.get(f"/api/notes/{SID}").json()
    assert any(n["id"] == nid for n in rows)

    # 改：勾选跟进完成
    r = client.put(f"/api/notes/{nid}", json={"follow_up_done": True})
    assert r.status_code == 200
    assert r.json()["follow_up_done"] is True

    # 非法分类回落到「其他」
    r = client.put(f"/api/notes/{nid}", json={"category": "乱填"})
    assert r.json()["category"] == "谈话"  # 非法值被忽略，保留原值

    # 删（清理）
    assert client.delete(f"/api/notes/{nid}").status_code == 200
    assert all(n["id"] != nid for n in client.get(f"/api/notes/{SID}").json())


def test_empty_content_rejected(client):
    r = client.post("/api/notes", json={"student_id": SID, "content": "   "})
    assert r.status_code == 400


def test_notes_visible_across_linked_student_ids(client):
    """挂在 g1 学号的档案，经 g2 学号查询可见（person union，03 期）。

    用 identity 层把两个合成学号链为同一人，建档案于一号、从另一号验证可见，
    最后清理（删 note + unlink + 删 identity），不污染其它用例。
    """
    from app.db.models import SessionLocal, StudentIdentity, StudentNote, StudentAlias
    from app.analysis.identity import ensure_identity, link_aliases, unlink_alias

    G1, G2 = "union_g1_note", "union_g2_note"
    s = SessionLocal()
    try:
        iid = ensure_identity(s, display_name="档案联合测试")
        link_aliases(s, iid, [(G1, 1), (G2, 2)], "manual")
    finally:
        s.close()

    # 档案挂在 g1 学号
    r = client.post(
        "/api/notes",
        json={"student_id": G1, "category": "观察", "content": "档案联合测试内容"},
    )
    assert r.status_code == 200, r.text
    nid = r.json()["id"]

    try:
        # 经 g2 学号查询可见（person union）
        rows = client.get(f"/api/notes/{G2}").json()
        assert any(n["id"] == nid for n in rows), "g2 学号应能看到 g1 学号的档案"
        # 经 g1 学号查询同样可见
        rows2 = client.get(f"/api/notes/{G1}").json()
        assert any(n["id"] == nid for n in rows2)
    finally:
        # 清理：删 note + unlink 两个 alias + 删 identity
        client.delete(f"/api/notes/{nid}")
        s = SessionLocal()
        try:
            unlink_alias(s, G1)
            unlink_alias(s, G2)
            s.query(StudentIdentity).filter(StudentIdentity.id == iid).delete()
            s.query(StudentNote).filter(StudentNote.student_id.in_([G1, G2])).delete()
            s.commit()
        finally:
            s.close()
