'use client'

import { useMemo } from 'react'
import { CheckCircle2, CornerDownRight, TriangleAlert } from 'lucide-react'

import { cn } from '@/lib/utils'

export type EntryMode = 'smart' | 'by_student' | 'by_subject'

export interface HomeworkSubmitFeedback {
  tone: 'success' | 'partial' | 'error'
  title: string
  details?: string[]
}

type PreviewLine = {
  source: string
  valid: boolean
  left: string
  right: string
  /** smart 模式的无冒号行：整行交给后端按「姓名+动作」识别，不判格式错 */
  smartGuess?: boolean
}

interface HomeworkEntryPreviewProps {
  raw: string
  mode: EntryMode
}

/** 输入时即时的格式解析预览：按中英冒号切分逐行展示，缺冒号的行给警示（smart 模式除外）。 */
export function HomeworkEntryPreview({ raw, mode }: HomeworkEntryPreviewProps) {
  const preview = useMemo(
    () =>
      raw
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line): PreviewLine => {
          const separator = line.search(/[:：]/)
          if (separator >= 1) {
            return {
              source: line,
              valid: true,
              left: line.slice(0, separator).trim(),
              right: line.slice(separator + 1).trim(),
            }
          }
          return mode === 'smart'
            ? { source: line, valid: true, left: line, right: '', smartGuess: true }
            : { source: line, valid: false, left: line, right: '' }
        }),
    [raw, mode]
  )
  const invalidCount = preview.filter((line) => !line.valid).length

  if (preview.length === 0) return null

  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-3" aria-label="录入解析预览">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-semibold text-slate-900">解析预览 · {preview.length} 行</p>
        {mode === 'smart' ? (
          <p className="text-xs text-slate-500">点「解析并预览」核对学生匹配与作业种类</p>
        ) : (
          <p className={cn('text-xs', invalidCount ? 'text-warning-700' : 'text-success-600')}>
            {invalidCount ? `${invalidCount} 行缺少冒号，将由后端返回具体错误` : '格式检查通过'}
          </p>
        )}
      </div>
      <div className="max-h-40 space-y-1.5 overflow-y-auto">
        {preview.map((line, index) => (
          <div
            key={`${line.source}-${index}`}
            className={cn(
              'flex min-h-9 items-start gap-2 rounded-md border bg-white px-2.5 py-1.5 text-xs',
              line.valid ? 'border-slate-200' : 'border-warning-500/40'
            )}
          >
            <CornerDownRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" />
            <span className="font-medium text-slate-900">{line.left}</span>
            {line.smartGuess ? (
              <span className="text-slate-400">智能识别「姓名+动作」</span>
            ) : (
              line.valid && <span className="text-slate-500">→ {line.right || '（空）'}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

interface SubmitFeedbackProps {
  feedback: HomeworkSubmitFeedback
}

/** 录入提交后的三态反馈卡：全部成功绿、部分成功橙、全部失败红，错误明细逐条列出。 */
export function SubmitFeedback({ feedback }: SubmitFeedbackProps) {
  return (
    <div
      role={feedback.tone === 'error' ? 'alert' : 'status'}
      className={cn(
        'rounded-lg border px-3 py-2.5 text-sm',
        feedback.tone === 'success' && 'border-success-500/30 bg-success-50 text-success-600',
        feedback.tone === 'partial' && 'border-warning-500/30 bg-warning-50 text-warning-700',
        feedback.tone === 'error' && 'border-danger-500/30 bg-danger-50 text-danger-600'
      )}
    >
      <div className="flex items-start gap-2 font-medium">
        {feedback.tone === 'success' ? (
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
        ) : (
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
        )}
        <span>{feedback.title}</span>
      </div>
      {feedback.details && feedback.details.length > 0 && (
        <ul className="mt-1.5 space-y-1 pl-6 text-xs">
          {feedback.details.map((detail, index) => (
            <li key={`${detail}-${index}`}>{detail}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
