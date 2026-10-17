"""Canonical identity, consent, conflicts and deletion use only synthetic records."""
from datetime import date
import pytest
from tests.v1.test_exam_management import world, catalog
from app.db import workspace_models as wm


@pytest.fixture
def events_world(world):
    db = world.db
    for domain in ('homeroom', 'teaching'):
        event = wm.WorkspaceExamEvent(academic_year_id=world.ay, grade=2, exam_name='合成管理考')
        db.add(event); db.flush()
        setattr(world, domain + '_event', event.id)
        for f in db.query(wm.ScoreFact).filter_by(academic_year_id=world.ay, data_domain=domain, exam_name='合成管理考'):
            if domain == 'homeroom' or f.teaching_exam_id == world.exam:
                f.exam_event_id = event.id
        if domain == 'teaching':
            db.get(wm.TeachingExamScope, world.exam).exam_event_id = event.id
    link = wm.HomeroomTeachingLink(admin_class_id=world.h, teaching_class_id=world.ta,
        academic_year_id=world.ay, subject='物理', status='active', valid_from=date(2025,9,1),
        share_categories='current_subject_score', share_history_from=date(2025,9,1))
    db.add(link); db.flush()
    db.add(wm.LinkedStudent(link_id=link.id, homeroom_identity_id=world.people['ha'],
        teaching_identity_id=world.people['ta'], confirm_basis='synthetic'))
    db.commit()
    return world


def merge(client, w):
    p = client.post('/api/v1/teaching/exams/merge-preview', json=dict(academic_year_id=w.ay,
        source_event_id=w.homeroom_event, target_event_id=w.teaching_event))
    assert p.status_code == 200, p.text
    return p.json()


def test_history_merge_is_explicit_and_does_not_overwrite_scores(client, events_world):
    w = events_world
    before = [(f.id, f.score, f.grade_score) for f in w.db.query(wm.ScoreFact).order_by(wm.ScoreFact.id)]
    rows = [e for e in catalog(client, w) if e['event_id'] in (w.homeroom_event, w.teaching_event)]
    assert len(rows) == 2
    p = merge(client, w)
    assert p['differences'] and 'score' in p['differences'][0]['fields']
    body = dict(token=p['token'], confirm_name=p['target']['exam_name'])
    assert client.post('/api/v1/teaching/exams/merge-confirm', json=body).status_code == 409
    r = client.post('/api/v1/teaching/exams/merge-confirm', json={**body, 'accept_conflicts':True})
    assert r.status_code == 200, r.text
    assert client.post('/api/v1/teaching/exams/merge-confirm', json={**body, 'accept_conflicts':True}).status_code == 409
    w.db.expire_all()
    assert [(f.id, f.score, f.grade_score) for f in w.db.query(wm.ScoreFact).order_by(wm.ScoreFact.id)] == before
    rows = [e for e in catalog(client, w) if e['event_id'] == w.teaching_event]
    assert len(rows) == 1 and rows[0]['sources'] == ['homeroom', 'teaching'] and rows[0]['conflicts']
    for mode, key in [('teaching', 'teaching_class_id'), ('homeroom', 'class_id')]:
        response = client.get(f'/api/v1/{mode}/diagnosis/homework-students', params={
            'academic_year_id': w.ay, key: w.ta if mode == 'teaching' else w.h, 'subject':'物理'})
        assert response.status_code == 200, response.text
        for s in response.json()['students']:
            assert sum(t['exam_name'] == '合成管理考' for t in s['timeline']) <= 1


def test_merge_rechecks_fingerprint_and_scope(client, events_world):
    w = events_world
    p = merge(client, w)
    fact = w.db.get(wm.ScoreFact, w.facts[0]); fact.grade_score = 66; w.db.commit()
    r = client.post('/api/v1/teaching/exams/merge-confirm', json=dict(token=p['token'],
        confirm_name=p['target']['exam_name'], accept_conflicts=True))
    assert r.status_code == 409
    w.db.expire_all()
    assert w.db.get(wm.WorkspaceExamEvent, w.homeroom_event).merged_into_id is None
    assert client.post('/api/v1/teaching/exams/merge-preview', json=dict(academic_year_id=w.ay,
        source_event_id=w.homeroom_event, target_event_id=99999999)).status_code == 404


