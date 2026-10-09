"""P6 AI 工具 get_homework_correlation（P2-C1 起改调 app.diagnosis.correlation
同源模块，契约 docs/diagnosis-roadmap/p2-contracts.md §2.5；p6-ai-mcp.md §2
的装配与错误语义要求继续适用）。

- 对外参数与错误语义保持：exam_name 必传（缺 → invalid_scope_param）、
  homeroom 缺 subject → invalid_scope_param、越界考试 → 模型可读错误文本、
  跨年参数透传拒绝。
- A01 同源同果：工具输出与 P2 新端点 /api/v1/{域}/diagnosis/correlation
  逐字段一致（旧的 /api/v1/homework/correlation 名次口径端点由
  test_p5_warnings_correlation.py 继续覆盖，工具不再走它）。
- 返回体只加不删：旧键 metadata/pairs/n/r/direction/caveats 保留（y=成绩
  分数、direction 值域 submit_up_score_*），新增 rho/layers/sample/window_*/
  note/calc_version=p2-v1。
- 注意窗口：考前 [考试日−window_days, 考试日) 不含考后作业——本模块种子
  批次（11-01..05）相对考试（12-01）只有 window_days=30 才入窗。
  模块独立 DB（conftest 每模块重建）。
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
    批次里甲乙部分提交、丙全缺——配对样本与数值断言只针对 5 名新学生。"""
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


def test_correlation_tool_homeroom_a01_same_as_new_endpoint(client, v1_seed, db_session):
    """A01 同源同果：homeroom 会话工具输出与 P2 新端点逐字段一致。

    5 名新学生有 EXAM_CORR 主三门总分与梯度提交率（完全正相关）；
    甲乙丙进批次但无该考试总分 → 不配对，n=5。考试 2025-12-01、批次
    2025-11-01..05 → 须 window_days=30（14 天窗口不含这批考前作业）。"""
    _seed(client, v1_seed, db_session)
    db = _db()
    try:
        tool = _run(
            db, _snap(db, "homeroom"),
            "get_homework_correlation",
            {"exam_name": EXAM_CORR, "subject": "物理",
             "homework_type": "练习册", "window_days": 30},
        )
    finally:
        db.close()
    assert "error" not in tool, tool
    resp = client.get(
        "/api/v1/homeroom/diagnosis/correlation",
        params={"exam_name": EXAM_CORR, "metric": "total:主三门",
                "window_days": 30, "subject": "物理",
                "homework_type": "练习册"},
    )
    assert resp.status_code == 200, resp.text
    api = resp.json()
    assert tool == api
    # 旧键保持：metadata/pairs/n/r/direction/caveats 一个不缺
    for key in ("metadata", "pairs", "n", "r", "direction", "caveats"):
        assert key in api, key
    assert api["n"] == 5
    assert api["calc_version"] == "p2-v1"
    # y=成绩分数（不再有名次口径）；提交率与总分完全正相关
    assert [p["y"] for p in api["pairs"]] == [100.0, 90.0, 80.0, 70.0, 60.0]
    assert [p["x"] for p in api["pairs"]] == [1.0, 0.8, 0.6, 0.4, 0.2]
    # n=5 < 8 → 全班层不可计算（新阈值），r/rho null 绝不编造
    assert api["r"] is None and api["rho"] is None
    assert api["layers"]["all"]["reason"] == "n_too_small"
    # 窗口必含：window_days、起止日（[考试日−30, 考试日)）
    assert api["window_days"] == 30
    assert api["window_start"] == "2025-11-01"
    assert api["window_end"] == "2025-11-30"
    assert api["exam_date"] == "2025-12-01"
    assert "因果" in api["note"] and "提分保证" in api["note"]
    assert any("不可计算" in c for c in api["caveats"])


def test_correlation_tool_teaching_pins_snapshot_subject(client, v1_seed, db_session):
    """teaching 会话：不传 subject 用快照任教学科；传别的学科也钉住物理
    （绝不扩大范围）。数据走 G08 通路——H6 批次经 link 投影为 T6 的 X，
    Y 用「2025期中」物理分数（甲 91 / 乙 85）。link 授权显式开齐三类
    共享（v1_seed 默认不含作业共享）。批次改到 2025-10-20 以进入考试
    （2025-11-06）的 30 天窗口。"""
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
        client, v1_seed, assigned_date="2025-10-20", homework_type="P6工具练习",
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
                       {"exam_name": "2025期中", "homework_type": "P6工具练习",
                        "window_days": 30})
        forged = _run(
            db, snap, "get_homework_correlation",
            {"exam_name": "2025期中", "subject": "语文",
             "homework_type": "P6工具练习", "window_days": 30},
        )
    finally:
        db.close()
    assert "error" not in default, default
    assert default == forged
    pair_ids = {pr["person_id"]: pr for pr in default["pairs"]}
    assert set(pair_ids) == {v1_seed.jia_t_id, v1_seed.yi_t_id}
    assert pair_ids[v1_seed.jia_t_id]["y"] == 91.0
    assert pair_ids[v1_seed.jia_t_id]["x"] == 1.0
    assert pair_ids[v1_seed.yi_t_id]["y"] == 85.0
    assert pair_ids[v1_seed.yi_t_id]["x"] == 0.0
    assert default["n"] == 2
    # n<8 → r/rho=null 绝不编造
    assert default["r"] is None and default["rho"] is None
    assert default["layers"]["all"]["reason"] == "n_too_small"
    assert any("不可计算" in c for c in default["caveats"])
    # 教学域无总分行：段位层如实标不可用
    assert default["layers"]["high_score"]["reason"] == "band_unavailable"
    # 与新端点（同单班显式范围）同源同果
    api = client.get(
        "/api/v1/teaching/diagnosis/correlation",
        params={"academic_year_id": v1_seed.ay_id,
                "teaching_class_id": v1_seed.t6_id,
                "window_days": 30,
                "homework_type": "P6工具练习",
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
            "window_days": 30,
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
