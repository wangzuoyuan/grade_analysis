"""学生管理服务：以 StudentIdentity 为主档、StudentAlias 为历年学号、
ClassRoster 为唯一花名册（绝不另建第二套名册）。

作用域铁律：所有读写都限定在教师当前绑定的 grade + class_num（active_grade
驱动，与作业模块同一口径）；显式请求其他班级一律拒绝。

写操作口径：
  - 纠正录错学号（correct_sid）：单一事务把 SubjectScore / TotalScore /
    ClassRoster / HomeworkRecord / SpecialRecord / StudentNote / StudentAlias
    的全部运行时引用从旧学号迁到新学号；目标学号被其他身份占用、同场考试
    已有双方数据、跨班越权 → 整体拒绝回滚（纠正后旧学号不再存在）。
  - 新增学年学号（new_year_sid）：只新增同一身份的 alias 与目标学年花名册
    行，旧学号与历史数据原样保留。
  - 删除：无关联数据的误建学生可彻底删除；有任何关联数据时必须先看影响
    计数并显式确认，删除前自动打包备份（复用 backup.router.create_backup），
    整笔事务失败回滚。转班/毕业用 archive 状态表达，绝不冒充删除。
  - 合并（merge）：教师显式确认后把重复学号并入主学号；两学号在同一场
    考试都有数据时冲突拒绝（哪条成绩为准无法自动裁决），绝不自动合并。
  - 身份回填（backfill）：给没有 identity 的当前班学生建主档，幂等；绝不
    按姓名自动合并同名学生（同名各建独立 identity）。

所有写操作写入 StudentChangeLog（同事务，字段级前后摘要，不含任何凭据）。
身份层复用 app.analysis.identity；学号校验复用 rollover.service 的
_validate_official_sid / temp_sid / _norm_name，保持全站同一口径；显式学号
写入前统一经 app.db.sid_space.ensure_sid_space 跨届撞车守门（与换届名册
导入 / 成绩上传同一口径）。
"""

from datetime import datetime


class _Unset:
    """「字段未提交」哨兵：区别于显式提交的 None（显式清空）。"""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return "UNSET"


UNSET = _Unset()


class ScopeError(ValueError):
    """与教师绑定作用域不一致（HTTP 409）。"""


class NotFoundError(ValueError):
    """学生在当前作用域不存在（HTTP 404）。"""


class ConfirmRequired(Exception):
    """有关联数据的破坏性操作缺少显式确认（HTTP 409，payload 为影响计数）。"""

    def __init__(self, payload: dict):
        super().__init__("该学生存在关联数据，需要明确确认")
        self.payload = payload


class MergeConflict(Exception):
    """两个学号在同一场考试都有数据，无法自动合并（HTTP 409）。"""

    def __init__(self, payload: dict):
        super().__init__("两个学号在同一场考试都有数据，无法自动合并")
        self.payload = payload


ARCHIVE_STATUSES = ("transferred", "graduated")  # 离班状态；None/'active'=在班
_OP_TYPES = (
    "create",
    "update",
    "correct_sid",
    "new_year_sid",
    "archive",
    "restore",
    "delete",
    "merge",
    "backfill",
)


def _norm_name(name) -> str:
    """姓名规范化：去掉全部空白（与 rollover.service._norm_name 同口径）。"""
    return "".join(str(name or "").split())


def _managed_scope(db) -> tuple[int, int]:
    """当前班主任作用域：active_grade + 该年级绑定行政班。未绑定 → 409。"""
    from app.homework.service import get_active_class_num
    from app.rollover.service import get_active_grade

    grade = get_active_grade(db)
    class_num = get_active_class_num(db, grade=grade)
    if class_num is None:
        raise ScopeError("当前年级尚未绑定班级，请先完成班级配置")
    return int(grade), int(class_num)


def _scope_roster_row(db, sid: str, grade: int, class_num: int):
    from app.db.models import ClassRoster

    return (
        db.query(ClassRoster)
        .filter(
            ClassRoster.student_id == sid,
            ClassRoster.grade == grade,
            ClassRoster.class_num == class_num,
        )
        .first()
    )


def _in_scope(db, sid: str, grade: int, class_num: int) -> bool:
    """学号属于当前作用域：本班花名册有行，或本年级本班成绩库有数据。"""
    from app.db.models import Exam, SubjectScore

    if _scope_roster_row(db, sid, grade, class_num) is not None:
        return True
    row = (
        db.query(SubjectScore.id)
        .join(Exam, Exam.id == SubjectScore.exam_id)
        .filter(
            SubjectScore.student_id == sid,
            Exam.grade == grade,
            SubjectScore.class_num == class_num,
        )
        .first()
    )
    return row is not None


def _require_in_scope(db, sid: str, grade: int, class_num: int) -> str:
    sid = str(sid)
    if not _in_scope(db, sid, grade, class_num):
        raise NotFoundError("当前班级中不存在该学生")
    return sid


def _ref_counts_many(db, sids) -> dict:
    """批量统计多个学号在各业务表的关联行数（GROUP BY，避免逐人 N+1）。"""
    from sqlalchemy import func

    from app.db.models import (
        HomeworkRecord,
        SpecialRecord,
        StudentNote,
        SubjectScore,
        TotalScore,
    )

    ids = list(dict.fromkeys(str(s) for s in sids))
    out = {
        sid: {
            "subject_score": 0,
            "total_score": 0,
            "homework": 0,
            "special": 0,
            "note": 0,
        }
        for sid in ids
    }
    for model, key in (
        (SubjectScore, "subject_score"),
        (TotalScore, "total_score"),
        (HomeworkRecord, "homework"),
        (SpecialRecord, "special"),
        (StudentNote, "note"),
    ):
        rows = (
            db.query(model.student_id, func.count())
            .filter(model.student_id.in_(ids))
            .group_by(model.student_id)
            .all()
            if ids
            else []
        )
        for sid, cnt in rows:
            out[sid][key] = int(cnt or 0)
    return out


def _ref_counts(db, sid: str) -> dict:
    """某学号在各业务表的关联行数（删除/合并影响预览共用口径）。"""
    return _ref_counts_many(db, [sid])[str(sid)]


def _migrate_refs(db, old_sid: str, new_sid: str) -> dict:
    """把某学号在所有运行时业务表的引用整体迁到另一学号（调用方负责事务）。"""
    from app.db.models import (
        HomeworkRecord,
        SpecialRecord,
        StudentNote,
        SubjectScore,
        TotalScore,
    )

    moved = {}
    for model in (SubjectScore, TotalScore, HomeworkRecord, SpecialRecord, StudentNote):
        moved[model.__tablename__] = (
            db.query(model)
            .filter(model.student_id == old_sid)
            .update({model.student_id: new_sid}, synchronize_session=False)
        )
    return moved


