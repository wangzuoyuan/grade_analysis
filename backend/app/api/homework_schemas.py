"""P5 作业端点的请求/响应模型（契约 docs/contracts/p5-homework.md）。

独立成文件（不放 schemas.py）：P5 与 P4 波次并行，schemas.py 由其他
负责人持有，避免同文件冲突。请求字段一律用窄类型（str/int）+ 端点内
手工解析校验，保证业务错误统一走 DomainError 契约错误体
（{"error": "invalid_scope_param", ...}），而不是 FastAPI 默认 422 形态。
"""

from typing import List, Optional

from pydantic import BaseModel, Field

from app.api.schemas import Metadata


# ────────────────────────────── 请求（§1.1 / §1.3 / §5） ──────────────────────────────


class HomeworkRowInput(BaseModel):
    """逐人行：person_id 与 name_or_alias 二选一（都给时 person_id 优先）。"""

    person_id: Optional[int] = None
    name_or_alias: Optional[str] = None
    status: str
    evaluation: Optional[str] = None
    attendance: Optional[str] = None


class HomeworkInputSpec(BaseModel):
    """三模式录入：full（全交台账+例外）/ names（名单）/ detailed（逐行）。"""

    kind: str
    names: Optional[List[str]] = None
    rows: Optional[List[HomeworkRowInput]] = None
    all_submitted: Optional[bool] = None
    exceptions: Optional[List[HomeworkRowInput]] = None


class HomeworkPreviewRequest(BaseModel):
    mode: str
    class_id: Optional[int] = None
    teaching_class_id: Optional[int] = None
    academic_year_id: Optional[int] = None
    subject: str
    homework_type: str
    assigned_date: str
    due_date: Optional[str] = None
    input: HomeworkInputSpec


class HomeworkConfirmRequest(BaseModel):
    token: str


class HomeworkPatchRequest(BaseModel):
    revision: int
    rows: Optional[List[HomeworkRowInput]] = None
    due_date: Optional[str] = None
    assigned_date: Optional[str] = None
    homework_type: Optional[str] = None
    subject: Optional[str] = None


class SemesterCreateRequest(BaseModel):
    academic_year_id: int
    name: str
    start_date: str
    end_date: str


class SemesterUpdateRequest(BaseModel):
    name: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


# ────────────────────────────── 录入响应（§1.1 / §1.2） ──────────────────────────────


class HomeworkPersonBrief(BaseModel):
    person_id: int
    name: Optional[str] = None


class HomeworkSubmissionBrief(HomeworkPersonBrief):
    status: str
    evaluation: Optional[str] = None
    attendance: Optional[str] = None
    special_note: Optional[str] = None
    quality_negative: bool = False


class HomeworkExistingBatch(BaseModel):
    assignment_id: int
    batch_token: str
    revision: int


class HomeworkPreviewAssignment(BaseModel):
    subject: str
    homework_type: str
    assigned_date: str
    due_date: Optional[str] = None
    expected_members: List[HomeworkPersonBrief]
    submissions: List[HomeworkSubmissionBrief]
    warnings: List[str]


class HomeworkPreviewResponse(BaseModel):
    token: str
    expires_at: str
    assignment: HomeworkPreviewAssignment
    existing_batches: List[HomeworkExistingBatch]


class HomeworkConfirmResponse(BaseModel):
    assignment_id: int
    revision: int
    submitted: int
    missing: int
    excused: int


# ────────────────────────────── 详情 / 列表 / 看板（§1.3 / §2） ──────────────────────────────


class HomeworkRateStats(BaseModel):
    """逐批次的计数与提交率；分母 = 快照人数 − excused，快照为空 →
    rate 为 null 且 rate_unavailable=True（H03：仅缺交历史不推断全交）。"""

    expected_count: int
    submitted: int
    missing: int
    excused: int
    submission_rate: Optional[float] = None
    rate_unavailable: bool = False


class HomeworkAssignmentListItem(HomeworkRateStats):
    assignment_id: int
    data_domain: str
    subject: str
    homework_type: str
    assigned_date: str
    due_date: Optional[str] = None
    revision: int
    status: str
    attendance_count: int = 0
    negative_count: int = 0
    excused_ids: Optional[List[int]] = None
    missing_ids: Optional[List[int]] = None
    attendance_ids: Optional[List[int]] = None
    negative_ids: Optional[List[int]] = None
    # 忘带学生名单（evaluation 含忘带词）；忘带是缺交的子集，绝不改变 missing 计数口径
    forgot_ids: Optional[List[int]] = None


class HomeworkAssignmentListResponse(BaseModel):
    metadata: Metadata
    total: int
    items: List[HomeworkAssignmentListItem]


class HomeworkSubmissionOut(HomeworkSubmissionBrief):
    pass


class HomeworkAssignmentDetail(HomeworkRateStats):
    metadata: Metadata
    assignment_id: int
    data_domain: str
    academic_year_id: int
    subject: str
    homework_type: str
    assigned_date: str
    due_date: Optional[str] = None
    revision: int
    status: str
    expected_members: List[HomeworkPersonBrief]
    submissions: List[HomeworkSubmissionOut]


