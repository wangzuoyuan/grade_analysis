"""Route-level multi-metric lifecycle and evidence isolation."""
from datetime import date, timedelta
from app.db.models import SessionLocal
from app.db.workspace_models import ScoreFact, WsStudentNote


def iso(days=0):
    return (date.today() - timedelta(days=days)).isoformat()


def create(client, seed, **extra):
    body = dict(date=iso(), category='谈话', content='合成多指标跟进', problem='综合学习跟进',
                start_date=iso(30), review_date=iso(-14), force=True,
                targets=[dict(metric='total:主三门'), dict(metric='subject:语文')])
    body.update(extra)
    return client.post(f'/api/v1/homeroom/students/{seed.dang_id}/notes', json=body)


def patch(client, nid, **body):
    return client.patch(f'/api/v1/homeroom/notes/{nid}', json=body)


def test_lifecycle_and_partial_review(client, p2c4_seed):
    seed=p2c4_seed
    res=create(client,seed)
    assert res.status_code==200,res.text
    note=res.json(); nid=note['id']
    assert len(note['targets'])==2
    assert note['targets'][0]['baseline_value']['value']==210
    assert note['targets'][1]['baseline_value'] is None
    with SessionLocal() as db:
        db.add(ScoreFact(data_domain='homeroom',academic_year_id=seed.seed.ay_id,exam_name='多指标合成后测',
            exam_date=date.fromisoformat(iso(5)),class_ref_id=seed.seed.h6_id,identity_id=seed.dang_id,
            total_type='主三门',score=290,xueji_rank=190,source='multi-test'))
        db.commit()
    def contrast():
        return client.get(f'/api/v1/homeroom/diagnosis/review-contrast?follow_up_id={nid}').json()
    result=contrast()
    assert [r['status'] for r in result['results']]==['ready','pending']
    assert result['results'][0]['change']['value']==-20
    res=patch(client,nid,targets=[dict(metric='total:主三门',status='done'),dict(metric='subject:语文')])
    assert res.status_code==200,res.text
    assert res.json()['status']=='open' and res.json()['follow_up_done']==0
    closed=res.json()['targets'][0]['closed_at']
    assert contrast()['status']=='pending'
    res=patch(client,nid,targets=[dict(metric='total:主三门',status='done'),dict(metric='subject:语文',status='dismissed')])
    assert res.json()['status']=='done' and res.json()['follow_up_done']==1
    assert res.json()['targets'][0]['closed_at']==closed
    res=patch(client,nid,targets=[dict(metric='total:主三门',status='done'),dict(metric='subject:语文',status='dismissed'),dict(metric='subject:数学')])
    assert res.status_code==200,res.text
    assert res.json()['status']=='open'
    assert res.json()['targets'][0]['baseline_value']==note['targets'][0]['baseline_value']
    res=patch(client,nid,status='dismissed')
    assert res.json()['status']=='done'
    assert all(t['status']!='open' for t in res.json()['targets'])


def test_edit_removal_and_manual_units(client,p2c4_seed):
    res=create(client,p2c4_seed,targets=[dict(metric='subject:数学',baseline_value={'value':.4,'unit':'percentile'}),
        dict(metric='total:主三门',baseline_value={'value':300,'unit':'rank'})])
    assert res.status_code==200,res.text
    nid=res.json()['id']
    res=patch(client,nid,start_date=iso(90),targets=[dict(metric='subject:数学')])
    assert res.status_code==200,res.text
    assert res.json()['targets'][0]['baseline_value']['value']==.4
    assert res.json()['targets'][1]['status']=='dismissed'
    assert res.json()['targets'][1]['baseline_value']['value']==300
    res=patch(client,nid,targets=[dict(metric='subject:数学',baseline_value={'value':.3,'unit':'percentile'})])
    assert res.json()['targets'][0]['baseline_value']['value']==.3


def test_invalid_requests_atomic(client,p2c4_seed):
    with SessionLocal() as db: before=db.query(WsStudentNote).count()
    for body in [dict(target_metric='total:主三门'),dict(targets=[]),
        dict(targets=[dict(metric='subject:数学'),dict(metric='subject:数学')]),dict(targets=[dict(metric='total:五门')]),
        dict(targets=[dict(metric='subject:数学',baseline_value={'value':40})]),
        dict(targets=[dict(metric='subject:数学',baseline_value={'value':.4,'unit':'rank'})]),
        dict(targets=[dict(metric='total:主三门',baseline_value={'value':1.5})])]:
        res=create(client,p2c4_seed,**body)
        assert res.status_code==422,res.text
    with SessionLocal() as db: assert db.query(WsStudentNote).count()==before


def test_legacy_edit_and_scope(client,p2c4_seed):
    res=client.post(f'/api/v1/homeroom/students/{p2c4_seed.dang_id}/notes',json=dict(date=iso(),
        category='谈话',content='旧版请求',problem='旧单项',force=True,target_metric='total:主三门',start_date=iso(30)))
    note=res.json(); original=note['baseline_value']
    res=patch(client,note['id'],targets=[dict(metric='total:主三门'),dict(metric='subject:英语')])
    assert res.status_code==200,res.text
    assert res.json()['targets'][0]['baseline_value']==original
    assert client.patch(f"/api/v1/teaching/notes/{note['id']}",json={'targets':[{'metric':'subject_grade:物理'}]}).status_code==404
    assert client.get(f"/api/v1/teaching/diagnosis/review-contrast?follow_up_id={note['id']}").status_code==404
    opts=client.get('/api/v1/teaching/diagnosis/intervention-metrics').json()['options']
    assert [x['value'] for x in opts]==['subject_grade:物理']


def test_attention_snapshot_survives_adding_metrics(client,p2c4_seed):
    snapshot={'source':'attention-v1','signals':[{'code':'quality'}],'attention':{'value':1}}
    with SessionLocal() as db:
        note=WsStudentNote(data_domain='homeroom',person_id=p2c4_seed.dang_id,date=date.today(),
            category='谈话',content='合成证据跟进',status='open',baseline_value=snapshot,start_date=date.fromisoformat(iso(30)))
        db.add(note);db.commit();nid=note.id
    res=patch(client,nid,targets=[dict(metric='total:主三门')])
    assert res.status_code==200,res.text
    assert res.json()['baseline_value']==snapshot
    assert res.json()['targets'][0]['baseline_value']['value']==210
    contrast=client.get(f'/api/v1/homeroom/diagnosis/review-contrast?follow_up_id={nid}').json()
    assert contrast['results'][0]['baseline_source']=='auto'

    res=patch(client,nid,start_date=iso(90),targets=[dict(metric='total:主三门')])
    assert res.status_code==200,res.text
    assert res.json()['baseline_value']==snapshot
    assert res.json()['targets'][0]['baseline_value'] is None
