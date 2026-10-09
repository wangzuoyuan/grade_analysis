/**
 * 作业跟进页共用小工具（P5-FE）。
 *
 * 约束（契约 p5-homework.md §0）：
 * - 提交率分母 = 应交快照（确认时冻结）− excused；快照为空 → 一律显示
 *   「无法计算（无可靠分母）」，绝不推断其余全交、绝不显示百分比或 0%；
 * - 默认已交，只登记缺交和请假例外；旧 unknown 兼容显示为已交。
 */

import type { HomeworkScopeQuery, HomeworkStatus, WorkspaceMode } from '@/lib/api-v1'
import type { WorkspaceFilter } from '@/lib/workspace'

/**
 * 班主任工作台不采集作业种类（UI 不出现该概念），但后端契约仍要求非空：
 * 录入时统一代填此值。历史迁移批次（homework_type='legacy'）在班主任侧同样不展示。
 */
export const HOMEROOM_HOMEWORK_TYPE = '日常作业'

/** 把工作台筛选映射为作业端点的作用域查询参数（'all'/缺省不传，由后端按绑定解析）。 */
export function homeworkScopeQuery(filter: WorkspaceFilter): HomeworkScopeQuery {
  const q: HomeworkScopeQuery = {}
  if (typeof filter.academic_year_id === 'number') q.academic_year_id = filter.academic_year_id
  if (typeof filter.class_id === 'number') q.class_id = filter.class_id
  if (typeof filter.teaching_class_id === 'number') q.teaching_class_id = filter.teaching_class_id
  return q
}

/** 提交率展示：不可计算时给确定文案，绝不显示百分比（H03：不推断其余全交）。 */
export function formatSubmissionRate(rate: number | null, unavailable: boolean): string {
  if (unavailable || rate == null) return '无法计算（无可靠分母）'
  return `${(rate * 100).toFixed(1)}%`
}

/** 交作业状态选项（录入行编辑器/批次编辑共用同一份白名单与文案）。 */
export const HOMEWORK_STATUS_OPTIONS: Array<{ value: HomeworkStatus; label: string }> = [
  { value: 'submitted', label: '已交' },
  { value: 'missing', label: '缺交' },
  { value: 'excused', label: '请假免交' },
]

const STATUS_LABELS: Record<string, string> = {
  submitted: '已交',
  missing: '缺交',
  excused: '请假免交',
}

/** 状态中文标签；旧 unknown 统一按新的默认已交口径展示。 */
export function homeworkStatusLabel(status: string): string {
  return STATUS_LABELS[status] ?? '已交'
}

/** 批次状态标签（active/revoked）。 */
export function assignmentStatusLabel(status: string): string {
  if (status === 'active') return '有效'
  if (status === 'revoked') return '已撤销'
  return status
}

/**
 * 相关性方向文案（P2-C1 起 y=成绩分数：r/rho>0 → submit_up_score_up，
 * <0 → submit_up_score_down；详见 /diagnosis/correlation 响应的 note 字段）。
 */
export function correlationDirectionLabel(direction: string | null): string {
  if (direction === 'submit_up_score_up') return '提交率越高，成绩越高（r 为正）'
  if (direction === 'submit_up_score_down') return '提交率越高，成绩越低（r 为负）'
  return '—'
}

/** 两工作台的作业域说明（容器头与空态复用）。 */
export function homeworkDomainNote(mode: WorkspaceMode, scopeSubject: string | null): string {
  if (mode === 'homeroom') {
    return '行政班全科作业，以及关联教学班的任教学科作业（经共享投影，成员为关联交集）'
  }
  return `任教学科（${scopeSubject ?? '解析中…'}）作业；其他学科不进入教学工作台`
}
