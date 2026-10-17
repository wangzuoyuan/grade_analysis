'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { AlertCircle, CalendarDays, RefreshCw, Trash2, Upload } from 'lucide-react'
import { listManagedExams, previewExamDelete, confirmExamDelete, type ManagedExam, type ExamDeletePreview, type WorkspaceMode } from '@/lib/api-v1'
import { useWorkspace } from '@/lib/workspace'
import { apiErrorMessage } from '@/components/link/error-text'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { MetricFill } from './MetricFill'
import { ExamMerge } from './ExamMerge'
import { ScoreYearPicker } from './ScoreYearPicker'

function ExamRow({ exam, pending, onDelete, mode }: { exam: ManagedExam; pending: boolean; onDelete: (exam: ManagedExam) => void; mode: WorkspaceMode }) {
  return <article className="flex min-w-0 flex-col gap-3 rounded-xl border border-sky-100 bg-white p-4 sm:flex-row sm:items-center sm:justify-between" data-testid="managed-exam">
    <div className="min-w-0 space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="break-words text-base font-semibold text-slate-800">{exam.exam_name}</h3>
        <span className={`rounded px-2 py-0.5 text-xs ${exam.can_delete ? 'bg-sky-50 text-sky-700' : 'bg-slate-100 text-slate-500'}`}>{exam.source_label}</span>
      </div>
      <p className="text-xs text-slate-500">{exam.grade ? `高${['一', '二', '三'][exam.grade - 1]}` : '年级未注明'}{exam.subject ? ` · ${exam.subject}` : ''} · {exam.dates.join(' / ') || '日期未注明'}</p>
      <p className="break-words text-xs text-slate-500">{exam.classes.length ? exam.classes.join('、') : '全年级统计参照'}</p>
      <p className="text-xs tabular-nums text-slate-600">本域学生 {exam.student_count} 人 · 成绩 {exam.score_count} 条 · 班级均分 {exam.average_count} 行 · 官方质量 {exam.quality_count} 行{exam.projected_students ? ` · 另有 ${exam.projected_students} 位学生的关联成绩` : ''}</p>
      <details className="text-xs text-slate-600"><summary>查看来源与字段差异</summary><p className="mt-2">来源：{exam.sources?.map(s => s === 'homeroom' ? '班主任工作台' : '教学工作台').join('、') || exam.source_label} · 字段差异 {exam.conflicts?.length ?? 0} 组</p>{exam.conflicts?.map((c, i) => <div key={i}><p className="mt-2 font-medium">{c.name ?? '未命名学生'}</p><p>{Object.entries(c.fields).map(([f, v]) => `${({score:'原始分',grade_score:'等级分',grade_percentile:'年级百分位'} as Record<string,string>)[f] ?? f}：本域 ${v.own ?? '—'} / 关联 ${v.other ?? '—'}`).join('；')}</p>{c.fact_id && !c.fields.score && Object.entries(c.fields).some(([field, value]) => field !== 'score' && value.own == null && value.other != null) ? <MetricFill factId={c.fact_id} mode={mode}/> : null}</div>)}</details>
      {!exam.can_delete && <p className="text-xs text-slate-500">来自{mode === 'homeroom' ? '教学' : '班主任'}工作台，请到来源工作台管理。</p>}
    </div>
    <div className="flex shrink-0 items-center gap-2">
      {exam.platform_exam_id != null && <Button asChild variant="outline" size="sm"><Link href={`/compare?ws=teaching&exam_id=${exam.platform_exam_id}&grade=${exam.grade}&academic_year_id=${exam.academic_year_id}`}>查看对比</Link></Button>}
      <Button variant="outline" size="sm" className="text-rose-600 hover:text-rose-700" disabled={!exam.can_delete || pending} onClick={() => onDelete(exam)} aria-label={`删除考试${exam.exam_name}`}><Trash2 className="h-4 w-4" />删除</Button>
    </div>
  </article>
}

