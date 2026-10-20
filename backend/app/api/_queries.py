"""/api/v1 共享查询助手：作用域参数组装、名册、成绩事实、跨域共享门。

共享规则（docs/contracts/p1-api.md §1.2.2/§1.3/§1.4.1）：
- 2026-10 用户裁决：两工作台成绩信息不互通（各自上传、互不投影），
  成绩跨域投影门与冲突/规范值链路（Q10）已整体退役；share_categories
  仅剩 roster / current_subject_homework 两类授权继续生效。
- 名册共享（name/seat 投影）须 roster 已授权且查询时点双侧成员有效；
  作业共享（current_subject_homework）须作业日期不早于历史授权下限
  （share_history_floor）且作业时点双侧成员交集（eligible_linked_pairs）。
- 无 link 的教学班数据绝不进入 homeroom 响应；缺考 score 保持 null。
- 统一可读事实口径（v2.1/F09，readable_facts / readable_exam_summaries）：
  仅本域事实（同键重复行经 _deduplicate_entries 去重），供 /scores、
  学生列表/画像、分析端点与 /shared/exams 共用；各端点不得自建第二套
  投影逻辑。
- 考试维度成员口径（v2.1/F08，members_at）：考试维度端点按考试发生时
  名册解析成员，且不按当前 status 过滤（v2.2/G07：status 是当下状态，
  考后离班不得改写历史考试人群）；dashboard/看板/预警类沿用查询时点
  （含当期 status 口径）。
"""

from dataclasses import dataclass
from datetime import date
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
from pypinyin import lazy_pinyin

from app.core.context import WorkspaceContext
from app.core.errors import InvalidScopeParam, ResourceOutOfScope, WorkspaceNotConfigured
from app.db.sid_space import strip_ns
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
            .filter(
                TeachingClass.academic_year_id == academic_year_id,
                TeachingClass.status == "active",
            )
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
        # 未换届自动延续：本学年无任何教学班时沿用最近旧学年的学科口径
        #（唯一才延续；多学科旧学年保持需显式/未配置）。
        carried = carryover_subjects(db, academic_year_id)
        if len(carried) == 1:
            return next(iter(carried))
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
                TeachingClass.status == "active",
            )
            .order_by(TeachingClass.sort_order.asc(), TeachingClass.id.asc())
            .all()
        )
    ]


# ────────────────────────────── 学年延续（未换届自动延续） ──────────────────────────────
# 换届不是每学年必须操作：目标学年还没有建立本班/教学班时，读侧沿最近一个
# 有班的旧学年延续（零写入、不建新行），成员有效期本就开放（valid_to NULL），
# 因此旧班成员在新学年继续可见可用；教师随时可用换届把名册正式升入新学年。
# 延续只向「更晚的学年」投影旧班，绝不把后来的班投影回历史学年；旧学年
# 自身无班且更早已无班时保持未配置错误，不猜测。


def carryover_homeroom_class(db: Session, teacher, academic_year_id: int):
    """目标学年无绑定行政班时，沿教师各年级绑定对 (grade, 班号) 取最近一个
    更早学年的行政班作延续班；无候选返回 None（调用方按未配置处理）。"""
    ay = db.get(AcademicYear, academic_year_id)
    if ay is None or teacher is None:
        return None
    pairs = [
        (grade, getattr(teacher, f"target_class_high{grade}"))
        for grade in (1, 2, 3)
    ]
    pairs = [(g, n) for g, n in pairs if n is not None]
    if not pairs:
        return None
    return (
        db.query(AdministrativeClass)
        .join(AcademicYear, AcademicYear.id == AdministrativeClass.academic_year_id)
        .filter(
            AcademicYear.start_date < ay.start_date,
            or_(
                *[
                    and_(
                        AdministrativeClass.grade == g,
                        AdministrativeClass.class_num == n,
                    )
                    for g, n in pairs
                ]
            ),
        )
        .order_by(
            AcademicYear.start_date.desc(),
            AcademicYear.id.desc(),
            AdministrativeClass.id.desc(),
        )
        .first()
    )


