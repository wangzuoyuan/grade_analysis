"""P0-A1 统一分析口径：新旧两条分析路径共用的概念定义（唯一实现）。

背景：项目存在两条分析路径——旧版 app/analysis/router.py + rank_metrics.py
（挂载 /api，见 app/main.py）与新版 app/api/analysis.py（/api/v1）。P0 要求
同一分析概念两处不得各算各的、输出冲突数值：判定逻辑一律收敛到本模块，
两条路径只做取数与展示。对照清单见 docs/diagnosis-roadmap/p0-definitions.md。

统一口径（阈值为 app/analysis/config.py 的出厂常量，段位阈值运行时读
AnalysisConfig）：

- 年级名次 resolve_year_rank：学籍名次 xueji_rank 优先，其次年级名次
  grade_rank；两者皆缺 → None（名次不可得）。绝不以「百分位 × 人数」
  推算名次，也不允许 9999/999999 之类哨兵值冒充最弱名次。
- 进退步 progress_issue：名次差 = 上一次名次 − 本次名次（名次数值变小
  = 正数 = 进步）；≥ PROGRESS_RANK_THRESHOLD(80) 判「明显进步」，
  ≤ −PROGRESS_RANK_THRESHOLD 判「明显退步」。任一场名次缺失不判。
- 波动 volatility_issue：最近 ≥ min_points(3) 场有效名次的极差
  （max − min）≥ VOLATILITY_RANK_THRESHOLD(120) 判「波动风险」。
  不足 3 场不判（样本过少，波动结论不稳定）。
- 偏科 subject_weakness_subjects：单科年级百分位 − 主三门年级百分位
  ≥ SUBJECT_WEAKNESS_PCT_DIFF(0.20)。任一侧百分位缺失不判（缺考不残留
  上次百分位——每行百分位只来自该场考试自身的数据）。
- 段位 band_flags / band_issues：AnalysisConfig 的年级名次阈值
  （高分 1–high_score_max、临界 critical_min–critical_max、薄弱
  ≥ weak_min）。名次不可得时不落任何段——绝不把缺名次学生按哨兵名次
  落进薄弱段，也绝不把名次阈值当分数比较（v2.1/F10）。
- 分箱 percentile_bin / rank_bin / grade_score_bin：两条路径的排名频次
  统计共用同一分箱边界与 bin key；展示文案（label）允许按前端代次不同，
  但分箱数值必须一致。
- 班内名次 min_ranks：同分同名次（min-rank，1,2,2,4）；None 不参与名次，
  该 key 名次为 None。
- 名次区间筛选：只按真实名次（resolve_year_rank）过滤；名次不可得的
  学生不进结果，由 metric_note 明确标注缺失，不按百分位换算补位。

跨学年趋势：结构上按学年/年级分组陈列（新版 trends 端点按学年分组，
旧版画像按年级排序输出），不做任何跨学年连算的进退步/波动结论；
进退步与波动判定只在同一年度内的相邻考试间进行。
"""

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.analysis.config import (
    PROGRESS_RANK_THRESHOLD,
    SUBJECT_WEAKNESS_PCT_DIFF,
    VOLATILITY_RANK_THRESHOLD,
)

# ── 学科与分箱常量（旧 rank_metrics 与新 api/analysis 共用同一份）──
BASE_SUBJECTS = ["语文", "数学", "英语"]
ELECTIVE_SUBJECTS = ["物理", "化学", "生物", "政治", "历史", "地理"]
ALL_SUBJECTS = BASE_SUBJECTS + ELECTIVE_SUBJECTS

PERCENTILE_BINS = [
    ("p0_20", "前20%", 0.0, 0.2),
    ("p20_40", "20%-40%", 0.2, 0.4),
    ("p40_60", "40%-60%", 0.4, 0.6),
    ("p60_80", "60%-80%", 0.6, 0.8),
    ("p80_100", "后20%", 0.8, 1.0),
]
GRADE_SCORE_VALUES = [70, 67, 64, 61, 58, 55, 52, 49, 46, 43, 40]
GRADE_SCORE_SEPARATOR_AFTER = {67, 58, 49, 43}
GRADE_SCORE_BINS = [
    (f"g{score}", f"{score}分", score, score in GRADE_SCORE_SEPARATOR_AFTER)
    for score in GRADE_SCORE_VALUES
]

