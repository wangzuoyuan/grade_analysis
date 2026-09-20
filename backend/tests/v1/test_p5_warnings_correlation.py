"""P5 预警时间轴与相关性（契约 docs/contracts/p5-homework.md §3/§4）。

- 预警：事件维度（basis='events'）、min_missing 阈值、subject 过滤、
  unknown 使 current_streak 置 null 且 basis='unknown'。
- 相关性：Pearson（正/负/不可算三态）、n<5 与零方差 caveat、分母不可用
  批次剔除、teaching 口径单科名次 + LinkedStudent 投影。
"""

from datetime import date

from app.db import workspace_models as wm

CORR_EXAM_POS = "P5关联考正"
CORR_EXAM_NEG = "P5关联考负"
CORR_EXAM_ZERO = "P5零方差考"

# 模块内 DB 共享：播种只做一次（空快照批次的 batch_token 有唯一约束），
# 各相关性用例复用同一批批次/成绩数据。
_CORR_STATE: dict = {}


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


def _confirm(client, token):
    return client.post("/api/v1/homework/confirm", json={"token": token})


def _missing_preview(client, seed, day, name, subject="物理"):
    """预警事件统一用"默写"种类，与相关性用例的"练习册"批次互不污染
    （模块内共享 DB，相关性的 X 按 homework_type 过滤）。"""
    p = _preview(
        client, seed,
        assigned_date=day, subject=subject, homework_type="默写",
        input={"kind": "detailed",
               "rows": [{"name_or_alias": name, "status": "missing"}]},
    )
    assert _confirm(client, p.json()["token"]).status_code == 200


