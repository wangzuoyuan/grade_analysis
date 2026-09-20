'use client'

/**
 * 班主任工作台成绩分析页（契约 p3-imports-analysis.md §2.1/§3）。
 *
 * 数据全部来自 /api/v1：shared/exams（考试清单）、homeroom/analysis/*（stats/students/bands/trends）。
 * 计量红线（契约 §2.3）：缺考（score NULL）一律显示「—」不转 0；名次不在本页展示，
 * 表格按总分降序仅为浏览排序；shared_conflict 格子警示并注明教学域分数待人工核对；
 * 趋势按学年分段展示，不跨年连线；迟到回包按资源分离的序号丢弃（F11：任一资源重拉
 * 绝不使其他资源的合法回包失效），bands 在总分具备年级名次时计算，
 * 无名次口径的 409 呈现「不可计算」提示卡（F10）。
 */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle, BarChart3, CheckCircle2, ChevronDown, ClipboardList, Download, FileSpreadsheet, ListChecks, Search, TrendingUp, Users } from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useSearchParams } from 'next/navigation'

import {
  ApiV1Error,
  confirmCanonicalScores,
  fetchHomeroomClassAverages,
  fetchHomeroomBands,
  fetchHomeroomFocus,
  fetchHomeroomRankDistribution,
  fetchHomeroomRankFrequency,
  fetchHomeroomRankMetrics,
  fetchHomeroomRankRange,
  fetchHomeroomStats,
  fetchHomeroomStudents,
  fetchHomeroomTrends,
  listExams,
  listLinks,
  listScoreConflicts,
  normalizeSharedConflicts,
  type CanonicalResolution,
  type CanonicalSide,
  type ExamSummary,
  type HomeroomBandsResponse,
  type HomeroomClassAverageRow,
  type HomeroomClassAveragesResponse,
  type HomeroomFocusResponse,
  type HomeroomRankDistributionResponse,
  type HomeroomRankFrequencyResponse,
  type HomeroomRankMetric,
  type HomeroomRankRangeResponse,
  type HomeroomStatsResponse,
  type HomeroomStudentRow,
  type HomeroomTrendsResponse,
  type LinkSharedConflict,
  type PersonId,
  type ScoreConflictItem,
} from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { useWorkspace } from '@/lib/workspace'
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { CorrelationCard } from '@/components/homework/CorrelationCard'
import { homeworkScopeQuery } from '@/components/homework/shared'
import { ExamSelect } from './ExamSelect'
import { ScoreYearPicker } from './ScoreYearPicker'
import { analysisScopeQuery, formatScore, sortExamsDesc } from './shared'

const TREND_COLORS = ['#1f7fd6', '#22b7d9', '#39a97b', '#f0a83a', '#8b5cf6', '#e05a68', '#64748b', '#0d9488', '#c2410c']

function TrendChart({
  title,
  series,
}: {
  title: string
  series: Record<string, Array<{ exam_name: string; exam_date: string | null; rank: number | null; rank_basis: 'school' | 'grade_percentile' | null }>>
}) {
  const entries = Object.entries(series)
  const [hiddenMetrics, setHiddenMetrics] = useState<Set<string>>(() => new Set())
  if (entries.length === 0) return null
  const exams = new Map<string, { exam: string; date: string; order: string; [key: string]: string | number | null }>()
  for (const [metric, points] of entries) {
    for (const point of points) {
      const id = `${point.exam_name}|${point.exam_date ?? ''}`
      const row = exams.get(id) ?? {
        exam: point.exam_name,
        date: point.exam_date ? point.exam_date.slice(5, 10) : '日期未知',
        order: point.exam_date ?? point.exam_name,
      }
      row[metric] = point.rank
      row[`${metric}__basis`] = point.rank_basis ?? '不可用'
      exams.set(id, row)
    }
  }
  const data = [...exams.values()].sort((a, b) => a.order.localeCompare(b.order))
  const usesPercentile = entries.some(([, points]) => points.some((point) => point.rank_basis === 'grade_percentile'))
  const toggleMetric = (metric: string) => {
    setHiddenMetrics((current) => {
      const next = new Set(current)
      if (next.has(metric)) next.delete(metric)
      else next.add(metric)
      return next
    })
  }
  return (
    <div className="space-y-2">
      <p className="text-xs font-medium text-slate-600">{title}</p>
      <div className="h-56 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 18, bottom: 4, left: -12 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e8f1f8" />
            <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#7890a8' }} />
            <YAxis
              reversed
              allowDecimals={usesPercentile}
              domain={usesPercentile ? [0, 100] : ['auto', 'auto']}
              tick={{ fontSize: 11, fill: '#7890a8' }}
              tickFormatter={(value) => usesPercentile ? `${String(value)}%` : String(value)}
              label={{ value: usesPercentile ? '年级排名百分位' : '年级名次', angle: -90, position: 'insideLeft', fill: '#7890a8', fontSize: 11 }}
            />
            <Tooltip<number, string>
              labelFormatter={(_, payload) => payload?.[0]?.payload?.exam ?? ''}
              formatter={(value, name, item) => {
                const basis = item.payload?.[`${String(name)}__basis`]
                const label = basis === 'grade_percentile'
                  ? `年级排名百分位 ${String(value)}%（越小越靠前）`
                  : `第 ${String(value)} 名（学籍/年级名次）`
                return [value == null ? '—' : label, name]
              }}
            />
            {entries.map(([metric], index) => (
              <Line
                key={metric}
                type="monotone"
                dataKey={metric}
                name={metric}
                stroke={TREND_COLORS[index % TREND_COLORS.length]}
                strokeWidth={2}
                dot={{ r: 3, fill: '#fff', strokeWidth: 2 }}
                connectNulls={false}
                hide={hiddenMetrics.has(metric)}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="flex min-h-8 flex-wrap items-center justify-center gap-x-1 gap-y-1" aria-label={`${title}图线选择`}>
        {entries.map(([metric], index) => {
          const hidden = hiddenMetrics.has(metric)
          const color = TREND_COLORS[index % TREND_COLORS.length]
          return (
            <button
              key={metric}
              type="button"
              aria-pressed={!hidden}
              aria-label={`${hidden ? '显示' : '隐藏'}${metric}图线`}
              onClick={() => toggleMetric(metric)}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300',
                hidden ? 'text-slate-400 line-through hover:bg-slate-100' : 'text-slate-600 hover:bg-brand-50',
              )}
              title={`点击${hidden ? '显示' : '隐藏'}${metric}图线`}
            >
              <span
                aria-hidden="true"
                className={cn('h-0.5 w-4 rounded-full transition-opacity', hidden && 'opacity-30')}
                style={{ backgroundColor: color }}
              />
              {metric}
            </button>
          )
        })}
      </div>
    </div>
  )
}

/** 规范值确认单选行（契约 §1.6 v2.3）：按域选择、每域一个单选——任一
 * 侧均可选（缺考侧标注「确认为缺考」：null 是现行业务状态，核实缺考是
 * 合法规范结果），本阶段不做人工改分。 */
function ConflictChoiceRow({
  item,
  value,
  onSelect,
}: {
  item: ScoreConflictItem
  value: CanonicalSide | undefined
  onSelect: (side: CanonicalSide) => void
}) {
  const key = `${String(item.person_id)}|${item.subject}|${item.exam_name}`
  const options: Array<{ domain: CanonicalSide; label: string; score: number | null }> = [
    { domain: 'homeroom', label: '班主任域', score: item.homeroom_score },
    { domain: 'teaching', label: '教学域', score: item.teaching_score },
  ]
  return (
    <div className="rounded-lg border border-slate-100 bg-white px-3 py-2">
      <p className="text-xs font-medium text-slate-700">
        {item.name ?? `学生 ${String(item.person_id)}`} · {item.subject} · {item.exam_name}
        {item.exam_date ? <span className="ml-2 font-normal text-slate-400">{item.exam_date.slice(0, 10)}</span> : null}
      </p>
      <div className="mt-1.5 flex flex-wrap gap-x-5 gap-y-1">
        {options.map((o) => (
          <label
            key={o.domain}
            className="flex cursor-pointer items-center gap-1.5 text-sm text-slate-600"
          >
            <input
              type="radio"
              name={key}
              checked={value === o.domain}
              onChange={() => onSelect(o.domain)}
            />
            <span>
              {o.label}{' '}
              {o.score == null ? '缺考 —（确认为缺考）' : `${formatScore(o.score)} 分`}
            </span>
          </label>
        ))}
      </div>
    </div>
  )
}

/** 行展开区：跨学年趋势（E03 按学年分段，绝不跨年连线/合并）。 */
function TrendPanel({
  trends,
  error,
  onRetry,
}: {
  trends: HomeroomTrendsResponse | null
  error: string | null
  onRetry: () => void
}) {
  if (error) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-2 py-3">
        <p className="text-sm text-danger-500">{error}</p>
        <Button type="button" variant="outline" size="sm" onClick={onRetry}>
          重试
        </Button>
      </div>
    )
  }
  if (trends == null) {
    return <Skeleton className="h-24 w-full" />
  }
  if (trends.years.length === 0) {
    return <p className="py-3 text-sm text-slate-400">该生暂无历史成绩</p>
  }
  return (
    <div className="space-y-3">
      {trends.years.map((year) => {
        const hasLines = Object.keys(year.subjects).length > 0 || Object.keys(year.totals).length > 0
        return (
          <div key={year.academic_year_id} className="rounded-lg border border-slate-100 bg-white px-4 py-3">
            <p className="mb-3 text-sm font-semibold text-slate-700">{year.academic_year_name}</p>
            {!hasLines ? (
              <p className="mt-1 text-xs text-slate-400">本学年暂无成绩</p>
            ) : (
              <div className="grid gap-5 xl:grid-cols-2">
                <TrendChart title="单科年级排名趋势" series={year.subjects} />
                <TrendChart title="总分排名趋势" series={year.totals} />
              </div>
            )}
          </div>
        )
      })}
      <p className="text-[10px] text-slate-400">跨学年成绩分段展示，不同学年总分口径不直接比较。</p>
    </div>
  )
}

