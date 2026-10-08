"""P1-B2 可解释学生类型引擎测试（契约 docs/diagnosis-roadmap/p1-contracts.md §3/§4）。

三层覆盖：
1. ``classify_student`` 纯函数单测——输入直接构造契约 §2 形状的 features
   dict（B1 未合并，本分支无 features.py；dict 夹具以契约 §2 JSON 为准）。
   覆盖 11 类型命中顺序、阈值边界、insufficient_data 门、缺失字段纪律、
   evidence 可解释（引用具体字段值）。
2. 阈值常量与契约 §4 同名同值（B1 合并前走 types.py 内的同构占位，
   合并后自动切换为 app/diagnosis/thresholds.py，本断言两种形态都有效）。
3. 端到端——ORM 直种合成数据走本任务的两个 types 端点，覆盖：全部
   名次类类型的现算链路、缺考（缺名次场不残留上次值）、缺科（偏科连续
   断链）、百分位方向与单位（百分数形态归一 + diff 负值=更弱）、同分
   同判、跨学年隔离、范围隔离（教学域不读全科/总分）、稀疏历史
   （<2 场 → insufficient_data）。

样本全部为合成姓名（秦一～秦十、复用 v1_seed 的秦甲/秦甲·T），
不使用任何真实学生数据。
"""

import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from .conftest import SUBJECT

API = "/api/v1"

# 本模块自建的考试（日期均在 v1_seed 的 2025期中(2025-11-06) 之后，
# 保证「最近一场」落在自建考试上）
EXAM_M1 = "2026年3月月考"
EXAM_M2 = "2026年4月月考"
EXAM_M3 = "2026年5月月考"
EXAM_M4 = "2026年6月月考"
EXAM_DATES = {
    EXAM_M1: "2026-03-10", EXAM_M2: "2026-04-10",
    EXAM_M3: "2026-05-10", EXAM_M4: "2026-06-10",
}
TOTAL_MAIN3 = "主三门"


# ────────────────────────── 1. classify_student 纯函数 ──────────────────────────


def _features(**overrides):
    """构造契约 §2 形状的单生 features dict（数据充分的最小骨架）。"""
    base = {
        "person_id": 9,
        "calc_version": "p1-v1",
        "indicators": {
            "current_level": {
                "exam_name": "期末",
                "as_of": "2026-05-01",
                "main3": {"rank": 200, "percentile": 0.30, "basis": "school", "missing_reason": None},
                "subjects": [],
                "bands": {"high_score": False, "critical": False, "weak": False},
            },
            "trend": {
                "last_change": {"from": "期中", "to": "期末", "rank_change": 5},
                "direction_recent": "持平",
                "streak": {"kind": None, "count": 0},
                "long_term": "平稳",
                "valid_exam_count": 3,
            },
            "stability": {
                "window_n": 5, "statistic": "range",
                "value": 0.02, "label": "稳定", "min_points": 3,
            },
            "imbalance": {"subjects": [], "severe": []},
            "homework_behavior": {
                "missing_7d": 0, "missing_30d": 0,
                "current_streak_days": 0, "trend": "持平",
                "forgot_30d": 0, "negative_notes_30d": 0,
                "missing_by_subject": {},
            },
            "teacher_attention": {
                "last_contact": {"kind": None, "days_ago": None},
                "open_follow_ups": 0, "done_follow_ups_30d": 0,
            },
        },
        "data_quality": {"valid_exam_count": 3, "notes": []},
    }
    for path, value in overrides.items():
        target = base
        keys = path.split("__")
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    return base


def test_thresholds_match_contract_section4():
    """阈值常量与契约 §4 同名同值。

    B1 未合并时这些值来自 types.py 内的同构占位；合并后 import 自动切换
    为 app/diagnosis/thresholds.py 的唯一定义，本断言两种形态都成立。"""
    from app.diagnosis import types

    assert types.STABILITY_WINDOW_N == 5
    assert types.STABILITY_MIN_POINTS == 3
    assert types.STABILITY_RANGE_LABELS == ((0.10, "稳定"), (0.25, "中等波动"))
    assert types.TREND_DIRECTION_MIN_CHANGE == 80
    assert types.HOMEWORK_RISK_30D == 3
    assert types.HOMEWORK_RISK_STREAK_DAYS == 2
    assert types.IMBALANCE_MIN_CONSECUTIVE == 2
    assert types.CALC_VERSION == "p1-v1"


