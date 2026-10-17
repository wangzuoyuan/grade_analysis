"""Create a fresh, explicit personal-analysis preview using synthetic students only."""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--backup-dir', required=True)
    args = parser.parse_args()
    data = Path(args.data_dir).resolve()
    backups = Path(args.backup_dir).resolve()
    if data.exists() or backups.exists():
        raise SystemExit('Preview data and backup directories must both be new; refusing overwrite')
    data.mkdir(parents=True, mode=0o700)
    backups.mkdir(parents=True, mode=0o700)
    os.environ['EXAM_TRACKER_DIR'] = str(data)
    os.environ['EXAM_TRACKER_BACKUP_DIR'] = str(backups)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
    from datetime import date, timedelta
    from app.db.schema import ensure_app_schema
    from app.db.models import SessionLocal, Teacher, HomeworkSetting
    from app.db import workspace_models as wm

    ensure_app_schema()
    db = SessionLocal()
    db.add(Teacher(id=1, name='合成演示教师', target_class_high2=6))
    db.add(HomeworkSetting(key='active_grade', value='2'))
    year = wm.AcademicYear(name='2026-2027', start_date=date(2026, 9, 1), end_date=date(2027, 8, 31))
    db.add(year)
    db.flush()
    classes, identities = {}, {}
    for grade in (1, 2, 3):
        cls = wm.TeachingClass(academic_year_id=year.id, grade=grade, subject='物理', label=f'合成高{grade}教学班')
        db.add(cls)
        db.flush()
        classes[grade] = cls.id
        people = []
        for i, label in enumerate(('甲', '乙', '丙')):
            person = wm.WsStudentIdentity(data_domain='teaching', display_name=f'合成高{grade}学生{label}')
            db.add(person)
            db.flush()
            people.append(person.id)
            db.add(wm.WsStudentAlias(identity_id=person.id, data_domain='teaching', academic_year_id=year.id,
                alias_scope=str(year.id), alias_value=f'SYN{grade}{i+1:03d}', source='synthetic-preview'))
            db.add(wm.TeachingClassMember(teaching_class_id=cls.id, identity_id=person.id, valid_from=date(2026, 9, 1)))
        identities[grade] = people
        for i in range(8):
            day = date(2026, 9, 8) + timedelta(days=i*5)
            exam = wm.TeachingExamScope(academic_year_id=year.id, grade=grade, subject='物理',
                exam_name=f'合成高{grade}测评{i+1}', exam_date=day)
            db.add(exam)
            db.flush()
            for j, pid in enumerate(people):
                achievement = [52, 57, 60, 66, 59, 70, 75, 79][i] if j == 0 else 85-i*3 if j == 1 else 60+i
                db.add(wm.ScoreFact(data_domain='teaching', academic_year_id=year.id, class_ref_id=cls.id,
                    identity_id=pid, subject='物理', exam_name=exam.exam_name, exam_date=day,
                    teaching_exam_id=exam.id, score=80-i, grade_score=achievement if grade > 1 and j != 2 else None,
                    grade_percentile=(100-achievement)/100 if grade == 1 and j != 2 else None, source='synthetic-preview'))
            for k in range(8):
                batch = wm.HomeworkAssignment(data_domain='teaching', academic_year_id=year.id, class_ref_id=cls.id,
                    subject='物理', homework_type='合成练习', assigned_date=day-timedelta(days=4-k//2),
                    batch_token=f'synthetic-{grade}-{i}-{k}', expected_members_json=json.dumps(people))
                db.add(batch)
                db.flush()
                for j, pid in enumerate(people):
                    submitted = j == 1 or k < [3, 4, 5, 6, 4, 6, 7, 8][i]
                    db.add(wm.HomeworkSubmission(assignment_id=batch.id, person_id=pid,
                        submission_status='submitted' if submitted else 'missing',
                        evaluation=('差，没有过程' if k < 2 else '订正完成') if submitted else None))
    # A distinct H identity per linked student; one explicit link opens this subject only.
    hclass = wm.AdministrativeClass(academic_year_id=year.id, grade=2, class_num=6, label='合成行政班')
    db.add(hclass)
    db.flush()
    link = wm.HomeroomTeachingLink(admin_class_id=hclass.id, teaching_class_id=classes[2], academic_year_id=year.id,
        subject='物理', valid_from=date(2026, 9, 1), share_categories='roster,current_subject_score,current_subject_homework')
    db.add(link)
    db.flush()
    for i, tpid in enumerate(identities[2]):
        person = wm.WsStudentIdentity(data_domain='homeroom', display_name=f'合成行政学生{chr(65+i)}')
        db.add(person)
        db.flush()
        db.add(wm.Enrollment(admin_class_id=hclass.id, identity_id=person.id, valid_from=date(2026, 9, 1), status='active'))
        db.add(wm.LinkedStudent(link_id=link.id, homeroom_identity_id=person.id, teaching_identity_id=tpid, confirm_basis='synthetic-preview'))
    db.commit()
    print(json.dumps({'academic_year_id': year.id, 'teaching_classes': classes, 'homeroom_class': hclass.id}))
    db.close()


if __name__ == '__main__':
    main()
