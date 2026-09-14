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


class HomeroomExamStatsResponse(BaseModel):
    metadata: AnalysisMetadata
    subjects: List[HomeroomSubjectStat] = Field(default_factory=list)
    totals: List[HomeroomTotalStat] = Field(default_factory=list)
    cohort_size: int
    # §2.3：任一 subject/total 行 valid_count < 5 即 true（两域统一红线）
    small_sample: bool = False


class HomeroomAnalysisStudent(BaseModel):
    """单人单场成绩行：scores/totals 的键为该场本班出现的学科/总分并集，
    无行科目记 null（缺考显示"—"口径，不编造、不省略）。"""

    person_id: int
    name: Optional[str] = None
    alias: Optional[str] = None
    scores: Dict[str, Optional[float]] = Field(default_factory=dict)
    totals: Dict[str, Optional[float]] = Field(default_factory=dict)
    # linked 学生任教学科两域同场值不同时的待人工核对提示：
    # {"teaching_score": <对方值>}；绝不静默采用对方值（§1.4.1 R5）
    shared_conflicts: Optional[dict] = None


class HomeroomExamStudentsResponse(BaseModel):
    metadata: AnalysisMetadata
    students: List[HomeroomAnalysisStudent] = Field(default_factory=list)


class TrendExamPoint(BaseModel):
    """趋势上的一次考试点（E03：只按学年分组陈列，无跨年同比字段）。"""

    exam_name: str
    exam_date: Optional[str] = None
    score: Optional[float] = None
    # 契约 §1.3 增列（迁移 0004）：列未就绪时缺省 null，不编造
    grade_score: Optional[float] = None


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
    """段位分布行：students 为落在该段的人；分母只计非 NULL 分数。"""

    label: str
    count: int = 0
    students: List[int] = Field(default_factory=list)


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
    source_domain 标注行来源（teaching 本域 / homeroom 反向投影）。"""

    person_id: int
    name: Optional[str] = None
    class_label: Optional[str] = None
    score: Optional[float] = None
    grade_score: Optional[float] = None
    rank: Optional[int] = None
    source_domain: str


class TeachingExamStudentsResponse(BaseModel):
    metadata: AnalysisMetadata
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