def _exam_overlap(db, sid_a: str, sid_b: str) -> list[dict]:
    """两学号在同一场考试都有成绩（SubjectScore 或 TotalScore）的考试列表。"""
    from app.db.models import Exam, SubjectScore, TotalScore

    overlap_ids: set[int] = set()
    for model in (SubjectScore, TotalScore):
        rows = (
            db.query(model.student_id, model.exam_id)
            .filter(model.student_id.in_([sid_a, sid_b]))
            .distinct()
            .all()
        )
        by_sid: dict[str, set[int]] = {sid_a: set(), sid_b: set()}
        for sid, exam_id in rows:
            if sid in by_sid and exam_id is not None:
                by_sid[sid].add(exam_id)
        overlap_ids |= by_sid[sid_a] & by_sid[sid_b]

    if not overlap_ids:
        return []
    exams = db.query(Exam).filter(Exam.id.in_(overlap_ids)).all()
    name_by = {e.id: e.name for e in exams}
    return [
        {"exam_id": eid, "exam_name": name_by.get(eid, str(eid))}
        for eid in sorted(overlap_ids)
    ]


def _canonical_name(db, sid: str) -> tuple[str, str]:
    """学号的规范姓名与来源：主档 display_name > 花名册 > 成绩快照。"""
    from app.analysis.identity import display_names
    from app.db.models import ClassRoster, SubjectScore

    canonical = display_names(db, [sid]).get(sid)
    if canonical:
        return canonical, "identity"
    roster = (
        db.query(ClassRoster.name)
        .filter(ClassRoster.student_id == sid, ClassRoster.name.isnot(None))
        .first()
    )
    if roster and roster[0]:
        return roster[0], "roster"
    score = (
        db.query(SubjectScore.name)
        .filter(SubjectScore.student_id == sid, SubjectScore.name.isnot(None))
        .first()
    )
    if score and score[0]:
        return score[0], "score"
    return sid, "sid"


_EMPTY_LATEST = {"latest_exam_name": None, "latest_main_score": None, "latest_main_rank": None}


def _latest_main_many(db, sids) -> dict:
    """批量取多个学号最近一场主三门总分摘要（沿用 /api/students 口径）。"""
    from app.db.models import Exam, TotalScore

    ids = list(dict.fromkeys(str(s) for s in sids))
    out = {sid: dict(_EMPTY_LATEST) for sid in ids}
    if not ids:
        return out
    rows = (
        db.query(TotalScore, Exam)
        .join(Exam, Exam.id == TotalScore.exam_id)
        .filter(
            TotalScore.student_id.in_(ids),
            TotalScore.total_type == "主三门",
        )
        .order_by(Exam.exam_date.desc().nullslast(), Exam.id.desc())
        .all()
    )
    for total, exam in rows:  # 已按考试时间倒序：每个学号取第一条
        slot = out.get(total.student_id)
        if slot is None or slot["latest_exam_name"] is not None:
            continue
        slot.update(
            {
                "latest_exam_name": exam.name,
                "latest_main_score": total.total_score,
                "latest_main_rank": (
                    total.xueji_rank
                    if total.xueji_rank is not None
                    else total.grade_percentile
                ),
            }
        )
    return out


def _latest_main(db, sid: str) -> dict:
    """该学号最近一场有主三门总分的考试摘要（沿用 /api/students 口径）。"""
    return _latest_main_many(db, [sid])[str(sid)]


def _log_change(
    db,
    op_type: str,
    *,
    identity_id=None,
    student_id=None,
    before=None,
    after=None,
    detail=None,
    grade=None,
    class_num=None,
) -> None:
    """写一条变更日志（调用方事务内，commit/rollback 随主操作）。

    grade/class_num 记录操作发生时教师绑定的作用域，供变更日志按当前班
    过滤（绝不让本班视图读到其他班级的操作留痕）。"""
    from app.db.models import StudentChangeLog

    if op_type not in _OP_TYPES:
        raise ValueError(f"未知操作类型：{op_type}")
    db.add(
        StudentChangeLog(
            op_type=op_type,
            identity_id=identity_id,
            student_id=student_id,
            before_summary=before,
            after_summary=after,
            detail=detail,
            grade=grade,
            class_num=class_num,
            created_at=datetime.utcnow(),
        )
    )


# ─────────────────────────── 列表 / 详情 ───────────────────────────


def _scope_student_ids(db, grade: int, class_num: int, include_archived: bool) -> list[str]:
    """当前作用域学生全集 = 本班花名册 ∪ 本年级本班成绩库学号。

    默认剔除已归档（transferred/graduated）的花名册学生——归档即离开当前
    班；include_archived=True 时保留展示。
    """
    from app.db.models import ClassRoster, Exam, SubjectScore

    roster_rows = (
        db.query(ClassRoster)
        .filter(ClassRoster.grade == grade, ClassRoster.class_num == class_num)
        .all()
    )
    score_sids = {
        row[0]
        for row in (
            db.query(SubjectScore.student_id)
            .join(Exam, Exam.id == SubjectScore.exam_id)
            .filter(Exam.grade == grade, SubjectScore.class_num == class_num)
            .distinct()
            .all()
        )
        if row[0] is not None
    }
    sids: list[str] = []
    for r in roster_rows:
        if not include_archived and r.status in ARCHIVE_STATUSES:
            continue
        sids.append(r.student_id)
    roster_sid_set = {r.student_id for r in roster_rows}
    sids.extend(sorted(score_sids - roster_sid_set))
    return sids


def _identity_rows_for(db, sids) -> dict:
    """批量解析学号 -> StudentIdentity（无 alias 的学号不出现）。"""
    from app.db.models import StudentAlias, StudentIdentity

    ids = list(dict.fromkeys(str(s) for s in sids))
    if not ids:
        return {}
    rows = (
        db.query(StudentAlias.student_id, StudentIdentity)
        .join(StudentIdentity, StudentIdentity.id == StudentAlias.identity_id)
        .filter(StudentAlias.student_id.in_(ids))
        .all()
    )
    return {sid: ident for sid, ident in rows}


