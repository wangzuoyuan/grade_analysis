"""Personal, descriptive longitudinal homework/achievement analysis. No writes.

See docs/contracts/student-homework-analysis.md for denominators and limits.
"""
import math
from collections import Counter
from datetime import timedelta

from app.analysis.definitions import normalized_percentile
from app.api import _queries as q
from app.api import homework as hw
from app.core.errors import InvalidScopeParam, ResourceOutOfScope
from app.db.workspace_models import AdministrativeClass, TeachingClass, TeachingExamScope
from app.diagnosis.correlation import _as_context, _pearson, _spearman

MIN_EXAMS = 6
MIN_HOMEWORK = 3
MIN_EVALUATIONS = 3


def _number(value):
    return float(value) if value is not None and math.isfinite(float(value)) else None


def _rounded(value):
    return round(value, 4) if value is not None else None


def _correlation(pairs):
    """Six is an operational guardrail, never a significance claim."""
    n = len(pairs)
    result = {"n": n, "r": None, "rho": None, "reason": None,
              "rho_without_one": None, "sensitive": False}
    if n < MIN_EXAMS:
        result["reason"] = "insufficient_exams"
        return result
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    if len(set(xs)) < 2:
        result["reason"] = "homework_unchanged"
        return result
    if len(set(ys)) < 2:
        result["reason"] = "achievement_unchanged"
        return result
    result.update(r=_rounded(_pearson(xs, ys)), rho=_rounded(_spearman(xs, ys)))
    leave_one = [_spearman(xs[:i] + xs[i + 1:], ys[:i] + ys[i + 1:]) for i in range(n)]
    values = [v for v in leave_one if v is not None]
    if values:
        lo, hi = min(values), max(values)
        result["rho_without_one"] = [_rounded(lo), _rounded(hi)]
        result["sensitive"] = (len(values) != n or lo <= 0 <= hi or hi - lo > .5)
    return result


def _homework_events(db, ctx, subject, homework_type):
    """Read projected snapshots once per batch, never once per student/exam."""
    events = []
    for a, mapping in hw._visible_assignments(db, ctx, active_only=True):
        if a.subject == "考勤" or a.subject != subject:
            continue
        if homework_type and a.homework_type != homework_type:
            continue
        # A legacy reconstructed roster is not a confirmed denominator here.
        known = bool(hw._parse_expected_ids(a.expected_members_json))
        expected = set(hw._projected_expected(db, a, mapping)) if known else (
            set(mapping.values()) if mapping is not None else set(q.members_at(db, ctx.mode, [a.class_ref_id], a.assigned_date)))
        rows = dict((pid, row) for row, pid in hw._projected_submissions(db, a, mapping))
        events.append((a.assigned_date, known, expected, rows))
    return events


def _window(events, pid, start, end):
    counts = dict(submitted=0, missing=0, eligible=0, excused=0, ignored=0,
                  negative=0, evaluated=0, unknown_batches=0)
    days = set()
    for assigned, known, expected, rows in events:
        if not start <= assigned < end:
            continue
        if not known:
            # Batch count, not an invented personal denominator.
            if pid in expected:
                counts["unknown_batches"] += 1
            continue
        if pid not in expected:
            continue
        row = rows.get(pid)
        status = hw._effective_status(row) if row is not None else "submitted"
        if status == "excused":
            counts["excused"] += 1
            continue
        if row is not None and (hw._attendance_of(row.evaluation) or hw._forgot_of(row.evaluation)):
            counts["ignored"] += 1
            continue
        days.add(assigned)
        counts["eligible"] += 1
        if status == "missing":
            counts["missing"] += 1
        else:
            counts["submitted"] += 1
            evaluation = hw._quality_text(row.evaluation) if row is not None else ""
            if evaluation:
                counts["evaluated"] += 1
                if hw._evaluation_tone(evaluation) == "negative":
                    counts["negative"] += 1
    return {**counts, "days": len(days),
            "submission_rate": _rounded(counts["submitted"] / counts["eligible"]) if counts["eligible"] else None,
            "negative_rate": _rounded(counts["negative"] / counts["evaluated"]) if counts["evaluated"] else None,
            "evaluation_coverage": _rounded(counts["evaluated"] / counts["submitted"]) if counts["submitted"] else None}


