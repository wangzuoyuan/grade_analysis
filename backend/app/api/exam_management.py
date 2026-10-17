"""Workspace exam catalogue and transactional two-step deletion (no legacy tables)."""
import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api import domain_endpoint, _queries as q
from app.api.imports import _scope_snapshot
from app.api.shared import _load_preview_batch
from app.api.students_mgmt import _homeroom_ctx, _teaching_ctx
from app.core.errors import DomainError, InvalidScopeParam, ResourceOutOfScope, LinkVersionConflict
from app.db.models import get_db
from app.db.workspace_models import (AcademicYear, AdministrativeClass, TeachingClass, ScoreFact,
    WorkspaceClassAverage, TeachingExamScope, TeachingClassQuality, TeachingComparisonRoster,
    ImportBatch, SourceMap, SourceProjectionMap, WorkspaceExamEvent)
from app.api import exam_events as events

router = APIRouter(tags=['exam-management'])


class MergePreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    academic_year_id: Optional[int] = None
    class_id: Optional[int] = None
    source_event_id: int
    target_event_id: int


class MergeConfirm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str
    confirm_name: str
    accept_conflicts: bool = False


class MetricFillPreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    academic_year_id: Optional[int] = None
    class_id: Optional[int] = None
    fact_id: int


class MetricFillConfirm(BaseModel):
    token: str
    confirm_name: str


def _metric_fill_plan(db, ctx, fact_id):
    entry = next((e for e in q.readable_facts(db, ctx)
                  if e.fact.id == fact_id and not e.projected and e.source_domain == ctx.mode), None)
    if entry is None or entry.conflict_fact is None:
        raise ResourceOutOfScope('没有可确认补齐的关联来源')
    own, other = entry.fact, entry.conflict_fact
    if own.score is None or other.score is None or own.score != other.score:
        raise InvalidScopeParam('原始分缺失或不一致，请先核对缺考及原始成绩，不能补齐指标')
    changes = {f: getattr(other, f) for f in ('grade_score', 'grade_percentile')
               if getattr(own, f) is None and getattr(other, f) is not None}
    if not changes:
        raise InvalidScopeParam('没有可补齐的空白指标；已有不同值需到来源工作台明确修订')
    return own, other, changes, _digest([_row(own), _row(other), _scope_snapshot(ctx, None, 'metric_fill')])


@router.post('/{mode}/exams/metric-fill-preview')
@domain_endpoint
def metric_fill_preview(mode: str, req: MetricFillPreview, db: Session = Depends(get_db)):
    ctx = _ctx(db, mode, req.academic_year_id, req.class_id)
    own, other, changes, fingerprint = _metric_fill_plan(db, ctx, req.fact_id)
    token = secrets.token_hex(24)
    s = dict(kind='metric_fill', mode=mode, academic_year_id=ctx.academic_year_id,
             class_id=req.class_id, fact_id=own.id, fingerprint=fingerprint, changes=changes,
             source_fact_id=other.id, exam_name=own.exam_name)
    db.add(ImportBatch(token=token, data_domain=mode, status='pending', scope_json=json.dumps(s),
        content_digest=fingerprint, expires_at=datetime.utcnow() + timedelta(minutes=30)))
    db.commit()
    return dict(token=token, changes=changes, exam_name=own.exam_name, source_domain=other.data_domain)


@router.post('/{mode}/exams/metric-fill-confirm')
@domain_endpoint
def metric_fill_confirm(mode: str, req: MetricFillConfirm, db: Session = Depends(get_db)):
    batch = _load_preview_batch(db, req.token)
    s = json.loads(batch.scope_json or '{}')
    if s.get('kind') != 'metric_fill' or s.get('mode') != mode or batch.data_domain != mode:
        raise LinkVersionConflict('指标补齐凭据不属于当前工作台')
    try:
        locked = db.execute(update(ImportBatch).where(ImportBatch.id == batch.id,
            ImportBatch.status == 'pending').values(status='applying'))
        if locked.rowcount != 1:
            raise LinkVersionConflict('凭据已消费')
        db.expire_all()
        ctx = _ctx(db, mode, s['academic_year_id'], s.get('class_id'))
        own, other, changes, fingerprint = _metric_fill_plan(db, ctx, s['fact_id'])
        if fingerprint != s['fingerprint'] or req.confirm_name != s['exam_name']:
            raise LinkVersionConflict('数据已变化或考试名称不符，请重新预览')
        provenance = dict(own.field_sources or {})
        for field, value in changes.items():
            setattr(own, field, value)
            provenance[field] = dict(action='confirmed_metric_fill', source_domain=other.data_domain,
                                     source_fact_id=other.id, confirmed_at=datetime.utcnow().isoformat())
        own.field_sources = provenance
        own.data_revision += 1
        db.get(ImportBatch, batch.id).status = 'confirmed'
        db.commit()
        return dict(success=True, changes=changes)
    except Exception:
        db.rollback()
        raise


