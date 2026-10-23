"""2026-10-23 AI 覆盖审计补齐：口径护栏 + 新工具 A01 同源（契约 docs/contracts/p6-ai-mcp.md v2.1）。

为同日第一波落地改动补回归护栏：
- 口径文案契约（注册描述 + 系统提示词规则 4 四锚点 + rank_frequency/
  rank_range metric_note 学籍口径）。metric_note 取 service 实际输出
  （经工具调用拿模型真正可见的载荷）而非源码断言——文案断在输出上，
  重构提示词组装方式也不会误报，漏改口径则必然红。
- 新工具注册与域投影：get_rank_bands / get_teaching_score_bands /
  get_homework_student_subjects 三工具的 domains 声明（teaching/homeroom
  互不可见，与 test_p6_tools 的注册表全集断言互补、此处聚焦三新工具）。
- A01 同源（带种子数据）：三新工具 vs 对应 /api/v1 端点逐字段一致。
  · get_rank_bands：名次段表随考试导入（照抄 test_p3_rank_bands 造表法）；
    纯名次段考试无 ScoreFact，工具的 _resolve_exam_name 候选基于
    readable facts，故补一条同考试名 ScoreFact 让精确名可解析——
    端点自身无此门（直接 filter_exam），两者读同一 service 结果仍相等。
  · get_teaching_score_bands：教学域分数段事实 ORM 直种（照抄
    test_p3_score_bands 造数法）+ band_width=5 变体。
  · get_homework_student_subjects：作业×成绩种子在 v1_seed 班内直种
    （照抄 test_student_homework_subjects 造数法，但不另建学年，避免
    污染模块内其他用例的学年解析）+ window_days=30 变体与 7 白名单错误。
- 旧 POST /api/chat 与 GET /api/chat/config 下线冒烟（主守卫在
  tests/test_mcp.py，此处按同一断言风格在 v1 client 上复验一条）。

样本全部合成（测试学生甲/乙、P6AI* 考试名），绝不含真实学生/教师/考试/学期名。
"""

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4

import pytest

API = "/api/v1"
EXAM_E1 = "2025期中"  # v1_seed 既有考试（tests/v1/conftest.py）
EXAM_RB = "P6AI名次段考"  # 名次段表导入考试（本模块自造）
EXAM_SB = "P6AI分数段考"  # 教学分数段考试（本模块自造）

# 2026-10-23 覆盖补齐的三个新工具
NEW_TOOLS = {"get_rank_bands", "get_teaching_score_bands", "get_homework_student_subjects"}


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _snap(db, mode, **kw):
    from app.api.chat_tools import resolve_scope_snapshot

    return resolve_scope_snapshot(db, 1, mode, **kw)


def _run(db, snapshot, name, args=None):
    from app.api.chat_tools import execute_session_tool

    return execute_session_tool(db, snapshot, name, args or {})


# ────────────────────── 1. 口径文案契约 ──────────────────────


def test_tool_description_caliber_wording():
    """注册描述护栏：get_class_averages 写明 0=无人选与班型内排名口径；
    get_exam_students 已删「跨域冲突注记」（Q10 退役）、改为百分位宽表口径。"""
    from app.api.chat_tools import TOOL_REGISTRY

    by_name = {spec.name: spec for spec in TOOL_REGISTRY}
    desc_avg = by_name["get_class_averages"].description
    assert "无人选" in desc_avg  # 选科 0=无人选，不得计入平均/比较/当最低分
    assert "班型" in desc_avg  # total_ranks=班型内排名，不是全年级名次
    desc_students = by_name["get_exam_students"].description
    assert "冲突" not in desc_students  # 跨域冲突注记已随 Q10 退役
    assert "百分位" in desc_students  # 现行口径：宽表含年级百分位


def test_system_prompt_rule4_caliber_anchors():
    """系统提示词规则 4（数字口径）四锚点：百分位方向、学籍=1 统计口径、
    均分表 0=无人选、班型内排名（chat.py build_system_prompt，homeroom 快照）。"""
    from app.api.chat import build_system_prompt

    snapshot = {
        "mode": "homeroom",
        "data_domain": "homeroom",
        "academic_year_id": 1,
        "class_ids": [1],
        "subject": None,
        "link_id": None,
        "link_version": None,
        "member_person_ids": [1, 2, 3],
        "cohort_size": 3,
        "as_of": "2026-10-23",
    }
    prompt = build_system_prompt(snapshot)  # time_anchor 缺省：无锚点行，规则文本不变
    assert "年级前百分之几" in prompt  # b) 百分位=前百分之几，越小越靠前
    assert "学籍=1" in prompt  # c) 总分口径只统计学籍=1
    assert "无人选" in prompt  # d) 选科 0=无人选
    assert "班型内排名" in prompt  # d) total_ranks 是班型内排名


