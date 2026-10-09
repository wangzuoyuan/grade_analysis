"""P2-C3 诊断版学生报告服务（契约 docs/diagnosis-roadmap/p2-contracts.md §4）。

服务签名::

    student_report(db, scope, person_id: int, academic_year_id: int,
                   exam_name: Optional[str] = None) -> dict

HTTP：``GET /api/v1/{homeroom|teaching}/diagnosis/report?person_id=&academic_year_id=&exam_name=<可选，缺省最近一场>``

口径红线（契约 §4 + P1 §0，违反即返工）：
- 计算同源：学习状态一律出自 B1 ``app.diagnosis.features.student_features``
  与 B2 ``app.diagnosis.types.classify_student``（六类指标与类型判定的唯一
  实现），本模块不另算第二口径；显式锚定非最近一场考试时，各科百分位与
  偏科仍复用同一批共享定义（``definitions.normalized_percentile`` /
  ``subject_weakness_subjects``）与 B1 的分组/连缺辅助函数。
- 三层分节标注：事实（fact）＝可直接读出的数据；规则判断（rule_judgment）
  ＝由共享口径与阈值规则生成；建议（suggestion）＝由证据生成的「值得关注
  事项」。每节携带 ``layer`` 字段，``layer_legend`` 给出图例。
- 建议纪律：1–3 条、每条以「值得关注：」开头、由 B2 evidence 与 B1 特征值
  生成并附 ``based_on``（可追溯）；绝不表述为因果或提分保证（措辞黑名单
  由测试把守）；``generated: true``；教师编辑仅在前端本地（``edit_scope:
  "frontend_local_only"``），后端不提供任何建议写入路径。
- 缺失纪律：缺考不转 0、不进分母、不残留；任何指标缺输入 → null/[] +
  ``missing_reason``（沿用 B1 词表），数据不足态原样透传。
- 双工作台范围与 P1 一致：homeroom=本班全科+总分；teaching=仅任教学科，
  无总分行时总体类指标如实 not_computable，绝不跨域取数。档案摘录遵守
  N01 域隔离（只读本域）+ ``_human_notes_filter``（排除系统辅助行）。
- 事实版报告（/homeroom/students/{id}/report、/student/{id}/report 的既有
  数据端点与页面）保留不动；本模块只新增诊断版。

``exam_name`` 语义：仅重新锚定 ``subject_performance``（各科百分位与偏科）；
学习状态（B1/B2 学年口径）与作业行为（7/30 天窗口，锚定 scope.as_of）不受
影响。显式考试在该生可读事实中不存在 → 422 invalid_scope_param。
"""

from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.analysis import definitions as defs
from app.api import _queries as q
from app.api.homework import DEFAULT_TOTAL_TYPE
from app.core.errors import InvalidScopeParam
from app.db.workspace_models import WsStudentNote
from app.diagnosis import thresholds as th
from app.diagnosis.features import (
    MAIN3_ABSENT,
    NO_EXAM_DATA,
    NO_MAIN3_PERCENTILE,
    NO_MAIN3_ROW,
    _ScopeShim,
    _consecutive_weak_exams,
    _group_exams,
    _ordered_exam_names,
    student_features,
)
from app.diagnosis.types import classify_student

# 本波口径版本（契约 p2 §0：本波版本号 p2-v1；B1/B2 数据自身仍携带 p1-v1）
CALC_VERSION = "p2-v1"

# 档案「观察/谈话」类摘录范围（契约 §4「档案中『观察/谈话』类摘录」）：
# notes/router.py CATEGORIES 中点名的两类；家访/家长沟通/奖惩等其他类别不进摘录。
OBSERVATION_CATEGORIES = ("谈话", "观察")
OBSERVATION_LIMIT = 10  # 摘录条数上限（超出 truncated=true，total 仍如实）

LAYER_LEGEND: Dict[str, str] = {
    "fact": "事实：来自可读事实的直接数据（各科百分位、作业窗口计数、档案摘录）；缺考/缺失如实标注，不转 0",
    "rule_judgment": "规则判断：由共享口径与阈值规则（app/analysis/definitions + diagnosis 阈值）生成，非直接事实",
    "suggestion": "建议：由证据生成的值得关注事项（generated=true），仅供参考；不构成因果结论或提分保证",
}

