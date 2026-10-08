"""P2-C2 教师行动首页摘要服务（契约 docs/diagnosis-roadmap/p2-contracts.md §3）。

服务签名::

    def action_summary(db, scope, academic_year_id: int) -> dict

**同源红线**：摘要全部从 B1 ``class_features`` + B2 ``classify_student``
生成——本模块只做「读字段 + 排序 + 计数 + 装配」，绝不另算任何第二口径：
- 类型/优先关注 = B2 ``classify_student(B1 单生 features)`` 的判定结果
  （与 B4 班级路径同法：``classify_student(row["features"])``）；
- 趋势/结构/作业计数 = B1 features 输出字段的直接聚合（direction_recent、
  bands、homework_behavior.missing_30d、teacher_attention.open_follow_ups）；
- 作业风险人数 = B2 判定命中「作业风险型」（主类型或次标签，含
  insufficient_data 下仍如实输出的次标签）的人数，阈值决策在 B2，本模块
  不重写阈值判断；
- 待办 follow_ups 只读既有 ``ws_student_note`` 的 follow_up/follow_up_done/
  review_date/date 字段（**不依赖 C4 其余新列**）：open_n = Σ B1 每生
  teacher_attention.open_follow_ups；due_this_week = 未关闭跟进中
  「计划复查日（review_date，缺省回落档案日期）」落在「本周」的条数。
  「本周」沿用项目既有约定（analysis.py weekly-focus：today-6 天起、含当日，
  P1 §0.7 自然日回溯窗口）。
- 作业统计豁免（ADR-023，与作业预警同一名单）：豁免学生的**作业类信号**
  被抑制（不以缺交/连缺理由进优先关注、不计入作业摘要 risk_n /
  missing_30d_total）；考试类关注（持续下滑等）不受影响；个人档案与
  相关性分析保留完整数据的既有规则不受影响。豁免人数在
  sections.homework.stats_excluded_n 如实展示。

排序（契约 §3 冻结）：综合风险型 > 持续下滑型 > 临界下滑型 > 作业风险
（次标签）> 短期下滑型；同级按 evidence 数多者在前、再按最近一次主三门
名次变化幅度 |rank_change| 大者在前（无变化按 0），最后 person_id 兜底。

理由（reasons）可追溯：类型名 + 由 B1 输出字段拼装的人读短句；作业细节
（缺交次数/连缺天数）只在 B2 判定命中作业风险时输出，阈值常量 import 自
共享 ``thresholds`` 模块（不自带副本），数字来自 B1 features 输出。

输出（契约 §3 冻结形状 + 1 个追加键）：``calc_version="p2-v1"``、``as_of``、
``priority_persons``（每条 person_id/reasons/evidence_ref，另追加 ``name``
供前端「姓名+理由+直达学生页」展示——来自 B1 class_features 的同源名单，
契约形状的追加键，不删不改任何既有键）、``sections``（trend_changes /
structure / homework / follow_ups）。

缺失纪律：零数据/数据不足的学生不进优先关注（无理由不编造）；教学域无
总分/名次 → 结构计数如实为 0；任何计数都是「如实统计 B1 输出」，缺输入
按缺失（不计入对应桶），绝不转 0 冒充有数据——计数类字段本身非 0 即有据。
本模块输出不构成因果判断，也不预测成绩（作业风险=已观察异常的描述）。
"""

from datetime import date, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from app.core.context import WorkspaceContext
from app.core.errors import InvalidScopeParam
from app.diagnosis import thresholds as th
from app.diagnosis.features import class_features
from app.diagnosis.types import classify_student

# 本波口径版本（契约 p2-contracts.md 开头：「本波版本号 p2-v1」）
CALC_VERSION = "p2-v1"

# 优先关注排序（契约 §3）：值越小越靠前；作业风险型按契约括注以次标签身份
# 参与（B2 insufficient_data 下也如实输出的那个次标签）。
PRIORITY_TYPE_ORDER = {
    "综合风险型": 0,   # B2 TYPE_COMPOSITE_RISK
    "持续下滑型": 1,   # B2 TYPE_CONTINUOUS_DECLINE
    "临界下滑型": 2,   # B2 TYPE_CRITICAL_DECLINE
    "作业风险型": 3,   # B2 TYPE_HOMEWORK_RISK（次标签）
    "短期下滑型": 4,   # B2 TYPE_SHORT_TERM_DECLINE
}

