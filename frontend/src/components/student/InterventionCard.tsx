'use client'

/**
 * 干预卡（P2-C4，契约 docs/diagnosis-roadmap/p2-contracts.md §5）。
 *
 * 学生档案内的轻量干预管理：
 * - 创建：问题/学科/措施/目标指标/基线（自动捕获或手填）/开始日/复查日；
 *   同人同科已有未关闭干预时后端 409 duplicate_follow_up → 展示既有干预
 *   明细与「确认知情仍要创建」二次确认（防重复录入提示）；
 * - 查看：open/done/dismissed 三态、基线与目标指标；
 * - 复查对照：每条未关闭干预可展开「复查对照」——到期或 start_date 后有
 *   新可比考试时展示 {baseline, latest, change}；缺考/无可比 → pending
 *   +原因；对照只陈述数据事实，不判定成功/失败（后端保证，前端不演绎）；
 * - 关闭：done（跟进收口）/ dismissed（不再跟进），走 status 关闭路径。
 */

import { useCallback, useEffect, useState } from 'react'
import { CalendarCheck, ClipboardList, RefreshCw } from 'lucide-react'

import {
  ApiV1Error,
  createFollowUp,
  fetchReviewContrast,
  listStudentNotes,
  patchFollowUp,
  type BaselineValue,
  type NoteScopeQuery,
  type ReviewContrast,
  type StudentNote,
  type WorkspaceMode,
} from '@/lib/api-v1'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

/** 干预状态徽章样式与文案。 */
const STATUS_META: Record<string, { label: string; className: string }> = {
  open: { label: '进行中', className: 'bg-warning-50 text-warning-700' },
  done: { label: '已关闭', className: 'bg-success-50 text-success-600' },
  dismissed: { label: '不再跟进', className: 'bg-slate-100 text-slate-500' },
}

/** 目标指标快捷选项（与 definitions.metric_options 的 value 同名，后端唯一口径校验）。 */
const METRIC_OPTIONS = [
  { value: 'total:主三门', label: '主三门总分名次' },
  { value: 'total:3+3', label: '3+3 总分名次' },
  { value: 'subject:语文', label: '语文（年级前百分位）' },
  { value: 'subject:数学', label: '数学（年级前百分位）' },
  { value: 'subject:英语', label: '英语（年级前百分位）' },
  { value: 'subject:物理', label: '物理（年级前百分位）' },
  { value: 'subject:化学', label: '化学（年级前百分位）' },
  { value: 'subject:生物', label: '生物（年级前百分位）' },
  { value: 'subject_grade:物理', label: '物理等级分' },
  { value: 'subject_grade:化学', label: '化学等级分' },
  { value: 'subject_grade:生物', label: '生物等级分' },
  { value: 'subject_grade:政治', label: '政治等级分' },
  { value: 'subject_grade:历史', label: '历史等级分' },
  { value: 'subject_grade:地理', label: '地理等级分' },
]

const DASH = '—'

/** 指标单位（后端 unit_of_metric 同口径）：rank=名次、
 *  percentile=年级前百分位（存储 0–1 小数，输入/展示按百分数）、
 *  grade_score=等级分。 */
type MetricUnit = 'rank' | 'percentile' | 'grade_score'

function metricUnit(metric: string | null | undefined): MetricUnit {
  if (!metric) return 'rank'
  if (metric.startsWith('total:')) return 'rank'
  if (metric.startsWith('subject_grade:')) return 'grade_score'
  return 'percentile'
}

const BASELINE_PLACEHOLDER: Record<MetricUnit, string> = {
  percentile: '基线值（可选，按百分数填：40 = 年级前 40%；留空=自动取最近一场）',
  rank: '基线值（可选，名次，如 300；留空=自动取最近一场）',
  grade_score: '基线值（可选，等级分；留空=自动取最近一场）',
}

function todayStr(offsetDays = 0) {
  const d = new Date()
  d.setDate(d.getDate() + offsetDays)
  return d.toISOString().slice(0, 10)
}

function isIntervention(note: StudentNote): boolean {
  return note.status != null
}

function fmtNumber(v: number): string {
  return Number.isInteger(v) ? String(v) : v.toFixed(2)
}

/** 数值展示：percentile 按百分数（0.4 → 前 40%），其余原样。 */
function formatValue(v: number | null | undefined, unit?: string | null): string {
  if (v == null || !Number.isFinite(v)) return DASH
  if (unit === 'percentile') {
    const pct = v * 100
    return `前 ${Number.isInteger(pct) ? pct : pct.toFixed(1)}%`
  }
  return fmtNumber(v)
}

/** change 的方向措辞：后端已按口径给 smaller_is_better，前端只照读，不二次演绎；
 *  percentile 按百分点展示（存储 0–1 小数 × 100）。 */
