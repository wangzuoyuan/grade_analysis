"""升级换届 REST 路由（/api/rollover 前缀）。

四个职责：
  - 四态分类预览（preview）
  - 作业花名册结转（roster）
  - 跨学年身份链接（link / link-batch / link 删除）
  - 名册导入（crosswalk）/ 历史成绩导入（import-history）/ 当前年级切换（active-grade）

身份层完全委托 app.analysis.identity，绝不按姓名自动合并；所有链接必须由
教师确认（name_confirmed）、名册导入（crosswalk）或手工（manual）触发。
"""

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.models import get_db
from app.analysis import identity
from app.rollover import service

router = APIRouter(tags=["rollover"])


# ─────────────────────────── 辅助 ───────────────────────────


def _grade_of_student(db, student_id) -> Optional[int]:
    """该学号在成绩库的 grade（SubjectScore JOIN Exam 的 max grade）；缺省 None。"""
    from app.db.models import SubjectScore, Exam
    from sqlalchemy import func

    if student_id is None:
        return None
    g = (
        db.query(func.max(Exam.grade))
        .join(SubjectScore, SubjectScore.exam_id == Exam.id)
        .filter(SubjectScore.student_id == str(student_id))
        .scalar()
    )
    return int(g) if g is not None else None


def _serialize_alias(al) -> dict:
    return {
        "student_id": al.student_id,
        "grade": al.grade,
        "identity_id": al.identity_id,
        "link_source": al.link_source,
    }


# ─────────────────────────── 四态分类 ───────────────────────────


@router.get("/rollover/preview")
async def rollover_preview(grade: int, class_num: int):
    db = next(get_db())
    try:
        return service.classify(db, grade, class_num)
    finally:
        db.close()


# ─────────────────────────── 花名册结转 ───────────────────────────


class RosterRow(BaseModel):
    # student_id 可空：仅姓名行（后端生成临时学号）；行级 class_num 一律忽略，
    # 严格按目标 grade+class（与教师绑定一致）写入，防止 class_num=None 脏行。
    student_id: Optional[str] = None
    name: Optional[str] = None
    seat_no: Optional[int] = None
    gender: Optional[str] = None
    class_num: Optional[int] = None


class RosterPayload(BaseModel):
    grade: int
    class_num: Optional[int] = None
    from_scores: bool = False
    rows: Optional[list[RosterRow]] = None


@router.post("/rollover/roster")
async def rollover_roster(payload: RosterPayload):
    db = next(get_db())
    try:
        return service.build_roster(
            db,
            payload.grade,
            class_num=payload.class_num,
            from_scores=payload.from_scores,
            rows=[r.model_dump() for r in payload.rows] if payload.rows else None,
        )
    except service.RosterScopeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        db.close()


@router.get("/rollover/roster/last-import")
async def rollover_roster_last_import():
    """最近一次未撤销的「写入名册」批次摘要（供页面恢复撤销按钮）。"""
    db = next(get_db())
    try:
        return service.last_roster_import(db)
    finally:
        db.close()