# 每档名次宽度（排名频次/名次分布的 40 名一档）
RANK_BIN_WIDTH = 40

# 段位判定输出文案（重点关注 issues 与段位分布共用）
ISSUE_PROGRESS = "明显进步"
ISSUE_REGRESSION = "明显退步"
ISSUE_VOLATILE = "波动风险"
ISSUE_CRITICAL = "临界段"
ISSUE_WEAK = "薄弱段"
ISSUE_STABLE_EXCELLENT = "稳定优秀"


def normalized_percentile(value: Optional[float]) -> Optional[float]:
    """百分位归一化：>1 的按百分数（0–100）处理成 0–1；越界截断。"""
    if value is None:
        return None
    number = float(value)
    if number > 1:
        number /= 100
    return min(max(number, 0), 1)


def resolve_year_rank(
    xueji_rank: Optional[int], grade_rank: Optional[int]
) -> Optional[int]:
    """年级名次解析（唯一口径）：学籍名次优先，其次年级名次。

    两者皆缺（或为非正数的非法值）返回 None（名次不可得）。调用方必须把
    None 当「缺失」处理：不落段、不进名次区间、不参与进退步/波动，
    绝不编造名次。"""
    for candidate in (xueji_rank, grade_rank):
        if candidate is not None and int(candidate) >= 1:
            return int(candidate)
    return None


def progress_issue(
    rank_change: Optional[int],
    threshold: int = PROGRESS_RANK_THRESHOLD,
) -> Optional[str]:
    """进退步判定（唯一口径）：rank_change = 上一次名次 − 本次名次，
    正数=进步。≥ threshold → 明显进步；≤ −threshold → 明显退步；
    其余（含 None，即任一场名次缺失）→ None 不判。"""
    if rank_change is None:
        return None
    if rank_change >= threshold:
        return ISSUE_PROGRESS
    if rank_change <= -threshold:
        return ISSUE_REGRESSION
    return None


def volatility_issue(
    ranks: Sequence[Optional[int]],
    threshold: int = VOLATILITY_RANK_THRESHOLD,
    min_points: int = 3,
) -> Optional[str]:
    """波动判定（唯一口径）：有效名次极差（max − min）≥ threshold 且
    有效名次数 ≥ min_points → 波动风险；否则 None。None 名次不参与极差
    （缺考那一场不残留上次名次、不按 0 处理）。"""
    values = [int(rank) for rank in ranks if rank is not None]
    if len(values) < min_points:
        return None
    if max(values) - min(values) >= threshold:
        return ISSUE_VOLATILE
    return None


def subject_weakness_subjects(
    subject_percentiles: Iterable[Tuple[str, Optional[float]]],
    main_percentile: Optional[float],
    diff: float = SUBJECT_WEAKNESS_PCT_DIFF,
) -> List[str]:
    """严重偏科判定（唯一口径）：单科年级百分位 − 主三门年级百分位
    ≥ diff 的学科，按学科名排序。主三门百分位或该科百分位缺失不判。"""
    if main_percentile is None:
        return []
    base = normalized_percentile(main_percentile)
    if base is None:
        return []
    weak = []
    for subject, percentile in subject_percentiles:
        subject_pct = normalized_percentile(percentile)
        if subject_pct is None:
            continue
        if subject_pct - base >= diff:
            weak.append(subject)
    return sorted(set(weak))


def band_flags(rank: Optional[int], config: Dict[str, int]) -> Dict[str, bool]:
    """段位判定（唯一口径）：返回 {high_score, critical, weak} 三个布尔。
    名次不可得（None）→ 三段全 False，绝不落段。"""
    if rank is None:
        return {"high_score": False, "critical": False, "weak": False}
    return {
        "high_score": 1 <= rank <= config["high_score_max"],
        "critical": config["critical_min"] <= rank <= config["critical_max"],
        "weak": rank >= config["weak_min"],
    }


def band_issues(rank: Optional[int], config: Dict[str, int]) -> List[str]:
    """段位判定输出的关注文案：临界段 / 薄弱段（关注名单语境没有
    「高分段」issue，高分且稳定才输出「稳定优秀」，由调用方组合）。"""
    flags = band_flags(rank, config)
    issues = []
    if flags["critical"]:
        issues.append(ISSUE_CRITICAL)
    if flags["weak"]:
        issues.append(ISSUE_WEAK)
    return issues


