"""作业缺交统计服务层。

集中缺交看板/排行/预警/学生汇总/相关性等查询，供 homework/router.py 的
REST 端点和 chat/tools.py 的对话工具共同复用，避免逻辑重复。口径与原
Flask 应用一致：缺交有效记录 = remark 为空 且 subject != '全科'；默认排除
excluded=1 的学生（指定具体学生查询时不排除）。
"""

from collections import defaultdict
from datetime import date

from app.db.models import (
    ClassRoster,
    HomeworkCollection,
    HomeworkRecord,
    HomeworkSemester,
    SpecialRecord,
)
from app.homework.parser import SUBJECT_GROUPS, normalize_subject


def derive_semester(today=None):
    """未配置当前学期时按日期自动推算：9~1 月为第一学期，2~6 月为第二
    学期，7~8 月暑假沿用刚结束的第二学期。只计算不落库，跨年自动滚动。"""
    d = today or date.today()
    y, m = d.year, d.month
    if m >= 9:
        start, end, name = f"{y}-09-01", f"{y + 1}-01-31", f"{y}学年第一学期"
    elif m == 1:
        start, end, name = f"{y - 1}-09-01", f"{y}-01-31", f"{y - 1}学年第一学期"
    else:
        start, end, name = f"{y}-02-01", f"{y}-06-30", f"{y - 1}学年第二学期"
    return {
        "semester_id": None,
        "semester_start": start,
        "semester_end": end,
        "semester_name": name,
        "auto": True,
    }


def _current_semester_row(db):
    return (
        db.query(HomeworkSemester)
        .filter(HomeworkSemester.is_current == 1)
        .order_by(HomeworkSemester.id.desc())
        .first()
    )


def get_semester(db):
    current = _current_semester_row(db)
    if current:
        return {
            "semester_id": current.id,
            "semester_start": current.start_date,
            "semester_end": current.end_date,
            "semester_name": current.name,
            "auto": False,
        }
    return derive_semester()


def get_active_grade(db) -> int:
    """当前作业学年（单一来源：rollover.service.get_active_grade）。

    读 homework_setting.active_grade；缺省回落 max(class_roster.grade) ->
    max(Exam.grade) -> 1。homework 模块 lazy-import rollover 以避免循环，并
    保持全应用一处定义。
    """
    from app.rollover.service import get_active_grade as _gag
    return _gag(db)


def get_active_class_num(db, grade=None):
    """返回当前教师在指定年级绑定的行政班；未绑定返回 None。"""
    from app.db.models import Teacher

    grade = int(grade if grade is not None else get_active_grade(db))
    if grade not in (1, 2, 3):
        return None
    teacher = db.query(Teacher).first()
    if teacher is None:
        return None
    value = getattr(teacher, f"target_class_high{grade}", None)
    return int(value) if value is not None else None


def set_semester(db, data):
    current = _current_semester_row(db)
    derived = derive_semester()
    start = str(data.get("semester_start") or (current.start_date if current else derived["semester_start"]))
    end = str(data.get("semester_end") or (current.end_date if current else derived["semester_end"]))
    name = str(data.get("semester_name") or (current.name if current else "") or derived["semester_name"])
    if start > end:
        raise ValueError("学期开始日期不能晚于结束日期")
    if current:
        current.start_date, current.end_date, current.name = start, end, name
    else:
        current = HomeworkSemester(name=name, start_date=start, end_date=end, is_current=1)
        db.add(current)
    db.commit()
    return get_semester(db)


def list_semesters(db):
    items = [
        {
            "id": row.id,
            "name": row.name,
            "start_date": row.start_date,
            "end_date": row.end_date,
            "is_current": bool(row.is_current),
            "auto": False,
        }
        for row in db.query(HomeworkSemester)
        .order_by(HomeworkSemester.start_date.desc(), HomeworkSemester.id.desc()).all()
    ]
    if not any(item["is_current"] for item in items):
        derived = derive_semester()
        items.insert(0, {
            "id": None,
            "name": derived["semester_name"],
            "start_date": derived["semester_start"],
            "end_date": derived["semester_end"],
            "is_current": True,
            "auto": True,
        })
    return items


