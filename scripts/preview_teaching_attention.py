"""Fresh synthetic preview. Explicit new data/backup paths required by base seeder."""
import json
from datetime import date, timedelta
from uuid import uuid4


def add_attention_examples():
    from app.db.models import SessionLocal
    from app.db import workspace_models as wm
    db = SessionLocal()
    year = db.query(wm.AcademicYear).first()
    db.add(wm.WsHomeworkSemester(academic_year_id=year.id, name="合成关注学期",
        start_date=year.start_date, end_date=year.end_date, is_current=1, mode="manual"))
    for cls in db.query(wm.TeachingClass).all():
        ids = [m.identity_id for m in db.query(wm.TeachingClassMember).filter_by(teaching_class_id=cls.id)]
        for offset in (2, 1):
            batch = wm.HomeworkAssignment(data_domain="teaching", academic_year_id=year.id,
                class_ref_id=cls.id, subject=cls.subject, homework_type="合成近期练习",
                assigned_date=date.today()-timedelta(days=offset), batch_token=uuid4().hex,
                expected_members_json=json.dumps(ids))
            db.add(batch); db.flush()
            for pid in ids:
                db.add(wm.HomeworkSubmission(assignment_id=batch.id,person_id=pid,submission_status="missing"))
    db.commit(); db.close()


if __name__ == "__main__":
    from preview_student_homework import main
    main()  # refuses existing paths, sets explicit environment before importing app
    add_attention_examples()
