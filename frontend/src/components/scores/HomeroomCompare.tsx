'use client'

/**
 * 班主任工作台「班级对比」页（契约 p3-imports-analysis.md §2.1 class-averages）。
 *
 * 数据来自 /api/v1：shared/exams（考试清单）、homeroom/analysis/exams/{exam}/
 * class-averages（全年级官方班级均分表，ADR-025 官方事实）。与教学工作台的
 * 教学班样本对比是两回事：本页只读官方导入的班级均分表，绝不拿本班学生
 * 成绩反推年级数据。
 *
 * 计量红线（契约 §2.3）：均分缺值显示「—」不转 0；总分口径名次直接用后端
 * 官方排名（全零口径后端返回空 → 显示「—」不编造）；单科口径名次前端按
 * 各班均分降序、同分同名次计算；较年级均差 = 本班值 − 各班非空值简单平均。
 * 迟到回包按资源分离的序号丢弃（F11）。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  Tooltip as RTooltip,
  CartesianGrid,
  ResponsiveContainer,
} from 'recharts'
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronUp,
  ChevronsUpDown,
  ClipboardList,
  FileSpreadsheet,
} from 'lucide-react'
import { useSearchParams } from 'next/navigation'

import {
  fetchHomeroomClassAverages,
  listExams,
  type ExamSummary,
  type HomeroomClassAveragesResponse,
  type HomeroomClassAverageRow,
} from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { useWorkspace } from '@/lib/workspace'
import { formatClassLabel } from '@/lib/labels'
import { ExamSelect } from '@/components/scores/ExamSelect'
import { ScoreYearPicker } from '@/components/scores/ScoreYearPicker'
import { analysisScopeQuery, sortExamsDesc } from '@/components/scores/shared'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'

// 本班主题色沿用教学版对比页柱色；其他班灰色半透明弱化
const CURRENT_BAR_COLOR = '#8f7be0'
const OTHER_BAR_COLOR = '#94a3b8'

// 统计口径值前缀：区分总分口径与单科口径（单科名可能与总分口径撞名）
const TOTAL_PREFIX = '__total__'

function isTotalMetric(value: string): boolean {
  return value.startsWith(TOTAL_PREFIX)
}

function metricKey(value: string): string {
  return isTotalMetric(value) ? value.slice(TOTAL_PREFIX.length) : value
}

function metricValueOf(row: HomeroomClassAverageRow, value: string): number | null {
  const key = metricKey(value)
  return isTotalMetric(value) ? (row.totals[key] ?? null) : (row.subjects[key] ?? null)
}

interface CompareRow {
  row: HomeroomClassAverageRow
  value: number | null
  diff: number | null
  rank: number | null
  isCurrent: boolean
}

type SortKey = 'class_num' | 'value' | 'diff' | 'rank'
type SortDir = 'asc' | 'desc'

/** 单科口径名次：各班均分降序、同分同名次（min-rank）；缺值排后、
 * 全零口径（来源未提供）名次一律 null，不制造并列第一。 */
function deriveSubjectRanks(
  rows: HomeroomClassAverageRow[],
  metric: string,
): Map<number, number | null> {
  const valueOf = (row: HomeroomClassAverageRow) => row.subjects[metric] ?? null
  const nums = rows.map(valueOf).filter((v): v is number => v != null)
  const unavailable = nums.length === 0 || nums.every((v) => v === 0)
  const sorted = [...rows].sort((a, b) => {
    const av = valueOf(a)
    const bv = valueOf(b)
    if (av == null && bv == null) return a.class_num - b.class_num
    if (av == null) return 1
    if (bv == null) return -1
    if (av !== bv) return bv - av
    return a.class_num - b.class_num
  })
  const ranks = new Map<number, number | null>()
  let lastValue: number | null = null
  let lastRank = 0
  sorted.forEach((row, index) => {
    const v = valueOf(row)
    if (unavailable || v == null) {
      ranks.set(row.class_num, null)
      return
    }
    const rank = lastValue !== null && v === lastValue ? lastRank : index + 1
    lastValue = v
    lastRank = rank
    ranks.set(row.class_num, rank)
  })
  return ranks
}

