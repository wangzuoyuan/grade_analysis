"""关注回看：B2 主/次标签选人，B3 比较事实，C4 逐条复查。

旧 research API 保持兼容。本服务不写库；历史名单只由起点决定。
"""
from datetime import date, timedelta

from app.analysis import definitions as defs
from app.api import _queries as q
from app.api.homework import _person_events, _stats_excluded_ids, _visible_assignments, _parse_expected_ids
from app.core.errors import InvalidScopeParam
from app.diagnosis import thresholds as th
from app.diagnosis.changes import _ensure_exams_readable, _exam_date_of, student_change_decomposition
from app.diagnosis.features import class_features, class_features_at_anchors, _group_exams, _homework_from
from app.diagnosis.research import (
    _check_year, _metric_options, _metric_change_of, _intervention_notes,
    LIMITATIONS_RETROSPECTIVE,
)
from app.diagnosis.review import metric_meta_or_422, unit_of_metric, metric_points, review_contrast
from app.diagnosis.types import classify_student

CALC_VERSION = "p3-focus-v1"
# 仅用于本页变化展示；不修改诊断类型。百分点/等级分阈值为待校准的暂定值。
DISPLAY_THRESHOLDS = {"rank": th.TREND_DIRECTION_MIN_CHANGE, "percentile": 5, "grade_score": 3}


def _display_thresholds(db):
    return {**DISPLAY_THRESHOLDS, "rank": th.get_trend_thresholds(db)["direction_rank_change"]}


PROBLEMS = ("明显偏科型", "持续下滑型", "短期下滑型", "临界下滑型", "稳定临界型", "临界上升型", "作业风险型", "综合风险型", "持续进步型", "稳定优秀型", "高位波动型")


def _rows(cls, problem, suppressed):
    result = []
    for row in cls.get("students", []):
        features = row.get("features") or {}
        if problem == "作业风险型" and row["person_id"] in suppressed:
            continue
        typed = classify_student(features)
        if problem not in {typed.get("main_type"), *(typed.get("secondary_tags") or [])}:
            continue
        evidence = next((e["basis"] for e in typed["evidence"] if e["type"] == problem), problem)
        result.append({**row, "reason": evidence, "main_type": typed.get("main_type"), "is_secondary": typed.get("main_type") != problem})
    return result


def focus_cohorts(db, ctx):
    """当前名单一次提供；历史日期不可证明时不生成伪历史队列。"""
    exams = q.readable_exam_summaries(db, ctx)
    anchored = class_features_at_anchors(db, ctx, ctx.academic_year_id, [e["exam_name"] for e in exams])
    current = anchored[None]  # 多锚点读取已包含当前桶，无须再次查询整班事实
    suppressed = _stats_excluded_ids(db, ctx.data_domain, ctx.class_ids)
    cohorts = []
    for basis, anchor, cls in [("current_time_point", None, current)] + [
        ("exam_anchor", e["exam_name"], anchored[e["exam_name"]]) for e in exams
        if (anchored[e["exam_name"]].get("anchor") or {}).get("date_resolved")
    ]:
        for problem in PROBLEMS:
            members = _rows(cls, problem, suppressed)
            # 保留零人数供切换时说明，避免老师不知道该问题能否筛选。
            cohorts.append({
                "cohort": f"focus:{problem}@{anchor}" if anchor else f"current-focus:{problem}",
                "kind": "focus", "type_name": problem, "exam_name": anchor,
                "membership_basis": basis, "student_count": len(members),
                "person_ids": [r["person_id"] for r in members],
            })
    return {"calc_version": CALC_VERSION, "as_of": ctx.as_of.isoformat(), "academic_year_id": ctx.academic_year_id,
            "exams": [{"exam_name": e["exam_name"], "exam_date": e["exam_date"],
                       "historical_available": bool((anchored[e["exam_name"]].get("anchor") or {}).get("date_resolved"))} for e in exams],
            "cohorts": cohorts, "metric_options": _metric_options(db, ctx),
            "display_thresholds": _display_thresholds(db), "limitations": [LIMITATIONS_RETROSPECTIVE]}