# 「本周」窗口（天）：沿用 analysis.py weekly-focus 的既有约定
# （week_start = today - timedelta(days=6)，自然日回溯、含当日，§0.7）。
DUE_THIS_WEEK_DAYS = 7


def _homework_suppressed_features(features: dict) -> dict:
    """豁免学生（ADR-023）的作业特征按「不参与统计」呈现。

    教师已明确将该生移出作业统计（与作业预警同一名单）：作业类字段按
    零值/无数据呈现——这是教师决定的口径，不是数据伪造；B2 分类据此
    不再命中作业风险（主类型或次标签）。考试类指标（趋势/段位/偏科）
    不受影响：豁免针对作业统计，不隐藏考试类关注信号。"""
    indicators = dict(features.get("indicators") or {})
    homework = dict(indicators.get("homework_behavior") or {})
    homework.update(
        {
            "missing_7d": 0,
            "missing_30d": 0,
            "current_streak_days": 0,
            "trend": "无数据",
            "forgot_30d": 0,
            "negative_notes_30d": 0,
            "missing_by_subject": {},
        }
    )
    indicators["homework_behavior"] = homework
    return {**features, "indicators": indicators}


def _as_context(scope) -> WorkspaceContext:
    """服务层接受 WorkspaceContext 或 chat_tools.resolve_scope_snapshot 的
    快照 dict（同源机制的两种载体），统一还原为不可变上下文——与
    changes.py 的双载体先例一致。"""
    if isinstance(scope, WorkspaceContext):
        return scope
    if isinstance(scope, dict):
        as_of = scope.get("as_of")
        return WorkspaceContext(
            teacher_id=int(scope.get("teacher_id") or 0),
            mode=scope["mode"],
            data_domain=scope.get("data_domain") or scope["mode"],
            academic_year_id=scope.get("academic_year_id"),
            class_ids=tuple(scope.get("class_ids") or ()),
            subject=scope.get("subject"),
            link_id=scope.get("link_id"),
            link_version=scope.get("link_version"),
            member_person_ids=tuple(scope.get("member_person_ids") or ()),
            as_of=date.fromisoformat(as_of) if isinstance(as_of, str) else date.today(),
        )
    raise InvalidScopeParam(
        "scope must be a WorkspaceContext or a resolved scope snapshot dict",
        details={"param": "scope"},
    )


# ────────────────────────── 优先关注（排序 + 理由） ──────────────────────────


def _qualifying_ranks(types: dict) -> List[int]:
    """该生命中的优先关注类型等级（去重升序）；空 = 不进优先关注。"""
    ranks = set()
    main_type = types.get("main_type")
    if main_type in PRIORITY_TYPE_ORDER:
        ranks.add(PRIORITY_TYPE_ORDER[main_type])
    for tag in types.get("secondary_tags") or []:
        if tag in PRIORITY_TYPE_ORDER:
            ranks.add(PRIORITY_TYPE_ORDER[tag])
    return sorted(ranks)


def _homework_reason_parts(features: dict) -> List[str]:
    """作业风险的人读细节（契约示例：「连续缺交 3 天」）。

    只在 B2 已判定命中作业风险时被调用：哪些条件命中沿用共享阈值常量
    （thresholds.py，不自带副本），数字直接读 B1 features 输出；字段缺失
    → 不输出该句（绝不按 0 编造）。"""
    homework = ((features.get("indicators") or {}).get("homework_behavior")) or {}
    parts: List[str] = []
    missing_30d = homework.get("missing_30d")
    if missing_30d is not None and int(missing_30d) >= th.HOMEWORK_RISK_30D:
        parts.append(f"近 30 天缺交 {int(missing_30d)} 次")
    streak_days = homework.get("current_streak_days")
    if streak_days is not None and int(streak_days) >= th.HOMEWORK_RISK_STREAK_DAYS:
        parts.append(f"连续缺交 {int(streak_days)} 天")
    return parts


def _decline_reason(features: dict) -> Optional[str]:
    """持续下滑的连击细节（数字来自 B1 trend 输出）。"""
    streak = ((features.get("indicators") or {}).get("trend") or {}).get("streak") or {}
    count = streak.get("count")
    if count is None:
        return None
    return f"连续退步 {int(count)} 次"


