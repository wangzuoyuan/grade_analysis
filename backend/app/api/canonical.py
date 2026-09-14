"""P3 §1.6 跨域冲突规范值确认（Q10——R5 收口）。骨架由集成者预建，子代理填充。

- GET /api/v1/shared/links/{link_id}/score-conflicts：当前冲突清单
- POST /api/v1/shared/links/{link_id}/canonical-scores：确认规范值（两域一致）

审计口径（契约原文）：不另建 audit 表——写入痕迹由两域 score_fact 的
source 追加 ``canonical:<原来源域>`` 与 data_revision+1、以及本端点响应
回执承载。冲突判定统一走 _queries.score_conflict_pairs（与读接口同一门），
本文件不自建第二套投影/过滤逻辑。
"""

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api import _queries as q
from app.api import current_teacher_id, domain_endpoint
from app.core.errors import InvalidScopeParam, LinkVersionConflict, ResourceOutOfScope
from app.db.models import get_db
from app.db.workspace_models import HomeroomTeachingLink, ScoreFact

router = APIRouter(tags=["canonical-scores"])


# ────────────────────────── 请求/响应模型（本文件自治，不改 schemas.py） ──────────────────────────


class ScoreConflictItem(BaseModel):
    """一条当前冲突（H 侧视角；缺考为 null，绝不转 0）。"""

    person_id: int
    name: Optional[str] = None
    subject: str
    exam_name: str
    exam_date: Optional[str] = None
    homeroom_score: Optional[float] = None
    teaching_score: Optional[float] = None


class ScoreConflictsResponse(BaseModel):
    conflicts: List[ScoreConflictItem] = []


class CanonicalResolution(BaseModel):
    """单条确认（契约 §1.6 v2.3）：按"来源侧"选择——canonical_side 必属
    两域之一，规范值 = 该侧当前值（可为 NULL=缺考：核实后缺考是合法规范
    结果，不得强制改成实分）；同一冲突一次只选一侧，本阶段不做人工改分。"""

    person_id: int
    subject: str
    exam_name: str
    canonical_side: Literal["homeroom", "teaching"]
    # 契约字段：缺省 canonical_confirm:<日期>；审计由 source/data_revision
    # 与响应回执承载，本字段仅校验形状（自由文本），不单独落库
    basis: Optional[str] = None


class CanonicalConfirmRequest(BaseModel):
    resolutions: List[CanonicalResolution]


class CanonicalFactResult(BaseModel):
    person_id: int
    subject: str
    exam_name: str
    score: Optional[float] = None
    data_revision: int


class CanonicalConfirmResponse(BaseModel):
    resolved: int
    skipped: int
    facts: List[CanonicalFactResult] = []


# ────────────────────────── 内部助手 ──────────────────────────


def _active_link(db: Session, link_id: int) -> HomeroomTeachingLink:
    """两端点共用的前置门：link 存在且 active（否则 404 / 409）。"""
    link = db.get(HomeroomTeachingLink, link_id)
    if link is None:
        raise ResourceOutOfScope("link not found", details={"link_id": link_id})
    if link.status != "active":
        raise LinkVersionConflict(
            "link is not active", details={"link_id": link_id, "status": link.status}
        )
    return link


def _conflict_items(db: Session, link: HomeroomTeachingLink) -> List[ScoreConflictItem]:
    """冲突配对 → 契约条目（name 取 homeroom 侧身份展示名；H 侧视角）。"""
    pairs = q.score_conflict_pairs(db, link)
    if not pairs:
        return []
    names = q.names_for(db, [h.identity_id for h, _ in pairs])
    return [
        ScoreConflictItem(
            person_id=h_fact.identity_id,
            name=names.get(h_fact.identity_id),
            subject=h_fact.subject or "",
            exam_name=h_fact.exam_name,
            exam_date=(
                h_fact.exam_date.isoformat()
                if h_fact.exam_date is not None
                else (
                    t_fact.exam_date.isoformat()
                    if t_fact.exam_date is not None
                    else None
                )
            ),
            homeroom_score=h_fact.score,
            teaching_score=t_fact.score,
        )
        for h_fact, t_fact in pairs
    ]


