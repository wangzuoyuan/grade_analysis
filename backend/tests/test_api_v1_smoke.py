"""P1 /api/v1 路由层冒烟测试。

契约样本（docs/contracts/p1-api.md §4）：学年 2025-2026；H6=高二(grade=2)
6 班（教师绑定）；T6=物理「高二6班(教)」（与 H6 关联 active）、T8=物理
「高二8班(教)」（无关联）；甲乙（H6∩T6，已确认 LinkedStudent）、丙（仅
H6）、丁（仅 T6）、戊（仅 T8，与甲同名同裸号）、己（仅 T8）；考试
「2025期中」两域分值故意不同以验证投影来源。

路由挂载方式与 app.main 无关（挂载由集成者完成）：本文件自建 FastAPI
应用并 include create_api_router()。数据经 backend/tests/conftest.py 的
模块级隔离（独立 EXAM_TRACKER_DIR + 重建 schema）。
"""

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import create_api_router
from app.db import workspace_models as wm
from app.db.models import HomeworkSetting, SessionLocal, Teacher

API = "/api/v1"
AY_NAME = "2025-2026"
SUBJECT = "物理"
EXAM = "2025期中"

HOMEROOM_SCORES = {
    "jia": {"语文": 88.0, "数学": 92.0, "英语": 95.0, "物理": 90.0},
    "yi": {"语文": 76.0, "数学": 81.0, "英语": 79.0, "物理": 84.0},
    "bing": {"语文": 65.0, "数学": 70.0, "英语": 72.0, "物理": 68.0},
}
TOTALS = {"jia": 275.0, "yi": 236.0, "bing": 207.0}
TEACHING_T6 = {"jia": 91.0, "yi": 85.0, "ding": None}  # 丁缺考 → null
TEACHING_T8 = {"wu": 77.0, "ji": 82.0}


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(create_api_router())
    return TestClient(app)


