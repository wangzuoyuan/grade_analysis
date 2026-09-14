"""P4 契约用例：学生档案（WsStudentNote）N01 域隔离 + 同域 CRUD。

本文件用例按定义顺序共享 v1_seed 的模块级数据。N01 红线：档案永不跨域——
homeroom 写的档案 teaching 路径读不到（404/空），反向亦然；直接拿 note id
走另一域路径改/删也必须 404（不泄露存在性）。
"""

API = "/api/v1"


def test_n01_homeroom_note_never_leaks_to_teaching(client, v1_seed):
    jia_h = v1_seed.jia_h_id

    # homeroom 域写档案（家访）
    r = client.post(
        f"{API}/homeroom/students/{jia_h}/notes",
        json={
            "date": "2026-03-05",
            "category": "家访",
            "content": "家长反馈作息问题，约定周日晚电话回访",
            "follow_up": "3月12日电话回访",
        },
    )
    assert r.status_code == 200, r.text
    note = r.json()
    assert note["category"] == "家访" and note["follow_up_done"] == 0

    # 本域可读：恰好 1 条、内容一致
    r = client.get(f"{API}/homeroom/students/{jia_h}/notes")
    assert r.status_code == 200
    rows = r.json()["notes"]
    assert len(rows) == 1 and rows[0]["id"] == note["id"]

    # N01：teaching 路径读不到 homeroom 域档案
    # 1) homeroom 身份经 teaching 路径 → 404（不在教学名册）
    r = client.get(f"{API}/teaching/students/{jia_h}/notes")
    assert r.status_code == 404 and r.json()["error"] == "resource_out_of_scope"
    # 2) 配对学生甲T 自己的教学档案为空（不得借出班主任档案）
    r = client.get(f"{API}/teaching/students/{v1_seed.jia_t_id}/notes")
    assert r.status_code == 200
    assert r.json()["notes"] == []
    # 3) 拿 homeroom note id 走 teaching 路径改/删 → 404，内容不受影响
    r = client.patch(f"{API}/teaching/notes/{note['id']}", json={"content": "越权改"})
    assert r.status_code == 404 and r.json()["error"] == "resource_out_of_scope"
    assert client.delete(f"{API}/teaching/notes/{note['id']}").status_code == 404
    r = client.get(f"{API}/homeroom/students/{jia_h}/notes")
    assert r.json()["notes"][0]["content"] == "家长反馈作息问题，约定周日晚电话回访"


def test_n01_teaching_note_never_leaks_to_homeroom(client, v1_seed):
    jia_t = v1_seed.jia_t_id
    yi_h = v1_seed.yi_h_id  # 前序用例未给乙写过 homeroom 档案，空态干净

    # teaching 域写观察档案（默认作用域为教师任教班并集，甲T 属 T6）
    r = client.post(
        f"{API}/teaching/students/{jia_t}/notes",
        json={
            "date": "2026-03-08",
            "category": "观察",
            "content": "物理课实验操作规范，举手积极",
        },
    )
    assert r.status_code == 200, r.text
    t_note = r.json()

    r = client.get(f"{API}/teaching/students/{jia_t}/notes")
    assert r.status_code == 200
    rows = r.json()["notes"]
    assert len(rows) == 1 and rows[0]["id"] == t_note["id"]

    # N01 反向：homeroom 路径读不到 teaching 域档案
    r = client.get(f"{API}/homeroom/students/{yi_h}/notes")
    assert r.status_code == 200
    assert r.json()["notes"] == []
    # 教学身份不在班主任名册 → 404
    r = client.get(f"{API}/homeroom/students/{jia_t}/notes")
    assert r.status_code == 404
    # 拿 teaching note id 走 homeroom 路径改 → 404
    r = client.patch(
        f"{API}/homeroom/notes/{t_note['id']}", json={"content": "越权改"}
    )
    assert r.status_code == 404 and r.json()["error"] == "resource_out_of_scope"
    # teaching 身份经 homeroom 写路径 → 404
    r = client.post(
        f"{API}/homeroom/students/{jia_t}/notes",
        json={"date": "2026-03-08", "category": "观察", "content": "x"},
    )
    assert r.status_code == 404