def test_rule_order_trend_beats_high_and_critical():
    """命中顺序：3/4/5（趋势）→ 1/2（高位）→ 6/7/8（临界）；首个命中为主。"""
    from app.diagnosis.types import classify_student

    # 同时满足 规则3（连续进步）与 规则1（稳定优秀）→ 主类型=持续进步型
    f = _features(
        **{
            "indicators__current_level__bands": {"high_score": True, "critical": False, "weak": False},
            "indicators__trend__direction_recent": "进步",
            "indicators__trend__streak": {"kind": "进步", "count": 2},
        }
    )
    result = classify_student(f)
    assert result["main_type"] == "持续进步型"
    assert result["secondary_tags"] == ["稳定优秀型"]
    assert result["classification_status"] == "classified"
    assert [item["type"] for item in result["evidence"]] == ["持续进步型", "稳定优秀型"]

    # 同时满足 规则5（连续退步）+ 规则8（临界下滑）+ 规则11（两个风险面）
    f = _features(
        **{
            "indicators__current_level__bands": {"high_score": False, "critical": True, "weak": False},
            "indicators__trend__direction_recent": "退步",
            "indicators__trend__streak": {"kind": "退步", "count": 3},
        }
    )
    result = classify_student(f)
    assert result["main_type"] == "持续下滑型"
    assert result["secondary_tags"] == ["临界下滑型", "综合风险型"]

    # 退步已成方向但连击=1：规则5 不命中，规则4（短期下滑）优先于临界类
    f = _features(
        **{
            "indicators__current_level__bands": {"high_score": False, "critical": True, "weak": False},
            "indicators__trend__direction_recent": "退步",
            "indicators__trend__streak": {"kind": "退步", "count": 1},
        }
    )
    result = classify_student(f)
    assert result["main_type"] == "短期下滑型"
    assert "临界下滑型" in result["secondary_tags"]


def test_stable_excellent_requires_no_severe_and_not_declining():
    from app.diagnosis.types import classify_student

    base = _features(
        **{
            "indicators__current_level__bands": {"high_score": True, "critical": False, "weak": False},
            "indicators__stability__label": "稳定",
        }
    )
    assert classify_student(base)["main_type"] == "稳定优秀型"

    # 近 3 次方向=退步 → 规则1 不命中（退步 + 连击 1 → 短期下滑）
    f = _features(
        **{
            "indicators__current_level__bands": {"high_score": True, "critical": False, "weak": False},
            "indicators__trend__direction_recent": "退步",
            "indicators__trend__streak": {"kind": "退步", "count": 1},
            "indicators__stability__label": "稳定",
        }
    )
    assert classify_student(f)["main_type"] == "短期下滑型"

    # 有严重偏科 → 规则1 让位于规则9
    f = _features(
        **{
            "indicators__current_level__bands": {"high_score": True, "critical": False, "weak": False},
            "indicators__imbalance__severe": ["英语"],
            "indicators__imbalance__subjects": [
                {"subject": "英语", "diff_pct_point": -25.0, "consecutive_exams": 2}
            ],
        }
    )
    result = classify_student(f)
    assert result["main_type"] == "明显偏科型"
    assert "稳定优秀型" not in result["secondary_tags"]


def test_high_volatile_and_critical_types():
    from app.diagnosis.types import classify_student

    f = _features(
        **{
            "indicators__current_level__bands": {"high_score": True, "critical": False, "weak": False},
            "indicators__stability__label": "高波动",
            "indicators__stability__value": 0.31,
        }
    )
    assert classify_student(f)["main_type"] == "高位波动型"

    # 规则6：临界 + 稳定 + 持平/数据不足（两种方向都命中）
    for direction in ("持平", "数据不足"):
        f = _features(
            **{
                "indicators__current_level__bands": {"high_score": False, "critical": True, "weak": False},
                "indicators__trend__direction_recent": direction,
                "indicators__stability__label": "稳定",
            }
        )
        result = classify_student(f)
        assert result["main_type"] == "稳定临界型", direction

    # 临界 + 稳定 + 进步 → 规则6 不命中，规则7 临界上升
    f = _features(
        **{
            "indicators__current_level__bands": {"high_score": False, "critical": True, "weak": False},
            "indicators__trend__direction_recent": "进步",
            "indicators__stability__label": "稳定",
        }
    )
    assert classify_student(f)["main_type"] == "临界上升型"

    # 临界 + 中等波动 + 数据不足方向 → 无类型命中 → classified 且 main=null
    f = _features(
        **{
            "indicators__current_level__bands": {"high_score": False, "critical": True, "weak": False},
            "indicators__trend__direction_recent": "数据不足",
            "indicators__stability__label": "中等波动",
        }
    )
    result = classify_student(f)
    assert result["main_type"] is None
    assert result["classification_status"] == "classified"