def _catalog(db, ctx, subject):
    model = TeachingClass if ctx.mode == "teaching" else AdministrativeClass
    classes = {row.id: row for row in db.query(model).filter(model.id.in_(ctx.class_ids)).all()}
    platform = {row.id: row for row in db.query(TeachingExamScope).filter(
        TeachingExamScope.academic_year_id == ctx.academic_year_id,
        TeachingExamScope.subject == subject).all()} if ctx.mode == "teaching" else {}
    grouped = {}
    membership_cache = {}
    for entry in q.readable_facts(db, ctx):
        f = entry.fact
        if f.subject != subject or f.total_type is not None:
            continue
        cls = classes.get(entry.class_ref_id)
        class_grade = cls.grade if cls is not None else None
        exam = platform.get(f.teaching_exam_id)
        exam_grade = exam.grade if exam else class_grade
        conflict = exam is not None and class_grade is not None and exam_grade != class_grade
        # IDs keep same-name platform exams in different grades separate.
        precision = f.exam_date_precision or "day"
        day = f.exam_date if precision == "day" else None
        if day is not None:
            membership_key = (entry.class_ref_id, day)
            if membership_key not in membership_cache:
                membership_cache[membership_key] = set(q.members_at(db, ctx.mode, [entry.class_ref_id], day))
            if entry.person_id not in membership_cache[membership_key]:
                continue
        key = f"platform:{exam.id}" if exam else f"legacy:{f.exam_name}:{day or f.source_exam_date}:{exam_grade}"
        bucket = grouped.setdefault(key, dict(key=key, exam_name=f.exam_name, date=day,
            grade=exam_grade, grade_conflict=False, date_conflict=False, classes=set(), facts={}))
        bucket["grade_conflict"] |= conflict
        bucket["date_conflict"] |= day != bucket["date"] or (exam is not None and day is not None and day != exam.exam_date)
        bucket["classes"].add(entry.class_ref_id)
        bucket["facts"].setdefault(entry.person_id, f)
    # Per-class historical membership: an empty history never falls back to today.
    for exam in grouped.values():
        day = exam["date"]
        allowed = set()
        if day is not None:
            for class_id in exam["classes"]:
                key = (class_id, day)
                if key not in membership_cache:
                    membership_cache[key] = set(q.members_at(db, ctx.mode, [class_id], day))
                allowed.update(membership_cache[key])
        else:
            # Undated records remain visible as missing-date evidence, not pairs.
            allowed = set(exam["facts"])
        exam["members"] = allowed.intersection(ctx.member_person_ids)
    return classes, sorted(grouped.values(), key=lambda e: (e["date"] is None, e["date"] or ctx.as_of, e["key"]))


def _observation(exam, pid, events, previous_day, same_day):
    day, grade = exam["date"], exam["grade"]
    fact = exam["facts"].get(pid)
    raw = _number(fact.score) if fact else None
    basis = "grade_percentile" if grade == 1 else "grade_score" if grade in (2, 3) else None
    percentile = _number(fact.grade_percentile) if fact else None
    value = None
    reason = None
    if grade not in (1, 2, 3):
        reason = "grade_missing"
    elif exam["grade_conflict"]:
        reason = "grade_conflict"
    elif raw is None:
        reason = "score_missing"
    elif grade == 1:
        if percentile is not None and 0 <= percentile <= 100:
            percentile = normalized_percentile(percentile)
            value = 100 * (1 - percentile)
        else:
            reason = "percentile_missing"
    else:
        value = _number(fact.grade_score)
        if value is None:
            reason = "grade_score_missing"
    if day is None or exam["date_conflict"]:
        reason = "exact_date_missing"
    elif same_day:
        reason = "same_day_exams"
    window_day = day if not exam["date_conflict"] else None
    start = max(window_day - timedelta(days=exam["window_days"]), previous_day + timedelta(days=1)) if window_day and previous_day else (
        window_day - timedelta(days=exam["window_days"]) if window_day else None)
    homework = _window(events, pid, start, day) if start is not None else None
    if reason is None and (homework is None or homework["eligible"] < MIN_HOMEWORK):
        reason = "insufficient_homework"
    if reason is None and homework["unknown_batches"] > homework["eligible"]:
        reason = "denominator_unknown_over_half"
    return dict(exam_key=exam["key"], exam_name=exam["exam_name"], exam_date=day.isoformat() if day else None,
        grade=grade, basis=basis, raw_score=raw, percentile=_rounded(percentile) if grade == 1 else None,
        achievement=_rounded(value), window_start=start.isoformat() if start else None,
        window_end=(day - timedelta(days=1)).isoformat() if day and start is not None and start < day else None,
        homework=homework, pair_reason=reason, gain=None, submission_change=None)