def carryover_year_any(db: Session, academic_year_id: int):
    """目标学年没有任何教学班时，最近一个有 active 教学班的更早学年；无则 None。"""
    ay = db.get(AcademicYear, academic_year_id)
    if ay is None:
        return None
    if (
        db.query(TeachingClass.id)
        .filter(TeachingClass.academic_year_id == ay.id)
        .first()
        is not None
    ):
        return None
    return (
        db.query(AcademicYear)
        .join(TeachingClass, TeachingClass.academic_year_id == AcademicYear.id)
        .filter(
            AcademicYear.start_date < ay.start_date,
            TeachingClass.status == "active",
        )
        .order_by(AcademicYear.start_date.desc(), AcademicYear.id.desc())
        .first()
    )


def carryover_subjects(db: Session, academic_year_id: int) -> set:
    """延续学年的 active 学科集合（目标学年无任何教学班时才有意义）。"""
    cy = carryover_year_any(db, academic_year_id)
    if cy is None:
        return set()
    return {
        row[0]
        for row in (
            db.query(TeachingClass.subject)
            .filter(
                TeachingClass.academic_year_id == cy.id,
                TeachingClass.status == "active",
            )
            .distinct()
            .all()
        )
    }


def carryover_teaching_year(db: Session, subject: str, academic_year_id: int):
    """目标学年该学科无任何教学班时，最近一个有该学科 active 班的更早学年。"""
    ay = db.get(AcademicYear, academic_year_id)
    if ay is None:
        return None
    if (
        db.query(TeachingClass.id)
        .filter(
            TeachingClass.academic_year_id == ay.id,
            TeachingClass.subject == subject,
        )
        .first()
        is not None
    ):
        return None
    return (
        db.query(AcademicYear)
        .join(TeachingClass, TeachingClass.academic_year_id == AcademicYear.id)
        .filter(
            AcademicYear.start_date < ay.start_date,
            TeachingClass.subject == subject,
            TeachingClass.status == "active",
        )
        .order_by(AcademicYear.start_date.desc(), AcademicYear.id.desc())
        .first()
    )


def carryover_teaching_classes(db: Session, subject: str, academic_year_id: int):
    """延续口径下目标学年该学科应展示/可用的教学班（旧学年 active 班，排序同目录）。"""
    cy = carryover_teaching_year(db, subject, academic_year_id)
    if cy is None:
        return []
    return (
        db.query(TeachingClass)
        .filter(
            TeachingClass.academic_year_id == cy.id,
            TeachingClass.subject == subject,
            TeachingClass.status == "active",
        )
        .order_by(TeachingClass.sort_order.asc(), TeachingClass.id.asc())
        .all()
    )


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
            # 未换届自动延续：本学年该学科无班时接受最近旧学年的班。
            cy = carryover_teaching_year(db, tc.subject, ay.id)
            if cy is None or tc.academic_year_id != cy.id:
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
            # 未换届自动延续：本学年该学科无班时沿用最近旧学年的班并集。
            class_ids = [tc.id for tc in carryover_teaching_classes(db, resolved_subject, ay.id)]
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


# ────────────────────────────── 跨域共享门（契约 §1.4.1，R2/R3） ──────────────────────────────


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


def _natural_sid_chunks(sid: str) -> List[tuple]:
    """学号自然排序键：数字段按数值、字母段按字符比较。

    学号格式并不统一（纯数字 / 跨届前缀 G1::1234567 / 字母混合
    2025H6-07），纯字典序会把 1234567 排到 999999 之后；
    每段统一成 (是否数字, 数值, 小写文本) 三元组，保证任意两段可比。
    """
    key: List[tuple] = []
    for chunk in re.split(r"(\d+)", sid):
        if chunk.isdigit():
            key.append((1, int(chunk), ""))
        elif chunk:
            key.append((0, 0, chunk.lower()))
    return key