export function HomeroomCompare() {
  const { filter, generation, switching } = useWorkspace()
  const searchParams = useSearchParams()
  const requestedExam = searchParams.get('exam')

  // 考试清单
  const [exams, setExams] = useState<ExamSummary[] | null>(null)
  const [examsError, setExamsError] = useState<string | null>(null)
  const [examNonce, setExamNonce] = useState(0)
  const [selectedExam, setSelectedExam] = useState<string | null>(null)

  // 官方班级均分表
  const [averages, setAverages] = useState<HomeroomClassAveragesResponse | null>(null)
  const [dataError, setDataError] = useState<string | null>(null)

  // 统计口径（下拉值；接口未返回的口径绝不出现）
  const [metric, setMetric] = useState<string | null>(null)

  // 请求序号按资源分离（F11）：考试清单与均分表各自比对各自序号
  const examsReqRef = useRef(0)
  const dataReqRef = useRef(0)

  const scopeQ = useMemo(() => analysisScopeQuery(filter), [filter])

  // 考试清单：跟随工作台筛选/世代变化整体重置；选中记忆沿用成绩分析页
  // （?exam= 指定优先，否则取最新一场）
  useEffect(() => {
    const req = ++examsReqRef.current
    setExams(null)
    setExamsError(null)
    setSelectedExam(null)
    listExams('homeroom', scopeQ)
      .then((r) => {
        if (req !== examsReqRef.current) return
        const sorted = sortExamsDesc(r.exams ?? [])
        setExams(sorted)
        if (sorted.length > 0) {
          setSelectedExam(
            sorted.some((exam) => exam.exam_name === requestedExam)
              ? requestedExam
              : sorted[0].exam_name,
          )
        }
      })
      .catch((err: unknown) => {
        if (req !== examsReqRef.current) return
        setExams([])
        setExamsError(apiErrorMessage(err))
      })
  }, [scopeQ, generation, examNonce, requestedExam])

  // 官方班级均分表（迟到回包按 dataReqRef 丢弃）
  useEffect(() => {
    if (selectedExam == null) return
    const req = ++dataReqRef.current
    setAverages(null)
    setDataError(null)
    fetchHomeroomClassAverages(selectedExam, scopeQ)
      .then((result) => {
        if (req !== dataReqRef.current) return
        setAverages(result)
      })
      .catch((err: unknown) => {
        if (req !== dataReqRef.current) return
        setAverages(null)
        setDataError(apiErrorMessage(err))
      })
  }, [selectedExam, scopeQ])

  // 统计口径由接口返回动态生成：total_types 在前（默认「主三门」，没有则取
  // 第一个），subjects 其后显示为「X 均分」；接口没返回的口径绝不出现
  const metricOptions = useMemo(() => {
    const opts: Array<{ value: string; label: string }> = []
    for (const t of averages?.total_types ?? []) {
      opts.push({ value: `${TOTAL_PREFIX}${t}`, label: t })
    }
    for (const s of averages?.subjects ?? []) {
      opts.push({ value: s, label: `${s} 均分` })
    }
    return opts
  }, [averages])

  const defaultMetric = useMemo(() => {
    const totals = averages?.total_types ?? []
    const preferred = totals.includes('主三门') ? '主三门' : totals[0]
    if (preferred != null) return `${TOTAL_PREFIX}${preferred}`
    return averages?.subjects?.[0] ?? null
  }, [averages])

  // 当前值失效时回退默认口径
  const activeMetric = metricOptions.some((o) => o.value === metric)
    ? (metric as string)
    : defaultMetric
  const activeMetricLabel =
    metricOptions.find((o) => o.value === activeMetric)?.label ?? '均分'

  const currentClassNum = averages?.current_class_num ?? null

  const compareRows = useMemo<CompareRow[]>(() => {
    if (averages == null || activeMetric == null) return []
    const key = metricKey(activeMetric)
    const valueOf = (row: HomeroomClassAverageRow) => metricValueOf(row, activeMetric)
    const nums = averages.rows.map(valueOf).filter((v): v is number => v != null)
    // 全零口径视为来源未提供：不作差（年级均分 null）、不编造名次
    const allZero = nums.length === 0 || nums.every((v) => v === 0)
    // 年级均分 = 各班该口径非空值的简单平均
    const gradeAvg = allZero
      ? null
      : nums.reduce((sum, v) => sum + v, 0) / nums.length
    const subjectRanks = isTotalMetric(activeMetric)
      ? null
      : deriveSubjectRanks(averages.rows, key)
    return averages.rows.map((row) => {
      const value = valueOf(row)
      // 总分口径名次直接用后端官方排名（全零口径为 null → 「—」）
      const rank = isTotalMetric(activeMetric)
        ? (row.total_ranks[key] ?? null)
        : (subjectRanks?.get(row.class_num) ?? null)
      return {
        row,
        value,
        diff: value != null && gradeAvg != null ? value - gradeAvg : null,
        rank,
        isCurrent: currentClassNum != null && row.class_num === currentClassNum,
      }
    })
  }, [averages, activeMetric, currentClassNum])

  // 柱状图数据：每班一根柱，本班高亮
  const chartData = useMemo(() => {
    return compareRows.map((c) => ({
      classLabel: `${String(c.row.class_num)}班`,
      value: c.value,
      isCurrent: c.isCurrent,
    }))
  }, [compareRows])

  // Y 轴上限取整到 10 的倍数，消除小数顶刻度
  const dataMax = useMemo(() => {
    const nums = compareRows.map((c) => c.value).filter((v): v is number => v != null)
    return nums.length === 0 ? 0 : Math.max(...nums)
  }, [compareRows])
  const yMax = Math.ceil((dataMax + 20) / 10) * 10

  // 排名表排序（默认按班号升序；表头可点击切换）
  const [sortKey, setSortKey] = useState<SortKey>('class_num')
  const [sortDir, setSortDir] = useState<SortDir>('asc')
  const sortValueOf = (c: CompareRow, key: SortKey): number | string | null => {
    if (key === 'class_num') return c.row.class_num
    return c[key]
  }
  const rankRows = useMemo(() => {
    return [...compareRows].sort((a, b) => {
      const dir = sortDir === 'asc' ? 1 : -1
      const va = sortValueOf(a, sortKey)
      const vb = sortValueOf(b, sortKey)
      if (typeof va === 'number' || typeof vb === 'number') {
        if (va == null && vb == null) return 0
        if (va == null) return 1
        if (vb == null) return -1
        return ((va as number) - (vb as number)) * dir
      }
      return String(va).localeCompare(String(vb), 'zh-Hans-CN') * dir
    })
  }, [compareRows, sortKey, sortDir])

  const onSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir(key === 'class_num' ? 'asc' : 'desc')
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

  const loadingExams = exams == null && examsError == null
  const isLoading = averages == null && dataError == null

  return (
    <div className="space-y-6">
      {/* ---------- 顶部标题 ---------- */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">班级对比</h1>
          <p className="mt-1 text-sm text-slate-500">
            同年级各行政班均分对比（官方班级均分表口径）
            {switching ? ' · 正在切换…' : ''}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setExamNonce((n) => n + 1)}
            disabled={loadingExams}
          >
            <ClipboardList className="h-4 w-4" />
            刷新考试
          </Button>
        </div>
      </div>

      {/* ---------- 筛选栏（不参与打印，U01） ---------- */}
      <Card className="print:hidden">
        <CardContent className="flex flex-col gap-3 py-4 sm:flex-row sm:items-end sm:gap-4">
          <ScoreYearPicker />
          <div className="space-y-1">
            <label className="text-xs font-medium text-slate-500">考试</label>
            <ExamSelect
              exams={exams ?? []}
              value={selectedExam}
              onChange={setSelectedExam}
              loading={loadingExams}
            />
          </div>
          {metricOptions.length > 0 ? (
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-500">统计口径</label>
              <Select value={activeMetric ?? undefined} onValueChange={setMetric}>
                <SelectTrigger className="h-8 w-[170px] text-xs" aria-label="统计口径">
                  <SelectValue placeholder="选择口径" />
                </SelectTrigger>
                <SelectContent>
                  {metricOptions.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}
          {currentClassNum != null ? (
            <div className="ml-auto flex items-center gap-2">
              <span className="text-xs text-slate-500">本班</span>
              <Badge
                variant="secondary"
                className="bg-brand-50 text-brand-700 hover:bg-brand-50"
              >
                {formatClassLabel(averages?.grade, currentClassNum) ?? `${currentClassNum}班`}
              </Badge>
            </div>
          ) : null}
        </CardContent>
      </Card>

      {examsError ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-8 text-center">
            <AlertCircle className="h-8 w-8 text-amber-400" />
            <p className="text-sm text-slate-600">{examsError}</p>
            <Button variant="outline" size="sm" onClick={() => setExamNonce((n) => n + 1)}>
              重试
            </Button>
          </CardContent>
        </Card>
      ) : exams != null && exams.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-2 py-10 text-center">
            <ClipboardList className="h-8 w-8 text-slate-300" />
            <p className="text-sm text-slate-600">当前范围暂无已导入考试</p>
            <p className="text-xs text-slate-400">
              可切换上方学年查看历史考试，或到「数据上传」同时导入
              「学生成绩明细表」和「班级均分表」后在此对比全年级各班。
            </p>
          </CardContent>
        </Card>
      ) : dataError ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-8 text-center">
            <AlertCircle className="h-8 w-8 text-amber-400" />
            <p className="text-sm text-slate-600">{dataError}</p>
            <Button variant="outline" size="sm" onClick={() => setExamNonce((n) => n + 1)}>
              重试
            </Button>
          </CardContent>
        </Card>
      ) : isLoading ? (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
          <Card className="lg:col-span-3">
            <CardContent className="py-6">
              <Skeleton className="h-[380px] w-full" />
            </CardContent>
          </Card>
          <Card className="lg:col-span-2">
            <CardContent className="space-y-2 py-6">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </CardContent>
          </Card>
        </div>
      ) : averages != null && averages.rows.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center">
            <FileSpreadsheet className="mx-auto h-9 w-9 text-slate-300" />
            <p className="mt-3 text-sm font-medium text-slate-700">该考试未导入班级均分表</p>
            <p className="mt-1 text-xs text-slate-400">
              在数据上传中同时选择「学生成绩明细表」和「班级均分表」，确认后这里会显示全年级各班对比。
            </p>
          </CardContent>
        </Card>
      ) : (
        /* ---------- 主体：柱状图 + 排名表 ---------- */
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
          {/* 左：柱状图 */}
          <Card className="lg:col-span-3">
            <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
              <div>
                <CardTitle className="text-base font-semibold text-slate-800">
                  各班{activeMetricLabel}
                </CardTitle>
                <CardDescription>
                  {selectedExam} · 来自官方班级均分表；主题色为本班，灰色为其他班。
                </CardDescription>
              </div>
              <Badge variant="secondary" className="shrink-0 text-[10px]">
                官方班级均分表
              </Badge>
            </CardHeader>
            <CardContent>
              {chartData.length === 0 ? (
                <p className="py-12 text-center text-sm text-slate-500">
                  该考试暂无班级均分数据
                </p>
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
                      domain={[0, yMax]}
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
                    <Bar
                      dataKey="value"
                      name={activeMetricLabel}
                      fill={CURRENT_BAR_COLOR}
                      radius={[4, 4, 0, 0]}
                      maxBarSize={28}
                    >
                      {chartData.map((entry, idx) => (
                        <Cell
                          key={idx}
                          fill={entry.isCurrent ? CURRENT_BAR_COLOR : OTHER_BAR_COLOR}
                          fillOpacity={entry.isCurrent ? 0.9 : 0.35}
                        />
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
                班级排名表
              </CardTitle>
              <CardDescription>
                总分口径名次来自班级均分表官方排名；单科名次按各班均分降序、同分同名次计算。
              </CardDescription>
            </CardHeader>
            <CardContent>
              {rankRows.length === 0 ? (
                <p className="py-12 text-center text-sm text-slate-500">
                  该考试暂无班级均分数据
                </p>
              ) : (
                <div className="overflow-hidden rounded-lg border border-slate-200">
                  <Table>
                    <TableHeader>
                      <TableRow className="bg-slate-50 hover:bg-slate-50">
                        <SortableHead
                          label="班级"
                          active={sortKey === 'class_num'}
                          dir={sortDir}
                          onClick={() => onSort('class_num')}
                          icon={sortIcon('class_num')}
                        />
                        <SortableHead
                          label="均分"
                          active={sortKey === 'value'}
                          dir={sortDir}
                          onClick={() => onSort('value')}
                          icon={sortIcon('value')}
                          align="right"
                        />
                        <SortableHead
                          label="较年级均差"
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
                      {rankRows.map((entry) => {
                        // 缺差值显示「—」；符号只对非空 diff 判定（缺值绝不折算成 0 参与显示）
                        const positive = entry.diff != null && entry.diff >= 0
                        const experimental = /实验/.test(entry.row.class_type ?? '')
                        return (
                          <TableRow
                            key={String(entry.row.class_num)}
                            className={cn(
                              'transition-colors',
                              entry.isCurrent && 'border-l-[3px] border-[#1f7fd6] bg-brand-50/60',
                            )}
                          >
                            <TableCell className="font-medium text-slate-800">
                              <div className="flex items-center gap-2">
                                <span>{String(entry.row.class_num)}班</span>
                                {experimental ? (
                                  <Badge variant="secondary" className="text-[10px]">
                                    {entry.row.class_type}
                                  </Badge>
                                ) : null}
                                {entry.isCurrent ? (
                                  <Badge
                                    variant="secondary"
                                    className="bg-brand-50 text-[10px] text-brand-700 hover:bg-brand-50"
                                  >
                                    本班
                                  </Badge>
                                ) : null}
                              </div>
                            </TableCell>
                            <TableCell className="text-right tabular-nums text-slate-800">
                              {entry.value != null ? entry.value.toFixed(1) : '—'}
                            </TableCell>
                            <TableCell
                              className={cn(
                                'text-right tabular-nums font-medium',
                                entry.diff == null
                                  ? 'text-slate-400'
                                  : positive
                                    ? 'text-success-500'
                                    : 'text-danger-500',
                              )}
                            >
                              {entry.diff == null ? (
                                '—'
                              ) : (
                                <span className="inline-flex items-center justify-end">
                                  {positive ? (
                                    <ArrowUp className="mr-0.5 h-3.5 w-3.5" />
                                  ) : (
                                    <ArrowDown className="mr-0.5 h-3.5 w-3.5" />
                                  )}
                                  {Math.abs(entry.diff).toFixed(1)}
                                </span>
                              )}
                            </TableCell>
                            <TableCell className="text-right tabular-nums text-slate-600">
                              {entry.rank != null ? `#${String(entry.rank)}` : '—'}
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