def _selection(db, ctx, cohort, from_exam):
    if cohort.startswith("current-focus:"):
        problem, anchor = cohort[len("current-focus:"):], None
    elif cohort.startswith("focus:") and "@" in cohort:
        problem, anchor = cohort[len("focus:"):].rsplit("@", 1)
        if anchor != from_exam:
            raise InvalidScopeParam("历史关注名单必须按起点考试判定")
    else:
        raise InvalidScopeParam("请选择关注问题")
    if problem not in PROBLEMS:
        raise InvalidScopeParam("未知的关注问题")
    cls = class_features(db, ctx, ctx.academic_year_id, anchor_exam=anchor)
    if anchor and not (cls.get("anchor") or {}).get("date_resolved"):
        raise InvalidScopeParam("起点考试日期不足，无法还原当时名单；请切换当前问题学生")
    suppressed = _stats_excluded_ids(db, ctx.data_domain, ctx.class_ids)
    return problem, _rows(cls, problem, suppressed), "exam_anchor" if anchor else "current_time_point"


def _direction(change, unit, display_thresholds=None):
    t = (display_thresholds or DISPLAY_THRESHOLDS)[unit]
    oriented = change if unit == "grade_score" else -change
    return "进步" if oriented >= t else "退步" if oriented <= -t else "未达变化阈值"


def _metric_sides(decomposition, meta, unit):
    rows = decomposition.get("totals" if unit == "rank" else "subjects", [])
    key = "total_type" if unit == "rank" else "subject"
    row = next((r for r in rows if r[key] == meta["key"]), {})
    field = {"rank": "rank", "percentile": "percentile", "grade_score": "grade_score"}[unit]
    factor = 100 if unit == "percentile" else 1
    return {side: (row.get(side) or {}).get(field) * factor if (row.get(side) or {}).get(field) is not None else None for side in ("from", "to")}


def _imbalance(db, ctx, pid, decomposition, from_exam, to_exam, member):
    # B3 提供单科前后值，基准百分位按同场可读事实取值，不用名次估算。
    buckets = _group_exams(q.readable_facts(db, ctx, member_ids=[pid]))
    base = {}
    for side, exam in (("from", from_exam), ("to", to_exam)):
        fact = (buckets.get(exam) or {}).get("totals", {}).get("主三门")
        base[side] = defs.normalized_percentile(fact.grade_percentile) if fact is not None and fact.score is not None else None
    severe = set(((member.get("features") or {}).get("indicators") or {}).get("imbalance", {}).get("severe", []))
    details = []
    for row in decomposition.get("subjects", []):
        if row["subject"] not in severe:
            continue
        fp, tp = row["from"].get("percentile"), row["to"].get("percentile")
        fg = round((fp - base["from"]) * 100, 2) if fp is not None and base["from"] is not None else None
        tg = round((tp - base["to"]) * 100, 2) if tp is not None and base["to"] is not None else None
        if fg is None or tg is None:
            verdict = "暂不可比"
        elif tg < fg and tp > fp:
            verdict = "差距缩小，但该科退步"
        elif tg <= fg - DISPLAY_THRESHOLDS["percentile"] and tp <= fp:
            verdict = "短板差距缩小，且该科未退步"
        elif tg >= fg + DISPLAY_THRESHOLDS["percentile"]:
            verdict = "短板差距扩大"
        else:
            verdict = "差距未达变化阈值"
        details.append({"subject": row["subject"], "from_percentile": round(fp * 100, 2) if fp is not None else None,
                        "to_percentile": round(tp * 100, 2) if tp is not None else None,
                        "from_overall_percentile": round(base["from"] * 100, 2) if base["from"] is not None else None,
                        "to_overall_percentile": round(base["to"] * 100, 2) if base["to"] is not None else None,
                        "from_gap": fg, "to_gap": tg, "verdict": verdict,
                        "missing_reason": row.get("missing_reason") if fg is None or tg is None else None})
    valid = [r for r in details if r["from_gap"] is not None and r["to_gap"] is not None]
    # 多个短板不合并成虚假单一名次贡献；逐科展示，人数分类仅作提示。
    if not valid:
        verdict = "暂不可比"
    elif any(r["verdict"] == "差距缩小，但该科退步" for r in valid):
        verdict = "需核查：差距缩小但单科退步"
    elif any(r["verdict"] == "短板差距扩大" for r in valid):
        verdict = "短板差距扩大"
    elif all(r["verdict"] == "短板差距缩小，且该科未退步" for r in valid):
        verdict = "短板有所改善" if len(valid) == len(details) else "部分学科改善，其余暂不可比"
    else:
        verdict = "短板仍需关注"
    return details, verdict, bool(valid)


