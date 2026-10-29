import type {InterventionTarget} from '@/lib/api-v1'

export interface TargetDraft {
  metric: string
  baseline: string
  original?: InterventionTarget
}
export function metricUnit(metric: string | null | undefined): 'rank' | 'percentile' | 'grade_score' {
  if (!metric || metric.startsWith('total:')) return 'rank'
  return metric.startsWith('subject_grade:') ? 'grade_score' : 'percentile'
}
/** An omitted baseline preserves an existing capture; the server captures new metrics. */
export function buildTargetPayload(targets: TargetDraft[]): InterventionTarget[] {
  return targets.map(t => {
    const unit = metricUnit(t.metric)
    const raw = t.baseline.trim()
    if (!raw) return {metric: t.metric, status: t.original?.status ?? 'open'}
    const value = Number(raw)
    if (!Number.isFinite(value) || (unit === 'percentile' && (value < 0 || value > 100)) ||
        (unit === 'rank' && (!Number.isInteger(value) || value < 1))) {
      throw new Error('请填写有效基线；百分位填0–100，名次填正整数。')
    }
    return {metric: t.metric, status: t.original?.status ?? 'open',
      baseline_value: {value: unit === 'percentile' ? value / 100 : value, unit, source: 'teacher'}}
  })
}
