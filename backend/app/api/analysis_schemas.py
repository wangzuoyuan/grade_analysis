"""P3 分析端点响应模型（契约 docs/contracts/p3-imports-analysis.md §2）。

独立成模块（不放 app.api.schemas）以避开并行波次的文件冲突；公共
Metadata/ScopeInfo 直接复用 app.api.schemas，不再复制。

计量红线（契约 §2.3，模型层面落实）：
- 缺考 score 为 null，绝不转 0；NULL 不进均分/名次分母；
- valid_count < 5 的响应附 small_sample: true；
- 未知/不可计算（如无可计名次时的 rank_min/rank_max）字段为 null，不编造；
- teaching 域变体不设 total_type/其他学科键，键本身缺席而非置 null。
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from app.api.schemas import Metadata


class AnalysisMetadata(Metadata):
    """分析端点元数据：契约 p3 §1.4.1（v2.1/F08）要求注明成员口径——
    membership_basis: "exam" = 按考试发生时名册解析；"current" = 查询
    时点名册（dashboard/看板类）。"""

    membership_basis: str = "current"


# ────────────────────── homeroom（§2.1） ──────────────────────


class HomeroomSubjectStat(BaseModel):
    """单科聚合行：本域事实必有原始分或 NULL，故 score_basis 恒 raw。"""

    subject: str
    avg: Optional[float] = None
    max: Optional[float] = None
    min: Optional[float] = None
    valid_count: int = 0
    missing_count: int = 0
    score_basis: str = "raw"


class HomeroomTotalStat(BaseModel):
    """总分聚合行（契约冻结结构：无 missing_count 字段；NULL 同样不进分母）。"""

    total_type: str
    avg: Optional[float] = None
    max: Optional[float] = None
    min: Optional[float] = None
    valid_count: int = 0
    rank_min: Optional[int] = None
    rank_max: Optional[int] = None


class HomeroomExamStatsResponse(BaseModel):
    metadata: AnalysisMetadata
    subjects: List[HomeroomSubjectStat] = Field(default_factory=list)
    totals: List[HomeroomTotalStat] = Field(default_factory=list)
    cohort_size: int
    # §2.3：任一 subject/total 行 valid_count < 5 即 true（两域统一红线）
    small_sample: bool = False


class HomeroomClassAverageRow(BaseModel):
    class_type: Optional[str] = None
    class_num: int
    teacher_name: Optional[str] = None
    subjects: Dict[str, Optional[float]] = Field(default_factory=dict)
    # 每口径的指标包：均分/及格率/高分率/低分率/标准差（旧数据仅均分，其余
    # 键缺省 null；校名次为文件自带值，仅入库备查不下发）
    totals: Dict[str, Optional[dict]] = Field(default_factory=dict)
    # 班型内排名：按 class_type 分组（平行班/实验班各自 1..n，同分同名次）
    total_ranks: Dict[str, Optional[int]] = Field(default_factory=dict)


class HomeroomClassAveragesResponse(BaseModel):
    metadata: AnalysisMetadata
    exam_name: str
    grade: int
    subjects: List[str] = Field(default_factory=list)
    total_types: List[str] = Field(default_factory=list)
    # 当前班主任绑定行政班班号（前端用于高亮本班；作用域解析失败无绑定班时为 None）
    current_class_num: Optional[int] = None
    rows: List[HomeroomClassAverageRow] = Field(default_factory=list)


class HomeroomAnalysisStudent(BaseModel):
    """单人单场成绩行：scores/totals 的键为该场本班出现的学科/总分并集，
    无行科目记 null（缺考显示"—"口径，不编造、不省略）。

    grade_scores 为选科等级分；percentiles 为年级百分位（单科+总分）；
    ranks 为各总分口径校名次（学籍名次优先口径）。"""

    person_id: int
    name: Optional[str] = None
    alias: Optional[str] = None
    xueji: Optional[int] = None
    class_num: Optional[int] = None
    scores: Dict[str, Optional[float]] = Field(default_factory=dict)
    grade_scores: Dict[str, Optional[float]] = Field(default_factory=dict)
    percentiles: Dict[str, Optional[float]] = Field(default_factory=dict)
    totals: Dict[str, Optional[float]] = Field(default_factory=dict)
    ranks: Dict[str, Optional[int]] = Field(default_factory=dict)


class HomeroomExamStudentsResponse(BaseModel):
    metadata: AnalysisMetadata
    students: List[HomeroomAnalysisStudent] = Field(default_factory=list)


class HomeroomRankBandClass(BaseModel):
    """名次段表一个班的行：bands 与导入文件列序一致。"""

    class_num: int
    teacher_name: Optional[str] = None
    total: int = 0
    bands: List[dict] = Field(default_factory=list)


class HomeroomRankBandsResponse(BaseModel):
    """段位分布（读导入的名次段表，不依赖学生名次现算）。

    classes 为各班行；grade_bands 为全年级各段合计；无导入数据时
    classes 为空列表（前端给上传引导空态）。"""

    metadata: AnalysisMetadata
    exam_name: str
    grade: Optional[int] = None
    total_type: Optional[str] = None
    available_total_types: List[str] = Field(default_factory=list)
    current_class_num: Optional[int] = None
    classes: List[HomeroomRankBandClass] = Field(default_factory=list)
    grade_bands: List[dict] = Field(default_factory=list)


class FocusBandConfig(BaseModel):
    high_score_max: int
    critical_min: int
    critical_max: int
    weak_min: int
    subject_weakness_diff: float
    progress_rank_threshold: int
    volatility_rank_threshold: int


class HomeroomFocusStudent(BaseModel):
    person_id: int
    name: Optional[str] = None
    alias: Optional[str] = None
    total_score: Optional[float] = None
    xueji_rank: Optional[int] = None
    issues: List[str] = Field(default_factory=list)
    weak_subjects: List[str] = Field(default_factory=list)
    previous_rank: Optional[int] = None
    rank_change: Optional[int] = None
    rank_range: Optional[int] = None
    exam_count: int = 1


class HomeroomFocusResponse(BaseModel):
    metadata: AnalysisMetadata
    exam_name: str
    total_type: str = "主三门"
    basis: str = "school_rank_and_grade_percentile"
    config: FocusBandConfig
    students: List[HomeroomFocusStudent] = Field(default_factory=list)


class HomeroomRankMetric(BaseModel):
    value: str
    label: str
    kind: str


class HomeroomRankMetricsResponse(BaseModel):
    metadata: AnalysisMetadata
    grade: int
    metrics: List[HomeroomRankMetric] = Field(default_factory=list)


class HomeroomRankFrequencyExam(BaseModel):
    exam_name: str
    exam_date: Optional[str] = None


class HomeroomRankFrequencyBin(BaseModel):
    key: str
    label: str
    separator_after: bool = False


class HomeroomRankFrequencyStudent(BaseModel):
    person_id: int
    name: Optional[str] = None
    alias: Optional[str] = None
    counts: Dict[str, int] = Field(default_factory=dict)
    total_count: int = 0


class HomeroomRankFrequencyResponse(BaseModel):
    metadata: AnalysisMetadata
    metric: str
    metric_label: str
    metric_kind: str
    exams: List[HomeroomRankFrequencyExam] = Field(default_factory=list)
    bins: List[HomeroomRankFrequencyBin] = Field(default_factory=list)
    students: List[HomeroomRankFrequencyStudent] = Field(default_factory=list)
    metric_note: str


class HomeroomRankRangeStudent(BaseModel):
    person_id: int
    name: Optional[str] = None
    alias: Optional[str] = None
    score: Optional[float] = None
    class_rank: Optional[int] = None
    year_rank: Optional[int] = None
    # 筛选依据值，单位随 metric_kind：total_rank=名次、
    # subject_percentile=年级前%（0–100 一位小数）、subject_grade_score=等级分。
    metric_value: Optional[float] = None


class HomeroomRankRangeResponse(BaseModel):
    metadata: AnalysisMetadata
    exam_name: str
    metric: str
    metric_label: str
    metric_kind: str
    rank_min: int
    rank_max: int
    students: List[HomeroomRankRangeStudent] = Field(default_factory=list)
    metric_note: str


class HomeroomRankDistributionSeries(BaseModel):
    total_type: str
    counts: Dict[str, int] = Field(default_factory=dict)


class HomeroomRankDistributionResponse(BaseModel):
    metadata: AnalysisMetadata
    exam_name: str
    grade: int
    bins: List[HomeroomRankFrequencyBin] = Field(default_factory=list)
    series: List[HomeroomRankDistributionSeries] = Field(default_factory=list)
    metric_note: str


class TrendExamPoint(BaseModel):
    """趋势上的一次考试点（E03：只按学年分组陈列，无跨年同比字段）。"""

    exam_name: str
    exam_date: Optional[str] = None
    score: Optional[float] = None
    # 契约 §1.3 增列（迁移 0004）：列未就绪时缺省 null，不编造
    grade_score: Optional[float] = None
    # 趋势图专用排名口径：单科使用来源 grade_percentile（输出 0–100），
    # 总分使用来源保存的学籍/年级名次。缺失时保持 null，不用分数推算。
    rank: Optional[float] = None
    rank_basis: Optional[str] = None
    # 来源 grade_percentile 的百分数表示（0–100），单科与总分统一下发；
    # 2026-10-25 学生成绩雷达图「总分」轴定位用（总分 rank 是名次、无法
    # 直接落到 0–100 径向刻度）。缺失时保持 null，不用分数推算。
    percentile: Optional[float] = None


class TrendYearGroup(BaseModel):
    """一个学年内的成绩分组：跨学年比较由结构上阻断（各自独立分组）。"""

    academic_year_id: int
    academic_year_name: str
    subjects: Dict[str, List[TrendExamPoint]] = Field(default_factory=dict)
    totals: Dict[str, List[TrendExamPoint]] = Field(default_factory=dict)


class TrendsResponse(BaseModel):
    person_id: int
    name: Optional[str] = None
    years: List[TrendYearGroup] = Field(default_factory=list)


class BandEntry(BaseModel):
    """段位分布行：students 为落在该段的人；分母只计非 NULL 分数。

    2026-10-23：新增 names（段内学生姓名，按名次升序、与 students 一一对应，
    供前端悬浮展示名单；display_name 缺失者跳过）。
    """

    label: str
    count: int = 0
    students: List[int] = Field(default_factory=list)
    names: List[str] = Field(default_factory=list)


class BandsResponse(BaseModel):
    metadata: AnalysisMetadata
    bands: List[BandEntry] = Field(default_factory=list)
    # metric=total 时回显所统计的 total_type；score 模式恒 null
    total_type: Optional[str] = None


# ────────────────────── teaching（§2.2） ──────────────────────


class TeachingExamStatsResponse(BaseModel):
    """单科统计：rank 只在本教学班成员内计算（含反向投影的 H 域行），
    同分同名次；无可计名次时 rank_min/rank_max 为 null。"""

    metadata: AnalysisMetadata
    subject: str
    avg: Optional[float] = None
    max: Optional[float] = None
    min: Optional[float] = None
    valid_count: int = 0
    missing_count: int = 0
    rank_min: Optional[int] = None
    rank_max: Optional[int] = None
    # P3 域内事实以原始分计量（score 列）；grade_score 仅透出展示，
    # 不作计量基准，故恒 raw
    score_basis: str = "raw"
    cohort_size: int
    small_sample: bool = False


class TeachingAnalysisStudent(BaseModel):
    """教学班学生行：无 total_type/其他学科键（键本身缺席）。
    source_domain 标注行来源；两域成绩隔离后仅为 teaching 本域。"""

    person_id: int
    name: Optional[str] = None
    class_label: Optional[str] = None
    score: Optional[float] = None
    grade_score: Optional[float] = None
    grade_label: Optional[str] = None
    xueji_rank: Optional[int] = None
    grade_percentile: Optional[float] = None
    rank: Optional[int] = None
    source_domain: str


class TeachingExamStudentsResponse(BaseModel):
    metadata: AnalysisMetadata
    grade: Optional[int] = None
    students: List[TeachingAnalysisStudent] = Field(default_factory=list)


class ClassCompareEntry(BaseModel):
    """班级对比行：E04 红线——本班样本均分恒标 estimated，官方班均表
    未入库前绝不提供 official；member_count 为参与该场考试的有效成员数
    （非 NULL），不是名册人数。"""

    teaching_class_id: int
    class_label: str
    member_count: int = 0
    subject_avg: Optional[float] = None
    score_basis: str = "raw"
    source: str = "estimated"


class ClassCompareResponse(BaseModel):
    # 契约 §2.2 冻结结构无 metadata；small_sample 为 §2.3 统一红线字段
    # （任一班 valid_count < 5 即 true）
    classes: List[ClassCompareEntry] = Field(default_factory=list)
    small_sample: bool = False


class TeachingScoreBand(BaseModel):
    """单个分数段（左闭右开，90 入顶段、89.5 入 [80,90)）：顶段
    high=None 表示 [low,+∞)；count 为本段人数，cumulative 自高分段
    向下累计，cumulative_pct 为累计比例（%），valid_count=0 时 None。"""

    low: float
    high: Optional[float] = None
    count: int = 0
    cumulative: int = 0
    cumulative_pct: Optional[float] = None


class TeachingScoreBandClass(BaseModel):
    """分数段分析的单列（一个教学班，或 merged 当前范围合并列——
    teaching_class_id=None）。stddev 为总体标准差（σ）；缺考
    （score NULL）不计入任何段与计量，单独计 missing_count。"""

    teaching_class_id: Optional[int] = None
    class_label: str
    valid_count: int = 0
    missing_count: int = 0
    max: Optional[float] = None
    min: Optional[float] = None
    avg: Optional[float] = None
    stddev: Optional[float] = None
    bands: List[TeachingScoreBand] = Field(default_factory=list)


class TeachingScoreBandsResponse(BaseModel):
    """分数段分析（2026-10-18，官方《分数段分析》表同构的本班口径）：
    仅当前范围所教教学班，不做年级/官方口径（官方班级均分表走
    class-compare/班级对比页）。"""

    metadata: AnalysisMetadata
    subject: str
    band_width: int = 10
    score_basis: str = "raw"
    classes: List[TeachingScoreBandClass] = Field(default_factory=list)
    merged: TeachingScoreBandClass
    small_sample: bool = False


class WeeklyFocusStudent(BaseModel):
    student_id: str
    name: str
    score: int
    reasons: List[str] = Field(default_factory=list)


class HomeroomWeeklyFocusResponse(BaseModel):
    metadata: AnalysisMetadata
    class_id: int
    week: Dict[str, str] = Field(default_factory=dict)
    students: List[WeeklyFocusStudent] = Field(default_factory=list)
    total_count: int = 0
    note: str = ""
