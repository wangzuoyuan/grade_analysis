"""Synthetic integration tests for teaching-class deletion and isolation."""
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import text

@pytest.fixture
def world():
    from app.db import workspace_models as w
    from app.db.models import SessionLocal, Teacher
    db=SessionLocal(); db.merge(Teacher(id=1,name='合成教师',target_class_high2=6))
    year=w.AcademicYear(name='合成删除年-'+uuid4().hex[:8],start_date=date(2030,9,1),end_date=date(2031,7,1))
    db.add(year);db.flush()
    other=w.TeachingClass(academic_year_id=year.id,subject='物理',label='保留班',grade=2)
    db.add(other);db.flush()
    target=w.TeachingClass(academic_year_id=year.id,subject='物理',label='待删班',grade=2)
    db.add(target);db.flush()
    admin=w.AdministrativeClass(id=target.id,academic_year_id=year.id,grade=2,class_num=6)
    t=w.WsStudentIdentity(data_domain='teaching',display_name='合成甲')
    h=w.WsStudentIdentity(data_domain='homeroom',display_name='合成乙')
    db.add_all([admin,t,h]);db.flush()
    for cid,start,end in [(target.id,date(2030,9,1),None),(target.id,date(2029,9,1),date(2030,6,1)),(other.id,date(2030,9,1),None)]:
        db.add(w.TeachingClassMember(teaching_class_id=cid,identity_id=t.id,valid_from=start,valid_to=end))
    db.add(w.Enrollment(admin_class_id=admin.id,identity_id=h.id,valid_from=date(2030,9,1)))
    exam=w.TeachingExamScope(academic_year_id=year.id,grade=2,subject='物理',exam_name='合成考试',exam_date=date(2030,10,1),
        class_snapshot=[{'class_kind':'teaching','class_label':'待删班','managed_class_id':target.id},{'class_kind':'teaching','class_label':'保留班','managed_class_id':other.id}],aggregates=[{'actual_count':2}])
    db.add(exam);db.flush()
    roster=w.TeachingComparisonRoster(academic_year_id=year.id,grade=2,subject='物理',version=1,source_exam_date=exam.exam_date,classes=exam.class_snapshot)
    quality=w.TeachingClassQuality(exam_id=exam.id,class_kind='teaching',class_label='待删班',metrics={'raw_avg':81})
    f=w.ScoreFact(data_domain='teaching',academic_year_id=year.id,class_ref_id=target.id,identity_id=t.id,subject='物理',score=81,exam_name='合成考试',teaching_exam_id=exam.id)
    keep=w.ScoreFact(data_domain='teaching',academic_year_id=year.id,class_ref_id=other.id,identity_id=t.id,subject='物理',score=87,exam_name='保留考试')
    hf=w.ScoreFact(data_domain='homeroom',academic_year_id=year.id,class_ref_id=target.id,identity_id=h.id,subject='物理',score=85,exam_name='合成考试')
    db.add_all([roster,quality,f,keep,hf]);db.flush()
    tasks=[]
    for domain,cid,pid in [('teaching',target.id,t.id),('teaching',other.id,t.id),('homeroom',admin.id,h.id)]:
        a=w.HomeworkAssignment(data_domain=domain,class_ref_id=cid,academic_year_id=year.id,subject='物理',homework_type='练习',assigned_date=date(2030,9,20),batch_token=uuid4().hex,expected_members_json=json.dumps([pid]))
        db.add(a);db.flush();db.add(w.HomeworkSubmission(assignment_id=a.id,person_id=pid,submission_status='missing'));tasks.append(a.id)
    link=w.HomeroomTeachingLink(admin_class_id=admin.id,teaching_class_id=target.id,academic_year_id=year.id,subject='物理',valid_from=date(2030,9,1))
    db.add(link);db.flush()
    db.add_all([w.LinkedStudent(link_id=link.id,homeroom_identity_id=h.id,teaching_identity_id=t.id),
        w.HomeworkStatsExclusion(data_domain='teaching',class_ref_id=target.id,identity_id=t.id),
        w.HomeworkStatsExclusion(data_domain='homeroom',class_ref_id=target.id,identity_id=h.id),
        w.WsStudentNote(data_domain='teaching',person_id=t.id,date=date(2030,9,20),category='观察',content='合成保留档案'),
        w.WsStudentAlias(data_domain='teaching',identity_id=t.id,alias_value=uuid4().hex,academic_year_id=year.id,alias_scope=str(year.id))])
    sm=w.SourceMap(source_fingerprint='synthetic',source_table='score',source_pk=uuid4().hex,target_table='score_fact',target_id=f.id)
    pm=w.SourceProjectionMap(data_domain='teaching',source_fingerprint='synthetic',source_table='score',source_pk=uuid4().hex,target_table='score_fact',target_id=f.id,projection_kind='native')
    db.add_all([sm,pm])
    def batch(domain,payload,status='pending'):
        b=w.ImportBatch(token=uuid4().hex,data_domain=domain,status=status,scope_json=json.dumps(payload),expires_at=datetime.utcnow()+timedelta(minutes=30))
        db.add(b);db.flush();return b.id
    pending=batch('teaching',{'kind':'homework_preview','academic_year_id':year.id,'class_ref_id':target.id})
    other_pending=batch('teaching',{'kind':'homework_preview','academic_year_id':year.id,'class_ref_id':other.id})
    linked_pending=batch('homeroom',{'kind':'link_preview','teaching_class_id':target.id})
    broad=batch('teaching',{'kind':'teaching_platform','academic_year_id':year.id,'subject':'物理'})
    undo=batch('teaching',{'kind':'teaching_rollover_preview','target_class_ids':[target.id]},'confirmed')
    chats=[]
    for cid in [target.id,other.id]:
        chat=w.ChatSession(type='chat',scope_json=json.dumps({'mode':'teaching','class_ids':[cid]}));db.add(chat);db.flush();chats.append(chat.id)
    db.commit()
    yield SimpleNamespace(db=db,year=year.id,target=target.id,other=other.id,person=t.id,hperson=h.id,fact=f.id,keep=keep.id,hfact=hf.id,exam=exam.id,roster=roster.id,quality=quality.id,tasks=tasks,link=link.id,sm=sm.id,pm=pm.id,pending=pending,other_pending=other_pending,linked_pending=linked_pending,broad=broad,undo=undo,chats=chats)
    db.close()

