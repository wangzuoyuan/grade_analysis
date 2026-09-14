"""/api/v1 共享查询助手：作用域参数组装、名册、成绩事实、跨域投影门。

投影规则（docs/contracts/p1-api.md §1.2.2/§1.3/§1.4.1）：
- 跨域成绩投影必须先经统一投影门（share_categories_of / gate_fact /
  eligible_linked_pairs / gated_*_facts_for_*）：current_subject_score 已
  授权、考试日期已知且不早于历史授权下限、双侧成员在考试时点均有效
  （v2.1/F03：名册响应的共享分数字段同样按考试时点验证，仅姓名/座号
  投影用查询时点）。替换式投影（删本域行取对方值）一律禁止。
- homeroom 响应先取本班 enrollment 成员；通过门的 teaching 域事实与
  H 域同 (人, 学科, 考试) 事实冲突时各自保留本域值，homeroom 侧行由
  调用方附 shared_conflict 提示；H 域无该场事实才投影 T 行。
- teaching 响应仅任教学科；linked 学生 H 域同学科事实反向投影同理
  （T 域已有同场则不投影、不附字段）。
- 名册共享（name/seat 投影）须 roster 已授权且查询时点双侧成员有效。
- 无 link 的教学班数据绝不进入 homeroom 响应；缺考 score 保持 null。
- 统一可读事实口径（v2.1/F09，readable_facts / readable_exam_summaries）：
  本域事实 + 经五条件门的对侧投影行 + 冲突注记，供 /scores、学生列表/
  画像、分析端点与 /shared/exams 共用；各端点不得自建第二套投影逻辑。
- 考试维度成员口径（v2.1/F08，members_at）：考试维度端点按考试发生时
  名册解析成员，且不按当前 status 过滤（v2.2/G07：status 是当下状态，
  考后离班不得改写历史考试人群）；dashboard/看板/预警类沿用查询时点
  （含当期 status 口径）。
"""

from dataclasses import dataclass
from datetime import date
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.context import WorkspaceContext
from app.core.errors import InvalidScopeParam, ResourceOutOfScope, WorkspaceNotConfigured
from app.db.workspace_models import (
    AcademicYear,
    AdministrativeClass,
    Enrollment,
    HomeroomTeachingLink,
    LinkedStudent,
    ScoreFact,
    TeachingClass,
    TeachingClassMember,
    WsStudentAlias,
    WsStudentIdentity,
)

PREVIEW_TTL_MINUTES = 10
_SOURCE_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


# ────────────────────────────── 学年 / 学科口径 ──────────────────────────────


def resolve_year(db: Session, academic_year_id: Optional[int]) -> AcademicYear:
    """academic_year_id 显式传入则校验存在；缺省取最新学年。"""
    if academic_year_id is not None:
        ay = db.get(AcademicYear, academic_year_id)
        if ay is None:
            raise ResourceOutOfScope(
                "academic year not found", details={"academic_year_id": academic_year_id}
            )
        return ay
    ay = (
        db.query(AcademicYear)
        .order_by(AcademicYear.start_date.desc(), AcademicYear.id.desc())
        .first()
    )
    if ay is None:
        raise WorkspaceNotConfigured("no academic year configured")
    return ay


def display_exam_date(fact: ScoreFact) -> Optional[str]:
    """Return the truthful display date without weakening sharing gates.

    A full ``exam_date`` remains the only value used for membership-at-exam and
    cross-domain authorization.  Real legacy rows may only prove ``YYYY-MM``;
    those keep ``exam_date=NULL`` and expose the validated source month solely
    for ordering and display.
    """
    if fact.exam_date is not None:
        return fact.exam_date.isoformat()
    raw = (getattr(fact, "source_exam_date", None) or "").strip()
    if getattr(fact, "exam_date_precision", None) == "month" and _SOURCE_MONTH_RE.fullmatch(raw):
        return raw
    return None


def teaching_subject_for_year(
    db: Session, academic_year_id: int, explicit_subject: Optional[str]
) -> str:
    """任教学科口径（与 config/scope 一致）：显式优先；否则由该学年
    教学班学科推导。无任何教学班 → 未配置 409；多学科且未显式 → 422。"""
    subjects = {
        row[0]
        for row in (
            db.query(TeachingClass.subject)
            .filter(TeachingClass.academic_year_id == academic_year_id)
            .distinct()
            .all()
        )
    }
    if explicit_subject is not None:
        subject = explicit_subject.strip()
        if not subject:
            raise InvalidScopeParam(
                "subject must be a non-empty string", details={"param": "subject"}
            )
        return subject
    if not subjects:
        raise WorkspaceNotConfigured("teaching subject not configured")
    if len(subjects) == 1:
        return next(iter(subjects))
    raise InvalidScopeParam(
        "subject is required when multiple teaching subjects exist",
        details={"param": "subject", "subjects": sorted(subjects)},
    )


