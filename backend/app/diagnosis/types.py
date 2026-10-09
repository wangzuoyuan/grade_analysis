"""P1-B2 可解释学生类型引擎（契约 docs/diagnosis-roadmap/p1-contracts.md §3）。

``classify_student(features)`` 是纯函数：输入 = B1 单生 features JSON
（契约 §2 形状，字段缺失按「缺输入」处理，绝不编造），输出 = 契约 §3 的
类型判定结果。判定只做规则组合，不复算任何共享口径——bands/stability/
imbalance/trend 的语义由 B1 特征层与 ``app.analysis.definitions`` 保证。

11 类型与命中顺序（契约 §3，首个命中为主类型，其余命中为次标签）：

    3/4/5（趋势类）→ 1/2（高位类）→ 6/7/8（临界类）→ 9 → 11 → 10

- 有效考试数 ``valid_exam_count < 2`` → ``insufficient_data``：主类型
  null、不强行归类（契约 §3「数据不足不强行归类」）。
- 作业风险型（规则 10）语义为「描述已观察到的作业异常，不预测成绩」：
  数据不足被门挡住时仍允许以次标签如实呈现（契约规则 10 括注「仅次标签
  或数据充分可主」）；数据充分且无其他类型命中时升为主类型。

本模块同时承载 B1 阈值常量的 **读取与占位**：契约 §4 规定阈值集中在
``app/diagnosis/thresholds.py``（B1 建、B2 import）。B1 未合并的分支上
该文件尚不存在，此处回退到与契约 §4 完全同名的默认值；B1 合并后
import 自动切换到 thresholds.py 的唯一定义，本文件无需改动。
"""

from typing import Any, Dict, List, Optional, Tuple

try:  # 契约 §4：阈值唯一来源是 app/diagnosis/thresholds.py（B1 建、B2 import）
    from app.diagnosis.thresholds import (  # type: ignore  # noqa: F401
        HOMEWORK_RISK_30D,
        HOMEWORK_RISK_STREAK_DAYS,
        IMBALANCE_MIN_CONSECUTIVE,
        STABILITY_MIN_POINTS,
        STABILITY_RANGE_LABELS,
        STABILITY_WINDOW_N,
        TREND_DIRECTION_MIN_CHANGE,
    )
except ModuleNotFoundError:  # B1 未合并的分支：占位值与契约 §4 默认值一致
    STABILITY_WINDOW_N = 5            # 稳定性窗口场次
    STABILITY_MIN_POINTS = 3
    STABILITY_RANGE_LABELS = ((0.10, "稳定"), (0.25, "中等波动"))  # 极差>0.25→高波动
    TREND_DIRECTION_MIN_CHANGE = 80   # 近三次净名次变化默认阈值
    HOMEWORK_RISK_30D = 3             # 30 天缺交次数阈值
    HOMEWORK_RISK_STREAK_DAYS = 2     # 当前连缺天数阈值
    IMBALANCE_MIN_CONSECUTIVE = 2     # 偏科连续场数

CALC_VERSION = "p1-v1"

# 规则 3/5 的连击下限直接写在契约 §3 规则文本里（≥2），不属 §4 阈值；
# 提成具名常量只为可读，数值冻结于契约。
STREAK_TYPE_MIN_COUNT = 2

# 类型名（契约 §3 规则标题；evidence.type 与 secondary_tags 使用同一词表）
TYPE_STABLE_EXCELLENT = "稳定优秀型"
TYPE_HIGH_VOLATILE = "高位波动型"
TYPE_CONTINUOUS_PROGRESS = "持续进步型"
TYPE_SHORT_TERM_DECLINE = "短期下滑型"
TYPE_CONTINUOUS_DECLINE = "持续下滑型"
TYPE_STABLE_CRITICAL = "稳定临界型"
TYPE_CRITICAL_RISING = "临界上升型"
TYPE_CRITICAL_DECLINE = "临界下滑型"
TYPE_IMBALANCE = "明显偏科型"
TYPE_HOMEWORK_RISK = "作业风险型"
TYPE_COMPOSITE_RISK = "综合风险型"

DIRECTION_PROGRESS = "进步"
DIRECTION_DECLINE = "退步"
DIRECTION_FLAT = "持平"
DIRECTION_INSUFFICIENT = "数据不足"

