'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { Bell } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

interface FocusStudent {
  student_id: string
  name: string
  score: number
  reasons: string[]
}
interface WeeklyFocus {
  week: { start: string; end: string }
  students: FocusStudent[]
  teaching_subject?: string | null
}

function reasonStyle(reason: string): string {
  if (reason.startsWith('连续缺交')) return 'bg-danger-50 text-danger-600'
  if (reason.startsWith('本周缺交激增')) return 'bg-warning-50 text-warning-700'
  if (reason.startsWith('谈话跟进')) return 'bg-brand-50 text-brand-700'
  return 'bg-slate-100 text-slate-600'
}

export default function WeeklyFocusCard({ teachingClassId }: { teachingClassId?: number }) {
  const [data, setData] = useState<WeeklyFocus | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    // teachingClassId 缺省：后端按当前任教学科教学班成员并集合成本周关注
    const url =
      teachingClassId != null
        ? `/api/weekly-focus?teaching_class_id=${teachingClassId}`
        : '/api/weekly-focus'
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
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoaded(true))
  }, [teachingClassId])

  if (loaded && (!data || data.students.length === 0)) return null

  const students = data?.students || []
  const subjectLabel = data?.teaching_subject ?? '当前学科'

  return (
    <Card className="border-warning-500/30 bg-warning-50/30">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="flex items-center gap-2 text-base">
          <Bell className="h-4 w-4 text-warning-600" />
          本周关注（{subjectLabel}·{students.length} 人）
        </CardTitle>
        {data && (
          <span className="text-xs text-slate-400">
            {data.week.start.slice(5)} ~ {data.week.end.slice(5)}
          </span>
        )}
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {students.slice(0, 8).map((s) => (
            <div
              key={s.student_id}
              className="flex flex-col gap-1.5 rounded-md border border-slate-100 bg-white px-3 py-2 sm:flex-row sm:items-center sm:justify-between"
            >
              <Link
                href={`/student/${s.student_id}`}
                className="shrink-0 text-sm font-medium text-brand-700 hover:underline sm:w-20"
              >
                {s.name}
              </Link>
              <div className="flex flex-wrap gap-1.5">
                {s.reasons.map((r, i) => (
                  <span key={i} className={`rounded px-2 py-0.5 text-xs ${reasonStyle(r)}`}>
                    {r}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
        {students.length > 8 && (
          <p className="mt-2 text-center text-xs text-slate-400">
            另有 {students.length - 8} 人需关注
          </p>
        )}
        <p className="mt-3 text-xs text-slate-400">
          合并连续缺交预警、本周缺交激增、最近考试临界/薄弱（仅{subjectLabel}）、谈话跟进待办。不依赖新考试，每天更新。
        </p>
      </CardContent>
    </Card>
  )
}