def _summarize(pid, name, alias, labels, timeline, uncertain_order=False):
    level, gain, quality = [], [], []
    previous = None
    unordered = uncertain_order or any(p["exam_date"] is None for p in timeline)
    for point in timeline:
        h = point["homework"]
        # No bridging over missing exams, uncertain dates, or a grade change.
        if (not unordered and previous is not None and point["achievement"] is not None
                and previous["achievement"] is not None and point["exam_date"] and previous["exam_date"]
                and point["exam_date"] > previous["exam_date"]
                and point["grade"] == previous["grade"] and point["basis"] == previous["basis"]
                and point["pair_reason"] not in ("same_day_exams", "exact_date_missing", "grade_conflict")
                and previous["pair_reason"] not in ("same_day_exams", "exact_date_missing", "grade_conflict")):
            point["gain"] = _rounded(point["achievement"] - previous["achievement"])
            prev_h = previous["homework"]
            if h and prev_h and h["submission_rate"] is not None and prev_h["submission_rate"] is not None:
                point["submission_change"] = _rounded(h["submission_rate"] - prev_h["submission_rate"])
        if point["pair_reason"] is None:
            level.append((h["submission_rate"], point["achievement"]))
            if point["gain"] is not None:
                gain.append((h["submission_rate"], point["gain"]))
            if h["evaluated"] >= MIN_EVALUATIONS and h["evaluation_coverage"] >= .5:
                quality.append((h["negative_rate"], point["achievement"]))
        previous = point
    grades = {p["grade"] for p in timeline if p["grade"] is not None}
    mixed = len(grades) > 1
    correlations = {key: _correlation(pairs) for key, pairs in
                    (("submission", level), ("progress", gain), ("negative", quality))}
    if mixed:
        for result in correlations.values():
            result.update(r=None, rho=None, reason="mixed_grades", rho_without_one=None, sensitive=False)
    caveats = []
    if unordered:
        caveats.append("部分考试日期不完整，无法确认相邻顺序，暂不计算较上次进步。")
    if len(level) < 10:
        caveats.append("有效考试较少，结果仅供初步观察；6场是展示门槛，不代表统计显著。")
    if level and max(x for x, _ in level) - min(x for x, _ in level) < .1:
        caveats.append("提交率集中，难以区分变化；请结合明确评价与作业内容观察。")
    if level and len({x for x, _ in level}) < 3:
        caveats.append("提交率不同取值少，并列较多，相关方向容易受少数考试影响。")
    if len(quality) < MIN_EXAMS:
        caveats.append("明确评价覆盖不足的窗口未参与评价关联；没有评价不等于作业质量好。")
    if any(p["homework"] and p["homework"]["unknown_batches"] for p in timeline):
        caveats.append("有应交快照不明的批次被排除；提交率只代表已知分母部分。")
    if any(r["sensitive"] for r in correlations.values()):
        caveats.append("删去一场考试后相关方向或大小明显变化，暂不据此判断稳定关系。")
    latest = next((p for p in reversed(timeline) if p["exam_date"]), None)
    if not level:
        advice = "先核对考试指标与考前收交记录，补齐缺失后再观察个人趋势。"
    elif correlations["submission"]["reason"] == "homework_unchanged" or max(x for x, _ in level) - min(x for x, _ in level) < .1:
        advice = "收交率变化很少，优先记录订正、准确性等明确评价，再结合考试变化回看。"
    elif latest and latest["gain"] is not None and latest["gain"] < 0:
        advice = "回看最近一次考试的错题与作业内容，区分完成、理解和迁移；记录跟进后继续观察。"
    else:
        advice = "结合逐场变化选择一次具体跟进，持续记录后再回看；暂不把同向变化解释为作业带来的提分。"
    return dict(person_id=pid, name=name, alias=alias, class_labels=labels, timeline=timeline,
                correlations=correlations, latest_change=latest, paired_n=len(level),
                excluded_reasons=dict(Counter(p["pair_reason"] for p in timeline if p["pair_reason"])),
                advice=advice, caveats=caveats)


