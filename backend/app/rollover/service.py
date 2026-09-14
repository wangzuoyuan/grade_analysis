"""升级换届服务：四态分类 + 名册结转 + 高一成绩导入 + 同名批量确认。

换届把班主任从某学年带到下一学年：根据跨学年身份（StudentAlias）与上一
年级成绩同名记录，把新班级里每个学生判为 inherited（已链身份）/ ambiguous
（同名需消歧）/ new（新人）/ unmatched（仅花名册无成绩），并报告 left_class
（上学年本班未在新班出现的学生）。同时支持按成绩或手填名单结转作业花名册
（ClassRoster，grade=target_grade），以及把班主任手头的历史分数隔离地写
入 ImportedHistory（不参与任何聚合）。

同名批量确认（confirm_batch）：先完整预检（姓名规范化一致、唯一候选、
候选未被占用、教师绑定目标班、目标班 roster/成绩归属、批内 g1/g2 唯一），
全部通过后在同一事务落库并记录批次快照；任一项不通过整批拒绝回滚，绝不
部分成功。撤销（undo_confirm_batch）只删本批实际新建的 alias/identity。

身份层完全复用 app.analysis.identity，绝不在本模块按姓名自动合并。
"""

import uuid
from typing import Optional


class RosterScopeError(ValueError):
    """目标年级/班级与教师绑定不一致（HTTP 409，区别于行级校验错误）。"""


# 仅姓名导入的临时学号前缀：可识别、稳定、按目标班隔离，绝不与真实学号同形。
TEMP_SID_PREFIX = "TMP-"


def temp_sid(grade, class_num, name) -> str:
    """为「只有姓名、还没有学号」的学生生成临时学号。

    形如 TMP-2-6-张三：同一目标班内同名重复导入得到同一学号（幂等），
    不同班/年级的同名学生各自独立，不会跨班冲突；拿到正式学号后可整体替换。
    """
    return f"{TEMP_SID_PREFIX}{grade}-{class_num}-{name}"


def _teacher_target_class(db, grade) -> Optional[int]:
    """读 Teacher.target_class_high{grade}（grade 1/2/3）。未绑定返回 None。"""
    from app.db.models import Teacher

    t = db.query(Teacher).first()
    if t is None:
        return None
    attr = f"target_class_high{grade}"
    return getattr(t, attr, None)


def _class_students(db, grade, class_num) -> list:
    """某年级某班全体 distinct student_id（按 SubjectScore 派生）。"""
    from app.db.models import SubjectScore, Exam

    rows = (
        db.query(SubjectScore.student_id)
        .join(Exam, Exam.id == SubjectScore.exam_id)
        .filter(Exam.grade == grade, SubjectScore.class_num == class_num)
        .distinct()
        .all()
    )
    return [r[0] for r in rows]


def _student_name_in_grade(db, student_id, grade) -> Optional[str]:
    """该学号在该 grade 的 SubjectScore.name 首个非空。"""
    from app.db.models import SubjectScore, Exam

    row = (
        db.query(SubjectScore.name)
        .join(Exam, Exam.id == SubjectScore.exam_id)
        .filter(
            Exam.grade == grade,
            SubjectScore.student_id == student_id,
            SubjectScore.name.isnot(None),
        )
        .first()
    )
    return row[0] if row else None


def _class_num_in_grade(db, student_id, grade) -> Optional[int]:
    """该学号在该 grade 的 SubjectScore.class_num 首个非空。"""
    from app.db.models import SubjectScore, Exam

    row = (
        db.query(SubjectScore.class_num)
        .join(Exam, Exam.id == SubjectScore.exam_id)
        .filter(
            Exam.grade == grade,
            SubjectScore.student_id == student_id,
            SubjectScore.class_num.isnot(None),
        )
        .first()
    )
    return row[0] if row else None


def classify(db, target_grade, class_num) -> dict:
    """对 target_grade 目标班 class_num 每个学生判定四态 + left_class。

    学生集合 = distinct SubjectScore.student_id JOIN Exam.grade==target_grade
    AND class_num==class_num，并入 class_roster(grade==target_grade) 的
    student_id。对每个学生：
      - identity_of 非空 -> inherited（附上学年 alias 学号 + 其班级）
      - 否则 name_candidates(name, target_grade-1) 非空 -> ambiguous（附候选）
      - 否则该 sid 在 target_grade 成绩库有数据 -> new
      - 否则（仅 roster 无成绩）-> unmatched

    left_class：上学年（prev=target_grade-1）本班（Teacher.target_class_high{prev}）
    全体学号中，不在本班集合的 -> 离班学生（附 name + g1 class_num）。该年级
    未绑定班则 []。
    """
    from app.db.models import SubjectScore, Exam, ClassRoster
    from app.analysis.identity import (
        identity_of,
        aliases_of,
        name_candidates,
    )

    # 目标班学生集合（成绩库 distinct sid）
    score_sids = (
        db.query(SubjectScore.student_id)
        .join(Exam, Exam.id == SubjectScore.exam_id)
        .filter(Exam.grade == target_grade, SubjectScore.class_num == class_num)
        .distinct()
        .all()
    )
    sid_set = {r[0] for r in score_sids}

    # 并入 roster（grade==target_grade）的 student_id
    roster_sids = (
        db.query(ClassRoster.student_id)
        .filter(
            ClassRoster.grade == target_grade,
            ClassRoster.class_num == class_num,
        )
        .all()
    )
    sid_set |= {r[0] for r in roster_sids}

    has_score_set = {r[0] for r in score_sids}  # 仅用于 new/unmatched 区分

    prev_grade = target_grade - 1

    inherited, ambiguous, new, unmatched = [], [], [], []
    for sid in sorted(sid_set):
        # 姓名：优先 target_grade 成绩库，回退 roster.name
        name = _student_name_in_grade(db, sid, target_grade)
        if name is None:
            r = (
                db.query(ClassRoster.name)
                .filter(
                    ClassRoster.student_id == sid,
                    ClassRoster.grade == target_grade,
                    ClassRoster.class_num == class_num,
                )
                .first()
            )
            name = r[0] if r else None

        iid = identity_of(db, sid)
        if iid is not None:
            # 附 g1（grade<target_grade）alias 学号 + 其 class_num
            prev_aliases = []
            for al in aliases_of(db, iid):
                if al.grade is not None and al.grade < target_grade:
                    prev_aliases.append(
                        {
                            "student_id": al.student_id,
                            "grade": al.grade,
                            "class_num": _class_num_in_grade(db, al.student_id, al.grade),
                        }
                    )
            inherited.append(
                {
                    "student_id": sid,
                    "name": name,
                    "identity_id": iid,
                    "prev_aliases": prev_aliases,
                }
            )
        elif name is not None and prev_grade >= 1:
            cands = name_candidates(db, name, prev_grade)
            if cands:
                ambiguous.append(
                    {
                        "student_id": sid,
                        "name": name,
                        "candidates": cands,
                    }
                )
            elif sid in has_score_set:
                new.append({"student_id": sid, "name": name})
            else:
                unmatched.append({"student_id": sid, "name": name})
        else:
            if sid in has_score_set:
                new.append({"student_id": sid, "name": name})
            else:
                unmatched.append({"student_id": sid, "name": name})

    # left_class：上学年本班「真正离开」的学生。
    # 学号跨学年会变，不能用 `高一学号 in 高二学号集合` 判断（两套学号空间永不相等，
    # 否则全体高一都会被误报为离班）。判定「仍在本班」的两条信号：
    #   1) 身份已链接：该生所属 identity 拥有的任一学号出现在新班集合中 -> 已继承，不算离班；
    #   2) 同名待确认：该生姓名出现在新班学生姓名中 -> 换届向导里还在 ambiguous，不算离班。
    # 两者都不满足才算真正离班。
    left_class = []
    if prev_grade >= 1:
        prev_class = _teacher_target_class(db, prev_grade)
        if prev_class is not None:
            # 新班学生姓名集合（成绩库优先，回退 roster）
            target_names = set()
            for tsid in sid_set:
                tname = _student_name_in_grade(db, tsid, target_grade)
                if tname is None:
                    r = (
                        db.query(ClassRoster.name)
                        .filter(
                            ClassRoster.student_id == tsid,
                            ClassRoster.grade == target_grade,
                            ClassRoster.class_num == class_num,
                        )
                        .first()
                    )
                    tname = r[0] if r else None
                if tname:
                    target_names.add(tname)

            prev_sids = _class_students(db, prev_grade, prev_class)
            for sid in prev_sids:
                iid = identity_of(db, sid)
                if iid is not None:
                    person_sids = {al.student_id for al in aliases_of(db, iid)}
                    if person_sids & sid_set:
                        continue  # 同一人已在新班（已继承）
                pname = _student_name_in_grade(db, sid, prev_grade)
                if pname and pname in target_names:
                    continue  # 新班有同名学生，尚在待确认，不算离班
                left_class.append(
                    {
                        "student_id": sid,
                        "name": pname,
                        "class_num": prev_class,
                    }
                )

    # 撞车预告：目标班学生的裸学号若与其他年级的裸学号空间重号，向导页
    # 据此提示「写入本届时会把整个旧届前缀化」。这里只统计、不迁移——
    # 真正的迁移发生在各写入入口的 ensure_sid_space 守门。
    from app.db.sid_space import bare_sid_spaces, is_namespaced

    spaces = bare_sid_spaces(db)
    bare_target = {sid for sid in sid_set if not is_namespaced(sid)}
    other_spaces = {g: sids for g, sids in spaces.items() if g != target_grade}
    sid_clash = {
        "would_namespace": sorted(
            g for g, sids in other_spaces.items() if sids & bare_target
        ),
        "clash_count": len(
            bare_target & {s for sids in other_spaces.values() for s in sids}
        ),
    }

    return {
        "inherited": inherited,
        "ambiguous": ambiguous,
        "new": new,
        "unmatched": unmatched,
        "left_class": left_class,
        "sid_clash": sid_clash,
        "summary": {
            "inherited": len(inherited),
            "ambiguous": len(ambiguous),
            "new": len(new),
            "unmatched": len(unmatched),
            "left_class": len(left_class),
            "total": len(sid_set),
        },
    }


