"""P1-B3 学生与班级变化分解（契约 docs/diagnosis-roadmap/p1-contracts.md §5）。

服务函数（契约冻结签名）::

    student_change_decomposition(db, scope, person_id, from_exam, to_exam) -> dict
    class_change_decomposition(db, scope, from_exam, to_exam) -> dict

HTTP 端点见 changes_router（GET /api/v1/{homeroom|teaching}/diagnosis/changes/…）。

口径要点（§0 通用规则 + §5）：
- 计算语义只复用 app/analysis/definitions.py：百分位归一化
  normalized_percentile、年级名次 resolve_year_rank、百分位五等分箱
  percentile_bin、学校段位 band_flags、班内同分同名次 min_ranks。本模块
  不重新实现任何共享定义。
- 取数走 app/api/_queries.readable_facts（F09 统一可读事实口径：本域事实
  + 经 §1.4.1 门的对侧投影行），绝不自建第二套投影/过滤逻辑，绝不跨域取数
  （教学域只有任教学科事实，主三门/总分特征如实标 not_computable）。
- 方向语义（§5 强制文字解释字段 direction_note）：所有变化 = 本次 − 上次。
  年级百分位数值越小相对位置越靠前，故 percentile_change（单位：百分点）
  负值 = 相对位置上升；rank_change 负值 = 名次数值变小 = 相对位置上升。
  分数类变化（等级分/总分）正值 = 分数提高。
- 缺失纪律：缺考不转 0、不进分母、不残留上次值——每场考试的每个指标只从
  该场自身的事实行读取；任一侧缺失 → 该变化 null + missing_reason。
- 名次类指标只报告变化（分布描述），不做任何贡献分解；输出不带因果断言
  字段。
- 班级分解：可比集合固定 = 两场都有效的学生交集（homeroom 基准=主三门
  总分行 score 非 NULL；teaching=任教学科行 score 非 NULL）；按基期
  percentile_bin 与学校段位两种分组输出；组内可加指标 = 总分变化均值
  （teaching 无总分，改为任教学科分数变化均值并如实注明）；每组附
  person_id 下钻名单；excluded（缺考/转入/转出）与 comparable_n 显式输出。
- 阈值常量按 §4 从 app.diagnosis.thresholds 取（B1 建档）；并行窗口内
  thresholds.py 尚未落库时按契约 §4 同值兜底（合并后自动切换到 B1 文件）。
"""

import statistics
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from app.analysis import definitions as defs
from app.core.context import WorkspaceContext
from app.core.errors import InvalidScopeParam, ResourceOutOfScope

try:  # 契约 §4：阈值常量集中在 app/diagnosis/thresholds.py（B1 建、B2/B3 import）
    from app.diagnosis.thresholds import (  # type: ignore
        CHANGE_DECOMPOSITION_TOP_N,
        CLASS_GROUP_MIN_SIZE,
    )
except ImportError:  # B1 未合并的并行开发窗口：按契约 §4 默认值同值兜底
    CHANGE_DECOMPOSITION_TOP_N = 3
    CLASS_GROUP_MIN_SIZE = 3

CALC_VERSION = "p1-v1"
MAIN3_TOTAL_TYPE = "主三门"

DIRECTION_NOTE = (
    "变化方向说明：所有变化值均按「本次 − 上次」计算。年级百分位（0–1，"
    "数值越小相对位置越靠前）的变化 percentile_change 单位为百分点：负值="
    "相对位置上升（进步），正值=相对位置下降（退步）；rank_change 同理，"
    "负值=名次数值变小=相对位置上升。等级分/总分/单科分数变化为分数差，"
    "正值=分数提高。以上均为对既有数据的描述，不做任何因果断言。"
)

RANK_GROUP_NOTE = (
    "名次变化=本次名次−上次名次，负值=名次数值变小=相对位置上升。此处仅"
    "描述组内名次变化分布，不做贡献分解、不归因到任何科目。"
)

COMPARABLE_RULE_NOTE = (
    "可比集合=两场考试都有效的学生交集（homeroom 基准=主三门总分行且"
    "score 非空；teaching=任教学科行且 score 非空）；缺考/无行不进集合、"
    "绝不转 0。"
)

