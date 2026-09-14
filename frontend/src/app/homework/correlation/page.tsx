'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import {
  CartesianGrid,
  Cell,
  LabelList,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts'
import { ChevronLeft } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ClassScopePicker } from '@/components/ClassScopePicker'
import { useClassScope } from '@/lib/class-scope'

interface Row {
  student_id: string
  name: string
  miss_count: number
  subject_rank?: number | null
}
interface Correlation {
  exam_id: number | null
  teaching_subject?: string | null
  teaching_class_id?: number | null
  subject?: string | null
  y_field: string
  y_label: string
  rows: Row[]
}

export default function CorrelationPage() {
  const scope = useClassScope()
  const { currentClass } = scope
  const tidParam = scope.scopeParam(currentClass?.grade).teaching_class_id

  const [data, setData] = useState<Correlation | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const url = tidParam
        ? `/api/homework/correlation?teaching_class_id=${tidParam}`
        : '/api/homework/correlation'
      const corr = await fetch(url).then((r) => r.json())
      setData(corr)
    } catch {
      setError('加载失败')
    }
  }, [tidParam])

  useEffect(() => {
    load()
  }, [load])

  const subjectLabel = data?.teaching_subject ?? '当前学科'
  const yLabel = data?.y_label ?? `${subjectLabel}班内名次`

  const points = useMemo(
    () =>
      (data?.rows || [])
        .filter((r) => r.subject_rank != null)
        .map((r) => ({
          x: r.miss_count,
          y: r.subject_rank as number,
          name: r.name,
        })),
    [data],
  )

  const missThreshold = useMemo(() => {
    if (points.length === 0) return 0
    const xs = points.map((p) => p.x).sort((a, b) => a - b)
    return xs[Math.floor(xs.length * 0.7)]
  }, [points])
  const yThreshold = useMemo(() => {
    if (points.length === 0) return 0
    const ys = points.map((p) => p.y).sort((a, b) => a - b)
    // rank 越大越差，取 60 分位为"靠后"阈值
    return ys[Math.floor(ys.length * 0.6)]
  }, [points])
  const flagged = useMemo(
    // 高缺交（x 大）且名次靠后（y 大）
    () => points.filter((p) => p.x >= missThreshold && p.y >= yThreshold),
    [points, missThreshold, yThreshold],
  )
  const flaggedNames = new Set(flagged.map((p) => p.name))

  return (
    <div className="space-y-6">
      <Link
        href="/homework"
        className="inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900"
      >
        <ChevronLeft className="h-4 w-4" />
        返回作业看板
      </Link>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-900">
            缺交 × {subjectLabel}班内名次
          </h1>
          <p className="mt-0.5 text-xs text-slate-500">
            缺交越多且名次越靠后（rank 越大）的学生，落在重点关注象限
          </p>
        </div>
        <ClassScopePicker compact />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            缺交次数 × {yLabel}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {error ? (
            <p className="py-10 text-center text-sm text-slate-400">{error}</p>
          ) : points.length === 0 ? (
            <p className="py-10 text-center text-sm text-slate-400">
              暂无可对照数据（需当前学科已导入考试成绩）
            </p>
          ) : (
            <>
              <ResponsiveContainer width="100%" height={420}>
                <ScatterChart margin={{ top: 16, right: 24, bottom: 32, left: 8 }}>
                  <CartesianGrid stroke="#dcecf8" strokeDasharray="3 3" />
                  <XAxis
                    type="number"
                    dataKey="x"
                    name="缺交次数"
                    tick={{ fontSize: 11, fill: '#58789b' }}
                    stroke="#dcecf8"
                    label={{
                      value: '缺交次数 →',
                      position: 'insideBottom',
                      offset: -16,
                      fontSize: 12,
                      fill: '#58789b',
                    }}
                  />
                  <YAxis
                    type="number"
                    dataKey="y"
                    name={yLabel}
                    reversed
                    domain={['auto', 'auto']}
                    tick={{ fontSize: 11, fill: '#58789b' }}
                    stroke="#dcecf8"
                    label={{
                      value: `${yLabel}（越上越好）`,
                      angle: -90,
                      position: 'insideLeft',
                      fontSize: 12,
                      fill: '#58789b',
                    }}
                  />
                  <ZAxis range={[60, 60]} />
                  <RTooltip
                    cursor={{ strokeDasharray: '3 3' }}
                    content={({ payload }) => {
                      const p = payload?.[0]?.payload as
                        | { name: string; x: number; y: number }
                        | undefined
                      if (!p) return null
                      return (
                        <div className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs shadow-sm">
                          <div className="font-medium text-slate-800">{p.name}</div>
                          <div className="text-slate-500">
                            缺交 {p.x} 次 · {yLabel} {p.y}
                          </div>
                        </div>
                      )
                    }}
                  />
                  <Scatter data={points}>
                    {points.map((p, i) => (
                      <Cell
                        key={i}
                        fill={flaggedNames.has(p.name) ? '#c74444' : '#8fb8dc'}
                      />
                    ))}
                    <LabelList
                      dataKey="name"
                      position="top"
                      content={(props) => {
                        const { x, y, value } = props as {
                          x?: number
                          y?: number
                          value?: string
                        }
                        if (x == null || y == null || !value) return null
                        const flagged = flaggedNames.has(value)
                        return (
                          <text
                            x={x}
                            y={y - 6}
                            textAnchor="middle"
                            fontSize={10}
                            fill={flagged ? '#c74444' : '#8fb8dc'}
                            fontWeight={flagged ? 600 : 400}
                          >
                            {value}
                          </text>
                        )
                      }}
                    />
                  </Scatter>
                </ScatterChart>
              </ResponsiveContainer>
              <p className="mt-2 text-xs text-slate-400">
                红点 = 缺交偏多且名次靠后的学生，落在重点关注象限。
                {yLabel}越小越靠前（图中越高）。作业数据仅反映缺交，不代表完成质量。
              </p>
            </>
          )}
        </CardContent>
      </Card>

      {flagged.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              重点关注（高缺交 + 名次靠后）
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {flagged
                .sort((a, b) => b.x - a.x)
                .map((p) => (
                  <span
                    key={p.name}
                    className="rounded-md bg-danger-50 px-3 py-1.5 text-sm text-danger-600"
                  >
                    {p.name} · 缺交{p.x}次 · 名次{p.y}
                  </span>
                ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
