"""Synthetic tests for workspace exam deletion; production data is never used."""
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def world(v1_seed, db_session):
    from app.db import workspace_models as w
    db = db_session
    ay = w.AcademicYear(name='考试管理合成-'+uuid4().hex[:10], start_date=date(2025,9,1), end_date=date(2026,8,31))
    db.add(ay); db.flush()
    h = w.AdministrativeClass(academic_year_id=ay.id, grade=2, class_num=6, label='合成行政班')
    ta = w.TeachingClass(academic_year_id=ay.id, grade=2, subject='物理', label='合成教学甲班')
    tb = w.TeachingClass(academic_year_id=ay.id, grade=2, subject='物理', label='合成教学乙班')
    t3 = w.TeachingClass(academic_year_id=ay.id, grade=3, subject='物理', label='合成高三班')
    db.add_all([h,ta,tb,t3]); db.flush()
    people = {}
    for key,domain in [('ha','homeroom'),('hf','homeroom'),('ta','teaching'),('tb','teaching'),('t3','teaching')]:
        p = w.WsStudentIdentity(data_domain=domain, display_name='合成管理学生'+key)
        db.add(p); db.flush(); people[key] = p.id
    db.add_all([w.Enrollment(admin_class_id=h.id, identity_id=people['ha'], valid_from=date(2025,9,1)),
        w.Enrollment(admin_class_id=h.id, identity_id=people['hf'], valid_from=date(2025,9,1), valid_to=date(2025,12,1), status='graduated')])
    for key,cls in [('ta',ta),('tb',tb),('t3',t3)]:
        db.add(w.TeachingClassMember(teaching_class_id=cls.id, identity_id=people[key], valid_from=date(2025,9,1)))
    exam = w.TeachingExamScope(academic_year_id=ay.id, grade=2, subject='物理', exam_name='合成管理考', exam_date=date(2025,11,21),
        class_snapshot=[{'class_kind':'teaching','class_label':'合成教学甲班'}], aggregates=[{'synthetic':True}])
    other = w.TeachingExamScope(academic_year_id=ay.id, grade=3, subject='物理', exam_name='合成管理考', exam_date=date(2025,11,21))
    older = w.TeachingExamScope(academic_year_id=ay.id, grade=2, subject='物理', exam_name='合成旧质量考', exam_date=date(2025,10,21),
        class_snapshot=[{'class_kind':'teaching','class_label':'合成旧参照'}])
    db.add_all([exam,other,older]); db.flush()
    facts=[]
    for key,cls,e in [('ta',ta,exam),('tb',tb,exam),('t3',t3,other)]:
        f=w.ScoreFact(data_domain='teaching', academic_year_id=ay.id, class_ref_id=cls.id, identity_id=people[key],
            exam_name=e.exam_name, exam_date=e.exam_date, subject='物理', score=70, grade_score=65, teaching_exam_id=e.id)
        db.add(f); facts.append(f)
    for key in ('ha','hf'):
        for subject,total in [('物理',None),('总分','主三门')]:
            db.add(w.ScoreFact(data_domain='homeroom', academic_year_id=ay.id, class_ref_id=h.id, identity_id=people[key],
                exam_name=exam.exam_name, exam_date=exam.exam_date, subject=subject, total_type=total, score=80))
    db.add(w.ScoreFact(data_domain='teaching', academic_year_id=ay.id, class_ref_id=ta.id, identity_id=people['ta'],
        exam_name='合成旧格式考', exam_date=date(2025,10,1), subject='物理', score=60))
    for grade in (2,3):
        db.add(w.WorkspaceClassAverage(data_domain='homeroom', academic_year_id=ay.id, grade=grade,
            exam_name=exam.exam_name, exam_date=exam.exam_date, class_num=6, subject_averages={'物理':60}))
    for e in (exam,other,older):
        db.add(w.TeachingClassQuality(exam_id=e.id, class_kind='teaching', class_label='合成参照', metrics={'raw_avg':60}))
    db.add(w.TeachingComparisonRoster(academic_year_id=ay.id, grade=2, subject='物理', version=1,
        source_exam_date=exam.exam_date, classes=exam.class_snapshot))
    db.flush()
    db.add(w.SourceMap(source_fingerprint='synthetic-'+uuid4().hex, source_table='scores', source_pk='1', target_table='score_fact', target_id=facts[0].id))
    db.add(w.SourceProjectionMap(data_domain='teaching', source_fingerprint='synthetic-'+uuid4().hex, source_table='scores', source_pk='1',
        target_table='score_fact', target_id=facts[0].id, projection_kind='own'))
    # Non-score records must survive deletion.
    db.add(w.HomeworkAssignment(data_domain='teaching', academic_year_id=ay.id, class_ref_id=ta.id, subject='物理', homework_type='合成练习',
        assigned_date=date(2025,11,20), batch_token=uuid4().hex, expected_members_json=json.dumps([people['ta']])))
    db.add(w.WsStudentNote(data_domain='teaching', person_id=people['ta'], date=date(2025,11,21), category='观察', content='合成观察记录'))
    db.commit()
    return SimpleNamespace(db=db, ay=ay.id, h=h.id, ta=ta.id, tb=tb.id, t3=t3.id, exam=exam.id, other=other.id,
        older=older.id, people=people, facts=[f.id for f in facts])