# percentile_bin 分组顺序（与 defs.PERCENTILE_BINS 一致）与段位分组顺序
_BAND_ORDER = ("high_score", "critical", "weak", "no_band")
_BAND_LABELS = {
    "high_score": "高分段",
    "critical": "临界段",
    "weak": "薄弱段",
    "no_band": "未落段（基期名次不可得或不在任何段位）",
}


# ────────────────────────────── scope 与取数 ──────────────────────────────


def _as_context(scope) -> WorkspaceContext:
    """服务层接受 WorkspaceContext 或 chat_tools.resolve_scope_snapshot 的
    快照 dict（同源机制的两种载体），统一还原为不可变上下文。"""
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


def _validate_exam_pair(from_exam: str, to_exam: str) -> None:
    if not from_exam or not to_exam:
        raise InvalidScopeParam(
            "from_exam/to_exam must be non-empty exam names",
            details={"param": "from_exam,to_exam"},
        )
    if from_exam == to_exam:
        raise InvalidScopeParam(
            "from_exam and to_exam must differ",
            details={"from_exam": from_exam, "to_exam": to_exam},
        )


def _exam_rows(db, ctx: WorkspaceContext, exam_name: str):
    from app.db.workspace_models import ScoreFact

    return (
        db.query(ScoreFact.exam_date)
        .filter(
            ScoreFact.data_domain == ctx.data_domain,
            ScoreFact.academic_year_id == ctx.academic_year_id,
            ScoreFact.exam_name == exam_name,
        )
        .all()
    )


def _max_date(rows) -> Optional[date]:
    dates = [row[0] for row in rows if row[0] is not None]
    return max(dates) if dates else None


def _exam_date_of(db, ctx: WorkspaceContext, exam_name: str) -> Optional[date]:
    """按考试名解析考试日期（逐场解析，不复用 exam_date_for 的「本班全场
    最大日期」口径——变化分解的转入/转出名册需要每场各自的时点）。
    回退顺序：本域本班 → 本域全域 → 对侧域全域；全未知 → None。"""
    from app.db.workspace_models import ScoreFact

    class_rows = (
        db.query(ScoreFact.exam_date)
        .filter(
            ScoreFact.data_domain == ctx.data_domain,
            ScoreFact.academic_year_id == ctx.academic_year_id,
            ScoreFact.exam_name == exam_name,
            ScoreFact.class_ref_id.in_(list(ctx.class_ids)),
        )
        .all()
    )
    direct = _max_date(class_rows)
    if direct is not None:
        return direct
    domain_wide = _max_date(_exam_rows(db, ctx, exam_name))
    if domain_wide is not None:
        return domain_wide
    other = "teaching" if ctx.data_domain == "homeroom" else "homeroom"
    rows = (
        db.query(ScoreFact.exam_date)
        .filter(
            ScoreFact.data_domain == other,
            ScoreFact.academic_year_id == ctx.academic_year_id,
            ScoreFact.exam_name == exam_name,
        )
        .all()
    )
    return _max_date(rows)


def _exam_in_domain(db, data_domain: str, academic_year_id: Optional[int], exam_name: str) -> bool:
    from app.db.workspace_models import ScoreFact

    return (
        db.query(ScoreFact.id)
        .filter(
            ScoreFact.data_domain == data_domain,
            ScoreFact.academic_year_id == academic_year_id,
            ScoreFact.exam_name == exam_name,
        )
        .limit(1)
        .first()
        is not None
    )


def _ensure_exams_readable(db, ctx: WorkspaceContext, exams: Sequence[str]) -> None:
    """两场考试都必须在当前作用域可读（域内存在；homeroom 经生效关联可读
    对侧投影考试）——域内完全不存在 → 404，与 analysis 端点同一规则。"""
    for exam_name in exams:
        if _exam_in_domain(db, ctx.data_domain, ctx.academic_year_id, exam_name):
            continue
        if ctx.data_domain == "homeroom" and ctx.link_id is not None:
            if _exam_in_domain(db, "teaching", ctx.academic_year_id, exam_name):
                continue
        if ctx.data_domain == "teaching" and _exam_in_domain(
            db, "homeroom", ctx.academic_year_id, exam_name
        ):
            continue
        raise ResourceOutOfScope(
            "exam not found in current scope",
            details={"exam_name": exam_name, "data_domain": ctx.data_domain},
        )


