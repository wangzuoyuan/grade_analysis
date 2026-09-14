'use client'

/**
 * 学生单科画像（教学工作台，P8-UXFIX 重接 v1）。
 *
 * 数据来自 /api/v1：teaching/students/{person_id}（画像）、
 * teaching/analysis/exams/{exam}/students（逐场名次/班级标签）、
 * homework/students/{person_id}（作业事件）、{mode}/students/{id}/notes（档案）。
 * v1 无年级百分位——相应列与 KPI 移除，趋势指标为等级分优先、原始分兜底；
 * 缺考/无名次显示「—」，不转 0；迟到回包按请求序号丢弃。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import {
  LineChart,
  Line,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis as RXAxis,
  YAxis as RYAxis,
} from 'recharts'
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  ChevronLeft,
  Hash,
  Minus,
  TrendingUp,
} from 'lucide-react'

import {
  fetchStudent,
  fetchTeachingStudents,
  type StudentProfile,
} from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { useWorkspace, WorkspaceSwitcher } from '@/lib/workspace'
import { analysisScopeQuery } from '@/components/scores/shared'
import HomeworkCard from '@/components/HomeworkCard'
import StudentNotes from '@/components/StudentNotes'
import { cn } from '@/lib/utils'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

interface TrendPoint {
  exam_name: string
  exam_date: string | null
  subject: string
  score: number | null
  grade_score: number | null
  source_domain: string
  rank: number | null
  class_label: string | null
}

const DASH = '—'

function safeNum(v: unknown): number | null {
  if (v === null || v === undefined) return null
  if (typeof v === 'number' && Number.isFinite(v)) return v
  return null
}

function formatScoreText(v: number | null | undefined): string {
  if (v == null) return DASH
  return Number.isInteger(v) ? String(v) : v.toFixed(1)
}

function nameInitial(name: string | null): string {
  if (!name) return '?'
  return name.trim().charAt(0)
}

function metricValue(point: TrendPoint): number | null {
  return safeNum(point.grade_score) ?? safeNum(point.score)
}

function metricLabelFor(point: TrendPoint | null): string {
  if (point != null && safeNum(point.grade_score) != null) return '最新等级分'
  return '最新原始分'
}

function DeltaArrow({
  current,
  previous,
  invert = false,
  threshold = 0,
}: {
  current: number | null
  previous: number | null
  invert?: boolean
  threshold?: number
}) {
  if (current === null || previous === null) {
    return <span className="text-slate-400">{DASH}</span>
  }
  const diff = current - previous
  if (Math.abs(diff) <= threshold) {
    return (
      <span className="inline-flex items-center gap-1 text-slate-500">
        <Minus className="h-3.5 w-3.5" />
        持平
      </span>
    )
  }
  const improved = invert ? diff < 0 : diff > 0
  const Icon = improved ? ArrowUpRight : ArrowDownRight
  const cls = improved ? 'text-success-500' : 'text-danger-500'
  const display = `${diff > 0 ? '+' : ''}${diff}`
  return (
    <span className={cn('inline-flex items-center gap-1 font-medium', cls)}>
      <Icon className="h-3.5 w-3.5" />
      {display}
    </span>
  )
}

function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-slate-200 bg-slate-50 px-6 py-10 text-center">
      <p className="text-sm font-medium text-slate-600">{title}</p>
      {hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>}
    </div>
  )
}

function StudentDetailSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-5 w-20" />
      <Card>
        <CardContent className="flex items-center gap-4 py-6">
          <Skeleton className="h-16 w-16 rounded-full" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-6 w-40" />
            <Skeleton className="h-4 w-56" />
          </div>
          <Skeleton className="h-7 w-24" />
        </CardContent>
      </Card>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Card key={i}>
            <CardContent className="space-y-3 py-6">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-8 w-20" />
              <Skeleton className="h-3 w-16" />
            </CardContent>
          </Card>
        ))}
      </div>
      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-48" />
        </CardHeader>
        <CardContent>
          <Skeleton className="h-64 w-full" />
        </CardContent>
      </Card>
    </div>
  )
}

export default function StudentPage() {
  const params = useParams<{ id: string }>()
  const rawId = Array.isArray(params?.id) ? params?.id[0] : params?.id
  const personId = rawId != null && /^\d+$/.test(rawId) ? Number(rawId) : null
  const { filter, generation, mode } = useWorkspace()
  const scopeQ = useMemo(() => analysisScopeQuery(filter), [filter])

  const [profile, setProfile] = useState<StudentProfile | null>(null)
  const [ranks, setRanks] = useState<Map<string, { rank: number | null; class_label: string | null }>>(new Map())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const reqRef = useRef(0)

  useEffect(() => {
    // R03：班主任工作台不发任何教学域请求（页面显示指引卡）
    if (mode !== 'teaching') return
    if (personId == null) {
      setLoading(false)
      setError('学生编号无效')
      return
    }
    const req = ++reqRef.current
    setLoading(true)
    setError(null)
    setProfile(null)
    setRanks(new Map())
    fetchStudent('teaching', personId, scopeQ)
      .then(async (p) => {
        // 逐场名次：对画像里出现过的每场考试按教学分析口径取一次该场学生表
        const examNames = Array.from(
          new Set((p.subjects ?? []).flatMap((s) => (s.exams ?? []).map((e) => e.exam_name))),
        )
        const rows = await Promise.all(
          examNames.map((name) =>
            fetchTeachingStudents(name, scopeQ)
              .then((r) => [name, r.students ?? []] as const)
              .catch(() => [name, []] as const),
          ),
        )
        return { p, rows }
      })
      .then(({ p, rows }) => {
        if (req !== reqRef.current) return
        const map = new Map<string, { rank: number | null; class_label: string | null }>()
        for (const [name, students] of rows) {
          const row = students.find((s) => s.person_id === personId)
          map.set(name, { rank: row?.rank ?? null, class_label: row?.class_label ?? null })
        }
        setProfile(p)
        setRanks(map)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (req !== reqRef.current) return
        setError(apiErrorMessage(err))
        setLoading(false)
      })
  }, [mode, personId, scopeQ, generation])

  const scoreTrend = useMemo<TrendPoint[]>(() => {
    const points: TrendPoint[] = []
    for (const group of profile?.subjects ?? []) {
      for (const e of group.exams ?? []) {
        const extra = ranks.get(e.exam_name)
        points.push({
          exam_name: e.exam_name,
          exam_date: e.exam_date,
          subject: group.subject,
          score: e.score,
          grade_score: e.grade_score ?? null,
          source_domain: e.source_domain,
          rank: extra?.rank ?? null,
          class_label: extra?.class_label ?? null,
        })
      }
    }
    points.sort((a, b) => (a.exam_date ?? '').localeCompare(b.exam_date ?? '') || a.exam_name.localeCompare(b.exam_name))
    return points
  }, [profile, ranks])

  const subject = profile?.subjects?.[0]?.subject ?? null

  const kpi = useMemo(() => {
    const last = scoreTrend[scoreTrend.length - 1] || null
    const prev = scoreTrend.length >= 2 ? scoreTrend[scoreTrend.length - 2] : null
    return {
      metricNow: last ? metricValue(last) : null,
      metricPrev: prev ? metricValue(prev) : null,
      usesGrade: last != null && safeNum(last.grade_score) != null,
      scopeRankNow: last?.rank ?? null,
      scopeRankPrev: prev?.rank ?? null,
      rawScoreNow: last?.score ?? null,
      gradeScoreNow: last?.grade_score ?? null,
    }
  }, [scoreTrend])

  if (mode === 'homeroom') {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">学生画像</h1>
          <p className="mt-1 text-sm text-slate-500">按任教学科教学班组织，属教学工作台</p>
        </div>
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-10 text-center">
            <AlertTriangle className="h-8 w-8 text-slate-300" />
            <p className="text-sm text-slate-600">
              学生单科画像属教学工作台；班主任工作台请在「学生管理」查看行政班学生。
            </p>
            <Button asChild>
              <Link href="/homeroom/students">进入学生管理</Link>
            </Button>
            <WorkspaceSwitcher />
          </CardContent>
        </Card>
      </div>
    )
  }

  if (loading) {
    return <StudentDetailSkeleton />
  }

  if (error || !profile) {
    return (
      <div className="space-y-6">
        <Link
          href="/student"
          className="inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900"
        >
          <ChevronLeft className="h-4 w-4" />
          返回
        </Link>
        <Card>
          <CardContent className="py-10">
            <EmptyState
              title="加载学生数据失败"
              hint={error || '请稍后重试，或确认该学生是否在当前教学班范围内。'}
            />
          </CardContent>
        </Card>
      </div>
    )
  }

  const name = profile.person?.name ?? null
  const latest = scoreTrend[scoreTrend.length - 1] ?? null
  const classHeaderText = latest?.class_label ?? null
  const multiSubject = new Set(scoreTrend.map((p) => p.subject)).size > 1

  // 趋势图数据
  const sparkData = scoreTrend.map((p) => ({ name: p.exam_name, value: metricValue(p) ?? undefined }))
  const hasSparkData = sparkData.some((d) => d.value !== undefined)
  const metricLabel = metricLabelFor(latest)

  return (
    <div className="space-y-6">
      {/* 返回 + 导出 */}
      <div className="flex items-center justify-between">
        <Link
          href="/student"
          className="inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900"
        >
          <ChevronLeft className="h-4 w-4" />
          返回
        </Link>
        {personId != null && (
          <Link href={`/student/${personId}/report`}>
            <span className="inline-flex items-center gap-1 rounded-md border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50">
              导出家长会一页纸
            </span>
          </Link>
        )}
      </div>

      {/* 学生卡 */}
      <Card>
        <CardContent className="flex flex-col gap-4 py-6 md:flex-row md:items-center">
          <Avatar className="h-16 w-16">
            <AvatarFallback className="bg-brand-50 text-lg font-semibold text-brand-700">
              {nameInitial(name)}
            </AvatarFallback>
          </Avatar>
          <div className="flex-1 min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
              {name || DASH}
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              {classHeaderText ?? DASH}
            </p>
            {subject && (
              <p className="mt-0.5 text-xs text-brand-600">
                任教学科：{subject}
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      {/* KPI 行 */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <Card>
          <CardContent className="py-5">
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <Hash className="h-4 w-4" />
              最新教学班排名
            </div>
            <div className="mt-2 flex items-baseline gap-3">
              <span className="text-3xl font-semibold text-slate-900">
                {kpi.scopeRankNow !== null ? kpi.scopeRankNow : DASH}
              </span>
              <DeltaArrow
                current={kpi.scopeRankNow}
                previous={kpi.scopeRankPrev}
                invert
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="py-5">
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <TrendingUp className="h-4 w-4" />
              {metricLabel}
            </div>
            <div className="mt-2 flex items-baseline gap-3">
              <span className="text-3xl font-semibold text-slate-900">
                {kpi.metricNow !== null ? kpi.metricNow : DASH}
              </span>
              <DeltaArrow current={kpi.metricNow} previous={kpi.metricPrev} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="py-5">
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <Badge variant="secondary" className="text-xs">
                最新
              </Badge>
              原始分 / 等级分
            </div>
            <div className="mt-2 flex items-baseline gap-3">
              <span className="text-3xl font-semibold text-slate-900">
                {kpi.rawScoreNow !== null ? kpi.rawScoreNow : DASH}
              </span>
              <span className="text-sm text-slate-400">
                等级分 {kpi.gradeScoreNow !== null ? kpi.gradeScoreNow : DASH}
              </span>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* 作业缺交（v1 学生事件流；无事件时卡片自隐藏） */}
      {personId != null && <HomeworkCard mode="teaching" personId={personId} scopeQ={scopeQ} />}

      {/* 成长 / 谈话档案 */}
      {personId != null && <StudentNotes mode="teaching" personId={personId} scopeQ={scopeQ} />}

      {/* 单科趋势图 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            {subject ? `${subject}` : '单科'}成绩趋势
          </CardTitle>
        </CardHeader>
        <CardContent>
          {hasSparkData ? (
            <>
              <ResponsiveContainer width="100%" height={280}>
                <LineChart data={sparkData} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
                  <RXAxis dataKey="name" tick={{ fontSize: 11 }} />
                  <RYAxis tick={{ fontSize: 11 }} />
                  <RTooltip
                    contentStyle={{ fontSize: 12 }}
                    labelFormatter={(label) => String(label)}
                  />
                  <Line
                    type="monotone"
                    dataKey="value"
                    name={kpi.usesGrade ? '等级分' : '原始分'}
                    stroke="#1f7fd6"
                    strokeWidth={2}
                    dot={{ r: 3 }}
                  />
                </LineChart>
              </ResponsiveContainer>
              <p className="mt-2 text-xs text-slate-400">
                趋势指标为等级分优先、原始分兜底（越高越好）；排名按教学班成员集合统计。
              </p>
            </>
          ) : (
            <EmptyState title="趋势图数据待补" hint="尚无当前学科考试记录" />
          )}
        </CardContent>
      </Card>

      {/* 历次考试明细 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            历次{subject ?? ''}考试明细
          </CardTitle>
        </CardHeader>
        <CardContent>
          {scoreTrend.length > 0 ? (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>考试</TableHead>
                    <TableHead>日期</TableHead>
                    {multiSubject && <TableHead>学科</TableHead>}
                    <TableHead className="text-right">原始分</TableHead>
                    <TableHead className="text-right">等级分</TableHead>
                    <TableHead className="text-right">教学班</TableHead>
                    <TableHead className="text-right">班内排名</TableHead>
                    <TableHead>来源</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {[...scoreTrend].reverse().map((p, i) => (
                    <TableRow key={`${p.exam_name}-${p.subject}-${i}`} className="hover:bg-slate-50">
                      <TableCell className="font-medium">{p.exam_name}</TableCell>
                      <TableCell className="text-slate-500">{p.exam_date || DASH}</TableCell>
                      {multiSubject && <TableCell className="text-slate-500">{p.subject}</TableCell>}
                      <TableCell className="text-right tabular-nums">{formatScoreText(p.score)}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatScoreText(p.grade_score)}</TableCell>
                      <TableCell>
                        {p.class_label ? (
                          <Badge variant="secondary">{p.class_label}</Badge>
                        ) : (
                          <span className="text-slate-400">{DASH}</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {p.rank !== null ? p.rank : DASH}
                      </TableCell>
                      <TableCell>
                        {p.source_domain === 'homeroom' ? (
                          <Badge variant="outline">班主任域投影</Badge>
                        ) : (
                          <Badge variant="secondary">教学域</Badge>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <EmptyState title="暂无考试记录" />
          )}
        </CardContent>
      </Card>
    </div>
  )
}