def preview(client,world,cid=None):
    r=client.post(f'/api/v1/teaching/classes/{cid or world.target}/delete-preview');assert r.status_code==200,r.text;return r.json()

def confirm(client,world,p,name='待删班',cid=None):
    return client.post(f'/api/v1/teaching/classes/{cid or world.target}/delete-confirm',json={'token':p['token'],'confirm_name':name})

def test_populated_class_delete_preserves_other_scopes(client,world):
    from app.db import workspace_models as w
    p=preview(client,world);db=world.db
    assert (p['impact']['membership_count'],p['impact']['member_count'])==(2,1)
    assert (p['impact']['score_count'],p['impact']['homework_count'],p['impact']['submission_count'])==(1,1,1)
    assert (p['impact']['link_count'],p['impact']['pair_count'],p['impact']['reference_count'])==(1,1,2)
    assert p['impact']['invalidated_operations']==4 and p['impact']['closed_sessions']==1
    assert 'deleted_rows' not in p and db.get(w.TeachingClass,world.target)
    r=confirm(client,world,p);assert r.status_code==200,r.text
    assert [c['class_id'] for c in r.json()['classes']]==[world.other]
    db.expire_all()
    for model,ident in [(w.TeachingClass,world.target),(w.ScoreFact,world.fact),(w.HomeworkAssignment,world.tasks[0]),(w.HomeroomTeachingLink,world.link)]:
        assert db.get(model,ident) is None
    assert db.query(w.HomeworkSubmission).filter_by(assignment_id=world.tasks[0]).count()==0
    assert db.query(w.LinkedStudent).filter_by(link_id=world.link).count()==0
    assert db.query(w.TeachingClassMember).filter_by(teaching_class_id=world.target).count()==0
    assert db.get(w.ScoreFact,world.keep) and db.get(w.ScoreFact,world.hfact)
    assert db.get(w.HomeworkAssignment,world.tasks[1]) and db.get(w.HomeworkAssignment,world.tasks[2])
    assert db.query(w.HomeworkStatsExclusion).filter_by(data_domain='homeroom',class_ref_id=world.target).count()==1
    assert db.query(w.TeachingClassMember).filter_by(teaching_class_id=world.other,identity_id=world.person).count()==1
    assert db.get(w.WsStudentIdentity,world.person) and db.get(w.WsStudentIdentity,world.hperson)
    assert db.query(w.WsStudentAlias).filter_by(identity_id=world.person).count()==1
    assert db.query(w.WsStudentNote).filter_by(person_id=world.person).count()==1
    assert db.get(w.TeachingClassQuality,world.quality).metrics=={'raw_avg':81}
    exam=db.get(w.TeachingExamScope,world.exam)
    assert exam.class_snapshot[0]['managed_class_id'] is None and exam.class_snapshot[1]['managed_class_id']==world.other
    assert exam.aggregates==[{'actual_count':2}]
    assert db.get(w.TeachingComparisonRoster,world.roster).classes[0]['managed_class_id'] is None
    for bid in [world.pending,world.linked_pending,world.broad,world.undo]:assert db.get(w.ImportBatch,bid).status=='invalidated'
    assert db.get(w.ImportBatch,world.other_pending).status=='pending'
    assert db.get(w.ChatSession,world.chats[0]).status=='closed' and db.get(w.ChatSession,world.chats[1]).status=='open'
    assert db.get(w.SourceMap,world.sm).active==0 and db.get(w.SourceProjectionMap,world.pm).status=='deleted'
    audit=db.query(w.ImportBatch).filter_by(token=p['token']).one()
    assert audit.status=='confirmed' and json.loads(audit.scope_json)['deleted_rows']['score_fact'][0]['score']==81
    assert db.execute(text('PRAGMA foreign_key_check')).all()==[]
    assert confirm(client,world,p).status_code==409