def test_imbalance_requires_min_consecutive():
    """规则 9：severe 非空且连续 ≥IMBALANCE_MIN_CONSECUTIVE 场。"""
    from app.diagnosis.types import classify_student

    f = _features(
        **{
            "indicators__imbalance__severe": ["英语"],
            "indicators__imbalance__subjects": [
                {"subject": "英语", "diff_pct_point": -22.0, "consecutive_exams": 2}
            ],
        }
    )
    result = classify_student(f)
    assert result["main_type"] == "明显偏科型"
    assert "连续" in result["evidence"][0]["basis"]

    # 连续仅 1 场 → 未达阈值，不判明显偏科
    f = _features(
        **{
            "indicators__imbalance__severe": ["英语"],
            "indicators__imbalance__subjects": [
                {"subject": "英语", "diff_pct_point": -22.0, "consecutive_exams": 1}
            ],
        }
    )
    assert classify_student(f)["main_type"] is None

    # B1 未带 consecutive_exams 信息时信任 severe 本身（不因输出形状误杀）
    f = _features(**{"indicators__imbalance__severe": ["英语"]})
    assert classify_student(f)["main_type"] == "明显偏科型"


def test_composite_risk_faces_counting():
    """规则 11：风险面 = {规则5, 规则8} ∪ bands.weak ∪ severe ∪ 作业风险。"""
    from app.diagnosis.types import classify_student

    # 薄弱段 + 作业风险（两个风险面，无其他类型命中）→ 综合风险为主
    f = _features(
        **{
            "indicators__current_level__bands": {"high_score": False, "critical": False, "weak": True},
            "indicators__homework_behavior__missing_30d": 3,
        }
    )
    result = classify_student(f)
    assert result["main_type"] == "综合风险型"
    assert result["secondary_tags"] == ["作业风险型"]

    # 单一风险面 → 不判综合风险
    f = _features(
        **{
            "indicators__current_level__bands": {"high_score": False, "critical": False, "weak": True},
        }
    )
    result = classify_student(f)
    assert "综合风险型" not in [result["main_type"], *result["secondary_tags"]]


def test_homework_risk_thresholds_and_main_eligibility():
    """规则 10：missing_30d≥3 或 current_streak_days≥2；数据充分且无其他
    类型命中时可为主类型；描述已观察到的作业异常，不预测成绩。"""
    from app.diagnosis.types import classify_student

    f = _features(**{"indicators__homework_behavior__missing_30d": 3})
    result = classify_student(f)
    assert result["main_type"] == "作业风险型"
    assert "缺交 3 次" in result["evidence"][0]["basis"]

    f = _features(**{"indicators__homework_behavior__current_streak_days": 2})
    assert classify_student(f)["main_type"] == "作业风险型"

    # 边界下不命中：missing_30d=2 且 streak=1
    f = _features(
        **{
            "indicators__homework_behavior__missing_30d": 2,
            "indicators__homework_behavior__current_streak_days": 1,
        }
    )
    assert classify_student(f)["main_type"] is None

    # 数据充分 + 其他类型命中 → 作业风险退为次标签
    f = _features(
        **{
            "indicators__trend__streak": {"kind": "进步", "count": 2},
            "indicators__trend__direction_recent": "进步",
            "indicators__homework_behavior__missing_30d": 5,
        }
    )
    result = classify_student(f)
    assert result["main_type"] == "持续进步型"
    assert result["secondary_tags"] == ["作业风险型"]

    # 字段缺失（None）不判：绝不按 0 之外编造，也不误报风险
    f = _features(
        **{
            "indicators__homework_behavior__missing_30d": None,
            "indicators__homework_behavior__current_streak_days": None,
        }
    )
    assert classify_student(f)["main_type"] is None