def _validated_target_class(db, target_grade, class_num) -> int:
    """粘贴名册的目标年级+班必须与教师绑定一致；禁止裸 class_num / 越权写入。"""
    if target_grade not in (1, 2, 3):
        raise RosterScopeError("目标年级必须是高一/高二/高三")
    bound = _teacher_target_class(db, target_grade)
    if bound is None:
        raise RosterScopeError(f"高{target_grade}尚未绑定行政班，请先在向导第 1 步确认绑定")
    if class_num is None or int(class_num) != int(bound):
        shown = class_num if class_num is not None else "未填"
        raise RosterScopeError(
            f"目标班级（{shown}）与教师绑定的高{target_grade}班级（{bound}）不一致"
        )
    return int(bound)


def _fill_roster_info(row, plan) -> None:
    """幂等更新：只补 seat_no/gender，不碰 excluded 与学号。"""
    if plan["seat_no"] is not None:
        row.seat_no = plan["seat_no"]
    if plan["gender"] is not None:
        row.gender = plan["gender"]


def _legacy_broken_rows(db, name, target_grade) -> list:
    """旧版缺陷形态：student_id=粘贴的姓名、grade=目标年级、class_num 为 NULL、
    name 为 NULL 或空串（不同代 schema 的 NOT NULL 约束差异，两种都算缺陷行）。

    只有四个条件全部严格命中才视为可收编的缺陷行，绝不触碰高一正常数据。
    """
    from app.db.models import ClassRoster
    from sqlalchemy import or_

    return (
        db.query(ClassRoster)
        .filter(
            ClassRoster.student_id == name,
            ClassRoster.grade == target_grade,
            ClassRoster.class_num.is_(None),
            or_(ClassRoster.name.is_(None), ClassRoster.name == ""),
        )
        .all()
    )


def _migrate_student_refs(db, old_sid, new_sid) -> dict:
    """把依赖学号的业务数据从 old_sid 迁到 new_sid（homework/special/note）。

    返回 {表名: [迁移的记录 id]}，供导入批次快照记录、撤销时逆向迁回。
    """
    from app.db.models import HomeworkRecord, SpecialRecord, StudentNote

    moved: dict[str, list] = {}
    for model, label in (
        (HomeworkRecord, "homework_record"),
        (SpecialRecord, "special_record"),
        (StudentNote, "student_note"),
    ):
        rows = db.query(model).filter(model.student_id == old_sid).all()
        if rows:
            for r in rows:
                r.student_id = new_sid
            moved[label] = [r.id for r in rows]
    return moved


def _snapshot_row(row) -> dict:
    """花名册行全字段快照（撤销重建用）。"""
    return {
        "student_id": row.student_id,
        "name": row.name,
        "class_num": row.class_num,
        "grade": row.grade,
        "seat_no": row.seat_no,
        "gender": row.gender,
        "excluded": row.excluded,
        "status": row.status,
    }


def _absorb_legacy_row(db, legacy, target_sid) -> dict:
    """收编旧缺陷行：其依赖数据迁到 target_sid 后删除该行。

    调用方已保证：legacy 形态严格匹配、姓名与本批明确对应、target_sid 是
    本批写入的正常行。迁移任何记录前先比对两侧 StudentAlias：
      - 两边 alias 指向不同 identity -> 整批拒绝（不迁记录、不删旧行）；
      - 同一 identity -> 业务记录随迁，删除缺陷学号的 alias（目标侧已有，不留孤儿）；
      - 仅旧侧有 alias -> 随学号迁到 target_sid；
      - 仅目标侧有 alias -> 只迁业务记录。
    返回快照 dict（撤销时逆向还原用）。
    """
    from app.db.models import StudentAlias

    old_alias = (
        db.query(StudentAlias).filter(StudentAlias.student_id == legacy.student_id).first()
    )
    new_alias = (
        db.query(StudentAlias).filter(StudentAlias.student_id == target_sid).first()
    )
    if (
        old_alias is not None
        and new_alias is not None
        and old_alias.identity_id != new_alias.identity_id
    ):
        raise ValueError(
            f"历史异常行「{legacy.student_id}」与目标学号 {target_sid} 已关联不同的"
            "跨学年身份，无法自动收编，请先在逐人判定中处理"
        )

    snapshot = {
        "old_row": _snapshot_row(legacy),
        "new_student_id": target_sid,
        "moved_refs": {},
        "alias_action": None,
        "old_alias": None,
    }
    snapshot["moved_refs"] = _migrate_student_refs(db, legacy.student_id, target_sid)
    if old_alias is not None and new_alias is None:
        snapshot["alias_action"] = "moved"
        snapshot["old_alias"] = {
            "student_id": old_alias.student_id,
            "identity_id": old_alias.identity_id,
            "grade": old_alias.grade,
            "link_source": old_alias.link_source,
        }
        db.query(StudentAlias).filter(
            StudentAlias.student_id == legacy.student_id
        ).update({"student_id": target_sid}, synchronize_session=False)
    elif old_alias is not None:
        snapshot["alias_action"] = "deleted"
        snapshot["old_alias"] = {
            "student_id": old_alias.student_id,
            "identity_id": old_alias.identity_id,
            "grade": old_alias.grade,
            "link_source": old_alias.link_source,
        }
        db.query(StudentAlias).filter(
            StudentAlias.student_id == legacy.student_id
        ).delete(synchronize_session=False)
    db.delete(legacy)
    return snapshot