def add_semester(db, name, start_date, end_date, make_current=False):
    if start_date > end_date:
        raise ValueError("学期开始日期不能晚于结束日期")
    final_name = name.strip() or f"{start_date} 至 {end_date}"
    duplicate = db.query(HomeworkSemester).filter(
        HomeworkSemester.name == final_name,
        HomeworkSemester.start_date == start_date,
        HomeworkSemester.end_date == end_date,
    ).first()
    if duplicate:
        raise ValueError("同名同起止日期的学期已存在")
    if make_current:
        db.query(HomeworkSemester).update({HomeworkSemester.is_current: 0})
    row = HomeworkSemester(
        name=final_name,
        start_date=start_date,
        end_date=end_date,
        is_current=1 if make_current else 0,
    )
    db.add(row)
    db.commit()
    return list_semesters(db)


def set_current_semester(db, semester_id):
    row = db.query(HomeworkSemester).filter(HomeworkSemester.id == semester_id).first()
    if not row:
        return False
    db.query(HomeworkSemester).update({HomeworkSemester.is_current: 0})
    row.is_current = 1
    db.commit()
    return True


def excluded_names(db):
    rows = db.query(ClassRoster.name).filter(ClassRoster.excluded == 1).all()
    return {r[0] for r in rows}


def _subject_keywords(subject):
    for canonical_name, keywords in SUBJECT_GROUPS:
        if canonical_name == subject:
            return keywords
    return None


def _base_miss_query(db, start, end, student=None, subject=None,
                     respect_excluded=True, grade=None, class_num=None):
    """缺交有效记录基础查询（join 花名册），返回 (HomeworkRecord, ClassRoster)。

    年级收口：当 respect_excluded=True（即看板/排行/预警路径）时按 active_grade
    收口，避免高一+高二名册并存后看板串年级。grade 显式传入则覆盖 active_grade；
    grade=0 表示不收口（跨学年查询用）。student 模糊查询路径（respect_excluded
    被绕过）同样按 active_grade 收口。
    """
    if grade is None:
        grade = get_active_grade(db)
    active_grade = grade
    q = (
        db.query(HomeworkRecord, ClassRoster)
        .join(ClassRoster, ClassRoster.student_id == HomeworkRecord.student_id)
        .filter(
            (HomeworkRecord.remark.is_(None)) | (HomeworkRecord.remark == ""),
            HomeworkRecord.subject != "全科",
        )
    )
    if active_grade:
        q = q.filter(ClassRoster.grade == active_grade)
    if class_num is not None:
        q = q.filter(ClassRoster.class_num == int(class_num))
    if start and end:
        q = q.filter(HomeworkRecord.date >= start, HomeworkRecord.date <= end)
    if student:
        q = q.filter(ClassRoster.name.like(f"%{student}%"))
    elif respect_excluded:
        q = q.filter(ClassRoster.excluded == 0)
    if subject:
        keywords = _subject_keywords(subject)
        if keywords:
            from sqlalchemy import or_
            q = q.filter(or_(*[HomeworkRecord.subject.like(f"%{k}%") for k in keywords]))
        else:
            q = q.filter(HomeworkRecord.subject == subject)
    return q