def test_insufficient_data_gate_keeps_homework_as_secondary():
    """valid_exam_count<2 → insufficient_data：主类型 null；作业风险仅次标签。"""
    from app.diagnosis.types import classify_student

    # 数据充分时本应判稳定优秀，但有效考试仅 1 场 → 门生效
    f = _features(
        **{
            "indicators__current_level__bands": {"high_score": True, "critical": False, "weak": False},
            "data_quality__valid_exam_count": 1,
            "indicators__trend__valid_exam_count": 1,
        }
    )
    result = classify_student(f)
    assert result["classification_status"] == "insufficient_data"
    assert result["main_type"] is None
    assert result["secondary_tags"] == []
    assert result["evidence"][-1]["type"] == "数据不足"
    assert "有效考试" in result["evidence"][-1]["basis"]

    # 门内作业风险仍以次标签如实呈现（规则 10「仅次标签或数据充分可主」）
    f = _features(
        **{
            "data_quality__valid_exam_count": 0,
            "indicators__homework_behavior__missing_30d": 4,
            "indicators__homework_behavior__current_streak_days": 2,
        }
    )
    result = classify_student(f)
    assert result["classification_status"] == "insufficient_data"
    assert result["main_type"] is None
    assert result["secondary_tags"] == ["作业风险型"]
    assert result["evidence"][0]["type"] == "作业风险型"

    # valid_exam_count 字段整体缺失 → 无法验证充分性 → insufficient_data
    f = _features()
    del f["data_quality"]["valid_exam_count"]
    del f["indicators"]["trend"]["valid_exam_count"]
    result = classify_student(f)
    assert result["classification_status"] == "insufficient_data"

    # 完全空 dict 也不抛异常，按数据不足处理
    result = classify_student({})
    assert result["classification_status"] == "insufficient_data"
    assert result["main_type"] is None


def test_no_type_hits_returns_classified_null_main():
    """中位稳定学生：无任何规则命中 → classified + main=null（不强行归类）。"""
    from app.diagnosis.types import classify_student

    result = classify_student(_features())
    assert result["classification_status"] == "classified"
    assert result["main_type"] is None
    assert result["secondary_tags"] == []
    assert result["evidence"] == []


def test_evidence_cites_concrete_field_values():
    """契约 §3「规则可解释」：每条 evidence 引用具体 B1 字段值。"""
    from app.diagnosis.types import classify_student

    f = _features(
        **{
            "person_id": 9,
            "indicators__trend__streak": {"kind": "进步", "count": 3},
            "indicators__trend__direction_recent": "进步",
            "indicators__trend__last_change": {"from": "期中", "to": "期末", "rank_change": -9},
            "indicators__homework_behavior__missing_30d": 5,
            "indicators__homework_behavior__current_streak_days": 3,
        }
    )
    result = classify_student(f)
    assert result["person_id"] == 9
    assert result["calc_version"] == "p1-v1"
    main_ev = result["evidence"][0]
    assert main_ev["type"] == "持续进步型"
    assert "连续进步 3 次" in main_ev["basis"] and "进步" in main_ev["basis"]
    assert "-9" in main_ev["basis"]  # 引用 last_change.rank_change 具体值
    hw_ev = result["evidence"][1]
    assert hw_ev["type"] == "作业风险型"
    assert "缺交 5 次" in hw_ev["basis"]
    assert "连缺 3 天" in hw_ev["basis"]
    for item in result["evidence"]:
        assert item["type"] and item["basis"]


# ────────────────────────── 3. 端到端（合成数据 + types 端点） ──────────────────────────


def _seed_homework(db, ay_id, *, domain, class_ref_id, subject, day, person_id):
    """直种一个作业批次 + 该生缺交例外行（expected_members 仅含该生）。"""
    from app.db.workspace_models import HomeworkAssignment, HomeworkSubmission

    assignment = HomeworkAssignment(
        data_domain=domain,
        class_ref_id=class_ref_id,
        academic_year_id=ay_id,
        subject=subject,
        homework_type="练习册",
        assigned_date=day,
        batch_token=f"b2-{domain}-{class_ref_id}-{subject}-{day.isoformat()}-{person_id}",
        expected_members_json=json.dumps([person_id]),
        status="active",
    )
    db.add(assignment)
    db.flush()
    db.add(
        HomeworkSubmission(
            assignment_id=assignment.id,
            person_id=person_id,
            submission_status="missing",
        )
    )


