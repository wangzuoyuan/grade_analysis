'use client'
import { useEffect, useRef, useState } from 'react'
import { previewExamMerge, confirmExamMerge, type ManagedExam, type ExamMergePreview, type WorkspaceMode } from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

const fieldLabel: Record<string, string> = {score: '原始分', grade_score: '等级分', grade_percentile: '年级百分位'}
export function ExamMerge({ exams, mode, query, onComplete }: {
  exams: ManagedExam[]; mode: WorkspaceMode; query: {academic_year_id?: number; class_id?: number}; onComplete: () => void
}) {
  const [source, setSource] = useState(''), [target, setTarget] = useState('')
  const [preview, setPreview] = useState<ExamMergePreview | null>(null)
  const [name, setName] = useState(''), [accept, setAccept] = useState(false)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const generation = useRef(0)
  useEffect(() => { generation.current += 1; setPreview(null); setName(''); setAccept(false); setError('') }, [source, target])
  useEffect(() => () => { generation.current += 1 }, [])
  const choices = exams.filter(e => e.event_id != null)
  if (choices.length < 2) return null
  async function prepare() {
    const id = ++generation.current
    setBusy(true); setError('')
    try {
      const p = await previewExamMerge(mode, {academic_year_id: query.academic_year_id, class_id: query.class_id, source_event_id: Number(source), target_event_id: Number(target)})
      if (id === generation.current) setPreview(p)
    } catch (e) { if (id === generation.current) setError(apiErrorMessage(e)) }
    finally { if (id === generation.current) setBusy(false) }
  }
  async function confirm() {
    if (!preview) return
    const id = ++generation.current
    setBusy(true); setError('')
    try {
      await confirmExamMerge(mode, {token: preview.token, confirm_name: name, accept_conflicts: accept})
      if (id === generation.current) onComplete()
    } catch (e) { if (id === generation.current) setError(apiErrorMessage(e)) }
    finally { if (id === generation.current) setBusy(false) }
  }
  return <details className="rounded-lg border border-sky-100 p-3 text-sm">
    <summary className="cursor-pointer font-medium text-sky-800">关联历史重复考试</summary>
    <div className="mt-3 space-y-3">
      <p className="text-slate-500">确认这两条记录属于同一场考试后，可合并展示归属。先核对日期、年级及来源；来源成绩继续分别保留。</p>
      <div className="grid gap-3 sm:grid-cols-2">{(['来源考试', '归入考试'] as const).map((label, i) => <label key={label}>{label}
        <select aria-label={label} className="mt-1 w-full rounded border bg-white py-2 pl-2" disabled={busy} value={i ? target : source} onChange={e => { if (i) setTarget(e.target.value); else { setSource(e.target.value); setTarget('') } }}>
          <option value="">请选择</option>{choices.filter(e => !i || !source || (e.grade === choices.find(c => String(c.event_id) === source)?.grade && String(e.event_id) !== source)).map(e => <option key={e.key} value={e.event_id!}>高{e.grade ?? '未知'} · {e.exam_name} · {e.dates.join(' / ') || '日期未注明'} · {e.source_label}</option>)}
        </select><span className="mt-1 block break-words text-xs text-slate-500">{choices.find(e => String(e.event_id) === (i ? target : source))?.dates.join(' / ')}</span></label>)}</div>
      <Button variant="outline" disabled={busy || !source || !target || source === target} onClick={prepare}>预览关联影响</Button>
      {preview && <div className="space-y-3 rounded-lg bg-sky-50 p-3">
        <p className="font-medium">{preview.source.exam_name} → {preview.target.exam_name}</p>
        <p>{preview.message}</p>
        <p>涉及来源成绩 {preview.source.score_count + preview.target.score_count} 条，来源班级：{[...new Set([...preview.source.classes, ...preview.target.classes])].join('、')}</p>
        <p>待核对字段差异 {preview.differences.length} 组</p>
        {preview.differences.length > 0 && <div className="max-h-64 overflow-auto"><table className="w-full text-left text-xs"><thead><tr><th>学科 / 学生</th><th>字段</th><th>来源一</th><th>来源二</th></tr></thead><tbody>{preview.differences.flatMap((d, i) => Object.entries(d.fields).map(([field, values]) => <tr key={`${i}:${field}`}><td className="py-2">{d.subject} · {d.name ?? '未命名学生'}</td><td>{fieldLabel[field] ?? field}</td><td>{values.own ?? '—'}</td><td>{values.other ?? '—'}</td></tr>))}</tbody></table></div>}
        {preview.differences.length > 0 && <label className="flex gap-2"><input type="checkbox" checked={accept} disabled={busy} onChange={e => setAccept(e.target.checked)}/>我已核对差异，关联后保留全部来源值，稍后分别处理</label>}
        <label className="block">输入归入考试完整名称确认<Input aria-label="关联考试确认名称" value={name} onChange={e => setName(e.target.value)} disabled={busy}/></label>
        <Button disabled={busy || name !== preview.target.exam_name || (preview.differences.length > 0 && !accept)} onClick={confirm}>确认关联考试</Button>
      </div>}
      {error && <p role="alert" className="text-rose-700">{error}</p>}
    </div>
  </details>
}