def _homework_compare(db, ctx, pid, from_day, to_day):
    if from_day is None or to_day is None:
        return {"comparable": False, "reason": "考试日期不足，无法确定两个作业窗口"}
    events, axes = _person_events(db, ctx, pid, academic_year_id=ctx.academic_year_id)
    visible = {a.id: (a, mapping) for a, mapping in _visible_assignments(db, ctx, active_only=True)}
    events = [(a, submission) for a, submission in events if a.id in visible]
    axes = {key: [a for a in axis if a.id in visible] for key, axis in axes.items()}
    sides = {}
    for side, end in (("from", from_day), ("to", to_day)):
        start = end - timedelta(days=29)
        behavior = _homework_from(ctx.mode, events, axes, end)
        # 只计该生被明确纳入应交名单的有效作业批次；旧无分母不能冒充全交。
        # event 包含明确应交的默认已交行及真实例外行；同班但未要求该生提交的
        # 批次不能算该生的缺分母旧记录。
        batches = {a.id: a for a, _ in events if a.subject != "考勤" and start <= a.assigned_date <= end}
        eligible = [a for a in batches.values() if pid in {visible[a.id][1].get(source_id) if visible[a.id][1] is not None else source_id for source_id in _parse_expected_ids(a.expected_members_json)}]
        sides[side] = {"start": start.isoformat(), "end": end.isoformat(), "missing": behavior["missing_30d"],
                       "streak_days": behavior["current_streak_days"], "valid_batches": len(eligible),
                       "subjects": sorted({a.subject for a in eligible}), "legacy_batches": len(batches) - len(eligible)}
    comparable = all(s["valid_batches"] > 0 and s["legacy_batches"] == 0 for s in sides.values())
    return {**sides, "comparable": comparable,
            "reason": "缺交次数受批次数和学科构成影响，先核对覆盖，再判断是否改善" if comparable else "无有效应交名单或窗口覆盖不足，不能把缺交次数下降认作改善"}


def focus_outcome(db, ctx, cohort, from_exam, to_exam, metric):
    _check_year(ctx, ctx.academic_year_id)
    if from_exam == to_exam:
        raise InvalidScopeParam("请选择不同的起点与终点考试")
    _ensure_exams_readable(db, ctx, [from_exam, to_exam])
    # 校验考试顺序，包含日期不足时按既有有序考试清单核对，不能颠倒回看。
    exams = q.readable_exam_summaries(db, ctx)
    names = [e["exam_name"] for e in exams]
    if names.index(from_exam) <= names.index(to_exam):
        raise InvalidScopeParam("终点考试必须晚于起点考试")
    problem, members, basis = _selection(db, ctx, cohort, from_exam)
    problem_metric = metric in ("problem:imbalance", "problem:homework")
    if metric == "problem:imbalance" and problem != "明显偏科型":
        raise InvalidScopeParam("偏科指标仅适用于偏科关注名单")
    if metric == "problem:homework" and problem != "作业风险型":
        raise InvalidScopeParam("作业指标仅适用于作业风险关注名单")
    meta = metric_meta_or_422(db, ctx, metric) if not problem_metric else None
    unit = unit_of_metric(meta) if meta else "percentile" if metric == "problem:imbalance" else "count"
    display_thresholds = _display_thresholds(db)
    notes = _intervention_notes(db, ctx)
    note_counts = {}
    for note in notes:
        note_counts[note.person_id] = note_counts.get(note.person_id, 0) + 1
    students, excluded = [], []
    from_day, to_day = _exam_date_of(db, ctx, from_exam), _exam_date_of(db, ctx, to_exam)
    for member in members:
        pid = member["person_id"]
        decomposition = student_change_decomposition(db, ctx, pid, from_exam, to_exam)
        row = {"person_id": pid, "name": member.get("name"), "reason": member["reason"],
               "main_type": member["main_type"], "is_secondary": member["is_secondary"],
               "follow_up_count": note_counts.get(pid, 0), "change": None, "from_value": None, "to_value": None,
               "imbalance": [], "homework": None, "timeline": [], "missing_reason": None}
        if metric == "problem:imbalance":
            details, direction, comparable = _imbalance(db, ctx, pid, decomposition, from_exam, to_exam, member)
            row.update(imbalance=details, direction=direction, comparable=comparable)
            if not comparable:
                row["missing_reason"] = "原短板学科或主三门基准缺考／缺少百分位，暂不可比"
        elif metric == "problem:homework":
            h = _homework_compare(db, ctx, pid, from_day, to_day)
            row.update(homework=h, comparable=h["comparable"], direction="核对作业覆盖" if h["comparable"] else "暂不可比")
            if not h["comparable"]:
                row["missing_reason"] = h["reason"]
        else:
            change, reason = _metric_change_of(decomposition, meta, unit)
            sides = _metric_sides(decomposition, meta, unit)
            row.update(change=change, from_value=sides["from"], to_value=sides["to"], comparable=change is not None,
                       missing_reason=reason, direction=_direction(change, unit, display_thresholds) if change is not None else "暂不可比")
            points = metric_points(db, ctx, pid, metric)
            ordered = list(reversed(names))
            lo, hi = ordered.index(from_exam), ordered.index(to_exam)
            row["timeline"] = [{**p, "value": round(p["value"] * 100, 2) if unit == "percentile" and p["value"] is not None else p["value"]}
                               for p in points if p["exam_name"] in ordered[lo:hi + 1]]
        if row["comparable"]:
            students.append(row)
        else:
            excluded.append(row)
    # 先呈现需要核查的人；不把关注排序当风险概率。
    students.sort(key=lambda r: (0 if any(s in r["direction"] for s in ("退步", "扩大", "需核查")) else 1, r["person_id"]))
    return {"calc_version": CALC_VERSION, "as_of": ctx.as_of.isoformat(), "cohort": cohort, "problem": problem,
            "membership_basis": basis, "from_exam": from_exam, "to_exam": to_exam,
            "from_exam_date": from_day.isoformat() if from_day else None, "to_exam_date": to_day.isoformat() if to_day else None,
            "metric": metric, "metric_unit": unit, "threshold": display_thresholds.get(unit),
            "threshold_provisional": unit in ("percentile", "grade_score"),
            "selected_n": len(members), "comparable_n": len(students), "excluded_n": len(excluded),
            "students": students, "excluded_students": excluded,
            "summary": {"improved_n": sum(r["direction"] in ("进步", "短板有所改善") for r in students),
                        "needs_check_n": sum(any(w in r["direction"] for w in ("退步", "扩大", "需核查", "仍需", "部分", "覆盖")) for r in students)},
            "limitations": [LIMITATIONS_RETROSPECTIVE, "名单来自当前名册；历史转出学生不属于本次类型回看。"]}