@pytest.fixture(scope="module")
def b2_seed(v1_seed):
    """在 v1_seed 之上补种 B2 类型场景（ORM 直种，全部合成姓名）。

    场景学生（homeroom 域 H6，主三门总分行带 grade_rank/grade_percentile；
    默认段位阈值：高分 1-80、临界 400-500、薄弱 501+）：
    - 秦一/秦七：名次 300→260→230→200（同分同判对）→ 持续进步型
    - 秦二：100→150→420 → 持续下滑型 + 临界下滑 + 综合风险次标签
    - 秦三：英语持续偏科（0.55 vs 0.30，3 场）→ 明显偏科型
    - 秦四：仅 1 场考试 + 近 3 天数学连缺 → insufficient_data + 作业风险次标签
    - 秦五：稳定优秀（名次 40、百分位用百分数形态 2.0/3.0/2.0 检验归一）
    - 秦六：高位波动（名次 30，百分位极差 0.28）
    - 秦八：稳定临界（450×3 持平）；秦九：临界上升（480→440）
    - 秦十：第 2 场总分缺考（score/rank/percentile 全 NULL）→ 短期下滑型
    - 跨学年：秦五在 2024-2025 学年的劣质百分位事实不得影响本学年判定
    - 跨域：秦甲（H 域 3 场临界稳定）↔ 秦甲·T（T 域物理 + T6 连缺）
    """
    from app.db import workspace_models as wm
    from app.db.models import DiagnosisThresholdConfig, SessionLocal

    s = v1_seed
    db = SessionLocal()
    # 既有合成样本按旧 20 名口径设计，保留为手动配置的回归验证。
    db.add(DiagnosisThresholdConfig(id=1, direction_rank_change=20, streak_rank_change=20))
    db.flush()

    ay2 = wm.AcademicYear(
        name="2024-2025", start_date=date(2024, 9, 1), end_date=date(2025, 7, 15)
    )
    db.add(ay2)
    db.flush()

    def enroll(ident, seat):
        db.add(
            wm.Enrollment(
                admin_class_id=s.h6_id,
                identity_id=ident.id,
                status="active",
                valid_from=date(2025, 9, 1),
                seat_no=seat,
            )
        )

    students = {}
    for idx, name in enumerate(
        ["秦一", "秦二", "秦三", "秦四", "秦五", "秦六", "秦七", "秦八", "秦九", "秦十"],
        start=1,
    ):
        ident = wm.WsStudentIdentity(data_domain="homeroom", display_name=name)
        db.add(ident)
        students[name] = ident
    db.flush()
    for idx, ident in enumerate(students.values(), start=1):
        enroll(ident, 10 + idx)

    def total_row(ident, exam, rank, percentile, score=500.0):
        """主三门总分行；缺考传 score=None 且 rank/percentile 均 None。"""
        db.add(
            wm.ScoreFact(
                data_domain="homeroom",
                academic_year_id=s.ay_id,
                exam_name=exam,
                exam_date=date.fromisoformat(EXAM_DATES[exam]),
                class_ref_id=s.h6_id,
                identity_id=ident.id,
                subject=None,
                total_type=TOTAL_MAIN3,
                score=score,
                xueji_rank=rank,
                grade_rank=rank,
                grade_percentile=percentile,
            )
        )

    def subject_row(ident, exam, subject, percentile, score=80.0):
        db.add(
            wm.ScoreFact(
                data_domain="homeroom",
                academic_year_id=s.ay_id,
                exam_name=exam,
                exam_date=date.fromisoformat(EXAM_DATES[exam]),
                class_ref_id=s.h6_id,
                identity_id=ident.id,
                subject=subject,
                total_type=None,
                score=score,
                grade_percentile=percentile,
            )
        )

    # 秦一/秦七：连续进步（每次变化 ≥20 名）；百分位稳定（极差 0.04）
    for name in ("秦一", "秦七"):
        for exam, rank, pct in (
            (EXAM_M1, 300, 0.50), (EXAM_M2, 260, 0.52),
            (EXAM_M3, 230, 0.48), (EXAM_M4, 200, 0.51),
        ):
            total_row(students[name], exam, rank, pct)

    # 跨学年隔离样本：秦五在旧学年的劣质百分位（若泄漏进本学年稳定性
    # 窗口，极差 0.88 → 高波动 → 主类型会变成高位波动型而非稳定优秀型）
    db.add(
        wm.ScoreFact(
            data_domain="homeroom",
            academic_year_id=ay2.id,
            exam_name="2024期末",
            exam_date=date(2025, 1, 10),
            class_ref_id=s.h6_id,  # 故意复用当期班 id：单独检验学年过滤
            identity_id=students["秦五"].id,
            subject=None,
            total_type=TOTAL_MAIN3,
            score=300.0,
            xueji_rank=50,
            grade_rank=50,
            grade_percentile=0.90,
        )
    )

    # 秦二：连续退步进临界段（420 落在默认临界段 400-500）
    for exam, rank, pct in ((EXAM_M1, 100, 0.10), (EXAM_M2, 150, 0.20), (EXAM_M3, 420, 0.60)):
        total_row(students["秦二"], exam, rank, pct)

    # 秦三：英语持续偏科（该科百分位 0.55 弱于主三门 0.30，连续 3 场）
    for exam in (EXAM_M1, EXAM_M2, EXAM_M3):
        total_row(students["秦三"], exam, 200, 0.30)
        subject_row(students["秦三"], exam, "英语", 0.55)

    # 秦四：仅 1 场考试（稀疏历史）+ 近 3 天数学连续缺交
    total_row(students["秦四"], EXAM_M4, 300, 0.50)
    today = date.today()
    for offset in (3, 2, 1):
        _seed_homework(
            db, s.ay_id, domain="homeroom", class_ref_id=s.h6_id, subject="数学",
            day=today - timedelta(days=offset),
            person_id=students["秦四"].id,
        )

    # 秦五：稳定优秀（名次 40 ≤ 高分段 80；百分位以百分数形态存储 2.0/3.0/2.0，
    # 归一后 0.02/0.03/0.02 → 极差 0.01 → 稳定；若单位处理错误则判高波动）
    for exam, rank, pct in ((EXAM_M1, 50, 2.0), (EXAM_M2, 45, 3.0), (EXAM_M3, 40, 2.0)):
        total_row(students["秦五"], exam, rank, pct)
        subject_row(students["秦五"], exam, "英语", 3.0)

    # 秦六：高位波动（名次 30 高分段；百分位极差 0.28 > 0.25 → 高波动）
    for exam, rank, pct in ((EXAM_M1, 30, 0.02), (EXAM_M2, 30, 0.30), (EXAM_M3, 30, 0.05)):
        total_row(students["秦六"], exam, rank, pct)

    # 秦八：临界稳定持平（450 ∈ 临界段 400-500）
    for exam, rank, pct in ((EXAM_M1, 450, 0.60), (EXAM_M2, 450, 0.61), (EXAM_M3, 450, 0.60)):
        total_row(students["秦八"], exam, rank, pct)

    # 秦九：临界上升（480→440 变化 40 ≥ 20；仅 2 场 → 稳定性数据不足）
    total_row(students["秦九"], EXAM_M2, 480, 0.70)
    total_row(students["秦九"], EXAM_M3, 440, 0.65)

    # 秦十：缺考样本——第 2 场总分缺考（score=NULL、名次/百分位 NULL，
    # 不转 0、不残留上次值）；名次 100→(缺考)→300 → 短期下滑
    total_row(students["秦十"], EXAM_M1, 100, 0.10)
    total_row(students["秦十"], EXAM_M2, None, None, score=None)
    total_row(students["秦十"], EXAM_M3, 300, 0.50)

    # 范围隔离样本：秦甲（v1_seed homeroom 身份）3 场临界稳定总分行——
    # 教学域端点（秦甲·T）绝不能据此判出临界类型
    for exam, rank, pct in ((EXAM_M1, 450, 0.60), (EXAM_M2, 450, 0.61), (EXAM_M3, 450, 0.60)):
        db.add(
            wm.ScoreFact(
                data_domain="homeroom",
                academic_year_id=s.ay_id,
                exam_name=exam,
                exam_date=date.fromisoformat(EXAM_DATES[exam]),
                class_ref_id=s.h6_id,
                identity_id=s.jia_h_id,
                subject=None,
                total_type=TOTAL_MAIN3,
                score=430.0,
                xueji_rank=rank,
                grade_rank=rank,
                grade_percentile=pct,
            )
        )

    # 教学域：秦甲·T 物理事实（2 场，凑足有效考试数）+ T6 近 3 天缺交
    for exam in (EXAM_M2, EXAM_M3):
        db.add(
            wm.ScoreFact(
                data_domain="teaching",
                academic_year_id=s.ay_id,
                exam_name=exam,
                exam_date=date.fromisoformat(EXAM_DATES[exam]),
                class_ref_id=s.t6_id,
                identity_id=s.jia_t_id,
                subject=SUBJECT,
                total_type=None,
                score=88.0,
            )
        )
    for offset in (3, 2, 1):
        _seed_homework(
            db, s.ay_id, domain="teaching", class_ref_id=s.t6_id, subject=SUBJECT,
            day=today - timedelta(days=offset),
            person_id=s.jia_t_id,
        )

    db.commit()
    yield SimpleNamespace(ay2_id=ay2.id, ids={name: ident.id for name, ident in students.items()})
    db.close()


