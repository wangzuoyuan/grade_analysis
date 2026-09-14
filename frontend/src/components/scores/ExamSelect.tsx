'use client'

/**
 * 考试选择下拉（两成绩页共用，契约 p3 §1.4）。
 * 选项按考试日期降序，展示「考试名（日期）」；空清单时提示暂无已导入考试。
 */

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { ExamSummary } from '@/lib/api-v1'
import { examOptionLabel } from './shared'

export function ExamSelect({
  exams,
  value,
  onChange,
  loading = false,
}: {
  exams: ExamSummary[]
  value: string | null
  onChange: (examName: string) => void
  loading?: boolean
}) {
  return (
    <Select value={value ?? undefined} onValueChange={onChange}>
      <SelectTrigger className="h-8 w-[260px] max-w-full text-xs" aria-label="选择考试">
        <SelectValue placeholder={loading ? '考试加载中…' : '选择考试'} />
      </SelectTrigger>
      <SelectContent>
        {exams.length === 0 ? (
          <div className="px-3 py-2 text-xs text-slate-400">当前范围暂无已导入考试</div>
        ) : null}
        {exams.map((e) => (
          <SelectItem key={`${e.exam_name}|${e.exam_date ?? ''}`} value={e.exam_name}>
            {examOptionLabel(e)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
