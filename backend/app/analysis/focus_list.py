from app.analysis.config import SUBJECT_WEAKNESS_PCT_DIFF, CRITICAL_RANGE, WEAK_RANGE

def build_focus_list(exam_id: int, class_num: int, db) -> list:
    """生成重点关注名单"""
    from app.db.models import SubjectScore, TotalScore
    from sqlalchemy import and_

    # 获取临界段学生（学籍排名400-500）
    critical_students = db.query(TotalScore.student_id).filter(
        TotalScore.exam_id == exam_id,
        TotalScore.total_type == "主三门",
        TotalScore.xueji_rank >= CRITICAL_RANGE[0],
        TotalScore.xueji_rank <= CRITICAL_RANGE[1]
    ).all()

    # 获取薄弱段学生（学籍排名>500）
    weak_students = db.query(TotalScore.student_id).filter(
        TotalScore.exam_id == exam_id,
        TotalScore.total_type == "主三门",
        TotalScore.xueji_rank > WEAK_RANGE[0]
    ).all()

    # 获取严重偏科学生
    # （偏科判定：单科年级百分位比主三门百分位差>=0.20）
    # 具体实现待完成

    return {
        "critical": [s[0] for s in critical_students],
        "weak": [s[0] for s in weak_students],
    }