def teaching_class_ids_for_subject(
    db: Session, academic_year_id: int, subject: str
) -> List[int]:
    return [
        row[0]
        for row in (
            db.query(TeachingClass.id)
            .filter(
                TeachingClass.academic_year_id == academic_year_id,
                TeachingClass.subject == subject,
            )
            .order_by(TeachingClass.sort_order.asc(), TeachingClass.id.asc())
            .all()
        )
    ]


def build_teaching_params(
    db: Session,
    academic_year_id: Optional[int],
    teaching_class_id: Optional[int],
    subject: Optional[str],
    term_id: Optional[int] = None,
) -> Tuple[dict, str, List[int]]:
    """组装 resolve_workspace_context 的 teaching 参数。

    teaching_class_id 缺省 → 该学年同学科全部教学班（"全部所教班"并集）。
    返回 (params, subject, class_ids)。
    """
    ay = resolve_year(db, academic_year_id)
    resolved_subject = teaching_subject_for_year(db, ay.id, subject)
    if teaching_class_id is not None:
        tc = db.get(TeachingClass, teaching_class_id)
        if tc is None:
            raise ResourceOutOfScope(
                "teaching class not found",
                details={"teaching_class_id": teaching_class_id},
            )
        if tc.academic_year_id != ay.id:
            raise ResourceOutOfScope(
                "teaching class not in this academic year",
                details={"teaching_class_id": teaching_class_id, "academic_year_id": ay.id},
            )
        if tc.subject != resolved_subject:
            raise InvalidScopeParam(
                "subject does not match teaching class",
                details={"teaching_class_id": teaching_class_id, "subject": resolved_subject},
            )
        class_ids = [tc.id]
    else:
        class_ids = teaching_class_ids_for_subject(db, ay.id, resolved_subject)
        if not class_ids:
            raise WorkspaceNotConfigured(
                "no teaching class configured",
                details={"academic_year_id": ay.id, "subject": resolved_subject},
            )
    params: dict = {
        "academic_year_id": ay.id,
        "teaching_class_id": class_ids,
        "subject": resolved_subject,
    }
    if term_id is not None:
        params["term_id"] = term_id
    return params, resolved_subject, class_ids


def check_admin_class_exists(db: Session, class_id: int) -> AdministrativeClass:
    ac = db.get(AdministrativeClass, class_id)
    if ac is None:
        raise ResourceOutOfScope(
            "administrative class not found", details={"class_id": class_id}
        )
    return ac


# ────────────────────────────── 关联 ──────────────────────────────


def active_link_for_teaching_class(
    db: Session, teaching_class_id: int, academic_year_id: int, as_of: date
) -> Optional[HomeroomTeachingLink]:
    return (
        db.query(HomeroomTeachingLink)
        .filter(
            HomeroomTeachingLink.teaching_class_id == teaching_class_id,
            HomeroomTeachingLink.academic_year_id == academic_year_id,
            HomeroomTeachingLink.status == "active",
            HomeroomTeachingLink.valid_from <= as_of,
            or_(
                HomeroomTeachingLink.valid_to.is_(None),
                HomeroomTeachingLink.valid_to >= as_of,
            ),
        )
        .order_by(HomeroomTeachingLink.id.asc())
        .first()
    )


def linked_students_for_link(db: Session, link_id: int) -> List[LinkedStudent]:
    return (
        db.query(LinkedStudent).filter(LinkedStudent.link_id == link_id).all()
    )


def linked_map_for_link(db: Session, link_id: int) -> Dict[int, LinkedStudent]:
    """{homeroom_identity_id: LinkedStudent}（已确认的跨域映射）。"""
    return {ls.homeroom_identity_id: ls for ls in linked_students_for_link(db, link_id)}


# ────────────────────────────── 跨域投影门（契约 §1.4.1，R2/R3/R5） ──────────────────────────────


def share_categories_of(link: HomeroomTeachingLink) -> set:
    """share_categories 逗号分隔白名单（仅识别 roster /
    current_subject_score / current_subject_homework，契约 §1.2.2）。"""
    return {
        part.strip()
        for part in (link.share_categories or "").split(",")
        if part.strip()
    }


def share_history_floor(link: HomeroomTeachingLink) -> date:
    """历史授权下限 = share_history_from ?? valid_from：早于该日期的考试
    事实不得跨域共享。显式授权可以早于关联生效日（重新开放生效前历史，
    架构 §7）；未授权时默认不开放生效前历史（等于 valid_from）。"""
    if link.share_history_from is not None:
        return link.share_history_from
    return link.valid_from