SUGGESTION_DISCLAIMER = (
    "以下建议由数据与规则自动生成（generated=true），仅供参考，"
    "不构成因果结论或提分保证；教师编辑仅保存在前端本地，不入库。"
)

SUGGESTION_EDIT_SCOPE = "frontend_local_only"


# ────────────────────────────── 服务入口 ──────────────────────────────


def student_report(
    db: Session,
    scope,
    person_id: int,
    academic_year_id: int,
    exam_name: Optional[str] = None,
) -> dict:
    """诊断版学生报告：四节 + 建议（契约 §4 的唯一实现）。"""
    if isinstance(scope, dict):
        scope = _ScopeShim(scope)
    # B1 唯一口径（含学年校验 422 与越界 person 404）；B2 唯一口径
    features = student_features(db, scope, person_id, academic_year_id)
    types = classify_student(features)

    anchor, exams, ordered, anchor_source = _resolve_anchor(
        db, scope, person_id, features, exam_name
    )

    return {
        "person_id": person_id,
        "name": q.names_for(db, [person_id]).get(person_id),
        "academic_year_id": scope.academic_year_id,
        "scope_mode": scope.mode,
        "data_domain": scope.data_domain,
        "calc_version": CALC_VERSION,
        "as_of": scope.as_of.isoformat(),
        "exam_name": anchor,
        "exam_name_source": anchor_source,
        "layer_legend": LAYER_LEGEND,
        "learning_state": _learning_state(features, types),
        "subject_performance": _subject_performance(features, exams, ordered, anchor),
        "behavior": _behavior(features),
        "teacher_observations": _teacher_observations(db, scope, person_id),
        "suggestions": _suggestions_section(features, types),
        "data_quality": features["data_quality"],
    }


# ────────────────────────────── 锚定考试 ──────────────────────────────


def _resolve_anchor(
    db: Session,
    scope,
    person_id: int,
    features: dict,
    exam_name: Optional[str],
) -> Tuple[Optional[str], Dict[str, dict], List[str], str]:
    """解析 subject_performance 的锚定考试（缺省=最近一场有数据考试）。

    返回 (anchor, exams, ordered, source)；exams/ordered 与 B1 特征层同源
    （readable_facts → _group_exams → _ordered_exam_names）。"""
    entries = q.readable_facts(db, scope, member_ids=[person_id])
    exams = _group_exams(entries)
    ordered = _ordered_exam_names(exams)
    if exam_name is None or exam_name == "":
        return (ordered[-1] if ordered else None), exams, ordered, "default_latest"
    if exam_name not in exams:
        raise InvalidScopeParam(
            "exam_name not found in the student's readable exams",
            details={"exam_name": exam_name, "available_exams": ordered},
        )
    return exam_name, exams, ordered, "explicit"


# ────────────────────────────── 节 1：学习状态（规则判断层） ──────────────────────────────


def _learning_state(features: dict, types: dict) -> dict:
    """类型 + 趋势 + 稳定性摘要：B2 判定与 B1 指标原样透传（同源唯一口径）。"""
    indicators = features["indicators"]
    current_level = indicators["current_level"]
    return {
        "layer": "rule_judgment",
        "summary_source": "B1 student_features + B2 classify_student（同源唯一口径）",
        "main_type": types["main_type"],
        "secondary_tags": types["secondary_tags"],
        "classification_status": types["classification_status"],
        "evidence": types["evidence"],
        "latest_exam_name": current_level.get("exam_name"),
        "latest_exam_as_of": current_level.get("as_of"),
        "main3": current_level.get("main3"),
        "bands": current_level.get("bands"),
        "trend": indicators["trend"],
        "stability": indicators["stability"],
    }


# ────────────────────────────── 节 2：各科表现（事实层 + 偏科规则标注） ──────────────────────────────


