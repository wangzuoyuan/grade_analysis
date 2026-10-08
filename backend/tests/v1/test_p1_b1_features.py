"""P1-B1 诊断特征层测试（契约 docs/diagnosis-roadmap/p1-contracts.md §2/§4/§8）。

在 tests/v1/conftest.py 合成样本（v1_seed）之上 ORM 直种诊断场景，覆盖 §8
要求的全部边界情形：
- 缺考：丙期末主三门/语文 score=NULL → missing_reason、percentile=null，绝不转 0；
- 缺科：甲 2026一模 无物理行 → subjects 不出现物理，偏科不判该科；
- 百分位方向与单位：percentile 归一 0–1（存 18 → 0.18）、diff_pct_point 为
  百分点（0.37 → 37.0，正值=该科相对位置更差）、名次变小=进步；
- 同分：辛与乙成绩/名次/百分位/作业状态完全一致 → features 除 person_id 外逐字段一致；
- 跨学年：AY2 事实与作业绝不漏进 AY1 特征；显式 academic_year_id 按该学年取数；
  服务层学年错配 422；
- 范围隔离：教学域只有任教学科、无总分行（总体类指标 not_computable）、
  作业/档案只读本域；
- 稀疏历史：单场考试 → trend/stability「数据不足」，偏科连续 1 场不进 severe。
作业窗口边界（7/30 天含当日）与教师关注（follow_up/note、系统行排除）单列断言。
样本全部合成（秦甲/秦乙/秦丙/秦辛/秦壬 及教学域 秦X·T）。
"""

import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

API = "/api/v1"

EXAM_MID = "2025期中"  # v1_seed 原有（2025-11-06）
EXAM_FINAL = "2025期末"  # 本模块补种（2026-01-15）
EXAM_YIMO = "2026一模"  # 本模块补种（2026-03-20，甲缺物理=缺科样本）
EXAM_OLD = "2024期末"  # AY2 旧学年（2025-01-10）


