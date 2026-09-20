"""P4 请求/响应模型（契约 docs/contracts/p4-students.md）。

独立于 schemas.py 的原因：P3/P4 并行开发期 schemas.py 是共享文件，P4 模型
单列本模块避免并行冲突。字段口径沿用 p1-api.md §0：*_id 一律整数、日期为
ISO 字符串（格式校验在端点内执行，保证 422 统一 {"error": code} 错误形态）、
缺考/空值保持 null 绝不转 0。
"""

from typing import Dict, List, Optional

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
    """同一路由承载两阶段导入：第一段 text，第二段 token。"""

    text: Optional[str] = None
    token: Optional[str] = None


class TeachingSyncRequest(BaseModel):
    confirm: bool = False


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


class RolloverPreviewResponse(BaseModel):
    token: str
    expires_at: str
    from_year: RolloverYearInfo
    to_year: RolloverYearInfo
    students: List[RolloverPreviewStudent] = Field(default_factory=list)


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


class RolloverConflictedStudent(BaseModel):
    person_id: int
    name: Optional[str] = None
    reason: str


class RolloverUndoResponse(BaseModel):
    success: bool
    undone: int
    conflicted: List[RolloverConflictedStudent] = Field(default_factory=list)
    class_removed: bool


class TeachingRolloverClassResult(BaseModel):
    class_id: int
    label: str
    class_created: bool


class TeachingRolloverConfirmResponse(BaseModel):
    """教学班换届确认：一次可升入多个教学班（同学科同标签逐班建新学年行）。"""

    rolled_over: int
    academic_year_id: int
    classes: List[TeachingRolloverClassResult] = Field(default_factory=list)


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


class NoteItem(BaseModel):
    id: int
    person_id: int
    date: str
    category: str
    content: str
    follow_up: Optional[str] = None
    follow_up_done: int = 0
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


class NotePatchRequest(BaseModel):
    date: Optional[str] = None
    category: Optional[str] = None
    content: Optional[str] = None
    follow_up: Optional[str] = None
    follow_up_done: Optional[int] = None


class NoteDeleteResponse(BaseModel):
    success: bool

    model_config = ConfigDict(json_schema_extra={"examples": [{"success": True}]})


NotesSummary.model_rebuild()