function changeText(change: ReviewContrast['change'], unit?: string | null): string {
  if (!change) return DASH
  if (unit === 'percentile') {
    const pct = change.value * 100
    if (pct === 0) return '无变化'
    const sign = pct > 0 ? '+' : ''
    const value = `${sign}${Number.isInteger(pct) ? pct : pct.toFixed(1)} 个百分点`
    return pct < 0 ? `${value}（相对位置上升）` : `${value}（相对位置下降）`
  }
  const sign = change.value > 0 ? '+' : ''
  const value = `${sign}${fmtNumber(change.value)}`
  if (change.value === 0) return '无变化'
  if (change.smaller_is_better) {
    return change.value < 0 ? `${value}（相对位置上升）` : `${value}（相对位置下降）`
  }
  return change.value > 0 ? `${value}（数值升高）` : `${value}（数值降低）`
}

/** 单条干预的复查对照展示（展开时按需取数；scopeQ 与列表/创建同源透传，
 *  跨学年/跨班查询才不会丢作用域拿不到基线）。 */
function ContrastPanel({
  mode,
  noteId,
  scopeQ = {},
}: {
  mode: WorkspaceMode
  noteId: number
  scopeQ?: NoteScopeQuery
}) {
  const [contrast, setContrast] = useState<ReviewContrast | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    fetchReviewContrast(mode, noteId, scopeQ)
      .then((body) => {
        setContrast(body)
        setLoading(false)
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : '复查对照加载失败')
        setLoading(false)
      })
  }, [mode, noteId, scopeQ])

  useEffect(() => {
    load()
  }, [load])

  if (loading) {
    return (
      <p className="mt-2 flex items-center gap-1 text-xs text-slate-400">
        <RefreshCw className="h-3 w-3 animate-spin" /> 正在生成复查对照…
      </p>
    )
  }
  if (error) {
    return <p className="mt-2 text-xs text-danger-500">{error}</p>
  }
  if (!contrast) return null

  if (contrast.status === 'pending') {
    return (
      <div className="mt-2 rounded-md bg-slate-50 p-2 text-xs text-slate-500">
        <p>
          复查对照：<span className="font-medium text-warning-700">暂不可对照（pending）</span>
        </p>
        <p className="mt-1">{contrast.note}</p>
      </div>
    )
  }

  return (
    <div className="mt-2 space-y-1 rounded-md bg-brand-50/60 p-2 text-xs text-slate-600">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span>
          基线：
          <span className="font-medium text-slate-800">
            {formatValue(contrast.baseline?.value, contrast.unit)}
          </span>{' '}
          {contrast.baseline?.exam_name ? `（${contrast.baseline.exam_name}）` : null}
        </span>
        <span>
          最新：
          <span className="font-medium text-slate-800">
            {formatValue(contrast.latest?.value, contrast.unit)}
          </span>{' '}
          {contrast.latest?.exam_name ? `（${contrast.latest.exam_name}）` : null}
        </span>
        <span>
          变化：
          <span className="font-medium text-slate-800">
            {changeText(contrast.change, contrast.unit)}
          </span>
        </span>
      </div>
      {contrast.note && <p className="text-[11px] leading-relaxed text-slate-400">{contrast.note}</p>}
    </div>
  )
}

interface FormState {
  problem: string
  subject_scope: string
  measures: string
  target_metric: string
  start_date: string
  review_date: string
  content: string
  baseline_value: string
}

const EMPTY_FORM: FormState = {
  problem: '',
  subject_scope: '',
  measures: '',
  target_metric: 'total:主三门',
  start_date: todayStr(),
  review_date: todayStr(14),
  content: '',
  baseline_value: '',
}

