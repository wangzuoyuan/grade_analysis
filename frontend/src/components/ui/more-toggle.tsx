'use client'

/**
 * 统一的「列表底部展开/收起」折叠控件：各处列表默认条数维持现状，
 * 超出部分不再静默截断，收起态显示「还有 N {unit}{suffix} · 点击展开」，
 * 展开态显示「收起」。hiddenCount <= 0 时不渲染（含列表本身不足默认条数）。
 * print:hidden：打印/导出报告时按钮隐藏，按当前展开状态所见即所得。
 */

import { ChevronDown, ChevronUp } from 'lucide-react'

export function MoreToggle({
  hiddenCount,
  unit = '条',
  suffix = '',
  expanded,
  onToggle,
}: {
  /** 收起时被隐藏的条目数；<= 0 时控件不渲染 */
  hiddenCount: number
  /** 量词（默认「条」；也接受「条记录」「份备份」这类组合量词，由调用方拼出通顺文案） */
  unit?: string
  /** 可选后缀（如「需关注」） */
  suffix?: string
  expanded: boolean
  onToggle: () => void
}) {
  if (hiddenCount <= 0) return null
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={expanded ? '收起' : `展开其余 ${hiddenCount} 项`}
      className="print:hidden flex w-full items-center justify-center gap-1 rounded-md py-1.5 text-xs text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-700"
    >
      {expanded ? (
        <>
          收起
          <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />
        </>
      ) : (
        <>
          还有 <span className="font-semibold text-slate-600">{hiddenCount}</span> {unit}
          {suffix} · 点击展开
          <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
        </>
      )}
    </button>
  )
}
