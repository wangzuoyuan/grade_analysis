'use client'

/**
 * 换届向导（契约 docs/contracts/p4-students.md §2.2/§6，I01）。
 * 双域共用：domain='homeroom' 行政班换届；domain='teaching' 教学班换届
 * （预览逐人带 class_label，确认一次升入多个教学班）。
 *
 * 流程：选来源学年 → rollover/preview（token，R4 语义：pending/未过期/成员无漂移）
 * → 逐人确认新学年学号（默认取后端建议，可改）→ rollover/confirm（单事务建新学年班级 +
 * 每生学籍/成员 + 新学段 alias）→ 成功摘要；撤销入口按 token 快照回滚，
 * conflicted 学生（换届后已有新写入）单独列出「保留现状」，绝不静默覆盖。
 * token 过期/成员漂移 409 → 中文错误 + 重新预览。确认成功的 token 暂存为页面草稿，
 * 撤销成功后清除。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle, ArrowRight, Loader2, RefreshCw, Undo2 } from 'lucide-react'

import {
  listAcademicYears,
  rolloverConfirm,
  rolloverPreview,
  rolloverUndo,
  teachingRolloverConfirm,
  teachingRolloverPreview,
  teachingRolloverUndo,
  type AcademicYear,
  type RolloverPreview,
  type RolloverUndoConflict,
} from '@/lib/api-v1'
import { apiErrorMessage, isApiErrorCode } from '@/components/link/error-text'
import { useWorkspace } from '@/lib/workspace'
import { clearPageDraft, loadPageDraft, savePageDraft } from '@/lib/page-draft'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

const DRAFT_KEY = 'token'

export type RolloverDomain = 'homeroom' | 'teaching'

/** 换届确认成功后的本地摘要（主要来自请求数据，不依赖未冻结的响应字段）。 */
interface RolloverSuccess {
  token: string
  toYearName: string
  confirmedCount: number
}

/** 撤销结果的本地摘要：conflicted 读响应宽容字段（形状未冻结）。 */
interface UndoOutcome {
  conflicted: RolloverUndoConflict[]
}

function readConflicted(result: { conflicted?: unknown }): RolloverUndoConflict[] {
  if (!Array.isArray(result.conflicted)) return []
  return result.conflicted.filter(
    (c): c is RolloverUndoConflict => typeof c === 'object' && c !== null,
  )
}

function isTokenDraft(v: unknown): v is string {
  return typeof v === 'string'
}