def _ensure_date_order(from_date: Optional[date], to_date: Optional[date]) -> None:
    if from_date is not None and to_date is not None and from_date > to_date:
        raise InvalidScopeParam(
            "from_exam must not be later than to_exam",
            details={"from_exam_date": from_date.isoformat(), "to_exam_date": to_date.isoformat()},
        )


def _collect_person_facts(db, ctx: WorkspaceContext, exam_name: str, person_id: int):
    """单生两域统一可读事实：{(subject): fact} 与全部总分口径行
    （{total_type: fact}，主三门为其中键之一；B3/research 按所选口径取用）。"""
    from app.api import _queries as q

    entries = q.readable_facts(db, ctx, exam_name, member_ids=[person_id])
    subject_rows: Dict[str, object] = {}
    total_rows: Dict[str, object] = {}
    for entry in entries:
        fact = entry.fact
        if fact.total_type is None and fact.subject:
            subject_rows[fact.subject] = fact
        elif fact.total_type is not None:
            total_rows.setdefault(fact.total_type, fact)
    return subject_rows, total_rows


def _collect_class_facts(db, ctx: WorkspaceContext, exam_name: str, roster: Sequence[int]):
    """班级两域统一可读事实（按考试时点名册取数，F08）：
    subject_rows[pid][subject] = fact；total_rows[pid][total_type] = fact。"""
    from app.api import _queries as q

    entries = q.readable_facts(db, ctx, exam_name, member_ids=list(roster))
    subject_rows: Dict[int, Dict[str, object]] = {}
    total_rows: Dict[int, Dict[str, object]] = {}
    for entry in entries:
        fact = entry.fact
        if fact.total_type is None and fact.subject:
            subject_rows.setdefault(entry.person_id, {})[fact.subject] = fact
        elif fact.total_type is not None:
            total_rows.setdefault(entry.person_id, {})[fact.total_type] = fact
    return subject_rows, total_rows


# ────────────────────────────── 缺失说明 ──────────────────────────────


def _side_reason(fact, need_percentile: bool = False) -> Optional[str]:
    """单侧缺失说明（调用方拼上「上次考试/本次考试」前缀）：
    行缺失 → 缺考（score NULL）→ 字段缺失。"""
    if fact is None:
        return "无此成绩行"
    if fact.score is None:
        return "缺考"
    if need_percentile and defs.normalized_percentile(fact.grade_percentile) is None:
        return "年级百分位未导入"
    return None


def _join_reasons(*reasons: Optional[str]) -> Optional[str]:
    parts = [f"上次考试{r}" if i == 0 else f"本次考试{r}" for i, r in enumerate(reasons) if r]
    return "；".join(parts) if parts else None


# ────────────────────────────── 学生变化分解 ──────────────────────────────


def _subject_change_item(subject: str, from_fact, to_fact) -> dict:
    # 缺考纪律（score=NULL）：该场该科不可比，百分位/等级分一律不取
    # （行内残留的旧指标值绝不冒充本次成绩），只报缺考原因。
    def _pct(fact):
        if fact is None or fact.score is None:
            return None
        return defs.normalized_percentile(getattr(fact, "grade_percentile", None))

    def _grade(fact):
        if fact is None or fact.score is None:
            return None
        return getattr(fact, "grade_score", None)

    from_pct, to_pct = _pct(from_fact), _pct(to_fact)
    from_grade, to_grade = _grade(from_fact), _grade(to_fact)

    percentile_change = None
    if from_pct is not None and to_pct is not None:
        percentile_change = round((to_pct - from_pct) * 100, 1)  # 百分点
    grade_score_change = None
    if from_grade is not None and to_grade is not None:
        grade_score_change = round(float(to_grade) - float(from_grade), 1)

    missing_reason = _join_reasons(
        _side_reason(from_fact, need_percentile=True),
        _side_reason(to_fact, need_percentile=True),
    )
    return {
        "subject": subject,
        "from": {
            "present": from_fact is not None,
            "score": from_fact.score if from_fact is not None else None,
            "percentile": round(from_pct, 4) if from_pct is not None else None,
            "grade_score": from_grade,
        },
        "to": {
            "present": to_fact is not None,
            "score": to_fact.score if to_fact is not None else None,
            "percentile": round(to_pct, 4) if to_pct is not None else None,
            "grade_score": to_grade,
        },
        # 百分点（to−from 后 ×100）：负值=相对位置上升；缺失 → null+说明
        "percentile_change": percentile_change,
        "grade_score_change": grade_score_change,
        "missing_reason": missing_reason,
    }


