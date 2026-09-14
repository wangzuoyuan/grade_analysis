"""作业跟踪 REST 路由（/api/homework 前缀）。

由原「作业跟踪」Flask app.py 全部端点迁移而来，数据访问改为 SQLAlchemy、
学生关联键改为真实学号。聚合查询委托给 service.py。
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from app.db.models import (
    ClassRoster,
    HomeworkCollection,
    HomeworkRecord,
    SpecialRecord,
    get_db,
)
from app.homework import service
from app.homework.export import export_daily_report
from app.homework.parser import (
    is_full_submission,
    is_subject_item,
    parse_homework_item,
    split_colon,
    split_names,
)

router = APIRouter(tags=["homework"])


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _filters(start_date, end_date, student, subject, db):
    """缺看板筛选：未给日期时回落到学期区间。"""
    sem = service.get_semester(db)
    return (
        start_date or sem["semester_start"],
        end_date or sem["semester_end"],
        student or None,
        subject or None,
    )


def _validated_scope(db, class_num: Optional[int] = None) -> tuple[int, int]:
    """解析并校验当前班主任作用域，不允许显式请求其他班级。"""
    grade = service.get_active_grade(db)
    bound_class_num = service.get_active_class_num(db, grade=grade)
    if bound_class_num is None:
        raise HTTPException(status_code=409, detail="当前年级尚未绑定班级，请先完成班级配置")
    if class_num is not None and int(class_num) != bound_class_num:
        raise HTTPException(status_code=409, detail="请求班级与当前教师绑定班级不一致")
    return grade, bound_class_num


def _validated_scope_class_num(db, class_num: Optional[int]) -> int:
    return _validated_scope(db, class_num)[1]


def _scope_roster_query(db, class_num: Optional[int] = None):
    grade, resolved_class_num = _validated_scope(db, class_num)
    return (
        db.query(ClassRoster).filter(
            ClassRoster.grade == grade,
            ClassRoster.class_num == resolved_class_num,
        ),
        grade,
        resolved_class_num,
    )


def _scoped_roster_row(db, student_id: str, class_num: Optional[int] = None):
    query, _, _ = _scope_roster_query(db, class_num)
    row = query.filter(ClassRoster.student_id == student_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="当前班级中不存在该学生")
    return row


# ─────────────────────────── 看板统计 ───────────────────────────

@router.get("/homework/kpi")
async def hw_kpi(start_date: str = "", end_date: str = "",
                 student: str = "", subject: str = "",
                 class_num: Optional[int] = None):
    db = next(get_db())
    try:
        class_num = _validated_scope_class_num(db, class_num)
        s, e, stu, sub = _filters(start_date, end_date, student, subject, db)
        return service.kpi(db, s, e, stu, sub, class_num=class_num)
    finally:
        db.close()


@router.get("/homework/trend")
async def hw_trend(start_date: str = "", end_date: str = "",
                   student: str = "", subject: str = "",
                   class_num: Optional[int] = None):
    db = next(get_db())
    try:
        class_num = _validated_scope_class_num(db, class_num)
        s, e, stu, sub = _filters(start_date, end_date, student, subject, db)
        return service.trend(db, s, e, stu, sub, class_num=class_num)
    finally:
        db.close()


@router.get("/homework/subjects")
async def hw_subjects(start_date: str = "", end_date: str = "",
                      student: str = "", subject: str = "",
                      class_num: Optional[int] = None):
    db = next(get_db())
    try:
        class_num = _validated_scope_class_num(db, class_num)
        s, e, stu, sub = _filters(start_date, end_date, student, subject, db)
        return service.subjects(db, s, e, stu, sub, class_num=class_num)
    finally:
        db.close()


@router.get("/homework/rankings")
async def hw_rankings(start_date: str = "", end_date: str = "",
                      student: str = "", subject: str = "", limit: int = 10,
                      class_num: Optional[int] = None):
    db = next(get_db())
    try:
        class_num = _validated_scope_class_num(db, class_num)
        s, e, stu, sub = _filters(start_date, end_date, student, subject, db)
        return service.rankings(db, s, e, stu, sub, limit, class_num=class_num)
    finally:
        db.close()


@router.get("/homework/warnings")
async def hw_warnings(class_num: Optional[int] = None):
    db = next(get_db())
    try:
        class_num = _validated_scope_class_num(db, class_num)
        sem = service.get_semester(db)
        return service.warnings(
            db, sem["semester_start"], sem["semester_end"], class_num=class_num
        )
    finally:
        db.close()


@router.get("/homework/correlation")
async def hw_correlation(class_num: Optional[int] = None, exam_id: Optional[int] = None,
                         total_type: str = "主三门", subject: str = ""):
    db = next(get_db())
    try:
        class_num = _validated_scope_class_num(db, class_num)
        try:
            return service.grade_correlation(
                db, class_num, exam_id, total_type, subject=subject or None
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        db.close()


@router.get("/homework/correlation/subjects")
async def hw_correlation_subjects(class_num: Optional[int] = None, exam_id: Optional[int] = None):
    db = next(get_db())
    try:
        class_num = _validated_scope_class_num(db, class_num)
        try:
            return service.subject_correlation_ranking(db, class_num, exam_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        db.close()


@router.get("/homework/student/{student_id}")
async def hw_student_summary(student_id: str, class_num: Optional[int] = None):
    """单个学生作业概况（供学生画像页作业卡片）。"""
    db = next(get_db())
    try:
        _, grade, resolved_class_num = _scope_roster_query(db, class_num)
        from app.analysis.identity import person_ids
        ids = person_ids(db, student_id)
        allowed = db.query(ClassRoster).filter(
            ClassRoster.student_id.in_(ids),
            ClassRoster.grade == grade,
            ClassRoster.class_num == resolved_class_num,
        ).first()
        if allowed is None:
            raise HTTPException(status_code=404, detail="当前班级中不存在该学生")
        return service.student_summary(
            db, student_id=student_id, class_num=resolved_class_num
        )
    finally:
        db.close()


@router.get("/weekly-focus")
async def weekly_focus(class_num: Optional[int] = None):
    """本周关注名单（仪表盘主动提醒）。"""
    db = next(get_db())
    try:
        class_num = _validated_scope_class_num(db, class_num)
        return service.weekly_focus(db, class_num)
    finally:
        db.close()


# ─────────────────────────── 录入 ───────────────────────────

class RecordsPayload(BaseModel):
    raw_text: str
    date: Optional[str] = None
    mode: str = "by_student"  # by_student | by_subject


def _find_student_id(db, name, grade: int, class_num: int):
    """姓名只在当前行政班内解析，避免同名跨班写错学生。"""
    row = db.query(ClassRoster).filter(
        ClassRoster.name == name,
        ClassRoster.grade == grade,
        ClassRoster.class_num == class_num,
    ).first()
    return row.student_id if row else None


@router.post("/homework/records")
async def hw_add_records(payload: RecordsPayload, class_num: Optional[int] = None):
    if not payload.raw_text.strip():
        raise HTTPException(400, "请输入记录内容")
    date = payload.date or _today()
    db = next(get_db())
    added = 0
    errors = []
    try:
        grade, class_num = _validated_scope(db, class_num)
        lines = [l.strip() for l in payload.raw_text.split("\n") if l.strip()]
        for line in lines:
            parts = split_colon(line)
            if not parts:
                errors.append(f"格式错误: {line}")
                continue
            left, right = parts

            if payload.mode == "by_subject":
                # 学科/情况：学生1、学生2
                names = split_names(right)
                if not is_subject_item(left):
                    for name in names:
                        sid = _find_student_id(db, name, grade, class_num)
                        if not sid:
                            errors.append(f"找不到学生: {name}")
                            continue
                        db.add(SpecialRecord(student_id=sid, date=date, type=left, note=None))
                        added += 1
                else:
                    parsed = parse_homework_item(left)
                    if not parsed:
                        errors.append(f"无法识别科目: {left}")
                        continue
                    subj, content, remark = parsed
                    if is_full_submission(right):
                        # 收交台账：「数学：全交」记一条收交事件（幂等），
                        # 供连续缺交预警构建完整时间轴
                        existing = db.query(HomeworkCollection).filter(
                            HomeworkCollection.date == date,
                            HomeworkCollection.subject == subj,
                            HomeworkCollection.grade == grade,
                            HomeworkCollection.class_num == class_num,
                        ).first()
                        if not existing:
                            db.add(HomeworkCollection(date=date, subject=subj,
                                                      grade=grade, class_num=class_num))
                            added += 1
                        continue
                    for name in names:
                        sid = _find_student_id(db, name, grade, class_num)
                        if not sid:
                            errors.append(f"找不到学生: {name}")
                            continue
                        db.add(HomeworkRecord(student_id=sid, date=date, subject=subj,
                                              content=content, remark=remark))
                        added += 1
            else:
                # 学生：科目1、科目2 / 情况
                name = left
                sid = _find_student_id(db, name, grade, class_num)
                if not sid:
                    errors.append(f"找不到学生: {name}")
                    continue
                for item in split_names(right):
                    if not is_subject_item(item):
                        db.add(SpecialRecord(student_id=sid, date=date, type=item, note=None))
                        added += 1
                    else:
                        parsed = parse_homework_item(item)
                        if not parsed:
                            continue
                        subj, content, remark = parsed
                        db.add(HomeworkRecord(student_id=sid, date=date, subject=subj,
                                              content=content, remark=remark))
                        added += 1
        db.commit()
        if added > 0:
            export_daily_report(date, db=db)
        return {"success": True, "added_count": added, "errors": errors}
    finally:
        db.close()


class SpecialPayload(BaseModel):
    raw_text: str
    date: Optional[str] = None
    mode: str = "by_student"  # by_student | by_type


@router.post("/homework/special-records")
async def hw_add_special(payload: SpecialPayload, class_num: Optional[int] = None):
    if not payload.raw_text.strip():
        raise HTTPException(400, "请输入记录内容")
    date = payload.date or _today()
    db = next(get_db())
    added = 0
    errors = []
    try:
        grade, class_num = _validated_scope(db, class_num)
        for line in [l.strip() for l in payload.raw_text.split("\n") if l.strip()]:
            parts = split_colon(line)
            if not parts:
                errors.append(f"格式错误: {line}")
                continue
            left, right = parts
            if payload.mode == "by_type":
                for name in split_names(right):
                    sid = _find_student_id(db, name, grade, class_num)
                    if not sid:
                        errors.append(f"找不到学生: {name}")
                        continue
                    db.add(SpecialRecord(student_id=sid, date=date, type=left, note=None))
                    added += 1
            else:
                sid = _find_student_id(db, left, grade, class_num)
                if not sid:
                    errors.append(f"找不到学生: {left}")
                    continue
                for rec_type in split_names(right):
                    db.add(SpecialRecord(student_id=sid, date=date, type=rec_type, note=None))
                    added += 1
        db.commit()
        return {"success": True, "added_count": added, "errors": errors}
    finally:
        db.close()


@router.get("/homework/special-records")
async def hw_get_special(date: str = "", class_num: Optional[int] = None):
    target = date or _today()
    db = next(get_db())
    try:
        grade, class_num = _validated_scope(db, class_num)
        rows = (
            db.query(SpecialRecord, ClassRoster)
            .join(ClassRoster, ClassRoster.student_id == SpecialRecord.student_id)
            .filter(
                SpecialRecord.date == target,
                ClassRoster.grade == grade,
                ClassRoster.class_num == class_num,
            )
            .order_by(SpecialRecord.type, ClassRoster.name)
            .all()
        )
        return [
            {"id": sr.id, "name": roster.name, "date": sr.date, "type": sr.type, "note": sr.note}
            for sr, roster in rows
        ]
    finally:
        db.close()


@router.delete("/homework/special-records/{record_id}")
async def hw_delete_special(record_id: int, class_num: Optional[int] = None):
    db = next(get_db())
    try:
        grade, class_num = _validated_scope(db, class_num)
        record = (
            db.query(SpecialRecord)
            .join(ClassRoster, ClassRoster.student_id == SpecialRecord.student_id)
            .filter(
                SpecialRecord.id == record_id,
                ClassRoster.grade == grade,
                ClassRoster.class_num == class_num,
            )
            .first()
        )
        if record is None:
            raise HTTPException(status_code=404, detail="当前班级中不存在该记录")
        db.delete(record)
        db.commit()
        return {"success": True}
    finally:
        db.close()


# ─────────────────────────── 记录管理 ───────────────────────────

@router.get("/homework/manage/records")
async def hw_manage_list(date: str = "", student: str = "", subject: str = "",
                         start_date: str = "", end_date: str = "",
                         class_num: Optional[int] = None):
    from sqlalchemy import or_
    from app.homework.service import _subject_keywords

    db = next(get_db())
    try:
        grade, class_num = _validated_scope(db, class_num)
        rec_q = (
            db.query(HomeworkRecord, ClassRoster)
            .join(ClassRoster, ClassRoster.student_id == HomeworkRecord.student_id)
            .filter(ClassRoster.grade == grade, ClassRoster.class_num == class_num)
        )
        sp_q = (
            db.query(SpecialRecord, ClassRoster)
            .join(ClassRoster, ClassRoster.student_id == SpecialRecord.student_id)
            .filter(ClassRoster.grade == grade, ClassRoster.class_num == class_num)
        )
        if start_date and end_date:
            rec_q = rec_q.filter(HomeworkRecord.date >= start_date, HomeworkRecord.date <= end_date)
            sp_q = sp_q.filter(SpecialRecord.date >= start_date, SpecialRecord.date <= end_date)
        elif date:
            rec_q = rec_q.filter(HomeworkRecord.date == date)
            sp_q = sp_q.filter(SpecialRecord.date == date)
        if student:
            rec_q = rec_q.filter(ClassRoster.name.like(f"%{student}%"))
            sp_q = sp_q.filter(ClassRoster.name.like(f"%{student}%"))

        # 按学科过滤：只看该科缺交记录，不含无学科的特殊记录
        include_specials = True
        if subject:
            include_specials = False
            keywords = _subject_keywords(subject)
            if keywords:
                rec_q = rec_q.filter(or_(*[HomeworkRecord.subject.like(f"%{k}%") for k in keywords]))
            else:
                rec_q = rec_q.filter(HomeworkRecord.subject == subject)

        records = [
            {"id": r.id, "name": roster.name, "date": r.date, "subject": r.subject,
             "content": r.content or "", "remark": r.remark or "", "is_special": False}
            for r, roster in rec_q.order_by(HomeworkRecord.date.desc()).limit(200).all()
        ]
        specials = []
        if include_specials:
            specials = [
                {"id": sr.id, "name": roster.name, "date": sr.date, "subject": "",
                 "content": sr.note or "", "remark": sr.type, "is_special": True}
                for sr, roster in sp_q.order_by(SpecialRecord.date.desc()).limit(200).all()
            ]
        allr = sorted(records + specials, key=lambda x: (x["date"], x["name"]), reverse=True)
        return allr
    finally:
        db.close()


class UpdateRecordPayload(BaseModel):
    subject: str = ""
    content: str = ""
    remark: str = ""


@router.put("/homework/manage/records/{record_id}")
async def hw_manage_update(record_id: int, payload: UpdateRecordPayload,
                           class_num: Optional[int] = None):
    db = next(get_db())
    try:
        grade, class_num = _validated_scope(db, class_num)
        rec = (
            db.query(HomeworkRecord)
            .join(ClassRoster, ClassRoster.student_id == HomeworkRecord.student_id)
            .filter(
                HomeworkRecord.id == record_id,
                ClassRoster.grade == grade,
                ClassRoster.class_num == class_num,
            )
            .first()
        )
        if not rec:
            raise HTTPException(404, "当前班级中不存在该记录")
        rec.subject = payload.subject
        rec.content = payload.content
        rec.remark = payload.remark
        db.commit()
        export_daily_report(rec.date, db=db)
        return {"success": True}
    finally:
        db.close()


@router.delete("/homework/manage/records/{record_id}")
async def hw_manage_delete(record_id: int, class_num: Optional[int] = None):
    db = next(get_db())
    try:
        grade, class_num = _validated_scope(db, class_num)
        rec = (
            db.query(HomeworkRecord)
            .join(ClassRoster, ClassRoster.student_id == HomeworkRecord.student_id)
            .filter(
                HomeworkRecord.id == record_id,
                ClassRoster.grade == grade,
                ClassRoster.class_num == class_num,
            )
            .first()
        )
        if rec is None:
            raise HTTPException(404, "当前班级中不存在该记录")
        rec_date = rec.date if rec else None
        if rec:
            db.delete(rec)
            db.commit()
        if rec_date:
            export_daily_report(rec_date, db=db)
        return {"success": True}
    finally:
        db.close()


# ─────────────────────────── 花名册 ───────────────────────────

@router.get("/homework/roster")
async def hw_roster(class_num: Optional[int] = None):
    db = next(get_db())
    try:
        query, _, _ = _scope_roster_query(db, class_num)
        rows = (
            query
            .order_by(ClassRoster.excluded.asc(), ClassRoster.seat_no.asc())
            .all()
        )
        out = []
        for r in rows:
            count = db.query(HomeworkRecord).filter(
                HomeworkRecord.student_id == r.student_id
            ).count()
            out.append({
                "student_id": r.student_id, "name": r.name, "seat_no": r.seat_no,
                "gender": r.gender, "excluded": r.excluded, "class_num": r.class_num,
                "grade": r.grade,
                "record_count": count,
            })
        return out
    finally:
        db.close()


class AddStudentPayload(BaseModel):
    name: str
    student_id: Optional[str] = None
    seat_no: Optional[int] = None
    gender: Optional[str] = None
    class_num: Optional[int] = None
    grade: Optional[int] = None


@router.post("/homework/roster")
async def hw_add_student(payload: AddStudentPayload):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "姓名不能为空")
    db = next(get_db())
    try:
        grade, class_num = _validated_scope(db, payload.class_num)
        if payload.grade is not None and int(payload.grade) != grade:
            raise HTTPException(status_code=409, detail="请求年级与当前作用域不一致")
        if db.query(ClassRoster).filter(
            ClassRoster.name == name,
            ClassRoster.grade == grade,
            ClassRoster.class_num == class_num,
        ).first():
            raise HTTPException(400, f"学生 {name} 已存在")
        if payload.student_id:
            sid = payload.student_id.strip()
            if db.query(ClassRoster).filter(ClassRoster.student_id == sid).first():
                raise HTTPException(400, f"学号 {sid} 已存在")
            # 撞号防呆：该学号在历史成绩中已属另一位学生 → 拒绝，静默接受
            # 会让跨学年画像串人（分班重新编号撞历史旧号的典型形态）
            from app.db.models import SubjectScore

            score_name = (
                db.query(SubjectScore.name)
                .filter(
                    SubjectScore.student_id == sid,
                    SubjectScore.name.isnot(None),
                )
                .first()
            )
            if score_name and (score_name[0] or "").strip() != name:
                raise HTTPException(
                    400,
                    f"学号 {sid} 在历史成绩中属于「{score_name[0]}」，"
                    f"与「{name}」不一致，请核对该学号",
                )
        else:
            token = str(payload.seat_no) if payload.seat_no is not None else name
            base_sid = f"HW-{grade}-{class_num}-{token}"
            sid = base_sid
            suffix = 2
            while db.query(ClassRoster).filter(ClassRoster.student_id == sid).first():
                sid = f"{base_sid}-{suffix}"
                suffix += 1
        db.add(ClassRoster(student_id=sid, name=name, class_num=class_num,
                           seat_no=payload.seat_no, gender=payload.gender, excluded=0,
                           grade=grade))
        db.commit()
        return {"success": True, "student_id": sid, "grade": grade}
    finally:
        db.close()


@router.delete("/homework/roster/{student_id}")
async def hw_delete_student(student_id: str, class_num: Optional[int] = None):
    db = next(get_db())
    try:
        _scoped_roster_row(db, student_id, class_num)
        dates = [
            r[0] for r in db.query(HomeworkRecord.date)
            .filter(HomeworkRecord.student_id == student_id).distinct().all()
        ]
        db.query(HomeworkRecord).filter(HomeworkRecord.student_id == student_id).delete()
        db.query(SpecialRecord).filter(SpecialRecord.student_id == student_id).delete()
        db.query(ClassRoster).filter(ClassRoster.student_id == student_id).delete()
        db.commit()
        for d in dates:
            export_daily_report(d, db=db)
        return {"success": True, "affected_dates": len(dates)}
    finally:
        db.close()


@router.put("/homework/roster/{student_id}/toggle-excluded")
async def hw_toggle_excluded(student_id: str, class_num: Optional[int] = None):
    db = next(get_db())
    try:
        r = _scoped_roster_row(db, student_id, class_num)
        r.excluded = 0 if r.excluded else 1
        db.commit()
        return {"success": True, "excluded": r.excluded}
    finally:
        db.close()


# ─────────────────────────── 学期配置 ───────────────────────────

class SemesterPayload(BaseModel):
    semester_start: Optional[str] = None
    semester_end: Optional[str] = None
    semester_name: Optional[str] = None


@router.get("/homework/semester")
async def hw_get_semester(class_num: Optional[int] = None):
    db = next(get_db())
    try:
        _validated_scope(db, class_num)
        return service.get_semester(db)
    finally:
        db.close()


@router.put("/homework/semester")
async def hw_set_semester(payload: SemesterPayload, class_num: Optional[int] = None):
    db = next(get_db())
    try:
        _validated_scope(db, class_num)
        try:
            return service.set_semester(db, payload.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    finally:
        db.close()


class NewSemesterPayload(BaseModel):
    name: str
    start_date: str
    end_date: str
    make_current: bool = False


@router.get("/homework/semesters")
async def hw_list_semesters():
    db = next(get_db())
    try:
        return service.list_semesters(db)
    finally:
        db.close()


@router.post("/homework/semesters")
async def hw_add_semester(payload: NewSemesterPayload):
    db = next(get_db())
    try:
        try:
            return service.add_semester(
                db, payload.name, payload.start_date, payload.end_date, payload.make_current
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    finally:
        db.close()


@router.put("/homework/semesters/{semester_id}/current")
async def hw_set_current_semester(semester_id: int):
    db = next(get_db())
    try:
        if not service.set_current_semester(db, semester_id):
            raise HTTPException(404, "学期不存在")
        return service.get_semester(db)
    finally:
        db.close()