def catalog(client, world, mode='teaching', **extra):
    params={'academic_year_id':world.ay, **({'class_id':world.h} if mode=='homeroom' else {}), **extra}
    r=client.get(f'/api/v1/{mode}/exams',params=params)
    assert r.status_code==200,r.text
    return r.json()['exams']


def preview(client, world, mode='teaching', name='合成管理考', grade=2):
    exam=next(e for e in catalog(client,world,mode) if e['exam_name']==name and e['grade']==grade)
    r=client.post(f'/api/v1/{mode}/exams/delete-preview',json={'academic_year_id':world.ay,
        **({'class_id':world.h} if mode=='homeroom' else {}),'exam_key':exam['key']})
    assert r.status_code==200,r.text
    return r.json()


def confirm(client,p,mode='teaching',name=None):
    return client.post(f'/api/v1/{mode}/exams/delete-confirm',json={'token':p['token'],'confirm_name':name or p['exam']['exam_name']})


def test_platform_delete_all_owned_classes_and_references(client,world):
    from app.db import workspace_models as w
    p=preview(client,world)
    assert p['exam']['score_count']==2 and len(p['exam']['classes'])==2
    assert 'deleted_rows' not in p
    r=confirm(client,p)
    assert r.status_code==200,r.text
    db=world.db; db.expire_all()
    assert db.get(w.TeachingExamScope,world.exam) is None
    assert db.query(w.ScoreFact).filter_by(teaching_exam_id=world.exam).count()==0
    assert db.query(w.TeachingClassQuality).filter_by(exam_id=world.exam).count()==0
    assert db.get(w.TeachingExamScope,world.other) is not None
    assert db.query(w.ScoreFact).filter_by(data_domain='homeroom',academic_year_id=world.ay,exam_name='合成管理考').count()==4
    assert db.query(w.HomeworkAssignment).filter_by(academic_year_id=world.ay).count()==1
    assert db.query(w.WsStudentNote).filter_by(person_id=world.people['ta']).count()==1
    roster=db.query(w.TeachingComparisonRoster).filter_by(academic_year_id=world.ay,grade=2).order_by(w.TeachingComparisonRoster.version.desc()).first()
    assert roster.version==2 and roster.classes==db.get(w.TeachingExamScope,world.older).class_snapshot
    assert db.query(w.SourceMap).filter_by(target_id=world.facts[0],target_table='score_fact').one().active==0
    assert db.query(w.SourceProjectionMap).filter_by(target_id=world.facts[0],target_table='score_fact').one().status=='deleted'
    audit=db.query(w.ImportBatch).filter_by(token=p['token']).one()
    assert audit.status=='confirmed' and len(json.loads(audit.scope_json)['deleted_rows']['score_fact'])==2
    assert confirm(client,p).status_code==409
    assert not any(e['exam_name']=='合成管理考' and e['grade']==2 for e in catalog(client,world))


def test_homeroom_includes_former_students_but_not_other_grade(client,world):
    from app.db import workspace_models as w
    p=preview(client,world,'homeroom')
    assert (p['exam']['student_count'],p['exam']['score_count'],p['exam']['average_count'])==(2,4,1)
    assert confirm(client,p,'homeroom').status_code==200
    db=world.db; db.expire_all()
    assert db.query(w.ScoreFact).filter_by(data_domain='homeroom',academic_year_id=world.ay).count()==0
    assert db.query(w.WorkspaceClassAverage).filter_by(academic_year_id=world.ay,grade=2).count()==0
    assert db.query(w.WorkspaceClassAverage).filter_by(academic_year_id=world.ay,grade=3).count()==1
    assert db.get(w.ScoreFact,world.facts[0]) is not None
    assert db.query(w.Enrollment).filter_by(admin_class_id=world.h).count()==2


