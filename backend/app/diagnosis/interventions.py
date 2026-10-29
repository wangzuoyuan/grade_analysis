"""Metric persistence and status aggregation; callers resolve the note's scope first."""
from datetime import datetime
from app.core.errors import InvalidScopeParam
from app.db.workspace_models import WsInterventionTarget


def target_items(note):
    rows = list(note.targets)
    if rows:
        return [dict(metric=t.metric, baseline_value=t.baseline_value, status=t.status,
                     closed_at=t.closed_at.isoformat() if t.closed_at else None) for t in rows]
    if note.status and note.target_metric:
        return [dict(metric=note.target_metric, baseline_value=note.baseline_value,
                     status=note.status, closed_at=None)]
    return []


def check_input(req):
    fields = req.model_fields_set
    if 'targets' in fields and {'target_metric', 'baseline_value'} & fields:
        raise InvalidScopeParam('targets cannot be combined with legacy metric fields', details={'param': 'targets'})
    if 'targets' in fields and not req.targets:
        raise InvalidScopeParam('至少选择一个目标指标', details={'param': 'targets'})


def options_for(db, ctx):
    from app.analysis.definitions import metric_options
    from app.rollover.service import get_active_grade
    grade = ctx.grade if ctx.grade is not None else int(get_active_grade(db))
    options = metric_options(grade)
    if ctx.mode == 'teaching':
        options = [m for m in options if m['value'] in (f'subject:{ctx.subject}', f'subject_grade:{ctx.subject}')]
    return options


def ensure_legacy_target(note):
    if not note.targets and note.status and note.target_metric:
        note.targets.append(WsInterventionTarget(metric=note.target_metric,
            baseline_value=note.baseline_value, status=note.status))


def aggregate(note):
    if not note.targets:
        return
    statuses = [t.status for t in note.targets]
    note.status = 'open' if 'open' in statuses else 'done' if 'done' in statuses else 'dismissed'
    note.follow_up_done = 0 if note.status == 'open' else 1
    # Legacy fields remain the first metric's projection, never overwrite attention snapshots.
    first = note.targets[0]
    note.target_metric = first.metric
    if (note.baseline_value or {}).get("source") != "attention-v1":
        note.baseline_value = first.baseline_value
    note.updated_at = datetime.utcnow()


def apply_targets(db, ctx, note, inputs, anchor_changed=False):
    from app.api.students_mgmt import _validate_manual_baseline
    from app.diagnosis.review import capture_baseline, _is_strictly_before, _numeric_baseline_value, metric_meta_or_422, unit_of_metric
    import math
    ensure_legacy_target(note)
    existing = {t.metric: t for t in note.targets}
    allowed = {o['value'] for o in options_for(db, ctx)}
    seen = set()
    for item in inputs:
        metric = item.metric.strip()
        if metric in seen or not metric:
            raise InvalidScopeParam('目标指标不可重复或为空', details={'param': 'targets'})
        seen.add(metric)
        old = existing.get(metric)
        # Historical unsupported metrics can be retained/closed, but cannot expand read access.
        if metric not in allowed and (old is None or (item.status == 'open' and old.status != 'open')):
            raise InvalidScopeParam('目标指标不适用于当前学段或任教学科', details={'param': 'targets', 'metric': metric})
        row = old or WsInterventionTarget(metric=metric, status='open')
        if old is None:
            note.targets.append(row)
        if 'baseline_value' in item.model_fields_set and item.baseline_value is not None:
            _validate_manual_baseline(db, ctx, item.baseline_value, metric)
            baseline = dict(item.baseline_value)
            unit = unit_of_metric(metric_meta_or_422(db, ctx, metric))
            numeric = _numeric_baseline_value(baseline, unit)
            if numeric is None or not math.isfinite(numeric):
                raise InvalidScopeParam('基线数值或单位与指标不一致', details={'param': 'targets'})
            if baseline.get('metric', metric) != metric:
                raise InvalidScopeParam('基线与目标指标不一致', details={'param': 'targets'})
            baseline.update(metric=metric, source='teacher')
            row.baseline_value = baseline
        elif old is None or ('baseline_value' in item.model_fields_set and item.baseline_value is None) or (
            anchor_changed and (row.baseline_value or {}).get('source') != 'teacher'
            and not _is_strictly_before(row.baseline_value or {}, note.start_date or note.date)
        ):
            row.baseline_value = capture_baseline(db, ctx, note.person_id, metric, anchor=note.start_date or note.date)
        if row.status != item.status or (item.status != 'open' and row.closed_at is None):
            row.closed_at = None if item.status == 'open' else datetime.utcnow()
        row.status = item.status
    for metric, row in existing.items():
        if metric not in seen and row.status == 'open':
            row.status, row.closed_at = 'dismissed', datetime.utcnow()
    aggregate(note)


def sync_legacy_edit(note, req, db=None, ctx=None):
    """Keep pre-existing whole-note close/edit clients working."""
    fields = req.model_fields_set
    ensure_legacy_target(note)
    if not note.targets:
        return
    if 'target_metric' in fields or 'baseline_value' in fields or 'start_date' in fields or 'date' in fields:
        first = note.targets[0]
        if note.target_metric is None:
            for row in note.targets:
                if row.status == 'open':
                    row.status, row.closed_at = 'dismissed', datetime.utcnow()
            # Explicit legacy clearing still preserves the metric history.
        else:
            first.metric = note.target_metric
            if (note.baseline_value or {}).get('source') != 'attention-v1':
                first.baseline_value = note.baseline_value
    if db is not None and ({'start_date', 'date'} & fields):
        from app.diagnosis.review import capture_baseline, _is_strictly_before
        for row in note.targets:
            baseline = row.baseline_value or {}
            if baseline.get('source') != 'teacher' and not _is_strictly_before(baseline, note.start_date or note.date):
                row.baseline_value = capture_baseline(db, ctx, note.person_id, row.metric, anchor=note.start_date or note.date)
    if 'status' in fields or 'follow_up_done' in fields:
        state = note.status
        for row in note.targets:
            if state == 'open' or row.status == 'open':
                row.status = state
                row.closed_at = None if state == 'open' else datetime.utcnow()
    aggregate(note)
