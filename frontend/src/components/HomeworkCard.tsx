'use client'

import { useEffect, useState } from 'react'
import { NotebookPen } from 'lucide-react'

import {
  homeworkStudentEvents,
  type HomeworkScopeQuery,
  type HomeworkStudentResponse,
  type WorkspaceMode,
} from '@/lib/api-v1'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'

const STATUS_LABEL: Record<string, string> = {
  missing: '缺交',
  excused: '请假',
  late: '迟到',
}

/** 学生作业缺交卡（P8-UXFIX：改接 /api/v1/homework/students/{person_id} 事件流 + streaks）。 */
export default function HomeworkCard({
  mode,
  personId,
  scopeQ = {},
}: {
  mode: WorkspaceMode
  personId: number
  scopeQ?: HomeworkScopeQuery
}) {
  const [data, setData] = useState<HomeworkStudentResponse | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let cancelled = false
    homeworkStudentEvents(personId, mode, scopeQ)
      .then((r) => {
        if (!cancelled) setData(r)
      })
      .catch(() => {
        if (!cancelled) setData(null)
      })
      .finally(() => {
        if (!cancelled) setLoaded(true)
      })
    return () => {
      cancelled = true
    }
  }, [personId, mode, scopeQ])

  if (loaded && (!data || !data.events || data.events.length === 0)) return null

  const events = data?.events ?? []
  const missing = events.filter((e) => e.status === 'missing')
  const excused = events.filter((e) => e.status === 'excused')
  const byType = new Map<string, number>()
  for (const e of missing) {
    byType.set(e.homework_type, (byType.get(e.homework_type) ?? 0) + 1)
  }
  const streaks = data?.streaks
  const streakUnknown = streaks == null || streaks.current_missing_streak == null

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <NotebookPen className="h-4 w-4" />
          作业缺交记录
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
          <div>
            <span className="text-3xl font-semibold text-slate-900">{missing.length}</span>
            <span className="ml-1 text-sm text-slate-500">次缺交</span>
          </div>
          {excused.length > 0 && (
            <div className="text-sm text-slate-500">请假 {excused.length} 次</div>
          )}
          <div className="text-sm text-slate-500">
            {streakUnknown ? (
              <Badge className="border-transparent bg-slate-100 text-slate-500">连续性未知</Badge>
            ) : (
              <>
                当前连续缺交 <span className="font-medium text-slate-800">{streaks!.current_missing_streak}</span> 次
                （最长 {streaks!.longest_missing_streak} 次）
              </>
            )}
          </div>
        </div>

        {byType.size > 0 ? (
          <div className="flex flex-wrap gap-2">
            {[...byType.entries()].map(([type, count]) => (
              <span key={type} className="rounded-md bg-slate-100 px-2.5 py-1 text-xs text-slate-600">
                {type} <span className="font-medium text-slate-800">{count}</span>
              </span>
            ))}
          </div>
        ) : (
          <p className="text-sm text-slate-400">暂无缺交记录</p>
        )}

        {events.length > 0 && (
          <details className="border-t border-slate-100 pt-3" open>
            <summary className="cursor-pointer text-xs font-medium text-slate-500">
              近期事件明细（{events.length} 条）
            </summary>
            <table className="mt-2 w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-400">
                  <th className="py-1 font-normal">日期</th>
                  <th className="py-1 font-normal">作业种类</th>
                  <th className="py-1 font-normal">状态</th>
                  <th className="py-1 font-normal">说明</th>
                </tr>
              </thead>
              <tbody>
                {events.slice(0, 20).map((r, i) => (
                  <tr key={`${r.assignment_id}-${i}`} className="border-t border-slate-50">
                    <td className="py-1.5 text-slate-500">{r.assigned_date}</td>
                    <td className="py-1.5 text-slate-700">{r.homework_type}</td>
                    <td className="py-1.5 text-slate-700">{STATUS_LABEL[r.status] ?? r.status}</td>
                    <td className="py-1.5 text-slate-500">{r.evaluation || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        )}

        <p className="text-xs text-slate-400">
          仅含缺交、请假等事件记录，不代表作业完成质量；按事件逐次统计，非按日折算。
        </p>
      </CardContent>
    </Card>
  )
}
