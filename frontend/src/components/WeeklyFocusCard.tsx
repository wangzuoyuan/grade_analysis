'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { Bell, CheckCircle2, RefreshCw } from 'lucide-react'

import {
  fetchHomeroomWeeklyFocus,
  withQuery,
  type AnalysisScopeQuery,
  type HomeroomWeeklyFocusResponse,
  type QueryValue,
} from '@/lib/api-v1'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

interface WeeklyFocus extends HomeroomWeeklyFocusResponse {}

function reasonStyle(reason: string): string {
  if (reason.startsWith('连续缺交')) {
    return 'border-rose-200 bg-rose-50 text-rose-700 font-medium'
  }
  if (reason.startsWith('本周缺交激增')) {
    return 'border-amber-200 bg-amber-50 text-amber-800 font-medium'
  }
  if (reason.startsWith('谈话跟进')) {
    return 'border-blue-200 bg-blue-50 text-blue-800 font-medium'
  }
  if (reason.includes('严重偏科')) {
    return 'border-purple-200 bg-purple-50 text-purple-800'
  }
  if (reason.includes('临界') || reason.includes('薄弱')) {
    return 'border-amber-200 bg-amber-50/70 text-amber-900'
  }
  return 'border-slate-200 bg-slate-100 text-slate-700'
}

export default function WeeklyFocusCard({
  scopeQuery = {},
}: {
  scopeQuery?: AnalysisScopeQuery
}) {
  const [data, setData] = useState<HomeroomWeeklyFocusResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(null)

    // 优先调用 v1 作用域端点并校验 students 数组（契约安全底线）
    const url = withQuery('/api/v1/homeroom/analysis/weekly-focus', scopeQuery as Record<string, QueryValue>)
    fetch(url)
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const payload: unknown = await r.json()
        if (
          !payload ||
          typeof payload !== 'object' ||
          !Array.isArray((payload as WeeklyFocus).students)
        ) {
          throw new Error('Invalid weekly focus response')
        }
        return payload as WeeklyFocus
      })
      .then((res) => {
        if (!active) return
        setData(res)
      })
      .catch((err: unknown) => {
        if (!active) return
        setError(err instanceof Error ? err.message : '本周关注加载失败')
      })
      .finally(() => {
        if (active) setLoading(false)
      })

    return () => {
      active = false
    }
  }, [
    scopeQuery.academic_year_id,
    scopeQuery.class_id,
    scopeQuery.term_id,
    reloadKey,
  ])

  const students = data?.students ?? []
  const weekLabel = data?.week
    ? `${data.week.start.slice(5)}—${data.week.end.slice(5)}`
    : ''

  return (
    <Card className="border-amber-400/40 bg-gradient-to-b from-amber-50/20 via-white to-white shadow-sm">
      <CardHeader className="flex-row items-start justify-between space-y-0 pb-3">
        <div>
          <CardTitle className="flex items-center gap-2 text-base font-semibold text-slate-900">
            <Bell className="h-4 w-4 text-amber-500" />
            本周关注
            {!loading && !error && (
              <Badge
                variant="outline"
                className="border-amber-300 bg-amber-100/70 text-amber-800 text-xs font-semibold px-2 py-0.5"
              >
                {students.length} 人
              </Badge>
            )}
          </CardTitle>
          <CardDescription className="mt-1 text-xs text-slate-500">
            合并成绩预警、连续缺交与待跟进事项
          </CardDescription>
        </div>
        {weekLabel ? (
          <span className="text-xs text-slate-400 tabular-nums">{weekLabel}</span>
        ) : null}
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="space-y-2.5 py-1" aria-label="正在加载本周关注">
            {Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-10 w-full rounded-md" />
            ))}
          </div>
        ) : error ? (
          <div className="flex min-h-28 flex-col items-center justify-center gap-2 text-center">
            <p className="text-xs font-medium text-slate-500">本周关注读取受阻：{error}</p>
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              onClick={() => setReloadKey((v) => v + 1)}
            >
              <RefreshCw className="mr-1 h-3.5 w-3.5" />
              重试
            </Button>
          </div>
        ) : students.length === 0 ? (
          <div className="flex min-h-28 flex-col items-center justify-center gap-1.5 text-center text-slate-500">
            <CheckCircle2 className="h-7 w-7 text-emerald-500" />
            <p className="text-sm font-medium text-slate-700">本周暂无重点关注</p>
            <p className="text-xs text-slate-400">系统仍会每日自动排查缺交预警、考试偏科与谈话跟进待办。</p>
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {students.slice(0, 8).map((student) => (
              <div
                key={student.student_id}
                className="flex flex-col justify-center gap-1.5 py-2.5 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between"
              >
                <Link
                  href={`/homeroom/profile?person_id=${encodeURIComponent(student.student_id)}`}
                  className="shrink-0 text-sm font-medium text-slate-800 hover:text-brand-600 hover:underline sm:w-28"
                >
                  {student.name}
                </Link>
                <div className="flex flex-wrap gap-1.5 sm:justify-end">
                  {student.reasons.map((reason, idx) => (
                    <span
                      key={`${reason}-${idx}`}
                      className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs ${reasonStyle(
                        reason,
                      )}`}
                    >
                      {reason}
                    </span>
                  ))}
                </div>
              </div>
            ))}
            {students.length > 8 && (
              <p className="pt-3 text-center text-xs text-slate-400">
                另有 {students.length - 8} 人需关注
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