@pytest.fixture(scope="module")
def p1b1_seed(v1_seed):
    """在 v1_seed 基础上补种诊断场景（ORM 直种，不走导入链路）。"""
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    s = v1_seed
    db = SessionLocal()
    today = date.today()

    def d(days_ago: int) -> date:
        return today - timedelta(days=days_ago)

    def fact(domain, ay_id, cls_id, ident_id, exam, exam_date, subject=None,
             total_type=None, score=None, pct=None, xueji=None, grade_rank=None,
             grade_score=None):
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
        f.grade_score = grade_score
        f.source = "p1-b1-test"
        db.add(f)
        return f

    def update_fact(domain, ident_id, exam, subject=None, total_type=None, **fields):
        query = db.query(wm.ScoreFact).filter(
            wm.ScoreFact.data_domain == domain,
            wm.ScoreFact.identity_id == ident_id,
            wm.ScoreFact.exam_name == exam,
        )
        row = (
            query.filter(wm.ScoreFact.total_type == total_type).one()
            if total_type is not None
            else query.filter(wm.ScoreFact.subject == subject).one()
        )
        for key, value in fields.items():
            setattr(row, key, value)
        return row

    # ── 甲（H6）：三场主三门名次 120→108→96（进步方向、无 ≥20 单步连击）──
    update_fact("homeroom", s.jia_h_id, EXAM_MID, total_type="主三门",
                xueji_rank=120, grade_rank=130, grade_percentile=0.30)
    update_fact("homeroom", s.jia_h_id, EXAM_MID, subject="语文", grade_percentile=0.25)
    update_fact("homeroom", s.jia_h_id, EXAM_MID, subject="数学", grade_percentile=18)  # 百分数形式
    update_fact("homeroom", s.jia_h_id, EXAM_MID, subject="英语", grade_percentile=0.50)
    update_fact("homeroom", s.jia_h_id, EXAM_MID, subject="物理", grade_percentile=0.10)
    fact("homeroom", s.ay_id, s.h6_id, s.jia_h_id, EXAM_FINAL, "2026-01-15", None, "主三门", 279.0,
         pct=0.24, xueji=108, grade_rank=112)
    fact("homeroom", s.ay_id, s.h6_id, s.jia_h_id, EXAM_FINAL, "2026-01-15", "语文", None, 90.0, pct=0.20)
    fact("homeroom", s.ay_id, s.h6_id, s.jia_h_id, EXAM_FINAL, "2026-01-15", "数学", None, 94.0, pct=0.15)
    fact("homeroom", s.ay_id, s.h6_id, s.jia_h_id, EXAM_FINAL, "2026-01-15", "英语", None, 93.0, pct=0.52)
    fact("homeroom", s.ay_id, s.h6_id, s.jia_h_id, EXAM_FINAL, "2026-01-15", "物理", None, 92.0, pct=0.08)
    # 一模：物理缺科；化学为选考等级分样本
    fact("homeroom", s.ay_id, s.h6_id, s.jia_h_id, EXAM_YIMO, "2026-03-20", None, "主三门", 276.0,
         pct=0.18, xueji=96, grade_rank=101)
    fact("homeroom", s.ay_id, s.h6_id, s.jia_h_id, EXAM_YIMO, "2026-03-20", "语文", None, 91.0, pct=0.18)
    fact("homeroom", s.ay_id, s.h6_id, s.jia_h_id, EXAM_YIMO, "2026-03-20", "数学", None, 95.0, pct=0.12)
    fact("homeroom", s.ay_id, s.h6_id, s.jia_h_id, EXAM_YIMO, "2026-03-20", "英语", None, 90.0, pct=0.55)
    fact("homeroom", s.ay_id, s.h6_id, s.jia_h_id, EXAM_YIMO, "2026-03-20", "化学", None, 82.0,
         pct=0.09, grade_score=85.0)

    # ── 乙（H6）：稀疏历史（仅期中），临界段，语文偏科仅连续 1 场 ──
    update_fact("homeroom", s.yi_h_id, EXAM_MID, total_type="主三门",
                xueji_rank=420, grade_percentile=0.62)
    update_fact("homeroom", s.yi_h_id, EXAM_MID, subject="语文", grade_percentile=0.85)
    update_fact("homeroom", s.yi_h_id, EXAM_MID, subject="数学", grade_percentile=0.60)
    update_fact("homeroom", s.yi_h_id, EXAM_MID, subject="物理", grade_percentile=0.40)
    # 英语百分位不导入 → 缺百分位样本

    # ── 丙（H6）：期末缺考（主三门/语文 score=NULL，无名次无百分位）──
    update_fact("homeroom", s.bing_h_id, EXAM_MID, total_type="主三门",
                xueji_rank=510, grade_percentile=None)
    update_fact("homeroom", s.bing_h_id, EXAM_MID, subject="语文", grade_percentile=0.80)
    fact("homeroom", s.ay_id, s.h6_id, s.bing_h_id, EXAM_FINAL, "2026-01-15", None, "主三门", None)
    fact("homeroom", s.ay_id, s.h6_id, s.bing_h_id, EXAM_FINAL, "2026-01-15", "语文", None, None)

    # ── 辛（H6 新增）：与乙成绩/名次/百分位/作业状态完全一致（同分样本）──
    # ── 壬（H6 新增）：在班但零数据（空态样本）──
    xin_h = wm.WsStudentIdentity(data_domain="homeroom", display_name="秦辛")
    ren_h = wm.WsStudentIdentity(data_domain="homeroom", display_name="秦壬")
    db.add_all([xin_h, ren_h])
    db.flush()
    for ident, seat in ((xin_h, 4), (ren_h, 5)):
        db.add(wm.Enrollment(
            admin_class_id=s.h6_id,
            identity_id=ident.id,
            status="active",
            valid_from=date(2025, 9, 1),
            seat_no=seat,
        ))
    fact("homeroom", s.ay_id, s.h6_id, xin_h.id, EXAM_MID, "2025-11-06", "语文", None, 76.0, pct=0.85)
    fact("homeroom", s.ay_id, s.h6_id, xin_h.id, EXAM_MID, "2025-11-06", "数学", None, 81.0, pct=0.60)
    fact("homeroom", s.ay_id, s.h6_id, xin_h.id, EXAM_MID, "2025-11-06", "英语", None, 79.0)
    fact("homeroom", s.ay_id, s.h6_id, xin_h.id, EXAM_MID, "2025-11-06", "物理", None, 84.0, pct=0.40)
    fact("homeroom", s.ay_id, s.h6_id, xin_h.id, EXAM_MID, "2025-11-06", None, "主三门", 236.0,
         pct=0.62, xueji=420)

    # ── 教学：甲·T 物理百分位（教学域无总分行）──
    update_fact("teaching", s.jia_t_id, EXAM_MID, subject="物理", grade_percentile=0.10)

    # ── AY2 旧学年（跨学年隔离样本）：甲在旧班有一场 + 一条旧学年缺交 ──
    ay2 = wm.AcademicYear(name="2024-2025", start_date=date(2024, 9, 1), end_date=date(2025, 7, 15))
    db.add(ay2)
    db.flush()
    old_cls = wm.AdministrativeClass(academic_year_id=ay2.id, grade=1, class_num=6, label="高一6班")
    db.add(old_cls)
    db.flush()
    db.add(wm.Enrollment(
        admin_class_id=old_cls.id,
        identity_id=s.jia_h_id,
        status="active",
        valid_from=date(2024, 9, 1),
        seat_no=1,
    ))
    fact("homeroom", ay2.id, old_cls.id, s.jia_h_id, EXAM_OLD, "2025-01-10", None, "主三门", 260.0,
         pct=0.50, xueji=300)
    fact("homeroom", ay2.id, old_cls.id, s.jia_h_id, EXAM_OLD, "2025-01-10", "语文", None, 85.0, pct=0.40)
    old_assignment = wm.HomeworkAssignment(
        data_domain="homeroom",
        class_ref_id=old_cls.id,
        academic_year_id=ay2.id,
        subject="数学",
        homework_type="练习册",
        assigned_date=d(1),
        batch_token="p1b1-ay2-old-1",
        expected_members_json=json.dumps([s.jia_h_id]),
        status="active",
    )
    db.add(old_assignment)
    db.flush()
    db.add(wm.HomeworkSubmission(
        assignment_id=old_assignment.id, person_id=s.jia_h_id, submission_status="missing",
    ))

    # ── 作业（H6，班主任按学科分线）：窗口边界 + 连缺 + 忘带 + 负面评价 ──
    expected_h6 = [s.jia_h_id, s.yi_h_id, s.bing_h_id, xin_h.id, ren_h.id]

    def assignment(domain, cls_id, ay_id, subject, hw_type, on_date, expected_ids, token):
        a = wm.HomeworkAssignment(
            data_domain=domain,
            class_ref_id=cls_id,
            academic_year_id=ay_id,
            subject=subject,
            homework_type=hw_type,
            assigned_date=on_date,
            batch_token=token,
            expected_members_json=json.dumps(expected_ids),
            status="active",
        )
        db.add(a)
        return a

    def submission(a, pid, status, evaluation=None):
        db.add(wm.HomeworkSubmission(
            assignment_id=a.id, person_id=pid, submission_status=status, evaluation=evaluation,
        ))

    m1 = assignment("homeroom", s.h6_id, s.ay_id, "数学", "练习册", d(1), expected_h6, "p1b1-m1")
    m2 = assignment("homeroom", s.h6_id, s.ay_id, "数学", "练习册", d(2), expected_h6, "p1b1-m2")
    m3 = assignment("homeroom", s.h6_id, s.ay_id, "数学", "练习册", d(3), expected_h6, "p1b1-m3")
    m4 = assignment("homeroom", s.h6_id, s.ay_id, "数学", "练习册", d(29), expected_h6, "p1b1-m4")
    m5 = assignment("homeroom", s.h6_id, s.ay_id, "数学", "练习册", d(31), expected_h6, "p1b1-m5")
    w1 = assignment("homeroom", s.h6_id, s.ay_id, "数学", "练习册", d(4), expected_h6, "p1b1-w1")
    g1 = assignment("homeroom", s.h6_id, s.ay_id, "语文", "练习册", d(8), expected_h6, "p1b1-g1")
    g2 = assignment("homeroom", s.h6_id, s.ay_id, "语文", "练习册", d(7), expected_h6, "p1b1-g2")
    g3 = assignment("homeroom", s.h6_id, s.ay_id, "语文", "练习册", d(5), expected_h6, "p1b1-g3")
    db.flush()
    # 甲：近 2 天连缺（数学线 current=2），30 天 5 次纯缺交（m4/g1/g2 计入、m5 出窗）
    submission(m1, s.jia_h_id, "missing")
    submission(m2, s.jia_h_id, "missing")
    submission(m3, s.jia_h_id, "submitted")
    submission(m4, s.jia_h_id, "missing")
    submission(m5, s.jia_h_id, "missing")
    submission(w1, s.jia_h_id, "submitted", "忘带了，明天补交")
    submission(g1, s.jia_h_id, "missing")
    submission(g2, s.jia_h_id, "missing")
    submission(g3, s.jia_h_id, "submitted", "马虎，重做")
    # 乙/辛（同分同作业状态）：近 3 天连缺，前 7 天仅 1 次 → 恶化
    for pid in (s.yi_h_id, xin_h.id):
        submission(m1, pid, "missing")
        submission(m2, pid, "missing")
        submission(m3, pid, "missing")
        submission(m4, pid, "submitted")
        submission(g1, pid, "missing")
    # 丙/壬无任何例外行 → 全部默认已交（计数 0）

    # 教学（T6 物理，教学不分作业种类单线）：甲·T 近 2 天连缺
    expected_t6 = [s.jia_t_id, s.yi_t_id, s.ding_t_id]
    p1 = assignment("teaching", s.t6_id, s.ay_id, "物理", "练习册", d(1), expected_t6, "p1b1-t6-p1")
    p2 = assignment("teaching", s.t6_id, s.ay_id, "物理", "练习册", d(2), expected_t6, "p1b1-t6-p2")
    db.flush()
    submission(p1, s.jia_t_id, "missing")
    submission(p2, s.jia_t_id, "missing")

    # ── 档案（N01 域隔离）：甲 homeroom 域谈话/家访/跟进；甲·T teaching 域 ──
    def note(domain, pid, on_date, category, content, follow_up=None, done=0, source=None):
        db.add(wm.WsStudentNote(
            data_domain=domain, person_id=pid, date=on_date, category=category,
            content=content, follow_up=follow_up, follow_up_done=done, source=source,
        ))

    note("homeroom", s.jia_h_id, d(12), "谈话", "近期状态波动，已谈", follow_up="每周谈一次", done=0)
    note("homeroom", s.jia_h_id, d(40), "家访", "家长沟通开学情况")
    note("homeroom", s.jia_h_id, d(2), "观察", "课堂打瞌睡一次")
    note("homeroom", s.jia_h_id, d(20), "谈话", "跟进完成", follow_up="已沟通", done=1)
    note("homeroom", s.jia_h_id, d(3), "其他", "[忘带] 作业本", source="homework:999")  # 系统行，排除
    note("teaching", s.jia_t_id, d(3), "谈话", "物理作业连缺提醒", follow_up="盯作业", done=0)

    db.commit()
    yield SimpleNamespace(
        seed=s,
        xin_h_id=xin_h.id,
        ren_h_id=ren_h.id,
        ay2_id=ay2.id,
        old_cls_id=old_cls.id,
        today=today,
    )
    db.close()


