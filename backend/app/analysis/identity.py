"""跨学年身份解析（人 ↔ 学号）。

StudentIdentity 是「同一个人」的唯一聚合根，StudentAlias 把每学年的真实
学号挂回 identity（一人多号，grade 区分学年）。本模块是 identity 子系统
对外的唯一契约，rollover / chat / router 等都从这层取数据。

叠加层（zero-config / zero-regression）：
  - 学号上无 alias 时，该学号视为「独立一个人」，identity_of 返回 None。
  - person_ids 无 alias 时退化为 {student_id}，与换届前的行为完全一致。
  - 链接只能来自教师确认（name_confirmed）、名册导入（crosswalk）、
    手工（manual）或 ext_key——绝不按姓名自动合并（同名必须人工消歧）。
"""

from typing import Optional

# student_id 在本模块统一按 str 处理（与 SubjectScore.student_id 同口径）。


def identity_of(db, student_id) -> Optional[int]:
    """学号 -> identity_id；无 alias 则 None（视为独立人）。"""
    from app.db.models import StudentAlias

    if student_id is None:
        return None
    row = (
        db.query(StudentAlias.identity_id)
        .filter(StudentAlias.student_id == str(student_id))
        .first()
    )
    return row[0] if row else None


def person_ids(db, student_id) -> set:
    """同一人全部学段学号；无 alias 时返回 {student_id}。"""
    from app.db.models import StudentAlias

    if student_id is None:
        return set()
    sid = str(student_id)
    iid = identity_of(db, sid)
    if iid is None:
        return {sid}
    rows = (
        db.query(StudentAlias.student_id)
        .filter(StudentAlias.identity_id == iid)
        .all()
    )
    return {r[0] for r in rows} if rows else {sid}


def aliases_of(db, identity_id) -> list:
    """某 identity 的全部 StudentAlias，按 grade 升序。"""
    from app.db.models import StudentAlias

    if identity_id is None:
        return []
    rows = (
        db.query(StudentAlias)
        .filter(StudentAlias.identity_id == identity_id)
        .order_by(StudentAlias.grade.asc())
        .all()
    )
    return rows


def display_names(db, student_ids) -> dict:
    """批量解析学号 -> identity.display_name（主档规范姓名）。

    只返回已链接 identity 且主档有规范名的学号；其余学号不出现（调用方
    回退成绩/花名册姓名）。展示侧优先用它覆盖 SubjectScore.name 的原始
    录入快照，快照本身绝不改写。"""
    from app.db.models import StudentAlias, StudentIdentity

    ids = [str(s) for s in (student_ids or []) if s is not None]
    if not ids:
        return {}
    rows = (
        db.query(StudentAlias.student_id, StudentIdentity.display_name)
        .join(StudentIdentity, StudentIdentity.id == StudentAlias.identity_id)
        .filter(
            StudentAlias.student_id.in_(ids),
            StudentIdentity.display_name.isnot(None),
        )
        .all()
    )
    return {sid: name for sid, name in rows if name}


def ensure_identity(db, *, display_name=None, gender=None, ext_key=None, commit=True) -> int:
    """新建 StudentIdentity 并 commit，返回 id。

    commit=False 时只 flush 不提交，供调用方在同一事务内组合多个写操作
    （如换届同名批量确认）后统一 commit / rollback。
    """
    from app.db.models import StudentIdentity

    ident = StudentIdentity(
        display_name=display_name,
        gender=gender,
        ext_key=ext_key,
    )
    db.add(ident)
    if commit:
        db.commit()
    else:
        db.flush()
    return ident.id


def link_aliases(db, identity_id, items, source, commit=True) -> dict:
    """把多个学号挂到同一 identity。

    items=[(student_id, grade), ...]；source in
    {name_confirmed, crosswalk, manual, ext_key}；commit=False 时只入事务
    不提交，供调用方组合多个写后统一 commit / rollback。

    对每个 student_id：
      - 已存在 alias 指向*其他* identity -> 记 conflict（不覆盖）
      - 指向本 identity -> skip（幂等）
      - 无 alias -> 新建 StudentAlias
    """
    from app.db.models import StudentAlias

    linked, conflicts, skipped = [], [], []
    for student_id, grade in items:
        if student_id is None:
            continue
        sid = str(student_id)
        existing = (
            db.query(StudentAlias)
            .filter(StudentAlias.student_id == sid)
            .first()
        )
        if existing is not None:
            if existing.identity_id == identity_id:
                skipped.append(sid)
            else:
                conflicts.append(
                    {
                        "student_id": sid,
                        "grade": grade,
                        "conflict_identity_id": existing.identity_id,
                    }
                )
            continue
        db.add(
            StudentAlias(
                identity_id=identity_id,
                student_id=sid,
                grade=grade,
                link_source=source,
            )
        )
        linked.append(sid)
    if commit:
        db.commit()
    return {"linked": linked, "conflicts": conflicts, "skipped": skipped}


