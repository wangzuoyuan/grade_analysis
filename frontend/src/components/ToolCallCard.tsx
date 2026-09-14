'use client'

import { useState } from 'react'
import { Wrench, ChevronRight } from 'lucide-react'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface ToolCall {
  name: string
  input: Record<string, any>
  output?: any
  error?: string
}

interface ToolCallCardProps {
  toolCall: ToolCall
  collapsed?: boolean
}

type Status = 'running' | 'success' | 'error'

function getStatus(toolCall: ToolCall): Status {
  if (toolCall.error) return 'error'
  if (toolCall.output !== undefined) return 'success'
  return 'running'
}

const STATUS_LABEL: Record<Status, string> = {
  running: '运行中',
  success: '成功',
  error: '失败',
}

const STATUS_VARIANT: Record<Status, 'warning' | 'success' | 'destructive'> = {
  running: 'warning',
  success: 'success',
  error: 'destructive',
}

export default function ToolCallCard({ toolCall, collapsed = true }: ToolCallCardProps) {
  const [isCollapsed, setIsCollapsed] = useState(collapsed)
  const status = getStatus(toolCall)

  return (
    <Card className="overflow-hidden py-2 px-3">
      <button
        type="button"
        onClick={() => setIsCollapsed(!isCollapsed)}
        className="flex w-full items-center justify-between gap-2 text-left"
      >
        <div className="flex min-w-0 items-center gap-2">
          <Wrench className="h-3.5 w-3.5 shrink-0 text-slate-500" />
          <span className="truncate font-mono text-xs text-slate-700">
            {toolCall.name}
          </span>
          <Badge variant={STATUS_VARIANT[status]} className="shrink-0">
            {STATUS_LABEL[status]}
          </Badge>
        </div>
        <ChevronRight
          className={cn(
            'h-4 w-4 shrink-0 text-slate-400 transition-transform',
            !isCollapsed && 'rotate-90'
          )}
        />
      </button>

      {!isCollapsed && (
        <div className="mt-2 space-y-2">
          {Object.keys(toolCall.input || {}).length > 0 && (
            <div>
              <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-slate-400">
                参数
              </div>
              <pre className="overflow-auto rounded bg-slate-50 p-2 text-xs text-slate-700">
                {JSON.stringify(toolCall.input, null, 2)}
              </pre>
            </div>
          )}
          {toolCall.error ? (
            <div>
              <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-slate-400">
                错误
              </div>
              <pre className="overflow-auto rounded bg-danger-50 p-2 text-xs text-danger-500">
                {toolCall.error}
              </pre>
            </div>
          ) : toolCall.output !== undefined ? (
            <div>
              <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-slate-400">
                结果
              </div>
              <pre className="overflow-auto rounded bg-slate-50 p-2 text-xs text-slate-700">
                {typeof toolCall.output === 'string'
                  ? toolCall.output
                  : JSON.stringify(toolCall.output, null, 2)}
              </pre>
            </div>
          ) : (
            <div className="text-xs text-slate-400">运行中...</div>
          )}
        </div>
      )}
    </Card>
  )
}
