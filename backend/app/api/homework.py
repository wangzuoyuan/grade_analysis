"""/api/v1 作业批次生命周期（P5，契约 docs/contracts/p5-homework.md）。

本模块骨架由集成者预建（含挂载），P5-BE 子代理在此填充实现：
- §1 录入 preview/confirm（full/names/detailed 三模式 + 全交展开 + 例外）
- §1.3 编辑/撤销（revision 乐观锁 + 撤销冲突）
- §2 读取/看板（含 rate_unavailable 标注）
- §3 预警时间轴（事件口径 streak，默认已交打断）
- §4 相关性（Pearson 两域口径）
- §5 学期管理（ws_homework_semester）

核心语义（契约 §0，违反即返工）：
- subject（学科）与 homework_type（作业种类）分列；同日同科同种类多份
  作业 = 多个 assignment（不同 batch_token），绝不合并。
- 应交分母 = assignment.expected_members_json 确认时快照，不是当天人数；
  快照为空 → rate 为 null 且 rate_unavailable=true，绝不推断其余全交。
- 采用例外登记：应交名单中没有缺交/请假记录的人默认已交；旧 unknown
  兼容读取为已交，不再作为面向老师的业务状态。
- 双向共享：assignment 归属一域（data_domain + class_ref_id），对侧经
  active link + 有效期 + LinkedStudent 交集 + share_categories 含
  current_subject_homework 才可读/写同一事实；跨域读一律经投影映射
  person_id（复用 _queries 的 eligible_linked_pairs 门），绝不绕过门自写
  SQL 读对侧；其他教学班（如 T8）永不可见。
- v2（G01/G02/G03，契约 §0）：跨域共享按 assignment.assigned_date 逐批次
  过事件时点门（link 事件时点有效期 + share_history_from 下限 + 双侧成员
  覆盖 assigned_date 的交集），查询时点有效不等于历史批次放行；跨域可写
  集 = 事件时点授权成员 ∩ 批次 expected_members_json 快照；跨域撤销只作
  用于共享成员事实，整批撤销仅源域可发起。
- 全部写路径两段式（preview token → confirm，R4 语义）+ 单事务；同
  token 重试幂等返回既有结果；绝不物理删 assignment/submission 行。
"""

import json
import math
import secrets
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Set, Tuple

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import _queries as q
from app.api import current_teacher_id, domain_endpoint
from app.api.homework_schemas import (
    HomeworkAssignmentDetail,
    HomeworkAssignmentListResponse,
    HomeworkAssignmentListItem,
    HomeworkConfirmRequest,
    HomeworkConfirmResponse,
    HomeworkCorrelationPair,
    HomeworkCorrelationResponse,
    CurrentSemesterResponse,
    HomeworkDashboardGroup,
    HomeworkDashboardResponse,
    HomeworkDeleteResponse,
    HomeworkExistingBatch,
    HomeworkPatchRequest,
    HomeworkPatchResponse,
    HomeworkPersonBrief,
    HomeworkPreviewAssignment,
    HomeworkPreviewRequest,
    HomeworkPreviewResponse,
    HomeworkRecentMissing,
    HomeworkSemesterCurrentResponse,
    HomeworkSemesterEntry,
    HomeworkSemesterRestoreResponse,
    HomeworkSemestersResponse,
    HomeworkStatsExclusionEntry,
    HomeworkStatsExclusionResponse,
    HomeworkStatsExclusionSetRequest,
    HomeworkStudentEvent,
    HomeworkStudentResponse,
    HomeworkStudentStreaks,
    HomeworkSubmissionBrief,
    HomeworkSubmissionOut,
    HomeworkWarningStudent,
    HomeworkAuxWarningStudent,
    HomeworkWarningsResponse,
    HomeworkWarningDismissRequest,
    HomeworkWarningDismissResponse,
    SemesterCreateRequest,
    SemesterUpdateRequest,
)
from app.api.shared import _load_preview_batch
from app.api.students import _metadata
from app.core.context import VALID_MODES, WorkspaceContext, resolve_workspace_context
from app.core.errors import (
    DomainError,
    InvalidScopeParam,
    LinkVersionConflict,
    ResourceOutOfScope,
    WorkspaceNotConfigured,
)
from app.db.models import get_db
from app.db.workspace_models import (
    AcademicYear,
    Enrollment,
    HomeworkAssignment,
    HomeworkStatsExclusion,
    HomeworkSubmission,
    HomeroomTeachingLink,
    ImportBatch,
    TeachingClassMember,
    WsStudentNote,
    WsHomeworkSemester,
)

router = APIRouter(tags=["homework"])

# 数据库仍兼容历史 unknown；所有业务读取会把它归一为 submitted。
_VALID_STATUSES = ("submitted", "missing", "excused", "unknown")
_PUBLIC_STATUSES = ("submitted", "missing", "excused")
_VALID_INPUT_KINDS = ("full", "names", "detailed")
_ATTENDANCE_WORDS = ("没来", "迟到", "早退", "旷课", "缺课", "缺席")
_FORGOT_WORDS = ("忘带", "没带", "未带")
_NEGATIVE_EVALUATIONS = (
    "不合格", "不认真", "马虎", "潦草", "敷衍", "不工整", "退步",
    "差劲", "作业乱", "作业没做", "错误率高",
)
# 单字「差」不做子串匹配（"误差分析""差错订正"等作业名含差字）：
# 仅整段就是「差」，或以「差：细节内容」打头时才认定为负面评价。
_POOR_MARKERS = ("差：", "差:")
# 撤销冲突判定的时间容差：同事务内 confirm 写入的行 updated_at 会比
# assignment.created_at 晚微小量，不视为"后续编辑"；真正的编辑另由
# submission.revision > 1 精确刻画。
_REVOKE_EDIT_TOLERANCE = timedelta(seconds=1)
DEFAULT_TOTAL_TYPE = "主三门"


# ────────────────────────────── 通用小工具 ──────────────────────────────


def _parse_date(value: Optional[str], name: str, required: bool = False) -> Optional[date]:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise InvalidScopeParam(f"{name} is required", details={"param": name})
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise InvalidScopeParam(
            f"{name} must be an ISO date (YYYY-MM-DD)", details={"param": name}
        ) from exc


def _non_empty(value: Optional[str], name: str) -> str:
    text = (value or "").strip()
    if not text:
        raise InvalidScopeParam(
            f"{name} must be a non-empty string", details={"param": name}
        )
    return text


def _parse_expected_ids(raw: str) -> List[int]:
    """expected_members_json → 发起域 person_id 列表。兼容 R10 时期的
    裸数组与可能的对象封装（{"person_ids": [...]}）。"""
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        return []
    if isinstance(data, dict):
        data = data.get("person_ids") or []
    if not isinstance(data, list):
        return []
    return [int(pid) for pid in data]


def _min_rank_desc(values_by_key: Dict[int, float]) -> Dict[int, int]:
    """同分同名次（min-rank，1,2,2,4）：名次 = 1 + 严格更高值人数。
    与 analysis 的 _rank_map 同规则但独立实现（任务书要求不 import，
    避免跨模块耦合）；值越大名次越小（分数/总分的自然口径）。"""
    vals = list(values_by_key.values())
    return {
        key: 1 + sum(1 for other in vals if other > value)
        for key, value in values_by_key.items()
    }


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    """样本 Pearson 相关系数；零方差 → None（调用方转 caveat）。"""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return None
    return cov / math.sqrt(vx * vy)


def _streaks_of(statuses: Sequence[str]) -> Tuple[Optional[int], str, int]:
    """按 assigned_date 升序的事件状态序列 → (current_missing_streak,
    streak_basis, longest_missing_streak)。

    - current：从最新事件往前数连续 missing，遇 submitted 停；
      excused 跳过（既不计缺交，也不打断）。
    - longest：excused 跳过，submitted 中断连续段。历史 unknown 在进入
      本函数前已按新的例外登记口径归一为 submitted。"""
    current: Optional[int] = 0
    basis = "events"
    for status in reversed(list(statuses)):
        if status == "missing":
            current = (current or 0) + 1
        elif status in ("attendance", "excused"):
            continue
        else:  # submitted 打断
            break
    longest = 0
    run = 0
    for status in statuses:
        if status == "missing":
            run += 1
            longest = max(longest, run)
        elif status in ("attendance", "excused"):
            continue
        else:
            run = 0
    return current, basis, longest


def _attendance_of(evaluation: Optional[str]) -> Optional[str]:
    """出勤与作业收交正交：保留老师原始表述，不借此推断已交/缺交。"""
    text = (evaluation or "").strip()
    if not text:
        return None
    for part in text.split("｜"):
        if any(word in part for word in _ATTENDANCE_WORDS):
            return part.strip()
    return None


def _forgot_of(evaluation: Optional[str]) -> Optional[str]:
    """从作业行中识别明确的忘带记录；普通谈话备注不走此入口。"""
    text = (evaluation or "").strip()
    if text and any(word in text for word in _FORGOT_WORDS):
        return text
    return None


def _special_note_of(evaluation: Optional[str]) -> Optional[str]:
    """提取忘带等特殊情况事实，避免塞入质量评价列。"""
    text = (evaluation or "").strip()
    if not text:
        return None
    for part in text.split("｜"):
        part_clean = part.strip()
        if any(word in part_clean for word in _FORGOT_WORDS):
            return part_clean
    return None


def _sync_forgot_note(
    db: Session,
    assignment: HomeworkAssignment,
    person_id: int,
    evaluation: Optional[str],
) -> None:
    """把日常作业录入中的忘带与迟到/没来出勤异常同步为可追溯的独立预警事实。
    清除作业行上的忘带时保留一条已清除标记，避免物理删除审计事实。
    """
    source = f"homework:{assignment.id}"
    note = (
        db.query(WsStudentNote)
        .filter(
            WsStudentNote.data_domain == assignment.data_domain,
            WsStudentNote.person_id == person_id,
            WsStudentNote.source == source,
        )
        .one_or_none()
    )
    forgot = _forgot_of(evaluation)
    att = _attendance_of(evaluation)
    tag = None
    if forgot:
        tag = f"[忘带] {forgot}"
    elif att:
        tag = f"[{att}] {assignment.subject}"

    if tag:
        if note is None:
            db.add(WsStudentNote(
                data_domain=assignment.data_domain,
                person_id=person_id,
                date=assignment.assigned_date,
                category="其他",
                content=tag,
                source=source,
            ))
        else:
            note.date = assignment.assigned_date
            note.content = tag
    elif note is not None:
        note.content = "[作业记录已清除]"


def _is_pure_missing(submission: HomeworkSubmission) -> bool:
    """是否计入连续缺交统计：只有纯缺交才计入连续缺交。
    迟到、没来等出勤异常与忘带/没带，均不计入连续缺交统计。
    """
    if _effective_status(submission) != "missing":
        return False
    if _attendance_of(submission.evaluation):
        return False
    if _forgot_of(submission.evaluation):
        return False
    return True


def _streak_status(submission: HomeworkSubmission) -> str:
    """为 _streaks_of 映射状态：迟到/没来/忘带映射为 'attendance' 跳过（不累加，不打断）。"""
    eff = _effective_status(submission)
    if eff == "missing":
        if _attendance_of(submission.evaluation) or _forgot_of(submission.evaluation):
            return "attendance"
        return "missing"
    return eff


# 连续缺交统一按天口径（预警端点与画像端点共用，单一实现）：
# - 连缺线：班主任 =（域, 班, 学科）；教学 =（域, 班）单线，不再按作业种类分线。
#   轴 = 该线范围内全部批次（不分种类）的 assigned_date 全集，保持按班分界，
#   B 班的收交日不得重置 A 班学生的连击。
# - 天判定：任一行 missing → 该天计 1（同日多种作业缺只算 1 天）；否则任一行
#   submitted（含合成的默认已交）→ 清零停止；当天只有 attendance/excused → 跳过；
#   当天该生无任何行：当天批次全部是 legacy（无应交快照）→ 跳过（无法核实），
#   否则（有新批次）→ 默认已交、清零停止。
LineKey = Tuple[str, int, Optional[str]]


def _day_merged_statuses(
    rows_by_date: Dict[date, List[str]],
    day_is_legacy: Dict[date, List[bool]],
) -> List[str]:
    """把某条连缺线内该生的逐行状态按 assigned_date 合并成天状态序列
    （喂给 _streaks_of；跳过的天不出现 = 既不累加也不打断）。"""
    statuses: List[str] = []
    for day in sorted(day_is_legacy):
        rows = rows_by_date.get(day) or []
        if not rows:
            if day_is_legacy[day] and all(day_is_legacy[day]):
                continue  # 当天只有 legacy 批次，无法核实，跳过
            statuses.append("submitted")  # 有新批次而无例外行 → 默认已交
            continue
        if "missing" in rows:
            statuses.append("missing")
        elif "submitted" in rows:
            statuses.append("submitted")
    return statuses


def _streak_line_of(
    line_events: Sequence[Tuple[HomeworkAssignment, HomeworkSubmission]],
    line_axis: Sequence[HomeworkAssignment],
    legacy_ids: Set[int],
) -> Tuple[int, str, int]:
    """一条连缺线的按天 (current, basis, longest)。basis 恒 'events'
    （按天序列走 _streaks_of；legacy 日期轴另有 _legacy_homeroom_streak）。"""
    rows_by_date: Dict[date, List[str]] = {}
    for a, s in line_events:
        rows_by_date.setdefault(a.assigned_date, []).append(_streak_status(s))
    day_is_legacy: Dict[date, List[bool]] = {}
    for a in line_axis:
        day_is_legacy.setdefault(a.assigned_date, []).append(a.id in legacy_ids)
    statuses = _day_merged_statuses(rows_by_date, day_is_legacy)
    current, basis, longest = _streaks_of(statuses)
    return current, basis, longest