def _validate_official_sid(db, sid, name, target_grade, class_num, expect_iid=None) -> None:
    """所有带学号的导入行统一过闸（无论是否命中占位行）：

    1. 成绩库姓名：该学号在【目标年级】的成绩姓名与导入姓名不符 → 拒绝。
       其他年级的同号记录是合法的跨届重号（每学年重新编学号的学校，高二
       新号可与高一旧号相同），不参与本校验——届间隔离由调用方先行
       ensure_sid_space（G{g}:: 前缀化）保证；
    2. 目标年级班级：该学号在目标年级的成绩属于他班 → 拒绝；
    3. 身份别名：学号已挂 StudentAlias 时必须仍属于「本行学生」——
       expect_iid（占位行的 identity）给定且不一致 → 直接拒绝；
       否则用身份证据核对（display_name / 其它别名的成绩姓名，任一非空
       且与导入姓名不符 → 拒绝；全部相符才允许安全接续）。
    """
    from app.db.models import SubjectScore, Exam, StudentAlias, StudentIdentity
    from app.analysis.identity import aliases_of

    score_name = (
        db.query(SubjectScore.name)
        .join(Exam, Exam.id == SubjectScore.exam_id)
        .filter(
            Exam.grade == target_grade,
            SubjectScore.student_id == sid,
            SubjectScore.name.isnot(None),
        )
        .first()
    )
    if score_name is not None and score_name[0] != name:
        raise ValueError(
            f"学号 {sid} 在成绩库属于「{score_name[0]}」，与导入姓名「{name}」不一致，已拒绝"
        )

    other_class = (
        db.query(SubjectScore.class_num)
        .join(Exam, Exam.id == SubjectScore.exam_id)
        .filter(
            Exam.grade == target_grade,
            SubjectScore.student_id == sid,
            SubjectScore.class_num.isnot(None),
            SubjectScore.class_num != class_num,
        )
        .first()
    )
    if other_class is not None:
        raise ValueError(
            f"学号 {sid} 的高{target_grade}成绩在 {other_class[0]} 班，"
            f"与目标班级（{class_num}）不一致，已拒绝"
        )

    alias = db.query(StudentAlias).filter(StudentAlias.student_id == sid).first()
    if alias is None:
        return
    if expect_iid is not None:
        if alias.identity_id != expect_iid:
            raise ValueError(
                f"学号 {sid} 已关联其他跨学年身份，与本行学生不一致，已拒绝；请先在逐人判定中处理"
            )
        return
    ident = (
        db.query(StudentIdentity)
        .filter(StudentIdentity.id == alias.identity_id)
        .first()
    )
    if ident is not None and ident.display_name and ident.display_name != name:
        raise ValueError(
            f"学号 {sid} 已关联跨学年身份「{ident.display_name}」，"
            f"与导入姓名「{name}」不一致，已拒绝"
        )
    for al in aliases_of(db, alias.identity_id):
        if al.student_id == sid:
            continue
        other_name = (
            db.query(SubjectScore.name)
            .filter(
                SubjectScore.student_id == al.student_id,
                SubjectScore.name.isnot(None),
            )
            .first()
        )
        if other_name is not None and other_name[0] != name:
            raise ValueError(
                f"学号 {sid} 关联的身份含学号 {al.student_id}"
                f"（成绩库姓名「{other_name[0]}」），与导入姓名「{name}」不一致，已拒绝"
            )


def _replace_placeholder_sid(db, row, sid, name, target_grade, class_num, seat_no=None, gender=None) -> dict:
    """把临时学号占位行替换为正式学号：插新行 → 迁移依赖 → 删旧行。

    全程在调用方的同一事务里（统一 commit，出错整体回滚）；保留
    excluded/seat_no/gender 与年级班级归属，StudentAlias 随学号迁移。
    统一走 _validate_official_sid（成绩库姓名 / 目标年级班级 / 别名冲突，
    expect_iid=占位行 identity：别名指向他人时整批拒绝）。
    返回快照 dict（撤销时逆向还原用）。
    """
    from app.db.models import ClassRoster, StudentAlias
    from app.analysis.identity import identity_of

    old_sid = row.student_id
    old_iid = identity_of(db, old_sid)

    _validate_official_sid(
        db, sid, name, target_grade, class_num, expect_iid=old_iid
    )

    snapshot = {
        "old_row": _snapshot_row(row),
        "new_student_id": sid,
        "moved_refs": {},
        "alias_action": None,
        "old_alias": None,
    }

    db.add(
        ClassRoster(
            student_id=sid,
            name=name,
            class_num=row.class_num,
            grade=row.grade,
            seat_no=seat_no if seat_no is not None else row.seat_no,
            gender=gender if gender is not None else row.gender,
            excluded=row.excluded,
        )
    )
    db.flush()

    snapshot["moved_refs"] = _migrate_student_refs(db, old_sid, sid)
    new_alias = db.query(StudentAlias).filter(StudentAlias.student_id == sid).first()
    if new_alias is not None:
        # 学号的 alias 属于本人（校验已过）：保留，临时学号旧 alias 删除
        snapshot["alias_action"] = "deleted"
        old_alias_row = (
            db.query(StudentAlias)
            .filter(StudentAlias.student_id == old_sid)
            .first()
        )
        if old_alias_row is not None:
            snapshot["old_alias"] = {
                "student_id": old_alias_row.student_id,
                "identity_id": old_alias_row.identity_id,
                "grade": old_alias_row.grade,
                "link_source": old_alias_row.link_source,
            }
        db.query(StudentAlias).filter(StudentAlias.student_id == old_sid).delete()
    elif old_iid is not None:
        snapshot["alias_action"] = "moved"
        snapshot["old_alias"] = {
            "student_id": old_sid,
            "identity_id": old_iid,
            "grade": None,
            "link_source": None,
        }
        db.query(StudentAlias).filter(StudentAlias.student_id == old_sid).update(
            {"student_id": sid}, synchronize_session=False
        )
    db.delete(row)
    return snapshot