def list_students(db, search=None, include_archived=False) -> list[dict]:
    """学生管理列表（当前作用域），含主档/花名册字段、关联计数与最近主三门。

    主档字段（规范姓名/性别/备注）优先 identity；列表批量取数，绝不逐人
    查 identity/计数/最近考试。"""
    from app.analysis.identity import display_names
    from app.db.models import ClassRoster, StudentAlias, SubjectScore

    grade, class_num = _managed_scope(db)
    sids = _scope_student_ids(db, grade, class_num, include_archived)

    roster_by_sid: dict[str, ClassRoster] = {}
    for r in db.query(ClassRoster).filter(
        ClassRoster.grade == grade, ClassRoster.class_num == class_num
    ).all():
        roster_by_sid[r.student_id] = r

    score_name_by_sid: dict[str, str] = {}
    if sids:
        for sid, nm in (
            db.query(SubjectScore.student_id, SubjectScore.name)
            .filter(SubjectScore.student_id.in_(sids), SubjectScore.name.isnot(None))
            .all()
        ):
            if sid not in score_name_by_sid and nm:
                score_name_by_sid[sid] = nm

    canonical_map = display_names(db, sids)
    ident_by_sid = _identity_rows_for(db, sids)
    aliases_by_iid: dict = {}
    if ident_by_sid:
        identity_ids = {i.id for i in ident_by_sid.values()}
        for a in (
            db.query(StudentAlias)
            .filter(StudentAlias.identity_id.in_(identity_ids))
            .order_by(StudentAlias.grade.asc())
            .all()
        ):
            aliases_by_iid.setdefault(a.identity_id, []).append(a)
    all_alias_sids = {a.student_id for group in aliases_by_iid.values() for a in group}
    alias_roster_by_sid = {
        r.student_id: r
        for r in db.query(ClassRoster).filter(ClassRoster.student_id.in_(all_alias_sids)).all()
    } if all_alias_sids else {}

    counts_by_sid = _ref_counts_many(db, sids)
    latest_by_sid = _latest_main_many(db, sids)

    results = []
    for sid in sids:
        roster = roster_by_sid.get(sid)
        ident = ident_by_sid.get(sid)
        iid = ident.id if ident is not None else None

        canonical = canonical_map.get(sid)
        if canonical:
            name, name_source = canonical, "identity"
        elif roster is not None and roster.name:
            name, name_source = roster.name, "roster"
        elif sid in score_name_by_sid:
            name, name_source = score_name_by_sid[sid], "score"
        else:
            name, name_source = sid, "sid"

        if search:
            hay = f"{name} {sid}"
            if search not in _norm_name(hay) and search not in hay:
                continue

        alias_items = []
        for a in aliases_by_iid.get(iid, []) if iid is not None else []:
            if a.student_id == sid:
                continue
            ar = alias_roster_by_sid.get(a.student_id)
            alias_items.append(
                {
                    "student_id": a.student_id,
                    "grade": a.grade,
                    "class_num": ar.class_num if ar is not None else None,
                }
            )

        results.append(
            {
                "student_id": sid,
                "name": name,
                "name_source": name_source,
                "gender": (ident.gender if ident is not None and ident.gender else None)
                or (roster.gender if roster is not None else None),
                "seat_no": roster.seat_no if roster is not None else None,
                "note": ident.note if ident is not None else None,
                "excluded": roster.excluded if roster is not None else 0,
                "status": roster.status if roster is not None else None,
                "in_roster": roster is not None,
                "identity_id": iid,
                "aliases": alias_items,
                "counts": counts_by_sid[sid],
                **latest_by_sid[sid],
            }
        )

    results.sort(
        key=lambda r: (
            r["seat_no"] is None,
            r["seat_no"] if r["seat_no"] is not None else 0,
            r["name"],
        )
    )
    return results


def student_detail(db, student_id: str) -> dict:
    """单个学生管理详情：主档 + 花名册 + 关联计数 + 历史学号。"""
    from app.analysis.identity import aliases_of, identity_of
    from app.db.models import ClassRoster, StudentIdentity

    grade, class_num = _managed_scope(db)
    sid = _require_in_scope(db, student_id, grade, class_num)

    roster = _scope_roster_row(db, sid, grade, class_num)
    iid = identity_of(db, sid)
    ident = (
        db.query(StudentIdentity).filter(StudentIdentity.id == iid).first()
        if iid is not None
        else None
    )
    name, name_source = _canonical_name(db, sid)

    aliases = []
    alias_rows = aliases_of(db, iid) if iid is not None else []
    roster_by_key: dict = {}
    if alias_rows:
        for r in (
            db.query(ClassRoster)
            .filter(ClassRoster.student_id.in_([a.student_id for a in alias_rows]))
            .all()
        ):
            roster_by_key[(r.student_id, r.grade)] = r
    for a in alias_rows:
        ar = roster_by_key.get((a.student_id, a.grade)) if a.grade is not None else None
        aliases.append(
            {
                "student_id": a.student_id,
                "grade": a.grade,
                "class_num": ar.class_num if ar is not None else None,
                "link_source": a.link_source,
                "is_current": a.student_id == sid,
            }
        )

    return {
        "student_id": sid,
        "name": name,
        "name_source": name_source,
        "grade": grade,
        "class_num": class_num,
        "gender": (ident.gender if ident is not None and ident.gender else None)
        or (roster.gender if roster is not None else None),
        "seat_no": roster.seat_no if roster is not None else None,
        "note": ident.note if ident is not None else None,
        "excluded": roster.excluded if roster is not None else 0,
        "status": roster.status if roster is not None else None,
        "in_roster": roster is not None,
        "identity_id": iid,
        "identity_display_name": ident.display_name if ident is not None else None,
        "aliases": aliases,
        "counts": _ref_counts(db, sid),
        **_latest_main(db, sid),
    }


# ─────────────────────────── 新建 / 编辑 ───────────────────────────