@pytest.mark.parametrize('status',['active','inactive'])
def test_empty_last_class_and_id_not_reused(client,world,status):
    from app.db import workspace_models as w
    db=world.db
    year=w.AcademicYear(name='空年-'+uuid4().hex[:8],start_date=date(2032,9,1),end_date=date(2033,7,1));db.add(year);db.flush()
    cls=w.TeachingClass(academic_year_id=year.id,subject='物理',label='空班',status=status);db.add(cls);db.commit();cid=cls.id
    p=preview(client,world,cid);r=confirm(client,world,p,'空班',cid);assert r.status_code==200,r.text
    assert r.json()['classes']==[]
    r=client.post('/api/v1/teaching/classes',json={'academic_year_id':year.id,'subject':'物理','label':'新班'})
    assert r.status_code==200,r.text
    assert r.json()['classes'][0]['class_id']>cid
    direct=w.TeachingClass(academic_year_id=year.id,subject='物理',label='直接新建');db.add(direct);db.commit()
    assert direct.id>r.json()['classes'][0]['class_id']
    assert client.post(f'/api/v1/teaching/classes/{cid}/delete-preview').status_code==404

def test_name_binding_expiry_and_invalid_id(client,world):
    from app.db.workspace_models import ImportBatch,TeachingClass
    p=preview(client,world)
    assert confirm(client,world,p,'错误').status_code==422
    assert confirm(client,world,p,cid=world.other).status_code==409
    world.db.expire_all();b=world.db.query(ImportBatch).filter_by(token=p['token']).one();assert b.status=='pending'
    b.expires_at=datetime.utcnow()-timedelta(seconds=1);world.db.commit()
    assert confirm(client,world,p).status_code==409 and world.db.get(TeachingClass,world.target)
    assert client.post('/api/v1/teaching/classes/999999/delete-preview').status_code==404
    assert confirm(client,world,{'token':'missing'}).status_code==409

