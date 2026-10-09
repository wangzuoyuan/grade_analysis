"""P2-C4 轻量干预与自动复查（契约 docs/diagnosis-roadmap/p2-contracts.md §5）。

``GET /api/v1/{mode}/diagnosis/review-contrast?follow_up_id=`` 的服务实现；
干预本体是 ``ws_student_note`` 行（follow_up 扩展列，模型见
app/db/workspace_models.WsStudentNote），创建/关闭走既有 notes 路由的扩展
（app/api/students_mgmt.py），本模块只负责复查对照计算。

口径红线（契约 §0 / p1 §0 沿用）：
- 成绩取数一律走 ``app/api/_queries.readable_facts`` 统一可读事实口径；
  考试分组/排序复用 B1 ``features._group_exams/_ordered_exam_names``（同一
  数据管道，绝不另造第二套时间线），指标值解析复用共享定义
  ``definitions.resolve_year_rank / normalized_percentile`` 与
  ``definitions.metric_meta``（目标指标解析唯一口径）。
- 缺失纪律：缺考不转 0、不进可比集合；某场缺指标值 → 该场不可比并如实
  给出缺失原因；绝不残留上次值、绝不编造可比考试。
- 符号裁决（p1 §9）：变化值 change = 最新值 − 基线值。名次/年级前百分位
  两类指标均为「越小越好」，负值 = 相对位置上升；选考等级分为「越大越好」，
  正值 = 提升。方向含义随指标口径在 note 中解释。
- 复查结论纪律（契约 §5.2）：对照只陈述数据事实，**绝不自动标成功/失败**，
  响应不含任何成功/失败判定字段，note 显式声明不构成因果或提分保证。

复查触发（契约 §5.2，二者任一即返回对照）：
1. 到达 review_date（scope.as_of >= review_date）；
2. 该生在 start_date 之后（含当日，§0.7 窗口边界）有新「可比考试」——
   该场考试在本域可读且目标指标值可解析。

缺考/无可比考试 → ``{"status": "pending", "reason": ...}``，绝不硬凑对照。
"""

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.analysis import definitions as defs
from app.api import _queries as q
from app.core.context import WorkspaceContext
from app.core.errors import InvalidScopeParam
from app.db.workspace_models import WsStudentNote
from app.diagnosis.features import _group_exams, _ordered_exam_names

# 本波口径版本（p2-contracts.md 头部：本波版本号 p2-v1）
CALC_VERSION = "p2-v1"

# ── pending 原因词表（契约 §5.2：pending + reason） ──
PENDING_NO_TARGET_METRIC = "no_target_metric"      # 未设定目标指标
PENDING_NO_BASELINE = "no_baseline"                # 无基线值
PENDING_BASELINE_INCOMPARABLE = "baseline_incomparable"  # 基线缺失/口径与指标不符
PENDING_NO_COMPARABLE_EXAM = "no_comparable_exam"  # 锚点后无可比考试（含缺考）
PENDING_NOT_DUE_YET = "not_due_yet"                # 未到复查日且尚无新可比考试

# 状态词表（模型列同源）
STATUS_OPEN = "open"
STATUS_DONE = "done"
STATUS_DISMISSED = "dismissed"
INTERVENTION_STATUSES = (STATUS_OPEN, STATUS_DONE, STATUS_DISMISSED)

# 指标单位方向：较小值更优（负变化=改善） vs 较大值更优（正变化=改善）
_SMALLER_IS_BETTER_UNITS = ("rank", "percentile")

# 免责声明（铁律 5：绝不表述为因果或提分保证）
_NOTE_DISCLAIMER = "复查对照只陈述数据事实，不构成成功/失败判定，也不构成因果或提分保证。"


# ────────────────────────── 目标指标解析（唯一口径） ──────────────────────────