def create_student(db, *, name, student_id=None, gender=None, seat_no=None, note=None) -> dict:
    """新建当前班学生：花名册行 + 身份主档 + alias 一次建齐。

    - 不填学号：沿用换届临时学号 temp_sid（幂等、之后可被正式学号事务性
      替换，不产生第二套学号体系）；
    - 显式学号：统一过 _validate_official_sid（成绩库姓名 / 目标年级班级 /
      身份别名占用），被占用或越权直接拒绝；
    - 本班已有同名学生：仅姓名行拒绝（无法区分），提供学号可建（同名不同
      人靠学号区分，绝不自动合并）。
    """
    from app.analysis.identity import ensure_identity, identity_of, link_aliases
    from app.db.models import ClassRoster
    from app.rollover.service import _validate_official_sid, temp_sid

    grade, class_num = _managed_scope(db)
    name = str(name or "").strip()
    if not name:
        raise ValueError("姓名不能为空")

    sid = str(student_id).strip() if student_id else None
    same_name = (
        db.query(ClassRoster)
        .filter(
            ClassRoster.grade == grade,
            ClassRoster.class_num == class_num,
            ClassRoster.name == name,
        )
        .first()
    )

    try:
        if sid:
            if db.query(ClassRoster).filter(ClassRoster.student_id == sid).first():
                raise ValueError(f"学号 {sid} 已存在于花名册，不能重复使用")
            if identity_of(db, sid) is not None:
                raise ValueError(f"学号 {sid} 已关联跨学年身份，不能重复建档")
            # 跨届学号守门：显式学号若与其他年级的裸学号撞车，先把撞车旧届
            # 整体 G{g}:: 前缀化让位，本届再写裸号；与下方落库同一事务，
            # 任一步失败整体回滚（自动备份与删除/合并的安全口径一致）。
            from app.db.sid_space import ensure_sid_space

            ensure_sid_space(db, {sid}, grade, commit=False, auto_backup=True)
            _validate_official_sid(db, sid, name, grade, class_num)
        else:
            if same_name is not None:
                raise ValueError(
                    f"本班已存在同名学生「{name}」。若为同一人请使用合并功能；"
                    "若为不同人请提供学号以区分"
                )
            sid = temp_sid(grade, class_num, name)

        db.add(
            ClassRoster(
                student_id=sid,
                name=name,
                class_num=class_num,
                grade=grade,
                seat_no=seat_no,
                gender=gender,
                excluded=0,
                status=None,
            )
        )
        iid = ensure_identity(db, display_name=name, gender=gender, commit=False)
        if note:
            from app.db.models import StudentIdentity

            ident = db.query(StudentIdentity).filter(StudentIdentity.id == iid).first()
            if ident is not None:
                ident.note = str(note).strip() or None
        link_aliases(db, iid, [(sid, grade)], "manual", commit=False)
        _log_change(
            db,
            "create",
            identity_id=iid,
            student_id=sid,
            after={"name": name, "student_id": sid, "gender": gender, "seat_no": seat_no},
            detail={"grade": grade, "class_num": class_num},
            grade=grade,
            class_num=class_num,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"student_id": sid, "identity_id": iid, "grade": grade, "class_num": class_num}


def update_student(
    db,
    student_id: str,
    *,
    name=UNSET,
    gender=UNSET,
    seat_no=UNSET,
    note=UNSET,
    status=UNSET,
) -> dict:
    """编辑学生：规范姓名/性别/备注写主档（StudentIdentity），座号/在班状态
    写花名册。单事务原子提交（编辑 + 归档不再分两次请求）。

    未提交字段传 UNSET 哨兵、显式清空传 None——二者语义不同：UNSET 保持
    原值，None 清空该字段（gender/seat_no/note 均可显式清空）。规范姓名
    变更同步到该身份全部花名册行的展示名；SubjectScore.name 作为原始上传
    快照绝不改写。仅限当前作用域学生；无主档的学生编辑时顺带补建（幂等，
    不合并同名）。
    """
    from app.analysis.identity import aliases_of, ensure_identity, identity_of, link_aliases
    from app.db.models import ClassRoster, StudentIdentity

    grade, class_num = _managed_scope(db)
    sid = _require_in_scope(db, student_id, grade, class_num)
    roster = _scope_roster_row(db, sid, grade, class_num)

    iid = identity_of(db, sid)
    ident = (
        db.query(StudentIdentity).filter(StudentIdentity.id == iid).first()
        if iid is not None
        else None
    )
    old_name, _ = _canonical_name(db, sid)
    old_gender = (ident.gender if ident is not None and ident.gender else None) or (
        roster.gender if roster is not None else None
    )

    before = {
        "name": old_name,
        "gender": old_gender,
        "seat_no": roster.seat_no if roster is not None else None,
        "note": ident.note if ident is not None else None,
        "status": (roster.status or "active") if roster is not None else None,
    }

    def _create_identity(display_name: str, *, gender_value=None):
        """顺带补建主档：identity + alias 同事务建齐（绝不合并同名）。"""
        nonlocal iid, ident
        iid = ensure_identity(
            db, display_name=display_name, gender=gender_value, commit=False
        )
        link_aliases(db, iid, [(sid, grade)], "manual", commit=False)
        ident = db.query(StudentIdentity).filter(StudentIdentity.id == iid).first()
        return ident

    after = dict(before)
    applied: set = set()

    try:
        if name is not UNSET:
            applied.add("name")
            new_name = str(name).strip() if name is not None else ""
            if not new_name:
                raise ValueError("姓名不能为空")
            if _norm_name(new_name) != _norm_name(old_name):
                after["name"] = new_name
                if ident is None:
                    _create_identity(new_name)
                else:
                    ident.display_name = new_name
                # 规范名同步到该身份的全部花名册行（展示名统一，快照不动）
                person_sids = [a.student_id for a in aliases_of(db, iid)]
                if person_sids:
                    db.query(ClassRoster).filter(ClassRoster.student_id.in_(person_sids)).update(
                        {ClassRoster.name: new_name}, synchronize_session=False
                    )

        if gender is not UNSET:
            applied.add("gender")
            after["gender"] = None if gender is None else (str(gender).strip() or None)
            if ident is None:
                _create_identity(old_name, gender_value=after["gender"])
            else:
                ident.gender = after["gender"]

        if note is not UNSET:
            applied.add("note")
            after["note"] = None if note is None else (str(note).strip() or None)
            if ident is None:
                ident = _create_identity(old_name)
            ident.note = after["note"]

        if seat_no is not UNSET:
            applied.add("seat_no")
            after["seat_no"] = (
                None
                if seat_no is None or str(seat_no).strip() == ""
                else int(seat_no)
            )

        if status is not UNSET:
            applied.add("status")
            st = str(status or "").strip()
            if st not in ("active", "transferred", "graduated"):
                raise ValueError("状态只能是 active / transferred / graduated")
            after["status"] = st

        if roster is None:
            # 成绩在册但未建花名册的学生：编辑时补建花名册行（仍只有一个名册）
            roster = ClassRoster(
                student_id=sid,
                name=after.get("name") or old_name,
                class_num=class_num,
                grade=grade,
                seat_no=after.get("seat_no"),
                gender=after.get("gender"),
                excluded=0,
                status=None
                if after.get("status", "active") == "active"
                else after.get("status"),
            )
            db.add(roster)
            db.flush()
        else:
            if "name" in applied and after["name"] != before["name"]:
                roster.name = after["name"]
            if "gender" in applied:
                roster.gender = after["gender"]
            if "seat_no" in applied:
                roster.seat_no = after["seat_no"]
            if "status" in applied:
                roster.status = None if after["status"] == "active" else after["status"]

        _log_change(
            db,
            "update",
            identity_id=iid,
            student_id=sid,
            before={k: v for k, v in before.items() if v is not None},
            after=after,
            detail={"grade": grade, "class_num": class_num},
            grade=grade,
            class_num=class_num,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"student_id": sid, "identity_id": iid, "changed": {
        k: {"before": before.get(k), "after": after.get(k)}
        for k in after
        if after.get(k) != before.get(k)
    }}


