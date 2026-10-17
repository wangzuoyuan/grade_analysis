'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { CalendarCheck, Settings2 } from 'lucide-react'
import { fetchTeachingAttention, createAttentionFollowUp, finishAttentionFollowUp,
  saveAttentionSettings, type AttentionScopeQuery } from '@/lib/api-v1'
import { ATTENTION_TABS, attentionRows, type AttentionResponse, type AttentionSettings,
  type AttentionStudent, type AttentionFollowUp, type AttentionSnapshot, type AttentionTab } from '@/lib/attention'
import { personalReason } from '@/lib/student-homework'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { MoreToggle } from '@/components/ui/more-toggle'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import { CardFoldToggle, useCardFold } from './card-fold'

const fieldClass = 'w-full rounded-md border border-slate-200 bg-white p-2 text-sm'
const settingFields = [
  ['percentile_drop', '高一明显下降（百分点）', false],
  ['grade_score_drop', '高二、高三明显下降（等级分）', false],
  ['target_top_percent', '高一支持目标（年级前百分之几）', true],
  ['target_grade2', '高二支持目标（等级分）', true],
  ['target_grade3', '高三支持目标（等级分）', true],
] as const

function Snapshot({ value }: { value: AttentionSnapshot }) {
  return <div className="space-y-1 text-xs text-slate-600">
    <p>{value.achievement ?? '—'}</p>
    {value.timeline?.map((p, i) => <p key={i}>{p.date ?? '日期未知'} · {p.exam} · {p.value}</p>)}
    {value.reasons?.map((r, i) => <p key={i}>{r}</p>)}
  </div>
}

function SettingsForm({ initial, saving, onSave }: {
  initial: AttentionSettings; saving: boolean; onSave: (value: AttentionSettings) => void
}) {
  const [draft, setDraft] = useState(initial)
  return <form className="space-y-4" onSubmit={e => { e.preventDefault(); onSave(draft) }}>
    <p className="text-sm text-slate-500">应用于当前学年所教学科的全部教学班。支持目标留空时不启用；初始变化门槛用于提醒核查，可按实际教学调整。</p>
    {settingFields.map(([key, label, nullable]) => <label key={key} className="block space-y-1 text-sm">
      <span>{label}{nullable ? '（可留空）' : ''}</span>
      <Input type="number" min={nullable ? 0 : 0.1} max={100} step="0.1" required={!nullable}
        value={draft[key] ?? ''} onChange={e => setDraft({ ...draft, [key]: e.target.value === '' ? null : Number(e.target.value) })} />
    </label>)}
    <Button type="submit" disabled={saving}>{saving ? '保存中…' : '保存规则'}</Button>
  </form>
}

type Action = { student: AttentionStudent; followup?: AttentionFollowUp }