def _merge_plan(db, ctx, source, target):
    if source == target:
        raise InvalidScopeParam('请选择两场不同的考试')
    catalog = _catalog(db, ctx)
    visible = {g['event_id']: g for g in catalog.values() if g['event_id']}
    if source not in visible or target not in visible:
        raise ResourceOutOfScope('待关联考试不在当前可读范围')
    a, b = db.get(WorkspaceExamEvent, source), db.get(WorkspaceExamEvent, target)
    if a.merged_into_id or b.merged_into_id or a.grade != b.grade or a.grade is None:
        raise InvalidScopeParam('考试关联或年级已变化，请先核对明确年级后重新选择')
    ids = events.event_ids(db, source) + events.event_ids(db, target)
    snapshots = {m.__tablename__: [_row(r) for r in db.query(m).filter(m.exam_event_id.in_(ids)).order_by(m.id)]
                 for m in (ScoreFact, TeachingExamScope, WorkspaceClassAverage)}
    snapshots['events'] = [_row(db.get(WorkspaceExamEvent, i)) for i in sorted(ids)]
    averages = {}
    for r in snapshots['workspace_class_average']:
        key = (r['data_domain'], r['grade'], r['class_num'])
        value = (r['subject_averages'], r['total_averages'], r['teacher_name'], r['class_type'])
        if key in averages and averages[key] != value:
            raise InvalidScopeParam('两场考试的同班均分参照不同，请在各自来源核对并修订一致后再关联')
        averages[key] = value
    seen, differences = {}, []
    for entry in q.readable_facts(db, ctx):
        if entry.fact.exam_event_id not in ids:
            continue
        key = (entry.person_id, entry.fact.subject, entry.fact.total_type)
        if key in seen:
            fields = events.field_conflicts(seen[key], entry.fact)
            if fields:
                differences.append(dict(person_id=entry.person_id, subject=entry.fact.subject, fields=fields))
        seen[key] = entry.fact
    names = q.names_for(db, [d["person_id"] for d in differences])
    for difference in differences:
        difference["name"] = names.get(difference["person_id"])
    impact = dict(source=_public(visible[source]), target=_public(visible[target]), differences=differences,
        message='仅合并考试归属，保留全部来源与原始成绩；不同学科日期继续保留。字段差异不会自动覆盖或填补。')
    scope = _scope_snapshot(ctx, None, 'exam_merge')
    return impact, _digest(dict(scope=scope, snapshots=snapshots)), snapshots


@router.post('/{mode}/exams/merge-preview')
@domain_endpoint
def merge_preview(mode: str, req: MergePreview, db: Session = Depends(get_db)):
    ctx = _ctx(db, mode, req.academic_year_id, req.class_id)
    impact, fingerprint, snapshots = _merge_plan(db, ctx, req.source_event_id, req.target_event_id)
    token = secrets.token_hex(24)
    scope = dict(kind='exam_merge', mode=mode, academic_year_id=ctx.academic_year_id,
        class_id=req.class_id, source=req.source_event_id, target=req.target_event_id,
        fingerprint=fingerprint, impact=impact, event_snapshots=snapshots["events"])
    db.add(ImportBatch(token=token, data_domain=mode, status='pending',
        scope_json=json.dumps(scope, default=str, ensure_ascii=False), content_digest=fingerprint,
        expires_at=datetime.utcnow() + timedelta(minutes=30)))
    db.commit()
    return dict(token=token, **impact)


