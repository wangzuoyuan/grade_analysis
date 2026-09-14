from collections import Counter
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

router = APIRouter(tags=["analysis"])


class BandConfigPayload(BaseModel):
    high_score_max: int
    critical_min: int
    critical_max: int
    weak_min: int


@router.get("/rank-metrics")
async def get_rank_metrics(grade: int, mode: str = "frequency"):
    """返回指定年级在排名区间筛选/排名频次统计中可选的指标。"""
    from app.analysis.rank_metrics import rank_metric_options

    if mode not in {"range", "frequency"}:
        raise HTTPException(400, "mode 只能是 range 或 frequency")
    return {"grade": grade, "mode": mode, "metrics": rank_metric_options(grade, mode)}


@router.get("/rank-range")
async def get_rank_range(
    exam_id: int,
    metric: str,
    rank_min: int = 1,
    rank_max: int = 100,
    class_num: Optional[int] = None,
):
    """按单次考试、指标和年级排名区间筛选学生。"""
    from app.analysis.rank_metrics import rank_range_filter

    try:
        return rank_range_filter(
            exam_id=exam_id,
            metric=metric,
            rank_min=rank_min,
            rank_max=rank_max,
            class_num=class_num,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/rank-frequency")
async def get_rank_frequency(
    grade: int,
    metric: str,
    exam_ids: Optional[str] = None,
    class_num: Optional[int] = None,
    recent_count: int = 5,
):
    """按多场考试统计学生落入各排名/百分位/等级分区间的频次。"""
    from app.analysis.rank_metrics import rank_frequency_stats

    try:
        return rank_frequency_stats(
            grade=grade,
            metric=metric,
            exam_ids=exam_ids,
            class_num=class_num,
            recent_count=recent_count,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/analysis-config")
async def get_analysis_config():
    """返回当前重点关注段位阈值（供前端展示/编辑）。"""
    from app.analysis.config import get_band_config
    return get_band_config()


@router.put("/analysis-config")
async def update_analysis_config(payload: BandConfigPayload):
    """保存用户自定义的段位阈值（全局单行）。"""
    from app.db.models import SessionLocal, AnalysisConfig
    from datetime import datetime

    # 基本合法性校验：边界须为正、区间下界不大于上界
    if payload.high_score_max < 1 or payload.critical_min < 1 or payload.weak_min < 1:
        raise HTTPException(400, "排名阈值必须为正整数")
    if payload.critical_min > payload.critical_max:
        raise HTTPException(400, "临界段下界不能大于上界")

    db = SessionLocal()
    try:
        cfg = db.query(AnalysisConfig).filter(AnalysisConfig.id == 1).first()
        if not cfg:
            cfg = AnalysisConfig(id=1)
            db.add(cfg)
        cfg.high_score_max = payload.high_score_max
        cfg.critical_min = payload.critical_min
        cfg.critical_max = payload.critical_max
        cfg.weak_min = payload.weak_min
        cfg.updated_at = datetime.utcnow()
        db.commit()
        return {
            "high_score_max": cfg.high_score_max,
            "critical_min": cfg.critical_min,
            "critical_max": cfg.critical_max,
            "weak_min": cfg.weak_min,
        }
    finally:
        db.close()


@router.get("/band-trend")
async def get_band_trend(grade: int, class_num: Optional[int] = None):
    """某年级历次考试的三段（高分/临界/薄弱）人数趋势。
    class_num 为空时统计全年级；按当前 band_config 分段，改阈值后趋势同步变化。"""
    from app.db.models import SessionLocal, Exam, TotalScore, SubjectScore
    from app.analysis.config import get_band_config

    db = SessionLocal()
    try:
        cfg = get_band_config(db)
        # 按考试时间排序（exam_id 是上传顺序，不能用）
        exams = (
            db.query(Exam)
            .filter(Exam.grade == grade)
            .order_by(Exam.grade, Exam.exam_date, Exam.id)
            .all()
        )

        series = []
        # 该年级历次考试出现过的班号，供前端下拉
        class_set = set()
        for exam in exams:
            # 限定班级时先取本班学生集合
            if class_num is not None:
                sid_rows = (
                    db.query(SubjectScore.student_id)
                    .filter(SubjectScore.exam_id == exam.id, SubjectScore.class_num == class_num)
                    .distinct()
                    .all()
                )
                allowed = {r[0] for r in sid_rows}
            else:
                allowed = None

            cls_rows = (
                db.query(SubjectScore.class_num)
                .filter(SubjectScore.exam_id == exam.id, SubjectScore.class_num.isnot(None))
                .distinct()
                .all()
            )
            class_set.update(r[0] for r in cls_rows)

            totals = (
                db.query(TotalScore)
                .filter(TotalScore.exam_id == exam.id, TotalScore.total_type == "主三门")
                .all()
            )
            high = crit = weak = 0
            for t in totals:
                if allowed is not None and t.student_id not in allowed:
                    continue
                rank = t.xueji_rank or t.grade_rank
                if rank is None:
                    continue
                if 1 <= rank <= cfg["high_score_max"]:
                    high += 1
                if cfg["critical_min"] <= rank <= cfg["critical_max"]:
                    crit += 1
                if rank >= cfg["weak_min"]:
                    weak += 1

            series.append({
                "exam_id": exam.id,
                "exam_name": exam.name,
                "exam_date": exam.exam_date,
                "high_score": high,
                "critical": crit,
                "weak": weak,
            })

        return {
            "series": series,
            "band_config": cfg,
            "grade": grade,
            "class_num": class_num,
            "available_classes": sorted(class_set),
        }
    finally:
        db.close()

@router.get("/exams")
async def list_exams(grade: Optional[int] = None):
    """列出已建档考试 - Step 6"""
    from app.db.models import SessionLocal, Exam
    db = SessionLocal()
    query = db.query(Exam).order_by(Exam.exam_date.desc())
    if grade:
        query = query.filter(Exam.grade == grade)
    exams = query.all()
    db.close()
    return {
        "exams": [{
            "id": e.id,
            "name": e.name,
            "grade": e.grade,
            "semester": e.semester,
            "exam_date": e.exam_date,
            "exam_type": e.exam_type,
        } for e in exams]
    }

@router.delete("/exams/{exam_id}")
async def delete_exam(exam_id: int):
    """删除考试及其全部关联数据（学生分数、总分、班均分、上传记录）"""
    from app.db.models import (
        SessionLocal,
        Exam,
        Upload,
        SubjectScore,
        TotalScore,
        ClassAverage,
    )

    db = SessionLocal()
    try:
        exam = db.query(Exam).filter(Exam.id == exam_id).first()
        if not exam:
            raise HTTPException(404, "考试不存在")

        exam_name = exam.name
        subject_deleted = db.query(SubjectScore).filter(SubjectScore.exam_id == exam_id).delete(synchronize_session=False)
        total_deleted = db.query(TotalScore).filter(TotalScore.exam_id == exam_id).delete(synchronize_session=False)
        class_avg_deleted = db.query(ClassAverage).filter(ClassAverage.exam_id == exam_id).delete(synchronize_session=False)
        upload_deleted = db.query(Upload).filter(Upload.exam_id == exam_id).delete(synchronize_session=False)
        db.delete(exam)
        db.commit()
        return {
            "ok": True,
            "exam_id": exam_id,
            "exam_name": exam_name,
            "deleted": {
                "subject_score": subject_deleted,
                "total_score": total_deleted,
                "class_average": class_avg_deleted,
                "upload": upload_deleted,
            },
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"删除失败: {e}")
    finally:
        db.close()


@router.get("/exams/{exam_id}")
async def get_exam(exam_id: int):
    """获取考试详情 - Step 6"""
    from collections import Counter, defaultdict

    from app.db.models import SessionLocal, Exam, ClassAverage, SubjectScore, Teacher, TotalScore
    db = SessionLocal()
    exam = db.query(Exam).filter(Exam.id == exam_id).first()
    if not exam:
        db.close()
        raise HTTPException(404, "考试不存在")

    class_avgs = db.query(ClassAverage).filter(ClassAverage.exam_id == exam_id).all()

    # 获取本班学生统计
    main_totals = db.query(TotalScore).filter(
        TotalScore.exam_id == exam_id,
        TotalScore.total_type == "主三门"
    ).all()

    subject_rows = db.query(SubjectScore).filter(SubjectScore.exam_id == exam_id).all()

    students_by_id = {}
    class_counter_by_student = defaultdict(Counter)
    for row in subject_rows:
        student = students_by_id.setdefault(
            row.student_id,
            {
                "student_id": row.student_id,
                "name": row.name or row.student_id,
                "class_num": row.class_num,
                "xueji": row.xueji,
                "subject_scores": {},
                "subject_grade_scores": {},
                "subject_percentiles": {},
                "total_scores": {},
                "total_score": None,
                "grade_rank": None,
            },
        )
        if row.name:
            student["name"] = row.name
        if row.class_num is not None:
            class_counter_by_student[row.student_id][row.class_num] += 1
        if row.xueji is not None:
            student["xueji"] = row.xueji
        student["subject_scores"][row.subject] = row.raw_score
        student["subject_grade_scores"][row.subject] = row.grade_score
        student["subject_percentiles"][row.subject] = row.grade_percentile

    for student_id, counter in class_counter_by_student.items():
        if counter:
            students_by_id[student_id]["class_num"] = counter.most_common(1)[0][0]

    main_total_by_student = {t.student_id: t for t in main_totals}
    for student_id, total in main_total_by_student.items():
        student = students_by_id.setdefault(
            student_id,
            {
                "student_id": student_id,
                "name": student_id,
                "class_num": None,
                "xueji": None,
                "subject_scores": {},
                "subject_grade_scores": {},
                "subject_percentiles": {},
                "total_scores": {},
                "total_score": None,
                "grade_rank": None,
            },
        )
        student["total_score"] = total.total_score
        student["grade_rank"] = total.xueji_rank or total.grade_rank

    teacher = db.query(Teacher).first()
    target_class = None
    if teacher:
        target_class = {
            1: teacher.target_class_high1,
            2: teacher.target_class_high2,
            3: teacher.target_class_high3,
        }.get(exam.grade)

    all_students = list(students_by_id.values())
    if target_class is not None and any(s["class_num"] == target_class for s in all_students):
        stat_student_ids = {s["student_id"] for s in all_students if s["class_num"] == target_class}
    else:
        stat_student_ids = {s["student_id"] for s in all_students}

    all_totals = db.query(TotalScore).filter(TotalScore.exam_id == exam_id).all()
    for total in all_totals:
        student = students_by_id.setdefault(
            total.student_id,
            {
                "student_id": total.student_id,
                "name": total.student_id,
                "class_num": None,
                "xueji": None,
                "subject_scores": {},
                "subject_grade_scores": {},
                "subject_percentiles": {},
                "total_scores": {},
                "total_score": None,
                "grade_rank": None,
            },
        )
        student["total_scores"][total.total_type] = {
            "score": total.total_score,
            "rank": total.xueji_rank or total.grade_rank,
            "percentile": total.grade_percentile,
            "xueji_rank": total.xueji_rank,
            "grade_rank": total.grade_rank,
        }
        if total.total_type == "主三门":
            student["total_score"] = total.total_score
            student["grade_rank"] = total.xueji_rank or total.grade_rank

    stat_totals = [t for t in main_totals if t.student_id in stat_student_ids]
    stat_totals_by_type = defaultdict(list)
    for total in all_totals:
        if total.student_id in stat_student_ids:
            stat_totals_by_type[total.total_type].append(total)

    def summarize_totals(rows):
        scores = [t.total_score for t in rows if t.total_score is not None]
        ranks = [
            rank
            for rank in ((t.xueji_rank or t.grade_rank) for t in rows)
            if rank is not None
        ]
        return {
            "count": len(scores),
            "avg": round(sum(scores) / len(scores), 1) if scores else None,
            "max": max(scores) if scores else None,
            "min": min(scores) if scores else None,
            "rank_min": min(ranks) if ranks else None,
            "rank_max": max(ranks) if ranks else None,
        }

    stats_by_total_type = {
        total_type: summarize_totals(rows)
        for total_type, rows in sorted(stat_totals_by_type.items())
    }
    main_summary = summarize_totals(stat_totals)
    valid_scores = [t.total_score for t in stat_totals if t.total_score is not None]
    valid_ranks = [
        rank
        for rank in ((t.xueji_rank or t.grade_rank) for t in stat_totals)
        if rank is not None
    ]
    avg_score = sum(valid_scores) / len(valid_scores) if valid_scores else None

    from app.analysis.config import get_band_config
    band_cfg = get_band_config(db)
    rank_band_total_types = ["主三门"] if exam.grade == 1 else ["主三门", "3+3"]
    rank_bands_by_class = defaultdict(lambda: {"high_score": 0, "critical": 0, "weak": 0})
    for total in all_totals:
        if total.total_type not in rank_band_total_types:
            continue
        student = students_by_id.get(total.student_id)
        if not student:
            continue
        class_num = student.get("class_num")
        if class_num is None:
            continue
        rank = total.xueji_rank or total.grade_rank
        if rank is None:
            continue
        bands = rank_bands_by_class[(total.total_type, class_num)]
        if 1 <= rank <= band_cfg["high_score_max"]:
            bands["high_score"] += 1
        if band_cfg["critical_min"] <= rank <= band_cfg["critical_max"]:
            bands["critical"] += 1
        if rank >= band_cfg["weak_min"]:
            bands["weak"] += 1

    students = sorted(
        all_students,
        key=lambda s: (
            s["grade_rank"] is None,
            s["grade_rank"] if s["grade_rank"] is not None else 10**9,
            s["student_id"],
        ),
    )

    # 展示名优先主档规范姓名；SubjectScore.name 保留为原始上传快照，不改写
    from app.analysis.identity import display_names
    for sid, canonical in display_names(db, students_by_id.keys()).items():
        if sid in students_by_id:
            students_by_id[sid]["name"] = canonical
    students = [
        {**s, "name": students_by_id[s["student_id"]]["name"]} for s in students
    ]
    rank_bands = [
        {"total_type": total_type, "class_num": class_num, **bands}
        for (total_type, class_num), bands in sorted(rank_bands_by_class.items())
    ]

    distribution_total_types = (
        ["主三门", "五门", "九门"] if exam.grade == 1 else ["主三门", "+3", "3+3"]
    )
    distribution_rows = db.query(TotalScore).filter(
        TotalScore.exam_id == exam_id,
        TotalScore.total_type.in_(distribution_total_types),
    ).all()
    max_rank = max(
        (
            rank
            for rank in ((row.xueji_rank or row.grade_rank) for row in distribution_rows)
            if rank is not None
        ),
        default=0,
    )
    max_bucket = max(40, ((max_rank + 39) // 40) * 40)
    rank_distribution = [
        {"band": f"{start}-{start + 39}名次数", **{total_type: 0 for total_type in distribution_total_types}}
        for start in range(1, max_bucket + 1, 40)
    ]
    distribution_index = {
        item["band"]: item for item in rank_distribution
    }
    for row in distribution_rows:
        rank = row.xueji_rank or row.grade_rank
        if rank is None or rank < 1:
            continue
        start = ((rank - 1) // 40) * 40 + 1
        band = f"{start}-{start + 39}名次数"
        if band not in distribution_index:
            distribution_index[band] = {
                "band": band,
                **{total_type: 0 for total_type in distribution_total_types},
            }
            rank_distribution.append(distribution_index[band])
        distribution_index[band][row.total_type] = distribution_index[band].get(row.total_type, 0) + 1

    db.close()

    return {
        "exam": {
            "id": exam.id,
            "name": exam.name,
            "grade": exam.grade,
            "semester": exam.semester,
            "exam_date": exam.exam_date,
            "exam_type": exam.exam_type,
        },
        "class_averages": [{
            "class_num": c.class_num,
            "class_type": c.class_type,
            "teacher_name": c.teacher_name,
            "subject_averages": c.subject_averages,
            "total_averages": c.total_averages,
        } for c in class_avgs],
        "stats": {
            "total_students": len(valid_scores),
            "avg_main_total": round(avg_score, 1) if avg_score is not None else None,
            "max_total": max(valid_scores) if valid_scores else None,
            "min_total": min(valid_scores) if valid_scores else None,
            "rank_min": min(valid_ranks) if valid_ranks else None,
            "rank_max": max(valid_ranks) if valid_ranks else None,
            "by_total_type": stats_by_total_type,
            "main_total": main_summary,
        },
        "students": students,
        "rank_bands": rank_bands,
        "band_config": band_cfg,
        "rank_distribution": rank_distribution,
    }

@router.get("/focus-list/{exam_id}")
async def get_focus_list(exam_id: int, class_num: Optional[int] = None):
    """获取重点关注名单 - Step 5"""
    from app.db.models import SessionLocal, TotalScore, SubjectScore
    from app.analysis.config import SUBJECT_WEAKNESS_PCT_DIFF, get_band_config

    db = SessionLocal()
    band_cfg = get_band_config(db)

    # 基础查询：主三门成绩
    query = db.query(TotalScore).filter(
        TotalScore.exam_id == exam_id,
        TotalScore.total_type == "主三门"
    )

    # 班级筛选
    if class_num:
        student_ids_in_class = db.query(SubjectScore.student_id).filter(
            SubjectScore.exam_id == exam_id,
            SubjectScore.class_num == class_num
        ).distinct().all()
        student_ids_in_class = [s[0] for s in student_ids_in_class]
        query = query.filter(TotalScore.student_id.in_(student_ids_in_class))

    all_totals = query.all()

    focus_list = []

    for t in all_totals:
        student_id = t.student_id
        rank = t.xueji_rank or t.grade_rank or 9999

        # 获取该生各科成绩用于偏科检测
        subject_scores = db.query(SubjectScore).filter(
            SubjectScore.exam_id == exam_id,
            SubjectScore.student_id == student_id
        ).all()

        # 获取姓名
        name = student_id
        class_num_value = None
        for s in subject_scores:
            if s.name:
                name = s.name
            if class_num_value is None and s.class_num is not None:
                class_num_value = s.class_num
            if name != student_id and class_num_value is not None:
                break

        issues = []

        # 临界段（用户可自定义）
        if band_cfg["critical_min"] <= rank <= band_cfg["critical_max"]:
            issues.append("临界段")

        # 薄弱段（用户可自定义）
        if rank >= band_cfg["weak_min"]:
            issues.append("薄弱段")

        # 严重偏科检测（单科百分位 vs 主三门百分位差>=0.20）
        if t.grade_percentile is not None:
            main_pct = t.grade_percentile
            for ss in subject_scores:
                if ss.grade_percentile is not None:
                    diff = ss.grade_percentile - main_pct
                    if diff >= SUBJECT_WEAKNESS_PCT_DIFF:
                        issues.append(f"严重偏科({ss.subject})")

        if issues:
            focus_list.append({
                "student_id": student_id,
                "name": name,
                "class_num": class_num_value,
                "xueji_rank": rank,
                "total_score": t.total_score,
                "issues": issues,
            })

    # 按名次排序
    focus_list.sort(key=lambda x: x["xueji_rank"])

    db.close()
    return {"focus_list": focus_list[:50]}

@router.get("/students/{student_id}")
async def get_student(student_id: str):
    """获取学生画像（跨学年）- Step 5

    03 期改造：按「人」聚合。person_ids(db, student_id) 返回同一人的全部
    学段学号（无 alias 时退化为 {student_id}，行为与换届前一致）；过滤全部
    按 .in_(ids)。identity_of 返回 identity_id 时合并 ImportedHistory（手工
    导入的历史成绩，与排名/均分计算完全隔离），并在响应里带上 identity / aliases。
    """
    from app.db.models import (
        SessionLocal,
        TotalScore,
        SubjectScore,
        Exam,
        ImportedHistory,
        StudentIdentity,
        ClassRoster,
        Teacher,
    )
    from app.analysis.identity import person_ids, identity_of, aliases_of

    db = SessionLocal()
    try:
        # 同一人的全部学号（无 alias 退化为 {student_id}）
        ids = person_ids(db, student_id)
        iid = identity_of(db, student_id)

        # 花名册行（roster-only 学生：已建册尚无成绩，至少能安全展示姓名/作业/档案）
        roster_rows = (
            db.query(ClassRoster).filter(ClassRoster.student_id.in_(ids)).all()
        )

        # 获取该生所有考试（按年级分组）—— 同一人所有学号的考试并集
        exams = db.query(Exam).join(TotalScore, Exam.id == TotalScore.exam_id).filter(
            TotalScore.student_id.in_(ids)
        ).order_by(Exam.grade, Exam.exam_date).all()

        if not exams and iid is None and not roster_rows:
            raise HTTPException(404, "该学生无成绩记录")

        # roster-only 作用域：仅凭花名册可见的学生，行必须属于教师绑定的
        # grade+class（他班 roster-only 直接请求 → 404，不越权展示）；
        # 有成绩（或身份/导入历史）的画像维持既有跨学年行为不变。
        if not exams and roster_rows:
            t = db.query(Teacher).first()
            bound = (
                {
                    1: t.target_class_high1,
                    2: t.target_class_high2,
                    3: t.target_class_high3,
                }
                if t
                else {}
            )
            if not any(
                r.grade is not None and bound.get(r.grade) == r.class_num
                for r in roster_rows
            ):
                raise HTTPException(404, "该学生不在当前班级花名册中")

        # 年级集合并入合法花名册年级（有年级且有班级的正常行；缺陷行不算）：
        # 高二先建册后出分的学生 grades 含高二，顶层 class_num 取最高年级作用域，
        # 不出现「class_by_grade 有高二、顶层仍高一」的错位。
        grades = set(e.grade for e in exams)
        for r in roster_rows:
            if r.grade in (1, 2, 3) and r.class_num is not None:
                grades.add(r.grade)
        has_cross_year = len(grades) > 1

        # 主三门趋势（跨学年只取主三门）
        main_totals = db.query(TotalScore).filter(
            TotalScore.student_id.in_(ids),
            TotalScore.total_type == "主三门"
        ).order_by(TotalScore.exam_id).all()

        # 五门总分趋势（高一：语数英物化）
        five_totals = db.query(TotalScore).filter(
            TotalScore.student_id.in_(ids),
            TotalScore.total_type == "五门"
        ).order_by(TotalScore.exam_id).all()

        # 九门总分趋势（高一：九科固定口径）
        nine_totals = db.query(TotalScore).filter(
            TotalScore.student_id.in_(ids),
            TotalScore.total_type == "九门"
        ).order_by(TotalScore.exam_id).all()

        # +3 总分趋势（高二/高三用）
        plus3_totals = db.query(TotalScore).filter(
            TotalScore.student_id.in_(ids),
            TotalScore.total_type == "+3"
        ).order_by(TotalScore.exam_id).all()

        # 3+3 学籍排名趋势（高二/高三用）
        san3_totals = db.query(TotalScore).filter(
            TotalScore.student_id.in_(ids),
            TotalScore.total_type == "3+3"
        ).order_by(TotalScore.exam_id).all()

        # 返回全部单科成绩；学生详情页的历次明细需要展示加三学科。
        subject_scores = db.query(SubjectScore).filter(
            SubjectScore.student_id.in_(ids)
        ).order_by(SubjectScore.exam_id).all()

        # 姓名：优先 identity.display_name（人工确认），其次成绩录入的 name，
        # 最后回退花名册 name（roster-only 学生）
        name = student_id
        if iid is not None:
            ident = db.query(StudentIdentity).filter(StudentIdentity.id == iid).first()
            if ident and ident.display_name:
                name = ident.display_name
        if name == student_id:
            name_row = db.query(SubjectScore).filter(SubjectScore.student_id.in_(ids)).first()
            if name_row and name_row.name:
                name = name_row.name
        if name == student_id:
            for r in roster_rows:
                if r.name:
                    name = r.name
                    break

        # ── ImportedHistory 合并（仅展示，不参与任何排名/均分计算）──
        imported_rows = []
        if iid is not None:
            imported_rows = (
                db.query(ImportedHistory)
                .filter(ImportedHistory.identity_id == iid)
                .order_by(ImportedHistory.grade, ImportedHistory.exam_seq)
                .all()
            )

        # 构建考试ID到名称的映射
        exam_map = {e.id: e for e in exams}

        # 计算每场考试该生的主三门班级排名（按本班内 total_score 降序）
        # 先构建 (exam_id, student_id) -> class_num 映射
        student_class_by_exam: dict[int, int] = {}
        for s in subject_scores:
            if s.class_num is not None and s.exam_id not in student_class_by_exam:
                student_class_by_exam[s.exam_id] = s.class_num

        class_rank_by_exam: dict[int, int | None] = {}
        for t in main_totals:
            cls = student_class_by_exam.get(t.exam_id)
            if cls is None or t.total_score is None:
                class_rank_by_exam[t.exam_id] = None
                continue
            # 同班同考试的所有 student_id
            peer_ids = [
                row[0]
                for row in db.query(SubjectScore.student_id)
                .filter(SubjectScore.exam_id == t.exam_id, SubjectScore.class_num == cls)
                .distinct()
                .all()
            ]
            if not peer_ids:
                class_rank_by_exam[t.exam_id] = None
                continue
            peer_totals = (
                db.query(TotalScore.total_score)
                .filter(
                    TotalScore.exam_id == t.exam_id,
                    TotalScore.total_type == "主三门",
                    TotalScore.student_id.in_(peer_ids),
                    TotalScore.total_score.isnot(None),
                )
                .all()
            )
            peer_scores = [row[0] for row in peer_totals]
            # 排名 = 严格高于本人的人数 + 1
            class_rank_by_exam[t.exam_id] = sum(1 for s in peer_scores if s > t.total_score) + 1

        # 班级 / 学籍：取该生历次记录中出现最多的取值（前端头部展示与学籍徽章用）
        # 按年级分别取众数班级：class_by_grade[grade] = 该年级出现最多的 class_num
        class_by_grade: dict[int, int] = {}
        grade_class_counter: dict[int, Counter] = {}
        for s in subject_scores:
            if s.class_num is None or s.exam_id is None:
                continue
            exam = exam_map.get(s.exam_id)
            if exam is None or exam.grade is None:
                continue
            grade_class_counter.setdefault(exam.grade, Counter())[s.class_num] += 1
        for g, counter in grade_class_counter.items():
            if counter:
                class_by_grade[g] = counter.most_common(1)[0][0]
        # 花名册补位：该学号无成绩但已建册的年级（高二先建册后出分）
        for r in roster_rows:
            if r.grade is not None and r.class_num is not None and r.grade not in class_by_grade:
                class_by_grade[r.grade] = r.class_num
        # 标量班级（向后兼容）：取最高年级的班级；无成绩时回退花名册最高年级
        class_num_value = class_by_grade[max(grades)] if grades else None
        if class_num_value is None and roster_rows:
            for r in sorted(roster_rows, key=lambda r: (r.grade or 0), reverse=True):
                if r.class_num is not None:
                    class_num_value = r.class_num
                    break
        xueji_counter = Counter(s.xueji for s in subject_scores if s.xueji is not None)
        xueji_code_value = xueji_counter.most_common(1)[0][0] if xueji_counter else None

        # identity / aliases（aliases 的 class_num 派生：该学号首个非空 class_num）
        identity_aliases = []
        if iid is not None:
            for a in aliases_of(db, iid):
                cn_row = (
                    db.query(SubjectScore.class_num)
                    .filter(
                        SubjectScore.student_id == a.student_id,
                        SubjectScore.class_num.isnot(None),
                    )
                    .first()
                )
                identity_aliases.append({
                    "student_id": a.student_id,
                    "grade": a.grade,
                    "class_num": cn_row[0] if cn_row else None,
                })

        def exam_sort_key(exam_id):
            e = exam_map.get(exam_id)
            return (e.grade if e else 0, e.exam_date if e else "")

        main_totals_sorted = sorted(main_totals, key=lambda t: exam_sort_key(t.exam_id))
        five_totals_sorted = sorted(five_totals, key=lambda t: exam_sort_key(t.exam_id))
        nine_totals_sorted = sorted(nine_totals, key=lambda t: exam_sort_key(t.exam_id))
        plus3_totals_sorted = sorted(plus3_totals, key=lambda t: exam_sort_key(t.exam_id))
        san3_totals_sorted = sorted(san3_totals, key=lambda t: exam_sort_key(t.exam_id))
        subject_scores_sorted = sorted(subject_scores, key=lambda s: exam_sort_key(s.exam_id))
        subject_scores_with_score = [
            s for s in subject_scores_sorted if s.raw_score is not None or s.grade_score is not None
        ]

        def _exam_attr(exam_id, attr, fallback=None):
            e = exam_map.get(exam_id)
            return getattr(e, attr) if e is not None else fallback

        main_total_trend = [{
            "exam_id": t.exam_id,
            "exam_name": _exam_attr(t.exam_id, "name", str(t.exam_id) if t.exam_id else ""),
            "exam_date": _exam_attr(t.exam_id, "exam_date"),
            "grade": _exam_attr(t.exam_id, "grade"),
            "total_score": t.total_score,
            "xueji_rank": t.xueji_rank,
            "grade_percentile": t.grade_percentile,
            "class_rank": class_rank_by_exam.get(t.exam_id),
            "imported": False,
        } for t in main_totals_sorted]

        five_trend = [{
            "exam_id": t.exam_id,
            "exam_name": _exam_attr(t.exam_id, "name", str(t.exam_id) if t.exam_id else ""),
            "exam_date": _exam_attr(t.exam_id, "exam_date"),
            "grade": _exam_attr(t.exam_id, "grade"),
            "total_score": t.total_score,
            "xueji_rank": t.xueji_rank,
            "grade_percentile": t.grade_percentile,
            "imported": False,
        } for t in five_totals_sorted]

        nine_trend = [{
            "exam_id": t.exam_id,
            "exam_name": _exam_attr(t.exam_id, "name", str(t.exam_id) if t.exam_id else ""),
            "exam_date": _exam_attr(t.exam_id, "exam_date"),
            "grade": _exam_attr(t.exam_id, "grade"),
            "total_score": t.total_score,
            "xueji_rank": t.xueji_rank,
            "grade_percentile": t.grade_percentile,
            "imported": False,
        } for t in nine_totals_sorted]

        subject_trend = [{
            "exam_id": s.exam_id,
            "exam_name": _exam_attr(s.exam_id, "name", str(s.exam_id) if s.exam_id else ""),
            "exam_date": _exam_attr(s.exam_id, "exam_date"),
            "grade": _exam_attr(s.exam_id, "grade"),
            "subject": s.subject,
            "raw_score": s.raw_score,
            "grade_score": s.grade_score,
            "grade_percentile": s.grade_percentile,
            "imported": False,
        } for s in subject_scores_with_score]

        plus3_trend = [{
            "exam_id": t.exam_id,
            "exam_name": _exam_attr(t.exam_id, "name", str(t.exam_id) if t.exam_id else ""),
            "exam_date": _exam_attr(t.exam_id, "exam_date"),
            "grade": _exam_attr(t.exam_id, "grade"),
            "total_score": t.total_score,
            "xueji_rank": t.xueji_rank,
            "grade_percentile": t.grade_percentile,
            "imported": False,
        } for t in plus3_totals_sorted]

        san3_trend = [{
            "exam_id": t.exam_id,
            "exam_name": _exam_attr(t.exam_id, "name", str(t.exam_id) if t.exam_id else ""),
            "exam_date": _exam_attr(t.exam_id, "exam_date"),
            "grade": _exam_attr(t.exam_id, "grade"),
            "total_score": t.total_score,
            "xueji_rank": t.xueji_rank,
            "grade_percentile": t.grade_percentile,
            "imported": False,
        } for t in san3_totals_sorted]

        # ── ImportedHistory 合并到对应 trend 列表（导入点 exam_id=None，imported=True）──
        if imported_rows:
            for row in imported_rows:
                g = row.grade if row.grade is not None else 0
                label = row.exam_label or ""
                if row.kind == "total":
                    point = {
                        "exam_id": None,
                        "exam_name": label,
                        "exam_date": None,
                        "grade": g,
                        "total_score": row.raw_score,
                        "xueji_rank": row.xueji_rank,
                        "grade_percentile": row.grade_percentile,
                        "imported": True,
                    }
                    tt = row.total_type
                    if tt == "主三门":
                        point["class_rank"] = None
                        main_total_trend.append(point)
                    elif tt == "五门":
                        five_trend.append(point)
                    elif tt == "九门":
                        nine_trend.append(point)
                    elif tt == "+3":
                        plus3_trend.append(point)
                    elif tt == "3+3":
                        san3_trend.append(point)
                elif row.kind == "subject":
                    subject_trend.append({
                        "exam_id": None,
                        "exam_name": label,
                        "exam_date": None,
                        "grade": g,
                        "subject": row.subject,
                        "raw_score": row.raw_score,
                        "grade_score": row.grade_score,
                        "grade_percentile": row.grade_percentile,
                        "imported": True,
                    })

            def _sort_key(p):
                # 真实点按 (grade, exam_date)；导入点 exam_date=None 排在同年级真实点之后
                return (p.get("grade") or 0, p.get("exam_date") or "", 1 if p.get("imported") else 0)

            main_total_trend.sort(key=_sort_key)
            five_trend.sort(key=_sort_key)
            nine_trend.sort(key=_sort_key)
            subject_trend.sort(key=_sort_key)
            plus3_trend.sort(key=_sort_key)
            san3_trend.sort(key=_sort_key)

        return {
            "student_id": student_id,
            "name": name,
            "has_cross_year": has_cross_year,
            "grades": sorted(list(grades)),
            "class_num": class_num_value,
            "class_by_grade": class_by_grade,
            "xueji_code": xueji_code_value,
            "main_total_trend": main_total_trend,
            "five_trend": five_trend,
            "nine_trend": nine_trend,
            "subject_trend": subject_trend,
            "plus3_trend": plus3_trend,
            "san3_trend": san3_trend,
            "identity": {"id": iid, "aliases": identity_aliases},
        }
    finally:
        db.close()

@router.get("/students")
async def list_students(search: Optional[str] = Query(None)):
    """学生列表（按「人」去重）—— 03 期 §2.3。

    把所有学号按 identity 聚合：有 alias 的归到同一 identity（一桶多号），
    无 alias 的学号各成一桶。每桶挑一个代表学号（出现在最高年级、且最近
    考试的学号；roster-only 学号按花名册年级参与比较），返回 {student_id,
    name, current_grade, class_num, history, latest_exam_name,
    latest_main_score, latest_main_rank}。仅花名册的学生只纳入教师绑定的
    年级班级。?search= 按 name / student_id 服务端模糊匹配。
    """
    from collections import defaultdict
    from app.db.models import (
        SessionLocal,
        SubjectScore,
        Exam,
        TotalScore,
        Teacher,
        StudentIdentity,
        ClassRoster,
    )
    from app.analysis.identity import identity_of, aliases_of

    db = SessionLocal()
    try:
        # 收集所有 (student_id, name) —— 与原学生搜索口径一致（全部学生）
        rows = (
            db.query(SubjectScore.student_id, SubjectScore.name)
            .filter(SubjectScore.student_id.isnot(None))
            .distinct()
            .all()
        )

        # 服务端搜索：按 name 或 student_id 模糊
        if search:
            kw = f"%{search}%"
            rows = [
                (sid, nm)
                for (sid, nm) in rows
                if (nm and kw.replace("%", "") in nm)
                or (sid and search in sid)
            ]

        # 成绩学号全集（未过滤），用于判断花名册行是否 roster-only
        score_sid_set = {
            r[0]
            for r in db.query(SubjectScore.student_id)
            .filter(SubjectScore.student_id.isnot(None))
            .distinct()
            .all()
        }

        # 花名册补位：只有 ClassRoster、尚无成绩的学生（如高二先建册后出分）。
        # 已出现在成绩库的学号跳过；已链接身份的不跳过——其 roster 学号并入
        # 同一 identity 桶（先建册后关联高一的学生仍以当前班为代表，绝不消失）。
        # roster-only 行只纳入教师绑定的年级班级（他班/未绑定不入列，防越权）。
        # 搜索时同样按姓名/学号过滤，保证「按姓名搜索 roster-only 学生」可用。
        teacher_row = db.query(Teacher).first()
        bound_class: dict = (
            {
                1: teacher_row.target_class_high1,
                2: teacher_row.target_class_high2,
                3: teacher_row.target_class_high3,
            }
            if teacher_row
            else {}
        )
        roster_meta: dict[str, dict] = {}
        for r in db.query(ClassRoster).all():
            sid = r.student_id
            if sid is None or sid in score_sid_set or sid in roster_meta:
                continue
            if r.class_num is None and not r.name:
                continue  # 旧版缺陷行（student_id=姓名、无班级无名），待收编，不入列
            if r.grade is None or bound_class.get(r.grade) != r.class_num:
                continue  # 他班 / 教师未绑定的 roster-only 学生不入列
            if search:
                if not ((r.name and search in r.name) or (sid and search in sid)):
                    continue
            roster_meta[sid] = {"name": r.name, "grade": r.grade, "class_num": r.class_num}

        if not rows and not roster_meta:
            return []

        # name 取每个学号的第一个非空 name
        name_by_sid: dict[str, str] = {}
        for sid, nm in rows:
            if sid is None:
                continue
            if sid not in name_by_sid and nm:
                name_by_sid[sid] = nm
        all_sids = [sid for sid, _ in rows if sid is not None]
        # 去重学号
        all_sids = list(dict.fromkeys(all_sids))
        for sid in roster_meta:
            all_sids.append(sid)
            if roster_meta[sid]["name"]:
                name_by_sid[sid] = roster_meta[sid]["name"]

        # 按人分桶：iid 相同归一桶；无 iid 的学号用 ("sid", sid) 哨兵各成单桶
        buckets: dict = defaultdict(list)  # key -> [student_id, ...]
        sid_to_iid: dict[str, Optional[int]] = {}
        for sid in all_sids:
            iid = identity_of(db, sid)
            sid_to_iid[sid] = iid
            key = iid if iid is not None else ("sid", sid)
            buckets[key].append(sid)

        # 预取每个学号出现过的 (max_grade, latest_exam_date, latest_exam_id) ——
        # 通过 SubjectScore JOIN Exam 聚合，避免 N+1
        grade_info: dict[str, dict] = {}  # sid -> {max_grade, latest_date, latest_exam_id}
        agg_rows = (
            db.query(
                SubjectScore.student_id,
                Exam.grade,
                Exam.exam_date,
                Exam.id,
            )
            .join(Exam, Exam.id == SubjectScore.exam_id)
            .filter(SubjectScore.student_id.in_(all_sids))
            .all()
        )
        for sid, g, d, eid in agg_rows:
            info = grade_info.setdefault(
                sid, {"max_grade": None, "latest_date": None, "latest_exam_id": None}
            )
            cur_g = info["max_grade"]
            if g is not None and (cur_g is None or g > cur_g):
                info["max_grade"] = g
                # 换到更高年级时重置最近考试
                info["latest_date"] = d
                info["latest_exam_id"] = eid
            elif g is not None and cur_g is not None and g == cur_g:
                # 同年级内取最近考试
                if d is not None and (info["latest_date"] is None or d > info["latest_date"]):
                    info["latest_date"] = d
                    info["latest_exam_id"] = eid
            elif g is None and info["latest_exam_id"] is None:
                # 没有年级信息的兜底
                info["latest_date"] = d
                info["latest_exam_id"] = eid

        # 每个学号在其最高年级内的班级（首个非空 class_num）
        class_in_grade: dict[str, Optional[int]] = {}
        if all_sids:
            cn_rows = (
                db.query(SubjectScore.student_id, SubjectScore.class_num, Exam.grade)
                .join(Exam, Exam.id == SubjectScore.exam_id)
                .filter(
                    SubjectScore.student_id.in_(all_sids),
                    SubjectScore.class_num.isnot(None),
                )
                .all()
            )
            for sid, cn, g in cn_rows:
                mg = grade_info.get(sid, {}).get("max_grade")
                if mg is not None and g == mg:
                    if class_in_grade.get(sid) is None:
                        class_in_grade[sid] = cn

        # 每桶挑代表学号：(max_grade desc, latest_date desc) 最大的那个。
        # roster-only 学号没有成绩年级，用花名册年级参与比较：高二先建册后
        # 出分（即使已关联高一学号）的学生以高二 roster 学号为当前代表，
        # 旧学号进 history；current_grade/class_num 即高二目标班。
        def _rep_key(sid):
            info = grade_info.get(sid, {})
            m = roster_meta.get(sid) or {}
            return (
                info.get("max_grade") or m.get("grade") or 0,
                info.get("latest_date") or "",
            )

        results = []
        # 代表学号最近一场考试的主三门分数/排名
        rep_latest_exam_ids: set[int] = set()
        for key, sids in buckets.items():
            rep_sid = max(sids, key=_rep_key)
            info = grade_info.get(rep_sid, {})
            latest_exam_id = info.get("latest_exam_id")
            if latest_exam_id is not None:
                rep_latest_exam_ids.add(latest_exam_id)

        # 批量取代表学号最近考试主三门（一次查询）
        rep_totals: dict[tuple[str, int], TotalScore] = {}
        if rep_latest_exam_ids:
            ts_rows = (
                db.query(TotalScore)
                .filter(
                    TotalScore.exam_id.in_(rep_latest_exam_ids),
                    TotalScore.total_type == "主三门",
                )
                .all()
            )
            for t in ts_rows:
                rep_totals[(t.student_id, t.exam_id)] = t

        # 考试名映射
        exam_name_map: dict[int, str] = {}
        if rep_latest_exam_ids:
            for e in db.query(Exam).filter(Exam.id.in_(rep_latest_exam_ids)).all():
                exam_name_map[e.id] = e.name

        for key, sids in buckets.items():
            rep_sid = max(sids, key=_rep_key)
            info = grade_info.get(rep_sid, {})
            latest_exam_id = info.get("latest_exam_id")
            # roster-only 学生（尚无成绩）：年级/班级/姓名回退花名册，考试字段为 None
            meta = roster_meta.get(rep_sid) or {}
            current_grade = info.get("max_grade") or meta.get("grade")
            class_num = class_in_grade.get(rep_sid) or meta.get("class_num")
            name = name_by_sid.get(rep_sid) or meta.get("name") or rep_sid

            # identity display_name 优先（已链接时）
            iid = key if isinstance(key, int) else None
            if iid is not None:
                ident = db.query(StudentIdentity).filter(StudentIdentity.id == iid).first()
                if ident and ident.display_name:
                    name = ident.display_name

            latest_main_score = None
            latest_main_rank = None
            latest_exam_name = exam_name_map.get(latest_exam_id) if latest_exam_id else None
            ts = rep_totals.get((rep_sid, latest_exam_id)) if latest_exam_id else None
            if ts is not None:
                latest_main_score = ts.total_score
                latest_main_rank = ts.xueji_rank if ts.xueji_rank is not None else ts.grade_percentile

            # history：除代表外的其它学号
            history = []
            for sid in sids:
                if sid == rep_sid:
                    continue
                m = roster_meta.get(sid) or {}
                history.append({
                    "student_id": sid,
                    "grade": grade_info.get(sid, {}).get("max_grade") or m.get("grade"),
                    "class_num": class_in_grade.get(sid) or m.get("class_num"),
                })

            # 搜索过滤（针对桶内代表 name/sid；上面已对原始行过滤，这里再兜底）
            if search:
                hay = f"{name} {rep_sid}"
                if search not in hay:
                    continue

            results.append({
                "student_id": rep_sid,
                "name": name,
                "current_grade": current_grade,
                "class_num": class_num,
                "history": history,
                "latest_exam_name": latest_exam_name,
                "latest_main_score": latest_main_score,
                "latest_main_rank": latest_main_rank,
            })

        # 排序：current_grade desc, 然后按 name
        results.sort(key=lambda r: (-(r["current_grade"] or 0), r["name"] or ""))
        return results
    finally:
        db.close()

@router.get("/class/compare")
async def compare_classes(exam_id: Optional[int] = None):
    """班级对比 - Step 5"""
    from app.db.models import SessionLocal, Exam, ClassAverage, SubjectScore, TotalScore
    from sqlalchemy import func

    db = SessionLocal()

    exams_query = db.query(Exam).order_by(Exam.exam_date.desc())
    if exam_id:
        exams_query = exams_query.filter(Exam.id == exam_id)

    exams = exams_query.limit(10).all()

    result = []
    for e in exams:
        # 从 ClassAverage 表读取
        avgs = db.query(ClassAverage).filter(ClassAverage.exam_id == e.id).all()

        if avgs:
            classes = [{
                "class_num": a.class_num,
                "class_type": a.class_type,
                "main_total_avg": a.total_averages.get("主三门") if a.total_averages else None,
                "five_total_avg": (
                    a.total_averages.get("五门")
                    or a.total_averages.get("五门总分")
                    if a.total_averages
                    else None
                ),
                "nine_total_avg": (
                    a.total_averages.get("九门")
                    or a.total_averages.get("九门总分")
                    if a.total_averages
                    else None
                ),
                "plus3_avg": a.total_averages.get("+3") if a.total_averages else None,
                "total_avg": (
                    a.total_averages.get("3+3总分")
                    or a.total_averages.get("3+3")
                    if a.total_averages
                    else None
                ),
            } for a in avgs]
        else:
            # 图片/班均分表缺失时，直接从学生总分表按班级聚合，不能用单科均分粗算。
            student_classes = db.query(
                SubjectScore.student_id.label("student_id"),
                SubjectScore.class_num.label("class_num"),
            ).filter(
                SubjectScore.exam_id == e.id,
                SubjectScore.class_num.isnot(None),
            ).group_by(
                SubjectScore.student_id,
                SubjectScore.class_num,
            ).subquery()

            total_rows = db.query(
                student_classes.c.class_num,
                TotalScore.total_type,
                func.avg(TotalScore.total_score).label("avg_total"),
            ).join(
                TotalScore,
                (TotalScore.student_id == student_classes.c.student_id)
                & (TotalScore.exam_id == e.id),
            ).filter(
                TotalScore.total_type.in_(["主三门", "五门", "九门", "+3", "3+3"]),
            ).group_by(
                student_classes.c.class_num,
                TotalScore.total_type,
            ).all()

            by_class = {}
            for row in total_rows:
                entry = by_class.setdefault(
                    row.class_num,
                    {
                        "class_num": row.class_num,
                        "main_total_avg": None,
                        "five_total_avg": None,
                        "nine_total_avg": None,
                        "plus3_avg": None,
                        "total_avg": None,
                    },
                )
                if row.total_type == "主三门":
                    entry["main_total_avg"] = round(row.avg_total, 1) if row.avg_total is not None else None
                elif row.total_type == "五门":
                    entry["five_total_avg"] = round(row.avg_total, 1) if row.avg_total is not None else None
                elif row.total_type == "九门":
                    entry["nine_total_avg"] = round(row.avg_total, 1) if row.avg_total is not None else None
                elif row.total_type == "+3":
                    entry["plus3_avg"] = round(row.avg_total, 1) if row.avg_total is not None else None
                elif row.total_type == "3+3":
                    entry["total_avg"] = round(row.avg_total, 1) if row.avg_total is not None else None
            classes = [by_class[key] for key in sorted(by_class)]

        result.append({
            "exam_id": e.id,
            "exam_name": e.name,
            "grade": e.grade,
            "classes": classes,
        })

    db.close()
    return {"exams": result}

@router.get("/subject-weakness/{exam_id}")
async def subject_weakness(exam_id: int, class_num: Optional[int] = None):
    """单科薄弱名单 - Step 5"""
    from app.db.models import SessionLocal, SubjectScore, TotalScore
    from app.analysis.config import SUBJECT_WEAKNESS_PCT_DIFF

    db = SessionLocal()

    # 获取主三门百分位作为基准
    main_totals = db.query(TotalScore).filter(
        TotalScore.exam_id == exam_id,
        TotalScore.total_type == "主三门"
    ).all()

    main_pct_map = {t.student_id: t.grade_percentile for t in main_totals if t.grade_percentile is not None}

    # 获取所有单科成绩
    query = db.query(SubjectScore).filter(SubjectScore.exam_id == exam_id)
    if class_num:
        query = query.filter(SubjectScore.class_num == class_num)

    all_subjects = query.all()

    # 按学生分组
    student_subjects = {}
    for s in all_subjects:
        if s.student_id not in student_subjects:
            student_subjects[s.student_id] = []
        student_subjects[s.student_id].append(s)

    weakness_list = []

    for student_id, subjects in student_subjects.items():
        main_pct = main_pct_map.get(student_id)
        if main_pct is None:
            continue

        for s in subjects:
            if s.grade_percentile is not None:
                diff = s.grade_percentile - main_pct
                if diff >= SUBJECT_WEAKNESS_PCT_DIFF:
                    name = student_id
                    for sub in subjects:
                        if sub.name:
                            name = sub.name
                            break
                    weakness_list.append({
                        "student_id": student_id,
                        "name": name,
                        "subject": s.subject,
                        "raw_score": s.raw_score,
                        "grade_percentile": s.grade_percentile,
                        "diff": round(diff, 3),
                    })

    weakness_list.sort(key=lambda x: x["grade_percentile"])

    db.close()
    return {"subject_weakness": weakness_list[:50]}