def test_confirmed_fill_requires_equal_present_raw_score_and_retains_provenance(client, events_world):
    w = events_world
    h = w.db.query(wm.ScoreFact).filter_by(academic_year_id=w.ay, data_domain='homeroom',
        identity_id=w.people['ha'], subject='物理').one()
    t = w.db.get(wm.ScoreFact, w.facts[0])
    h.score=t.score; h.grade_score=72; t.grade_score=None
    w.db.commit()
    p=merge(client,w)
    assert client.post('/api/v1/teaching/exams/merge-confirm', json=dict(token=p['token'],
        confirm_name=p['target']['exam_name'],accept_conflicts=True)).status_code==200
    request=dict(academic_year_id=w.ay, fact_id=t.id)
    p=client.post('/api/v1/teaching/exams/metric-fill-preview',json=request)
    assert p.status_code==200,p.text
    assert p.json()['changes']=={'grade_score':72}
    w.db.expire_all(); assert w.db.get(wm.ScoreFact,t.id).grade_score is None
    r=client.post('/api/v1/teaching/exams/metric-fill-confirm',json=dict(token=p.json()['token'],confirm_name=p.json()['exam_name']))
    assert r.status_code==200,r.text
    w.db.expire_all(); t=w.db.get(wm.ScoreFact,t.id)
    assert t.grade_score==72 and t.field_sources['grade_score']['source_fact_id']==h.id
    t.grade_score=None; t.score=None; w.db.commit()
    assert client.post('/api/v1/teaching/exams/metric-fill-preview',json=request).status_code==422


def test_delete_merged_event_only_deletes_owned_domain(client, events_world):
    w=events_world; p=merge(client,w)
    assert client.post('/api/v1/teaching/exams/merge-confirm',json=dict(token=p['token'],
        confirm_name=p['target']['exam_name'],accept_conflicts=True)).status_code==200
    row=next(e for e in catalog(client,w) if e['event_id']==w.teaching_event)
    p=client.post('/api/v1/teaching/exams/delete-preview',json=dict(academic_year_id=w.ay,exam_key=row['key']))
    assert p.status_code==200,p.text
    r=client.post('/api/v1/teaching/exams/delete-confirm',json=dict(token=p.json()['token'],confirm_name=row['exam_name']))
    assert r.status_code==200,r.text
    w.db.expire_all()
    assert w.db.query(wm.ScoreFact).filter_by(exam_event_id=w.homeroom_event,data_domain='homeroom').count()==4
    linked=next(e for e in catalog(client,w) if e['event_id']==w.teaching_event)
    assert not linked['can_delete'] and linked['projected_students']==1


def test_event_metadata_can_match_without_enabling_score_sharing(client, events_world):
    from app.api import exam_events as service
    from app.api.exam_management import _ctx
    w=events_world
    for link in w.db.query(wm.HomeroomTeachingLink).filter_by(academic_year_id=w.ay):
        link.status='cancelled'
    # One existing homeroom event and no same-name teaching event.
    t=w.db.get(wm.WorkspaceExamEvent,w.teaching_event);t.exam_name='另一合成考'
    for f in w.db.query(wm.ScoreFact).filter_by(exam_event_id=t.id): f.exam_name=t.exam_name
    w.db.get(wm.TeachingExamScope,w.exam).exam_name=t.exam_name
    w.db.commit()
    ctx=_ctx(w.db,'teaching',w.ay)
    resolution=service.candidates(w.db,ctx,'合成管理考',2,'2025-11-21')
    assert resolution['selected']==w.homeroom_event
    from app.api import _queries as q
    assert all(e.source_domain=='teaching' for e in q.readable_facts(w.db,ctx))
