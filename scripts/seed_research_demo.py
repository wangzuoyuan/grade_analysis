"""关注回看合成数据。命令行只允许在本工作树 .test-data 中的空库运行。

先设置 EXAM_TRACKER_DIR 与 EXAM_TRACKER_BACKUP_DIR；不读取或复制生产数据。
"""
import os
from datetime import date, timedelta
from pathlib import Path


def seed_demo(db):
    from app.db import workspace_models as wm
    from app.db.models import Teacher, HomeworkSetting
    db.merge(Teacher(id=1, name="演示教师（虚拟数据）", school="合成数据演示学校", target_class_high1=6))
    db.merge(HomeworkSetting(key="active_grade", value="1"))
    year = wm.AcademicYear(name="2026-2027", start_date=date(2026,8,20), end_date=date(2027,7,31))
    db.add(year); db.flush()
    classroom = wm.AdministrativeClass(academic_year_id=year.id, grade=1, class_num=6, label="高一6班 · 虚拟演示")
    db.add(classroom); db.flush()
    db.add(wm.Term(academic_year_id=year.id, name="第一学期", start_date=date(2026,8,20), end_date=date(2027,1,31)))
    specs = [
        ("示例甲", [200,205,180], [.30,.31,.27], [.65,.66,.42]),
        ("示例乙", [195,200,320], [.30,.31,.50], [.65,.66,.70]),
        ("示例丙", [300,250,240], [.45,.40,.38], [.75,.70,.76]),
        ("示例丁", [200,250,270], [.30,.40,.44], [.65,.75,.80]),
        ("示例戊", [200,205,210], [.30,.31,.32], [.65,.66,None]),
        ("示例己", [200,205,None], [.30,.31,None], [.65,.66,.68]),
        ("示例庚", [450,460,430], [.72,.73,.68], [.74,.77,.69]),
        ("示例辛", [150,120,100], [.25,.20,.17], [.28,.23,.20]),
    ]
    exams = [("开学诊断",date(2026,8,31)),("九月月考",date(2026,9,15)),("阶段复查",date(2026,10,1))]
    ids = {}
    for seat,(name,ranks,overall,math) in enumerate(specs,1):
        person = wm.WsStudentIdentity(data_domain="homeroom", display_name=name)
        db.add(person); db.flush(); ids[name] = person.id
        db.add(wm.Enrollment(admin_class_id=classroom.id, identity_id=person.id, valid_from=date(2026,8,20), seat_no=seat, status="active"))
        for i,(exam,day) in enumerate(exams):
            common = dict(data_domain="homeroom", academic_year_id=year.id, class_ref_id=classroom.id,
                          identity_id=person.id, exam_name=exam, exam_date=day, source="research-synthetic-demo")
            db.add(wm.ScoreFact(**common, total_type="主三门", score=250 if ranks[i] is not None else None,
                               xueji_rank=ranks[i], grade_percentile=overall[i]))
            # 缺考行故意残留百分位，用于证明不会把残留值当作成绩。
            db.add(wm.ScoreFact(**common, subject="数学", score=80 if math[i] is not None else None,
                               grade_percentile=math[i] if math[i] is not None else .01))
            db.add(wm.ScoreFact(**common, subject="语文", score=85, grade_percentile=overall[i]))
    db.flush()
    all_ids=list(ids.values())
    for offset in range(7):
        for start in (date(2026,8,25),date(2026,9,24)):
            day = start+timedelta(days=offset)
            import json
            assignment=wm.HomeworkAssignment(data_domain="homeroom",academic_year_id=year.id,class_ref_id=classroom.id,
                subject="数学",homework_type="例题订正",assigned_date=day,batch_token=f"synthetic-{day}",expected_members_json=json.dumps(all_ids))
            db.add(assignment);db.flush()
            # 从原窗口4次缺交到后窗口1次，保持同科同批次数；不把其它人无记录视为无覆盖。
            if offset in ((0,1,2,3) if start.month==8 else (2,)):
                db.add(wm.HomeworkSubmission(assignment_id=assignment.id,person_id=ids["示例乙"],submission_status="missing"))
    def note(name,metric,baseline,problem,review,status="open"):
        unit="percentile" if metric and metric.startswith("subject:") else "rank"
        db.add(wm.WsStudentNote(data_domain="homeroom",person_id=ids[name],date=date(2026,9,16),category="谈话",
            content="虚拟数据：用于关注回看与复查验收",problem=problem,measures="每周面批与错题复述",subject_scope="数学",
            target_metric=metric,baseline_value={"metric":metric,"unit":unit,"value":baseline,"exam_name":"九月月考","exam_date":"2026-09-15","source":"auto"} if baseline is not None else None,
            start_date=date(2026,9,16),review_date=review,status=status,source="research-synthetic-demo"))
    note("示例甲","subject:数学",.66,"数学持续弱于总体",date(2026,10,5))
    note("示例乙","subject:数学",.66,"差距缩小仍需核对单科",date(2026,10,5))
    note("示例戊","subject:数学",.66,"复查场数学缺考",date(2026,10,5))
    note("示例己","total:主三门",None,"尚无可用基线",date(2026,10,6))
    note("示例庚","total:主三门",460,"临界段观察",date(2026,10,20))
    note("示例辛","total:主三门",120,"已关闭跟进仍保留事实",date(2026,10,5),status="done")
    db.commit()
    return {"academic_year_id":year.id,"class_id":classroom.id,"person_ids":ids,"exams":[e[0] for e in exams]}


def main():
    root=Path(__file__).resolve().parents[1]
    data=os.environ.get("EXAM_TRACKER_DIR")
    backup=os.environ.get("EXAM_TRACKER_BACKUP_DIR")
    if not data or not backup:
        raise SystemExit("必须先显式设置独立的数据与备份目录")
    for path in (Path(data).resolve(),Path(backup).resolve()):
        if not path.is_relative_to(root/'.test-data'):
            raise SystemExit("合成演示只允许使用本工作树 .test-data 下的目录")
        path.mkdir(parents=True,exist_ok=True)
    if (Path(data)/'db.sqlite').exists():
        raise SystemExit("拒绝覆盖已存在的库，请使用新的演示数据目录")
    # 验证完隔离路径才导入模型。
    from app.db.schema import ensure_app_schema
    ensure_app_schema()
    from app.db.models import SessionLocal
    with SessionLocal() as db:
        metadata=seed_demo(db)
    import json
    print(json.dumps(metadata,ensure_ascii=False))

if __name__=='__main__':main()
