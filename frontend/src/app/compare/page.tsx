'use client'

/**
 * 班级对比（教学工作台，P8-UXFIX 重接 v1）。
 *
 * 数据来自 /api/v1：shared/exams（考试清单）、teaching/analysis/class-compare
 * （各教学班样本均分，恒 estimated）、teaching/analysis/exams/{exam}/stats
 * （学科/口径/总体均分）。与教学成绩页同源同口径；行政班全科对比在班主任
 * 工作台 /homeroom/compare（官方班级均分表口径），homeroom 模式进入本页时
 * 直接跳转过去。迟到回包按资源分离的序号丢弃（F11）。
 * 名次为各班样本均分的排序名次（同分同名次），不可算为「—」。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  Tooltip as RTooltip,
  Legend,
  CartesianGrid,
  ResponsiveContainer,
} from 'recharts'
import {
  AlertTriangle,
  ChevronUp,
  ChevronDown,
  ChevronsUpDown,
  ArrowUp,
  ArrowDown,
  Info,
  Inbox,
} from 'lucide-react'
import Link from 'next/link'

import {
  fetchTeachingClassCompare,
  fetchTeachingStats,
  listExams,
  type ExamSummary,
  type TeachingClassCompareItem,
  type TeachingClassCompareResponse,
  type TeachingStatsResponse,
} from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { useWorkspace } from '@/lib/workspace'
import { ScoreYearPicker } from '@/components/scores/ScoreYearPicker'
import { ExamSelect } from '@/components/scores/ExamSelect'
import { analysisScopeQuery, scoreBasisLabel, sortExamsDesc } from '@/components/scores/shared'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'

// ---------- 类型 ----------

function displayLabel(label: string | null | undefined): string {
  if (!label) return '—'
  return /^\d+$/.test(label) ? `${label}班` : label
}

interface CompareClass extends TeachingClassCompareItem {
  diff: number | null
  rank: number | null
}

type SortKey = 'class_label' | 'subject_avg' | 'diff' | 'rank'
type SortDir = 'asc' | 'desc'

/** 样本均分排序名次：同分同名次（min-rank），不可算（null）排后为 null。 */
function deriveRanks(classes: TeachingClassCompareItem[]): Array<TeachingClassCompareItem & { rank: number | null }> {
  const sorted = [...classes].sort((a, b) => {
    if (a.subject_avg == null && b.subject_avg == null) return a.class_label.localeCompare(b.class_label, 'zh-CN')
    if (a.subject_avg == null) return 1
    if (b.subject_avg == null) return -1
    if (a.subject_avg !== b.subject_avg) return (b.subject_avg as number) - (a.subject_avg as number)
    return a.class_label.localeCompare(b.class_label, 'zh-CN')
  })
  let lastAvg: number | null = null
  let lastRank = 0
  return sorted.map((c, i) => {
    if (c.subject_avg == null) return { ...c, rank: null }
    const rank = lastAvg !== null && c.subject_avg === lastAvg ? lastRank : i + 1
    lastAvg = c.subject_avg
    lastRank = rank
    return { ...c, rank }
  })
}