def _total_change(from_fact, to_fact, data_domain: str, total_type: str = MAIN3_TOTAL_TYPE) -> dict:
    def _side(fact):
        if fact is None:
            return {"present": False, "score": None, "rank": None, "rank_basis": None}
        # 缺考（score=NULL）：行在但名次不取——行内残留名次绝不冒充成绩
        rank = (
            defs.resolve_year_rank(fact.xueji_rank, fact.grade_rank)
            if fact.score is not None
            else None
        )
        return {
            "present": True,
            "score": fact.score,
            "rank": rank,
            "rank_basis": "school" if rank is not None else None,
        }

    from_side = _side(from_fact)
    to_side = _side(to_fact)
    label = "主三门" if total_type == MAIN3_TOTAL_TYPE else f"{total_type}总分"
    if data_domain == "teaching" and from_fact is None and to_fact is None:
        missing_reason = "教学域只有任教学科事实，无总分（绝不跨域取数）"
    else:
        reasons = []
        for side_label, fact, side in (
            ("上次", from_fact, from_side),
            ("本次", to_fact, to_side),
        ):
            if not side["present"]:
                reasons.append(f"{side_label}考试{label}成绩行缺失")
            elif fact.score is None:
                reasons.append(f"{side_label}考试{label}缺考")
            elif side["rank"] is None:
                reasons.append(f"{side_label}考试{label}名次不可得（学籍/年级名次均缺失）")
        missing_reason = "；".join(reasons) if reasons else None
    rank_change = None
    if from_side["rank"] is not None and to_side["rank"] is not None:
        rank_change = int(to_side["rank"]) - int(from_side["rank"])  # 负=名次变小=上升
    return {
        "total_type": total_type,
        "from": from_side,
        "to": to_side,
        "rank_change": rank_change,
        "missing_reason": missing_reason,
    }


def _main3_change(from_fact, to_fact, data_domain: str) -> dict:
    return _total_change(from_fact, to_fact, data_domain)


def student_change_decomposition(db, scope, person_id, from_exam, to_exam) -> dict:
    """单生两场考试的变化分解（契约 §5）。

    输出各科 percentile_change（百分点，本次−上次，负值=相对位置上升）、
    主三门名次变化（resolve_year_rank 口径）、等级分变化、top_movers
    （|percentile_change| 前 CHANGE_DECOMPOSITION_TOP_N 科）与每科缺失
    说明；方向语义见 direction_note（§5 强制文字解释字段）。"""
    ctx = _as_context(scope)
    _validate_exam_pair(from_exam, to_exam)
    _ensure_exams_readable(db, ctx, [from_exam, to_exam])
    if person_id not in set(ctx.member_person_ids):
        raise ResourceOutOfScope(
            "person not in current scope", details={"person_id": person_id}
        )
    from_date = _exam_date_of(db, ctx, from_exam)
    to_date = _exam_date_of(db, ctx, to_exam)
    _ensure_date_order(from_date, to_date)

    from_subjects, from_totals = _collect_person_facts(db, ctx, from_exam, person_id)
    to_subjects, to_totals = _collect_person_facts(db, ctx, to_exam, person_id)

    subjects = [
        _subject_change_item(subject, from_subjects.get(subject), to_subjects.get(subject))
        for subject in sorted(set(from_subjects) | set(to_subjects))
    ]
    # 全口径总分分解（主三门/五门/3+3……两场任一侧存在的口径都输出；
    # research 结局聚合按所选 metric 取对应口径，不再固定主三门）
    totals = [
        _total_change(from_totals.get(tt), to_totals.get(tt), ctx.data_domain, tt)
        for tt in sorted(set(from_totals) | set(to_totals))
    ]
    main3 = _main3_change(
        from_totals.get(MAIN3_TOTAL_TYPE), to_totals.get(MAIN3_TOTAL_TYPE), ctx.data_domain
    )

    movers = [
        item
        for item in subjects
        if item["percentile_change"] is not None
    ]
    movers.sort(key=lambda item: (-abs(item["percentile_change"]), item["subject"]))
    top_movers = [
        {
            "subject": item["subject"],
            "percentile_change": item["percentile_change"],
            "abs_change": abs(item["percentile_change"]),
            "direction": "相对位置上升" if item["percentile_change"] < 0 else "相对位置下降",
        }
        for item in movers[:CHANGE_DECOMPOSITION_TOP_N]
    ]

    computable = (
        bool(top_movers)
        or main3["rank_change"] is not None
        or any(item["grade_score_change"] is not None for item in subjects)
        or any(item["rank_change"] is not None for item in totals)
    )
    if not computable:
        has_any = (
            bool(from_subjects)
            or bool(to_subjects)
            or bool(from_totals)
            or bool(to_totals)
        )
        missing_reason = (
            "两场考试间无可比变化（缺考或年级百分位/名次字段缺失）"
            if has_any
            else "两次考试均无该生可读成绩事实"
        )
        status = "not_computable"
    else:
        missing_reason = None
        status = "ok"

    return {
        "person_id": person_id,
        "calc_version": CALC_VERSION,
        "data_domain": ctx.data_domain,
        "academic_year_id": ctx.academic_year_id,
        "from_exam": from_exam,
        "from_exam_date": from_date.isoformat() if from_date else None,
        "to_exam": to_exam,
        "to_exam_date": to_date.isoformat() if to_date else None,
        "direction_note": DIRECTION_NOTE,
        "status": status,
        "missing_reason": missing_reason,
        "main3": main3,
        "totals": totals,
        "subjects": subjects,
        "top_movers": top_movers,
    }