function summaryValue(
  rows: HomeroomClassAverageRow[],
  values: (row: HomeroomClassAverageRow) => number | null | undefined,
  kind: '平均' | '最高' | '最低',
): number | null {
  const nums = rows.map(values).filter((value): value is number => typeof value === 'number')
  if (nums.length === 0) return null
  if (nums.every((value) => value === 0)) return null
  if (kind === '最高') return Math.max(...nums)
  if (kind === '最低') return Math.min(...nums)
  return Math.round((nums.reduce((sum, value) => sum + value, 0) / nums.length) * 100) / 100
}

function ClassAveragesTable({ data }: { data: HomeroomClassAveragesResponse | null }) {
  if (data == null) {
    return <Card><CardContent className="py-8 text-center text-sm text-slate-500">正在读取班级均分表…</CardContent></Card>
  }
  if (data.rows.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center">
          <FileSpreadsheet className="mx-auto h-9 w-9 text-slate-300" />
          <p className="mt-3 text-sm font-medium text-slate-700">这场考试还没有导入班级均分表</p>
          <p className="mt-1 text-xs text-slate-400">在数据上传中同时选择“学生成绩明细表”和“班级均分表”，确认后这里会显示全年级各班数据。</p>
        </CardContent>
      </Card>
    )
  }
  const resolved = data

  const groups = Array.from(
    resolved.rows.reduce((map, row) => {
      const key = row.class_type?.trim() || '未分类班级'
      const items = map.get(key) ?? []
      items.push(row)
      map.set(key, items)
      return map
    }, new Map<string, HomeroomClassAverageRow[]>()),
  )

  function exportCsv() {
    const header = ['班级类型', '班级', '班主任', ...resolved.subjects]
    for (const totalType of resolved.total_types) header.push(`${totalType}分数`, `${totalType}排名`)
    const rows = resolved.rows.map((row) => {
      const values: Array<string | number> = [row.class_type ?? '', row.class_num, row.teacher_name ?? '']
      for (const subject of resolved.subjects) values.push(row.subjects[subject] ?? '')
      for (const totalType of resolved.total_types) values.push(row.totals[totalType] ?? '', row.total_ranks[totalType] ?? '')
      return values
    })
    const csv = [header, ...rows]
      .map((row) => row.map((value) => `"${String(value).replaceAll('"', '""')}"`).join(','))
      .join('\n')
    const url = URL.createObjectURL(new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${resolved.exam_name}-班级均分表.csv`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  const summaryKinds = ['平均', '最高', '最低'] as const
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-3 space-y-0">
        <div>
          <CardTitle>全年级班级均分表</CardTitle>
          <CardDescription>{data.exam_name} · 来自已导入的班级均分表；总分排名按全部班级计算。</CardDescription>
        </div>
        <Button variant="outline" size="sm" onClick={exportCsv} className="shrink-0 print:hidden">
          <Download className="h-4 w-4" />导出数据
        </Button>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <Table className="min-w-max">
            <TableHeader>
              <TableRow className="bg-slate-100">
                <TableHead rowSpan={2} className="whitespace-nowrap border-r text-center text-xs">班级类型</TableHead>
                <TableHead rowSpan={2} className="whitespace-nowrap border-r text-center text-xs">班级</TableHead>
                <TableHead rowSpan={2} className="whitespace-nowrap border-r text-center text-xs">班主任</TableHead>
                {data.subjects.map((subject) => <TableHead key={subject} rowSpan={2} className="whitespace-nowrap border-r text-center text-xs">{subject.replace('_', '')}</TableHead>)}
                {data.total_types.map((totalType) => <TableHead key={totalType} colSpan={2} className="whitespace-nowrap border-r text-center text-xs font-semibold">{totalType}总分</TableHead>)}
              </TableRow>
              <TableRow className="bg-slate-50">
                {data.total_types.flatMap((totalType) => [
                  <TableHead key={`${totalType}-score`} className="whitespace-nowrap text-center text-xs">分数</TableHead>,
                  <TableHead key={`${totalType}-rank`} className="whitespace-nowrap border-r text-center text-xs">排名</TableHead>,
                ])}
              </TableRow>
            </TableHeader>
            <TableBody>
              {groups.flatMap(([groupName, rows]) => [
                ...rows.map((row) => (
                  <TableRow key={`${groupName}-${String(row.class_num)}`}>
                    <TableCell className="whitespace-nowrap border-r text-xs text-slate-500">{groupName}</TableCell>
                    <TableCell className="border-r text-center text-sm font-medium text-brand-700">{String(row.class_num).padStart(2, '0')}</TableCell>
                    <TableCell className="whitespace-nowrap border-r text-sm">{row.teacher_name ?? '—'}</TableCell>
                    {data.subjects.map((subject) => <TableCell key={subject} className="border-r text-right tabular-nums text-sm">{formatScore(row.subjects[subject])}</TableCell>)}
                    {data.total_types.flatMap((totalType) => [
                      <TableCell key={`${totalType}-score`} className="text-right tabular-nums text-sm">{formatScore(row.totals[totalType])}</TableCell>,
                      <TableCell key={`${totalType}-rank`} className="border-r text-center tabular-nums text-sm font-medium">{row.total_ranks[totalType] ?? '—'}</TableCell>,
                    ])}
                  </TableRow>
                )),
                ...summaryKinds.map((kind) => (
                  <TableRow key={`${groupName}-${kind}`} className={kind === '最高' ? 'bg-orange-50' : kind === '最低' ? 'bg-slate-100' : 'bg-slate-50/70'}>
                    <TableCell className="whitespace-nowrap border-r text-xs font-semibold text-slate-600">{groupName}汇总</TableCell>
                    <TableCell className="border-r text-center text-xs font-semibold">{kind}</TableCell>
                    <TableCell className="border-r" />
                    {data.subjects.map((subject) => <TableCell key={subject} className={cn('border-r text-right tabular-nums text-sm font-semibold', kind === '最高' && 'bg-orange-400 text-white', kind === '最低' && 'bg-slate-600 text-white')}>{formatScore(summaryValue(rows, (row) => row.subjects[subject], kind))}</TableCell>)}
                    {data.total_types.flatMap((totalType) => [
                      <TableCell key={`${totalType}-score`} className={cn('text-right tabular-nums text-sm font-semibold', kind === '最高' && 'bg-orange-400 text-white', kind === '最低' && 'bg-slate-600 text-white')}>{formatScore(summaryValue(rows, (row) => row.totals[totalType], kind))}</TableCell>,
                      <TableCell key={`${totalType}-rank`} className="border-r" />,
                    ])}
                  </TableRow>
                )),
              ])}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  )
}

export function HomeroomScores() {
  const { filter, generation, switching, scope } = useWorkspace()
  const searchParams = useSearchParams()
  const requestedExam = searchParams.get('exam')
  // Q10：active 关联 id（homeroom 模式由后端 scope 解析；无关联时确认入口隐藏）
  const linkId = scope?.link_id ?? null

  // 考试清单
  const [exams, setExams] = useState<ExamSummary[] | null>(null)
  const [examsError, setExamsError] = useState<string | null>(null)
  const [examNonce, setExamNonce] = useState(0)
  const [selectedExam, setSelectedExam] = useState<string | null>(null)

  // 当前考试的 stats + 学生表
  const [stats, setStats] = useState<HomeroomStatsResponse | null>(null)
  const [students, setStudents] = useState<HomeroomStudentRow[] | null>(null)
  const [classAverages, setClassAverages] = useState<HomeroomClassAveragesResponse | null>(null)
  const [dataError, setDataError] = useState<string | null>(null)

  // 段位分布
  const [bandMetric, setBandMetric] = useState<string>('__total__')
  const [bands, setBands] = useState<HomeroomBandsResponse | null>(null)
  const [bandsError, setBandsError] = useState<string | null>(null)
  // F10/F11：后端 bands 对无名次口径返回 409（确定性不可算），区别于网络/瞬时错误展示
  const [bandsUnavailable, setBandsUnavailable] = useState(false)
  const [bandsNonce, setBandsNonce] = useState(0)

  // 旧班主任重点关注：临界段、薄弱段、严重偏科
  const [focus, setFocus] = useState<HomeroomFocusResponse | null>(null)
  const [focusError, setFocusError] = useState<string | null>(null)

  // 旧考试详情页：顶部排名分段 + 排名频次 + 排名区间筛选。
  const [rankDistribution, setRankDistribution] = useState<HomeroomRankDistributionResponse | null>(null)
  const [rankDistributionError, setRankDistributionError] = useState<string | null>(null)
  const [frequencyMetrics, setFrequencyMetrics] = useState<HomeroomRankMetric[]>([])
  const [rangeMetrics, setRangeMetrics] = useState<HomeroomRankMetric[]>([])
  const [frequencyMetric, setFrequencyMetric] = useState<string | null>(null)
  const [rangeMetric, setRangeMetric] = useState<string | null>(null)
  const [frequencyExamNames, setFrequencyExamNames] = useState<string[]>([])
  const [frequency, setFrequency] = useState<HomeroomRankFrequencyResponse | null>(null)
  const [frequencyError, setFrequencyError] = useState<string | null>(null)
  const [rangeMin, setRangeMin] = useState(1)
  const [rangeMax, setRangeMax] = useState(100)
  const [rankRange, setRankRange] = useState<HomeroomRankRangeResponse | null>(null)
  const [rankRangeError, setRankRangeError] = useState<string | null>(null)

  // 行展开趋势（一次只展开一人，避免并发趋势请求堆叠）
  const [expandedPerson, setExpandedPerson] = useState<string | null>(null)
  const [trends, setTrends] = useState<HomeroomTrendsResponse | null>(null)
  const [trendsError, setTrendsError] = useState<string | null>(null)
  const [trendsNonce, setTrendsNonce] = useState(0)

  // 请求序号按资源分离（F11）：考试清单、stats+students、bands、trends 各自比对各自
  // 序号——共用一个序号时，切考试后同轮 bands/trends effect 的递增会把 stats/students
  // 的合法回包当作过期丢弃，造成统计卡与学生表骨架永久加载。
  const examsReqRef = useRef(0)
  const dataReqRef = useRef(0)
  const bandsReqRef = useRef(0)
  const focusReqRef = useRef(0)
  const distributionReqRef = useRef(0)
  const metricsReqRef = useRef(0)
  const frequencyReqRef = useRef(0)
  const rangeReqRef = useRef(0)
  const trendsReqRef = useRef(0)
  const conflictsReqRef = useRef(0)

  // Q10：确认成功后 +1，触发 stats/students 重拉（冲突标记随之消失）
  const [dataNonce, setDataNonce] = useState(0)

  const scopeQ = useMemo(() => analysisScopeQuery(filter), [filter])

  // 相关性卡走作业域作用域（homeworkScopeQuery，不带 term_id），考试跟随页面顶部选择
  const hwScopeQ = useMemo(() => homeworkScopeQuery(filter), [filter])
  const scopeSubject = scope?.subject ?? null

  // Q10：关联学科（冲突只可能出现在 link.subject 上）。后端学生表
  // shared_conflicts 为逐人 flat 形状 {"teaching_score": X}（契约测试冻结，
  // 无学科键），归一化失败时用 link.subject 反推冲突学科格。
  const [linkSubject, setLinkSubject] = useState<string | null>(null)
  useEffect(() => {
    if (linkId == null) {
      setLinkSubject(null)
      return
    }
    let stale = false
    listLinks(scopeQ.academic_year_id)
      .then((r) => {
        if (!stale) {
          const found = (r.links ?? []).find((l) => l.id === linkId)
          setLinkSubject(found?.subject ?? null)
        }
      })
      .catch(() => {
        if (!stale) setLinkSubject(null)
      })
    return () => {
      stale = true
    }
  }, [linkId, scopeQ.academic_year_id])

  // 考试清单：跟随工作台筛选/世代变化整体重置（仅考试列表自身状态；
  // stats/students/bands/trends 由各自 effect 的 scopeQ/考试依赖负责重置，互不作废）
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
          setSelectedExam(sorted.some((exam) => exam.exam_name === requestedExam) ? requestedExam : sorted[0].exam_name)
          setFrequencyExamNames(sorted.slice(0, 5).map((exam) => exam.exam_name))
        }
      })
      .catch((err: unknown) => {
        if (req !== examsReqRef.current) return
        setExams([])
        setExamsError(apiErrorMessage(err))
      })
  }, [scopeQ, generation, examNonce, requestedExam])

  // 当前考试的 stats + 学生表（并行拉取，任一失败进统一错误态；只比对 dataReqRef）
  useEffect(() => {
    if (selectedExam == null) return
    const req = ++dataReqRef.current
    setStats(null)
    setStudents(null)
    setClassAverages(null)
    setDataError(null)
    setExpandedPerson(null)
    setTrends(null)
    setTrendsError(null)
    Promise.all([
      fetchHomeroomStats(selectedExam, scopeQ),
      fetchHomeroomStudents(selectedExam, scopeQ),
      fetchHomeroomClassAverages(selectedExam, scopeQ),
    ])
      .then(([s, st, averages]) => {
        if (req !== dataReqRef.current) return
        setStats(s)
        setStudents(st.students ?? [])
        setClassAverages(averages)
      })
      .catch((err: unknown) => {
        if (req !== dataReqRef.current) return
        setStats(null)
        setStudents(null)
        setClassAverages(null)
        setDataError(apiErrorMessage(err))
      })
  }, [selectedExam, scopeQ, dataNonce])

  // 列头顺序以 stats.subjects 为准（任务书 §/homeroom/scores）
  const subjectCols = useMemo(() => (stats?.subjects ?? []).map((s) => s.subject), [stats])
  const totalCols = useMemo(() => (stats?.totals ?? []).map((t) => t.total_type), [stats])

  // 段位指标选项：各总分口径（value 前缀 __total__ 区分，subject 参数承载 total_type）+ 各科；
  // 当前值失效时回退首个选项
  const metricOptions = useMemo(() => {
    const opts: Array<{ value: string; label: string }> = []
    for (const t of stats?.totals ?? []) opts.push({ value: `__total__${t.total_type}`, label: `总分（${t.total_type}）` })
    for (const s of subjectCols) opts.push({ value: s, label: s })
    return opts
  }, [stats, subjectCols])
  const activeMetric =
    metricOptions.find((o) => o.value === bandMetric)?.value ?? metricOptions[0]?.value ?? null

  // 段位分布：考试或指标变化即重拉（只比对 bandsReqRef，绝不作废 stats/students 回包）
  useEffect(() => {
    if (selectedExam == null || activeMetric == null) return
    const req = ++bandsReqRef.current
    setBands(null)
    setBandsError(null)
    setBandsUnavailable(false)
    fetchHomeroomBands(
      activeMetric.startsWith('__total__')
        ? { exam_name: selectedExam, metric: 'total', subject: activeMetric.slice('__total__'.length), ...scopeQ }
        : { exam_name: selectedExam, metric: 'score', subject: activeMetric, ...scopeQ },
    )
      .then((b) => {
        if (req !== bandsReqRef.current) return
        setBands(b)
      })
      .catch((err: unknown) => {
        if (req !== bandsReqRef.current) return
        setBands(null)
        setBandsUnavailable(err instanceof ApiV1Error && err.status === 409)
        setBandsError(apiErrorMessage(err))
      })
  }, [selectedExam, activeMetric, scopeQ, bandsNonce])

  useEffect(() => {
    if (selectedExam == null) {
      setFocus(null)
      setFocusError(null)
      return
    }
    const req = ++focusReqRef.current
    setFocus(null)
    setFocusError(null)
    fetchHomeroomFocus(selectedExam, scopeQ)
      .then((result) => {
        if (req === focusReqRef.current) setFocus(result)
      })
      .catch((err: unknown) => {
        if (req !== focusReqRef.current) return
        setFocus(null)
        setFocusError(apiErrorMessage(err))
      })
  }, [selectedExam, scopeQ, dataNonce])

  useEffect(() => {
    const req = ++metricsReqRef.current
    Promise.all([
      fetchHomeroomRankMetrics('frequency', scopeQ),
      fetchHomeroomRankMetrics('range', scopeQ),
    ])
      .then(([frequencyResult, rangeResult]) => {
        if (req !== metricsReqRef.current) return
        setFrequencyMetrics(frequencyResult.metrics)
        setRangeMetrics(rangeResult.metrics)
        setFrequencyMetric((current) =>
          frequencyResult.metrics.some((metric) => metric.value === current)
            ? current
            : frequencyResult.metrics[0]?.value ?? null,
        )
        setRangeMetric((current) =>
          rangeResult.metrics.some((metric) => metric.value === current)
            ? current
            : rangeResult.metrics[0]?.value ?? null,
        )
      })
      .catch(() => {
        if (req !== metricsReqRef.current) return
        setFrequencyMetrics([])
        setRangeMetrics([])
      })
  }, [scopeQ, generation])

  useEffect(() => {
    if (selectedExam == null) return
    const req = ++distributionReqRef.current
    setRankDistribution(null)
    setRankDistributionError(null)
    fetchHomeroomRankDistribution(selectedExam, scopeQ)
      .then((result) => {
        if (req === distributionReqRef.current) setRankDistribution(result)
      })
      .catch((err: unknown) => {
        if (req !== distributionReqRef.current) return
        setRankDistributionError(apiErrorMessage(err))
      })
  }, [selectedExam, scopeQ, dataNonce])

  useEffect(() => {
    if (frequencyMetric == null || frequencyExamNames.length === 0) return
    const req = ++frequencyReqRef.current
    setFrequency(null)
    setFrequencyError(null)
    fetchHomeroomRankFrequency(frequencyMetric, frequencyExamNames, scopeQ)
      .then((result) => {
        if (req === frequencyReqRef.current) setFrequency(result)
      })
      .catch((err: unknown) => {
        if (req !== frequencyReqRef.current) return
        setFrequencyError(apiErrorMessage(err))
      })
  }, [frequencyMetric, frequencyExamNames, scopeQ])

  useEffect(() => {
    if (selectedExam == null || rangeMetric == null) return
    const req = ++rangeReqRef.current
    setRankRange(null)
    setRankRangeError(null)
    fetchHomeroomRankRange(selectedExam, rangeMetric, rangeMin, rangeMax, scopeQ)
      .then((result) => {
        if (req === rangeReqRef.current) setRankRange(result)
      })
      .catch((err: unknown) => {
        if (req !== rangeReqRef.current) return
        setRankRangeError(apiErrorMessage(err))
      })
  }, [selectedExam, rangeMetric, rangeMin, rangeMax, scopeQ])

  // 行展开趋势：懒加载，切换人选/范围即重拉（只比对 trendsReqRef）
  useEffect(() => {
    if (expandedPerson == null) {
      setTrends(null)
      setTrendsError(null)
      return
    }
    const req = ++trendsReqRef.current
    setTrends(null)
    setTrendsError(null)
    fetchHomeroomTrends(expandedPerson, scopeQ)
      .then((t) => {
        if (req !== trendsReqRef.current) return
        setTrends(t)
      })
      .catch((err: unknown) => {
        if (req !== trendsReqRef.current) return
        setTrendsError(apiErrorMessage(err))
      })
  }, [expandedPerson, scopeQ, trendsNonce])

  // 学生表排序：按主 total_type（第一个）降序，缺考排后；仅浏览排序，非正式名次
  const sortedStudents = useMemo(() => {
    const primaryTotal = totalCols[0] ?? null
    const list = (students ?? []).slice()
    list.sort((a, b) => {
      if (primaryTotal != null) {
        const av = a.totals?.[primaryTotal] ?? null
        const bv = b.totals?.[primaryTotal] ?? null
        if (av != null && bv == null) return -1
        if (av == null && bv != null) return 1
        if (av != null && bv != null && av !== bv) return bv - av
      }
      return (a.name ?? '').localeCompare(b.name ?? '', 'zh-CN')
    })
    return list
  }, [students, totalCols])

  // 冲突归一：person_id →（学科 → 教学域记录分）。
  // 后端 /analysis/students 的 shared_conflicts 为逐人 flat 形状
  // {"teaching_score": X}（无学科键，契约测试已冻结该形状），归一化读不到
  // 学科时按 link.subject 反推冲突学科格（冲突只可能出现在任教学科上）。
  const conflictByPerson = useMemo(() => {
    const m = new Map<string, Map<string, LinkSharedConflict>>()
    for (const s of students ?? []) {
      const map = normalizeSharedConflicts(s.shared_conflicts)
      const raw: unknown = s.shared_conflicts
      if (map.size === 0 && linkSubject != null && raw != null && typeof raw === 'object' && !Array.isArray(raw)) {
        const teaching = (raw as Record<string, unknown>).teaching_score
        if (typeof teaching === 'number' || teaching === null) {
          map.set(linkSubject, { teaching_score: teaching })
        }
      }
      m.set(String(s.person_id), map)
    }
    return m
  }, [students, linkSubject])

  // ─────────── Q10 规范值确认（契约 p3 §1.6） ───────────
  const [conflictOpen, setConflictOpen] = useState(false)
  const [conflictTarget, setConflictTarget] = useState<{
    personId: PersonId
    personName: string
    subject: string
    examName: string
  } | null>(null)
  const [conflictList, setConflictList] = useState<ScoreConflictItem[] | null>(null)
  const [conflictLoading, setConflictLoading] = useState(false)
  const [conflictError, setConflictError] = useState<string | null>(null)
  // 选择集：conflictKey → 选定来源侧（v2.3：按域选择，缺考侧同样可选）
  const [choices, setChoices] = useState<Record<string, CanonicalSide>>({})
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const conflictKey = useCallback(
    (c: Pick<ScoreConflictItem, 'person_id' | 'subject' | 'exam_name'>) =>
      `${String(c.person_id)}|${c.subject}|${c.exam_name}`,
    [],
  )

  /** 打开确认弹窗：single = 某生某科某场的冲突格入口；batch = 工具栏全量清单。 */
  const openConflicts = useCallback(
    (
      target: { personId: PersonId; personName: string; subject: string; examName: string } | null,
    ) => {
      if (linkId == null) return
      setConflictOpen(true)
      setConflictTarget(target)
      setConflictList(null)
      setConflictError(null)
      setChoices({})
      setSubmitError(null)
      const req = ++conflictsReqRef.current
      setConflictLoading(true)
      listScoreConflicts(linkId)
        .then((r) => {
          if (req !== conflictsReqRef.current) return
          setConflictList(r.conflicts ?? [])
        })
        .catch((err: unknown) => {
          if (req !== conflictsReqRef.current) return
          setConflictList([])
          setConflictError(apiErrorMessage(err))
        })
        .finally(() => {
          if (req === conflictsReqRef.current) setConflictLoading(false)
        })
    },
    [linkId],
  )

  // 弹窗内实际可确认的条目：single 模式按（人, 科, 场）收窄
  const visibleConflicts = useMemo(() => {
    const all = conflictList ?? []
    if (conflictTarget == null) return all
    const matched = all.filter(
      (c) =>
        String(c.person_id) === String(conflictTarget.personId) &&
        c.subject === conflictTarget.subject,
    )
    const inExam = matched.filter((c) => c.exam_name === conflictTarget.examName)
    return inExam.length > 0 ? inExam : matched
  }, [conflictList, conflictTarget])

  const submitConfirm = useCallback(async () => {
    if (linkId == null) return
    const resolutions: CanonicalResolution[] = Object.entries(choices).map(([key, side]) => {
      const [pid, subject, examName] = key.split('|')
      return { person_id: Number(pid), subject, exam_name: examName, canonical_side: side }
    })
    if (resolutions.length === 0) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      await confirmCanonicalScores(linkId, resolutions)
      setConflictOpen(false)
      // 成功后重拉 stats/students：两域已一致，冲突标记随之消失
      setDataNonce((n) => n + 1)
    } catch (err: unknown) {
      setSubmitError(apiErrorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }, [choices, linkId])

  const maxBandCount = useMemo(
    () => Math.max(...(bands?.bands ?? []).map((b) => b.count), 1),
    [bands],
  )
  const distributionChartData = useMemo(
    () =>
      (rankDistribution?.bins ?? []).map((bin) => ({
        band: bin.label,
        ...Object.fromEntries(
          (rankDistribution?.series ?? []).map((series) => [
            series.total_type,
            series.counts[bin.key] || 0,
          ]),
        ),
      })),
    [rankDistribution],
  )
  const distributionColors: Record<string, string> = {
    主三门: '#2f73b8',
    五门: '#ce8a43',
    九门: '#c65353',
    '3+3': '#ce8a43',
  }

  const loadingExams = exams == null && examsError == null
  const hasExamData = selectedExam != null

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">成绩分析</h1>
          <p className="mt-1 text-sm text-slate-500">
            行政班全科成绩、总分与段位分布{switching ? ' · 正在切换…' : ''}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {linkId != null ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => openConflicts(null)}
              disabled={loadingExams}
            >
              <ListChecks className="h-4 w-4" />
              冲突清单
            </Button>
          ) : null}
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

      {/* 考试选择卡（筛选控件不参与打印，U01） */}
      <Card className="print:hidden">
        <CardContent className="flex flex-col gap-3 py-4 sm:flex-row sm:items-end">
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
          <p className="text-xs text-slate-400">
            显示所选学年行政班已导入的考试（日期降序）；缺考显示「—」，不计入均分与有效数。
          </p>
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
              可切换上方学年查看历史考试，或到「数据上传」导入本班成绩表；空范围不会扩展到其他班级或全年级。
            </p>
          </CardContent>
        </Card>
      ) : hasExamData ? (
        <>
          {dataError ? (
            <Card>
              <CardContent className="flex flex-col items-center justify-center gap-3 py-8 text-center">
                <AlertCircle className="h-8 w-8 text-amber-400" />
                <p className="text-sm text-slate-600">{dataError}</p>
                <Button variant="outline" size="sm" onClick={() => setExamNonce((n) => n + 1)}>
                  重试
                </Button>
              </CardContent>
            </Card>
          ) : (
            <>
              <Card className="overflow-hidden">
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center gap-2">
                    <BarChart3 className="h-4 w-4 text-brand-500" />
                    排名分段
                  </CardTitle>
                  <CardDescription>
                    {rankDistribution == null
                      ? '按年级展示对应总分口径的学籍排名分布。'
                      : rankDistribution.grade === 1
                        ? '高一展示主三门、五门、九门总分的学籍排名分布。'
                        : '高二、高三展示主三门与 3+3 总分的学籍排名分布。'}
                    每 40 名一档，缺少真实名次的口径不生成柱子。
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  {rankDistributionError ? (
                    <p className="py-8 text-center text-sm text-slate-500">排名分段暂不可用：{rankDistributionError}</p>
                  ) : rankDistribution == null ? (
                    <Skeleton className="h-[300px] w-full" />
                  ) : distributionChartData.length === 0 ? (
                    <p className="py-12 text-center text-sm text-slate-500">当前考试暂无可用的总分名次</p>
                  ) : (
                    <div className="h-[320px] w-full" aria-label="排名分段柱状图">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={distributionChartData} margin={{ top: 12, right: 12, left: -18, bottom: 28 }}>
                          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                          <XAxis dataKey="band" tick={{ fontSize: 11, fill: '#64748b' }} angle={-28} textAnchor="end" interval={0} height={66} />
                          <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: '#64748b' }} />
                          <Tooltip cursor={{ fill: '#f8fafc' }} />
                          <Legend wrapperStyle={{ fontSize: 12 }} />
                          {rankDistribution.series.map((series) => (
                            <Bar
                              key={series.total_type}
                              dataKey={series.total_type}
                              name={`${series.total_type}总分`}
                              fill={distributionColors[series.total_type] ?? '#64748b'}
                              radius={[3, 3, 0, 0]}
                              maxBarSize={24}
                            />
                          ))}
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </CardContent>
              </Card>

              <Tabs defaultValue="averages" className="space-y-4">
                <TabsList className="h-auto w-full justify-start gap-1 overflow-x-auto rounded-xl bg-slate-100/80 p-1 print:hidden">
                  <TabsTrigger value="averages" className="whitespace-nowrap">班级均分表</TabsTrigger>
                  <TabsTrigger value="students" className="whitespace-nowrap">学生成绩明细表</TabsTrigger>
                  <TabsTrigger value="bands" className="whitespace-nowrap">班级名次段位表</TabsTrigger>
                  <TabsTrigger value="frequency" className="whitespace-nowrap">排名频次统计</TabsTrigger>
                  <TabsTrigger value="range" className="whitespace-nowrap">排名区间筛选</TabsTrigger>
                  <TabsTrigger value="focus" className="whitespace-nowrap">重点关注</TabsTrigger>
                </TabsList>

                <TabsContent value="averages" className="space-y-4">
                  <ClassAveragesTable data={classAverages} />
                </TabsContent>

                <TabsContent value="students">
              {/* 学生成绩表：行点击展开跨学年趋势 */}
              <Card>
                <CardHeader>
                  <CardTitle>学生成绩表</CardTitle>
                  <CardDescription>
                    {stats ? `${selectedExam} · 范围 ${String(stats.cohort_size)} 人 · ` : ''}
                    按总分降序仅为浏览排序，非正式名次；点击行可展开跨学年趋势。
                    {stats?.small_sample === true ? <Badge variant="warning" className="ml-2">样本&lt;5</Badge> : null}
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  {students == null ? (
                    <div className="space-y-2">
                      <Skeleton className="h-8 w-full" />
                      <Skeleton className="h-8 w-full" />
                      <Skeleton className="h-8 w-2/3" />
                    </div>
                  ) : students.length === 0 ? (
                    <div className="py-8 text-center">
                      <Users className="mx-auto h-8 w-8 text-slate-300" />
                      <p className="mt-2 text-sm text-slate-600">本场考试暂无本班学生成绩</p>
                      <p className="mt-1 text-xs text-slate-400">
                        可能名册为空或尚未导入该场成绩；空范围不会扩展到全年级。
                      </p>
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead className="whitespace-nowrap text-xs">姓名</TableHead>
                            <TableHead className="whitespace-nowrap text-xs">别名</TableHead>
                            {subjectCols.map((subj) => (
                              <TableHead key={subj} className="whitespace-nowrap text-xs text-right">
                                {subj}
                              </TableHead>
                            ))}
                            {totalCols.map((tt) => (
                              <TableHead
                                key={tt}
                                className="whitespace-nowrap text-xs text-right font-semibold"
                              >
                                总分（{tt}）
                              </TableHead>
                            ))}
                            <TableHead className="w-10 print:hidden" aria-label="趋势" />
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {sortedStudents.map((s) => {
                            const key = String(s.person_id)
                            const expanded = expandedPerson === key
                            const conflicts = conflictByPerson.get(key) ?? null
                            // 趋势数据按 person_id 守卫：切换人选的瞬间绝不闪现上一个人的数据
                            const trendsForPerson =
                              trends != null && String(trends.person_id) === key ? trends : null
                            return (
                              <Fragment key={key}>
                                <TableRow
                                  className="cursor-pointer"
                                  aria-expanded={expanded}
                                  onClick={() => setExpandedPerson(expanded ? null : key)}
                                >
                                  <TableCell className="whitespace-nowrap text-sm font-medium text-slate-900">
                                    {s.name ?? '（未命名）'}
                                  </TableCell>
                                  <TableCell className="whitespace-nowrap text-xs text-slate-500">
                                    {s.alias ?? '—'}
                                  </TableCell>
                                  {subjectCols.map((subj) => {
                                    const v = s.scores?.[subj] ?? null
                                    const conflict = conflicts?.get(subj) ?? null
                                    return (
                                      <TableCell
                                        key={subj}
                                        className={cn(
                                          'whitespace-nowrap text-right tabular-nums text-sm',
                                          conflict
                                            ? 'cursor-pointer bg-warning-50 text-warning-700'
                                            : v == null
                                              ? 'text-slate-300'
                                              : 'text-slate-900',
                                        )}
                                        title={
                                          conflict
                                            ? `两域分数不一致待人工核对（教学域 ${
                                                conflict.teaching_score == null ? '缺考' : `${String(conflict.teaching_score)} 分`
                                              }）；点击确认规范值`
                                            : undefined
                                        }
                                        onClick={
                                          conflict
                                            ? (e) => {
                                                // 格子级入口：不触发行展开
                                                e.stopPropagation()
                                                openConflicts({
                                                  personId: s.person_id,
                                                  personName: s.name ?? '（未命名）',
                                                  subject: subj,
                                                  examName: selectedExam ?? '',
                                                })
                                              }
                                            : undefined
                                        }
                                      >
                                        {v == null ? '—' : formatScore(v)}
                                        {conflict ? <span className="ml-1 text-[10px]">※</span> : null}
                                      </TableCell>
                                    )
                                  })}
                                  {totalCols.map((tt) => {
                                    const v = s.totals?.[tt] ?? null
                                    return (
                                      <TableCell
                                        key={tt}
                                        className={cn(
                                          'whitespace-nowrap text-right tabular-nums text-sm font-medium',
                                          v == null ? 'text-slate-300' : 'text-slate-900',
                                        )}
                                      >
                                        {v == null ? '—' : formatScore(v)}
                                      </TableCell>
                                    )
                                  })}
                                  <TableCell className="print:hidden">
                                    <ChevronDown
                                      className={cn(
                                        'h-4 w-4 text-slate-400 transition-transform',
                                        expanded && 'rotate-180',
                                      )}
                                    />
                                  </TableCell>
                                </TableRow>
                                {expanded ? (
                                  <TableRow className="hover:bg-transparent">
                                    <TableCell
                                      colSpan={2 + subjectCols.length + totalCols.length + 1}
                                      className="bg-slate-50/70 p-4"
                                    >
                                      <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
                                        <TrendingUp className="h-4 w-4 text-brand-500" />
                                        跨学年趋势 · {s.name ?? '（未命名）'}
                                      </div>
                                      <div className="mt-2">
                                        <TrendPanel
                                          trends={trendsForPerson}
                                          error={trendsError}
                                          onRetry={() => setTrendsNonce((n) => n + 1)}
                                        />
                                      </div>
                                    </TableCell>
                                  </TableRow>
                                ) : null}
                              </Fragment>
                            )
                          })}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </CardContent>
              </Card>
                </TabsContent>

                <TabsContent value="frequency">
                  <Card>
                    <CardHeader>
                      <CardTitle>排名频次统计</CardTitle>
                      <CardDescription>
                        多场考试按年级百分位、精确等级分或总分名次档统计每名学生的落点次数。
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-5">
                      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px]">
                        <div>
                          <p className="mb-2 text-xs font-medium text-slate-500">选择考试（可多选）</p>
                          <div className="flex flex-wrap gap-2">
                            {(exams ?? []).map((exam) => {
                              const checked = frequencyExamNames.includes(exam.exam_name)
                              return (
                                <label
                                  key={exam.exam_name}
                                  className={cn(
                                    'flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-xs transition-colors',
                                    checked
                                      ? 'border-brand-200 bg-brand-50 text-brand-700'
                                      : 'border-slate-200 bg-white text-slate-500 hover:border-slate-300',
                                  )}
                                >
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    onChange={() =>
                                      setFrequencyExamNames((current) => {
                                        if (checked) {
                                          return current.length === 1
                                            ? current
                                            : current.filter((name) => name !== exam.exam_name)
                                        }
                                        return [...current, exam.exam_name]
                                      })
                                    }
                                  />
                                  {exam.exam_name}
                                </label>
                              )
                            })}
                          </div>
                        </div>
                        <div>
                          <p className="mb-2 text-xs font-medium text-slate-500">选择指标</p>
                          <Select value={frequencyMetric ?? undefined} onValueChange={setFrequencyMetric}>
                            <SelectTrigger aria-label="排名频次指标"><SelectValue placeholder="选择指标" /></SelectTrigger>
                            <SelectContent>
                              {frequencyMetrics.map((metric) => (
                                <SelectItem key={metric.value} value={metric.value}>{metric.label}</SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                      </div>
                      {frequencyError ? (
                        <p className="py-8 text-center text-sm text-slate-500">排名频次暂不可用：{frequencyError}</p>
                      ) : frequency == null ? (
                        <Skeleton className="h-48 w-full" />
                      ) : frequency.students.length === 0 ? (
                        <p className="py-8 text-center text-sm text-slate-500">所选考试与指标暂无可统计数据</p>
                      ) : (
                        <div className="overflow-x-auto">
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead className="whitespace-nowrap">学生</TableHead>
                                <TableHead className="whitespace-nowrap">别名</TableHead>
                                {frequency.bins.map((bin) => (
                                  <TableHead key={bin.key} className="whitespace-nowrap text-right">{bin.label}</TableHead>
                                ))}
                                <TableHead className="whitespace-nowrap text-right">有效次数</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {frequency.students.map((student) => (
                                <TableRow key={String(student.person_id)}>
                                  <TableCell className="whitespace-nowrap font-medium">{student.name ?? '（未命名）'}</TableCell>
                                  <TableCell className="whitespace-nowrap text-xs text-slate-500">{student.alias ?? '—'}</TableCell>
                                  {frequency.bins.map((bin) => (
                                    <TableCell key={bin.key} className="text-right tabular-nums">{student.counts[bin.key] || 0}</TableCell>
                                  ))}
                                  <TableCell className="text-right font-medium tabular-nums">{student.total_count}</TableCell>
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                        </div>
                      )}
                      {frequency ? <p className="text-xs text-slate-400">{frequency.metric_note}</p> : null}
                    </CardContent>
                  </Card>
                </TabsContent>

                <TabsContent value="range">
                  <Card>
                    <CardHeader>
                      <CardTitle className="flex items-center gap-2"><Search className="h-4 w-4 text-brand-500" />排名区间筛选</CardTitle>
                      <CardDescription>筛出当前考试指定年级名次区间的学生，并同时显示本班名次。</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-5">
                      <div className="grid gap-3 sm:grid-cols-3">
                        <div>
                          <p className="mb-1.5 text-xs font-medium text-slate-500">指标</p>
                          <Select value={rangeMetric ?? undefined} onValueChange={setRangeMetric}>
                            <SelectTrigger aria-label="排名区间指标"><SelectValue placeholder="选择指标" /></SelectTrigger>
                            <SelectContent>
                              {rangeMetrics.map((metric) => (
                                <SelectItem key={metric.value} value={metric.value}>{metric.label}</SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                        <label className="text-xs font-medium text-slate-500">
                          起始名次
                          <Input className="mt-1.5" inputMode="numeric" value={rangeMin} onChange={(event) => setRangeMin(Math.max(1, Number(event.target.value) || 1))} />
                        </label>
                        <label className="text-xs font-medium text-slate-500">
                          结束名次
                          <Input className="mt-1.5" inputMode="numeric" value={rangeMax} onChange={(event) => setRangeMax(Math.max(rangeMin, Number(event.target.value) || rangeMin))} />
                        </label>
                      </div>
                      {rankRangeError ? (
                        <p className="py-8 text-center text-sm text-slate-500">排名区间暂不可用：{rankRangeError}</p>
                      ) : rankRange == null ? (
                        <Skeleton className="h-40 w-full" />
                      ) : rankRange.students.length === 0 ? (
                        <p className="py-8 text-center text-sm text-slate-500">当前区间暂无学生</p>
                      ) : (
                        <div className="overflow-x-auto">
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead>学生</TableHead>
                                <TableHead>别名</TableHead>
                                <TableHead className="text-right">成绩</TableHead>
                                <TableHead className="text-right">班级名次</TableHead>
                                <TableHead className="text-right">年级名次</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {rankRange.students.map((student) => (
                                <TableRow key={String(student.person_id)}>
                                  <TableCell className="font-medium">{student.name ?? '（未命名）'}</TableCell>
                                  <TableCell className="text-xs text-slate-500">{student.alias ?? '—'}</TableCell>
                                  <TableCell className="text-right tabular-nums">{formatScore(student.score)}</TableCell>
                                  <TableCell className="text-right tabular-nums">{student.class_rank ?? '—'}</TableCell>
                                  <TableCell className="text-right font-medium tabular-nums">{student.year_rank ?? '—'}</TableCell>
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                        </div>
                      )}
                      {rankRange ? <p className="text-xs text-slate-400">{rankRange.metric_note}</p> : null}
                    </CardContent>
                  </Card>
                </TabsContent>

                <TabsContent value="focus">
              <Card>
                <CardHeader>
                  <CardTitle>重点关注名单</CardTitle>
                  <CardDescription>
                    主三门学籍排名标注明显进退步、波动、临界段、薄弱段与稳定优秀；单科年级百分位较主三门落后 20 个百分点时标注严重偏科。
                    {focus ? ` 当前明显进退步阈值 ${String(focus.config.progress_rank_threshold)} 名，波动阈值 ${String(focus.config.volatility_rank_threshold)} 名；临界段 ${String(focus.config.critical_min)}–${String(focus.config.critical_max)} 名，薄弱段从 ${String(focus.config.weak_min)} 名起。` : ''}
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  {focusError ? (
                    <p className="text-sm text-slate-500">重点关注暂不可用：{focusError}</p>
                  ) : focus == null ? (
                    <Skeleton className="h-24 w-full" />
                  ) : focus.students.length === 0 ? (
                    <div className="flex items-center gap-2 rounded-lg bg-emerald-50 px-3 py-3 text-sm text-emerald-700">
                      <CheckCircle2 className="h-4 w-4" /> 当前考试暂无重点关注标注
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>学生</TableHead>
                            <TableHead className="text-right">学籍排名</TableHead>
                            <TableHead className="text-right">主三门</TableHead>
                            <TableHead>关注原因</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {focus.students.map((student) => (
                            <TableRow key={String(student.person_id)}>
                              <TableCell>
                                <p className="font-medium text-slate-800">{student.name ?? '（未命名）'}</p>
                                <p className="text-xs text-slate-400">{student.alias ?? '—'}</p>
                              </TableCell>
                              <TableCell className="text-right tabular-nums">{student.xueji_rank ?? '—'}</TableCell>
                              <TableCell className="text-right tabular-nums">{formatScore(student.total_score)}</TableCell>
                              <TableCell>
                                <div className="flex flex-wrap gap-1.5">
                                  {student.issues.map((issue) => (
                                    <span
                                      key={issue}
                                      className={cn(
                                        'rounded-md px-2 py-1 text-xs font-medium',
                                        issue.startsWith('严重偏科')
                                          ? 'bg-rose-50 text-rose-700'
                                          : issue === '明显进步' || issue === '稳定优秀'
                                            ? 'bg-emerald-50 text-emerald-700'
                                            : issue === '明显退步' || issue === '波动风险'
                                              ? 'bg-orange-50 text-orange-700'
                                          : issue === '薄弱段'
                                            ? 'bg-red-50 text-red-700'
                                            : 'bg-amber-50 text-amber-700',
                                      )}
                                    >
                                      {issue}
                                    </span>
                                  ))}
                                </div>
                                {student.rank_change != null || student.rank_range != null ? (
                                  <p className="mt-1.5 text-xs text-slate-400">
                                    {student.rank_change == null
                                      ? ''
                                      : `较前次${student.rank_change >= 0 ? '前进' : '后退'} ${String(Math.abs(student.rank_change))} 名`}
                                    {student.rank_change != null && student.rank_range != null ? '；' : ''}
                                    {student.rank_range == null ? '' : `近 ${String(student.exam_count)} 次排名极差 ${String(student.rank_range)} 名`}
                                  </p>
                                ) : null}
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </CardContent>
              </Card>
                </TabsContent>

                <TabsContent value="bands">
              {/* 段位分布：div 宽度百分比条形，不引图表库 */}
              <Card>
                <CardHeader className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <CardTitle>段位分布</CardTitle>
                    <CardDescription>
                      {activeMetric != null && bands?.total_type != null
                        ? `口径 ${bands.total_type}；`
                        : ''}
                      段位阈值沿用既有分析配置；条形为人数可视化，非百分比排位。
                    </CardDescription>
                  </div>
                  {/* 选择器为 button，打印时由全局规则隐藏（U01），标题保留 */}
                  {metricOptions.length > 0 ? (
                    <Select value={activeMetric ?? undefined} onValueChange={setBandMetric}>
                      <SelectTrigger className="h-8 w-[170px] text-xs" aria-label="段位指标">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {metricOptions.map((o) => (
                          <SelectItem key={o.value} value={o.value}>
                            {o.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : null}
                </CardHeader>
                <CardContent>
                  {bandsError && bandsUnavailable ? (
                    /* 契约 p3 §2.1 v2.1（F10）：无名次口径时 bands 恒 409，前端呈现
                       「不可计算」提示卡而非错误分段或永久骨架 */
                    <div className="flex items-start gap-2 rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-3">
                      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                      <div>
                        <p className="text-sm font-medium text-slate-600">段位不可计算（无名次口径）</p>
                        <p className="mt-0.5 text-xs text-slate-400">
                          段位阈值口径为年级名次，当前范围没有名次数据；引入合法名次来源前不呈现任何分段。
                        </p>
                      </div>
                    </div>
                  ) : bandsError ? (
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-sm text-slate-600">{bandsError}</p>
                      <Button variant="outline" size="sm" onClick={() => setBandsNonce((n) => n + 1)}>
                        重试
                      </Button>
                    </div>
                  ) : bands == null ? (
                    <Skeleton className="h-32 w-full" />
                  ) : bands.bands.length === 0 ? (
                    <p className="py-6 text-center text-sm text-slate-500">
                      当前指标暂无段位数据（该场可能全部缺考或尚未导入）。
                    </p>
                  ) : (
                    <ul className="space-y-2">
                      {bands.bands.map((b) => {
                        const pct = Math.round((b.count / maxBandCount) * 100)
                        return (
                          <li key={b.label} className="flex items-center gap-3">
                            <span className="w-24 shrink-0 truncate text-xs text-slate-500" title={b.label}>
                              {b.label}
                            </span>
                            <div className="h-3 flex-1 overflow-hidden rounded-full bg-slate-100">
                              <div
                                className="h-full rounded-full bg-gradient-to-r from-[#1f7fd6] to-[#35b9e9]"
                                style={{ width: `${String(pct)}%` }}
                              />
                            </div>
                            <span className="w-14 shrink-0 text-right text-xs tabular-nums text-slate-600">
                              {String(b.count)} 人
                            </span>
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </CardContent>
              </Card>
                </TabsContent>
              </Tabs>
            </>
          )}
        </>
      ) : (
        <Card>
          <CardContent className="py-8">
            <Skeleton className="h-8 w-full" />
          </CardContent>
        </Card>
      )}

      {/* 契约 p5 §7 相关性散点卡（r 不可计算态）；挂两域成绩分析页底部，考试跟随顶部选择 */}
      <CorrelationCard mode="homeroom" scopeQ={hwScopeQ} scopeSubject={scopeSubject} generation={generation} preferredExamName={selectedExam} />

      {/* Q10 规范值确认弹窗：列两域值与来源，选其一提交（契约 p3 §1.6） */}
      <Dialog open={conflictOpen} onOpenChange={setConflictOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {conflictTarget
                ? `确认规范值 · ${conflictTarget.personName} ${conflictTarget.subject}`
                : '跨域冲突清单'}
            </DialogTitle>
            <DialogDescription>
              两域分数不一致时各自保留本域值；确认规范值后两工作台读值一致、冲突标记消失。
              任一侧均可选（缺考侧即确认为缺考），不能手工改分。
            </DialogDescription>
          </DialogHeader>

          {conflictError ? (
            <p className="rounded-md bg-danger-50 px-3 py-2 text-sm text-danger-500">{conflictError}</p>
          ) : conflictLoading || conflictList == null ? (
            <div className="space-y-2">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : visibleConflicts.length === 0 ? (
            <p className="py-4 text-center text-sm text-slate-500">
              当前关联暂无跨域冲突（两域值已一致或共享未授权）。
            </p>
          ) : (
            <div className="max-h-[50vh] space-y-2 overflow-y-auto">
              {visibleConflicts.map((c) => {
                const key = conflictKey(c)
                return (
                  <ConflictChoiceRow
                    key={key}
                    item={c}
                    value={choices[key]}
                    onSelect={(side) =>
                      setChoices((prev) => ({ ...prev, [key]: side }))
                    }
                  />
                )
              })}
            </div>
          )}

          {!conflictLoading && visibleConflicts.length > 0 ? (
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
              <span>快捷全选：</span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={submitting}
                onClick={() =>
                  setChoices(
                    Object.fromEntries(
                      visibleConflicts.map((c) => [conflictKey(c), 'homeroom' as CanonicalSide]),
                    ),
                  )
                }
              >
                全选班主任域值
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={submitting}
                onClick={() =>
                  setChoices(
                    Object.fromEntries(
                      visibleConflicts.map((c) => [conflictKey(c), 'teaching' as CanonicalSide]),
                    ),
                  )
                }
              >
                全选教学域值
              </Button>
            </div>
          ) : null}

          {submitError ? (
            <p className="rounded-md bg-danger-50 px-3 py-2 text-sm text-danger-500">{submitError}</p>
          ) : null}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConflictOpen(false)} disabled={submitting}>
              取消
            </Button>
            <Button type="button" onClick={submitConfirm} disabled={submitting || Object.keys(choices).length === 0}>
              {submitting ? '提交中…' : `确认所选（${String(Object.keys(choices).length)}）`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
