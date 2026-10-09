"""P2-C3 诊断版学生报告测试（契约 docs/diagnosis-roadmap/p2-contracts.md §4/§6）。

复用 test_p1_b1_features 的 p1b1_seed（模块级 fixture：本模块获得独立重建的
同构数据集），其上补种本模块专属场景（合成姓名）：
- 秦癸（H6）：临界段连续退步（430→460→490）+ 作业缺交 → 建议 3 条封顶样本
  + 档案摘录 12 条（截断样本）+ 1 条家访 + 1 条系统行（均不入摘录）；
- 秦丑（H6）：持续进步、无风险命中（高分段+稳定）→ 建议兜底条样本；
- 甲（p1b1_seed）：偏科+作业风险+观察/谈话档案 → 完整形状与显式锚定考试样本；
- 乙/壬（p1b1_seed）：insufficient_data 与零数据空态；
- 甲·T：教学域范围隔离（仅物理、无总分、档案只读本域）。

契约 §4 覆盖点：四节结构 + suggestions；事实/规则判断/建议三层 layer 标注
（layer_legend 三层齐备）；建议 1–3 条、generated=true、「值得关注：」措辞、
based_on 可追溯、绝不越界成因果/提分保证（黑名单断言）；教师编辑不入库
（后端无写入路径，edit_scope=frontend_local_only）；exam_name 缺省最近一场/
显式更早一场（同口径重建）/未知考试 422；双工作台范围与 P1 一致（教学域
不读全科/总分/他域档案）；事实版报告端点不受影响（回归冒烟）。
"""

import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from .test_p1_b1_features import EXAM_FINAL, EXAM_MID, EXAM_YIMO, p1b1_seed  # noqa: F401

API = "/api/v1"