# ─────────────────────────── 学号管理 ───────────────────────────


def correct_student_id(db, student_id: str, new_student_id: str) -> dict:
    """纠正录错学号：单一事务迁移全部运行时引用，旧学号彻底退役。

    拒绝条件（任一命中整体回滚，绝不部分成功）：
      - 新学号已被花名册其他行占用，或已关联其他身份；
      - 新学号与旧学号在同一场考试都有成绩（SubjectScore/TotalScore），
        合并会让同场考试出现双份数据；
      - 新学号在成绩库的姓名与本生不符、或其本年级成绩属于其他班（越权）。
    """
    from app.analysis.identity import identity_of
    from app.db.models import ClassRoster, StudentAlias
    from app.rollover.service import _validate_official_sid

    grade, class_num = _managed_scope(db)
    sid = _require_in_scope(db, student_id, grade, class_num)
    new_sid = str(new_student_id or "").strip()
    if not new_sid:
        raise ValueError("新学号不能为空")
    if new_sid == sid:
        raise ValueError("新学号与当前学号相同")

    roster = _scope_roster_row(db, sid, grade, class_num)
    name, _ = _canonical_name(db, sid)

    existing_roster = (
        db.query(ClassRoster).filter(ClassRoster.student_id == new_sid).first()
    )
    if existing_roster is not None:
        raise ValueError(f"学号 {new_sid} 已被花名册中的「{existing_roster.name}」占用")

    iid = identity_of(db, sid)
    existing_alias = (
        db.query(StudentAlias).filter(StudentAlias.student_id == new_sid).first()
    )
    if existing_alias is not None and (iid is None or existing_alias.identity_id != iid):
        raise ValueError(f"学号 {new_sid} 已关联其他跨学年身份，不能纠正为本生学号")

    overlap = _exam_overlap(db, sid, new_sid)
    if overlap:
        names = "、".join(item["exam_name"] for item in overlap)
        raise ValueError(
            f"学号 {new_sid} 与当前学号在同一场考试（{names}）都有成绩，"
            "纠正会造成同场考试数据冲突，请先核对成绩归属"
        )

    try:
        # 跨届学号守门：新学号若与其他年级的裸学号撞车，先把撞车旧届整体
        # G{g}:: 前缀化让位，再过学号校验、迁移引用——守门、校验与下方写
        # 操作同一事务，任一步失败整体回滚（自动备份与删除/合并口径一致）。
        from app.db.sid_space import ensure_sid_space

        ensure_sid_space(db, {new_sid}, grade, commit=False, auto_backup=True)
        _validate_official_sid(db, new_sid, name, grade, class_num, expect_iid=iid)
        # 花名册主键换号必须桥接（homework_record/special_record 的外键指向
        # class_roster.student_id，直改父键或先迁子行都会被逐语句外键校验
        # 拒绝）：先以新号插入全字段拷贝 → 迁移全部运行时引用（新旧父行
        # 并存，子行双向合法）→ 最后删旧号行。无花名册行时（仅成绩的学生）
        # 不存在外键子行，直接迁移。
        if roster is not None:
            from app.db.sid_space import rekey_roster_row

            moved = rekey_roster_row(
                db,
                sid,
                new_sid,
                ref_mover=lambda: _migrate_refs(db, sid, new_sid),
            )
        else:
            moved = _migrate_refs(db, sid, new_sid)
        # 身份别名随迁：新学号已有本生 alias 则删旧 alias（目标侧保留），
        # 否则把旧 alias 原位改成新学号
        if existing_alias is not None:
            db.query(StudentAlias).filter(StudentAlias.student_id == sid).delete(
                synchronize_session=False
            )
        elif iid is not None:
            db.query(StudentAlias).filter(StudentAlias.student_id == sid).update(
                {StudentAlias.student_id: new_sid}, synchronize_session=False
            )
        # 花名册行已在上方桥接中换号（新行已插入、旧行已删），此处不再改键
        _log_change(
            db,
            "correct_sid",
            identity_id=iid,
            student_id=new_sid,
            before={"student_id": sid, "name": name},
            after={"student_id": new_sid, "name": name},
            detail={"moved": moved, "grade": grade, "class_num": class_num},
            grade=grade,
            class_num=class_num,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"old_student_id": sid, "new_student_id": new_sid, "moved": moved}


