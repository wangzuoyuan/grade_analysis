"""P2-C1 作业×成绩相关性 HTTP 端点（契约 docs/diagnosis-roadmap/p2-contracts.md §2）。

路由形状::

    GET /api/v1/homeroom/diagnosis/correlation?exam_name=&window_days=14|30&metric=
    GET /api/v1/teaching/diagnosis/correlation?exam_name=&window_days=14|30&metric=

作用域解析与 v1 既有端点同源（app.core.context.resolve_workspace_context；
homeroom 走 students_mgmt._homeroom_ctx，teaching 走 _teaching_ctx），
计算全部委托 app.diagnosis.correlation.exam_homework_correlation（契约
冻结签名，绝不在路由层另算）。错误契约沿用 app.api.domain_endpoint。

metric：沿用 definitions.metric_meta 词表（如 total:主三门 / subject:语文，
y 取对应「分数」口径）。homeroom 缺省 total:主三门（与 AI 工具同默认，
2026-09-29 统一）；teaching 缺省钉住
任教学科（subject:{任教学科}，传其他值 422——教学域仅任教学科，绝不跨域）。
window_days 仅允许 14|30（契约 HTTP 形状）。响应携带 calc_version="p2-v1"、
window_days、窗口起止日、sample（各层 n 与排除数）、分层 r/rho 与
note（方向与指标含义 + 「相关性不构成因果或提分保证」）。
"""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import domain_endpoint
from app.api.students_mgmt import _homeroom_ctx, _teaching_ctx
from app.core.errors import InvalidScopeParam
from app.db.models import get_db
from app.diagnosis.correlation import exam_homework_correlation

router = APIRouter(tags=["diagnosis-correlation"])

VALID_WINDOW_DAYS = (14, 30)


def _check_window_days(window_days: int) -> int:
    if window_days not in VALID_WINDOW_DAYS:
        raise InvalidScopeParam(
            "window_days must be 14 or 30",
            details={"param": "window_days", "window_days": window_days},
        )
    return window_days


# ────────────────────────────── homeroom 域 ──────────────────────────────


