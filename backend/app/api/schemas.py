"""P1 /api/v1 响应模型与 OpenAPI 样例。

字段与样例严格对齐 ``docs/contracts/p1-api.md`` §0–§5；样例值取契约 §4
的合成样本（学年 2025-2026、H6 高二 6 班、T6/T8 物理教学班、考试 2025期中、
学生甲乙丙丁戊己）。缺考成绩 ``score: null``，不得转 0。
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

# ────────────────────────────── 通用 ──────────────────────────────


class ScopeInfo(BaseModel):
    """metadata.scope：请求作用域快照（后端解析结果，非客户端回显）。"""

    academic_year_id: int
    term_id: Optional[int] = None
    class_id: Optional[int] = None
    teaching_class_id: Optional[int] = None
    link_id: Optional[int] = None
    link_version: Optional[int] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "academic_year_id": 1,
                    "term_id": 1,
                    "class_id": 1,
                    "teaching_class_id": None,
                    "link_id": 1,
                    "link_version": 1,
                }
            ]
        }
    )


class Metadata(BaseModel):
    """所有 200 数据响应的公共元数据（契约 §0）。"""

    mode: str
    subject: Optional[str] = None
    scope: ScopeInfo
    cohort_size: int
    data_revision: int

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "mode": "homeroom",
                    "subject": None,
                    "scope": {
                        "academic_year_id": 1,
                        "term_id": 1,
                        "class_id": 1,
                        "teaching_class_id": None,
                        "link_id": 1,
                        "link_version": 1,
                    },
                    "cohort_size": 3,
                    "data_revision": 1,
                }
            ]
        }
    )


# ────────────────────────────── shared/config · links ──────────────────────────────


class HomeroomConfig(BaseModel):
    configured: bool
    grade: Optional[int] = None
    class_num: Optional[int] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"configured": True, "grade": 2, "class_num": 6}]
        }
    )


class TeachingConfig(BaseModel):
    configured: bool
    subject: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"configured": True, "subject": "物理"}]}
    )


class TeacherInfo(BaseModel):
    id: int
    name: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"id": 1, "name": "王老师"}]}
    )


class LinkSummary(BaseModel):
    id: int
    admin_class_id: int
    teaching_class_id: int
    academic_year_id: int
    academic_year_name: str
    subject: str
    status: str
    version: int
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    # v2 §1.2.2：共享白名单与历史授权下限随 LinkSummary 暴露（逗号串拆分）
    share_categories: List[str] = Field(default_factory=list)
    share_history_from: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": 1,
                    "admin_class_id": 1,
                    "teaching_class_id": 1,
                    "academic_year_id": 1,
                    "academic_year_name": "2025-2026",
                    "subject": "物理",
                    "status": "active",
                    "version": 1,
                    "valid_from": "2025-09-01",
                    "valid_to": None,
                    "share_categories": ["roster", "current_subject_score"],
                    "share_history_from": None,
                }
            ]
        }
    )


class AcademicYearBrief(BaseModel):
    id: int
    name: str

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"id": 1, "name": "2025-2026"}]}
    )


class SharedConfigResponse(BaseModel):
    teacher: TeacherInfo
    homeroom: HomeroomConfig
    teaching: TeachingConfig
    current_academic_year: Optional[AcademicYearBrief] = None
    links: List[LinkSummary]

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "teacher": {"id": 1, "name": "王老师"},
                    "homeroom": {"configured": True, "grade": 2, "class_num": 6},
                    "teaching": {"configured": True, "subject": "物理"},
                    "current_academic_year": {"id": 1, "name": "2025-2026"},
                    "links": [
                        {
                            "id": 1,
                            "admin_class_id": 1,
                            "teaching_class_id": 1,
                            "academic_year_id": 1,
                            "academic_year_name": "2025-2026",
                            "subject": "物理",
                            "status": "active",
                            "version": 1,
                            "valid_from": "2025-09-01",
                            "valid_to": None,
                            "share_categories": ["roster", "current_subject_score"],
                            "share_history_from": None,
                        }
                    ],
                }
            ]
        }
    )


class LinkListResponse(BaseModel):
    links: List[LinkSummary]

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "links": [
                        {
                            "id": 1,
                            "admin_class_id": 1,
                            "teaching_class_id": 1,
                            "academic_year_id": 1,
                            "academic_year_name": "2025-2026",
                            "subject": "物理",
                            "status": "active",
                            "version": 1,
                            "valid_from": "2025-09-01",
                            "valid_to": None,
                            "share_categories": ["roster", "current_subject_score"],
                            "share_history_from": None,
                        }
                    ]
                }
            ]
        }
    )


class HomeroomClassInfo(BaseModel):
    """教师在该学年绑定的行政班（/shared/classes）。

    carried_from_* 非空 = 该学年尚未建立本班，目录延续自更早学年（未换届
    自动延续，零写入）；界面据此提示「延续自 X 学年」。"""

    class_id: int
    grade: int
    class_num: int
    label: Optional[str] = None
    carried_from_academic_year_id: Optional[int] = None
    carried_from_academic_year_name: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"class_id": 1, "grade": 2, "class_num": 6, "label": "高二6班"}]
        }
    )


class TeachingClassInfo(BaseModel):
    class_id: int
    label: str
    subject: str
    status: str = "active"
    carried_from_academic_year_id: Optional[int] = None
    carried_from_academic_year_name: Optional[str] = None


class TeachingClassManageRequest(BaseModel):
    academic_year_id: Optional[int] = None
    label: str
    subject: Optional[str] = None


class TeachingClassUpdateRequest(BaseModel):
    label: Optional[str] = None
    status: Optional[str] = None


class TeachingClassManageResponse(BaseModel):
    classes: List[TeachingClassInfo] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"class_id": 1, "label": "高二6班(教)", "subject": "物理"},
                {"class_id": 2, "label": "高二8班(教)", "subject": "物理"},
            ]
        }
    )


class SharedClassesResponse(BaseModel):
    academic_year_id: int
    academic_year_name: str
    homeroom: Optional[HomeroomClassInfo] = None
    teaching: List[TeachingClassInfo] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "academic_year_id": 1,
                    "academic_year_name": "2025-2026",
                    "homeroom": {"class_id": 1, "grade": 2, "class_num": 6, "label": "高二6班"},
                    "teaching": [
                        {"class_id": 1, "label": "高二6班(教)", "subject": "物理"},
                        {"class_id": 2, "label": "高二8班(教)", "subject": "物理"},
                    ],
                }
            ]
        }
    )


class ScopeResponse(BaseModel):
    """GET /shared/scope：后端解析出的当前作用域。"""

    mode: str
    data_domain: str
    subject: Optional[str] = None
    member_person_ids: List[int]
    cohort_size: int
    link_id: Optional[int] = None
    link_version: Optional[int] = None
    as_of: str
    carried_from_academic_year_id: Optional[int] = None
    carried_from_academic_year_name: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "mode": "homeroom",
                    "data_domain": "homeroom",
                    "subject": None,
                    "member_person_ids": [1, 2, 3],
                    "cohort_size": 3,
                    "link_id": 1,
                    "link_version": 1,
                    "as_of": "2026-09-11T08:00:00",
                },
                {
                    "mode": "teaching",
                    "data_domain": "teaching",
                    "subject": "物理",
                    "member_person_ids": [101, 102, 104],
                    "cohort_size": 3,
                    "link_id": None,
                    "link_version": None,
                    "as_of": "2026-09-11T08:00:00",
                },
            ]
        }
    )


class StudentBrief(BaseModel):
    person_id: int
    name: Optional[str] = None
    alias: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"person_id": 1, "name": "甲", "alias": "9900601"}]}
    )


class RosterDiff(BaseModel):
    both: List[StudentBrief] = Field(default_factory=list)
    homeroom_only: List[StudentBrief] = Field(default_factory=list)
    teaching_only: List[StudentBrief] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "both": [],
                    "homeroom_only": [
                        {"person_id": 1, "name": "甲", "alias": "9900601"},
                        {"person_id": 2, "name": "乙", "alias": "9900602"},
                        {"person_id": 3, "name": "丙", "alias": "9900603"},
                    ],
                    "teaching_only": [
                        {"person_id": 104, "name": "丁", "alias": "G2-06-01"}
                    ],
                }
            ]
        }
    )


class LinkPreviewResponse(BaseModel):
    token: str
    expires_at: str
    roster_diff: RosterDiff
    warning: str

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "token": "9f8e7d6c5b4a3210fedcba9876543210",
                    "expires_at": "2026-09-11T08:10:00",
                    "roster_diff": {
                        "both": [],
                        "homeroom_only": [
                            {"person_id": 1, "name": "甲", "alias": "9900601"},
                            {"person_id": 2, "name": "乙", "alias": "9900602"},
                            {"person_id": 3, "name": "丙", "alias": "9900603"},
                        ],
                        "teaching_only": [
                            {"person_id": 104, "name": "丁", "alias": "G2-06-01"}
                        ],
                    },
                    "warning": "同名同号候选（仅供人工确认，不自动配对）：甲（9900601）↔ 戊（9900601）",
                }
            ]
        }
    )


class LinkConfirmResponse(BaseModel):
    link_id: int
    version: int
    linked_count: int

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"link_id": 1, "version": 1, "linked_count": 0}]}
    )


class LinkCancelResponse(BaseModel):
    success: bool
    status: str

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"success": True, "status": "cancelled"}]}
    )


# ────────────────────── 学生配对与共享范围（v2 §1.2.1/§1.2.2——R6） ──────────────────────


class LinkedStudentPair(BaseModel):
    """一条已确认的跨域学生配对（linked_student 行的投影）。"""

    linked_id: int
    homeroom_person_id: int
    teaching_person_id: int
    homeroom_name: Optional[str] = None
    teaching_name: Optional[str] = None
    confirm_basis: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "linked_id": 1,
                    "homeroom_person_id": 1,
                    "teaching_person_id": 101,
                    "homeroom_name": "秦甲",
                    "teaching_name": "秦甲·T",
                    "confirm_basis": "manual_confirm:2026-09-11",
                }
            ]
        }
    )


class LinkStudentsListResponse(BaseModel):
    link_id: int
    pairs: List[LinkedStudentPair] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "link_id": 1,
                    "pairs": [
                        {
                            "linked_id": 1,
                            "homeroom_person_id": 1,
                            "teaching_person_id": 101,
                            "homeroom_name": "秦甲",
                            "teaching_name": "秦甲·T",
                            "confirm_basis": "manual_confirm:2026-09-11",
                        }
                    ],
                }
            ]
        }
    )


class LinkStudentPairInput(BaseModel):
    """待确认配对：只接受显式 person_id 对，同名同号绝不自动配对。"""

    homeroom_person_id: int
    teaching_person_id: int


class LinkStudentsCreateRequest(BaseModel):
    pairs: List[LinkStudentPairInput] = Field(default_factory=list)
    confirm_basis: Optional[str] = None


class LinkStudentsCreateResponse(BaseModel):
    created: int
    skipped: int
    pairs: List[LinkedStudentPair] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "created": 2,
                    "skipped": 0,
                    "pairs": [
                        {
                            "linked_id": 1,
                            "homeroom_person_id": 1,
                            "teaching_person_id": 101,
                            "homeroom_name": "秦甲",
                            "teaching_name": "秦甲·T",
                            "confirm_basis": "manual_confirm:2026-09-11",
                        }
                    ],
                }
            ]
        }
    )


class LinkStudentDeleteResponse(BaseModel):
    success: bool

    model_config = ConfigDict(json_schema_extra={"examples": [{"success": True}]})


class LinkShareScopeRequest(BaseModel):
    """共享范围收紧/放宽（仅 active link）。share_history_from 为 ISO 日期
    字符串或 null（null = 撤销历史授权，回到 valid_from 下限）；格式校验
    在端点内执行以保持统一错误码 JSON 形态。"""

    share_categories: Optional[List[str]] = None
    share_history_from: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"share_categories": ["roster"], "share_history_from": None},
                {"share_history_from": "2025-11-06"},
            ]
        }
    )


class LinkShareScopeResponse(BaseModel):
    link_id: int
    version: int
    share_categories: List[str] = Field(default_factory=list)
    share_history_from: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "link_id": 1,
                    "version": 2,
                    "share_categories": ["roster", "current_subject_score"],
                    "share_history_from": "2025-11-06",
                }
            ]
        }
    )


# ────────────────────────────── 学生 ──────────────────────────────


class SharedSubjectScore(BaseModel):
    """关联班学生在班主任视图看到的任教学科投影成绩。"""

    subject: str
    score: Optional[float] = None
    exam_name: str
    source_domain: str = "teaching"

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "subject": "物理",
                    "score": 88.0,
                    "exam_name": "2025期中",
                    "source_domain": "teaching",
                }
            ]
        }
    )


class StudentEntry(BaseModel):
    person_id: int
    name: Optional[str] = None
    seat_no: Optional[int] = None
    alias: Optional[str] = None
    status: Optional[str] = None
    linked_teaching_class_id: Optional[int] = None
    shared_subject_score: Optional[SharedSubjectScore] = None
    # §1.4.1 冲突提示：最近一场共享学科与 H 域同场事实值不同时，
    # 不设 shared_subject_score，改报 {"teaching_score": <对方值>}
    shared_conflict: Optional[dict] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "person_id": 1,
                    "name": "甲",
                    "seat_no": 1,
                    "alias": "9900601",
                    "status": "active",
                    "linked_teaching_class_id": 1,
                    "shared_subject_score": {
                        "subject": "物理",
                        "score": 88.0,
                        "exam_name": "2025期中",
                        "source_domain": "teaching",
                    },
                },
                {"person_id": 3, "name": "丙", "seat_no": 3, "alias": "9900603", "status": "active"},
            ]
        }
    )


class StudentsResponse(BaseModel):
    metadata: Metadata
    students: List[StudentEntry] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "metadata": {
                        "mode": "homeroom",
                        "subject": None,
                        "scope": {
                            "academic_year_id": 1,
                            "term_id": 1,
                            "class_id": 1,
                            "teaching_class_id": None,
                            "link_id": 1,
                            "link_version": 1,
                        },
                        "cohort_size": 3,
                        "data_revision": 1,
                    },
                    "students": [
                        {
                            "person_id": 1,
                            "name": "甲",
                            "seat_no": 1,
                            "alias": "9900601",
                            "status": "active",
                            "linked_teaching_class_id": 1,
                            "shared_subject_score": {
                                "subject": "物理",
                                "score": 88.0,
                                "exam_name": "2025期中",
                                "source_domain": "teaching",
                            },
                        },
                        {"person_id": 3, "name": "丙", "seat_no": 3, "alias": "9900603", "status": "active"},
                    ],
                }
            ]
        }
    )


class ProfileExam(BaseModel):
    exam_name: str
    exam_date: Optional[str] = None
    score: Optional[float] = None
    # P3 §1.3：等级分/赋分（缺考与 score 同为 null）
    grade_score: Optional[float] = None
    source_domain: str
    # §1.4.1 冲突提示（仅 homeroom 画像的 H 域条目携带，语义同 ScoreRow）
    shared_conflict: Optional[dict] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "exam_name": "2025期中",
                    "exam_date": "2025-11-10",
                    "score": 88.0,
                    "source_domain": "teaching",
                }
            ]
        }
    )


class SubjectGroup(BaseModel):
    subject: str
    exams: List[ProfileExam] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "subject": "物理",
                    "exams": [
                        {
                            "exam_name": "2025期中",
                            "exam_date": "2025-11-10",
                            "score": 88.0,
                            "source_domain": "teaching",
                        }
                    ],
                }
            ]
        }
    )


class TotalGroup(BaseModel):
    total_type: str
    exams: List[ProfileExam] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "total_type": "主三门",
                    "exams": [
                        {
                            "exam_name": "2025期中",
                            "exam_date": "2025-11-10",
                            "score": 265.0,
                            "source_domain": "homeroom",
                        }
                    ],
                }
            ]
        }
    )


class PersonInfo(BaseModel):
    person_id: int
    name: Optional[str] = None
    domain: str

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"person_id": 1, "name": "甲", "domain": "homeroom"}]}
    )


class StudentProfileResponse(BaseModel):
    metadata: Metadata
    person: PersonInfo
    subjects: List[SubjectGroup] = Field(default_factory=list)
    totals: Optional[List[TotalGroup]] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "metadata": {
                        "mode": "homeroom",
                        "subject": None,
                        "scope": {
                            "academic_year_id": 1,
                            "term_id": 1,
                            "class_id": 1,
                            "teaching_class_id": None,
                            "link_id": 1,
                            "link_version": 1,
                        },
                        "cohort_size": 3,
                        "data_revision": 1,
                    },
                    "person": {"person_id": 1, "name": "甲", "domain": "homeroom"},
                    "subjects": [
                        {
                            "subject": "语文",
                            "exams": [
                                {
                                    "exam_name": "2025期中",
                                    "exam_date": "2025-11-10",
                                    "score": 92.0,
                                    "source_domain": "homeroom",
                                }
                            ],
                        },
                        {
                            "subject": "物理",
                            "exams": [
                                {
                                    "exam_name": "2025期中",
                                    "exam_date": "2025-11-10",
                                    "score": 88.0,
                                    "source_domain": "teaching",
                                }
                            ],
                        },
                    ],
                    "totals": [
                        {
                            "total_type": "主三门",
                            "exams": [
                                {
                                    "exam_name": "2025期中",
                                    "exam_date": "2025-11-10",
                                    "score": 265.0,
                                    "source_domain": "homeroom",
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )


# ────────────────────────────── 成绩 ──────────────────────────────


class ScoreRow(BaseModel):
    person_id: int
    name: Optional[str] = None
    subject: Optional[str] = None
    score: Optional[float] = None
    # P3 §1.3：等级分/赋分（原始分在 score；缺考两者皆 null）
    grade_score: Optional[float] = None
    total_type: Optional[str] = None
    source_domain: str
    # §1.4.1 冲突提示：两域同 (人, 学科, 考试) 值不同时，本域行保留并附
    # {"teaching_score": <对方值>}；绝不静默采用对方值。teaching 侧行不携带。
    shared_conflict: Optional[dict] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "person_id": 1,
                    "name": "甲",
                    "subject": "物理",
                    "score": 88.0,
                    "total_type": None,
                    "source_domain": "teaching",
                },
                {
                    "person_id": 1,
                    "name": "甲",
                    "subject": None,
                    "score": 265.0,
                    "total_type": "主三门",
                    "source_domain": "homeroom",
                },
            ]
        }
    )


class ScoresResponse(BaseModel):
    metadata: Metadata
    rows: List[ScoreRow] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "metadata": {
                        "mode": "homeroom",
                        "subject": None,
                        "scope": {
                            "academic_year_id": 1,
                            "term_id": 1,
                            "class_id": 1,
                            "teaching_class_id": None,
                            "link_id": 1,
                            "link_version": 1,
                        },
                        "cohort_size": 3,
                        "data_revision": 1,
                    },
                    "rows": [
                        {
                            "person_id": 1,
                            "name": "甲",
                            "subject": "语文",
                            "score": 92.0,
                            "total_type": None,
                            "source_domain": "homeroom",
                        },
                        {
                            "person_id": 1,
                            "name": "甲",
                            "subject": "物理",
                            "score": 88.0,
                            "total_type": None,
                            "source_domain": "teaching",
                        },
                        {
                            "person_id": 1,
                            "name": "甲",
                            "subject": None,
                            "score": 265.0,
                            "total_type": "主三门",
                            "source_domain": "homeroom",
                        },
                    ],
                }
            ]
        }
    )


# ────────────────────────────── 导入（P3，契约 p3-imports-analysis.md §1） ──────────────────────────────


class ImportFileItem(BaseModel):
    """P1 骨架 JSON preview 的文件描述项（P3 起仅作兼容路径解析用）。"""

    filename: str
    content_digest: Optional[str] = None


class ImportsPreviewRequest(BaseModel):
    mode: str
    files: List[ImportFileItem] = Field(default_factory=list)


class ImportNewItem(BaseModel):
    """preview 识别出的新学号（不建行，confirm 才落 identity）。"""

    name: Optional[str] = None
    alias: str

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"name": "秦庚", "alias": "2025H6-09"}]}
    )


class ImportIdentityCandidate(BaseModel):
    """历史学年 alias 命中的身份候选（契约 §1.1 v2.1 / F06）。

    跨学年同号同名不自动接续——同名新生/学号回收/跨届重号无法区分，
    只能列出候选供用户在 confirm 时通过 identity_confirmations 逐项确认。"""

    alias: str
    name: Optional[str] = None
    person_id: int
    academic_year_id: Optional[int] = None
    academic_year_name: Optional[str] = None
    basis: str = "history_alias"


class ImportsPreviewItem(BaseModel):
    """单个上传文件的解析条目（契约 §1.1）。"""

    filename: str
    kind: str  # student_scores | class_averages | unknown
    parsed_ok: bool
    message: Optional[str] = None
    exam_name: Optional[str] = None
    exam_date: Optional[str] = None
    subject: Optional[str] = None
    class_label: Optional[str] = None
    row_count: int = 0
    known_students: int = 0
    new_students: List[ImportNewItem] = Field(default_factory=list)
    identity_candidates: List[ImportIdentityCandidate] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ImportsPreviewResponse(BaseModel):
    token: str
    expires_at: str
    mode: str
    items: List[ImportsPreviewItem] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "token": "0123456789abcdef0123456789abcdef",
                    "expires_at": "2026-09-11T08:10:00",
                    "mode": "homeroom",
                    "items": [
                        {
                            "filename": "高二第一学期期中.xlsx",
                            "kind": "student_scores",
                            "parsed_ok": True,
                            "exam_name": "高二第一学期期中考试",
                            "exam_date": "2025-11-01",
                            "row_count": 15,
                            "known_students": 3,
                            "new_students": [{"name": "秦庚", "alias": "2025H6-09"}],
                            "warnings": ["缺考（空分）行 1 行，按 NULL 入库不转 0"],
                        }
                    ],
                }
            ]
        }
    )


class ImportConfirmExam(BaseModel):
    exam_name: str
    exam_date: Optional[str] = None


class ImportsConfirmResponse(BaseModel):
    imported: int
    skipped: int = 0
    revised: int = 0
    exams: List[ImportConfirmExam] = Field(default_factory=list)
    students_created: int = 0
    members_synced: int = 0

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "imported": 15,
                    "skipped": 0,
                    "revised": 0,
                    "exams": [{"exam_name": "高二第一学期期中考试", "exam_date": "2025-11-01"}],
                    "students_created": 1,
                    "members_synced": 1,
                }
            ]
        }
    )


class SharedExamEntry(BaseModel):
    """该域该学年、当前班级范围内已导入的一场考试（契约 §1.4）。"""

    exam_name: str
    exam_date: Optional[str] = None
    row_count: int
    subjects: List[str] = Field(default_factory=list)


class SharedExamsResponse(BaseModel):
    exams: List[SharedExamEntry] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "exams": [
                        {
                            "exam_name": "高二第一学期期中考试",
                            "exam_date": "2025-11-06",
                            "row_count": 15,
                            "subjects": ["数学", "物理", "英语", "语文"],
                        }
                    ]
                }
            ]
        }
    )


# ────────────────────────────── 教学域变体 ──────────────────────────────
# 契约 §1.3/§1.4：teaching 模式响应"不得出现 total_type 行或其他学科键"。
# 用独立模型让键本身缺席（null 都不出现），而非靠序列化省略。


class TeachingScoreRow(BaseModel):
    """教学模式成绩行：无 total_type 键。"""

    person_id: int
    name: Optional[str] = None
    subject: str
    score: Optional[float] = None
    source_domain: str

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "person_id": 101,
                    "name": "秦甲·T",
                    "subject": "物理",
                    "score": 91.0,
                    "source_domain": "teaching",
                },
                {
                    "person_id": 104,
                    "name": "秦丁·T",
                    "subject": "物理",
                    "score": None,
                    "source_domain": "teaching",
                },
            ]
        }
    )


class TeachingScoresResponse(BaseModel):
    metadata: Metadata
    rows: List[TeachingScoreRow] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "metadata": {
                        "mode": "teaching",
                        "subject": "物理",
                        "scope": {
                            "academic_year_id": 1,
                            "term_id": None,
                            "class_id": None,
                            "teaching_class_id": None,
                            "link_id": None,
                            "link_version": None,
                        },
                        "cohort_size": 5,
                        "data_revision": 1,
                    },
                    "rows": [
                        {
                            "person_id": 101,
                            "name": "秦甲·T",
                            "subject": "物理",
                            "score": 91.0,
                            "source_domain": "teaching",
                        },
                        {
                            "person_id": 104,
                            "name": "秦丁·T",
                            "subject": "物理",
                            "score": None,
                            "source_domain": "teaching",
                        },
                    ],
                }
            ]
        }
    )


class TeachingStudentProfileResponse(BaseModel):
    """教学模式画像：仅任教学科，无 totals 键。"""

    metadata: Metadata
    person: PersonInfo
    subjects: List[SubjectGroup] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "metadata": {
                        "mode": "teaching",
                        "subject": "物理",
                        "scope": {
                            "academic_year_id": 1,
                            "term_id": None,
                            "class_id": None,
                            "teaching_class_id": 1,
                            "link_id": None,
                            "link_version": None,
                        },
                        "cohort_size": 3,
                        "data_revision": 1,
                    },
                    "person": {"person_id": 101, "name": "秦甲", "domain": "teaching"},
                    "subjects": [
                        {
                            "subject": "物理",
                            "exams": [
                                {
                                    "exam_name": "2025期中",
                                    "exam_date": "2025-11-06",
                                    "score": 91.0,
                                    "source_domain": "teaching",
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )
