"""Synthetic end-to-end API tests; never use the teacher's database."""
import json
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def world(v1_seed, db_session):
    from app.core.context import WorkspaceContext
    from app.db import workspace_models as wm
    from app.db.models import Teacher, HomeworkSetting

    def create(grade=2, mode='teaching', exams=8, class_grade='same'):
        db = db_session
        ay = wm.AcademicYear(name='个人合成-' + uuid4().hex[:10], start_date=date(2025, 9, 1), end_date=date(2026, 8, 31))
        db.add(ay)
        db.flush()
        if mode == 'teaching':
            cls = wm.TeachingClass(academic_year_id=ay.id, subject='物理', grade=grade if class_grade == 'same' else class_grade, label='合成教学班')
        else:
            cls = wm.AdministrativeClass(academic_year_id=ay.id, grade=grade, class_num=6, label='合成行政班')
            setattr(db.get(Teacher, 1), f'target_class_high{grade}', 6)
            db.merge(HomeworkSetting(key='active_grade', value=str(grade)))
        db.add(cls)
        db.flush()
        people = []
        for i in range(2):
            person = wm.WsStudentIdentity(data_domain=mode, display_name=f'合成学生{chr(65+i)}')
            db.add(person)
            db.flush()
            people.append(person.id)
            if mode == 'teaching':
                db.add(wm.TeachingClassMember(teaching_class_id=cls.id, identity_id=person.id, valid_from=date(2025, 9, 1)))
            else:
                db.add(wm.Enrollment(admin_class_id=cls.id, identity_id=person.id, valid_from=date(2025, 9, 1), status='active'))
        facts, batches = [], []
        for i in range(exams):
            day = date(2025, 10, 1) + timedelta(days=i*20)
            scope = wm.TeachingExamScope(academic_year_id=ay.id, grade=grade, subject='物理', exam_name=f'合成测评{i+1}', exam_date=day) if mode == 'teaching' else None
            if scope:
                db.add(scope)
                db.flush()
            for j, pid in enumerate(people):
                fact = wm.ScoreFact(data_domain=mode, academic_year_id=ay.id, class_ref_id=cls.id, identity_id=pid,
                    exam_name=f'合成测评{i+1}', exam_date=day, subject='物理', score=90-i,
                    grade_score=50 + i*5 if j == 0 else 90-i*5, grade_percentile=.8-i*.08,
                    teaching_exam_id=scope.id if scope else None)
                db.add(fact)
                facts.append(fact)
            for k in range(10):
                batch = wm.HomeworkAssignment(data_domain=mode, academic_year_id=ay.id, class_ref_id=cls.id,
                    subject='物理', homework_type='合成练习', assigned_date=day-timedelta(days=10-k),
                    batch_token=uuid4().hex, expected_members_json=json.dumps(people))
                db.add(batch)
                db.flush()
                batches.append(batch)
                for pid in people:
                    submitted = k < i+3
                    db.add(wm.HomeworkSubmission(assignment_id=batch.id, person_id=pid,
                        submission_status='submitted' if submitted else 'missing',
                        evaluation=('差，没有过程' if k < 2 else '订正完成') if submitted else None))
        db.commit()
        ctx = WorkspaceContext(teacher_id=1, mode=mode, data_domain=mode, academic_year_id=ay.id,
            class_ids=(cls.id,), subject='物理' if mode == 'teaching' else None,
            member_person_ids=tuple(people), as_of=date(2026, 10, 17))
        return SimpleNamespace(db=db, ay=ay.id, cls=cls, pids=people, facts=facts, batches=batches, ctx=ctx)
    return create