export function RolloverWizard({ domain = 'homeroom' }: { domain?: RolloverDomain } = {}) {
  const { filter, generation, switching } = useWorkspace()
  const draftRoute = domain === 'teaching' ? '/teaching/rollover' : '/homeroom/rollover'
  const previewFn = domain === 'teaching' ? teachingRolloverPreview : rolloverPreview
  const confirmFn = domain === 'teaching' ? teachingRolloverConfirm : rolloverConfirm
  const undoFn = domain === 'teaching' ? teachingRolloverUndo : rolloverUndo

  // 学年清单：换届来源候选（GET /shared/academic-years，start_date 降序）
  const [years, setYears] = useState<AcademicYear[] | null>(null)
  const [yearsError, setYearsError] = useState<string | null>(null)
  const [yearsNonce, setYearsNonce] = useState(0)
  const [selectedYear, setSelectedYear] = useState<number | null>(null)
  const yearsReqRef = useRef(0)

  // 预览 + 逐人学号草稿
  const [preview, setPreview] = useState<RolloverPreview | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [aliasDrafts, setAliasDrafts] = useState<Record<string, string>>({})
  const [confirming, setConfirming] = useState(false)
  const [confirmError, setConfirmError] = useState<string | null>(null)

  const [success, setSuccess] = useState<RolloverSuccess | null>(null)

  // 撤销入口（token 可手动粘贴，确认成功的 token 自动填入）
  const [undoToken, setUndoToken] = useState('')
  const [undoing, setUndoing] = useState(false)
  const [undoError, setUndoError] = useState<string | null>(null)
  const [undoOutcome, setUndoOutcome] = useState<UndoOutcome | null>(null)

  // 确认成功的 token 暂存草稿：中途切走再回来仍可撤销（提交撤销成功后清除）
  useEffect(() => {
    const saved = loadPageDraft<string>(draftRoute, DRAFT_KEY, isTokenDraft)
    if (saved != null) setUndoToken(saved)
  }, [draftRoute])

  const loadYears = useCallback(() => {
    const req = ++yearsReqRef.current
    setYears(null)
    setYearsError(null)
    listAcademicYears()
      .then((r) => {
        if (req !== yearsReqRef.current) return
        setYears(r.years ?? [])
      })
      .catch((err: unknown) => {
        if (req !== yearsReqRef.current) return
        setYears([])
        setYearsError(apiErrorMessage(err))
      })
  }, [])

  useEffect(() => {
    loadYears()
  }, [loadYears, generation, yearsNonce])

  // 默认选中来源学年：工作台筛选优先，否则取最新学年（列表按 start_date 降序）
  useEffect(() => {
    if (years == null || selectedYear != null) return
    if (typeof filter.academic_year_id === 'number' && years.some((y) => y.id === filter.academic_year_id)) {
      setSelectedYear(filter.academic_year_id)
    } else if (years.length > 0) {
      setSelectedYear(years[0].id)
    }
  }, [years, selectedYear, filter.academic_year_id])

  async function handlePreview() {
    if (selectedYear == null) return
    setPreviewing(true)
    setPreviewError(null)
    setConfirmError(null)
    setSuccess(null)
    try {
      const p = await previewFn(selectedYear)
      setPreview(p)
      // 逐人初始化新学年学号输入：后端建议（next_alias）优先，缺省回退当前学号
      const drafts: Record<string, string> = {}
      for (const s of p.students) {
        drafts[String(s.person_id)] = s.next_alias ?? s.current_alias ?? ''
      }
      setAliasDrafts(drafts)
    } catch (err) {
      setPreview(null)
      setPreviewError(
        domain !== 'teaching' && isApiErrorCode(err, 'workspace_not_configured')
          ? '还没有可升入的新学年：请先创建下一学年（学年管理）后再预览换届。'
          : apiErrorMessage(err),
      )
    } finally {
      setPreviewing(false)
    }
  }

  /** 重新预览：token 过期/漂移后的唯一出路（旧 token 一律拒绝）。 */
  function rePreview() {
    setPreview(null)
    setAliasDrafts({})
    setConfirmError(null)
    void handlePreview()
  }

  async function handleConfirm() {
    if (preview == null) return
    setConfirming(true)
    setConfirmError(null)
    try {
      const aliases: Record<string, string> = {}
      for (const s of preview.students) {
        const v = (aliasDrafts[String(s.person_id)] ?? '').trim()
        if (v !== '') aliases[String(s.person_id)] = v
      }
      await confirmFn({ token: preview.token, aliases })
      const outcome: RolloverSuccess = {
        token: preview.token,
        toYearName: preview.to_year?.name ?? '新学年',
        confirmedCount: Object.keys(aliases).length,
      }
      setSuccess(outcome)
      setPreview(null)
      setAliasDrafts({})
      setUndoToken(outcome.token)
      setUndoOutcome(null)
      savePageDraft<string>(draftRoute, DRAFT_KEY, outcome.token)
    } catch (err) {
      // token 过期/成员漂移/重复确认统一 409：提示重新预览，不静默重发
      setConfirmError(
        isApiErrorCode(err, 'link_version_conflict') || isApiErrorCode(err, 'resource_out_of_scope')
          ? '预览已过期或名册已变化，请重新预览后再确认'
          : apiErrorMessage(err),
      )
    } finally {
      setConfirming(false)
    }
  }

  async function handleUndo() {
    const token = undoToken.trim()
    if (token === '') return
    if (!window.confirm('确定要撤销本次换届吗？将删除本次新建的学籍与学号（已有新数据的学生会跳过并保留现状）。')) {
      return
    }
    setUndoing(true)
    setUndoError(null)
    setUndoOutcome(null)
    try {
      const r = await undoFn(token)
      setUndoOutcome({ conflicted: readConflicted(r) })
      clearPageDraft(draftRoute, DRAFT_KEY)
      setSuccess(null)
    } catch (err) {
      setUndoError(apiErrorMessage(err))
    } finally {
      setUndoing(false)
    }
  }

  const emptyAliasRows = useMemo(
    () =>
      (preview?.students ?? []).filter((s) => (aliasDrafts[String(s.person_id)] ?? '').trim() === '')
        .length,
    [preview, aliasDrafts],
  )

  const classCount = useMemo(
    () =>
      new Set(
        (preview?.students ?? [])
          .map((s) => s.class_label)
          .filter((label): label is string => typeof label === 'string' && label !== ''),
      ).size,
    [preview],
  )

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">换届</h1>
        <p className="mt-1 text-sm text-slate-500">
          {domain === 'teaching'
            ? `旧学年教学班整批升入新学年：建新班、转成员、换新学号，历史接续到同一人${switching ? ' · 正在切换…' : ''}`
            : `旧学年名册整批升入新学年：建新班、转学籍、换新学号，历史接续到同一人${switching ? ' · 正在切换…' : ''}`}
        </p>
      </div>

      {/* Step 1 · 选来源学年并预览 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Step 1 · 选择来源学年并预览</CardTitle>
          <CardDescription>
            预览只是映射模拟，不写入任何数据；确认前可逐人修改新学年学号。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {yearsError ? (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p role="alert" className="text-sm text-danger-500">
                {yearsError}
              </p>
              <Button variant="outline" size="sm" onClick={() => setYearsNonce((n) => n + 1)}>
                重试
              </Button>
            </div>
          ) : years == null ? (
            <Skeleton className="h-10 w-64" />
          ) : years.length === 0 ? (
            <p className="text-sm text-slate-500">还没有任何学年，请先创建学年。</p>
          ) : (
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <div className="w-64">
                <Select
                  value={selectedYear != null ? String(selectedYear) : undefined}
                  onValueChange={(v) => {
                    setSelectedYear(Number(v))
                    setPreview(null)
                    setSuccess(null)
                  }}
                >
                  <SelectTrigger aria-label="选择来源学年">
                    <SelectValue placeholder="选择来源学年" />
                  </SelectTrigger>
                  <SelectContent>
                    {years.map((y) => (
                      <SelectItem key={y.id} value={String(y.id)}>
                        {y.name} 学年
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button onClick={() => void handlePreview()} disabled={previewing || selectedYear == null}>
                {previewing ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  <ArrowRight className="mr-1 h-4 w-4" aria-hidden="true" />
                )}
                {previewing ? '预览生成中…' : '生成换届预览'}
              </Button>
            </div>
          )}

          {previewError ? (
            <div
              role="alert"
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-danger-300 bg-danger-50 p-3 text-sm text-danger-600"
            >
              <span className="flex items-start gap-2">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                {previewError}
              </span>
              <Button variant="outline" size="sm" onClick={() => void handlePreview()} disabled={previewing}>
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
                重新预览
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>

      {/* Step 2 · 逐人确认新学年学号 */}
      {preview != null ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              Step 2 · 确认升入 {preview.to_year?.name ?? '新学年'}
            </CardTitle>
            <CardDescription>
              来源 {preview.from_year?.name ?? '—'} 学年{' '}
              {domain === 'teaching' ? `${String(classCount)} 个教学班共 ${String(preview.students.length)} 人；` : `${String(preview.students.length)} 人；`}
              新学年学号默认取建议值（可逐人修改）。预览令牌过期或名册变化后需重新预览。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="whitespace-nowrap text-xs">姓名</TableHead>
                    {domain === 'teaching' ? (
                      <TableHead className="whitespace-nowrap text-xs">教学班</TableHead>
                    ) : null}
                    <TableHead className="whitespace-nowrap text-xs">当前学号</TableHead>
                    <TableHead className="whitespace-nowrap text-xs">新学年学号</TableHead>
                    <TableHead className="text-xs">备注</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {preview.students.map((s) => {
                    const key = String(s.person_id)
                    return (
                      <TableRow key={`${key}:${s.class_label ?? ''}`}>
                        <TableCell className="whitespace-nowrap text-sm font-medium text-slate-900">
                          {s.name ?? '（未命名）'}
                        </TableCell>
                        {domain === 'teaching' ? (
                          <TableCell className="whitespace-nowrap text-xs text-slate-600">
                            {s.class_label ?? '—'}
                          </TableCell>
                        ) : null}
                        <TableCell className="whitespace-nowrap font-mono text-xs text-slate-600">
                          {s.current_alias ?? '—'}
                        </TableCell>
                        <TableCell>
                          <Input
                            aria-label={`${s.name ?? ''} 的新学年学号`}
                            value={aliasDrafts[key] ?? ''}
                            onChange={(e) => setAliasDrafts({ ...aliasDrafts, [key]: e.target.value })}
                            className="h-8 w-40 font-mono"
                            maxLength={40}
                            disabled={confirming}
                          />
                        </TableCell>
                        <TableCell className="text-xs text-slate-500">{s.note ?? '—'}</TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </div>

            {confirmError ? (
              <div
                role="alert"
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-danger-300 bg-danger-50 p-3 text-sm text-danger-600"
              >
                <span className="flex items-start gap-2">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                  {confirmError}
                </span>
                <Button variant="outline" size="sm" onClick={rePreview} disabled={previewing}>
                  <RefreshCw className="h-4 w-4" aria-hidden="true" />
                  重新预览
                </Button>
              </div>
            ) : null}

            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-slate-400">
                {emptyAliasRows > 0
                  ? `${String(emptyAliasRows)} 人学号为空，确认时将跳过该部分学号写入。`
                  : domain === 'teaching'
                    ? '确认后单事务写入：新学年教学班 + 每成员有效期与新学号。'
                    : '确认后单事务写入：新学年班级 + 每生学籍与新学号。'}
              </p>
              <Button onClick={() => void handleConfirm()} disabled={confirming || preview.students.length === 0}>
                {confirming ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden="true" />
                ) : null}
                {confirming ? '换届确认中…' : '确认换届'}
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {/* 成功摘要 */}
      {success ? (
        <Card className="border-success-300 bg-success-50/40">
          <CardHeader>
            <CardTitle className="text-base">换届完成</CardTitle>
            <CardDescription>
              已升入 {success.toYearName}：确认学号 {String(success.confirmedCount)} 人。
              本次换届令牌：{success.token}
            </CardDescription>
          </CardHeader>
        </Card>
      ) : null}

      {/* 撤销入口：按 token 快照回滚；conflicted 单独展示「保留现状」 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">撤销换届</CardTitle>
          <CardDescription>
            按本次换届令牌快照回滚（删除本次新建的学籍/学号/班级行，恢复旧学号有效期）。
            换届后已产生新数据的学生会跳过并列出，保留现状，不覆盖后续合法数据。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <div className="space-y-1">
              <label htmlFor="undo-token" className="text-xs font-medium text-slate-500">
                换届令牌
              </label>
              <Input
                id="undo-token"
                value={undoToken}
                onChange={(e) => setUndoToken(e.target.value)}
                placeholder="确认换届成功后自动填入，也可手动粘贴"
                className="w-80 font-mono"
                disabled={undoing}
              />
            </div>
            <Button variant="outline" onClick={() => void handleUndo()} disabled={undoing || undoToken.trim() === ''}>
              {undoing ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Undo2 className="mr-1 h-4 w-4" aria-hidden="true" />
              )}
              {undoing ? '撤销中…' : '撤销本次换届'}
            </Button>
          </div>

          {undoError ? (
            <p role="alert" className="rounded-md border border-danger-300 bg-danger-50 p-2 text-sm text-danger-600">
              {undoError}
            </p>
          ) : null}

          {undoOutcome ? (
            <div className="space-y-2">
              <p role="status" className="text-sm text-success-600">
                撤销完成；以下学生已有新数据，已跳过撤销并保留现状。
              </p>
              {undoOutcome.conflicted.length === 0 ? (
                <p className="text-xs text-slate-400">本次撤销无冲突学生。</p>
              ) : (
                <div className="overflow-x-auto rounded-lg border border-warning-300 bg-warning-50/60">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="text-xs">姓名</TableHead>
                        <TableHead className="whitespace-nowrap text-xs">当前学号</TableHead>
                        <TableHead className="text-xs">处理</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {undoOutcome.conflicted.map((c, i) => (
                        <TableRow key={c.person_id != null ? String(c.person_id) : String(i)}>
                          <TableCell className="whitespace-nowrap text-sm">{c.name ?? '—'}</TableCell>
                          <TableCell className="whitespace-nowrap font-mono text-xs text-slate-600">
                            {c.current_alias ?? '—'}
                          </TableCell>
                          <TableCell className="text-xs text-warning-700">该生已有新数据，保留现状</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  )
}
