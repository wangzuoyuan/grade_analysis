"""Teaching attention: evidence-based selection, scoped configuration and follow-up.

No raw-score ranking, invented denominators, cross-grade comparison or causal claim.
"""
import hashlib
import json
from collections import Counter
from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_

from app.api import _queries as q
from app.api.homework import homework_warnings
from app.api.students_mgmt import _human_notes_filter, _note_or_404
from app.core.errors import InvalidScopeParam, LinkVersionConflict, ResourceOutOfScope
from app.db.models import HomeworkSetting
from app.db.workspace_models import AcademicYear, WsStudentNote
from app.diagnosis.student_homework import _catalog, _observation


class AttentionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    percentile_drop: float = Field(default=5, ge=0.1, le=100)
    grade_score_drop: float = Field(default=3, ge=0.1, le=100)
    target_top_percent: float | None = Field(default=None, ge=0, le=100)
    target_grade2: float | None = Field(default=None, ge=0, le=100)
    target_grade3: float | None = Field(default=None, ge=0, le=100)


class FollowUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_key: str = Field(min_length=1, max_length=64)
    measures: str = Field(min_length=1, max_length=2000)
    review_date: date


class FinishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result: str = Field(min_length=1, max_length=2000)


def settings_key(ctx):
    return f"teaching-attention:{ctx.teacher_id}:{ctx.academic_year_id}:{ctx.subject}"


def read_settings(db, ctx):
    row = db.get(HomeworkSetting, settings_key(ctx))
    return AttentionSettings.model_validate_json(row.value) if row and row.value else AttentionSettings()


def save_settings(db, ctx, settings):
    db.merge(HomeworkSetting(key=settings_key(ctx), value=settings.model_dump_json()))
    db.commit()
    return settings.model_dump()


def _key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _signal(code, category, title, detail, evidence, observed, severity):
    return dict(code=code, category=category, title=title, detail=detail,
                evidence=evidence, observed_on=observed, severity=severity,
                key=_key([code, evidence]))


def _display(point):
    if point["achievement"] is None:
        return "—"
    if point["basis"] == "grade_percentile":
        return f"年级前{point['percentile'] * 100:g}%"
    return f"{point['achievement']:g} 等级分"


def _learning_signals(points, settings):
    """Consecutive means adjacent exams; a missing point prevents bridging."""
    if not points or any(p["reason"] == "exact_date_missing" for p in points):
        return []
    latest = points[-1]
    if latest["achievement"] is None or latest["reason"]:
        return []
    grade = latest["grade"]
    threshold = settings.percentile_drop if grade == 1 else settings.grade_score_drop
    tail = []
    for p in reversed(points):
        if p["reason"] or p["achievement"] is None or p["grade"] != grade:
            break
        tail.insert(0, p)
        if len(tail) == 3:
            break
    evidence = [dict(exam_key=p["exam_key"], exam_name=p["exam_name"],
                     date=p["exam_date"], value=_display(p)) for p in tail]
    result = []
    drop = tail[-2]["achievement"] - latest["achievement"] if len(tail) >= 2 else 0
    net = tail[0]["achievement"] - latest["achievement"]
    continuous = (len(tail) == 3 and all(a["achievement"] > b["achievement"]
                  for a, b in zip(tail, tail[1:])) and net + 1e-8 >= threshold)
    unit = "个百分点" if grade == 1 else "个等级分"
    if continuous:
        result.append(_signal("continuous_decline", "learning", "持续下滑",
            f"连续两次下降，三场累计下降{net:g}{unit}",
            evidence, latest["exam_date"], net / threshold))
    elif drop + 1e-8 >= threshold:
        result.append(_signal("recent_decline", "learning", "明显下滑",
            f"较上次下降{drop:g}{unit}（提醒门槛{threshold:g}）",
            evidence[-2:], latest["exam_date"], drop / threshold))
    target = (100 - settings.target_top_percent if settings.target_top_percent is not None else None) if grade == 1 else (
        settings.target_grade2 if grade == 2 else settings.target_grade3)
    if target is not None and len(tail) == 3 and all(p["achievement"] < target for p in tail):
        label = f"年级前{settings.target_top_percent:g}%" if grade == 1 else f"{target:g}等级分"
        result.append(_signal("support", "support", "持续需要支持",
            f"连续三场低于设定的学科目标：{label}", evidence, latest["exam_date"], 1))
    return result


