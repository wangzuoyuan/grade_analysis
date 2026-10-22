'use client'

/**
 * 教学工作台成绩分析页（契约 p3-imports-analysis.md §2.2/§3）。
 *
 * 数据全部来自 /api/v1：shared/exams（考试清单）、teaching/analysis/*（stats/students/class-compare）。
 * 计量红线（契约 §2.2/§2.3）：缺考（score NULL）一律显示「—」不转 0；rank 为当前所选管理班范围内名次
 * （同分同名次），不可计算时缺省显示「—」；官方质量对比集中在班级对比页；迟到回包按资源分离的序号丢弃（F11：清单与数据互不作废）。
 */

import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { AlertCircle, BarChart3, BookOpen, Users } from 'lucide-react'

import {
  fetchTeachingScoreBands,
  fetchTeachingStats,
  fetchTeachingStudents,
  listExams,
  type ExamSummary,
  type TeachingScoreBandsResponse,
  type TeachingStatsResponse,
  type TeachingStudentRow,
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
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { CorrelationCard } from '@/components/homework/CorrelationCard'
import { homeworkScopeQuery } from '@/components/homework/shared'
import { ExamSelect } from './ExamSelect'
import { ScoreYearPicker } from './ScoreYearPicker'
import { TeachingStudentDetails } from './TeachingStudentDetails'
import { ClassScopePicker } from '@/components/ClassScopePicker'
import { analysisScopeQuery, formatScore, scoreBasisLabel, sortExamsDesc } from './shared'

/** 分数段段口标签：顶段 [90,+∞)，其余 [80,90)（左闭右开）。 */
function bandLabel(low: number, high: number | null): string {
  const edge = (n: number) => (Number.isInteger(n) ? String(n) : String(n))
  return high == null ? `[${edge(low)}, +∞)` : `[${edge(low)}, ${edge(high)})`
}

// 段位配色自本文件抽出至 band-colors.ts（两工作台共用，2026-10-19）；
// 锚点色值/插值钳制的契约断言改为读共享文件。
import { bandBarColor } from './band-colors'

/** 本段格热力底色：占本班有效人数比例分档（纯静态类，无运行时拼类名）。 */
function bandCellTone(count: number, valid: number): string {
  if (count <= 0 || valid <= 0) return ''
  const ratio = count / valid
  if (ratio >= 0.2) return 'bg-sky-100/70'
  if (ratio >= 0.1) return 'bg-sky-50'
  return 'bg-sky-50/50'
}

/** 分数段分析面板（2026-10-18）：官方《分数段分析》表同构的本班口径。
 *  各教学班 + 当前范围合并列；本段/累计/累计比例 + 人数/最值/均分/σ；
 *  全零尾段折叠为一行「(*, 下界)」；缺考列单独计数不入段。 */
function ScoreBandsPanel({
  bands,
  detail,
}: {
  bands: TeachingScoreBandsResponse
  detail: boolean
}) {
  const [hoverBand, setHoverBand] = useState<number | null>(null)
  const showMerged = bands.classes.length > 1
  const cols = showMerged ? [...bands.classes, bands.merged] : bands.classes
  const bandCount = bands.merged.bands.length
  let lastNonZero = -1
  for (const c of cols) {
    c.bands.forEach((b, i) => {
      if (b.count > 0 && i > lastNonZero) lastNonZero = i
    })
  }
  const visibleIdx: number[] = []
  for (let i = 0; i <= lastNonZero; i++) visibleIdx.push(i)
  const collapsedLow =
    lastNonZero >= 0 && lastNonZero + 1 < bandCount
      ? bands.merged.bands[lastNonZero].low
      : null
  const colKey = (c: (typeof cols)[number]) => String(c.teaching_class_id ?? 'merged')
  const colLabel = (c: (typeof cols)[number]) =>
    c.teaching_class_id == null ? `我的 ${String(bands.classes.length)} 个班` : c.class_label

  return (
    <div className="space-y-4">
      {/* 段位分布堆叠条：宽度=本段占有效人数比例；白色细分隔线分段；悬浮显示段名/人数/占比，
          且同一段在全部班级列联动高亮（其余降暗）。配色维持 Viridis（2026-10-21 用户拍板：
          名次段卡的 Inferno 不进教学卡，仅共享分隔线与联动交互） */}
      <TooltipProvider delayDuration={150}>
        <div className="space-y-2" onMouseLeave={() => setHoverBand(null)}>
          {cols.map((c) => (
            <div key={colKey(c)} className="flex items-center gap-3">
              <span
                className="w-32 shrink-0 truncate text-xs text-slate-500"
                title={`${colLabel(c)}（${String(c.valid_count)} 人）`}
              >
                {colLabel(c)} <span className="text-slate-400">{String(c.valid_count)} 人</span>
              </span>
              <div className="flex h-4 flex-1 overflow-hidden rounded-full bg-slate-100 [&>*+*]:border-l [&>*+*]:border-white/90">
                {c.bands.map((b, i) =>
                  b.count > 0 && c.valid_count > 0 ? (
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
                            width: `${String((b.count / c.valid_count) * 100)}%`,
                            background: bandBarColor(i, c.bands.length),
                          }}
                        />
                      </TooltipTrigger>
                      <TooltipContent sideOffset={6}>
                        <span className="font-medium">{bandLabel(b.low, b.high)}</span>
                        <span className="ml-2 text-slate-500">
                          {String(b.count)} 人 ·{' '}
                          {((b.count / c.valid_count) * 100).toFixed(1)}%
                        </span>
                      </TooltipContent>
                    </Tooltip>
                  ) : null,
                )}
              </div>
              <span className="w-16 shrink-0 text-right text-xs tabular-nums text-slate-500">
                均 {formatScore(c.avg)}
              </span>
            </div>
          ))}
        </div>
      </TooltipProvider>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-slate-500">
        {visibleIdx.map((i) => {
          const b = bands.merged.bands[i]
          return (
            <span key={i} className="inline-flex items-center gap-1">
              <span
                className="inline-block h-2 w-2 rounded-sm"
                style={{ background: bandBarColor(i, bandCount) }}
              />
              {bandLabel(b.low, b.high)}
            </span>
          )
        })}
      </div>

      {visibleIdx.length === 0 ? (
        <p className="py-4 text-sm text-slate-500">本场考试当前范围没有可计入分数段的有效成绩。</p>
      ) : (
        <div className="max-h-[60vh] overflow-x-auto">
          {/* 2026-10-22 尺寸改造：限高滚动 + 两级粘性表头 + 冻结「分数段」首列；
              内层 Table 包装改 overflow-visible（不产生滚动容器），sticky 绑定到本外层容器 */}
          <Table wrapperClassName="overflow-visible" className="min-w-max [&_th]:h-8 [&_th]:px-3 [&_td]:px-3 [&_td]:py-1.5">
            <TableHeader>
              <TableRow>
                <TableHead rowSpan={2} className="sticky left-0 top-0 z-30 bg-slate-100 text-xs">
                  分数段
                </TableHead>
                {cols.map((c) => (
                  <TableHead
                    key={colKey(c)}
                    colSpan={detail ? 3 : 1}
                    className="sticky top-0 z-20 bg-slate-100 whitespace-nowrap text-center text-xs"
                  >
                    {colLabel(c)}
                    <span className="ml-1 font-normal text-slate-400">{String(c.valid_count)} 人</span>
                  </TableHead>
                ))}
              </TableRow>
              {detail ? (
                <TableRow>
                  {cols.map((c) => (
                    <Fragment key={colKey(c)}>
                      <TableHead className="sticky top-8 z-20 bg-slate-50 text-right text-xs">本段</TableHead>
                      <TableHead className="sticky top-8 z-20 bg-slate-50 text-right text-xs">累计</TableHead>
                      <TableHead className="sticky top-8 z-20 bg-slate-50 text-right text-xs">比例%</TableHead>
                    </Fragment>
                  ))}
                </TableRow>
              ) : (
                <TableRow>
                  {cols.map((c) => (
                    <TableHead key={colKey(c)} className="sticky top-8 z-20 bg-slate-50 text-right text-xs">
                      人数
                    </TableHead>
                  ))}
                </TableRow>
              )}
            </TableHeader>
            <TableBody>
              {visibleIdx.map((i) => {
                const proto = bands.merged.bands[i]
                return (
                  <TableRow key={i}>
                    <TableCell className="sticky left-0 z-10 bg-white whitespace-nowrap text-xs font-medium text-slate-600">
                      {bandLabel(proto.low, proto.high)}
                    </TableCell>
                    {cols.map((c) => {
                      const b = c.bands[i]
                      return detail ? (
                        <Fragment key={colKey(c)}>
                          <TableCell
                            className={cn(
                              'text-right tabular-nums text-xs',
                              b.count > 0 ? 'text-slate-700' : 'text-slate-300',
                              bandCellTone(b.count, c.valid_count),
                            )}
                          >
                            {String(b.count)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums text-xs text-slate-500">
                            {String(b.cumulative)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums text-xs text-slate-400">
                            {b.cumulative_pct == null ? '—' : b.cumulative_pct.toFixed(1)}
                          </TableCell>
                        </Fragment>
                      ) : (
                        <TableCell
                          key={colKey(c)}
                          className={cn(
                            'text-right tabular-nums text-xs',
                            b.count > 0 ? 'text-slate-700' : 'text-slate-300',
                            bandCellTone(b.count, c.valid_count),
                          )}
                        >
                          {String(b.count)}
                        </TableCell>
                      )
                    })}
                  </TableRow>
                )
              })}
              {collapsedLow != null && (
                <TableRow>
                  <TableCell className="sticky left-0 z-10 bg-white whitespace-nowrap text-xs text-slate-400">
                    (*, {String(collapsedLow)})
                  </TableCell>
                  {cols.map((c) => (
                    <TableCell
                      key={colKey(c)}
                      colSpan={detail ? 3 : 1}
                      className="text-right tabular-nums text-xs text-slate-300"
                    >
                      0
                    </TableCell>
                  ))}
                </TableRow>
              )}
              {(
                [
                  ['有效人数', (c: (typeof cols)[number]) => String(c.valid_count)],
                  ['最高分', (c: (typeof cols)[number]) => formatScore(c.max)],
                  ['最低分', (c: (typeof cols)[number]) => formatScore(c.min)],
                  ['平均分', (c: (typeof cols)[number]) => formatScore(c.avg)],
                  ['标准差 σ', (c: (typeof cols)[number]) => formatScore(c.stddev)],
                ] as const
              ).map(([label, value], rowIdx) => (
                <TableRow
                  key={label}
                  className={rowIdx === 0 ? 'border-t-2 border-slate-200' : undefined}
                >
                  <TableCell className={cn('sticky left-0 z-10 bg-white whitespace-nowrap text-xs text-slate-500', rowIdx === 0 && 'border-t-2 border-slate-200')}>{label}</TableCell>
                  {cols.map((c) => (
                    <TableCell
                      key={colKey(c)}
                      colSpan={detail ? 3 : 1}
                      className="text-right tabular-nums text-xs font-medium text-slate-900"
                    >
                      {value(c)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <p className="text-[10px] leading-relaxed text-slate-400">
        段口左闭右开（90 入 [90,+∞)、89.5 入 [80,90)），累计自高分段向下；缺考（原始分为空）不计入任何段与计量；
        标准差为总体标准差（σ）；等级分（A+~E）分布见「班级对比」页官方质量卡。本卡仅本班口径，不提供年级/官方列。
        段位配色采用 Viridis 色带（matplotlib 默认色图，色盲安全）。
      </p>
    </div>
  )
}

export function TeachingScores() {
  const { filter, generation, switching, scope } = useWorkspace()
  const searchParams = useSearchParams()
  const requestedExam = searchParams.get('exam')

  // 考试清单
  const [exams, setExams] = useState<ExamSummary[] | null>(null)
  const [examsError, setExamsError] = useState<string | null>(null)
  const [examNonce, setExamNonce] = useState(0)
  const [selectedExam, setSelectedExam] = useState<string | null>(null)

  // 当前考试的单科统计 / 学生明细
  const [stats, setStats] = useState<TeachingStatsResponse | null>(null)
  const [students, setStudents] = useState<TeachingStudentRow[] | null>(null)
  const [examGrade, setExamGrade] = useState<number | null>(null)
  const [dataError, setDataError] = useState<string | null>(null)

  // 分数段分析卡：独立请求序号（F11 同模式），段宽切换只重拉本卡
  const [bandWidth, setBandWidth] = useState(10)
  const [bandDetail, setBandDetail] = useState(true)
  const [bands, setBands] = useState<TeachingScoreBandsResponse | null>(null)
  const [bandsError, setBandsError] = useState<string | null>(null)
  const [bandsNonce, setBandsNonce] = useState(0)
  const bandsReqRef = useRef(0)

  // 请求序号按资源分离（F11）：考试清单与 stats+students各自比对各自序号，
  // 任一资源重拉绝不使另一资源的合法回包失效（否则切考试后骨架永久加载）
  const examsReqRef = useRef(0)
  const dataReqRef = useRef(0)

  const scopeQ = useMemo(() => analysisScopeQuery(filter), [filter])

  // 相关性卡走作业域作用域（homeworkScopeQuery，不带 term_id），考试跟随页面顶部选择
  const hwScopeQ = useMemo(() => homeworkScopeQuery(filter), [filter])
  const scopeSubject = scope?.subject ?? null

  // 考试清单：跟随工作台筛选/世代变化整体重置（仅考试列表自身状态；
  // stats/students由数据 effect 的 scopeQ/考试依赖负责重置）
  useEffect(() => {
    const req = ++examsReqRef.current
    setExams(null)
    setExamsError(null)
    setSelectedExam(null)
    listExams('teaching', scopeQ)
      .then((r) => {
        if (req !== examsReqRef.current) return
        const sorted = sortExamsDesc(r.exams ?? [])
        setExams(sorted)
        if (sorted.length > 0) {
          setSelectedExam((sorted.find((exam) => (exam.exam_key ?? exam.exam_name) === requestedExam || exam.exam_name === requestedExam)?.exam_key ?? sorted.find((exam) => exam.exam_name === requestedExam)?.exam_name ?? sorted[0].exam_key ?? sorted[0].exam_name))
        }
      })
      .catch((err: unknown) => {
        if (req !== examsReqRef.current) return
        setExams([])
        setExamsError(apiErrorMessage(err))
      })
  }, [scopeQ, generation, examNonce, requestedExam])

  // 当前考试的 stats + 学生表（并行；任一失败进统一错误态；只比对 dataReqRef）
  useEffect(() => {
    const req = ++dataReqRef.current
    if (selectedExam == null) return
    setStats(null)
    setStudents(null)
    setExamGrade(null)
    setDataError(null)
    Promise.all([
      fetchTeachingStats(selectedExam, scopeQ),
      fetchTeachingStudents(selectedExam, scopeQ),
    ])
      .then(([st, sRows]) => {
        if (req !== dataReqRef.current) return
        setStats(st)
        setStudents(sRows.students ?? [])
        setExamGrade(sRows.grade ?? null)
      })
      .catch((err: unknown) => {
        if (req !== dataReqRef.current) return
        setStats(null)
        setStudents(null)
        setExamGrade(null)
        setDataError(apiErrorMessage(err))
      })
    return () => { dataReqRef.current++ }
  }, [selectedExam, scopeQ, generation])

  // 分数段分析（同考试/同范围；段宽变化仅重拉本卡，不影响 stats/students）
  useEffect(() => {
    const req = ++bandsReqRef.current
    if (selectedExam == null) return
    setBands(null)
    setBandsError(null)
    fetchTeachingScoreBands(selectedExam, scopeQ, bandWidth)
      .then((r) => {
        if (req !== bandsReqRef.current) return
        setBands(r)
      })
      .catch((err: unknown) => {
        if (req !== bandsReqRef.current) return
        setBands(null)
        setBandsError(apiErrorMessage(err))
      })
    return () => { bandsReqRef.current++ }
  }, [selectedExam, scopeQ, bandWidth, bandsNonce, generation])

  const loadingExams = exams == null && examsError == null
  const hasExamData = selectedExam != null

  const rankRangeLabel =
    stats == null
      ? '…'
      : stats.rank_min == null || stats.rank_max == null
        ? '—'
        : stats.rank_min === stats.rank_max
          ? `第 ${String(stats.rank_min)} 名`
          : `第 ${String(stats.rank_min)} ~ ${String(stats.rank_max)} 名`

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">成绩分析</h1>
          <p className="mt-1 text-sm text-slate-500">
            任教学科成绩、分数段与当前范围名次{switching ? ' · 正在切换…' : ''}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* 分班查看：选中即切换全页统计/成绩表/相关性的教学班范围 */}
          <ClassScopePicker />
          <Button
            variant="outline"
            size="sm"
            onClick={() => setExamNonce((n) => n + 1)}
            disabled={loadingExams}
          >
            <BarChart3 className="h-4 w-4" />
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
            显示所选学年教学班已导入的考试（日期降序）；缺考显示「—」，不计入均分与名次。
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
            <BarChart3 className="h-8 w-8 text-slate-300" />
            <p className="text-sm text-slate-600">当前范围暂无已导入考试</p>
            <p className="text-xs text-slate-400">
              可切换上方学年查看历史考试，或到「数据上传」导入任教学科成绩表；其他学科数据不会进入教学工作台。
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
              {/* 单科 stats 卡 */}
              <Card>
                <CardContent className="py-5">
                  {stats == null ? (
                    <Skeleton className="h-16 w-full" />
                  ) : (
                    <>
                      <div className="flex flex-wrap items-center gap-2 text-sm text-slate-500">
                        <BookOpen className="h-4 w-4" />
                        <span className="font-medium text-slate-700">{stats.subject}</span>
                        <Badge variant="outline">{scoreBasisLabel(stats.score_basis)}</Badge>
                        {(stats.small_sample === true || stats.valid_count < 5) && (
                          <Badge variant="warning">样本&lt;5</Badge>
                        )}
                      </div>
                      <div className="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
                        <div>
                          <p className="text-xs text-slate-400">平均分</p>
                          <p className="num-display text-xl font-semibold text-slate-900">
                            {formatScore(stats.avg)}
                          </p>
                        </div>
                        <div>
                          <p className="text-xs text-slate-400">最高</p>
                          <p className="num-display text-xl font-semibold text-slate-900">
                            {formatScore(stats.max)}
                          </p>
                        </div>
                        <div>
                          <p className="text-xs text-slate-400">最低</p>
                          <p className="num-display text-xl font-semibold text-slate-900">
                            {formatScore(stats.min)}
                          </p>
                        </div>
                        <div>
                          <p className="text-xs text-slate-400">有效 / 缺考</p>
                          <p className="num-display text-xl font-semibold text-slate-900">
                            {String(stats.valid_count)} / {String(stats.missing_count)}
                          </p>
                        </div>
                        <div>
                          <p className="text-xs text-slate-400">名次范围（当前范围）</p>
                          <p className="text-xl font-semibold text-slate-900">{rankRangeLabel}</p>
                        </div>
                        <div>
                          <p className="text-xs text-slate-400">范围人数</p>
                          <p className="num-display text-xl font-semibold text-slate-900">
                            {String(stats.cohort_size)}
                          </p>
                        </div>
                      </div>
                      <p className="mt-2 text-[10px] text-slate-400">
                        同分同名次（min-rank）；缺考不计入均分与名次分母；名次在当前范围成员内计算，学生明细合并展示，名次为当前所选班级范围内名次。
                      </p>
                    </>
                  )}
                </CardContent>
              </Card>

              {/* 分数段分析卡（官方《分数段分析》表同构的本班口径，2026-10-18） */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex flex-wrap items-center gap-2">
                    分数段分析（原始分 · {String(bandWidth)} 分一段）
                    {bands?.small_sample === true && <Badge variant="warning">样本&lt;5</Badge>}
                  </CardTitle>
                  <CardDescription>
                    各教学班与当前范围合并列的「本段 / 累计 / 累计比例」，底部为人数、最值、均分与标准差；
                    数据由本域学生明细实时计算，缺考不计入任何段与计量。
                  </CardDescription>
                  <div className="flex flex-wrap items-center gap-2 pt-1 print:hidden">
                    <label className="flex items-center gap-1.5 text-xs text-slate-500">
                      段宽
                      <select
                        aria-label="分数段宽"
                        value={String(bandWidth)}
                        onChange={(e) => setBandWidth(Number(e.target.value))}
                        className="rounded-md border border-slate-200 bg-white py-1.5 pl-2 text-xs"
                      >
                        <option value="10">10 分</option>
                        <option value="5">5 分</option>
                        <option value="20">20 分</option>
                      </select>
                    </label>
                    <Button
                      size="sm"
                      variant={bandDetail ? 'default' : 'outline'}
                      onClick={() => setBandDetail(true)}
                    >
                      本段 / 累计 / 比例
                    </Button>
                    <Button
                      size="sm"
                      variant={bandDetail ? 'outline' : 'default'}
                      onClick={() => setBandDetail(false)}
                    >
                      仅人数
                    </Button>
                  </div>
                </CardHeader>
                <CardContent>
                  {bandsError != null ? (
                    <div className="flex flex-col items-center gap-3 py-6 text-center">
                      <AlertCircle className="h-6 w-6 text-amber-400" />
                      <p className="text-sm text-slate-600">{bandsError}</p>
                      <Button variant="outline" size="sm" onClick={() => setBandsNonce((n) => n + 1)}>
                        重试
                      </Button>
                    </div>
                  ) : bands == null ? (
                    <div className="space-y-2">
                      <Skeleton className="h-4 w-full" />
                      <Skeleton className="h-28 w-full" />
                      <Skeleton className="h-4 w-2/3" />
                    </div>
                  ) : (
                    <ScoreBandsPanel bands={bands} detail={bandDetail} />
                  )}
                </CardContent>
              </Card>

              {/* 学生明细：当前范围合并展示，排名直接使用后端结果 */}
              <Card>
                <CardHeader>
                  <CardTitle>学生成绩明细</CardTitle>
                  <CardDescription>
                    仅本人管理班成员；名次按当前所选班级范围内原始分计算，同分同名次。学籍排名与年级百分比来自导入文件。
                    点击姓名查看个人档案与历次成绩趋势。
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
                        可能名册为空或尚未导入该场成绩；空范围不会扩展到其他班级。
                      </p>
                    </div>
                  ) : (
                    /* 2026-10-22 尺寸改造：限高纵向滚动（表头粘性在 TeachingStudentDetails
                       内部实现，受文件所有权约束本轮仅加容器限高） */
                    <div className="max-h-[70vh] overflow-x-auto">
                      <TeachingStudentDetails rows={students} grade={examGrade} />
                    </div>
                  )}
                </CardContent>
              </Card>


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
      <CorrelationCard mode="teaching" scopeQ={hwScopeQ} scopeSubject={scopeSubject} generation={generation} preferredExamName={selectedExam} />
    </div>
  )
}