def kpi(db, start, end, student=None, subject=None, class_num=None):
    rows = _base_miss_query(
        db, start, end, student, subject, class_num=class_num
    ).all()
    total = len(rows)

    subj_counts = defaultdict(int)
    stu_counts = defaultdict(int)
    for rec, roster in rows:
        subj_counts[normalize_subject(rec.subject)] += 1
        stu_counts[roster.name] += 1

    if subj_counts:
        worst_name, worst_count = max(subj_counts.items(), key=lambda x: x[1])
        worst_subject = {"name": worst_name, "count": worst_count}
    else:
        worst_subject = {"name": "无", "count": 0}

    top_students = [
        {"name": n, "count": c}
        for n, c in sorted(stu_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    ]
    return {
        "total_misses": total,
        "worst_subject": worst_subject,
        "top_students": top_students,
    }


def trend(db, start, end, student=None, subject=None, class_num=None):
    rows = _base_miss_query(
        db, start, end, student, subject, class_num=class_num
    ).all()
    by_date = defaultdict(int)
    for rec, _ in rows:
        by_date[rec.date] += 1
    dates = sorted(by_date)
    return {"dates": dates, "counts": [by_date[d] for d in dates]}


def subjects(db, start, end, student=None, subject=None, class_num=None):
    rows = _base_miss_query(
        db, start, end, student, subject, class_num=class_num
    ).all()
    totals = defaultdict(int)
    detail = defaultdict(lambda: defaultdict(int))
    for rec, roster in rows:
        canonical = normalize_subject(rec.subject)
        totals[canonical] += 1
        detail[canonical][roster.name] += 1
    out = []
    for name, count in sorted(totals.items(), key=lambda x: x[1], reverse=True):
        students = sorted(detail[name].items(), key=lambda x: x[1], reverse=True)
        out.append({
            "name": name,
            "value": count,
            "students": [{"name": s, "count": c} for s, c in students],
        })
    return out


def rankings(db, start, end, student=None, subject=None, limit=10, class_num=None):
    rows = _base_miss_query(
        db, start, end, student, subject, class_num=class_num
    ).all()
    counts = defaultdict(int)
    for _, roster in rows:
        counts[roster.name] += 1
    ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]
    return {
        "names": [n for n, _ in ranked],
        "counts": [c for _, c in ranked],
    }


def warnings(db, start, end, class_num=None):
    """同一学科「当前正在进行」的连续缺交预警。

    时间轴 = 该学科有缺交记录的日期 ∪ 收交台账日期（「数学：全交」这类
    全交日也进时间轴，缺-交-缺不再被误判为连续）；从最近一次收交向前回溯，
    统计某学生连续缺交了最近几次（必须含最后一次收交，否则视为已结束）。
    连续 2 次 → warning（黄），≥3 次 → serious（红）。排除 excluded 学生。
    """
    rows = _base_miss_query(
        db, start, end, respect_excluded=True, class_num=class_num
    ).all()

    timeline = defaultdict(set)            # subject -> {date}
    missed = defaultdict(set)             # (subject, student_id) -> {date}
    student_names = {}                    # student_id -> name
    for rec, roster in rows:
        subj = normalize_subject(rec.subject)
        timeline[subj].add(rec.date)
        missed[(subj, roster.student_id)].add(rec.date)
        student_names[roster.student_id] = roster.name

    # 收交台账并入时间轴，与缺交记录同 scope（active_grade / class_num）
    active_grade = get_active_grade(db)
    coll_q = db.query(HomeworkCollection).filter(
        HomeworkCollection.date >= start,
        HomeworkCollection.date <= end,
    )
    if active_grade:
        coll_q = coll_q.filter(HomeworkCollection.grade == active_grade)
    if class_num is not None:
        coll_q = coll_q.filter(HomeworkCollection.class_num == int(class_num))
    for coll in coll_q.all():
        timeline[normalize_subject(coll.subject)].add(coll.date)

    serious, warning = [], []
    for (subj, student_id), miss_dates in missed.items():
        axis = sorted(timeline[subj])
        streak = []
        for d in reversed(axis):
            if d in miss_dates:
                streak.append(d)
            else:
                break
        if len(streak) < 2:
            continue
        streak.reverse()
        item = {
            "name": student_names[student_id],
            "student_id": student_id,
            "subject": subj,
            "streak": len(streak),
            "dates": streak,
        }
        (serious if item["streak"] >= 3 else warning).append(item)

    sort_key = lambda x: (-x["streak"], x["name"])
    serious.sort(key=sort_key)
    warning.sort(key=sort_key)
    students = {i["student_id"] for i in serious + warning}
    return {
        "serious": serious,
        "warning": warning,
        "counts": {
            "serious": len(serious),
            "warning": len(warning),
            "students": len(students),
        },
    }