def follow_ups(db, ctx):
    notes = _intervention_notes(db, ctx)
    names = q.names_for(db, [n.person_id for n in notes])
    options = {m["value"]: m["label"] for m in _metric_options(db, ctx)}
    members = set(ctx.member_person_ids)
    records = []
    for note in notes:
        if note.person_id not in members:
            # 历史成员保留记录，但 C4 不在当前范围不可跨名册读成绩。
            contrast = {"status": "pending", "reason": "transferred_out", "note": "已离班，本页不跨当前名册读取后续成绩"}
        else:
            try:
                contrast = review_contrast(db, ctx, note)
            except InvalidScopeParam:
                contrast = {"status": "pending", "reason": "unsupported_metric", "note": "目标指标不适用于当前学段，请在学生档案核对"}
        closed = note.status != "open"
        due = bool(note.review_date and note.review_date <= ctx.as_of)
        records.append({"id": note.id, "person_id": note.person_id, "name": names.get(note.person_id),
                        "in_current_roster": note.person_id in members, "problem": note.problem, "measures": note.measures,
                        "subject": note.subject_scope, "start_date": (note.start_date or note.date).isoformat(),
                        "review_date": note.review_date.isoformat() if note.review_date else None,
                        "record_status": note.status, "due": due, "metric": note.target_metric,
                        "metric_label": options.get(note.target_metric, "未设定或不适用的目标指标"), "contrast": contrast,
                        "priority": 3 if closed else 0 if due else 1 if contrast["status"] == "ready" else 2})
    records.sort(key=lambda r: (r["priority"], r["review_date"] or "9999", r["id"]))
    return {"calc_version": CALC_VERSION, "as_of": ctx.as_of.isoformat(), "records": records,
            "summary": {"total_n": len(records), "due_n": sum(r["due"] and r["record_status"] == "open" for r in records),
                        "ready_n": sum(r["contrast"]["status"] == "ready" and r["record_status"] == "open" for r in records),
                        "pending_n": sum(r["contrast"]["status"] == "pending" and r["record_status"] == "open" for r in records)},
            "limitations": [LIMITATIONS_RETROSPECTIVE]}