STABILITY_LABEL_STABLE = "稳定"
STABILITY_LABEL_HIGH_VOLATILE = "高波动"


def _indicators(features: Optional[dict]) -> dict:
    return (features or {}).get("indicators") or {}


def _count_of(*candidates: Any) -> Optional[int]:
    """取第一个可解析为非负整数的候选值；缺失/非法 → None（不编造）。"""
    for value in candidates:
        if value is None:
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number >= 0:
            return number
    return None


def _valid_exam_count(features: dict) -> Optional[int]:
    """有效考试数：优先 data_quality.valid_exam_count，回退 trend 同名字段。"""
    trend = _indicators(features).get("trend") or {}
    data_quality = (features or {}).get("data_quality") or {}
    return _count_of(data_quality.get("valid_exam_count"), trend.get("valid_exam_count"))


def _severe_with_consecutive(imbalance: dict) -> List[str]:
    """规则 9 的严重偏科集合：``imbalance.severe`` 非空且该科连续场数
    ≥ IMBALANCE_MIN_CONSECUTIVE（契约 §3 规则 9「连续 ≥2 场」）。

    consecutive_exams 由 B1 的 ``imbalance.subjects[].consecutive_exams``
    提供；该信息缺失时信任 severe 本身（不因 B1 输出形状差异而误杀）。"""
    severe = imbalance.get("severe") or []
    consecutive = {
        (entry or {}).get("subject"): (entry or {}).get("consecutive_exams")
        for entry in (imbalance.get("subjects") or [])
        if isinstance(entry, dict) and (entry or {}).get("subject") is not None
    }
    effective: List[str] = []
    for subject in severe:
        count = consecutive.get(subject)
        if count is None or _count_of(count) is None or _count_of(count) >= IMBALANCE_MIN_CONSECUTIVE:
            if subject not in effective:
                effective.append(subject)
    return effective


def _stability_value(stability: dict) -> Optional[float]:
    value = stability.get("value")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _homework_risk(homework: dict) -> Tuple[bool, List[str]]:
    """规则 10：作业风险（缺考不转 0 的同源纪律——字段缺失不判，不按 0）。"""
    parts: List[str] = []
    hit = False
    missing_30d = _count_of(homework.get("missing_30d"))
    streak_days = _count_of(homework.get("current_streak_days"))
    if missing_30d is not None and missing_30d >= HOMEWORK_RISK_30D:
        hit = True
        parts.append(f"近 30 天缺交 {missing_30d} 次（阈值 ≥{HOMEWORK_RISK_30D} 次）")
    if streak_days is not None and streak_days >= HOMEWORK_RISK_STREAK_DAYS:
        hit = True
        parts.append(f"当前连缺 {streak_days} 天（阈值 ≥{HOMEWORK_RISK_STREAK_DAYS} 天）")
    return hit, parts