@router.post('/{mode}/exams/merge-confirm')
@domain_endpoint
def merge_confirm(mode: str, req: MergeConfirm, db: Session = Depends(get_db)):
    batch = _load_preview_batch(db, req.token)
    s = json.loads(batch.scope_json or '{}')
    if s.get('kind') != 'exam_merge' or s.get('mode') != mode or batch.data_domain != mode:
        raise LinkVersionConflict('考试关联凭据不属于当前工作台')
    try:
        locked = db.execute(update(ImportBatch).where(ImportBatch.id == batch.id,
            ImportBatch.status == 'pending').values(status='applying'))
        if locked.rowcount != 1:
            raise LinkVersionConflict('凭据已消费，请重新预览')
        db.expire_all()
        ctx = _ctx(db, mode, s['academic_year_id'], s.get('class_id'))
        impact, fingerprint, _ = _merge_plan(db, ctx, s['source'], s['target'])
        if fingerprint != s['fingerprint']:
            raise LinkVersionConflict('考试或来源已变化，请重新预览')
        if req.confirm_name != impact['target']['exam_name']:
            raise InvalidScopeParam('请输入目标考试完整名称')
        if impact['differences'] and not req.accept_conflicts:
            raise LinkVersionConflict('请确认保留来源字段差异，不会自动覆盖成绩')
        event = db.get(WorkspaceExamEvent, s['source'])
        event.merged_into_id = s['target']
        event.data_revision += 1
        db.get(WorkspaceExamEvent, s['target']).data_revision += 1
        batch = db.get(ImportBatch, batch.id)
        batch.status = 'confirmed'
        db.commit()
        return dict(success=True, event_id=s['target'])
    except Exception:
        db.rollback()
        raise


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _row(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def _ctx(db, mode, year, class_id=None):
    if mode == 'homeroom':
        return _homeroom_ctx(db, year, class_id, None)
    if mode == 'teaching':
        if class_id is not None:
            raise InvalidScopeParam('教学考试管理按本人全部教学班汇总，请勿传行政班参数')
        return _teaching_ctx(db, year, None, None, None)
    raise InvalidScopeParam('mode 必须是 homeroom 或 teaching')


def _catalog(db, ctx):
    groups = {}
    roots = events.canonical_map(db, ctx.academic_year_id)
    model = AdministrativeClass if ctx.mode == 'homeroom' else TeachingClass
    classes = {c.id: c for c in db.query(model).filter(model.id.in_(ctx.class_ids)).all()}
    platform = {e.id: e for e in db.query(TeachingExamScope).filter_by(
        academic_year_id=ctx.academic_year_id, subject=ctx.subject).all()} if ctx.mode == 'teaching' else {}

    def group(row, grade, kind='native'):
        eid = roots.get(row.exam_event_id) if row.exam_event_id else None
        event = db.get(WorkspaceExamEvent, eid) if eid else None
        key = f"event:{eid}" if eid else f"legacy:{row.exam_name}|{row.exam_date or getattr(row, 'source_exam_date', '') or ''}:grade:{grade}"
        key = _digest([ctx.mode, ctx.academic_year_id, ctx.subject, list(ctx.class_ids) if ctx.mode == "homeroom" else None, key])[:32]
        return groups.setdefault(key, dict(key=key, event_id=eid, kind=kind,
            exam_name=event.exam_name if event else row.exam_name, grade=event.grade if event else grade,
            academic_year_id=ctx.academic_year_id, subject=ctx.subject, platform_id=None,
            dates=set(), class_labels=set(), projected_ids=set(), sources=set(), conflicts=[],
            facts=[], averages=[], quality=[], exam=None, exams=[]))

    for e in platform.values():
        g = group(e, e.grade, 'platform')
        g['exam'] = e
        g['exams'].append(e)
        g['platform_id'] = e.id
        g['dates'].add(e.exam_date.isoformat())
        g['sources'].add('teaching')
    facts = db.query(ScoreFact).filter(ScoreFact.data_domain == ctx.mode,
        ScoreFact.academic_year_id == ctx.academic_year_id, ScoreFact.class_ref_id.in_(ctx.class_ids))
    if ctx.mode == 'teaching':
        facts = facts.filter(ScoreFact.subject == ctx.subject, ScoreFact.total_type.is_(None))
    for f in facts.order_by(ScoreFact.id):
        cls = classes.get(f.class_ref_id)
        g = group(f, cls.grade if cls else None)
        g['facts'].append(f)
        g['sources'].add(ctx.mode)
        if cls:
            g['class_labels'].add(cls.label)
        if f.exam_date or f.source_exam_date:
            g['dates'].add(str(f.exam_date or f.source_exam_date))
    if ctx.mode == 'homeroom':
        for avg in db.query(WorkspaceClassAverage).filter_by(data_domain='homeroom',
            academic_year_id=ctx.academic_year_id, grade=ctx.grade).order_by(WorkspaceClassAverage.id):
            g = group(avg, avg.grade)
            g['averages'].append(avg)
            g['sources'].add('homeroom')
            if avg.exam_date:
                g['dates'].add(avg.exam_date.isoformat())
    for quality in db.query(TeachingClassQuality).filter(TeachingClassQuality.exam_id.in_(platform)).order_by(TeachingClassQuality.id):
        e = platform[quality.exam_id]
        group(e, e.grade)['quality'].append(quality)
    readable = q.readable_facts(db, ctx)
    names = q.names_for(db, [e.person_id for e in readable])
    for entry in readable:
        cls = classes.get(entry.class_ref_id)
        g = group(entry.fact, cls.grade if cls else None, 'linked')
        g['sources'].add(entry.source_domain)
        if entry.conflict:
            if entry.conflict_fact is not None:
                g['sources'].add(entry.conflict_fact.data_domain)
            g['conflicts'].append(dict(person_id=entry.person_id, name=names.get(entry.person_id), fact_id=entry.fact.id if not entry.projected else None, fields=entry.conflict.get('fields', {})))
        if entry.source_domain == ctx.mode:
            continue
        g['projected_ids'].add(entry.person_id)
        if cls:
            g['class_labels'].add(cls.label)
        if entry.fact.exam_date:
            g['dates'].add(entry.fact.exam_date.isoformat())
    return groups


def _public(g):
    native = bool(g['facts'] or g['averages'] or g['exam'])
    return dict(key=g['key'], event_id=g['event_id'], sources=sorted(g['sources']), conflicts=g['conflicts'], exam_name=g['exam_name'], grade=g['grade'], subject=g['subject'],
        academic_year_id=g['academic_year_id'], platform_exam_id=g['platform_id'],
        dates=sorted(g['dates']), classes=sorted(g['class_labels']), kind=g['kind'], can_delete=native,
        student_count=len({f.identity_id for f in g['facts']}), score_count=len(g['facts']),
        average_count=len(g['averages']), quality_count=len(g['quality']),
        projected_students=len(g['projected_ids']),
        source_label='本工作台导入' if native else '关联来源（只读）')


def _pending_imports(db, ctx, g):
    result = []
    for batch in db.query(ImportBatch).filter_by(data_domain=ctx.mode, status='pending'):
        try:
            s = json.loads(batch.scope_json or '{}')
        except (ValueError, TypeError):
            continue
        if s.get('academic_year_id') != ctx.academic_year_id:
            continue
        if s.get('kind') == 'teaching_platform':
            if s.get('exam_name') == g['exam_name'] and s.get('subject') == ctx.subject and s.get('grade') == g['grade']:
                result.append(batch)
        elif s.get('kind') == 'import_preview':
            if ctx.mode == 'homeroom' and not set(s.get('class_ids') or []).intersection(ctx.class_ids):
                continue
            if ctx.mode == 'teaching' and (s.get('subject') != ctx.subject or not set(s.get('class_ids') or []).intersection(ctx.class_ids)):
                continue
            if any(f.get('exam_name') == g['exam_name'] and (g['grade'] is None or f.get('grade') in (None, g['grade'])) for f in s.get('files', [])):
                result.append(batch)
    return result


def _mappings(db, g):
    targets = {'score_fact': [f.id for f in g['facts']], 'workspace_class_average': [a.id for a in g['averages']]}
    maps, projections = [], []
    for table, ids in targets.items():
        if ids:
            maps.extend(db.query(SourceMap).filter(SourceMap.target_table == table, SourceMap.target_id.in_(ids)).all())
            projections.extend(db.query(SourceProjectionMap).filter(SourceProjectionMap.target_table == table, SourceProjectionMap.target_id.in_(ids)).all())
    return maps, projections


def _plan(db, ctx, key):
    g = _catalog(db, ctx).get(key)
    if g is None or not _public(g)['can_delete']:
        raise ResourceOutOfScope('该考试没有可删除的本工作台数据；关联成绩请在来源工作台管理')
    for exam in g['exams']:
        all_references = {f[0] for f in db.query(ScoreFact.id).filter_by(teaching_exam_id=exam.id)}
        if all_references - {f.id for f in g['facts']}:
            raise ResourceOutOfScope('该考试还包含当前工作台范围外的成绩，不能整场删除')
    pending = _pending_imports(db, ctx, g)
    roster = None
    if g['exam']:
        roster = db.query(TeachingComparisonRoster).filter_by(academic_year_id=ctx.academic_year_id,
            grade=g['grade'], subject=ctx.subject).order_by(TeachingComparisonRoster.version.desc()).first()
    maps, projections = _mappings(db, g)
    snapshots = {'score_fact': [_row(f) for f in g['facts']],
        'workspace_class_average': [_row(a) for a in g['averages']],
        'teaching_class_quality': [_row(v) for v in g['quality']],
        'teaching_exam_scope': [_row(e) for e in g['exams']],
        'source_map': [_row(v) for v in maps], 'source_projection_map': [_row(v) for v in projections]}
    scope = _scope_snapshot(ctx, None, 'exam_management')
    scope['teacher_id'] = ctx.teacher_id
    fingerprint = _digest({'scope': scope, 'rows': snapshots, 'roster': _row(roster) if roster else None,
        'pending': [(b.id, b.status, b.scope_json) for b in sorted(pending, key=lambda b: b.id)]})
    return g, pending, roster, maps, projections, snapshots, fingerprint


def _refresh_roster(db, ctx, g, roster):
    exam = g['exam']
    if exam is None or roster is None or roster.source_exam_date not in {e.exam_date for e in g['exams']}:
        return
    remaining = db.query(TeachingExamScope).filter(TeachingExamScope.academic_year_id == ctx.academic_year_id,
        TeachingExamScope.subject == ctx.subject, TeachingExamScope.grade == g['grade'], ~TeachingExamScope.id.in_([e.id for e in g['exams']])
    ).order_by(TeachingExamScope.exam_date.desc(), TeachingExamScope.id.desc()).all()
    quality_ids = {row[0] for row in db.query(TeachingClassQuality.exam_id).filter(
        TeachingClassQuality.exam_id.in_([e.id for e in remaining])).distinct()}
    previous = next((e for e in remaining if e.class_snapshot and e.id in quality_ids), None)
    year = db.get(AcademicYear, ctx.academic_year_id)
    db.add(TeachingComparisonRoster(academic_year_id=ctx.academic_year_id, grade=g['grade'], subject=ctx.subject,
        version=roster.version + 1, source_exam_date=previous.exam_date if previous else year.start_date,
        classes=previous.class_snapshot if previous else []))


class DeletePreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    academic_year_id: Optional[int] = None
    class_id: Optional[int] = None
    exam_key: str


class DeleteConfirm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str
    confirm_name: str


@router.get('/{mode}/exams')
@domain_endpoint
def list_exams(mode: str, academic_year_id: Optional[int] = None, class_id: Optional[int] = None,
               grade: Optional[int] = None, teaching_class_id: Optional[int] = None, db: Session = Depends(get_db)):
    if teaching_class_id is not None:
        raise InvalidScopeParam('考试管理不支持单班参数；教学工作台按本人全部教学班管理整场考试')
    if grade is not None and grade not in (1, 2, 3):
        raise InvalidScopeParam('grade 必须是1、2或3')
    ctx = _ctx(db, mode, academic_year_id, class_id)
    exams = [_public(g) for g in _catalog(db, ctx).values() if grade is None or g['grade'] == grade]
    exams.sort(key=lambda e: (max(e['dates'], default=''), e['exam_name'], e['key']), reverse=True)
    return {'exams': exams, 'scope': {'academic_year_id': ctx.academic_year_id, 'mode': mode,
        'subject': ctx.subject, 'grade': ctx.grade, 'class_ids': list(ctx.class_ids),
        'description': '本人全部教学班，按考试所属年级管理整场' if mode == 'teaching' else '本人绑定行政班'}}


@router.post('/{mode}/exams/delete-preview')
@domain_endpoint
def delete_preview(mode: str, req: DeletePreview, db: Session = Depends(get_db)):
    ctx = _ctx(db, mode, req.academic_year_id, req.class_id)
    g, pending, _, _, _, _, fingerprint = _plan(db, ctx, req.exam_key)
    token = secrets.token_hex(24)
    expires = datetime.utcnow() + timedelta(minutes=30)
    snapshot = dict(kind='exam_delete', mode=mode, academic_year_id=ctx.academic_year_id,
        class_id=ctx.class_ids[0] if mode == 'homeroom' else None, exam_key=req.exam_key,
        exam_name=g['exam_name'], fingerprint=fingerprint)
    db.add(ImportBatch(token=token, data_domain=mode, scope_json=json.dumps(snapshot),
        content_digest=fingerprint, expires_at=expires))
    db.commit()
    return {'token': token, 'expires_at': expires.isoformat(), 'exam': _public(g), 'pending_imports': len(pending)}


@router.post('/{mode}/exams/delete-confirm')
@domain_endpoint
def delete_confirm(mode: str, req: DeleteConfirm, db: Session = Depends(get_db)):
    batch = _load_preview_batch(db, req.token)
    s = json.loads(batch.scope_json or '{}')
    if s.get('kind') != 'exam_delete' or s.get('mode') != mode or batch.data_domain != mode:
        raise LinkVersionConflict('删除凭据不属于当前工作台，请重新预览')
    if req.confirm_name != s.get('exam_name'):
        raise InvalidScopeParam('请输入完整考试名称确认删除')
    try:
        # Acquire the SQLite writer lock before rereading any deletion target.
        locked = db.execute(update(ImportBatch).where(ImportBatch.id == batch.id,
            ImportBatch.status == 'pending').values(status='applying'))
        if locked.rowcount != 1:
            raise LinkVersionConflict('该删除凭据已被使用，请刷新列表')
        db.expire_all()
        try:
            ctx = _ctx(db, mode, s['academic_year_id'], s.get('class_id'))
        except DomainError:
            raise LinkVersionConflict('工作台范围已变化，请刷新后重新预览') from None
        try:
            g, pending, roster, maps, projections, snapshots, fingerprint = _plan(db, ctx, s['exam_key'])
        except ResourceOutOfScope:
            raise LinkVersionConflict('考试或来源范围已变化，请重新预览') from None
        if fingerprint != s['fingerprint']:
            raise LinkVersionConflict('考试数据或工作台范围已变化，请重新预览后删除')
        result = _public(g)
        _refresh_roster(db, ctx, g, roster)
        for b in pending:
            b.status = 'invalidated'
        for row in maps:
            row.active = 0
        for row in projections:
            row.status, row.reason = 'deleted', 'exam_management_delete'
        for row in [*g['facts'], *g['averages'], *g['quality']]:
            db.delete(row)
        db.flush()  # FK children first, then the platform exam.
        for exam in g['exams']:
            db.delete(exam)
        batch = db.get(ImportBatch, batch.id)
        batch.scope_json = json.dumps({**s, 'deleted_at': datetime.utcnow().isoformat(),
            'deleted_rows': snapshots, 'invalidated_import_ids': [b.id for b in pending]}, default=str, ensure_ascii=False)
        batch.status = 'confirmed'
        db.commit()
        return {'success': True, 'exam': result, 'invalidated_imports': len(pending)}
    except OperationalError as exc:
        db.rollback()
        if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
            raise LinkVersionConflict('数据正在更新，请刷新后重新预览') from None
        raise
    except Exception:
        db.rollback()
        raise