def _legacy_homeroom_streak(
    line_events: Sequence[Tuple[HomeworkAssignment, HomeworkSubmission]],
    line_axis: Sequence[HomeworkAssignment],
) -> Tuple[int, str]:
    """旧班主任迁移数据（仅缺交历史、无应交快照）按学科班的日期轴连缺：
    该生日无缺交（含全交台账日、migration-only 日）即结束连续段，但保留
    已从最新日数出的连缺值。返回 (current, basis='legacy_events')。"""
    own_by_date: Dict[date, List[str]] = {}
    for a, s in line_events:
        own_by_date.setdefault(a.assigned_date, []).append(_streak_status(s))
    rows_by_date: Dict[date, List[HomeworkAssignment]] = {}
    for a in line_axis:
        rows_by_date.setdefault(a.assigned_date, []).append(a)
    current = 0
    for event_date in reversed(sorted(rows_by_date)):
        statuses = own_by_date.get(event_date, [])
        if "missing" in statuses:
            current += 1
        elif any(status in ("attendance", "excused") for status in statuses):
            continue
        else:
            # 该生日已交（无论新台账全交日还是 migration:h 收交日）→ 连续段
            # 在此结束，保留已数值、不清零——清零会把「最近一次缺交」也抹掉，
            # 令整卡连续缺交预警配合 min_streak 过滤后显示为空。
            break
    return current, "legacy_events"


def _streak_lines_of(
    mode: str,
    events: Sequence[Tuple[HomeworkAssignment, HomeworkSubmission]],
    line_axis_map: Dict[LineKey, List[HomeworkAssignment]],
    legacy_ids: Set[int],
) -> List[Tuple[Optional[int], str, int, Optional[str], date, int]]:
    """某人全部事件逐连缺线计算 (current, basis, longest, 维度, 最后事件日,
    纯缺交数)。班主任按（域, 班, 学科）分线；教学按（域, 班）单线。
    homework_warnings 与 homework_student 共用，保证口径一致。"""
    grouped: Dict[LineKey, List[Tuple[HomeworkAssignment, HomeworkSubmission]]] = {}
    for a, s in events:
        dimension = a.subject if mode == "homeroom" else None
        grouped.setdefault((a.data_domain, a.class_ref_id, dimension), []).append((a, s))
    lines: List[Tuple[Optional[int], str, int, Optional[str], date, int]] = []
    for key, line_events in grouped.items():
        line_axis = line_axis_map.get(key, [])
        if mode == "homeroom" and any(a.id in legacy_ids for a in line_axis):
            current, basis = _legacy_homeroom_streak(line_events, line_axis)
            longest = 0
        else:
            current, basis, longest = _streak_line_of(line_events, line_axis, legacy_ids)
        last_date = max(a.assigned_date for a, _s in line_events)
        missing_in_line = sum(1 for _a, s in line_events if _is_pure_missing(s))
        lines.append((current, basis, longest, key[2], last_date, missing_in_line))
    return lines


def _normalized_row_semantics(
    status: str, evaluation: Optional[str], attendance: Optional[str]
) -> Tuple[str, Optional[str]]:
    """出勤文本与质量评价共存于原评价字段，API 分列返回。"""
    evaluation_text = (evaluation or "").strip()
    attendance_text = (attendance or "").strip()
    if attendance_text:
        if not any(word in attendance_text for word in _ATTENDANCE_WORDS):
            raise InvalidScopeParam(
                "attendance must describe 没来/迟到/早退/旷课/缺课/缺席",
                details={"attendance": attendance_text},
            )
        stored = "｜".join(part for part in (attendance_text, evaluation_text) if part)
        return "submitted" if status == "unknown" else status, stored
    if _attendance_of(evaluation_text):
        return "submitted" if status == "unknown" else status, evaluation_text
    return ("submitted" if status == "unknown" else status), evaluation_text or None


def _effective_status(submission: HomeworkSubmission) -> str:
    return "submitted" if submission.submission_status == "unknown" else submission.submission_status


def _evaluation_tone(value: Optional[str]) -> str:
    """按「｜」分段判定评价倾向；出勤/忘带片段由 _quality_text 剥离口径另行处理。"""
    text = (value or "").strip()
    if not text:
        return "neutral"
    for part in text.split("｜"):
        part = part.strip()
        if not part:
            continue
        if part == "差" or part.startswith(_POOR_MARKERS):
            return "negative"
        if any(word in part for word in _NEGATIVE_EVALUATIONS):
            return "negative"
    return "neutral"


def _quality_text(value: Optional[str]) -> str:
    """从兼容存储字段中剔除出勤与忘带等非评价片段，只返回纯作业质量评价。"""
    return "｜".join(
        part.strip()
        for part in (value or "").split("｜")
        if (
            part.strip()
            and not any(word in part for word in _ATTENDANCE_WORDS)
            and not any(word in part for word in _FORGOT_WORDS)
        )
    )


# ────────────────────────────── 作用域与跨域门 ──────────────────────────────


def _resolve_hw_scope(
    db: Session,
    teacher_id: int,
    mode: Optional[str],
    academic_year_id: Optional[int],
    class_id: Optional[int],
    teaching_class_id: Optional[int],
    subject: Optional[str] = None,
) -> WorkspaceContext:
    """读端点统一作用域解析：homeroom 缺省教师绑定班（显式非绑定班 404）；
    teaching 走 build_teaching_params（teaching_class_id 缺省 = 全部所教班
    并集）。mode 缺失/非法 → 422。"""
    if mode is None or mode not in VALID_MODES:
        raise InvalidScopeParam(
            "mode must be 'homeroom' or 'teaching'", details={"param": "mode"}
        )
    if mode == "homeroom":
        params: dict = {"academic_year_id": academic_year_id}
        if class_id is not None:
            q.check_admin_class_exists(db, class_id)
            params["class_id"] = class_id
        return resolve_workspace_context(db, teacher_id, "homeroom", params)
    params, _subject, _ids = q.build_teaching_params(
        db, academic_year_id, teaching_class_id, subject
    )
    return resolve_workspace_context(db, teacher_id, "teaching", params)


def _link_valid_at(link: HomeroomTeachingLink, as_of: date) -> bool:
    return link.valid_from <= as_of and (
        link.valid_to is None or link.valid_to >= as_of
    )


def _links_for_ctx(db: Session, ctx: WorkspaceContext) -> List[HomeroomTeachingLink]:
    """当前作用域可经其跨域读作业的 active link（已含查询时点有效期）。"""
    links: List[HomeroomTeachingLink] = []
    if ctx.mode == "homeroom":
        if ctx.link_id is not None:
            link = db.get(HomeroomTeachingLink, ctx.link_id)
            if (
                link is not None
                and link.status == "active"
                and _link_valid_at(link, ctx.as_of)
            ):
                links.append(link)
    else:
        seen: Set[int] = set()
        for tc_id in ctx.class_ids:
            link = q.active_link_for_teaching_class(
                db, tc_id, ctx.academic_year_id, ctx.as_of
            )
            if link is not None and link.id not in seen:
                seen.add(link.id)
                links.append(link)
    return links


def _homework_share_links(db: Session, ctx: WorkspaceContext) -> List[HomeroomTeachingLink]:
    """作业类共享的候选 link：link active + 查询时点有效期 + share_categories
    含 current_subject_homework。

    这里只回答"此刻是否存在关联"；具体批次能否跨域可见/可写由
    _event_time_mapping 按 assigned_date 逐批次裁决（G01：查询时点
    关联有效绝不等于历史批次放行）。"""
    links: List[HomeroomTeachingLink] = []
    for link in _links_for_ctx(db, ctx):
        if "current_subject_homework" in q.share_categories_of(link):
            links.append(link)
    return links


def _event_time_mapping(
    db: Session, link: HomeroomTeachingLink, ctx: WorkspaceContext, a: HomeworkAssignment
) -> Optional[Dict[int, int]]:
    """G01 事件时点门（契约 §0 v2）：单批次按 assigned_date 核验。

    - link 在事件时点仍处有效期（[valid_from, valid_to]）；
    - assigned_date >= max(valid_from, share_history_from ?? valid_from)
      （share_history_from 收紧后，更早的历史批次对侧立即不可见）；
    - share_categories 含 current_subject_homework；
    - 双侧成员有效期覆盖 assigned_date（eligible_linked_pairs 事件时点
      交集，历史 status 不参与——G07 口径）。
    全部通过 → {发起域 person_id → 读域 person_id}（仅事件时点授权成员）；
    任一不满足 → None（该批次对侧不可见、不可写）。"""
    if not _link_valid_at(link, a.assigned_date):
        return None
    if a.assigned_date < q.share_history_floor(link):
        return None
    if "current_subject_homework" not in q.share_categories_of(link):
        return None
    pairs = q.eligible_linked_pairs(db, link, a.assigned_date, require_active_status=False)
    if ctx.mode == "homeroom":
        return {t: h for h, t in pairs.items()}
    return dict(pairs)


def _visible_assignments(
    db: Session, ctx: WorkspaceContext, active_only: bool = False
) -> List[Tuple[HomeworkAssignment, Optional[Dict[int, int]]]]:
    """当前作用域可见的作业批次 [(assignment, 发起域→读域映射|None)]。

    直读：同 data_domain + class_ref_id ∈ ctx.class_ids + 当学年。
    跨域：经 _homework_share_links 门，且 assignment.class_ref_id == 对侧
    班、subject == link.subject（任教学科外的作业绝不互见）、当学年，
    再逐批次过 _event_time_mapping 事件时点门（G01）。
    映射 None 表示直读（person_id 无需换算）。"""
    items: Dict[int, Tuple[HomeworkAssignment, Optional[Dict[int, int]]]] = {}
    direct = db.query(HomeworkAssignment).filter(
        HomeworkAssignment.data_domain == ctx.data_domain,
        HomeworkAssignment.class_ref_id.in_(list(ctx.class_ids)),
        HomeworkAssignment.academic_year_id == ctx.academic_year_id,
    )
    if active_only:
        direct = direct.filter(HomeworkAssignment.status == "active")
    for a in direct.all():
        items[a.id] = (a, None)
    other_domain = "teaching" if ctx.mode == "homeroom" else "homeroom"
    for link in _homework_share_links(db, ctx):
        other_class = (
            link.teaching_class_id if ctx.mode == "homeroom" else link.admin_class_id
        )
        rows = db.query(HomeworkAssignment).filter(
            HomeworkAssignment.data_domain == other_domain,
            HomeworkAssignment.class_ref_id == other_class,
            HomeworkAssignment.academic_year_id == ctx.academic_year_id,
            HomeworkAssignment.subject == link.subject,
        )
        if active_only:
            rows = rows.filter(HomeworkAssignment.status == "active")
        for a in rows.all():
            if a.id in items:
                continue
            mapping = _event_time_mapping(db, link, ctx, a)
            if mapping is None:
                continue
            items[a.id] = (a, mapping)
    return list(items.values())


def _load_accessible_assignment(
    db: Session, ctx: WorkspaceContext, assignment_id: int
) -> Tuple[HomeworkAssignment, Optional[Dict[int, int]]]:
    """单批次读/写的访问解析：直读本域班；跨域经共享门（含 subject 匹配）
    加 assigned_date 事件时点门（G01）。不可见一律 404（不向探测性请求
    泄露其他班资源存在性）。"""
    a = db.get(HomeworkAssignment, assignment_id)
    if a is None:
        raise ResourceOutOfScope(
            "assignment not found", details={"assignment_id": assignment_id}
        )
    if a.data_domain == ctx.data_domain:
        if (
            a.academic_year_id == ctx.academic_year_id
            and a.class_ref_id in ctx.class_ids
        ):
            return a, None
        raise ResourceOutOfScope(
            "assignment not in current workspace scope",
            details={"assignment_id": assignment_id},
        )
    other_domain = "teaching" if ctx.mode == "homeroom" else "homeroom"
    if a.data_domain == other_domain:
        for link in _homework_share_links(db, ctx):
            other_class = (
                link.teaching_class_id
                if ctx.mode == "homeroom"
                else link.admin_class_id
            )
            if (
                a.class_ref_id == other_class
                and a.subject == link.subject
                and a.academic_year_id == ctx.academic_year_id
            ):
                mapping = _event_time_mapping(db, link, ctx, a)
                if mapping is not None:
                    return a, mapping
    raise ResourceOutOfScope(
        "assignment not in current workspace scope",
        details={"assignment_id": assignment_id},
    )


def _projected_submissions(
    db: Session, a: HomeworkAssignment, mapping: Optional[Dict[int, int]]
) -> List[Tuple[HomeworkSubmission, int]]:
    """批次的逐人行投影为读域视角 [(submission, 读域 person_id)]。
    跨域时只保留 LinkedStudent 交集内的行（其余成员是对方域私有名册）。"""
    subs = (
        db.query(HomeworkSubmission)
        .filter(HomeworkSubmission.assignment_id == a.id)
        .all()
    )
    if mapping is None:
        return [(s, s.person_id) for s in subs]
    return [(s, mapping[s.person_id]) for s in subs if s.person_id in mapping]


def _legacy_expected_ids(db: Session, a: HomeworkAssignment) -> List[int]:
    """旧迁移批次没有快照时，只按原班级与事件日的有效成员补读写投影。"""
    if a.data_domain == "homeroom":
        rows = db.query(Enrollment.identity_id).filter(
            Enrollment.admin_class_id == a.class_ref_id,
            Enrollment.valid_from <= a.assigned_date,
            (Enrollment.valid_to.is_(None)) | (Enrollment.valid_to >= a.assigned_date),
        ).all()
    else:
        rows = db.query(TeachingClassMember.identity_id).filter(
            TeachingClassMember.teaching_class_id == a.class_ref_id,
            TeachingClassMember.valid_from <= a.assigned_date,
            (TeachingClassMember.valid_to.is_(None)) | (TeachingClassMember.valid_to >= a.assigned_date),
        ).all()
    return sorted({row[0] for row in rows})


