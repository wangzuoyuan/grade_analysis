"""P4 契约用例：名册 CRUD / 追加学号（S08 历史接续）/ 学生报告（I01/I02）。

本文件用例按定义顺序共享 v1_seed 的模块级数据（isolated_module_schema
按模块重建空库）。s08 用例会通过 ORM 建第二学年（2026-2027），此后所有
用例必须显式传 academic_year_id，不能依赖"缺省=最新学年"。
档案域隔离见 test_p4_notes.py，换届全流程见 test_p4_rollover.py。
"""

from datetime import date, timedelta

API = "/api/v1"


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _roster(client, seed, academic_year_id=None):
    params = {"class_id": seed.h6_id}
    if academic_year_id is not None:
        params["academic_year_id"] = academic_year_id
    r = client.get(f"{API}/homeroom/students", params=params)
    assert r.status_code == 200
    return r.json()["students"]


def _find(students, person_id):
    return next((s for s in students if s["person_id"] == person_id), None)


def _identity_id_by_name(db, name):
    from app.db import workspace_models as wm

    row = (
        db.query(wm.WsStudentIdentity)
        .filter(wm.WsStudentIdentity.display_name == name)
        .filter(wm.WsStudentIdentity.data_domain == "homeroom")
        .one()
    )
    return row.id