def _notes(db, ctx, ay):
    rows = db.query(WsStudentNote).filter(
        WsStudentNote.data_domain == "teaching",
        WsStudentNote.person_id.in_(ctx.member_person_ids),
        or_(WsStudentNote.subject_scope.is_(None), WsStudentNote.subject_scope == ctx.subject),
        WsStudentNote.date >= ay.start_date, WsStudentNote.date <= min(ctx.as_of, ay.end_date),
        _human_notes_filter(),
    ).order_by(WsStudentNote.id.desc()).all()
    result = []
    for note in rows:
        snapshot = note.baseline_value or {}
        if snapshot.get("source") == "attention-v1":
            if snapshot.get("academic_year_id") != ctx.academic_year_id:
                continue
            if not set(snapshot.get("class_ids", [])).intersection(ctx.class_ids):
                continue
        result.append(note)
    return result


def attention_summary(db, ctx):
    settings = read_settings(db, ctx)
    ay = db.get(AcademicYear, ctx.academic_year_id)
    classes, exams = _catalog(db, ctx, ctx.subject)
    # Unknown dates remain explicit gaps. Future dated exams are not current evidence.
    exams = [e for e in exams if e["date"] is None or e["date"] <= ctx.as_of]
    member_classes = {cid: set(q.members_at(db, "teaching", [cid], ctx.as_of)) for cid in classes}
    ids = list(ctx.member_person_ids)
    names, aliases = q.names_for(db, ids), q.aliases_for(db, ids, "teaching", ctx.academic_year_id)
    end = min(ctx.as_of, ay.end_date)
    start = max(ay.start_date, end - timedelta(days=29))
    warnings = {}
    if start <= end:
        warnings = homework_warnings(mode="teaching", academic_year_id=ctx.academic_year_id,
            teaching_class_id=ctx.class_ids[0] if len(ctx.class_ids) == 1 else None,
            subject=ctx.subject, min_missing=1, min_streak=2,
            date_from=start.isoformat(), date_to=end.isoformat(), db=db).model_dump()
    homework = {}
    for kind, code, title in (("students", "missing", "连续缺交"),
                               ("quality", "quality", "连续负面评价"),
                               ("forgot", "attendance", "忘带或出勤反复异常")):
        for warning in warnings.get(kind, []):
            pid = warning["person_id"]
            if pid not in ids:
                continue
            if kind == "students":
                count = warning["current_streak"]
                evidence = warning["recent_missing"]
                observed = evidence[0]["assigned_date"] if evidence else None
                detail = f"当前连续缺交{count}天（按天、不分作业种类）"
                # 2026-10-24 单账本口径：冲突日已按已交处理（打断连缺），
                # 单列天数提示人工核实两侧登记
                conflicts = warning.get("conflicts") or []
                if conflicts:
                    days_text = "、".join(c["date"] for c in conflicts[:3])
                    suffix = "等" if len(conflicts) > 3 else ""
                    detail += f"；另有{len(conflicts)}天两侧登记不一致（{days_text}{suffix}）待核实"
            else:
                count = warning["count"]
                evidence = [dict(date=d, detail=t) for d, t in zip(warning["dates"], warning["details"])]
                observed = max(warning["dates"], default=None)
                detail = f"{title}{count}次"
            homework.setdefault(pid, []).append(_signal(code, "homework", title, detail, evidence, observed, count))
    by_person = {}
    for n in _notes(db, ctx, ay):
        by_person.setdefault(n.person_id, []).append(n)
    students, gaps = [], []
    for pid in ids:
        relevant = [e for e in exams if pid in e["members"]]
        days = Counter(e["date"] for e in relevant if e["date"])
        points = []
        for exam in relevant:
            exam["window_days"] = 14
            p = _observation(exam, pid, [], None, bool(exam["date"] and days[exam["date"]] > 1))
            reason = p["pair_reason"]
            if reason == "insufficient_homework":
                reason = None  # score changes do not require a homework correlation
            points.append({**{k: p[k] for k in ("exam_key", "exam_name", "exam_date", "grade",
                          "basis", "achievement", "percentile")}, "reason": reason})
        identity = dict(person_id=pid, name=names.get(pid), alias=aliases.get(pid),
                        class_labels=[c.label for cid, c in classes.items() if pid in member_classes[cid]])
        if not points:
            gaps.append({**identity, "reason": "no_exams"})
        elif any(p["reason"] == "exact_date_missing" for p in points):
            gaps.append({**identity, "reason": "exact_date_missing"})
        elif points[-1]["reason"]:
            gaps.append({**identity, "reason": points[-1]["reason"]})
        elif len(points) < 2:
            gaps.append({**identity, "reason": "one_exam"})
        signals = _learning_signals(points, settings) + homework.get(pid, [])
        all_signals = list(signals)
        notes = by_person.get(pid, [])
        # New dashboard notes alone carry a captured evidence key. Other notes never silence alerts.
        captured = [n for n in notes if (n.baseline_value or {}).get("source") == "attention-v1"]
        for note in captured:
            baseline = note.baseline_value
            old = {s["code"]: s for s in baseline.get("signals", [])}
            def already_handled(s):
                prior = old.get(s["code"])
                return prior and (s["key"] == prior["key"] or (
                    s["code"] in ("missing", "quality", "attendance")
                    and (s["observed_on"] or "") <= (prior.get("observed_on") or "")
                    and s["severity"] <= prior.get("severity", 0)))
            signals = [s for s in signals if not already_handled(s)]
        open_notes = [n for n in notes if not n.follow_up_done and (
            n.status == "open" or (n.status is None and n.follow_up))]
        followups = []
        for n in open_notes:
            due = n.review_date is not None and n.review_date <= ctx.as_of
            baseline = n.baseline_value or {}
            followups.append(dict(id=n.id, problem=n.problem or n.content, measures=n.measures or n.follow_up,
                review_date=n.review_date.isoformat() if n.review_date else None, due=due,
                baseline=baseline.get("attention", {}), current=dict(
                    achievement=_display(points[-1]) if points else "—",
                    reasons=[s["detail"] for s in all_signals]),
                note="复查保留建档时的依据；只对照事实，由老师判断是否完成。"))
            if due:
                signals.append(_signal(f"review:{n.id}", "review", "到期复查",
                    n.problem or n.follow_up or "请回看跟进措施", [
                        dict(review_date=n.review_date.isoformat(), measures=n.measures or n.follow_up)],
                    n.review_date.isoformat(), (ctx.as_of - n.review_date).days))
        if not signals and not followups:
            continue
        categories = sorted(set(s["category"] for s in signals))
        learning = any(s["category"] == "learning" for s in signals)
        hw = any(s["category"] == "homework" for s in signals)
        priority = (0 if "review" in categories else 10 if learning and hw else
                    20 if any(s["code"] == "continuous_decline" for s in signals) else
                    30 if learning or hw else 40 if signals else 90)
        snapshot = dict(achievement=_display(points[-1]) if points else "—",
                        timeline=[dict(exam=p["exam_name"], date=p["exam_date"], value=_display(p)) for p in points[-3:]],
                        reasons=[s["detail"] for s in all_signals])
        students.append({**identity, "signals": signals, "categories": categories,
            "active": bool(signals), "priority": priority, "followups": followups,
            "timeline": points[-5:], "snapshot": snapshot,
            "evidence_key": _key([pid, ctx.academic_year_id, ctx.subject, all_signals, snapshot]),
            "capture_signals": all_signals,
            "suggestion": ("回看原定措施与基线，记录本次复查观察。" if priority == 0 else
                "核对最近错题与具体作业，记录一次针对性跟进。" if learning and hw else
                "回看近几场同科表现，核实变化后安排辅导。" if learning else
                "查看具体作业与日期，核实收交或评价情况。" if hw else "按约定日期复查学科支持措施。")})
    students.sort(key=lambda s: (s["priority"], -len(s["categories"]),
        -max((x["severity"] for x in s["signals"]), default=0),
        q.roster_sid_sort_key(s["alias"], None, s["name"], s["person_id"])))
    return dict(calc_version="teaching-attention-v1", as_of=ctx.as_of.isoformat(),
        academic_year_id=ctx.academic_year_id, subject=ctx.subject,
        window_start=start.isoformat(), window_end=end.isoformat(), settings=settings.model_dump(),
        students=students, gaps=gaps, counts=dict(
            all=sum(s["active"] for s in students),
            learning=sum("learning" in s["categories"] for s in students),
            homework=sum("homework" in s["categories"] for s in students),
            review=sum(bool(s["followups"]) for s in students),
            support=sum("support" in s["categories"] for s in students)),
        note="按明确事项选人；作业异常不构成成绩下降的因果解释。初始变化阈值可按教学需要调整。")


