"""P4 请求/响应模型（契约 docs/contracts/p4-students.md）。

独立于 schemas.py 的原因：P3/P4 并行开发期 schemas.py 是共享文件，P4 模型
单列本模块避免并行冲突。字段口径沿用 p1-api.md §0：*_id 一律整数、日期为
ISO 字符串（格式校验在端点内执行，保证 422 统一 {"error": code} 错误形态）、
缺考/空值保持 null 绝不转 0。
"""

from typing import Literal, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas import Metadata, SubjectGroup, TotalGroup


# ────────────────────────────── §1 学年 / 学期 ──────────────────────────────


class AcademicYearItem(BaseModel):
    id: int
    name: str
    start_date: str
    end_date: str

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"id": 1, "name": "2025-2026", "start_date": "2025-09-01", "end_date": "2026-07-15"}
            ]
        }
    )


class AcademicYearsResponse(BaseModel):
    years: List[AcademicYearItem] = Field(default_factory=list)


class AcademicYearCreateRequest(BaseModel):
    name: str
    start_date: str
    end_date: str


class AcademicYearPatchRequest(BaseModel):
    """字段缺省保持原值；日期格式与区间校验在端点内做（统一 422 形态）。"""

    name: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class TermItem(BaseModel):
    id: int
    academic_year_id: int
    name: str
    start_date: str
    end_date: str


class TermsResponse(BaseModel):
    terms: List[TermItem] = Field(default_factory=list)


class TermCreateRequest(BaseModel):
    academic_year_id: int
    name: str
    start_date: str
    end_date: str


# ────────────────────────────── §2.1 名册 CRUD ──────────────────────────────


class StudentCreateRequest(BaseModel):
    name: str
    alias: Optional[str] = None
    seat_no: Optional[int] = None
    # 问题8：入班/学籍生效日（ISO 日期字符串）。缺省=今天（教学域沿用
    # 夹到学年内的时间轴）；显式传入时校验落在所选学年内，用于历史名册
    # 补录与中途转入的区分。绝不默认回填学年首日。
    effective_on: Optional[str] = None


class StudentPatchRequest(BaseModel):
    """name/seat_no 缺省不改；用 model_fields_set 区分"显式置空"与"未传"。"""

    name: Optional[str] = None
    seat_no: Optional[int] = None


class StudentMutationResponse(BaseModel):
    person_id: int
    name: Optional[str] = None
    alias: Optional[str] = None
    seat_no: Optional[int] = None
    status: Optional[str] = None


class StudentArchiveRequest(BaseModel):
    """status=active 表示恢复在班（valid_to 强制清空）；离班必须给 valid_to。"""

    status: str  # transferred | graduated | active
    valid_to: Optional[str] = None


class StudentArchiveResponse(BaseModel):
    person_id: int
    status: str
    valid_to: Optional[str] = None


class StudentAliasAppendRequest(BaseModel):
    """追加新学号（换号接续，S08）：identity 不变，旧 alias 收尾。"""

    alias: str
    valid_from: str


class AliasItem(BaseModel):
    id: int
    alias_value: str
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"id": 1, "alias_value": "2025H6-01", "valid_from": "2025-09-01", "valid_to": None}
            ]
        }
    )


class AliasesResponse(BaseModel):
    aliases: List[AliasItem] = Field(default_factory=list)


# ────────────────────────────── §3 教学班成员 ──────────────────────────────


class TeachingMemberItem(BaseModel):
    person_id: int
    name: Optional[str] = None
    alias: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    source: Optional[str] = None


class TeachingMembersResponse(BaseModel):
    active: List[TeachingMemberItem] = Field(default_factory=list)
    left: List[TeachingMemberItem] = Field(default_factory=list)


class TeachingMemberImportRequest(BaseModel):
    """同一路由承载两阶段导入：第一段 text，第二段 token。

    effective_on（问题8）：成员生效日，两段必须一致（快照作用域比对），
    越界/漂移返回可读 422/409。"""

    text: Optional[str] = None
    token: Optional[str] = None
    effective_on: Optional[str] = None


class TeachingSyncNewStudentDecision(BaseModel):
    """问题7：关联班同步时对「未配对行政班学生」的逐人显式决定。

    action 语义：
    - new：新建教学域身份（同名；学号沿行政班学号在教学域独立登记）；
    - pair：选用既有教学身份（teaching_person_id，须为 teaching 域且未在
      本 link 配对他人）——候选清单仅提示，绝不自动认定；
    - skip：本次不动（默认行为，教师逐人确认）。
    effective_on 可逐人覆盖请求级日期（均在学年范围内校验）。
    """

    homeroom_person_id: int
    action: str  # new | pair | skip
    teaching_person_id: Optional[int] = None
    effective_on: Optional[str] = None


