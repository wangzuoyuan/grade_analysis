'use client'

/**
 * 班主任工作台成绩分析页（契约 p3-imports-analysis.md §2.1/§3）。
 *
 * 数据全部来自 /api/v1：shared/exams（考试清单）、homeroom/analysis/*（stats/students/bands/trends）。
 * 计量红线（契约 §2.3）：缺考（score NULL）一律显示「—」不转 0；
 * 明细默认按显示学号升序，可单独按原始分/等级分/总分降序；班名次独立按成绩计算；趋势按学年分段展示，不跨年连线；迟到回包按资源分离的序号丢弃（F11：任一资源重拉
 * 绝不使其他资源的合法回包失效），bands 在总分具备年级名次时计算，
 * 无名次口径的 409 呈现「不可计算」提示卡（F10）。
 */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle, BarChart3, CheckCircle2, ChevronDown, ClipboardList, Download, FileSpreadsheet, Search, TrendingUp, Users } from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useSearchParams } from 'next/navigation'

import {
  ApiV1Error,
  fetchHomeroomBands,
  fetchHomeroomClassAverages,
  fetchHomeroomFocus,
  fetchHomeroomRankBands,
  fetchHomeroomRankDistribution,
  fetchHomeroomRankFrequency,
  fetchHomeroomRankMetrics,
  fetchHomeroomRankRange,
  fetchHomeroomStats,
  fetchHomeroomStudents,
  fetchHomeroomTrends,
  listExams,
  type ExamSummary,
  type HomeroomBandsResponse,
  type HomeroomClassAverageRow,
  type HomeroomClassAveragesResponse,
  type HomeroomFocusResponse,
  type HomeroomRankBandsResponse,
  type HomeroomRankDistributionResponse,
  type HomeroomRankFrequencyResponse,
  type HomeroomRankMetric,
  type HomeroomRankRangeResponse,
  type HomeroomStatsResponse,
  type HomeroomStudentRow,
  type HomeroomTrendsResponse,
  type PersonId,
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
import { displayStudentId } from '@/lib/student-id'
import { sortHomeroomStudents, studentSubjectGroups, type StudentScoreSort } from '@/lib/homeroom-score-sort'
import { ScoreSortButton } from './ScoreSortButton'
import {
  Tooltip as UITooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { ExamSelect } from './ExamSelect'
import { ScoreYearPicker } from './ScoreYearPicker'
import { analysisScopeQuery, formatScore, sortExamsDesc } from './shared'
import { bandBarColor, INFERNO_ANCHORS } from './band-colors'
import { StudentScoreRadar, type RadarExamRow } from '@/components/students/StudentScoreRadar'
import tableStyles from './homeroom-tables.module.css'

/** 排名区间输入的单位/钳制/默认值随指标类型（2026-10-23 拍板，与后端
 *  rank-range 的区间校验同口径）：总分=名次、语数英/高一全科=年级前%
 *  （0–100，越小越靠前）、加三学科=等级分（40–70）。 */
const RANGE_KIND_BOUNDS = {
  total_rank: { minLabel: '起始名次', maxLabel: '结束名次', lo: 1, hi: Number.POSITIVE_INFINITY, defMin: 1, defMax: 100 },
  subject_percentile: { minLabel: '起始百分位（前%）', maxLabel: '结束百分位（前%）', lo: 0, hi: 100, defMin: 0, defMax: 100 },
  subject_grade_score: { minLabel: '起始等级分', maxLabel: '结束等级分', lo: 40, hi: 70, defMin: 40, defMax: 70 },
} as const

/** ?tab= 直达白名单（2026-10-22）：仪表盘摘要卡等外链按 tab 锚点进入对应分析板块；
 *  仅作初次进入的初始值（Tabs 非受控 defaultValue），页内切换照旧，非法值回退班级均分表。 */
const SCORES_TABS = new Set(['averages', 'students', 'bands', 'duanwei', 'frequency', 'range', 'focus'])

/** 无名次总分排除项：+3 为选修三科等级分之和，没有名次数据——不进段位分布口径下拉
 *  （2026-10-23 用户裁决，选中恒「段位不可计算」）与排名频次等名次口径消费方
 *  （2026-10-24 泛化）。明细表展开行雷达（2026-10-25）的「总分」轴排除逻辑
 *  内聚在 StudentScoreRadar（RADAR_EXCLUDED_TOTALS），此处不再重复过滤。 */
const RANKLESS_TOTALS = new Set<string>(['+3'])

/** 明细表带「百分位/名次/班名次」4 子列的总分口径：高二/三 主三门/3+3，高一 主三门/五门/九门
 *  （2026-10-28 用户拍板：五门/九门总分行同样导入了年级百分位与学籍/年级名次，与学校表一致）；
 *  +3 为等级和无名次恒单列。totalCols 本身按年级下发，无需再判年级。 */
const RANK_CAPABLE_TOTALS = new Set<string>(['主三门', '3+3', '五门', '九门'])

/** 年级百分位（接口 0–1 小数）→「77.81%」前%文案；缺失交调用方按「—」灰显，不转 0。 */
function formatGradePct(pct: number): string {
  return `${String(Math.round(pct * 10000) / 100)}%`
}

/** 趋势响应的一个学年段 → 雷达数据行（2026-10-25 用户裁决：展开区两张排名
 *  折线换成历次重叠雷达；percentile 兼容旧后端——缺失时单科回退 rank
 *  （rank_basis=grade_percentile 时它就是前%），总分行只有 school 名次不回退）。 */
function radarRowsFromTrendYear(year: HomeroomTrendsResponse['years'][number]): RadarExamRow[] {
  const examMap = new Map<string, RadarExamRow>()
  const rowOf = (exam_name: string, exam_date: string | null): RadarExamRow => {
    const key = `${exam_name}|${exam_date ?? ''}`
    let row = examMap.get(key)
    if (!row) {
      row = { exam_key: key, exam_name, exam_date, subjects: {}, totals: {} }
      examMap.set(key, row)
    }
    return row
  }
  for (const [subject, points] of Object.entries(year.subjects)) {
    for (const p of points) {
      rowOf(p.exam_name, p.exam_date).subjects[subject] = {
        percentile: p.percentile ?? (p.rank_basis === 'grade_percentile' ? p.rank : null),
        grade_score: p.grade_score ?? null,
      }
    }
  }
  for (const [totalType, points] of Object.entries(year.totals)) {
    for (const p of points) {
      rowOf(p.exam_name, p.exam_date).totals[totalType] = {
        percentile: p.percentile ?? (p.rank_basis === 'grade_percentile' ? p.rank : null),
        rank: p.rank_basis === 'school' ? p.rank : null,
      }
    }
  }
  return [...examMap.values()].sort(
    (a, b) =>
      (a.exam_date ?? '').localeCompare(b.exam_date ?? '') || a.exam_name.localeCompare(b.exam_name, 'zh'),
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
            {hasLines ? (
              // max-w 限每张雷达 560px（2026-10-28 用户反馈「雷达分太开」）：
              // 展开区面板已被限到可视宽，两组雷达相邻排布而非扯到两端
              <StudentScoreRadar rows={radarRowsFromTrendYear(year)} groupClassName="max-w-[560px]" />
            ) : (
              <p className="mt-1 text-xs text-slate-400">本学年暂无成绩</p>
            )}
          </div>
        )
      })}
      <p className="text-[10px] text-slate-400">跨学年成绩分段展示，不同学年总分口径不直接比较。</p>
    </div>
  )
}

/** 均分表学科列分组：语数英单列；六选科各挂 原始分/等级分 两个子列
 *  （键为 物理_原始 / 物理_等级，后端 preferred 顺序：语数英 + 选科×2）。 */
function subjectColumnGroups(subjects: string[]): Array<{ subject: string; paired: boolean }> {
  const groups: Array<{ subject: string; paired: boolean }> = []
  const seen = new Set<string>()
  for (const key of subjects) {
    const subject = key.replace(/_(原始|等级)$/, '')
    if (seen.has(subject)) continue
    seen.add(subject)
    groups.push({ subject, paired: key.includes('_') })
  }
  return groups
}