def test_legacy_teaching_and_quality_only_exam(client,world):
    p=preview(client,world,name='合成旧格式考')
    assert p['exam']['kind']=='native'
    assert confirm(client,p).status_code==200
    p=preview(client,world,name='合成旧质量考')
    assert p['exam']['score_count']==0 and p['exam']['quality_count']==1
    assert confirm(client,p).status_code==200


def test_average_only_homeroom_exam(client,world):
    from app.db.workspace_models import WorkspaceClassAverage
    world.db.add(WorkspaceClassAverage(data_domain='homeroom',academic_year_id=world.ay,grade=2,class_num=7,exam_name='合成纯均分考'))
    world.db.commit()
    p=preview(client,world,'homeroom',name='合成纯均分考')
    assert p['exam']['score_count']==0 and p['exam']['average_count']==1
    assert confirm(client,p,'homeroom').status_code==200


def test_shared_origin_readonly_and_no_cross_domain_delete(client,world):
    from app.db import workspace_models as w
    db=world.db
    link=w.HomeroomTeachingLink(admin_class_id=world.h,teaching_class_id=world.ta,academic_year_id=world.ay,subject='物理',
        valid_from=date(2025,9,1),share_categories='roster,current_subject_score,current_subject_homework')
    db.add(link); db.flush()
    db.add(w.LinkedStudent(link_id=link.id,homeroom_identity_id=world.people['ha'],teaching_identity_id=world.people['ta'],confirm_basis='synthetic'))
    db.commit()
    p=preview(client,world,'homeroom')
    assert confirm(client,p,'homeroom').status_code==200
    exams=catalog(client,world,'homeroom')
    shared=next(e for e in exams if e['exam_name']=='合成管理考')
    assert shared['can_delete'] is False and shared['projected_students']==1
    r=client.post('/api/v1/homeroom/exams/delete-preview',json={'academic_year_id':world.ay,'class_id':world.h,'exam_key':shared['key']})
    assert r.status_code==404
    assert db.get(w.TeachingExamScope,world.exam) is not None


def test_name_domain_year_and_foreign_key_guards(client,world,v1_seed):
    p=preview(client,world)
    assert confirm(client,p,name='错误名称').status_code==422
    assert confirm(client,p,'homeroom').status_code==409
    assert client.post('/api/v1/teaching/exams/delete-preview',json={'academic_year_id':v1_seed.ay_id,'exam_key':p['exam']['key']}).status_code==404
    assert client.get('/api/v1/homeroom/exams',params={'academic_year_id':world.ay,'class_id':v1_seed.h9_id}).status_code==404
    assert client.get('/api/v1/teaching/exams',params={'academic_year_id':world.ay,'grade':4}).status_code==422
    assert client.get('/api/v1/teaching/exams',params={'academic_year_id':world.ay,'teaching_class_id':world.ta}).status_code==422
    assert client.post('/api/v1/teaching/exams/delete-preview',json={'academic_year_id':world.ay,'exam_key':p['exam']['key'],'teaching_class_id':world.ta}).status_code==422
    assert confirm(client,p).status_code==200


def test_changed_data_or_new_import_after_preview_causes_zero_deletion(client,world):
    from app.db import workspace_models as w
    p=preview(client,world)
    world.db.get(w.ScoreFact,world.facts[0]).score=12
    world.db.commit()
    assert confirm(client,p).status_code==409
    world.db.expire_all()
    assert world.db.get(w.TeachingExamScope,world.exam) is not None
    assert world.db.query(w.ImportBatch).filter_by(token=p['token']).one().status=='pending'
    p=preview(client,world)
    world.db.add(w.ImportBatch(token=uuid4().hex,data_domain='teaching',scope_json=json.dumps({'kind':'teaching_platform',
        'academic_year_id':world.ay,'exam_name':'合成管理考','grade':2,'subject':'物理'})))
    world.db.commit()
    assert confirm(client,p).status_code==409