class TeachingSyncRequest(BaseModel):
    confirm: bool = False
    # 问题8：同步写入成员行的生效日（缺省=今天，教学域沿用夹到学年内）。
    effective_on: Optional[str] = None
    # 问题7：未配对行政班学生的逐人决定；缺省=全部不动（仅同步已配对方）。
    new_students: Optional[List[TeachingSyncNewStudentDecision]] = None


# ────────────────────────────── §2.2 换届 ──────────────────────────────


class RolloverYearInfo(BaseModel):
    id: int
    name: str


class RolloverPreviewStudent(BaseModel):
    """next_alias 为保守建议（沿用旧学号），confirm 时可逐人覆盖。"""

    person_id: int
    name: Optional[str] = None
    current_alias: Optional[str] = None
    next_alias: Optional[str] = None
    note: Optional[str] = None
    # 教学班换届：该成员所属来源教学班标签（班主任换届恒为 None）。
    class_label: Optional[str] = None


class RolloverCarryoverPlan(BaseModel):
    """延续期写入承接计划（问题9）：academic_year_id=目标学年但 class_ref_id
    仍指来源学年班级的成绩/作业批次，以及 valid_from 落在目标学年的学籍
    （延续期 create_student 归位路径）。confirm 随换届迁入新学年对应班，
    academic_year_id 不动、不碰来源学年正常数据；undo 时随班级行命运回迁。"""

    score_facts: int = 0
    homework_assignments: int = 0
    stray_enrollments: int = 0
    exam_names: List[str] = Field(default_factory=list)
    homework_subjects: List[str] = Field(default_factory=list)
    stray_student_names: List[str] = Field(default_factory=list)


class RolloverPreviewResponse(BaseModel):
    token: str
    expires_at: str
    from_year: RolloverYearInfo
    to_year: RolloverYearInfo
    students: List[RolloverPreviewStudent] = Field(default_factory=list)
    # 问题13.4：名册取样时点 = min(今天, 源学年结束日)——源学年已结束的
    # 补做换届不再因 valid_to 截止而漏人；basis 为口径说明，
    # excluded_students 列出取样时点不在班的成员及原因。
    roster_as_of: Optional[str] = None
    roster_basis: Optional[str] = None
    excluded_students: List[RolloverPreviewStudent] = Field(default_factory=list)
    # 问题9：延续期写入承接清单（只读影响预览；确认时在同一事务迁移）。
    carryover: Optional[RolloverCarryoverPlan] = None


class RolloverConfirmRequest(BaseModel):
    """aliases 键为 person_id 字符串（JSON 对象键恒为字符串）；缺省条目
    沿用 preview 建议值。"""

    token: str
    aliases: Dict[str, str] = Field(default_factory=dict)


class RolloverConfirmResponse(BaseModel):
    rolled_over: int
    class_id: int
    academic_year_id: int
    class_created: bool
    # 问题9：本次换届承接迁移的延续期写入行数（成绩/作业/学籍）。
    carryover_moved: int = 0


class RolloverConflictedStudent(BaseModel):
    person_id: int
    name: Optional[str] = None
    reason: str


class RolloverUndoResponse(BaseModel):
    success: bool
    undone: int
    conflicted: List[RolloverConflictedStudent] = Field(default_factory=list)
    class_removed: bool
    # 问题13.1：换届复用（confirm 前已存在）的班级绝不删除，标签在此如实
    # 列出；本次新建的班级仍按既有安全条件决定去留。
    kept_reused_class_labels: List[str] = Field(default_factory=list)
    # 问题9：延续期迁移行的回迁口径——仅本次新建班级被删除时回迁到来源班
    # （carryover_restored）；班级保留（复用班/有残留）则迁移保留
    # （carryover_kept），数据始终挂在存活班级上。
    carryover_restored: int = 0
    carryover_kept: int = 0


class TeachingRolloverClassResult(BaseModel):
    class_id: int
    label: str
    class_created: bool


class TeachingRolloverConfirmResponse(BaseModel):
    """教学班换届确认：一次可升入多个教学班（同学科同标签逐班建新学年行）。"""

    rolled_over: int
    academic_year_id: int
    classes: List[TeachingRolloverClassResult] = Field(default_factory=list)
    # 问题9：本次换届承接迁移的延续期写入行数（成绩/作业批次）。
    carryover_moved: int = 0


# ────────────────────────────── §2.3 学生报告 ──────────────────────────────