def test_i01_create_student_lands_identity_alias_enrollment(client, v1_seed):
    r = client.post(
        f"{API}/homeroom/students",
        json={"name": "秦新", "alias": "2025H6-07", "seat_no": 7},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    pid = body["person_id"]
    assert body["status"] == "active" and body["alias"] == "2025H6-07"

    entry = _find(_roster(client, v1_seed, v1_seed.ay_id), pid)
    assert entry is not None
    assert entry["seat_no"] == 7 and entry["alias"] == "2025H6-07"

    from app.db import workspace_models as wm

    db = _db()
    try:
        ident = db.get(wm.WsStudentIdentity, pid)
        assert ident.data_domain == "homeroom" and ident.display_name == "秦新"
        alias = db.query(wm.WsStudentAlias).filter_by(identity_id=pid).one()
        assert alias.alias_value == "2025H6-07"
        assert alias.academic_year_id == v1_seed.ay_id
        assert alias.valid_from == date.today()  # 学年起点与今天较晚者
        enr = db.query(wm.Enrollment).filter_by(identity_id=pid).one()
        assert enr.admin_class_id == v1_seed.h6_id
        assert enr.status == "active"
        assert enr.valid_from == max(date(2025, 9, 1), date.today())
    finally:
        db.close()

    # 无学号新建是合法形态；空名 422
    r2 = client.post(f"{API}/homeroom/students", json={"name": "秦无号"})
    assert r2.status_code == 200 and r2.json()["alias"] is None
    r3 = client.post(f"{API}/homeroom/students", json={"name": "   "})
    assert r3.status_code == 422 and r3.json()["error"] == "invalid_scope_param"


def test_i01_alias_conflict_lists_owner_422(client, v1_seed):
    from app.db import workspace_models as wm

    db = _db()
    try:
        before = (
            db.query(wm.WsStudentIdentity)
            .filter_by(data_domain="homeroom")
            .count()
        )
    finally:
        db.close()

    # 甲的学号已存在 → 422，detail 列出冲突人 person_id/name
    r = client.post(
        f"{API}/homeroom/students",
        json={"name": "秦撞号", "alias": "2025H6-01"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "invalid_scope_param"
    assert any(
        c["person_id"] == v1_seed.jia_h_id and c["name"] == "秦甲"
        for c in body["conflicts"]
    )

    # 冲突路径零写入
    db = _db()
    try:
        assert (
            db.query(wm.WsStudentIdentity).filter_by(data_domain="homeroom").count()
            == before
        )
    finally:
        db.close()


def test_i01_patch_student_only_current_member(client, v1_seed):
    db = _db()
    try:
        pid = _identity_id_by_name(db, "秦新")
    finally:
        db.close()

    r = client.patch(f"{API}/homeroom/students/{pid}", json={"seat_no": 8})
    assert r.status_code == 200 and r.json()["seat_no"] == 8
    r = client.patch(f"{API}/homeroom/students/{pid}", json={"name": "秦新改"})
    assert r.status_code == 200 and r.json()["name"] == "秦新改"
    entry = _find(_roster(client, v1_seed, v1_seed.ay_id), pid)
    assert entry["name"] == "秦新改" and entry["seat_no"] == 8

    # 越界（教学域身份）/不存在人 → 404 resource_out_of_scope
    r = client.patch(f"{API}/homeroom/students/{v1_seed.jia_t_id}", json={"name": "x"})
    assert r.status_code == 404 and r.json()["error"] == "resource_out_of_scope"
    r = client.patch(f"{API}/homeroom/students/999999", json={"name": "x"})
    assert r.status_code == 404
    # 空名 → 422
    assert (
        client.patch(f"{API}/homeroom/students/{pid}", json={"name": ""}).status_code
        == 422
    )


def test_i01_archive_transfer_and_restore(client, v1_seed):
    db = _db()
    try:
        pid = _identity_id_by_name(db, "秦新改")
        # 新建学籍 valid_from=今天；回拨到学年起点才能模拟"已离班"
        # （归档 valid_to 晚于 valid_from 的守卫拒绝倒挂数据）
        from app.db import workspace_models as wm

        enr = db.query(wm.Enrollment).filter_by(identity_id=pid).one()
        enr.valid_from = date(2025, 9, 1)
        db.commit()
    finally:
        db.close()

    # 离班：改当期行 status+valid_to，不物理删
    r = client.post(
        f"{API}/homeroom/students/{pid}/archive",
        json={"status": "transferred", "valid_to": "2026-07-01"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "transferred"
    assert _find(_roster(client, v1_seed, v1_seed.ay_id), pid) is None

    from app.db import workspace_models as wm

    db = _db()
    try:
        enrs = db.query(wm.Enrollment).filter_by(identity_id=pid).all()
        assert len(enrs) == 1  # 不物理删、不追加行
        assert enrs[0].status == "transferred"
        assert enrs[0].valid_to == date(2026, 7, 1)
    finally:
        db.close()

    # 离班必须给 valid_to → 422；非法 status → 422
    r = client.post(
        f"{API}/homeroom/students/{pid}/archive", json={"status": "graduated"}
    )
    assert r.status_code == 422
    r = client.post(
        f"{API}/homeroom/students/{pid}/archive",
        json={"status": "deleted", "valid_to": "2026-07-01"},
    )
    assert r.status_code == 422

    # 恢复 active：valid_to 清空，回到名册
    r = client.post(
        f"{API}/homeroom/students/{pid}/archive", json={"status": "active"}
    )
    assert r.status_code == 200 and r.json()["valid_to"] is None
    assert _find(_roster(client, v1_seed, v1_seed.ay_id), pid) is not None
    db = _db()
    try:
        enr = db.query(wm.Enrollment).filter_by(identity_id=pid).one()
        assert enr.status == "active" and enr.valid_to is None
    finally:
        db.close()

    # 越界 archive（教学域身份）→ 404
    r = client.post(
        f"{API}/homeroom/students/{v1_seed.jia_t_id}/archive",
        json={"status": "active"},
    )
    assert r.status_code == 404


def test_s08_alias_append_closes_old_and_conflicts_422(client, v1_seed):
    jia = v1_seed.jia_h_id
    yi = v1_seed.yi_h_id
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    r = client.get(f"{API}/homeroom/students/{jia}/aliases")
    assert r.status_code == 200
    rows = r.json()["aliases"]
    assert len(rows) == 1 and rows[0]["alias_value"] == "2025H6-01"
    assert rows[0]["valid_to"] is None

    # 追加新学号：旧 alias 收尾（valid_to=前一日），identity 不变
    r = client.post(
        f"{API}/homeroom/students/{jia}/alias",
        json={"alias": "2025H6-01B", "valid_from": today},
    )
    assert r.status_code == 200, r.text
    rows = r.json()["aliases"]
    assert len(rows) == 2
    old = next(a for a in rows if a["alias_value"] == "2025H6-01")
    assert old["valid_to"] == yesterday
    new = next(a for a in rows if a["alias_value"] == "2025H6-01B")
    assert new["valid_from"] == today and new["valid_to"] is None

    # 撞他人当前号 → 422 列冲突（乙）
    r = client.post(
        f"{API}/homeroom/students/{jia}/alias",
        json={"alias": "2025H6-02", "valid_from": today},
    )
    assert r.status_code == 422
    assert any(c["person_id"] == yi for c in r.json()["conflicts"])

    # 撞他人历史号：乙先把 2025H6-02 收尾为历史，甲再要 → 仍 422
    r = client.post(
        f"{API}/homeroom/students/{yi}/alias",
        json={"alias": "2025H6-02B", "valid_from": today},
    )
    assert r.status_code == 200
    r = client.post(
        f"{API}/homeroom/students/{jia}/alias",
        json={"alias": "2025H6-02", "valid_from": today},
    )
    assert r.status_code == 422
    assert any(c["person_id"] == yi for c in r.json()["conflicts"])

    # 同人同学年重复登记同号 → 422（不 500）
    r = client.post(
        f"{API}/homeroom/students/{jia}/alias",
        json={"alias": "2025H6-01B", "valid_from": today},
    )
    assert r.status_code == 422

    # valid_from 早于现有学号生效日 → 422
    r = client.post(
        f"{API}/homeroom/students/{jia}/alias",
        json={
            "alias": "2025H6-01C",
            "valid_from": (date.today() - timedelta(days=10)).isoformat(),
        },
    )
    assert r.status_code == 422

    # 非法入参 / 越界
    assert (
        client.post(
            f"{API}/homeroom/students/{jia}/alias",
            json={"alias": "  ", "valid_from": today},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"{API}/homeroom/students/{jia}/alias",
            json={"alias": "X", "valid_from": "2026/01/01"},
        ).status_code
        == 422
    )
    r = client.post(
        f"{API}/homeroom/students/{v1_seed.jia_t_id}/alias",
        json={"alias": "T-X", "valid_from": today},
    )
    assert r.status_code == 404 and r.json()["error"] == "resource_out_of_scope"
    assert (
        client.get(f"{API}/homeroom/students/{v1_seed.jia_t_id}/aliases").status_code
        == 404
    )


def test_s08_profile_continues_across_years(client, v1_seed):
    """S08：两学年同 identity 各自 alias，画像跨学年接续（同一 person_id）。"""
    from app.db import workspace_models as wm

    db = _db()
    try:
        ay2 = wm.AcademicYear(
            name="2026-2027", start_date=date(2026, 9, 1), end_date=date(2027, 7, 15)
        )
        db.add(ay2)
        db.flush()
        cls2 = wm.AdministrativeClass(
            academic_year_id=ay2.id, grade=2, class_num=6, label="高二6班(新)"
        )
        db.add(cls2)
        db.flush()
        db.add(
            wm.Enrollment(
                admin_class_id=cls2.id,
                identity_id=v1_seed.jia_h_id,
                seat_no=1,
                status="active",
                valid_from=date(2026, 9, 1),
            )
        )
        db.add(
            wm.WsStudentAlias(
                identity_id=v1_seed.jia_h_id,
                alias_value="2026H6-01",
                data_domain="homeroom",
                academic_year_id=ay2.id,
                alias_scope=str(ay2.id),
                valid_from=date(2026, 9, 1),
                source="p4-test",
            )
        )
        db.add(
            wm.ScoreFact(
                data_domain="homeroom",
                academic_year_id=ay2.id,
                exam_name="2026期中",
                exam_date=date(2026, 11, 5),
                class_ref_id=cls2.id,
                identity_id=v1_seed.jia_h_id,
                subject="语文",
                score=90.0,
                source="p4-test",
            )
        )
        db.commit()
        ay2_id = ay2.id
        cls2_id = cls2.id
    finally:
        db.close()

    # 新学年画像：同一 person_id + 新学年考试 → 历史接续
    r = client.get(
        f"{API}/homeroom/students/{v1_seed.jia_h_id}",
        params={"academic_year_id": ay2_id},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["person"]["person_id"] == v1_seed.jia_h_id
    chinese = next(s for s in body["subjects"] if s["subject"] == "语文")
    assert chinese["exams"][0]["exam_name"] == "2026期中"
    assert chinese["exams"][0]["score"] == 90.0

    # 旧学年画像仍在同一身份下
    r = client.get(
        f"{API}/homeroom/students/{v1_seed.jia_h_id}",
        params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
    )
    assert r.status_code == 200
    chinese_old = next(s for s in r.json()["subjects"] if s["subject"] == "语文")
    assert chinese_old["exams"][0]["exam_name"] == "2025期中"

    # 新学年名册可见该成员（P1 homeroom_roster→aliases_for 不带学年参数，
    # 名册 alias 固定回退最早学号，见交付报告关联问题；新学号落库由
    # ORM 建行 + aliases 接口断言覆盖）
    r = client.get(
        f"{API}/homeroom/students",
        params={"academic_year_id": ay2_id, "class_id": cls2_id},
    )
    assert r.status_code == 200
    entry = _find(r.json()["students"], v1_seed.jia_h_id)
    assert entry is not None

    # 别名史跨学年可查（旧号已收尾 + 本学年新号 + 新学年号）
    rows = client.get(f"{API}/homeroom/students/{v1_seed.jia_h_id}/aliases").json()[
        "aliases"
    ]
    assert {a["alias_value"] for a in rows} == {
        "2025H6-01",
        "2025H6-01B",
        "2026H6-01",
    }


def test_report_profile_roster_and_notes_summary(client, v1_seed):
    # 先写一条 homeroom 档案供摘要
    r = client.post(
        f"{API}/homeroom/students/{v1_seed.jia_h_id}/notes",
        json={
            "date": "2026-01-10",
            "category": "谈话",
            "content": "月考后状态波动，已谈心",
            "follow_up": "一周后回访",
        },
    )
    assert r.status_code == 200

    r = client.get(
        f"{API}/homeroom/students/{v1_seed.jia_h_id}/report",
        params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["metadata"]["mode"] == "homeroom"
    assert body["person"]["name"] == "秦甲"
    assert {a["alias_value"] for a in body["person"]["aliases"]} >= {
        "2025H6-01",
        "2025H6-01B",
    }
    assert body["roster"]["class_id"] == v1_seed.h6_id
    assert body["roster"]["seat_no"] == 1 and body["roster"]["status"] == "active"

    # 画像与共享投影：H 物理 90 与 T 91 冲突 → 保留 H 条目并附 shared_conflict
    physics = next(s for s in body["subjects"] if s["subject"] == "物理")
    exam = physics["exams"][0]
    assert exam["source_domain"] == "homeroom"
    assert exam["shared_conflict"] == {"teaching_score": 91.0}
    chinese = next(s for s in body["subjects"] if s["subject"] == "语文")
    assert chinese["exams"][0]["score"] == 88.0
    totals = next(t for t in body["totals"] if t["total_type"] == "主三门")
    assert totals["exams"][0]["score"] == 275.0

    # 档案摘要（homeroom 域）
    assert body["notes_summary"]["count"] == 1
    assert body["notes_summary"]["recent"][0]["category"] == "谈话"

    # 越界 person → 404
    r = client.get(
        f"{API}/homeroom/students/{v1_seed.jia_t_id}/report",
        params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
    )
    assert r.status_code == 404 and r.json()["error"] == "resource_out_of_scope"


def test_i02_history_member_kept_queryable_and_manageable(client, v1_seed):
    bing = v1_seed.bing_h_id
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    # 丙离班（valid_to 已过）
    r = client.post(
        f"{API}/homeroom/students/{bing}/archive",
        json={"status": "transferred", "valid_to": yesterday},
        params={"academic_year_id": v1_seed.ay_id},
    )
    assert r.status_code == 200

    # 不出现在当期名册
    students = _roster(client, v1_seed, v1_seed.ay_id)
    assert _find(students, bing) is None
    assert _find(students, v1_seed.jia_h_id) is not None

    # archive 状态保留可查：别名史 + 学籍行
    rows = client.get(
        f"{API}/homeroom/students/{bing}/aliases",
        params={"academic_year_id": v1_seed.ay_id},
    ).json()["aliases"]
    assert rows and rows[0]["alias_value"] == "2025H6-03"

    from app.db import workspace_models as wm

    db = _db()
    try:
        enr = (
            db.query(wm.Enrollment)
            .filter_by(admin_class_id=v1_seed.h6_id, identity_id=bing)
            .one()
        )
        assert enr.status == "transferred"
        assert enr.valid_to == date.today() - timedelta(days=1)
    finally:
        db.close()

    # 历史成员仍可追加学号（档案留存优先于删除）
    r = client.post(
        f"{API}/homeroom/students/{bing}/alias",
        json={"alias": "2025H6-03B", "valid_from": date.today().isoformat()},
        params={"academic_year_id": v1_seed.ay_id},
    )
    assert r.status_code == 200

    # 无成绩成员（秦新改/秦无号）可正常管理
    db = _db()
    try:
        pid = _identity_id_by_name(db, "秦新改")
    finally:
        db.close()
    r = client.patch(
        f"{API}/homeroom/students/{pid}",
        json={"seat_no": 9},
        params={"academic_year_id": v1_seed.ay_id},
    )
    assert r.status_code == 200 and r.json()["seat_no"] == 9