def _subject_performance(
    features: dict,
    exams: Dict[str, dict],
    ordered: List[str],
    anchor: Optional[str],
) -> dict:
    """各科百分位与偏科。

    锚定=最近一场时直接复用 B1 current_level.subjects 与 imbalance（逐字段
    同源）；锚定=更早一场时按同一批共享定义重建（normalized_percentile /
    subject_weakness_subjects / B1 连续偏科辅助），偏科基准=该场主三门百分位。"""
    latest = ordered[-1] if ordered else None
    if anchor is None:
        return {
            "layer": "fact",
            "exam_name": None,
            "subjects": [],
            "imbalance": _imbalance_payload("not_computable", NO_EXAM_DATA),
        }
    if anchor == latest:
        current_level = features["indicators"]["current_level"]
        return {
            "layer": "fact",
            "exam_name": anchor,
            "subjects": current_level["subjects"],
            "imbalance": _imbalance_payload(
                features["indicators"]["imbalance"]["status"],
                features["indicators"]["imbalance"]["missing_reason"],
                features["indicators"]["imbalance"]["subjects"],
                features["indicators"]["imbalance"]["severe"],
            ),
        }
    bucket = exams[anchor]
    subjects = []
    for subject, fact in sorted(bucket["subjects"].items()):
        if not subject:
            continue
        if fact.score is None:
            # 缺考科目：行保留、值一律 null（B1 同构，行内残留百分位/等级分绝不取）
            subjects.append({"subject": subject, "percentile": None, "grade_score": None})
            continue
        percentile = defs.normalized_percentile(fact.grade_percentile)
        subjects.append(
            {
                "subject": subject,
                "percentile": round(percentile, 4) if percentile is not None else None,
                "grade_score": getattr(fact, "grade_score", None),
            }
        )
    ordered_upto = ordered[: ordered.index(anchor) + 1]
    return {
        "layer": "fact",
        "exam_name": anchor,
        "subjects": subjects,
        "imbalance": _imbalance_for_anchor(exams, ordered_upto, bucket),
    }


def _imbalance_for_anchor(
    exams: Dict[str, dict], ordered_upto: List[str], bucket: dict
) -> dict:
    """锚定考试的偏科：与 B1 _imbalance_indicator 同一口径（共享定义 +
    连续场数从锚定场向前数，任一侧缺失即断）。"""
    main3_row = bucket["totals"].get(DEFAULT_TOTAL_TYPE)
    if main3_row is None:
        return _imbalance_payload("not_computable", NO_MAIN3_ROW)
    if main3_row.score is None:
        return _imbalance_payload("not_computable", MAIN3_ABSENT)
    base = defs.normalized_percentile(main3_row.grade_percentile)
    if base is None:
        return _imbalance_payload("not_computable", NO_MAIN3_PERCENTILE)
    subjects = []
    for subject, fact in sorted(bucket["subjects"].items()):
        if not subject or fact.score is None:
            continue  # 缺考/无百分位：该科本场不可比，不判不残留
        percentile = defs.normalized_percentile(fact.grade_percentile)
        if percentile is None:
            continue  # 缺考/无百分位：该科本场不可比，不判不残留
        consecutive = _consecutive_weak_exams(exams, ordered_upto, subject, percentile, base)
        subjects.append(
            {
                "subject": subject,
                "diff_pct_point": round((percentile - base) * 100, 2),
                "consecutive_exams": consecutive,
            }
        )
    severe = sorted(
        entry["subject"]
        for entry in subjects
        if entry["consecutive_exams"] >= th.IMBALANCE_MIN_CONSECUTIVE
    )
    return _imbalance_payload("ok", None, subjects, severe)


def _imbalance_payload(
    status: str,
    reason: Optional[str],
    subjects: Optional[list] = None,
    severe: Optional[list] = None,
) -> dict:
    """偏科子块（嵌套规则判断层标注：偏科=规则判断，各科百分位本身=事实）。"""
    return {
        "layer": "rule_judgment",
        "status": status,
        "missing_reason": reason,
        "subjects": subjects or [],
        "severe": severe or [],
    }


# ────────────────────────────── 节 3：作业行为（事实层） ──────────────────────────────