class ReportPerson(BaseModel):
    """基本信息 + 别名史（跨学年接续的可读凭证，S08）。"""

    person_id: int
    name: Optional[str] = None
    domain: str
    aliases: List[AliasItem] = Field(default_factory=list)


class ReportRoster(BaseModel):
    """当期名册位：座号/状态/当期学号 + 班级与学年展示名（打印抬头用）。"""

    class_id: int
    seat_no: Optional[int] = None
    status: Optional[str] = None
    alias: Optional[str] = None
    class_label: Optional[str] = None
    academic_year_name: Optional[str] = None


class NotesSummary(BaseModel):
    count: int
    recent: List["NoteItem"] = Field(default_factory=list)


class StudentReportResponse(BaseModel):
    metadata: Metadata
    person: ReportPerson
    roster: ReportRoster
    subjects: List[SubjectGroup] = Field(default_factory=list)
    totals: Optional[List[TotalGroup]] = None
    notes_summary: NotesSummary


# ────────────────────────────── §4 档案 notes ──────────────────────────────

# 契约 §4 枚举：谈话/观察/家访/家长沟通/奖惩/其他（写入侧校验依据）
NOTE_CATEGORIES = ("谈话", "观察", "家访", "家长沟通", "奖惩", "其他")

# P2-C4 干预状态枚举（p2-contracts.md §5.1；NULL=非干预旧档案）
NOTE_FOLLOW_UP_STATUSES = ("open", "done", "dismissed")

# P2-C4 干预扩展列（创建请求出现任一列 → 该档案按干预建档：status=open、
# 防重复校验、target_metric 合法性校验 + 基线自动捕获）
NOTE_INTERVENTION_FIELDS = (
    "targets",
    "problem",
    "subject_scope",
    "measures",
    "target_metric",
    "baseline_value",
    "start_date",
    "review_date",
)


class InterventionTargetInput(BaseModel):
    metric: str
    baseline_value: Optional[Dict] = None
    status: Literal["open", "done", "dismissed"] = "open"


class InterventionTargetItem(BaseModel):
    metric: str
    baseline_value: Optional[Dict] = None
    status: str
    closed_at: Optional[str] = None


class NoteItem(BaseModel):
    id: int
    person_id: int
    date: str
    category: str
    content: str
    follow_up: Optional[str] = None
    follow_up_done: int = 0
    # ── P2-C4 干预扩展（全可空；旧档案/普通档案一律 null） ──
    problem: Optional[str] = None
    subject_scope: Optional[str] = None
    measures: Optional[str] = None
    targets: Optional[List[InterventionTargetItem]] = None
    target_metric: Optional[str] = None
    baseline_value: Optional[Dict] = None
    start_date: Optional[str] = None
    review_date: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": 1,
                    "person_id": 1,
                    "date": "2026-03-02",
                    "category": "家访",
                    "content": "家访沟通住宿适应情况",
                    "follow_up": "两周后电话回访",
                    "follow_up_done": 0,
                }
            ]
        }
    )


class NotesResponse(BaseModel):
    notes: List[NoteItem] = Field(default_factory=list)


class NoteCreateRequest(BaseModel):
    date: str
    category: str
    content: str
    follow_up: Optional[str] = None
    # ── P2-C4 干预扩展（全可空；出现任一列按干预建档） ──
    problem: Optional[str] = None
    subject_scope: Optional[str] = None
    measures: Optional[str] = None
    targets: Optional[List[InterventionTargetInput]] = None
    target_metric: Optional[str] = None
    baseline_value: Optional[Dict] = None
    start_date: Optional[str] = None
    review_date: Optional[str] = None
    # 防重复录入（契约 §5.1）：同人同科已有未关闭干预时返回 409
    # duplicate_follow_up；教师确认知情后带 force=true 可仍建。
    force: bool = False


class NotePatchRequest(BaseModel):
    date: Optional[str] = None
    category: Optional[str] = None
    content: Optional[str] = None
    follow_up: Optional[str] = None
    follow_up_done: Optional[int] = None
    # ── P2-C4 干预扩展（status 关闭路径：open/done/dismissed） ──
    problem: Optional[str] = None
    subject_scope: Optional[str] = None
    measures: Optional[str] = None
    targets: Optional[List[InterventionTargetInput]] = None
    target_metric: Optional[str] = None
    baseline_value: Optional[Dict] = None
    start_date: Optional[str] = None
    review_date: Optional[str] = None
    status: Optional[str] = None


class NoteDeleteResponse(BaseModel):
    success: bool

    model_config = ConfigDict(json_schema_extra={"examples": [{"success": True}]})


NotesSummary.model_rebuild()
