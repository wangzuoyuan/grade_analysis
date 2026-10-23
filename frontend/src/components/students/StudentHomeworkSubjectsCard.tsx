'use client'

/**
 * 个人作业与成绩分析（全科并列卡，班主任学生档案页专用，2026-10-22）。
 *
 * 数据源：GET /api/v1/homeroom/diagnosis/homework-student-subjects（单生全科一次请求），
 * 提交率区间 window_mode=between_exams：上一次考试次日至本次考试前（首场考试
 * 自本学期开始，学期取作业学期设置），随 personId/学年变化重拉并丢弃迟到回包。
 * 每个学科一块：左「考前提交率」迷你折线（0–100%，蓝 #0ea5e9），右「成绩」迷你折线（紫 #8b5cf6）。
 * 成绩口径按学科分：语数英/高一为年级百分位（前%，越小越好；该科全部点同为该口径时
 * Y 域固定 0–100 且 reversed，越靠前画得越高），高二/高三六选科为等级分（自动域）。
 * 点只取 exam_date 非空者；缺考点断线不补连（connectNulls=false）。
 */

import { useEffect, useRef, useState } from 'react'
import { BookOpenCheck } from 'lucide-react'
import {
  LineChart,
  Line,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis as RXAxis,
  YAxis as RYAxis,
} from 'recharts'

import {
  studentHomeworkSubjects,
  type HomeworkSubjectPoint,
  type HomeworkSubjectSeries,
  type StudentHomeworkSubjectsResponse,
} from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { subjectAchievementLabel } from '@/lib/student-homework'

const DASH = '—'

/** 只取考试日期完整的点（无日期点无法落到时间轴，入图前剔除）。 */
function chartablePoints(s: HomeworkSubjectSeries): HomeworkSubjectPoint[] {
  return s.timeline.filter((p) => p.exam_date != null && p.exam_date !== '')
}

/** 悬浮标题：考试名 + 该场提交率统计起点（区间口径下说明数据从哪天算起）。 */
function examSpanLabel(label: unknown, payload?: ReadonlyArray<{ payload?: HomeworkSubjectPoint }>): string {
  const start = payload?.[0]?.payload?.window_start
  return `考试：${String(label)}${start ? `，提交率统计自 ${start}` : ''}`
}