def gate_fact(link: HomeroomTeachingLink, fact: ScoreFact) -> bool:
    """成绩投影的 fact 级门（§1.4.1 条件 2/3）：current_subject_score 已
    授权；exam_date 已知（未知日期不得默认放开）且不早于历史授权下限。
    条件 1（link active + 查询时点有效期）由 ctx.link_id 解析隐含；
    条件 4（双侧成员交集）由 eligible_linked_pairs 隐含；
    条件 5（事实落在对方班级）由取数的 class_ref_id 过滤隐含。"""
    if "current_subject_score" not in share_categories_of(link):
        return False
    # P8/ADR-017：旧库 YYYY-MM 不能被迁移器伪造成某日。整月成员交集
    # 若无经摘要绑定的人工决策无法证明，故月精度事实默认不跨域投影。
    if fact.exam_date is None or getattr(fact, "exam_date_precision", "day") != "day":
        return False
    return fact.exam_date >= share_history_floor(link)


def eligible_linked_pairs(
    db: Session,
    link: HomeroomTeachingLink,
    at: Optional[date],
    require_active_status: bool = True,
) -> Dict[int, int]:
    """统一投影入口（R2）：给定【时点】返回双侧成员均有效的 LinkedStudent
    映射 {homeroom_identity_id: teaching_identity_id}。

    - 成绩 fact 投影：at = fact.exam_date（考试时点；None = 考试日期
      未知，按契约一律不投影），并传 require_active_status=False。
    - 名册类共享（name/seat 投影）：at = ctx.as_of（查询时点），保持
      默认 require_active_status=True。
    - require_active_status（v2.2/G07）：H 侧 Enrollment 的 status 描述
      是"当下"在班状态，考后 transferred/graduated 不得改写历史考试
      时点的成员事实——考试时点交集（成绩投影）只看有效期覆盖；查询
      时点用途（名册投影/配对候选等当前语义）才要求 status='active'。
      T 侧 TeachingClassMember 本无 status 列，不受该参数影响。
    有效性 = H 侧人在 link 行政班有（require_active_status 时 active 的）
    有效期覆盖 at 的 enrollment + T 侧人在 link 教学班有有效期覆盖 at
    的 membership；已确认的映射历史不等于永远允许共享。"""
    if at is None:
        return {}
    pairs = linked_students_for_link(db, link.id)
    if not pairs:
        return {}
    h_filters = [
        Enrollment.admin_class_id == link.admin_class_id,
        Enrollment.identity_id.in_([p.homeroom_identity_id for p in pairs]),
        Enrollment.valid_from <= at,
        or_(Enrollment.valid_to.is_(None), Enrollment.valid_to >= at),
    ]
    if require_active_status:
        # 查询时点用途（名册投影/配对候选）才把"当下 active"并入口径；
        # 考试时点交集绝不能让离班状态改写历史成员（v2.2/G07）
        h_filters.append(Enrollment.status == "active")
    valid_h = {
        row[0]
        for row in (
            db.query(Enrollment.identity_id)
            .filter(*h_filters)
            .distinct()
            .all()
        )
    }
    valid_t = {
        row[0]
        for row in (
            db.query(TeachingClassMember.identity_id)
            .filter(
                TeachingClassMember.teaching_class_id == link.teaching_class_id,
                TeachingClassMember.identity_id.in_(
                    [p.teaching_identity_id for p in pairs]
                ),
                TeachingClassMember.valid_from <= at,
                or_(
                    TeachingClassMember.valid_to.is_(None),
                    TeachingClassMember.valid_to >= at,
                ),
            )
            .distinct()
            .all()
        )
    }
    return {
        p.homeroom_identity_id: p.teaching_identity_id
        for p in pairs
        if p.homeroom_identity_id in valid_h and p.teaching_identity_id in valid_t
    }


def _pairs_valid_at(
    db: Session, link: HomeroomTeachingLink, cache: Dict[date, Dict[int, int]], at: date
) -> Dict[int, int]:
    """按日期去重缓存的 eligible_linked_pairs（同场考试的成员校验只查一次）。

    调用方均为 fact 投影的考试时点交集（at = fact.exam_date），因此固定
    require_active_status=False——考后 transferred/graduated 不得把该生
    从历史考试的共享投影中剔除（v2.2/G07）。"""
    if at not in cache:
        cache[at] = eligible_linked_pairs(
            db, link, at, require_active_status=False
        )
    return cache[at]