def student_summary(db, student_id=None, name=None, class_num=None):
    """单个学生作业概况（跨学年可视）：缺交总数、按科目分布、迟到/请假次数、
    当前连续缺交预警。姓名多义时返回候选。

    缺交/特殊记录按「同一人」全部学号聚合（person_ids），跨学年可见——换届后
    班主任从高二学号打开卡片也能看到该生高一的缺交历史。展示表头用 active_grade
    名册行（找不到时回退任一别名学号的名册行）。当前连续缺交预警仅当前学年
    （warnings 本身按 active_grade 收口，streak 是当前学期的）。"""
    from app.analysis.identity import person_ids

    roster_q = db.query(ClassRoster)
    if student_id:
        roster_q = roster_q.filter(ClassRoster.student_id == student_id)
    elif name:
        roster_q = roster_q.filter(ClassRoster.name.like(f"%{name}%"))
    else:
        return {"error": "需提供 student_id 或 name"}
    matches = roster_q.limit(10).all()
    if not matches:
        return {"error": "未在作业花名册中找到该学生", "student_id": student_id, "name": name}
    if len(matches) > 1 and not student_id:
        return {
            "error": "匹配到多个学生，请指定学号",
            "candidates": [{"student_id": m.student_id, "name": m.name} for m in matches],
        }
    roster = matches[0]

    # 同一人全部学号（无 alias 时退化为 {student_id}，与换届前一致）
    active_grade = get_active_grade(db)
    ids = person_ids(db, roster.student_id)

    # 展示表头：优先 active_grade 名册行，回退任一别名学号的名册行
    header = (
        db.query(ClassRoster)
        .filter(ClassRoster.student_id.in_(ids), ClassRoster.grade == active_grade)
        .first()
    )
    if header is None:
        header = roster

    sem = get_semester(db)
    start, end = sem["semester_start"], sem["semester_end"]

    miss_rows = (
        db.query(HomeworkRecord)
        .filter(
            HomeworkRecord.student_id.in_(ids),
            (HomeworkRecord.remark.is_(None)) | (HomeworkRecord.remark == ""),
            HomeworkRecord.subject != "全科",
            HomeworkRecord.date >= start,
            HomeworkRecord.date <= end,
        )
        .all()
    )
    by_subject = defaultdict(int)
    for r in miss_rows:
        by_subject[normalize_subject(r.subject)] += 1

    special_rows = (
        db.query(SpecialRecord)
        .filter(
            SpecialRecord.student_id.in_(ids),
            SpecialRecord.date >= start,
            SpecialRecord.date <= end,
        )
        .all()
    )
    special_counts = defaultdict(int)
    for s in special_rows:
        special_counts[s.type] += 1

    # 该生当前连续缺交预警（warnings 已按 active_grade 收口，仅当前学年 streak）
    all_warn = warnings(db, start, end, class_num=class_num)
    student_warnings = [
        w for w in (all_warn["serious"] + all_warn["warning"])
        if w.get("student_id") in ids or w["name"] == roster.name
    ]

    recent_records = [
        {"date": r.date, "subject": normalize_subject(r.subject), "content": r.content or ""}
        for r in sorted(miss_rows, key=lambda r: r.date, reverse=True)[:10]
    ]

    return {
        "student": {
            "student_id": header.student_id,
            "name": header.name,
            "class_num": header.class_num,
            "excluded": bool(header.excluded),
        },
        "semester": sem,
        "total_misses": len(miss_rows),
        "miss_by_subject": dict(sorted(by_subject.items(), key=lambda x: x[1], reverse=True)),
        "special_counts": dict(special_counts),
        "active_warnings": student_warnings,
        "recent_records": recent_records,
        "note": "作业数据仅含缺交、请假、迟到等负面信号，不代表作业完成质量。",
    }


