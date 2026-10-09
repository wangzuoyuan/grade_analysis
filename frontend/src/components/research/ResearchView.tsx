'use client'
import {useEffect, useMemo, useRef, useState} from 'react'
import {FlaskConical, AlertTriangle} from 'lucide-react'
import {useWorkspace} from '@/lib/workspace'
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from '@/components/ui/card'
import {FocusResults} from './FocusResults'
import {FollowUpReview} from './FollowUpReview'
import {COHORTS_ENDPOINT, OUTCOME_ENDPOINT, researchGet, type FocusCohorts, type FocusOutcome} from './focus-api'

const PROBLEM_LABELS: Record<string,string> = {
  明显偏科型: '偏科短板', 持续下滑型: '持续下滑', 短期下滑型: '短期下滑', 临界下滑型: '临界段下滑',
  稳定临界型: '稳定临界', 临界上升型: '临界段上升', 作业风险型: '作业缺交', 综合风险型: '多项问题',
  持续进步型: '持续进步', 稳定优秀型: '稳定优秀', 高位波动型: '高位波动',
}
const selectClass = 'h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700'
export function defaultMetric(problem: string, data: FocusCohorts): string {
  if (problem === '明显偏科型') return 'problem:imbalance'
  if (problem === '作业风险型') return 'problem:homework'
  return data.metric_options.find(m => m.value === 'total:主三门')?.value ?? data.metric_options[0]?.value ?? ''
}
export function LimitationBanner() {
  return <div data-testid="research-limitations" className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2 text-xs leading-5 text-amber-800">
    <AlertTriangle className="h-4 w-4 shrink-0"/>前后变化用于回看与跟进，不能证明某项措施有效或无效。
  </div>
}
export function ResearchView() {
  const {mode, filter, scope, scopeError, generation} = useWorkspace()
  const academicYearId = filter.academic_year_id
  const classId = filter.class_id
  const scopeQ = useMemo(() => ({academic_year_id: academicYearId, class_id: classId}), [academicYearId, classId])
  const [tab, setTab] = useState<'focus'|'reviews'>('focus')
  const [data, setData] = useState<FocusCohorts | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [basis, setBasis] = useState('current_time_point')
  const [problem, setProblem] = useState('明显偏科型')
  const [fromExam, setFromExam] = useState('')
  const [toExam, setToExam] = useState('')
  const [metric, setMetric] = useState('problem:imbalance')
  const [outcome, setOutcome] = useState<FocusOutcome | null>(null)
  const [outcomeError, setOutcomeError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [reload, setReload] = useState(0)
  const seq = useRef(0)
  const outcomeReqRef = useRef(0)
  const abortOutcome = useRef<AbortController | null>(null)
  function resetOutcome() {
    outcomeReqRef.current += 1
    abortOutcome.current?.abort()
    setOutcome(null); setOutcomeError(null); setLoading(false)
  }
  useEffect(() => {
    const current = ++seq.current
    const abort = new AbortController()
    setData(null); setError(null); resetOutcome()
    if (mode !== 'homeroom' || !scopeQ || !scope || scopeError) return
    researchGet<FocusCohorts>(COHORTS_ENDPOINT, scopeQ, abort.signal).then(result => {
      if (current !== seq.current) return
      setData(result)
      const exams = result.exams
      const end = exams[0]?.exam_name ?? ''
      // 优先从倒数第二场有命中者的历史名单开始，避免默认一人也无法判型。
      const historical = result.cohorts.find(c => c.exam_name !== end && c.membership_basis === 'exam_anchor' && c.type_name === '明显偏科型' && c.student_count > 0)
      const selected = historical ?? result.cohorts.find(c => c.membership_basis === 'current_time_point' && c.student_count > 0)
      const nextProblem = selected?.type_name ?? '明显偏科型'
      setBasis(historical ? 'exam_anchor' : 'current_time_point')
      setProblem(nextProblem)
      setFromExam(historical?.exam_name ?? exams[exams.length - 1]?.exam_name ?? '')
      setToExam(end); setMetric(defaultMetric(nextProblem,result))
    }).catch(reason => {if (!abort.signal.aborted && current === seq.current) setError(reason instanceof Error ? reason.message : '关注名单加载失败')})
    return () => {seq.current += 1; abort.abort(); outcomeReqRef.current += 1; abortOutcome.current?.abort()}
  }, [mode, scopeQ, Boolean(scope), Boolean(scopeError), generation, reload])

  const cohort = basis === 'exam_anchor' ? `focus:${problem}@${fromExam}` : `current-focus:${problem}`
  const selected = data?.cohorts.find(c => c.cohort === cohort)
  const fromIndex = data?.exams.findIndex(e => e.exam_name === fromExam) ?? -1
  const laterExams = data?.exams.slice(0,Math.max(0,fromIndex)) ?? []
  const historicalPossible = data?.exams.some((e,i) => e.historical_available && i > 0) ?? false
  const canRun = Boolean(selected && fromExam && toExam && metric && laterExams.some(e => e.exam_name === toExam) && !loading)
  const metricOptions = data ? [
    ...(problem === '明显偏科型' ? [{value:'problem:imbalance',label:'原薄弱学科与短板差距'}] : []),
    ...(problem === '作业风险型' ? [{value:'problem:homework',label:'两时点近 30 天作业事实'}] : []),
    ...data.metric_options,
  ] : []
  async function runOutcome() {
    if (!scopeQ || !canRun) return
    const current = ++outcomeReqRef.current
    const abort = new AbortController(); abortOutcome.current?.abort(); abortOutcome.current = abort
    setLoading(true); setOutcomeError(null); setOutcome(null)
    try {
      const result = await researchGet<FocusOutcome>(OUTCOME_ENDPOINT, {...scopeQ, cohort, from_exam:fromExam, to_exam:toExam, metric}, abort.signal)
      if (current === outcomeReqRef.current) setOutcome(result)
    } catch (reason) {if (!abort.signal.aborted && current === outcomeReqRef.current) setOutcomeError(reason instanceof Error ? reason.message : '回看加载失败')}
    finally {if (current === outcomeReqRef.current) setLoading(false)}
  }
  if (mode !== 'homeroom') return null
  return <Card>
    <CardHeader><CardTitle className="flex items-center gap-2"><FlaskConical className="h-5 w-5 text-brand-600"/>关注学生回看</CardTitle><CardDescription>原来的问题有没有改善，哪些学生还需要继续跟进</CardDescription></CardHeader>
    <CardContent className="space-y-5">
      <div role="tablist" aria-label="回看方式" className="flex gap-1 rounded-lg bg-slate-100 p-1" onKeyDown={event => {
        if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return
        event.preventDefault()
        const next = event.key === 'Home' ? 'focus' : event.key === 'End' ? 'reviews' : tab === 'focus' ? 'reviews' : 'focus'
        setTab(next)
        document.getElementById(`research-tab-${next}`)?.focus()
      }}>
        {([['focus','问题学生回看'],['reviews','已建档跟进复查']] as const).map(([value,label]) => <button key={value} id={`research-tab-${value}`} role="tab" type="button" tabIndex={tab === value ? 0 : -1} aria-selected={tab === value} aria-controls={`research-panel-${value}`} onClick={() => setTab(value)} className={`flex-1 rounded-md px-3 py-2 text-sm font-medium ${tab === value ? 'bg-white text-brand-700 shadow-sm' : 'text-slate-500'}`}>{label}</button>)}
      </div>
      <LimitationBanner/>
      {scopeError && <p role="alert" className="text-sm text-amber-800">当前班级范围不可用，请先核对工作台设置。</p>}
      <div id="research-panel-focus" role="tabpanel" aria-labelledby="research-tab-focus" hidden={tab !== 'focus'} className="space-y-5">
        {error ? <div role="alert" className="rounded-lg bg-amber-50 p-4 text-sm text-amber-800">{error}<button type="button" onClick={() => setReload(r => r+1)} className="ml-3 underline">重试</button></div> : !data ? <p role="status" className="py-6 text-sm text-slate-500">正在读取关注名单…</p> : data.exams.length < 2 ? <p className="rounded-lg bg-slate-50 p-6 text-sm text-slate-500">至少需要两场考试才能回看前后变化。可以先查看已建档跟进复查。</p> : <>
          <div className="flex flex-wrap gap-2" aria-label="选人时点">{[['exam_anchor','当时的问题学生'],['current_time_point','现在的问题学生']].map(([value,label]) => <button key={value} type="button" disabled={value === 'exam_anchor' && !historicalPossible} aria-pressed={basis === value} onClick={() => {
            resetOutcome(); setBasis(value)
            if (value === 'exam_anchor' && !data.exams.find(e => e.exam_name === fromExam)?.historical_available) {
              const start = data.exams.find((e,i) => e.historical_available && i > 0)!
              setFromExam(start.exam_name); setToExam(data.exams[0].exam_name)
            }
          }} className={`rounded-full px-3 py-1.5 text-xs disabled:opacity-40 ${basis === value ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600'}`}>{label}</button>)}</div>
          <p className="text-xs leading-5 text-slate-500">{basis === 'exam_anchor' ? '按起点考试判定并固定名单，后续改善者仍保留；主类型和次标签都纳入。' : '按当前问题选人，回看这批学生的历史走势；不代表他们当时已有该问题。'}{!historicalPossible && ' 历史日期不足，暂不能还原当时名单。'}</p>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <label className="space-y-1 text-xs text-slate-500"><span>关注问题</span><select aria-label="关注问题" className={selectClass} value={problem} onChange={e => {resetOutcome(); setProblem(e.target.value); setMetric(defaultMetric(e.target.value,data))}}>{Object.entries(PROBLEM_LABELS).map(([value,label]) => {
              const option = data.cohorts.find(c => c.type_name === value && c.membership_basis === basis && (basis !== 'exam_anchor' || c.exam_name === fromExam))
              return <option key={value} value={value}>{label}（{option?.student_count ?? 0} 人）</option>
            })}</select></label>
            <label className="space-y-1 text-xs text-slate-500"><span>从哪场考试看起</span><select aria-label="从哪场考试看起" className={selectClass} value={fromExam} onChange={e => {resetOutcome(); setFromExam(e.target.value); const idx=data.exams.findIndex(x => x.exam_name === e.target.value); if (!data.exams.slice(0,idx).some(x => x.exam_name === toExam)) setToExam(data.exams[0].exam_name)}}>{data.exams.filter((e,i) => i>0 && (basis !== 'exam_anchor' || e.historical_available)).map(e => <option key={e.exam_name}>{e.exam_name}</option>)}</select></label>
            <label className="space-y-1 text-xs text-slate-500"><span>回看到哪场考试</span><select aria-label="回看到哪场考试" className={selectClass} value={toExam} onChange={e => {resetOutcome(); setToExam(e.target.value)}}>{laterExams.map(e => <option key={e.exam_name}>{e.exam_name}</option>)}</select></label>
            <label className="space-y-1 text-xs text-slate-500"><span>观察指标</span><select aria-label="观察指标" className={selectClass} value={metric} onChange={e => {resetOutcome(); setMetric(e.target.value)}}>{metricOptions.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}</select></label>
          </div>
          <div className="flex flex-wrap items-center gap-3"><button type="button" disabled={!canRun} onClick={runOutcome} className="rounded-lg bg-gradient-to-r from-[#1f7fd6] to-[#35b9e9] px-4 py-2 text-sm font-medium text-white disabled:opacity-40">{loading ? '正在回看…' : '查看前后变化'}</button><span className="text-xs text-slate-500">入选 {selected?.student_count ?? 0} 人 · 可查看每个人的入选依据与前后事实</span></div>
          {outcomeError && <p role="alert" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">{outcomeError}</p>}
          {outcome ? <FocusResults outcome={outcome}/> : !loading && !outcomeError ? <p className="rounded-lg border border-dashed border-slate-200 py-8 text-center text-sm text-slate-400">选择关注问题，点击“查看前后变化”</p> : null}
        </>}
      </div>
      {tab === 'reviews' && scopeQ && scope && !scopeError && <div id="research-panel-reviews" role="tabpanel" aria-labelledby="research-tab-reviews"><FollowUpReview key={generation} scopeQ={scopeQ}/></div>}
      <details className="rounded-lg border border-slate-100 px-3 py-2"><summary className="cursor-pointer text-xs text-slate-400">口径与版本</summary><div className="mt-2 space-y-1 text-xs leading-5 text-slate-500"><p>关注回看：{data?.calc_version ?? '—'} · 结果：{outcome?.calc_version ?? '—'}</p><p>沿用既有诊断标签、成绩变化分解和逐条复查，按当前工作台及学年解析范围。缺考、缺科不转零。</p><p>旧历史记录可能只有月份日期；无法证明时点时只提供当前问题学生的历史走势。</p></div></details>
    </CardContent>
  </Card>
}