def gated_teaching_facts_for_homeroom(
    db: Session,
    ctx: WorkspaceContext,
    link: HomeroomTeachingLink,
    exam_name: Optional[str] = None,
    h_ids: Optional[Sequence[int]] = None,
) -> List[Tuple[ScoreFact, int]]:
    """homeroom 响应的 teaching 域成绩投影（/scores、画像、学生列表共用）。

    返回 [(teaching_fact, homeroom_identity_id)]，仅含通过 §1.4.1 全部
    条件门的 fact。成员交集时点恒为 fact.exam_date（考试时点；v2.1/F03
    后名册响应的共享分数字段亦然，不再提供查询时点旁路）。h_ids 限定
    投影目标（单生画像）。冲突裁决（是否真正入响应）由调用方按 §1.4.1
    执行。"""
    pairs = linked_students_for_link(db, link.id)
    if h_ids is not None:
        allowed = set(h_ids)
        pairs = [p for p in pairs if p.homeroom_identity_id in allowed]
    if not pairs:
        return []
    t_to_pair = {p.teaching_identity_id: p for p in pairs}
    facts = teaching_facts(
        db,
        ctx,
        [link.teaching_class_id],
        link.subject,
        list(t_to_pair),
        exam_name,
    )
    if not facts:
        return []
    cache: Dict[date, Dict[int, int]] = {}
    result: List[Tuple[ScoreFact, int]] = []
    for fact in facts:
        if not gate_fact(link, fact):
            continue
        pair = t_to_pair.get(fact.identity_id)
        if pair is None:
            continue
        if _pairs_valid_at(db, link, cache, fact.exam_date).get(
            pair.homeroom_identity_id
        ) != pair.teaching_identity_id:
            continue
        result.append((fact, pair.homeroom_identity_id))
    return result


def gated_homeroom_facts_for_teaching(
    db: Session,
    ctx: WorkspaceContext,
    link: HomeroomTeachingLink,
    exam_name: Optional[str] = None,
    t_ids: Optional[Sequence[int]] = None,
) -> List[Tuple[ScoreFact, int]]:
    """teaching 响应的反向投影（R5）：linked 学生在 H 域 link 行政班的
    link.subject 事实（total_type 恒 NULL——教学侧绝无总分行）。

    门与正向投影对称：current_subject_score 授权 + 历史日期门 + 考试时点
    双侧成员交集。返回 [(homeroom_fact, teaching_identity_id)]。"""
    pairs = linked_students_for_link(db, link.id)
    if t_ids is not None:
        allowed = set(t_ids)
        pairs = [p for p in pairs if p.teaching_identity_id in allowed]
    if not pairs:
        return []
    facts = (
        _fact_query(
            db,
            "homeroom",
            ctx,
            [p.homeroom_identity_id for p in pairs],
            [link.admin_class_id],
            subject=link.subject,
            exam_name=exam_name,
        )
        .filter(ScoreFact.total_type.is_(None))
        .all()
    )
    if not facts:
        return []
    h_to_pair = {p.homeroom_identity_id: p for p in pairs}
    cache: Dict[date, Dict[int, int]] = {}
    result: List[Tuple[ScoreFact, int]] = []
    for fact in facts:
        if not gate_fact(link, fact):
            continue
        pair = h_to_pair.get(fact.identity_id)
        if pair is None:
            continue
        if _pairs_valid_at(db, link, cache, fact.exam_date).get(
            pair.homeroom_identity_id
        ) != pair.teaching_identity_id:
            continue
        result.append((fact, pair.teaching_identity_id))
    return result


# ────────────────────────────── 身份 / 名册 ──────────────────────────────


def names_for(db: Session, identity_ids: Sequence[int]) -> Dict[int, Optional[str]]:
    if not identity_ids:
        return {}
    rows = (
        db.query(WsStudentIdentity.id, WsStudentIdentity.display_name)
        .filter(WsStudentIdentity.id.in_(list(identity_ids)))
        .all()
    )
    return {row[0]: row[1] for row in rows}


def aliases_for(
    db: Session,
    identity_ids: Sequence[int],
    data_domain: str,
    academic_year_id: Optional[int] = None,
) -> Dict[int, str]:
    """每个身份取一个展示别名：优先本学年登记，其次最早一条。"""
    if not identity_ids:
        return {}
    rows = (
        db.query(WsStudentAlias)
        .filter(
            WsStudentAlias.identity_id.in_(list(identity_ids)),
            WsStudentAlias.data_domain == data_domain,
        )
        .order_by(WsStudentAlias.id.asc())
        .all()
    )
    in_year: Dict[int, str] = {}
    fallback: Dict[int, str] = {}
    for alias in rows:
        if academic_year_id is not None and alias.academic_year_id == academic_year_id:
            in_year.setdefault(alias.identity_id, alias.alias_value)
        else:
            fallback.setdefault(alias.identity_id, alias.alias_value)
    for key, value in fallback.items():
        in_year.setdefault(key, value)
    return in_year