# 9 门学考学科（按学科相关性用，排除"全科"）
ACADEMIC_SUBJECTS = ("语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理")


def _pearson(xs, ys):
    """皮尔逊相关系数；样本<3 或任一方差为 0 返回 None。"""
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return round(sxy / (sxx ** 0.5 * syy ** 0.5), 4)


def _latest_exam_id(db, grade=None):
    from app.db.models import Exam, TotalScore
    query = (
        db.query(Exam)
        .join(TotalScore, TotalScore.exam_id == Exam.id)
    )
    if grade is not None:
        query = query.filter(Exam.grade == int(grade))
    exam = query.order_by(Exam.exam_date.desc(), Exam.id.desc()).first()
    return exam.id if exam else None


def _exam_id_for_active_grade(db, exam_id, active_grade):
    from app.db.models import Exam

    if exam_id is None:
        return _latest_exam_id(db, grade=active_grade)
    exam = db.query(Exam).filter(Exam.id == exam_id).first()
    if exam is None:
        raise ValueError("考试不存在")
    if exam.grade != active_grade:
        raise ValueError("所选考试不属于当前年级")
    return exam.id


def grade_correlation(db, class_num, exam_id=None, total_type="主三门",
                      start=None, end=None, subject=None):
    """班级「缺交 × 成绩」相关性数据。

    - 总分模式（subject 为空）：X=学期总缺交次数，Y=该次考试 total_type 学籍排名。
    - 单科模式（subject 非空）：X=该科学期缺交次数，Y=该科年级百分位（越小越靠前）。
    """
    from app.db.models import SubjectScore, TotalScore

    if not start or not end:
        sem = get_semester(db)
        start, end = sem["semester_start"], sem["semester_end"]
    active_grade = get_active_grade(db)
    exam_id = _exam_id_for_active_grade(db, exam_id, active_grade)
    roster = {
        r.student_id: r
        for r in db.query(ClassRoster).filter(
            ClassRoster.class_num == class_num, ClassRoster.grade == active_grade
        ).all()
    }

    # 学期内缺交次数（subject 非空时只算该科）；_base_miss_query 已按 active_grade 收口
    miss_rows = _base_miss_query(
        db, start, end, subject=subject, respect_excluded=True,
        class_num=class_num,
    ).all()
    miss_by_sid = defaultdict(int)
    for rec, _ in miss_rows:
        miss_by_sid[rec.student_id] += 1

    if subject:
        scores = {}
        if exam_id is not None:
            for s in db.query(SubjectScore).filter(
                SubjectScore.exam_id == exam_id,
                SubjectScore.subject == subject,
            ).all():
                scores[s.student_id] = s
        rows = []
        for sid, roster_row in roster.items():
            if roster_row.excluded:
                continue
            s = scores.get(sid)
            rows.append({
                "student_id": sid,
                "name": roster_row.name,
                "miss_count": miss_by_sid.get(sid, 0),
                "grade_percentile": s.grade_percentile if s else None,
                "raw_score": s.raw_score if s else None,
            })
        rows.sort(key=lambda x: x["miss_count"], reverse=True)
        return {
            "class_num": class_num,
            "exam_id": exam_id,
            "subject": subject,
            "y_field": "grade_percentile",
            "y_label": "年级百分位",
            "semester": {"start": start, "end": end},
            "rows": rows,
            "note": f"miss_count 为学期内{subject}缺交次数；grade_percentile 越小越靠前。{subject}缺交多且百分位大（成绩差）即落在重点关注象限。作业数据仅反映缺交，不代表完成质量。",
        }

    # 总分模式
    totals = {}
    if exam_id is not None:
        for t in db.query(TotalScore).filter(
            TotalScore.exam_id == exam_id,
            TotalScore.total_type == total_type,
        ).all():
            totals[t.student_id] = t
    rows = []
    for sid, roster_row in roster.items():
        if roster_row.excluded:
            continue
        t = totals.get(sid)
        rows.append({
            "student_id": sid,
            "name": roster_row.name,
            "miss_count": miss_by_sid.get(sid, 0),
            "xueji_rank": t.xueji_rank if t else None,
            "grade_rank": t.grade_rank if t else None,
            "total_score": t.total_score if t else None,
        })
    rows.sort(key=lambda x: x["miss_count"], reverse=True)
    return {
        "class_num": class_num,
        "exam_id": exam_id,
        "total_type": total_type,
        "subject": None,
        "y_field": "xueji_rank",
        "y_label": "学籍排名",
        "semester": {"start": start, "end": end},
        "rows": rows,
        "note": "miss_count 为学期内缺交次数；xueji_rank 越小越靠前。缺交多且排名靠后即落在「高缺交+低排名」象限，值得重点关注。作业数据仅反映缺交，不代表完成质量。",
    }