def add_new_year_student_id(db, student_id: str, new_student_id: str, grade: int, class_num: int) -> dict:
    """新增学年学号：同一身份新增 alias + 目标学年花名册行，旧学号与历史
    数据原样保留。目标 grade+class 必须与教师绑定一致；学生本人无主档时
    先幂等补建（不合并同名）。

    目标班已存在同号花名册行时绝不提前返回成功：同一身份才幂等；无 alias
    的既有行只有在教师显式输入、规范姓名一致且全部冲突检查通过时才挂到
    当前身份；不同姓名或已挂其他身份一律拒绝。"""
    from app.analysis.identity import ensure_identity, identity_of, link_aliases
    from app.db.models import ClassRoster, StudentAlias
    from app.rollover.service import _teacher_target_class, _validate_official_sid

    if grade not in (1, 2, 3):
        raise ValueError("年级必须是 1、2 或 3")
    bound = _teacher_target_class(db, grade)
    if bound is None:
        raise ScopeError(f"高{grade}尚未绑定行政班")
    if class_num is None or int(class_num) != int(bound):
        raise ScopeError(f"目标班级（{class_num}）与教师绑定的高{grade}班级（{bound}）不一致")

    current_grade, current_class = _managed_scope(db)
    sid = _require_in_scope(db, student_id, current_grade, current_class)
    new_sid = str(new_student_id or "").strip()
    if not new_sid:
        raise ValueError("新学号不能为空")
    if new_sid == sid:
        raise ValueError("新学号与当前学号相同")
    if grade == current_grade and class_num == current_class:
        raise ValueError(
            "目标学年班级与当前班级相同；纠正录错学号请使用「纠正录错学号」"
        )

    name, _ = _canonical_name(db, sid)
    iid = identity_of(db, sid)

    existing_roster = (
        db.query(ClassRoster).filter(ClassRoster.student_id == new_sid).first()
    )
    if existing_roster is not None and not (
        existing_roster.grade == grade and existing_roster.class_num == class_num
    ):
        raise ValueError(f"学号 {new_sid} 已被花名册中的「{existing_roster.name}」占用")

    existing_alias = (
        db.query(StudentAlias).filter(StudentAlias.student_id == new_sid).first()
    )
    if existing_alias is not None and (iid is None or existing_alias.identity_id != iid):
        raise ValueError(f"学号 {new_sid} 已关联其他跨学年身份")

    if existing_roster is not None and _norm_name(existing_roster.name or "") != _norm_name(name):
        raise ValueError(
            f"学号 {new_sid} 在目标班级已属于「{existing_roster.name}」，"
            f"与本学生「{name}」不一致，已拒绝"
        )

    try:
        # 跨届学号守门：目标学年新学号若与其他年级的裸学号撞车，先把撞车
        # 旧届整体 G{g}:: 前缀化让位，本届再写裸号；守门、校验与下方写
        # 操作同一事务，任一步失败整体回滚。
        from app.db.sid_space import ensure_sid_space

        ensure_sid_space(db, {new_sid}, grade, commit=False, auto_backup=True)
        _validate_official_sid(db, new_sid, name, grade, class_num, expect_iid=iid)
        if iid is None:
            iid = ensure_identity(db, display_name=name, commit=False)
            link_aliases(db, iid, [(sid, current_grade)], "manual", commit=False)
        created_roster = False
        if existing_roster is None:
            from app.db.models import StudentIdentity

            ident = (
                db.query(StudentIdentity).filter(StudentIdentity.id == iid).first()
            )
            gender = ident.gender if ident is not None else None
            roster_gender = (
                db.query(ClassRoster.gender)
                .filter(ClassRoster.student_id == sid, ClassRoster.gender.isnot(None))
                .first()
            )
            db.add(
                ClassRoster(
                    student_id=new_sid,
                    name=name,
                    class_num=class_num,
                    grade=grade,
                    seat_no=None,
                    gender=gender or (roster_gender[0] if roster_gender else None),
                    excluded=0,
                    status=None,
                )
            )
            created_roster = True
        if existing_alias is None:
            link_aliases(db, iid, [(new_sid, grade)], "manual", commit=False)
        _log_change(
            db,
            "new_year_sid",
            identity_id=iid,
            student_id=sid,
            before={"student_id": sid},
            after={"added_student_id": new_sid, "grade": grade, "class_num": class_num},
            detail={"created_roster": created_roster, "name": name},
            grade=current_grade,
            class_num=current_class,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "student_id": new_sid,
        "identity_id": iid,
        "created": created_roster or existing_alias is None,
    }


# ─────────────────────────── 删除 ───────────────────────────


def delete_preview(db, student_id: str) -> dict:
    """删除影响预览：各业务表关联计数 + 身份/历史档案影响。"""
    from app.analysis.identity import aliases_of, identity_of
    from app.db.models import ImportedHistory

    grade, class_num = _managed_scope(db)
    sid = _require_in_scope(db, student_id, grade, class_num)
    iid = identity_of(db, sid)
    counts = _ref_counts(db, sid)
    aliases = []
    if iid is not None:
        aliases = [a.student_id for a in aliases_of(db, iid)]
    imported_history = (
        db.query(ImportedHistory).filter(ImportedHistory.identity_id == iid).count()
        if iid is not None
        else 0
    )
    return {
        "student_id": sid,
        "identity_id": iid,
        "counts": counts,
        "total_refs": sum(counts.values()),
        "aliases": aliases,
        "other_aliases_kept": [a for a in aliases if a != sid],
        "imported_history_kept": imported_history,
        # 除业务表数据外，已有跨学年关联（其他学号别名 / 导入历史）的删除
        # 会切断主档连续性，同样需要确认；单人单号的新建档案不在此列
        "requires_confirm": (
            sum(counts.values()) > 0
            or bool([a for a in aliases if a != sid])
            or imported_history > 0
        ),
    }


def delete_student(db, student_id: str, confirm: bool = False) -> dict:
    """删除学生。

    - 干净的误建学生（无业务引用、无其他学号别名、无导入历史）：无需
      confirm 即可彻底删除（与 delete-preview 的 requires_confirm=False
      契约一致）；
    - 存在上述任一风险时必须 confirm=True；有业务数据时删除前自动调用
      现有备份能力打包快照，随后单一事务删除该学号的全部引用
      （SubjectScore/TotalScore/HomeworkRecord/SpecialRecord/StudentNote/
      花名册行/alias）。身份还有其他学号或导入历史则保留主档（只删本
      学号），否则连主档一起删。
    - 整笔事务失败回滚（备份文件保留，可手工恢复）。
    """
    from app.analysis.identity import aliases_of, identity_of
    from app.db.models import (
        ClassRoster,
        HomeworkRecord,
        ImportedHistory,
        SpecialRecord,
        StudentAlias,
        StudentIdentity,
        StudentNote,
        SubjectScore,
        TotalScore,
    )

    grade, class_num = _managed_scope(db)
    sid = _require_in_scope(db, student_id, grade, class_num)
    roster = _scope_roster_row(db, sid, grade, class_num)
    iid = identity_of(db, sid)
    counts = _ref_counts(db, sid)
    total_refs = sum(counts.values())
    other_aliases = (
        [a.student_id for a in aliases_of(db, iid) if a.student_id != sid]
        if iid is not None
        else []
    )
    imported_history = (
        db.query(ImportedHistory).filter(ImportedHistory.identity_id == iid).count()
        if iid is not None
        else 0
    )
    requires_confirm = total_refs > 0 or bool(other_aliases) or imported_history > 0

    if not confirm and requires_confirm:
        raise ConfirmRequired(
            {
                "student_id": sid,
                "counts": counts,
                "total_refs": total_refs,
                "other_aliases": other_aliases,
                "imported_history": imported_history,
                "requires_confirm": True,
                "message": (
                    "该学生存在关联数据，删除前请确认影响并会自动备份"
                    if total_refs
                    else "该学生存在跨学年主档关联，删除前请确认影响"
                ),
            }
        )

    backup_file = None
    if total_refs > 0:
        from app.backup.router import create_backup

        backup_file = create_backup(prefix="before-student-delete")

    name, _ = _canonical_name(db, sid)
    try:
        for model in (SubjectScore, TotalScore, HomeworkRecord, SpecialRecord, StudentNote):
            db.query(model).filter(model.student_id == sid).delete(synchronize_session=False)
        if iid is not None:
            db.query(StudentAlias).filter(StudentAlias.student_id == sid).delete(
                synchronize_session=False
            )
        if roster is not None:
            db.delete(roster)
        # 主档清理：身份再无学号且无隔离历史档案时一并删除
        identity_deleted = False
        if iid is not None:
            left = db.query(StudentAlias).filter(StudentAlias.identity_id == iid).count()
            history = (
                db.query(ImportedHistory)
                .filter(ImportedHistory.identity_id == iid)
                .count()
            )
            if left == 0 and history == 0:
                db.query(StudentIdentity).filter(StudentIdentity.id == iid).delete(
                    synchronize_session=False
                )
                identity_deleted = True
        _log_change(
            db,
            "delete",
            identity_id=None if identity_deleted else iid,
            student_id=sid,
            before={"student_id": sid, "name": name},
            detail={
                "counts": counts,
                "backup_file": backup_file,
                "identity_deleted": identity_deleted,
                "grade": grade,
                "class_num": class_num,
            },
            grade=grade,
            class_num=class_num,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "student_id": sid,
        "deleted": True,
        "counts": counts,
        "backup_file": backup_file,
        "identity_deleted": identity_deleted,
    }


