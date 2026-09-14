'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { ChevronLeft } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useClassScope } from '@/lib/class-scope'

interface WarnItem {
  name: string
  student_id: string | null
  subject: string
  streak: number
  dates: string[]
}
interface Warnings {
  serious: WarnItem[]
  warning: WarnItem[]
  counts: { serious: number; warning: number; students: number }
}

function StreakBadge({ streak }: { streak: number }) {
  return (
    <Badge
      className={
        streak >= 3
          ? 'border-transparent bg-danger-50 text-danger-600'
          : 'border-transparent bg-warning-50 text-warning-700'
      }
    >
      连续{streak}次
    </Badge>
  )
}

function dateRange(dates: string[]) {
  if (dates.length === 0) return ''
  return `${dates[0].slice(5)} ~ ${dates[dates.length - 1].slice(5)}`
}

export default function WarningsPage() {
  const { current } = useClassScope()
  const [data, setData] = useState<Warnings | null>(null)

  useEffect(() => {
    const query = current === 'all' ? '' : `?teaching_class_id=${current}`
    fetch(`/api/homework/warnings${query}`)
      .then((r) => r.json())
      .then(setData)
      .catch(() => {})
  }, [current])

  const all = useMemo(() => [...(data?.serious || []), ...(data?.warning || [])], [data])

  // 按学生分组
  const byStudent = useMemo(() => {
    const map = new Map<string, { name: string; student_id: string | null; items: WarnItem[] }>()
    for (const w of all) {
      const key = w.student_id || w.name
      if (!map.has(key)) map.set(key, { name: w.name, student_id: w.student_id, items: [] })
      map.get(key)!.items.push(w)
    }
    return Array.from(map.values())
      .map((g) => ({ ...g, max: Math.max(...g.items.map((i) => i.streak)) }))
      .sort((a, b) => b.max - a.max)
  }, [all])

  // 按作业种类分组
  const bySubject = useMemo(() => {
    const map = new Map<string, WarnItem[]>()
    for (const w of all) {
      if (!map.has(w.subject)) map.set(w.subject, [])
      map.get(w.subject)!.push(w)
    }
    return Array.from(map.entries())
      .map(([subject, items]) => ({
        subject,
        items: items.sort((a, b) => b.streak - a.streak),
        red: items.filter((i) => i.streak >= 3).length,
      }))
      .sort((a, b) => b.red - a.red || b.items.length - a.items.length)
  }, [all])

  return (
    <div className="space-y-6">
      <Link
        href="/homework"
        className="inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900"
      >
        <ChevronLeft className="h-4 w-4" />
        返回作业看板
      </Link>

      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">连续缺交预警</h1>
        {data && (
          <div className="flex items-center gap-3 text-sm">
            <span className="text-danger-600">红 {data.counts.serious}</span>
            <span className="text-warning-600">黄 {data.counts.warning}</span>
            <span className="text-slate-400">{data.counts.students} 人</span>
          </div>
        )}
      </div>

      <p className="text-xs text-slate-400">
        口径：某类作业从最近一次收交向前回溯，连续缺交 2 次为黄、≥3 次为红；已排除「不计入统计」学生。
      </p>

      {all.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-slate-400">
            暂无连续缺交预警
          </CardContent>
        </Card>
      ) : (
        <Tabs defaultValue="student">
          <TabsList>
            <TabsTrigger value="student">按学生</TabsTrigger>
            <TabsTrigger value="subject">按作业种类</TabsTrigger>
          </TabsList>

          {/* 按学生 */}
          <TabsContent value="student">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {byStudent.map((g) => (
                <Card key={g.student_id || g.name}>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-base">
                      {g.student_id ? (
                        <Link href={`/student/${g.student_id}`} className="text-brand-700 hover:underline">
                          {g.name}
                        </Link>
                      ) : (
                        g.name
                      )}
                      <span className="ml-2 text-xs font-normal text-slate-400">
                        {g.items.length} 类预警
                      </span>
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-1.5">
                    {g.items.map((w, i) => (
                      <div key={i} className="flex items-center gap-2 text-sm">
                        <StreakBadge streak={w.streak} />
                        <span className="text-slate-700">{w.subject}</span>
                        <span className="text-xs text-slate-400">{dateRange(w.dates)}</span>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              ))}
            </div>
          </TabsContent>

          {/* 按作业种类 */}
          <TabsContent value="subject">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {bySubject.map((g) => (
                <Card key={g.subject}>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-base">
                      {g.subject}
                      <span className="ml-2 text-xs font-normal text-slate-400">
                        {g.items.length} 人{g.red > 0 ? ` · ${g.red} 人红` : ''}
                      </span>
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-1.5">
                    {g.items.map((w, i) => (
                      <div key={i} className="flex items-center gap-2 text-sm">
                        <StreakBadge streak={w.streak} />
                        {w.student_id ? (
                          <Link href={`/student/${w.student_id}`} className="text-slate-700 hover:underline">
                            {w.name}
                          </Link>
                        ) : (
                          <span className="text-slate-700">{w.name}</span>
                        )}
                        <span className="text-xs text-slate-400">{dateRange(w.dates)}</span>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              ))}
            </div>
          </TabsContent>
        </Tabs>
      )}
    </div>
  )
}