def homeroom_roster(
    db: Session,
    admin_class_id: int,
    as_of: date,
    academic_year_id: Optional[int] = None,
) -> List[dict]:
    """行政班当期在班成员（status='active' 且有效期覆盖 as_of），
    按座号（空座号最后）排序。条目：person_id/name/seat_no/alias/status。
    academic_year_id 用于别名学年优先（v2.1/名册学年别名：优先该学年
    登记，回退最早；缺省保持旧行为）。"""
    rows = (
        db.query(Enrollment, WsStudentIdentity)
        .join(WsStudentIdentity, WsStudentIdentity.id == Enrollment.identity_id)
        .filter(
            Enrollment.admin_class_id == admin_class_id,
            Enrollment.status == "active",
            Enrollment.valid_from <= as_of,
            or_(Enrollment.valid_to.is_(None), Enrollment.valid_to >= as_of),
        )
        .all()
    )
    alias_map = aliases_for(
        db,
        [enrollment.identity_id for enrollment, _ in rows],
        "homeroom",
        academic_year_id,
    )
    roster = [
        {
            "person_id": enrollment.identity_id,
            "name": identity.display_name,
            "seat_no": enrollment.seat_no,
            "alias": alias_map.get(enrollment.identity_id),
            "status": enrollment.status,
        }
        for enrollment, identity in rows
    ]
    roster.sort(key=lambda item: (item["seat_no"] is None, item["seat_no"] or 0, item["person_id"]))
    return roster


def teaching_roster(
    db: Session,
    teaching_class_ids: Sequence[int],
    as_of: date,
    academic_year_id: Optional[int] = None,
) -> List[dict]:
    """教学班当期成员（有效期覆盖 as_of；教学成员无状态列，不推断）。
    条目：person_id/name/seat_no(None)/alias/status(None)，按 person_id
    排序。academic_year_id 透传 aliases_for（别名学年优先，同上）。"""
    if not teaching_class_ids:
        return []
    rows = (
        db.query(TeachingClassMember, WsStudentIdentity)
        .join(WsStudentIdentity, WsStudentIdentity.id == TeachingClassMember.identity_id)
        .filter(
            TeachingClassMember.teaching_class_id.in_(list(teaching_class_ids)),
            TeachingClassMember.valid_from <= as_of,
            or_(
                TeachingClassMember.valid_to.is_(None),
                TeachingClassMember.valid_to >= as_of,
            ),
        )
        .all()
    )
    alias_map = aliases_for(
        db,
        [member.identity_id for member, _ in rows],
        "teaching",
        academic_year_id,
    )
    roster = [
        {
            "person_id": member.identity_id,
            "name": identity.display_name,
            "seat_no": None,
            "alias": alias_map.get(member.identity_id),
            "status": None,
        }
        for member, identity in rows
    ]
    seen: set = set()
    deduped = []
    for item in sorted(roster, key=lambda entry: entry["person_id"]):
        if item["person_id"] not in seen:
            seen.add(item["person_id"])
            deduped.append(item)
    return deduped


# ────────────────────────────── 成绩事实 ──────────────────────────────


def _fact_query(db: Session, data_domain: str, ctx: WorkspaceContext,
                identity_ids: Sequence[int], class_ids: Sequence[int],
                subject: Optional[str] = None, exam_name: Optional[str] = None):
    query = db.query(ScoreFact).filter(
        ScoreFact.data_domain == data_domain,
        ScoreFact.academic_year_id == ctx.academic_year_id,
        ScoreFact.identity_id.in_(list(identity_ids)),
    )
    if class_ids:
        query = query.filter(ScoreFact.class_ref_id.in_(list(class_ids)))
    if subject is not None:
        query = query.filter(ScoreFact.subject == subject)
    if exam_name:
        query = query.filter(ScoreFact.exam_name == exam_name)
    return query


def homeroom_facts(
    db: Session,
    ctx: WorkspaceContext,
    exam_name: Optional[str] = None,
    identity_ids: Optional[Sequence[int]] = None,
) -> List[ScoreFact]:
    """H 域全科+总分事实。identity_ids 缺省取全班当期成员（/scores）；
    画像传 [person_id] 限定目标人（R1：画像绝不混入他人成绩）。
    空成员 → 空列表。"""
    ids = ctx.member_person_ids if identity_ids is None else identity_ids
    if not ids:
        return []
    return _fact_query(
        db, "homeroom", ctx, ids, ctx.class_ids, exam_name=exam_name
    ).all()


def teaching_facts(
    db: Session,
    ctx: WorkspaceContext,
    class_ids: Sequence[int],
    subject: str,
    identity_ids: Sequence[int],
    exam_name: Optional[str] = None,
) -> List[ScoreFact]:
    """teaching 域指定教学班、指定学科的成绩事实（仅任教学科，不含总分行）。"""
    if not identity_ids:
        return []
    return (
        _fact_query(
            db, "teaching", ctx, identity_ids, class_ids, subject=subject, exam_name=exam_name
        )
        .filter(ScoreFact.total_type.is_(None))
        .all()
    )


