'use client'
import { Fragment, useState } from 'react'
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
import type { PlatformCompare, PlatformMetrics } from '@/lib/api-v1'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import { bandBarColor } from './band-colors'

import { qualityNumber, qualityPercent } from '@/lib/teaching-score-display'

const gradeBands = ['A+', 'A', 'B+', 'B', 'B-', 'C+', 'C', 'C-', 'D+', 'D', 'E']
type ClassRow = PlatformCompare['classes'][number]
export const byClassName = (a: ClassRow, b: ClassRow) =>
  a.class_label.localeCompare(b.class_label, 'zh-CN', { numeric: true }) || a.id.localeCompare(b.id)

/** 我教的班级柱色（主题蓝）与其他班级（灰色半透明）——同 HomeroomCompare 高亮口径。 */
const MINE_BAR_COLOR = '#1f7fd6'
const OTHER_BAR_COLOR = '#94a3b8'

type BandBar = { key: string; label: string; avg: number | null | undefined; weight: number; counts: number[]; total: number }

/** 等级（高一为分数段）人数堆叠条（2026-10-21，样式对齐教学分数段卡）：
 *  每班一行、段宽=该等级人数占本班有等级人数比例；白色细分隔线分段；悬浮显示
 *  「等级 N 人 · 占比」且同等级跨全部行联动高亮；多班时附合计行，均分按实考人数加权。 */