export default function ComparePage() {
  const { filter, generation, switching, mode } = useWorkspace()
  const router = useRouter()
  const scopeQ = useMemo(() => analysisScopeQuery(filter), [filter])

  const [exams, setExams] = useState<ExamSummary[] | null>(null)
  const [examsError, setExamsError] = useState<string | null>(null)
  const [examNonce, setExamNonce] = useState(0)
  const [selectedExam, setSelectedExam] = useState<string | null>(null)

  const [compare, setCompare] = useState<TeachingClassCompareResponse | null>(null)
  const [stats, setStats] = useState<TeachingStatsResponse | null>(null)
  const [dataError, setDataError] = useState<string | null>(null)

  const examsReqRef = useRef(0)
  const dataReqRef = useRef(0)

  const [sortKey, setSortKey] = useState<SortKey>('subject_avg')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  // ---------- fetch: exams（F11：与数据请求互不作废；R03：仅教学域取数） ----------
  useEffect(() => {
    if (mode !== 'teaching') return
    const req = ++examsReqRef.current
    setExams(null)
    setExamsError(null)
    setSelectedExam(null)
    listExams('teaching', scopeQ)
      .then((r) => {
        if (req !== examsReqRef.current) return
        const sorted = sortExamsDesc(r.exams ?? [])
        setExams(sorted)
        if (sorted.length > 0) setSelectedExam(sorted[0].exam_name)
      })
      .catch((err: unknown) => {
        if (req !== examsReqRef.current) return
        setExams([])
        setExamsError(apiErrorMessage(err))
      })
  }, [mode, scopeQ, generation, examNonce])

  // ---------- fetch: class-compare + stats（同序号同命运） ----------
  useEffect(() => {
    if (mode !== 'teaching' || selectedExam == null) return
    const req = ++dataReqRef.current
    setCompare(null)
    setStats(null)
    setDataError(null)
    Promise.all([
      fetchTeachingClassCompare(selectedExam, scopeQ),
      fetchTeachingStats(selectedExam, scopeQ),
    ])
      .then(([cmp, st]) => {
        if (req !== dataReqRef.current) return
        setCompare(cmp)
        setStats(st)
      })
      .catch((err: unknown) => {
        if (req !== dataReqRef.current) return
        setCompare(null)
        setStats(null)
        setDataError(apiErrorMessage(err))
      })
  }, [mode, selectedExam, scopeQ])

  const classes = useMemo<CompareClass[]>(() => {
    const overall = stats?.avg ?? null
    return deriveRanks(compare?.classes ?? []).map((c) => ({
      ...c,
      diff: overall != null && c.subject_avg != null ? c.subject_avg - overall : null,
    }))
  }, [compare, stats])

  const teachingSubject = stats?.subject ?? null
  const scoreBasis = compare?.classes?.[0]?.score_basis ?? stats?.score_basis ?? 'raw'
  const basisLabel = scoreBasisLabel(scoreBasis).replace('分', '')
  const overallAvg = stats?.avg ?? null
  const smallSample = compare?.small_sample === true
  const metricShort = `${teachingSubject ?? '当前学科'}${basisLabel}均分`

  // 图表数据
  const chartData = useMemo(() => {
    return classes.map((c) => ({
      classLabel: displayLabel(c.class_label),
      class_label: c.class_label,
      subject_avg: c.subject_avg,
      member_count: c.member_count,
    }))
  }, [classes])

  // 排名表数据（应用用户排序）
  const rankRows = useMemo(() => {
    return [...classes].sort((a, b) => {
      const dir = sortDir === 'asc' ? 1 : -1
      const va = a[sortKey]
      const vb = b[sortKey]
      if (typeof va === 'number' || typeof vb === 'number') {
        if (va == null && vb == null) return 0
        if (va == null) return 1
        if (vb == null) return -1
        return ((va as number) - (vb as number)) * dir
      }
      return String(va).localeCompare(String(vb), 'zh-Hans-CN') * dir
    })
  }, [classes, sortKey, sortDir])

  const onSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir(key === 'class_label' ? 'asc' : 'desc')
    }
  }

  const sortIcon = (key: SortKey) => {
    if (sortKey !== key) return <ChevronsUpDown className="ml-1 h-3.5 w-3.5 opacity-50" />
    return sortDir === 'asc' ? (
      <ChevronUp className="ml-1 h-3.5 w-3.5" />
    ) : (
      <ChevronDown className="ml-1 h-3.5 w-3.5" />
    )
  }

  const isLoading = compare == null || stats == null
  const loadingExams = exams == null && examsError == null

  // 班主任工作台：此页的教学班样本对比属教学工作台；homeroom 模式直达
  // 班主任「班级对比」页（同年级各行政班均分，官方班级均分表口径）
  useEffect(() => {
    if (mode === 'homeroom') router.replace('/homeroom/compare')
  }, [mode, router])

  // 跳转期间渲染极简加载态（不发起任何教学域数据请求）
  if (mode === 'homeroom') {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">班级对比</h1>
          <p className="mt-1 text-sm text-slate-500">正在前往班主任「班级对比」…</p>
        </div>
        <Card>
          <CardContent className="space-y-2 py-6">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-40 w-full" />
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <TooltipProvider delayDuration={150}>
      <div className="space-y-6">
        {/* ---------- 顶部标题 ---------- */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
              班级对比
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              {teachingSubject
                ? `${teachingSubject}（${scoreBasisLabel(scoreBasis)}）· 我教的教学班横向对比`
                : '当前任教学科教学班横向对比'}
              {switching ? ' · 正在切换…' : ''}
            </p>
          </div>
        </div>

        {/* ---------- Filter 行（sticky；R01：常驻渲染，冷启动空态也能切学年） ---------- */}
        <div
          className={cn(
            'sticky top-14 z-20 -mx-1 rounded-xl border border-slate-200 print:hidden',
            'bg-white/80 backdrop-blur supports-[backdrop-filter]:bg-white/70',
          )}
        >
          <div className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:flex-wrap sm:items-center sm:gap-4">
            <ScoreYearPicker />
            <div className="flex w-full items-center gap-2 sm:w-auto">
              <span className="shrink-0 text-sm font-medium text-slate-700">选择考试</span>
              <ExamSelect
                exams={exams ?? []}
                value={selectedExam}
                onChange={setSelectedExam}
                loading={loadingExams}
              />
            </div>

            {overallAvg != null && (
              <div className="ml-auto flex items-center gap-1.5">
                <span className="text-sm text-slate-500">总体均分</span>
                <Badge variant="secondary" className="bg-brand-50 text-brand-700 hover:bg-brand-50">
                  {overallAvg.toFixed(1)}
                </Badge>
              </div>
            )}
            {smallSample && (
              <Badge variant="warning" className="shrink-0">
                样本&lt;5
              </Badge>
            )}
          </div>
        </div>

        {examsError ? (
          <Card>
            <CardContent className="flex flex-col items-center justify-center gap-3 py-8 text-center">
              <AlertTriangle className="h-8 w-8 text-amber-400" />
              <p className="text-sm text-slate-600">{examsError}</p>
              <Button variant="outline" size="sm" onClick={() => setExamNonce((n) => n + 1)}>
                重试
              </Button>
            </CardContent>
          </Card>
        ) : exams != null && exams.length === 0 ? (
          <Card>
            <CardContent className="py-10">
              <EmptyState
                title="当前范围暂无已导入考试"
                desc="可用上方学年选择器切换到历史学年，或到「数据上传」导入任教学科成绩表。"
                height={280}
              />
            </CardContent>
          </Card>
        ) : dataError ? (
          <Card>
            <CardContent className="flex flex-col items-center justify-center gap-3 py-8 text-center">
              <AlertTriangle className="h-8 w-8 text-amber-400" />
              <p className="text-sm text-slate-600">{dataError}</p>
            </CardContent>
          </Card>
        ) : (
              /* ---------- 主体 ---------- */
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
                {/* 左：柱状图 */}
                <Card className="lg:col-span-3">
                  <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                    <CardTitle className="text-base font-semibold text-slate-800">
                      各教学班{metricShort}
                    </CardTitle>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          className="inline-flex h-7 w-7 items-center justify-center rounded-md text-slate-400 hover:bg-slate-50 hover:text-slate-600"
                        >
                          <Info className="h-4 w-4" />
                        </button>
                      </TooltipTrigger>
                      <TooltipContent side="left" className="max-w-[260px]">
                        当前按{teachingSubject ?? '当前学科'}的{scoreBasisLabel(scoreBasis)}均分对比，仅含我教的教学班；均为班级样本估算（estimated），非年级或官方口径。
                      </TooltipContent>
                    </Tooltip>
                  </CardHeader>
                  <CardContent>
                    {isLoading ? (
                      <Skeleton className="h-[380px] w-full" />
                    ) : chartData.length === 0 ? (
                      <EmptyState
                        title={selectedExam ? '该考试无对比数据' : '请先选择考试'}
                        desc="当前学科在教学班范围内暂无有效分数。"
                        height={380}
                      />
                    ) : (
                      <ResponsiveContainer width="100%" height={380}>
                        <BarChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#dcecf8" vertical={false} />
                          <XAxis
                            dataKey="classLabel"
                            tick={{ fill: '#58789b', fontSize: 12 }}
                            tickLine={false}
                            axisLine={{ stroke: '#cbe2f5' }}
                          />
                          <YAxis
                            domain={[0, 'dataMax + 20']}
                            tick={{ fill: '#58789b', fontSize: 12 }}
                            tickLine={false}
                            axisLine={{ stroke: '#cbe2f5' }}
                          />
                          <RTooltip
                            cursor={{ fill: 'rgba(148, 163, 184, 0.08)' }}
                            contentStyle={{
                              borderRadius: 8,
                              border: '1px solid #dcecf8',
                              fontSize: 12,
                              boxShadow: '0 4px 12px rgba(15, 23, 42, 0.08)',
                            }}
                            formatter={(v: number | string) =>
                              typeof v === 'number' ? v.toFixed(1) : v
                            }
                          />
                          <Legend wrapperStyle={{ fontSize: 12, paddingTop: 8 }} iconType="circle" />
                          <Bar
                            dataKey="subject_avg"
                            name={metricShort}
                            fill="#8f7be0"
                            radius={[4, 4, 0, 0]}
                            maxBarSize={28}
                          >
                            {chartData.map((_row, idx) => (
                              <Cell key={idx} fill="#8f7be0" fillOpacity={0.9} />
                            ))}
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                    )}
                  </CardContent>
                </Card>

                {/* 右：排名表 */}
                <Card className="lg:col-span-2">
                  <CardHeader className="pb-2">
                    <CardTitle className="text-base font-semibold text-slate-800">
                      <span className="inline-flex items-center gap-2">
                        教学班排名表
                        {/* E04：样本估算口径必须可见，绝不冒充年级/官方口径 */}
                        <Badge variant="secondary" className="text-[10px]">
                          样本估算
                        </Badge>
                      </span>
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    {isLoading ? (
                      <div className="space-y-2">
                        {Array.from({ length: 5 }).map((_, i) => (
                          <Skeleton key={i} className="h-10 w-full" />
                        ))}
                      </div>
                    ) : rankRows.length === 0 ? (
                      <EmptyState
                        title={selectedExam ? '该考试无对比数据' : '请先选择考试'}
                        desc={`未找到该场考试的${metricShort}数据。`}
                        height={320}
                      />
                    ) : (
                      <div className="overflow-hidden rounded-lg border border-slate-200">
                        <Table>
                          <TableHeader>
                            <TableRow className="bg-slate-50 hover:bg-slate-50">
                              <SortableHead
                                label="教学班"
                                active={sortKey === 'class_label'}
                                dir={sortDir}
                                onClick={() => onSort('class_label')}
                                icon={sortIcon('class_label')}
                              />
                              <SortableHead
                                label={metricShort}
                                active={sortKey === 'subject_avg'}
                                dir={sortDir}
                                onClick={() => onSort('subject_avg')}
                                icon={sortIcon('subject_avg')}
                                align="right"
                              />
                              <SortableHead
                                label="较总体均差"
                                active={sortKey === 'diff'}
                                dir={sortDir}
                                onClick={() => onSort('diff')}
                                icon={sortIcon('diff')}
                                align="right"
                              />
                              <SortableHead
                                label="名次"
                                active={sortKey === 'rank'}
                                dir={sortDir}
                                onClick={() => onSort('rank')}
                                icon={sortIcon('rank')}
                                align="right"
                              />
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {rankRows.map((row) => {
                              const positive = (row.diff ?? 0) >= 0
                              return (
                                <TableRow key={row.teaching_class_id} className="transition-colors">
                                  <TableCell className="font-medium text-slate-800">
                                    <div className="flex items-center gap-2">
                                      <span>{displayLabel(row.class_label)}</span>
                                      <span className="text-[10px] text-slate-400">
                                        {row.member_count}人
                                      </span>
                                    </div>
                                  </TableCell>
                                  <TableCell className="text-right tabular-nums text-slate-800">
                                    {row.subject_avg != null ? row.subject_avg.toFixed(1) : '—'}
                                  </TableCell>
                                  <TableCell
                                    className={cn(
                                      'text-right tabular-nums font-medium',
                                      row.diff == null
                                        ? 'text-slate-400'
                                        : positive
                                          ? 'text-success-500'
                                          : 'text-danger-500',
                                    )}
                                  >
                                    {row.diff == null ? (
                                      '—'
                                    ) : (
                                      <span className="inline-flex items-center justify-end">
                                        {positive ? (
                                          <ArrowUp className="mr-0.5 h-3.5 w-3.5" />
                                        ) : (
                                          <ArrowDown className="mr-0.5 h-3.5 w-3.5" />
                                        )}
                                        {Math.abs(row.diff).toFixed(1)}
                                      </span>
                                    )}
                                  </TableCell>
                                  <TableCell className="text-right tabular-nums text-slate-600">
                                    {row.rank != null ? `#${row.rank}` : '—'}
                                  </TableCell>
                                </TableRow>
                              )
                            })}
                          </TableBody>
                        </Table>
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>
        )}
      </div>
    </TooltipProvider>
  )
}

// ---------- 子组件 ----------

interface SortableHeadProps {
  label: string
  active: boolean
  dir: SortDir
  onClick: () => void
  icon: React.ReactNode
  align?: 'left' | 'right'
}

function SortableHead({ label, onClick, icon, align = 'left' }: SortableHeadProps) {
  return (
    <TableHead
      className={cn(
        'cursor-pointer select-none text-slate-600 hover:text-slate-900',
        align === 'right' && 'text-right',
      )}
      onClick={onClick}
    >
      <span className={cn('inline-flex items-center', align === 'right' && 'justify-end')}>
        {label}
        {icon}
      </span>
    </TableHead>
  )
}

function EmptyState({
  title,
  desc,
  height = 320,
}: {
  title: string
  desc?: string
  height?: number
}) {
  return (
    <div
      className="flex flex-col items-center justify-center text-center"
      style={{ height }}
    >
      <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-400">
        <Inbox className="h-6 w-6" />
      </div>
      <p className="text-sm font-medium text-slate-700">{title}</p>
      {desc && <p className="mt-1 max-w-[260px] text-xs text-slate-500">{desc}</p>}
    </div>
  )
}