def latest_fact(facts: Iterable[ScoreFact]) -> Optional[ScoreFact]:
    """最近一场；月精度来源按真实 YYYY-MM 排序，未知日期才按 id 兜底。"""
    return max(
        facts,
        key=lambda f: (display_exam_date(f) is not None, display_exam_date(f) or "", f.id),
        default=None,
    )


def bare_alias_suffix(alias_value: Optional[str]) -> Optional[str]:
    """别名的末尾裸号（如 '2025H6-01' → '01'），用于同名同号候选提示。"""
    if not alias_value:
        return None
    end = len(alias_value)
    while end > 0 and alias_value[end - 1].isdigit():
        end -= 1
    suffix = alias_value[end:]
    return suffix or None


# ────────────────────── 统一可读事实口径（v2.1/F08/F09） ──────────────────────


def members_at(
    db: Session, mode: str, class_ids: Sequence[int], at_date: date
) -> List[int]:
    """指定时点的班级成员（F08 考试维度口径；v2.2/G07：历史时点只看
    有效期覆盖，不按当前 status 过滤——status 描述的是"当下"在班状态，
    考后 transferred/graduated 不得改写历史考试人群，考后入班同理不进
    历史）：homeroom = Enrollment 有效期覆盖 at_date；teaching =
    TeachingClassMember 有效期覆盖 at_date（本无 status 列）。教师可访问
    历史班的校验仍由 ctx（绑定/任课）负责，这里只做成员时点解析；
    当前名册（查询时点 + status 口径）走 homeroom_roster/teaching_roster。
    空结果为合法空态。"""
    if not class_ids:
        return []
    if mode == "homeroom":
        rows = (
            db.query(Enrollment.identity_id)
            .filter(
                Enrollment.admin_class_id.in_(list(class_ids)),
                Enrollment.valid_from <= at_date,
                or_(Enrollment.valid_to.is_(None), Enrollment.valid_to >= at_date),
            )
            .distinct()
            .all()
        )
    else:
        rows = (
            db.query(TeachingClassMember.identity_id)
            .filter(
                TeachingClassMember.teaching_class_id.in_(list(class_ids)),
                TeachingClassMember.valid_from <= at_date,
                or_(
                    TeachingClassMember.valid_to.is_(None),
                    TeachingClassMember.valid_to >= at_date,
                ),
            )
            .distinct()
            .all()
        )
    return sorted({row[0] for row in rows})


def _max_exam_date(
    db: Session, data_domain: str, academic_year_id: int, class_ids: Sequence[int]
) -> Optional[date]:
    if class_ids:
        rows = (
            db.query(ScoreFact.exam_date)
            .filter(
                ScoreFact.data_domain == data_domain,
                ScoreFact.academic_year_id == academic_year_id,
                ScoreFact.class_ref_id.in_(list(class_ids)),
            )
            .distinct()
            .all()
        )
    else:
        rows = (
            db.query(ScoreFact.exam_date)
            .filter(
                ScoreFact.data_domain == data_domain,
                ScoreFact.academic_year_id == academic_year_id,
            )
            .distinct()
            .all()
        )
    dates = [row[0] for row in rows if row[0] is not None]
    return max(dates) if dates else None


def exam_date_for(db: Session, ctx: WorkspaceContext, exam_name: str) -> Optional[date]:
    """考试维度成员解析用的考试日期（F08）：优先本域本班范围事实的
    exam_date（取最大），回退同学年本域其他班，再回退同学年对侧域
    （teaching 统计含反向投影 H 行，H-only 考试也要能解析时点）。
    全部未知（事实均无日期）→ None，调用方回退查询时点成员。"""
    direct = _max_exam_date(db, ctx.data_domain, ctx.academic_year_id, ctx.class_ids)
    if direct is not None:
        return direct
    domain_wide = _max_exam_date(db, ctx.data_domain, ctx.academic_year_id, [])
    if domain_wide is not None:
        return domain_wide
    other = "teaching" if ctx.data_domain == "homeroom" else "homeroom"
    return _max_exam_date(db, other, ctx.academic_year_id, [])


@dataclass
class ReadableFact:
    """统一可读事实行（F09）：本域事实或经五条件门的对侧投影行。

    - fact：底层 ScoreFact（score/exam_date/data_revision 的来源）；
    - person_id：响应所在域的身份 ID（投影行已映射为本域身份）；
    - source_domain：事实来源域（"homeroom" | "teaching"）；
    - class_ref_id：该行归属的班级引用——本域行 = fact.class_ref_id；
      teaching 侧投影行 = 提供投影的 link 教学班 id（H 事实的
      class_ref_id 是行政班，不能混用）；
    - projected：True = 跨域投影行（本域无同 (人, 学科, 考试) 事实）；
    - conflict：本域行与对方同场值不同时的提示 {"teaching_score": 对方值}
      （仅 homeroom 侧本域行携带；冲突保留本域值，绝不投影对方值）；
    - conflict_fact：触发冲突的对方域事实行（便于调用方取最近一场
      冲突来源、data_revision 等；无冲突时为 None）。"""

    fact: ScoreFact
    person_id: int
    source_domain: str
    class_ref_id: Optional[int] = None
    projected: bool = False
    conflict: Optional[dict] = None
    conflict_fact: Optional[ScoreFact] = None