def metric_meta_or_422(db: Session, ctx: WorkspaceContext, metric: str) -> dict:
    """target_metric → definitions.metric_meta 元数据（解析唯一口径）。

    教学域 ctx.grade 可为 None（任教学科不钉年级），此时按
    active_grade（与 context 解析同源）回落；metric 不受支持 → 422
    invalid_scope_param（与 v1 分析端点旧路径转 400/新路径转 422 一致）。
    """
    grade = ctx.grade
    if grade is None:
        from app.rollover.service import get_active_grade

        grade = int(get_active_grade(db))
    try:
        return defs.metric_meta(int(grade), metric, "frequency")
    except ValueError as exc:
        raise InvalidScopeParam(
            "unsupported target_metric",
            details={"param": "target_metric", "target_metric": metric},
        ) from exc


def unit_of_metric(meta: dict) -> str:
    """指标种类 → 数值单位（决定 change 的方向含义与可比性校验）。

    total:{T} → 学籍/年级名次 rank；subject:{S} → 年级前百分位 percentile
    （top%，越小越靠前）；subject_grade:{S} → 选考等级分 grade_score。
    """
    kind = meta.get("kind", "")
    if kind == "total_rank":
        return "rank"
    if kind == "subject_grade_score":
        return "grade_score"
    return "percentile"


def _smaller_is_better(unit: str) -> bool:
    return unit in _SMALLER_IS_BETTER_UNITS


# ────────────────────────── 指标时间线（与 B1 同一取数管道） ──────────────────────────


def metric_points(
    db: Session, ctx: WorkspaceContext, person_id: int, metric: str
) -> List[Dict[str, Any]]:
    """该生该指标在本域学年内逐场的取值点列（B1 同一数据管道）。

    每场输出 {exam_name, exam_date, value, unit, missing_reason}：
    缺考/缺行的场 value=None 并给 missing_reason（绝不转 0、绝不编造）；
    exam_date 为 None（仅有月份精度/未导入日期）时该场不参与「锚点后」
    判定——无法证明在 start_date 之后，绝不当可比证据。
    """
    meta = metric_meta_or_422(db, ctx, metric)
    unit = unit_of_metric(meta)
    entries = q.readable_facts(db, ctx, member_ids=[person_id])
    exams = _group_exams(entries)
    ordered = _ordered_exam_names(exams)
    source, key = meta["source"], meta["key"]
    points: List[Dict[str, Any]] = []
    for name in ordered:
        bucket = exams[name]
        value, missing = _metric_value_of(bucket, source, key)
        points.append(
            {
                "exam_name": name,
                "exam_date": bucket["display_date"],
                "value": value,
                "unit": unit,
                "missing_reason": missing,
            }
        )
    return points


def _metric_value_of(bucket: dict, source: str, key: str) -> Tuple[Optional[float], Optional[str]]:
    """单场分组事实 → (指标值, 缺失原因)。共享定义解析，绝不另算。
    缺考纪律：score=NULL 的行（登记缺考）指标值一律不可得——行内残留的
    名次/百分位/等级分绝不冒成绩（reason="absent"）。"""
    if source == "total":
        fact = bucket["totals"].get(key)
        if fact is None:
            return None, "no_total_row"
        if fact.score is None:
            return None, "absent"
        rank = defs.resolve_year_rank(fact.xueji_rank, fact.grade_rank)
        if rank is None:
            return None, "rank_missing"
        return float(rank), None
    fact = bucket["subjects"].get(key)
    if fact is None:
        return None, "no_subject_row"
    if fact.score is None:
        return None, "absent"
    if source == "subject_grade":
        grade_score = getattr(fact, "grade_score", None)
        if grade_score is None:
            return None, "grade_score_missing"
        return float(grade_score), None
    percentile = defs.normalized_percentile(fact.grade_percentile)
    if percentile is None:
        return None, "percentile_missing"
    return float(percentile), None