def test_metric_note_xueji_caliber_in_tool_output(v1_seed):
    """rank_frequency / rank_range 的 metric_note 含学籍=1 口径——取工具
    实际输出（模型可见载荷）断言，而非源码文本：种子无名次事实时两工具均
    返回合法空态且 metric_note 照常下发，文案即口径护栏本体。"""
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        frequency = _run(
            db, snap, "get_rank_frequency",
            {"metric": "total:主三门", "exam_names": EXAM_E1},
        )
        rank_range = _run(
            db, snap, "get_rank_range",
            {"exam_name": EXAM_E1, "metric": "total:主三门"},
        )
    finally:
        db.close()
    assert "学籍=1" in frequency["metric_note"]
    assert frequency["students"] == []  # 种子无名次事实：合法空态（同 test_p6_tools）
    assert "学籍=1" in rank_range["metric_note"]


# ────────────────────── 2. 新工具注册与域投影 ──────────────────────


def test_new_tools_registered_with_domain_projection(v1_seed):
    """三新工具在注册表且总数 28；域投影：get_rank_bands/
    get_homework_student_subjects 仅 homeroom、get_teaching_score_bands
    仅 teaching（对侧会话不可见）。"""
    from app.api.chat_tools import TOOL_REGISTRY, tools_for_domain

    names = {spec.name for spec in TOOL_REGISTRY}
    assert NEW_TOOLS <= names
    assert len(TOOL_REGISTRY) == 28
    by_name = {spec.name: spec for spec in TOOL_REGISTRY}
    assert by_name["get_rank_bands"].domains == ("homeroom",)
    assert by_name["get_homework_student_subjects"].domains == ("homeroom",)
    assert by_name["get_teaching_score_bands"].domains == ("teaching",)
    homeroom_tools = {t["name"] for t in tools_for_domain("homeroom")}
    teaching_tools = {t["name"] for t in tools_for_domain("teaching")}
    assert {"get_rank_bands", "get_homework_student_subjects"} <= homeroom_tools
    assert "get_teaching_score_bands" not in homeroom_tools
    assert "get_teaching_score_bands" in teaching_tools
    assert not ({"get_rank_bands", "get_homework_student_subjects"} & teaching_tools)


# ────────────────────── 3a. A01：get_rank_bands（名次段表导入种子） ──────────────────────