def test_pending_imports_invalidated_but_confirmed_receipts_and_other_grade_kept(client,world):
    from app.db import workspace_models as w
    db=world.db
    batches=[]
    scopes=[{'kind':'teaching_platform','grade':2,'exam_name':'合成管理考','subject':'物理'},
        {'kind':'import_preview','class_ids':[world.ta],'subject':'物理','files':[{'exam_name':'合成管理考','grade':2},{'exam_name':'合成另场','grade':2}]},
        {'kind':'teaching_platform','grade':3,'exam_name':'合成管理考','subject':'物理'}]
    for i,s in enumerate(scopes):
        batch=w.ImportBatch(token=uuid4().hex,data_domain='teaching',scope_json=json.dumps({**s,'academic_year_id':world.ay}))
        db.add(batch); batches.append(batch)
    receipt=w.ImportBatch(token=uuid4().hex,data_domain='teaching',status='confirmed',scope_json=json.dumps({**scopes[0],'academic_year_id':world.ay}))
    db.add(receipt); db.commit()
    p=preview(client,world)
    assert p['pending_imports']==2
    assert confirm(client,p).status_code==200
    db.expire_all()
    assert [b.status for b in batches]==['invalidated','invalidated','pending']
    assert receipt.status=='confirmed'
    assert client.post('/api/v1/teaching/platform/imports/confirm',json={'token':batches[0].token}).status_code==409
    assert client.post('/api/v1/imports/confirm',json={'token':batches[1].token}).status_code==409


def test_expired_token_and_old_import_token_rejected(client,world):
    from app.db.workspace_models import ImportBatch
    p=preview(client,world)
    batch=world.db.query(ImportBatch).filter_by(token=p['token']).one()
    batch.expires_at=datetime.utcnow()-timedelta(seconds=1)
    world.db.commit()
    assert confirm(client,p).status_code==409
    batch.expires_at=datetime.utcnow()+timedelta(minutes=10)
    batch.scope_json=json.dumps({'kind':'import_preview','mode':'teaching'})
    world.db.commit()
    assert confirm(client,p).status_code==409


def test_existing_legacy_rows_adopted_by_unique_platform_exam(client,world):
    from app.db.workspace_models import ScoreFact
    world.db.get(ScoreFact,world.facts[0]).teaching_exam_id=None
    world.db.commit()
    p=preview(client,world)
    assert p['exam']['score_count']==2
    assert confirm(client,p).status_code==200
    world.db.expire_all()
    assert world.db.get(ScoreFact,world.facts[0]) is None


def test_forbidden_platform_reference_never_partially_deletes(client,world):
    from app.db.workspace_models import ScoreFact
    # Corrupt/foreign-domain reference cannot broaden the deletion scope to satisfy the FK.
    f=world.db.query(ScoreFact).filter_by(data_domain='homeroom',academic_year_id=world.ay,subject='物理').first()
    f.teaching_exam_id=world.exam
    world.db.commit()
    exam=next(e for e in catalog(client,world) if e['exam_name']=='合成管理考' and e['grade']==2)
    r=client.post('/api/v1/teaching/exams/delete-preview',json={'academic_year_id':world.ay,'exam_key':exam['key']})
    assert r.status_code==404