export function ExamManagement({ mode }: { mode: WorkspaceMode }) {
  const { filter, generation, refreshScope } = useWorkspace()
  const [grade, setGrade] = useState('')
  const [search, setSearch] = useState('')
  const [nonce, setNonce] = useState(0)
  const [result, setResult] = useState<{key: string; exams: ManagedExam[]} | null>(null)
  const [error, setError] = useState<{key: string; message: string} | null>(null)
  const [notice, setNotice] = useState('')
  const [preview, setPreview] = useState<{key: string; data: ExamDeletePreview} | null>(null)
  const [preparing, setPreparing] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmName, setConfirmName] = useState('')
  const [deleteError, setDeleteError] = useState('')
  const query = useMemo(() => ({ academic_year_id: filter.academic_year_id,
    class_id: mode === 'homeroom' ? filter.class_id : undefined,
    grade: mode === 'teaching' && grade ? Number(grade) : undefined,
  }), [mode, filter.academic_year_id, filter.class_id, grade])
  const key = JSON.stringify([mode, query, generation, nonce])
  const currentKey = useRef(key)
  currentKey.current = key
  useEffect(() => {
    let cancelled = false
    setResult(null)
    setError(null)
    setPreview(null)
    setConfirmName('')
    setDeleteError('')
    listManagedExams(mode, query).then(data => { if (!cancelled) setResult({ key, exams: data.exams }) })
      .catch((err: unknown) => { if (!cancelled) setError({ key, message: apiErrorMessage(err) }) })
    return () => { cancelled = true }
  }, [mode, query, key])
  const data = result?.key === key ? result.exams : null
  const visible = (data ?? []).filter(e => e.exam_name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()))
  const message = error?.key === key ? error.message : null
  const activePreview = preview?.key === key ? preview.data : null

  async function prepare(exam: ManagedExam) {
    const requestKey = key
    setPreparing(true)
    setDeleteError('')
    setNotice('')
    try {
      const data = await previewExamDelete(mode, { academic_year_id: query.academic_year_id, class_id: query.class_id, exam_key: exam.key })
      if (currentKey.current === requestKey) { setPreview({ key: requestKey, data }); setConfirmName('') }
    } catch (err) {
      if (currentKey.current === requestKey) setError({ key: requestKey, message: apiErrorMessage(err) })
    } finally { setPreparing(false) }
  }

  async function remove() {
    if (!activePreview || deleting) return
    const requestKey = key
    const selected = activePreview
    setDeleting(true)
    setDeleteError('')
    try {
      await confirmExamDelete(mode, { token: selected.token, confirm_name: confirmName })
      if (currentKey.current === requestKey) {
        setPreview(null)
        setNotice(`已删除“${selected.exam.exam_name}”的本工作台数据。学生名册、作业、档案和另一工作台的数据均已保留；关联来源仍可只读查看。`)
      }
      refreshScope()
    } catch (err) {
      if (currentKey.current === requestKey) setDeleteError(apiErrorMessage(err))
    } finally { setDeleting(false) }
  }

  return <div className="min-w-0 space-y-5" data-testid="exam-management">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h1 className="flex items-center gap-2 text-xl font-semibold text-slate-800"><CalendarDays className="h-5 w-5 text-sky-600" />考试管理</h1><p className="mt-1 text-sm text-slate-500">{mode === 'homeroom' ? '管理本行政班的考试成绩与全年级均分参照。' : '按学年、年级管理本人全部教学班的整场考试，包含该场官方质量表；这里不使用成绩页的单班筛选。'}</p></div>
      <Button asChild variant="outline"><Link href={`/upload?ws=${mode}`}><Upload className="h-4 w-4" />导入考试</Link></Button>
    </div>
    <Card><CardHeader><CardTitle>考试列表</CardTitle><CardDescription>删除前可查看实际影响范围。关联来源只读，需要在来源工作台删除。</CardDescription></CardHeader>
      <CardContent className="min-w-0 space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <ScoreYearPicker />
          {mode === 'teaching' && <label className="space-y-1 text-xs text-slate-500"><span className="block">年级</span><select aria-label="考试管理年级" value={grade} onChange={e => { setGrade(e.target.value); setNotice('') }} className="h-9 w-40 rounded-md border border-slate-200 bg-white py-1 pl-2 text-sm"><option value="">全部年级</option><option value="1">高一</option><option value="2">高二</option><option value="3">高三</option></select></label>}
          <label className="min-w-[180px] flex-1 space-y-1 text-xs text-slate-500"><span className="block">搜索考试</span><Input aria-label="搜索考试名称" placeholder="输入考试名称" value={search} onChange={e => setSearch(e.target.value)} className="h-9 min-w-0" /></label>
          <Button variant="outline" size="sm" onClick={() => { setNonce(n => n + 1); setNotice('') }}><RefreshCw className="h-4 w-4" />刷新</Button>
        </div>
        {notice && <p role="status" className="rounded-lg border border-sky-100 bg-sky-50 p-3 text-sm text-sky-800">{notice}</p>}
        {message && <p role="alert" className="flex items-start gap-2 rounded-lg bg-rose-50 p-3 text-sm text-rose-700"><AlertCircle className="h-4 w-4 shrink-0" />{message}</p>}
        {!data && !message && <Skeleton className="h-48 w-full" />}
        {data && <p className="text-xs text-slate-500">当前范围共 {data.length} 场，显示 {visible.length} 场。</p>}
        {data && <ExamMerge key={key} exams={data} mode={mode} query={query} onComplete={() => { setNotice('考试归属已关联，来源成绩已保留。'); refreshScope() }}/>}
        {visible.map(exam => <ExamRow key={exam.key} exam={exam} pending={preparing || deleting} onDelete={prepare} mode={mode} />)}
        {data && !visible.length && <p className="rounded-lg bg-slate-50 p-6 text-center text-sm text-slate-500">{data.length ? '没有匹配的考试，请调整搜索。' : '当前范围暂无考试，可前往数据上传导入。'}</p>}
      </CardContent>
    </Card>
    <Dialog open={activePreview != null} onOpenChange={open => { if (!open && !deleting) { setPreview(null); setDeleteError('') } }}>
      <DialogContent className="max-h-[90vh] max-w-[calc(100%-2rem)] overflow-y-auto sm:max-w-lg">
        <DialogHeader><DialogTitle>删除考试</DialogTitle><DialogDescription>确认后移除下列本工作台数据，页面不提供撤销。请核对考试与范围。</DialogDescription></DialogHeader>
        {activePreview && <>
          <div className="space-y-2 rounded-lg bg-slate-50 p-3 text-sm text-slate-700">
            <p className="break-words font-semibold">{activePreview.exam.exam_name}</p>
            <p>{activePreview.exam.grade ? `高${['一', '二', '三'][activePreview.exam.grade - 1]}` : '年级未注明'} · {activePreview.exam.subject || '全科 / 总分'} · {activePreview.exam.dates.join(' / ') || '日期未注明'}</p>
            <p className="break-words">涉及班级：{activePreview.exam.classes.join('、') || '全年级统计参照'}</p>
            <p>{activePreview.exam.student_count}名学生 · {activePreview.exam.score_count}条成绩 · {activePreview.exam.average_count}行班级均分 · {activePreview.exam.quality_count}行官方质量</p>
            {!!activePreview.pending_imports && <p>{activePreview.pending_imports}个相关待确认导入将失效，后续导入需重新预览。</p>}
          </div>
          <p className="text-xs leading-relaxed text-slate-500">学生名册、作业与档案保留。另一工作台的数据保留；若有共享成绩，删除后仍可能以关联来源显示。</p>
          <label htmlFor="delete-exam-name" className="space-y-2 text-sm text-slate-700"><span className="block">输入完整考试名称确认</span><Input id="delete-exam-name" aria-label="输入完整考试名称确认" autoComplete="off" value={confirmName} onChange={e => setConfirmName(e.target.value)} disabled={deleting} /></label>
          {deleteError && <p role="alert" className="text-sm text-rose-700">{deleteError}。请关闭弹窗、刷新列表后重新预览。</p>}
          <DialogFooter><Button variant="outline" disabled={deleting} onClick={() => setPreview(null)}>取消</Button><Button variant="destructive" disabled={deleting || confirmName !== activePreview.exam.exam_name || !!deleteError} onClick={remove}>{deleting ? '正在删除…' : '确认删除考试'}</Button></DialogFooter>
        </>}
      </DialogContent>
    </Dialog>
  </div>
}