def _types(client, mode, person_id, **params):
    resp = client.get(
        f"{API}/{mode}/diagnosis/types", params={"person_id": person_id, **params}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["calc_version"] == "p1-v1"
    assert body["person_id"] == person_id
    return body


def _homeroom(client, ids, name):
    return _types(client, "homeroom", ids[name])


@pytest.mark.usefixtures("b2_seed")
class TestHomeroomTypesEndpoint:
    def test_progress_type_and_same_score_determinism(self, client, v1_seed, b2_seed):
        """连续进步 → 持续进步型；同分同判：秦七与秦一同分同型。"""
        body = _homeroom(client, b2_seed.ids, "秦一")
        assert body["classification_status"] == "classified"
        assert body["main_type"] == "持续进步型"
        main_ev = body["evidence"][0]
        assert "连续进步 3 次" in main_ev["basis"]  # 300→260→230→200 三次 ≥20 名进步
        assert "-30" in main_ev["basis"]  # 230→200：本次−上次为负 = 相对位置上升
        assert "上升" in main_ev["basis"]
        assert _homeroom(client, b2_seed.ids, "秦七")["main_type"] == "持续进步型"

    def test_continuous_decline_with_critical_and_composite(self, client, v1_seed, b2_seed):
        body = _homeroom(client, b2_seed.ids, "秦二")
        assert body["main_type"] == "持续下滑型"
        assert body["secondary_tags"] == ["临界下滑型", "综合风险型"]

    def test_imbalance_type(self, client, v1_seed, b2_seed):
        """英语连续 3 场偏科（0.55 vs 0.30，差 0.25 ≥ 0.20）→ 明显偏科型。"""
        body = _homeroom(client, b2_seed.ids, "秦三")
        assert body["main_type"] == "明显偏科型"
        assert "英语" in body["evidence"][0]["basis"]
        assert "连续" in body["evidence"][0]["basis"]

    def test_insufficient_with_homework_risk_secondary(self, client, v1_seed, b2_seed):
        """稀疏历史（1 场）+ 连续缺交 → insufficient_data + 作业风险次标签。"""
        body = _homeroom(client, b2_seed.ids, "秦四")
        assert body["classification_status"] == "insufficient_data"
        assert body["main_type"] is None
        assert body["secondary_tags"] == ["作业风险型"]
        assert body["evidence"][0]["type"] == "作业风险型"
        assert "缺交 3 次" in body["evidence"][0]["basis"]
        assert body["evidence"][-1]["type"] == "数据不足"
        assert "有效考试" in body["evidence"][-1]["basis"]

    def test_high_band_and_critical_types(self, client, v1_seed, b2_seed):
        assert _homeroom(client, b2_seed.ids, "秦五")["main_type"] == "稳定优秀型"
        assert _homeroom(client, b2_seed.ids, "秦六")["main_type"] == "高位波动型"
        assert _homeroom(client, b2_seed.ids, "秦八")["main_type"] == "稳定临界型"
        assert _homeroom(client, b2_seed.ids, "秦九")["main_type"] == "临界上升型"

    def test_absent_exam_does_not_leave_stale_values(self, client, v1_seed, b2_seed):
        """缺考（总分 NULL）：不转 0、不残留名次/百分位——名次序列直接跨过
        缺考场（100→300 判短期下滑、连击=1），稳定性只剩 2 个有效点，
        绝不把缺考场凑成第 3 点。"""
        body = _homeroom(client, b2_seed.ids, "秦十")
        assert body["main_type"] == "短期下滑型"
        assert "连续退步 1 次" in body["evidence"][0]["basis"]

    def test_cross_year_facts_do_not_leak(self, client, v1_seed, b2_seed):
        """跨学年隔离：秦五旧学年（2024-2025）的劣质百分位若泄漏进本学年
        稳定性窗口（极差 0.88→高波动），主类型会错判为高位波动型。"""
        body = _homeroom(client, b2_seed.ids, "秦五")
        assert body["main_type"] == "稳定优秀型"
        assert all("波动" not in ev["type"] for ev in body["evidence"])

    def test_person_out_of_scope_404(self, client, v1_seed, b2_seed):
        """越界 person（不存在 / 教学域身份访问班主任端点）→ 404。"""
        resp = client.get(f"{API}/homeroom/diagnosis/types", params={"person_id": 999999})
        assert resp.status_code == 404
        resp = client.get(
            f"{API}/homeroom/diagnosis/types",
            params={"person_id": v1_seed.jia_t_id},  # 教学域身份不在本班名册
        )
        assert resp.status_code == 404


@pytest.mark.usefixtures("b2_seed")
class TestTeachingScopeIsolation:
    def test_teaching_domain_does_not_read_homeroom_totals(self, client, v1_seed, b2_seed):
        """教学域不读全科/总分：秦甲·T 的类型只能由教学域自身数据（物理
        事实 + T6 作业）判出作业风险，绝不能借秦甲在班主任域的临界段
        总分行判出临界类型（对照组：班主任域秦甲=稳定临界型）。"""
        homeroom_body = _types(client, "homeroom", v1_seed.jia_h_id)
        assert homeroom_body["main_type"] == "稳定临界型"

        teaching_body = _types(client, "teaching", v1_seed.jia_t_id)
        assert teaching_body["classification_status"] == "classified"
        # 教学域无总分行/名次 → 名次类类型全部不可判；作业风险来自 T6 批次
        assert teaching_body["main_type"] == "作业风险型"
        assert all(
            "临界" not in tag and "优秀" not in tag and "波动" not in tag
            for tag in [teaching_body["main_type"], *teaching_body["secondary_tags"]]
        )
        hw_ev = next(
            item for item in teaching_body["evidence"] if item["type"] == "作业风险型"
        )
        assert "缺交 3 次" in hw_ev["basis"]

    def test_teaching_out_of_scope_person_404(self, client, v1_seed, b2_seed):
        """非教学班成员访问教学端点 → 404（绝不跨域取数）。"""
        resp = client.get(
            f"{API}/teaching/diagnosis/types",
            params={"person_id": v1_seed.jia_h_id + 100000},
        )
        assert resp.status_code == 404


class TestB1WiredFeatureSemantics:
    """types 端点接线 B1.student_features 后的口径细节（主控合并时改写）。"""

    @pytest.mark.usefixtures("b2_seed")
    def test_percentile_units_and_diff_direction(self, v1_seed, b2_seed):
        """百分位单位（百分数形态归一为 0-1）与 diff 方向（正值=该科弱于
        主三门——路线图公式「单科百分位减个人总体百分位」）。"""
        from app.db.models import SessionLocal
        from app.diagnosis.features import student_features
        from app.diagnosis.types_router import _resolve_ctx

        s = v1_seed
        db = SessionLocal()
        try:
            ctx = _resolve_ctx(db, 1, "homeroom", s.ay_id, class_id=s.h6_id)
            # 秦五：百分数形态 2.0/3.0/2.0 → 归一后极差 0.01 → 稳定
            feats = student_features(db, ctx, b2_seed.ids["秦五"], ctx.academic_year_id)
            stability = feats["indicators"]["stability"]
            assert stability["value"] == 0.01
            assert stability["label"] == "稳定"
            # 秦三：英语偏科 diff = (0.55 − 0.30)×100 = +25（正值=更弱）
            feats3 = student_features(db, ctx, b2_seed.ids["秦三"], ctx.academic_year_id)
            imbalance = feats3["indicators"]["imbalance"]
            assert [item["subject"] for item in imbalance["subjects"]] == ["英语"]
            assert imbalance["subjects"][0]["diff_pct_point"] == 25.0
            assert imbalance["subjects"][0]["consecutive_exams"] >= 1
            assert imbalance["severe"] == ["英语"]
        finally:
            db.close()