def capture_baseline(
    db: Session,
    ctx: WorkspaceContext,
    person_id: int,
    metric: str,
    anchor: Optional[date] = None,
) -> Optional[dict]:
    """创建干预时自动捕获基线：最新一个可比点的值+口径。

    ``anchor``（干预锚点 = start_date，缺省回落档案日期）给出时，只取
    **锚点之前**的可比点（考试日 < 锚点，与复查侧「锚点之后才算新可比」
    严格互补）——补录过去开始的干预才不会把干预后的成绩当成基线；
    日期不可解析（月精度）的点无法证明在锚点之前，不取。
    无任何可比点（如缺考/未导成绩）→ None（创建不被数据缺失阻塞，
    复查对照将如实给出 pending/no_baseline）。
    """
    points = metric_points(db, ctx, person_id, metric)
    if anchor is None:
        comparable = [p for p in points if p["value"] is not None]
    else:
        comparable = [
            p
            for p in points
            if p["value"] is not None and _is_strictly_before(p, anchor)
        ]
    if not comparable:
        return None
    latest = comparable[-1]
    return {
        "metric": metric,
        "value": latest["value"],
        "unit": latest["unit"],
        "exam_name": latest["exam_name"],
        "exam_date": latest["exam_date"],
        "captured_as_of": ctx.as_of.isoformat(),
        "source": "auto",
    }


def _is_strictly_before(point: dict, anchor: date) -> bool:
    """该点考试日是否严格早于锚点（日期确切才可证明；月精度 → False）。"""
    exam_date = point.get("exam_date")
    if exam_date is None:
        return False
    try:
        return date.fromisoformat(exam_date) < anchor
    except (ValueError, TypeError):
        return False


# ────────────────────────── 复查对照主服务 ──────────────────────────


def review_contrast(
    db: Session, ctx: WorkspaceContext, note: WsStudentNote, as_of: Optional[date] = None
) -> dict:
    """复查对照主服务（契约 §5.2）。

    到达 review_date 或 start_date 之后有新可比考试 → status="ready" 并给
    {baseline, latest, change, note}；否则 status="pending" + reason。
    绝不自动标成功/失败。``note`` 须已通过域/作用域校验（路由层负责）。
    """
    as_of = as_of or ctx.as_of
    envelope: Dict[str, Any] = {
        "calc_version": CALC_VERSION,
        "follow_up_id": note.id,
        "person_id": note.person_id,
        "as_of": as_of.isoformat(),
        "review": {
            "start_date": note.start_date.isoformat() if note.start_date else None,
            "review_date": note.review_date.isoformat() if note.review_date else None,
            "due": bool(note.review_date is not None and as_of >= note.review_date),
        },
    }

    # 锚点：start_date 缺省回落档案日期（该干预记录的起点）
    anchor = note.start_date or note.date
    due = note.review_date is not None and as_of >= note.review_date

    if not (note.target_metric or "").strip():
        return _pending(
            envelope, PENDING_NO_TARGET_METRIC,
            "该干预未设定目标指标，无法生成对照；请在干预卡补充 target_metric。",
        )
    metric = note.target_metric.strip()
    envelope["metric"] = metric

    baseline = dict(note.baseline_value) if isinstance(note.baseline_value, dict) else None
    envelope["baseline_source"] = (baseline or {}).get("source") or (
        "teacher" if baseline else None
    )
    if not baseline:
        return _pending(
            envelope, PENDING_NO_BASELINE,
            "该干预没有基线值（创建时无可比成绩且未手填），无法生成对照。",
        )
    # 基线口径与目标指标必须一致：改过 target_metric 的旧基线（如语文百分位
    # 基线配数学目标）绝不跨指标硬算（PATCH 侧已自动重取，此处兜底旧数据）。
    baseline_metric = (baseline.get("metric") or "").strip()
    if baseline_metric and baseline_metric != metric:
        return _pending(
            envelope, PENDING_BASELINE_INCOMPARABLE,
            f"基线属 {baseline_metric} 口径，与当前目标指标 {metric} 不符"
            "（修改目标后会自动重取基线；也可手填覆盖），无法生成对照。",
        )

    meta = metric_meta_or_422(db, ctx, metric)  # 非法指标 → 422（路由层转契约错误）
    unit = unit_of_metric(meta)
    envelope["unit"] = unit

    base_value = _numeric_baseline_value(baseline, unit)
    if base_value is None:
        unit_hint = {
            "percentile": "年级前百分位以 0–1 小数存储（前 40% = 0.4，不是 40）",
            "rank": "名次为不小于 1 的整数",
            "grade_score": "等级分按原始分值存储",
        }.get(unit, f"期望 unit={unit}")
        return _pending(
            envelope, PENDING_BASELINE_INCOMPARABLE,
            f"基线值缺失、单位与目标指标不符或超出合理范围（{unit_hint}），无法生成对照。",
        )

    # 可比考试：锚点（含当日）之后、日期确切、指标值可解析的场
    points = metric_points(db, ctx, note.person_id, metric)
    comparable = [p for p in points if _is_comparable_after(p, anchor)]
    envelope["comparable_exams_n"] = len(comparable)
    if not comparable:
        if due:
            missed = [
                {"exam_name": p["exam_name"], "exam_date": p["exam_date"],
                 "missing_reason": p["missing_reason"]}
                for p in points
                if p["exam_date"] is not None and p["exam_date"] >= anchor.isoformat()
                and p["value"] is None
            ]
            detail = "复查日已到，但该生在开始日之后没有可比考试"
            if missed:
                detail += f"（最近相关场缺考/缺指标：{missed[-1]['exam_name']}）"
            return _pending(envelope, PENDING_NO_COMPARABLE_EXAM, detail + "。")
        return _pending(
            envelope, PENDING_NOT_DUE_YET,
            "未到计划复查日期，且开始日之后暂无新的可比考试。",
        )

    latest = comparable[-1]
    change_value = round(latest["value"] - base_value, 4)
    # ready 分支必有可比考试：review_date 已到 → 双触发；否则仅新可比考试
    # （review_date 已到但无可比考试在上面的 pending 分支处理）。
    envelope["review"]["trigger"] = "both" if due else "new_comparable_exam"

    envelope["status"] = "ready"
    envelope["baseline"] = {
        "value": base_value,
        "unit": unit,
        "exam_name": baseline.get("exam_name"),
        "exam_date": baseline.get("exam_date"),
    }
    envelope["latest"] = {
        "value": latest["value"],
        "unit": unit,
        "exam_name": latest["exam_name"],
        "exam_date": latest["exam_date"],
    }
    envelope["change"] = {
        "value": change_value,
        "smaller_is_better": _smaller_is_better(unit),
    }
    envelope["note"] = _contrast_note(unit)
    return envelope


