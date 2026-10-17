/** One observation is one student/exam; absence and unknown denominators stay null. */
export interface PersonalCorrelation {
  n: number
  r: number | null
  rho: number | null
  reason: string | null
  rho_without_one: [number, number] | null
  sensitive: boolean
}
export interface PersonalHomeworkWindow {
  submitted: number; missing: number; eligible: number; excused: number; ignored: number; days: number
  negative: number; evaluated: number; unknown_batches: number
  submission_rate: number | null; negative_rate: number | null; evaluation_coverage: number | null
}
export interface PersonalHomeworkPoint {
  exam_key: string; exam_name: string; exam_date: string | null; grade: number | null
  basis: 'grade_percentile' | 'grade_score' | null
  raw_score: number | null; percentile: number | null; achievement: number | null
  window_start: string | null; window_end: string | null; homework: PersonalHomeworkWindow | null
  pair_reason: string | null; gain: number | null; submission_change: number | null
}
export interface PersonalHomeworkStudent {
  person_id: number; name: string | null; alias: string | null; class_labels: string[]
  timeline: PersonalHomeworkPoint[]
  correlations: Record<'submission' | 'progress' | 'negative', PersonalCorrelation>
  latest_change: PersonalHomeworkPoint | null; paired_n: number
  excluded_reasons: Record<string, number>; advice: string; caveats: string[]
}
export interface StudentHomeworkResponse {
  calc_version: string; mode: string; academic_year_id: number | null; subject: string
  window_days: number; end_exam_name: string | null; minimum_exams: number; minimum_homework: number
  students: PersonalHomeworkStudent[]; note: string
}
export function personalReason(reason: string | null): string {
  const labels: Record<string, string> = {
    insufficient_exams: '配对考试不足6场，先看逐场变化', homework_unchanged: '作业指标没有变化，无法判断相关',
    achievement_unchanged: '成绩指标没有变化，无法判断相关', mixed_grades: '跨年级不可混算，请选择一个年级',
    grade_missing: '班级或考试尚未注明年级', grade_conflict: '考试与班级年级不一致，需核对',
    score_missing: '缺考或未导入有效成绩', percentile_missing: '缺少年级百分位', grade_score_missing: '缺少等级分',
    exact_date_missing: '考试日期不完整或存在冲突', same_day_exams: '同日多场，暂不重复计入观察',
    insufficient_homework: '考前有效作业不足3份', denominator_unknown_over_half: '应交分母不明批次过半',
  }
  return reason ? labels[reason] ?? reason : '已配对'
}
export function personalAchievement(point: PersonalHomeworkPoint | null): string {
  if (point?.achievement == null) return '—'
  if (point.basis === 'grade_percentile') return point.percentile == null ? '—' : `前 ${(point.percentile * 100).toFixed(1)}%`
  return `${point.achievement.toFixed(1)} 等级分`
}