@pytest.fixture(scope="module")
def seed(isolated_module_schema):
    db = SessionLocal()
    db.merge(Teacher(id=1, name="测试班主任", target_class_high2=6))
    db.merge(HomeworkSetting(key="active_grade", value="2"))

    ay = wm.AcademicYear(name=AY_NAME, start_date=date(2025, 9, 1), end_date=date(2026, 7, 15))
    db.add(ay)
    db.flush()

    h6 = wm.AdministrativeClass(academic_year_id=ay.id, grade=2, class_num=6, label="高二6班")
    h9 = wm.AdministrativeClass(academic_year_id=ay.id, grade=2, class_num=9, label="高二9班")
    db.add_all([h6, h9])
    db.flush()

    t6 = wm.TeachingClass(academic_year_id=ay.id, subject=SUBJECT, label="高二6班(教)")
    t8 = wm.TeachingClass(academic_year_id=ay.id, subject=SUBJECT, label="高二8班(教)")
    t_empty = wm.TeachingClass(academic_year_id=ay.id, subject=SUBJECT, label="高二6班(教·空)")
    db.add_all([t6, t8, t_empty])
    db.flush()

    def identity(domain, name):
        obj = wm.WsStudentIdentity(data_domain=domain, display_name=name)
        db.add(obj)
        return obj

    jia_h = identity("homeroom", "秦甲")
    yi_h = identity("homeroom", "秦乙")
    bing_h = identity("homeroom", "秦丙")
    jia_t = identity("teaching", "秦甲·T")
    yi_t = identity("teaching", "秦乙·T")
    ding_t = identity("teaching", "秦丁·T")
    wu_t = identity("teaching", "秦甲")  # 与甲同名（跨域碰撞样本）
    ji_t = identity("teaching", "秦己·T")
    db.flush()

    def alias(ident, value, domain):
        db.add(
            wm.WsStudentAlias(
                identity_id=ident.id,
                alias_value=value,
                data_domain=domain,
                source="smoke-test",
            )
        )

    alias(jia_h, "2025H6-01", "homeroom")
    alias(yi_h, "2025H6-02", "homeroom")
    alias(bing_h, "2025H6-03", "homeroom")
    alias(jia_t, "2025T6-01", "teaching")
    alias(yi_t, "2025T6-02", "teaching")
    alias(ding_t, "2025T6-04", "teaching")
    alias(wu_t, "2025T8-01", "teaching")  # 与甲同裸号 01
    alias(ji_t, "2025T8-02", "teaching")

    def enrollment(cls_obj, ident, seat):
        db.add(
            wm.Enrollment(
                admin_class_id=cls_obj.id,
                identity_id=ident.id,
                seat_no=seat,
                status="active",
                valid_from=date(2025, 9, 1),
            )
        )

    enrollment(h6, jia_h, 1)
    enrollment(h6, yi_h, 2)
    enrollment(h6, bing_h, 3)

    def member(cls_obj, ident):
        db.add(
            wm.TeachingClassMember(
                teaching_class_id=cls_obj.id,
                identity_id=ident.id,
                valid_from=date(2025, 9, 1),
                source="manual",
            )
        )

    member(t6, jia_t)
    member(t6, yi_t)
    member(t6, ding_t)
    member(t8, wu_t)
    member(t8, ji_t)

    link = wm.HomeroomTeachingLink(
        admin_class_id=h6.id,
        teaching_class_id=t6.id,
        academic_year_id=ay.id,
        subject=SUBJECT,
        valid_from=date(2025, 9, 1),
        valid_to=None,
        share_categories="roster,current_subject_score",
        status="active",
        version=1,
    )
    db.add(link)
    db.flush()
    for h_ident, t_ident in ((jia_h, jia_t), (yi_h, yi_t)):
        db.add(
            wm.LinkedStudent(
                link_id=link.id,
                homeroom_identity_id=h_ident.id,
                teaching_identity_id=t_ident.id,
                confirm_basis="smoke-test",
            )
        )

    def fact(domain, cls_obj, ident, subject, total_type, score):
        db.add(
            wm.ScoreFact(
                data_domain=domain,
                academic_year_id=ay.id,
                exam_name=EXAM,
                exam_date=date(2025, 11, 6),
                class_ref_id=cls_obj.id,
                identity_id=ident.id,
                subject=subject,
                total_type=total_type,
                score=score,
                source="smoke-test",
            )
        )

    for key, ident in (("jia", jia_h), ("yi", yi_h), ("bing", bing_h)):
        for subject_name, value in HOMEROOM_SCORES[key].items():
            fact("homeroom", h6, ident, subject_name, None, value)
        fact("homeroom", h6, ident, "总分", "主三门", TOTALS[key])
    for key, ident in (("jia", jia_t), ("yi", yi_t), ("ding", ding_t)):
        fact("teaching", t6, ident, SUBJECT, None, TEACHING_T6[key])
    for key, ident in (("wu", wu_t), ("ji", ji_t)):
        fact("teaching", t8, ident, SUBJECT, None, TEACHING_T8[key])

    db.commit()
    try:
        yield SimpleNamespace(
            ay_id=ay.id,
            h6_id=h6.id,
            h9_id=h9.id,
            t6_id=t6.id,
            t8_id=t8.id,
            t_empty_id=t_empty.id,
            link_id=link.id,
            jia_h=jia_h.id,
            yi_h=yi_h.id,
            bing_h=bing_h.id,
            jia_t=jia_t.id,
            yi_t=yi_t.id,
            ding_t=ding_t.id,
            wu_t=wu_t.id,
            ji_t=ji_t.id,
            h_person_ids=[jia_h.id, yi_h.id, bing_h.id],
            t6_person_ids=[jia_t.id, yi_t.id, ding_t.id],
            t8_person_ids=[wu_t.id, ji_t.id],
        )
    finally:
        db.close()


# ────────────────────────────── 配置 / 班级 ──────────────────────────────


