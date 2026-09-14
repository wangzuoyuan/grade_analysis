'use client'

/**
 * 作业录入面板（契约 p5-homework.md §1，两段式 preview → confirm）。
 *
 * 约束：
 * - 教学工作台 subject 固定任教学科（只读，来自工作台作用域）；班主任工作台可自由输入学科；
 * - 三模式（full=全交+例外 / names=名单 / detailed=逐行明细）切换即清空对应输入行，
 *   不同模式的行结构不同，混带必出错；
 * - preview 零写入；响应里的 existing_batches（同日同科同种类既有批次）必须醒目提示
 *   「编辑既有或新建」，绝不静默叠加（H02）；
 * - confirm 409（token 单次消费/成员漂移/范围变化）→ 中文提示 + 重新预览，
 *   绝不拿旧 token 重试；preview 422 同名歧义 → 列候选引导用学号消歧；
 * - 未提交表单走 page-draft 草稿（键按工作台分路由），confirm 成功后清除。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertCircle, CheckCircle2, ListPlus, Plus, RefreshCw, Trash2 } from 'lucide-react'

import {
  ApiV1Error,
  homeworkConfirm,
  homeworkPreview,
  readHomeworkAmbiguityCandidates,
  type HomeworkAmbiguityCandidate,
  type HomeworkConfirmResponse,
  type HomeworkInputKind,
  type HomeworkInputSpec,
  type HomeworkPreviewResponse,
  type HomeworkRowInput,
  type HomeworkStatus,
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
import { clearPageDraft, loadPageDraft, savePageDraft } from '@/lib/page-draft'
import { cn } from '@/lib/utils'
import { HOMEWORK_STATUS_OPTIONS, homeworkStatusLabel } from './shared'

const DRAFT_KEY = 'entry'

/** 未提交录入表单（草稿形状；loadPageDraft 按此收窄，损坏/不符当作无草稿）。 */
interface EntryFormState {
  subject: string
  homeworkType: string
  assignedDate: string
  dueDate: string
  kind: HomeworkInputKind
  /** names 模式：每行一个姓名/学号。 */
  namesText: string
  /** full 模式例外行。 */
  exceptions: HomeworkRowInput[]
  /** detailed 模式逐人行。 */
  rows: HomeworkRowInput[]
}

const EMPTY_FORM: EntryFormState = {
  subject: '',
  homeworkType: '',
  assignedDate: '',
  dueDate: '',
  kind: 'full',
  namesText: '',
  exceptions: [],
  rows: [],
}

function isRowInput(v: unknown): v is HomeworkRowInput {
  if (typeof v !== 'object' || v === null) return false
  return typeof (v as Record<string, unknown>).status === 'string'
}

function isEntryForm(v: unknown): v is EntryFormState {
  if (typeof v !== 'object' || v === null) return false
  const o = v as Record<string, unknown>
  if (typeof o.subject !== 'string' || typeof o.homeworkType !== 'string') return false
  if (typeof o.assignedDate !== 'string' || typeof o.dueDate !== 'string') return false
  if (o.kind !== 'full' && o.kind !== 'names' && o.kind !== 'detailed') return false
  if (typeof o.namesText !== 'string') return false
  if (!Array.isArray(o.exceptions) || !o.exceptions.every(isRowInput)) return false
  if (!Array.isArray(o.rows) || !o.rows.every(isRowInput)) return false
  return true
}

/** names 文本 → 每行一个姓名/学号（空行丢弃）。 */
function parseNames(text: string): string[] {
  return text
    .split('\n')
    .map((s) => s.trim())
    .filter((s) => s !== '')
}

/** 把表单组装成 preview 的 input 规格：空引用行丢弃，评价空串归 null。 */
function buildInput(form: EntryFormState): HomeworkInputSpec {
  if (form.kind === 'full') {
    return {
      kind: 'full',
      exceptions: form.exceptions
        .filter((r) => (r.name_or_alias ?? '').trim() !== '' || r.person_id != null)
        .map((r) => ({
          name_or_alias: (r.name_or_alias ?? '').trim() || null,
          status: r.status,
          evaluation: (r.evaluation ?? '').trim() || null,
        })),
    }
  }
  if (form.kind === 'names') {
    return { kind: 'names', names: parseNames(form.namesText) }
  }
  return {
    kind: 'detailed',
    rows: form.rows
      .filter((r) => (r.name_or_alias ?? '').trim() !== '' || r.person_id != null)
      .map((r) => ({
        name_or_alias: (r.name_or_alias ?? '').trim() || null,
        status: r.status,
        evaluation: (r.evaluation ?? '').trim() || null,
      })),
  }
}