@pytest.mark.parametrize('change',['member','score','homework','link','label','reference','pending'])
def test_drift_rejected_even_if_counts_unchanged(client,world,change):
    from app.db import workspace_models as w
    p=preview(client,world);db=world.db
    if change=='member':db.query(w.TeachingClassMember).filter_by(teaching_class_id=world.target).first().source='changed'
    elif change=='score':db.get(w.ScoreFact,world.fact).score=82
    elif change=='homework':db.get(w.HomeworkAssignment,world.tasks[0]).revision+=1
    elif change=='link':db.get(w.HomeroomTeachingLink,world.link).version+=1
    elif change=='label':db.get(w.TeachingClass,world.target).label='改名'
    elif change=='reference':db.get(w.TeachingExamScope,world.exam).aggregates=[{'actual_count':3}]
    else:db.add(w.ImportBatch(token=uuid4().hex,data_domain='teaching',scope_json=json.dumps({'class_id':world.target})))
    db.commit();r=confirm(client,world,p);assert r.status_code==409,r.text
    db.expire_all();assert db.get(w.TeachingClass,world.target) and db.get(w.ScoreFact,world.fact)
    assert db.query(w.ImportBatch).filter_by(token=p['token']).one().status=='pending'

def test_rollback_on_failure(client,world,monkeypatch):
    from app.db import workspace_models as w
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy.orm import Session
    p=preview(client,world);original=Session.flush
    def fail(self,*args,**kwargs):
        if any(isinstance(r,w.TeachingClass) for r in self.deleted):raise RuntimeError('synthetic failure')
        return original(self,*args,**kwargs)
    with monkeypatch.context() as m:
        m.setattr(Session,'flush',fail);assert confirm(TestClient(app,raise_server_exceptions=False),world,p).status_code==500
    db=world.db;db.expire_all()
    assert db.get(w.TeachingClass,world.target) and db.get(w.ScoreFact,world.fact) and db.get(w.HomeworkAssignment,world.tasks[0]) and db.get(w.HomeroomTeachingLink,world.link)
    assert db.get(w.ImportBatch,world.pending).status=='pending' and db.get(w.ChatSession,world.chats[0]).status=='open'
    assert confirm(client,world,p).status_code==200

def test_unrelated_change_and_second_preview_safe(client,world):
    from app.db import workspace_models as w
    p=preview(client,world);preview(client,world)
    world.db.get(w.ScoreFact,world.keep).score=88;world.db.commit()
    assert confirm(client,world,p).status_code==200

def test_catalog_empty_after_deleting_only_class_without_carryover(client,world):
    from app.db import workspace_models as w
    db=world.db
    year=w.AcademicYear(name='最早空年-'+uuid4().hex[:8],start_date=date(2000,9,1),end_date=date(2001,7,1));db.add(year);db.flush()
    cls=w.TeachingClass(academic_year_id=year.id,subject='物理',label='唯一班');db.add(cls);db.commit();cid=cls.id
    p=preview(client,world,cid);assert confirm(client,world,p,'唯一班',cid).status_code==200
    r=client.get('/api/v1/teaching/classes',params={'academic_year_id':year.id})
    assert r.status_code==200 and r.json()=={'classes':[]}

def test_delete_high_then_lower_id_keeps_counter_monotonic(client,world):
    assert world.target>world.other
    assert confirm(client,world,preview(client,world)).status_code==200
    assert confirm(client,world,preview(client,world,world.other),'保留班',world.other).status_code==200
    r=client.post('/api/v1/teaching/classes',json={'academic_year_id':world.year,'subject':'物理','label':'重建班'})
    assert r.status_code==200,r.text
    assert r.json()['classes'][0]['class_id']>world.target