function summaryNumber(
  rows: HomeroomClassAverageRow[],
  values: (row: HomeroomClassAverageRow) => number | null | undefined,
  kind: '平均' | '最高' | '最低',
): number | null {
  // 学校均分表口径：0 = 该班无人选此科，不算成绩——除数与最高/最低都只统计非零班（与学校汇总行一致）
  const nums = rows
    .map(values)
    .filter((value): value is number => typeof value === 'number' && value !== 0)
  if (nums.length === 0) return null
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
  const subjectGroups = subjectColumnGroups(resolved.subjects)

  // 班级类型列：同类型连续行合并单元格（后端已按 班型序+班号 排序）
  const rows = resolved.rows
  const typeSpans: number[] = []
  for (let i = 0; i < rows.length; i += 1) {
    if (i > 0 && (rows[i - 1].class_type ?? '未分类') === (rows[i].class_type ?? '未分类')) {
      typeSpans.push(0)
    } else {
      let span = 1
      while (
        i + span < rows.length &&
        (rows[i + span].class_type ?? '未分类') === (rows[i].class_type ?? '未分类')
      ) {
        span += 1
      }
      typeSpans[i] = span
    }
  }

  const groupRanges: Array<[number, number]> = []
  typeSpans.forEach((span, idx) => {
    if (span > 0) groupRanges.push([idx, span])
  })

  function exportCsv() {
    const header = ['班级类型', '班级', '班主任', ...resolved.subjects]
    for (const totalType of resolved.total_types) {
      header.push(`${totalType}均分`, `${totalType}班型排名`)
    }
    const csvRows = rows.map((row) => {
      const values: Array<string | number> = [row.class_type ?? '', row.class_num, row.teacher_name ?? '']
      for (const subject of resolved.subjects) values.push(row.subjects[subject] ?? '')
      for (const totalType of resolved.total_types) {
        const entry = row.totals[totalType]
        values.push(
          entry?.均分 ?? '',
          row.total_ranks[totalType] ?? '',
        )
      }
      return values
    })
    const csv = [header, ...csvRows]
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
          <CardDescription>
            {data.exam_name} · 来自已导入的班级均分表；排名按平行班、实验班分别计算，同类型班级内同分同名次。
          </CardDescription>
        </div>
        <Button variant="outline" size="sm" onClick={exportCsv} className="shrink-0 print:hidden">
          <Download className="h-4 w-4" />导出数据
        </Button>
      </CardHeader>
      <CardContent>
        {/* 2026-10-22 尺寸改造：限高双轴滚动 + 两级粘性表头 + 冻结前两列（类型/班级）；
            内层 Table 自带 overflow-auto 包装会截胡 sticky，须以 overflow-visible 交还外层滚动容器 */}
        <div className="max-h-[70vh] overflow-auto rounded-lg border border-slate-200">
          <Table wrapperClassName="overflow-visible" className={cn('min-w-max', tableStyles.table)}>
            <TableHeader>
              <TableRow className="bg-slate-100">
                {/* 两级表头的吸顶（top/z/行高）由 module.css 统一注入；冻结列 left 偏移在此，
                    freezeCol 把冻结格 z 抬到滑动表头之上（z 相同按 DOM 序绘制会被盖住） */}
                <TableHead rowSpan={2} className={cn('sticky left-0 w-24 whitespace-nowrap border-r text-center text-xs', tableStyles.freezeCol)}>班级类型</TableHead>
                <TableHead rowSpan={2} className={cn('sticky left-24 w-16 whitespace-nowrap border-r text-center text-xs', tableStyles.freezeCol)}>班级</TableHead>
                <TableHead rowSpan={2} className="whitespace-nowrap border-r text-center text-xs">班主任</TableHead>
                {subjectGroups.map((g) => g.paired ? (
                  <TableHead key={g.subject} colSpan={2} className="whitespace-nowrap border-r text-center text-xs">{g.subject}</TableHead>
                ) : (
                  <TableHead key={g.subject} rowSpan={2} className="whitespace-nowrap border-r text-center text-xs">{g.subject}</TableHead>
                ))}
                {resolved.total_types.map((totalType) => (
                  <TableHead key={totalType} colSpan={2} className="whitespace-nowrap border-r text-center text-xs font-semibold">{totalType}总分</TableHead>
                ))}
              </TableRow>
              <TableRow className="bg-slate-50">
                {subjectGroups.flatMap((g) => g.paired ? [
                  <TableHead key={`${g.subject}-raw`} className="whitespace-nowrap text-center text-xs">原始分</TableHead>,
                  <TableHead key={`${g.subject}-graded`} className="whitespace-nowrap border-r text-center text-xs">等级分</TableHead>,
                ] : [])}
                {resolved.total_types.flatMap((totalType) => [
                  <TableHead key={`${totalType}-avg`} className="whitespace-nowrap text-center text-xs">均分</TableHead>,
                  <TableHead key={`${totalType}-rank`} className="whitespace-nowrap border-r text-center text-xs">班型排名</TableHead>,
                ])}
              </TableRow>
            </TableHeader>
            <TableBody>
              {groupRanges.map(([start, span]) => {
                const groupRows = rows.slice(start, start + span)
                return (
                  <Fragment key={start}>
                    {groupRows.map((row, groupIndex) => {
                      const idx = start + groupIndex
                      const isCurrent = row.class_num === resolved.current_class_num
                      return (
                        <TableRow key={String(row.class_num)} data-stripe={groupIndex % 2 === 1}>
                          {typeSpans[idx] > 0 ? (
                            <TableCell rowSpan={typeSpans[idx]} className={cn('sticky left-0 z-10 w-24 whitespace-nowrap border-r align-middle text-center text-xs', tableStyles.classType, tableStyles.stickyCol)}>{row.class_type ?? '未分类'}</TableCell>
                          ) : null}
                          <TableCell className={cn('sticky left-24 z-10 w-16 border-r text-center text-sm font-medium', tableStyles.classNumber, tableStyles.stickyCol, isCurrent && tableStyles.currentClassNumber)}>
                            {String(row.class_num).padStart(2, '0')}
                            {isCurrent ? <span className="block text-xs">本班</span> : null}
                          </TableCell>
                          <TableCell className="whitespace-nowrap border-r text-sm">{row.teacher_name ?? '—'}</TableCell>
                          {subjectGroups.map((g) => {
                            if (!g.paired) {
                              return <TableCell key={g.subject} className="border-r text-right tabular-nums text-sm">{formatScore(row.subjects[g.subject])}</TableCell>
                            }
                            return [
                              <TableCell key={`${g.subject}-raw`} className="text-right tabular-nums text-sm">{formatScore(row.subjects[`${g.subject}_原始`])}</TableCell>,
                              <TableCell key={`${g.subject}-graded`} className="border-r text-right tabular-nums text-sm">{formatScore(row.subjects[`${g.subject}_等级`])}</TableCell>,
                            ]
                          })}
                          {resolved.total_types.flatMap((totalType) => {
                            const entry = row.totals[totalType]
                            return [
                              <TableCell key={`${totalType}-avg`} className="text-right tabular-nums text-sm font-medium">{formatScore(entry?.均分)}</TableCell>,
                              <TableCell key={`${totalType}-rank`} className="border-r text-center tabular-nums text-sm font-medium">{row.total_ranks[totalType] ?? '—'}</TableCell>,
                            ]
                          })}
                        </TableRow>
                      )
                    })}
                    {/* 班型分组汇总：平均/最高/最低（排名列留空） */}
                    {summaryKinds.map((kind, kindIndex) => (
                      <TableRow key={`${start}-${kind}`} data-summary={kind}>
                        {kindIndex === 0 ? <TableCell rowSpan={3} className={cn('sticky left-0 z-10 w-24 whitespace-nowrap border-r align-middle text-xs font-semibold', tableStyles.summaryGroup, tableStyles.stickyCol)}>{groupRows[0].class_type ?? '未分类'}汇总</TableCell> : null}
                        <TableCell colSpan={2} className={cn('sticky left-24 z-10 border-r text-xs font-semibold', tableStyles.summaryLabel, tableStyles.stickyCol)}>{kind}</TableCell>
                        {subjectGroups.map((g) => {
                          if (!g.paired) {
                            return <TableCell key={g.subject} className="border-r text-right tabular-nums text-sm font-semibold">{formatScore(summaryNumber(groupRows, (row) => row.subjects[g.subject], kind))}</TableCell>
                          }
                          return [
                            <TableCell key={`${g.subject}-raw`} className="text-right tabular-nums text-sm font-semibold">{formatScore(summaryNumber(groupRows, (row) => row.subjects[`${g.subject}_原始`], kind))}</TableCell>,
                            <TableCell key={`${g.subject}-graded`} className="border-r text-right tabular-nums text-sm font-semibold">{formatScore(summaryNumber(groupRows, (row) => row.subjects[`${g.subject}_等级`], kind))}</TableCell>,
                          ]
                        })}
                        {resolved.total_types.flatMap((totalType) => [
                          <TableCell key={`${totalType}-avg`} className="text-right tabular-nums text-sm font-semibold">{formatScore(summaryNumber(groupRows, (row) => row.totals[totalType]?.均分, kind))}</TableCell>,
                          <TableCell key={`${totalType}-rank`} className="border-r" />,
                        ])}
                      </TableRow>
                    ))}
                  </Fragment>
                )
              })}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  )
}