@router.get("/homeroom/diagnosis/correlation")
@domain_endpoint
def homeroom_diagnosis_correlation(
    exam_name: str,
    window_days: int = 14,
    metric: Optional[str] = None,
    subject: Optional[str] = None,
    homework_type: Optional[str] = None,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    """班主任域：X=窗口内作业提交率（缺省全科+关联学科；subject 可选过滤
    哪科作业），Y=metric 分数；分层=该场学校段位（高分/临界/薄弱）+ 全班。"""
    _check_window_days(window_days)
    ctx = _homeroom_ctx(db, academic_year_id, class_id, term_id)
    return exam_homework_correlation(
        db,
        ctx,
        exam_name,
        window_days=window_days,
        metric=metric or "total:主三门",
        homework_subject=subject,
        homework_type=homework_type,
    )


# ────────────────────────────── teaching 域 ──────────────────────────────


@router.get("/teaching/diagnosis/correlation")
@domain_endpoint
def teaching_diagnosis_correlation(
    exam_name: str,
    window_days: int = 14,
    metric: Optional[str] = None,
    subject: Optional[str] = None,
    homework_type: Optional[str] = None,
    academic_year_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    term_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    """教学域：X=窗口内任教学科作业提交率（subject 参数忽略，恒钉任教学科），
    Y=任教学科单科分数（范围隔离，metric 钉住任教学科）；无总分行，段位层
    如实标不可用。"""
    _check_window_days(window_days)
    ctx = _teaching_ctx(db, academic_year_id, teaching_class_id, term_id, subject)
    return exam_homework_correlation(
        db,
        ctx,
        exam_name,
        window_days=window_days,
        metric=metric or f"subject:{ctx.subject}",
        homework_type=homework_type,
    )


@router.get("/homeroom/diagnosis/homework-students")
@domain_endpoint
def homeroom_student_homework(
    subject: str,
    window_days: int = 14,
    person_id: Optional[int] = None,
    end_exam_name: Optional[str] = None,
    grade: Optional[int] = None,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    from app.diagnosis.student_homework import student_homework_analysis
    ctx = _homeroom_ctx(db, academic_year_id, class_id, None)
    return student_homework_analysis(db, ctx, subject, window_days,
        person_id=person_id, end_exam_name=end_exam_name, grade=grade)


@router.get("/homeroom/diagnosis/homework-student-subjects")
@domain_endpoint
def homeroom_student_homework_subjects(
    person_id: int,
    window_days: int = 14,
    window_mode: str = "between_exams",
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    """班主任域单人逐学科总览（仅班主任域，教学域不提供）。

    按规范学科序（语文/数学/英语/物理/化学/生物/政治/历史/地理）逐科复用
    student_homework_analysis（钉 person_id 单人、不设截止考试与年级过滤），
    timeline 为空的学科跳过。2026-10-23 起 window_mode 默认 between_exams：
    提交率统计区间为上一次考试次日至本次考试前（首场自本学期开始，学期取
    作业学期设置，缺手工行按学年日期二分推导）；window_mode=fixed 保留
    考试日前 window_days 天的既有口径。成绩口径由各科 timeline 点 basis
    字段自带（见 app/diagnosis/student_homework.py CORE_SUBJECTS 按学科
    规则）；person_id 越界 404、window_days 非 14|30 或 window_mode 非法
    422 沿用既有领域错误契约。
    """
    from app.diagnosis.student_homework import SUBJECT_ORDER, WINDOW_MODES, student_homework_analysis
    _check_window_days(window_days)
    if window_mode not in WINDOW_MODES:
        raise InvalidScopeParam("window_mode 必须是 fixed 或 between_exams")
    ctx = _homeroom_ctx(db, academic_year_id, class_id, None)
    subjects = []
    name = None
    for subject in SUBJECT_ORDER:
        students = student_homework_analysis(db, ctx, subject, window_days,
            person_id=person_id, end_exam_name=None, grade=None,
            window_mode=window_mode)["students"]
        if not students:
            continue
        name = name or students[0]["name"]
        if not students[0]["timeline"]:
            continue
        # 学生未选修/无成绩事实的学科（timeline 全部点 raw_score 与 achievement
        # 均为 None，如高二学生的非选考科目）不进入全科并列卡，避免空数据块噪音。
        if all(p["raw_score"] is None and p["achievement"] is None for p in students[0]["timeline"]):
            continue
        subjects.append({
            "subject": subject,
            "paired_n": students[0]["paired_n"],
            "total_n": len(students[0]["timeline"]),
            "timeline": [{
                "exam_key": p["exam_key"], "exam_name": p["exam_name"], "exam_date": p["exam_date"],
                "grade": p["grade"], "basis": p["basis"], "raw_score": p["raw_score"],
                "achievement": p["achievement"], "percentile": p["percentile"],
                "submission_rate": (p["homework"] or {}).get("submission_rate"),
                "submitted": (p["homework"] or {}).get("submitted"),
                "eligible": (p["homework"] or {}).get("eligible"),
                "pair_reason": p["pair_reason"],
                "window_start": p.get("window_start"),
                "window_end": p.get("window_end"),
            } for p in students[0]["timeline"]],
        })
    window_note = ("提交率统计区间为上一次考试次日至本次考试前（首场自本学期开始），不含考试当日与考后。"
                   if window_mode == "between_exams" else
                   "提交率为考试日前 window_days 天窗口，不含考试当日与考后。")
    return dict(person_id=person_id, name=name, window_days=window_days, window_mode=window_mode,
                subjects=subjects,
                note="逐科时间线口径：高一全部学科与高二/三语数英用年级百分位（换算为100−前百分比，大为好），"
                     "高二/三六选科用等级分；" + window_note +
                     "相关性等完整结论请回看单科 homework-students。")


@router.get("/teaching/diagnosis/homework-students")
@domain_endpoint
def teaching_student_homework(
    window_days: int = 14,
    person_id: Optional[int] = None,
    end_exam_name: Optional[str] = None,
    subject: Optional[str] = None,
    homework_type: Optional[str] = None,
    grade: Optional[int] = None,
    academic_year_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> dict:
    """教学域个人作业成绩分析。

    subject（2026-10-18 审查修复 12.3）：多学科教师切「全部所教班」时由
    前端显式传入并透传 _teaching_ctx（与教学域 correlation 端点同款写法），
    不再写死 None 导致多学科 422。"""
    from app.diagnosis.student_homework import student_homework_analysis
    ctx = _teaching_ctx(db, academic_year_id, teaching_class_id, None, subject)
    return student_homework_analysis(db, ctx, window_days=window_days,
        homework_type=homework_type, person_id=person_id, end_exam_name=end_exam_name, grade=grade)