def min_ranks(pairs: Sequence[Tuple[int, Optional[float]]]) -> Dict[int, Optional[int]]:
    """班内（成员集内）名次（唯一口径）：同分同名次 min-rank（1,2,2,4），
    名次 = 1 + 严格更高分人数；None 不参与名次（该 key 的名次为 None）。
    只对传入的成员集合计算，绝不外扩到年级。"""
    scores = [s for _, s in pairs if s is not None]
    return {
        key: (None if s is None else 1 + sum(1 for other in scores if other > s))
        for key, s in pairs
    }


def rank_bucket_start(rank: int) -> int:
    """40 名一档的档位起点：第 1–40 名 → 1，第 41–80 名 → 41……"""
    return ((int(rank) - 1) // RANK_BIN_WIDTH) * RANK_BIN_WIDTH + 1


def rank_bin(rank: Optional[int]) -> Optional[str]:
    """名次分箱 bin key：r{start}_{end}（如 r1_40）。名次缺失/非法 → None。"""
    if rank is None or int(rank) < 1:
        return None
    start = rank_bucket_start(rank)
    return f"r{start}_{start + RANK_BIN_WIDTH - 1}"


def rank_bin_label(key: str) -> str:
    """名次分箱展示文案（新版 /api/v1 格式）。旧版前端沿用
    「{start}-{end}名次数」文案，属展示层兼容差异（分箱边界一致）。"""
    start, end = key.removeprefix("r").split("_")
    return f"{start}–{end}名"


def percentile_bin(value: Optional[float]) -> Optional[str]:
    """年级百分位五等分箱：p0_20 / p20_40 / p40_60 / p60_80 / p80_100。
    百分位缺失（含缺考）→ None，绝不残留上次百分位、不按 0 落箱。"""
    percentile = normalized_percentile(value)
    if percentile is None:
        return None
    for key, _label, lower, upper in PERCENTILE_BINS:
        if percentile <= upper and (percentile > lower or lower == 0):
            return key
    return PERCENTILE_BINS[-1][0]


def grade_score_bin(value: Optional[float]) -> Optional[str]:
    """选考科目精确等级分箱：g70/g67/…/g40。非标准档位值 → None。"""
    if value is None:
        return None
    score = int(round(float(value)))
    if score in GRADE_SCORE_VALUES:
        return f"g{score}"
    return None


def metric_options(grade: int, mode: str = "frequency") -> List[Dict[str, str]]:
    """排名指标选项（唯一口径）：单科按年级百分位、选考等级分、总分按
    学籍/年级名次。高一多科 + 主三门/五门；高二/三语数英 + 选考等级分
    （频次模式）+ 主三门/3+3。"""
    options: List[Dict[str, str]] = []
    if grade == 1:
        options.extend(
            {"value": f"subject:{subject}", "label": subject, "kind": "subject_percentile"}
            for subject in ALL_SUBJECTS
        )
        options.extend(
            {"value": f"total:{total_type}", "label": f"{total_type}总分", "kind": "total_rank"}
            for total_type in ["主三门", "五门"]
        )
        return options

    options.extend(
        {"value": f"subject:{subject}", "label": subject, "kind": "subject_percentile"}
        for subject in BASE_SUBJECTS
    )
    if mode == "frequency":
        options.extend(
            {"value": f"subject_grade:{subject}", "label": f"{subject}等级分", "kind": "subject_grade_score"}
            for subject in ELECTIVE_SUBJECTS
        )
    options.extend(
        {"value": f"total:{total_type}", "label": f"{total_type}总分", "kind": "total_rank"}
        for total_type in ["主三门", "3+3"]
    )
    return options


def metric_meta(grade: int, metric: str, mode: str = "frequency") -> Dict[str, str]:
    """指标元数据解析；不支持的指标抛 ValueError（旧路径转 400，
    新路径转 422 invalid_scope_param）。"""
    for option in metric_options(grade, mode):
        if option["value"] == metric:
            source, key = metric.split(":", 1)
            return {**option, "source": source, "key": key}
    raise ValueError("该年级不支持此排名指标")


def distribution_total_types(grade: int) -> Tuple[str, ...]:
    """名次分布图例的总分口径（唯一口径）：高一 主三门/五门/九门；
    高二/三 主三门/3+3。"""
    return ("主三门", "五门", "九门") if grade == 1 else ("主三门", "3+3")