def _effective_expected_ids(db: Session, a: HomeworkAssignment) -> List[int]:
    """返回可用于业务统计的应交名单。

    新批次使用确认时快照；旧迁移批次的源系统只保存例外，因此按原班级、
    原日期的有效成员恢复名单。恢复只发生在原范围内，不扩大跨域权限。
    """
    expected = _parse_expected_ids(a.expected_members_json)
    if not expected and a.batch_token.startswith("migration:"):
        expected = _legacy_expected_ids(db, a)
    return expected


def _projected_expected(
    db: Session, a: HomeworkAssignment, mapping: Optional[Dict[int, int]]
) -> List[int]:
    expected = _effective_expected_ids(db, a)
    if mapping is None:
        return expected
    return [mapping[pid] for pid in expected if pid in mapping]


def _assignment_status_counts(
    db: Session, a: HomeworkAssignment, mapping: Optional[Dict[int, int]],
    skip_reader_ids: Optional[Set[int]] = None,
) -> Tuple[List[int], List[Tuple[HomeworkSubmission, int]], Dict[str, int]]:
    """按例外登记口径统计批次。

    应交名单里没有逐人行的人默认已交；旧 unknown 也归为已交。显式缺交和
    请假覆盖默认值。极少数历史快照外的保留行继续计入，避免丢失旧例外。
    skip_reader_ids（ADR-023 统计排除）只影响聚合计数：这些读域 id 的
    应交与逐人行都不计入，行本身仍然保留。
    """
    expected_reader = _projected_expected(db, a, mapping)
    pairs = _projected_submissions(db, a, mapping)
    status_by_reader = {rid: _effective_status(s) for s, rid in pairs}
    reader_ids = sorted(set(expected_reader) | set(status_by_reader))
    if skip_reader_ids:
        reader_ids = [rid for rid in reader_ids if rid not in skip_reader_ids]
    counts = {key: 0 for key in _PUBLIC_STATUSES}
    for rid in reader_ids:
        status = status_by_reader.get(rid, "submitted")
        if status not in counts:
            status = "submitted"
        counts[status] += 1
    return reader_ids, pairs, counts


def _stats_excluded_ids(
    db: Session, data_domain: str, class_ref_ids: Sequence[int]
) -> Set[int]:
    """班级维度（ADR-023）被排除作业统计的写域身份集合；行存在即排除。"""
    if not class_ref_ids:
        return set()
    rows = db.query(HomeworkStatsExclusion.identity_id).filter(
        HomeworkStatsExclusion.data_domain == data_domain,
        HomeworkStatsExclusion.class_ref_id.in_(list(class_ref_ids)),
    ).all()
    return {row[0] for row in rows}


def _excluded_readers_of(
    db: Session, a: HomeworkAssignment, mapping: Optional[Dict[int, int]]
) -> Set[int]:
    """某批次在当前读域视角下被排除的 reader id 集合。

    排除按批次归属班（写域）登记；跨域读时经 mapping 投影回读域 id。
    未映射到的排除身份（不在交集内）本就不可见，无需处理。"""
    source_excluded = _stats_excluded_ids(db, a.data_domain, [a.class_ref_id])
    if not source_excluded:
        return set()
    if mapping is None:
        return source_excluded
    return {mapping[pid] for pid in source_excluded if pid in mapping}


def _rate_stats_of(
    expected_count: int,
    submitted: int,
    missing: int,
    excused: int,
) -> Tuple[Optional[float], bool]:
    """分母 = 快照人数 − excused；快照为空（或扣完）→ rate null +
    rate_unavailable（仅缺交历史不推断全交，契约 §0）。"""
    denom = expected_count - excused
    if expected_count == 0 or denom <= 0:
        return None, True
    return round(submitted / denom, 4), False


def _current_semester_bounds(db: Session, academic_year_id: int) -> Tuple[date, date]:
    ay = db.get(AcademicYear, academic_year_id)
    if ay is None:
        raise ResourceOutOfScope("academic year not found", details={"academic_year_id": academic_year_id})
    semester = db.query(WsHomeworkSemester).filter(
        WsHomeworkSemester.academic_year_id == academic_year_id,
        WsHomeworkSemester.is_current == 1,
    ).order_by(WsHomeworkSemester.id.desc()).first()
    return (semester.start_date, semester.end_date) if semester is not None else (ay.start_date, ay.end_date)


# ────────────────────────────── §1.1 preview ──────────────────────────────


def _resolve_roster_ref(
    roster: List[dict],
    person_id: Optional[int],
    name_or_alias: Optional[str],
) -> dict:
    """在应交成员快照内解析一个人：person_id 直接命中；学号（alias）精确
    命中优先消歧；姓名唯一命中；同班同名多解 → 422 列候选（契约 §1.1）。"""
    if person_id is not None:
        for item in roster:
            if item["person_id"] == person_id:
                return item
        raise InvalidScopeParam(
            "person not in expected members of this class",
            details={"person_id": person_id},
        )
    ref = (name_or_alias or "").strip()
    if not ref:
        raise InvalidScopeParam(
            "each row needs person_id or name_or_alias", details={"param": "rows"}
        )
    by_alias = [item for item in roster if item.get("alias") and item["alias"] == ref]
    if len(by_alias) == 1:
        return by_alias[0]
    if len(by_alias) > 1:
        raise InvalidScopeParam(
            "alias is ambiguous in this class",
            details={
                "name_or_alias": ref,
                "candidates": [
                    {"person_id": i["person_id"], "name": i["name"], "alias": i.get("alias")}
                    for i in by_alias
                ],
            },
        )
    by_name = [item for item in roster if (item.get("name") or "") == ref]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        # H01 红线：同班同名必须 422 列候选，绝不静默取第一个
        raise InvalidScopeParam(
            "姓名歧义：班内存在多名同名学生，请用学号或 person_id 消歧",
            details={
                "name_or_alias": ref,
                "candidates": [
                    {"person_id": i["person_id"], "name": i["name"], "alias": i.get("alias")}
                    for i in by_name
                ],
            },
        )
    raise InvalidScopeParam(
        "name not found in current roster", details={"name_or_alias": ref}
    )


def _merge_resolved_rows(
    resolved: List[Tuple[int, str, Optional[str]]]
) -> Dict[int, Tuple[str, Optional[str]]]:
    """同人多行合并：完全相同去重；两种状态（或评价）矛盾 → 422
    （H01：请求内同人两行不同状态拒绝）。"""
    merged: Dict[int, Tuple[str, Optional[str]]] = {}
    for pid, status, evaluation in resolved:
        prev = merged.get(pid)
        if prev is not None and prev != (status, evaluation):
            raise InvalidScopeParam(
                "同人同批存在两种矛盾状态，请检查录入行",
                details={"person_id": pid},
            )
        merged.setdefault(pid, (status, evaluation))
    return merged


def _validate_status(status: str) -> str:
    if status not in _VALID_STATUSES:
        raise InvalidScopeParam(
            "status must be one of submitted/missing/excused",
            details={"status": status},
        )
    # 兼容旧草稿或旧客户端；新界面不再提供 unknown。
    return "submitted" if status == "unknown" else status


def _preview_target_teaching_class(
    db: Session, teaching_class_id: Optional[int], academic_year_id: Optional[int], subject: str
) -> int:
    """写入路径的 teaching 目标班：缺省仅当该学年该学科只有一个教学班时
    自动命中，多班必须显式（422）——绝不往"全部所教班并集"写批次。"""
    if teaching_class_id is not None:
        return teaching_class_id
    ay = q.resolve_year(db, academic_year_id)
    resolved = q.teaching_subject_for_year(db, ay.id, subject)
    ids = q.teaching_class_ids_for_subject(db, ay.id, resolved)
    if len(ids) != 1:
        raise InvalidScopeParam(
            "teaching_class_id is required when multiple teaching classes exist",
            details={"param": "teaching_class_id", "teaching_class_ids": ids},
        )
    return ids[0]


@router.post("/homework/preview", response_model=HomeworkPreviewResponse)
@domain_endpoint
def homework_preview(req: HomeworkPreviewRequest, db: Session = Depends(get_db)):
    """零写入解析（仅 import_batch 台账行）：三模式展开 + 成员快照 +
    existing_batches 提示；token 化后由 confirm 单事务落地。"""
    teacher_id = current_teacher_id(db)
    if req.mode not in VALID_MODES:
        raise InvalidScopeParam(
            "mode must be 'homeroom' or 'teaching'", details={"param": "mode"}
        )
    subject = _non_empty(req.subject, "subject")
    homework_type = _non_empty(req.homework_type, "homework_type")
    assigned_date = _parse_date(req.assigned_date, "assigned_date", required=True)
    due_date = _parse_date(req.due_date, "due_date")
    spec = req.input
    if spec.kind not in _VALID_INPUT_KINDS:
        raise InvalidScopeParam(
            "input.kind must be full/names/detailed", details={"kind": spec.kind}
        )

    if req.mode == "homeroom":
        params: dict = {"academic_year_id": req.academic_year_id}
        if req.class_id is not None:
            q.check_admin_class_exists(db, req.class_id)
            params["class_id"] = req.class_id
        ctx = resolve_workspace_context(db, teacher_id, "homeroom", params)
        target_class = ctx.class_ids[0]
        roster = q.homeroom_roster(db, target_class, ctx.as_of)
    else:
        target_class = _preview_target_teaching_class(
            db, req.teaching_class_id, req.academic_year_id, subject
        )
        teach_params, _s, _ids = q.build_teaching_params(
            db, req.academic_year_id, target_class, subject
        )
        ctx = resolve_workspace_context(db, teacher_id, "teaching", teach_params)
        roster = q.teaching_roster(db, [target_class], ctx.as_of)

    # 三模式解析（先解析全批次再应用例外，行顺序不影响结果）
    resolved: List[Tuple[int, str, Optional[str]]] = []
    if spec.kind == "full":
        # 全交台账：快照全员 submitted；例外在合并后的覆盖层上应用
        # （同名多人仍先经冲突检查，再整体覆盖全量行——契约 §1.1）
        merged = {item["person_id"]: ("submitted", None) for item in roster}
        exception_rows: List[Tuple[int, str, Optional[str]]] = []
        for exc in spec.exceptions or []:
            status = _validate_status(exc.status)
            status, evaluation = _normalized_row_semantics(status, exc.evaluation, exc.attendance)
            item = _resolve_roster_ref(roster, exc.person_id, exc.name_or_alias)
            exception_rows.append((item["person_id"], status, evaluation))
        merged.update(_merge_resolved_rows(exception_rows))
    elif spec.kind == "names":
        names = [n.strip() for n in (spec.names or []) if n and n.strip()]
        if not names:
            raise InvalidScopeParam(
                "input.names must not be empty for kind=names",
                details={"param": "input.names"},
            )
        for name in names:
            item = _resolve_roster_ref(roster, None, name)
            resolved.append((item["person_id"], "submitted", None))
        merged = _merge_resolved_rows(resolved)
    else:  # detailed
        if not spec.rows:
            raise InvalidScopeParam(
                "input.rows must not be empty for kind=detailed",
                details={"param": "input.rows"},
            )
        for row in spec.rows:
            status = _validate_status(row.status)
            status, evaluation = _normalized_row_semantics(status, row.evaluation, row.attendance)
            item = _resolve_roster_ref(roster, row.person_id, row.name_or_alias)
            resolved.append((item["person_id"], status, evaluation))
        merged = _merge_resolved_rows(resolved)

    existing = (
        db.query(HomeworkAssignment)
        .filter(
            HomeworkAssignment.data_domain == ctx.data_domain,
            HomeworkAssignment.class_ref_id == target_class,
            HomeworkAssignment.academic_year_id == ctx.academic_year_id,
            HomeworkAssignment.subject == subject,
            HomeworkAssignment.homework_type == homework_type,
            HomeworkAssignment.assigned_date == assigned_date,
            HomeworkAssignment.status == "active",
        )
        .all()
    )
    warnings: List[str] = []
    if existing:
        warnings.append(
            f"同日同科同种类已有 {len(existing)} 个 active 批次：可编辑既有批次，"
            "确认后将新建独立批次（同日多份作业不自动叠加）"
        )
    if due_date is not None and due_date < assigned_date:
        warnings.append("due_date 早于 assigned_date，请核对")

    expected_ids = [item["person_id"] for item in roster]
    name_map = {item["person_id"]: item["name"] for item in roster}
    ordered_ids = [pid for pid in expected_ids if pid in merged]
    token = secrets.token_hex(16)
    expires_at = datetime.utcnow() + timedelta(minutes=q.PREVIEW_TTL_MINUTES)
    snapshot = {
        "kind": "homework_preview",
        "mode": ctx.mode,
        "data_domain": ctx.data_domain,
        "academic_year_id": ctx.academic_year_id,
        "class_ref_id": target_class,
        "subject": subject,
        "homework_type": homework_type,
        "assigned_date": assigned_date.isoformat(),
        "due_date": due_date.isoformat() if due_date else None,
        "expected_member_ids": expected_ids,
        "submissions": [
            [pid, merged[pid][0], merged[pid][1]] for pid in ordered_ids
        ],
        "ctx_class_ids": list(ctx.class_ids),
        "ctx_member_ids": list(ctx.member_person_ids),
        "ctx_subject": ctx.subject,
        "ctx_link_id": ctx.link_id,
        "ctx_link_version": ctx.link_version,
        "as_of": ctx.as_of.isoformat(),
    }
    db.add(
        ImportBatch(
            token=token,
            data_domain=ctx.data_domain,
            scope_json=json.dumps(snapshot, ensure_ascii=False),
            status="pending",
            expires_at=expires_at,
        )
    )
    db.commit()
    return HomeworkPreviewResponse(
        token=token,
        expires_at=expires_at.isoformat(),
        assignment=HomeworkPreviewAssignment(
            subject=subject,
            homework_type=homework_type,
            assigned_date=assigned_date.isoformat(),
            due_date=due_date.isoformat() if due_date else None,
            expected_members=[
                HomeworkPersonBrief(person_id=pid, name=name_map.get(pid))
                for pid in expected_ids
            ],
            submissions=[
                HomeworkSubmissionBrief(
                    person_id=pid,
                    name=name_map.get(pid),
                    status=merged[pid][0],
                    evaluation=_quality_text(merged[pid][1]) or None,
                    attendance=_attendance_of(merged[pid][1]),
                    special_note=_special_note_of(merged[pid][1]),
                    quality_negative=_evaluation_tone(merged[pid][1]) == "negative",
                )
                for pid in ordered_ids
            ],
            warnings=warnings,
        ),
        existing_batches=[
            HomeworkExistingBatch(
                assignment_id=a.id, batch_token=a.batch_token, revision=a.revision
            )
            for a in existing
        ],
    )