export default function StudentHomeworkSubjectsCard({
  personId,
  academicYearId,
}: {
  personId: number
  academicYearId?: string | number
}) {
  const [data, setData] = useState<StudentHomeworkSubjectsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const reqRef = useRef(0)

  // 切换学生/学年时重拉；迟到回包按请求序号丢弃（与档案页 getStudentReport 同模式）
  useEffect(() => {
    const req = ++reqRef.current
    setData(null)
    setError(null)
    setLoaded(false)
    studentHomeworkSubjects({ person_id: personId, academic_year_id: academicYearId, window_days: 14, window_mode: 'between_exams' })
      .then((r) => {
        if (req !== reqRef.current) return
        setData(r)
        setLoaded(true)
      })
      .catch((err: unknown) => {
        if (req !== reqRef.current) return
        setError(apiErrorMessage(err))
        setLoaded(true)
      })
  }, [personId, academicYearId])

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <BookOpenCheck className="h-4 w-4 text-slate-600" />
          个人作业与成绩分析
        </CardTitle>
        <CardDescription>
          各学科作业提交率（上次考试后至本次考试前，首场自开学起）与成绩变化并列对比；语数英与高一用年级百分位（前%），高二/高三六选科用等级分
        </CardDescription>
      </CardHeader>
      <CardContent>
        {!loaded ? (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            <Skeleton className="h-44 w-full" />
            <Skeleton className="h-44 w-full" />
            <Skeleton className="h-44 w-full" />
          </div>
        ) : error != null ? (
          <div className="py-6 text-center text-sm text-slate-500">
            个人作业与成绩分析暂不可用：{error}
          </div>
        ) : !data || data.subjects.length === 0 ? (
          <div className="py-6 text-center text-sm text-slate-400">该生暂无可配对的考试与作业记录</div>
        ) : (
          <>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {data.subjects.map((s) => (
                <SubjectBlock key={s.subject} series={s} />
              ))}
            </div>
            {data.note ? (
              <p className="mt-3 text-[11px] leading-relaxed text-slate-400">{data.note}</p>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  )
}

function SubjectBlock({ series }: { series: HomeworkSubjectSeries }) {
  const points = chartablePoints(series)
  const rateRows = points.map((p) => ({
    exam_name: p.exam_name,
    rate: p.submission_rate == null ? null : p.submission_rate * 100,
  }))
  const scoreRows = points.map((p) => ({
    exam_name: p.exam_name,
    // percentile 口径画「前%」本身（percentile 字段 = 年级位置，越小越好）；
    // achievement 是换算后的 100×(1−p)「越大越好」分数，不直接当「前%」展示。
    score:
      p.basis === 'grade_percentile'
        ? p.percentile != null
          ? p.percentile * 100
          : p.achievement != null
            ? 100 - p.achievement
            : null
        : p.achievement,
  }))
  // 成绩口径：该科全部点同为年级百分位（前%）时固定 0–100 域 + reversed（越靠前画得越高）
  const allPercentile = points.length > 0 && points.every((p) => p.basis === 'grade_percentile')
  const latest = points.length > 0 ? points[points.length - 1] : null

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
        <span className="text-sm font-semibold text-slate-800">{series.subject}</span>
        <span className="flex items-center gap-1.5">
          <span className="text-xs text-slate-500">最近：{subjectAchievementLabel(latest)}</span>
          <Badge variant="secondary" className="text-[11px] font-normal">
            配对 {series.paired_n} 场
          </Badge>
        </span>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2">
        <div>
          <div className="text-[11px] text-slate-400">考前提交率</div>
          <div className="h-24 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rateRows} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <RXAxis dataKey="exam_name" tick={false} axisLine={{ stroke: '#e2e8f0' }} tickLine={false} />
                <RYAxis
                  domain={[0, 100]}
                  width={30}
                  tick={{ fontSize: 10, fill: '#94a3b8' }}
                  tickFormatter={(v: number) => `${v}%`}
                />
                <RTooltip
                  formatter={(val: unknown) => [val == null ? DASH : `${Number(val).toFixed(0)}%`, '考前提交率']}
                  labelFormatter={examSpanLabel}
                />
                <Line
                  type="monotone"
                  dataKey="rate"
                  name="考前提交率"
                  stroke="#0ea5e9"
                  strokeWidth={2}
                  dot={{ r: 2.5, fill: '#0ea5e9' }}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div>
          <div className="text-[11px] text-slate-400">成绩{allPercentile ? '（前%，越小越好）' : ''}</div>
          <div className="h-24 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={scoreRows} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <RXAxis dataKey="exam_name" tick={false} axisLine={{ stroke: '#e2e8f0' }} tickLine={false} />
                <RYAxis
                  width={34}
                  tick={{ fontSize: 10, fill: '#94a3b8' }}
                  domain={allPercentile ? [0, 100] : ['auto', 'auto']}
                  reversed={allPercentile}
                  tickFormatter={allPercentile ? (v: number) => `前${v}%` : undefined}
                />
                <RTooltip
                  formatter={(val: unknown) =>
                    val == null
                      ? [DASH, '成绩']
                      : allPercentile
                        ? [`前${Number(val).toFixed(1)}%`, '成绩']
                        : [`${Number(val).toFixed(1)} 等级分`, '成绩']
                  }
                  labelFormatter={examSpanLabel}
                />
                <Line
                  type="monotone"
                  dataKey="score"
                  name="成绩"
                  stroke="#8b5cf6"
                  strokeWidth={2}
                  dot={{ r: 2.5, fill: '#8b5cf6' }}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </div>
  )
}