# ────────────────────────────── 班级变化分解 ──────────────────────────────


def _roster_at(db, ctx: WorkspaceContext, exam_name: str, exam_date: Optional[date]) -> List[int]:
    """考试时点名册（F08）：有考试日期按 members_at 解析；日期未知回退
    查询时点成员（membership_basis 由调用方标注为 current）。"""
    from app.api import _queries as q

    if exam_date is not None:
        return q.members_at(db, ctx.mode, ctx.class_ids, exam_date)
    return list(ctx.member_person_ids)


def _basis_records_homeroom(subject_rows, total_rows, person_ids) -> Dict[int, dict]:
    records: Dict[int, dict] = {}
    for pid in person_ids:
        main3 = (total_rows.get(pid) or {}).get(MAIN3_TOTAL_TYPE)
        if main3 is None or main3.score is None:
            continue  # 基准行缺考/缺失 → 不进可比集合（绝不转 0）
        records[pid] = {
            "score": main3.score,
            "percentile": defs.normalized_percentile(main3.grade_percentile),
            "rank": defs.resolve_year_rank(main3.xueji_rank, main3.grade_rank),
            "grade_score": None,
        }
    return records


def _basis_records_teaching(subject_rows, person_ids, subject: str) -> Dict[int, dict]:
    records: Dict[int, dict] = {}
    for pid in person_ids:
        row = subject_rows.get(pid, {}).get(subject)
        if row is None or row.score is None:
            continue
        records[pid] = {
            "score": row.score,
            "percentile": defs.normalized_percentile(row.grade_percentile),
            "rank": None,  # 教学域无年级名次；组内名次用 min_ranks 另算
            "grade_score": getattr(row, "grade_score", None),
        }
    return records


def _rank_summary(changes: List[int]) -> dict:
    improved = sum(1 for c in changes if c < 0)
    declined = sum(1 for c in changes if c > 0)
    unchanged = sum(1 for c in changes if c == 0)
    median = round(float(statistics.median(changes)), 1) if changes else None
    return {
        "rank_basis": None,  # 由调用方填写
        "counted_n": len(changes),
        "improved_n": improved,
        "declined_n": declined,
        "unchanged_n": unchanged,
        "median_change": median,
        "note": RANK_GROUP_NOTE,
        "missing_reason": None if changes else "组内没有可用的成对名次",
    }


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 2) if values else None