class HomeworkPatchResponse(BaseModel):
    assignment_id: int
    revision: int
    updated: int


class HomeworkDeleteResponse(BaseModel):
    success: bool
    assignment_id: int
    status: str
    revision: int


class HomeworkDashboardGroup(HomeworkRateStats):
    label: str
    assignments: int
    negative_count: int = 0


class HomeworkDashboardResponse(BaseModel):
    metadata: Metadata
    group_by: str
    basis: str
    groups: List[HomeworkDashboardGroup]


# ────────────────────────────── 学生事件流 / 预警（§2 / §3） ──────────────────────────────


class HomeworkStudentEvent(BaseModel):
    assignment_id: int
    assigned_date: str
    subject: str
    homework_type: str
    status: str
    evaluation: Optional[str] = None


class HomeworkStudentStreaks(BaseModel):
    current_missing_streak: Optional[int] = None
    streak_basis: str = "events"
    longest_missing_streak: int = 0


class HomeworkStudentResponse(BaseModel):
    metadata: Metadata
    person_id: int
    name: Optional[str] = None
    events: List[HomeworkStudentEvent]
    streaks: HomeworkStudentStreaks


class HomeworkRecentMissing(BaseModel):
    assignment_id: int
    assigned_date: str
    subject: str
    homework_type: str


class HomeworkWarningStudent(BaseModel):
    person_id: int
    name: Optional[str] = None
    missing_count: int
    current_streak: Optional[int] = None
    streak_basis: str = "events"
    streak_subject: Optional[str] = None
    streak_homework_type: Optional[str] = None
    recent_missing: List[HomeworkRecentMissing]


class HomeworkAuxWarningStudent(BaseModel):
    person_id: int
    name: Optional[str] = None
    count: int
    dates: List[str]
    details: List[str] = Field(default_factory=list)


class HomeworkWarningsResponse(BaseModel):
    metadata: Metadata
    basis: str
    min_missing: int
    min_streak: Optional[int] = None
    students: List[HomeworkWarningStudent]
    quality: List[HomeworkAuxWarningStudent] = Field(default_factory=list)
    forgot: List[HomeworkAuxWarningStudent] = Field(default_factory=list)


class HomeworkWarningDismissRequest(BaseModel):
    mode: Optional[str] = None
    academic_year_id: Optional[int] = None
    class_id: Optional[int] = None
    teaching_class_id: Optional[int] = None
    subject: Optional[str] = None
    person_id: int
    warning_kind: str = "quality"
    dismiss_date: Optional[str] = None


class HomeworkWarningDismissResponse(BaseModel):
    ok: bool = True
    person_id: int
    warning_kind: str
    dismissed_date: str


# ────────────────────────────── 相关性（§4） ──────────────────────────────


class HomeworkCorrelationPair(BaseModel):
    person_id: int
    name: Optional[str] = None
    x: float
    y: float


class HomeworkCorrelationResponse(BaseModel):
    metadata: Metadata
    pairs: List[HomeworkCorrelationPair]
    n: int
    r: Optional[float] = None
    direction: Optional[str] = None
    caveats: List[str]


# ────────────────────────────── 学期（§5） ──────────────────────────────


class HomeworkSemesterEntry(BaseModel):
    id: Optional[int] = None
    name: str
    start_date: str
    end_date: str
    is_current: bool
    mode: str


class HomeworkSemestersResponse(BaseModel):
    academic_year_id: int
    academic_year_name: str
    auto: bool
    semesters: List[HomeworkSemesterEntry]


class HomeworkSemesterRestoreResponse(BaseModel):
    restored: bool
    before: HomeworkSemesterEntry
    after: List[HomeworkSemesterEntry]


class HomeworkSemesterCurrentResponse(BaseModel):
    id: int
    academic_year_id: int
    is_current: bool


class CurrentSemesterResponse(BaseModel):
    id: Optional[int] = None
    academic_year_id: int
    academic_year_name: str
    name: str
    start_date: str
    end_date: str
    mode: str


# ────────────────────────────── 统计排除（ADR-023） ──────────────────────────────


class HomeworkStatsExclusionEntry(BaseModel):
    """名册条目 + 当前排除状态；行存在即排除。"""

    person_id: int
    name: Optional[str] = None
    alias: Optional[str] = None
    excluded: bool


class HomeworkStatsExclusionResponse(BaseModel):
    metadata: Metadata
    data_domain: str
    class_ref_id: int
    entries: List[HomeworkStatsExclusionEntry]


class HomeworkStatsExclusionSetRequest(BaseModel):
    """PUT /homework/stats-exclusion：开关一个学生的统计排除。

    teaching 必须显式 teaching_class_id（并集作用域无法定位单班名册）；
    person 必须在该班当前名册内，杜绝任意 id 写入。
    """

    mode: str
    academic_year_id: Optional[int] = None
    class_id: Optional[int] = None
    teaching_class_id: Optional[int] = None
    person_id: int
    excluded: bool