/** 名次段分布面板（2026-10-19）：堆叠条（每班一行，Inferno 段色按段绝对
 *  位置、高名次亮端；2026-10-21 用户拍板换 Inferno——16 段离散采样下中段
 *  色相变化比 Viridis 绿青中段更易分辨）+ 白色细分隔线 + 悬浮联动高亮同段
 *  （跨班对比某段人数）+ 学校名次段表原样表格（行=班级，列=段，本班行高亮，
 *  末行全年级合计）。分段与人数全部来自导入的名次段表，不从学生名次现算。 */
function RankBandsPanel({ data }: { data: HomeroomRankBandsResponse }) {
  const [hoverBand, setHoverBand] = useState<number | null>(null)
  const bandCount = Math.max(data.grade_bands.length, ...data.classes.map((c) => c.bands.length), 0)
  // 全零尾段折叠：与教学分数段卡同款（图例与表格都截到最后一个非零段）
  let lastNonZero = -1
  for (const c of [...data.classes, { bands: data.grade_bands } as { bands: typeof data.grade_bands }]) {
    c.bands.forEach((b, i) => {
      if (b.count > 0 && i > lastNonZero) lastNonZero = i
    })
  }
  const visibleCount = lastNonZero >= 0 ? lastNonZero + 1 : bandCount
  const collapsedFrom = visibleCount < bandCount ? data.grade_bands[visibleCount]?.lo ?? null : null
  const gradeTotal = data.grade_bands.reduce((sum, b) => sum + b.count, 0)

  const bandCellTone = (count: number, total: number): string => {
    if (count <= 0 || total <= 0) return ''
    const ratio = count / total
    if (ratio >= 0.2) return 'bg-sky-100/70'
    if (ratio >= 0.1) return 'bg-sky-50'
    return 'bg-sky-50/50'
  }

  return (
    <div className="space-y-4">
      {/* 堆叠条：宽度=本段占本班人数比例；白色细分隔线分段；悬浮显示段名/人数/占比，
          且同一段在全部班级行联动高亮（其余降暗），跨班对比某段人数 */}
      <TooltipProvider delayDuration={150}>
        <div className="space-y-1.5" onMouseLeave={() => setHoverBand(null)}>
          {data.classes.map((c) => {
            const isCurrent = c.class_num === data.current_class_num
            return (
              <div key={c.class_num} className="flex items-center gap-3">
                <span
                  className={cn('w-32 shrink-0 truncate text-xs', isCurrent ? 'font-semibold text-brand-700' : 'text-slate-500')}
                  title={`${String(c.class_num).padStart(2, '0')} ${c.teacher_name ?? ''}（${String(c.total)} 人）`}
                >
                  {String(c.class_num).padStart(2, '0')}
                  {c.teacher_name ? ` ${c.teacher_name}` : ''}
                  <span className="ml-1 text-slate-400">{String(c.total)} 人</span>
                </span>
                <div className="flex h-4 flex-1 overflow-hidden rounded-full bg-slate-100 [&>*+*]:border-l [&>*+*]:border-white/90">
                  {c.bands.map((b, i) =>
                    b.count > 0 && c.total > 0 ? (
                      <UITooltip key={i}>
                        <TooltipTrigger asChild>
                          <div
                            className={cn(
                              'h-full cursor-default transition-opacity',
                              hoverBand != null && hoverBand !== i && 'opacity-25',
                              hoverBand === i && 'ring-1 ring-inset ring-slate-900/50',
                            )}
                            onMouseEnter={() => setHoverBand(i)}
                            style={{
                              width: `${String((b.count / c.total) * 100)}%`,
                              background: bandBarColor(i, c.bands.length, INFERNO_ANCHORS),
                            }}
                          />
                        </TooltipTrigger>
                        <TooltipContent sideOffset={6}>
                          <span className="font-medium">{b.label} 名</span>
                          <span className="ml-2 text-slate-500">
                            {String(b.count)} 人 · {((b.count / c.total) * 100).toFixed(1)}%
                          </span>
                        </TooltipContent>
                      </UITooltip>
                    ) : null,
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </TooltipProvider>

      {/* 图例：全零尾段折叠为一项「(*, 下界)」 */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {data.grade_bands.slice(0, visibleCount).map((b, i) => (
          <span key={b.label} className="inline-flex items-center gap-1 text-[10px] text-slate-500">
            <span
              className="inline-block h-2.5 w-2.5 rounded-sm"
              style={{ background: bandBarColor(i, bandCount, INFERNO_ANCHORS) }}
            />
            {b.label}
          </span>
        ))}
        {collapsedFrom != null ? (
          <span className="text-[10px] text-slate-400">（{String(collapsedFrom)} 名以后全零段已折叠）</span>
        ) : null}
      </div>

      {/* 学校名次段表原样：行=班级+班主任，列=段+合计；本班高亮；末行全年级 */}
      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <Table className="min-w-max text-xs">
          <TableHeader>
            <TableRow className="bg-slate-100">
              <TableHead className="whitespace-nowrap border-r text-center">班级</TableHead>
              <TableHead className="whitespace-nowrap border-r text-center">班主任</TableHead>
              {data.grade_bands.slice(0, visibleCount).map((b) => (
                <TableHead key={b.label} className="whitespace-nowrap text-center">{b.label}</TableHead>
              ))}
              {collapsedFrom != null ? (
                <TableHead className="whitespace-nowrap text-center">({String(collapsedFrom)}以下)</TableHead>
              ) : null}
              <TableHead className="whitespace-nowrap text-center">合计</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.classes.map((c) => {
              const isCurrent = c.class_num === data.current_class_num
              return (
                <TableRow key={c.class_num} className={cn(isCurrent && 'bg-brand-50/60 font-medium')}>
                  <TableCell className="border-r text-center">{String(c.class_num).padStart(2, '0')}{isCurrent ? '（本班）' : ''}</TableCell>
                  <TableCell className="whitespace-nowrap border-r text-center">{c.teacher_name ?? '—'}</TableCell>
                  {data.grade_bands.slice(0, visibleCount).map((b, i) => {
                    const band = c.bands[i]
                    const count = band == null ? 0 : band.count
                    return (
                      <TableCell key={b.label} className={cn('text-center tabular-nums', count === 0 && 'text-slate-300', bandCellTone(count, c.total))}>
                        {String(count)}
                      </TableCell>
                    )
                  })}
                  {collapsedFrom != null ? (
                    <TableCell className="text-center tabular-nums text-slate-400">
                      {String(c.bands.slice(visibleCount).reduce((sum, b) => sum + b.count, 0))}
                    </TableCell>
                  ) : null}
                  <TableCell className="text-center tabular-nums font-semibold">{String(c.total)}</TableCell>
                </TableRow>
              )
            })}
            <TableRow className="bg-slate-50 font-semibold">
              <TableCell className="border-r text-center">全年级</TableCell>
              <TableCell className="border-r" />
              {data.grade_bands.slice(0, visibleCount).map((b) => (
                <TableCell key={b.label} className="text-center tabular-nums">{String(b.count)}</TableCell>
              ))}
              {collapsedFrom != null ? (
                <TableCell className="text-center tabular-nums">
                  {String(data.grade_bands.slice(visibleCount).reduce((sum, b) => sum + b.count, 0))}
                </TableCell>
              ) : null}
              <TableCell className="text-center tabular-nums">{String(gradeTotal)}</TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

export function HomeroomScores() {
  const { filter, generation, switching } = useWorkspace()
  const searchParams = useSearchParams()
  const requestedExam = searchParams.get('exam')
  // ?tab= 直达（2026-10-22）：白名单内的板块作为初始 tab；非法/缺省保持原默认
  const requestedTab = searchParams.get('tab')
  const initialTab = requestedTab != null && SCORES_TABS.has(requestedTab) ? requestedTab : 'averages'
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
  const [studentSort, setStudentSort] = useState<StudentScoreSort>({ kind: 'id' })

  // 名次段分布（2026-10-19：读导入的名次段表 3+3/主三门，替代按学生名次现算的旧段位卡）
  const [rankBands, setRankBands] = useState<HomeroomRankBandsResponse | null>(null)
  const [rankBandsError, setRankBandsError] = useState<string | null>(null)
  const [rankBandsMetric, setRankBandsMetric] = useState<string | null>(null)
  const [rankBandsNonce, setRankBandsNonce] = useState(0)

  // 段位分布（2026-10-20 恢复：按明细表名次现算三段阈值；仅总分口径。
  // 2026-10-24 起名次按学籍代码取列——学籍=1 学籍排名（缺补年级排名）、
  // 学籍非1只认年级排名、未采集学籍排名优先；全员参与统计）
  const [bandMetric, setBandMetric] = useState<string>('__total__')
  const [duanweiBands, setDuanweiBands] = useState<HomeroomBandsResponse | null>(null)
  const [duanweiError, setDuanweiError] = useState<string | null>(null)
  // F10/F11：无名次口径后端 409，区别于网络/瞬时错误展示
  const [duanweiUnavailable, setDuanweiUnavailable] = useState(false)
  const [duanweiNonce, setDuanweiNonce] = useState(0)

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
  // 区间单位随选中指标的 kind（指标清单来自后端 rank-metrics，高一为
  // 全科百分位 + 主三门/五门/九门总分，高二/三为语数英 + 六选科等级分 +
  // 主三门/3+3 总分）。
  const rangeKind = rangeMetrics.find((metric) => metric.value === rangeMetric)?.kind ?? 'total_rank'
  const rangeBounds = RANGE_KIND_BOUNDS[rangeKind]
  const [rankRange, setRankRange] = useState<HomeroomRankRangeResponse | null>(null)
  const [rankRangeError, setRankRangeError] = useState<string | null>(null)

  // 行展开趋势（一次只展开一人，避免并发趋势请求堆叠）
  const [expandedPerson, setExpandedPerson] = useState<string | null>(null)
  const [trends, setTrends] = useState<HomeroomTrendsResponse | null>(null)
  const [trendsError, setTrendsError] = useState<string | null>(null)
  const [trendsNonce, setTrendsNonce] = useState(0)

  // 展开区雷达脱滚（2026-10-28 用户反馈：表格可左右滚动，但雷达必须钉在可视区
  // 完整显示）——展开行 td sticky left-0，内层宽度=滚动容器可视宽，ResizeObserver 跟随。
  const [studentsViewportW, setStudentsViewportW] = useState<number | null>(null)
  const studentsRoRef = useRef<ResizeObserver | null>(null)
  const studentsViewportRef = useCallback((el: HTMLDivElement | null) => {
    studentsRoRef.current?.disconnect()
    studentsRoRef.current = null
    if (el == null) return
    setStudentsViewportW(el.clientWidth)
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width
      if (width != null) setStudentsViewportW(Math.round(width))
    })
    ro.observe(el)
    studentsRoRef.current = ro
  }, [])

  // 请求序号按资源分离（F11）：考试清单、stats+students、bands、trends 各自比对各自
  // 序号——共用一个序号时，切考试后同轮 bands/trends effect 的递增会把 stats/students
  // 的合法回包当作过期丢弃，造成统计卡与学生表骨架永久加载。
  const examsReqRef = useRef(0)
  const dataReqRef = useRef(0)
  const bandsReqRef = useRef(0)
  const duanweiReqRef = useRef(0)
  const focusReqRef = useRef(0)
  const distributionReqRef = useRef(0)
  const metricsReqRef = useRef(0)
  const frequencyReqRef = useRef(0)
  const rangeReqRef = useRef(0)
  const trendsReqRef = useRef(0)

  const scopeQ = useMemo(() => analysisScopeQuery(filter), [filter])

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
          setSelectedExam((sorted.find((exam) => (exam.exam_key ?? exam.exam_name) === requestedExam || exam.exam_name === requestedExam)?.exam_key ?? sorted.find((exam) => exam.exam_name === requestedExam)?.exam_name ?? sorted[0].exam_key ?? sorted[0].exam_name))
          setFrequencyExamNames(sorted.slice(0, 5).map((exam) => exam.exam_key ?? exam.exam_name))
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
    setStudentSort({ kind: 'id' })
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
  }, [selectedExam, scopeQ])

  // 列头顺序以 stats.subjects 为准（任务书 §/homeroom/scores）
  const subjectCols = useMemo(() => (stats?.subjects ?? []).map((s) => s.subject), [stats])
  const totalCols = useMemo(() => (stats?.totals ?? []).map((t) => t.total_type), [stats])

  // 展示用考试名：selectedExam 持稳定 exam_key（event:N），界面不得显示内部编号
  const selectedExamLabel = useMemo(
    () => exams?.find((e) => (e.exam_key ?? e.exam_name) === selectedExam)?.exam_name ?? selectedExam,
    [exams, selectedExam],
  )

  // 名次段分布：考试或口径变化即重拉（只比对 bandsReqRef，绝不作废 stats/students 回包）；
  // 首次不传口径由后端取第一个可用口径，回包后同步本地选择态
  useEffect(() => {
    if (selectedExam == null) return
    const req = ++bandsReqRef.current
    setRankBands(null)
    setRankBandsError(null)
    fetchHomeroomRankBands(selectedExam, scopeQ, rankBandsMetric)
      .then((r) => {
        if (req !== bandsReqRef.current) return
        setRankBands(r)
        if (rankBandsMetric == null && r.total_type != null) setRankBandsMetric(r.total_type)
      })
      .catch((err: unknown) => {
        if (req !== bandsReqRef.current) return
        setRankBands(null)
        setRankBandsError(apiErrorMessage(err))
      })
  }, [selectedExam, scopeQ, rankBandsMetric, rankBandsNonce])

  // 段位分布指标选项：仅各总分口径（单科行无名次，后端恒 409 不再提供）；
  // +3 已被 RANKLESS_TOTALS 排除（2026-10-23），主三门/3+3 保留
  const duanweiOptions = useMemo(
    () =>
      (stats?.totals ?? [])
        .filter((t) => !RANKLESS_TOTALS.has(t.total_type))
        .map((t) => ({ value: `__total__${t.total_type}`, label: `总分（${t.total_type}）` })),
    [stats],
  )
  const activeDuanwei =
    duanweiOptions.find((o) => o.value === bandMetric)?.value ?? duanweiOptions[0]?.value ?? null

  // 段位分布（F10）：考试或口径变化即重拉（独立序号 duanweiReqRef）
  useEffect(() => {
    if (selectedExam == null || activeDuanwei == null) {
      setDuanweiBands(null)
      setDuanweiError(null)
      setDuanweiUnavailable(false)
      return
    }
    const req = ++duanweiReqRef.current
    setDuanweiBands(null)
    setDuanweiError(null)
    setDuanweiUnavailable(false)
    fetchHomeroomBands({
      exam_name: selectedExam,
      metric: 'total',
      subject: activeDuanwei.slice('__total__'.length),
      ...scopeQ,
    })
      .then((b) => {
        if (req !== duanweiReqRef.current) return
        setDuanweiBands(b)
      })
      .catch((err: unknown) => {
        if (req !== duanweiReqRef.current) return
        setDuanweiBands(null)
        setDuanweiUnavailable(err instanceof ApiV1Error && err.status === 409)
        setDuanweiError(apiErrorMessage(err))
      })
  }, [selectedExam, activeDuanwei, scopeQ, duanweiNonce])

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
  }, [selectedExam, scopeQ])

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
  }, [selectedExam, scopeQ])

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

  // 学号为默认序；每次仅按一个显示成绩字段排序，不改接口数组或成绩名次。
  const sortedStudents = useMemo(() => sortHomeroomStudents(students ?? [], studentSort), [students, studentSort])
  const studentGroups = useMemo(
    () => studentSubjectGroups(subjectCols, students ?? [], classAverages?.grade),
    [subjectCols, students, classAverages?.grade],
  )
  const isStudentSort = (kind: StudentScoreSort['kind'], key?: string) =>
    studentSort.kind === kind && (studentSort.kind === 'id' || studentSort.key === key)
  const studentSortLabel = studentSort.kind === 'id' ? '学号 · 升序' :
    `${studentSort.key}${studentSort.kind === 'totals' ? '总分' : studentSort.kind === 'grade_scores' ? '等级分' : '原始分'} · 从高到低`

  // 班名次（2026-10-19 明细表新列）：按各总分口径对本班有总分学生计算，
  // 同分同名次（1,2,2,4 语义）、缺考不参与不编造；仅为浏览名次。
  const rankTotalCols = useMemo<string[]>(
    () => totalCols.filter((tt) => RANK_CAPABLE_TOTALS.has(tt)),
    [totalCols],
  )
  const classRanksByTotal = useMemo(() => {
    const out: Record<string, Map<string, number>> = {}
    for (const tt of rankTotalCols) {
      const valid = (students ?? [])
        .map((s) => ({ s, v: s.totals?.[tt] ?? null }))
        .filter((e): e is { s: HomeroomStudentRow; v: number } => e.v != null)
        .sort((a, b) => b.v - a.v)
      const m = new Map<string, number>()
      let lastScore: number | null = null
      let lastRank = 0
      valid.forEach(({ s, v }, idx) => {
        const rank = lastScore === v ? lastRank : idx + 1
        lastScore = v
        lastRank = rank
        m.set(String(s.person_id), rank)
      })
      out[tt] = m
    }
    return out
  }, [students, rankTotalCols])

  const duanweiMaxCount = useMemo(
    () => Math.max(...(duanweiBands?.bands ?? []).map((b) => b.count), 1),
    [duanweiBands],
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
                      ? '按年级展示对应总分口径的名次分布。'
                      : rankDistribution.grade === 1
                        ? '高一展示主三门、五门、九门总分的名次分布。'
                        : '高二、高三展示主三门与 3+3 总分的名次分布。'}
                    每 40 名一档，缺少名次的口径不生成柱子。名次按学籍代码取列：学籍=1（闵中学籍）取学籍排名、学籍非1取年级排名、学籍未采集取学籍排名。
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

              <Tabs defaultValue={initialTab} className="space-y-4">
                <TabsList className="h-auto w-full justify-start gap-1 overflow-x-auto rounded-xl bg-slate-100/80 p-1 print:hidden">
                  <TabsTrigger value="averages" className="whitespace-nowrap">班级均分表</TabsTrigger>
                  <TabsTrigger value="students" className="whitespace-nowrap">学生成绩明细表</TabsTrigger>
                  <TabsTrigger value="bands" className="whitespace-nowrap">名次段分布</TabsTrigger>
                  <TabsTrigger value="duanwei" className="whitespace-nowrap">段位分布</TabsTrigger>
                  <TabsTrigger value="frequency" className="whitespace-nowrap">排名频次统计</TabsTrigger>
                  <TabsTrigger value="range" className="whitespace-nowrap">排名区间筛选</TabsTrigger>
                  <TabsTrigger value="focus" className="whitespace-nowrap">重点关注</TabsTrigger>
                </TabsList>

                <TabsContent value="averages" className="space-y-4">
                  <ClassAveragesTable data={classAverages} />
                </TabsContent>

                <TabsContent value="students">
              {/* 学生成绩明细表（2026-10-19 表头按学校导出重制；2026-10-28 学科分数旁
                  加年级百分位、高一五门/九门总分扩 4 子列）：
                  学号/班级/学籍/姓名 + 各学科组 2 子列（选科=原始分/等级分，
                  其余=分数/百分位）+ +3总分单列 + 主三门/3+3/五门/九门(总分/百分位/名次/班名次)；
                  行点击展开趋势（展开区 sticky 脱滚限宽） */}
              <Card>
                <CardHeader>
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <CardTitle>学生成绩明细表</CardTitle>
                    <div className="flex flex-wrap items-center gap-3 text-sm print:hidden">
                      <p role="status" aria-live="polite" className="text-slate-500">
                        当前排序：<span className="font-medium text-brand-700">{studentSortLabel}</span>
                      </p>
                      <Button variant="outline" size="sm" disabled={studentSort.kind === 'id'} onClick={() => setStudentSort({ kind: 'id' })}>
                        恢复学号升序
                      </Button>
                    </div>
                  </div>
                  <CardDescription>
                    {stats ? `${selectedExamLabel} · 范围 ${String(stats.cohort_size)} 人 · ` : ''}
                    缺考显示「—」；学科与总分的百分位均为年级前%（越小越靠前）；班名次按本班同口径总分计算（同分同名次），名次来自导入明细——学籍=1（闵中学籍）取学籍排名、学籍非1取年级排名、学籍未采集取学籍排名；点击行可展开跨学年趋势。
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
                    <div ref={studentsViewportRef} className="max-h-[70vh] overflow-auto rounded-lg border border-slate-200">
                      {/* 2026-10-22 尺寸改造：限高双轴滚动 + 两级粘性表头 + 冻结学号/姓名列
                          （班级/学籍两列在其下横向滑过）；内层 Table 包装以 overflow-visible
                          交还外层滚动容器，sticky 才能同时吸顶/冻结。
                          ref 供展开区雷达测可视宽（2026-10-28 脱滚） */}
                      <Table wrapperClassName="overflow-visible" className={cn('min-w-max', tableStyles.table)}>
                        <TableHeader>
                          <TableRow className="bg-slate-100">
                            {/* 两级表头的吸顶（top/z/行高）由 module.css 统一注入；冻结列 left 偏移在此，
                                freezeCol 把冻结格 z 抬到滑动表头之上（z 相同按 DOM 序绘制会被盖住） */}
                            <TableHead rowSpan={2} aria-sort={studentSort.kind === 'id' ? 'ascending' : 'none'} className={cn('sticky left-0 w-24 whitespace-nowrap border-r text-center text-xs', tableStyles.freezeCol, studentSort.kind === 'id' && tableStyles.sortedHead)}>
                              <ScoreSortButton label="学号" active={studentSort.kind === 'id'} ascending onClick={() => setStudentSort({ kind: 'id' })}>学号</ScoreSortButton>
                            </TableHead>
                            <TableHead rowSpan={2} className="whitespace-nowrap border-r text-center text-xs">班级</TableHead>
                            <TableHead rowSpan={2} className="whitespace-nowrap border-r text-center text-xs">学籍</TableHead>
                            <TableHead rowSpan={2} className={cn('sticky left-24 w-20 whitespace-nowrap border-r text-center text-xs', tableStyles.freezeCol)}>姓名</TableHead>
                            {/* 2026-10-28：学科组恒 2 子列——选科=原始分/等级分，
                                其余（高一全科+高二/三语数英）=分数/百分位（排序按钮在分数子列） */}
                            {studentGroups.map((g) => (
                              <TableHead key={g.subject} colSpan={2} className="whitespace-nowrap border-r text-center text-xs">{g.subject}</TableHead>
                            ))}
                            {totalCols.filter((tt) => !rankTotalCols.includes(tt)).map((tt) => (
                              <TableHead key={tt} rowSpan={2} aria-sort={isStudentSort('totals', tt) ? 'descending' : 'none'} className={cn('whitespace-nowrap border-r text-center text-xs font-semibold', isStudentSort('totals', tt) && tableStyles.sortedHead)}>
                                <ScoreSortButton label={`${tt}总分`} active={isStudentSort('totals', tt)} onClick={() => setStudentSort({ kind: 'totals', key: tt })}>{tt}总分</ScoreSortButton>
                              </TableHead>
                            ))}
                            {rankTotalCols.map((tt) => (
                              <TableHead key={tt} colSpan={4} className="whitespace-nowrap border-r text-center text-xs font-semibold">{tt}总分</TableHead>
                            ))}
                            <TableHead rowSpan={2} className="w-10 print:hidden" aria-label="趋势" />
                          </TableRow>
                          <TableRow className="bg-slate-50">
                            {studentGroups.flatMap((g) => g.paired ? [
                              <TableHead key={`${g.subject}-raw`} aria-sort={isStudentSort('scores', g.subject) ? 'descending' : 'none'} className={cn('whitespace-nowrap text-center text-xs', isStudentSort('scores', g.subject) && tableStyles.sortedHead)}>
                                <ScoreSortButton label={`${g.subject}原始分`} active={isStudentSort('scores', g.subject)} onClick={() => setStudentSort({ kind: 'scores', key: g.subject })}>原始分</ScoreSortButton>
                              </TableHead>,
                              <TableHead key={`${g.subject}-graded`} aria-sort={isStudentSort('grade_scores', g.subject) ? 'descending' : 'none'} className={cn('whitespace-nowrap border-r text-center text-xs', isStudentSort('grade_scores', g.subject) && tableStyles.sortedHead)}>
                                <ScoreSortButton label={`${g.subject}等级分`} active={isStudentSort('grade_scores', g.subject)} onClick={() => setStudentSort({ kind: 'grade_scores', key: g.subject })}>等级分</ScoreSortButton>
                              </TableHead>,
                            ] : [
                              <TableHead key={`${g.subject}-score`} aria-sort={isStudentSort('scores', g.subject) ? 'descending' : 'none'} className={cn('whitespace-nowrap text-center text-xs', isStudentSort('scores', g.subject) && tableStyles.sortedHead)}>
                                <ScoreSortButton label={`${g.subject}分数`} active={isStudentSort('scores', g.subject)} onClick={() => setStudentSort({ kind: 'scores', key: g.subject })}>分数</ScoreSortButton>
                              </TableHead>,
                              <TableHead key={`${g.subject}-pct`} className="whitespace-nowrap border-r text-center text-xs">百分位</TableHead>,
                            ])}
                            {rankTotalCols.flatMap((tt) => [
                              <TableHead key={`${tt}-score`} aria-sort={isStudentSort('totals', tt) ? 'descending' : 'none'} className={cn('whitespace-nowrap text-center text-xs', isStudentSort('totals', tt) && tableStyles.sortedHead)}>
                                <ScoreSortButton label={`${tt}总分`} active={isStudentSort('totals', tt)} onClick={() => setStudentSort({ kind: 'totals', key: tt })}>总分</ScoreSortButton>
                              </TableHead>,
                              <TableHead key={`${tt}-pct`} className="whitespace-nowrap text-center text-xs">百分位</TableHead>,
                              <TableHead key={`${tt}-school`} className="whitespace-nowrap text-center text-xs">名次</TableHead>,
                              <TableHead key={`${tt}-class`} className="whitespace-nowrap border-r text-center text-xs">班名次</TableHead>,
                            ])}
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {sortedStudents.map((s, idx) => {
                            const key = String(s.person_id)
                            const expanded = expandedPerson === key
                            // 趋势数据按 person_id 守卫：切换人选的瞬间绝不闪现上一个人的数据
                            const trendsForPerson =
                              trends != null && String(trends.person_id) === key ? trends : null
                            return (
                              <Fragment key={key}>
                                <TableRow
                                  className="cursor-pointer"
                                  data-stripe={idx % 2 === 1}
                                  aria-expanded={expanded}
                                  onClick={() => setExpandedPerson(expanded ? null : key)}
                                >
                                  <TableCell className={cn('sticky left-0 z-10 w-24 whitespace-nowrap border-r text-xs tabular-nums text-slate-500', tableStyles.stickyCol)}>
                                    {displayStudentId(s.alias) || '—'}
                                  </TableCell>
                                  <TableCell className="border-r text-center text-xs text-slate-500">
                                    {s.class_num != null ? String(s.class_num).padStart(2, '0') : '—'}
                                  </TableCell>
                                  <TableCell className="border-r text-center text-xs text-slate-500">
                                    {s.xueji != null ? String(s.xueji) : '—'}
                                  </TableCell>
                                  <TableCell className={cn('sticky left-24 z-10 w-20 whitespace-nowrap border-r text-sm font-medium text-slate-900', tableStyles.stickyCol)}>
                                    {s.name ?? '（未命名）'}
                                  </TableCell>
                                  {studentGroups.flatMap((g) => {
                                    if (!g.paired) {
                                      const v = s.scores?.[g.subject] ?? null
                                      const pct = s.percentiles?.[g.subject] ?? null
                                      return [
                                        <TableCell
                                          key={`${g.subject}-score`}
                                          className={cn(
                                            'whitespace-nowrap text-right tabular-nums text-sm',
                                            v == null ? 'text-slate-300' : 'text-slate-900',
                                            isStudentSort('scores', g.subject) && tableStyles.sortedCell,
                                          )}
                                        >
                                          {v == null ? '—' : formatScore(v)}
                                        </TableCell>,
                                        // 学科年级百分位（2026-10-28）：与总分组同口径（年级前%，0–1 → %），
                                        // 缺失显示「—」不转 0；高二三选科无此列（源表不带）
                                        <TableCell key={`${g.subject}-pct`} className="whitespace-nowrap border-r text-right tabular-nums text-xs text-slate-500">
                                          {pct == null ? <span className="text-slate-300">—</span> : formatGradePct(pct)}
                                        </TableCell>,
                                      ]
                                    }
                                    const raw = s.scores?.[g.subject] ?? null
                                    const graded = s.grade_scores?.[g.subject] ?? null
                                    return [
                                      <TableCell key={`${g.subject}-raw`} className={cn('whitespace-nowrap text-right tabular-nums text-sm', isStudentSort('scores', g.subject) && tableStyles.sortedCell)}>
                                        {raw == null ? <span className="text-slate-300">—</span> : formatScore(raw)}
                                      </TableCell>,
                                      <TableCell key={`${g.subject}-graded`} className={cn('whitespace-nowrap border-r text-right tabular-nums text-sm', isStudentSort('grade_scores', g.subject) && tableStyles.sortedCell)}>
                                        {graded == null ? <span className="text-slate-300">—</span> : formatScore(graded)}
                                      </TableCell>,
                                    ]
                                  })}
                                  {totalCols.filter((tt) => !rankTotalCols.includes(tt)).map((tt) => {
                                    const v = s.totals?.[tt] ?? null
                                    return (
                                      <TableCell
                                        key={tt}
                                        className={cn(
                                          'whitespace-nowrap border-r text-right tabular-nums text-sm font-medium',
                                          v == null ? 'text-slate-300' : 'text-slate-900',
                                          isStudentSort('totals', tt) && tableStyles.sortedCell,
                                        )}
                                      >
                                        {v == null ? '—' : formatScore(v)}
                                      </TableCell>
                                    )
                                  })}
                                  {rankTotalCols.flatMap((tt) => {
                                    const v = s.totals?.[tt] ?? null
                                    const pct = s.percentiles?.[tt] ?? null
                                    const schoolRank = s.ranks?.[tt] ?? null
                                    const classRank = classRanksByTotal[tt]?.get(key) ?? null
                                    return [
                                      <TableCell key={`${tt}-score`} className={cn('whitespace-nowrap text-right tabular-nums text-sm font-medium', v == null ? 'text-slate-300' : 'text-slate-900', isStudentSort('totals', tt) && tableStyles.sortedCell)}>
                                        {v == null ? '—' : formatScore(v)}
                                      </TableCell>,
                                      <TableCell key={`${tt}-pct`} className="whitespace-nowrap text-right tabular-nums text-xs">
                                        {pct == null ? <span className="text-slate-300">—</span> : formatGradePct(pct)}
                                      </TableCell>,
                                      <TableCell key={`${tt}-school`} className="whitespace-nowrap text-right tabular-nums text-xs">
                                        {schoolRank == null ? <span className="text-slate-300">—</span> : String(schoolRank)}
                                      </TableCell>,
                                      <TableCell key={`${tt}-class`} className="whitespace-nowrap border-r text-right tabular-nums text-xs">
                                        {classRank == null ? <span className="text-slate-300">—</span> : String(classRank)}
                                      </TableCell>,
                                    ]
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
                                    {/* 2026-10-28 雷达脱滚：面板钉在滚动容器可视区——横向滚动时不动、
                                        其余行数据从两侧滑过；内层宽度=容器可视宽（ResizeObserver 跟随），
                                        雷达不再被几千像素的宽表扯开。sticky 必须挂在内层 div 而不是 td：
                                        sticky 元素不能越出包含块（td 的包含块是整行、td 本身横跨全列=行宽，
                                        零余量故永不钉住；div 的包含块是 td，余量=表宽−面板宽=最大滚动距离）。
                                        z-10 与冻结数据格同级，纵向滚动时从两级粘性表头（z 30/20/40）下方穿过 */}
                                    <TableCell
                                      colSpan={
                                        4 +
                                        studentGroups.length * 2 +
                                        (totalCols.length - rankTotalCols.length) +
                                        rankTotalCols.length * 4 +
                                        1
                                      }
                                      className="bg-white p-0 align-top"
                                    >
                                      <div
                                        className="sticky left-0 z-10 border-t border-slate-100 bg-slate-50/70 p-4"
                                        // 比可视宽少 24px：td 起始有十余像素自然偏移，不留余量滚到最右端面板会被钉出 ~10px
                                        style={{ width: Math.max((studentsViewportW ?? 1124) - 24, 480), maxWidth: '100%' }}
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
                  <p className="mt-3 text-xs text-slate-500">
                    点击科目或总分表头可从高到低排序；同分按学号升序，缺考置底，排序不改变班名次、名次。
                  </p>
                  {/* 2026-10-22：+3 口径脚注（学校导出口径说明） */}
                  {totalCols.includes('+3') ? (
                    <p className="mt-2 text-xs text-slate-400">
                      +3总分为选修三门等级分之和（学校导出口径），与各科原始分列不直接相加。等级分缺失时显示「—」，不以原始分替代。
                    </p>
                  ) : null}
                </CardContent>
              </Card>
                </TabsContent>

                <TabsContent value="frequency">
                  <Card>
                    <CardHeader>
                      <CardTitle>排名频次统计</CardTitle>
                      <CardDescription>
                        多场考试按年级百分位、精确等级分或总分名次档统计每名学生的落点次数。总分名次按学籍代码取列（学籍=1（闵中学籍）取学籍排名、学籍非1取年级排名、学籍未采集取学籍排名），单科口径统计全部学生。
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-5">
                      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px]">
                        <div>
                          <p className="mb-2 text-xs font-medium text-slate-500">选择考试（可多选）</p>
                          <div className="flex flex-wrap gap-2">
                            {(exams ?? []).map((exam) => {
                              const checked = frequencyExamNames.includes(exam.exam_key ?? exam.exam_name)
                              return (
                                <label
                                  key={exam.exam_key ?? exam.exam_name}
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
                                            : current.filter((name) => name !== (exam.exam_key ?? exam.exam_name))
                                        }
                                        return [...current, exam.exam_key ?? exam.exam_name]
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
                        <div className="max-h-[70vh] overflow-x-auto">
                          {/* 2026-10-23 表头冻结：限高滚动容器在本层；内层 Table 默认包装也是
                              overflow-auto（同样产生滚动容器，sticky 会绑错对象），改 overflow-visible
                              让表头 sticky 绑定到本外层 —— 与教学分数段/学生表同款。
                              表头底色由全局 thead th 渐变提供（不透明，滑过的行不会透出）。 */}
                          <Table wrapperClassName="overflow-visible">
                            <TableHeader>
                              <TableRow>
                                <TableHead className="sticky top-0 z-20 whitespace-nowrap">学生</TableHead>
                                <TableHead className="sticky top-0 z-20 whitespace-nowrap">别名</TableHead>
                                {frequency.bins.map((bin) => (
                                  <TableHead key={bin.key} className="sticky top-0 z-20 whitespace-nowrap text-right">{bin.label}</TableHead>
                                ))}
                                <TableHead className="sticky top-0 z-20 whitespace-nowrap text-right">有效次数</TableHead>
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
                      <CardDescription>
                        按指标类型筛选：总分按名次（学籍=1（闵中学籍）取学籍排名、学籍非1取年级排名、学籍未采集取学籍排名）；语数英与高一各学科按年级排名百分位（前%，越小越靠前）；加三学科按等级分（40–70）。同时显示本班名次；筛选依据值缺失的学生不进名单。
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-5">
                      <div className="grid gap-3 sm:grid-cols-3">
                        <div>
                          <p className="mb-1.5 text-xs font-medium text-slate-500">指标</p>
                          <Select
                            value={rangeMetric ?? undefined}
                            onValueChange={(value) => {
                              // 跨类型切换（名次/前%/等级分）时区间单位变了，
                              // 沿用旧数值会立刻 422，重置为该类型默认区间。
                              const nextKind = rangeMetrics.find((metric) => metric.value === value)?.kind ?? 'total_rank'
                              if (nextKind !== rangeKind) {
                                const next = RANGE_KIND_BOUNDS[nextKind]
                                setRangeMin(next.defMin)
                                setRangeMax(next.defMax)
                              }
                              setRangeMetric(value)
                            }}
                          >
                            <SelectTrigger aria-label="排名区间指标"><SelectValue placeholder="选择指标" /></SelectTrigger>
                            <SelectContent>
                              {rangeMetrics.map((metric) => (
                                <SelectItem key={metric.value} value={metric.value}>{metric.label}</SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                        <label className="text-xs font-medium text-slate-500">
                          {rangeBounds.minLabel}
                          <Input className="mt-1.5" inputMode="numeric" value={rangeMin} onChange={(event) => setRangeMin(Math.min(rangeBounds.hi, Math.max(rangeBounds.lo, Number(event.target.value) || rangeBounds.lo)))} />
                        </label>
                        <label className="text-xs font-medium text-slate-500">
                          {rangeBounds.maxLabel}
                          <Input className="mt-1.5" inputMode="numeric" value={rangeMax} onChange={(event) => setRangeMax(Math.min(rangeBounds.hi, Math.max(rangeMin, Number(event.target.value) || rangeMin)))} />
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
                                <TableHead className="text-right">{rangeKind === 'subject_percentile' ? '年级前%' : rangeKind === 'subject_grade_score' ? '等级分' : '年级名次'}</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {rankRange.students.map((student) => (
                                <TableRow key={String(student.person_id)}>
                                  <TableCell className="font-medium">{student.name ?? '（未命名）'}</TableCell>
                                  <TableCell className="text-xs text-slate-500">{student.alias ?? '—'}</TableCell>
                                  <TableCell className="text-right tabular-nums">{formatScore(student.score)}</TableCell>
                                  <TableCell className="text-right tabular-nums">{student.class_rank ?? '—'}</TableCell>
                                  <TableCell className="text-right font-medium tabular-nums">
                                    {rangeKind === 'subject_percentile'
                                      ? (student.metric_value != null ? `${student.metric_value}%` : '—')
                                      : rangeKind === 'subject_grade_score'
                                        ? (student.metric_value ?? '—')
                                        : (student.year_rank ?? '—')}
                                  </TableCell>
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
                    主三门名次标注明显进退步、波动、临界段、薄弱段与稳定优秀（名次按学籍代码取列：学籍=1（闵中学籍）取学籍排名、学籍非1取年级排名、学籍未采集取学籍排名）；单科年级百分位较主三门落后 20 个百分点时标注严重偏科。
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
                            <TableHead className="text-right">名次</TableHead>
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
                  {/* 名次段分布（2026-10-19）：读导入的名次段表（3+3总分/主三门两口径）；
                      堆叠条样式与教学工作台分数段卡同源（Viridis，段色按段绝对位置、
                      高名次亮端），表格为学校名次段表原样（行=班级，列=段）。 */}
                  <Card>
                    <CardHeader className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <CardTitle>名次段分布</CardTitle>
                        <CardDescription>
                          {rankBands != null && rankBands.classes.length > 0
                            ? `${rankBands.exam_name} · ${rankBands.total_type} 口径 · 全年级 ${String(rankBands.grade_bands.reduce((sum, b) => sum + b.count, 0))} 人；分段按导入的名次段表。`
                            : '按导入的《3+3总分名次段》《主三门名次段》表展示各班名次段人数。'}
                        </CardDescription>
                      </div>
                      {rankBands != null && rankBands.available_total_types.length > 0 ? (
                        <Select
                          value={rankBandsMetric ?? rankBands.total_type ?? undefined}
                          onValueChange={setRankBandsMetric}
                        >
                          <SelectTrigger className="h-8 w-[170px] text-xs" aria-label="名次段口径">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {rankBands.available_total_types.map((tt) => (
                              <SelectItem key={tt} value={tt}>
                                {tt === '3+3' ? '3+3总分' : `${tt}总分`}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : null}
                    </CardHeader>
                    <CardContent>
                      {rankBandsError ? (
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <p className="text-sm text-slate-600">{rankBandsError}</p>
                          <Button variant="outline" size="sm" onClick={() => setRankBandsNonce((n) => n + 1)}>
                            重试
                          </Button>
                        </div>
                      ) : rankBands == null ? (
                        <Skeleton className="h-40 w-full" />
                      ) : rankBands.classes.length === 0 ? (
                        <div className="flex items-start gap-2 rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-3">
                          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                          <div>
                            <p className="text-sm font-medium text-slate-600">本场考试尚未导入名次段表</p>
                            <p className="mt-0.5 text-xs text-slate-400">
                              请到「数据上传」随考试上传学校的《3+3总分名次段表》与《主三门名次段表》，导入后这里展示各班分段人数。
                            </p>
                          </div>
                        </div>
                      ) : (
                        <RankBandsPanel data={rankBands} />
                      )}
                    </CardContent>
                  </Card>
                </TabsContent>

                <TabsContent value="duanwei">
                  {/* 段位分布（2026-10-20 恢复，F10 三段阈值）：按成绩明细表导入的名次现算；
                      仅总分口径。2026-10-24 起名次按学籍代码取列（学籍=1 学籍排名、
                      学籍非1 年级排名、未采集学籍排名优先），全员参与统计。 */}
                  <Card>
                    <CardHeader className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <CardTitle>段位分布</CardTitle>
                        <CardDescription>
                          按成绩明细表名次计算（学籍=1（闵中学籍）取学籍排名、学籍非1取年级排名、学籍未采集取学籍排名）；段位阈值沿用分析配置。悬浮分段条可查看段内学生名单。
                        </CardDescription>
                      </div>
                      {duanweiOptions.length > 0 ? (
                        <Select value={activeDuanwei ?? undefined} onValueChange={setBandMetric}>
                          <SelectTrigger className="h-8 w-[170px] text-xs" aria-label="段位口径">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {duanweiOptions.map((o) => (
                              <SelectItem key={o.value} value={o.value}>
                                {o.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : null}
                    </CardHeader>
                    <CardContent>
                      {duanweiError && duanweiUnavailable ? (
                        /* 契约 p3 §2.1 v2.1（F10）：无名次口径时 409，呈现「不可计算」
                           提示卡而非错误分段或永久骨架 */
                        <div className="flex items-start gap-2 rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-3">
                          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                          <div>
                            <p className="text-sm font-medium text-slate-600">段位不可计算（无名次口径）</p>
                            <p className="mt-0.5 text-xs text-slate-400">
                              当前口径的学生没有名次数据；导入带排名的成绩明细表后可计算。
                            </p>
                          </div>
                        </div>
                      ) : duanweiError ? (
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <p className="text-sm text-slate-600">{duanweiError}</p>
                          <Button variant="outline" size="sm" onClick={() => setDuanweiNonce((n) => n + 1)}>
                            重试
                          </Button>
                        </div>
                      ) : duanweiBands == null ? (
                        <Skeleton className="h-32 w-full" />
                      ) : duanweiBands.bands.length === 0 ? (
                        <p className="py-6 text-center text-sm text-slate-500">
                          当前口径暂无段位数据（该场可能全部缺考或尚未导入）。
                        </p>
                      ) : (
                        /* 2026-10-23：悬浮分段条浮出段内学生名单（names 按名次升序，与 students 同序） */
                        <TooltipProvider delayDuration={150}>
                          <ul className="space-y-2">
                            {duanweiBands.bands.map((b) => {
                              const pct = Math.round((b.count / duanweiMaxCount) * 100)
                              const totalPeople = duanweiBands.bands.reduce((sum, x) => sum + x.count, 0)
                              const namesLabel = (b.names ?? []).filter(Boolean).join('、')
                              const track = (
                                <div className="h-3 flex-1 cursor-default overflow-hidden rounded-full bg-slate-100">
                                  <div
                                    className="h-full rounded-full bg-gradient-to-r from-[#1f7fd6] to-[#35b9e9]"
                                    style={{ width: `${String(pct)}%` }}
                                  />
                                </div>
                              )
                              return (
                                <li key={b.label} className="flex items-center gap-3">
                                  <span className="w-40 shrink-0 truncate text-xs text-slate-500" title={b.label}>
                                    {b.label}
                                  </span>
                                  {b.count > 0 ? (
                                    <UITooltip>
                                      <TooltipTrigger asChild>{track}</TooltipTrigger>
                                      <TooltipContent sideOffset={6} className="max-w-[320px] whitespace-normal">
                                        <p className="font-medium">
                                          {b.label} · {String(b.count)} 人
                                        </p>
                                        {namesLabel ? (
                                          <p className="mt-1 leading-relaxed text-slate-600">{namesLabel}</p>
                                        ) : null}
                                      </TooltipContent>
                                    </UITooltip>
                                  ) : (
                                    track
                                  )}
                                  <span className="w-24 shrink-0 text-right text-xs tabular-nums text-slate-600">
                                    {String(b.count)} 人{totalPeople > 0 ? ` · ${((b.count / totalPeople) * 100).toFixed(1)}%` : ''}
                                  </span>
                                </li>
                              )
                            })}
                          </ul>
                        </TooltipProvider>
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

    </div>
  )
}