def _behavior(features: dict) -> dict:
    """作业行为窗口数据：B1 homework_behavior 原样透传 + 窗口说明。"""
    homework = features["indicators"]["homework_behavior"]
    return {
        "layer": "fact",
        "window_note": (
            f"近 {th.HOMEWORK_WINDOW_7D}/{th.HOMEWORK_WINDOW_30D} 天按自然日回溯、"
            "窗口边界含当日；例外登记（忘带/请假/出勤）不计缺交；"
            "无有效批次时只给计数特征"
        ),
        **homework,
    }


# ────────────────────────────── 节 4：教师观察摘录（事实层） ──────────────────────────────


def _teacher_observations(db: Session, scope, person_id: int) -> dict:
    """档案「观察/谈话」类摘录：N01 域隔离（只读本域）+ 排除系统辅助行。"""
    from app.api.students_mgmt import _human_notes_filter

    rows = (
        db.query(WsStudentNote)
        .filter(
            WsStudentNote.data_domain == scope.data_domain,
            WsStudentNote.person_id == person_id,
            WsStudentNote.category.in_(OBSERVATION_CATEGORIES),
            _human_notes_filter(),
        )
        .order_by(WsStudentNote.date.desc(), WsStudentNote.id.desc())
        .all()
    )
    items = [
        {
            "id": note.id,
            "date": note.date.isoformat(),
            "category": note.category,
            "content": note.content,
        }
        for note in rows[:OBSERVATION_LIMIT]
    ]
    return {
        "layer": "fact",
        "visible_domain": scope.data_domain,
        "categories": list(OBSERVATION_CATEGORIES),
        "limit": OBSERVATION_LIMIT,
        "total": len(rows),
        "truncated": len(rows) > OBSERVATION_LIMIT,
        "items": items,
    }


# ────────────────────────────── 节 5：建议（建议层，generated） ──────────────────────────────


def _item(index: int, text: str, based_on: List[str]) -> dict:
    return {"id": f"sug-{index}", "text": text, "based_on": based_on}