def readable_facts(
    db: Session,
    ctx: WorkspaceContext,
    exam_name: Optional[str] = None,
    member_ids: Optional[Sequence[int]] = None,
    class_ids: Optional[Sequence[int]] = None,
) -> List[ReadableFact]:
    """当前范围允许读取的成绩事实（契约 p3 §1.4 v2.1/F09 统一口径）：
    本域事实（域/学年/班级/成员过滤）+ 经 §1.4.1 五条件门的对侧投影行
    + 冲突注记。/scores、学生列表/画像、分析端点共用本入口；投影门
    （含考试时点成员交集）复用 gated_*_facts_for_*，不另建第二套逻辑。

    member_ids 缺省 = ctx.member_person_ids（查询时点成员）；考试维度
    端点传 members_at(db, ctx.mode, ctx.class_ids, exam_date) 的考试时点
    成员。class_ids 缺省 = ctx.class_ids；class-compare 等按单班取数的
    调用方显式传 [teaching_class_id]，避免多班并集串味。空成员 → 空列表
    （合法空态）。"""
    ids = list(member_ids) if member_ids is not None else list(ctx.member_person_ids)
    scope_class_ids = list(class_ids) if class_ids is not None else list(ctx.class_ids)
    if not ids:
        return []

    if ctx.mode == "homeroom":
        own = _fact_query(
            db, "homeroom", ctx, ids, scope_class_ids, exam_name=exam_name
        ).all()
        entries = [
            ReadableFact(
                fact=f,
                person_id=f.identity_id,
                source_domain="homeroom",
                class_ref_id=f.class_ref_id,
            )
            for f in own
        ]
        if ctx.link_id is None:
            return entries
        link = db.get(HomeroomTeachingLink, ctx.link_id)
        own_by_key = {
            (e.person_id, e.fact.subject, e.fact.exam_name): e
            for e in entries
            if e.fact.total_type is None
        }
        for t_fact, h_id in gated_teaching_facts_for_homeroom(
            db, ctx, link, exam_name, h_ids=ids
        ):
            entry = own_by_key.get((h_id, t_fact.subject, t_fact.exam_name))
            if entry is not None:
                # 值不同（含一方缺考）：保留本域值并附提示；值相同视为
                # 已一致的规范值，不投影、不重复（R5）
                if entry.fact.score != t_fact.score:
                    entry.conflict = {"teaching_score": t_fact.score}
                    entry.conflict_fact = t_fact
            else:
                entries.append(
                    ReadableFact(
                        fact=t_fact,
                        person_id=h_id,
                        source_domain="teaching",
                        class_ref_id=link.admin_class_id,
                        projected=True,
                    )
                )
        return entries

    # teaching：仅任教学科；linked 学生 H 域同学科事实反向投影（T 域
    # 已有同场（值同或值异）保留 T 行、不投影、不附字段）。
    own = teaching_facts(db, ctx, scope_class_ids, ctx.subject, ids, exam_name)
    entries = [
        ReadableFact(
            fact=f,
            person_id=f.identity_id,
            source_domain="teaching",
            class_ref_id=f.class_ref_id,
        )
        for f in own
    ]
    taken_keys = {(e.person_id, e.fact.subject, e.fact.exam_name) for e in entries}
    for tc_id in scope_class_ids:
        link = active_link_for_teaching_class(db, tc_id, ctx.academic_year_id, ctx.as_of)
        if link is None or link.subject != ctx.subject:
            continue
        for h_fact, t_id in gated_homeroom_facts_for_teaching(
            db, ctx, link, exam_name, t_ids=ids
        ):
            key = (t_id, h_fact.subject, h_fact.exam_name)
            if key in taken_keys:
                continue
            taken_keys.add(key)
            entries.append(
                ReadableFact(
                    fact=h_fact,
                    person_id=t_id,
                    source_domain="homeroom",
                    class_ref_id=tc_id,
                    projected=True,
                )
            )
    return entries