# ────────────────────────────── §1.2 confirm ──────────────────────────────


def _assignment_counts(db: Session, a: HomeworkAssignment) -> HomeworkConfirmResponse:
    _reader_ids, _pairs, counts = _assignment_status_counts(db, a, None)
    return HomeworkConfirmResponse(
        assignment_id=a.id,
        revision=a.revision,
        submitted=counts["submitted"],
        missing=counts["missing"],
        excused=counts["excused"],
    )


@router.post("/homework/confirm", response_model=HomeworkConfirmResponse)
@domain_endpoint
def homework_confirm(req: HomeworkConfirmRequest, db: Session = Depends(get_db)):
    """token 单次消费 + 漂移校验 + 单事务写入；同 token 重试幂等返回既有
    批次统计（不新增，契约 §1.2 / H02）。"""
    teacher_id = current_teacher_id(db)

    # 幂等路径先行：batch 已 confirmed 的作业预览 → 返回既有批次统计
    existing_batch = (
        db.query(ImportBatch).filter(ImportBatch.token == req.token).first()
    )
    if existing_batch is not None and existing_batch.status == "confirmed":
        snap0 = json.loads(existing_batch.scope_json or "{}")
        if snap0.get("kind") == "homework_preview":
            a = (
                db.query(HomeworkAssignment)
                .filter(HomeworkAssignment.batch_token == req.token)
                .one_or_none()
            )
            if a is not None:
                return _assignment_counts(db, a)
        # confirmed 但不是作业预览/批次丢失 → 走下方统一 409（已消费）

    batch = _load_preview_batch(db, req.token)
    snapshot = json.loads(batch.scope_json or "{}")
    if snapshot.get("kind") != "homework_preview":
        raise LinkVersionConflict(
            "token is not a homework preview", details={"token": req.token}
        )

    try:
        if snapshot["mode"] == "homeroom":
            ctx = resolve_workspace_context(
                db,
                teacher_id,
                "homeroom",
                {
                    "academic_year_id": snapshot["academic_year_id"],
                    "class_id": snapshot["class_ref_id"],
                },
            )
        else:
            params, _s, _ids = q.build_teaching_params(
                db,
                snapshot["academic_year_id"],
                snapshot["class_ref_id"],
                snapshot["subject"],
            )
            ctx = resolve_workspace_context(db, teacher_id, "teaching", params)
    except DomainError as exc:
        if isinstance(exc, LinkVersionConflict):
            raise
        # 范围已无法解析（学年/班级/学科配置变化）→ 统一 409
        raise LinkVersionConflict(
            f"preview scope no longer valid: {exc.message}", details=exc.details
        ) from exc

    # 漂移校验（R4）：班级/学年/学科/关联/成员集合任一变化 → 409 零写入
    drift: dict = {}
    if ctx.academic_year_id != snapshot.get("academic_year_id"):
        drift["academic_year_id"] = [
            snapshot.get("academic_year_id"), ctx.academic_year_id,
        ]
    if list(ctx.class_ids) != list(snapshot.get("ctx_class_ids") or []):
        drift["class_ids"] = [snapshot.get("ctx_class_ids"), list(ctx.class_ids)]
    if list(ctx.member_person_ids) != list(snapshot.get("ctx_member_ids") or []):
        drift["member_person_ids"] = [
            snapshot.get("ctx_member_ids"), list(ctx.member_person_ids),
        ]
    if (ctx.subject or None) != (snapshot.get("ctx_subject") or None):
        drift["subject"] = [snapshot.get("ctx_subject"), ctx.subject]
    if ctx.link_id != snapshot.get("ctx_link_id"):
        drift["link_id"] = [snapshot.get("ctx_link_id"), ctx.link_id]
    if ctx.link_version != snapshot.get("ctx_link_version"):
        drift["link_version"] = [snapshot.get("ctx_link_version"), ctx.link_version]
    if drift:
        raise LinkVersionConflict(
            "preview scope changed", details={"drift": drift}
        )

    # 单事务写入（任一步失败全量回滚，batch 保持 pending 可重试）
    try:
        assignment = HomeworkAssignment(
            data_domain=snapshot["data_domain"],
            class_ref_id=snapshot["class_ref_id"],
            academic_year_id=snapshot["academic_year_id"],
            subject=snapshot["subject"],
            homework_type=snapshot["homework_type"],
            assigned_date=date.fromisoformat(snapshot["assigned_date"]),
            due_date=(
                date.fromisoformat(snapshot["due_date"])
                if snapshot.get("due_date")
                else None
            ),
            batch_token=batch.token,
            expected_members_json=json.dumps(
                snapshot["expected_member_ids"], ensure_ascii=False
            ),
            revision=1,
            status="active",
        )
        db.add(assignment)
        db.flush()
        now = datetime.utcnow()
        for pid, status, evaluation in snapshot["submissions"]:
            db.add(
                HomeworkSubmission(
                    assignment_id=assignment.id,
                    person_id=pid,
                    submission_status=status,
                    evaluation=evaluation,
                    submitted_at=now if status == "submitted" else None,
                )
            )
            _sync_forgot_note(db, assignment, pid, evaluation)
        batch.status = "confirmed"
        db.commit()
    except DomainError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return _assignment_counts(db, assignment)


# ────────────────────────────── §1.3 详情 / 编辑 / 撤销 ──────────────────────────────


def _resolve_universe(
    db: Session, person_ids: Sequence[int], data_domain: str
) -> List[dict]:
    """构造与 homeroom_roster/teaching_roster 同形的解析表
    [{person_id, name, alias}]，供 PATCH 行的 person_id/学号/姓名消歧。"""
    ids = sorted(set(person_ids))
    names = q.names_for(db, ids)
    aliases = q.aliases_for(db, ids, data_domain)
    return [
        {"person_id": pid, "name": names.get(pid), "alias": aliases.get(pid)}
        for pid in ids
    ]