def _link_fact_index(
    db: Session, link: HomeroomTeachingLink, data_domain: str, identity_ids: List[int], class_ref_id: int
) -> dict:
    """幂等判定用的两域事实索引 {(identity_id, subject, exam_name): fact}。

    仅定位事实（自然键与读接口 _fact_query 同口径：域/学年/班级/成员/
    link.subject/非总分），不做门判定——门语义只在 score_conflict_pairs
    里生效，这里只为区分"已一致的幂等重复"与"伪造键"。"""
    if not identity_ids:
        return {}
    rows = (
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
    return {(f.identity_id, f.subject or "", f.exam_name): f for f in rows}


# ────────────────────────── §1.6 端点 ──────────────────────────


@router.get(
    "/shared/links/{link_id}/score-conflicts", response_model=ScoreConflictsResponse
)
@domain_endpoint
def list_score_conflicts(link_id: int, db: Session = Depends(get_db)):
    """当前 link 下按 §1.4.1 规则仍处冲突状态的事实清单（两域均有事实且
    值不同，含一方缺考；被共享门挡住的事实不构成冲突）。"""
    current_teacher_id(db)
    link = _active_link(db, link_id)
    return ScoreConflictsResponse(conflicts=_conflict_items(db, link))


@router.post(
    "/shared/links/{link_id}/canonical-scores",
    response_model=CanonicalConfirmResponse,
)
@domain_endpoint
def confirm_canonical_scores(
    link_id: int, req: CanonicalConfirmRequest, db: Session = Depends(get_db)
):
    """确认规范值（契约 §1.6 v2.3）：每条 resolution 对应当前真实冲突，
    canonical_side 指定取哪一侧现值（可为 NULL=缺考）；全部校验通过后单
    事务把两域 ScoreFact 写成所选侧现值（score + data_revision+1 + source
    追加 canonical:<所选来源域>），任一条非法 → 422 整批零写入。两域值
    一致后 §1.4.1 冲突自然消失；已不构成冲突的重复提交 → skipped；伪造
    人/科/考试 → 422。"""
    current_teacher_id(db)
    link = _active_link(db, link_id)
    if not req.resolutions:
        raise InvalidScopeParam(
            "resolutions must contain at least one entry",
            details={"param": "resolutions"},
        )

    # 请求内去重：同键同侧只处理一次（避免同事务重复 +revision）；
    # 同键给出不同侧 → 422（一次只能选一侧）
    unique: dict = {}
    for r in req.resolutions:
        key = (r.person_id, r.subject, r.exam_name)
        prev = unique.get(key)
        if prev is not None:
            if prev.canonical_side != r.canonical_side:
                raise InvalidScopeParam(
                    "conflicting canonical sides for the same conflict",
                    details={
                        "person_id": r.person_id,
                        "subject": r.subject,
                        "exam_name": r.exam_name,
                    },
                )
            continue
        unique[key] = r

    # 校验分三档（对照当前真实冲突清单，判定复用 _queries.score_conflict_pairs）：
    # 1) 在清单 → 任一侧均合法（含缺考 NULL 侧），按侧取现值为规范值；
    # 2) 不在清单但两域事实已一致 → 幂等 skipped（确认后的重复提交；
    #    两域已相等，选任一侧都是同一值，无需再比对）；
    # 3) 人不在映射/科不符/两域事实缺失或仍不同（被门挡住等）→ 422。
    conflict_map = {
        (h.identity_id, h.subject or "", h.exam_name): (h, t)
        for h, t in q.score_conflict_pairs(db, link)
    }
    linked = {p.homeroom_identity_id: p for p in q.linked_students_for_link(db, link.id)}
    h_index = _link_fact_index(
        db, link, "homeroom", list(linked), link.admin_class_id
    )
    t_index = _link_fact_index(
        db, link, "teaching", [p.teaching_identity_id for p in linked.values()],
        link.teaching_class_id,
    )

    def _not_a_conflict(r: CanonicalResolution) -> InvalidScopeParam:
        return InvalidScopeParam(
            "resolution does not match a current conflict",
            details={
                "person_id": r.person_id,
                "subject": r.subject,
                "exam_name": r.exam_name,
            },
        )

    plans: List[tuple] = []
    skipped = 0
    for r in unique.values():
        pair = conflict_map.get((r.person_id, r.subject, r.exam_name))
        if pair is not None:
            plans.append((r, pair[0], pair[1]))
            continue
        pair_row = linked.get(r.person_id)
        if pair_row is None or r.subject != link.subject:
            raise _not_a_conflict(r)
        h_fact = h_index.get((r.person_id, r.subject, r.exam_name))
        t_fact = t_index.get((pair_row.teaching_identity_id, r.subject, r.exam_name))
        if h_fact is None or t_fact is None:
            raise _not_a_conflict(r)
        if h_fact.score != t_fact.score:
            # 两域事实都在但值不同却不在冲突清单：被共享门挡住（历史授权/
            # 成员时点等）——读接口互不可见，不提供确认路径
            raise _not_a_conflict(r)
        skipped += 1

    # 单事务写入：规范值 = 所选侧现值（含 NULL——两域同置缺考，后续读取
    # 不计有效分/排名，§2.3 缺考红线不变）；两域 score 一致 + revision
    # 各 +1 + source 追加规范值来源侧
    facts: List[CanonicalFactResult] = []
    for r, h_fact, t_fact in plans:
        chosen = h_fact.score if r.canonical_side == "homeroom" else t_fact.score
        marker = f"canonical:{r.canonical_side}"
        for fact in (h_fact, t_fact):
            fact.score = chosen
            fact.data_revision = (fact.data_revision or 1) + 1
            source = fact.source or ""
            if marker not in source:
                # 追加而非覆盖：保留原导入来源；SQLite VARCHAR 不强制长度
                fact.source = f"{source} {marker}".strip()
        facts.append(
            CanonicalFactResult(
                person_id=h_fact.identity_id,
                subject=h_fact.subject or "",
                exam_name=h_fact.exam_name,
                score=chosen,
                data_revision=h_fact.data_revision,
            )
        )
    db.commit()
    return CanonicalConfirmResponse(resolved=len(facts), skipped=skipped, facts=facts)
