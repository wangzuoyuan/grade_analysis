'use client'

/**
 * 批次列表 + 看板（契约 p5-homework.md §1.3/§2）。
 *
 * 约束：
 * - rate_unavailable=true 的行显示「无法计算（无可靠分母）」，绝不显示百分比（H03：
 *   仅缺交历史的批次不推断其余全交）；
 * - 行内编辑走 PATCH revision 乐观锁：409 时重新拉取详情拿最新 revision（绝不拿旧
 *   revision 死重试）；撤销前 window.confirm，409 冲突清单（该批次有后续评价编辑）
 *   原样展示，绝不静默撤销；
 * - 请求序号按资源分离（F11）：列表 / 看板 / 行详情互不作废——共用一个序号时，
 *   任一资源重拉会把其他资源的合法回包当过期丢弃。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertCircle, ChevronDown, ClipboardList, Pencil, RefreshCw, Undo2 } from 'lucide-react'

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
import {
  HOMEWORK_STATUS_OPTIONS,
  assignmentStatusLabel,
  formatSubmissionRate,
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
}

export function AssignmentTable({
  mode,
  scopeQ,
  generation,
  refreshKey,
}: {
  mode: WorkspaceMode
  scopeQ: HomeworkScopeQuery
  /** 工作台世代号（切换/筛选变化即作废在途回包）。 */
  generation: number
  /** 父容器触发的刷新信号（如录入确认成功）。 */
  refreshKey: number
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

  // 按期看板（week/month）
  const [groupBy, setGroupBy] = useState<'week' | 'month'>('month')
  const [dash, setDash] = useState<HomeworkDashboardResponse | null>(null)
  const [dashError, setDashError] = useState<string | null>(null)
  const dashReqRef = useRef(0)

  // 行展开详情（一次只展开一个批次）
  const [expandedId, setExpandedId] = useState<number | null>(null)
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

  const reload = useCallback(() => {
    setListNonce((n) => n + 1)
  }, [])

  // 批次列表（请求序号只比对 listReqRef）
  useEffect(() => {
    const req = ++listReqRef.current
    setItems(null)
    setListError(null)
    homeworkAssignmentsList(mode, {
      ...scopeQ,
      subject: applied.subject !== '' ? applied.subject : undefined,
      homework_type: applied.homeworkType !== '' ? applied.homeworkType : undefined,
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
  }, [mode, scopeQ, generation, listNonce, applied])

  // 按期看板（请求序号只比对 dashReqRef，绝不作废列表回包）
  useEffect(() => {
    const req = ++dashReqRef.current
    setDash(null)
    setDashError(null)
    homeworkDashboard(
      mode,
      {
        ...scopeQ,
        subject: applied.subject !== '' ? applied.subject : undefined,
        homework_type: applied.homeworkType !== '' ? applied.homeworkType : undefined,
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
  }, [mode, scopeQ, generation, listNonce, applied, groupBy])

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
    // 只提交真正变化的行：未编辑行的 status/evaluation 以详情原值为准，绝不默认改状态
    const changed = detail.submissions
      .map((s) => {
        const edit = edits[String(s.person_id)]
        const evaluation = edit?.evaluation ?? s.evaluation ?? ''
        return {
          person_id: s.person_id as PersonId,
          status: edit?.status ?? (s.status as HomeworkStatus),
          evaluation: evaluation === '' ? null : evaluation,
        }
      })
      .filter((row, i) => {
        const orig = detail.submissions[i]
        return row.status !== orig.status || (row.evaluation ?? null) !== (orig.evaluation ?? null)
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
        setPatchError('批次已被其他修改更新（版本冲突），已重新加载最新版本，请核对后重试')
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
        `确认撤销批次「${a.subject} ${a.homework_type} ${a.assigned_date}」？撤销后不再计入统计，且不可在此界面恢复。`,
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
        },
      }
    })
  }

  const appliedNote =
    applied.subject !== '' || applied.homeworkType !== '' || applied.fromDate !== '' || applied.toDate !== ''

  return (
    <div className="space-y-4">
      {/* 过滤卡（筛选控件不参与打印） */}
      <Card className="print:hidden">
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
                setExpandedId(null)
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
                setExpandedId(null)
              }}
            >
              重置
            </Button>
            <Button type="button" variant="outline" size="sm" onClick={reload}>
              <RefreshCw className="h-4 w-4" /> 刷新
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 撤销 409 冲突清单：原样展示，提示先核对再处理 */}
      {revokeConflicts != null ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-800">
          <p className="flex items-start gap-2 font-medium">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            {revokeError}
          </p>
          <ul className="mt-2 list-disc space-y-0.5 pl-9 text-xs">
            {revokeConflicts.map((c) => (
              <li key={String(c.person_id)}>
                {c.name ?? '（未命名）'}（person_id {String(c.person_id)}）· 状态{' '}
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
      ) : revokeError != null ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {revokeError}
        </div>
      ) : null}

      {/* 按期看板：仅计有可靠分母的批次；组内全部无分母 → 无法计算 */}
      <Card>
        <CardHeader className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <CardTitle>按期汇总</CardTitle>
            <CardDescription>
              日维度聚合口径（basis={dash?.basis ?? 'day'}）；提交率只统计有可靠分母的批次，
              分母为应交快照减请假。
            </CardDescription>
          </div>
          <div className="flex gap-2 print:hidden">
            <Button
              type="button"
              size="sm"
              variant={groupBy === 'month' ? 'secondary' : 'outline'}
              onClick={() => setGroupBy('month')}
              aria-pressed={groupBy === 'month'}
            >
              按月
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
                    <TableHead className="text-xs">{groupBy === 'week' ? '周（周一）' : '月份'}</TableHead>
                    <TableHead className="text-right text-xs">批次</TableHead>
                    <TableHead className="text-right text-xs">已交</TableHead>
                    <TableHead className="text-right text-xs">缺交</TableHead>
                    <TableHead className="text-right text-xs">请假</TableHead>
                    <TableHead className="text-right text-xs">未记录</TableHead>
                    <TableHead className="text-right text-xs">提交率</TableHead>
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
                      <TableCell className="text-right text-sm tabular-nums">{String(g.unknown)}</TableCell>
                      <TableCell
                        className={cn(
                          'whitespace-nowrap text-right text-sm',
                          g.rate_unavailable ? 'text-slate-400' : 'tabular-nums',
                        )}
                      >
                        {formatSubmissionRate(g.submission_rate, g.rate_unavailable)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 批次列表 */}
      <Card>
        <CardHeader>
          <CardTitle>作业批次</CardTitle>
          <CardDescription>
            {appliedNote ? '按当前筛选' : '全部'}共 {String(total)} 个批次（布置日期降序，含跨域共享投影）；
            点击「编辑」展开逐人明细。提交率分母 = 应交快照 − 请假，快照为空时显示无法计算。
          </CardDescription>
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
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-xs">布置日期</TableHead>
                    <TableHead className="text-xs">学科</TableHead>
                    <TableHead className="text-xs">种类</TableHead>
                    <TableHead className="text-xs">状态</TableHead>
                    <TableHead className="text-xs">版本</TableHead>
                    <TableHead className="text-right text-xs">应交</TableHead>
                    <TableHead className="text-right text-xs">已交</TableHead>
                    <TableHead className="text-right text-xs">缺交</TableHead>
                    <TableHead className="text-right text-xs">请假</TableHead>
                    <TableHead className="text-right text-xs">未记录</TableHead>
                    <TableHead className="text-right text-xs">提交率</TableHead>
                    <TableHead className="print:hidden" aria-label="操作" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((a) => (
                    <FragmentRow
                      key={a.assignment_id}
                      item={a}
                      expanded={expandedId === a.assignment_id}
                      onToggle={() => setExpandedId(expandedId === a.assignment_id ? null : a.assignment_id)}
                      onRevoke={() => revoke(a)}
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
      </Card>
    </div>
  )
}

/** 表格一行 + 展开编辑区（Fragment 结构沿用成绩页行展开模式）。 */
function FragmentRow({
  item,
  expanded,
  onToggle,
  onRevoke,
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
  expanded: boolean
  onToggle: () => void
  onRevoke: () => void
  revoking: boolean
  detail: HomeworkAssignmentDetail | null
  detailError: string | null
  edits: Record<string, RowEdit>
  setEdit: (personId: PersonId, patch: Partial<RowEdit>, fallbackStatus: HomeworkStatus) => void
  patching: boolean
  patchError: string | null
  onSaveEdits: () => void
}) {
  return (
    <>
      <TableRow className="cursor-pointer" aria-expanded={expanded} onClick={onToggle}>
        <TableCell className="whitespace-nowrap text-sm">{item.assigned_date}</TableCell>
        <TableCell className="whitespace-nowrap text-sm">{item.subject}</TableCell>
        <TableCell className="whitespace-nowrap text-sm">{item.homework_type}</TableCell>
        <TableCell className="whitespace-nowrap text-sm">
          <Badge variant={item.status === 'active' ? 'secondary' : 'outline'}>
            {assignmentStatusLabel(item.status)}
          </Badge>
        </TableCell>
        <TableCell className="whitespace-nowrap text-xs text-slate-500">rev{String(item.revision)}</TableCell>
        <TableCell className="text-right text-sm tabular-nums">{String(item.expected_count)}</TableCell>
        <TableCell className="text-right text-sm tabular-nums">{String(item.submitted)}</TableCell>
        <TableCell className="text-right text-sm tabular-nums">{String(item.missing)}</TableCell>
        <TableCell className="text-right text-sm tabular-nums">{String(item.excused)}</TableCell>
        <TableCell className="text-right text-sm tabular-nums">{String(item.unknown)}</TableCell>
        <TableCell
          className={cn(
            'whitespace-nowrap text-right text-sm font-medium',
            item.rate_unavailable ? 'text-slate-400' : 'tabular-nums text-slate-900',
          )}
        >
          {/* H03 红线：分母不可用时绝不显示百分比 */}
          {formatSubmissionRate(item.submission_rate, item.rate_unavailable)}
        </TableCell>
        <TableCell className="print:hidden" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center gap-1">
            {item.status === 'active' ? (
              <>
                <Button type="button" variant="ghost" size="icon" aria-label="编辑批次" onClick={onToggle}>
                  <Pencil className="h-4 w-4" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label="撤销批次"
                  disabled={revoking}
                  onClick={onRevoke}
                >
                  <Undo2 className="h-4 w-4" />
                </Button>
              </>
            ) : (
              <span className="text-xs text-slate-400">已撤销</span>
            )}
            <ChevronDown
              className={cn('h-4 w-4 text-slate-400 transition-transform', expanded && 'rotate-180')}
            />
          </div>
        </TableCell>
      </TableRow>
      {expanded ? (
        <TableRow className="hover:bg-transparent">
          <TableCell colSpan={12} className="bg-slate-50/70 p-4">
            {detailError ? (
              <p className="text-sm text-slate-600">{detailError}</p>
            ) : detail == null ? (
              <div className="space-y-2">
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-2/3" />
              </div>
            ) : (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
                  <span className="font-medium text-slate-700">
                    {detail.subject} · {detail.homework_type} · {detail.assigned_date}
                  </span>
                  <Badge variant="outline">rev{String(detail.revision)}</Badge>
                  <span className="text-xs text-slate-400">
                    应交 {String(detail.expected_count)} 人 · 修改保存后版本号自动 +1（乐观锁）
                  </span>
                </div>
                {patchError ? (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                    {patchError}
                  </div>
                ) : null}
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="text-xs">学生</TableHead>
                        <TableHead className="text-xs">状态</TableHead>
                        <TableHead className="text-xs">评价</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {detail.submissions.map((s) => {
                        const edit = edits[String(s.person_id)]
                        const currentStatus = edit?.status ?? (s.status as HomeworkStatus)
                        const currentEvaluation = edit?.evaluation ?? s.evaluation ?? ''
                        const dirty =
                          currentStatus !== s.status || currentEvaluation !== (s.evaluation ?? '')
                        return (
                          <TableRow key={String(s.person_id)} className={dirty ? 'bg-brand-50/50' : undefined}>
                            <TableCell className="whitespace-nowrap text-sm">
                              {s.name ?? '（未命名）'}
                            </TableCell>
                            <TableCell>
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
                            </TableCell>
                            <TableCell>
                              <Input
                                className="h-8 w-40 text-sm"
                                value={currentEvaluation}
                                placeholder="评价（可选）"
                                onChange={(e) => setEdit(s.person_id, { evaluation: e.target.value }, s.status as HomeworkStatus)}
                                aria-label={`${String(s.name ?? s.person_id)} 评价`}
                              />
                            </TableCell>
                          </TableRow>
                        )
                      })}
                    </TableBody>
                  </Table>
                </div>
                <Button type="button" size="sm" onClick={onSaveEdits} disabled={patching}>
                  {patching ? '保存中…' : '保存修改'}
                </Button>
              </div>
            )}
          </TableCell>
        </TableRow>
      ) : null}
    </>
  )
}
