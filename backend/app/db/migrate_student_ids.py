"""跨届撞号迁移：高二重新编号后，新学号可能撞历史旧学号。

学校的学号在分班/升级后重新编号时，新编号值域可能覆盖旧编号（如高一
9900629=林一舟，高二重新编号后 9900629=周子墨）。student_id 字符串被假设
全局唯一（student_alias 有唯一约束 uq_alias_student），撞号会破坏这一前提：
按「人」聚合的画像（person_ids → 全部学号并集查询）会把两个学生的成绩、
总分、导入历史混到一起。

班主任版的当前身份依据是花名册行（class_roster 主键即学号，一人一行，
name 即该学号当前属主）。迁移策略：
- 检测：成绩表里某学号出现与花名册姓名不一致的姓名行；
- 改写：这些「别人的历史行」学号改为带届别命名空间的 g<年级>-<原号>
  （如 g1-9900629），SubjectScore / TotalScore 同批考试一并改指；
- 建链：按姓名在花名册中找唯一同名行（含 TMP- 占位行），把改写号与该行
  学号挂到同一 identity（ensure_identity + link_aliases，source=collision_auto），
  画像经身份链正确合并跨学年历史；同名歧义/无候选留待人工（换届页确认）；
- 幂等：改写后不再命中检测；已挂 alias 跳过。

显示层用 strip_id_namespace 还原原学号。无花名册行的纯历史学号不动。
"""

import re

from sqlalchemy.orm import Session

_NS_PATTERN = re.compile(r"^g(\d+)-(.+)$")


def strip_id_namespace(sid: str) -> str:
    """显示用：去掉届别命名空间前缀（g1-9900629 → 9900629）。"""
    m = _NS_PATTERN.match(sid or "")
    return m.group(2) if m else (sid or "")


def _namespaced_sid(grade, sid: str) -> str:
    return f"g{int(grade) if grade is not None else 0}-{sid}"


def migrate_colliding_student_ids(db: Session) -> dict:
    """检测并修复「学号名下挂了别人的历史成绩」。可重复执行。

    返回 {collisions, rekeyed_rows, rekeyed_ids, linked, pending}。
    """
    from app.db.models import (
        ClassRoster,
        Exam,
        StudentAlias,
        SubjectScore,
        TotalScore,
    )
    from app.analysis.identity import ensure_identity, link_aliases

    stats = {
        "collisions": 0, "rekeyed_rows": 0, "rekeyed_ids": 0,
        "linked": 0, "pending": [],
    }

    grade_map = {row[0]: row[1] for row in db.query(Exam.id, Exam.grade).all()}

    # 批量预载映射，内存筛选候选，快路径仅少量整表扫描
    score_names: dict[str, set[str]] = {}
    for sid, nm in db.query(SubjectScore.student_id, SubjectScore.name).all():
        nm = (nm or "").strip()
        if nm:
            score_names.setdefault(sid, set()).add(nm)
    roster_map: dict[str, set[str]] = {}
    roster_rows: list[tuple[str, str, int | None]] = []
    for sid, nm, rg in db.query(
        ClassRoster.student_id, ClassRoster.name, ClassRoster.grade
    ).all():
        nm = (nm or "").strip()
        if nm:
            roster_map.setdefault(sid, set()).add(nm)
            roster_rows.append((sid, nm, rg))

    # 候选：花名册持有该学号（当前身份），且成绩里存在与花名册姓名不一致
    # 的行。纯历史学号（花名册无人持有）无当前身份争议，不动。
    candidates = []
    for sid, names in score_names.items():
        rnames = roster_map.get(sid, set())
        if rnames and (names - rnames):
            candidates.append(sid)

    for sid in candidates:
        stats["collisions"] += 1
        rows = (
            db.query(SubjectScore)
            .filter(SubjectScore.student_id == sid)
            .all()
        )
        # 按姓名分组；姓名为空的行无法判定归属，不动
        groups: dict[str, list] = {}
        for r in rows:
            key = (r.name or "").strip()
            if key:
                groups.setdefault(key, []).append(r)
        if not groups:
            continue

        active_names = roster_map.get(sid, set())
        # 未命中花名册姓名的组 = 别人的历史，改写为命名空间学号
        dead = [
            (name, grp) for name, grp in groups.items()
            if name not in active_names
        ]

        for dead_name, dead_rows in dead:
            grades = [grade_map.get(r.exam_id) for r in dead_rows]
            grades = [g for g in grades if g is not None]
            new_sid = _namespaced_sid(min(grades) if grades else None, sid)
            if new_sid == sid:
                continue
            if (
                db.query(SubjectScore.id)
                .filter(SubjectScore.student_id == new_sid)
                .first()
            ):
                stats["pending"].append(
                    {"new_sid": new_sid, "name": dead_name, "reason": "target_sid_exists"}
                )
                continue

            exam_ids = {r.exam_id for r in dead_rows}
            for r in dead_rows:
                r.student_id = new_sid
            stats["rekeyed_rows"] += len(dead_rows)
            stats["rekeyed_ids"] += 1
            stats["rekeyed_rows"] += (
                db.query(TotalScore)
                .filter(
                    TotalScore.student_id == sid,
                    TotalScore.exam_id.in_(exam_ids),
                )
                .update(
                    {TotalScore.student_id: new_sid},
                    synchronize_session=False,
                )
            )

            # 按姓名自动建链：花名册里唯一同名行（含 TMP- 占位行）
            cands = [
                (rsid, rgrade) for rsid, rname, rgrade in roster_rows
                if rsid != sid and rsid != new_sid
                and rname == dead_name and not _NS_PATTERN.match(rsid)
            ]
            if len(cands) != 1:
                stats["pending"].append({
                    "new_sid": new_sid,
                    "name": dead_name,
                    "reason": "ambiguous" if len(cands) > 1 else "no_candidate",
                })
                continue
            target_sid, target_grade = cands[0]
            existing = (
                db.query(StudentAlias)
                .filter(StudentAlias.student_id == new_sid)
                .first()
            )
            if existing:
                continue
            tgt_alias = (
                db.query(StudentAlias)
                .filter(StudentAlias.student_id == target_sid)
                .first()
            )
            if tgt_alias:
                identity_id = tgt_alias.identity_id
            else:
                identity_id = ensure_identity(
                    db, display_name=dead_name, commit=False
                )
                link_aliases(
                    db, identity_id, [(target_sid, target_grade)],
                    "collision_auto", commit=False,
                )
            link_aliases(
                db, identity_id, [(new_sid, min(grades) if grades else None)],
                "collision_auto", commit=False,
            )
            stats["linked"] += 1

    db.commit()
    return stats


if __name__ == "__main__":
    # 手工救援入口：仅当出现守门无法覆盖的既有撞号脏库时由人决策执行
    # （main.py 不再随启动自动跑本迁移，见 main.py 内说明）。
    from app.db.models import SessionLocal

    _db = SessionLocal()
    try:
        print(migrate_colliding_student_ids(_db))
    finally:
        _db.close()
