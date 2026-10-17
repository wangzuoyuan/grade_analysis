'use client'
import type { ExamResolution } from '@/lib/api-v1'

export function ExamResolutionSelect({ resolution, value, onChange, disabled = false }: {
  resolution?: ExamResolution; value?: number | string
  onChange: (value: number | 'new') => void; disabled?: boolean
}) {
  if (!resolution) return null
  const selection = value ?? resolution.selected ?? (resolution.requires_choice ? '' : 'new')
  return <div className="space-y-2 rounded-lg border border-sky-200 bg-sky-50/50 p-3 text-sm">
    <p className="font-medium text-sky-900">{resolution.message}</p>
    <label className="block text-slate-600">考试归属
      <select aria-label="考试归属" className="mt-1 w-full rounded border bg-white py-2 pl-2" value={selection}
        disabled={disabled} onChange={e => onChange(e.target.value === 'new' ? 'new' : Number(e.target.value))}>
        <option value="" disabled>请选择已有考试或另建考试</option>
        {resolution.candidates.map(e => <option key={e.id} value={e.id}>{e.exam_name} · {e.dates.join(' / ') || '日期未注明'} · {e.sources.map(s => s === 'homeroom' ? '班主任' : '教学').join('、')}</option>)}
        <option value="new">另建一场考试</option>
      </select>
    </label>
    {resolution.candidates.filter(e => String(e.id) === String(selection)).map(e => <p key={e.id} className="break-words text-xs text-slate-600">{e.exam_name} · {e.dates.join(' / ') || '日期未注明'} · {e.sources.map(s => s === 'homeroom' ? '班主任' : '教学').join('、')}</p>)}
    <p className="text-xs text-slate-500">确认导入后保存。选择已有考试会合并展示归属，各工作台的成绩与删除范围仍独立保留。</p>
  </div>
}