@pytest.fixture(scope="module")
def p2c3_seed(p1b1_seed):
    """在 p1b1_seed 之上补种 C3 场景（ORM 直种，合成姓名：秦癸/秦丑）。"""
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    s = p1b1_seed.seed
    db = SessionLocal()
    today = date.today()

    def d(days_ago: int) -> date:
        return today - timedelta(days=days_ago)

    def fact(domain, ay_id, cls_id, ident_id, exam, exam_date, subject=None,
             total_type=None, score=None, pct=None, xueji=None, grade_rank=None):
        f = wm.ScoreFact()
        f.data_domain = domain
        f.academic_year_id = ay_id
        f.exam_name = exam
        f.exam_date = date.fromisoformat(exam_date) if exam_date else None
        f.class_ref_id = cls_id
        f.identity_id = ident_id
        f.subject = subject
        f.total_type = total_type
        f.score = score
        f.grade_percentile = pct
        f.xueji_rank = xueji
        f.grade_rank = grade_rank
        f.source = "p2-c3-test"
        db.add(f)
        return f

    gui = wm.WsStudentIdentity(data_domain="homeroom", display_name="秦癸")
    chou = wm.WsStudentIdentity(data_domain="homeroom", display_name="秦丑")
    db.add_all([gui, chou])
    db.flush()
    for ident, seat in ((gui, 6), (chou, 7)):
        db.add(wm.Enrollment(
            admin_class_id=s.h6_id,
            identity_id=ident.id,
            status="active",
            valid_from=date(2025, 9, 1),
            seat_no=seat,
        ))

    # ── 秦癸：临界段连续退步 430→460→490；语文/数学贴近总体（不偏科）──
    exam_dates = {EXAM_MID: "2025-11-06", EXAM_FINAL: "2026-01-15", EXAM_YIMO: "2026-03-20"}
    gui_ranks = {EXAM_MID: 430, EXAM_FINAL: 460, EXAM_YIMO: 490}
    gui_pcts = {EXAM_MID: 0.60, EXAM_FINAL: 0.62, EXAM_YIMO: 0.63}
    for exam in (EXAM_MID, EXAM_FINAL, EXAM_YIMO):
        fact("homeroom", s.ay_id, s.h6_id, gui.id, exam, exam_dates[exam], None, "主三门",
             200.0, pct=gui_pcts[exam], xueji=gui_ranks[exam], grade_rank=gui_ranks[exam] + 10)
        fact("homeroom", s.ay_id, s.h6_id, gui.id, exam, exam_dates[exam], "语文", None,
             70.0, pct=gui_pcts[exam] - 0.05)
        fact("homeroom", s.ay_id, s.h6_id, gui.id, exam, exam_dates[exam], "数学", None,
             75.0, pct=gui_pcts[exam] + 0.05)

    # ── 秦丑：持续进步 120→95→70（高分段+稳定），无作业风险/偏科 ──
    chou_ranks = {EXAM_MID: 120, EXAM_FINAL: 95, EXAM_YIMO: 70}
    chou_pcts = {EXAM_MID: 0.30, EXAM_FINAL: 0.28, EXAM_YIMO: 0.26}
    for exam in (EXAM_MID, EXAM_FINAL, EXAM_YIMO):
        fact("homeroom", s.ay_id, s.h6_id, chou.id, exam, exam_dates[exam], None, "主三门",
             240.0, pct=chou_pcts[exam], xueji=chou_ranks[exam], grade_rank=chou_ranks[exam] + 5)
        fact("homeroom", s.ay_id, s.h6_id, chou.id, exam, exam_dates[exam], "语文", None,
             80.0, pct=chou_pcts[exam])

    # ── 作业：癸近 30 天 4 次缺交、当前连缺 3 天 ──
    expected_gui = [gui.id]

    def assignment(subject, hw_type, on_date, token):
        a = wm.HomeworkAssignment(
            data_domain="homeroom",
            class_ref_id=s.h6_id,
            academic_year_id=s.ay_id,
            subject=subject,
            homework_type=hw_type,
            assigned_date=on_date,
            batch_token=token,
            expected_members_json=json.dumps(expected_gui),
            status="active",
        )
        db.add(a)
        return a

    hw1 = assignment("数学", "练习册", d(1), "p2c3-gui-1")
    hw2 = assignment("数学", "练习册", d(2), "p2c3-gui-2")
    hw3 = assignment("数学", "练习册", d(3), "p2c3-gui-3")
    hw4 = assignment("语文", "练习册", d(29), "p2c3-gui-4")
    db.flush()
    for a in (hw1, hw2, hw3, hw4):
        db.add(wm.HomeworkSubmission(
            assignment_id=a.id, person_id=gui.id, submission_status="missing",
        ))

    # ── 档案：癸 12 条观察/谈话（截断样本）+ 1 条家访 + 1 条系统行（均排除）──
    for i in range(12):
        db.add(wm.WsStudentNote(
            data_domain="homeroom", person_id=gui.id, date=d(i + 1),
            category="谈话" if i % 2 == 0 else "观察",
            content=f"第 {i + 1} 次合成记录：状态跟踪",
        ))
    db.add(wm.WsStudentNote(
        data_domain="homeroom", person_id=gui.id, date=d(13), category="家访",
        content="家访记录：不应进入观察摘录",
    ))
    db.add(wm.WsStudentNote(
        data_domain="homeroom", person_id=gui.id, date=d(14), category="观察",
        content="[忘带] 系统辅助行：不应进入观察摘录", source="homework:777",
    ))

    db.commit()
    yield SimpleNamespace(seed=s, gui_h_id=gui.id, chou_h_id=chou.id, today=today)
    db.close()


def _get(client, path, **params):
    return client.get(f"{API}{path}", params=params)


# ────────────── 契约 §4：四节结构 + 三层标注 ──────────────


