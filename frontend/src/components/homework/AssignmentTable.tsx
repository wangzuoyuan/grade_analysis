'use client'

/**
 * 批次列表 + 看板（契约 p5-homework.md §1.3/§2）。
 *
 * 约束：
 * - 并发保护仅在后台携带版本值，页面只提示“内容已更新”；
 * - 撤销前 window.confirm，409 冲突清单（该批次有后续评价编辑）
 *   原样展示，绝不静默撤销；
 * - 请求序号按资源分离（F11）：列表 / 看板 / 行详情互不作废——共用一个序号时，
 *   任一资源重拉会把其他资源的合法回包当过期丢弃。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle, ChevronDown, ClipboardList, Pencil, RefreshCw, Trash2, Undo2 } from 'lucide-react'

export type HomeworkDetailFilter =
  | 'all'
  | 'submitted'
  | 'missing'
  | 'forgot'
  | 'excused'
  | 'attendance'
  | 'negative'


import {
  ApiV1Error,
  homeworkAssignmentDetail,
  homeworkAssignmentsList,
  homeworkDashboard,
  homeworkDeleteAssignment,
  homeworkPatchAssignment,
  readHomeworkRevokeConflicts,
  type HomeworkAssignmentDetail,
  type HomeworkAssignmentListItem,
  type HomeworkDashboardResponse,
  type HomeworkRevokeConflict,
  type HomeworkScopeQuery,
  type HomeworkStatus,
  type PersonId,
  type WorkspaceMode,
} from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { pureEvaluation } from './homework-vocab'
import {
  HOMEROOM_HOMEWORK_TYPE,
  HOMEWORK_STATUS_OPTIONS,
  assignmentStatusLabel,
  homeworkStatusLabel,
} from './shared'

interface AppliedFilters {
  subject: string
  homeworkType: string
  fromDate: string
  toDate: string
}

const EMPTY_FILTERS: AppliedFilters = { subject: '', homeworkType: '', fromDate: '', toDate: '' }

/** 行内编辑的未保存修改（key = 读域 person_id）。 */
interface RowEdit {
  status: HomeworkStatus
  evaluation: string
  attendance: string
}

/** 批次作业内容的展示口径：班主任占位种类与迁移批次不显示，记为「—」。 */
function displayHomeworkContent(homeworkType: string | null | undefined): string {
  const t = (homeworkType ?? '').trim()
  if (!t || t === HOMEROOM_HOMEWORK_TYPE || t === 'legacy') return '—'
  return t
}

/** 学科展示标签：同科多批次时带上具体作业名区分（如「英语 · 练习册」）。 */
function subjectWithContent(subject: string, homeworkType: string | null | undefined): string {
  const content = displayHomeworkContent(homeworkType)
  if (subject === '考勤' || content === '—') return subject
  return `${subject} · ${content}`
}

/**
 * 评价草稿文本：清洗出勤/忘带词后展示。
 * 兼容旧数据：方案A时期曾把批次作业名塞进 evaluation（两者相同）→ 视为无评价。
 */
function evaluationDraftText(
  evaluation: string | null | undefined,
  attendance: string | null | undefined,
  homeworkType: string | null | undefined,
): string {
  const q = pureEvaluation(evaluation, attendance)
  if (q && q === (homeworkType ?? '').trim()) return ''
  return q
}

export interface HomeworkDayGroup {
  date: string
  dayOfWeek: string
  assignments: HomeworkAssignmentListItem[]
  totalExpected: number
  totalSubmitted: number
  totalMissing: number
  totalExcused: number
  totalAttendance: number
  totalNegative: number
  hasRevoked: boolean
  allRevoked: boolean
}

/** 数字单元格与筛选胶囊的统一配色口径（缺交=玫红、请假=蓝、已交=绿、出勤=琥珀、负面=紫）。 */
const FILTER_TONES: Record<Exclude<HomeworkDetailFilter, 'all'>, { chipActive: string; chipHot: string; chipIdle: string; numberActive: string; numberHot: string | null; numberIdle: string }> = {
  submitted: {
    chipActive: 'bg-emerald-600 text-white shadow-sm',
    chipHot: 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-100',
    chipIdle: 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-100',
    numberActive: 'bg-emerald-100 text-emerald-800 font-semibold ring-1 ring-emerald-300',
    numberHot: null,
    numberIdle: 'text-slate-700 hover:bg-slate-100 hover:text-slate-900',
  },
  missing: {
    chipActive: 'bg-rose-600 text-white shadow-sm',
    chipHot: 'bg-rose-50 text-rose-700 border border-rose-200 hover:bg-rose-100',
    chipIdle: 'bg-white text-slate-500 border border-slate-200 hover:bg-slate-100',
    numberActive: 'bg-rose-100 text-rose-800 font-semibold ring-1 ring-rose-300',
    numberHot: 'text-rose-600 font-medium hover:bg-rose-50 hover:text-rose-700',
    numberIdle: 'text-slate-400 hover:bg-slate-100 hover:text-slate-600',
  },
  // 忘带：琥珀系与忘带徽章一致；忘带是缺交的子集筛选，不改缺交数字
  forgot: {
    chipActive: 'bg-amber-600 text-white shadow-sm',
    chipHot: 'bg-amber-50 text-amber-700 border border-amber-200 hover:bg-amber-100',
    chipIdle: 'bg-white text-slate-500 border border-slate-200 hover:bg-slate-100',
    numberActive: 'bg-amber-100 text-amber-800 font-semibold ring-1 ring-amber-300',
    numberHot: 'text-amber-600 font-medium hover:bg-amber-50 hover:text-amber-700',
    numberIdle: 'text-slate-400 hover:bg-slate-100 hover:text-slate-600',
  },
  excused: {
    chipActive: 'bg-blue-600 text-white shadow-sm',
    chipHot: 'bg-blue-50 text-blue-700 border border-blue-200 hover:bg-blue-100',
    chipIdle: 'bg-white text-slate-500 border border-slate-200 hover:bg-slate-100',
    numberActive: 'bg-blue-100 text-blue-800 font-semibold ring-1 ring-blue-300',
    numberHot: 'text-blue-600 font-medium hover:bg-blue-50 hover:text-blue-700',
    numberIdle: 'text-slate-400 hover:bg-slate-100 hover:text-slate-600',
  },
  attendance: {
    chipActive: 'bg-amber-600 text-white shadow-sm',
    chipHot: 'bg-amber-50 text-amber-700 border border-amber-200 hover:bg-amber-100',
    chipIdle: 'bg-white text-slate-500 border border-slate-200 hover:bg-slate-100',
    numberActive: 'bg-amber-100 text-amber-800 font-semibold ring-1 ring-amber-300',
    numberHot: 'text-amber-600 font-medium hover:bg-amber-50 hover:text-amber-700',
    numberIdle: 'text-slate-400 hover:bg-slate-100 hover:text-slate-600',
  },
  negative: {
    chipActive: 'bg-purple-600 text-white shadow-sm',
    chipHot: 'bg-purple-50 text-purple-700 border border-purple-200 hover:bg-purple-100',
    chipIdle: 'bg-white text-slate-500 border border-slate-200 hover:bg-slate-100',
    numberActive: 'bg-purple-100 text-purple-800 font-semibold ring-1 ring-purple-300',
    numberHot: 'text-purple-600 font-medium hover:bg-purple-50 hover:text-purple-700',
    numberIdle: 'text-slate-400 hover:bg-slate-100 hover:text-slate-600',
  },
}

/** 表格里的「数字筛选单元格」：点击数字按分类筛选人员（主行 5 列共用一个实现）。 */
function NumberFilterCell({
  count,
  filter,
  title,
  active,
  onClick,
}: {
  count: number
  filter: Exclude<HomeworkDetailFilter, 'all'>
  title: string
  active: boolean
  onClick: (filter: HomeworkDetailFilter) => void
}) {
  const tone = FILTER_TONES[filter]
  return (
    <TableCell className="text-right text-sm tabular-nums p-1 sm:p-2">
      <button
        type="button"
        title={title}
        onClick={(e) => {
          e.stopPropagation()
          onClick(filter)
        }}
        className={cn(
          'inline-flex items-center justify-end px-2 py-0.5 rounded text-right transition-colors cursor-pointer text-xs sm:text-sm',
          active ? tone.numberActive : tone.numberHot && count > 0 ? tone.numberHot : tone.numberIdle,
        )}
      >
        {String(count)}
      </button>
    </TableCell>
  )
}

const FILTER_CHIP_DEFS: Array<{ filter: Exclude<HomeworkDetailFilter, 'all'>; label: string }> = [
  { filter: 'missing', label: '缺交' },
  { filter: 'forgot', label: '忘带' },
  { filter: 'excused', label: '请假' },
  { filter: 'submitted', label: '已交' },
  { filter: 'attendance', label: '出勤异常' },
  { filter: 'negative', label: '负面评价' },
]

/** 「人员筛选」胶囊控制栏：明细展开区与全科透视共用一个实现。 */
function FilterChipBar({
  activeFilter,
  onFilterChange,
  counts,
  allLabel = '全部',
}: {
  activeFilter: HomeworkDetailFilter
  onFilterChange: (filter: HomeworkDetailFilter) => void
  counts: Record<HomeworkDetailFilter, number>
  allLabel?: string
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs">
      <button
        type="button"
        onClick={() => onFilterChange('all')}
        className={cn(
          'rounded-md px-2.5 py-1 font-medium transition-colors cursor-pointer',
          activeFilter === 'all'
            ? 'bg-slate-800 text-white shadow-sm'
            : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-100',
        )}
      >
        {allLabel} ({counts.all})
      </button>
      {FILTER_CHIP_DEFS.map((c) => {
        const tone = FILTER_TONES[c.filter]
        const count = counts[c.filter]
        // 0 人类别不渲染胶囊，避免一排灰点；但当前选中的即使 0 也显示，避免出现不可见的选中态
        if (count === 0 && activeFilter !== c.filter) return null
        return (
          <button
            key={c.filter}
            type="button"
            onClick={() => onFilterChange(c.filter)}
            className={cn(
              'rounded-md px-2.5 py-1 font-medium transition-colors cursor-pointer',
              activeFilter === c.filter ? tone.chipActive : count > 0 ? tone.chipHot : tone.chipIdle,
            )}
          >
            {c.label} ({count})
          </button>
        )
      })}
    </div>
  )
}

/** 吸顶筛选行右侧的「当前只看」提示（非 all 筛选时出现；明细视图与全科透视共用）。 */
function FilterFocusHint({ filter, count }: { filter: HomeworkDetailFilter; count: number }) {
  if (filter === 'all') return null
  const label = FILTER_CHIP_DEFS.find((d) => d.filter === filter)?.label ?? '全部'
  return (
    <span className="inline-flex items-center rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-800">
      🔍 当前只看：{label} {count} 人，点「全部」查看全班
    </span>
  )
}

function formatDayOfWeek(dateStr: string): string {
  try {
    const d = new Date(dateStr + 'T00:00:00')
    const days = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
    return days[d.getDay()] || ''
  } catch {
    return ''
  }
}