def test_config_reports_binding_teaching_and_links(client, seed):
    r = client.get(f"{API}/shared/config")
    assert r.status_code == 200
    body = r.json()

    assert body["teacher"]["id"] == 1
    assert body["homeroom"] == {"configured": True, "grade": 2, "class_num": 6}
    assert body["teaching"] == {"configured": True, "subject": SUBJECT}
    # 契约补丁：服务端解析当前学年，替代客户端按日期推导
    assert body["current_academic_year"] == {"id": seed.ay_id, "name": AY_NAME}

    links = body["links"]
    assert len(links) == 1
    summary = links[0]
    assert summary["id"] == seed.link_id
    assert summary["status"] == "active"
    assert summary["version"] == 1
    assert summary["subject"] == SUBJECT
    assert summary["teaching_class_id"] == seed.t6_id
    # 契约补丁：LinkSummary 带学年名称
    assert summary["academic_year_name"] == AY_NAME


def test_classes_lists_bound_homeroom_and_all_teaching_classes(client, seed):
    r = client.get(f"{API}/shared/classes", params={"academic_year_id": seed.ay_id})
    assert r.status_code == 200
    body = r.json()

    assert body["academic_year_id"] == seed.ay_id
    assert body["academic_year_name"] == AY_NAME
    assert body["homeroom"] == {
        "class_id": seed.h6_id,
        "grade": 2,
        "class_num": 6,
        "label": "高二6班",
    }
    teaching_ids = {item["class_id"] for item in body["teaching"]}
    assert teaching_ids == {seed.t6_id, seed.t8_id, seed.t_empty_id}
    for item in body["teaching"]:
        assert item["subject"] == SUBJECT
        assert item["label"]

    # 非法学年：缺失 / 不存在 → 422 invalid_scope_param（本端点口径）
    assert (
        client.get(f"{API}/shared/classes").status_code == 422
    )
    r2 = client.get(f"{API}/shared/classes", params={"academic_year_id": 999999})
    assert r2.status_code == 422
    assert r2.json()["error"] == "invalid_scope_param"


# ────────────────────────────── 作用域 ──────────────────────────────


