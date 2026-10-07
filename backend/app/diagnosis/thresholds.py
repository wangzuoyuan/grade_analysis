"""P1 诊断阈值常量（契约 docs/diagnosis-roadmap/p1-contracts.md §4，B1 建、B2/B3 import）。

命名与默认值照抄契约 §4；改动阈值不改 calc_version（§0.5）。
B2（类型引擎）/B3（变化分解）一律从本模块 import，不得自带副本；
下方「B1 附加常量」是 §2 输出形状需要、但 §4 清单未列出的版本化标注，
随本模块一并发布，B2/B3 可直接复用。
"""

# ── 契约 §4 常量（名称与默认值照抄） ──────────────────────────────

STABILITY_WINDOW_N = 5            # 稳定性窗口场次
STABILITY_MIN_POINTS = 3
STABILITY_RANGE_LABELS = ((0.10, "稳定"), (0.25, "中等波动"))  # 极差>0.25→高波动
TREND_DIRECTION_MIN_CHANGE = 20   # 名次变化≥20 名才算 进步/退步 方向
HOMEWORK_RISK_30D = 3             # 30 天缺交次数阈值
HOMEWORK_RISK_STREAK_DAYS = 2     # 当前连缺天数阈值
IMBALANCE_MIN_CONSECUTIVE = 2     # 偏科连续场数
CHANGE_DECOMPOSITION_TOP_N = 3    # B3 主要变化科目数
CLASS_GROUP_MIN_SIZE = 3          # B3 班级分组最小样本

# ── B1 附加常量（§2 输出形状的版本化标注，供 B2/B3 复用） ─────────

CALC_VERSION = "p1-v1"            # 所有诊断输出携带的口径版本（§0.5）
STABILITY_STATISTIC = "range"     # 稳定性统计量：主三门百分位极差（版本化）
TREND_DIRECTION_RECENT_N = 3      # direction_recent 回看的相邻变化次数（近 2-3 次）
HOMEWORK_WINDOW_7D = 7            # 作业行为近窗（天，含当日）
HOMEWORK_WINDOW_30D = 30          # 作业行为远窗（天，含当日）


def stability_label(range_value: float) -> str:
    """主三门百分位极差 → 稳定性标签（§4 STABILITY_RANGE_LABELS 语义）：
    ≤0.10 稳定；≤0.25 中等波动；>0.25 高波动。"""
    for upper, label in STABILITY_RANGE_LABELS:
        if range_value <= upper:
            return label
    return "高波动"
