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

import { useCallback, useEffect, useMemo, useState } from 'react'
import { CalendarCheck, ClipboardList, RefreshCw } from 'lucide-react'

import {
  ApiV1Error,
  createFollowUp,
  fetchReviewContrast,
  fetchInterventionMetrics,
  type InterventionTarget,
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
import {metricUnit, buildTargetPayload, type TargetDraft} from './intervention-targets'

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

  return <div className="space-y-2">{(contrast.results ?? [contrast]).map((result, i) => (
    <div key={result.metric ?? i}>
      <p className="mt-2 text-xs font-medium">{metricLabel(result.metric ?? '')}</p>
      <MetricResult contrast={result} />
    </div>
  ))}</div>
}

function MetricResult({contrast}: {contrast: ReviewContrast}) {
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

function metricLabel(metric: string): string {
  return METRIC_OPTIONS.find(m => m.value === metric)?.label ?? (
    metric.startsWith('total:') ? `${metric.slice(6)}总分名次` :
    metric.startsWith('subject_grade:') ? `${metric.slice(14)}等级分` : `${metric.slice(8)}（年级前百分位）`)
}

interface FormState {
  problem: string; subject_scope: string; measures: string; start_date: string; review_date: string; content: string
  targets: TargetDraft[]
}
function emptyForm(): FormState {
  return {problem: '', subject_scope: '', measures: '', start_date: todayStr(), review_date: todayStr(14), content: '', targets: []}
}
function noteTargets(note: StudentNote): InterventionTarget[] {
  return note.targets?.length ? note.targets : note.target_metric ? [{metric: note.target_metric, baseline_value: note.baseline_value, status: note.status ?? 'open'}] : []
}
const DEFAULT_SCOPE: NoteScopeQuery = {}
export default function InterventionCard({mode, personId, scopeQ: scopeInput = DEFAULT_SCOPE}: {mode: WorkspaceMode; personId: number; scopeQ?: NoteScopeQuery}) {
  const scopeQ = useMemo(() => ({academic_year_id: scopeInput.academic_year_id, term_id: scopeInput.term_id,
    class_id: scopeInput.class_id, teaching_class_id: scopeInput.teaching_class_id, subject: scopeInput.subject}),
    [scopeInput.academic_year_id, scopeInput.term_id, scopeInput.class_id, scopeInput.teaching_class_id, scopeInput.subject])
  const [items, setItems] = useState<StudentNote[]>([])
  const [options, setOptions] = useState<Array<{value: string; label: string; kind: string}>>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<StudentNote | null>(null)
  const [form, setForm] = useState<FormState>(emptyForm)
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [duplicates, setDuplicates] = useState<StudentNote[] | null>(null)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const load = useCallback(async () => {
    const data = await listStudentNotes(mode, personId, scopeQ)
    setItems((data.notes ?? []).filter(isIntervention))
  }, [mode, personId, scopeQ])
  useEffect(() => {
    let active = true
    setLoading(true); setShowForm(false); setEditing(null); setItems([]); setOptions([]); setFormError(null)
    Promise.all([listStudentNotes(mode, personId, scopeQ), fetchInterventionMetrics(mode, scopeQ)])
      .then(([data, choices]) => {if (active) {setItems((data.notes ?? []).filter(isIntervention)); setOptions(choices.options)}})
      .catch((err: unknown) => {if (active) setFormError(err instanceof Error ? err.message : '读取失败')})
      .finally(() => {if (active) setLoading(false)})
    return () => {active = false}
  }, [mode, personId, scopeQ])
  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm(prev => ({...prev, [key]: value}))
  }
  function edit(note: StudentNote) {
    setEditing(note); setDuplicates(null); setFormError(null)
    setForm({problem: note.problem ?? note.content, subject_scope: note.subject_scope ?? '', measures: note.measures ?? '',
      start_date: note.start_date ?? note.date, review_date: note.review_date ?? '', content: note.content,
      targets: noteTargets(note).map(t => ({metric: t.metric, baseline: '', original: t}))})
    setShowForm(true)
  }
  function toggleMetric(metric: string) {
    setForm(prev => ({...prev, targets: prev.targets.some(t => t.metric === metric)
      ? prev.targets.filter(t => t.metric !== metric)
      : [...prev.targets, {metric, baseline: '', original: editing ? noteTargets(editing).find(t => t.metric === metric) : undefined}]}))
  }
  async function submit(force = false) {
    if (!form.problem.trim() || !form.targets.length) return
    setSaving(true); setFormError(null)
    try {
      const payload = {problem: form.problem.trim(), subject_scope: form.subject_scope.trim() || null,
        measures: form.measures.trim() || null, start_date: form.start_date || null, review_date: form.review_date || null,
        content: form.content.trim() || form.problem.trim(), targets: buildTargetPayload(form.targets)}
      if (editing) await patchFollowUp(mode, editing.id, payload, scopeQ)
      else await createFollowUp(mode, personId, {...payload, date: todayStr(), category: '谈话',
        follow_up: form.review_date ? `复查：${form.review_date}` : null, force}, scopeQ)
      setForm(emptyForm()); setEditing(null); setDuplicates(null); setShowForm(false); setExpanded(new Set()); await load()
    } catch (err: unknown) {
      if (err instanceof ApiV1Error && err.code === 'duplicate_follow_up') {
        setDuplicates(Array.isArray(err.body?.existing) ? err.body.existing as StudentNote[] : [])
        setFormError('该生同科目已有进行中的干预，请先处理下方已有干预。')
      } else setFormError(err instanceof Error ? err.message : '保存失败，请稍后重试')
    } finally {setSaving(false)}
  }
  async function closeTarget(note: StudentNote, metric: string | null, status: 'done' | 'dismissed') {
    setSaving(true); setFormError(null)
    try {
      await patchFollowUp(mode, note.id, metric === null ? {status} : {targets: noteTargets(note).map(t => ({metric: t.metric, status: t.metric === metric ? status : t.status}))}, scopeQ)
      setExpanded(new Set()); await load()
    } catch (err: unknown) {setFormError(err instanceof Error ? err.message : '更新失败')}
    finally {setSaving(false)}
  }
  function toggleExpand(id: number) {
    setExpanded(prev => {const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next})
  }
  const openCount = items.filter(n => n.status === 'open').length
  return <Card>
    <CardHeader className="flex-row items-center justify-between space-y-0">
      <CardTitle className="flex flex-wrap items-center gap-2 text-base"><ClipboardList className="h-4 w-4"/>干预跟踪
        {openCount > 0 && <Badge className="border-transparent bg-warning-50 text-warning-700">{openCount} 项进行中</Badge>}
      </CardTitle>
      <Button variant="outline" size="sm" disabled={saving} onClick={() => {setShowForm(!showForm); setEditing(null); setForm(emptyForm()); setDuplicates(null); setFormError(null)}}>{showForm ? '取消' : '新建干预'}</Button>
    </CardHeader>
    <CardContent className="space-y-4">
      {formError && <p role="alert" className="text-sm text-danger-500">{formError}</p>}
      {showForm && <form onSubmit={e => {e.preventDefault(); void submit(false)}} className="space-y-3 rounded-md border border-slate-200 p-3">
        <p className="font-medium">{editing ? '编辑干预' : '新建干预'}</p>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          <input aria-label="问题" value={form.problem} onChange={e => update('problem', e.target.value)} placeholder="问题（必填，例如：语数英相对位置下滑）" className="min-w-0 rounded-md border p-2 text-sm md:col-span-2"/>
          <input aria-label="学科范围" value={form.subject_scope} onChange={e => update('subject_scope', e.target.value)} placeholder="学科范围（可选，如：语数英）" className="min-w-0 rounded-md border p-2 text-sm"/>
          <input aria-label="措施" value={form.measures} onChange={e => update('measures', e.target.value)} placeholder="措施（可选，例如：每周一次错题面批）" className="min-w-0 rounded-md border p-2 text-sm"/>
          <label className="flex min-w-0 flex-wrap items-center gap-2 text-sm">开始日<input required type="date" value={form.start_date} onChange={e => update('start_date', e.target.value)} className="min-w-0 flex-1 rounded-md border p-2"/></label>
          <label className="flex min-w-0 flex-wrap items-center gap-2 text-sm">计划复查日<input type="date" value={form.review_date} onChange={e => update('review_date', e.target.value)} className="min-w-0 flex-1 rounded-md border p-2"/></label>
        </div>
        <fieldset className="rounded-md border p-3"><legend className="px-1 text-sm font-medium">目标指标（可多选，至少一项）</legend>
          <p className="mb-2 text-xs text-slate-500">学科范围是文字说明，请在这里分别勾选需要跟进的指标。</p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">{[...options, ...form.targets.filter(t => !options.some(o => o.value === t.metric)).map(t => ({value: t.metric, label: metricLabel(t.metric), kind: ''}))].map(o => <label key={o.value} className="flex items-start gap-2 text-sm"><input type="checkbox" checked={form.targets.some(t => t.metric === o.value)} onChange={() => toggleMetric(o.value)} className="mt-1"/>{metricLabel(o.value)}</label>)}</div>
        </fieldset>
        <div className="space-y-2">{form.targets.map(t => <label key={t.metric} className="block rounded-md bg-slate-50 p-2 text-sm">
          <span>{metricLabel(t.metric)} · 基线</span>
          {t.original?.baseline_value?.value != null && <span className="ml-2 text-xs text-slate-500">原值：{formatValue(Number(t.original.baseline_value.value), t.original.baseline_value.unit ?? metricUnit(t.metric))}</span>}
          <input aria-label={`${metricLabel(t.metric)}基线`} value={t.baseline} onChange={e => update('targets', form.targets.map(row => row.metric === t.metric ? {...row, baseline: e.target.value} : row))} placeholder={t.original ? '留空保留原基线；填写数值可修改' : BASELINE_PLACEHOLDER[metricUnit(t.metric)]} className="mt-1 w-full min-w-0 rounded-md border p-2 text-sm"/>
        </label>)}</div>
        {editing && <p className="text-xs text-slate-500">取消已保存指标的勾选会结束该指标并保留历史。新增指标沿用本条干预的开始日。</p>}
        <textarea aria-label="谈话沟通纪要" value={form.content} onChange={e => update('content', e.target.value)} placeholder="本次谈话/沟通纪要（可选，缺省用问题摘要）" rows={2} className="w-full rounded-md border p-2 text-sm"/>
        {duplicates && duplicates.length > 0 && <div className="rounded-md bg-warning-50 p-2 text-sm"><p>该生已有进行中的同科干预：</p>{duplicates.map(d => <p key={d.id}>{d.start_date || d.date} · {d.problem || d.content}</p>)}<Button type="button" variant="outline" disabled={saving} onClick={() => submit(true)}>确认知情，仍要创建</Button></div>}
        <div className="flex justify-end"><Button type="submit" disabled={saving || !form.problem.trim() || !form.targets.length}>{saving ? '保存中…' : editing ? '保存修改' : '创建干预'}</Button></div>
      </form>}
      {loading ? <p className="py-6 text-center text-sm text-slate-400">加载中…</p> : items.length === 0 ? <p className="py-6 text-center text-sm text-slate-400">暂无干预记录；需要长期跟进的问题可在此建档并按复查日回看。</p> : <div className="space-y-3">{items.map(n => {
        const targets = noteTargets(n); const meta = STATUS_META[n.status ?? 'open']
        return <div key={n.id} className="space-y-2 rounded-md border p-3">
          <div className="flex flex-wrap items-center gap-2"><Badge className={cn('border-transparent', meta.className)}>{meta.label}</Badge>{n.subject_scope && <Badge variant="secondary">{n.subject_scope}</Badge>}
            <span className="text-xs text-slate-500">{n.start_date || n.date}{n.review_date ? ` → 复查 ${n.review_date}` : ''}</span>
            <span className="text-xs text-slate-500">已结束{targets.filter(t => t.status !== 'open').length}/{targets.length}项</span>
          </div>
          <p className="font-medium">{n.problem || n.content}</p>{n.measures && <p className="text-sm text-slate-600">措施：{n.measures}</p>}
          {targets.map(t => <div key={t.metric} className="flex flex-wrap items-center gap-2 rounded-md bg-slate-50 p-2 text-sm">
            <span>{metricLabel(t.metric)}</span><Badge variant="secondary">{STATUS_META[t.status].label}</Badge>
            <span className="text-xs text-slate-500">基线：{formatValue(t.baseline_value?.value == null ? null : Number(t.baseline_value.value), t.baseline_value?.unit ?? metricUnit(t.metric))}{t.baseline_value?.exam_name ? `（${t.baseline_value.exam_name}）` : ''}{t.baseline_value ? t.baseline_value.source === 'teacher' ? ' · 手填' : ' · 自动捕获' : ''}</span>
            {t.status === 'open' && <div className="flex flex-wrap gap-1"><Button size="sm" variant="outline" disabled={saving} aria-label={`${metricLabel(t.metric)}完成跟进`} onClick={() => closeTarget(n, t.metric, 'done')}><CalendarCheck className="mr-1 h-3 w-3"/>完成跟进</Button><Button size="sm" variant="ghost" disabled={saving} aria-label={`${metricLabel(t.metric)}不再跟进`} onClick={() => closeTarget(n, t.metric, 'dismissed')}>不再跟进</Button></div>}
          </div>)}
          {targets.length === 0 && n.status === 'open' && <div className="flex gap-2"><Button size="sm" variant="outline" disabled={saving} onClick={() => closeTarget(n, null, 'done')}>标记完成并关闭</Button><Button size="sm" variant="ghost" disabled={saving} onClick={() => closeTarget(n, null, 'dismissed')}>不再跟进</Button></div>}
          <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={saving} onClick={() => edit(n)}>编辑干预</Button>{targets.length > 0 && <Button size="sm" variant="ghost" onClick={() => toggleExpand(n.id)}>{expanded.has(n.id) ? '收起复查对照' : '查看复查对照'}</Button>}</div>
          {expanded.has(n.id) && <ContrastPanel mode={mode} noteId={n.id} scopeQ={scopeQ} />}
        </div>
      })}<p className="text-[11px] text-slate-400">复查对照只呈现数据事实，不自动判定干预成功或失败；结合班级近况由教师自行研判。</p></div>}
    </CardContent>
  </Card>
}
