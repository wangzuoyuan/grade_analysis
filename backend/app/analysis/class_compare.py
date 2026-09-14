def compute_class_compare(exam_id: int, class_num: int, db) -> dict:
    """计算本班 vs 平行班均分对比"""
    from app.db.models import ClassAverage, TotalScore, SubjectScore
    from sqlalchemy import func

    # 获取本班各科均分
    subject_avgs = db.query(
        SubjectScore.subject,
        func.avg(SubjectScore.raw_score).label("avg")
    ).filter(
        SubjectScore.exam_id == exam_id,
        SubjectScore.class_num == class_num
    ).group_by(SubjectScore.subject).all()

    # 获取平行班均分
    parallel_avgs = db.query(ClassAverage).filter(
        ClassAverage.exam_id == exam_id,
        ClassAverage.class_num != class_num
    ).all()

    return {
        "class_num": class_num,
        "subject_avgs": {s.subject: s.avg for s in subject_avgs},
        "parallel_classes": [{
            "class_num": p.class_num,
            "subject_averages": p.subject_averages,
        } for p in parallel_avgs],
    }