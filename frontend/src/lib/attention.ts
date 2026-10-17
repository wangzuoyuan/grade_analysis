export interface AttentionSettings {
  percentile_drop: number
  grade_score_drop: number
  target_top_percent: number | null
  target_grade2: number | null
  target_grade3: number | null
}
export interface AttentionSignal {
  code: string; category: string; title: string; detail: string
  evidence: Array<Record<string, string | number | null>>
  observed_on: string | null; severity: number; key: string
}
export interface AttentionSnapshot {
  achievement?: string
  reasons?: string[]
  timeline?: Array<{ exam: string; date: string | null; value: string }>
}
export interface AttentionFollowUp {
  id: number; problem: string; measures: string | null; review_date: string | null; due: boolean
  baseline: AttentionSnapshot; current: AttentionSnapshot; note: string
}
export interface AttentionStudent {
  person_id: number; name: string | null; alias: string | null; class_labels: string[]
  signals: AttentionSignal[]; categories: string[]; active: boolean; priority: number
  followups: AttentionFollowUp[]; snapshot: AttentionSnapshot; evidence_key: string
  suggestion: string
}
export interface AttentionResponse {
  as_of: string; subject: string; window_start: string; window_end: string
  settings: AttentionSettings; students: AttentionStudent[]
  gaps: Array<{ person_id: number; name: string | null; class_labels: string[]; reason: string }>
  counts: Record<'all' | 'learning' | 'homework' | 'review' | 'support', number>
  note: string
}
export const ATTENTION_TABS = [
  ['all', '全部'], ['learning', '学习变化'], ['homework', '作业异常'],
  ['review', '待复查'], ['support', '持续支持'],
] as const
export type AttentionTab = typeof ATTENTION_TABS[number][0]
export function attentionRows(students: AttentionStudent[], tab: AttentionTab) {
  return students.filter(s => tab === 'all' ? s.active :
    tab === 'review' ? s.followups.length > 0 : s.categories.includes(tab))
}
