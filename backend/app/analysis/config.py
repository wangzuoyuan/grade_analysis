# 阈值配置（与metric-definitions.md保持同步）
PROGRESS_RANK_THRESHOLD = 80
VOLATILITY_RANK_THRESHOLD = 120
SUBJECT_PCT_THRESHOLD = 0.10

# 名次段定义（基于学籍排名）——以下为出厂默认值，运行时以数据库 AnalysisConfig 为准
HIGH_SCORE_RANGE = (1, 80)
CRITICAL_RANGE = (400, 500)
WEAK_RANGE = (501, 999999)


def get_band_config(db=None) -> dict:
    """读取用户自定义的段位阈值（全局单行）。无记录时回落到出厂默认。
    所有名次段计算（exam 详情 rank_bands、focus-list、chat 工具）都应调用这里，
    保证页面展示与 AI 问答口径一致。"""
    from app.db.models import SessionLocal, AnalysisConfig

    own = db is None
    if own:
        db = SessionLocal()
    try:
        cfg = db.query(AnalysisConfig).filter(AnalysisConfig.id == 1).first()
        return {
            "high_score_max": cfg.high_score_max if cfg else HIGH_SCORE_RANGE[1],
            "critical_min": cfg.critical_min if cfg else CRITICAL_RANGE[0],
            "critical_max": cfg.critical_max if cfg else CRITICAL_RANGE[1],
            "weak_min": cfg.weak_min if cfg else WEAK_RANGE[0],
        }
    finally:
        if own:
            db.close()

# 偏科阈值
SUBJECT_WEAKNESS_PCT_DIFF = 0.20

# 趋势标签
TREND_LABELS = {
    "stable_excellent": "稳定优秀",
    "significant_progress": "明显进步",
    "significant_regression": "明显退步",
    "volatile": "波动较大",
    "normal": "正常波动",
}