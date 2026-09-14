"""每日缺交记录 Excel 导出。

移植自原「作业跟踪」tracker.export_daily_report，改用 openpyxl（项目已依赖，
避免引入 pandas）。按学生聚合当天缺交科目/说明/特殊情况，按座号排序，
落盘到 <导出根>/<年>/<月>月/<日期>缺交记录.xlsx。

导出根目录用环境变量 HOMEWORK_EXPORT_DIR 配置，默认
~/.exam-tracker/homework_exports。
"""

import os
from datetime import datetime

from openpyxl import Workbook

from app.db.models import (
    ClassRoster,
    HomeworkRecord,
    SpecialRecord,
    get_db,
)
from app.db.sid_space import display_sid
from app.paths import DATA_DIR

EXPORT_DIR = os.environ.get(
    "HOMEWORK_EXPORT_DIR",
    os.path.join(DATA_DIR, "homework_exports"),
)

HEADERS = ["学号", "姓名", "缺交科目", "说明", "特殊情况"]


def _unique_join(values):
    seen = []
    for v in values:
        if v and v not in seen:
            seen.append(v)
    return "、".join(seen)


def export_daily_report(target_date, db=None):
    """导出某天的缺交记录 Excel。无任何记录则不生成文件，返回 None。

    名册按 active_grade 收口，避免高一+高二名册并存后日清单混入其他年级。
    """
    from app.homework.service import get_active_grade

    own_db = db is None
    if own_db:
        db = next(get_db())
    try:
        active_grade = get_active_grade(db)
        roster = {
            r.student_id: r
            for r in db.query(ClassRoster).filter(ClassRoster.grade == active_grade).all()
        }
        active_sids = set(roster.keys())

        # 缺交记录（含说明/特殊情况备注）——仅当前学年学生，避免串年级
        records = (
            db.query(HomeworkRecord)
            .filter(HomeworkRecord.date == target_date)
            .all()
        )
        records = [r for r in records if r.student_id in active_sids]
        specials = (
            db.query(SpecialRecord)
            .filter(SpecialRecord.date == target_date)
            .all()
        )
        specials = [s for s in specials if s.student_id in active_sids]
        if not records and not specials:
            return None

        # 按学生聚合
        agg = {}  # student_id -> {subjects, contents, specials}

        def ensure(sid):
            if sid not in agg:
                agg[sid] = {"subjects": [], "contents": [], "specials": []}
            return agg[sid]

        for r in records:
            entry = ensure(r.student_id)
            entry["subjects"].append(r.subject)
            if r.content:
                entry["contents"].append(r.content)
            if r.remark:
                entry["specials"].append(r.remark)
        for s in specials:
            entry = ensure(s.student_id)
            entry["specials"].append(s.type)

        rows = []
        for sid, data in agg.items():
            r = roster.get(sid)
            seat = r.seat_no if r else None
            rows.append(
                {
                    "seat": seat if seat is not None else 10**9,
                    # Excel 是给人看的：学号/姓名兜底剥掉届前缀（聚合 key 仍用原值）
                    "学号": (str(seat) if seat is not None else display_sid(sid)),
                    "姓名": r.name if r else display_sid(sid),
                    "缺交科目": _unique_join(data["subjects"]),
                    "说明": _unique_join(data["contents"]),
                    "特殊情况": _unique_join(data["specials"]),
                }
            )
        rows.sort(key=lambda x: x["seat"])

        wb = Workbook()
        ws = wb.active
        ws.append(HEADERS)
        for row in rows:
            ws.append([row[h] for h in HEADERS])

        dt = datetime.strptime(target_date, "%Y-%m-%d")
        month_dir = os.path.join(EXPORT_DIR, str(dt.year), f"{dt.month:02d}月")
        os.makedirs(month_dir, exist_ok=True)
        file_path = os.path.join(month_dir, f"{target_date}缺交记录.xlsx")
        wb.save(file_path)
        return file_path
    finally:
        if own_db:
            db.close()