def classify_student(features: dict) -> dict:
    """按契约 §3 规则判定学生类型（纯函数，输入 = B1 单生 features JSON）。

    返回：{person_id, calc_version, main_type, secondary_tags, evidence,
    classification_status}。每条 evidence 均以中文人读短句引用具体 B1
    输出值（次数/天数/科目/方向等，契约 §3「规则可解释」红线——解释力
    来自数值与阈值，不依赖英文字段路径）。"""
    indicators = _indicators(features)
    current_level = indicators.get("current_level") or {}
    bands = current_level.get("bands") or {}
    trend = indicators.get("trend") or {}
    stability = indicators.get("stability") or {}
    imbalance = indicators.get("imbalance") or {}
    homework = indicators.get("homework_behavior") or {}

    band_high = bool(bands.get("high_score"))
    band_critical = bool(bands.get("critical"))
    band_weak = bool(bands.get("weak"))
    direction = trend.get("direction_recent") or DIRECTION_INSUFFICIENT
    streak = trend.get("streak") or {}
    streak_kind = streak.get("kind")
    streak_count = _count_of(streak.get("count")) or 0
    stability_label = stability.get("label")
    stability_value = _stability_value(stability)
    severe = _severe_with_consecutive(imbalance)
    homework_hit, homework_parts = _homework_risk(homework)
    person_id = (features or {}).get("person_id")

    def evidence_of(type_name: str, basis: str) -> dict:
        return {"type": type_name, "basis": basis}

    def rule_stable_excellent() -> Optional[dict]:
        # 规则 1：高分段 + 稳定 + 无严重偏科 + 近 3 次方向≠退步
        if not (band_high and stability_label == STABILITY_LABEL_STABLE):
            return None
        if severe or direction == DIRECTION_DECLINE:
            return None
        return evidence_of(
            TYPE_STABLE_EXCELLENT,
            "位于高分段 且 成绩稳定"
            + (f"（主三门百分位极差 {stability_value}）" if stability_value is not None else "")
            + "，无严重偏科"
            + f"，最近方向为{direction}（非退步）",
        )

    def rule_high_volatile() -> Optional[dict]:
        # 规则 2：高分段 + 高波动
        if not (band_high and stability_label == STABILITY_LABEL_HIGH_VOLATILE):
            return None
        return evidence_of(
            TYPE_HIGH_VOLATILE,
            "位于高分段 且 高波动"
            + (f"（主三门百分位极差 {stability_value}）" if stability_value is not None else ""),
        )

    def rule_continuous_progress() -> Optional[dict]:
        # 规则 3：连续进步 ≥2 次
        if not (streak_kind == DIRECTION_PROGRESS and streak_count >= STREAK_TYPE_MIN_COUNT):
            return None
        last_change = trend.get("last_change") or {}
        suffix = ""
        rank_change = last_change.get("rank_change")
        if rank_change is not None:
            word = "上升" if rank_change < 0 else ("下降" if rank_change > 0 else "持平")
            suffix = (
                f"；最近一场主三门名次变化 {int(rank_change):+d} 名"
                f"（相对位置{word}）"
            )
        return evidence_of(
            TYPE_CONTINUOUS_PROGRESS,
            f"连续进步 {streak_count} 次（≥{STREAK_TYPE_MIN_COUNT}）" + suffix,
        )

    def rule_short_term_decline() -> Optional[dict]:
        # 规则 4：近方向退步且未成连击（短期）
        if not (direction == DIRECTION_DECLINE and streak_count < STREAK_TYPE_MIN_COUNT):
            return None
        return evidence_of(
            TYPE_SHORT_TERM_DECLINE,
            f"最近方向退步，连续退步 {streak_count} 次（<{STREAK_TYPE_MIN_COUNT}，未成持续退步）",
        )

    def rule_continuous_decline() -> Optional[dict]:
        # 规则 5：连续退步 ≥2 次
        if not (streak_kind == DIRECTION_DECLINE and streak_count >= STREAK_TYPE_MIN_COUNT):
            return None
        return evidence_of(
            TYPE_CONTINUOUS_DECLINE,
            f"连续退步 {streak_count} 次（≥{STREAK_TYPE_MIN_COUNT}）",
        )

    def rule_stable_critical() -> Optional[dict]:
        # 规则 6：临界段 + 稳定 + 方向持平/数据不足
        if not (band_critical and stability_label == STABILITY_LABEL_STABLE):
            return None
        if direction not in (DIRECTION_FLAT, DIRECTION_INSUFFICIENT):
            return None
        return evidence_of(
            TYPE_STABLE_CRITICAL,
            f"位于临界段 且 成绩稳定，最近方向为{direction}",
        )

    def rule_critical_rising() -> Optional[dict]:
        # 规则 7：临界段 + 方向进步
        if not (band_critical and direction == DIRECTION_PROGRESS):
            return None
        return evidence_of(
            TYPE_CRITICAL_RISING,
            "位于临界段 且 最近方向进步",
        )

    def rule_critical_decline() -> Optional[dict]:
        # 规则 8：临界段 + 方向退步
        if not (band_critical and direction == DIRECTION_DECLINE):
            return None
        return evidence_of(
            TYPE_CRITICAL_DECLINE,
            "位于临界段 且 最近方向退步",
        )

    def rule_imbalance() -> Optional[dict]:
        # 规则 9：严重偏科非空（连续 ≥IMBALANCE_MIN_CONSECUTIVE 场）
        if not severe:
            return None
        return evidence_of(
            TYPE_IMBALANCE,
            f"严重偏科：{'、'.join(severe)}（连续 ≥{IMBALANCE_MIN_CONSECUTIVE} 场弱于总体）",
        )

    def risk_faces() -> List[str]:
        """规则 11 的风险面：{规则 5, 规则 8} ∪ bands.weak ∪ severe ∪ 作业风险。"""
        faces: List[str] = []
        if streak_kind == DIRECTION_DECLINE and streak_count >= STREAK_TYPE_MIN_COUNT:
            faces.append(f"持续下滑（连续退步 {streak_count} 次）")
        if band_critical and direction == DIRECTION_DECLINE:
            faces.append("临界下滑（临界段且最近方向退步）")
        if band_weak:
            faces.append("薄弱段（位于年级薄弱段）")
        if severe:
            faces.append(f"严重偏科（{'、'.join(severe)}）")
        if homework_hit:
            faces.append("作业风险（近期作业异常）")
        return faces

    def rule_composite_risk() -> Optional[dict]:
        # 规则 11：命中 ≥2 个风险面
        faces = risk_faces()
        if len(faces) < 2:
            return None
        return evidence_of(
            TYPE_COMPOSITE_RISK,
            f"命中 {len(faces)} 个风险面（≥2）：{'；'.join(faces)}",
        )

    def rule_homework_risk() -> Optional[dict]:
        # 规则 10：作业风险——描述已观察到的作业异常，不预测成绩
        if not homework_hit:
            return None
        return evidence_of(
            TYPE_HOMEWORK_RISK,
            "；".join(homework_parts) if homework_parts else "近 30 天缺交或当前连缺达到阈值",
        )

    # 契约 §3 命中顺序：3/4/5（趋势类）→ 1/2（高位类）→ 6/7/8（临界类）
    # → 9 → 11 → 10
    ordered_rules = (
        (TYPE_CONTINUOUS_PROGRESS, rule_continuous_progress),
        (TYPE_SHORT_TERM_DECLINE, rule_short_term_decline),
        (TYPE_CONTINUOUS_DECLINE, rule_continuous_decline),
        (TYPE_STABLE_EXCELLENT, rule_stable_excellent),
        (TYPE_HIGH_VOLATILE, rule_high_volatile),
        (TYPE_STABLE_CRITICAL, rule_stable_critical),
        (TYPE_CRITICAL_RISING, rule_critical_rising),
        (TYPE_CRITICAL_DECLINE, rule_critical_decline),
        (TYPE_IMBALANCE, rule_imbalance),
        (TYPE_COMPOSITE_RISK, rule_composite_risk),
        (TYPE_HOMEWORK_RISK, rule_homework_risk),
    )

    valid_exam_count = _valid_exam_count(features)
    insufficient = valid_exam_count is None or valid_exam_count < 2

    if insufficient:
        # 契约 §3 门：valid_exam_count<2 → insufficient_data，主类型 null
        # 不强行归类；作业风险（规则 10，描述已观察到的作业异常、不依赖
        # 考试数）仍以次标签如实呈现（契约规则 10「仅次标签或数据充分可主」）
        homework_item = rule_homework_risk()
        evidence: List[dict] = []
        if homework_item is not None:
            evidence.append(homework_item)
        evidence.append(
            evidence_of(
                "数据不足",
                f"有效考试 "
                f"{valid_exam_count if valid_exam_count is not None else '未知'} 场"
                f"（不足 2 场），数据不足不强行归类",
            )
        )
        return {
            "person_id": person_id,
            "calc_version": CALC_VERSION,
            "main_type": None,
            "secondary_tags": [TYPE_HOMEWORK_RISK] if homework_item is not None else [],
            "evidence": evidence,
            "classification_status": "insufficient_data",
        }

    hits: List[dict] = []
    for _type_name, rule in ordered_rules:
        item = rule()
        if item is not None:
            hits.append(item)

    return {
        "person_id": person_id,
        "calc_version": CALC_VERSION,
        "main_type": hits[0]["type"] if hits else None,
        "secondary_tags": [item["type"] for item in hits[1:]],
        "evidence": hits,
        "classification_status": "classified",
    }