export function TeachingAttention({ scopeQuery }: { scopeQuery: AttentionScopeQuery }) {
  const [data, setData] = useState<AttentionResponse | null>(null)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [tab, setTab] = useState<AttentionTab>('all')
  const [expanded, setExpanded] = useState(false)
  const [configOpen, setConfigOpen] = useState(false)
  const [action, setAction] = useState<Action | null>(null)
  const [measures, setMeasures] = useState('')
  const [reviewDate, setReviewDate] = useState('')
  const [saving, setSaving] = useState(false)
  const [actionError, setActionError] = useState('')
  const fold = useCardFold('teaching:priority-attention')
  const year = scopeQuery.academic_year_id
  const classId = scopeQuery.teaching_class_id
  useEffect(() => {
    let cancelled = false
    setData(null); setError('')
    fetchTeachingAttention({ academic_year_id: year, teaching_class_id: classId })
      .then(value => { if (!cancelled) setData(value) })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : '关注名单加载失败') })
    return () => { cancelled = true }
  }, [year, classId, refresh])
  const rows = data ? attentionRows(data.students, tab) : []
  function startAction(student: AttentionStudent, followup?: AttentionFollowUp) {
    setAction({ student, followup }); setMeasures(''); setActionError('')
    const day = new Date(`${data?.as_of}T12:00:00`)
    day.setDate(day.getDate() + 7)
    setReviewDate(`${day.getFullYear()}-${String(day.getMonth()+1).padStart(2,'0')}-${String(day.getDate()).padStart(2,'0')}`)
  }
  async function perform(work: () => Promise<unknown>) {
    if (saving) return
    setSaving(true); setActionError('')
    try {
      await work()
      setAction(null); setConfigOpen(false); setRefresh(n => n + 1)
    } catch (e) { setActionError(e instanceof Error ? e.message : '保存失败，请重试') }
    finally { setSaving(false) }
  }
  return <Card data-testid="teaching-attention" className="overflow-hidden">
    <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div><CardTitle>优先跟进学生</CardTitle><CardDescription className="mt-1">根据学习变化、作业异常与复查事项选人，每人合并展示关注依据</CardDescription></div>
      <div className="flex shrink-0 items-center gap-1">
        <Button size="sm" variant="ghost" disabled={!data} onClick={() => { setActionError(''); setConfigOpen(true) }}><Settings2 className="h-4 w-4" />关注规则</Button>
        <CardFoldToggle folded={fold.folded} onToggle={fold.toggle} />
      </div>
    </CardHeader>
    {fold.folded ? null : <CardContent className="space-y-4">
      {error ? <div role="alert" className="text-sm text-amber-800">{error} <Button variant="outline" size="sm" onClick={() => setRefresh(n => n+1)}>重试</Button></div> : !data ? <p className="text-sm text-slate-500">正在核对关注依据…</p> : <>
        <div className="flex flex-wrap gap-2" aria-label="关注事项筛选">
          {ATTENTION_TABS.map(([key,label]) => <Button key={key} size="sm" variant={tab===key?'default':'outline'}
            aria-pressed={tab===key} onClick={() => { setTab(key); setExpanded(false) }}>{label} {data.counts[key]}</Button>)}
        </div>
        <p className="text-xs text-slate-500">作业观察：{data.window_start} 至 {data.window_end} · 截至 {data.as_of} · 人数按学生去重</p>
        {!rows.length ? <p className="rounded-lg bg-slate-50 p-5 text-sm text-slate-600">{tab==='review' ? '当前没有跟进中的复查事项。' : '当前范围暂无符合此类条件的学生。'}</p> :
          <ul className="divide-y divide-slate-100">{(expanded?rows:rows.slice(0,6)).map(student => <li key={student.person_id} data-testid="attention-student" className="space-y-2 py-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 space-y-1">
                <Link className="font-semibold text-slate-800 hover:text-brand-600" href={`/student/${student.person_id}?ws=teaching`}>{student.name ?? '未命名学生'}</Link>
                <p className="text-xs text-slate-500">{student.class_labels.join('、')} {student.alias ? `· ${student.alias}` : ''}</p>
              </div>
              <div className="flex flex-wrap gap-1">{student.signals.map(s => <Badge key={s.code} variant="secondary">{s.title}</Badge>)}
                {!student.active ? <Badge variant="secondary">已跟进 · 待复查</Badge> : null}</div>
            </div>
            {student.signals.map(s => <p key={s.code} className="text-sm text-slate-700">{s.detail}</p>)}
            <p className="text-xs text-slate-500">建议：{student.suggestion}</p>
            <details className="rounded-lg border border-slate-100 p-3">
              <summary className="cursor-pointer text-sm text-brand-700">查看依据</summary>
              <div className="mt-3 space-y-3">
                <Snapshot value={student.snapshot} />
                {student.signals.map(s => <div key={s.code} className="text-xs text-slate-600">
                  <p className="font-medium">{s.title}</p>
                  {s.evidence.map((e,i) => <p key={i} className="break-words">{[e.date ?? e.assigned_date ?? e.review_date, e.exam_name, e.subject, e.homework_type === 'legacy' ? null : e.homework_type, e.value, e.detail ?? e.measures].filter(v => v != null && v !== '').join(' · ')}</p>)}
                </div>)}
              </div>
            </details>
            {student.followups.map(f => <div key={f.id} className="space-y-2 rounded-lg bg-blue-50/60 p-3 text-sm">
              <p className="flex items-center gap-2 font-medium text-slate-700"><CalendarCheck className="h-4 w-4" />{f.due?'已到复查日':'跟进中'} · {f.review_date ?? '尚未设复查日'}</p>
              <p className="break-words text-xs text-slate-600">措施：{f.measures ?? '—'}</p>
              <details><summary className="cursor-pointer text-xs text-brand-700">查看基线与当前情况</summary>
                <div className="mt-2 grid gap-3 sm:grid-cols-2">
                  <div><p className="mb-1 text-xs font-medium">建档时</p><Snapshot value={f.baseline} /></div>
                  <div><p className="mb-1 text-xs font-medium">当前</p><Snapshot value={f.current} /></div>
                </div><p className="mt-2 text-xs text-slate-500">{f.note}</p>
              </details>
              <Button size="sm" variant="outline" onClick={() => startAction(student,f)}>记录复查并完成</Button>
            </div>)}
            {!student.followups.length && student.active ? <Button size="sm" variant="outline" onClick={() => startAction(student)}>加入跟进</Button> : null}
          </li>)}</ul>}
        <MoreToggle expanded={expanded} hiddenCount={rows.length-6} unit="人" onToggle={() => setExpanded(v => !v)} />
        {data.gaps.length ? <details className="rounded-lg bg-amber-50/70 p-3 text-sm">
          <summary className="cursor-pointer text-amber-900">数据待补齐或继续积累 · {data.gaps.length} 人</summary>
          <p className="mt-2 text-xs text-slate-600">这些缺口不作为学生风险。高一需要年级百分位，高二、高三需要等级分；只有一场考试时先观察，不判断持续变化。</p>
          <ul className="mt-2 max-h-48 space-y-1 overflow-y-auto text-xs">{data.gaps.map(g => <li key={g.person_id}>
            {g.name ?? '未命名学生'} · {g.class_labels.join('、')} · {g.reason==='one_exam'?'只有一场考试':g.reason==='no_exams'?'暂无同科成绩':personalReason(g.reason)}
          </li>)}</ul>
          <Link className="mt-2 inline-block text-xs text-brand-700" href="/upload?ws=teaching">前往成绩上传或补录 →</Link>
        </details> : null}
        <p className="text-xs text-slate-400">{data.note} <Link className="text-brand-700" href="/teaching/homework?tab=overview">查看作业概况 →</Link></p>
      </>}
    </CardContent>}
    <Dialog open={configOpen} onOpenChange={value => { if (!saving) setConfigOpen(value) }}>
      <DialogContent className="max-h-[90vh] w-[calc(100%-2rem)] overflow-y-auto"><DialogHeader>
        <DialogTitle>关注规则</DialogTitle><DialogDescription>高一年级百分位，高二、高三等级分；不以原始分补造。</DialogDescription>
      </DialogHeader>
        {actionError ? <p role="alert" className="text-sm text-red-700">{actionError}</p> : null}
        {data ? <SettingsForm initial={data.settings} saving={saving} onSave={value => void perform(() => saveAttentionSettings(value,scopeQuery))} /> : null}
      </DialogContent>
    </Dialog>
    <Dialog open={action != null} onOpenChange={value => { if (!value && !saving) setAction(null) }}>
      <DialogContent className="max-h-[90vh] w-[calc(100%-2rem)] overflow-y-auto"><DialogHeader>
        <DialogTitle>{action?.followup?'记录复查并完成':'加入跟进'} · {action?.student.name}</DialogTitle>
        <DialogDescription>{action?.followup?'记录本次观察后关闭事项，原始基线和跟进历史保留。':'保存当前关注依据与基线，再约定一次复查。'}</DialogDescription>
      </DialogHeader>
        {actionError ? <p role="alert" className="text-sm text-red-700">{actionError}</p> : null}
        <form className="space-y-4" onSubmit={e => { e.preventDefault(); if (!action) return
          void perform(() => action.followup ? finishAttentionFollowUp(action.followup.id,measures,scopeQuery) :
            createAttentionFollowUp(action.student.person_id,{ evidence_key:action.student.evidence_key, measures, review_date:reviewDate },scopeQuery))
        }}>
          <label className="block space-y-1 text-sm"><span>{action?.followup?'复查观察':'跟进措施'}</span>
            <textarea className={fieldClass} rows={4} required maxLength={2000} value={measures} onChange={e => setMeasures(e.target.value)} /></label>
          {!action?.followup ? <label className="block space-y-1 text-sm"><span>复查日期</span>
            <Input type="date" required min={data?.as_of} value={reviewDate} onChange={e => setReviewDate(e.target.value)} /></label> : null}
          <Button type="submit" disabled={saving || !measures.trim()}>{saving?'保存中…':action?.followup?'保存复查并完成':'保存跟进'}</Button>
        </form>
      </DialogContent>
    </Dialog>
  </Card>
}