def test_delete_failure_rolls_back_rows_roster_and_token(client,world,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api import exam_management as service
    from app.db import workspace_models as w
    p=preview(client,world)
    original=service._refresh_roster
    def fail(db,ctx,g,roster):
        original(db,ctx,g,roster)
        db.delete(g['facts'][0]); db.flush()
        raise RuntimeError('synthetic failure after actual DELETE')
    monkeypatch.setattr(service,'_refresh_roster',fail)
    failing_client=TestClient(app,raise_server_exceptions=False)
    try:
        assert confirm(failing_client,p).status_code==500
    finally:
        failing_client.close()
    world.db.expire_all()
    assert world.db.get(w.ScoreFact,world.facts[0]) is not None
    assert world.db.get(w.TeachingExamScope,world.exam) is not None
    assert world.db.query(w.TeachingComparisonRoster).filter_by(academic_year_id=world.ay).count()==1
    assert world.db.query(w.ImportBatch).filter_by(token=p['token']).one().status=='pending'


def test_latest_roster_becomes_empty_when_no_quality_exams_remain(client,world):
    from app.db import workspace_models as w
    p=preview(client,world,name='合成旧质量考')
    assert confirm(client,p).status_code==200
    p=preview(client,world)
    assert confirm(client,p).status_code==200
    world.db.expire_all()
    roster=world.db.query(w.TeachingComparisonRoster).filter_by(academic_year_id=world.ay,grade=2).order_by(w.TeachingComparisonRoster.version.desc()).first()
    assert roster.classes==[]
    assert roster.source_exam_date==date(2025,9,1)


def test_changed_membership_rejects_delete_and_scope_is_not_broadened(client,world):
    from app.db import workspace_models as w
    p=preview(client,world)
    member=world.db.query(w.TeachingClassMember).filter_by(teaching_class_id=world.ta,identity_id=world.people['ta']).one()
    member.valid_to=date(2025,12,1)
    world.db.commit()
    assert confirm(client,p).status_code==409
    world.db.expire_all()
    assert world.db.get(w.TeachingExamScope,world.exam) is not None


def test_same_name_other_year_survives_and_class_ownership_rechecked(client,world,v1_seed):
    from app.db import workspace_models as w
    other=w.ScoreFact(data_domain='teaching',academic_year_id=v1_seed.ay_id,class_ref_id=v1_seed.t6_id,
        identity_id=v1_seed.jia_t_id,exam_name='合成管理考',exam_date=date(2025,11,21),subject='物理',score=33)
    world.db.add(other); world.db.commit()
    p=preview(client,world)
    assert confirm(client,p).status_code==200
    world.db.expire_all()
    assert world.db.get(w.ScoreFact,other.id) is not None


def test_deleted_exam_is_gone_from_analysis_correlation_and_comparison(client,world):
    p=preview(client,world)
    assert confirm(client,p).status_code==200
    exams=client.get('/api/v1/shared/exams',params={'mode':'teaching','academic_year_id':world.ay,'teaching_class_id':world.ta}).json()['exams']
    assert all(e['exam_name']!='合成管理考' for e in exams)
    assert client.get(f'/api/v1/teaching/platform/exams/{world.exam}/compare').status_code==404
    analysis=client.get('/api/v1/teaching/diagnosis/homework-students',params={'academic_year_id':world.ay,'teaching_class_id':world.ta})
    assert analysis.status_code==200
    assert all(p['exam_name']!='合成管理考' for s in analysis.json()['students'] for p in s['timeline'])


def test_audit_failure_after_child_deletes_rolls_everything_back(client,world,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api import exam_management as service
    from app.db import workspace_models as w
    p=preview(client,world)
    real_dumps=service.json.dumps
    def fail(value,*args,**kwargs):
        if isinstance(value,dict) and 'deleted_rows' in value:
            raise RuntimeError('synthetic failure writing final audit')
        return real_dumps(value,*args,**kwargs)
    monkeypatch.setattr(service.json,'dumps',fail)
    failing_client=TestClient(app,raise_server_exceptions=False)
    try:
        assert confirm(failing_client,p).status_code==500
    finally:
        failing_client.close()
    world.db.expire_all()
    assert world.db.get(w.ScoreFact,world.facts[0]) is not None
    assert world.db.query(w.SourceMap).filter_by(target_id=world.facts[0],target_table='score_fact').one().active==1
    assert world.db.query(w.SourceProjectionMap).filter_by(target_id=world.facts[0],target_table='score_fact').one().status=='projected'
    assert world.db.query(w.TeachingClassQuality).filter_by(exam_id=world.exam).count()==1
    assert world.db.query(w.ImportBatch).filter_by(token=p['token']).one().status=='pending'


def test_changed_teacher_binding_rejects_before_any_deletion(client,world):
    from app.db.models import Teacher
    from app.db.workspace_models import ScoreFact,ImportBatch
    p=preview(client,world,'homeroom')
    teacher=world.db.get(Teacher,1)
    old=teacher.target_class_high2
    teacher.target_class_high2=9
    world.db.commit()
    try:
        assert confirm(client,p,'homeroom').status_code==409
        world.db.expire_all()
        assert world.db.query(ScoreFact).filter_by(data_domain='homeroom',academic_year_id=world.ay).count()==4
        assert world.db.query(ImportBatch).filter_by(token=p['token']).one().status=='pending'
    finally:
        teacher.target_class_high2=old
        world.db.commit()


def test_legacy_exam_key_cannot_alias_a_changed_teaching_subject(client,world):
    from app.db import workspace_models as w
    old=next(e for e in catalog(client,world) if e['exam_name']=='合成旧格式考')
    for cls in world.db.query(w.TeachingClass).filter_by(academic_year_id=world.ay):
        cls.status='inactive'
    chem=w.TeachingClass(academic_year_id=world.ay,grade=2,subject='化学',label='合成化学班')
    world.db.add(chem);world.db.flush()
    world.db.add(w.TeachingClassMember(teaching_class_id=chem.id,identity_id=world.people['ta'],valid_from=date(2025,9,1)))
    world.db.add(w.ScoreFact(data_domain='teaching',academic_year_id=world.ay,class_ref_id=chem.id,identity_id=world.people['ta'],
        subject='化学',exam_name='合成旧格式考',exam_date=date(2025,10,1),score=80))
    world.db.commit()
    new=next(e for e in catalog(client,world) if e['exam_name']=='合成旧格式考')
    assert old['key']!=new['key']
    assert client.post('/api/v1/teaching/exams/delete-preview',json={'academic_year_id':world.ay,'exam_key':old['key']}).status_code==404