def test_warnings_min_missing_threshold_and_streaks(client, v1_seed):
    """min_missing 阈值过滤；甲连续缺交（current=3）；乙最近一次已交
    （current=0）。"""
    for day in ("2025-09-01", "2025-09-02", "2025-09-03"):
        _missing_preview(client, v1_seed, day, "秦甲")
    _missing_preview(client, v1_seed, "2025-09-01", "秦乙")
    p = _preview(
        client, v1_seed, assigned_date="2025-09-02", homework_type="默写",
        input={"kind": "detailed",
               "rows": [{"name_or_alias": "秦乙", "status": "submitted"}]},
    )
    assert _confirm(client, p.json()["token"]).status_code == 200

    resp = client.get(
        "/api/v1/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "min_missing": 2},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["basis"] == "events"
    assert [s["person_id"] for s in body["students"]] == [v1_seed.jia_h_id]
    jia = body["students"][0]
    assert jia["missing_count"] == 3
    # 其间其他独立批次没有登记秦甲缺交，按默认已交中断。
    assert jia["current_streak"] == 1
    assert jia["streak_basis"] == "events"

    relaxed = client.get(
        "/api/v1/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "min_missing": 1},
    ).json()
    by_id = {s["person_id"]: s for s in relaxed["students"]}
    assert by_id[v1_seed.yi_h_id]["missing_count"] == 1
    assert by_id[v1_seed.yi_h_id]["current_streak"] == 0  # 最近事件 submitted


def test_warnings_subject_filter_separate_domains(client, v1_seed):
    """subject 过滤：数学缺交不计入物理口径（班主任可录多科作业）。"""
    _missing_preview(client, v1_seed, "2025-09-05", "秦甲", subject="数学")

    physics = client.get(
        "/api/v1/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "min_missing": 1, "subject": "物理"},
    ).json()
    jia = next(s for s in physics["students"] if s["person_id"] == v1_seed.jia_h_id)
    assert jia["missing_count"] == 3

    math = client.get(
        "/api/v1/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "min_missing": 1, "subject": "数学"},
    ).json()
    jia_math = next(s for s in math["students"] if s["person_id"] == v1_seed.jia_h_id)
    assert jia_math["missing_count"] == 1

    # 不同学科不得串成连续段；同科其他批次未登记缺交时按已交中断。
    grouped = client.get(
        "/api/v1/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "min_missing": 1, "min_streak": 1},
    )
    assert grouped.status_code == 200, grouped.text
    grouped_jia = next(
        s for s in grouped.json()["students"] if s["person_id"] == v1_seed.jia_h_id
    )
    assert grouped_jia["current_streak"] == 1
    assert grouped_jia["streak_subject"] == "数学"
    assert grouped_jia["streak_homework_type"] is None
    assert grouped.json()["min_streak"] == 1

    too_high = client.get(
        "/api/v1/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "min_missing": 1, "min_streak": 4},
    ).json()
    assert v1_seed.jia_h_id not in {s["person_id"] for s in too_high["students"]}


def test_legacy_missing_only_uses_disclosed_preserved_timeline(
    client, v1_seed, db_session
):
    """迁移的 empty expected 快照按旧版日期轴给出连续值，但 basis 必须
    标成 legacy_events，不能冒充完整的新批次事件链。"""
    for i, day in enumerate(("2025-10-01", "2025-10-02"), start=1):
        assignment = wm.HomeworkAssignment(
            data_domain="homeroom",
            class_ref_id=v1_seed.h6_id,
            academic_year_id=v1_seed.ay_id,
            subject="化学",
            homework_type="legacy",
            assigned_date=date.fromisoformat(day),
            batch_token=f"p5-legacy-streak-{i}",
            expected_members_json="[]",
        )
        db_session.add(assignment)
        db_session.flush()
        db_session.add(
            wm.HomeworkSubmission(
                assignment_id=assignment.id,
                person_id=v1_seed.jia_h_id,
                submission_status="missing",
            )
        )
    db_session.commit()

    body = client.get(
        "/api/v1/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "subject": "化学", "min_missing": 1, "min_streak": 2},
    ).json()
    jia = next(s for s in body["students"] if s["person_id"] == v1_seed.jia_h_id)
    assert jia["current_streak"] == 2
    assert jia["streak_basis"] == "legacy_events"
    assert jia["streak_subject"] == "化学"


def test_legacy_streak_keeps_trailing_run_when_interrupted_by_submitted(
    client, v1_seed, db_session
):
    """legacy 轴回溯遇「该生日已交」只结束连续段、保留已数的尾部连缺值：
    学生在最新新批次缺交、更早新批次默认已交 → current=1（曾误清零成 0，
    配合 min_streak 过滤会令整卡连续缺交预警显示为空）。"""
    # 日期取学期最前（09-01~03）：晚于他案的批次会在同模块后续用例的
    # 「最优连缺维度」平手裁决中胜出，把别人的 basis 顶成 legacy_events。
    legacy = wm.HomeworkAssignment(
        data_domain="homeroom",
        class_ref_id=v1_seed.h6_id,
        academic_year_id=v1_seed.ay_id,
        subject="生物",
        homework_type="legacy",
        assigned_date=date.fromisoformat("2025-09-01"),
        batch_token="p5-legacy-keep-1",
        expected_members_json="[]",
    )
    db_session.add(legacy)
    db_session.flush()
    db_session.add(
        wm.HomeworkSubmission(
            assignment_id=legacy.id,
            person_id=v1_seed.jia_h_id,
            submission_status="missing",
        )
    )
    db_session.commit()

    # 更早的新批次：全班全交（秦甲无行 = 默认已交，构成打断日）
    p_full = _preview(
        client, v1_seed, assigned_date="2025-09-02", subject="生物",
        homework_type="课堂练习",
        input={"kind": "full", "exceptions": []},
    )
    assert _confirm(client, p_full.json()["token"]).status_code == 200
    # 最新的新批次：秦甲缺交（尾部连缺起点）
    p_miss = _preview(
        client, v1_seed, assigned_date="2025-09-03", subject="生物",
        homework_type="课后订正",
        input={"kind": "detailed",
               "rows": [{"name_or_alias": "秦甲", "status": "missing"}]},
    )
    assert _confirm(client, p_miss.json()["token"]).status_code == 200

    body = client.get(
        "/api/v1/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "subject": "生物", "min_missing": 1},
    ).json()
    jia = next(s for s in body["students"] if s["person_id"] == v1_seed.jia_h_id)
    assert jia["current_streak"] == 1
    assert jia["streak_basis"] == "legacy_events"


def test_warnings_legacy_unknown_is_submitted(client, v1_seed):
    """旧 unknown 输入兼容归一为已交。"""
    p = _preview(
        client, v1_seed, assigned_date="2025-09-08", homework_type="默写",
        input={"kind": "detailed",
               "rows": [{"name_or_alias": "秦乙", "status": "unknown"}]},
    )
    assert _confirm(client, p.json()["token"]).status_code == 200

    body = client.get(
        "/api/v1/homework/warnings",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id, "min_missing": 1},
    ).json()
    yi = next(s for s in body["students"] if s["person_id"] == v1_seed.yi_h_id)
    assert yi["current_streak"] == 0
    assert yi["streak_basis"] == "events"
    assert yi["missing_count"] == 1


def _seed_corr_students(db_session, seed):
    """5 名新 H6 学生 + 两场总分（正/反向）+ 零方差场 + 空快照批次。"""
    names = ["秦corr一", "秦corr二", "秦corr三", "秦corr四", "秦corr五"]
    totals_pos = [100.0, 90.0, 80.0, 70.0, 60.0]
    ids = []
    for name in names:
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
    db_session.flush()

    def total_fact(exam, ident_id, value):
        db_session.add(
            wm.ScoreFact(
                data_domain="homeroom",
                academic_year_id=seed.ay_id,
                exam_name=exam,
                exam_date=date(2025, 12, 1),
                class_ref_id=seed.h6_id,
                identity_id=ident_id,
                subject="总分",
                total_type="主三门",
                score=value,
                source="p5-corr-test",
            )
        )

    for pos, (ident_id, value) in enumerate(zip(ids, totals_pos)):
        total_fact(CORR_EXAM_POS, ident_id, value)
        total_fact(CORR_EXAM_NEG, ident_id, 110.0 - value)  # 反向
        total_fact(CORR_EXAM_ZERO, ident_id, 80.0)  # 零方差

    # 空应交快照的物理批次（分母不可用 → 相关性剔除并计 caveat）
    db_session.add(
        wm.HomeworkAssignment(
            data_domain="homeroom",
            class_ref_id=seed.h6_id,
            academic_year_id=seed.ay_id,
            subject="物理",
            homework_type="练习册",
            assigned_date=date(2025, 11, 30),
            batch_token="p5-corr-empty-batch",
            expected_members_json="[]",
        )
    )
    db_session.commit()
    return ids