/** 状态选择（原生 select：行编辑器里逐行渲染，Radix Select 过重）。 */
function StatusSelect({
  value,
  onChange,
  label,
}: {
  value: string
  onChange: (v: HomeworkStatus) => void
  label: string
}) {
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value as HomeworkStatus)}
      className="h-9 rounded-md border border-slate-200 bg-white px-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {HOMEWORK_STATUS_OPTIONS.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  )
}

const KIND_OPTIONS: Array<{ value: HomeworkInputKind; label: string; hint: string }> = [
  { value: 'full', label: '全交台账', hint: '默认全员已交，只补记例外（请假/缺交/未记录）' },
  { value: 'names', label: '交名单', hint: '只列出的学生记为已交；其余不写行、不推断缺交' },
  { value: 'detailed', label: '逐行明细', hint: '逐人录入状态与评价，支持 unknown 显式记录' },
]

export function HomeworkEntryPanel({
  mode,
  scopeSubject,
  teachingClassId,
  onConfirmed,
}: {
  mode: WorkspaceMode
  /** 教学工作台的任教学科（作用域解析）；班主任工作台传 null。 */
  scopeSubject: string | null
  /**
   * 教学工作台显式选择的教学班 id（写入路径绝不往"全部所教班并集"写批次）：
   * 未显式选择时不传，后端单班自动命中、多班 422 引导先选班。
   */
  teachingClassId?: number
  /** confirm 成功后通知父容器刷新批次列表等资源。 */
  onConfirmed: () => void
}) {
  const teaching = mode === 'teaching'
  // 草稿键按工作台分路由：两工作台页共用本组件，绝不能互串草稿
  const draftRoute = `/${mode}/homework`

  const [form, setForm] = useState<EntryFormState>(EMPTY_FORM)
  const [preview, setPreview] = useState<HomeworkPreviewResponse | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [candidates, setCandidates] = useState<HomeworkAmbiguityCandidate[] | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [confirmResult, setConfirmResult] = useState<HomeworkConfirmResponse | null>(null)
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)

  const restoredRef = useRef(false)
  useEffect(() => {
    if (restoredRef.current) return
    restoredRef.current = true
    const d = loadPageDraft<EntryFormState>(draftRoute, DRAFT_KEY, isEntryForm)
    if (d) {
      // 教学域 subject 固定任教学科：恢复草稿时以当前作用域为准，不复活旧值
      setForm(teaching ? { ...d, subject: scopeSubject ?? d.subject } : d)
    }
  }, [draftRoute, teaching, scopeSubject])

  /** 任何表单修改即时落草稿（sessionStorage，尽力而为）。 */
  const patchForm = useCallback(
    (patch: Partial<EntryFormState>) => {
      setForm((prev) => {
        const next = { ...prev, ...patch }
        savePageDraft<EntryFormState>(draftRoute, DRAFT_KEY, next)
        return next
      })
    },
    [draftRoute],
  )

  function switchKind(kind: HomeworkInputKind) {
    if (kind === form.kind) return
    // 模式切换清空输入行（任务书 §录入面板）；旧预览不再代表当前输入，一并作废
    patchForm({ kind, exceptions: [], rows: [], namesText: '' })
    setPreview(null)
    setPreviewError(null)
    setCandidates(null)
    setConfirmResult(null)
  }

  /** 行编辑统一入口：index<0 表示追加新行。 */
  function updateRow(list: 'exceptions' | 'rows', index: number, patch: Partial<HomeworkRowInput>) {
    setForm((prev) => {
      const arr = list === 'exceptions' ? prev.exceptions : prev.rows
      const next =
        index < 0
          ? [...arr, { name_or_alias: '', status: 'missing' as HomeworkStatus, evaluation: '', ...patch }]
          : arr.map((r, i) => (i === index ? { ...r, ...patch } : r))
      const nextState: EntryFormState = { ...prev, [list]: next }
      savePageDraft<EntryFormState>(draftRoute, DRAFT_KEY, nextState)
      return nextState
    })
  }

  function removeRow(list: 'exceptions' | 'rows', index: number) {
    setForm((prev) => {
      const arr = (list === 'exceptions' ? prev.exceptions : prev.rows).filter((_, i) => i !== index)
      const nextState: EntryFormState = { ...prev, [list]: arr }
      savePageDraft<EntryFormState>(draftRoute, DRAFT_KEY, nextState)
      return nextState
    })
  }

  const effectiveSubject = teaching ? (scopeSubject ?? '') : form.subject.trim()
  const canPreview = effectiveSubject !== '' && form.homeworkType.trim() !== '' && form.assignedDate !== ''

  async function runPreview() {
    if (!canPreview) {
      setPreviewError('请先填写学科、作业种类与布置日期')
      return
    }
    // names/detailed 空输入前端先行拦截（后端同样 422，这里给更直接的引导）
    if (form.kind === 'names' && parseNames(form.namesText).length === 0) {
      setPreviewError('请填写至少一名学生（每行一个姓名或学号）')
      return
    }
    if (
      form.kind === 'detailed' &&
      !form.rows.some((r) => (r.name_or_alias ?? '').trim() !== '' || r.person_id != null)
    ) {
      setPreviewError('请至少添加一行明细')
      return
    }
    setPreviewing(true)
    setPreviewError(null)
    setCandidates(null)
    setConfirmResult(null)
    try {
      const resp = await homeworkPreview({
        mode,
        // 教学域显式班选择优先；未选班时交由后端单班自动命中 / 多班 422 引导
        ...(teaching && typeof teachingClassId === 'number' ? { teaching_class_id: teachingClassId } : {}),
        subject: effectiveSubject,
        homework_type: form.homeworkType.trim(),
        assigned_date: form.assignedDate,
        due_date: form.dueDate !== '' ? form.dueDate : undefined,
        input: buildInput(form),
      })
      setPreview(resp)
    } catch (err) {
      if (err instanceof ApiV1Error && err.body?.param === 'teaching_class_id') {
        // 多班 422 的通用文案不含引导：指明先在工作台顶栏选具体班
        setPreviewError('您教多个教学班，请先在工作台顶部选择具体班级后再录入')
      } else {
        setCandidates(readHomeworkAmbiguityCandidates(err))
        setPreviewError(apiErrorMessage(err))
      }
    } finally {
      setPreviewing(false)
    }
  }

  async function runConfirm() {
    if (preview == null) return
    setConfirming(true)
    setConfirmError(null)
    try {
      const result = await homeworkConfirm(preview.token)
      setConfirmResult(result)
      setPreview(null)
      clearPageDraft(draftRoute, DRAFT_KEY)
      setForm(EMPTY_FORM)
      onConfirmed()
    } catch (err) {
      // 409 = token 已消费/成员漂移/范围变化（R4 语义）：旧 token 绝不重试，引导重新预览
      setConfirmError(
        err instanceof ApiV1Error && err.status === 409
          ? '预览已过期或班级成员已变化，请重新预览后再确认（原 token 已作废）'
          : apiErrorMessage(err),
      )
    } finally {
      setConfirming(false)
    }
  }

  return (
    <div className="space-y-4">
      <Card className="print:hidden">
        <CardHeader>
          <CardTitle>作业录入</CardTitle>
          <CardDescription>
            两段式：先预览解析（零写入），确认后单事务入库；同批次重复确认不新增（幂等）。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-500" htmlFor="hw-subject">
                学科{teaching ? '（任教学科，固定）' : ''}
              </label>
              <Input
                id="hw-subject"
                value={effectiveSubject}
                onChange={(e) => patchForm({ subject: e.target.value })}
                disabled={teaching}
                placeholder={teaching ? '作用域解析中…' : '如 物理'}
              />
              {teaching ? (
                <p className="text-[10px] text-slate-400">教学工作台只能录入任教学科作业</p>
              ) : null}
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-500" htmlFor="hw-type">
                作业种类
              </label>
              <Input
                id="hw-type"
                value={form.homeworkType}
                onChange={(e) => patchForm({ homeworkType: e.target.value })}
                placeholder="如 家庭作业 / 练习册 / 默写"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-500" htmlFor="hw-assigned">
                布置日期
              </label>
              <Input
                id="hw-assigned"
                type="date"
                value={form.assignedDate}
                onChange={(e) => patchForm({ assignedDate: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-500" htmlFor="hw-due">
                截止日期（可选）
              </label>
              <Input
                id="hw-due"
                type="date"
                value={form.dueDate}
                onChange={(e) => patchForm({ dueDate: e.target.value })}
              />
            </div>
          </div>

          {/* 录入模式三选：切换即清空对应输入行 */}
          <div className="space-y-2">
            <div role="group" aria-label="录入模式" className="flex flex-wrap gap-2">
              {KIND_OPTIONS.map((o) => (
                <button
                  key={o.value}
                  type="button"
                  aria-pressed={form.kind === o.value}
                  onClick={() => switchKind(o.value)}
                  className={cn(
                    'rounded-md border px-3 py-1.5 text-sm font-medium transition-colors',
                    form.kind === o.value
                      ? 'border-brand-200 bg-brand-50 text-brand-700'
                      : 'border-slate-200 bg-white text-slate-500 hover:bg-slate-50',
                  )}
                >
                  {o.label}
                </button>
              ))}
            </div>
            <p className="text-xs text-slate-400">
              {KIND_OPTIONS.find((o) => o.value === form.kind)?.hint}
            </p>
          </div>

          {form.kind === 'full' ? (
            <div className="space-y-2">
              <p className="text-xs font-medium text-slate-500">
                例外行（默认全员已交；仅在此列出请假/缺交/未记录的人）
              </p>
              {form.exceptions.map((row, i) => (
                <div key={i} className="flex flex-col gap-2 sm:flex-row">
                  <Input
                    className="sm:w-44"
                    placeholder="姓名或学号"
                    value={row.name_or_alias ?? ''}
                    onChange={(e) => updateRow('exceptions', i, { name_or_alias: e.target.value })}
                    aria-label={`例外行 ${i + 1} 姓名/学号`}
                  />
                  <StatusSelect
                    label={`例外行 ${i + 1} 状态`}
                    value={row.status}
                    onChange={(v) => updateRow('exceptions', i, { status: v })}
                  />
                  <Input
                    className="sm:flex-1"
                    placeholder="评价（可选）"
                    value={row.evaluation ?? ''}
                    onChange={(e) => updateRow('exceptions', i, { evaluation: e.target.value })}
                    aria-label={`例外行 ${i + 1} 评价`}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="删除例外行"
                    onClick={() => removeRow('exceptions', i)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => updateRow('exceptions', -1, { status: 'missing' })}
              >
                <Plus className="h-4 w-4" /> 添加例外
              </Button>
            </div>
          ) : null}

          {form.kind === 'names' ? (
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-500" htmlFor="hw-names">
                已交名单（每行一个姓名或学号；学号优先消歧）
              </label>
              <textarea
                id="hw-names"
                rows={6}
                value={form.namesText}
                onChange={(e) => patchForm({ namesText: e.target.value })}
                placeholder={'张三\n138021\n李四'}
                className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              <p className="text-[10px] text-slate-400">
                仅名单内的人记为已交；名单外不写行、不推断缺交（无记录 ≠ 已交）。
              </p>
            </div>
          ) : null}

          {form.kind === 'detailed' ? (
            <div className="space-y-2">
              <p className="text-xs font-medium text-slate-500">逐行明细（同班同名请用学号消歧）</p>
              {form.rows.map((row, i) => (
                <div key={i} className="flex flex-col gap-2 sm:flex-row">
                  <Input
                    className="sm:w-44"
                    placeholder="姓名或学号"
                    value={row.name_or_alias ?? ''}
                    onChange={(e) => updateRow('rows', i, { name_or_alias: e.target.value })}
                    aria-label={`明细行 ${i + 1} 姓名/学号`}
                  />
                  <StatusSelect
                    label={`明细行 ${i + 1} 状态`}
                    value={row.status}
                    onChange={(v) => updateRow('rows', i, { status: v })}
                  />
                  <Input
                    className="sm:flex-1"
                    placeholder="评价（可选）"
                    value={row.evaluation ?? ''}
                    onChange={(e) => updateRow('rows', i, { evaluation: e.target.value })}
                    aria-label={`明细行 ${i + 1} 评价`}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="删除明细行"
                    onClick={() => removeRow('rows', i)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
              <Button type="button" variant="outline" size="sm" onClick={() => updateRow('rows', -1, {})}>
                <Plus className="h-4 w-4" /> 添加一行
              </Button>
            </div>
          ) : null}

          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" onClick={runPreview} disabled={previewing || !canPreview}>
              {previewing ? '解析中…' : '预览解析'}
            </Button>
            {previewError ? (
              <Button type="button" variant="outline" size="sm" onClick={runPreview} disabled={previewing}>
                <RefreshCw className="h-4 w-4" /> 重新预览
              </Button>
            ) : null}
          </div>

          {previewError ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
              <p className="flex items-start gap-2 text-sm text-amber-800">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                {previewError}
              </p>
              {candidates != null ? (
                <div className="mt-2 space-y-1 pl-6">
                  <p className="text-xs font-medium text-amber-800">
                    同班存在多名同名学生，请改用学号（或让班主任提供人员编号）后重试：
                  </p>
                  <ul className="list-disc pl-4 text-xs text-amber-700">
                    {candidates.map((c) => (
                      <li key={String(c.person_id)}>
                        {c.name ?? '（未命名）'}
                        {c.alias ? ` · 学号 ${c.alias}` : ''} · person_id {String(c.person_id)}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}
        </CardContent>
      </Card>

      {/* 预览卡（零写入解析结果）：确认按钮在此，既有批次提示醒目呈现 */}
      {preview != null ? (
        <Card>
          <CardHeader>
            <CardTitle>预览确认</CardTitle>
            <CardDescription>
              {preview.assignment.subject} · {preview.assignment.homework_type} ·{' '}
              {preview.assignment.assigned_date}
              {preview.assignment.due_date ? `（截止 ${preview.assignment.due_date}）` : ''} · 应交{' '}
              {String(preview.assignment.expected_members.length)} 人（确认时冻结为分母）
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {preview.existing_batches.length > 0 ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                <p className="flex items-start gap-2 font-medium">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  同日同科同种类已有 {String(preview.existing_batches.length)} 个批次
                  （{preview.existing_batches
                    .map((b) => `#${String(b.assignment_id)} rev${String(b.revision)}`)
                    .join('、')}
                  ）
                </p>
                <p className="mt-1 pl-6 text-xs">
                  可到「批次列表」编辑既有批次；此时确认将新建独立批次——同日多份作业绝不自动叠加。
                </p>
              </div>
            ) : null}

            {preview.assignment.warnings.map((w, i) => (
              <div
                key={i}
                className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
              >
                <ListPlus className="mt-0.5 h-4 w-4 shrink-0" />
                {w}
              </div>
            ))}

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
                  {preview.assignment.submissions.map((s) => (
                    <TableRow key={String(s.person_id)}>
                      <TableCell className="whitespace-nowrap text-sm">{s.name ?? '（未命名）'}</TableCell>
                      <TableCell className="whitespace-nowrap text-sm">
                        <Badge
                          variant={
                            s.status === 'submitted'
                              ? 'success'
                              : s.status === 'missing'
                                ? 'destructive'
                                : 'outline'
                          }
                        >
                          {homeworkStatusLabel(s.status)}
                        </Badge>
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-sm text-slate-500">
                        {s.evaluation ?? '—'}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Button type="button" onClick={runConfirm} disabled={confirming}>
                {confirming ? '入库中…' : '确认入库'}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={runPreview}
                disabled={previewing}
              >
                <RefreshCw className="h-4 w-4" /> 重新预览
              </Button>
            </div>

            {confirmError ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                <p className="flex items-start gap-2">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  {confirmError}
                </p>
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : previewing ? (
        <Card>
          <CardContent className="py-6">
            <Skeleton className="h-24 w-full" />
          </CardContent>
        </Card>
      ) : null}

      {confirmResult != null ? (
        <Card className="border-success-100">
          <CardContent className="flex items-start gap-3 py-5">
            <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-success-500" />
            <div>
              <p className="text-sm font-medium text-slate-900">
                批次 #{String(confirmResult.assignment_id)} 已入库（revision {String(confirmResult.revision)}）
              </p>
              <p className="mt-1 text-sm text-slate-600">
                已交 {String(confirmResult.submitted)} · 缺交 {String(confirmResult.missing)} · 请假{' '}
                {String(confirmResult.excused)} · 未记录 {String(confirmResult.unknown)}
              </p>
              <p className="mt-1 text-[10px] text-slate-400">
                应交分母为确认时的成员快照（非当天人数）；同一批次重复确认不会新增数据。
              </p>
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}
