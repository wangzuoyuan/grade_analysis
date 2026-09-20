"""P6 AI 工具 get_homework_correlation（契约 p6-ai-mcp.md §2 + p5-homework §4）。

- A01：工具输出与 /api/v1/homework/correlation 页面端点逐字段一致
- teaching 会话不传 subject 钉住任教学科；homeroom 缺 subject / 缺
  exam_name / 越界考试 → 模型可读错误文本，不抛栈
- r 数值正确性由页面端点测试（test_p5_warnings_correlation.py）覆盖，
  此处只验工具层装配与同源；模块独立 DB（conftest 每模块重建）。
"""

from datetime import date

EXAM_CORR = "P6关联工具考"
_SEEDED = {"done": False}


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _snap(db, mode, **kw):
    from app.api.chat_tools import resolve_scope_snapshot

    return resolve_scope_snapshot(db, 1, mode, **kw)


def _run(db, snapshot, name, args=None):
    from app.api.chat_tools import execute_session_tool

    return execute_session_tool(db, snapshot, name, args or {})


def _preview(client, seed, **over):
    payload = {
        "mode": "homeroom",
        "class_id": seed.h6_id,
        "academic_year_id": seed.ay_id,
        "subject": "物理",
        "homework_type": "练习册",
        "assigned_date": "2025-09-10",
        "input": {"kind": "full"},
    }
    payload.update(over)
    return client.post("/api/v1/homework/preview", json=payload)


def _seed(client, seed, db_session):
    """5 名新 H6 学生 + 主三门总分（与提交率完全正相关）+ 5 个物理批次。

    新学生 i（0 起）提交率 = (5-i)/5、总分 = 100-10*i；甲乙丙另有成绩但
    批次里甲乙部分提交、丙全缺——配对样本与数值断言只针对 5 名新学生。
    """
    if _SEEDED["done"]:
        return
    from app.db import workspace_models as wm

    names = ["P6工具关一", "P6工具关二", "P6工具关三", "P6工具关四", "P6工具关五"]
    ids = []
    for i, name in enumerate(names):
        ident = wm.WsStudentIdentity(data_domain="homeroom", display_name=name)
        db_session.add(ident)
        db_session.flush()
        ids.append(ident.id)
        db_session.add(
            wm.Enrollment(
                admin_class_id=seed.h6_id,
                identity_id=ident.id,
                status="active",
                valid_from=date(2025, 9, 1),
            )
        )
        db_session.add(
            wm.ScoreFact(
                data_domain="homeroom",
                academic_year_id=seed.ay_id,
                exam_name=EXAM_CORR,
                exam_date=date(2025, 12, 1),
                class_ref_id=seed.h6_id,
                identity_id=ident.id,
                subject="总分",
                total_type="主三门",
                score=100.0 - 10.0 * i,
                source="p6-corr-tool-test",
            )
        )
    db_session.commit()

    for j in range(1, 6):  # 批次 j：新学生 i 当且仅当 j <= 5-i 已交
        rows = [
            {"person_id": pid, "status": "submitted" if j <= 5 - i else "missing"}
            for i, pid in enumerate(ids)
        ]
        rows += [
            {"person_id": seed.jia_h_id, "status": "submitted"},
            {"person_id": seed.yi_h_id,
             "status": "submitted" if j <= 4 else "missing"},
            {"person_id": seed.bing_h_id, "status": "missing"},
        ]
        p = _preview(
            client, seed,
            assigned_date=f"2025-11-{j:02d}",
            input={"kind": "detailed", "rows": rows},
        )
        assert p.status_code == 200, p.text
        assert client.post(
            "/api/v1/homework/confirm", json={"token": p.json()["token"]}
        ).status_code == 200
    _SEEDED["done"] = True


def test_correlation_tool_homeroom_a01_same_as_endpoint(client, v1_seed, db_session):
    """A01 同源同果：homeroom 会话工具输出与页面端点逐字段一致。

    5 名新学生有 EXAM_CORR 主三门总分与梯度提交率（完全正相关）；
    甲乙丙进批次但无该考试总分 → 不配对，n=5。"""
    _seed(client, v1_seed, db_session)
    db = _db()
    try:
        tool = _run(
            db, _snap(db, "homeroom"),
            "get_homework_correlation",
            {"exam_name": EXAM_CORR, "subject": "物理",
             "homework_type": "练习册"},
        )
    finally:
        db.close()
    assert "error" not in tool, tool
    resp = client.get(
        "/api/v1/homework/correlation",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "subject": "物理", "homework_type": "练习册",
                "exam_name": EXAM_CORR},
    )
    assert resp.status_code == 200, resp.text
    api = resp.json()
    assert tool == api
    assert api["n"] == 5
    # 完全正相关（提交率高→名次数值小）：r=-1、方向 submit_up_rank_up
    assert api["r"] == -1.0
    assert api["direction"] == "submit_up_rank_up"
    assert api["pairs"][0]["y"] == 1 and api["pairs"][0]["x"] == 1.0
    assert any("因果" in c for c in api["caveats"])


