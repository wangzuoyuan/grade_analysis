'use client'

import { useMemo, useState } from 'react'
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from 'recharts'
import { cn } from '@/lib/utils'

/**
 * 学生历次成绩雷达（2026-10-25 用户拍板）：
 * - 每场考试一个半透明多边形重叠，图例按钮点选筛选显示哪几场；
 * - 轴值统一换算 0–100「位次分」，越靠外越好——年级前% 轴 = 100 − 前%，
 *   等级分轴 =（等级分 − 40）÷ 30 × 100（等级分制 40–70），悬浮提示显示原值；
 * - 「总分」轴：位置按总分行年级百分位换算（与学科轴同刻度），悬浮显示第 N 名；
 *   总分口径优先 3+3 / 九门 → 主三门 / 五门 → 第一个有名次的，+3（等级和）无名次不进；
 * - 轴数 > 7 拆两张（高一九门场景）：主科雷达 = 语数英 + 总分，选科雷达 = 其余学科；
 *   轴数 ≤ 7（高二/三 6 学科 + 总分）一张。全部数据驱动，不依赖年级配置。
 */

export const GRADE_SCORE_MIN = 40
export const GRADE_SCORE_MAX = 70

/** 年级前% → 位次分（0–100，越小越靠前 → 越外越好）。 */
export function percentileToRadarScore(p: number): number {
  return 100 - p
}

/** 等级分（40–70）→ 位次分（0–100 线性映射，越大越好方向一致）。 */
export function gradeScoreToRadarScore(g: number): number {
  return ((g - GRADE_SCORE_MIN) / (GRADE_SCORE_MAX - GRADE_SCORE_MIN)) * 100
}

/** 规范学科序（后端 CANONICAL_SUBJECT_ORDER 同序；未知学科按字母序追加）。 */
const SUBJECT_ORDER = ['语文', '数学', '英语', '物理', '化学', '生物', '政治', '历史', '地理']
const CORE_SUBJECTS = ['语文', '数学', '英语']

/** 总分轴口径优先级（+3 为等级分之和、无名次数据，不进雷达）。 */
const TOTAL_PRIORITY = ['3+3', '九门', '主三门', '五门']
const RADAR_EXCLUDED_TOTALS = new Set(['+3'])

/** 轴数上限：超过拆「主科雷达 + 选科雷达」两张。 */
const RADAR_MAX_AXES = 7

/** 总分口径列头映射：学校导出口径名 → 展示名（未映射口径原样展示）。 */
export const TOTAL_TYPE_LABELS: Record<string, string> = {
  '+3': '+3总分（等级和）',
  '主三门': '三门总分',
  '3+3': '3+3总分',
  '五门': '五门总分',
  '九门': '九门总分',
}

export function totalTypeLabel(t: string): string {
  return TOTAL_TYPE_LABELS[t] ?? t
}

/** 一场考试的雷达数据行（与数据源解耦，两个调用方各自组装）。 */
export interface RadarSubjectCell {
  /** 来源年级百分位的百分数（0–100，越小越靠前）；缺失 null 不推算。 */
  percentile: number | null
  grade_score: number | null
}
export interface RadarTotalCell {
  /** 来源年级百分位的百分数（0–100）；总分轴定位用。 */
  percentile: number | null
  /** 校名次（总分行才有）；悬浮显示「第 N 名」。 */
  rank: number | null
}
export interface RadarExamRow {
  /** 同场考试去重键（exam_name|date），作 recharts dataKey。 */
  exam_key: string
  exam_name: string
  exam_date: string | null
  subjects: Record<string, RadarSubjectCell>
  totals: Record<string, RadarTotalCell>
}

type AxisStandard = 'percentile' | 'grade' | 'total'

interface AxisDef {
  /** recharts dataKey / 显示名。 */
  key: string
  standard: AxisStandard
  /** 轴上任何考试有值（否则整轴无顶点，不画）。 */
  hasValue: boolean
}

const RADAR_COLORS = [
  '#2563eb',
  '#dc2626',
  '#16a34a',
  '#9333ea',
  '#ea580c',
  '#0891b2',
  '#65a30d',
  '#db2777',
  '#475569',
]

function radarColor(index: number): string {
  return RADAR_COLORS[index % RADAR_COLORS.length]
}

/** 学科轴标准按数据判定：任一考试有等级分 → 等级分轴；否则前%轴。 */
function subjectStandard(rowCells: Array<RadarSubjectCell | undefined>): AxisStandard {
  return rowCells.some((c) => c?.grade_score != null) ? 'grade' : 'percentile'
}

