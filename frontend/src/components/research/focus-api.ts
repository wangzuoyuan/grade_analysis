export interface FocusCohort {
  cohort: string; kind: 'focus'; type_name: string; exam_name: string | null
  membership_basis: 'exam_anchor' | 'current_time_point'; student_count: number; person_ids: number[]
}
export interface FocusCohorts {
  calc_version: string; as_of: string; academic_year_id: number
  exams: Array<{ exam_name: string; exam_date: string | null; historical_available: boolean }>
  cohorts: FocusCohort[]; metric_options: Array<{value: string; label: string; kind: string}>
  limitations: string[]
}
export interface FocusStudent {
  person_id: number; name: string | null; reason: string; main_type: string | null; is_secondary: boolean
  follow_up_count: number; comparable: boolean; change: number | null; direction: string
  from_value: number | null; to_value: number | null; missing_reason: string | null
  imbalance: Array<{subject: string; from_percentile: number | null; to_percentile: number | null
    from_overall_percentile: number | null; to_overall_percentile: number | null
    from_gap: number | null; to_gap: number | null; verdict: string; missing_reason: string | null}>
  homework: {comparable: boolean; reason: string; from?: HomeworkSide; to?: HomeworkSide} | null
  timeline: Array<{exam_name: string; exam_date: string | null; value: number | null; missing_reason: string | null}>
}
interface HomeworkSide {start: string; end: string; missing: number; streak_days: number; valid_batches: number; legacy_batches: number; subjects: string[]}
export interface FocusOutcome {
  calc_version: string; as_of: string; cohort: string; problem: string; membership_basis: string
  from_exam: string; to_exam: string; from_exam_date: string | null; to_exam_date: string | null
  metric: string; metric_unit: string; threshold: number | null; threshold_provisional: boolean
  selected_n: number; comparable_n: number; excluded_n: number; students: FocusStudent[]; excluded_students: FocusStudent[]
  summary: {improved_n: number; needs_check_n: number}; limitations: string[]
}
interface ContrastValue {value: number; unit: string; exam_name: string | null; exam_date: string | null}
export interface FollowUpRecord {
  id: number; person_id: number; name: string | null; in_current_roster: boolean
  problem: string | null; measures: string | null; subject: string | null
  start_date: string; review_date: string | null; record_status: string; due: boolean
  metric: string | null; metric_label: string; priority: number
  contrast: {status: 'ready' | 'pending'; reason?: string; note: string; baseline?: ContrastValue; latest?: ContrastValue
    unit?: string; change?: {value: number; smaller_is_better: boolean}}
}
export interface FollowUps {calc_version: string; as_of: string; records: FollowUpRecord[]; summary: {total_n: number; due_n: number; ready_n: number; pending_n: number}; limitations: string[]}
export type ScopeQuery = {academic_year_id?: number; class_id?: number}
export const COHORTS_ENDPOINT = '/api/v1/homeroom/diagnosis/research/focus/cohorts'
export const OUTCOME_ENDPOINT = '/api/v1/homeroom/diagnosis/research/focus/outcome'
export const FOLLOW_UPS_ENDPOINT = '/api/v1/homeroom/diagnosis/research/follow-ups'
export async function researchGet<T>(endpoint: string, q: Record<string, string | number | undefined>, signal?: AbortSignal): Promise<T> {
  const params = new URLSearchParams()
  Object.entries(q).forEach(([key, value]) => {if (value !== undefined) params.set(key, String(value))})
  const response = await fetch(`${endpoint}?${params}`, {signal, headers: {Accept: 'application/json'}})
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `请求失败（${response.status}）`)
  return body as T
}
export function formatValue(value: number | null | undefined, unit: string): string {
  if (value == null) return '—'
  return unit === 'percentile' ? `前 ${Number(value.toFixed(2))}%` : `${Number(value.toFixed(2))}${unit === 'rank' ? ' 名' : ' 分'}`
}
export function formatChange(value: number | null | undefined, unit: string): string {
  if (value == null) return '—'
  if (value === 0) return '无变化'
  const magnitude = Number(Math.abs(value).toFixed(2))
  if (unit === 'rank') return `${value < 0 ? '前进' : '后退'} ${magnitude} 名`
  if (unit === 'percentile') return `相对位置${value < 0 ? '上升' : '下降'} ${magnitude} 个百分点`
  return `${value < 0 ? '下降' : '上升'} ${magnitude} 分`
}
export function profileHref(personId: number): string {return `/homeroom/profile?person_id=${personId}`}