function BandBars({ labels, rows }: { labels: string[]; rows: ClassRow[] }) {
  const [hoverBand, setHoverBand] = useState<number | null>(null)
  const bars: BandBar[] = rows.map(c => {
    const counts = labels.map(label => c.metrics.bands?.find(b => b.label === label)?.count ?? 0)
    return {
      key: c.id, label: c.class_label, avg: c.metrics.raw_avg,
      weight: c.metrics.actual_count && c.metrics.actual_count > 0 ? c.metrics.actual_count : 0,
      counts, total: counts.reduce((s, n) => s + n, 0),
    }
  }).filter(b => b.total > 0)
  if (!labels.length || !bars.length) return null
  const totals = labels.map((_, i) => bars.reduce((s, b) => s + b.counts[i], 0))
  const grand = totals.reduce((s, n) => s + n, 0)
  const rowsOut: BandBar[] = bars.length > 1 && grand > 0
    ? [...bars, { key: '__total__', label: '合计', avg: null, weight: 0, counts: totals, total: grand }]
    : bars
  const visible = labels.map((label, i) => ({ label, i })).filter(x => rowsOut.some(b => b.counts[x.i] > 0))
  const avgBars = bars.filter(b => b.avg != null)
  const wSum = avgBars.reduce((s, b) => s + (b.weight || b.total), 0)
  const weightedAvg = wSum > 0
    ? avgBars.reduce((s, b) => s + (b.avg as number) * (b.weight || b.total), 0) / wSum
    : null
  return (
    <TooltipProvider delayDuration={150}>
      <div className="space-y-2" onMouseLeave={() => setHoverBand(null)}>
        {rowsOut.map(b => (
          <div key={b.key} className="flex items-center gap-3">
            <span className="w-32 shrink-0 truncate text-xs text-slate-500" title={`${b.label}（${b.total} 人）`}>
              {b.label} <span className="text-slate-400">{b.total} 人</span>
            </span>
            <div className="flex h-4 flex-1 overflow-hidden rounded-full bg-slate-100 [&>*+*]:border-l [&>*+*]:border-white/90">
              {visible.map(({ i }) => b.counts[i] > 0 ? (
                <Tooltip key={i}>
                  <TooltipTrigger asChild>
                    <div
                      className={cn(
                        'h-full cursor-default transition-opacity',
                        hoverBand != null && hoverBand !== i && 'opacity-25',
                        hoverBand === i && 'ring-1 ring-inset ring-slate-900/50',
                      )}
                      onMouseEnter={() => setHoverBand(i)}
                      style={{
                        width: `${String((b.counts[i] / b.total) * 100)}%`,
                        background: bandBarColor(i, labels.length),
                      }}
                    />
                  </TooltipTrigger>
                  <TooltipContent sideOffset={6}>
                    <span className="font-medium">{labels[i]}</span>
                    <span className="ml-2 text-slate-500">
                      {String(b.counts[i])} 人 · {((b.counts[i] / b.total) * 100).toFixed(1)}%
                    </span>
                  </TooltipContent>
                </Tooltip>
              ) : null)}
            </div>
            <span className="w-16 shrink-0 text-right text-xs tabular-nums text-slate-500">
              均 {qualityNumber(b.key === '__total__' ? weightedAvg : b.avg)}
            </span>
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-slate-500">
        {visible.map(({ label, i }) => (
          <span key={label} className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm" style={{ background: bandBarColor(i, labels.length) }} />
            {label}
          </span>
        ))}
      </div>
    </TooltipProvider>
  )
}

/** 各班平均分柱状图（2026-10-22 用户拍板）：全部班级的原始分均分 + 等级分均分
 *  双柱（高一只原始分、无图例）；我教的班级（managed_class_id 非空）主题色高亮，
 *  其余灰色半透明（同 HomeroomCompare 逐格 Cell 口径）。全部均分为空时整块不渲染。 */
function ClassAvgBars({ rows, grade }: { rows: ClassRow[]; grade: number }) {
  const chartData = rows.map(c => ({
    classLabel: c.class_label,
    // 我教的班级：后端 is_managed 判定（教学班 label 命中 或 行政班任课教师同名），
    // 兼容只带 managed_class_id 的旧快照。
    mine: c.is_managed ?? c.managed_class_id != null,
    raw_avg: c.metrics.raw_avg ?? null,
    grade_avg: grade === 1 ? null : c.metrics.grade_avg ?? null,
  }))
  if (!chartData.length || !chartData.some(d => d.raw_avg != null || d.grade_avg != null)) return null
  return (
    <div className="rounded-lg border border-slate-100 p-3">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-medium text-slate-700">各班平均分对比</p>
        <p className="text-xs text-slate-400">主题色为我教的教学班，灰色为其他班级</p>
      </div>
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#dcecf8" vertical={false} />
            <XAxis
              dataKey="classLabel"
              interval={0}
              angle={-20}
              textAnchor="end"
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
              formatter={(v: number | string) => (typeof v === 'number' ? v.toFixed(1) : v)}
            />
            {grade !== 1 && <Legend wrapperStyle={{ fontSize: 12, paddingTop: 4 }} iconType="circle" />}
            <Bar dataKey="raw_avg" name="原始分均分" maxBarSize={22} radius={[3, 3, 0, 0]}>
              {chartData.map((d, i) => (
                <Cell key={i} fill={d.mine ? MINE_BAR_COLOR : OTHER_BAR_COLOR} fillOpacity={d.mine ? 0.9 : 0.35} />
              ))}
            </Bar>
            {grade !== 1 && (
              <Bar dataKey="grade_avg" name="等级分均分" maxBarSize={22} radius={[3, 3, 0, 0]}>
                {chartData.map((d, i) => (
                  <Cell key={i} fill={d.mine ? MINE_BAR_COLOR : OTHER_BAR_COLOR} fillOpacity={d.mine ? 0.9 : 0.35} />
                ))}
              </Bar>
            )}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

export function TeachingQualityTables({ data, grade }: { data: PlatformCompare; grade: number }) {
  // 2026-10-22 用户拍板：行政班与教学班不再分两张卡，合并为一张表展示
  // （导入仍是两个文件，class_kind 保留为班级名后的类型徽标）。
  // 原表区只展示已导入的官方指标，不以学生样本估算补齐原表空值。
  const rows = data.classes.filter(c => c.source === 'official').sort(byClassName)
  const bands = grade === 1 ? Array.from(new Set(rows.flatMap(c => (c.metrics.bands ?? []).map(b => b.label)))) : gradeBands
  return <Card className="min-w-0">
    <CardHeader><CardTitle>班级质量分析</CardTitle>
      <p className="text-sm text-slate-500">行政班与教学班合并展示 · {rows.length} 个班级 · 官方指标按导入文件展示，空值为“—”。</p>
    </CardHeader>
    <CardContent>{!rows.length ? <p className="py-6 text-sm text-slate-500">本场尚未导入质量表。</p> :
      <div className="space-y-4">
        <ClassAvgBars rows={rows} grade={grade} />
        <BandBars labels={bands} rows={rows} />
        <Table className="whitespace-nowrap" aria-label="质量分析表">
          <TableHeader>
            <TableRow>
              <TableHead rowSpan={2}>班级</TableHead><TableHead rowSpan={2}>任课教师</TableHead>
              <TableHead colSpan={2} className="text-center">人数</TableHead>
              <TableHead colSpan={grade === 1 ? 1 : 2} className="text-center">平均分</TableHead>
              <TableHead rowSpan={2} className="text-right">标准差</TableHead>
              {grade === 1 && ['单科总分', '最高分', '最低分'].map(label =>
                <TableHead key={label} rowSpan={2} className="text-right">{label}</TableHead>)}
              {bands.length > 0 && <TableHead colSpan={bands.length * 2} className="text-center">{grade === 1 ? '分数段人数及比率' : '等级人数及比率'}</TableHead>}
            </TableRow>
            <TableRow><TableHead className="text-right">应考</TableHead><TableHead className="text-right">实考</TableHead>
              <TableHead className="text-right">原始</TableHead>{grade !== 1 && <TableHead className="text-right">等级</TableHead>}
              {bands.map(label => <Fragment key={label}><TableHead className="text-right">{label}</TableHead><TableHead className="text-right">比率</TableHead></Fragment>)}
            </TableRow>
          </TableHeader>
          <TableBody>{rows.map(c => <TableRow key={c.id}>
            <TableCell className="font-medium">{c.class_label}
              <span className="ml-1.5 text-[10px] text-slate-400">{c.class_kind === 'administrative' ? '行政班' : '教学班'}</span>
            </TableCell><TableCell>{c.teacher_name ?? '—'}</TableCell>
            <TableCell className="text-right tabular-nums">{qualityNumber(c.metrics.expected_count, 0)}</TableCell>
            <TableCell className="text-right tabular-nums">{qualityNumber(c.metrics.actual_count, 0)}</TableCell>
            <TableCell className="text-right tabular-nums">{qualityNumber(c.metrics.raw_avg)}</TableCell>
            {grade !== 1 && <TableCell className="text-right tabular-nums">{qualityNumber(c.metrics.grade_avg)}</TableCell>}
            <TableCell className="text-right tabular-nums" title={c.metrics.stddev_basis === 'unspecified' ? '官方未注明原始分/等级分口径' : '原始分标准差'}>{qualityNumber(c.metrics.stddev)}</TableCell>
            {grade === 1 && [c.metrics.score_sum, c.metrics.max_score, c.metrics.min_score].map((v, i) =>
              <TableCell key={i} className="text-right tabular-nums">{qualityNumber(v)}</TableCell>)}
            {bands.map(label => { const band = c.metrics.bands?.find(b => b.label === label); return <Fragment key={label}>
              <TableCell className="text-right tabular-nums">{qualityNumber(band?.count, 0)}</TableCell><TableCell className="text-right tabular-nums">{qualityPercent(band?.ratio)}</TableCell>
            </Fragment> })}
          </TableRow>)}</TableBody>
        </Table>
      </div>}
    </CardContent>
  </Card>
}
