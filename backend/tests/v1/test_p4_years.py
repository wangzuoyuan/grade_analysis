"""P4 契约用例：学年 / 学期管理（契约 docs/contracts/p4-students.md §1）。

本文件不依赖 v1_seed（不需要班级/名册）；用例按定义顺序共享模块级空库
（isolated_module_schema 按模块重建）。重点：重名 422、有业务引用改日期
422、start_date 降序、正常 CRUD。
"""

API = "/api/v1"


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def test_years_empty_then_create_order_and_duplicate_422(client):
    # 空态 200
    r = client.get(f"{API}/shared/academic-years")
    assert r.status_code == 200
    assert r.json() == {"years": []}

    # 正常创建
    r = client.post(
        f"{API}/shared/academic-years",
        json={"name": "2025-2026", "start_date": "2025-09-01", "end_date": "2026-07-15"},
    )
    assert r.status_code == 200
    y2025 = r.json()
    assert y2025["name"] == "2025-2026" and y2025["start_date"] == "2025-09-01"

    # 重名 422 invalid_scope_param
    r = client.post(
        f"{API}/shared/academic-years",
        json={"name": "2025-2026", "start_date": "2025-09-01", "end_date": "2026-07-15"},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_scope_param"

    # 第二个学年；GET 按 start_date 降序
    r = client.post(
        f"{API}/shared/academic-years",
        json={"name": "2026-2027", "start_date": "2026-09-01", "end_date": "2027-07-15"},
    )
    assert r.status_code == 200
    r = client.get(f"{API}/shared/academic-years")
    years = r.json()["years"]
    assert [y["name"] for y in years] == ["2026-2027", "2025-2026"]

    # 非法日期格式 → 422（统一错误 JSON，而非 FastAPI 校验形态）
    r = client.post(
        f"{API}/shared/academic-years",
        json={"name": "2027-2028", "start_date": "2027/09/01", "end_date": "2028-07-15"},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_scope_param"
    # 起止倒挂 → 422
    r = client.post(
        f"{API}/shared/academic-years",
        json={"name": "2027-2028", "start_date": "2028-07-15", "end_date": "2027-09-01"},
    )
    assert r.status_code == 422


def test_year_patch_name_ok_date_guarded_by_references(client):
    r = client.get(f"{API}/shared/academic-years")
    years = {y["name"]: y for y in r.json()["years"]}
    y2025 = years["2025-2026"]
    y2026 = years["2026-2027"]

    # 无引用学年：改名/改日期均放行
    r = client.patch(f"{API}/shared/academic-years/{y2026['id']}", json={"name": "2026-2027新"})
    assert r.status_code == 200 and r.json()["name"] == "2026-2027新"
    r = client.patch(
        f"{API}/shared/academic-years/{y2026['id']}",
        json={"start_date": "2026-09-02", "end_date": "2027-07-16"},
    )
    assert r.status_code == 200 and r.json()["start_date"] == "2026-09-02"

    # 行政班引用 → 改日期 422，改名不受限
    from app.db import workspace_models as wm

    db = _db()
    try:
        db.add(
            wm.AdministrativeClass(
                academic_year_id=y2025["id"], grade=2, class_num=6, label="高二6班"
            )
        )
        db.commit()
    finally:
        db.close()
    r = client.patch(
        f"{API}/shared/academic-years/{y2025['id']}", json={"start_date": "2025-09-02"}
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_scope_param"
    r = client.patch(
        f"{API}/shared/academic-years/{y2025['id']}", json={"name": "2025-2026改"}
    )
    assert r.status_code == 200

    # 教学班引用 → 改日期 422
    r = client.post(
        f"{API}/shared/academic-years",
        json={"name": "2027-2028", "start_date": "2027-09-01", "end_date": "2028-07-15"},
    )
    y2027 = r.json()
    db = _db()
    try:
        db.add(
            wm.TeachingClass(
                academic_year_id=y2027["id"], subject="物理", label="高二6班(教)"
            )
        )
        db.commit()
    finally:
        db.close()
    r = client.patch(
        f"{API}/shared/academic-years/{y2027['id']}", json={"end_date": "2028-07-16"}
    )
    assert r.status_code == 422

    # 成绩事实引用 → 改日期 422
    r = client.post(
        f"{API}/shared/academic-years",
        json={"name": "2028-2029", "start_date": "2028-09-01", "end_date": "2029-07-15"},
    )
    y2028 = r.json()
    db = _db()
    try:
        ident = wm.WsStudentIdentity(data_domain="homeroom", display_name="秦引用")
        db.add(ident)
        db.flush()
        db.add(
            wm.ScoreFact(
                data_domain="homeroom",
                academic_year_id=y2028["id"],
                exam_name="2028期中",
                identity_id=ident.id,
                subject="语文",
                score=90.0,
            )
        )
        db.commit()
    finally:
        db.close()
    r = client.patch(
        f"{API}/shared/academic-years/{y2028['id']}", json={"start_date": "2028-09-02"}
    )
    assert r.status_code == 422

    # 不存在的学年 → 404
    r = client.patch(f"{API}/shared/academic-years/999999", json={"name": "x"})
    assert r.status_code == 404
    assert r.json()["error"] == "resource_out_of_scope"


def test_terms_crud_and_duplicate_in_year_422(client):
    r = client.get(f"{API}/shared/academic-years")
    years = {y["name"]: y for y in r.json()["years"]}
    y2025_id = years["2025-2026改"]["id"]
    y2026_id = years["2026-2027新"]["id"]

    # 缺 academic_year_id → 422；不存在的学年 → 404
    assert client.get(f"{API}/shared/terms").status_code == 422
    r = client.get(f"{API}/shared/terms", params={"academic_year_id": 999999})
    assert r.status_code == 404

    # 空态 + 正常创建
    r = client.get(f"{API}/shared/terms", params={"academic_year_id": y2025_id})
    assert r.status_code == 200 and r.json() == {"terms": []}
    r = client.post(
        f"{API}/shared/terms",
        json={
            "academic_year_id": y2025_id,
            "name": "上学期",
            "start_date": "2025-09-01",
            "end_date": "2026-01-20",
        },
    )
    assert r.status_code == 200
    term1 = r.json()
    assert term1["academic_year_id"] == y2025_id

    # 同学年重名 → 422；不同学年同名合法
    r = client.post(
        f"{API}/shared/terms",
        json={
            "academic_year_id": y2025_id,
            "name": "上学期",
            "start_date": "2026-02-20",
            "end_date": "2026-07-01",
        },
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_scope_param"
    r = client.post(
        f"{API}/shared/terms",
        json={
            "academic_year_id": y2026_id,
            "name": "上学期",
            "start_date": "2026-09-02",
            "end_date": "2027-01-20",
        },
    )
    assert r.status_code == 200

    # 列表按 start_date 升序
    client.post(
        f"{API}/shared/terms",
        json={
            "academic_year_id": y2025_id,
            "name": "下学期",
            "start_date": "2026-02-20",
            "end_date": "2026-07-01",
        },
    )
    r = client.get(f"{API}/shared/terms", params={"academic_year_id": y2025_id})
    assert [t["name"] for t in r.json()["terms"]] == ["上学期", "下学期"]

    # 学年不存在 → 404；非法日期 → 422
    r = client.post(
        f"{API}/shared/terms",
        json={
            "academic_year_id": 999999,
            "name": "上学期",
            "start_date": "2025-09-01",
            "end_date": "2026-01-20",
        },
    )
    assert r.status_code == 404
    r = client.post(
        f"{API}/shared/terms",
        json={
            "academic_year_id": y2025_id,
            "name": "坏学期",
            "start_date": "2025.09.01",
            "end_date": "2026-01-20",
        },
    )
    assert r.status_code == 422