def subject_correlation_ranking(db, class_num, exam_id=None, start=None, end=None):
    """各学科「缺交次数 × 该科年级百分位」皮尔逊相关排序。

    r>0 表示该科缺交越多、百分位越大（成绩越差），即"缺交越拖成绩"。
    仅纳入有该科百分位、非 excluded 的学生（缺交为 0 也计入）。按 r 降序。
    """
    from app.db.models import SubjectScore

    if not start or not end:
        sem = get_semester(db)
        start, end = sem["semester_start"], sem["semester_end"]
    active_grade = get_active_grade(db)
    exam_id = _exam_id_for_active_grade(db, exam_id, active_grade)
    roster_rows = db.query(ClassRoster).filter(
        ClassRoster.class_num == class_num,
        ClassRoster.grade == active_grade,
    ).all()
    roster_ids = {r.student_id for r in roster_rows}
    excluded = {r.student_id for r in roster_rows if r.excluded == 1}

    # 一次取全学期缺交，按 (科目, 学生) 计数；_base_miss_query 已按 active_grade 收口
    miss_rows = _base_miss_query(
        db, start, end, respect_excluded=True, class_num=class_num
    ).all()
    miss_by_subj_sid = defaultdict(lambda: defaultdict(int))
    for rec, _ in miss_rows:
        miss_by_subj_sid[normalize_subject(rec.subject)][rec.student_id] += 1

    results = []
    for subject in ACADEMIC_SUBJECTS:
        score_rows = []
        if exam_id is not None:
            score_rows = db.query(SubjectScore).filter(
                SubjectScore.exam_id == exam_id,
                SubjectScore.subject == subject,
                SubjectScore.student_id.in_(roster_ids),
            ).all()
        miss_map = miss_by_subj_sid.get(subject, {})
        xs, ys = [], []
        for s in score_rows:
            if s.student_id not in roster_ids or s.student_id in excluded or s.grade_percentile is None:
                continue
            xs.append(miss_map.get(s.student_id, 0))
            ys.append(s.grade_percentile)
        r = _pearson(xs, ys)
        results.append({"subject": subject, "r": r, "n": len(xs)})

    # r 降序；None（样本不足）排末尾
    results.sort(key=lambda x: (x["r"] is None, -(x["r"] or 0)))
    return {
        "class_num": class_num,
        "exam_id": exam_id,
        "semester": {"start": start, "end": end},
        "rankings": results,
        "note": "r 为皮尔逊相关系数，正值越大表示该科缺交越多、年级百分位越大（成绩越差），即缺交越拖该科成绩；n 为样本数，n<3 记 r=null。作业数据仅反映缺交，不代表完成质量。",
    }


