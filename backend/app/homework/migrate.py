"""一次性数据迁移：旧「作业跟踪」homework.db → 成绩库 ~/.exam-tracker/db.sqlite。

把旧库的 students / records / special_records / settings 搬入新表
ClassRoster / HomeworkRecord / SpecialRecord / HomeworkSetting。学生标识
从「班内序号」改为按姓名匹配成绩库当前绑定行政班的真实学号 student_id；
匹配不到的学生用占位学号 HW-<座号或姓名> 并在结尾报告。

幂等：每次运行先清空作业相关表再重新导入，可反复执行。

用法：
    HOMEWORK_DB_PATH=/path/to/homework.db \
    python -m app.homework.migrate [--class-num 班号]
默认源路径指向旧仓库 06_工具项目/作业跟踪/homework.db。
"""

import argparse
import os
import sqlite3

from app.db.models import (
    ClassRoster,
    HomeworkRecord,
    HomeworkSetting,
    SpecialRecord,
    SubjectScore,
    Exam,
    get_db,
)

DEFAULT_SOURCE = os.path.expanduser(
    "~/Documents/Monster/班主任/06_工具项目/作业跟踪/homework.db"
)


def _grade_name_to_student_id(db, class_num, grade):
    """成绩库该班「姓名 → 真实学号」映射。重名时取首个并不致命，
    因为单班作业花名册姓名唯一。"""
    rows = (
        db.query(SubjectScore.name, SubjectScore.student_id)
        .join(Exam, Exam.id == SubjectScore.exam_id)
        .filter(SubjectScore.class_num == class_num, Exam.grade == grade)
        .distinct()
        .all()
    )
    mapping = {}
    for name, student_id in rows:
        if name and student_id and name not in mapping:
            mapping[name] = student_id
    return mapping


def migrate(source_path=DEFAULT_SOURCE, class_num=None):
    if not os.path.exists(source_path):
        raise SystemExit(f"找不到源数据库：{source_path}")

    src = sqlite3.connect(source_path)
    src.row_factory = sqlite3.Row

    db = next(get_db())
    try:
        from app.homework.service import get_active_class_num, get_active_grade

        active_grade = get_active_grade(db)
        if class_num is None:
            class_num = get_active_class_num(db, active_grade)
        if class_num is None:
            raise SystemExit("当前年级尚未绑定班级，请通过 --class-num 明确指定")
        name_to_sid = _grade_name_to_student_id(db, class_num, active_grade)

        # 幂等：先清空作业相关表
        db.query(HomeworkRecord).delete()
        db.query(SpecialRecord).delete()
        db.query(ClassRoster).delete()
        # active_grade 是全局班级作用域，不属于旧作业库的学期配置。
        # 迁移时必须保留，否则刚按当前年级导入的数据会立即失去同一作用域来源。
        db.query(HomeworkSetting).filter(HomeworkSetting.key != "active_grade").delete(
            synchronize_session=False
        )
        db.commit()

        # 1) 花名册：旧 students → ClassRoster
        unmatched = []
        old_id_to_sid = {}  # 旧 students.id → 新 student_id
        for row in src.execute(
            "SELECT id, student_no, name, gender, "
            "COALESCE(excluded, 0) AS excluded FROM students"
        ).fetchall():
            name = row["name"]
            sid = name_to_sid.get(name)
            if not sid:
                # 占位学号，便于人工后续修正
                sid = f"HW-{row['student_no'] or name}"
                unmatched.append(name)
            old_id_to_sid[row["id"]] = sid

            seat_no = None
            if row["student_no"] is not None:
                try:
                    seat_no = int(str(row["student_no"]).strip())
                except (TypeError, ValueError):
                    seat_no = None

            db.merge(
                ClassRoster(
                    student_id=sid,
                    name=name,
                    class_num=class_num,
                    grade=active_grade,
                    seat_no=seat_no,
                    gender=row["gender"],
                    excluded=int(row["excluded"] or 0),
                )
            )
        db.commit()

        # 2) 缺交记录
        rec_count = 0
        for row in src.execute(
            "SELECT student_id, date, subject, content, remark FROM records"
        ).fetchall():
            sid = old_id_to_sid.get(row["student_id"])
            if not sid:
                continue
            db.add(
                HomeworkRecord(
                    student_id=sid,
                    date=row["date"],
                    subject=row["subject"],
                    content=row["content"],
                    remark=row["remark"],
                )
            )
            rec_count += 1

        # 3) 特殊记录
        sp_count = 0
        for row in src.execute(
            "SELECT student_id, date, type, note FROM special_records"
        ).fetchall():
            sid = old_id_to_sid.get(row["student_id"])
            if not sid:
                continue
            db.add(
                SpecialRecord(
                    student_id=sid,
                    date=row["date"],
                    type=row["type"],
                    note=row["note"],
                )
            )
            sp_count += 1
        db.commit()

        # 4) 学期配置
        set_count = 0
        for row in src.execute("SELECT key, value FROM settings").fetchall():
            if row["key"] in ("semester_start", "semester_end", "semester_name"):
                db.merge(HomeworkSetting(key=row["key"], value=row["value"]))
                set_count += 1
        db.commit()

        roster_count = db.query(ClassRoster).count()
        print("迁移完成：")
        print(f"  花名册 ClassRoster   : {roster_count} 人")
        print(f"  缺交记录 HomeworkRecord: {rec_count} 条")
        print(f"  特殊记录 SpecialRecord : {sp_count} 条")
        print(f"  学期配置 HomeworkSetting: {set_count} 项")
        if unmatched:
            print(f"  ⚠ 成绩库未匹配到学号（用占位 HW- 学号）: {unmatched}")
        else:
            print("  ✓ 全部学生姓名均匹配到成绩库真实学号")
    finally:
        src.close()
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="作业跟踪数据迁移")
    parser.add_argument(
        "--source",
        default=os.environ.get("HOMEWORK_DB_PATH", DEFAULT_SOURCE),
        help="旧 homework.db 路径",
    )
    parser.add_argument("--class-num", type=int, help="作业花名册所属班号；不填时使用当前绑定班级")
    args = parser.parse_args()
    migrate(args.source, args.class_num)