def _recent_rank_magnitude(features: dict) -> int:
    """最近一次主三门名次变化幅度 |rank_change|（本次−上次，B1 输出）；
    缺失按 0（无幅度证据），仅用于同级排序，不参与任何判定。"""
    last_change = (((features.get("indicators") or {}).get("trend")) or {}).get("last_change") or {}
    rank_change = last_change.get("rank_change")
    if rank_change is None:
        return 0
    try:
        return abs(int(rank_change))
    except (TypeError, ValueError):
        return 0


def _priority_persons(cls: dict, excluded_ids: set) -> List[dict]:
    """从 B1 class_features 每生行 + B2 classify 结果装配优先关注列表
    （排序：综合风险 > 持续下滑 > 临界下滑 > 作业风险（次标签）> 短期下滑；
    同级 evidence 数多者在前、再按最近变化幅度）。

    作业统计豁免学生（ADR-023，与作业预警同一名单）：作业类信号被抑制
    （不再以缺交/连缺理由进关注清单）；考试类关注（持续下滑等）保留。"""
    entries = []
    for row in cls.get("students") or []:
        features = row.get("features")
        if not isinstance(features, dict):
            continue  # 无 features 的行不编造（缺失纪律）
        if row.get("person_id") in excluded_ids:
            features = _homework_suppressed_features(features)
        types = classify_student(features)
        ranks = _qualifying_ranks(types)
        if not ranks:
            continue  # 无优先关注理由 → 不进列表（不凑数）
        hit_types = {types.get("main_type"), *(types.get("secondary_tags") or [])}
        reasons: List[str] = []
        # 类型理由：按优先级从高到低列命中类型；作业风险型以具体细节呈现
        # （契约示例 reasons=["综合风险型", "连续缺交 3 天"]）
        for rank in ranks:
            type_name = next(
                (name for name, r in PRIORITY_TYPE_ORDER.items() if r == rank), None
            )
            if type_name is not None and type_name != "作业风险型":
                reasons.append(type_name)
        if "作业风险型" in hit_types:
            reasons.extend(_homework_reason_parts(features))
        if "持续下滑型" in hit_types:
            decline = _decline_reason(features)
            if decline:
                reasons.append(decline)
        sort_key = (
            ranks[0],
            -len(types.get("evidence") or []),
            -_recent_rank_magnitude(features),
            row.get("person_id") or 0,
        )
        entries.append((sort_key, row, features, reasons))
    entries.sort(key=lambda item: item[0])
    return [
        {
            "person_id": row.get("person_id"),
            "name": row.get("name"),  # 追加键（B1 class_features 同源名单）
            "reasons": reasons,
            "evidence_ref": {"types": True, "features": True},  # 详情走既有端点
        }
        for _key, row, _features, reasons in entries
    ]


# ────────────────────────── sections 四摘要 ──────────────────────────


def _trend_changes(cls: dict) -> dict:
    """趋势摘要：近方向进步/退步人数（B1 trend.direction_recent 直接计数；
    「持平/数据不足」不属于任一桶，如实不计）。"""
    improving = declining = 0
    for row in cls.get("students") or []:
        features = row.get("features")
        if not isinstance(features, dict):
            continue
        direction = (((features.get("indicators") or {}).get("trend")) or {}).get("direction_recent")
        if direction == "进步":
            improving += 1
        elif direction == "退步":
            declining += 1
    return {"improving_n": improving, "declining_n": declining}


def _structure(cls: dict) -> dict:
    """结构摘要：最近一场段位人数（B1 current_level.bands 布尔直接计数；
    名次不可得的学生三段全 False → 不进任何桶，如实不估算）。"""
    counts = {"high_score": 0, "critical": 0, "weak": 0}
    for row in cls.get("students") or []:
        features = row.get("features")
        if not isinstance(features, dict):
            continue
        bands = (((features.get("indicators") or {}).get("current_level")) or {}).get("bands") or {}
        for key in counts:
            if bands.get(key):
                counts[key] += 1
    return {"band_counts": counts}