# ─────────────────────────── 归档（转班 / 毕业） ───────────────────────────


def archive_student(db, student_id: str, status: str) -> dict:
    """设置在班状态：transferred=转班离班、graduated=毕业、active=恢复在班。

    归档只改花名册行状态，绝不删除任何成绩/作业/档案数据；归档学生从管理
    列表默认视图移出，历史数据完整保留。"""
    from app.db.models import ClassRoster

    grade, class_num = _managed_scope(db)
    sid = _require_in_scope(db, student_id, grade, class_num)
    roster = _scope_roster_row(db, sid, grade, class_num)
    if roster is None:
        raise NotFoundError("该学生不在当前班级花名册中，无法归档")
    status = str(status or "").strip()
    if status not in ("transferred", "graduated", "active"):
        raise ValueError("状态只能是 transferred / graduated / active")

    before = {"status": roster.status or "active"}
    roster.status = None if status == "active" else status
    _log_change(
        db,
        "restore" if status == "active" else "archive",
        identity_id=None,
        student_id=sid,
        before=before,
        after={"status": roster.status or "active"},
        detail={"grade": grade, "class_num": class_num},
        grade=grade,
        class_num=class_num,
    )
    db.commit()
    return {"student_id": sid, "status": roster.status or "active"}


# ─────────────────────────── 重复学生合并 ───────────────────────────


def merge_preview(db, primary_student_id: str, duplicate_student_id: str) -> dict:
    """合并预览：双方信息、将迁入的引用计数、同场考试冲突清单。"""
    grade, class_num = _managed_scope(db)
    primary = _require_in_scope(db, primary_student_id, grade, class_num)
    duplicate = _require_in_scope(db, duplicate_student_id, grade, class_num)
    if primary == duplicate:
        raise ValueError("不能把学生与自身合并")

    conflicts = _exam_overlap(db, primary, duplicate)
    return {
        "primary": {
            "student_id": primary,
            "name": _canonical_name(db, primary)[0],
            "counts": _ref_counts(db, primary),
            "identity_id": None,  # 详情接口可查，预览不重复解析
        },
        "duplicate": {
            "student_id": duplicate,
            "name": _canonical_name(db, duplicate)[0],
            "counts": _ref_counts(db, duplicate),
        },
        "conflicts": conflicts,
        "mergeable": not conflicts,
        "message": (
            "两个学号在同一场考试都有成绩，无法自动合并；请先用「纠正学号」核对归属"
            if conflicts
            else "合并后重复学号的成绩/作业/档案将并入主学号并保留为其历史学号"
        ),
    }


def merge_students(db, primary_student_id: str, duplicate_student_id: str, confirm: bool = False) -> dict:
    """事务性合并：duplicate 学号的全部引用并入 primary 学号。

    - 同场考试冲突（双方都有成绩）→ MergeConflict 拒绝，绝不自动裁决；
    - 有数据时需 confirm=True，落库前自动备份；
    - 身份：双方 identity 合并到 primary 侧（alias / ImportedHistory 随迁，
      重复 identity 删除）；duplicate 学号保留为合并后身份的历史学号；
    - 花名册：primary 无花名册行时把 duplicate 行改为 primary 学号，否则
      删除 duplicate 行（座号/性别空缺时从 duplicate 行补齐）。
    """
    from app.analysis.identity import ensure_identity, identity_of
    from app.db.models import (
        ClassRoster,
        ImportedHistory,
        StudentAlias,
        StudentIdentity,
    )

    grade, class_num = _managed_scope(db)
    primary = _require_in_scope(db, primary_student_id, grade, class_num)
    duplicate = _require_in_scope(db, duplicate_student_id, grade, class_num)
    if primary == duplicate:
        raise ValueError("不能把学生与自身合并")

    conflicts = _exam_overlap(db, primary, duplicate)
    if conflicts:
        raise MergeConflict(
            {
                "conflicts": conflicts,
                "message": "两个学号在同一场考试都有成绩，无法自动合并",
            }
        )

    dup_counts = _ref_counts(db, duplicate)
    total_moved = sum(dup_counts.values())
    if not confirm:
        raise ConfirmRequired(
            {
                "primary_student_id": primary,
                "duplicate_student_id": duplicate,
                "moved": dup_counts,
                "total_refs": total_moved,
                "requires_confirm": True,
                "message": "合并前请确认影响并会自动备份",
            }
        )

    backup_file = None
    if total_moved > 0:
        from app.backup.router import create_backup

        backup_file = create_backup(prefix="before-student-merge")

    primary_name, _ = _canonical_name(db, primary)
    dup_name, _ = _canonical_name(db, duplicate)
    primary_roster = _scope_roster_row(db, primary, grade, class_num)
    dup_roster = _scope_roster_row(db, duplicate, grade, class_num)
    # dup_roster 的字段在桥接删行前先取好（对象删除并 flush 后属性不可访问）
    dup_roster_grade = dup_roster.grade if dup_roster is not None else None

    try:
        # ── 花名册合并（必须伴随引用迁移先行）──
        # primary 侧没有花名册行时，duplicate 行整体过户为 primary 学号：
        # homework/special 的外键指向 class_roster，主键换号必须走桥接
        # （先建新号行 → 迁子行 → 删旧号行），全部运行时引用随 ref_mover
        # 一并迁移。primary 侧已有行时子行直接迁往已存在的父行，duplicate
        # 行在最后补齐字段后删除（届时已无子行引用它）。
        if dup_roster is not None and primary_roster is None:
            from app.db.sid_space import rekey_roster_row

            moved = rekey_roster_row(
                db,
                duplicate,
                primary,
                ref_mover=lambda: _migrate_refs(db, duplicate, primary),
            )
        else:
            moved = _migrate_refs(db, duplicate, primary)

        # ── 身份合并 ──
        iid_p = identity_of(db, primary)
        iid_d = identity_of(db, duplicate)
        if iid_p is None and iid_d is None:
            # 双方都无主档：合并本身即教师确认「同一人」，以主学号规范姓名
            # 建主档，两个学号都保留为该身份的 alias（重复学号即历史学号，
            # 绝不丢号导致日后无法按人合并）
            iid_p = ensure_identity(db, display_name=primary_name, commit=False)
            db.add(
                StudentAlias(
                    identity_id=iid_p,
                    student_id=primary,
                    grade=primary_roster.grade if primary_roster is not None else grade,
                    link_source="manual",
                )
            )
            db.flush()
        else:
            if iid_p is None:
                # 保留 duplicate 侧身份为主档，但换用 primary 学号代表的规范名
                ident_d = (
                    db.query(StudentIdentity)
                    .filter(StudentIdentity.id == iid_d)
                    .first()
                )
                if ident_d is not None and not ident_d.display_name:
                    ident_d.display_name = primary_name
                iid_p = iid_d
                # primary 学号若无 alias 则挂到该身份
                if identity_of(db, primary) is None:
                    db.add(
                        StudentAlias(
                            identity_id=iid_p,
                            student_id=primary,
                            grade=grade,
                            link_source="manual",
                        )
                    )
                    db.flush()
            elif iid_d is not None and iid_d != iid_p:
                db.query(StudentAlias).filter(StudentAlias.identity_id == iid_d).update(
                    {StudentAlias.identity_id: iid_p}, synchronize_session=False
                )
                db.query(ImportedHistory).filter(ImportedHistory.identity_id == iid_d).update(
                    {ImportedHistory.identity_id: iid_p}, synchronize_session=False
                )
                db.query(StudentIdentity).filter(StudentIdentity.id == iid_d).delete(
                    synchronize_session=False
                )

        # duplicate 学号保留为历史学号：确保其 alias 指向合并后身份
        dup_alias = (
            db.query(StudentAlias)
            .filter(StudentAlias.student_id == duplicate)
            .first()
        )
        if dup_alias is None:
            dup_grade = dup_roster_grade if dup_roster_grade is not None else grade
            db.add(
                StudentAlias(
                    identity_id=iid_p,
                    student_id=duplicate,
                    grade=dup_grade,
                    link_source="manual",
                )
            )
        elif dup_alias.identity_id != iid_p:
            dup_alias.identity_id = iid_p

        # ── 花名册合并 ──
        # primary 无行的分支已在事务开头桥接过户（dup_roster 已删），此处
        # 只处理双方都有行的分支：补齐座号/性别后删除 duplicate 行——引用
        # 已全部迁往 primary，删除不会留下外键孤儿。
        if dup_roster is not None and primary_roster is not None:
            if primary_roster.seat_no is None:
                primary_roster.seat_no = dup_roster.seat_no
            if not primary_roster.gender:
                primary_roster.gender = dup_roster.gender
            db.delete(dup_roster)

        _log_change(
            db,
            "merge",
            identity_id=iid_p,
            student_id=primary,
            before={"duplicate_student_id": duplicate, "duplicate_name": dup_name},
            after={"primary_student_id": primary, "primary_name": primary_name},
            detail={
                "moved": moved,
                "backup_file": backup_file,
                "grade": grade,
                "class_num": class_num,
            },
            grade=grade,
            class_num=class_num,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "primary_student_id": primary,
        "duplicate_student_id": duplicate,
        "moved": moved,
        "identity_id": iid_p,
        "backup_file": backup_file,
    }