def test_correlation_tool_teaching_pins_snapshot_subject(client, v1_seed, db_session):
    """teaching 会话：不传 subject 用快照任教学科；传别的学科也钉住物理
    （绝不扩大范围）。数据走 G08 通路——H6 批次经 link 投影为 T6 的 X，
    Y 用「2025期中」物理成绩（甲乙班内名次 1/2）。link 授权显式开齐
    三类共享（v1_seed 默认不含作业共享）。"""
    from app.db import workspace_models as wm

    link = (
        db_session.query(wm.HomeroomTeachingLink)
        .filter(wm.HomeroomTeachingLink.id == v1_seed.link_id)
        .one()
    )
    link.valid_from = date(2025, 9, 1)
    link.share_categories = "roster,current_subject_score,current_subject_homework"
    db_session.commit()

    p = _preview(
        client, v1_seed, assigned_date="2025-09-15", homework_type="P6工具练习",
        input={"kind": "detailed",
               "rows": [{"name_or_alias": "秦甲", "status": "submitted"},
                        {"name_or_alias": "秦乙", "status": "missing"}]},
    )
    assert p.status_code == 200, p.text
    assert client.post(
        "/api/v1/homework/confirm", json={"token": p.json()["token"]}
    ).status_code == 200

    db = _db()
    try:
        # 单班 T6 会话（G08 通路：H6 批次经 link 投影为 T6 的 X；并集
        # 会话不加载 link 投影、X 为空，故不用并集做数值断言）
        snap = _snap(
            db, "teaching", teaching_class_id=v1_seed.t6_id, subject="物理"
        )
        # 不传 subject（钉快照学科）；homework_type 过滤避免混入 _seed
        # 的「练习册」批次（甲乙在其中部分提交，会改变 X）
        default = _run(db, snap, "get_homework_correlation",
                       {"exam_name": "2025期中", "homework_type": "P6工具练习"})
        forged = _run(
            db, snap, "get_homework_correlation",
            {"exam_name": "2025期中", "subject": "语文",
             "homework_type": "P6工具练习"},
        )
    finally:
        db.close()
    assert "error" not in default, default
    assert default == forged
    pair_ids = {pr["person_id"]: pr for pr in default["pairs"]}
    assert set(pair_ids) == {v1_seed.jia_t_id, v1_seed.yi_t_id}
    assert pair_ids[v1_seed.jia_t_id]["y"] == 1
    assert pair_ids[v1_seed.jia_t_id]["x"] == 1.0
    assert pair_ids[v1_seed.yi_t_id]["y"] == 2
    assert pair_ids[v1_seed.yi_t_id]["x"] == 0.0
    assert default["n"] == 2
    # n<5 → r=null 绝不编造
    assert default["r"] is None
    assert any("n=" in c for c in default["caveats"])
    # 与页面端点（同单班显式范围）同源同果
    api = client.get(
        "/api/v1/homework/correlation",
        params={"mode": "teaching", "academic_year_id": v1_seed.ay_id,
                "teaching_class_id": v1_seed.t6_id,
                "subject": "物理", "homework_type": "P6工具练习",
                "exam_name": "2025期中"},
    ).json()
    assert default == api


def test_correlation_tool_validation_errors(client, v1_seed, db_session):
    """缺 exam_name / homeroom 缺 subject / 越界考试 → 模型可读错误文本。"""
    _seed(client, v1_seed, db_session)
    db = _db()
    try:
        snap_h = _snap(db, "homeroom")
        no_exam = _run(db, snap_h, "get_homework_correlation", {"subject": "物理"})
        assert no_exam["error"] == "invalid_scope_param"
        assert "exam_name" in no_exam["detail"]

        no_subject = _run(db, snap_h, "get_homework_correlation",
                          {"exam_name": EXAM_CORR})
        assert no_subject["error"] == "invalid_scope_param"
        assert "subject" in no_subject["detail"]

        # 考试不在 homeroom 域 → 越界文本（DomainError 由工具层转译）
        oos = _run(db, snap_h, "get_homework_correlation",
                   {"exam_name": "不存在的考试", "subject": "物理"})
        assert oos["error"] == "resource_out_of_scope"
        assert "不在当前会话范围" in oos["detail"]
    finally:
        db.close()


def test_correlation_tool_cross_year_param_passthrough(client, v1_seed, db_session):
    """跨年参数透传（时间语义批次）：year_offset=-1 / academic_year_id=
    早学年 id 时，参数真实到达 service 作用域解析——早学年无该考试/班 →
    拒绝（可读错误），绝不静默回退到本学年数据（对照：不传参数 n=5）。"""
    _seed(client, v1_seed, db_session)
    from app.db import workspace_models as wm

    ay_old = wm.AcademicYear(
        name="2014-2015",
        start_date=date(2014, 9, 1),
        end_date=date(2015, 7, 15),
    )
    db_session.add(ay_old)
    db_session.commit()

    db = _db()
    try:
        snap = _snap(db, "homeroom")
        args = {
            "exam_name": EXAM_CORR,
            "subject": "物理",
            "homework_type": "练习册",
        }
        base = _run(db, snap, "get_homework_correlation", dict(args))
        by_offset = _run(
            db, snap, "get_homework_correlation", {**args, "year_offset": -1}
        )
        by_id = _run(
            db, snap, "get_homework_correlation",
            {**args, "academic_year_id": ay_old.id},
        )
    finally:
        db.close()
    assert base["n"] == 5  # 本学年（快照锚点）正常出数
    assert "error" in by_offset and "n" not in by_offset
    assert "error" in by_id and "n" not in by_id