/** 悬浮原值文案（不造数：缺前%/等级分/名次一律「—」）。 */
function axisRawLabel(axis: AxisDef, row: RadarExamRow): string {
  if (axis.standard === 'total') {
    const cell = row.totals[axis.key]
    if (!cell) return '—'
    if (cell.rank != null) return `第 ${cell.rank} 名`
    if (cell.percentile != null) return `前 ${cell.percentile}%`
    return '—'
  }
  const cell = row.subjects[axis.key]
  if (!cell) return '—'
  if (axis.standard === 'grade') return cell.grade_score != null ? `等级分 ${cell.grade_score}` : '—'
  return cell.percentile != null ? `前 ${cell.percentile}%` : '—'
}

function radarValue(axis: AxisDef, row: RadarExamRow): number | null {
  if (axis.standard === 'total') {
    const p = row.totals[axis.key]?.percentile
    return p != null ? percentileToRadarScore(p) : null
  }
  const cell = row.subjects[axis.key]
  if (!cell) return null
  if (axis.standard === 'grade') {
    return cell.grade_score != null ? gradeScoreToRadarScore(cell.grade_score) : null
  }
  return cell.percentile != null ? percentileToRadarScore(cell.percentile) : null
}

/** 自定义悬浮：列出该场考试在各轴的原值（前% / 等级分 / 第 N 名）。 */
function RadarTooltipContent({
  active,
  payload,
  rows,
  axes,
}: {
  active?: boolean
  payload?: Array<{ dataKey?: string | number }>
  rows: RadarExamRow[]
  axes: AxisDef[]
}) {
  if (!active || !payload?.length) return null
  const dataKey = String(payload[0]?.dataKey ?? '')
  const row = rows.find((r) => r.exam_key === dataKey)
  if (!row) return null
  return (
    <div className="rounded-md border border-slate-200 bg-white/95 px-3 py-2 text-xs shadow-sm">
      <p className="mb-1 font-semibold text-slate-700">
        {row.exam_name}
        {row.exam_date ? `（${row.exam_date.slice(0, 7)}）` : ''}
      </p>
      <ul className="space-y-0.5">
        {axes.map((axis) => (
          <li key={axis.key} className="flex items-center justify-between gap-4">
            <span className="text-slate-500">{axis.key}</span>
            <span className="font-medium text-slate-700">{axisRawLabel(axis, row)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

interface RadarGroup {
  caption: string | null
  axes: AxisDef[]
}

export function StudentScoreRadar({ rows, className }: { rows: RadarExamRow[]; className?: string }) {
  const [hiddenExams, setHiddenExams] = useState<Set<string>>(() => new Set())

  const { axes, totalType, groups } = useMemo(() => {
    // 学科轴：规范序 ∩ 出现过的学科，任一考试有值才成轴
    const present = SUBJECT_ORDER.filter((sub) =>
      rows.some((r) => r.subjects[sub] != null),
    )
    const extras = Array.from(
      new Set(
        rows.flatMap((r) => Object.keys(r.subjects)).filter((s) => !SUBJECT_ORDER.includes(s)),
      ),
    ).sort((a, b) => a.localeCompare(b, 'zh'))
    const subjectAxes: AxisDef[] = [...present, ...extras].map((sub) => {
      const standard = subjectStandard(rows.map((r) => r.subjects[sub]))
      const probe: AxisDef = { key: sub, standard, hasValue: true }
      return { key: sub, standard, hasValue: rows.some((r) => radarValue(probe, r) != null) }
    })

    // 总分轴：优先级选口径（排除 +3），任一考试有百分位才可定位
    const totalCandidates = Array.from(new Set(rows.flatMap((r) => Object.keys(r.totals))))
      .filter((t) => !RADAR_EXCLUDED_TOTALS.has(t))
      .sort((a, b) => {
        const ia = TOTAL_PRIORITY.indexOf(a)
        const ib = TOTAL_PRIORITY.indexOf(b)
        return (ia === -1 ? TOTAL_PRIORITY.length : ia) - (ib === -1 ? TOTAL_PRIORITY.length : ib) || a.localeCompare(b, 'zh')
      })
    let chosenTotal: string | null = null
    for (const t of totalCandidates) {
      if (rows.some((r) => r.totals[t]?.percentile != null)) {
        chosenTotal = t
        break
      }
    }
    const totalAxis: AxisDef | null =
      chosenTotal != null && rows.some((r) => r.totals[chosenTotal]?.percentile != null)
        ? { key: totalTypeLabel(chosenTotal), standard: 'total', hasValue: true }
        : null

    const allAxes = [...subjectAxes.filter((a) => a.hasValue), ...(totalAxis?.hasValue ? [totalAxis] : [])]

    // 轴数 > 7（高一九门场景）：主科雷达 = 语数英 + 总分，选科雷达 = 其余学科
    let radarGroups: RadarGroup[]
    if (allAxes.length > RADAR_MAX_AXES) {
      const coreKeys = new Set<string>(CORE_SUBJECTS)
      const coreAxes = allAxes.filter((a) => coreKeys.has(a.key) || a.standard === 'total')
      const restAxes = allAxes.filter((a) => !coreKeys.has(a.key) && a.standard !== 'total')
      radarGroups =
        restAxes.length > 0
          ? [
              { caption: '主科雷达（语数英 + 总分）', axes: coreAxes },
              { caption: '选科雷达', axes: restAxes },
            ]
          : [{ caption: null, axes: coreAxes }]
    } else {
      radarGroups = [{ caption: null, axes: allAxes }]
    }
    return { axes: allAxes, totalType: chosenTotal, groups: radarGroups }
  }, [rows])

  const toggleExam = (key: string) => {
    setHiddenExams((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  if (rows.length === 0 || axes.length === 0) {
    return (
      <div className={cn('flex h-40 items-center justify-center text-xs text-slate-400', className)}>
        该生本段暂无可画雷达的成绩数据（需年级前%、等级分或总分百分位）
      </div>
    )
  }

  return (
    <div className={cn('space-y-3', className)}>
      <div className="flex min-h-8 flex-wrap items-center justify-center gap-x-1 gap-y-1" aria-label="雷达考试选择">
        {rows.map((row, index) => {
          const hidden = hiddenExams.has(row.exam_key)
          const color = radarColor(index)
          return (
            <button
              key={row.exam_key}
              type="button"
              aria-pressed={!hidden}
              aria-label={`${hidden ? '显示' : '隐藏'}${row.exam_name}雷达`}
              onClick={() => toggleExam(row.exam_key)}
              title={row.exam_date ? `${row.exam_name}（${row.exam_date}）` : row.exam_name}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300',
                hidden ? 'text-slate-400 line-through hover:bg-slate-100' : 'text-slate-600 hover:bg-brand-50',
              )}
            >
              <span
                aria-hidden="true"
                className={cn('h-2 w-2 rounded-full transition-opacity', hidden && 'opacity-30')}
                style={{ backgroundColor: color }}
              />
              {row.exam_name}
            </button>
          )
        })}
      </div>
      <div className={cn('grid gap-5', groups.length > 1 && 'xl:grid-cols-2')}>
        {groups.map((group) => {
          const data = group.axes.map((axis) => {
            const item: Record<string, string | number | null> = { axis: axis.key }
            for (const row of rows) item[row.exam_key] = radarValue(axis, row)
            return item
          })
          return (
            <div key={group.caption ?? 'all'} className="space-y-1">
              {group.caption && <p className="text-center text-xs font-medium text-slate-600">{group.caption}</p>}
              <div className="h-80 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <RadarChart data={data} outerRadius="72%" innerRadius="8%">
                    <PolarGrid stroke="#dbe7f3" />
                    <PolarAngleAxis dataKey="axis" tick={{ fontSize: 11, fill: '#475569' }} />
                    <PolarRadiusAxis
                      angle={90}
                      domain={[0, 100]}
                      tick={{ fontSize: 9, fill: '#94a3b8' }}
                      tickFormatter={(v: number) => `${v}`}
                      axisLine={false}
                    />
                    <Tooltip
                      content={<RadarTooltipContent rows={rows} axes={group.axes} />}
                      isAnimationActive={false}
                    />
                    {rows.map((row, index) => {
                      const color = radarColor(index)
                      return (
                        <Radar
                          key={row.exam_key}
                          name={row.exam_name}
                          dataKey={row.exam_key}
                          stroke={color}
                          fill={color}
                          fillOpacity={0.1}
                          strokeWidth={2}
                          dot={{ r: 2, strokeWidth: 1 }}
                          connectNulls={false}
                          isAnimationActive={false}
                          hide={hiddenExams.has(row.exam_key)}
                        />
                      )
                    })}
                  </RadarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )
        })}
      </div>
      <p className="text-[10px] text-slate-400">
        轴值已统一换算为 0–100 位次分（越靠外越好）：年级前% 轴 = 100 − 前%，等级分轴 =（等级分 − 40）÷ 30 × 100
        {totalType ? `；总分轴为${totalTypeLabel(totalType)}口径，悬浮显示第 N 名` : ''}。缺考或缺失指标不画顶点。
      </p>
    </div>
  )
}
