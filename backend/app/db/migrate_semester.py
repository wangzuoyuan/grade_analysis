"""学期配置升级迁移：旧 homework_setting KV 的单学期配置迁入
homework_semester 表（幂等，启动时执行）。

与旧单学期行为的差异：迁入的历史学期不设为当前（is_current=0），
当前学期由 service.get_semester 的按日自动推算兜底——若把旧学期种成
当前，部署更新后新学期的记录仍会被旧学期窗口过滤（看板全 0 的根因）。
"""

from app.db.models import (
    Base,
    HomeworkSemester,
    HomeworkSetting,
    SessionLocal,
    engine,
)


def migrate_semester_table():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(HomeworkSemester).count() > 0:
            return
        settings = {
            row.key: row.value
            for row in db.query(HomeworkSetting).filter(
                HomeworkSetting.key.in_(
                    ["semester_start", "semester_end", "semester_name"]
                )
            ).all()
        }
        start = settings.get("semester_start")
        end = settings.get("semester_end")
        if not start or not end:
            return
        name = settings.get("semester_name") or f"{start} 至 {end}"
        db.add(HomeworkSemester(name=name, start_date=start, end_date=end, is_current=0))
        db.commit()
    finally:
        db.close()