/** 学生档案干预卡（P2-C4）：创建 / 查看 / 复查对照 / 关闭。 */
export default function InterventionCard({
  mode,
  personId,
  scopeQ = {},
}: {
  mode: WorkspaceMode
  personId: number
  scopeQ?: NoteScopeQuery
}) {
  const [items, setItems] = useState<StudentNote[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  /** 409 duplicate_follow_up 的既有干预明细（确认知情后可 force 重发）。 */
  const [duplicates, setDuplicates] = useState<StudentNote[] | null>(null)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  const load = useCallback(async () => {
    const data = await listStudentNotes(mode, personId, scopeQ)
    setItems(((data.notes ?? []) as StudentNote[]).filter(isIntervention))
  }, [mode, personId, scopeQ])

  useEffect(() => {
    setLoading(true)
    load()
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }, [load])

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  function parseBaseline(raw: string, unit: MetricUnit): BaselineValue | null {
    const text = raw.trim()
    if (!text) return null
    const value = Number(text)
    if (!Number.isFinite(value)) return { value: text, source: 'teacher' }
    // 教师按百分数输入（40 = 年级前 40%），存储口径是 0–1 小数，此处转换
    if (unit === 'percentile') return { value: value / 100, unit, source: 'teacher' }
    return { value, unit, source: 'teacher' }
  }

  async function submit(force = false) {
    if (!form.problem.trim()) return
    const unit = metricUnit(form.target_metric)
    const baselineText = form.baseline_value.trim()
    const baselineNum = baselineText ? Number(baselineText) : NaN
    if (unit === 'percentile' && Number.isFinite(baselineNum) && (baselineNum < 0 || baselineNum > 100)) {
      setFormError('基线值：年级前百分位请按百分数填写（0–100，如 40 表示年级前 40%）。')
      return
    }
    if (unit === 'rank' && Number.isFinite(baselineNum) && baselineNum < 1) {
      setFormError('基线值：名次请填不小于 1 的整数。')
      return
    }
    if (unit === 'rank' && Number.isFinite(baselineNum) && !Number.isInteger(baselineNum)) {
      setFormError('基线值：名次为整数（如 300），不支持小数。')
      return
    }
    setSaving(true)
    setFormError(null)
    try {
      await createFollowUp(mode, personId, {
        date: todayStr(),
        category: '谈话',
        content: form.content.trim() || form.problem.trim(),
        follow_up: form.review_date ? `复查：${form.review_date}` : null,
        problem: form.problem.trim(),
        subject_scope: form.subject_scope.trim() || null,
        measures: form.measures.trim() || null,
        target_metric: form.target_metric.trim() || null,
        baseline_value: parseBaseline(form.baseline_value, metricUnit(form.target_metric)),
        start_date: form.start_date || null,
        review_date: form.review_date || null,
        force,
      }, scopeQ)
      setForm(EMPTY_FORM)
      setDuplicates(null)
      setShowForm(false)
      await load()
    } catch (err: unknown) {
      if (err instanceof ApiV1Error && err.code === 'duplicate_follow_up') {
        const existing = Array.isArray(err.body?.existing)
          ? (err.body.existing as StudentNote[])
          : []
        setDuplicates(existing)
        setFormError('该生同科目已有进行中的干预，请先处理下方已有干预。')
      } else {
        setFormError(err instanceof Error ? err.message : '保存失败，请稍后重试')
      }
    } finally {
      setSaving(false)
    }
  }

  async function close(note: StudentNote, status: 'done' | 'dismissed') {
    await patchFollowUp(mode, note.id, { status }, scopeQ)
    await load()
  }

  function toggleExpand(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const openCount = items.filter((n) => n.status === 'open').length

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="flex items-center gap-2 text-base">
          <ClipboardList className="h-4 w-4" />
          干预跟踪
          {openCount > 0 && (
            <Badge className="border-transparent bg-warning-50 text-warning-700">
              {openCount} 项进行中
            </Badge>
          )}
        </CardTitle>
        <Button variant="outline" size="sm" onClick={() => setShowForm((v) => !v)}>
          {showForm ? '取消' : '新建干预'}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {showForm && (
          <div className="space-y-2 rounded-md border border-slate-200 p-3">
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              <input
                value={form.problem}
                onChange={(e) => update('problem', e.target.value)}
                placeholder="问题（必填，例如：主三门名次连续下滑）"
                className="rounded-md border border-slate-200 px-3 py-2 text-sm md:col-span-2"
              />
              <input
                value={form.subject_scope}
                onChange={(e) => update('subject_scope', e.target.value)}
                placeholder="学科范围（可选，如：数学；留空=不区分学科）"
                className="rounded-md border border-slate-200 px-3 py-2 text-sm"
              />
              <select
                value={form.target_metric}
                onChange={(e) => update('target_metric', e.target.value)}
                className="rounded-md border border-slate-200 px-2 py-2 text-sm"
              >
                {METRIC_OPTIONS.map((m) => (
                  <option key={m.value} value={m.value}>
                    目标指标：{m.label}
                  </option>
                ))}
              </select>
              <input
                value={form.measures}
                onChange={(e) => update('measures', e.target.value)}
                placeholder="措施（可选，例如：每周一次错题面批）"
                className="rounded-md border border-slate-200 px-3 py-2 text-sm"
              />
              <input
                value={form.baseline_value}
                onChange={(e) => update('baseline_value', e.target.value)}
                placeholder={BASELINE_PLACEHOLDER[metricUnit(form.target_metric)]}
                className="rounded-md border border-slate-200 px-3 py-2 text-sm"
              />
              <label className="flex items-center gap-2 text-sm text-slate-500">
                开始日
                <input
                  type="date"
                  value={form.start_date}
                  onChange={(e) => update('start_date', e.target.value)}
                  className="flex-1 rounded-md border border-slate-200 px-2 py-1 text-sm"
                />
              </label>
              <label className="flex items-center gap-2 text-sm text-slate-500">
                计划复查日
                <input
                  type="date"
                  value={form.review_date}
                  onChange={(e) => update('review_date', e.target.value)}
                  className="flex-1 rounded-md border border-slate-200 px-2 py-1 text-sm"
                />
              </label>
              <textarea
                value={form.content}
                onChange={(e) => update('content', e.target.value)}
                rows={2}
                placeholder="本次谈话/沟通纪要（可选，缺省用问题摘要）"
                className="rounded-md border border-slate-200 px-3 py-2 text-sm md:col-span-2"
              />
            </div>
            {duplicates && duplicates.length > 0 && (
              <div className="rounded-md border border-warning-200 bg-warning-50 p-2 text-xs text-warning-700">
                <p className="font-medium">该生已有进行中的同科干预：</p>
                <ul className="mt-1 list-inside list-disc space-y-0.5">
                  {duplicates.map((d) => (
                    <li key={d.id}>
                      {d.start_date || d.date} 起 · {d.subject_scope || '不区分学科'} ·{' '}
                      {d.problem || d.content}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {formError && <p className="text-xs text-danger-500">{formError}</p>}
            <div className="flex items-center justify-end gap-2">
              {duplicates && duplicates.length > 0 && (
                <Button size="sm" variant="outline" onClick={() => submit(true)} disabled={saving}>
                  确认知情，仍要创建
                </Button>
              )}
              <Button
                size="sm"
                onClick={() => submit(false)}
                disabled={saving || !form.problem.trim()}
              >
                {saving ? '保存中…' : '创建干预'}
              </Button>
            </div>
          </div>
        )}

        {loading ? (
          <p className="py-6 text-center text-sm text-slate-400">加载中…</p>
        ) : items.length === 0 ? (
          <p className="py-6 text-center text-sm text-slate-400">
            暂无干预记录；需要长期跟进的问题可在此建档并按复查日回看。
          </p>
        ) : (
          <div className="space-y-3">
            {items.map((n) => {
              const status = STATUS_META[n.status || 'open'] || STATUS_META.open
              const isOpen = n.status === 'open'
              return (
                <div key={n.id} className="rounded-md border border-slate-100 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge className={cn('border-transparent', status.className)}>
                      {status.label}
                    </Badge>
                    {n.subject_scope && <Badge variant="secondary">{n.subject_scope}</Badge>}
                    <span className="text-xs text-slate-400">
                      {n.start_date || n.date}
                      {n.review_date ? ` → 复查 ${n.review_date}` : ''}
                    </span>
                  </div>
                  <p className="mt-2 text-sm font-medium text-slate-800">{n.problem || n.content}</p>
                  {n.measures && <p className="mt-1 text-sm text-slate-600">措施：{n.measures}</p>}
                  {n.target_metric && (
                    <p className="mt-1 text-xs text-slate-500">
                      目标指标：
                      {n.target_metric}
                      {n.baseline_value?.value != null && (
                        <>
                          {' '}
                          · 基线{' '}
                          {formatValue(
                            Number(n.baseline_value.value),
                            n.baseline_value.unit ?? metricUnit(n.target_metric),
                          )}
                          {n.baseline_value.exam_name ? `（${n.baseline_value.exam_name}）` : ''}
                          {n.baseline_value.source === 'teacher' ? ' · 手填' : ' · 自动捕获'}
                        </>
                      )}
                    </p>
                  )}
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {isOpen && (
                      <>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => close(n, 'done')}
                          className="h-7 px-2 text-xs"
                        >
                          <CalendarCheck className="mr-1 h-3 w-3" />
                          标记完成并关闭
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => close(n, 'dismissed')}
                          className="h-7 px-2 text-xs text-slate-500"
                        >
                          不再跟进
                        </Button>
                      </>
                    )}
                    {n.target_metric && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => toggleExpand(n.id)}
                        className="h-7 px-2 text-xs text-brand-600"
                      >
                        {expanded.has(n.id) ? '收起复查对照' : '查看复查对照'}
                      </Button>
                    )}
                  </div>
                  {expanded.has(n.id) && n.target_metric != null && (
                    <ContrastPanel mode={mode} noteId={n.id} scopeQ={scopeQ} />
                  )}
                </div>
              )
            })}
            <p className="text-[11px] text-slate-400">
              复查对照只呈现数据事实，不自动判定干预成功或失败；结合班级近况由教师自行研判。
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