def test_note_crud_and_validation(client, v1_seed):
    yi_h = v1_seed.yi_h_id
    wu_t = v1_seed.wu_t_id

    # 同域 CRUD：乙的两条 homeroom 档案，列表按 date 降序
    r1 = client.post(
        f"{API}/homeroom/students/{yi_h}/notes",
        json={"date": "2026-02-01", "category": "谈话", "content": "开学状态回访"},
    )
    assert r1.status_code == 200
    n1 = r1.json()
    r2 = client.post(
        f"{API}/homeroom/students/{yi_h}/notes",
        json={
            "date": "2026-04-01",
            "category": "家长沟通",
            "content": "沟通选科倾向",
            "follow_up": "带选科表",
        },
    )
    assert r2.status_code == 200
    n2 = r2.json()
    rows = client.get(f"{API}/homeroom/students/{yi_h}/notes").json()["notes"]
    assert [n["id"] for n in rows] == [n2["id"], n1["id"]]

    # PATCH：跟进完成 + 改内容 + 改分类
    r = client.patch(
        f"{API}/homeroom/notes/{n2['id']}",
        json={
            "follow_up_done": 1,
            "content": "沟通选科倾向（已面谈）",
            "category": "谈话",
        },
    )
    assert r.status_code == 200
    patched = r.json()
    assert patched["follow_up_done"] == 1
    assert patched["content"] == "沟通选科倾向（已面谈）"
    assert patched["category"] == "谈话"

    # 校验：非法分类 / 坏日期 / 空内容 / follow_up_done 越界 → 422
    r = client.post(
        f"{API}/homeroom/students/{yi_h}/notes",
        json={"date": "2026-04-01", "category": "突击检查", "content": "x"},
    )
    assert r.status_code == 422 and r.json()["error"] == "invalid_scope_param"
    r = client.post(
        f"{API}/homeroom/students/{yi_h}/notes",
        json={"date": "2026.04.01", "category": "谈话", "content": "x"},
    )
    assert r.status_code == 422
    r = client.post(
        f"{API}/homeroom/students/{yi_h}/notes",
        json={"date": "2026-04-01", "category": "谈话", "content": "   "},
    )
    assert r.status_code == 422
    r = client.patch(f"{API}/homeroom/notes/{n1['id']}", json={"follow_up_done": 2})
    assert r.status_code == 422
    # PATCH 不存在的档案 → 404
    assert (
        client.patch(f"{API}/homeroom/notes/999999", json={"content": "x"}).status_code
        == 404
    )

    # homeroom DELETE：删除生效、列表更新、重复删除 404
    r = client.delete(f"{API}/homeroom/notes/{n1['id']}")
    assert r.status_code == 200 and r.json()["success"] is True
    rows = client.get(f"{API}/homeroom/students/{yi_h}/notes").json()["notes"]
    assert [n["id"] for n in rows] == [n2["id"]]
    assert client.delete(f"{API}/homeroom/notes/{n1['id']}").status_code == 404

    # teaching 域：默认作用域（任教班并集）下 T8 成员同样可管理
    r = client.post(
        f"{API}/teaching/students/{wu_t}/notes",
        json={"date": "2026-05-01", "category": "其他", "content": "作业本整洁，提出表扬"},
    )
    assert r.status_code == 200
    w_note = r.json()
    r = client.patch(f"{API}/teaching/notes/{w_note['id']}", json={"follow_up_done": 1})
    assert r.status_code == 200 and r.json()["follow_up_done"] == 1
    r = client.delete(f"{API}/teaching/notes/{w_note['id']}")
    assert r.status_code == 200
    assert client.get(f"{API}/teaching/students/{wu_t}/notes").json()["notes"] == []

    # 非法 mode → 422 invalid_scope_param
    r = client.get(f"{API}/magic/students/{yi_h}/notes")
    assert r.status_code == 422 and r.json()["error"] == "invalid_scope_param"
