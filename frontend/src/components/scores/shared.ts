/**
 * 两成绩页共用的小工具（P3-FE）。
 *
 * 约束（契约 p3 §2.3 计量红线）：
 * - 缺考（score NULL）一律显示「—」，绝不在展示层转 0；
 * - 作用域参数从 useWorkspace 的 filter 映射，后端解析，空成员范围不退化。
 */

import type { AnalysisScopeQuery, ExamSummary } from '@/lib/api-v1'
import type { WorkspaceFilter } from '@/lib/workspace'

/** 把工作台筛选映射为分析端点的作用域查询参数（'all'/缺省不传，由后端按并集解析）。 */
export function analysisScopeQuery(filter: WorkspaceFilter): AnalysisScopeQuery {
  const q: AnalysisScopeQuery = {}
  if (typeof filter.academic_year_id === 'number') q.academic_year_id = filter.academic_year_id
  if (typeof filter.term_id === 'number') q.term_id = filter.term_id
  if (typeof filter.class_id === 'number') q.class_id = filter.class_id
  if (typeof filter.teaching_class_id === 'number') q.teaching_class_id = filter.teaching_class_id
  return q
}

/** 分数展示：缺考/不可算显示「—」（不转 0）；整数不带小数、小数保留 1 位。 */
export function formatScore(v: number | null | undefined): string {
  if (v == null) return '—'
  return Number.isInteger(v) ? String(v) : v.toFixed(1)
}

/** 考试日期（ISO）取日期部分；无日期显示「日期未知」。 */
export function examDateLabel(e: ExamSummary): string {
  return e.exam_date ? e.exam_date.slice(0, 10) : '日期未知'
}

/** 下拉选项文案：「考试名（日期）」。 */
export function examOptionLabel(e: ExamSummary): string {
  return `${e.exam_name}（${examDateLabel(e)}）`
}

/**
 * 考试按日期降序（ISO 字符串比较即日期比较）；无日期视为最早排最后，
 * 同日期按考试名排序保证稳定。后端契约即降序，这里做一道兜底。
 */
export function sortExamsDesc(exams: ExamSummary[]): ExamSummary[] {
  return exams.slice().sort((a, b) => {
    const ad = a.exam_date ?? ''
    const bd = b.exam_date ?? ''
    if (ad === bd) return a.exam_name.localeCompare(b.exam_name, 'zh-CN')
    if (ad === '') return 1
    if (bd === '') return -1
    return bd.localeCompare(ad)
  })
}

/** score_basis 口径的中文标签（契约 §2：raw=原始分，grade=等级分/赋分）。 */
export function scoreBasisLabel(basis: string | null | undefined): string {
  if (basis === 'raw') return '原始分'
  if (basis === 'grade') return '等级分'
  return basis ?? '—'
}
