"""跨次趋势标签（旧 chat 工具 student_trend 的底层实现）。

P0-A1：进退步/波动判定一律走 app.analysis.definitions 的共享口径——
- 进退步 = 最近一次相对上一场有效名次的名次差（正数=进步），
  |差| ≥ PROGRESS_RANK_THRESHOLD(80) 判「明显进步/明显退步」；
  与新 /api/v1 重点关注端点同一口径（历史实现为首末两场差，已统一）。
- 波动 = 有效名次极差 ≥ VOLATILITY_RANK_THRESHOLD(120) 且 ≥3 场；
  volatility 字段即该极差（历史实现为名次标准差，已统一为极差，
  与新路径波动风险判定同一数值口径）。
- 名次缺失（缺考/未导入）的那一场不参与计算：不转 0、不残留上次名次、
  不伪造连续性；不足 2 场有效名次 → 「数据不足」。
"""

from app.analysis import definitions as defs
from app.analysis.config import TREND_LABELS

def compute_student_trend(student_id, total_type: str, exam_ids: list, db) -> dict:
    """计算学生趋势（基于名次时间序列）。

    student_id 既可传单个学号(str)，也可传同一人的多个学号集合
    (set/list/tuple)——跨学年场景下传 person_ids 合并后的全部学号，
    本函数会按考试时间(grade, exam_date)合并成一条时间线。"""
    # 从数据库获取该生的各次考试名次
    # 必须按考试时间（grade, exam_date）排序——exam_id 是上传顺序，与时间顺序无关，
    # 否则相邻名次差取的"上一次/最新"会错位，进退步判断随之出错。
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

    # 名次缺失的那一场直接不进时间线（缺考不残留上次名次、不转 0）
    ranks = [
        (s.exam_id, defs.resolve_year_rank(s.xueji_rank, s.grade_rank))
        for s in scores
    ]
    ranks = [(exam_id, rank) for exam_id, rank in ranks if rank is not None]
    if len(ranks) < 2:
        return {"trend_label": "数据不足", "ranks": ranks, "volatility": None}

    # 进退步（共享口径）：最近一次相对上一场
    previous_rank = ranks[-2][1]
    last_rank = ranks[-1][1]
    rank_change = previous_rank - last_rank  # 正数=进步

    # 波动性（共享口径）：有效名次极差，≥3 场才判波动风险
    rank_values = [r[1] for r in ranks]
    volatility = max(rank_values) - min(rank_values)

    # 判断趋势标签（共享口径：波动优先于进退步）
    if defs.volatility_issue(rank_values):
        trend_label = TREND_LABELS["volatile"]
    else:
        change_issue = defs.progress_issue(rank_change)
        if change_issue == defs.ISSUE_PROGRESS:
            trend_label = TREND_LABELS["significant_progress"]
        elif change_issue == defs.ISSUE_REGRESSION:
            trend_label = TREND_LABELS["significant_regression"]
        else:
            trend_label = TREND_LABELS["normal"]

    return {
        "trend_label": trend_label,
        "ranks": ranks,
        "rank_change": rank_change,
        "volatility": volatility,
    }
