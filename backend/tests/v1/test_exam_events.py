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
    # 2026-10 两域成绩不互通：班主任域事件不再投影进教学目录，跨域 merge 预览会被
    # 目录可见性门拦下（404），merge 用例改用同域「第二场教学事件」作为源事件，
    # 模拟同一场考试分两次上传（旧模板 + 教学平台）后按 id 关联的真实场景。
    second = wm.WorkspaceExamEvent(academic_year_id=world.ay, grade=2, exam_name='合成管理考乙')
    db.add(second); db.flush()
    world.teaching_event2 = second.id
    db.add(wm.ScoreFact(data_domain='teaching', academic_year_id=world.ay, class_ref_id=world.ta,
        identity_id=world.people['ta'], exam_name='合成管理考乙', exam_date=date(2025,11,21),
        subject='物理', score=88, teaching_exam_id=None, exam_event_id=second.id))
    db.commit()
    return world


def merge(client, w):
    p = client.post('/api/v1/teaching/exams/merge-preview', json=dict(academic_year_id=w.ay,
        source_event_id=w.teaching_event2, target_event_id=w.teaching_event))
    assert p.status_code == 200, p.text
    return p.json()


def test_history_merge_is_explicit_and_does_not_overwrite_scores(client, events_world):
    w = events_world
    before = [(f.id, f.score, f.grade_score) for f in w.db.query(wm.ScoreFact).order_by(wm.ScoreFact.id)]
    rows = [e for e in catalog(client, w) if e['event_id'] in (w.homeroom_event, w.teaching_event)]
    # 2026-10 两域成绩不互通：教学目录只列本域事件，班主任域的同名事件不再可见（原为两行）。
    assert len(rows) == 1 and rows[0]['event_id'] == w.teaching_event
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
    # 2026-10 两域成绩不互通：合并后 sources 只剩本域；conflicts 只剩同域重复来源注记
    #（跨域 teaching_score/homeroom_score 冲突标记不再产生）。
    assert len(rows) == 1 and rows[0]['sources'] == ['teaching'] and rows[0]['conflicts']
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
    # 2026-10 两域成绩不互通：merge 源事件改为同域第二场教学事件，指纹失配时同样不得标记已关联。
    assert w.db.get(wm.WorkspaceExamEvent, w.teaching_event2).merged_into_id is None
    assert client.post('/api/v1/teaching/exams/merge-preview', json=dict(academic_year_id=w.ay,
        source_event_id=w.teaching_event2, target_event_id=99999999)).status_code == 404
    # 2026-10 两域成绩不互通：班主任域事件已不在教学目录可见范围，作为 merge 源同样 404。
    assert client.post('/api/v1/teaching/exams/merge-preview', json=dict(academic_year_id=w.ay,
        source_event_id=w.homeroom_event, target_event_id=w.teaching_event)).status_code == 404


def test_confirmed_fill_requires_equal_present_raw_score_and_retains_provenance(client, events_world):
    w = events_world
    # 2026-10 两域成绩不互通：补齐来源不再可能是对侧域事实（跨域冲突注记已随投影移除），
    # 改为同域重复导入的第二条教学事实（较大 id 的一条承载冲突注记、即被补齐行 own）。
    own = w.db.query(wm.ScoreFact).filter_by(academic_year_id=w.ay, exam_name='合成管理考乙').one()
    other = w.db.get(wm.ScoreFact, w.facts[0])
    own.score = other.score; own.grade_score = None; other.grade_score = 72
    w.db.commit()
    p=merge(client,w)
    assert client.post('/api/v1/teaching/exams/merge-confirm', json=dict(token=p['token'],
        confirm_name=p['target']['exam_name'],accept_conflicts=True)).status_code==200
    request=dict(academic_year_id=w.ay, fact_id=own.id)
    p=client.post('/api/v1/teaching/exams/metric-fill-preview',json=request)
    assert p.status_code==200,p.text
    assert p.json()['changes']=={'grade_score':72}
    w.db.expire_all(); assert w.db.get(wm.ScoreFact,own.id).grade_score is None
    r=client.post('/api/v1/teaching/exams/metric-fill-confirm',json=dict(token=p.json()['token'],confirm_name=p.json()['exam_name']))
    assert r.status_code==200,r.text
    w.db.expire_all(); own=w.db.get(wm.ScoreFact,own.id)
    assert own.grade_score==72 and own.field_sources['grade_score']['source_fact_id']==other.id
    own.grade_score=None; own.score=None; w.db.commit()
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
    # 2026-10 两域成绩不互通：教学侧内容删空后，合并事件不再因对侧域投影滞留教学目录
    #（原为 can_delete=False、projected_students=1 的只读行），班主任域事实仍只在本域可见。
    assert all(e['event_id']!=w.teaching_event for e in catalog(client,w))


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