@router.get(
    "/homework/assignments/{assignment_id}", response_model=HomeworkAssignmentDetail
)
@domain_endpoint
def homework_assignment_detail(
    assignment_id: int,
    mode: Optional[str] = None,
    class_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    academic_year_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    teacher_id = current_teacher_id(db)
    ctx = _resolve_hw_scope(
        db, teacher_id, mode, academic_year_id, class_id, teaching_class_id
    )
    a, mapping = _load_accessible_assignment(db, ctx, assignment_id)
    expected_reader = _projected_expected(db, a, mapping)
    editable_init = _parse_expected_ids(a.expected_members_json)
    if not editable_init and a.batch_token.startswith("migration:"):
        editable_init = _legacy_expected_ids(db, a)
    editable_reader = (
        editable_init if mapping is None
        else [mapping[pid] for pid in editable_init if pid in mapping]
    )
    counted_ids, pairs, counts = _assignment_status_counts(db, a, mapping)
    reader_ids = sorted(set(editable_reader) | set(counted_ids))
    names = q.names_for(db, reader_ids)
    pair_by_reader = {rid: s for s, rid in pairs}
    rate, unavailable = _rate_stats_of(
        len(expected_reader),
        counts["submitted"],
        counts["missing"],
        counts["excused"],
    )
    return HomeworkAssignmentDetail(
        metadata=_metadata(ctx),
        assignment_id=a.id,
        data_domain=a.data_domain,
        academic_year_id=a.academic_year_id,
        subject=a.subject,
        homework_type=a.homework_type,
        assigned_date=a.assigned_date.isoformat(),
        due_date=a.due_date.isoformat() if a.due_date else None,
        revision=a.revision,
        status=a.status,
        expected_count=len(expected_reader),
        submitted=counts["submitted"],
        missing=counts["missing"],
        excused=counts["excused"],
        submission_rate=rate,
        rate_unavailable=unavailable,
        expected_members=[
            HomeworkPersonBrief(person_id=pid, name=names.get(pid))
            for pid in expected_reader
        ],
        submissions=[
            HomeworkSubmissionOut(
                person_id=rid,
                name=names.get(rid),
                status=(
                    _effective_status(pair_by_reader[rid])
                    if rid in pair_by_reader else "submitted"
                ),
                evaluation=(
                    _quality_text(pair_by_reader[rid].evaluation) or None
                    if rid in pair_by_reader else None
                ),
                attendance=(
                    _attendance_of(pair_by_reader[rid].evaluation)
                    if rid in pair_by_reader else None
                ),
                special_note=(
                    _special_note_of(pair_by_reader[rid].evaluation)
                    if rid in pair_by_reader else None
                ),
                quality_negative=(
                    _evaluation_tone(pair_by_reader[rid].evaluation) == "negative"
                    if rid in pair_by_reader else False
                ),
            )
            for rid in reader_ids
        ],
    )


@router.patch(
    "/homework/assignments/{assignment_id}", response_model=HomeworkPatchResponse
)
@domain_endpoint
def homework_assignment_patch(
    assignment_id: int,
    req: HomeworkPatchRequest,
    mode: Optional[str] = None,
    class_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    academic_year_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """逐行 upsert（唯一键 assignment_id+person_id）；revision 乐观锁
    （旧版 409）；跨域写经共享门映射回发起域 person_id（H04 同一事实）。"""
    teacher_id = current_teacher_id(db)
    ctx = _resolve_hw_scope(
        db, teacher_id, mode, academic_year_id, class_id, teaching_class_id
    )
    a, mapping = _load_accessible_assignment(db, ctx, assignment_id)
    if a.status != "active":
        raise LinkVersionConflict(
            "assignment is revoked", details={"assignment_id": a.id}
        )
    if req.revision != a.revision:
        raise LinkVersionConflict(
            "revision conflict: assignment was modified",
            details={"assignment_id": a.id, "current_revision": a.revision},
        )
    due_date = _parse_date(req.due_date, "due_date") if req.due_date is not None else None
    assigned_date = (
        _parse_date(req.assigned_date, "assigned_date")
        if req.assigned_date is not None
        else None
    )
    has_type = req.homework_type is not None
    has_subject = req.subject is not None
    has_rows = req.rows is not None
    if (
        not has_rows
        and req.due_date is None
        and req.assigned_date is None
        and not has_type
        and not has_subject
    ):
        raise InvalidScopeParam(
            "nothing to update: provide rows, due_date, assigned_date, homework_type, and/or subject",
            details={"param": "rows"},
        )

    updated = 0
    if has_rows:
        expected_init = _parse_expected_ids(a.expected_members_json)
        if not expected_init and a.batch_token.startswith("migration:"):
            expected_init = _legacy_expected_ids(db, a)
        if mapping is None:
            reader_to_init = {pid: pid for pid in expected_init}
        else:
            # G03：跨域可写集 = 事件时点授权成员 ∩ 该批次应交快照；
            # 快照外的人不在解析表内，行解析即 422（零写入）——状态
            # upsert 不得暗中扩人，分母与提交率因此不会被改写。
            reader_to_init = {
                mapping[pid]: pid for pid in expected_init if pid in mapping
            }
        universe = _resolve_universe(
            db, reader_to_init.keys(), ctx.data_domain
        )
        resolved: List[Tuple[int, str, Optional[str]]] = []
        for row in req.rows or []:
            status = _validate_status(row.status)
            status, evaluation = _normalized_row_semantics(status, row.evaluation, row.attendance)
            item = _resolve_roster_ref(universe, row.person_id, row.name_or_alias)
            resolved.append(
                (reader_to_init[item["person_id"]], status, evaluation)
            )
        merged = _merge_resolved_rows(resolved)
        try:
            for pid, (status, evaluation) in merged.items():
                existing = (
                    db.query(HomeworkSubmission)
                    .filter(
                        HomeworkSubmission.assignment_id == a.id,
                        HomeworkSubmission.person_id == pid,
                    )
                    .one_or_none()
                )
                if existing is None:
                    db.add(
                        HomeworkSubmission(
                            assignment_id=a.id,
                            person_id=pid,
                            submission_status=status,
                            evaluation=evaluation,
                            submitted_at=(
                                datetime.utcnow() if status == "submitted" else None
                            ),
                        )
                    )
                else:
                    existing.submission_status = status
                    existing.evaluation = evaluation
                    existing.revision = (existing.revision or 1) + 1
                    if status == "submitted" and existing.submitted_at is None:
                        existing.submitted_at = datetime.utcnow()
                _sync_forgot_note(db, a, pid, evaluation)
                updated += 1
        except DomainError:
            db.rollback()
            raise
    if req.due_date is not None:
        a.due_date = due_date
    if assigned_date is not None:
        a.assigned_date = assigned_date
    if has_type:
        new_type = (req.homework_type or "").strip()
        if not new_type:
            raise InvalidScopeParam(
                "homework_type cannot be empty", details={"param": "homework_type"}
            )
        a.homework_type = new_type
    if has_subject:
        new_sub = (req.subject or "").strip()
        if not new_sub:
            raise InvalidScopeParam(
                "subject cannot be empty", details={"param": "subject"}
            )
        if ctx.data_domain == "teaching" and new_sub not in ctx.authorized_subjects:
            raise InvalidScopeParam(
                f"subject {new_sub} is not authorized for current teacher",
                details={"param": "subject"},
            )
        a.subject = new_sub
    a.revision = (a.revision or 1) + 1
    db.commit()
    return HomeworkPatchResponse(
        assignment_id=a.id, revision=a.revision, updated=updated
    )


def _revoke_conflicts(
    db: Session, a: HomeworkAssignment, subs: List[Tuple[HomeworkSubmission, int]]
) -> List[dict]:
    """批次撤销的后续编辑冲突清单（契约 §1.3）：行状态/评价在批次创建后
    被改过（submission.revision > 1，或 updated_at 晚于同事务写入容差）。"""
    conflicts: List[dict] = []
    for s, rid in subs:
        edited_after_create = (s.revision or 1) > 1 or (
            s.updated_at is not None
            and a.created_at is not None
            and s.updated_at > a.created_at + _REVOKE_EDIT_TOLERANCE
        )
        if edited_after_create:
            conflicts.append(
                {
                    "person_id": rid,
                    "name": q.names_for(db, [rid]).get(rid),
                    "submission_status": s.submission_status,
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                }
            )
    return conflicts


@router.delete(
    "/homework/assignments/{assignment_id}", response_model=HomeworkDeleteResponse
)
@domain_endpoint
def homework_assignment_delete(
    assignment_id: int,
    mode: Optional[str] = None,
    class_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    academic_year_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """软撤销（status='revoked'，绝不物理删行）；其上有晚于批次创建的
    评价/状态编辑 → 409 列冲突清单。撤销后读侧不再计入指标（自然重算）。

    G02 跨域撤销边界：跨域请求只允许作用于共享成员事实——批次内存在
    本工作台无权管理的成员（快照内、事件时点授权交集外的源域独有成员，
    或快照外的越界行）→ 整批撤销 409，且错误只说明原因，绝不列源域
    私有学生姓名；全部成员均为共享且无冲突时才允许撤销（此时源域独有
    成员不存在，整批 revoked 合法）。整批（含非共享成员）的依赖检查
    由源域 DELETE 承担。"""
    teacher_id = current_teacher_id(db)
    ctx = _resolve_hw_scope(
        db, teacher_id, mode, academic_year_id, class_id, teaching_class_id
    )
    a, mapping = _load_accessible_assignment(db, ctx, assignment_id)
    if a.status == "revoked":
        # 幂等：重复撤销不重复递增 revision
        return HomeworkDeleteResponse(
            success=True, assignment_id=a.id, status="revoked", revision=a.revision
        )
    if mapping is not None:
        # 跨域：先核验无权管理的成员（含快照外越界行），命中即整批拒绝
        expected_init = set(_parse_expected_ids(a.expected_members_json))
        if not expected_init and a.batch_token.startswith("migration:"):
            expected_init = set(_legacy_expected_ids(db, a))
        all_rows = (
            db.query(HomeworkSubmission.person_id)
            .filter(HomeworkSubmission.assignment_id == a.id)
            .all()
        )
        managed = expected_init | {row[0] for row in all_rows}
        if any(pid not in mapping for pid in managed):
            raise LinkVersionConflict(
                "该批次含本工作台无权管理的成员，跨域撤销被拒绝"
                "（整批撤销仅数据归属域可发起）",
                details={"assignment_id": a.id},
            )
        conflicts = _revoke_conflicts(
            db, a, _projected_submissions(db, a, mapping)
        )
    else:
        # 源域：全批次（含全部成员）依赖检查
        conflicts = _revoke_conflicts(db, a, _projected_submissions(db, a, None))
    if conflicts:
        raise LinkVersionConflict(
            "批次存在后续评价编辑，撤销被拒绝（请先确认冲突清单）",
            details={"assignment_id": a.id, "conflicts": conflicts},
        )
    a.status = "revoked"
    a.revision = (a.revision or 1) + 1
    db.commit()
    return HomeworkDeleteResponse(
        success=True, assignment_id=a.id, status="revoked", revision=a.revision
    )


# ────────────────────────────── §2 列表 / 看板 / 学生事件流 ──────────────────────────────


def _list_item_of(
    db: Session, a: HomeworkAssignment, mapping: Optional[Dict[int, int]]
) -> HomeworkAssignmentListItem:
    expected_reader = _projected_expected(db, a, mapping)
    _reader_ids, pairs, counts = _assignment_status_counts(db, a, mapping)
    attendance_count = sum(1 for s, _rid in pairs if _attendance_of(s.evaluation))
    negative_count = sum(1 for s, _rid in pairs if _evaluation_tone(s.evaluation) == "negative")
    rate, unavailable = _rate_stats_of(
        len(expected_reader),
        counts["submitted"],
        counts["missing"],
        counts["excused"],
    )
    excused_ids = [rid for s, rid in pairs if _effective_status(s) == "excused"]
    missing_ids = [
        rid for s, rid in pairs
        if _effective_status(s) == "missing" and a.subject != "考勤"
    ]
    attendance_ids = [
        rid for s, rid in pairs
        if _attendance_of(s.evaluation) or (a.subject == "考勤" and _effective_status(s) == "missing")
    ]
    negative_ids = [
        rid for s, rid in pairs
        if _evaluation_tone(s.evaluation) == "negative"
    ]
    # 忘带名单与既有 4 类例外 ID 同一套 reader id 映射；仅作标注，不改任何计数口径
    forgot_ids = [rid for s, rid in pairs if _forgot_of(s.evaluation)]
    return HomeworkAssignmentListItem(
        assignment_id=a.id,
        data_domain=a.data_domain,
        subject=a.subject,
        homework_type=a.homework_type,
        assigned_date=a.assigned_date.isoformat(),
        due_date=a.due_date.isoformat() if a.due_date else None,
        revision=a.revision,
        status=a.status,
        attendance_count=attendance_count,
        negative_count=negative_count,
        expected_count=len(expected_reader),
        submitted=counts["submitted"],
        missing=counts["missing"],
        excused=counts["excused"],
        excused_ids=excused_ids,
        missing_ids=missing_ids,
        attendance_ids=attendance_ids,
        negative_ids=negative_ids,
        forgot_ids=forgot_ids,
        submission_rate=rate,
        rate_unavailable=unavailable,
    )


@router.get("/homework/assignments", response_model=HomeworkAssignmentListResponse)
@domain_endpoint
def homework_assignments_list(
    mode: Optional[str] = None,
    class_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    academic_year_id: Optional[int] = None,
    subject: Optional[str] = None,
    homework_type: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """分页列表（assigned_date 降序）：直读本域班 + 经共享门的对侧投影；
    含计数与 submission_rate（快照为空 → rate null + rate_unavailable）。"""
    if limit < 1 or offset < 0:
        raise InvalidScopeParam(
            "limit must be >= 1 and offset >= 0",
            details={"param": "limit"},
        )
    teacher_id = current_teacher_id(db)
    ctx = _resolve_hw_scope(
        db, teacher_id, mode, academic_year_id, class_id, teaching_class_id, subject
    )
    date_from = _parse_date(from_date, "from_date")
    date_to = _parse_date(to_date, "to_date")
    subject_f = (subject or "").strip() or None
    type_f = (homework_type or "").strip() or None

    entries = []
    for a, mapping in _visible_assignments(db, ctx):
        if subject_f is not None and a.subject != subject_f:
            continue
        if type_f is not None and a.homework_type != type_f:
            continue
        if date_from is not None and a.assigned_date < date_from:
            continue
        if date_to is not None and a.assigned_date > date_to:
            continue
        entries.append((a, mapping))
    entries.sort(key=lambda item: (item[0].assigned_date, item[0].id), reverse=True)
    total = len(entries)
    page = entries[offset : offset + limit]
    return HomeworkAssignmentListResponse(
        metadata=_metadata(ctx),
        total=total,
        items=[_list_item_of(db, a, mapping) for a, mapping in page],
    )


@router.get("/homework/dashboard", response_model=HomeworkDashboardResponse)
@domain_endpoint
def homework_dashboard(
    mode: Optional[str] = None,
    class_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    academic_year_id: Optional[int] = None,
    subject: Optional[str] = None,
    homework_type: Optional[str] = None,
    group_by: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """按期聚合（day=布置日，week=ISO 周一，month=月首）；rate 仅计有可靠分母的批次，
    组内全部无分母 → rate null + rate_unavailable。"""
    if group_by not in ("day", "week", "month"):
        raise InvalidScopeParam(
            "group_by must be 'day', 'week' or 'month'", details={"param": "group_by"}
        )
    teacher_id = current_teacher_id(db)
    ctx = _resolve_hw_scope(
        db, teacher_id, mode, academic_year_id, class_id, teaching_class_id, subject
    )
    subject_f = (subject or "").strip() or None
    type_f = (homework_type or "").strip() or None
    period_start, period_end = _current_semester_bounds(db, ctx.academic_year_id)

    def _label(d: date) -> str:
        if group_by == "day":
            return d.isoformat()
        if group_by == "week":
            monday = d - timedelta(days=d.weekday())
            return monday.isoformat()
        return d.replace(day=1).isoformat()

    groups: Dict[str, dict] = {}
    for a, mapping in _visible_assignments(db, ctx, active_only=True):
        if a.assigned_date < period_start or a.assigned_date > period_end:
            continue
        if subject_f is not None and a.subject != subject_f:
            continue
        if type_f is not None and a.homework_type != type_f:
            continue
        # ADR-023：被排除学生的应交与逐人行都不计入看板聚合
        skip_ids = _excluded_readers_of(db, a, mapping)
        expected_reader = [
            pid for pid in _projected_expected(db, a, mapping) if pid not in skip_ids
        ]
        _reader_ids, pairs, counts = _assignment_status_counts(
            db, a, mapping, skip_reader_ids=skip_ids
        )
        neg_count = sum(
            1 for s, rid in pairs
            if rid not in skip_ids and _evaluation_tone(s.evaluation) == "negative"
        )
        denom = len(expected_reader) - counts["excused"]
        bucket = groups.setdefault(
            _label(a.assigned_date),
            {"assignments": 0, "submitted": 0, "missing": 0, "excused": 0,
             "negative_count": 0,
             "expected": 0, "rate_sub": 0, "rate_denom": 0,
             "computable": 0},
        )
        bucket["assignments"] += 1
        bucket["negative_count"] += neg_count
        for key in _PUBLIC_STATUSES:
            bucket[key] += counts[key]
        # expected_count = 组内各批次应交快照人数的真实合计（v2 契约债务：
        # 不再恒 0；快照为空的批次自然贡献 0，rate_unavailable 语义不变）
        bucket["expected"] += len(expected_reader)
        if expected_reader and denom > 0:
            bucket["computable"] += 1
            bucket["rate_denom"] += denom
            bucket["rate_sub"] += counts["submitted"]
    result = []
    for label in sorted(groups):
        b = groups[label]
        if b["computable"] == 0 or b["rate_denom"] <= 0:
            rate, unavailable = None, True
        else:
            rate, unavailable = round(b["rate_sub"] / b["rate_denom"], 4), False
        result.append(
            HomeworkDashboardGroup(
                label=label,
                assignments=b["assignments"],
                expected_count=b["expected"],
                submitted=b["submitted"],
                missing=b["missing"],
                excused=b["excused"],
                negative_count=b.get("negative_count", 0),
                submission_rate=rate,
                rate_unavailable=unavailable,
            )
        )
    return HomeworkDashboardResponse(
        metadata=_metadata(ctx),
        group_by=group_by,
        basis="day",
        groups=result,
    )


def _person_events(
    db: Session,
    ctx: WorkspaceContext,
    person_id: int,
    academic_year_id: Optional[int] = None,
    all_history: bool = False,
) -> Tuple[List[Tuple[HomeworkAssignment, HomeworkSubmission]], Dict[LineKey, List[HomeworkAssignment]]]:
    """该生（读域 person_id）的作业事件（active 批次，assigned_date 升序）
    及连缺线批次轴。

    - 直读：若显式指定 academic_year_id（且 not all_history）则严格过滤该学年；
      未指定学年或 all_history=True 时查全部学年（H05 跨学年跟人）。
      没有例外行时合成 submitted 事件，使后续全交批次能正确中断连续缺交。
    - 跨域：经共享门取关联班、link.subject 的批次，逐批次过
      _event_time_mapping 事件时点门（G01：作业后入班者看不到入班前
      批次的记录），person 经映射换算。
    - 连缺线轴：与事件同一可见性口径，收齐每条线（班主任 =（域, 班, 学科），
      教学 =（域, 班））范围内全部 active 批次（含该生无行的默认已交日），
      供 _streak_lines_of 按天判定使用。"""
    events: List[Tuple[HomeworkAssignment, HomeworkSubmission]] = []
    line_axis_map: Dict[LineKey, List[HomeworkAssignment]] = {}
    target_ay_id = None if all_history else academic_year_id
    direct_query = db.query(HomeworkAssignment).filter(
        HomeworkAssignment.data_domain == ctx.data_domain,
        HomeworkAssignment.status == "active",
    )
    if target_ay_id is not None:
        direct_query = direct_query.filter(
            HomeworkAssignment.academic_year_id == target_ay_id
        )
    direct_assignments = direct_query.all()
    if ctx.mode == "teaching":
        direct_assignments = [a for a in direct_assignments if a.subject == ctx.subject]
    for a in direct_assignments:
        line_axis_map.setdefault(_line_key_of(ctx.mode, a), []).append(a)
        row = db.query(HomeworkSubmission).filter(
            HomeworkSubmission.assignment_id == a.id,
            HomeworkSubmission.person_id == person_id,
        ).one_or_none()
        if row is None and person_id not in _effective_expected_ids(db, a):
            continue
        events.append((a, row or HomeworkSubmission(
            assignment_id=a.id,
            person_id=person_id,
            submission_status="submitted",
        )))
    other_domain = "teaching" if ctx.mode == "homeroom" else "homeroom"
    for link in _homework_share_links(db, ctx):
        other_class = (
            link.teaching_class_id if ctx.mode == "homeroom" else link.admin_class_id
        )
        cross_query = (
            db.query(HomeworkAssignment)
            .filter(
                HomeworkAssignment.data_domain == other_domain,
                HomeworkAssignment.class_ref_id == other_class,
                HomeworkAssignment.subject == link.subject,
                HomeworkAssignment.status == "active",
            )
        )
        if target_ay_id is not None:
            cross_query = cross_query.filter(
                HomeworkAssignment.academic_year_id == target_ay_id
            )
        cross = cross_query.all()
        for a in cross:
            mapping = _event_time_mapping(db, link, ctx, a)
            if mapping is None:
                continue
            line_axis_map.setdefault(_line_key_of(ctx.mode, a), []).append(a)
            init_id = {v: k for k, v in mapping.items()}.get(person_id)
            if init_id is None:
                continue
            row = db.query(HomeworkSubmission).filter(
                HomeworkSubmission.assignment_id == a.id,
                HomeworkSubmission.person_id == init_id,
            ).one_or_none()
            if row is None and init_id not in _effective_expected_ids(db, a):
                continue
            events.append((a, row or HomeworkSubmission(
                assignment_id=a.id,
                person_id=init_id,
                submission_status="submitted",
            )))
    events.sort(key=lambda item: (item[0].assigned_date, item[0].id))
    return events, line_axis_map


def _person_events_for_members(
    db: Session,
    ctx: WorkspaceContext,
    person_ids: Sequence[int],
    academic_year_id: int,
) -> Dict[int, Tuple[List[Tuple[HomeworkAssignment, HomeworkSubmission]], Dict[LineKey, List[HomeworkAssignment]]]]:
    """B1 整班读取作业事件；口径沿用 _person_events，请求内共用批次与例外行。

    每个批次的应交名单只解析一次，旧迁移批次的事件日成员也只恢复一次。
    跨域映射仍经 G01 事件时点门，并按 link+日期复用同日结果。
    """
    ids = set(person_ids) & set(ctx.member_person_ids)
    result = {pid: ([], {}) for pid in ids}
    if not ids or not ctx.class_ids:
        return result

    direct_query = db.query(HomeworkAssignment).filter(
        HomeworkAssignment.data_domain == ctx.data_domain,
        HomeworkAssignment.class_ref_id.in_(list(ctx.class_ids)),
        HomeworkAssignment.academic_year_id == academic_year_id,
        HomeworkAssignment.status == "active",
    )
    if ctx.mode == "teaching":
        direct_query = direct_query.filter(HomeworkAssignment.subject == ctx.subject)
    direct = direct_query.all()

    cross: List[Tuple[HomeworkAssignment, Dict[int, int]]] = []
    other_domain = "teaching" if ctx.mode == "homeroom" else "homeroom"
    for link in _homework_share_links(db, ctx):
        other_class = link.teaching_class_id if ctx.mode == "homeroom" else link.admin_class_id
        assignments = db.query(HomeworkAssignment).filter(
            HomeworkAssignment.data_domain == other_domain,
            HomeworkAssignment.class_ref_id == other_class,
            HomeworkAssignment.academic_year_id == academic_year_id,
            HomeworkAssignment.subject == link.subject,
            HomeworkAssignment.status == "active",
        ).all()
        mapping_by_date: Dict[date, Optional[Dict[int, int]]] = {}
        for a in assignments:
            if a.assigned_date not in mapping_by_date:
                mapping_by_date[a.assigned_date] = _event_time_mapping(db, link, ctx, a)
            mapping = mapping_by_date[a.assigned_date]
            if mapping is not None:
                cross.append((a, mapping))

    assignment_ids = [a.id for a in direct] + [a.id for a, _ in cross]
    rows_by_assignment: Dict[int, Dict[int, HomeworkSubmission]] = {}
    # SQLite 的 IN 参数有上限；批次规模增长时仍保持查询数随批次数线性增长。
    for start in range(0, len(assignment_ids), 400):
        rows = db.query(HomeworkSubmission).filter(
            HomeworkSubmission.assignment_id.in_(assignment_ids[start:start + 400])
        ).all()
        for row in rows:
            rows_by_assignment.setdefault(row.assignment_id, {})[row.person_id] = row

    expected_by_assignment = {
        a.id: set(_effective_expected_ids(db, a))
        for a in [*direct, *(a for a, _ in cross)]
    }
    for a in direct:
        key = _line_key_of(ctx.mode, a)
        rows = rows_by_assignment.get(a.id, {})
        for pid in ids:
            events, axis = result[pid]
            axis.setdefault(key, []).append(a)
            row = rows.get(pid)
            if row is not None or pid in expected_by_assignment[a.id]:
                events.append((a, row or HomeworkSubmission(
                    assignment_id=a.id, person_id=pid, submission_status="submitted",
                )))

    for a, mapping in cross:
        key = _line_key_of(ctx.mode, a)
        rows = rows_by_assignment.get(a.id, {})
        expected = expected_by_assignment[a.id]
        # 发起域 ID → 读域 ID；未在事件日交集中的学生不能得到该批次事件。
        for source_id, reader_id in mapping.items():
            if reader_id not in ids:
                continue
            events, axis = result[reader_id]
            axis.setdefault(key, []).append(a)
            row = rows.get(source_id)
            if row is not None or source_id in expected:
                events.append((a, row or HomeworkSubmission(
                    assignment_id=a.id, person_id=source_id, submission_status="submitted",
                )))

    for events, _axis in result.values():
        events.sort(key=lambda item: (item[0].assigned_date, item[0].id))
    return result


def _line_key_of(mode: str, a: HomeworkAssignment) -> LineKey:
    """批次所属连缺线的键：班主任按学科分线，教学按班单线（不分种类）。"""
    return (a.data_domain, a.class_ref_id, a.subject if mode == "homeroom" else None)


@router.get("/homework/students/{person_id}", response_model=HomeworkStudentResponse)
@domain_endpoint
def homework_student(
    person_id: int,
    mode: Optional[str] = None,
    class_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    academic_year_id: Optional[int] = None,
    all_history: bool = False,
    db: Session = Depends(get_db),
):
    """学生维度事件流 + streaks（画像页消费）。person 不在当前作用域名册
    → 404；无缺交/请假例外的应交批次按已交事件返回。显式指定 academic_year_id 时
    过滤该学年，未指定或 all_history=True 时返回全部历史学年。

    streaks 与 homework_warnings 完全同一套算法（_streak_lines_of）：
    班主任按学科分线（按天）取最大 current；教学按天单线（不分作业种类）；
    longest 取各线最大；basis 取胜出线的 basis。"""
    teacher_id = current_teacher_id(db)
    ctx = _resolve_hw_scope(
        db, teacher_id, mode, academic_year_id, class_id, teaching_class_id
    )
    if person_id not in ctx.member_person_ids:
        raise ResourceOutOfScope(
            "person not in current workspace scope", details={"person_id": person_id}
        )
    events, line_axis_map = _person_events(
        db, ctx, person_id, academic_year_id=academic_year_id, all_history=all_history
    )
    # 考勤批次不参与连缺统计（事件列表照常返回供档案展示）
    streak_events = [(a, s) for a, s in events if a.subject != "考勤"]
    legacy_ids = {
        a.id
        for axis in line_axis_map.values()
        for a in axis
        if not _parse_expected_ids(a.expected_members_json)
    }
    lines = _streak_lines_of(ctx.mode, streak_events, line_axis_map, legacy_ids)
    if lines:
        winner = max(lines, key=lambda t: (t[0] or 0, t[2], t[4]))
        current, basis = winner[0], winner[1]
        longest = max(t[2] for t in lines)
    else:
        current, basis, longest = 0, "events", 0
    return HomeworkStudentResponse(
        metadata=_metadata(ctx),
        person_id=person_id,
        name=q.names_for(db, [person_id]).get(person_id),
        events=[
            HomeworkStudentEvent(
                assignment_id=a.id,
                assigned_date=a.assigned_date.isoformat(),
                subject=a.subject,
                homework_type=a.homework_type,
                status=_effective_status(s),
                evaluation=s.evaluation,
            )
            for a, s in events
        ],
        streaks=HomeworkStudentStreaks(
            current_missing_streak=current,
            streak_basis=basis,
            longest_missing_streak=longest,
        ),
    )


# ────────────────────────────── §3 预警时间轴 ──────────────────────────────


@router.get("/homework/warnings", response_model=HomeworkWarningsResponse)
@domain_endpoint
def homework_warnings(
    mode: Optional[str] = None,
    class_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    academic_year_id: Optional[int] = None,
    subject: Optional[str] = None,
    homework_type: Optional[str] = None,
    min_missing: int = 2,
    min_streak: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """按人聚合缺交（事件维度，basis='events'）：missing_count /
    current_streak / 最近 5 条缺交。无缺交/请假例外的应交批次按已交，
    因而会中断此前的连续缺交。

    连续口径按天（与画像端点共用 _streak_lines_of，单一实现）：同日多批次
    先合并成天——任一缺交该天计 1、默认/显式已交清零停止、请假与出勤异常
    跳过；班主任按（域, 班, 学科）分线取最大 current，教学按（域, 班）单线
    （不分作业种类，streak_homework_type 恒 None）。迁移的仅缺交历史批次
    没有 expected_members 快照时，班主任线按旧版保留下来的班级级日期轴
    计算并返回 basis='legacy_events'；教学线按天轴统一处理（当天仅 legacy
    批次无法核实 → 跳过）。本端点不计算 submission_rate。
    """
    if min_missing < 1:
        raise InvalidScopeParam(
            "min_missing must be >= 1", details={"param": "min_missing"}
        )
    if min_streak is not None and min_streak < 1:
        raise InvalidScopeParam(
            "min_streak must be >= 1", details={"param": "min_streak"}
        )
    teacher_id = current_teacher_id(db)
    ctx = _resolve_hw_scope(
        db, teacher_id, mode, academic_year_id, class_id, teaching_class_id, subject
    )
    subject_f = (subject or "").strip() or None
    type_f = (homework_type or "").strip() or None
    default_start, default_end = _current_semester_bounds(db, ctx.academic_year_id)
    period_start = _parse_date(date_from, "date_from") or default_start
    period_end = _parse_date(date_to, "date_to") or default_end
    if period_start > period_end:
        raise InvalidScopeParam("date_from must not be after date_to", details={"date_from": date_from, "date_to": date_to})

    events_by_person: Dict[int, List[Tuple[HomeworkAssignment, HomeworkSubmission]]] = {}
    visible_rows: List[
        Tuple[HomeworkAssignment, List[Tuple[HomeworkSubmission, int]]]
    ] = []
    for a, mapping in _visible_assignments(db, ctx, active_only=True):
        if a.subject == "考勤":
            continue
        if a.assigned_date < period_start or a.assigned_date > period_end:
            continue
        if subject_f is not None and a.subject != subject_f:
            continue
        if type_f is not None and a.homework_type != type_f:
            continue
        projected_rows = _projected_submissions(db, a, mapping)
        row_by_reader = {rid: s for s, rid in projected_rows}
        expected_reader = _projected_expected(db, a, mapping)
        # ADR-023：排除学生的行不进入预警事件流与排行/连续轴，其他学生的
        # 连续判定不受其缺交日期影响（老版 respect_excluded 同口径）
        skip_ids = _excluded_readers_of(db, a, mapping)
        projected = list(projected_rows)
        for rid in expected_reader:
            if rid not in row_by_reader:
                projected.append((HomeworkSubmission(
                    assignment_id=a.id,
                    person_id=rid,
                    submission_status="submitted",
                ), rid))
        if skip_ids:
            projected = [(s, rid) for s, rid in projected if rid not in skip_ids]
        visible_rows.append((a, projected))
        for s, rid in projected:
            events_by_person.setdefault(rid, []).append((a, s))

    # 连缺线批次轴与快照解析预计算（原实现把两件事放在「每学生 × 每维度」
    # 循环里，对同一 expected_members_json 反复 json.loads，班额大时是数万次
    # 重复解析）：每条连缺线的批次轴只收集一次，legacy 快照只解析一次。
    # 连缺线：班主任 =（域, 班, 学科）；教学 =（域, 班）按天单线，不分作业种类。
    line_axis_map: Dict[LineKey, List[HomeworkAssignment]] = {}
    legacy_assignment_ids: Set[int] = set()
    for a, _projected in visible_rows:
        line_axis_map.setdefault(_line_key_of(mode, a), []).append(a)
        if not _parse_expected_ids(a.expected_members_json):
            legacy_assignment_ids.add(a.id)

    students: List[HomeworkWarningStudent] = []
    # ADR-023：本域作用域班级中被排除统计的学生不进入任何预警清单
    # （缺交排行、负面评价、忘带）；其个人明细与相关性另走独立端点保留。
    scope_excluded = _stats_excluded_ids(db, ctx.data_domain, ctx.class_ids)
    roster_ids = sorted(
        (set(ctx.member_person_ids) | set(events_by_person.keys())) - scope_excluded
    )
    names = q.names_for(db, roster_ids)

    # 预警时间基准：以当前作业最新日期为时间轴基准点，无作业时以今日为准
    anchor_date = max((a.assigned_date for a, _ in visible_rows), default=date.today())

    # 人工解除记录（连续负面评价等）
    dismiss_notes = db.query(WsStudentNote).filter(
        WsStudentNote.data_domain == ctx.data_domain,
        WsStudentNote.person_id.in_(roster_ids),
        WsStudentNote.source.like("warning_dismissal:quality%"),
    ).all()
    dismissed_dates: Dict[int, date] = {}
    for note in dismiss_notes:
        if note.person_id not in dismissed_dates or note.date > dismissed_dates[note.person_id]:
            dismissed_dates[note.person_id] = note.date

    # 负面评价是与收交状态正交的独立预警。只沿本人“已交且有评价”
    # 的记录倒序判定；正面/中性评价打断，无评价不充当中性。
    # 退出机制：支持人工跟进解除。生效日及之前的历史负面不再计入连续段；
    # 若后续产生新负面且连续达到下限，则重新激活报警。
    quality: List[HomeworkAuxWarningStudent] = []
    for pid in roster_ids:
        cutoff = dismissed_dates.get(pid)
        evaluated = [
            (a, s)
            for a, s in sorted(
                events_by_person.get(pid, []),
                key=lambda item: (item[0].assigned_date, item[0].id),
            )
            if _effective_status(s) == "submitted"
            and _quality_text(s.evaluation)
            and (cutoff is None or a.assigned_date > cutoff)
        ]
        streak: List[Tuple[HomeworkAssignment, HomeworkSubmission]] = []
        for a, s in reversed(evaluated):
            if _evaluation_tone(s.evaluation) == "negative":
                streak.append((a, s))
            else:
                break
        if len(streak) >= 2:
            chronological = list(reversed(streak))
            quality.append(
                HomeworkAuxWarningStudent(
                    person_id=pid,
                    name=names.get(pid),
                    count=len(streak),
                    dates=[a.assigned_date.isoformat() for a, _s in chronological],
                    details=[_quality_text(s.evaluation) for _a, s in chronological],
                )
            )
    quality.sort(key=lambda item: (-item.count, item.name or "", item.person_id))

    # 忘带与出勤预警（忘带、没带、迟到、没来等）：
    # 来自旧特殊记录投影或日常作业行的明确录入，不与连续缺交/评价混算；
    # 自动冲刷：采用近 30 天滑动窗口（基于 anchor_date），30 天前的记录自然沉淀；支持人工解除。
    dismiss_forgot_notes = db.query(WsStudentNote).filter(
        WsStudentNote.data_domain == ctx.data_domain,
        WsStudentNote.person_id.in_(roster_ids),
        (
            WsStudentNote.source.like("warning_dismissal:forgot%")
            | WsStudentNote.source.like("warning_dismissal:attendance%")
        ),
    ).all()
    dismissed_forgot_dates: Dict[int, date] = {}
    for note in dismiss_forgot_notes:
        if note.person_id not in dismissed_forgot_dates or note.date > dismissed_forgot_dates[note.person_id]:
            dismissed_forgot_dates[note.person_id] = note.date

    forgot: List[HomeworkAuxWarningStudent] = []
    if roster_ids:
        visible_assignment_ids = {a.id for a, _projected in visible_rows}
        forgot_by_person: Dict[int, List[WsStudentNote]] = {}
        window_start = max(period_start, anchor_date - timedelta(days=30))
        for note in db.query(WsStudentNote).filter(
            WsStudentNote.data_domain == ctx.data_domain,
            WsStudentNote.person_id.in_(roster_ids),
            WsStudentNote.date >= window_start,
            WsStudentNote.date <= period_end,
            (
                WsStudentNote.source.like("migration:%")
                | WsStudentNote.source.like("homework:%")
            ),
        ).all():
            if note.source and note.source.startswith("homework:"):
                try:
                    if int(note.source.split(":", 1)[1]) not in visible_assignment_ids:
                        continue
                except ValueError:
                    continue
            if note.person_id in dismissed_forgot_dates and note.date <= dismissed_forgot_dates[note.person_id]:
                continue
            raw = f"{note.category or ''} {note.content or ''}"
            if note.content.startswith("[") and any(
                word in raw for word in ("忘带", "没带", "未带", "迟到", "没来", "早退", "旷课", "缺课", "缺席")
            ):
                forgot_by_person.setdefault(note.person_id, []).append(note)
        for pid, notes in forgot_by_person.items():
            if len(notes) < 3:
                continue
            ordered = sorted(notes, key=lambda note: (note.date, note.id))
            forgot.append(
                HomeworkAuxWarningStudent(
                    person_id=pid,
                    name=names.get(pid),
                    count=len(ordered),
                    dates=[note.date.isoformat() for note in ordered],
                    details=[note.content for note in ordered],
                )
            )
        forgot.sort(key=lambda item: (-item.count, item.name or "", item.person_id))
    for pid in roster_ids:
        evs = sorted(
            events_by_person.get(pid, []),
            key=lambda item: (item[0].assigned_date, item[0].id),
        )
        # 迟到、没来、忘带不计入连续缺交统计
        missing_events = [
            (a, s) for a, s in evs if _is_pure_missing(s)
        ]
        if len(missing_events) < min_missing:
            continue

        # 连续缺交按天口径（与画像端点共用 _streak_lines_of，单一实现）：
        # 班主任按（域, 班, 学科）分线取最大 current；教学按（域, 班）单线
        # （不分作业种类）。“全部所教班”只汇总结果，连续轴仍以实际归属班为
        # 边界；否则 B 班某日的事件会错误中断 A 班学生的连击。
        lines = _streak_lines_of(mode, evs, line_axis_map, legacy_assignment_ids)

        # 优先展示最长的当前连续段（平手依次看最后事件日、纯缺交数、维度名）。
        reliable = [item for item in lines if item[0] is not None]
        if reliable:
            current, basis, _longest, streak_dimension, _last_date, _count = max(
                reliable,
                key=lambda item: (item[0] or 0, item[4], item[5], item[3] or ""),
            )
        else:
            current, basis, _longest, streak_dimension, _last_date, _count = max(
                lines,
                key=lambda item: (item[4], item[5], item[3] or ""),
            )
        if min_streak is not None and (current is None or current < min_streak):
            continue
        recent = [
            HomeworkRecentMissing(
                assignment_id=a.id,
                assigned_date=a.assigned_date.isoformat(),
                subject=a.subject,
                homework_type=a.homework_type,
            )
            for a, _s in sorted(
                missing_events,
                key=lambda item: (item[0].assigned_date, item[0].id),
                reverse=True,
            )[:5]
        ]
        students.append(
            HomeworkWarningStudent(
                person_id=pid,
                name=names.get(pid),
                missing_count=len(missing_events),
                current_streak=current,
                streak_basis=basis,
                streak_subject=streak_dimension if mode == "homeroom" else None,
                # 教学改为按天单线后不再按作业种类标注维度
                streak_homework_type=None,
                recent_missing=recent,
            )
        )
    students.sort(
        key=lambda s: (-s.missing_count, s.current_streak is None, -(s.current_streak or 0), s.person_id)
    )
    return HomeworkWarningsResponse(
        metadata=_metadata(ctx),
        basis="events",
        min_missing=min_missing,
        min_streak=min_streak,
        students=students,
        quality=quality,
        forgot=forgot,
    )


@router.post("/homework/warnings/dismiss", response_model=HomeworkWarningDismissResponse)
@domain_endpoint
def dismiss_homework_warning(
    payload: HomeworkWarningDismissRequest,
    db: Session = Depends(get_db),
):
    """人工解除作业预警（当前主要支持 quality 连续负面评价）。
    写入 WsStudentNote，记录跟进日志并作为该生历史负面评价的截止点。
    """
    teacher_id = current_teacher_id(db)
    ctx = _resolve_hw_scope(
        db,
        teacher_id,
        payload.mode,
        payload.academic_year_id,
        payload.class_id,
        payload.teaching_class_id,
        payload.subject,
    )
    if payload.dismiss_date:
        d_date = _parse_date(payload.dismiss_date, "dismiss_date") or date.today()
    else:
        d_date = date.today()

    target_pid = payload.person_id
    if target_pid not in ctx.member_person_ids:
        existing_event = db.query(HomeworkSubmission).filter(
            HomeworkSubmission.person_id == target_pid
        ).first()
        if not existing_event:
            raise ResourceOutOfScope(
                "Student not found in current scope",
                details={"person_id": target_pid},
            )

    class_ref = ctx.class_ids[0] if ctx.class_ids else 0
    note = WsStudentNote(
        data_domain=ctx.data_domain,
        person_id=target_pid,
        date=d_date,
        category="谈话",
        content=f"[预警解除] {payload.warning_kind} 预警已跟进处理",
        source=f"warning_dismissal:{payload.warning_kind}:{class_ref}",
    )
    db.add(note)
    db.commit()
    return HomeworkWarningDismissResponse(
        ok=True,
        person_id=target_pid,
        warning_kind=payload.warning_kind,
        dismissed_date=d_date.isoformat(),
    )


# ────────────────────────────── §3.1 统计排除（ADR-023） ──────────────────────────────


def _exclusion_target_class(
    db: Session, teacher_id: int, mode: Optional[str],
    academic_year_id: Optional[int], class_id: Optional[int],
    teaching_class_id: Optional[int],
) -> Tuple[WorkspaceContext, int]:
    """解析排除管理的目标班：homeroom=当前绑定行政班；teaching=显式教学班
    （并集作用域无法定位单班名册，必须先选班）。"""
    if mode == "teaching" and teaching_class_id is None:
        raise InvalidScopeParam(
            "teaching_class_id is required to manage stats exclusion",
            details={"param": "teaching_class_id"},
        )
    ctx = _resolve_hw_scope(
        db, teacher_id, mode, academic_year_id, class_id, teaching_class_id
    )
    if mode == "teaching":
        if teaching_class_id not in ctx.class_ids:
            raise ResourceOutOfScope(
                "teaching class not in this workspace scope",
                details={"teaching_class_id": teaching_class_id},
            )
        return ctx, teaching_class_id
    return ctx, ctx.class_ids[0]


def _stats_exclusion_payload(
    db: Session, ctx: WorkspaceContext, target_class: int
) -> HomeworkStatsExclusionResponse:
    if ctx.mode == "homeroom":
        roster = q.homeroom_roster(db, target_class, ctx.as_of, ctx.academic_year_id)
    else:
        roster = q.teaching_roster(db, [target_class], ctx.as_of, ctx.academic_year_id)
    excluded = _stats_excluded_ids(db, ctx.data_domain, [target_class])
    return HomeworkStatsExclusionResponse(
        metadata=_metadata(ctx),
        data_domain=ctx.data_domain,
        class_ref_id=target_class,
        entries=[
            HomeworkStatsExclusionEntry(
                person_id=item["person_id"],
                name=item["name"],
                alias=item.get("alias"),
                excluded=item["person_id"] in excluded,
            )
            for item in roster
        ],
    )


@router.get("/homework/stats-exclusion", response_model=HomeworkStatsExclusionResponse)
@domain_endpoint
def homework_stats_exclusion_list(
    mode: Optional[str] = None,
    class_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    academic_year_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """当前班名册与排除状态（ADR-023）：排除=缺交不计入看板/排行/预警；
    相关性与个人明细保留。"""
    teacher_id = current_teacher_id(db)
    ctx, target_class = _exclusion_target_class(
        db, teacher_id, mode, academic_year_id, class_id, teaching_class_id
    )
    return _stats_exclusion_payload(db, ctx, target_class)


@router.put("/homework/stats-exclusion", response_model=HomeworkStatsExclusionResponse)
@domain_endpoint
def homework_stats_exclusion_set(
    req: HomeworkStatsExclusionSetRequest, db: Session = Depends(get_db)
):
    """开/关一个学生的统计排除（幂等；关闭=删除排除行，业务记录永不删除）。"""
    teacher_id = current_teacher_id(db)
    ctx, target_class = _exclusion_target_class(
        db, teacher_id, req.mode, req.academic_year_id, req.class_id,
        req.teaching_class_id,
    )
    if ctx.mode == "homeroom":
        roster = q.homeroom_roster(db, target_class, ctx.as_of, ctx.academic_year_id)
    else:
        roster = q.teaching_roster(db, [target_class], ctx.as_of, ctx.academic_year_id)
    if not any(item["person_id"] == req.person_id for item in roster):
        raise InvalidScopeParam(
            "person not in current roster of this class",
            details={"person_id": req.person_id},
        )
    if req.excluded:
        exists = (
            db.query(HomeworkStatsExclusion)
            .filter(
                HomeworkStatsExclusion.data_domain == ctx.data_domain,
                HomeworkStatsExclusion.class_ref_id == target_class,
                HomeworkStatsExclusion.identity_id == req.person_id,
            )
            .first()
        )
        if exists is None:
            db.add(HomeworkStatsExclusion(
                data_domain=ctx.data_domain,
                class_ref_id=target_class,
                identity_id=req.person_id,
            ))
    else:
        db.query(HomeworkStatsExclusion).filter(
            HomeworkStatsExclusion.data_domain == ctx.data_domain,
            HomeworkStatsExclusion.class_ref_id == target_class,
            HomeworkStatsExclusion.identity_id == req.person_id,
        ).delete(synchronize_session=False)
    db.commit()
    return _stats_exclusion_payload(db, ctx, target_class)


# ────────────────────────────── §4 相关性 ──────────────────────────────


def _exam_exists_in_domain(
    db: Session, data_domain: str, academic_year_id: int, exam_name: str
) -> bool:
    from app.db.workspace_models import ScoreFact

    return (
        db.query(ScoreFact.id)
        .filter(
            ScoreFact.data_domain == data_domain,
            ScoreFact.academic_year_id == academic_year_id,
            ScoreFact.exam_name == exam_name,
        )
        .limit(1)
        .first()
        is not None
    )


def _submission_rates_by_person(
    db: Session,
    ctx: WorkspaceContext,
    subject: str,
    homework_type: Optional[str],
) -> Tuple[Dict[int, float], int]:
    """X = 每人提交率：只计该人在应交快照内、未 excused 的批次；应交快照
    为空（分母不可用）的批次整批剔除并计数（响应 caveats 注明）。"""
    contrib: Dict[int, List[int]] = {}
    excluded = 0
    for a, mapping in _visible_assignments(db, ctx, active_only=True):
        if a.subject != subject:
            continue
        if homework_type is not None and a.homework_type != homework_type:
            continue
        expected_init = set(_parse_expected_ids(a.expected_members_json))
        if not expected_init:
            excluded += 1
            continue
        pairs = _projected_submissions(db, a, mapping)
        status_by_reader = {rid: _effective_status(s) for s, rid in pairs}
        expected_reader = _projected_expected(db, a, mapping)
        for rid in expected_reader:
            status = status_by_reader.get(rid, "submitted")
            if status == "excused":
                continue
            contrib.setdefault(rid, []).append(1 if status == "submitted" else 0)
    return {
        pid: sum(vals) / len(vals) for pid, vals in contrib.items() if vals
    }, excluded


@router.get("/homework/correlation", response_model=HomeworkCorrelationResponse)
@domain_endpoint
def homework_correlation(
    mode: Optional[str] = None,
    class_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    academic_year_id: Optional[int] = None,
    subject: Optional[str] = None,
    homework_type: Optional[str] = None,
    exam_name: Optional[str] = None,
    total_type: str = DEFAULT_TOTAL_TYPE,
    db: Session = Depends(get_db),
):
    """成绩 × 作业提交率 Pearson 相关（描述统计，绝不表述为因果）。

    - homeroom：Y = 指定 total_type 总分（homeroom 域该考试该人的值）
      降序名次；X = 作业提交率（subject 按参数过滤）。
    - teaching：Y = 该科单科本班名次（min-rank 同分同名次，独立实现不
      import analysis）；X 同上。
    成绩侧（G08，契约 §4 v2）一律经 q.readable_facts 统一可读事实入口：
    含经五条件门的对侧投影行与冲突保留规则；teaching 分支不得直连本域
    facts，否则 H-only 投影样本会丢失（pairs 假性为空、n=0）。"""
    teacher_id = current_teacher_id(db)
    subject = _non_empty(subject, "subject")
    exam = _non_empty(exam_name, "exam_name")
    ctx = _resolve_hw_scope(
        db, teacher_id, mode, academic_year_id, class_id, teaching_class_id,
        subject if mode == "teaching" else None,
    )

    y_by_person: Dict[int, float] = {}
    if ctx.mode == "homeroom":
        if not _exam_exists_in_domain(db, "homeroom", ctx.academic_year_id, exam):
            raise ResourceOutOfScope(
                "exam not found in homeroom domain", details={"exam_name": exam}
            )
        entries = q.readable_facts(db, ctx, exam_name=exam)
        y_by_person = {
            e.person_id: float(e.fact.score)
            for e in entries
            if e.fact.total_type == total_type and e.fact.score is not None
        }
    else:
        if not (
            _exam_exists_in_domain(db, "teaching", ctx.academic_year_id, exam)
            or _exam_exists_in_domain(db, "homeroom", ctx.academic_year_id, exam)
        ):
            raise ResourceOutOfScope(
                "exam not found in teaching domain", details={"exam_name": exam}
            )
        subject_scope = ctx.subject or subject
        entries = q.readable_facts(db, ctx, exam_name=exam)
        y_by_person = {
            e.person_id: float(e.fact.score)
            for e in entries
            if e.fact.total_type is None
            and e.fact.subject == subject_scope
            and e.fact.score is not None
        }
    y_ranks = _min_rank_desc(y_by_person)

    x_by_person, excluded = _submission_rates_by_person(
        db, ctx, subject, (homework_type or "").strip() or None
    )
    caveats: List[str] = []
    if excluded:
        caveats.append(
            f"{excluded} 个作业批次因应交快照为空（分母不可用）被剔除，未计入提交率"
        )

    pairs_raw = sorted(
        (
            (pid, x_by_person[pid], y_ranks[pid])
            for pid in ctx.member_person_ids
            if pid in x_by_person and pid in y_ranks
        ),
        key=lambda item: (item[2], item[0]),
    )
    names = q.names_for(db, [pid for pid, _x, _y in pairs_raw])
    n = len(pairs_raw)
    r: Optional[float] = None
    direction: Optional[str] = None
    if n < 5:
        caveats.append(f"配对样本数 n={n} < 5，r 不可计算")
    else:
        r = _pearson([x for _p, x, _y in pairs_raw], [y for _p, _x, y in pairs_raw])
        if r is None:
            caveats.append("提交率或名次零方差，r 不可计算")
        else:
            r = round(r, 4)
            # Y 是名次（越小越好）：提交率越高、名次数值越小（名次越好）
            # → submit_up_rank_up；反之 submit_up_rank_down。
            if r < 0:
                direction = "submit_up_rank_up"
            elif r > 0:
                direction = "submit_up_rank_down"
            else:
                caveats.append("提交率与名次无线性相关")
    caveats.append("相关性仅描述统计关联，不构成因果结论")
    return HomeworkCorrelationResponse(
        metadata=_metadata(ctx),
        pairs=[
            HomeworkCorrelationPair(
                person_id=pid, name=names.get(pid), x=round(x, 4), y=float(y)
            )
            for pid, x, y in pairs_raw
        ],
        n=n,
        r=r,
        direction=direction,
        caveats=caveats,
    )


# ────────────────────────────── §5 学期管理（H06） ──────────────────────────────


def _semester_entry(row: WsHomeworkSemester) -> HomeworkSemesterEntry:
    return HomeworkSemesterEntry(
        id=row.id,
        name=row.name,
        start_date=row.start_date.isoformat(),
        end_date=row.end_date.isoformat(),
        is_current=bool(row.is_current),
        mode=row.mode,
    )


def _derived_semester_entries(ay: AcademicYear) -> List[HomeworkSemesterEntry]:
    """无表数据时按学年日期二分推导上下学期（auto 模式，id=null）。"""
    total_days = (ay.end_date - ay.start_date).days
    mid = ay.start_date + timedelta(days=total_days // 2)
    today = date.today()
    entries = []
    for name, start, end in (
        ("上学期", ay.start_date, mid),
        ("下学期", mid + timedelta(days=1), ay.end_date),
    ):
        entries.append(
            HomeworkSemesterEntry(
                id=None,
                name=name,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                is_current=start <= today <= end,
                mode="auto",
            )
        )
    return entries


@router.get("/homework/current-semester", response_model=CurrentSemesterResponse)
@domain_endpoint
def get_current_semester(db: Session = Depends(get_db)):
    """Return the single application-wide current semester.

    An explicitly selected semester wins.  Before the user creates manual
    rows, fall back to the automatically derived semester for today's academic
    year (or the newest academic year when today is outside every configured
    year).
    """
    row = (
        db.query(WsHomeworkSemester)
        .filter(WsHomeworkSemester.is_current == 1)
        .order_by(WsHomeworkSemester.id.desc())
        .first()
    )
    if row is not None:
        ay = db.get(AcademicYear, row.academic_year_id)
        return CurrentSemesterResponse(
            id=row.id,
            academic_year_id=ay.id,
            academic_year_name=ay.name,
            name=row.name,
            start_date=row.start_date.isoformat(),
            end_date=row.end_date.isoformat(),
            mode=row.mode,
        )

    today = date.today()
    ay = (
        db.query(AcademicYear)
        .filter(AcademicYear.start_date <= today, AcademicYear.end_date >= today)
        .order_by(AcademicYear.start_date.desc(), AcademicYear.id.desc())
        .first()
        or db.query(AcademicYear)
        .order_by(AcademicYear.start_date.desc(), AcademicYear.id.desc())
        .first()
    )
    if ay is None:
        raise WorkspaceNotConfigured("no academic year configured")
    entries = _derived_semester_entries(ay)
    selected = next((item for item in entries if item.is_current), entries[0])
    return CurrentSemesterResponse(
        id=None,
        academic_year_id=ay.id,
        academic_year_name=ay.name,
        name=selected.name,
        start_date=selected.start_date,
        end_date=selected.end_date,
        mode="auto",
    )


def _check_semester_conflicts(
    db: Session,
    academic_year_id: int,
    name: str,
    start: date,
    end: date,
    exclude_id: Optional[int] = None,
) -> None:
    """同学年重名 / 日期重叠 → 422（H06：绝不 500，唯一键冲突先在应用层拒）。"""
    query = db.query(WsHomeworkSemester).filter(
        WsHomeworkSemester.academic_year_id == academic_year_id
    )
    if exclude_id is not None:
        query = query.filter(WsHomeworkSemester.id != exclude_id)
    for row in query.all():
        if row.name == name:
            raise InvalidScopeParam(
                "同学年下已存在同名学期", details={"name": name, "existing_id": row.id}
            )
        if start <= row.end_date and end >= row.start_date:
            raise InvalidScopeParam(
                "学期日期与既有学期重叠",
                details={
                    "existing_id": row.id,
                    "existing_name": row.name,
                    "existing_start": row.start_date.isoformat(),
                    "existing_end": row.end_date.isoformat(),
                },
            )


@router.get("/homework/semesters", response_model=HomeworkSemestersResponse)
@domain_endpoint
def list_homework_semesters(
    academic_year_id: Optional[int] = None, db: Session = Depends(get_db)
):
    """auto：该学年无表数据时按日期二分推导（id=null）；有手工/落库行则
    返回实际值（auto=false）。"""
    ay = q.resolve_year(db, academic_year_id)
    rows = (
        db.query(WsHomeworkSemester)
        .filter(WsHomeworkSemester.academic_year_id == ay.id)
        .order_by(WsHomeworkSemester.start_date.asc(), WsHomeworkSemester.id.asc())
        .all()
    )
    if not rows:
        return HomeworkSemestersResponse(
            academic_year_id=ay.id,
            academic_year_name=ay.name,
            auto=True,
            semesters=_derived_semester_entries(ay),
        )
    return HomeworkSemestersResponse(
        academic_year_id=ay.id,
        academic_year_name=ay.name,
        auto=False,
        semesters=[_semester_entry(row) for row in rows],
    )


@router.post("/homework/semesters", response_model=HomeworkSemestersResponse)
@domain_endpoint
def create_homework_semester(req: SemesterCreateRequest, db: Session = Depends(get_db)):
    ay = db.get(AcademicYear, req.academic_year_id)
    if ay is None:
        raise ResourceOutOfScope(
            "academic year not found",
            details={"academic_year_id": req.academic_year_id},
        )
    name = _non_empty(req.name, "name")
    start = _parse_date(req.start_date, "start_date", required=True)
    end = _parse_date(req.end_date, "end_date", required=True)
    if start > end:
        raise InvalidScopeParam(
            "start_date must not be after end_date", details={"param": "start_date"}
        )
    _check_semester_conflicts(db, ay.id, name, start, end)
    row = WsHomeworkSemester(
        academic_year_id=ay.id,
        name=name,
        start_date=start,
        end_date=end,
        is_current=0,
        mode="manual",
    )
    db.add(row)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise InvalidScopeParam(
            "学期保存失败（同学年重名或数据冲突）", details={"name": name}
        )
    return HomeworkSemestersResponse(
        academic_year_id=ay.id,
        academic_year_name=ay.name,
        auto=False,
        semesters=[_semester_entry(row)],
    )


@router.put(
    "/homework/semesters/{semester_id}", response_model=HomeworkSemestersResponse
)
@domain_endpoint
def update_homework_semester(
    semester_id: int, req: SemesterUpdateRequest, db: Session = Depends(get_db)
):
    """手工改日期/名称；改过即 mode='manual'（保留可经 restore-auto 回自动）。"""
    row = db.get(WsHomeworkSemester, semester_id)
    if row is None:
        raise ResourceOutOfScope("semester not found", details={"id": semester_id})
    ay = db.get(AcademicYear, row.academic_year_id)
    name = _non_empty(req.name, "name") if req.name is not None else row.name
    start = (
        _parse_date(req.start_date, "start_date", required=True)
        if req.start_date is not None
        else row.start_date
    )
    end = (
        _parse_date(req.end_date, "end_date", required=True)
        if req.end_date is not None
        else row.end_date
    )
    if start > end:
        raise InvalidScopeParam(
            "start_date must not be after end_date", details={"param": "start_date"}
        )
    _check_semester_conflicts(
        db, row.academic_year_id, name, start, end, exclude_id=row.id
    )
    row.name = name
    row.start_date = start
    row.end_date = end
    row.mode = "manual"
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise InvalidScopeParam(
            "学期保存失败（同学年重名或数据冲突）", details={"id": semester_id}
        )
    return HomeworkSemestersResponse(
        academic_year_id=ay.id,
        academic_year_name=ay.name,
        auto=False,
        semesters=[_semester_entry(row)],
    )


@router.post(
    "/homework/semesters/{semester_id}/restore-auto",
    response_model=HomeworkSemesterRestoreResponse,
)
@domain_endpoint
def restore_semester_auto(semester_id: int, db: Session = Depends(get_db)):
    """回自动模式：丢弃手工日期（删手工行），响应操作前后对比（任务书
    简化口径：直接返回 before/after 对象）。"""
    row = db.get(WsHomeworkSemester, semester_id)
    if row is None:
        raise ResourceOutOfScope("semester not found", details={"id": semester_id})
    ay = db.get(AcademicYear, row.academic_year_id)
    before = _semester_entry(row)
    db.delete(row)
    db.commit()
    return HomeworkSemesterRestoreResponse(
        restored=True,
        before=before,
        after=_derived_semester_entries(ay),
    )


@router.put(
    "/homework/semesters/{semester_id}/current",
    response_model=HomeworkSemesterCurrentResponse,
)
@domain_endpoint
def set_current_semester(semester_id: int, db: Session = Depends(get_db)):
    """设当前学期：切换时旧 current 清零；重复设同一条 → 422（不 500）。"""
    row = db.get(WsHomeworkSemester, semester_id)
    if row is None:
        raise ResourceOutOfScope("semester not found", details={"id": semester_id})
    if row.is_current:
        raise InvalidScopeParam(
            "该学期已是当前学期，请勿重复设置",
            details={"id": row.id, "name": row.name},
        )
    # “当前学期”是整个应用的唯一当前范围，不是每个学年各自一条。
    for other in db.query(WsHomeworkSemester).all():
        other.is_current = 0
    row.is_current = 1
    db.commit()
    return HomeworkSemesterCurrentResponse(
        id=row.id, academic_year_id=row.academic_year_id, is_current=True
    )