# ─────────────────────────── 身份回填 ───────────────────────────


def _scope_students_without_identity(db, grade: int, class_num: int) -> list[tuple[str, str]]:
    """当前作用域内没有主档的学生 [(sid, 候选规范名)]。"""
    from app.db.models import StudentAlias

    sids = _scope_student_ids(db, grade, class_num, include_archived=True)
    linked = {
        row[0]
        for row in db.query(StudentAlias.student_id)
        .filter(StudentAlias.student_id.in_(sids))
        .all()
    } if sids else set()
    return [
        (sid, _canonical_name(db, sid)[0])
        for sid in sids
        if sid not in linked
    ]


def backfill_preview(db) -> dict:
    """回填预览：当前班还没有主档的学生数（幂等，可重复执行）。"""
    grade, class_num = _managed_scope(db)
    pending = _scope_students_without_identity(db, grade, class_num)
    return {
        "grade": grade,
        "class_num": class_num,
        "pending": [{"student_id": sid, "name": name} for sid, name in pending],
        "count": len(pending),
    }


def backfill_identities(db) -> dict:
    """为当前班没有主档的学生逐人建 identity + alias（source=manual）。

    幂等：已有主档的跳过；同名学生各建独立主档，绝不按姓名合并。"""
    from app.analysis.identity import ensure_identity, link_aliases

    grade, class_num = _managed_scope(db)
    pending = _scope_students_without_identity(db, grade, class_num)
    if not pending:
        return {"created": 0, "skipped": 0, "total": 0}

    created, identity_ids = 0, []
    try:
        for sid, name in pending:
            iid = ensure_identity(db, display_name=name, commit=False)
            link_aliases(db, iid, [(sid, grade)], "manual", commit=False)
            identity_ids.append(iid)
            created += 1
        _log_change(
            db,
            "backfill",
            identity_id=None,
            student_id=None,
            detail={
                "created": created,
                "grade": grade,
                "class_num": class_num,
                "students": [{"student_id": sid, "name": name} for sid, name in pending],
            },
            grade=grade,
            class_num=class_num,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"created": created, "skipped": 0, "total": created, "identity_ids": identity_ids}


# ─────────────────────────── 变更日志 ───────────────────────────


def list_change_log(db, student_id=None, limit: int = 50) -> list[dict]:
    """变更日志（倒序），只返回教师当前绑定 grade+class_num 的留痕——
    其他班级的操作记录绝不外泄；带 student_id 过滤时同样受作用域约束
    （他班学号查不到任何本班视图的日志）。"""
    from app.db.models import StudentChangeLog

    grade, class_num = _managed_scope(db)
    limit = max(1, min(int(limit), 200))
    query = db.query(StudentChangeLog).filter(
        StudentChangeLog.grade == grade,
        StudentChangeLog.class_num == class_num,
    )
    if student_id:
        query = query.filter(StudentChangeLog.student_id == str(student_id))
    rows = query.order_by(StudentChangeLog.created_at.desc(), StudentChangeLog.id.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "op_type": r.op_type,
            "identity_id": r.identity_id,
            "student_id": r.student_id,
            "before_summary": r.before_summary,
            "after_summary": r.after_summary,
            "detail": r.detail,
            "grade": r.grade,
            "class_num": r.class_num,
            "created_at": r.created_at.isoformat(timespec="seconds") if r.created_at else None,
        }
        for r in rows
    ]