def get(client, w, **extra):
    params = {'academic_year_id': w.ay, 'teaching_class_id' if w.ctx.mode == 'teaching' else 'class_id': w.cls.id,
              **({'subject': '物理'} if w.ctx.mode == 'homeroom' else {}), **extra}
    response = client.get(f'/api/v1/{w.ctx.mode}/diagnosis/homework-students', params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('grade', [1, 2, 3])
def test_grade_measure_and_personal_opposite_directions(client, world, grade):
    w = world(grade=grade)
    data = get(client, w)
    a, b = data['students']
    assert a['correlations']['submission']['rho'] == 1
    if grade == 1:
        assert a['timeline'][0]['basis'] == 'grade_percentile'
        assert a['timeline'][0]['achievement'] == 20
        assert a['timeline'][0]['percentile'] == .8
        assert a['timeline'][1]['gain'] == 8
    else:
        assert a['timeline'][0]['basis'] == 'grade_score'
        assert a['timeline'][0]['achievement'] == 50
        assert a['timeline'][1]['gain'] == 5
        assert b['correlations']['submission']['rho'] == -1
    assert a['paired_n'] == 8 and a['correlations']['submission']['n'] == 8
    assert data['calc_version'] == 'student-homework-v1'
    assert 'layers' not in data and 'r' not in data
    # Raw scores fall; positive coefficient proves the chosen grade-specific metric.
    assert a['timeline'][-1]['raw_score'] < a['timeline'][0]['raw_score']


def test_missing_measure_and_absence_never_fall_back_or_bridge(client, world):
    w = world()
    w.facts[2].grade_score = None
    w.facts[4].score = None  # residual grade_score must not be used
    w.facts[6].score = 0
    w.facts[6].grade_score = 0
    w.db.commit()
    points = get(client, w)['students'][0]['timeline']
    assert points[1]['pair_reason'] == 'grade_score_missing'
    assert points[2]['pair_reason'] == 'score_missing'
    assert points[3]['achievement'] == 0 and points[3]['raw_score'] == 0
    assert points[1]['gain'] is None and points[2]['gain'] is None and points[3]['gain'] is None


def test_high1_zero_percentile_and_missing_percentile(client, world):
    w = world(grade=1)
    w.facts[0].grade_percentile = 0
    w.facts[2].grade_percentile = None
    w.db.commit()
    points = get(client, w)['students'][0]['timeline']
    assert points[0]['achievement'] == 100 and points[0]['percentile'] == 0
    assert points[1]['achievement'] is None and points[1]['pair_reason'] == 'percentile_missing'


def test_no_fabricated_grade_and_conflicting_metadata(client, world):
    w = world(class_grade=None)
    # New platform scope provides authoritative grade even when class grade is absent.
    assert get(client, w)['students'][0]['timeline'][0]['basis'] == 'grade_score'
    w.cls.grade = 1
    w.db.commit()
    assert get(client, w)['students'][0]['timeline'][0]['pair_reason'] == 'grade_conflict'
    w.cls.grade = None
    for fact in w.facts:
        fact.teaching_exam_id = None
    w.db.commit()
    assert get(client, w)['students'][0]['timeline'][0]['pair_reason'] == 'grade_missing'
    w.cls.grade = 1
    w.db.commit()
    # Grade was explicitly corrected; no raw fallback for high1 either.
    assert get(client, w)['students'][0]['timeline'][0]['basis'] == 'grade_percentile'


def test_month_dates_and_same_day_exams_are_not_replicates(client, world):
    w = world()
    for f in w.facts[:2]:
        f.exam_date_precision = 'month'
    # Same student with two distinct exams on one day.
    for f in w.facts[4:6]:
        f.exam_date = w.facts[2].exam_date
    from app.db.workspace_models import TeachingExamScope
    w.db.get(TeachingExamScope, w.facts[4].teaching_exam_id).exam_date = w.facts[2].exam_date
    w.db.commit()
    points = get(client, w)['students'][0]['timeline']
    assert sum(p['pair_reason'] == 'same_day_exams' for p in points) == 2
    assert points[-1]['pair_reason'] == 'exact_date_missing'
    assert points[-1]['homework'] is None
    assert all(p['gain'] is None for p in points)


def test_window_cutoff_no_reuse_or_exam_day(client, world):
    from app.db import workspace_models as wm
    w = world(exams=2)
    # Move second exam to 5 days after first, exposing overlapping 30-day windows.
    for f in w.facts[2:]:
        f.exam_date = w.facts[0].exam_date + timedelta(days=5)
    w.db.get(wm.TeachingExamScope, w.facts[2].teaching_exam_id).exam_date = w.facts[2].exam_date
    w.db.add(wm.HomeworkAssignment(data_domain='teaching', class_ref_id=w.cls.id, academic_year_id=w.ay,
        subject='物理', assigned_date=w.facts[0].exam_date, homework_type='合成练习', batch_token=uuid4().hex,
        expected_members_json=json.dumps(w.pids)))
    w.db.commit()
    data = get(client, w, window_days=30)
    points = data['students'][0]['timeline']
    assert points[0]['homework']['eligible'] == 10
    assert points[1]['window_start'] == (w.facts[0].exam_date + timedelta(days=1)).isoformat()
    assert points[1]['homework']['eligible'] == 0
    cutoff = get(client, w, end_exam_name='合成测评1')
    assert len(cutoff['students'][0]['timeline']) == 1
    assert cutoff['students'][0]['correlations']['submission']['rho'] is None


def test_denominators_and_quality_do_not_assume_unrated_good(client, world):
    from app.db import workspace_models as wm
    w = world(exams=1)
    rows = w.db.query(wm.HomeworkSubmission).filter(wm.HomeworkSubmission.person_id == w.pids[0]).order_by(wm.HomeworkSubmission.id).all()
    rows[0].submission_status, rows[0].evaluation = 'excused', None
    rows[1].evaluation = '忘带'
    rows[2].evaluation = None
    # Snapshot/default submitted and old unknown count as submitted, without inventing evaluations.
    rows[3].submission_status, rows[3].evaluation = 'unknown', None
    w.db.delete(rows[4])
    w.batches[-1].expected_members_json = '[]'
    w.db.commit()
    h = get(client, w)['students'][0]['timeline'][0]['homework']
    assert (h['excused'], h['ignored'], h['eligible'], h['submitted'], h['unknown_batches']) == (1, 1, 7, 3, 1)
    assert h['evaluated'] == 0 and h['negative_rate'] is None and h['evaluation_coverage'] == 0


def test_negative_rate_uses_rated_denominator_and_coverage(client, world):
    w = world()
    a = get(client, w)['students'][0]
    h = a['timeline'][0]['homework']
    assert h['negative'] == 2 and h['evaluated'] == 3
    assert h['negative_rate'] == pytest.approx(2/3, abs=.0001)
    assert a['correlations']['negative']['rho'] == -1
    assert a['correlations']['negative']['n'] == 8


def test_scope_person_empty_and_readonly(client, world, v1_seed):
    from app.db import workspace_models as wm
    w = world()
    before = {m.__tablename__: w.db.query(m).count() for m in (wm.ScoreFact, wm.HomeworkAssignment, wm.HomeworkSubmission)}
    personal = get(client, w, person_id=w.pids[1])
    assert [s['person_id'] for s in personal['students']] == [w.pids[1]]
    bad = client.get('/api/v1/teaching/diagnosis/homework-students', params={'academic_year_id': w.ay, 'teaching_class_id': w.cls.id, 'person_id': v1_seed.wu_t_id})
    assert bad.status_code == 404
    assert all(w.db.query(m).count() == before[m.__tablename__] for m in (wm.ScoreFact, wm.HomeworkAssignment, wm.HomeworkSubmission))
    assert get(client, w, grade=1)['students'] == []


def test_homeroom_same_subject_scope_and_legacy_stats_exclusion(client, world):
    from app.db import workspace_models as wm
    w = world(mode='homeroom')
    w.db.add(wm.HomeworkStatsExclusion(data_domain='homeroom', class_ref_id=w.cls.id, identity_id=w.pids[0]))
    w.db.commit()
    data = get(client, w)
    assert len(data['students']) == 2 and data['students'][0]['paired_n'] == 8
    assert get(client, w, subject='数学')['students'][0]['timeline'] == []


def test_constant_homework_and_small_samples_are_explained(client, world):
    from app.db import workspace_models as wm
    w = world(exams=6)
    for row in w.db.query(wm.HomeworkSubmission).filter(wm.HomeworkSubmission.person_id.in_(w.pids)):
        row.submission_status = 'submitted'
    w.db.commit()
    student = get(client, w)['students'][0]
    assert student['correlations']['submission']['rho'] is None
    assert student['correlations']['submission']['reason'] == 'homework_unchanged'
    assert '变化很少' in student['advice']
    assert get(client, w, end_exam_name='合成测评2')['students'][0]['correlations']['submission']['reason'] == 'insufficient_exams'


def test_statistical_sensitivity_and_average_ties():
    from app.diagnosis.student_homework import _correlation
    assert _correlation([(0, 1), (0, 2), (1, 2), (1, 3), (2, 3), (2, 4)])['rho'] == pytest.approx(.8616, abs=.0001)
    result = _correlation([(1, 1), (2, 1), (3, 1), (4, 1), (5, 1), (6, 10)])
    assert result['sensitive'] is True


def test_ai_personal_service_matches_page(client, world):
    from app.api.chat_tools import _tool_get_homework_correlation
    from dataclasses import asdict
    w = world()
    tool = _tool_get_homework_correlation(w.db, asdict(w.ctx), {'person_id': w.pids[0], 'exam_name': '合成测评8'})
    page = get(client, w, person_id=w.pids[0], end_exam_name='合成测评8')
    assert tool == page


def test_historical_empty_membership_does_not_fall_back(client, world):
    from app.db import workspace_models as wm
    w = world(exams=2)
    row = w.db.query(wm.TeachingClassMember).filter_by(teaching_class_id=w.cls.id, identity_id=w.pids[0]).one()
    row.valid_from = w.facts[0].exam_date + timedelta(days=1)
    w.db.commit()
    student = get(client, w)['students'][0]
    assert len(student['timeline']) == 1 and student['timeline'][0]['exam_name'] == '合成测评2'
    row.valid_from = w.facts[-1].exam_date + timedelta(days=1)
    w.db.commit()
    assert get(client, w)['students'][0]['timeline'] == []


def test_unknown_denominator_majority_suppresses_pair(client, world):
    w = world(exams=1)
    for a in w.batches[:6]:
        a.expected_members_json = '[]'
    w.db.commit()
    point = get(client, w)['students'][0]['timeline'][0]
    assert point['pair_reason'] == 'denominator_unknown_over_half'
    assert point['homework']['unknown_batches'] == 6


def test_explicit_link_projection_and_revocation(client, world):
    from app.db import workspace_models as wm
    w = world(mode='homeroom')
    tc = wm.TeachingClass(academic_year_id=w.ay, grade=2, subject='物理', label='合成关联教学班')
    ident = wm.WsStudentIdentity(data_domain='teaching', display_name='合成关联学生')
    w.db.add_all([tc, ident])
    w.db.flush()
    w.db.add(wm.TeachingClassMember(teaching_class_id=tc.id, identity_id=ident.id, valid_from=date(2025, 9, 1)))
    link = wm.HomeroomTeachingLink(admin_class_id=w.cls.id, teaching_class_id=tc.id, academic_year_id=w.ay,
        subject='物理', valid_from=date(2025, 9, 1), share_categories='roster,current_subject_score,current_subject_homework')
    w.db.add(link)
    w.db.flush()
    w.db.add(wm.LinkedStudent(link_id=link.id, homeroom_identity_id=w.pids[0], teaching_identity_id=ident.id, confirm_basis='synthetic-test'))
    # Leave only H source homework+scores; T sees this mapped student, not his H classmate.
    w.db.commit()
    response = client.get('/api/v1/teaching/diagnosis/homework-students', params={'academic_year_id': w.ay, 'teaching_class_id': tc.id})
    assert response.status_code == 200, response.text
    student = response.json()['students'][0]
    assert student['person_id'] == ident.id and student['paired_n'] == 8
    assert len(response.json()['students']) == 1
    link.share_categories = 'roster,current_subject_score'
    w.db.commit()
    response = client.get('/api/v1/teaching/diagnosis/homework-students', params={'academic_year_id': w.ay, 'teaching_class_id': tc.id})
    assert response.json()['students'][0]['paired_n'] == 0
    link.status = 'cancelled'
    w.db.commit()
    response = client.get('/api/v1/teaching/diagnosis/homework-students', params={'academic_year_id': w.ay, 'teaching_class_id': tc.id})
    assert response.json()['students'][0]['timeline'] == []


def test_invalid_window_and_unauthorized_cutoff(client, world):
    w = world(exams=1)
    base = {'academic_year_id': w.ay, 'teaching_class_id': w.cls.id}
    assert client.get('/api/v1/teaching/diagnosis/homework-students', params={**base, 'window_days': 7}).status_code == 422
    assert client.get('/api/v1/teaching/diagnosis/homework-students', params={**base, 'end_exam_name': '范围外合成考试'}).status_code == 404
    assert client.get('/api/v1/teaching/diagnosis/homework-students', params={**base, 'grade': 4}).status_code == 422


def test_conflicting_date_cannot_supply_a_window_or_cutoff(client, world):
    w = world(exams=2)
    w.facts[2].exam_date += timedelta(days=1)
    w.db.commit()
    data = get(client, w, end_exam_name='合成测评2')
    p = data['students'][0]['timeline'][0]
    assert len(data['students'][0]['timeline']) == 1
    assert p['pair_reason'] == 'exact_date_missing'
    assert p['homework'] is None and p['window_start'] is None and p['gain'] is None
    assert '只保留截止场明细' in data['note']
