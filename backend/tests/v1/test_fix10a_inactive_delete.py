"""修复 10a：停用教学班不再锁死多班联考的整场删除（合成数据）。

场景：1班+2班共用一场教学平台联考（同一 TeachingExamScope），2班停用后
旧代码把 2班成绩当「当前工作台范围外的成绩」→ 删除预览/删除永远 404。
修复后：删除路径把本人【全部】教学班（含 inactive）的成绩并入待删范围；
他人/其他范围的成绩仍由守卫阻止（404 不放松）。
"""
import json
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def world(v1_seed, db_session):
    """两个教学班共用一场平台联考；tb 停用后触发修复场景。"""
    from app.db import workspace_models as w
    db = db_session
    ay = w.AcademicYear(name='停用删除合成-' + uuid4().hex[:10],
                        start_date=date(2025, 9, 1), end_date=date(2026, 8, 31))
    db.add(ay); db.flush()
    ta = w.TeachingClass(academic_year_id=ay.id, grade=2, subject='物理', label='合成在用班')
    tb = w.TeachingClass(academic_year_id=ay.id, grade=2, subject='物理', label='合成停用班')
    db.add_all([ta, tb]); db.flush()
    people = {}
    for key, cls in [('ta', ta), ('tb', tb)]:
        p = w.WsStudentIdentity(data_domain='teaching', display_name='合成停用学生' + key)
        db.add(p); db.flush(); people[key] = p.id
        db.add(w.TeachingClassMember(teaching_class_id=cls.id, identity_id=p.id,
                                     valid_from=date(2025, 9, 1)))
    exam = w.TeachingExamScope(academic_year_id=ay.id, grade=2, subject='物理', exam_name='合成联考',
        exam_date=date(2025, 11, 21), class_snapshot=[{'class_kind': 'teaching', 'class_label': '合成在用班'}],
        aggregates=[{'synthetic': True}])
    db.add(exam); db.flush()
    facts = []
    for key, cls in [('ta', ta), ('tb', tb)]:
        f = w.ScoreFact(data_domain='teaching', academic_year_id=ay.id, class_ref_id=cls.id,
            identity_id=people[key], exam_name=exam.exam_name, exam_date=exam.exam_date,
            subject='物理', score=70, teaching_exam_id=exam.id)
        db.add(f); facts.append(f)
    db.commit()
    return SimpleNamespace(db=db, ay=ay.id, ta=ta.id, tb=tb.id,
                           people=people, exam=exam.id, facts=[f.id for f in facts])


def _catalog(client, world):
    r = client.get('/api/v1/teaching/exams', params={'academic_year_id': world.ay})
    assert r.status_code == 200, r.text
    return r.json()['exams']


def _preview(client, world):
    exam = next(e for e in _catalog(client, world) if e['exam_name'] == '合成联考')
    return client.post('/api/v1/teaching/exams/delete-preview', json={
        'academic_year_id': world.ay, 'exam_key': exam['key']})


def _confirm(client, body):
    return client.post('/api/v1/teaching/exams/delete-confirm', json={
        'token': body['token'], 'confirm_name': body['exam']['exam_name']})


def test_inactive_class_no_longer_locks_platform_delete(client, world):
    """probe2 10A 场景重放：停用一班后预览可过、确认后两班成绩都清、考试删除。"""
    from app.db import workspace_models as w
    db = world.db
    db.get(w.TeachingClass, world.tb).status = 'inactive'
    db.commit()

    resp = _preview(client, world)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 预览如实反映将删除的内容：两个班的成绩都在列。
    assert body['exam']['score_count'] == 2
    assert body['exam']['student_count'] == 2
    assert sorted(body['exam']['classes']) == ['合成停用班', '合成在用班']

    r = _confirm(client, body)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(w.TeachingExamScope, world.exam) is None
    assert db.query(w.ScoreFact).filter_by(teaching_exam_id=world.exam).count() == 0
    for fid in world.facts:
        assert db.get(w.ScoreFact, fid) is None
    # 审计快照里两班成绩都有留痕。
    audit = db.query(w.ImportBatch).filter_by(token=body['token']).one()
    assert len(json.loads(audit.scope_json)['deleted_rows']['score_fact']) == 2