def _numeric_baseline_value(baseline: dict, unit: str) -> Optional[float]:
    """基线 JSON → 数值；unit 缺省按指标自然单位，显式不一致 → None。

    单位范围校验：percentile 以 0–1 小数存储（前 40% = 0.4），rank 从 1
    起且为整数。越界或非整数（如手填 50、历史遗留 1.5）→ None（复查对照
    pending/baseline_incomparable，绝不按错误单位硬算 change）。"""
    raw_unit = baseline.get("unit")
    if raw_unit is not None and raw_unit != unit:
        return None
    value = baseline.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if unit == "percentile" and not 0.0 <= value <= 1.0:
        return None
    if unit == "rank" and (value < 1 or value != int(value)):
        return None
    return value


def _is_comparable_after(point: dict, anchor: date) -> bool:
    """锚点（含当日）之后的可比点：日期确切 + 指标值可解析。

    月份精度（"2025-09"）与任何不可解析日期都不构成可比证据（无法证明
    在锚点之后），返回 False 而不是崩溃——真实库以月精度为主。"""
    exam_date = point.get("exam_date")
    if exam_date is None:
        return False
    try:
        parsed = date.fromisoformat(exam_date)
    except (ValueError, TypeError):
        return False
    if parsed < anchor:
        return False
    return point.get("value") is not None


def _contrast_note(unit: str) -> str:
    if _smaller_is_better(unit):
        meaning = (
            "变化值 = 最新值 − 基线值；对名次/年级前百分位这类「越小越好」的指标，"
            "负值表示相对位置上升，正值表示相对位置下降。"
        )
    else:
        meaning = (
            "变化值 = 最新值 − 基线值；等级分这类「越大越好」的指标，"
            "正值表示数值升高，负值表示数值降低。"
        )
    return meaning + _NOTE_DISCLAIMER


def _pending(envelope: dict, reason: str, human: str) -> dict:
    envelope["status"] = "pending"
    envelope["reason"] = reason
    envelope["note"] = human + _NOTE_DISCLAIMER
    return envelope