def unlink_alias(db, student_id) -> dict:
    """删该 student_id 的 alias（学号还原为独立人）。

    identity 若无 alias 残留则保留（历史档案仍挂在 identity 上）。
    """
    from app.db.models import StudentAlias

    if student_id is None:
        return {"error": "无此关联"}
    sid = str(student_id)
    row = (
        db.query(StudentAlias)
        .filter(StudentAlias.student_id == sid)
        .first()
    )
    if row is None:
        return {"error": "无此关联"}
    db.delete(row)
    db.commit()
    return {"unlinked": sid}


def name_candidates(db, name, target_grade=1) -> list:
    """按姓名在 target_grade 成绩里找候选人（供同名消歧）。

    实现：SubjectScore JOIN Exam，filter Exam.grade==target_grade 且
    SubjectScore.name==name，distinct student_id。绝不去重同名——全部返回。
    每个候选人附最新一场考试的主三门成绩/排名，并标注是否已链接 identity。
    """
    from app.db.models import SubjectScore, Exam, TotalScore

    if not name:
        return []

    # 候选学号（distinct student_id，命中 target_grade 的同名记录）
    sid_rows = (
        db.query(SubjectScore.student_id)
        .join(Exam, Exam.id == SubjectScore.exam_id)
        .filter(Exam.grade == target_grade, SubjectScore.name == name)
        .distinct()
        .all()
    )
    sids = [r[0] for r in sid_rows]
    if not sids:
        return []

    # 班级：该学号在该 grade 的 subject_score.class_num 首个非空
    class_num_map = {}
    for sid in sids:
        cn = (
            db.query(SubjectScore.class_num)
            .join(Exam, Exam.id == SubjectScore.exam_id)
            .filter(
                Exam.grade == target_grade,
                SubjectScore.student_id == sid,
                SubjectScore.class_num.isnot(None),
            )
            .first()
        )
        class_num_map[sid] = cn[0] if cn else None

    results = []
    for sid in sids:
        # 该学号 target_grade 下最近一场考试（按 exam_date desc）
        latest_exam = (
            db.query(Exam)
            .join(SubjectScore, SubjectScore.exam_id == Exam.id)
            .filter(Exam.grade == target_grade, SubjectScore.student_id == sid)
            .order_by(Exam.exam_date.desc().nullslast(), Exam.id.desc())
            .first()
        )
        latest_exam_name = latest_exam.name if latest_exam else None
        latest_main_score = None
        latest_main_rank = None
        if latest_exam is not None:
            ts = (
                db.query(TotalScore)
                .filter(
                    TotalScore.exam_id == latest_exam.id,
                    TotalScore.student_id == sid,
                    TotalScore.total_type == "主三门",
                )
                .first()
            )
            if ts is not None:
                latest_main_score = ts.total_score
                latest_main_rank = (
                    ts.xueji_rank
                    if ts.xueji_rank is not None
                    else ts.grade_percentile
                )

        results.append(
            {
                "student_id": sid,
                "name": name,
                "class_num": class_num_map.get(sid),
                "latest_exam_name": latest_exam_name,
                "latest_main_score": latest_main_score,
                "latest_main_rank": latest_main_rank,
                "already_linked": identity_of(db, sid) is not None,
            }
        )

    return results


def import_crosswalk(db, rows, target_grade: int = 2) -> dict:
    """按名册（crosswalk）把每行的 g1_sid 与 g2_sid 链为同一人。

    rows=[{g1_sid, g2_sid, name?}]。source=crosswalk。
      - 两个都无 alias -> 新建 identity，链两个
      - 一个有 alias -> 把另一个 link 到该 identity
      - 两个指向同一 identity -> skipped
      - 两个指向不同 identity -> conflict（不合并）
    """
    if target_grade not in (2, 3):
        raise ValueError("目标年级必须为高二或高三")
    previous_grade = target_grade - 1
    linked = 0
    conflict = 0
    skipped = 0

    for row in rows:
        g1 = row.get("g1_sid")
        g2 = row.get("g2_sid")
        if g1 is None or g2 is None:
            skipped += 1
            continue
        g1 = str(g1)
        g2 = str(g2)
        iid1 = identity_of(db, g1)
        iid2 = identity_of(db, g2)

        name = row.get("name")
        if iid1 is None and iid2 is None:
            iid = ensure_identity(db, display_name=name)
            link_aliases(
                db,
                iid,
                [(g1, previous_grade), (g2, target_grade)],
                "crosswalk",
            )
            linked += 1
        elif iid1 is not None and iid2 is not None:
            if iid1 == iid2:
                skipped += 1
            else:
                conflict += 1
        elif iid1 is not None:
            link_aliases(db, iid1, [(g2, target_grade)], "crosswalk")
            linked += 1
        else:  # iid2 is not None
            link_aliases(db, iid2, [(g1, previous_grade)], "crosswalk")
            linked += 1

    return {"linked": linked, "conflict": conflict, "skipped": skipped}