def create_attention_followup(db, ctx, pid, req):
    if pid not in ctx.member_person_ids:
        raise ResourceOutOfScope("学生不在当前教学范围")
    if not req.measures.strip() or req.review_date < ctx.as_of:
        raise InvalidScopeParam("请填写措施，复查日不得早于今天")
    summary = attention_summary(db, ctx)
    student = next((s for s in summary["students"] if s["person_id"] == pid), None)
    if not student or not student["active"] or student["evidence_key"] != req.evidence_key:
        raise LinkVersionConflict("关注依据已变化，请刷新名单后重新核对")
    # BEGIN IMMEDIATE is obtained by the router before reading the snapshot and duplicates.
    if student["followups"]:
        raise LinkVersionConflict("该生已有跟进中事项，请先在待复查中处理")
    snapshot = dict(source="attention-v1", academic_year_id=ctx.academic_year_id,
        class_ids=list(ctx.class_ids), subject=ctx.subject,
        evidence_key=req.evidence_key, signals=student["capture_signals"], attention=student["snapshot"])
    problem = "；".join(s["detail"] for s in student["signals"])
    note = WsStudentNote(data_domain="teaching", person_id=pid, date=ctx.as_of, category="观察",
        content=problem, problem=problem, subject_scope=ctx.subject, measures=req.measures.strip(),
        follow_up=req.measures.strip(), baseline_value=snapshot,
        start_date=ctx.as_of, review_date=req.review_date, status="open", follow_up_done=0)
    db.add(note)
    db.commit()
    return dict(id=note.id, status=note.status)


def finish_attention_followup(db, ctx, note_id, req):
    note = _note_or_404(db, "teaching", note_id, ctx)
    ay = db.get(AcademicYear, ctx.academic_year_id)
    if note not in _notes(db, ctx, ay):
        raise ResourceOutOfScope("跟进不在当前学年、学科或班级范围")
    if note.follow_up_done or note.status in ("done", "dismissed"):
        raise LinkVersionConflict("该跟进已关闭，请刷新")
    if not req.result.strip():
        raise InvalidScopeParam("请填写复查观察")
    note.content += f"\n复查（{ctx.as_of.isoformat()}）：{req.result.strip()}"
    note.status, note.follow_up_done = "done", 1
    from app.diagnosis.interventions import sync_legacy_edit
    from types import SimpleNamespace
    sync_legacy_edit(note, SimpleNamespace(model_fields_set={'status'}))
    db.commit()
    return dict(id=note.id, status="done")