def _build_group(
    key: str,
    label: str,
    person_ids: List[int],
    from_records: Dict[int, dict],
    to_records: Dict[int, dict],
    *,
    data_domain: str,
    rank_basis: str,
) -> dict:
    n = len(person_ids)
    small = n < CLASS_GROUP_MIN_SIZE
    small_reason = (
        f"组内可比样本 {n} < 最小样本 {CLASS_GROUP_MIN_SIZE}"
        f"（CLASS_GROUP_MIN_SIZE），聚合指标不输出"
    )

    total_changes: List[float] = []
    score_changes: List[float] = []
    for pid in person_ids:
        base, cur = from_records[pid], to_records[pid]
        if data_domain == "homeroom" and base["score"] is not None and cur["score"] is not None:
            total_changes.append(float(cur["score"]) - float(base["score"]))
        if data_domain == "teaching" and base["score"] is not None and cur["score"] is not None:
            score_changes.append(float(cur["score"]) - float(base["score"]))

    if small:
        total_avg, total_reason = None, small_reason
        score_avg, score_reason = None, small_reason
        rank_change = None
    else:
        total_reason = None
        score_reason = None
        if data_domain == "homeroom":
            total_avg = _mean(total_changes)
            score_avg, score_reason = None, None
            if total_avg is None:
                total_reason = "组内没有成对总分（基准行缺失）"
        else:
            score_avg = _mean(score_changes)
            total_avg = None
            total_reason = "教学域只有任教学科事实，无总分变化均值（绝不跨域取数）"
            if score_avg is None:
                score_reason = "组内没有成对单科分数"
        changes: List[int] = []
        if rank_basis == "year_rank":
            for pid in person_ids:
                base_rank, cur_rank = from_records[pid]["rank"], to_records[pid]["rank"]
                if base_rank is not None and cur_rank is not None:
                    changes.append(int(cur_rank) - int(base_rank))
        else:  # class_min_rank：教学域组内名次（同分同名次 min-rank 共享口径）
            from_ranks = defs.min_ranks([(pid, from_records[pid]["score"]) for pid in person_ids])
            to_ranks = defs.min_ranks([(pid, to_records[pid]["score"]) for pid in person_ids])
            for pid in person_ids:
                if from_ranks[pid] is not None and to_ranks[pid] is not None:
                    changes.append(int(to_ranks[pid]) - int(from_ranks[pid]))
        rank_change = _rank_summary(changes)
        rank_change["rank_basis"] = rank_basis

    return {
        "key": key,
        "label": label,
        "n": n,
        "person_ids": sorted(person_ids),
        "metrics_available": not small,
        "total_change_avg": total_avg,
        "total_change_missing_reason": total_reason,
        "score_change_avg": score_avg,
        "score_change_missing_reason": score_reason,
        "rank_change": rank_change,
    }