/** 编辑批次基本信息对话框（布置日期、作业种类/名称、学科、截止日期等）。 */
function EditAssignmentModal({
  item,
  open,
  onOpenChange,
  mode,
  scopeQ,
  onSuccess,
}: {
  item: HomeworkAssignmentListItem | null
  open: boolean
  onOpenChange: (open: boolean) => void
  mode: WorkspaceMode
  scopeQ: HomeworkScopeQuery
  onSuccess: () => void
}) {
  const [assignedDate, setAssignedDate] = useState('')
  const [homeworkType, setHomeworkType] = useState('')
  const [subject, setSubject] = useState('')
  const [dueDate, setDueDate] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (item && open) {
      setAssignedDate(item.assigned_date || '')
      setHomeworkType(item.homework_type || '')
      setSubject(item.subject || '')
      setDueDate(item.due_date || '')
      setError(null)
    }
  }, [item, open])

  if (!item) return null

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!assignedDate) {
      setError('布置日期不能为空')
      return
    }
    if (!homeworkType.trim()) {
      setError('作业种类/名称不能为空')
      return
    }
    if (mode === 'homeroom' && !subject.trim()) {
      setError('学科不能为空')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await homeworkPatchAssignment(
        item.assignment_id,
        mode,
        {
          revision: item.revision,
          assigned_date: assignedDate,
          homework_type: homeworkType.trim(),
          subject: mode === 'homeroom' ? subject.trim() : undefined,
          due_date: dueDate.trim() ? dueDate.trim() : null,
        },
        scopeQ
      )
      onOpenChange(false)
      onSuccess()
    } catch (err) {
      if (err instanceof ApiV1Error && err.status === 409) {
        setError('该作业记录刚被其他操作更新，请刷新列表后重试')
      } else {
        setError(apiErrorMessage(err))
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={handleSave}>
          <DialogHeader>
            <DialogTitle>编辑作业批次</DialogTitle>
            <DialogDescription>
              修改当前批次的布置日期或作业种类/名称（如修正误录的名称）。
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-4 py-4">
            {error && (
              <div className="rounded-md bg-rose-50 p-2.5 text-xs text-rose-700 border border-rose-200">
                {error}
              </div>
            )}

            <div className="space-y-1.5">
              <label htmlFor="edit-assigned-date" className="text-xs font-medium text-slate-600">
                布置日期 <span className="text-rose-500">*</span>
              </label>
              <Input
                id="edit-assigned-date"
                type="date"
                value={assignedDate}
                onChange={(e) => setAssignedDate(e.target.value)}
                required
              />
            </div>

            <div className="space-y-1.5">
              <label htmlFor="edit-subject" className="text-xs font-medium text-slate-600">
                学科
              </label>
              {mode === 'homeroom' ? (
                <Input
                  id="edit-subject"
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                  placeholder="如：语文、数学、物理"
                  required
                />
              ) : (
                <Input
                  id="edit-subject"
                  value={subject}
                  disabled
                  className="bg-slate-50 text-slate-500 cursor-not-allowed"
                />
              )}
            </div>

            <div className="space-y-1.5">
              <label htmlFor="edit-homework-type" className="text-xs font-medium text-slate-600">
                作业种类 / 名称 <span className="text-rose-500">*</span>
              </label>
              <Input
                id="edit-homework-type"
                value={homeworkType}
                onChange={(e) => setHomeworkType(e.target.value)}
                placeholder="如：日常作业、习题集、实验报告"
                required
              />
            </div>

            <div className="space-y-1.5">
              <label htmlFor="edit-due-date" className="text-xs font-medium text-slate-600">
                截止日期（可选）
              </label>
              <Input
                id="edit-due-date"
                type="date"
                value={dueDate}
                onChange={(e) => setDueDate(e.target.value)}
              />
            </div>
          </div>

          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={saving}
            >
              取消
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? '保存中...' : '保存修改'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export function AssignmentTable({
  mode,
  scopeQ,
  generation,
  refreshKey,
  summaryOnly = false,
  hideSummary = false,
}: {
  mode: WorkspaceMode
  scopeQ: HomeworkScopeQuery
  /** 工作台世代号（切换/筛选变化即作废在途回包）。 */
  generation: number
  /** 父容器触发的刷新信号（如录入确认成功）。 */
  refreshKey: number
  /** 作业管理只显示按期统计；完整筛选和明细留在“作业记录”。 */
  summaryOnly?: boolean
  /** 作业记录不重复显示已经并入作业管理的按期统计。 */
  hideSummary?: boolean
}) {
  // 过滤输入（本地草稿）与已应用值分离：点「查询」才发请求，避免逐键打接口
  const [draft, setDraft] = useState<AppliedFilters>(EMPTY_FILTERS)
  const [applied, setApplied] = useState<AppliedFilters>(EMPTY_FILTERS)

  // 批次列表
  const [items, setItems] = useState<HomeworkAssignmentListItem[] | null>(null)
  const [total, setTotal] = useState(0)
  const [listError, setListError] = useState<string | null>(null)
  const [listNonce, setListNonce] = useState(0)
  const listReqRef = useRef(0)

  // 班主任工作台默认按日合并展示多学科；教学工作台单科平铺
  const [viewMode, setViewMode] = useState<'by_date' | 'flat'>(mode === 'homeroom' ? 'by_date' : 'flat')
  const [expandedDate, setExpandedDate] = useState<string | null>(null)
  const [activeDaySubject, setActiveDaySubject] = useState<string | number>('overview')

  // 班主任工作台不展示/不筛选作业种类（含迁移批次 'legacy'，见 HOMEROOM_HOMEWORK_TYPE 注记）
  const showType = mode !== 'homeroom'

  // 按期看板（day/week/month）
  const [groupBy, setGroupBy] = useState<'day' | 'week' | 'month'>('month')
  const [dash, setDash] = useState<HomeworkDashboardResponse | null>(null)
  const [dashError, setDashError] = useState<string | null>(null)
  const dashReqRef = useRef(0)

  // 行展开详情（一次只展开一个批次）
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [activeFilter, setActiveFilter] = useState<HomeworkDetailFilter>('all')
  const [detail, setDetail] = useState<HomeworkAssignmentDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const detailReqRef = useRef(0)


  // 行内编辑草稿 + 保存状态
  const [edits, setEdits] = useState<Record<string, RowEdit>>({})
  const [patching, setPatching] = useState(false)
  const [patchError, setPatchError] = useState<string | null>(null)

  // 撤销状态（409 冲突清单单独呈现）
  const [revoking, setRevoking] = useState(false)
  const [revokeError, setRevokeError] = useState<string | null>(null)
  const [revokeConflicts, setRevokeConflicts] = useState<HomeworkRevokeConflict[] | null>(null)

  // 编辑批次弹窗状态（修改布置日期、作业种类/名称、学科等元数据）
  const [editingAssignment, setEditingAssignment] = useState<HomeworkAssignmentListItem | null>(null)
  const [editModalOpen, setEditModalOpen] = useState(false)

  const handleOpenEditModal = useCallback((item: HomeworkAssignmentListItem) => {
    setEditingAssignment(item)
    setEditModalOpen(true)
  }, [])

  const reload = useCallback(() => {
    setListNonce((n) => n + 1)
  }, [])

  // 班主任工作台按日聚合
  const dayGroups = useMemo<HomeworkDayGroup[] | null>(() => {
    if (!items) return null
    const map = new Map<string, HomeworkAssignmentListItem[]>()
    for (const item of items) {
      const list = map.get(item.assigned_date)
      if (list) {
        list.push(item)
      } else {
        map.set(item.assigned_date, [item])
      }
    }
    const groups: HomeworkDayGroup[] = []
    for (const [date, list] of map.entries()) {
      let totalExpected = 0
      let totalSubmitted = 0
      let rawMissingSum = 0
      let rawExcusedSum = 0
      let rawAttendanceSum = 0
      let rawNegativeSum = 0
      let revokedCount = 0

      const excusedSet = new Set<number>()
      const missingSet = new Set<number>()
      const attendanceSet = new Set<number>()
      const negativeSet = new Set<number>()
      let hasIdInfo = false

      for (const a of list) {
        // 考勤批次单独计入出勤异常，不计入学科作业已交或缺交
        if (a.subject !== '考勤') {
          totalExpected += a.expected_count
          totalSubmitted += a.submitted
          rawMissingSum += a.missing
        }
        rawExcusedSum += a.excused
        rawAttendanceSum += a.attendance_count || (a.subject === '考勤' ? (a.missing || 0) : 0)
        rawNegativeSum += a.negative_count || 0
        if (a.status === 'revoked') revokedCount++

        if (a.excused_ids || a.missing_ids || a.attendance_ids || a.negative_ids) {
          hasIdInfo = true
          for (const id of a.excused_ids || []) excusedSet.add(id)
          for (const id of a.missing_ids || []) missingSet.add(id)
          for (const id of a.attendance_ids || []) attendanceSet.add(id)
          for (const id of a.negative_ids || []) negativeSet.add(id)
        }
      }

      // 班主任按日合并：
      // 缺交：多项作业缺交真实累加（例如练习册缺交1次、听力缺交1次，实打实累加）
      const totalMissing = rawMissingSum
      const totalExcused = hasIdInfo ? excusedSet.size : rawExcusedSum
      const totalAttendance = hasIdInfo ? attendanceSet.size : rawAttendanceSum
      const totalNegative = hasIdInfo ? negativeSet.size : rawNegativeSum

      groups.push({
        date,
        dayOfWeek: formatDayOfWeek(date),
        assignments: list,
        totalExpected,
        totalSubmitted,
        totalMissing,
        totalExcused,
        totalAttendance,
        totalNegative,
        hasRevoked: revokedCount > 0,
        allRevoked: revokedCount === list.length,
      })
    }
    return groups
  }, [items])

  /** 统一收起并清空所有展开/筛选残留状态。 */
  const resetExpansion = useCallback(() => {
    setExpandedDate(null)
    setExpandedId(null)
    setActiveDaySubject('overview')
    setActiveFilter('all')
  }, [])

  function toggleDayGroup(group: HomeworkDayGroup, preferredFilter: HomeworkDetailFilter = 'all') {
    const isCurrentlyExpanded = expandedDate === group.date
    // 1. 点击的是当前已激活的筛选数字（单科日或全科透视皆可）→ 再次点击收起
    if (isCurrentlyExpanded && preferredFilter !== 'all' && activeFilter === preferredFilter) {
      resetExpansion()
      return
    }

    // 2. 点击最右侧箭头/整行展开（preferredFilter === 'all'）且当前已是全景透视展开态 → 收起
    if (
      isCurrentlyExpanded &&
      preferredFilter === 'all' &&
      activeFilter === 'all' &&
      activeDaySubject === 'overview'
    ) {
      resetExpansion()
      return
    }

    // 3. 展开或切换视图
    setExpandedDate(group.date)
    setActiveFilter(preferredFilter)
    if (group.assignments.length === 1) {
      setActiveDaySubject(group.assignments[0].assignment_id)
      setExpandedId(group.assignments[0].assignment_id)
    } else {
      setActiveDaySubject('overview')
      setExpandedId(null)
    }
  }

  function handleDayTabChange(tab: string | number, filter: HomeworkDetailFilter = 'all') {
    setActiveDaySubject(tab)
    setActiveFilter(filter)
    if (tab === 'overview') {
      setExpandedId(null)
    } else {
      setExpandedId(Number(tab))
    }
  }

  // 批次列表（请求序号只比对 listReqRef）
  useEffect(() => {
    if (summaryOnly) {
      setItems([])
      setTotal(0)
      return
    }
    const req = ++listReqRef.current
    setItems(null)
    setListError(null)
    homeworkAssignmentsList(mode, {
      ...scopeQ,
      subject: applied.subject !== '' ? applied.subject : undefined,
      homework_type: showType && applied.homeworkType !== '' ? applied.homeworkType : undefined,
      from_date: applied.fromDate !== '' ? applied.fromDate : undefined,
      to_date: applied.toDate !== '' ? applied.toDate : undefined,
      limit: 200,
    })
      .then((r) => {
        if (req !== listReqRef.current) return
        setItems(r.items ?? [])
        setTotal(r.total ?? 0)
      })
      .catch((err: unknown) => {
        if (req !== listReqRef.current) return
        setItems([])
        setTotal(0)
        setListError(apiErrorMessage(err))
      })
  }, [mode, scopeQ, generation, listNonce, applied, summaryOnly])

  // 按期看板（请求序号只比对 dashReqRef，绝不作废列表回包）
  useEffect(() => {
    if (hideSummary) {
      ++dashReqRef.current
      setDash(null)
      setDashError(null)
      return
    }
    const req = ++dashReqRef.current
    setDash(null)
    setDashError(null)
    homeworkDashboard(
      mode,
      {
        ...scopeQ,
        subject: applied.subject !== '' ? applied.subject : undefined,
        homework_type: showType && applied.homeworkType !== '' ? applied.homeworkType : undefined,
      },
      groupBy,
    )
      .then((r) => {
        if (req === dashReqRef.current) setDash(r)
      })
      .catch((err: unknown) => {
        if (req !== dashReqRef.current) return
        setDash(null)
        setDashError(apiErrorMessage(err))
      })
  }, [mode, scopeQ, generation, listNonce, applied, groupBy, hideSummary])

  // 行展开：拉详情（请求序号只比对 detailReqRef；切换批次绝不闪现上一个批次的数据）
  useEffect(() => {
    if (expandedId == null) {
      setDetail(null)
      setDetailError(null)
      setEdits({})
      setPatchError(null)
      return
    }
    const req = ++detailReqRef.current
    setDetail(null)
    setDetailError(null)
    setEdits({})
    setPatchError(null)
    homeworkAssignmentDetail(expandedId, mode, scopeQ)
      .then((d) => {
        if (req === detailReqRef.current) setDetail(d)
      })
      .catch((err: unknown) => {
        if (req !== detailReqRef.current) return
        setDetailError(apiErrorMessage(err))
      })
  }, [expandedId, mode, scopeQ, refreshKey])

  /** 重新拉当前展开批次的详情（编辑保存/乐观锁 409 后取最新 revision）。 */
  function reloadDetail() {
    if (expandedId == null) return
    // expandedId 未变时 effect 不会自己重跑，这里手动再拉一次并比对序号
    const req = ++detailReqRef.current
    homeworkAssignmentDetail(expandedId, mode, scopeQ)
      .then((d) => {
        if (req === detailReqRef.current) {
          setDetail(d)
          setEdits({})
          setPatchError(null)
        }
      })
      .catch((err: unknown) => {
        if (req !== detailReqRef.current) return
        setDetail(null)
        setDetailError(apiErrorMessage(err))
      })
  }

  async function saveEdits() {
    if (detail == null) return
    // 只提交真正变化的行；详情已把无例外记录归一为“已交”。
    const changed = detail.submissions
      .map((s) => {
        const edit = edits[String(s.person_id)]
        const evaluation = edit?.evaluation ?? evaluationDraftText(s.evaluation, s.attendance, detail.homework_type)
        const attendance = edit?.attendance ?? s.attendance ?? ''
        const selectedStatus = edit?.status ?? (s.status as HomeworkStatus)
        return {
          person_id: s.person_id as PersonId,
          status: selectedStatus,
          evaluation: evaluation === '' ? null : evaluation,
          attendance: attendance === '' ? null : attendance,
        }
      })
      .filter((row, i) => {
        const orig = detail.submissions[i]
        return row.status !== orig.status || row.evaluation !== (evaluationDraftText(orig.evaluation, orig.attendance, detail.homework_type) || null) || row.attendance !== (orig.attendance ?? null)
      })
    if (changed.length === 0) {
      setPatchError('没有需要保存的修改')
      return
    }
    setPatching(true)
    setPatchError(null)
    try {
      await homeworkPatchAssignment(detail.assignment_id, mode, { revision: detail.revision, rows: changed }, scopeQ)
      reload()
      reloadDetail()
    } catch (err) {
      if (err instanceof ApiV1Error && err.status === 409) {
        // 乐观锁冲突：批次已被其他修改更新。重新拉详情拿最新 revision，提示用户核对后重改
        setPatchError('这份记录刚被其他操作更新，已重新加载最新内容，请核对后重试')
        reloadDetail()
      } else {
        setPatchError(apiErrorMessage(err))
      }
    } finally {
      setPatching(false)
    }
  }
  async function revoke(a: HomeworkAssignmentListItem) {
    if (
      !window.confirm(
        `确认撤销批次「${a.subject}${showType ? ` ${a.homework_type}` : ''} ${a.assigned_date}」？撤销后不再计入统计，且不可在此界面恢复。`,
      )
    ) {
      return
    }
    setRevoking(true)
    setRevokeError(null)
    setRevokeConflicts(null)
    try {
      await homeworkDeleteAssignment(a.assignment_id, mode, scopeQ)
      if (expandedId === a.assignment_id) setExpandedId(null)
      reload()
    } catch (err) {
      const conflicts = readHomeworkRevokeConflicts(err)
      if (conflicts != null) {
        // 409 + conflicts：该批次有后续评价编辑，后端拒绝撤销；清单原样展示，不静默撤销
        setRevokeConflicts(conflicts)
        setRevokeError('该批次存在后续评价编辑，撤销被拒绝；请先核对以下冲突清单')
      } else {
        setRevokeError(apiErrorMessage(err))
      }
    } finally {
      setRevoking(false)
    }
  }

  function setEdit(personId: PersonId, patch: Partial<RowEdit>, fallbackStatus: HomeworkStatus) {
    const key = String(personId)
    setEdits((prev) => {
      const prevEdit = prev[key]
      return {
        ...prev,
        [key]: {
          // 状态缺省沿用该行当前状态：仅改评价时绝不能默认改成 submitted
          status: patch.status ?? prevEdit?.status ?? fallbackStatus,
          evaluation: patch.evaluation ?? prevEdit?.evaluation ?? '',
          attendance: patch.attendance ?? prevEdit?.attendance ?? '',
        },
      }
    })
  }

  const appliedNote =
    applied.subject !== '' ||
    (showType && applied.homeworkType !== '') ||
    applied.fromDate !== '' ||
    applied.toDate !== ''

  return (
    <div className="space-y-4">
      {/* 过滤卡（筛选控件不参与打印） */}
      {!summaryOnly ? <Card className="print:hidden">
        <CardContent className="flex flex-col gap-3 py-4 lg:flex-row lg:items-end">
          <div className="grid flex-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-500" htmlFor="hw-f-subject">
                学科
              </label>
              <Input
                id="hw-f-subject"
                value={draft.subject}
                onChange={(e) => setDraft({ ...draft, subject: e.target.value })}
                placeholder="全部学科"
              />
            </div>
            {showType ? (
              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-500" htmlFor="hw-f-type">
                  作业种类
                </label>
                <Input
                  id="hw-f-type"
                  value={draft.homeworkType}
                  onChange={(e) => setDraft({ ...draft, homeworkType: e.target.value })}
                  placeholder="全部种类"
                />
              </div>
            ) : null}
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-500" htmlFor="hw-f-from">
                布置日期从
              </label>
              <Input
                id="hw-f-from"
                type="date"
                value={draft.fromDate}
                onChange={(e) => setDraft({ ...draft, fromDate: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-500" htmlFor="hw-f-to">
                到
              </label>
              <Input
                id="hw-f-to"
                type="date"
                value={draft.toDate}
                onChange={(e) => setDraft({ ...draft, toDate: e.target.value })}
              />
            </div>
          </div>
          <div className="flex gap-2">
            <Button
              type="button"
              size="sm"
              onClick={() => {
                setApplied(draft)
                resetExpansion()
              }}
            >
              查询
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                setDraft(EMPTY_FILTERS)
                setApplied(EMPTY_FILTERS)
                resetExpansion()
              }}
            >
              重置
            </Button>
            <Button type="button" variant="outline" size="sm" onClick={reload}>
              <RefreshCw className="h-4 w-4" /> 刷新
            </Button>
          </div>
        </CardContent>
      </Card> : null}

      {/* 撤销 409 冲突清单：原样展示，提示先核对再处理 */}
      {!summaryOnly && revokeConflicts != null ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-800">
          <p className="flex items-start gap-2 font-medium">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            {revokeError}
          </p>
          <ul className="mt-2 list-disc space-y-0.5 pl-9 text-xs">
            {revokeConflicts.map((c) => (
              <li key={String(c.person_id)}>
                {c.name ?? '（未命名）'} · 状态{' '}
                {homeworkStatusLabel(c.submission_status)} · 修改于 {c.updated_at ?? '—'} ——
                <span className="font-medium">该行有后续评价编辑</span>
              </li>
            ))}
          </ul>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="mt-2"
            onClick={() => {
              setRevokeConflicts(null)
              setRevokeError(null)
            }}
          >
            知道了
          </Button>
        </div>
      ) : !summaryOnly && revokeError != null ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {revokeError}
        </div>
      ) : null}

      {/* 按期看板只呈现老师可直接行动的收交计数。 */}
      {!hideSummary ? <Card>
        <CardHeader className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <CardTitle>按期汇总</CardTitle>
            <CardDescription>按日、按周或按月查看作业批次、已交、缺交与出勤例外。</CardDescription>
          </div>
          <div className="flex gap-2 print:hidden">
            <Button
              type="button"
              size="sm"
              variant={groupBy === 'day' ? 'secondary' : 'outline'}
              onClick={() => setGroupBy('day')}
              aria-pressed={groupBy === 'day'}
            >
              按日
            </Button>
            <Button
              type="button"
              size="sm"
              variant={groupBy === 'week' ? 'secondary' : 'outline'}
              onClick={() => setGroupBy('week')}
              aria-pressed={groupBy === 'week'}
            >
              按周
            </Button>
            <Button
              type="button"
              size="sm"
              variant={groupBy === 'month' ? 'secondary' : 'outline'}
              onClick={() => setGroupBy('month')}
              aria-pressed={groupBy === 'month'}
            >
              按月
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {dashError ? (
            <p className="text-sm text-slate-600">{dashError}</p>
          ) : dash == null ? (
            <Skeleton className="h-20 w-full" />
          ) : dash.groups.length === 0 ? (
            <p className="py-4 text-center text-sm text-slate-500">当前范围暂无有效作业批次</p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-xs">
                      {groupBy === 'day' ? '日期' : groupBy === 'week' ? '周（周一）' : '月份'}
                    </TableHead>
                    <TableHead className="text-right text-xs">批次</TableHead>
                    <TableHead className="text-right text-xs">已交</TableHead>
                    <TableHead className="text-right text-xs">缺交</TableHead>
                    <TableHead className="text-right text-xs">请假</TableHead>
                    <TableHead className="text-right text-xs">负面评价</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {dash.groups.map((g) => (
                    <TableRow key={g.label}>
                      <TableCell className="whitespace-nowrap text-sm">{g.label}</TableCell>
                      <TableCell className="text-right text-sm tabular-nums">{String(g.assignments)}</TableCell>
                      <TableCell className="text-right text-sm tabular-nums">{String(g.submitted)}</TableCell>
                      <TableCell className="text-right text-sm tabular-nums">{String(g.missing)}</TableCell>
                      <TableCell className="text-right text-sm tabular-nums">{String(g.excused)}</TableCell>
                      <TableCell className="text-right text-sm tabular-nums">
                        {typeof g.negative_count === 'number' && g.negative_count > 0 ? (
                          <span className="font-medium text-rose-600">{String(g.negative_count)}</span>
                        ) : (
                          <span className="text-slate-400">0</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card> : null}

      {/* 批次列表 */}
      {!summaryOnly ? <Card>
        <CardHeader className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <CardTitle>作业记录</CardTitle>
            <CardDescription>
              {appliedNote ? '按当前筛选' : '全部'}
              {mode === 'homeroom' && viewMode === 'by_date' && dayGroups
                ? `共 ${String(dayGroups.length)} 个教学日（${String(total)} 个学科批次）；点击日期行或学科标签展开全科透视与逐人明细。`
                : `共 ${String(total)} 个批次（布置日期降序）；点击行或展开箭头查看逐人明细，点击「✏️」编辑批次信息。`}
            </CardDescription>
          </div>
          {mode === 'homeroom' ? (
            <div className="flex items-center gap-1.5 print:hidden">
              <span className="text-xs text-slate-500 font-medium mr-1">视图：</span>
              <Button
                type="button"
                size="sm"
                variant={viewMode === 'by_date' ? 'secondary' : 'outline'}
                onClick={() => {
                  setViewMode('by_date')
                  resetExpansion()
                }}
                className={cn('text-xs h-7 px-2.5 cursor-pointer', viewMode === 'by_date' && 'font-medium shadow-xs')}
              >
                按日合并
              </Button>
              <Button
                type="button"
                size="sm"
                variant={viewMode === 'flat' ? 'secondary' : 'outline'}
                onClick={() => {
                  setViewMode('flat')
                  resetExpansion()
                }}
                className={cn('text-xs h-7 px-2.5 cursor-pointer', viewMode === 'flat' && 'font-medium shadow-xs')}
              >
                单科平铺
              </Button>
            </div>
          ) : null}
        </CardHeader>
        <CardContent>
          {listError ? (
            <div className="flex flex-col items-center gap-3 py-8 text-center">
              <AlertCircle className="h-8 w-8 text-amber-400" />
              <p className="text-sm text-slate-600">{listError}</p>
              <Button variant="outline" size="sm" onClick={reload}>
                重试
              </Button>
            </div>
          ) : items == null ? (
            <div className="space-y-2">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-2/3" />
            </div>
          ) : items.length === 0 ? (
            <div className="py-8 text-center">
              <ClipboardList className="mx-auto h-8 w-8 text-slate-300" aria-hidden="true" />
              <p className="mt-2 text-sm text-slate-600">当前范围暂无作业批次</p>
              <p className="mt-1 text-xs text-slate-400">
                可先在「作业录入」建立批次；空范围不会扩展到其他班级或全年级。
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {total > items.length ? (
                <p className="text-xs text-amber-600">
                  共 {String(total)} 个批次，当前显示最近 {String(items.length)} 个；可用日期筛选查看更早批次。
                </p>
              ) : null}
            {/* lg 起改 clip：wide 表在桌面端本就放得下，clip 不产生滚动容器，
                展开区内的吸顶筛选行（sticky top-14）才能相对页面钉住；窄屏保留横向滚动 */}
            <div className="overflow-x-auto lg:overflow-x-clip">
              <Table wrapperClassName="lg:overflow-x-clip">
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-xs">布置日期</TableHead>
                    <TableHead className="text-xs">{mode === 'homeroom' && viewMode === 'by_date' ? '涵盖学科与收交状态' : '学科'}</TableHead>
                    {showType && viewMode === 'flat' ? <TableHead className="text-xs">种类</TableHead> : null}
                    {viewMode === 'flat' ? <TableHead className="text-xs">状态</TableHead> : null}
                    <TableHead className="text-right text-xs">已交</TableHead>
                    <TableHead className="text-right text-xs">缺交</TableHead>
                    <TableHead className="text-right text-xs">请假</TableHead>
                    <TableHead className="text-right text-xs">出勤异常</TableHead>
                    <TableHead className="text-right text-xs">负面评价</TableHead>
                    <TableHead className="print:hidden" aria-label="操作" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {mode === 'homeroom' && viewMode === 'by_date' && dayGroups
                    ? dayGroups.map((g) => (
                        <DayGroupRow
                          key={g.date}
                          group={g}
                          expanded={expandedDate === g.date}
                          activeFilter={activeFilter}
                          activeTab={activeDaySubject}
                          onToggle={() => toggleDayGroup(g)}
                          onSelectTab={(tab, filter) => handleDayTabChange(tab, filter)}
                          onFilterChange={(filter) => setActiveFilter(filter)}
                          onNumberClick={(filter) => toggleDayGroup(g, filter)}
                          onRevoke={(assignment) => revoke(assignment)}
                          onEdit={(assignment) => handleOpenEditModal(assignment)}
                          revoking={revoking}
                          detail={expandedDate === g.date && typeof activeDaySubject === 'number' && expandedId === activeDaySubject ? detail : null}
                          detailError={expandedDate === g.date && typeof activeDaySubject === 'number' && expandedId === activeDaySubject ? detailError : null}
                          edits={edits}
                          setEdit={setEdit}
                          patching={patching}
                          patchError={expandedDate === g.date && typeof activeDaySubject === 'number' && expandedId === activeDaySubject ? patchError : null}
                          onSaveEdits={saveEdits}
                          mode={mode}
                          scopeQ={scopeQ}
                        />
                      ))
                    : items.map((a) => (
                        <FragmentRow
                          key={a.assignment_id}
                          item={a}
                          showType={showType}
                          expanded={expandedId === a.assignment_id}
                          activeFilter={activeFilter}
                          onToggle={() => {
                            if (expandedId === a.assignment_id) {
                              setExpandedId(null)
                              setActiveFilter('all')
                            } else {
                              setExpandedId(a.assignment_id)
                              setActiveFilter('all')
                            }
                          }}
                          onFilterChange={(filter) => setActiveFilter(filter)}
                          onNumberClick={(filter) => {
                            if (expandedId !== a.assignment_id) {
                              setExpandedId(a.assignment_id)
                            }
                            setActiveFilter(filter)
                          }}
                          onRevoke={() => revoke(a)}
                          onEdit={() => handleOpenEditModal(a)}
                          revoking={revoking}
                          detail={expandedId === a.assignment_id ? detail : null}
                          detailError={expandedId === a.assignment_id ? detailError : null}
                          edits={edits}
                          setEdit={setEdit}
                          patching={patching}
                          patchError={expandedId === a.assignment_id ? patchError : null}
                          onSaveEdits={saveEdits}
                        />
                      ))}
                </TableBody>
              </Table>
            </div>
            </div>
          )}
        </CardContent>
      </Card> : null}

      <EditAssignmentModal
        open={editModalOpen}
        onOpenChange={setEditModalOpen}
        item={editingAssignment}
        mode={mode}
        scopeQ={scopeQ}
        onSuccess={() => {
          reload()
          reloadDetail()
        }}
      />
    </div>
  )
}

/** 某个具体作业批次的逐人明细与编辑表格（平铺与合并展开共用）。 */
function AssignmentDetailView({
  item,
  detail,
  detailError,
  showType,
  activeFilter,
  onFilterChange,
  edits,
  setEdit,
  patching,
  patchError,
  onSaveEdits,
  onEditAssignment,
  onBackToOverview,
}: {
  item: HomeworkAssignmentListItem
  detail: HomeworkAssignmentDetail | null
  detailError: string | null
  showType: boolean
  activeFilter: HomeworkDetailFilter
  onFilterChange: (filter: HomeworkDetailFilter) => void
  edits: Record<string, RowEdit>
  setEdit: (personId: PersonId, patch: Partial<RowEdit>, fallbackStatus: HomeworkStatus) => void
  patching: boolean
  patchError: string | null
  onSaveEdits: () => void
  onEditAssignment?: (item: HomeworkAssignmentListItem) => void
  /** 按日合并且当日有多个批次时提供：点击回到该日全科透视并重置筛选。 */
  onBackToOverview?: () => void
}) {
  const counts = useMemo(() => {
    if (!detail) {
      return {
        all: item.expected_count || item.submitted + item.missing + item.excused,
        submitted: item.submitted,
        missing: item.missing,
        forgot: 0,
        excused: item.excused,
        attendance: item.attendance_count,
        negative: item.negative_count,
      }
    }
    let submitted = 0
    let missing = 0
    let forgot = 0
    let excused = 0
    let attendance = 0
    let negative = 0
    for (const s of detail.submissions) {
      const edit = edits[String(s.person_id)]
      const status = edit?.status ?? s.status
      const att = edit?.attendance ?? s.attendance ?? ''
      const evalText = edit?.evaluation ?? s.evaluation ?? ''
      const isForgot = Boolean(
        (s.special_note && /(?:忘带|没带|未带)/.test(s.special_note)) ||
        /(?:忘带|没带|未带)/.test(evalText)
      )
      const hasAttendance = Boolean(att && att.trim() !== '')

      // 迟到、没来、忘带不计入已交
      if (status === 'submitted' && !hasAttendance && !isForgot) submitted++
      else if (status === 'missing' || isForgot || (status !== 'excused' && hasAttendance)) missing++
      else if (status === 'excused') excused++
      // 忘带单独计数（忘带是缺交的子集，缺交数字保持不变）
      if (isForgot) forgot++

      if (hasAttendance) attendance++
      // 忘带绝不计入负面评价，仅统计真正的质量负面
      if (s.quality_negative) negative++
    }
    return {
      all: detail.submissions.length,
      submitted,
      missing,
      forgot,
      excused,
      attendance,
      negative,
    }
  }, [detail, edits, item])

  const filteredSubmissions = useMemo(() => {
    if (!detail) return []
    return detail.submissions.filter((s) => {
      if (activeFilter === 'all') return true
      const edit = edits[String(s.person_id)]
      const currentStatus = edit?.status ?? (s.status as HomeworkStatus)
      const currentAttendance = edit?.attendance ?? s.attendance ?? ''
      const currentEval = edit?.evaluation ?? s.evaluation ?? ''
      const isForgot = Boolean(
        (s.special_note && /(?:忘带|没带|未带)/.test(s.special_note)) ||
        /(?:忘带|没带|未带)/.test(currentEval)
      )
      const hasAttendance = Boolean(currentAttendance && currentAttendance.trim() !== '')

      // 迟到、没来、忘带在筛选时绝不属于已交
      if (activeFilter === 'submitted') return currentStatus === 'submitted' && !hasAttendance && !isForgot
      if (activeFilter === 'missing') return currentStatus === 'missing' || isForgot || (currentStatus !== 'excused' && hasAttendance)
      // 忘带：仅看带忘带标记的行（与忘带徽章判定完全同源）
      if (activeFilter === 'forgot') return isForgot
      if (activeFilter === 'excused') return currentStatus === 'excused'
      if (activeFilter === 'attendance') return hasAttendance
      // 负面评价只筛选真正的卷面质量负面
      if (activeFilter === 'negative') return Boolean(s.quality_negative)
      return true
    })
  }, [detail, edits, activeFilter])

  if (detailError) {
    return <p className="text-sm text-slate-600">{detailError}</p>
  }
  if (detail == null) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-2/3" />
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* 吸顶筛选行：滚动长名单时人员筛选始终可见（top-14 让出 h-14 的吸顶顶栏） */}
      <div className="sticky top-14 z-10 flex flex-col gap-2 border-b border-slate-200 bg-white py-2 shadow-sm sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
          {onBackToOverview ? (
            <button
              type="button"
              title="回到当日全科透视"
              onClick={onBackToOverview}
              className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium text-brand-600 hover:bg-brand-50 hover:text-brand-700 cursor-pointer"
            >
              &larr; 返回全科透视
            </button>
          ) : null}
          <span className="font-medium text-slate-800">
            {showType ? `${detail.subject} · ${detail.homework_type}` : subjectWithContent(detail.subject, detail.homework_type)}
            {' · '}{detail.assigned_date}
          </span>
          {onEditAssignment && item.status === 'active' ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 px-1.5 text-xs text-brand-600 hover:text-brand-700 hover:bg-brand-50"
              onClick={() => onEditAssignment(item)}
            >
              <Pencil className="h-3 w-3 mr-1" />
              修改批次信息
            </Button>
          ) : null}
          <span className="text-xs text-slate-400">修改保存后会自动刷新明细</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-slate-500 mr-0.5 font-medium text-xs">人员筛选：</span>
          <FilterChipBar activeFilter={activeFilter} onFilterChange={onFilterChange} counts={counts} />
          <FilterFocusHint filter={activeFilter} count={filteredSubmissions.length} />
        </div>
      </div>
      {patchError ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {patchError}
        </div>
      ) : null}
      {filteredSubmissions.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-6 text-center text-sm text-slate-500 bg-white rounded-md border border-dashed border-slate-200">
          <p>当前分类下暂无人员记录</p>
          <Button
            type="button"
            variant="link"
            size="sm"
            className="mt-1 text-xs text-brand-600"
            onClick={() => onFilterChange('all')}
          >
            查看全部学生名单 ({counts.all} 人)
          </Button>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs">学生</TableHead>
                <TableHead className="text-xs">{item.subject === '考勤' ? '出勤状态' : '状态'}</TableHead>
                {item.subject === '考勤' ? (
                  <TableHead className="text-xs">说明 / 备注</TableHead>
                ) : (
                  <>
                    <TableHead className="text-xs">出勤异常</TableHead>
                    <TableHead className="text-xs">作业内容</TableHead>
                    <TableHead className="text-xs">评价</TableHead>
                  </>
                )}
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredSubmissions.map((s) => {
                const edit = edits[String(s.person_id)]
                const currentStatus = edit?.status ?? (s.status as HomeworkStatus)
                const currentEvaluation = edit?.evaluation ?? evaluationDraftText(s.evaluation, s.attendance, item.homework_type)
                const currentAttendance = edit?.attendance ?? s.attendance ?? ''
                const isForgot = Boolean(
                  (s.special_note && /(?:忘带|没带|未带)/.test(s.special_note)) ||
                  /(?:忘带|没带|未带)/.test(edit?.evaluation ?? s.evaluation ?? '')
                )
                const dirty =
                  currentStatus !== s.status || currentEvaluation !== evaluationDraftText(s.evaluation, s.attendance, item.homework_type) || currentAttendance !== (s.attendance ?? '')
                return (
                  <TableRow key={String(s.person_id)} className={dirty ? 'bg-brand-50/50' : undefined}>
                    <TableCell className="whitespace-nowrap text-sm">
                      {s.name ?? '（未命名）'}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        <select
                          aria-label={`${String(s.name ?? s.person_id)} 状态`}
                          value={currentStatus}
                          onChange={(e) =>
                            setEdit(s.person_id, { status: e.target.value as HomeworkStatus }, s.status as HomeworkStatus)
                          }
                          className="h-8 rounded-md border border-slate-200 bg-white px-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          {HOMEWORK_STATUS_OPTIONS.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </select>
                        {isForgot ? (
                          <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-700 text-xs px-1.5 py-0.5 shrink-0">
                            忘带
                          </Badge>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Input
                        className="h-8 w-28 text-sm"
                        value={currentAttendance}
                        placeholder="如：迟到、没来"
                        onChange={(e) => setEdit(s.person_id, { attendance: e.target.value }, s.status as HomeworkStatus)}
                        aria-label={`${String(s.name ?? s.person_id)} 出勤异常`}
                      />
                    </TableCell>
                    <TableCell>
                      <span className="text-sm text-slate-600" title="作业内容来自批次信息，可用「✏️ 编辑批次」修改">
                        {displayHomeworkContent(item.homework_type)}
                      </span>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <Input
                          className="h-8 w-36 text-sm"
                          value={currentEvaluation}
                          placeholder={item.subject === '考勤' ? '备注（可选）' : '评价（可选，如 不认真）'}
                          onChange={(e) => setEdit(s.person_id, { evaluation: e.target.value }, s.status as HomeworkStatus)}
                          aria-label={`${String(s.name ?? s.person_id)} ${item.subject === '考勤' ? '备注' : '评价'}`}
                        />
                        {s.quality_negative ? <Badge variant="destructive">负面</Badge> : null}
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
        <Button type="button" size="sm" onClick={onSaveEdits} disabled={patching}>
          {patching ? '保存中…' : '保存修改'}
        </Button>
        {activeFilter !== 'all' ? (
          <span className="text-xs text-slate-400">
            当前仅显示「{
              activeFilter === 'missing' ? '缺交' :
              activeFilter === 'forgot' ? '忘带' :
              activeFilter === 'excused' ? '请假' :
              activeFilter === 'submitted' ? '已交' :
              activeFilter === 'attendance' ? '出勤异常' : '负面评价'
            }」人员（共 {filteredSubmissions.length} 人）；如需核对全班可切回「全部」
          </span>
        ) : null}
      </div>
    </div>
  )
}

/** 当日全科透视面板：汇总展示当日各科概况与待跟进学生（缺交/迟到/负面评价）。
 * 性能口径：全班名单只拉一次（取当日任一批次明细）；各学生的异常归属
 * 全部来自列表项自带的例外学生 ID（excused/missing/attendance/negative_ids），
 * 不再对每个批次各发一次明细请求。 */
function DayGroupOverview({
  assignments,
  mode,
  scopeQ,
  activeFilter = 'all',
  onFilterChange,
  onSelectSubject,
  onEdit,
  onRevoke,
  revoking = false,
}: {
  assignments: HomeworkAssignmentListItem[]
  mode: WorkspaceMode
  scopeQ: HomeworkScopeQuery
  activeFilter?: HomeworkDetailFilter
  onFilterChange?: (filter: HomeworkDetailFilter) => void
  onSelectSubject: (assignmentId: number, filter?: HomeworkDetailFilter) => void
  /** 卡片右上角 ✏️：打开既有修改批次信息弹窗。 */
  onEdit?: (assignment: HomeworkAssignmentListItem) => void
  /** 卡片右上角 🗑：走既有撤销流程（window.confirm + 409 冲突清单拦截）。 */
  onRevoke?: (assignment: HomeworkAssignmentListItem) => void
  /** 撤销请求进行中：禁用撤销图标防重复提交。 */
  revoking?: boolean
}) {
  const [loading, setLoading] = useState(true)
  const [roster, setRoster] = useState<Array<{ personId: number; name: string }> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showAllClean, setShowAllClean] = useState(false)

  const activeAssignments = useMemo(
    () => assignments.filter((a) => a.status === 'active'),
    [assignments]
  )
  const homeworkAssignments = useMemo(
    () => activeAssignments.filter((a) => a.subject !== '考勤'),
    [activeAssignments]
  )

  useEffect(() => {
    // 名册：取当日任一有效批次的全班名单（含姓名），一天只发一次请求
    const first = activeAssignments[0]
    let cancelled = false
    setLoading(true)
    setError(null)
    if (!first) {
      setRoster([])
      setLoading(false)
      return
    }
    homeworkAssignmentDetail(first.assignment_id, mode, scopeQ)
      .then((d) => {
        if (cancelled) return
        setRoster(d.submissions.map((s) => ({ personId: Number(s.person_id), name: s.name || '（未命名）' })))
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(apiErrorMessage(err))
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [activeAssignments, mode, scopeQ])

  // 聚合各学生当天的异常与提交情况（全部来自列表项例外 ID）
  const { studentSummaries, studentIssues, cleanStudents } = useMemo(() => {
    if (!roster) return { studentSummaries: [], studentIssues: [], cleanStudents: [] }
    interface Summary {
      personId: number
      name: string
      missing: { subject: string; assignmentId: number }[]
      forgot: { subject: string; assignmentId: number }[]
      attendance: { subject: string; assignmentId: number }[]
      negative: { subject: string; assignmentId: number }[]
      excused: { subject: string; assignmentId: number }[]
      submittedCount: number
    }
    const byId = new Map<number, Summary>()
    for (const r of roster) {
      byId.set(r.personId, {
        personId: r.personId,
        name: r.name,
        missing: [],
        forgot: [],
        attendance: [],
        negative: [],
        excused: [],
        submittedCount: 0,
      })
    }
    for (const a of activeAssignments) {
      const isAtt = a.subject === '考勤'
      const missingSet = new Set(a.missing_ids ?? [])
      const excusedSet = new Set(a.excused_ids ?? [])
      const label = isAtt ? '考勤' : subjectWithContent(a.subject, a.homework_type)
      for (const id of a.excused_ids ?? []) {
        byId.get(id)?.excused.push({ subject: label, assignmentId: a.assignment_id })
      }
      for (const id of a.missing_ids ?? []) {
        byId.get(id)?.missing.push({ subject: label, assignmentId: a.assignment_id })
      }
      // 忘带来自列表项新字段 forgot_ids（忘带是缺交子集，只加标签不改缺交归属）
      for (const id of a.forgot_ids ?? []) {
        byId.get(id)?.forgot.push({ subject: label, assignmentId: a.assignment_id })
      }
      for (const id of a.attendance_ids ?? []) {
        byId.get(id)?.attendance.push({ subject: label, assignmentId: a.assignment_id })
      }
      for (const id of a.negative_ids ?? []) {
        byId.get(id)?.negative.push({ subject: label, assignmentId: a.assignment_id })
      }
      // 已交 = 该学科批次中既不在缺交也不在请假例外中的学生（考勤批次不算作业）
      if (!isAtt) {
        for (const item of byId.values()) {
          if (!missingSet.has(item.personId) && !excusedSet.has(item.personId)) item.submittedCount++
        }
      }
    }

    const allList = Array.from(byId.values())
    const issues: typeof allList = []
    const clean: Array<{ personId: number; name: string }> = []
    for (const item of allList) {
      if (item.missing.length > 0 || item.attendance.length > 0 || item.negative.length > 0 || item.excused.length > 0) {
        issues.push(item)
      } else {
        clean.push({ personId: item.personId, name: item.name })
      }
    }
    issues.sort(
      (a, b) =>
        b.missing.length - a.missing.length ||
        b.excused.length - a.excused.length ||
        a.name.localeCompare(b.name, 'zh-CN')
    )
    return { studentSummaries: allList, studentIssues: issues, cleanStudents: clean }
  }, [roster, activeAssignments])

  // 根据 activeFilter 筛选得到当前应该展示的学生名单
  const filteredList = useMemo(() => {
    if (activeFilter === 'missing') {
      return studentSummaries
        .filter((s) => s.missing.length > 0)
        .sort((a, b) => b.missing.length - a.missing.length || a.name.localeCompare(b.name, 'zh-CN'))
    }
    if (activeFilter === 'forgot') {
      return studentSummaries
        .filter((s) => s.forgot.length > 0)
        .sort((a, b) => b.forgot.length - a.forgot.length || a.name.localeCompare(b.name, 'zh-CN'))
    }
    if (activeFilter === 'excused') {
      return studentSummaries
        .filter((s) => s.excused.length > 0)
        .sort((a, b) => b.excused.length - a.excused.length || a.name.localeCompare(b.name, 'zh-CN'))
    }
    if (activeFilter === 'attendance') {
      return studentSummaries
        .filter((s) => s.attendance.length > 0)
        .sort((a, b) => b.attendance.length - a.attendance.length || a.name.localeCompare(b.name, 'zh-CN'))
    }
    if (activeFilter === 'negative') {
      return studentSummaries
        .filter((s) => s.negative.length > 0)
        .sort((a, b) => b.negative.length - a.negative.length || a.name.localeCompare(b.name, 'zh-CN'))
    }
    if (activeFilter === 'submitted') {
      return studentSummaries
        .filter((s) => s.submittedCount > 0)
        .sort((a, b) => b.submittedCount - a.submittedCount || a.name.localeCompare(b.name, 'zh-CN'))
    }
    return studentIssues
  }, [studentSummaries, studentIssues, activeFilter])

  const missingCount = studentSummaries.filter((s) => s.missing.length > 0).length
  const forgotCount = studentSummaries.filter((s) => s.forgot.length > 0).length
  const excusedCount = studentSummaries.filter((s) => s.excused.length > 0).length
  const attendanceCount = studentSummaries.filter((s) => s.attendance.length > 0).length
  const negativeCount = studentSummaries.filter((s) => s.negative.length > 0).length
  const submittedCount = studentSummaries.filter((s) => s.submittedCount > 0).length

  const chipCounts: Record<HomeworkDetailFilter, number> = {
    all: studentSummaries.length,
    missing: missingCount,
    forgot: forgotCount,
    excused: excusedCount,
    submitted: submittedCount,
    attendance: attendanceCount,
    negative: negativeCount,
  }

  if (loading) {
    return (
      <div className="space-y-3 py-4">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-28 w-full" />
      </div>
    )
  }

  if (error) {
    return <div className="rounded-md bg-amber-50 p-3 text-sm text-amber-800">{error}</div>
  }

  return (
    <div className="space-y-3.5">
      {/* 顶部人员分类控制栏（吸顶：滚动长名单时筛选始终可见） */}
      <div className="sticky top-14 z-10 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white p-2 shadow-sm">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-slate-400 font-medium text-xs">人员筛选:</span>
          <FilterChipBar
            activeFilter={activeFilter}
            onFilterChange={(f) => onFilterChange?.(f)}
            counts={chipCounts}
            allLabel="📌 全部 (全科透视)"
          />
        </div>
        <FilterFocusHint filter={activeFilter} count={filteredList.length} />
        {activeFilter !== 'all' ? (
          <button
            type="button"
            onClick={() => onFilterChange?.('all')}
            className="text-xs text-brand-600 hover:text-brand-700 hover:underline flex items-center gap-1 cursor-pointer font-medium"
          >
            <span>返回完整全科透视</span>
            &rarr;
          </button>
        ) : null}
      </div>

      {/* 仅在 activeFilter === 'all' 时展示顶部各学科概况卡片（更扁更窄，一行约 6 张）：
          整卡点击进该科明细并默认筛选缺交；右上角 ✏️/🗑 走既有编辑弹窗与撤销流程 */}
      {activeFilter === 'all' ? (
        <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-2">
          {assignments.map((a) => {
            const isAtt = a.subject === '考勤'
            const abnormalCount = a.attendance_ids?.length ?? (a.attendance_count || a.missing)
            const hasAbnormal = isAtt && abnormalCount > 0
            // 请假人数与出勤异常分开呈现：有人请假时当天不再是「全勤」
            const excusedCount = isAtt ? (a.excused_ids?.length ?? a.excused) : 0
            const hasExcused = isAtt && excusedCount > 0
            const cardLabel = isAtt ? '📋 出勤登记' : subjectWithContent(a.subject, a.homework_type)

            return (
              <div
                key={a.assignment_id}
                role="button"
                tabIndex={0}
                title={`点击查看「${cardLabel}」明细（已筛选缺交）`}
                onClick={() => onSelectSubject(a.assignment_id, 'missing')}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    onSelectSubject(a.assignment_id, 'missing')
                  }
                }}
                className={cn(
                  'rounded-lg border px-2 py-1.5 transition-all cursor-pointer hover:shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  a.status === 'revoked'
                    ? 'border-slate-200 bg-slate-50/60 opacity-60'
                    : isAtt
                      ? hasAbnormal
                        ? 'border-amber-200 bg-amber-50/40 hover:border-amber-300'
                        : hasExcused
                          ? 'border-blue-200 bg-blue-50/40 hover:border-blue-300'
                          : 'border-slate-200 bg-white hover:border-slate-300'
                      : a.missing > 0
                        ? 'border-rose-200 bg-rose-50/40 hover:border-rose-300'
                        : 'border-slate-200 bg-white hover:border-slate-300'
                )}
              >
                <div className="flex items-start justify-between gap-1">
                  <span className="truncate text-xs font-medium text-slate-800" title={cardLabel}>
                    {cardLabel}
                  </span>
                  {a.status === 'active' ? (
                    <div className="flex shrink-0 items-center gap-0.5">
                      <button
                        type="button"
                        aria-label="编辑批次"
                        title="编辑批次（改日期/作业名/学科）"
                        className="rounded p-0.5 text-slate-400 hover:bg-brand-50 hover:text-brand-600 cursor-pointer"
                        onClick={(e) => {
                          // 阻止冒泡：图标按钮不触发整卡点击
                          e.stopPropagation()
                          onEdit?.(a)
                        }}
                      >
                        <Pencil className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        aria-label="撤销批次"
                        title="撤销批次"
                        disabled={revoking}
                        className="rounded p-0.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600 cursor-pointer disabled:opacity-40"
                        onClick={(e) => {
                          e.stopPropagation()
                          onRevoke?.(a)
                        }}
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </div>
                  ) : (
                    <span className="shrink-0 text-[10px] text-slate-400">已撤销</span>
                  )}
                </div>
                <div className="mt-1 flex items-center justify-between gap-1 text-[11px] text-slate-500">
                  <span className="truncate">
                    {a.status === 'revoked' ? (
                      '批次已撤销'
                    ) : isAtt ? (
                      hasAbnormal ? (
                        <>
                          出勤异常 <span className="font-semibold text-amber-600">{abnormalCount}</span>
                          {hasExcused ? (
                            <> · 请假 <span className="font-semibold text-blue-600">{excusedCount}</span></>
                          ) : null}
                          {' · '}正常出勤 {a.submitted}
                        </>
                      ) : hasExcused ? (
                        <><span className="font-semibold text-blue-600">请假 {excusedCount}</span> · 正常出勤 {a.submitted}</>
                      ) : (
                        <span className="font-medium text-emerald-600">全勤 · 正常出勤 {a.submitted}</span>
                      )
                    ) : a.missing > 0 ? (
                      <><span className="font-semibold text-rose-600">缺 {a.missing}</span> · 已交 {a.submitted}</>
                    ) : (
                      <span className="font-medium text-emerald-600">齐 · 已交 {a.submitted}</span>
                    )}
                  </span>
                  <span className="shrink-0 font-medium text-brand-600">{isAtt ? '去查看' : '去编辑'} &rarr;</span>
                </div>
              </div>
            )
          })}
        </div>
      ) : null}

      {/* 学生名单区域：如果是筛选态，直接呈现精准筛选名单；如果是全部，呈现待跟进学生综合 */}
      <div className="rounded-lg border border-slate-200 bg-white p-3.5 shadow-2xs">
        <div className="flex items-center justify-between border-b border-slate-100 pb-2.5">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-slate-800">
              {activeFilter === 'all'
                ? '当日待跟进学生'
                : activeFilter === 'forgot'
                  ? '当日忘带学生'
                  : activeFilter === 'excused'
                    ? '当日请假免交学生'
                    : activeFilter === 'negative'
                      ? '当日作业负面评价学生'
                      : activeFilter === 'attendance'
                        ? '当日出勤异常学生'
                        : activeFilter === 'missing'
                          ? '当日作业缺交学生'
                          : '当日正常已交学生'}
              {filteredList.length > 0 ? (
                <span
                  className={cn(
                    'ml-1.5 text-xs font-semibold',
                    activeFilter === 'excused'
                      ? 'text-blue-600'
                      : activeFilter === 'negative'
                        ? 'text-purple-600'
                        : activeFilter === 'attendance' || activeFilter === 'forgot'
                          ? 'text-amber-600'
                          : activeFilter === 'submitted'
                            ? 'text-emerald-600'
                            : 'text-rose-600'
                  )}
                >
                  ({filteredList.length} 人)
                </span>
              ) : null}
            </span>
            <span className="text-xs text-slate-400">
              {activeFilter === 'all'
                ? '汇总缺交、出勤异常、请假与负面评价情况；点击标签直接前往编辑对应学科'
                : activeFilter === 'excused'
                  ? '当日登记请假免交的学生名单'
                  : activeFilter === 'forgot'
                    ? '当日登记忘带作业的学生名单（忘带仍计入缺交统计）'
                    : '点击学科标签可直达该科作业批次进行查看与编辑'}
            </span>
          </div>
        </div>

        {filteredList.length === 0 ? (
          <div className="py-6 text-center text-sm">
            <p className="text-slate-500">
              {activeFilter === 'excused'
                ? '🎉 当日无学生请假免交记录'
                : activeFilter === 'negative'
                  ? '🎉 当日无作业负面评价记录'
                  : activeFilter === 'attendance'
                    ? '🎉 当日无出勤异常记录'
                    : activeFilter === 'forgot'
                      ? '🎉 本日无忘带记录'
                      : activeFilter === 'missing'
                        ? '🎉 本日各科作业全部收齐，无缺交记录'
                        : activeFilter === 'submitted'
                          ? '暂无已交记录'
                          : '🎉 本日各科作业全部收齐，无缺交或出勤异常记录'}
            </p>
            {activeFilter !== 'all' ? (
              <Button
                variant="outline"
                size="sm"
                className="mt-2 text-xs"
                onClick={() => onFilterChange?.('all')}
              >
                查看全部学生 (全科透视)
              </Button>
            ) : null}
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {filteredList.map((s) => (
              <div
                key={String(s.personId)}
                className="flex flex-col sm:flex-row sm:items-center justify-between py-2.5 gap-2"
              >
                <div className="flex items-center gap-3">
                  <span className="font-medium text-sm text-slate-800 min-w-[70px]">{s.name}</span>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {/* 缺交胶囊 */}
                    {(activeFilter === 'all' || activeFilter === 'missing') &&
                      s.missing.map((m) => (
                        <button
                          key={m.assignmentId}
                          type="button"
                          onClick={() => onSelectSubject(m.assignmentId, 'missing')}
                          title={`点击前往编辑「${m.subject}」作业（已筛选缺交）`}
                          className="inline-flex items-center gap-1 rounded bg-rose-50 px-2 py-0.5 text-xs font-medium text-rose-700 border border-rose-200/80 hover:bg-rose-100 cursor-pointer transition-colors"
                        >
                          <span>缺交: {m.subject}</span>
                        </button>
                      ))}

                    {/* 忘带胶囊：琥珀色（与忘带徽章一致），点击直达该科明细并筛选缺交（忘带是缺交子集） */}
                    {(activeFilter === 'all' || activeFilter === 'forgot') &&
                      s.forgot.map((f) => (
                        <button
                          key={`forgot-${f.assignmentId}`}
                          type="button"
                          onClick={() => onSelectSubject(f.assignmentId, 'missing')}
                          title={`点击前往编辑「${f.subject}」作业（已筛选缺交）`}
                          className="inline-flex items-center gap-1 rounded bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700 border border-amber-200/80 hover:bg-amber-100 cursor-pointer transition-colors"
                        >
                          <span>忘带: {f.subject}</span>
                        </button>
                      ))}

                    {/* 请假胶囊：请假不分学科，只显示统一的请假标签 */}
                    {(activeFilter === 'all' || activeFilter === 'excused') && s.excused.length > 0 ? (
                      <span
                        className="inline-flex items-center gap-1 rounded bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 border border-blue-200/80"
                        title="当日请假免交"
                      >
                        <span>请假</span>
                      </span>
                    ) : null}

                    {/* 出勤异常胶囊 */}
                    {(activeFilter === 'all' || activeFilter === 'attendance') &&
                      s.attendance.map((att) => (
                        <button
                          key={att.assignmentId}
                          type="button"
                          onClick={() => onSelectSubject(att.assignmentId, 'attendance')}
                          title={`点击前往查看「${att.subject}」出勤`}
                          className="inline-flex items-center gap-1 rounded bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700 border border-amber-200/80 hover:bg-amber-100 cursor-pointer transition-colors"
                        >
                          <span>{att.subject === '考勤' ? '出勤异常' : `${att.subject}: 出勤异常`}</span>
                        </button>
                      ))}

                    {/* 负面评价胶囊 */}
                    {(activeFilter === 'all' || activeFilter === 'negative') &&
                      s.negative.map((neg) => (
                        <button
                          key={neg.assignmentId}
                          type="button"
                          onClick={() => onSelectSubject(neg.assignmentId, 'negative')}
                          title={`点击前往查看「${neg.subject}」评价`}
                          className="inline-flex items-center gap-1 rounded bg-purple-50 px-2 py-0.5 text-xs font-medium text-purple-700 border border-purple-200/80 hover:bg-purple-100 cursor-pointer transition-colors"
                        >
                          <span>{neg.subject}: 负面评价</span>
                        </button>
                      ))}

                    {/* 已交汇总胶囊（仅在选已交时展示）：不逐科铺胶囊，一人一条汇总 */}
                    {activeFilter === 'submitted' && (
                      homeworkAssignments.length > 0 &&
                      s.submittedCount === homeworkAssignments.length ? (
                        <span className="inline-flex items-center gap-1 rounded bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 border border-emerald-200">
                          全齐（当日 {homeworkAssignments.length} 科作业）
                        </span>
                      ) : (
                        <span
                          className="inline-flex items-center gap-1 rounded bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 border border-emerald-200"
                          title="点击学科胶囊可进入对应批次明细"
                        >
                          已交 {s.submittedCount}/{homeworkAssignments.length} 科
                        </span>
                      )
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-2 self-start sm:self-auto">
                  {s.missing.length >= 2 && (activeFilter === 'all' || activeFilter === 'missing') ? (
                    <span className="text-[11px] text-rose-500 font-medium bg-rose-50/50 px-1.5 py-0.5 rounded">
                      连缺 {s.missing.length} 科
                    </span>
                  ) : null}
                  {s.negative.length >= 2 && activeFilter === 'negative' ? (
                    <span className="text-[11px] text-purple-500 font-medium bg-purple-50/50 px-1.5 py-0.5 rounded">
                      累计 {s.negative.length} 项负面
                    </span>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* 其余全齐学生折叠栏：仅在 activeFilter === 'all' 时展示 */}
        {activeFilter === 'all' && cleanStudents.length > 0 ? (
          <div className="mt-3 border-t border-slate-100 pt-2.5 text-xs">
            <button
              type="button"
              onClick={() => setShowAllClean(!showAllClean)}
              className="text-slate-500 hover:text-slate-700 flex items-center gap-1 cursor-pointer"
            >
              <span>{showAllClean ? '收起' : '展开'}其余各科全交学生 ({cleanStudents.length} 人)</span>
              <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', showAllClean && 'rotate-180')} />
            </button>
            {showAllClean ? (
              <div className="mt-2 flex flex-wrap gap-1.5 p-2 bg-slate-50/80 rounded border border-slate-100">
                {cleanStudents.map((c) => (
                  <span
                    key={String(c.personId)}
                    className="text-slate-600 text-xs px-1.5 py-0.5 bg-white rounded border border-slate-200/60"
                  >
                    {c.name}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  )
}

/** 班主任工作台按日合并的一行 + 展开透视与单科明细编辑。 */
function DayGroupRow({
  group,
  expanded,
  activeFilter,
  activeTab,
  onToggle,
  onSelectTab,
  onFilterChange,
  onNumberClick,
  onRevoke,
  onEdit,
  revoking,
  detail,
  detailError,
  edits,
  setEdit,
  patching,
  patchError,
  onSaveEdits,
  mode,
  scopeQ,
}: {
  group: HomeworkDayGroup
  expanded: boolean
  activeFilter: HomeworkDetailFilter
  activeTab: string | number
  onToggle: () => void
  onSelectTab: (tab: string | number, filter?: HomeworkDetailFilter) => void
  onFilterChange: (filter: HomeworkDetailFilter) => void
  onNumberClick: (filter: HomeworkDetailFilter) => void
  onRevoke: (assignment: HomeworkAssignmentListItem) => void
  onEdit?: (assignment: HomeworkAssignmentListItem) => void
  revoking: boolean
  detail: HomeworkAssignmentDetail | null
  detailError: string | null
  edits: Record<string, RowEdit>
  setEdit: (personId: PersonId, patch: Partial<RowEdit>, fallbackStatus: HomeworkStatus) => void
  patching: boolean
  patchError: string | null
  onSaveEdits: () => void
  mode: WorkspaceMode
  scopeQ: HomeworkScopeQuery
}) {
  const activeAssignment = typeof activeTab === 'number'
    ? group.assignments.find((a) => a.assignment_id === activeTab) ?? group.assignments[0]
    : group.assignments[0]

  return (
    <>
      <TableRow className="cursor-pointer hover:bg-slate-50/80 transition-colors" aria-expanded={expanded} onClick={onToggle}>
        <TableCell className="whitespace-nowrap text-sm">
          <div className="flex items-center gap-1.5">
            <span className="font-semibold text-slate-800">{group.date}</span>
            {group.dayOfWeek ? (
              <span className="text-[11px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 font-normal">
                {group.dayOfWeek}
              </span>
            ) : null}
          </div>
        </TableCell>
        <TableCell className="text-sm">
          <div className="flex flex-wrap items-center gap-1.5">
            {group.assignments.map((a) => {
              const isAtt = a.subject === '考勤'
              // 出勤异常数以例外学生 ID 去重为准：涵盖迟到/没来及未注明原因的缺勤
              const abnormalCount = a.attendance_ids?.length ?? (a.attendance_count || a.missing)
              const hasAbnormal = isAtt && abnormalCount > 0
              // 请假人数与出勤异常分开呈现：有人请假时当天不再是「全勤」
              const excusedCount = isAtt ? (a.excused_ids?.length ?? a.excused) : 0
              const hasExcused = isAtt && excusedCount > 0

              return (
                <button
                  key={a.assignment_id}
                  type="button"
                  title={
                    isAtt
                      ? hasAbnormal && hasExcused
                        ? `出勤异常 ${abnormalCount} · 请假 ${excusedCount}`
                        : hasAbnormal
                          ? `出勤异常 ${abnormalCount}`
                          : hasExcused
                            ? `请假 ${excusedCount}`
                            : '全勤'
                      : `点击查看/编辑「${subjectWithContent(a.subject, a.homework_type)}」作业`
                  }
                  onClick={(e) => {
                    e.stopPropagation()
                    onSelectTab(a.assignment_id, isAtt
                      ? (hasAbnormal ? 'attendance' : hasExcused ? 'excused' : 'all')
                      : (a.missing > 0 ? 'missing' : 'all'))
                  }}
                  className={cn(
                    'inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs transition-colors cursor-pointer border',
                    a.status === 'revoked'
                      ? 'bg-slate-100 text-slate-400 border-slate-200 line-through'
                      : isAtt
                        ? hasAbnormal
                          ? 'bg-amber-50 text-amber-700 border-amber-200 font-medium hover:bg-amber-100'
                          : hasExcused
                            ? 'bg-blue-50 text-blue-700 border-blue-200 font-medium hover:bg-blue-100'
                            : 'bg-white text-slate-700 border-slate-200 hover:bg-slate-100'
                        : a.missing > 0
                          ? 'bg-rose-50 text-rose-700 border-rose-200 font-medium hover:bg-rose-100'
                          : 'bg-white text-slate-700 border-slate-200 hover:bg-slate-100'
                  )}
                >
                  <span>{isAtt ? '📋 出勤' : subjectWithContent(a.subject, a.homework_type)}</span>
                  {a.status === 'revoked' ? (
                    <span className="text-[10px] text-slate-400">已撤销</span>
                  ) : isAtt ? (
                    hasAbnormal ? (
                      <span className="text-[11px] font-semibold text-amber-600">
                        {`出勤异常 ${abnormalCount}${hasExcused ? ` · 请假 ${excusedCount}` : ''}`}
                      </span>
                    ) : hasExcused ? (
                      <span className="text-[11px] font-semibold text-blue-600">请假 {excusedCount}</span>
                    ) : (
                      <span className="text-[10px] text-emerald-600 font-medium">全勤</span>
                    )
                  ) : a.missing > 0 ? (
                    <span className="text-[11px] font-semibold text-rose-600">缺{a.missing}</span>
                  ) : (
                    <span className="text-[10px] text-emerald-600 font-medium">齐</span>
                  )}
                </button>
              )
            })}
            {group.hasRevoked && !group.allRevoked ? (
              <span className="text-[11px] text-slate-400 ml-1">含撤销批次</span>
            ) : null}
          </div>
        </TableCell>
        <NumberFilterCell
          count={group.totalSubmitted}
          filter="submitted"
          title={`点击筛选全天已交学生（共 ${group.totalSubmitted} 份）`}
          active={expanded && activeFilter === 'submitted'}
          onClick={onNumberClick}
        />
        <NumberFilterCell
          count={group.totalMissing}
          filter="missing"
          title={`点击筛选全天作业缺交（共 ${group.totalMissing} 份，为各科作业缺交份数累加）`}
          active={expanded && activeFilter === 'missing'}
          onClick={onNumberClick}
        />
        <NumberFilterCell
          count={group.totalExcused}
          filter="excused"
          title={`点击筛选全天请假免交学生（共 ${group.totalExcused} 人）`}
          active={expanded && activeFilter === 'excused'}
          onClick={onNumberClick}
        />
        <NumberFilterCell
          count={group.totalAttendance}
          filter="attendance"
          title={`点击筛选全天出勤异常学生（共 ${group.totalAttendance} 人）`}
          active={expanded && activeFilter === 'attendance'}
          onClick={onNumberClick}
        />
        <NumberFilterCell
          count={group.totalNegative}
          filter="negative"
          title={`点击筛选全天负面评价学生（共 ${group.totalNegative} 人）`}
          active={expanded && activeFilter === 'negative'}
          onClick={onNumberClick}
        />
        <TableCell className="print:hidden" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center justify-end gap-1">
            <button
              type="button"
              className="p-1 rounded hover:bg-slate-100 cursor-pointer"
              aria-label={expanded && activeFilter === 'all' && activeTab === 'overview' ? '收起明细' : '展开全科透视下拉菜单'}
              title={expanded && activeFilter === 'all' && activeTab === 'overview' ? '收起明细' : '展开全科透视下拉菜单'}
              onClick={onToggle}
            >
              <ChevronDown
                className={cn('h-4 w-4 text-slate-400 transition-transform', expanded && 'rotate-180')}
              />
            </button>
          </div>
        </TableCell>
      </TableRow>
      {expanded ? (
        <TableRow className="hover:bg-transparent">
          <TableCell colSpan={8} className="bg-slate-50/70 p-4">
            <div className="space-y-3">
              {/* 展开区标题行：全科透视只留小标题+提示（原学科页签行已删，点卡片进单科明细）；
                  单科明细态保留编辑/撤销入口（单批次日没有透视卡片，这里是唯一撤销入口） */}
              <div className={cn('flex flex-wrap items-center justify-between gap-2', !(activeTab === 'overview' && group.assignments.length > 1) && 'border-b border-slate-200/80 pb-2.5')}>
                {activeTab === 'overview' && group.assignments.length > 1 ? (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-sm font-semibold text-slate-800">全科透视</span>
                    <span className="text-xs text-slate-400">点学科卡片进单科明细；点学生标签直达对应学科</span>
                  </div>
                ) : null}
                {activeAssignment && activeAssignment.status === 'active' && typeof activeTab === 'number' ? (
                  <div className="ml-auto flex items-center gap-1 text-xs">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs text-slate-700 border-slate-200 hover:bg-slate-50 cursor-pointer"
                      onClick={() => onEdit?.(activeAssignment)}
                    >
                      <Pencil className="h-3.5 w-3.5 mr-1" />
                      编辑批次
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs text-rose-600 border-rose-200 hover:bg-rose-50 cursor-pointer"
                      disabled={revoking}
                      onClick={() => onRevoke(activeAssignment)}
                    >
                      <Undo2 className="h-3.5 w-3.5 mr-1" />
                      撤销「{activeAssignment.subject}」批次
                    </Button>
                  </div>
                ) : null}
              </div>

              {activeTab === 'overview' && group.assignments.length > 1 ? (
                <DayGroupOverview
                  assignments={group.assignments}
                  mode={mode}
                  scopeQ={scopeQ}
                  activeFilter={activeFilter}
                  onFilterChange={onFilterChange}
                  onSelectSubject={(id, filter) => onSelectTab(id, filter)}
                  onEdit={onEdit}
                  onRevoke={onRevoke}
                  revoking={revoking}
                />
              ) : activeAssignment ? (
                <AssignmentDetailView
                  item={activeAssignment}
                  detail={detail}
                  detailError={detailError}
                  showType={false}
                  activeFilter={activeFilter}
                  onFilterChange={onFilterChange}
                  edits={edits}
                  setEdit={setEdit}
                  patching={patching}
                  patchError={patchError}
                  onSaveEdits={onSaveEdits}
                  onEditAssignment={onEdit}
                  onBackToOverview={group.assignments.length > 1 ? () => onSelectTab('overview') : undefined}
                />
              ) : null}
            </div>
          </TableCell>
        </TableRow>
      ) : null}
    </>
  )
}

/** 表格一行 + 展开编辑区（Fragment 结构沿用成绩页行展开模式，供平铺模式与教学工作台使用）。 */
function FragmentRow({
  item,
  showType,
  expanded,
  activeFilter,
  onToggle,
  onFilterChange,
  onNumberClick,
  onRevoke,
  onEdit,
  revoking,
  detail,
  detailError,
  edits,
  setEdit,
  patching,
  patchError,
  onSaveEdits,
}: {
  item: HomeworkAssignmentListItem
  /** 教学工作台展示作业种类；班主任工作台整列隐藏（含迁移批次 'legacy'）。 */
  showType: boolean
  expanded: boolean
  activeFilter: HomeworkDetailFilter
  onToggle: () => void
  onFilterChange: (filter: HomeworkDetailFilter) => void
  onNumberClick: (filter: HomeworkDetailFilter) => void
  onRevoke: () => void
  onEdit?: () => void
  revoking: boolean
  detail: HomeworkAssignmentDetail | null
  detailError: string | null
  edits: Record<string, RowEdit>
  setEdit: (personId: PersonId, patch: Partial<RowEdit>, fallbackStatus: HomeworkStatus) => void
  patching: boolean
  patchError: string | null
  onSaveEdits: () => void
}) {
  // 出勤异常数以例外学生 ID 去重为准（考勤批次的未注明缺勤也计入）
  const attAnomalyCount = item.attendance_ids?.length ?? item.attendance_count
  return (
    <>
      <TableRow className="cursor-pointer" aria-expanded={expanded} onClick={onToggle}>
        <TableCell className="whitespace-nowrap text-sm">{item.assigned_date}</TableCell>
        <TableCell className="whitespace-nowrap text-sm">{item.subject}</TableCell>
        {showType ? (
          <TableCell className="whitespace-nowrap text-sm">{item.homework_type}</TableCell>
        ) : null}
        <TableCell className="whitespace-nowrap text-sm">
          <Badge variant={item.status === 'active' ? 'secondary' : 'outline'}>
            {assignmentStatusLabel(item.status)}
          </Badge>
        </TableCell>
        <NumberFilterCell
          count={item.submitted}
          filter="submitted"
          title={`点击筛选已交人员（${item.submitted} 人）`}
          active={expanded && activeFilter === 'submitted'}
          onClick={onNumberClick}
        />
        <NumberFilterCell
          count={item.missing}
          filter="missing"
          title={`点击筛选缺交人员（${item.missing} 人）`}
          active={expanded && activeFilter === 'missing'}
          onClick={onNumberClick}
        />
        <NumberFilterCell
          count={item.excused}
          filter="excused"
          title={`点击筛选请假人员（${item.excused} 人）`}
          active={expanded && activeFilter === 'excused'}
          onClick={onNumberClick}
        />
        <NumberFilterCell
          count={attAnomalyCount}
          filter="attendance"
          title={`点击筛选出勤异常人员（${attAnomalyCount} 人）`}
          active={expanded && activeFilter === 'attendance'}
          onClick={onNumberClick}
        />
        <NumberFilterCell
          count={item.negative_count}
          filter="negative"
          title={`点击筛选负面评价人员（${item.negative_count} 人）`}
          active={expanded && activeFilter === 'negative'}
          onClick={onNumberClick}
        />
        <TableCell className="print:hidden" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center gap-1">
            {item.status === 'active' ? (
              <>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label="编辑批次信息"
                  title="编辑批次信息（修改种类、日期等）"
                  onClick={onEdit}
                >
                  <Pencil className="h-4 w-4" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label="撤销批次"
                  title="撤销批次"
                  disabled={revoking}
                  onClick={onRevoke}
                >
                  <Undo2 className="h-4 w-4" />
                </Button>
              </>
            ) : (
              <span className="text-xs text-slate-400">已撤销</span>
            )}
            <button
              type="button"
              className="p-1 rounded hover:bg-slate-100 cursor-pointer"
              aria-label={expanded ? "收起逐人明细" : "展开逐人明细"}
              title={expanded ? "收起逐人明细" : "展开逐人明细"}
              onClick={onToggle}
            >
              <ChevronDown
                className={cn('h-4 w-4 text-slate-400 transition-transform', expanded && 'rotate-180')}
              />
            </button>
          </div>
        </TableCell>
      </TableRow>
      {expanded ? (
        <TableRow className="hover:bg-transparent">
          <TableCell colSpan={showType ? 10 : 9} className="bg-slate-50/70 p-4">
            <AssignmentDetailView
              item={item}
              detail={detail}
              detailError={detailError}
              showType={showType}
              activeFilter={activeFilter}
              onFilterChange={onFilterChange}
              edits={edits}
              setEdit={setEdit}
              patching={patching}
              patchError={patchError}
              onSaveEdits={onSaveEdits}
              onEditAssignment={onEdit ? () => onEdit() : undefined}
            />
          </TableCell>
        </TableRow>
      ) : null}
    </>
  )
}