def student_homework_analysis(db, scope, subject=None, window_days=14,
                              homework_type=None, person_id=None, end_exam_name=None, grade=None):
    ctx = _as_context(scope)
    if window_days not in (14, 30) or isinstance(window_days, bool):
        raise InvalidScopeParam("window_days 必须是14或30")
    if grade is not None and grade not in (1, 2, 3):
        raise InvalidScopeParam("grade 必须是1、2或3")
    if ctx.mode == "teaching":
        subject = ctx.subject
    subject = (subject or "").strip()
    if not subject or subject == "考勤":
        raise InvalidScopeParam("请选择作业和成绩的同一学科")
    if person_id is not None and person_id not in ctx.member_person_ids:
        raise ResourceOutOfScope("person not in current scope")
    classes, all_exams = _catalog(db, ctx, subject)
    exams = [e for e in all_exams if grade is None or e["grade"] == grade]
    unordered_people = set().union(*(e["members"] for e in exams if e["date"] is None or e["date_conflict"]))
    cutoff_note = ""
    if end_exam_name:
        matches = [e for e in all_exams if e["exam_name"] == end_exam_name]
        if len(matches) > 1 and grade is not None:
            matches = [e for e in matches if e["grade"] == grade]
        if not matches:
            raise ResourceOutOfScope("截止考试不在当前学科和班级范围")
        if len(matches) > 1:
            raise InvalidScopeParam("截止考试同名，请先选择明确年级和班级")
        anchor = matches[0]
        if anchor["date"] is None or anchor["date_conflict"]:
            # Without a day, ordering earlier exams is unknowable; show this one.
            exams = [e for e in exams if e["key"] == anchor["key"]]
            cutoff_note = "截止考试日期不完整或冲突，无法确定此前范围，本次只保留截止场明细。"
        else:
            exams = [e for e in exams if e["date"] is not None and e["date"] <= anchor["date"]]
    events = _homework_events(db, ctx, subject, homework_type)
    ids = [person_id] if person_id is not None else list(ctx.member_person_ids)
    names = q.names_for(db, ids)
    aliases = q.aliases_for(db, ids, ctx.mode, ctx.academic_year_id)
    # Current roster classes for labels/filtering, never inferred from names.
    members_by_class = {cid: set(q.members_at(db, ctx.mode, [cid], ctx.as_of)) for cid in classes}
    students = []
    for pid in ids:
        relevant = [e for e in exams if pid in e["members"]]
        own_classes = [c for cid, c in classes.items() if pid in members_by_class[cid]]
        if grade is not None and not relevant and not any(c.grade == grade for c in own_classes):
            continue
        days = Counter(e["date"] for e in relevant if e["date"] is not None)
        timeline = []
        prev_by_grade = {}
        for exam in relevant:
            exam["window_days"] = window_days
            previous_day = prev_by_grade.get(exam["grade"])
            timeline.append(_observation(exam, pid, events, previous_day, days[exam["date"]] > 1))
            if exam["date"] is not None and not exam["date_conflict"]:
                prev_by_grade[exam["grade"]] = exam["date"]
        students.append(_summarize(pid, names.get(pid), aliases.get(pid), [c.label for c in own_classes], timeline,
                                   uncertain_order=pid in unordered_people))
    students.sort(key=lambda s: q.roster_sid_sort_key(s["alias"], None, s["name"], s["person_id"]))
    return dict(calc_version="student-homework-v1", mode=ctx.mode, academic_year_id=ctx.academic_year_id,
                subject=subject, window_days=window_days, end_exam_name=end_exam_name,
                minimum_exams=MIN_EXAMS, minimum_homework=MIN_HOMEWORK, students=students,
                note="高一用年级百分位（越小越靠前），计算系数时转换为100−前百分比；高二、高三用等级分。"
                     "不含考试当日与考后作业，窗口不重复使用上一场考前作业。"
                     "相关性仅描述该学生的统计共变，不构成因果结论或提分保证；时间趋势、考试内容和记录偏差仍可能影响结果。" + cutoff_note)