def _import_rows(db, target_grade, class_num, rows, *, allow_dup_names=False) -> dict:
    """名单导入（两种行：仅姓名 / 学号+姓名）。

    预检所有行（空姓名、格式、同批重复、冲突）后才落库，任何一行不通过
    整批拒绝并回滚，绝不静默写坏数据。旧缺陷行在姓名明确对应、形态严格
    匹配且无冲突时收编到目标作用域。allow_dup_names=True 供「从成绩派生」
    使用：同班同名多学号各自建行（成绩库本来就以学号区分），不走粘贴的
    同名互斥校验。
    """
    from app.db.models import ClassRoster, RosterImportBatch

    plans, errors = [], []
    seen_sid: dict[str, int] = {}
    seen_name: dict[str, int] = {}
    for idx, r in enumerate(rows, start=1):
        raw_sid = r.get("student_id")
        sid = str(raw_sid).strip() if raw_sid is not None else ""
        name = str(r.get("name") or "").strip()
        if not name:
            errors.append(f"第 {idx} 行：姓名为空，无法导入")
            continue
        if sid:
            if sid in seen_sid:
                errors.append(f"第 {idx} 行：学号 {sid} 与第 {seen_sid[sid]} 行重复")
            else:
                seen_sid[sid] = idx
        if name in seen_name and not allow_dup_names:
            errors.append(
                f"第 {idx} 行：姓名「{name}」与第 {seen_name[name]} 行重复，同批不能出现两个同名"
            )
            continue
        seen_name[name] = idx
        plans.append(
            {
                "sid": sid or None,
                "name": name,
                "seat_no": r.get("seat_no"),
                "gender": r.get("gender"),
            }
        )
    if errors:
        raise ValueError("；".join(errors))

    # 跨届学号守门：正式学号若与其他年级的裸学号撞车（每学年重新编学号
    # 的学校，高二新号可与高一旧号同号），先把撞车旧届整体 G{g}:: 前缀化
    # 让位，本届写入裸号。与下面的落库同一事务（出错整体回滚），自动备份
    # 与删除/合并的安全口径一致。renamed 记入批次快照，撤销时逆向还原。
    from app.db.sid_space import ensure_sid_space

    official_sids = {p["sid"] for p in plans if p["sid"]}
    renamed_map: dict = {}
    if official_sids:
        renamed_map = ensure_sid_space(
            db, official_sids, target_grade, commit=False, auto_backup=True
        )["renamed"]

    created = updated = replaced = repaired = 0
    batch_created: set[str] = set()  # 本批新建的学号（供派生时同名多号互不拦）
    snap_created: list[dict] = []
    snap_replaced: list[dict] = []
    snap_repaired: list[dict] = []
    try:
        for p in plans:
            sid, name = p["sid"], p["name"]

            legacy = _legacy_broken_rows(db, name, target_grade)
            if len(legacy) > 1:
                raise ValueError(
                    f"姓名「{name}」对应 {len(legacy)} 条历史异常数据行，无法自动收编，请先恢复备份核对"
                )

            # 本班本年级既有同名行（跨班同名不在其列，绝不跨作用域合并）
            same_name_rows = (
                db.query(ClassRoster)
                .filter(
                    ClassRoster.grade == target_grade,
                    ClassRoster.class_num == class_num,
                    ClassRoster.name == name,
                )
                .all()
            )

            if sid is None:
                # ── 仅姓名：生成/复用临时学号，幂等 ──
                target_sid = temp_sid(target_grade, class_num, name)
                existing = next(
                    (r for r in same_name_rows if r.student_id == target_sid), None
                )
                if existing is not None:
                    _fill_roster_info(existing, p)
                    updated += 1
                elif same_name_rows:
                    if len(same_name_rows) > 1:
                        raise ValueError(
                            f"「{name}」在本班名册已有 {len(same_name_rows)} 条同名行，"
                            "请先在作业花名册中核对"
                        )
                    _fill_roster_info(same_name_rows[0], p)
                    target_sid = same_name_rows[0].student_id
                    updated += 1
                else:
                    new_row = ClassRoster(
                        student_id=target_sid,
                        name=name,
                        class_num=class_num,
                        grade=target_grade,
                        seat_no=p["seat_no"],
                        gender=p["gender"],
                        excluded=0,
                    )
                    db.add(new_row)
                    db.flush()
                    snap_created.append(_snapshot_row(new_row))
                    created += 1
            else:
                # ── 学号+姓名：统一冲突校验（成绩库姓名 / 目标年级班级 / 别名）──
                _validate_official_sid(db, sid, name, target_grade, class_num)
                official = (
                    db.query(ClassRoster).filter(ClassRoster.student_id == sid).first()
                )
                # 占位判定：优先精确等于系统为该姓名/作用域生成的临时学号。
                # 回退：建册后改过名的学生，占位行学号里冻结着建册时的旧名
                # （改名只更新 name 字段、不动学号），精确匹配会落空——按
                # 「本班同名 + TMP- 前缀且唯一」识别。系统内 TMP- 学号只有
                # temp_sid 一个生成源（真实学号绝不同形），回退不会误伤；
                # 替换仍走 _replace_placeholder_sid 的完整校验与事务。
                placeholder = next(
                    (
                        r
                        for r in same_name_rows
                        if r.student_id == temp_sid(target_grade, class_num, name)
                    ),
                    None,
                )
                if placeholder is None:
                    tmp_candidates = [
                        r
                        for r in same_name_rows
                        if str(r.student_id).startswith(
                            f"{TEMP_SID_PREFIX}{target_grade}-{class_num}-"
                        )
                    ]
                    if len(tmp_candidates) == 1:
                        placeholder = tmp_candidates[0]
                if official is not None:
                    if official.name and official.name != name:
                        raise ValueError(
                            f"学号 {sid} 已属于「{official.name}」，与导入姓名「{name}」不一致，已拒绝"
                        )
                    if official.grade != target_grade or official.class_num != class_num:
                        raise ValueError(
                            f"学号 {sid} 已存在于高{official.grade}（{official.class_num}班），"
                            "与目标班级不一致，已拒绝"
                        )
                    if not official.name:
                        official.name = name
                    _fill_roster_info(official, p)
                    target_sid = sid
                    updated += 1
                elif placeholder is not None:
                    snap_replaced.append(
                        _replace_placeholder_sid(
                            db,
                            placeholder,
                            sid,
                            name,
                            target_grade,
                            class_num,
                            p["seat_no"],
                            p["gender"],
                        )
                    )
                    target_sid = sid
                    replaced += 1
                elif any(r.student_id not in batch_created for r in same_name_rows):
                    raise ValueError(
                        f"「{name}」在本班已用学号 "
                        f"{next(r.student_id for r in same_name_rows if r.student_id not in batch_created)}，"
                        "未找到待补学号占位行，已拒绝重复建册"
                    )
                else:
                    new_row = ClassRoster(
                        student_id=sid,
                        name=name,
                        class_num=class_num,
                        grade=target_grade,
                        seat_no=p["seat_no"],
                        gender=p["gender"],
                        excluded=0,
                    )
                    db.add(new_row)
                    db.flush()
                    snap_created.append(_snapshot_row(new_row))
                    target_sid = sid
                    created += 1
                    batch_created.add(sid)

            # 收编旧缺陷行（姓名明确对应 + 形态严格匹配 + 唯一）
            for lg in legacy:
                snap_repaired.append(
                    _absorb_legacy_row(db, lg, target_sid)
                )
                repaired += 1

        # 导入批次快照（与行级变更同一事务）：撤销时按快照逆向还原
        batch_id = uuid.uuid4().hex
        db.add(
            RosterImportBatch(
                id=batch_id,
                grade=target_grade,
                class_num=class_num,
                payload=[
                    {"student_id": p["sid"], "name": p["name"]} for p in plans
                ],
                created_rows=snap_created,
                replaced_rows=snap_replaced,
                repaired_rows=snap_repaired,
                renamed=renamed_map,
                summary={
                    "created": created,
                    "updated": updated,
                    "replaced": replaced,
                    "repaired": repaired,
                },
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    total = (
        db.query(ClassRoster)
        .filter(
            ClassRoster.grade == target_grade,
            ClassRoster.class_num == class_num,
        )
        .count()
    )
    return {
        "batch_id": batch_id,
        "created": created,
        "updated": updated,
        "replaced": replaced,
        "repaired": repaired,
        "total": total,
    }


def build_roster(
    db,
    target_grade,
    *,
    class_num=None,
    from_scores=False,
    rows=None,
) -> dict:
    """建/刷 target_grade 作业花名册（ClassRoster, grade=target_grade）。

    - rows（粘贴名单）两种行：
      {name}：仅姓名。生成临时学号 TMP-{grade}-{class}-{name}，幂等；同时
        收编旧版缺陷行（student_id=姓名、name/class_num 为 NULL）。
      {student_id, name}：正式学号。统一冲突校验（成绩库姓名/目标年级班级/
        已挂身份别名）；命中本班同名待补学号占位行（精确等于 temp_sid）时
        事务性替换，作业/特殊/档案记录与身份别名随学号迁移；学号已被占用、
        跨班同名等冲突整批拒绝。正式学号后续上传成绩可自然接续，跨学年身份
        仍走逐人判定。
    - from_scores=True：从 target_grade 成绩 class_num 派生 {student_id, name}，
      复用同一条导入/占位替换事务逻辑——先姓名建册的学生出分后按正式学号
      替换占位行并迁移全部依赖，绝不再 merge 出第二条重复行。
    目标 grade+class 必须与教师绑定一致，所有行严格写入目标作用域。
    返回 {created, updated, replaced, repaired, total}。
    """
    from app.db.models import ClassRoster, SubjectScore, Exam

    class_num = _validated_target_class(db, target_grade, class_num)

    if rows:
        return _import_rows(db, target_grade, class_num, rows)

    if from_scores:
        sid_name_rows = (
            db.query(SubjectScore.student_id, SubjectScore.name)
            .join(Exam, Exam.id == SubjectScore.exam_id)
            .filter(Exam.grade == target_grade, SubjectScore.class_num == class_num)
            .distinct()
            .all()
        )
        # 同一学号可能带多行姓名（含 NULL），取首个非空；无名的无法入册，跳过
        name_by: dict = {}
        for sid, nm in sid_name_rows:
            if sid is None:
                continue
            if sid not in name_by or (not name_by[sid] and nm):
                name_by[sid] = nm
        derived = [
            {"student_id": sid, "name": nm} for sid, nm in name_by.items() if nm
        ]
        if derived:
            return _import_rows(
                db, target_grade, class_num, derived, allow_dup_names=True
            )

    total = (
        db.query(ClassRoster)
        .filter(
            ClassRoster.grade == target_grade,
            ClassRoster.class_num == class_num,
        )
        .count()
    )
    return {
        "created": 0,
        "updated": 0,
        "replaced": 0,
        "repaired": 0,
        "total": total,
    }


# ─────────────────────────── 同名批量确认（confirm-batch） ───────────────────────────


def _norm_name(name) -> str:
    """姓名规范化：去掉全部空白（含全角空格/制表符），供同名安全判定。"""
    return "".join(str(name or "").split())


def _g2_scope_name(db, sid, target_grade, class_num) -> Optional[str]:
    """校验 g2 学号属于目标班作用域，并返回库内姓名（服务端真相，不信前端）。

    作用域口径与 _validate_official_sid 一致：
      - 目标年级成绩出现在其他班 -> 拒绝；
      - 目标年级本班有成绩，或目标班花名册（grade+class）有该行 -> 在册；
      - 两者皆无 -> 拒绝。
    姓名优先目标年级成绩库，回退目标班花名册；查不到返回 None（调用方拒绝）。
    """
    from app.db.models import SubjectScore, Exam, ClassRoster

    other_class = (
        db.query(SubjectScore.class_num)
        .join(Exam, Exam.id == SubjectScore.exam_id)
        .filter(
            Exam.grade == target_grade,
            SubjectScore.student_id == sid,
            SubjectScore.class_num.isnot(None),
            SubjectScore.class_num != class_num,
        )
        .first()
    )
    if other_class is not None:
        raise ValueError(
            f"学号 {sid} 的高{target_grade}成绩在 {other_class[0]} 班，"
            f"与你的班级（{class_num}班）不一致，已拒绝"
        )

    in_class = (
        db.query(SubjectScore.student_id)
        .join(Exam, Exam.id == SubjectScore.exam_id)
        .filter(
            Exam.grade == target_grade,
            SubjectScore.student_id == sid,
            SubjectScore.class_num == class_num,
        )
        .first()
    )
    roster_row = (
        db.query(ClassRoster)
        .filter(
            ClassRoster.student_id == sid,
            ClassRoster.grade == target_grade,
            ClassRoster.class_num == class_num,
        )
        .first()
    )
    if in_class is None and roster_row is None:
        raise ValueError(
            f"学号 {sid} 不在高{target_grade}（{class_num}班）的成绩或花名册中，已拒绝"
        )

    name = _student_name_in_grade(db, sid, target_grade)
    if name is None and roster_row is not None:
        name = roster_row.name
    return name


def confirm_batch(db, target_grade, class_num, items) -> dict:
    """同名批量确认：先完整预检，再单事务落库，任一违规整批回滚。

    items=[{g2_student_id, decision: link|new, g1_student_id?}]。「稍后处理」
    的行由前端直接不发，服务端不写。

    服务端重新核验（绝不信任前端的 safe 标记）：
      1. 教师目标 grade/class（与绑定一致，否则 409）；
      2. 每个 g2 学号的目标班 roster/成绩归属，且尚未关联任何身份；
      3. link：g1 候选在 prev_grade 成绩库姓名规范化后与本行一致；未显式
         指定时必须恰好一个同名候选，多个必须明确选择；g1 未被关联到别人；
      4. 批内 g2 / g1 学号各自唯一，且任意 g1 不得与本批任意 g2 相同
         （两阶段预检：逐行收集后统一比对，与输入顺序无关，否则落库时会
         撞 uq_alias_student 唯一约束）；
      5. new：同一事务内建独立 identity，只挂 g2 学号。

    返回 {batch_id, linked, new_students, results[]}；batch_id 供 undo 精确
    回滚本批新增的关联。
    """
    from app.db.models import SubjectScore, Exam, RolloverConfirmBatch
    from app.analysis.identity import (
        identity_of,
        ensure_identity,
        link_aliases,
        name_candidates,
    )

    if target_grade not in (2, 3):
        raise ValueError("批量确认只支持升入高二/高三的换届")
    class_num = _validated_target_class(db, target_grade, class_num)
    prev_grade = target_grade - 1
    if not items:
        raise ValueError("批量确认至少需要一名学生")

    errors = []
    seen_g2: dict[str, int] = {}
    seen_g1: dict[str, int] = {}
    plans = []
    for idx, raw in enumerate(items, start=1):
        g2 = str(raw.get("g2_student_id") or "").strip()
        decision = raw.get("decision")
        if not g2:
            errors.append(f"第 {idx} 行：缺少高{target_grade}学号")
            continue
        if g2 in seen_g2:
            errors.append(f"第 {idx} 行：学号 {g2} 与第 {seen_g2[g2]} 行重复")
            continue
        seen_g2[g2] = idx
        if decision not in ("link", "new"):
            errors.append(f"第 {idx} 行（{g2}）：判定必须是 link 或 new")
            continue
        try:
            name = _g2_scope_name(db, g2, target_grade, class_num)
        except ValueError as exc:
            errors.append(f"第 {idx} 行：{exc}")
            continue
        if not name:
            errors.append(f"第 {idx} 行：学号 {g2} 在成绩库/花名册中无姓名，无法核验")
            continue
        if identity_of(db, g2) is not None:
            errors.append(
                f"第 {idx} 行：学号 {g2} 已关联跨学年身份，请刷新预览后重试"
            )
            continue

        plan = {"idx": idx, "g2": g2, "name": name, "decision": decision, "g1": None}
        if decision == "link":
            g1_raw = raw.get("g1_student_id")
            g1 = str(g1_raw).strip() if g1_raw else None
            if g1 is None:
                cands = name_candidates(db, name, prev_grade)
                if len(cands) != 1:
                    errors.append(
                        f"第 {idx} 行（{name}）：高{prev_grade}同名候选有 "
                        f"{len(cands)} 个，必须明确选择其一后才能关联"
                    )
                    continue
                g1 = cands[0]["student_id"]
            if g1 in seen_g1:
                errors.append(
                    f"第 {idx} 行：高{prev_grade}学号 {g1} 与第 {seen_g1[g1]} 行重复使用，已拒绝"
                )
                continue
            # 候选核验：该学号在 prev_grade 成绩库确为本行学生的同名（规范化后一致）
            cand_names = {
                r[0]
                for r in db.query(SubjectScore.name)
                .join(Exam, Exam.id == SubjectScore.exam_id)
                .filter(
                    Exam.grade == prev_grade,
                    SubjectScore.student_id == g1,
                    SubjectScore.name.isnot(None),
                )
                .all()
            }
            if not cand_names or not any(
                _norm_name(n) == _norm_name(name) for n in cand_names
            ):
                errors.append(
                    f"第 {idx} 行：学号 {g1} 不是「{name}」在高{prev_grade}的同名候选，已拒绝"
                )
                continue
            if identity_of(db, g1) is not None:
                errors.append(
                    f"第 {idx} 行：高{prev_grade}学号 {g1} 已被关联到其他学生，已拒绝"
                )
                continue
            seen_g1[g1] = idx
            plan["g1"] = g1
        plans.append(plan)

    # 预检第二阶段（与输入顺序无关）：本批任意 g1 不得与本批任意 g2 相同。
    # g1 在前、g2 在后 / g2 在前、g1 在后 / 同行自撞三种顺序都必须在此拦下。
    for p in plans:
        if p["g1"] and p["g1"] in seen_g2:
            errors.append(
                f"第 {p['idx']} 行：高{prev_grade}候选学号 {p['g1']} 与第 "
                f"{seen_g2[p['g1']]} 行的高{target_grade}学号相同，"
                "批内不能混用同一学号，已拒绝"
            )

    if errors:
        raise ValueError("；".join(errors))

    # 单事务落库：任一步失败整体回滚（包括批次快照本身）
    batch_id = uuid.uuid4().hex
    created_aliases: list[dict] = []
    created_identities: list[int] = []
    results = []
    try:
        # 跨届学号守门：本届（g2）学号若与其他年级的裸学号撞车，先把撞车
        # 旧届整体 G{g}:: 前缀化让位。预检阶段 name_candidates 返回的 g1
        # 可能还是裸号，迁移发生后库里已是 G1::x，必须用 renamed 重写
        # plan 的 g1 再落库，否则新别名与已迁移的高一成绩对不上；g2 属
        # 本届，永不改写（renamed 为空时无操作）。
        from app.db.sid_space import ensure_sid_space

        renamed = ensure_sid_space(
            db,
            {p["g2"] for p in plans},
            target_grade,
            commit=False,
            auto_backup=True,
        )["renamed"]
        if renamed:
            for p in plans:
                if p["g1"] and p["g1"] in renamed:
                    p["g1"] = renamed[p["g1"]]
        for p in plans:
            iid = ensure_identity(db, display_name=p["name"], commit=False)
            created_identities.append(iid)
            alias_items = [(p["g2"], target_grade)]
            if p["g1"]:
                alias_items.append((p["g1"], prev_grade))
            res = link_aliases(db, iid, alias_items, "name_confirmed", commit=False)
            if res["conflicts"]:
                raise ValueError(
                    f"学号 {p['g2']} 关联时发生身份冲突，整批已回滚，请刷新后重试"
                )
            for sid in res["linked"]:
                created_aliases.append({"student_id": sid, "identity_id": iid})
            results.append(
                {
                    "g2_student_id": p["g2"],
                    "name": p["name"],
                    "decision": p["decision"],
                    "g1_student_id": p["g1"],
                    "identity_id": iid,
                    "status": "linked" if p["decision"] == "link" else "new",
                }
            )
        db.add(
            RolloverConfirmBatch(
                id=batch_id,
                grade=target_grade,
                class_num=class_num,
                payload=[
                    {
                        "g2_student_id": p["g2"],
                        "name": p["name"],
                        "decision": p["decision"],
                        "g1_student_id": p["g1"],
                    }
                    for p in plans
                ],
                created_aliases=created_aliases,
                created_identities=created_identities,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    linked_n = sum(1 for r in results if r["status"] == "linked")
    return {
        "batch_id": batch_id,
        "grade": target_grade,
        "class_num": class_num,
        "linked": linked_n,
        "new_students": len(results) - linked_n,
        "results": results,
    }


def undo_confirm_batch(db, batch_id) -> dict:
    """撤销一次同名批量确认：只删该批事务实际新建的 alias / identity。

    安全口径：
      - 批次不存在 -> 404；已撤销过 -> 409；
      - 教师当前绑定的高{grade}班级与批次目标班不一致 -> 409（越权防呆）；
      - 只删批内记录、且仍指向本批 identity、link_source 仍为
        name_confirmed 的 alias（被单独解除/后续改动的跳过并说明原因），
        绝不触碰提交前已存在的关联；
      - 本批新建的 identity 仅在无残留 alias 且无 imported_history 时删除。
    """
    from app.db.models import (
        RolloverConfirmBatch,
        StudentAlias,
        StudentIdentity,
        ImportedHistory,
    )

    batch = (
        db.query(RolloverConfirmBatch)
        .filter(RolloverConfirmBatch.id == str(batch_id))
        .first()
    )
    if batch is None:
        raise KeyError("找不到该确认批次")
    if batch.undone:
        raise ValueError("该批次已撤销过，不能重复撤销")
    bound = _teacher_target_class(db, batch.grade)
    if bound is None or int(bound) != int(batch.class_num):
        raise RosterScopeError(
            f"当前绑定的高{batch.grade}班级与该批次（{batch.class_num}班）不一致，拒绝撤销"
        )

    removed_aliases, skipped = [], []
    for rec in batch.created_aliases or []:
        sid, iid = rec["student_id"], rec["identity_id"]
        row = (
            db.query(StudentAlias).filter(StudentAlias.student_id == sid).first()
        )
        if row is None:
            skipped.append(
                {"student_id": sid, "reason": "该学号已无关联（可能已单独解除）"}
            )
            continue
        if row.identity_id != iid or row.link_source != "name_confirmed":
            skipped.append(
                {"student_id": sid, "reason": "关联已被后续操作改动，保留现状"}
            )
            continue
        db.delete(row)
        removed_aliases.append(sid)

    removed_identities = []
    for iid in batch.created_identities or []:
        still_aliased = (
            db.query(StudentAlias)
            .filter(StudentAlias.identity_id == iid)
            .count()
        )
        has_history = (
            db.query(ImportedHistory)
            .filter(ImportedHistory.identity_id == iid)
            .count()
        )
        if still_aliased == 0 and has_history == 0:
            db.query(StudentIdentity).filter(StudentIdentity.id == iid).delete()
            removed_identities.append(iid)

    batch.undone = 1
    db.commit()
    return {
        "batch_id": batch.id,
        "removed_aliases": removed_aliases,
        "removed_identities": removed_identities,
        "skipped": skipped,
    }


def undo_roster_import(db, batch_id) -> dict:
    """撤销一次「写入名册」导入：按批次快照单事务逆向还原。

    安全口径（与 undo_confirm_batch 一致）：
      - 批次不存在 -> KeyError（HTTP 404）；已撤销过 -> 409；
      - 教师当前绑定的高{grade}班级与批次目标班不一致 -> 409（越权防呆）；
      - 只逆本批事务实际做的变更，被后续操作改动过的行跳过并说明；
      - 逆向顺序与导入相反：先逆行级变更（新建删除/替换还原/收编还原），
        再逆届命名空间迁移（renamed 反向改写）。
    """
    from app.db.models import (
        RosterImportBatch, ClassRoster, StudentAlias,
        HomeworkRecord, SpecialRecord, StudentNote,
        SubjectScore, TotalScore, Exam,
        RolloverConfirmBatch, StudentChangeLog,
    )

    batch = (
        db.query(RosterImportBatch).filter(RosterImportBatch.id == str(batch_id)).first()
    )
    if batch is None:
        raise KeyError("找不到该导入批次")
    if batch.undone:
        raise ValueError("该导入批次已撤销过，不能重复撤销")
    bound = _teacher_target_class(db, batch.grade)
    if bound is None or int(bound) != int(batch.class_num):
        raise RosterScopeError(
            f"当前绑定的高{batch.grade}班级与该批次（{batch.class_num}班）不一致，拒绝撤销"
        )

    removed_rows: list[str] = []
    restored_rows: list[str] = []
    moved_back_refs: list[str] = []
    skipped: list[dict] = []

    # ── 1) 逆行级变更（与导入顺序相反）──

    # 1a. 新建行：仅当行原样存在且无任何业务引用/别名时删除（导入后学生
    # 已被使用——挂了身份/记了作业——则保留并说明，绝不破坏后续数据）
    for snap in batch.created_rows or []:
        sid = snap["student_id"]
        row = (
            db.query(ClassRoster).filter(ClassRoster.student_id == sid).first()
        )
        if row is None:
            skipped.append({"student_id": sid, "reason": "该行已不存在（可能已被后续操作删除）"})
            continue
        if _snapshot_row(row) != snap:
            skipped.append({"student_id": sid, "reason": "该行已被后续操作修改，保留现状"})
            continue
        used = (
            db.query(StudentAlias).filter(StudentAlias.student_id == sid).count()
            + db.query(HomeworkRecord).filter(HomeworkRecord.student_id == sid).count()
            + db.query(SpecialRecord).filter(SpecialRecord.student_id == sid).count()
            + db.query(StudentNote).filter(StudentNote.student_id == sid).count()
            + db.query(SubjectScore).filter(SubjectScore.student_id == sid).count()
        )
        if used:
            skipped.append(
                {"student_id": sid, "reason": "导入后已有作业/档案/身份等关联，保留该行"}
            )
            continue
        db.delete(row)
        removed_rows.append(sid)

    # 1b. 替换/收编逆向：记录迁回旧号 → 删新行 → 重建旧行 → alias 还原
    def _restore_replace(snap: dict) -> None:
        old_row, new_sid = snap["old_row"], snap["new_student_id"]
        old_sid = old_row["student_id"]
        new_row = (
            db.query(ClassRoster).filter(ClassRoster.student_id == new_sid).first()
        )
        if new_row is None:
            skipped.append({"student_id": old_sid, "reason": f"新学号 {new_sid} 的行已不存在，跳过"})
            return
        # 两个保守跳过判定都必须先于任何写操作：外键开启下先把子行迁回
        # 旧号会因旧父行尚未重建被立即拒绝；且跳过时不应已产生半截变更。
        # 新行的身份三要素与替换时写入的不一致 = 已被后续操作改动。
        if (new_row.name, new_row.grade, new_row.class_num) != (
            old_row["name"],
            old_row["grade"],
            old_row["class_num"],
        ):
            skipped.append(
                {"student_id": old_sid, "reason": f"新学号 {new_sid} 的行已被后续操作修改，保留现状"}
            )
            return
        # 旧号被占用（后续又建了同号行）→ 保守跳过，避免唯一键冲突
        existing_old = (
            db.query(ClassRoster)
            .filter(ClassRoster.student_id == old_sid)
            .first()
        )
        if existing_old is not None:
            skipped.append(
                {"student_id": old_sid, "reason": "旧学号已被其他行占用，无法重建旧行"}
            )
            return
        # 还原顺序 = 导入替换（_replace_placeholder_sid 桥接）的逆向：
        # 先重建旧号父行 → 子行（作业/特殊）从新号迁回旧号（此刻新旧父行
        # 并存，双向合法）→ 最后删新号行（已无快照内子行引用它）。
        db.add(ClassRoster(**old_row))
        db.flush()
        model_map = {
            "homework_record": HomeworkRecord,
            "special_record": SpecialRecord,
            "student_note": StudentNote,
        }
        for label, ids in (snap.get("moved_refs") or {}).items():
            model = model_map[label]
            for rid in ids:
                rec = db.query(model).filter(model.id == rid).first()
                if rec is not None and rec.student_id == new_sid:
                    rec.student_id = old_sid
                    moved_back_refs.append(str(rid))
        db.delete(new_row)
        db.flush()
        restored_rows.append(old_sid)

        action = snap.get("alias_action")
        old_alias = snap.get("old_alias")
        if action == "moved" and old_alias:
            cur = (
                db.query(StudentAlias)
                .filter(StudentAlias.student_id == new_sid)
                .first()
            )
            if cur is not None and cur.identity_id == old_alias["identity_id"]:
                cur.student_id = old_sid
            else:
                skipped.append(
                    {"student_id": old_sid, "reason": "身份关联已被后续操作改动，保留现状"}
                )
        elif action == "deleted" and old_alias:
            exists = (
                db.query(StudentAlias)
                .filter(StudentAlias.student_id == old_sid)
                .count()
            )
            if exists:
                skipped.append(
                    {"student_id": old_sid, "reason": "旧学号已有新的身份关联，保留现状"}
                )
            else:
                db.add(
                    StudentAlias(
                        identity_id=old_alias["identity_id"],
                        student_id=old_sid,
                        grade=old_alias["grade"],
                        link_source=old_alias["link_source"],
                    )
                )

    for snap in batch.replaced_rows or []:
        _restore_replace(snap)
    for snap in batch.repaired_rows or []:
        _restore_replace(snap)

    # ── 2) 逆届命名空间迁移（renamed 反向改写，与本批迁移同一表集合）──
    renamed = batch.renamed or {}
    if renamed:
        from app.db.sid_space import rekey_roster_row

        reversed_map = {new: old for old, new in renamed.items()}
        # 花名册主键反向改写必须走桥接（homework_record/special_record 的
        # 外键指向 class_roster 主键，直改父键或先迁子行都会被逐语句外键
        # 校验拒绝）：先建回旧号行 → 子行随桥迁回旧号 → 删前缀号行。
        for new_sid, old_sid in reversed_map.items():
            rekey_roster_row(db, new_sid, old_sid)
        # 其余表直改（student_id 无数据库外键）；homework/special 已随桥
        # 迁走，这里自然零命中，保留在循环里仅兜底历史孤儿行。
        for model in (SubjectScore, TotalScore, StudentAlias,
                      HomeworkRecord, SpecialRecord, StudentNote):
            rows = db.query(model).filter(model.student_id.in_(list(reversed_map))).all()
            for r in rows:
                r.student_id = reversed_map[r.student_id]
        for b in db.query(RolloverConfirmBatch).all():
            payload = _remap_json_local(b.payload, reversed_map)
            aliases = _remap_json_local(b.created_aliases, reversed_map)
            if payload != b.payload or aliases != b.created_aliases:
                b.payload, b.created_aliases = payload, aliases
        for lg in db.query(StudentChangeLog).all():
            if lg.student_id and lg.student_id in reversed_map:
                lg.student_id = reversed_map[lg.student_id]
            for field in ("before_summary", "after_summary", "detail"):
                old = getattr(lg, field)
                new = _remap_json_local(old, reversed_map)
                if new != old:
                    setattr(lg, field, new)

    batch.undone = 1
    db.commit()
    return {
        "batch_id": batch.id,
        "removed_rows": removed_rows,
        "restored_rows": restored_rows,
        "moved_back_refs": len(moved_back_refs),
        "renamed_reversed": len(renamed),
        "skipped": skipped,
    }


def _remap_json_local(node, mapping: dict):
    """undo 用的 JSON 整值反向重写（与 sid_space._remap_json 同规则）。"""
    if isinstance(node, dict):
        return {k: _remap_json_local(v, mapping) for k, v in node.items()}
    if isinstance(node, list):
        return [_remap_json_local(v, mapping) for v in node]
    if isinstance(node, str):
        return mapping.get(node, node)
    return node


def last_roster_import(db) -> dict:
    """最近一次未撤销的导入批次摘要（供页面恢复撤销按钮显示）。"""
    from app.db.models import RosterImportBatch

    batch = (
        db.query(RosterImportBatch)
        .filter(RosterImportBatch.undone == 0)
        .order_by(RosterImportBatch.created_at.desc(), RosterImportBatch.id.desc())
        .first()
    )
    if batch is None:
        return {"batch_id": None}
    return {
        "batch_id": batch.id,
        "grade": batch.grade,
        "class_num": batch.class_num,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "summary": batch.summary or {},
        "renamed_count": len(batch.renamed or {}),
    }


def set_active_grade(db, grade) -> dict:
    """写入 active_grade 配置行。"""
    from app.db.models import HomeworkSetting

    grade = int(grade)
    if grade not in (1, 2, 3):
        raise ValueError("年级必须是 1、2 或 3")
    if _teacher_target_class(db, grade) is None:
        raise ValueError(f"高{grade}尚未绑定行政班")

    db.merge(HomeworkSetting(key="active_grade", value=str(grade)))
    db.commit()
    return {"active_grade": grade}


def import_history(
    db,
    *,
    identity_id=None,
    student_id=None,
    link_g1_student_id=None,
    name=None,
    target_grade=None,
    rows,
) -> dict:
    """写 ImportedHistory（按 identity 挂，与全年级统计隔离）。

    identity 解析（按优先级）：
      1. 直接给 identity_id
      2. student_id 的 identity_of
      3. 都没有但给了 student_id/name -> ensure_identity 新建并 link 该 student_id
    link_g1_student_id 给定 -> link 到同一 identity（source=manual）。

    rows=[{exam_label, exam_seq?, kind(subject/total), subject?, total_type?,
    raw_score?, grade_score?, grade_percentile?, xueji_rank?, grade?}]
    """
    from app.db.models import ImportedHistory
    from app.analysis.identity import identity_of, ensure_identity, link_aliases

    # 跨届学号守门：本入口也会经 link_aliases 写学号，与 link / crosswalk
    # 同一口径——本届学号撞车时先前缀化旧届，请求里的旧届裸学号（含
    # link_g1_student_id）用 renamed 改写后再落库。target_grade 缺省时无
    # 届上下文可判定，跳过守门（此时也不写跨届 alias）。
    from app.db.sid_space import ensure_sid_space

    renamed = {}
    if target_grade in (1, 2, 3):
        incoming = {str(s) for s in (student_id,) if s is not None}
        if incoming:
            renamed = ensure_sid_space(
                db, incoming, target_grade, commit=False, auto_backup=True
            )["renamed"]
    if student_id is not None and str(student_id) in renamed:
        student_id = renamed[str(student_id)]
    if link_g1_student_id is not None and str(link_g1_student_id) in renamed:
        link_g1_student_id = renamed[str(link_g1_student_id)]

    # 1) 解析 identity_id
    if identity_id is None:
        if student_id is not None:
            identity_id = identity_of(db, str(student_id))
        if identity_id is None and (student_id is not None or name is not None):
            identity_id = ensure_identity(db, display_name=name)
            if student_id is not None:
                link_aliases(db, identity_id, [(str(student_id), target_grade)], "manual")

    if identity_id is None:
        return {"identity_id": None, "imported": 0}

    # 2) link_g1_student_id 挂到同一 identity
    if link_g1_student_id is not None:
        previous_grade = target_grade - 1 if target_grade in (2, 3) else None
        link_aliases(db, identity_id, [(str(link_g1_student_id), previous_grade)], "manual")

    # 3) 写历史行
    count = 0
    for r in rows:
        db.add(
            ImportedHistory(
                identity_id=identity_id,
                grade=r.get("grade", 1),
                exam_label=r.get("exam_label"),
                exam_seq=r.get("exam_seq"),
                kind=r.get("kind", "subject"),
                subject=r.get("subject"),
                total_type=r.get("total_type"),
                raw_score=r.get("raw_score"),
                grade_score=r.get("grade_score"),
                grade_percentile=r.get("grade_percentile"),
                xueji_rank=r.get("xueji_rank"),
            )
        )
        count += 1
    db.commit()

    return {"identity_id": identity_id, "imported": count}


def get_active_grade(db) -> int:
    """读 homework_setting active_grade；缺省回落 max(class_roster.grade)
    -> max(Exam.grade) -> 1。"""
    from app.db.models import HomeworkSetting, ClassRoster, Exam
    from sqlalchemy import func

    row = (
        db.query(HomeworkSetting.value)
        .filter(HomeworkSetting.key == "active_grade")
        .first()
    )
    if row and row[0] is not None:
        try:
            return int(row[0])
        except (TypeError, ValueError):
            pass

    mg = db.query(func.max(ClassRoster.grade)).scalar()
    if mg is not None:
        return int(mg)

    me = db.query(func.max(Exam.grade)).scalar()
    if me is not None:
        return int(me)

    return 1
