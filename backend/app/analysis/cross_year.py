def compute_cross_year_trend(student_id: str, db) -> dict:
    """计算跨学年趋势（只用主三门+语数英）"""
    from app.db.models import TotalScore, SubjectScore, Exam

    # 获取该生的所有考试（按年级分组）
    exams = db.query(Exam).join(TotalScore, Exam.id == TotalScore.exam_id).filter(
        TotalScore.student_id == student_id
    ).order_by(Exam.grade, Exam.exam_date).all()

    if not exams:
        return {"message": "无跨学年数据"}

    # 检测年级切换
    grades = set(e.grade for e in exams)
    has_cross_year = len(grades) > 1

    # 只返回主三门趋势（跨学年时排除五门/九门/+3/3+3）
    main_total_scores = db.query(TotalScore).join(Exam).filter(
        TotalScore.student_id == student_id,
        TotalScore.total_type == "主三门",
        Exam.grade.in_(grades)
    ).order_by(Exam.grade, Exam.exam_date).all()

    # 语文数学英语单科
    subject_scores = db.query(SubjectScore).join(Exam).filter(
        SubjectScore.student_id == student_id,
        SubjectScore.subject.in_(["语文", "数学", "英语"]),
        Exam.grade.in_(grades)
    ).order_by(Exam.grade, Exam.exam_date).all()

    return {
        "student_id": student_id,
        "has_cross_year": has_cross_year,
        "grades": list(grades),
        "main_total_trend": [{
            "exam_id": s.exam_id,
            "exam_name": exams[[e.id for e in exams].index(s.exam_id)].name if s.exam_id in [e.id for e in exams] else str(s.exam_id),
            "total_score": s.total_score,
            "xueji_rank": s.xueji_rank,
        } for s in main_total_scores],
        "subject_trend": [{
            "exam_id": s.exam_id,
            "subject": s.subject,
            "raw_score": s.raw_score,
            "grade_percentile": s.grade_percentile,
        } for s in subject_scores],
    }