def readable_exam_summaries(db: Session, ctx: WorkspaceContext) -> List[dict]:
    """统一可读事实口径的考试清单（F09，/shared/exams 接线入口）：
    readable_facts 全量聚合 exam_name/exam_date/row_count/subjects。月精度旧数据
    用经校验的 source_exam_date（YYYY-MM）展示和排序，但不把它提升为完整日期，
    因而不会绕过 gate_fact 的考试时点共享门。
    本域行与通过门的投影行都计入；冲突事实各自保留本域行、不放大
    row_count。subjects 只统计学科行（total_type 行不计入，契约 §1.4）。
    排序：有日期按日期降序，无日期按名称排后。返回 dict 与
    SharedExamEntry 字段一致，集成者可直接 SharedExamEntry(**item)。"""
    grouped: Dict[str, dict] = {}
    for entry in readable_facts(db, ctx):
        fact = entry.fact
        bucket = grouped.setdefault(
            fact.exam_name, {"exam_date": None, "row_count": 0, "subjects": set()}
        )
        bucket["row_count"] += 1
        shown_date = display_exam_date(fact)
        if shown_date is not None and (
            bucket["exam_date"] is None or shown_date > bucket["exam_date"]
        ):
            bucket["exam_date"] = shown_date
        if fact.total_type is None and fact.subject:
            bucket["subjects"].add(fact.subject)
    dated = sorted(
        ((name, e) for name, e in grouped.items() if e["exam_date"] is not None),
        key=lambda kv: kv[1]["exam_date"],
        reverse=True,
    )
    undated = sorted(
        ((name, e) for name, e in grouped.items() if e["exam_date"] is None),
        key=lambda kv: kv[0],
    )
    return [
        {
            "exam_name": name,
            "exam_date": e["exam_date"],
            "row_count": e["row_count"],
            "subjects": sorted(e["subjects"]),
        }
        for name, e in dated + undated
    ]


# ────────────────────── 跨域冲突配对（v2.2/Q10，契约 p3 §1.6） ──────────────────────


def score_conflict_pairs(
    db: Session, link: HomeroomTeachingLink
) -> List[Tuple[ScoreFact, ScoreFact]]:
    """当前 link 下两域事实仍处冲突状态的配对清单（Q10/R5 收口专用，
    GET score-conflicts 与 POST canonical-scores 共用同一判定）。

    门与读接口完全同一套（不得自建第二套口径）：teaching 侧事实必须通过
    gate_fact（current_subject_score 授权 + 考试日期已知且不早于历史授权
    下限）与 eligible_linked_pairs 的考试时点双侧成员交集
    （require_active_status=False，v2.2/G07）——被任一门挡住的事实不算
    冲突；H 侧事实按读接口语义是本域原生行（本班数据恒可见，readable_facts
    的冲突注记正是挂在 H 行上）。两域同 (人, link.subject, exam_name)
    事实均定位到且 score 不同（含一方缺考 NULL）→ 返回一条
    (homeroom_fact, teaching_fact)。总分行（total_type）与非 link.subject
    事实绝不参与（教学域无总分行，总分口径不跨域比较）。"""
    pairs = linked_students_for_link(db, link.id)
    if not pairs:
        return []
    h_to_pair = {p.homeroom_identity_id: p for p in pairs}
    t_ids = [p.teaching_identity_id for p in pairs]

    def link_facts(
        data_domain: str, identity_ids: Sequence[int], class_ref_id: int
    ) -> List[ScoreFact]:
        # 与读接口 _fact_query 的过滤口径一致（域/学年/班级/成员），仅把
        # ctx 换成 link 的静态范围（本助手不依赖工作台上下文）
        if not identity_ids:
            return []
        return (
            db.query(ScoreFact)
            .filter(
                ScoreFact.data_domain == data_domain,
                ScoreFact.academic_year_id == link.academic_year_id,
                ScoreFact.identity_id.in_(list(identity_ids)),
                ScoreFact.class_ref_id == class_ref_id,
                ScoreFact.subject == link.subject,
                ScoreFact.total_type.is_(None),
            )
            .all()
        )

    h_facts = link_facts("homeroom", list(h_to_pair), link.admin_class_id)
    if not h_facts:
        return []
    t_by_key = {
        (f.identity_id, f.exam_name): f
        for f in link_facts("teaching", t_ids, link.teaching_class_id)
    }
    if not t_by_key:
        return []

    cache: Dict[date, Dict[int, int]] = {}
    result: List[Tuple[ScoreFact, ScoreFact]] = []
    for h_fact in h_facts:
        pair = h_to_pair.get(h_fact.identity_id)
        if pair is None:  # h_ids 即来自 pairs，理论不可达；防御性保持
            continue
        t_fact = t_by_key.get((pair.teaching_identity_id, h_fact.exam_name))
        if t_fact is None:
            continue  # 单侧事实：按门投影为对方行，不是冲突
        if not gate_fact(link, t_fact):
            continue
        if (
            _pairs_valid_at(db, link, cache, t_fact.exam_date).get(
                pair.homeroom_identity_id
            )
            != pair.teaching_identity_id
        ):
            continue
        if h_fact.score == t_fact.score:
            continue  # 值已一致：即确认后的目标状态，不再是冲突
        result.append((h_fact, t_fact))
    result.sort(key=lambda ht: (ht[0].identity_id, ht[0].exam_name))
    return result
