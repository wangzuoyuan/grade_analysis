from app.analysis.config import (
    PROGRESS_RANK_THRESHOLD,
    VOLATILITY_RANK_THRESHOLD,
    SUBJECT_PCT_THRESHOLD,
    SUBJECT_WEAKNESS_PCT_DIFF,
    HIGH_SCORE_RANGE,
    CRITICAL_RANGE,
    WEAK_RANGE,
    TREND_LABELS,
)

def compute_student_trend(student_id, total_type: str, exam_ids: list, db) -> dict:
    """计算学生趋势（基于名次时间序列）。

    student_id 既可传单个学号(str)，也可传同一人的多个学号集合
    (set/list/tuple)——跨学年场景下传 person_ids 合并后的全部学号，
    本函数会按考试时间(grade, exam_date)合并成一条时间线。"""
    # 从数据库获取该生的各次考试名次
    # 必须按考试时间（grade, exam_date）排序——exam_id 是上传顺序，与时间顺序无关，
    # 否则下方 ranks[0]/ranks[-1] 取的"最早/最新"会错位，进退步判断随之出错。
    from app.db.models import TotalScore, Exam

    if isinstance(student_id, (set, list, tuple)):
        ids = set(student_id)
    else:
        ids = {student_id}

    scores = db.query(TotalScore).join(Exam, Exam.id == TotalScore.exam_id).filter(
        TotalScore.student_id.in_(ids),
        TotalScore.total_type == total_type,
        TotalScore.exam_id.in_(exam_ids)
    ).order_by(Exam.grade, Exam.exam_date, Exam.id).all()

    if not scores:
        return {"trend_label": "无数据", "ranks": [], "volatility": None}

    ranks = [(s.exam_id, s.xueji_rank or s.grade_rank) for s in scores if s.xueji_rank or s.grade_rank]
    if len(ranks) < 2:
        return {"trend_label": "数据不足", "ranks": ranks, "volatility": None}

    # 计算进退步
    first_rank = ranks[0][1]
    last_rank = ranks[-1][1]
    rank_change = first_rank - last_rank  # 正数=进步

    # 波动性
    rank_values = [r[1] for r in ranks]
    avg_rank = sum(rank_values) / len(rank_values)
    variance = sum((r - avg_rank) ** 2 for r in rank_values) / len(rank_values)
    volatility = variance ** 0.5

    # 判断趋势标签
    if volatility > VOLATILITY_RANK_THRESHOLD:
        trend_label = TREND_LABELS["volatile"]
    elif rank_change >= PROGRESS_RANK_THRESHOLD:
        trend_label = TREND_LABELS["significant_progress"]
    elif rank_change <= -PROGRESS_RANK_THRESHOLD:
        trend_label = TREND_LABELS["significant_regression"]
    else:
        trend_label = TREND_LABELS["normal"]

    return {
        "trend_label": trend_label,
        "ranks": ranks,
        "rank_change": rank_change,
        "volatility": volatility,
    }