def _get(client, path, **params):
    return client.get(f"{API}{path}", params=params)


def _by_subject(rows):
    return {row["subject"]: row for row in rows}


# ────────────── 契约 §4 阈值常量（B2/B3 import 面） ──────────────


def test_thresholds_constants_match_contract():
    """§4 常量名与默认值照抄；stability_label 按 (0.10, 0.25] 两档分界。"""
    from app.diagnosis import thresholds as th

    assert th.STABILITY_WINDOW_N == 5
    assert th.STABILITY_MIN_POINTS == 3
    assert th.STABILITY_RANGE_LABELS == ((0.10, "稳定"), (0.25, "中等波动"))
    assert th.TREND_DIRECTION_MIN_CHANGE == 20
    assert th.HOMEWORK_RISK_30D == 3
    assert th.HOMEWORK_RISK_STREAK_DAYS == 2
    assert th.IMBALANCE_MIN_CONSECUTIVE == 2
    assert th.CHANGE_DECOMPOSITION_TOP_N == 3
    assert th.CLASS_GROUP_MIN_SIZE == 3
    assert th.CALC_VERSION == "p1-v1"
    assert th.stability_label(0.10) == "稳定"
    assert th.stability_label(0.10 + 1e-9) == "中等波动"
    assert th.stability_label(0.25) == "中等波动"
    assert th.stability_label(0.26) == "高波动"