def _write_rank_band_xlsx(path: Path, title: str, classes: list, bands: list) -> Path:
    """名次段表 xlsx（造表法与 test_p3_rank_bands.py 同源）：row1 合并标题、
    row2 班级/班主任/段列、row3+ 每班一行。班主任名均为合成（周老师/王老师）。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.cell(row=1, column=1, value=title)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=2 + len(bands))
    for col, text in enumerate(["班级", "班主任", *bands], start=1):
        ws.cell(row=2, column=col, value=text)
    for offset, (class_num, teacher, counts) in enumerate(classes, start=3):
        ws.cell(row=offset, column=1, value=class_num)
        ws.cell(row=offset, column=2, value=teacher)
        for idx, count in enumerate(counts, start=3):
            ws.cell(row=offset, column=idx, value=count)
    wb.save(str(path))
    return path


def _upload(client, paths, mode, extra):
    data = {"mode": mode}
    data.update({k: str(v) for k, v in extra.items()})
    files = [
        (
            "files",
            (p.name, p.read_bytes(),
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        )
        for p in paths
    ]
    return client.post(f"{API}/imports/preview", data=data, files=files)


def test_a01_rank_bands_tool_matches_endpoint(client, v1_seed, tmp_path):
    """名次段表导入（3+3 与主三门两口径）→ get_rank_bands 工具 == rank-bands
    端点（默认口径与 total_type=主三门 变体）；结果含 classes/bands 结构与
    available_total_types。纯名次段考试无 ScoreFact，补一条同考试名
    ScoreFact 让工具的考试名解析（readable facts 候选）可精确命中。"""
    from app.db import workspace_models as wm

    s = v1_seed
    bands = ["1-40", "41-80", "81-120"]
    p33 = _write_rank_band_xlsx(
        tmp_path / "六门总分名次段表一（在籍）.xlsx",
        "六门总分名次段表一（在籍）",
        [(5, "周老师", [1, 2, 3]), (6, "王老师", [2, 2, 2])],
        bands,
    )
    main3 = _write_rank_band_xlsx(
        tmp_path / "三门总分名次段表一（在籍）.xlsx",
        "三门总分名次段表一（在籍）",
        [(5, "周老师", [0, 3, 3]), (6, "王老师", [1, 2, 3])],
        bands,
    )
    preview = _upload(client, [p33, main3], "homeroom", {
        "class_id": s.h6_id, "exam_name": EXAM_RB, "exam_date": "2025-12-01",
    })
    assert preview.status_code == 200, preview.text
    confirmed = client.post(f"{API}/imports/confirm", json={"token": preview.json()["token"]})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["imported"] == 4  # 2 班 × 2 口径

    # 补一条同考试名 ScoreFact（工具 _resolve_exam_name 候选基于 readable
    # facts；端点无此门。两路径最终都进同一 service，A01 仍逐字段一致）
    db = _db()
    try:
        db.add(wm.ScoreFact(
            data_domain="homeroom", academic_year_id=s.ay_id,
            exam_name=EXAM_RB, exam_date=date(2025, 12, 1),
            class_ref_id=s.h6_id, identity_id=s.jia_h_id,
            subject="语文", total_type=None, score=88.0,
            source="p6-ai-coverage-test",
        ))
        db.commit()
        tool = _run(
            db, _snap(db, "homeroom", academic_year_id=s.ay_id),
            "get_rank_bands", {"exam_name": EXAM_RB},
        )
        tool_main3 = _run(
            db, _snap(db, "homeroom", academic_year_id=s.ay_id),
            "get_rank_bands", {"exam_name": EXAM_RB, "total_type": "主三门"},
        )
    finally:
        db.close()
    api = client.get(
        f"{API}/homeroom/analysis/exams/{quote(EXAM_RB)}/rank-bands",
        params={"academic_year_id": s.ay_id, "class_id": s.h6_id},
    )
    assert api.status_code == 200, api.text
    assert tool == api.json()
    api_main3 = client.get(
        f"{API}/homeroom/analysis/exams/{quote(EXAM_RB)}/rank-bands",
        params={"academic_year_id": s.ay_id, "class_id": s.h6_id, "total_type": "主三门"},
    )
    assert tool_main3 == api_main3.json()

    # 结构断言：classes/bands 与 available_total_types（缺省口径=3+3 优先序）
    assert tool["total_type"] == "3+3"
    assert tool["available_total_types"] == ["3+3", "主三门"]
    assert tool["current_class_num"] == 6
    assert [c["class_num"] for c in tool["classes"]] == [5, 6]
    cls6 = tool["classes"][1]
    assert cls6["teacher_name"] == "王老师" and cls6["total"] == 6
    assert [b["count"] for b in cls6["bands"]] == [2, 2, 2]
    assert cls6["bands"][0]["label"] == "1-40"
    assert [b["count"] for b in tool["grade_bands"]] == [3, 4, 5]  # 年级行=各段求和
    assert tool_main3["total_type"] == "主三门"
    assert [b["count"] for b in tool_main3["grade_bands"]] == [1, 5, 6]


def test_rank_bands_pure_band_exam_resolvable(client, v1_seed, tmp_path):
    """纯名次段考试（零 ScoreFact，只导入名次段表）精确名可答：2026-10-23
    起 _exam_exists_in_domain 对齐 exam_events 可见性（均分表/名次段表导入
    也算考试存在），AI 工具精确名解析与走存在性门的读端点都不再误判
    「考试不存在」；此前精确名被拒、stats 等 7 个走门端点 404。"""
    from urllib.parse import quote

    s = v1_seed
    exam = "P6AI纯段考"
    p33 = _write_rank_band_xlsx(
        tmp_path / "六门总分名次段表二（在籍）.xlsx",
        "六门总分名次段表二（在籍）",
        [(5, "周老师", [1, 0, 0]), (6, "王老师", [0, 1, 0])],
        ["1-40", "41-80", "81-120"],
    )
    preview = _upload(client, [p33], "homeroom", {
        "class_id": s.h6_id, "exam_name": exam, "exam_date": "2025-12-08",
    })
    assert preview.status_code == 200, preview.text
    confirmed = client.post(f"{API}/imports/confirm", json={"token": preview.json()["token"]})
    assert confirmed.status_code == 200, confirmed.text

    db = _db()
    try:
        # 零 ScoreFact 断言前置：该考试在本域没有任何成绩事实
        from app.db import workspace_models as wm
        assert db.query(wm.ScoreFact).filter_by(
            data_domain="homeroom", academic_year_id=s.ay_id, exam_name=exam,
        ).count() == 0
        tool = _run(
            db, _snap(db, "homeroom", academic_year_id=s.ay_id),
            "get_rank_bands", {"exam_name": exam},
        )
    finally:
        db.close()
    assert "error" not in tool
    assert [b["count"] for b in tool["grade_bands"]] == [1, 1, 0]

    api = client.get(
        f"{API}/homeroom/analysis/exams/{quote(exam)}/rank-bands",
        params={"academic_year_id": s.ay_id, "class_id": s.h6_id},
    )
    assert api.status_code == 200, api.text
    assert tool == api.json()

    # 走存在性门的读端点对纯导入考试不再 404（合法空态 200）
    stats = client.get(
        f"{API}/homeroom/analysis/exams/{quote(exam)}/stats",
        params={"academic_year_id": s.ay_id, "class_id": s.h6_id},
    )
    assert stats.status_code == 200, stats.text


def test_rank_bands_unknown_exam_readable_error(v1_seed):
    """越界一例：不存在考试名 → resource_out_of_scope 可读错误，列出当前
    范围可用考试（含种子考试名），绝不抛栈。"""
    db = _db()
    try:
        result = _run(
            db, _snap(db, "homeroom", academic_year_id=v1_seed.ay_id),
            "get_rank_bands", {"exam_name": "P6AI不存在的考试"},
        )
    finally:
        db.close()
    assert result["error"] == "resource_out_of_scope"
    assert "不在当前会话范围" in result["detail"]
    assert "可用" in result["detail"]  # 可用考试提示
    assert EXAM_E1 in result["detail"]
    assert "Traceback" not in result["detail"]


# ────────────────────── 3b. A01：get_teaching_score_bands（教学分数段种子） ──────────────────────


@pytest.fixture(scope="module")
def bands_seed(v1_seed):
    """教学域分数段事实 ORM 直种（造数法与 test_p3_score_bands.py 同源）：
    T6 甲95 乙90 丁NULL；T8 戊89.5 己0。左闭右开/累计/σ 等数学口径由
    test_p3_score_bands 冻结，此处只验工具与端点同源。"""
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    s = v1_seed
    db = SessionLocal()

    def fact(cls_id, ident_id, score):
        f = wm.ScoreFact()
        f.data_domain = "teaching"
        f.academic_year_id = s.ay_id
        f.exam_name = EXAM_SB
        f.exam_date = date(2025, 11, 15)
        f.class_ref_id = cls_id
        f.identity_id = ident_id
        f.subject = "物理"
        f.total_type = None
        f.score = score
        f.source = "p6-ai-coverage-test"
        db.add(f)

    fact(s.t6_id, s.jia_t_id, 95.0)
    fact(s.t6_id, s.yi_t_id, 90.0)
    fact(s.t6_id, s.ding_t_id, None)
    fact(s.t8_id, s.wu_t_id, 89.5)
    fact(s.t8_id, s.ji_t_id, 0.0)
    db.commit()
    db.close()
    return None


def test_a01_teaching_score_bands_tool_matches_endpoint(client, v1_seed, bands_seed):
    """get_teaching_score_bands（teaching 并集会话快照）== score-bands 端点；
    band_width=5 变体同源；关键数值抽查（merged 顶段与 [80,90) 段）。"""
    s = v1_seed
    db = _db()
    try:
        tool = _run(
            db, _snap(db, "teaching", academic_year_id=s.ay_id),
            "get_teaching_score_bands", {"exam_name": EXAM_SB},
        )
        tool5 = _run(
            db, _snap(db, "teaching", academic_year_id=s.ay_id),
            "get_teaching_score_bands", {"exam_name": EXAM_SB, "band_width": 5},
        )
    finally:
        db.close()
    api = client.get(
        f"{API}/teaching/analysis/exams/{quote(EXAM_SB)}/score-bands",
        params={"academic_year_id": s.ay_id},
    )
    assert api.status_code == 200, api.text
    assert tool == api.json()
    api5 = client.get(
        f"{API}/teaching/analysis/exams/{quote(EXAM_SB)}/score-bands",
        params={"academic_year_id": s.ay_id, "band_width": 5},
    )
    assert tool5 == api5.json()

    # 关键数值（口径细节见 test_p3_score_bands；此处确认工具拿到的确是同数据）
    assert tool["subject"] == "物理" and tool["band_width"] == 10
    merged = tool["merged"]
    assert merged["valid_count"] == 4 and merged["missing_count"] == 1
    top = next(b for b in merged["bands"] if b["low"] == 90)
    assert top["count"] == 2 and top["cumulative_pct"] == 50.0
    b80 = next(b for b in merged["bands"] if b["low"] == 80)
    assert b80["count"] == 1 and b80["cumulative_pct"] == 75.0
    assert tool5["band_width"] == 5


# ────────────────────── 3c. A01：get_homework_student_subjects（作业×成绩种子） ──────────────────────


@pytest.fixture(scope="module")
def hw_seed(v1_seed):
    """作业×成绩种子（造数法与 test_student_homework_subjects.py 同源，但直接
    种在 v1_seed 学年/班级内，不另建学年——避免污染本模块其他用例的学年
    解析）：两名合成学生入册 H6；语文（高二语数英→年级百分位口径）与物理
    （高二六选科→等级分口径）各两场考试；每场考前窗口内 3 次作业，甲全交、
    乙缺一场。"""
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    s = v1_seed
    db = SessionLocal()
    people = []
    for display in ("测试学生甲", "测试学生乙"):
        person = wm.WsStudentIdentity(data_domain="homeroom", display_name=display)
        db.add(person)
        db.flush()
        people.append(person.id)
        db.add(wm.Enrollment(
            admin_class_id=s.h6_id, identity_id=person.id,
            valid_from=date(2025, 9, 1), status="active",
        ))
    exam_days = (date(2025, 10, 20), date(2025, 11, 10))
    for subject, is_core in (("语文", True), ("物理", False)):
        for i, day in enumerate(exam_days, start=1):
            for j, pid in enumerate(people):
                db.add(wm.ScoreFact(
                    data_domain="homeroom", academic_year_id=s.ay_id,
                    class_ref_id=s.h6_id, identity_id=pid, subject=subject,
                    exam_name=f"P6AI{subject}测评{i}", exam_date=day,
                    score=90 - i,
                    grade_score=None if is_core else (60 + i * 5 if j == 0 else 70 - i),
                    grade_percentile=(0.9 - i * 0.1 if j == 0 else 0.5) if is_core else None,
                    source="p6-ai-coverage-test",
                ))
            # 考前 14 天窗口内 3 次作业（3/7/12 天前），两场窗口互不重叠
            for k, offset in enumerate((3, 7, 12)):
                batch = wm.HomeworkAssignment(
                    data_domain="homeroom", academic_year_id=s.ay_id,
                    class_ref_id=s.h6_id, subject=subject, homework_type="P6AI合成练习",
                    assigned_date=day - timedelta(days=offset), batch_token=uuid4().hex,
                    expected_members_json=json.dumps(people),
                )
                db.add(batch)
                db.flush()
                for j, pid in enumerate(people):
                    missing = j == 1 and k == 1  # 乙缺第二场作业
                    db.add(wm.HomeworkSubmission(
                        assignment_id=batch.id, person_id=pid,
                        submission_status="missing" if missing else "submitted",
                        evaluation=None,
                    ))
    db.commit()
    ids = list(people)
    db.close()
    return SimpleNamespace(pids=ids)


def test_a01_homework_student_subjects_tool_matches_endpoint(client, v1_seed, hw_seed):
    """get_homework_student_subjects（person_id）== homework-student-subjects
    端点（默认 window_days=14 与 30 变体）；结构断言：学科序、口径按学科
    自适应（语文=百分位/物理=等级分）、换人可用。"""
    s = v1_seed
    db = _db()
    try:
        snap = _snap(db, "homeroom", academic_year_id=s.ay_id)
        tool = _run(db, snap, "get_homework_student_subjects",
                    {"person_id": hw_seed.pids[0]})
        tool30 = _run(db, snap, "get_homework_student_subjects",
                      {"person_id": hw_seed.pids[0], "window_days": 30})
    finally:
        db.close()
    api = client.get(
        f"{API}/homeroom/diagnosis/homework-student-subjects",
        params={"person_id": hw_seed.pids[0], "academic_year_id": s.ay_id,
                "class_id": s.h6_id},
    )
    assert api.status_code == 200, api.text
    assert tool == api.json()
    api30 = client.get(
        f"{API}/homeroom/diagnosis/homework-student-subjects",
        params={"person_id": hw_seed.pids[0], "academic_year_id": s.ay_id,
                "class_id": s.h6_id, "window_days": 30},
    )
    assert tool30 == api30.json()

    # 结构与口径断言（时间线含本班当期成员的全部同科考试——种子 E1 与本模块
    # 名次段考语文行也入线、raw 为 None 属正常；口径断言钉在 P6AI 自造点上）
    assert tool["person_id"] == hw_seed.pids[0]
    assert tool["name"] == "测试学生甲"
    assert tool["window_days"] == 14
    assert [item["subject"] for item in tool["subjects"]] == ["语文", "物理"]
    subjects = {item["subject"]: item for item in tool["subjects"]}
    chinese = [p for p in subjects["语文"]["timeline"]
               if p["exam_name"] in ("P6AI语文测评1", "P6AI语文测评2")]
    physics = [p for p in subjects["物理"]["timeline"]
               if p["exam_name"] in ("P6AI物理测评1", "P6AI物理测评2")]
    assert [p["exam_name"] for p in chinese] == ["P6AI语文测评1", "P6AI语文测评2"]
    assert [p["exam_name"] for p in physics] == ["P6AI物理测评1", "P6AI物理测评2"]
    first = chinese[0]  # 时间线按日期升序，测评1 是全科最早考试：窗口无裁剪
    assert first["basis"] == "grade_percentile"  # 高二语数英=年级百分位
    assert first["percentile"] == pytest.approx(0.8)
    assert first["submitted"] == 3 and first["eligible"] == 3
    assert first["submission_rate"] == pytest.approx(1.0)  # 甲三场作业全交
    assert physics[0]["basis"] == "grade_score"  # 高二六选科=等级分
    assert physics[0]["percentile"] is None
    assert tool30["window_days"] == 30
    # 换人可用：乙的名字回传
    db = _db()
    try:
        other = _run(db, _snap(db, "homeroom", academic_year_id=s.ay_id),
                     "get_homework_student_subjects", {"person_id": hw_seed.pids[1]})
    finally:
        db.close()
    assert other["name"] == "测试学生乙"
    other_first = next(
        p for p in {i["subject"]: i for i in other["subjects"]}["语文"]["timeline"]
        if p["exam_name"] == "P6AI语文测评1"
    )
    assert other_first["submission_rate"] == pytest.approx(2 / 3, abs=1e-3)  # 乙缺一场（service 四位小数舍入 0.6667）


def test_homework_student_subjects_window_days_whitelist(v1_seed, hw_seed):
    """参数错误一例：window_days=7 非白名单（14|30）→ invalid_scope_param
    可读错误，不触 service（同 service 侧 422 契约）。"""
    db = _db()
    try:
        snap = _snap(db, "homeroom", academic_year_id=v1_seed.ay_id)
        result = _run(db, snap, "get_homework_student_subjects",
                      {"person_id": hw_seed.pids[0], "window_days": 7})
    finally:
        db.close()
    assert result["error"] == "invalid_scope_param"
    assert "window_days" in result["detail"] and ("14" in result["detail"] or "30" in result["detail"])


# ────────────────────── 4. 旧端点下线守卫（冒烟） ──────────────────────


def test_legacy_chat_endpoints_offline_smoke(client):
    """旧 POST /api/chat 与 GET /api/chat/config 已于 2026-10-23 下线（404）。
    主守卫在 tests/test_mcp.py（test_legacy_chat_endpoints_offline），此处按
    同一断言风格在 v1 client 上复验一条冒烟，防止经 app.main 挂载复活。"""
    r = client.get("/api/chat/config")
    assert r.status_code == 404
    r2 = client.post("/api/chat", json={"messages": [], "context": {}})
    assert r2.status_code == 404