def weekly_focus(db, class_num=None, today=None):
    """本周关注名单：合并连续缺交预警、本周缺交激增、最近一次考试临界/薄弱/
    偏科、谈话跟进待办。主要由缺交信号驱动，不依赖新考试。"""
    from datetime import date, timedelta

    from app.db.models import StudentNote

    if class_num is None:
        class_num = get_active_class_num(db)
    if class_num is None:
        raise ValueError("当前年级尚未绑定班级")

    sem = get_semester(db)
    start, end = sem["semester_start"], sem["semester_end"]
    today = today or date.today().isoformat()
    week_start = (date.fromisoformat(today) - timedelta(days=6)).isoformat()

    active_grade = get_active_grade(db)
    roster = {
        r.student_id: r
        for r in db.query(ClassRoster).filter(
            ClassRoster.class_num == class_num, ClassRoster.excluded == 0,
            ClassRoster.grade == active_grade,
        ).all()
    }
    reasons = defaultdict(list)  # student_id -> [理由标签]

    def add(sid, tag, weight):
        if sid in roster:
            reasons[sid].append({"tag": tag, "weight": weight})

    # ① 连续缺交预警（取每生最高连续次数）
    w = warnings(db, start, end, class_num=class_num)
    best = {}
    for item in w["serious"] + w["warning"]:
        sid = item.get("student_id")
        if sid and (sid not in best or item["streak"] > best[sid]["streak"]):
            best[sid] = item
    for sid, item in best.items():
        sev = 3 if item["streak"] >= 3 else 2
        add(sid, f"连续缺交{item['streak']}次（{item['subject']}）", sev)

    # ② 本周缺交激增
    miss_rows = _base_miss_query(
        db, start, end, respect_excluded=True, class_num=class_num
    ).all()
    total_by_sid = defaultdict(int)
    week_by_sid = defaultdict(int)
    for rec, _ in miss_rows:
        total_by_sid[rec.student_id] += 1
        if week_start <= rec.date <= today:
            week_by_sid[rec.student_id] += 1
    weeks_elapsed = max(1, (date.fromisoformat(min(today, end)) - date.fromisoformat(start)).days / 7)
    for sid, wk in week_by_sid.items():
        avg = total_by_sid[sid] / weeks_elapsed
        if wk >= 3 and wk >= 2 * max(avg, 0.5):
            add(sid, f"本周缺交激增（{wk}次）", 2)

    # ③ 最近一次考试的临界/薄弱/偏科（复用 focus_list 口径，过滤到本班）
    try:
        from app.chat.tools import focus_list
        from app.db.models import Exam, TotalScore
        latest = (
            db.query(Exam).join(TotalScore, TotalScore.exam_id == Exam.id)
            .filter(Exam.grade == active_grade)
            .order_by(Exam.exam_date.desc(), Exam.id.desc()).first()
        )
        if latest:
            for row in focus_list(latest.id):
                sid = row.get("student_id")
                if sid in roster and row.get("issues"):
                    add(sid, "、".join(row["issues"]), 1)
    except Exception:
        pass

    # ④ 谈话跟进待办
    note_rows = (
        db.query(StudentNote)
        .filter(StudentNote.follow_up.isnot(None), StudentNote.follow_up_done == 0)
        .all()
    )
    for n in note_rows:
        if n.student_id in roster:
            add(n.student_id, f"谈话跟进待办：{n.follow_up}", 2)

    out = []
    for sid, items in reasons.items():
        out.append({
            "student_id": sid,
            "name": roster[sid].name,
            "score": sum(i["weight"] for i in items),
            "reasons": [i["tag"] for i in sorted(items, key=lambda x: -x["weight"])],
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return {
        "class_num": class_num,
        "week": {"start": week_start, "end": today},
        "students": out,
        "note": "合并连续缺交预警、本周缺交激增、最近考试临界/薄弱/偏科、谈话跟进待办。主要由缺交信号驱动，无新考试也每天更新。",
    }