def test_active_classes_delete_unchanged_without_inactive(client, world):
    """全部在用班的常规删除路径不受影响（计数与删除保持原口径）。"""
    from app.db import workspace_models as w
    resp = _preview(client, world)
    assert resp.status_code == 200, resp.text
    assert resp.json()['exam']['score_count'] == 2
    r = _confirm(client, resp.json())
    assert r.status_code == 200, r.text
    world.db.expire_all()
    assert world.db.query(w.ScoreFact).filter_by(teaching_exam_id=world.exam).count() == 0
    assert world.db.get(w.TeachingExamScope, world.exam) is None


def test_foreign_reference_still_blocks_delete_with_inactive_present(client, world):
    """停用班在场时，范围外（他学年/他人）成绩仍 404 阻止整场删除（守卫不放松）。"""
    from app.db import workspace_models as w
    db = world.db
    db.get(w.TeachingClass, world.tb).status = 'inactive'
    # 范围外样本：另一学年的物理班（非本人本学年班级）挂上同一平台考试的引用。
    other_year = w.AcademicYear(name='停用删除他年-' + uuid4().hex[:10],
                                start_date=date(2026, 9, 1), end_date=date(2027, 8, 31))
    db.add(other_year); db.flush()
    stranger = w.TeachingClass(academic_year_id=other_year.id, grade=2, subject='物理', label='合成他年班')
    db.add(stranger); db.flush()
    outsider = w.WsStudentIdentity(data_domain='teaching', display_name='合成范围外学生')
    db.add(outsider); db.flush()
    db.add(w.TeachingClassMember(teaching_class_id=stranger.id, identity_id=outsider.id,
                                 valid_from=date(2026, 9, 1)))
    db.add(w.ScoreFact(data_domain='teaching', academic_year_id=other_year.id,
        class_ref_id=stranger.id, identity_id=outsider.id, exam_name='合成联考',
        exam_date=date(2025, 11, 21), subject='物理', score=10, teaching_exam_id=world.exam))
    db.commit()

    r = _preview(client, world)
    assert r.status_code == 404, r.text
    assert '范围外' in r.json()['detail']
    db.expire_all()
    # 未发生任何删除。
    assert db.get(w.TeachingExamScope, world.exam) is not None
    assert db.query(w.ScoreFact).filter_by(teaching_exam_id=world.exam).count() == 3


def test_inactive_facts_deactivated_between_preview_and_confirm_rejected(client, world):
    """预览与确认之间范围变化（有班停用）→ 指纹变化，要求重新预览（安全语义保留）。"""
    from app.db import workspace_models as w
    resp = _preview(client, world)
    assert resp.status_code == 200
    body = resp.json()
    assert body['exam']['score_count'] == 2
    world.db.get(w.TeachingClass, world.tb).status = 'inactive'
    world.db.commit()
    r = _confirm(client, body)
    assert r.status_code == 409, r.text
    world.db.expire_all()
    assert world.db.get(w.TeachingExamScope, world.exam) is not None
    assert world.db.query(w.ScoreFact).filter_by(teaching_exam_id=world.exam).count() == 2


def test_legacy_name_group_without_platform_not_affected(client, world):
    """legacy 分组（无平台考试）本就不触发守卫；停用班不影响该口径。"""
    from app.db import workspace_models as w
    db = world.db
    db.get(w.TeachingClass, world.tb).status = 'inactive'
    db.commit()
    db.add(w.ScoreFact(data_domain='teaching', academic_year_id=world.ay, class_ref_id=world.ta,
        identity_id=world.people['ta'], exam_name='合成旧考', exam_date=date(2025, 10, 1),
        subject='物理', score=60))
    db.commit()
    exam = next(e for e in _catalog(client, world) if e['exam_name'] == '合成旧考')
    r = client.post('/api/v1/teaching/exams/delete-preview', json={
        'academic_year_id': world.ay, 'exam_key': exam['key']})
    assert r.status_code == 200, r.text
    assert r.json()['exam']['score_count'] == 1
