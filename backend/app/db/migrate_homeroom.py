"""班主任版数据层迁移：建表 + 给 class_roster 补 grade/status 列 + 给
student_change_log 补作用域列 + 写入 active_grade。

幂等：可安全地在每次启动时调用。
  - create_all 对已存在的表（student_identity / student_alias）为 no-op，
    只补建 imported_history 等缺失表。
  - class_roster.grade / status 与 student_change_log.grade / class_num 列
    用 PRAGMA table_info 门控：有则跳过，无则 ALTER ADD COLUMN。注意：
    不读 homework_setting.schema_version 做判断——历史遗留库的
    schema_version 可能是 'teaching_v1' 但 grade 列实际不存在，
    PRAGMA 才是真相之源。
  - active_grade / schema_version 走 homework_setting 的 KV merge（沿用
    get_semester / set_semester 的 upsert 模式）。

对「脏库」健壮：即便存在孤立的 teaching_class / teaching_class_member 表、
class_roster 多出 class_label 列，本函数也不会抛错。

用法：
    python -m app.db.migrate_homeroom
或在代码中：
    from app.db.migrate_homeroom import migrate
    result = migrate(engine=None)  # None 表示用 models.py 的默认 engine
"""

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    ClassRoster,
    Exam,
    HomeworkSetting,
    engine as _default_engine,
)


def migrate(engine=None) -> dict:
    """执行班主任版数据层迁移，返回操作摘要 dict。永不抛错（脏库也安全）。"""
    engine = engine or _default_engine
    created_tables: list[str] = []
    added_grade_column = False
    backfilled = 0
    active_grade = 1
    schema_version = "homeroom_v1"

    # (b) 建缺失表：create_all 对已存在表为 no-op
    try:
        # 先记录 create 前已有的表，便于事后算出实际新建了哪些
        with engine.connect() as conn:
            before = {
                row[0]
                for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            }
        Base.metadata.create_all(bind=engine)
        with engine.connect() as conn:
            after = {
                row[0]
                for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            }
        created_tables = sorted(after - before)
    except Exception as e:  # noqa: BLE001
        print(f"[migrate_homeroom] create_all skipped: {e}")

    # (c) PRAGMA 门控：给 class_roster 补 grade / status 列（核心幂等点）
    added_status_column = False
    added_changelog_columns: list[str] = []
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("PRAGMA table_info(class_roster)")).fetchall()
            cols = [r[1] for r in rows]
            if "grade" not in cols:
                conn.execute(text("ALTER TABLE class_roster ADD COLUMN grade INTEGER"))
                added_grade_column = True
            if "status" not in cols:
                # 在班状态（学生管理归档用）：旧库缺省 NULL 一律按在班处理
                conn.execute(text("ALTER TABLE class_roster ADD COLUMN status VARCHAR(20)"))
                added_status_column = True

            # student_change_log 作用域列：预览版本可能已建表（缺 grade/class_num）
            log_rows = conn.execute(text("PRAGMA table_info(student_change_log)")).fetchall()
            log_cols = [r[1] for r in log_rows]
            if log_cols:  # 表不存在时 create_all 已按新结构建出
                for col in ("grade", "class_num"):
                    if col not in log_cols:
                        conn.execute(
                            text(f"ALTER TABLE student_change_log ADD COLUMN {col} INTEGER")
                        )
                        added_changelog_columns.append(col)
    except Exception as e:  # noqa: BLE001
        print(f"[migrate_homeroom] roster column gate skipped: {e}")

    # (d)/(e)/(f) 回填 grade、写入 active_grade 与 schema_version 标记
    # 注意：必须绑定到传入的 engine（而非模块级 SessionLocal），否则用临时
    # engine 调 migrate() 时 KV 写入会落到默认库，与 create_all/ALTER 不一致。
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        # (d) 回填 grade：默认取库内最大 Exam.grade，没有则 1
        default_grade = 1
        try:
            max_exam_grade = db.query(Exam.grade).order_by(Exam.grade.desc()).first()
            if max_exam_grade and max_exam_grade[0] is not None:
                default_grade = int(max_exam_grade[0])
        except Exception as e:  # noqa: BLE001
            print(f"[migrate_homeroom] Exam.grade probe skipped: {e}")

        try:
            result = db.execute(
                text("UPDATE class_roster SET grade = :g WHERE grade IS NULL"),
                {"g": default_grade},
            )
            backfilled = int(result.rowcount or 0)
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            print(f"[migrate_homeroom] grade backfill skipped: {e}")

        # (e) active_grade：优先取已有 KV，否则取 class_roster.grade 最大值，否则 Exam，否则 1
        try:
            existing = (
                db.query(HomeworkSetting).filter(HomeworkSetting.key == "active_grade").first()
            )
            if existing and existing.value:
                try:
                    active_grade = int(existing.value)
                except (TypeError, ValueError):
                    active_grade = _compute_default_grade(db, default_grade)
                    db.merge(HomeworkSetting(key="active_grade", value=str(active_grade)))
                    db.commit()
            else:
                active_grade = _compute_default_grade(db, default_grade)
                db.merge(HomeworkSetting(key="active_grade", value=str(active_grade)))
                db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            print(f"[migrate_homeroom] active_grade upsert skipped: {e}")
            active_grade = _compute_default_grade(db, default_grade)

        # (f) schema_version 标记（信息性；PRAGMA 才是真守门人）
        try:
            db.merge(HomeworkSetting(key="schema_version", value=schema_version))
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            print(f"[migrate_homeroom] schema_version marker skipped: {e}")
    finally:
        db.close()

    summary = {
        "created": created_tables,
        "added_grade_column": added_grade_column,
        "added_status_column": added_status_column,
        "added_changelog_columns": added_changelog_columns,
        "backfilled": backfilled,
        "active_grade": active_grade,
        "schema_version": schema_version,
    }
    print(
        "[migrate_homeroom] "
        f"created={created_tables} "
        f"added_grade_column={added_grade_column} "
        f"added_status_column={added_status_column} "
        f"added_changelog_columns={added_changelog_columns} "
        f"backfilled={backfilled} "
        f"active_grade={active_grade} "
        f"schema_version={schema_version}"
    )
    return summary


def _compute_default_grade(db, fallback: int) -> int:
    """active_grade 默认值：class_roster.grade 最大值 → Exam.grade 最大值 → 1。"""
    try:
        row = db.query(ClassRoster.grade).filter(ClassRoster.grade.isnot(None)).order_by(
            ClassRoster.grade.desc()
        ).first()
        if row and row[0] is not None:
            return int(row[0])
    except Exception:  # noqa: BLE001
        pass
    return fallback


if __name__ == "__main__":
    migrate()