def class_change_decomposition(db, scope, from_exam, to_exam) -> dict:
    """班级两场考试的变化分解（契约 §5）。

    可比集合固定 = 两场都有效的学生交集；按基期 percentile_bin 与学校
    段位两种分组；组内可加指标（homeroom=主三门总分变化均值，teaching=
    任教学科分数变化均值）+ 名次类指标的仅变化描述（不做贡献分解）；
    每组附 person_id 下钻名单；excluded（缺考/转入/转出）与 comparable_n
    显式输出；分组样本 < CLASS_GROUP_MIN_SIZE 时聚合指标不输出。"""
    from app.db.workspace_models import AcademicYear

    ctx = _as_context(scope)
    _validate_exam_pair(from_exam, to_exam)
    _ensure_exams_readable(db, ctx, [from_exam, to_exam])
    from_date = _exam_date_of(db, ctx, from_exam)
    to_date = _exam_date_of(db, ctx, to_exam)
    _ensure_date_order(from_date, to_date)

    roster_from = set(_roster_at(db, ctx, from_exam, from_date))
    roster_to = set(_roster_at(db, ctx, to_exam, to_date))
    subject_rows_from, total_rows_from = _collect_class_facts(db, ctx, from_exam, roster_from)
    subject_rows_to, total_rows_to = _collect_class_facts(db, ctx, to_exam, roster_to)

    if ctx.data_domain == "homeroom":
        from_records = _basis_records_homeroom(subject_rows_from, total_rows_from, roster_from)
        to_records = _basis_records_homeroom(subject_rows_to, total_rows_to, roster_to)
    else:
        from_records = _basis_records_teaching(subject_rows_from, roster_from, ctx.subject)
        to_records = _basis_records_teaching(subject_rows_to, roster_to, ctx.subject)

    comparable = sorted(set(from_records) & set(to_records))
    transferred_out = sorted(roster_from - roster_to)
    transferred_in = sorted(roster_to - roster_from)
    missing_exam = sorted((roster_from & roster_to) - set(comparable))

    # 分组一律按基期（from_exam）口径：分箱走共享 percentile_bin，
    # 段位走共享 band_flags（名次不可得 → 不落段，绝不编造）。
    bin_members: Dict[str, List[int]] = {}
    ungrouped: List[int] = []
    for pid in comparable:
        key = defs.percentile_bin(from_records[pid]["percentile"])
        if key is None:
            ungrouped.append(pid)
        else:
            bin_members.setdefault(key, []).append(pid)

    band_members: Dict[str, List[int]] = {}
    band_config = None
    if ctx.data_domain == "homeroom":
        from app.analysis.config import get_band_config

        band_config = get_band_config(db)
        for pid in comparable:
            flags = defs.band_flags(from_records[pid]["rank"], band_config)
            key = next((name for name in ("high_score", "critical", "weak") if flags[name]), "no_band")
            band_members.setdefault(key, []).append(pid)

    def _group_for(key: str, members: List[int], kind: str) -> dict:
        if kind == "bin":
            label = next(lbl for k, lbl, _lo, _hi in defs.PERCENTILE_BINS if k == key)
        else:
            label = _BAND_LABELS[key]
            if key in ("high_score", "critical", "weak") and band_config is not None:
                ranges = {
                    "high_score": f"高分段（1–{band_config['high_score_max']}名）",
                    "critical": f"临界段（{band_config['critical_min']}–{band_config['critical_max']}名）",
                    "weak": f"薄弱段（{band_config['weak_min']}名起）",
                }
                label = ranges[key]
        return _build_group(
            key,
            label,
            members,
            from_records,
            to_records,
            data_domain=ctx.data_domain,
            rank_basis="year_rank" if ctx.data_domain == "homeroom" else "class_min_rank",
        )

    bin_order = [k for k, _l, _lo, _hi in defs.PERCENTILE_BINS]
    groups = [
        _group_for(key, bin_members[key], "bin")
        for key in sorted(bin_members, key=bin_order.index)
    ]
    band_groups = [
        _group_for(key, band_members[key], "band")
        for key in _BAND_ORDER
        if key in band_members
    ]

    ungrouped_payload = None
    if ungrouped:
        ungrouped_payload = {
            "n": len(ungrouped),
            "person_ids": sorted(ungrouped),
            "missing_reason": "基期年级百分位缺失，无法进百分位分箱（缺考不残留上次百分位）",
        }

    year_name = None
    if ctx.academic_year_id is not None:
        year = db.get(AcademicYear, ctx.academic_year_id)
        year_name = year.name if year is not None else None

    status = "ok" if comparable else "not_computable"
    missing_reason = None if comparable else "两场考试在本范围内没有可比学生交集"

    return {
        "calc_version": CALC_VERSION,
        "data_domain": ctx.data_domain,
        "academic_year_id": ctx.academic_year_id,
        "academic_year_name": year_name,
        "from_exam": from_exam,
        "from_exam_date": from_date.isoformat() if from_date else None,
        "to_exam": to_exam,
        "to_exam_date": to_date.isoformat() if to_date else None,
        "membership_basis": "exam" if from_date is not None and to_date is not None else "current",
        "comparable_rule": COMPARABLE_RULE_NOTE,
        "direction_note": DIRECTION_NOTE,
        "status": status,
        "missing_reason": missing_reason,
        "comparable_n": len(comparable),
        "excluded": {
            "missing_exam": {"n": len(missing_exam), "person_ids": missing_exam},
            "transferred_in": {"n": len(transferred_in), "person_ids": transferred_in},
            "transferred_out": {"n": len(transferred_out), "person_ids": transferred_out},
        },
        "groups": groups,
        "ungrouped": ungrouped_payload,
        "band_groups": band_groups,
        "band_groups_missing_reason": (
            "教学域无总分名次事实，不提供学校段位分组（绝不跨域取数）"
            if ctx.data_domain == "teaching"
            else None
        ),
    }