def test_scope_homeroom_and_teaching_modes(client, seed):
    r = client.get(
        f"{API}/shared/scope",
        params={"mode": "homeroom", "academic_year_id": seed.ay_id, "class_id": seed.h6_id},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "homeroom"
    assert body["data_domain"] == "homeroom"
    assert body["subject"] is None
    assert body["member_person_ids"] == seed.h_person_ids
    assert body["cohort_size"] == 3
    assert body["link_id"] == seed.link_id
    assert body["link_version"] == 1
    assert body["as_of"]

    r = client.get(
        f"{API}/shared/scope",
        params={
            "mode": "teaching",
            "academic_year_id": seed.ay_id,
            "teaching_class_id": seed.t6_id,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["subject"] == SUBJECT
    assert body["member_person_ids"] == seed.t6_person_ids
    assert body["cohort_size"] == 3

    # 不传 teaching_class_id → 同学年同学科全部所教班成员并集（T6∪T8）
    r = client.get(
        f"{API}/shared/scope",
        params={"mode": "teaching", "academic_year_id": seed.ay_id},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["subject"] == SUBJECT
    assert body["member_person_ids"] == sorted(seed.t6_person_ids + seed.t8_person_ids)
    assert body["cohort_size"] == 5

    # 空成员教学班：合法空态，绝不回退全年级
    r = client.get(
        f"{API}/shared/scope",
        params={
            "mode": "teaching",
            "academic_year_id": seed.ay_id,
            "teaching_class_id": seed.t_empty_id,
        },
    )
    assert r.status_code == 200
    assert r.json()["member_person_ids"] == []
    assert r.json()["cohort_size"] == 0


def test_scope_error_shapes(client, seed):
    r = client.get(
        f"{API}/shared/scope",
        params={"mode": "principal", "academic_year_id": seed.ay_id, "class_id": seed.h6_id},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_scope_param"

    # homeroom 缺 class_id：不得回退教师默认班
    r = client.get(f"{API}/shared/scope", params={"mode": "homeroom", "academic_year_id": seed.ay_id})
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_scope_param"

    # 格式合法但不存在的班 / 学年 → 404 resource_out_of_scope
    r = client.get(
        f"{API}/shared/scope",
        params={"mode": "homeroom", "academic_year_id": seed.ay_id, "class_id": 999999},
    )
    assert r.status_code == 404
    assert r.json()["error"] == "resource_out_of_scope"

    # 存在但非绑定班（H9）→ 404 域边界违规
    r = client.get(
        f"{API}/shared/scope",
        params={"mode": "homeroom", "academic_year_id": seed.ay_id, "class_id": seed.h9_id},
    )
    assert r.status_code == 404
    assert r.json()["error"] == "resource_out_of_scope"


# ────────────────────────────── 学生列表（投影与隔离） ──────────────────────────────


def test_homeroom_students_projection_and_isolation(client, seed):
    r = client.get(
        f"{API}/homeroom/students",
        params={"academic_year_id": seed.ay_id, "class_id": seed.h6_id},
    )
    assert r.status_code == 200
    body = r.json()

    assert body["metadata"]["mode"] == "homeroom"
    assert body["metadata"]["subject"] is None
    assert body["metadata"]["cohort_size"] == 3
    assert body["metadata"]["scope"]["class_id"] == seed.h6_id
    assert body["metadata"]["scope"]["link_id"] == seed.link_id

    students = body["students"]
    assert {s["person_id"] for s in students} == set(seed.h_person_ids)
    names = sorted(s["name"] for s in students)
    assert names == sorted(["秦甲", "秦乙", "秦丙"])
    assert len([n for n in names if n == "秦甲"]) == 1  # 同名同号绝不并档/多行

    by_pid = {s["person_id"]: s for s in students}
    jia = by_pid[seed.jia_h]
    assert jia["alias"] == "2025H6-01"
    assert jia["seat_no"] == 1
    assert jia["linked_teaching_class_id"] == seed.t6_id
    # 契约 v2 §1.4.1：最近一场与 H 域同场同学科值冲突（90 vs 91）→
    # 不设 shared_subject_score，改报 shared_conflict，绝不静默取 teaching 值
    assert jia["shared_subject_score"] is None
    assert jia["shared_conflict"] == {"teaching_score": 91.0}
    assert by_pid[seed.yi_h]["shared_conflict"] == {"teaching_score": 85.0}
    bing = by_pid[seed.bing_h]
    assert bing["shared_subject_score"] is None
    assert bing["shared_conflict"] is None
    assert bing["linked_teaching_class_id"] is None

    # 丁戊己（教学域身份）绝不出现
    for banned in (seed.jia_t, seed.ding_t, seed.wu_t, seed.ji_t):
        assert banned not in by_pid


def test_teaching_students_use_homeroom_roster_projection(client, seed):
    r = client.get(
        f"{API}/teaching/students",
        params={"academic_year_id": seed.ay_id, "teaching_class_id": seed.t6_id},
    )
    assert r.status_code == 200
    body = r.json()

    assert body["metadata"]["mode"] == "teaching"
    assert body["metadata"]["subject"] == SUBJECT
    assert body["metadata"]["cohort_size"] == 3
    assert body["metadata"]["scope"]["teaching_class_id"] == seed.t6_id

    students = body["students"]
    assert {s["person_id"] for s in students} == set(seed.t6_person_ids)
    by_pid = {s["person_id"]: s for s in students}
    # 甲乙经确认关联：name/seat_no 来自 H 名册投影
    assert by_pid[seed.jia_t]["name"] == "秦甲"
    assert by_pid[seed.jia_t]["seat_no"] == 1
    assert by_pid[seed.yi_t]["name"] == "秦乙"
    # 丁只有 teaching 身份：用教学域原始名
    assert by_pid[seed.ding_t]["name"] == "秦丁·T"

    for s in students:
        assert "total_type" not in s

    # 非关联教学班 T8：仅 teaching 域，无 H 投影
    r8 = client.get(
        f"{API}/teaching/students",
        params={"academic_year_id": seed.ay_id, "teaching_class_id": seed.t8_id},
    )
    assert r8.status_code == 200
    t8_students = r8.json()["students"]
    assert {s["person_id"] for s in t8_students} == set(seed.t8_person_ids)
    assert {s["name"] for s in t8_students} == {"秦甲", "秦己·T"}  # 戊用教学域名


# ────────────────────────────── 成绩查询 ──────────────────────────────


def test_homeroom_scores_replace_semantics_and_isolation(client, seed):
    r = client.get(
        f"{API}/scores",
        params={"mode": "homeroom", "academic_year_id": seed.ay_id, "exam_name": EXAM},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["metadata"]["mode"] == "homeroom"
    rows = body["rows"]

    # 3 人 × (4 科 + 1 总分) = 15 行，两域并存不重复计数
    assert len(rows) == 15
    keys = {(r["person_id"], r.get("subject"), r.get("total_type")) for r in rows}
    assert len(keys) == 15
    assert sum(1 for r in rows if r.get("total_type") == "主三门") == 3
    assert sum(1 for r in rows if r.get("subject") == "语文") == 3
    assert sum(1 for r in rows if r.get("subject") == "物理") == 3

    # 关联交集成员（甲乙）物理与 teaching 域同场冲突（90/91、84/85）→
    # 契约 v2 §1.4.1：保留 H 域行并附 shared_conflict，绝不替换/删行
    physics = {r["person_id"]: r for r in rows if r.get("subject") == "物理"}
    assert physics[seed.jia_h]["score"] == 90.0
    assert physics[seed.jia_h]["source_domain"] == "homeroom"
    assert physics[seed.jia_h]["shared_conflict"] == {"teaching_score": 91.0}
    assert physics[seed.yi_h]["score"] == 84.0
    assert physics[seed.yi_h]["shared_conflict"] == {"teaching_score": 85.0}
    assert physics[seed.bing_h]["score"] == 68.0
    assert physics[seed.bing_h]["source_domain"] == "homeroom"
    assert physics[seed.bing_h]["shared_conflict"] is None

    persons = {r["person_id"] for r in rows}
    assert persons == set(seed.h_person_ids)
    for banned in (seed.ding_t, seed.wu_t, seed.ji_t):
        assert banned not in persons
    for row in rows:
        if row.get("source_domain") == "teaching":
            assert row["person_id"] in {seed.jia_h, seed.yi_h}


def test_teaching_scores_subject_only_without_total_key(client, seed):
    r = client.get(
        f"{API}/scores",
        params={"mode": "teaching", "academic_year_id": seed.ay_id, "exam_name": EXAM},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["metadata"]["mode"] == "teaching"
    assert body["metadata"]["subject"] == SUBJECT
    rows = body["rows"]

    assert {r["subject"] for r in rows} == {SUBJECT}
    for r in rows:
        assert "total_type" not in r  # 键必须缺席，而非 null
        assert r["source_domain"] == "teaching"

    persons = {r["person_id"] for r in rows}
    assert persons == set(seed.t6_person_ids + seed.t8_person_ids)
    assert seed.bing_h not in persons

    # 丁缺考：行存在、score 为 null（显示「—」），不得转 0
    ding_rows = [r for r in rows if r["person_id"] == seed.ding_t]
    assert len(ding_rows) == 1
    assert ding_rows[0]["score"] is None


# ────────────────────────────── 画像 ──────────────────────────────


def test_homeroom_profile_includes_teaching_projection(client, seed):
    r = client.get(f"{API}/homeroom/students/{seed.jia_h}")
    assert r.status_code == 200
    body = r.json()
    assert body["person"] == {"person_id": seed.jia_h, "name": "秦甲", "domain": "homeroom"}

    subjects = {g["subject"]: g for g in body["subjects"]}
    assert set(subjects) == {"语文", "数学", "英语", "物理"}
    physics = subjects["物理"]["exams"]
    assert len(physics) == 1
    # 契约 v2 §1.4.1：同场冲突保留 H 域 90 并附提示，不替换为 teaching 域 91
    assert physics[0]["score"] == 90.0
    assert physics[0]["source_domain"] == "homeroom"
    assert physics[0]["shared_conflict"] == {"teaching_score": 91.0}
    assert subjects["语文"]["exams"][0]["source_domain"] == "homeroom"

    totals = {g["total_type"]: g for g in body["totals"]}
    assert totals["主三门"]["exams"][0]["score"] == 275.0


def test_teaching_profile_subject_only(client, seed):
    r = client.get(f"{API}/teaching/students/{seed.jia_t}")
    assert r.status_code == 200
    body = r.json()
    assert body["person"]["domain"] == "teaching"
    assert body["metadata"]["subject"] == SUBJECT
    assert {g["subject"] for g in body["subjects"]} == {SUBJECT}
    assert "totals" not in body  # 教学画像不得出现 totals 键
    exam = body["subjects"][0]["exams"][0]
    assert exam["score"] == 91.0
    assert exam["source_domain"] == "teaching"

    # 丁（缺考）画像：score null
    r_ding = client.get(f"{API}/teaching/students/{seed.ding_t}")
    assert r_ding.status_code == 200
    assert r_ding.json()["subjects"][0]["exams"][0]["score"] is None


def test_profile_and_roster_guards(client, seed):
    # 教学域身份在 H 画像端点 → 404 resource_out_of_scope（含与甲同名的戊）
    for person_id in (seed.wu_t, seed.ji_t):
        r = client.get(f"{API}/homeroom/students/{person_id}")
        assert r.status_code == 404
        assert r.json()["error"] == "resource_out_of_scope"

    # 非绑定班学生列表 → 404
    r = client.get(
        f"{API}/homeroom/students",
        params={"academic_year_id": seed.ay_id, "class_id": seed.h9_id},
    )
    assert r.status_code == 404
    assert r.json()["error"] == "resource_out_of_scope"

    # 空成员教学班：200 空态
    r = client.get(
        f"{API}/teaching/students",
        params={"academic_year_id": seed.ay_id, "teaching_class_id": seed.t_empty_id},
    )
    assert r.status_code == 200
    assert r.json()["students"] == []
    assert r.json()["metadata"]["cohort_size"] == 0


# ────────────────────────────── 关联生命周期 ──────────────────────────────


def _contract_table_counts():
    db = SessionLocal()
    try:
        models = (
            wm.AdministrativeClass, wm.Enrollment, wm.TeachingClass,
            wm.TeachingClassMember, wm.HomeroomTeachingLink, wm.LinkedStudent,
            wm.WsStudentIdentity, wm.WsStudentAlias, wm.ScoreFact, wm.ImportBatch,
        )
        return {model.__tablename__: db.query(model).count() for model in models}
    finally:
        db.close()


def test_links_preview_no_business_writes_and_no_auto_pairing(client, seed):
    before = _contract_table_counts()
    r = client.post(
        f"{API}/shared/links/preview",
        json={
            "admin_class_id": seed.h6_id,
            "teaching_class_id": seed.t6_id,
            "academic_year_id": seed.ay_id,
            "subject": SUBJECT,
        },
    )
    assert r.status_code == 200
    body = r.json()

    assert body["token"]
    assert body["expires_at"]
    diff = body["roster_diff"]
    # 契约 v2 §1.2：both 列出同对班已存在 link 时已确认 LinkedStudent 的
    # 双侧成员（每条配对一条 H 侧 brief）；同名同号候选不进 both；
    # homeroom_only/teaching_only 扣除已配对成员（候选集合与计数一致），
    # 已配对的甲乙不再出现在"未配对"候选列
    assert {b["person_id"] for b in diff["both"]} == {seed.jia_h, seed.yi_h}
    assert {b["person_id"] for b in diff["homeroom_only"]} == {seed.bing_h}
    assert {b["person_id"] for b in diff["teaching_only"]} == {seed.ding_t}
    # 同裸号候选只出现在 warning 文本，供人工确认
    assert "不自动配对" in body["warning"]

    after = _contract_table_counts()
    assert after["import_batch"] == before["import_batch"] + 1  # 仅台账行
    for table, count in before.items():
        if table != "import_batch":
            assert after[table] == count, table

    # 越界行政班 / 学科不符 → 404 / 422
    r_bad_class = client.post(
        f"{API}/shared/links/preview",
        json={
            "admin_class_id": seed.h9_id,
            "teaching_class_id": seed.t6_id,
            "academic_year_id": seed.ay_id,
            "subject": SUBJECT,
        },
    )
    assert r_bad_class.status_code == 404
    r_bad_subject = client.post(
        f"{API}/shared/links/preview",
        json={
            "admin_class_id": seed.h6_id,
            "teaching_class_id": seed.t6_id,
            "academic_year_id": seed.ay_id,
            "subject": "化学",
        },
    )
    assert r_bad_subject.status_code == 422
    assert r_bad_subject.json()["error"] == "invalid_scope_param"


def test_links_confirm_idempotent_reuses_active_link(client, seed):
    preview = client.post(
        f"{API}/shared/links/preview",
        json={
            "admin_class_id": seed.h6_id,
            "teaching_class_id": seed.t6_id,
            "academic_year_id": seed.ay_id,
            "subject": SUBJECT,
        },
    )
    token = preview.json()["token"]

    confirmed = client.post(f"{API}/shared/links/confirm", json={"token": token})
    assert confirmed.status_code == 200
    body = confirmed.json()
    # 同对班已有 active link → 幂等复用同一 link；LinkedStudent 保留 → 2
    assert body["link_id"] == seed.link_id
    assert body["version"] == 1
    assert body["linked_count"] == 2

    # 已消费 token 再次 confirm → 409（契约 v2 §1.2 校验 1：不得再次
    # 触发任何状态变化；幂等语义取消，重试必须重新预览）
    again = client.post(f"{API}/shared/links/confirm", json={"token": token})
    assert again.status_code == 409
    assert again.json()["error"] == "link_version_conflict"

    # 伪造 token → 409 link_version_conflict
    bogus = client.post(f"{API}/shared/links/confirm", json={"token": "no-such-token"})
    assert bogus.status_code == 409
    assert bogus.json()["error"] == "link_version_conflict"


def test_imports_confirm_rejects_tampered_link_version(client, seed):
    prev = client.post(
        f"{API}/imports/preview",
        json={
            "mode": "homeroom",
            "files": [{"filename": "e1.xlsx", "content_digest": "deadbeef01"}],
        },
    )
    assert prev.status_code == 200
    assert prev.json()["items"] == []
    token = prev.json()["token"]

    db = SessionLocal()
    try:
        link = db.get(wm.HomeroomTeachingLink, seed.link_id)
        link.version = (link.version or 1) + 1
        db.commit()
    finally:
        db.close()

    r = client.post(f"{API}/imports/confirm", json={"token": token})
    assert r.status_code == 409
    assert r.json()["error"] == "link_version_conflict"


def test_links_cancel_stops_sharing_immediately(client, seed):
    r = client.post(f"{API}/shared/links/{seed.link_id}/cancel")
    assert r.status_code == 200
    assert r.json() == {"success": True, "status": "cancelled"}

    db = SessionLocal()
    try:
        link = db.get(wm.HomeroomTeachingLink, seed.link_id)
        assert link.status == "cancelled"
        assert link.version >= 2
        assert link.cancelled_at is not None
    finally:
        db.close()

    # links 列表同步反映
    listed = client.get(f"{API}/shared/links", params={"academic_year_id": seed.ay_id})
    assert listed.status_code == 200
    mine = [l for l in listed.json()["links"] if l["id"] == seed.link_id]
    assert mine and mine[0]["status"] == "cancelled"

    # H 侧投影立即消失；本域全科/总分原样保留（物理回落 H 域 90/84/68）
    hr = client.get(
        f"{API}/homeroom/students",
        params={"academic_year_id": seed.ay_id, "class_id": seed.h6_id},
    )
    assert hr.status_code == 200
    for s in hr.json()["students"]:
        assert s.get("shared_subject_score") is None
        assert s.get("linked_teaching_class_id") is None

    sr = client.get(
        f"{API}/scores",
        params={"mode": "homeroom", "academic_year_id": seed.ay_id, "exam_name": EXAM},
    )
    rows = sr.json()["rows"]
    assert all(r.get("source_domain") != "teaching" for r in rows)
    assert len(rows) == 15
    physics = {r["person_id"]: r for r in rows if r.get("subject") == "物理"}
    assert physics[seed.jia_h]["score"] == 90.0
    assert physics[seed.jia_h]["source_domain"] == "homeroom"

    # 教学域原生数据不受影响
    tr = client.get(
        f"{API}/teaching/students",
        params={"academic_year_id": seed.ay_id, "teaching_class_id": seed.t6_id},
    )
    assert {s["person_id"] for s in tr.json()["students"]} == set(seed.t6_person_ids)

    # 重复 cancel 幂等；不存在的 link → 404
    again = client.post(f"{API}/shared/links/{seed.link_id}/cancel")
    assert again.status_code == 200
    assert again.json()["status"] == "cancelled"
    missing = client.post(f"{API}/shared/links/999999/cancel")
    assert missing.status_code == 404
    assert missing.json()["error"] == "resource_out_of_scope"


def test_links_reconfirm_reactivates_cancelled_link(client, seed):
    preview = client.post(
        f"{API}/shared/links/preview",
        json={
            "admin_class_id": seed.h6_id,
            "teaching_class_id": seed.t6_id,
            "academic_year_id": seed.ay_id,
            "subject": SUBJECT,
        },
    )
    token = preview.json()["token"]
    r = client.post(f"{API}/shared/links/confirm", json={"token": token})
    assert r.status_code == 200
    body = r.json()
    assert body["link_id"] == seed.link_id  # 复活原行（唯一键占位）
    assert body["version"] >= 3
    assert body["linked_count"] == 2

    # 复活后：valid_from 重置为当天（ctx.as_of），历史考试（2025-11-06）
    # 早于共享下限 → 按 §1.2.2 阻断，不投影也不报冲突；显式设置
    # share_history_from 才重新开放（见 test_r3_share_gating）。
    hr = client.get(
        f"{API}/homeroom/students",
        params={"academic_year_id": seed.ay_id, "class_id": seed.h6_id},
    )
    jia = next(s for s in hr.json()["students"] if s["person_id"] == seed.jia_h)
    assert jia["shared_subject_score"] is None
    assert jia["shared_conflict"] is None


# ────────────────────────────── 导入骨架 ──────────────────────────────


def test_imports_preview_confirm_skeleton(client, seed):
    prev = client.post(
        f"{API}/imports/preview",
        json={
            "mode": "teaching",
            "files": [{"filename": "physics.xlsx", "content_digest": "cafebabe02"}],
        },
    )
    assert prev.status_code == 200
    body = prev.json()
    assert body["token"] and body["expires_at"]
    assert body["items"] == []  # P1 恒空清单

    confirmed = client.post(f"{API}/imports/confirm", json={"token": body["token"]})
    assert confirmed.status_code == 200
    # P3 契约 §1.2 扩展了 confirm 响应（skipped/revised/exams/
    # students_created/members_synced）；P1 JSON 兼容路径零写入时全为空值
    cb = confirmed.json()
    assert cb["imported"] == 0
    assert cb["skipped"] == 0 and cb["revised"] == 0
    assert cb["exams"] == []
    assert cb["students_created"] == 0 and cb["members_synced"] == 0

    # 已消费 token 再次 confirm → 409（契约 §1.5：与 §1.2 同一规则）
    again = client.post(f"{API}/imports/confirm", json={"token": body["token"]})
    assert again.status_code == 409
    assert again.json()["error"] == "link_version_conflict"

    bogus = client.post(f"{API}/imports/confirm", json={"token": "missing"})
    assert bogus.status_code == 409
    assert bogus.json()["error"] == "link_version_conflict"

    bad_mode = client.post(f"{API}/imports/preview", json={"mode": "whatever", "files": []})
    assert bad_mode.status_code == 422
    assert bad_mode.json()["error"] == "invalid_scope_param"