@router.post("/rollover/roster/{batch_id}/undo")
async def rollover_roster_undo(batch_id: str):
    """撤销一次「写入名册」导入：按批次快照单事务逆向还原。"""
    db = next(get_db())
    try:
        return service.undo_roster_import(db, batch_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.RosterScopeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        db.close()


# ─────────────────────────── 身份链接 ───────────────────────────


class LinkPayload(BaseModel):
    g2_student_id: str
    name: Optional[str] = None
    gender: Optional[str] = None
    g1_student_id: Optional[str] = None
    grade: Optional[int] = None


def _link_one(db, item: LinkPayload) -> dict:
    """单个 link：解析/新建 g2 学号的 identity，挂 name_confirmed 链接。

    返回 {identity_id, identity, aliases}。
    """
    g2 = str(item.g2_student_id)
    # g2 年级：优先 payload.grade，回退成绩库推断，再回退 None
    g2_grade = item.grade
    if g2_grade is None:
        g2_grade = _grade_of_student(db, g2)
    if g2_grade not in (2, 3):
        raise HTTPException(422, "目标年级必须为高二或高三")

    # 跨届学号守门：本届（g2）学号若与其他年级的裸学号撞车，先把撞车旧届
    # 整体 G{g}:: 前缀化让位；请求里的旧届学号（g1）可能还是裸号，迁移
    # 发生后库里已是 G1::x，必须用 renamed 改写为新值再落库，否则别名与
    # 已迁移的高一成绩对不上；g2 属本届，永不改写。
    from app.db.sid_space import ensure_sid_space

    renamed = ensure_sid_space(
        db, {g2}, g2_grade, commit=False, auto_backup=True
    )["renamed"]

    iid = identity.identity_of(db, g2)
    if iid is None:
        # 无 alias：新建 identity（display_name/name, gender）
        iid = identity.ensure_identity(
            db, display_name=item.name, gender=item.gender
        )

    items = [(g2, g2_grade)]
    if item.g1_student_id is not None:
        g1 = renamed.get(str(item.g1_student_id), str(item.g1_student_id))
        items.append((g1, g2_grade - 1))

    identity.link_aliases(db, iid, items, "name_confirmed")

    return {
        "identity_id": iid,
        "identity": {
            "id": iid,
            "display_name": item.name,
            "gender": item.gender,
        },
        "aliases": [_serialize_alias(a) for a in identity.aliases_of(db, iid)],
    }


@router.post("/rollover/link")
async def rollover_link(payload: LinkPayload):
    db = next(get_db())
    try:
        return _link_one(db, payload)
    finally:
        db.close()


class LinkBatchPayload(BaseModel):
    items: list[LinkPayload]


@router.post("/rollover/link-batch")
async def rollover_link_batch(payload: LinkBatchPayload):
    db = next(get_db())
    try:
        linked = []
        conflicts = []
        for item in payload.items:
            res = _link_one(db, item)
            linked.append(
                {
                    "g2_student_id": item.g2_student_id,
                    "identity_id": res["identity_id"],
                }
            )
            # link_aliases 自身不抛冲突；如需上抛可在此扩展
        return {"linked": linked, "conflicts": conflicts}
    finally:
        db.close()


@router.delete("/rollover/link/{student_id}")
async def rollover_unlink(student_id: str):
    db = next(get_db())
    try:
        res = identity.unlink_alias(db, student_id)
        if "error" in res:
            raise HTTPException(404, res["error"])
        return res
    finally:
        db.close()


# ─────────────────────────── 同名批量确认（单事务，整批成败） ───────────────────────────


class ConfirmBatchItem(BaseModel):
    g2_student_id: str
    # name 仅作展示回显；服务端一律以成绩库/花名册内的姓名为真相重新核验。
    name: Optional[str] = None
    decision: Literal["link", "new"]
    g1_student_id: Optional[str] = None


class ConfirmBatchPayload(BaseModel):
    grade: int
    class_num: Optional[int] = None
    items: list[ConfirmBatchItem]


@router.post("/rollover/confirm-batch")
async def rollover_confirm_batch(payload: ConfirmBatchPayload):
    db = next(get_db())
    try:
        return service.confirm_batch(
            db,
            payload.grade,
            payload.class_num,
            [i.model_dump() for i in payload.items],
        )
    except service.RosterScopeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        db.close()


@router.post("/rollover/confirm-batch/{batch_id}/undo")
async def rollover_confirm_batch_undo(batch_id: str):
    db = next(get_db())
    try:
        return service.undo_confirm_batch(db, batch_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.RosterScopeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        db.close()


# ─────────────────────────── 名册导入 ───────────────────────────


class CrosswalkRow(BaseModel):
    g1_sid: str
    g2_sid: str
    name: Optional[str] = None


class CrosswalkPayload(BaseModel):
    rows: list[CrosswalkRow]
    target_grade: Literal[2, 3] = 2


@router.post("/rollover/crosswalk")
async def rollover_crosswalk(payload: CrosswalkPayload):
    db = next(get_db())
    try:
        # 跨届学号守门：本届（g2）学号若与其他年级的裸学号撞车，先把撞车
        # 旧届整体 G{g}:: 前缀化让位；行内 g1_sid 用 renamed 改写为新值再
        # 导入（g2_sid 属本届，永不改写），与 link / confirm-batch 同一理由。
        from app.db.sid_space import ensure_sid_space

        renamed = ensure_sid_space(
            db,
            {r.g2_sid for r in payload.rows},
            payload.target_grade,
            commit=False,
            auto_backup=True,
        )["renamed"]
        rows = []
        for r in payload.rows:
            d = r.model_dump()
            d["g1_sid"] = renamed.get(d["g1_sid"], d["g1_sid"])
            rows.append(d)
        return identity.import_crosswalk(db, rows, payload.target_grade)
    finally:
        db.close()


# ─────────────────────────── 历史成绩导入 ───────────────────────────


class HistoryRow(BaseModel):
    exam_label: Optional[str] = None
    exam_seq: Optional[int] = None
    kind: str = "subject"  # subject | total
    subject: Optional[str] = None
    total_type: Optional[str] = None
    raw_score: Optional[float] = None
    grade_score: Optional[float] = None
    grade_percentile: Optional[float] = None
    xueji_rank: Optional[int] = None
    grade: int = 1


class ImportHistoryPayload(BaseModel):
    identity_id: Optional[int] = None
    student_id: Optional[str] = None
    link_g1_student_id: Optional[str] = None
    name: Optional[str] = None
    target_grade: Optional[Literal[2, 3]] = None
    rows: list[HistoryRow]


@router.post("/rollover/import-history")
async def rollover_import_history(payload: ImportHistoryPayload):
    db = next(get_db())
    try:
        return service.import_history(
            db,
            identity_id=payload.identity_id,
            student_id=payload.student_id,
            link_g1_student_id=payload.link_g1_student_id,
            name=payload.name,
            target_grade=payload.target_grade,
            rows=[r.model_dump() for r in payload.rows],
        )
    finally:
        db.close()


# ─────────────────────────── 当前年级 ───────────────────────────


class ActiveGradePayload(BaseModel):
    grade: Literal[1, 2, 3]


@router.patch("/rollover/active-grade")
async def rollover_set_active_grade(payload: ActiveGradePayload):
    db = next(get_db())
    try:
        try:
            return service.set_active_grade(db, payload.grade)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        db.close()
