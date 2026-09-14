'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { AlertTriangle, ArrowRight, ClipboardList, NotebookPen, UserCheck } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

interface HomeworkOverview {
  scope: { label: string; member_count: number }
  date_range: { start: string; end: string }
  kpi: { total_misses: number }
  warnings: {
    streak: { counts: { serious: number; warning: number } }
  }
  honors: { full_attendance: unknown[] }
}

export default function HomeworkOverviewCard({ teachingClassId }: { teachingClassId?: number }) {
  const [data, setData] = useState<HomeworkOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(false)
    setData(null)
    const params = new URLSearchParams({ group_by: 'week' })
    if (teachingClassId != null) params.set('teaching_class_id', String(teachingClassId))

    fetch(`/api/homework/dashboard?${params}`)
      .then((response) => {
        if (!response.ok) throw new Error('作业看板加载失败')
        return response.json() as Promise<HomeworkOverview>
      })
      .then((payload) => {
        if (!cancelled) setData(payload)
      })
      .catch(() => {
        if (!cancelled) {
          setData(null)
          setError(true)
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [teachingClassId])

  const metrics = [
    { label: '区间缺交', value: data?.kpi.total_misses ?? 0, icon: ClipboardList, tone: 'text-slate-900' },
    { label: '红色预警', value: data?.warnings.streak.counts.serious ?? 0, icon: AlertTriangle, tone: 'text-danger-600' },
    { label: '黄色预警', value: data?.warnings.streak.counts.warning ?? 0, icon: AlertTriangle, tone: 'text-warning-600' },
    { label: '全勤之星', value: data?.honors.full_attendance.length ?? 0, icon: UserCheck, tone: 'text-success-600' },
  ]

  return (
    <Card data-testid="homework-overview">
      <CardHeader className="flex-row items-start justify-between gap-4 space-y-0">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <NotebookPen className="h-4 w-4 text-brand-600" />
            作业看板
          </CardTitle>
          <CardDescription className="mt-1">
            {data
              ? `${data.scope.label} · ${data.scope.member_count} 名学生 · ${data.date_range.start} 至 ${data.date_range.end}`
              : error
                ? '作业摘要暂时无法加载，可进入完整看板重试'
                : '加载当前班级范围的作业摘要'}
          </CardDescription>
        </div>
        <Button asChild variant="outline" size="sm">
          <Link href="/homework">
            进入作业看板
            <ArrowRight className="h-4 w-4" />
          </Link>
        </Button>
      </CardHeader>
      <CardContent>
        {error ? (
          <div className="rounded-lg border border-danger-100 bg-danger-50 p-4 text-sm text-danger-600">
            无法读取作业摘要，请进入完整作业看板重试。
          </div>
        ) : <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {metrics.map(({ label, value, icon: Icon, tone }) => (
            <div key={label} className="rounded-lg border border-slate-100 bg-slate-50/60 p-3">
              <div className="flex items-center gap-1.5 text-xs text-slate-500">
                <Icon className="h-3.5 w-3.5" />
                {label}
              </div>
              {loading ? (
                <Skeleton className="mt-2 h-7 w-12" />
              ) : (
                <div className={`mt-1 text-2xl font-semibold tabular-nums ${tone}`}>{value}</div>
              )}
            </div>
          ))}
        </div>}
      </CardContent>
    </Card>
  )
}