def _homework(cls: dict, excluded_ids: set) -> dict:
    """作业摘要：risk_n = B2 判定命中「作业风险型」（主/次标签）的人数；
    missing_30d_total = Σ B1 homework_behavior.missing_30d（无作业记录=0，
    B1 对无批次学生如实输出 0，不进分母纪律此处不适用——这是计数直加）。
    作业统计豁免学生（ADR-023）两处计数均不计入（与作业预警同一名单），
    stats_excluded_n 如实展示豁免人数。"""
    risk_n = 0
    missing_total = 0
    for row in cls.get("students") or []:
        features = row.get("features")
        if not isinstance(features, dict):
            continue
        if row.get("person_id") in excluded_ids:
            continue
        types = classify_student(features)
        hit = types.get("main_type") == "作业风险型" or "作业风险型" in (
            types.get("secondary_tags") or []
        )
        if hit:
            risk_n += 1
        missing_30d = (((features.get("indicators") or {}).get("homework_behavior")) or {}).get(
            "missing_30d"
        )
        if missing_30d is not None:
            missing_total += int(missing_30d)
    return {
        "risk_n": risk_n,
        "missing_30d_total": missing_total,
        "stats_excluded_n": len(excluded_ids),
    }


def _follow_ups(db: Session, ctx: WorkspaceContext, cls: dict, as_of: date) -> dict:
    """待办摘要（只读既有 follow_up/review_date 字段）。

    - open_n：未关闭跟进总数 = Σ B1 每生 teacher_attention.open_follow_ups
      （B1 已实现域内 + 系统辅助行排除的唯一口径，此处只做加总）；
    - due_this_week：未关闭跟进中「计划复查日」落在「本周」（as_of-6 天起、
      含当日，沿用 analysis.py weekly-focus 约定）的条数——到期日取
      review_date，未填复查日的旧跟进回落档案日期（同一批 ws_student_note
      行，同 _human_notes_filter 纪律）。
    """
    open_n = 0
    for row in cls.get("students") or []:
        features = row.get("features")
        if not isinstance(features, dict):
            continue
        open_follow_ups = (
            ((features.get("indicators") or {}).get("teacher_attention")) or {}
        ).get("open_follow_ups")
        if open_follow_ups is not None:
            open_n += int(open_follow_ups)

    from sqlalchemy import func

    from app.api.students_mgmt import _human_notes_filter
    from app.db.workspace_models import WsStudentNote

    member_ids = list(ctx.member_person_ids)
    due_this_week = 0
    if member_ids:
        week_start = as_of - timedelta(days=DUE_THIS_WEEK_DAYS - 1)
        due_date = func.coalesce(WsStudentNote.review_date, WsStudentNote.date)
        rows = (
            db.query(WsStudentNote.date)
            .filter(
                WsStudentNote.data_domain == ctx.data_domain,
                WsStudentNote.person_id.in_(member_ids),
                WsStudentNote.follow_up.isnot(None),
                WsStudentNote.follow_up != "",
                WsStudentNote.follow_up_done == 0,
                due_date >= week_start,
                due_date <= as_of,
                _human_notes_filter(),
            )
            .all()
        )
        due_this_week = len(rows)
    return {"open_n": open_n, "due_this_week": due_this_week}


# ────────────────────────── 服务入口 ──────────────────────────


def action_summary(db: Session, scope, academic_year_id: int) -> dict:
    """教师行动首页摘要（契约 p2-contracts.md §3 响应形状的唯一实现）。"""
    ctx = _as_context(scope)
    if academic_year_id != ctx.academic_year_id:
        raise InvalidScopeParam(
            "academic_year_id does not match the resolved scope",
            details={
                "academic_year_id": academic_year_id,
                "scope_academic_year_id": ctx.academic_year_id,
            },
        )
    cls = class_features(db, ctx, academic_year_id)  # B1：唯一特征来源
    from app.api.homework import _stats_excluded_ids

    excluded_ids = _stats_excluded_ids(db, ctx.data_domain, ctx.class_ids)  # ADR-023
    as_of = ctx.as_of
    return {
        "calc_version": CALC_VERSION,
        "as_of": as_of.isoformat() if isinstance(as_of, date) else as_of,
        "priority_persons": _priority_persons(cls, excluded_ids),
        # 首页类型分布复用本次 B1 结果；保持 B2 原始判定，与单生类型端点同源。
        "class_types": [
            {"person_id": row["person_id"], "types": classify_student(row["features"])}
            for row in cls["students"]
        ],
        "sections": {
            "trend_changes": _trend_changes(cls),
            "structure": _structure(cls),
            "homework": _homework(cls, excluded_ids),
            "follow_ups": _follow_ups(db, ctx, cls, as_of),
        },
    }