def test_report_full_shape_homeroom(client, p2c3_seed):
    """甲：四节齐备、layer 标注三层、建议 2 条（作业+偏科）、档案摘录域内可见。"""
    s = p2c3_seed.seed
    resp = _get(client, "/homeroom/diagnosis/report", person_id=s.jia_h_id,
                academic_year_id=s.ay_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["person_id"] == s.jia_h_id
    assert body["name"] == "秦甲"
    assert body["calc_version"] == "p2-v1"
    assert body["scope_mode"] == "homeroom"
    assert body["exam_name"] == EXAM_YIMO
    assert body["exam_name_source"] == "default_latest"

    # 三层图例与分节标注
    assert set(body["layer_legend"]) == {"fact", "rule_judgment", "suggestion"}
    assert body["learning_state"]["layer"] == "rule_judgment"
    assert body["subject_performance"]["layer"] == "fact"
    assert body["behavior"]["layer"] == "fact"
    assert body["teacher_observations"]["layer"] == "fact"
    assert body["suggestions"]["layer"] == "suggestion"
    # 偏科嵌套块=规则判断（各科百分位本身=事实）
    assert body["subject_performance"]["imbalance"]["layer"] == "rule_judgment"

    # 节 1：学习状态（B1/B2 同源透传）
    ls = body["learning_state"]
    assert ls["summary_source"].startswith("B1")
    assert ls["main_type"] == "明显偏科型"
    assert ls["secondary_tags"] == ["综合风险型", "作业风险型"]
    assert ls["classification_status"] == "classified"
    assert ls["latest_exam_name"] == EXAM_YIMO
    assert ls["main3"]["rank"] == 96
    assert ls["trend"]["direction_recent"] == "进步"
    assert ls["stability"]["label"] == "中等波动"
    assert ls["evidence"] and all(e["basis"] for e in ls["evidence"])

    # 节 2：各科表现（最近一场，逐字段与 B1 current_level 同源）
    sp = body["subject_performance"]
    assert sp["exam_name"] == EXAM_YIMO
    by_subject = {row["subject"]: row for row in sp["subjects"]}
    assert by_subject["化学"] == {"subject": "化学", "percentile": 0.09, "grade_score": 85.0}
    assert "物理" not in by_subject  # 一模缺科
    assert sp["imbalance"]["severe"] == ["英语"]
    assert sp["imbalance"]["subjects"][0]["subject"] == "化学"  # sorted() 中文码点序

    # 节 3：作业行为（B1 窗口数据透传）
    bh = body["behavior"]
    assert bh["missing_30d"] == 5
    assert bh["current_streak_days"] == 2
    assert "30" in bh["window_note"]

    # 节 4：观察/谈话摘录（谈话×2 + 观察×1；家访与系统行排除；日期降序）
    obs = body["teacher_observations"]
    assert obs["visible_domain"] == "homeroom"
    assert obs["total"] == 3
    assert obs["truncated"] is False
    assert [item["category"] for item in obs["items"]] == ["观察", "谈话", "谈话"]
    contents = " ".join(item["content"] for item in obs["items"])
    assert "课堂打瞌睡" in contents
    assert "家访记录" not in contents
    assert "[忘带]" not in contents  # 系统辅助行不入摘录
    assert "跟进完成" in contents

    # 节 5：建议（generated，1–3 条，「值得关注」措辞，based_on 可追溯）
    sug = body["suggestions"]
    assert sug["generated"] is True
    assert sug["edit_scope"] == "frontend_local_only"
    assert 1 <= len(sug["items"]) <= 3
    texts = [item["text"] for item in sug["items"]]
    assert all(text.startswith("值得关注：") for text in texts)
    assert any("缺交 5 次" in text for text in texts)
    assert any("英语" in text for text in texts)
    assert all(item["based_on"] for item in sug["items"])
    assert sug["items"][0]["based_on"] == ["作业风险型"]
    assert "不构成因果结论或提分保证" in sug["disclaimer"]


def test_report_layers_legend_human_readable(client, p2c3_seed):
    """图例必须让人能分辨三层（事实/规则判断/建议），且建议层含免责表述。"""
    s = p2c3_seed.seed
    body = _get(client, "/homeroom/diagnosis/report", person_id=s.jia_h_id,
                academic_year_id=s.ay_id).json()
    legend = body["layer_legend"]
    assert "事实" in legend["fact"]
    assert "规则判断" in legend["rule_judgment"]
    assert "建议" in legend["suggestion"]
    assert "不构成因果结论或提分保证" in legend["suggestion"]


# ────────────── 建议生成：触发器、封顶、兜底、因果黑名单 ──────────────


def test_suggestions_decline_cap_three(client, p2c3_seed):
    """秦癸：持续下滑+临界段+作业风险 → 恰好 3 条（封顶），首条=退步方向。"""
    s = p2c3_seed.seed
    body = _get(client, "/homeroom/diagnosis/report", person_id=p2c3_seed.gui_h_id,
                academic_year_id=s.ay_id).json()
    assert body["learning_state"]["main_type"] == "持续下滑型"
    assert "综合风险型" in body["learning_state"]["secondary_tags"]
    sug = body["suggestions"]
    assert len(sug["items"]) == 3
    first = sug["items"][0]
    assert "退步" in first["text"]
    assert "+30" in first["text"]  # 名次变化 +30（负值=名次提升的方向解释在场）
    assert first["based_on"] == ["持续下滑型"]
    assert any("缺交 4 次" in item["text"] for item in sug["items"])
    assert any("临界段" in item["text"] for item in sug["items"])


def test_suggestions_fallback_when_no_risk(client, p2c3_seed):
    """秦丑：持续进步、无风险命中 → 恰好 1 条兜底（不编造风险）。"""
    s = p2c3_seed.seed
    body = _get(client, "/homeroom/diagnosis/report", person_id=p2c3_seed.chou_h_id,
                academic_year_id=s.ay_id).json()
    assert body["learning_state"]["main_type"] == "持续进步型"
    sug = body["suggestions"]
    assert len(sug["items"]) == 1
    item = sug["items"][0]
    assert item["text"].startswith("值得关注：")
    assert "暂无触发风险类规则的项目" in item["text"]
    assert "持续进步型" in item["text"]
    assert item["based_on"] == ["持续进步型"]


def test_suggestions_insufficient_data(client, p2c3_seed):
    """乙：数据不足门 + 作业风险 + 临界段 → 3 条（含数据不足提示）。"""
    s = p2c3_seed.seed
    body = _get(client, "/homeroom/diagnosis/report", person_id=s.yi_h_id,
                academic_year_id=s.ay_id).json()
    assert body["learning_state"]["classification_status"] == "insufficient_data"
    texts = [item["text"] for item in body["suggestions"]["items"]]
    assert len(texts) == 3
    assert any("数据不足 2 场" in text for text in texts)
    assert any("临界段" in text for text in texts)


def test_suggestions_never_causal_or_promising(client, p2c3_seed, p1b1_seed):
    """契约 §6：建议绝不越界成因果表述/提分保证——对多名样本跑黑名单。"""
    s = p2c3_seed.seed
    banned = ["因为", "由于", "所以", "导致", "必然", "保证", "提分",
              "提高成绩", "提升成绩", "将会提高", "一定会"]
    checked = 0
    for person_id in (s.jia_h_id, s.yi_h_id, p2c3_seed.gui_h_id,
                      p2c3_seed.chou_h_id, p1b1_seed.ren_h_id):
        body = _get(client, "/homeroom/diagnosis/report", person_id=person_id,
                    academic_year_id=s.ay_id).json()
        items = body["suggestions"]["items"]
        assert 1 <= len(items) <= 3
        for item in items:
            assert item["text"].startswith("值得关注：")
            for word in banned:
                assert word not in item["text"], (person_id, word, item["text"])
            assert item["based_on"], item
            checked += 1
    assert checked >= 5


# ────────────── exam_name 锚定 ──────────────


def test_explicit_earlier_exam_anchor(client, p2c3_seed):
    """显式锚定更早一场：各科百分位/偏科按该场同口径重建；学习状态仍学年口径。"""
    s = p2c3_seed.seed
    resp = _get(client, "/homeroom/diagnosis/report", person_id=s.jia_h_id,
                academic_year_id=s.ay_id, exam_name=EXAM_MID)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["exam_name"] == EXAM_MID
    assert body["exam_name_source"] == "explicit"

    sp = body["subject_performance"]
    by_subject = {row["subject"]: row for row in sp["subjects"]}
    assert set(by_subject) == {"语文", "数学", "英语", "物理"}
    assert by_subject["语文"]["percentile"] == 0.25
    assert by_subject["数学"]["percentile"] == 0.18  # 18 → 0.18 归一
    imb = {row["subject"]: row for row in sp["imbalance"]["subjects"]}
    # diff_pct_point = 单科百分位 − 总体百分位（0.30），正值=更弱；单位=百分点
    assert imb["英语"]["diff_pct_point"] == 20.0
    assert imb["语文"]["diff_pct_point"] == -5.0
    # 连续场数只从锚定场向前数：期中即锚定场 → 英语仅 1 场 → 不进 severe
    assert imb["英语"]["consecutive_exams"] == 1
    assert sp["imbalance"]["severe"] == []

    # 学习状态不受锚定影响（B1/B2 学年口径）
    assert body["learning_state"]["latest_exam_name"] == EXAM_YIMO


def test_unknown_exam_name_422(client, p2c3_seed):
    s = p2c3_seed.seed
    resp = _get(client, "/homeroom/diagnosis/report", person_id=s.jia_h_id,
                academic_year_id=s.ay_id, exam_name="不存在的考试")
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_scope_param"


# ────────────── 双工作台范围（与 P1 一致） ──────────────


def test_teaching_domain_scope_isolation(client, p2c3_seed):
    """甲·T：仅物理、无总分（偏科 not_computable）、作业/档案只读本域。"""
    s = p2c3_seed.seed
    resp = _get(client, "/teaching/diagnosis/report", person_id=s.jia_t_id,
                academic_year_id=s.ay_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope_mode"] == "teaching"
    assert body["data_domain"] == "teaching"

    sp = body["subject_performance"]
    assert [row["subject"] for row in sp["subjects"]] == ["物理"]
    assert sp["imbalance"]["status"] == "not_computable"
    assert sp["imbalance"]["missing_reason"] == "no_main3_row"

    ls = body["learning_state"]
    assert ls["main_type"] == "作业风险型"  # 连缺 2 天达到作业风险阈值
    assert ls["classification_status"] == "classified"

    obs = body["teacher_observations"]
    assert obs["visible_domain"] == "teaching"
    assert obs["total"] == 1
    assert obs["items"][0]["content"] == "物理作业连缺提醒"
    # 班主任域档案绝不泄漏
    assert all("近期状态波动" not in item["content"] for item in obs["items"])

    texts = [item["text"] for item in body["suggestions"]["items"]]
    assert len(texts) == 1
    assert "连缺 2 天" in texts[0]


def test_out_of_scope_person_404(client, p2c3_seed):
    s = p2c3_seed.seed
    resp = _get(client, "/homeroom/diagnosis/report", person_id=s.jia_t_id,
                academic_year_id=s.ay_id)
    assert resp.status_code == 404
    assert resp.json()["error"] == "resource_out_of_scope"
    assert _get(client, "/teaching/diagnosis/report", person_id=s.jia_h_id,
                academic_year_id=s.ay_id).status_code == 404
    assert _get(client, "/homeroom/diagnosis/report", person_id=999999,
                academic_year_id=s.ay_id).status_code == 404
    assert client.get(f"{API}/homeroom/diagnosis/report").status_code == 422


# ────────────── 零数据空态 ──────────────


def test_zero_data_student(client, p2c3_seed, p1b1_seed):
    """壬：零数据学生 → 锚定 None、各科空、建议 1 条（数据不足）、摘录空态。"""
    s = p2c3_seed.seed
    resp = _get(client, "/homeroom/diagnosis/report", person_id=p1b1_seed.ren_h_id,
                academic_year_id=s.ay_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["exam_name"] is None
    assert body["subject_performance"]["subjects"] == []
    assert body["subject_performance"]["imbalance"]["status"] == "not_computable"
    assert body["learning_state"]["classification_status"] == "insufficient_data"
    assert body["teacher_observations"]["total"] == 0
    assert body["teacher_observations"]["items"] == []
    texts = [item["text"] for item in body["suggestions"]["items"]]
    assert len(texts) == 1
    assert "数据不足" in texts[0]


# ────────────── 档案摘录截断 ──────────────


def test_observations_truncated_at_limit(client, p2c3_seed):
    """秦癸：12 条观察/谈话 → 只取最近 10 条，total/truncated 如实。"""
    s = p2c3_seed.seed
    body = _get(client, "/homeroom/diagnosis/report", person_id=p2c3_seed.gui_h_id,
                academic_year_id=s.ay_id).json()
    obs = body["teacher_observations"]
    assert obs["total"] == 12
    assert obs["truncated"] is True
    assert len(obs["items"]) == obs["limit"] == 10
    dates = [item["date"] for item in obs["items"]]
    assert dates == sorted(dates, reverse=True)  # 日期降序（最近优先）
    assert all(item["category"] in ("谈话", "观察") for item in obs["items"])
    contents = " ".join(item["content"] for item in obs["items"])
    assert "家访记录" not in contents and "[忘带]" not in contents


# ────────────── 同源：服务函数输出 == HTTP 响应 ──────────────


def test_endpoint_matches_service_output(client, p2c3_seed):
    """B1/B2 同源前提的延伸：HTTP 响应与 service 输出逐字段一致（双域）。"""
    from app.api.students_mgmt import _homeroom_ctx, _teaching_ctx
    from app.db.models import SessionLocal
    from app.diagnosis.report import student_report

    s = p2c3_seed.seed
    db = SessionLocal()
    try:
        ctx = _homeroom_ctx(db, s.ay_id)
        expected = student_report(db, ctx, s.jia_h_id, s.ay_id)
        http = _get(client, "/homeroom/diagnosis/report", person_id=s.jia_h_id,
                    academic_year_id=s.ay_id).json()
        assert http == expected

        expected_anchored = student_report(db, ctx, s.jia_h_id, s.ay_id, EXAM_MID)
        http_anchored = _get(client, "/homeroom/diagnosis/report", person_id=s.jia_h_id,
                             academic_year_id=s.ay_id, exam_name=EXAM_MID).json()
        assert http_anchored == expected_anchored

        tctx = _teaching_ctx(db, s.ay_id)
        expected_t = student_report(db, tctx, s.jia_t_id, s.ay_id)
        http_t = _get(client, "/teaching/diagnosis/report", person_id=s.jia_t_id,
                      academic_year_id=s.ay_id).json()
        assert http_t == expected_t
    finally:
        db.close()


# ────────────── 事实版报告保留不动（回归冒烟） ──────────────


def test_fact_report_endpoints_untouched(client, p2c3_seed):
    """既有事实版报告端点照常工作，未受 C3 影响（契约 §4「事实版报告保留不动」）。"""
    s = p2c3_seed.seed
    resp = _get(client, f"/homeroom/students/{s.jia_h_id}/report")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["person"]["name"] == "秦甲"
    assert "notes_summary" in body
    # 诊断端点不改变事实版形状：事实版无诊断字段
    assert "suggestions" not in body and "learning_state" not in body