def _ensure_corr_data(client, seed, db_session):
    """模块内只播种一次：5 名新 H6 学生 + 5 个物理批次 + 空快照批次。
    甲全交 / 乙 4-of-5 / 丙全缺，保证 teaching 投影出的 X 非零且可断言。"""
    if _CORR_STATE.get("ids") is None:
        ids = _seed_corr_students(db_session, seed)
        for j in range(1, 6):  # 批次 j：第 i 名新学生 submitted 当且仅当 j <= 5-i
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
            assert _confirm(client, p.json()["token"]).status_code == 200
        _CORR_STATE["ids"] = ids
    return _CORR_STATE["ids"]


def test_correlation_homeroom_positive_with_caveats(client, v1_seed, db_session):
    """homeroom 口径：Y=主三门总分名次，X=提交率；完全正相关 → r≈1，
    direction=submit_up_rank_down；空快照批次剔除进 caveats。"""
    _ensure_corr_data(client, v1_seed, db_session)

    resp = client.get(
        "/api/v1/homework/correlation",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "subject": "物理", "homework_type": "练习册",
                "exam_name": CORR_EXAM_POS},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n"] == 5
    # Y 是名次数值（1=最好）：提交率越高名次数值越小 → r 为负
    assert body["r"] == -1.0
    assert body["direction"] == "submit_up_rank_up"
    assert any("分母不可用" in c for c in body["caveats"])
    assert any("因果" in c for c in body["caveats"])
    # 按 Y 升序（名次 1 = 总分最高）排第一的是 x=1.0 的第一名
    assert body["pairs"][0]["y"] == 1
    assert body["pairs"][0]["x"] == 1.0


def test_correlation_homeroom_negative_direction(client, v1_seed, db_session):
    """反向总分：同一 X 对调后的名次 → r 为正（名次数值变大=名次退步）。"""
    _ensure_corr_data(client, v1_seed, db_session)

    body = client.get(
        "/api/v1/homework/correlation",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "subject": "物理", "homework_type": "练习册",
                "exam_name": CORR_EXAM_NEG},
    ).json()
    assert body["n"] == 5
    assert body["r"] == 1.0
    assert body["direction"] == "submit_up_rank_down"


def test_correlation_small_sample_and_zero_variance(client, v1_seed, db_session):
    """n<5 与零方差 → r=null + caveat（绝不编造相关值）。"""
    _ensure_corr_data(client, v1_seed, db_session)

    small = client.get(
        "/api/v1/homework/correlation",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "subject": "物理", "homework_type": "练习册",
                "exam_name": "2025期中"},
    ).json()
    # 2025期中 只有甲乙丙有总分（5 名新学生没有）→ n=3 < 5
    assert small["n"] == 3
    assert small["r"] is None
    assert any("n=" in c for c in small["caveats"])

    zero = client.get(
        "/api/v1/homework/correlation",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id,
                "subject": "物理", "homework_type": "练习册",
                "exam_name": CORR_EXAM_ZERO},
    ).json()
    assert zero["n"] == 5
    assert zero["r"] is None
    assert any("零方差" in c for c in zero["caveats"])


def test_correlation_teaching_subject_rank_with_projection(client, v1_seed, db_session):
    """teaching 口径：Y=单科本班名次（teaching 域事实，缺考丁不进对），
    X=经 current_subject_homework 门投影的 H 批次提交率；n<5 → r=null。"""
    _ensure_corr_data(client, v1_seed, db_session)

    # 打开作业共享类别（share-scope 会 version+1，无 pending token 不受影响）
    scoped = client.post(
        f"/api/v1/shared/links/{v1_seed.link_id}/share-scope",
        json={"share_categories": ["roster", "current_subject_score",
                                   "current_subject_homework"]},
    )
    assert scoped.status_code == 200, scoped.text

    body = client.get(
        "/api/v1/homework/correlation",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t6_id,
                "subject": "物理", "homework_type": "练习册",
                "exam_name": "2025期中"},
    ).json()
    pair_ids = {p["person_id"]: p for p in body["pairs"]}
    # 甲乙有物理分（丁缺考 NULL 不参与），且经 LinkedStudent 交集拿到 X
    assert set(pair_ids) == {v1_seed.jia_t_id, v1_seed.yi_t_id}
    assert pair_ids[v1_seed.jia_t_id]["y"] == 1  # 91 分班内第一
    assert pair_ids[v1_seed.jia_t_id]["x"] == 1.0
    assert pair_ids[v1_seed.yi_t_id]["x"] == 0.8
    assert body["n"] == 2
    assert body["r"] is None
    assert any("n=" in c for c in body["caveats"])