def roster_sid_sort_key(
    alias: Optional[str],
    seat_no: Optional[int],
    name: Optional[str],
    person_id: int,
) -> tuple:
    """名册统一排序键（用户口径：座号就是学号，同一概念）。

    有效学号 = alias（学号）优先，缺时用座号值顶上；有效学号先剥跨届
    前缀（G1::/g1-）再自然升序。无学号者排最后，按姓名拼音（姓氏首字
    母优先）排序；person_id 终兜底保证稳定。
    """
    sid = alias
    if not sid and seat_no is not None:
        sid = str(seat_no)
    name_key = lazy_pinyin(name or "")
    if sid:
        bare, _ = strip_ns(sid)
        return (0, _natural_sid_chunks(bare), name_key, person_id)
    return (1, name_key, person_id)


def _roster_sort_key(item: dict) -> tuple:
    return roster_sid_sort_key(
        item.get("alias"), item.get("seat_no"), item.get("name"), item["person_id"]
    )


def homeroom_roster(
    db: Session,
    admin_class_id: int,
    as_of: date,
    academic_year_id: Optional[int] = None,
) -> List[dict]:
    """行政班当期在班成员（status='active' 且有效期覆盖 as_of），
    按学号（座号即学号：alias 优先、座号值兜底；无学号者按姓名拼音
    排最后）排序。条目：person_id/name/seat_no/alias/status。
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
    roster.sort(key=_roster_sort_key)
    return roster


def teaching_roster(
    db: Session,
    teaching_class_ids: Sequence[int],
    as_of: date,
    academic_year_id: Optional[int] = None,
) -> List[dict]:
    """教学班当期成员（有效期覆盖 as_of；教学成员无状态列，不推断）。
    条目：person_id/name/seat_no(None)/alias/status(None)，按学号
    （alias；无学号者按姓名拼音排最后）排序去重。
    academic_year_id 透传 aliases_for（别名学年优先，同上）。"""
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
    for item in sorted(roster, key=_roster_sort_key):
        if item["person_id"] not in seen:
            seen.add(item["person_id"])
            deduped.append(item)
    return deduped


# ────────────────────────────── 成绩事实 ──────────────────────────────


from app.api.exam_events import fact_key, filter_exam, field_conflicts, exam_predicate, canonical_map, exam_label


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
        query = filter_exam(query, db, ScoreFact, exam_name)
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
    own = filter_exam(db.query(ScoreFact), db, ScoreFact, exam_name).filter(
        ScoreFact.data_domain == ctx.mode, ScoreFact.academic_year_id == ctx.academic_year_id,
        ScoreFact.class_ref_id.in_(ctx.class_ids)).all()
    dates = [f.exam_date for f in own if f.exam_date]
    if dates:
        return max(dates)
    facts = readable_facts(db, ctx, exam_name)
    dates = [e.fact.exam_date for e in facts if e.fact.exam_date]
    return max(dates) if dates else None


@dataclass
class ReadableFact:
    """统一可读事实行（F09）：本域事实行（两域隔离后不再有对侧投影行）。

    - fact：底层 ScoreFact（score/exam_date/data_revision 的来源）；
    - person_id：本域身份 ID；
    - source_domain：事实来源域（"homeroom" | "teaching"）；
    - class_ref_id：该行归属的班级引用 = fact.class_ref_id；
    - conflict / conflict_fact：同 (人, 学科, 考试) 键在本域出现多条且
      字段值不同时的重复来源注记（考试管理「字段差异」展示与指标补齐
      metric-fill 消费；两域隔离后不再有跨域冲突语义）。"""

    fact: ScoreFact
    person_id: int
    source_domain: str
    class_ref_id: Optional[int] = None
    conflict: Optional[dict] = None
    conflict_fact: Optional[ScoreFact] = None


def _deduplicate_entries(db, entries, selector=None, roots=None):
    """同 (人, 学科, 总分口径, 考试) 键去重：保留 id 最大（最新写入）的
    一行；被丢弃的旧行与保留行字段值不同 → 在保留行上附重复来源注记
    （conflict={"fields": …, "duplicate_source": True}）。"""
    grouped = {}
    for entry in sorted(entries, key=lambda e: -e.fact.id):
        key = (entry.person_id, entry.fact.subject, entry.fact.total_type, fact_key(db, entry.fact, roots))
        first = grouped.get(key)
        if first is None:
            grouped[key] = entry
            continue
        fields = field_conflicts(first.fact, entry.fact)
        if fields:
            first.conflict = {**(first.conflict or {}), "fields": fields, "duplicate_source": True}
            first.conflict_fact = entry.fact
    result = list(grouped.values())
    if selector and not selector.startswith(("event:", "legacy:")) and len({fact_key(db, e.fact, roots) for e in result if e.fact.exam_event_id}) > 1:
        raise InvalidScopeParam("同名考试对应多场记录，请按日期和考试标识选择")
    return result


def readable_facts(
    db: Session,
    ctx: WorkspaceContext,
    exam_name: Optional[str] = None,
    member_ids: Optional[Sequence[int]] = None,
    class_ids: Optional[Sequence[int]] = None,
    teaching_exam_id: Optional[int] = None,
) -> List[ReadableFact]:
    """当前范围允许读取的成绩事实（契约 p3 §1.4 v2.1/F09 统一口径）：
    仅本域事实（域/学年/班级/成员过滤；两域隔离后无对侧投影与冲突
    注记）。/scores、学生列表/画像、分析端点共用本入口，不另建第二套
    逻辑。

    member_ids 缺省 = ctx.member_person_ids（查询时点成员）；考试维度
    端点传 members_at(db, ctx.mode, ctx.class_ids, exam_date) 的考试时点
    成员。class_ids 缺省 = ctx.class_ids；class-compare 等按单班取数的
    调用方显式传 [teaching_class_id]，避免多班并集串味。空成员 → 空列表
    （合法空态）。"""
    ids = list(member_ids) if member_ids is not None else list(ctx.member_person_ids)
    scope_class_ids = list(class_ids) if class_ids is not None else list(ctx.class_ids)
    if ctx.mode == "teaching" and exam_name and not exam_name.startswith(("event:", "legacy:")) and teaching_exam_id is None:
        from app.db.workspace_models import TeachingExamScope
        grades = db.query(TeachingExamScope.grade).filter_by(
            academic_year_id=ctx.academic_year_id, subject=ctx.subject, exam_name=exam_name
        ).distinct().all()
        if len(grades) > 1:
            raise InvalidScopeParam("同名考试跨多个年级，请在教学平台成绩页按年级和考试选择")
    if not ids:
        return []

    roots = canonical_map(db, ctx.academic_year_id)
    # 2026-10 用户裁决：两工作台成绩信息不互通（各自上传、互不投影）。
    # readable_facts 只读本域事实；关联（link）仅继续服务名册/作业共享。
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
        return _deduplicate_entries(db, entries, exam_name, roots)

    # teaching：仅任教学科本域事实。
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
    return _deduplicate_entries(db, entries, exam_name, roots)


def readable_exam_summaries(db: Session, ctx: WorkspaceContext) -> List[dict]:
    """统一可读事实口径的考试清单（F09，/shared/exams 接线入口）：
    readable_facts 全量聚合 exam_name/exam_date/row_count/subjects（两域
    隔离后仅本域行）。月精度旧数据用经校验的 source_exam_date（YYYY-MM）
    展示和排序，但不把它提升为完整日期。
    subjects 只统计学科行（total_type 行不计入，契约 §1.4）。
    排序：有日期按日期降序，无日期按名称排后。返回 dict 与
    SharedExamEntry 字段一致，集成者可直接 SharedExamEntry(**item)。"""
    roots = canonical_map(db, ctx.academic_year_id)
    grouped: Dict[str, dict] = {}
    for entry in readable_facts(db, ctx):
        fact = entry.fact
        key = fact_key(db, fact, roots)
        if key not in grouped:
            grouped[key] = {"exam_name": exam_label(db, key), "exam_date": None, "row_count": 0, "subjects": set()}
        bucket = grouped[key]
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
            "exam_name": e["exam_name"],
            "exam_key": name,
            "exam_date": e["exam_date"],
            "row_count": e["row_count"],
            "subjects": sorted(e["subjects"]),
        }
        for name, e in dated + undated
    ]