# ────────────── 单生特征：班主任域完整形状 ──────────────


def test_homeroom_features_full_shape(client, p1b1_seed):
    s = p1b1_seed.seed
    resp = _get(client, "/homeroom/diagnosis/features", person_id=s.jia_h_id,
                academic_year_id=s.ay_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["person_id"] == s.jia_h_id
    assert body["calc_version"] == "p1-v1"
    assert body["academic_year_id"] == s.ay_id
    assert set(body["indicators"]) == {
        "current_level", "trend", "stability", "imbalance",
        "homework_behavior", "teacher_attention",
    }
    assert body["data_quality"]["valid_exam_count"] == 3

    cl = body["indicators"]["current_level"]
    assert cl["status"] == "ok"
    assert cl["exam_name"] == EXAM_YIMO
    assert cl["as_of"] == "2026-03-20"
    main3 = cl["main3"]
    assert main3["rank"] == 96
    assert main3["percentile"] == 0.18
    assert main3["basis"] == "school"
    assert main3["missing_reason"] is None
    subjects = _by_subject(cl["subjects"])
    # 缺科：一模无物理行；等级分仅选考化学输出
    assert set(subjects) == {"语文", "数学", "英语", "化学"}
    assert subjects["化学"] == {"subject": "化学", "percentile": 0.09, "grade_score": 85.0}
    assert subjects["数学"]["percentile"] == 0.12
    assert cl["bands"] == {"high_score": False, "critical": False, "weak": False}

    trend = body["indicators"]["trend"]
    assert trend["status"] == "ok"
    assert trend["last_change"] == {"from": EXAM_FINAL, "to": EXAM_YIMO, "rank_change": -12}
    # 百分位方向语义：名次 120→96（变小）= 进步（方向正确性）
    assert trend["direction_recent"] == "进步"
    # 单步 |变化| 均 <20 → 无连击
    assert trend["streak"] == {"kind": None, "count": 0}
    assert trend["long_term"] == "上升"
    assert trend["valid_exam_count"] == 3

    stability = body["indicators"]["stability"]
    assert stability["window_n"] == 5
    assert stability["statistic"] == "range"
    assert stability["min_points"] == 3
    assert stability["value"] == 0.12  # 0.30-0.18 极差 → 中等波动
    assert stability["label"] == "中等波动"

    imbalance = body["indicators"]["imbalance"]
    assert imbalance["status"] == "ok"
    rows = _by_subject(imbalance["subjects"])
    # 百分点单位：0.55-0.18=0.37 → 37.0（正值=该科更差）；英语连续 3 场（含 0.20 恰等边界）
    assert rows["英语"]["diff_pct_point"] == 37.0
    assert rows["英语"]["consecutive_exams"] == 3
    assert rows["化学"]["diff_pct_point"] == -9.0
    assert rows["化学"]["consecutive_exams"] == 0
    assert imbalance["severe"] == ["英语"]

    hw = body["indicators"]["homework_behavior"]
    assert hw["status"] == "ok"
    assert hw["missing_7d"] == 2  # d7 的语文缺交在 7 天窗外（窗口含当日、按自然日回溯）
    assert hw["missing_30d"] == 5  # d29 计入、d31 出窗（30 天边界）
    assert hw["current_streak_days"] == 2  # 按天连缺：最近两天缺、d3 已交打断
    assert hw["trend"] == "持平"  # 近 7 天 2 次 vs 前 7 天 2 次
    assert hw["forgot_30d"] == 1  # 忘带不计纯缺交、独立计数
    assert hw["negative_notes_30d"] == 1  # 「马虎」负面评价
    assert hw["missing_by_subject"] == {"数学": 3, "语文": 2}

    att = body["indicators"]["teacher_attention"]
    assert att["last_contact"] == {"kind": "谈话", "days_ago": 12}  # 观察不算接触、家访更早
    assert att["open_follow_ups"] == 1
    assert att["done_follow_ups_30d"] == 1
    assert body["data_quality"]["notes"] == []


def test_homework_trend_deteriorates(client, p1b1_seed):
    """乙：近 7 天 3 次缺交 > 前 7 天 1 次 → 恶化（m4 已交打断在 d29）。"""
    s = p1b1_seed.seed
    resp = _get(client, "/homeroom/diagnosis/features", person_id=s.yi_h_id,
                academic_year_id=s.ay_id)
    assert resp.status_code == 200, resp.text
    hw = resp.json()["indicators"]["homework_behavior"]
    assert hw["missing_7d"] == 3
    assert hw["missing_30d"] == 4
    assert hw["current_streak_days"] == 3
    assert hw["trend"] == "恶化"
    assert hw["missing_by_subject"] == {"数学": 3, "语文": 1}


# ────────────── 缺考边界 ──────────────


def test_absent_exam_missing_reasons(client, p1b1_seed):
    """丙：期末主三门缺考（score=NULL）→ rank/percentile null + missing_reason，
    绝不转 0、不残留期中值；趋势只计有效名次场次。"""
    s = p1b1_seed.seed
    resp = _get(client, "/homeroom/diagnosis/features", person_id=s.bing_h_id,
                academic_year_id=s.ay_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    cl = body["indicators"]["current_level"]
    assert cl["status"] == "ok"
    assert cl["exam_name"] == EXAM_FINAL  # 缺考行存在 → 仍是有数据考试
    main3 = cl["main3"]
    assert main3["rank"] is None
    assert main3["percentile"] is None
    assert main3["basis"] is None
    assert main3["missing_reason"] == "main3_absent"  # 2026-09-29：缺考行专属 reason（原 rank_missing）
    subjects = _by_subject(cl["subjects"])
    assert subjects["语文"] == {"subject": "语文", "percentile": None, "grade_score": None}
    assert cl["bands"] == {"high_score": False, "critical": False, "weak": False}

    trend = body["indicators"]["trend"]
    assert trend["status"] == "ok"
    assert trend["direction_recent"] == "数据不足"
    assert trend["long_term"] == "数据不足"
    assert trend["last_change"] is None
    assert trend["streak"] == {"kind": None, "count": 0}
    assert trend["valid_exam_count"] == 1  # 期末缺考不计入名次序列

    stability = body["indicators"]["stability"]
    assert stability["status"] == "not_computable"
    assert stability["missing_reason"] == "percentile_missing"
    assert stability["value"] is None
    assert stability["label"] == "数据不足"

    imbalance = body["indicators"]["imbalance"]
    assert imbalance["status"] == "not_computable"
    assert imbalance["missing_reason"] == "main3_absent"
    assert imbalance["subjects"] == []
    assert imbalance["severe"] == []

    assert body["data_quality"]["valid_exam_count"] == 2  # 两场都有行（含缺考行）
    assert any("名次缺失" in note for note in body["data_quality"]["notes"])


# ────────────── 稀疏历史 + 偏科连续场数边界 ──────────────


def test_sparse_history_single_exam(client, p1b1_seed):
    """乙：仅期中一场 → 趋势/稳定性「数据不足」；临界段成立；
    语文偏科 diff 23 个百分点但仅连续 1 场（<2）→ 不进 severe。"""
    s = p1b1_seed.seed
    resp = _get(client, "/homeroom/diagnosis/features", person_id=s.yi_h_id,
                academic_year_id=s.ay_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    cl = body["indicators"]["current_level"]
    assert cl["status"] == "ok"
    assert cl["exam_name"] == EXAM_MID
    assert cl["main3"]["rank"] == 420
    assert cl["main3"]["percentile"] == 0.62
    assert cl["bands"] == {"high_score": False, "critical": True, "weak": False}

    trend = body["indicators"]["trend"]
    assert trend["status"] == "ok"
    assert trend["direction_recent"] == "数据不足"
    assert trend["long_term"] == "数据不足"
    assert trend["last_change"] is None
    assert trend["valid_exam_count"] == 1

    stability = body["indicators"]["stability"]
    assert stability["status"] == "ok"
    assert stability["value"] is None
    assert stability["label"] == "数据不足"

    imbalance = body["indicators"]["imbalance"]
    rows = _by_subject(imbalance["subjects"])
    assert rows["语文"]["diff_pct_point"] == 23.0
    assert rows["语文"]["consecutive_exams"] == 1
    # 英语无百分位 → 不判不残留
    assert "英语" not in rows
    assert imbalance["severe"] == []


def test_identical_scores_same_features(client, p1b1_seed):
    """同分：辛与乙成绩/名次/百分位/作业状态完全一致 → 六类指标逐字段一致。"""
    s = p1b1_seed.seed
    a = _get(client, "/homeroom/diagnosis/features", person_id=s.yi_h_id,
             academic_year_id=s.ay_id).json()
    b = _get(client, "/homeroom/diagnosis/features", person_id=p1b1_seed.xin_h_id,
             academic_year_id=s.ay_id).json()
    assert b["person_id"] == p1b1_seed.xin_h_id
    a.pop("person_id")
    b.pop("person_id")
    assert a == b


# ────────────── 范围隔离（教学域不读全科） ──────────────


def test_teaching_domain_scope_isolation(client, p1b1_seed):
    """甲·T：subjects 仅物理（H 域同学科事实经 F09 反向投影进入，仅任教学科）；
    无总分行 → 总体类指标 not_computable、绝不跨域借读总分；
    作业/档案只读本域（班主任域数学语文缺交与谈话档案不出现）。"""
    s = p1b1_seed.seed
    resp = _get(client, "/teaching/diagnosis/features", person_id=s.jia_t_id,
                academic_year_id=s.ay_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    cl = body["indicators"]["current_level"]
    assert cl["status"] == "ok"
    # 期末：H 域甲的物理行（92 分）经 link 投影进入（share_categories 默认含
    # current_subject_score，且甲乙是已确认 linked 学生）——仍是仅物理口径
    assert cl["exam_name"] == EXAM_FINAL
    assert cl["as_of"] == "2026-01-15"
    assert _by_subject(cl["subjects"]) == {
        "物理": {"subject": "物理", "percentile": 0.08, "grade_score": None}
    }
    assert cl["main3"] == {
        "rank": None, "percentile": None, "basis": None, "missing_reason": "no_main3_row",
    }
    assert cl["bands"] == {"high_score": False, "critical": False, "weak": False}

    for name in ("trend", "stability", "imbalance"):
        indicator = body["indicators"][name]
        assert indicator["status"] == "not_computable"
        assert indicator["missing_reason"] == "no_main3_row"

    hw = body["indicators"]["homework_behavior"]
    assert hw["missing_7d"] == 2
    assert hw["missing_30d"] == 2  # 班主任域的数学/语文缺交绝不出现（无作业类共享）
    assert hw["current_streak_days"] == 2
    assert hw["trend"] == "恶化"
    assert hw["missing_by_subject"] == {"物理": 2}
    assert hw["forgot_30d"] == 0
    assert hw["negative_notes_30d"] == 0

    att = body["indicators"]["teacher_attention"]
    assert att["last_contact"] == {"kind": "谈话", "days_ago": 3}  # H 域 d12 谈话不泄漏
    assert att["open_follow_ups"] == 1
    assert att["done_follow_ups_30d"] == 0

    assert body["data_quality"]["valid_exam_count"] == 2
    assert any("不跨域取数" in note for note in body["data_quality"]["notes"])


def test_teaching_no_batches_reports_no_data(client, p1b1_seed):
    """戊·T（T8 无任何作业批次）：计数全 0、trend=无数据、档案为空。"""
    s = p1b1_seed.seed
    resp = _get(client, "/teaching/diagnosis/features", person_id=s.wu_t_id,
                academic_year_id=s.ay_id)
    assert resp.status_code == 200, resp.text
    hw = resp.json()["indicators"]["homework_behavior"]
    assert hw["missing_7d"] == 0
    assert hw["missing_30d"] == 0
    assert hw["current_streak_days"] == 0
    assert hw["trend"] == "无数据"
    assert hw["missing_by_subject"] == {}
    att = resp.json()["indicators"]["teacher_attention"]
    assert att == {
        "status": "ok", "missing_reason": None,
        "last_contact": {"kind": None, "days_ago": None},
        "open_follow_ups": 0, "done_follow_ups_30d": 0,
    }


# ────────────── 越界与参数守卫 ──────────────


def test_scope_guards(client, p1b1_seed):
    s = p1b1_seed.seed
    # 教学域身份不在班主任名册
    r = _get(client, "/homeroom/diagnosis/features", person_id=s.ding_t_id,
             academic_year_id=s.ay_id)
    assert r.status_code == 404
    assert r.json()["error"] == "resource_out_of_scope"
    # 班主任域身份不在教学名册
    r = _get(client, "/teaching/diagnosis/features", person_id=s.jia_h_id,
             academic_year_id=s.ay_id)
    assert r.status_code == 404
    # 未知 person / 未知学年
    assert _get(client, "/homeroom/diagnosis/features", person_id=999999,
                academic_year_id=s.ay_id).status_code == 404
    assert _get(client, "/homeroom/diagnosis/features", person_id=s.jia_h_id,
                academic_year_id=999999).status_code == 404
    # person_id 必填
    assert client.get(f"{API}/homeroom/diagnosis/features").status_code == 422


def test_cross_year_isolation(client, p1b1_seed):
    """跨学年：AY2 事实与旧学年缺交绝不漏进 AY1 特征；显式 AY2 按旧学年取数；
    服务层学年错配 422。"""
    s = p1b1_seed.seed
    r1 = _get(client, "/homeroom/diagnosis/features", person_id=s.jia_h_id,
              academic_year_id=s.ay_id)
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["data_quality"]["valid_exam_count"] == 3  # 2024期末不计入
    assert body1["indicators"]["trend"]["valid_exam_count"] == 3
    assert body1["indicators"]["homework_behavior"]["missing_30d"] == 5  # 旧学年缺交不泄漏

    r2 = _get(client, "/homeroom/diagnosis/features", person_id=s.jia_h_id,
              academic_year_id=p1b1_seed.ay2_id)
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["academic_year_id"] == p1b1_seed.ay2_id
    cl2 = body2["indicators"]["current_level"]
    assert cl2["exam_name"] == EXAM_OLD
    assert cl2["main3"]["rank"] == 300
    assert body2["data_quality"]["valid_exam_count"] == 1
    # 旧学年作业按该学年口径可见（反向隔离：AY1 批次也不进 AY2）
    assert body2["indicators"]["homework_behavior"]["missing_30d"] == 1

    from app.core.errors import InvalidScopeParam
    from app.db.models import SessionLocal
    from app.core.context import resolve_workspace_context

    db = SessionLocal()
    try:
        ctx = resolve_workspace_context(db, 1, "homeroom", {"academic_year_id": s.ay_id})
        from app.diagnosis.features import student_features
        with pytest.raises(InvalidScopeParam):
            student_features(db, ctx, s.jia_h_id, p1b1_seed.ay2_id)
    finally:
        db.close()


def test_endpoint_matches_service_output(client, p1b1_seed):
    """B4 同源前提：HTTP 响应与 service 函数输出逐字段一致（homeroom/teaching）。
    作用域解析与端点同一路径（_homeroom_ctx/_teaching_ctx）。"""
    from app.api.students_mgmt import _homeroom_ctx, _teaching_ctx
    from app.db.models import SessionLocal
    from app.diagnosis.features import student_features

    s = p1b1_seed.seed
    db = SessionLocal()
    try:
        ctx = _homeroom_ctx(db, s.ay_id)
        expected = student_features(db, ctx, s.jia_h_id, s.ay_id)
        http = _get(client, "/homeroom/diagnosis/features", person_id=s.jia_h_id,
                    academic_year_id=s.ay_id).json()
        assert http == expected

        tctx = _teaching_ctx(db, s.ay_id)
        expected_t = student_features(db, tctx, s.jia_t_id, s.ay_id)
        http_t = _get(client, "/teaching/diagnosis/features", person_id=s.jia_t_id,
                      academic_year_id=s.ay_id).json()
        assert http_t == expected_t
    finally:
        db.close()


# ────────────── 班级特征汇总 ──────────────


@pytest.mark.parametrize("mode", ["homeroom", "teaching"])
def test_batched_class_features_match_single_student_endpoints(client, p1b1_seed, mode):
    """整班批量读取与单生读端点逐生一致，覆盖成绩、作业例外及共享投影。"""
    year_id = p1b1_seed.seed.ay_id
    body = _get(client, f"/{mode}/diagnosis/features/class", academic_year_id=year_id).json()
    for row in body["students"]:
        single = _get(client, f"/{mode}/diagnosis/features",
                      person_id=row["person_id"], academic_year_id=year_id)
        assert single.status_code == 200, single.text
        assert row["features"] == single.json()


def test_class_features_homeroom(client, p1b1_seed):
    """班级级汇总：每生 features + type_inputs（main_type 原始字段集合）+
    缺失统计（考试时点成员口径：缺行/缺考/缺主三门、零数据学生）。"""
    s = p1b1_seed.seed
    resp = _get(client, "/homeroom/diagnosis/features/class", academic_year_id=s.ay_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["calc_version"] == "p1-v1"
    assert body["scope_mode"] == "homeroom"
    assert body["student_count"] == 5
    assert [row["person_id"] for row in body["students"]] == sorted(
        [s.jia_h_id, s.yi_h_id, s.bing_h_id, p1b1_seed.xin_h_id, p1b1_seed.ren_h_id]
    )
    assert body["students"][0]["name"] == "秦甲"

    types = {row["person_id"]: row for row in body["class_summary"]["type_inputs"]}
    jia = types[s.jia_h_id]
    assert jia["valid_exam_count"] == 3
    assert jia["bands"] == {"high_score": False, "critical": False, "weak": False}
    assert jia["stability_label"] == "中等波动"
    assert jia["direction_recent"] == "进步"
    assert jia["streak_kind"] is None and jia["streak_count"] == 0
    assert jia["imbalance_severe"] == ["英语"]
    assert jia["homework_missing_30d"] == 5
    assert jia["homework_current_streak_days"] == 2
    yi = types[s.yi_h_id]
    assert yi["bands"]["critical"] is True
    assert yi["direction_recent"] == "数据不足"
    assert yi["imbalance_severe"] == []
    ren = types[p1b1_seed.ren_h_id]
    assert ren["valid_exam_count"] == 0

    # 每生 features 与单生端点同源
    jia_features = next(
        row["features"] for row in body["students"] if row["person_id"] == s.jia_h_id
    )
    single = _get(client, "/homeroom/diagnosis/features", person_id=s.jia_h_id,
                  academic_year_id=s.ay_id).json()
    assert jia_features == single

    stats = body["class_summary"]["missing_stats"]
    exams = {exam["exam_name"]: exam for exam in stats["exams"]}
    assert set(exams) == {EXAM_MID, EXAM_FINAL, EXAM_YIMO}
    final = exams[EXAM_FINAL]
    assert final["members_at"] == 5
    assert final["missing_by_subject"] == {"语文": 3, "数学": 4, "英语": 4, "物理": 4}
    assert final["absent_by_subject"] == {"语文": 1}  # 丙登记缺考（有行、score NULL）
    assert final["missing_main3"] == 3  # 乙/辛/壬无主三门行（丙缺考行存在仍算有行）
    mid = exams[EXAM_MID]
    assert mid["missing_by_subject"] == {"语文": 1, "数学": 1, "英语": 1, "物理": 1}
    assert mid["missing_main3"] == 1
    assert stats["students_without_exam_data"] == [p1b1_seed.ren_h_id]

    # 壬：零数据学生特征空态
    ren_features = next(
        row["features"] for row in body["students"] if row["person_id"] == p1b1_seed.ren_h_id
    )
    cl = ren_features["indicators"]["current_level"]
    assert cl["status"] == "not_computable"
    assert cl["missing_reason"] == "no_exam_data"
    assert cl["main3"]["missing_reason"] == "no_exam_data"
    assert cl["subjects"] == []
    for name in ("trend", "stability", "imbalance"):
        assert ren_features["indicators"][name]["status"] == "not_computable"
    assert ren_features["data_quality"]["valid_exam_count"] == 0


def test_class_features_teaching(client, p1b1_seed):
    """教学域班级汇总：T6+T8 并集、仅物理、无 missing_main3 键。
    期末 exam 来自 linked 学生 H 域物理行的 F09 反向投影（仅任教学科）。"""
    s = p1b1_seed.seed
    resp = _get(client, "/teaching/diagnosis/features/class", academic_year_id=s.ay_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope_mode"] == "teaching"
    assert body["student_count"] == 5
    assert {row["person_id"] for row in body["students"]} == {
        s.jia_t_id, s.yi_t_id, s.ding_t_id, s.wu_t_id, s.ji_t_id,
    }
    stats = body["class_summary"]["missing_stats"]
    exams = {exam["exam_name"]: exam for exam in stats["exams"]}
    assert set(exams) == {EXAM_MID, EXAM_FINAL}
    mid = exams[EXAM_MID]
    assert mid["members_at"] == 5
    assert mid["missing_by_subject"] == {"物理": 0}  # 丁缺考有行、不缺行
    assert "missing_main3" not in mid  # 教学域无总分行口径
    assert mid["absent_by_subject"] == {"物理": 1}  # 丁·T 登记缺考（score NULL）
    final = exams[EXAM_FINAL]
    assert final["missing_by_subject"] == {"物理": 4}  # 仅甲·T 经投影有 H 域物理行
    assert final["absent_by_subject"] == {}
    assert stats["students_without_exam_data"] == []