def _generate_suggestions(features: dict, types: dict) -> List[dict]:
    """由 B2 evidence 与 B1 特征值生成 1–3 条「值得关注事项」。

    触发器按优先级排列，截取前 3 条；全部未命中时给「保持观察」兜底条，
    保证 1–3 条。措辞纪律：每条以「值得关注：」开头，只描述已观察到的
    数据/规则命中并给观察性建议，绝不出现因果或提分保证表述（测试把守）。
    based_on：优先引用命中的 B2 evidence 类型名，否则引用 B1 字段路径
    （可追溯，不引入第二口径）。"""
    indicators = features["indicators"]
    trend = indicators.get("trend") or {}
    stability = indicators.get("stability") or {}
    imbalance = indicators.get("imbalance") or {}
    homework = indicators.get("homework_behavior") or {}
    bands = (indicators.get("current_level") or {}).get("bands") or {}
    evidence_types = [entry.get("type") for entry in (types.get("evidence") or [])]
    candidates: List[dict] = []

    def ref(evidence_name: str, field_path: str) -> List[str]:
        return [evidence_name] if evidence_name in evidence_types else [field_path]

    def _int(value) -> Optional[int]:
        if isinstance(value, bool) or value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    # 1) 趋势退步（方向=退步；对应 B2 持续/短期/临界下滑或综合风险）
    if trend.get("direction_recent") == "退步":
        last = trend.get("last_change") or {}
        detail = "最近方向为退步"
        rank_change = _int(last.get("rank_change"))
        if last.get("from") and last.get("to") and rank_change is not None:
            detail += (
                f"，最近一次 {last['from']}→{last['to']} 名次变化 {rank_change:+d} 名"
                "（负值=名次提升）"
            )
        evidence_name = next(
            (
                name
                for name in ("持续下滑型", "短期下滑型", "临界下滑型", "综合风险型")
                if name in evidence_types
            ),
            None,
        )
        candidates.append(
            (
                f"值得关注：最近几场考试主三门名次呈退步方向（{detail}）；"
                "建议结合课堂表现与作业情况了解具体情况",
                [evidence_name] if evidence_name else ["最近方向为退步"],
            )
        )

    # 2) 作业风险（缺交计数/连缺天数达到 B2 同一阈值）
    homework_parts: List[str] = []
    missing_30d = _int(homework.get("missing_30d"))
    streak_days = _int(homework.get("current_streak_days"))
    if missing_30d is not None and missing_30d >= th.HOMEWORK_RISK_30D:
        homework_parts.append(f"近 30 天缺交 {missing_30d} 次")
    if streak_days is not None and streak_days >= th.HOMEWORK_RISK_STREAK_DAYS:
        homework_parts.append(f"当前连缺 {streak_days} 天")
    if homework_parts:
        candidates.append(
            (
                f"值得关注：作业{'、'.join(homework_parts)}"
                "（近 7/30 天作业窗口数据）；建议关注近期的作业完成情况",
                ref("作业风险型", "作业缺交/连缺"),
            )
        )

    # 3) 严重偏科（imbalance.severe，B2 同一口径）
    severe = imbalance.get("severe") or []
    if severe:
        consecutive = {
            entry.get("subject"): entry.get("consecutive_exams")
            for entry in (imbalance.get("subjects") or [])
        }
        detail = "、".join(
            f"{subject}（连续 {consecutive[subject]} 场）"
            if consecutive.get(subject) is not None
            else subject
            for subject in severe
        )
        candidates.append(
            (
                f"值得关注：{detail} 弱于总体水平"
                "（偏科口径：单科百分位低于总体百分位）；建议关注对应学科的学习状态",
                ref("明显偏科型", "严重偏科"),
            )
        )

    # 4) 高分段高波动
    if bands.get("high_score") and stability.get("label") == "高波动":
        value = stability.get("value")
        range_note = f"（主三门百分位极差 {value}）" if value is not None else ""
        high_upper = th.STABILITY_RANGE_LABELS[-1][0]
        candidates.append(
            (
                f"值得关注：最近一场处于高分段但成绩波动较大{range_note}"
                f"（稳定性口径：主三门百分位极差 >{high_upper} 判定为高波动）；建议持续观察稳定性",
                ref("高位波动型", "成绩稳定性"),
            )
        )

    # 5) 临界段/薄弱段（学校段位口径）
    if bands.get("critical") or bands.get("weak"):
        band_label = "临界段" if bands.get("critical") else "薄弱段"
        candidates.append(
            (
                f"值得关注：最近一场处于{band_label}"
                "（学校段位口径）；建议保持关注",
                ref("综合风险型", "学段位置（临界/薄弱）"),
            )
        )

    # 6) 数据不足（B2 insufficient_data 门）
    if types.get("classification_status") == "insufficient_data":
        valid_n = (features.get("data_quality") or {}).get("valid_exam_count")
        shown = valid_n if valid_n is not None else "未知"
        candidates.append(
            (
                f"值得关注：有效考试数据不足 2 场（现有 {shown} 场），"
                "类型判定仅供参考；建议待后续考试数据补全后再评估",
                ref("数据不足", "有效考试场数"),
            )
        )

    # 7) 兜底：无风险命中时给观察性条目（保证 1–3 条）
    if not candidates:
        main_type = types.get("main_type")
        if main_type:
            candidates.append(
                (
                    f"值得关注：暂无触发风险类规则的项目（类型判定：{main_type}）；"
                    "建议保持常规观察节奏",
                    [main_type],
                )
            )
        else:
            candidates.append(
                (
                    "值得关注：暂无触发类型与风险类规则的项目；建议保持常规观察节奏",
                    ["classification_status"],
                )
            )

    return [
        _item(index, text, based_on)
        for index, (text, based_on) in enumerate(candidates[:3], start=1)
    ]


def _suggestions_section(features: dict, types: dict) -> dict:
    return {
        "layer": "suggestion",
        "generated": True,
        "edit_scope": SUGGESTION_EDIT_SCOPE,
        "disclaimer": SUGGESTION_DISCLAIMER,
        "items": _generate_suggestions(features, types),
    }
