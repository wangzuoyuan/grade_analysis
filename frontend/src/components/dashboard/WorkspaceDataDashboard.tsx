'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  ArrowRight,
  BookOpenCheck,
  CalendarDays,
  CheckCircle2,
  ClipboardCheck,
  FileSpreadsheet,
  RefreshCw,
  TrendingUp,
  UserRoundSearch,
  Users,
} from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import {
  fetchHomeroomStats,
  fetchHomeroomFocus,
  fetchTeachingClassCompare,
  fetchTeachingStats,
  fetchTeachingStudents,
  homeworkDashboard,
  homeworkCurrentSemester,
  homeworkWarnings,
  listExams,
  type ExamSummary,
  type HomeroomStatsResponse,
  type TeachingClassCompareItem,
  type TeachingStatsResponse,
  type TeachingStudentsResponse,
  type WorkspaceMode,
  type CurrentSemester,
} from '@/lib/api-v1'
import { useWorkspace } from '@/lib/workspace'
import { analysisScopeQuery, formatScore, sortExamsDesc } from '@/components/scores/shared'
import { homeworkScopeQuery } from '@/components/homework/shared'
import { ClassScopePicker } from '@/components/ClassScopePicker'
import WeeklyFocusCard from '@/components/WeeklyFocusCard'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

interface TrendDatum {
  exam: string
  date: string
  value: number | null
}

function ClickableTrendDot({
  cx,
  cy,
  payload,
  onOpen,
}: {
  cx?: number
  cy?: number
  payload?: TrendDatum
  onOpen: (exam: string) => void
}) {
  if (cx == null || cy == null || payload?.value == null) return <g />
  return (
    <circle
      cx={cx}
      cy={cy}
      r={5}
      fill="#fff"
      stroke="#1f7fd6"
      strokeWidth={2}
      className="cursor-pointer"
      role="button"
      aria-label={`查看${payload.exam}`}
      onClick={() => onOpen(payload.exam)}
    />
  )
}

interface FocusStudent {
  id: string
  name: string
  value: string
  reason: string
}

interface DashboardData {
  exams: ExamSummary[]
  latestHomeroom: HomeroomStatsResponse | null
  latestTeaching: TeachingStatsResponse | null
  trend: TrendDatum[]
  focus: FocusStudent[]
  compare: TeachingClassCompareItem[]
  homework: Array<{ label: string; assignments: number }>
  warnings: Array<{
    id: string
    name: string
    kind: 'missing' | 'quality' | 'forgot'
    summary: string
    missing: number
    streak: number | null
    streakBasis: string
    streakLabel: string | null
  }>
  warningTotal: number
}

const EMPTY_DATA: DashboardData = {
  exams: [],
  latestHomeroom: null,
  latestTeaching: null,
  trend: [],
  focus: [],
  compare: [],
  homework: [],
  warnings: [],
  warningTotal: 0,
}

function shortDate(date: string | null): string {
  return date ? date.slice(5, 10) : '日期未知'
}

function average(values: Array<number | null | undefined>): number | null {
  const valid = values.filter((value): value is number => typeof value === 'number')
  return valid.length ? valid.reduce((sum, value) => sum + value, 0) / valid.length : null
}

function examIsInSemester(examDate: string | null, semester: CurrentSemester): boolean {
  if (!examDate) return false
  const start = examDate.length === 7 ? `${examDate}-01` : examDate.slice(0, 10)
  const end = examDate.length === 7 ? `${examDate}-31` : examDate.slice(0, 10)
  return end >= semester.start_date && start <= semester.end_date
}

function MetricCard({
  label,
  value,
  note,
  icon: Icon,
  tone = 'blue',
  href,
}: {
  label: string
  value: string
  note: string
  icon: typeof Users
  tone?: 'blue' | 'cyan' | 'amber' | 'green'
  href?: string
}) {
  const tones = {
    blue: 'from-[#1f7fd6] to-[#155ca8]',
    cyan: 'from-[#22b7d9] to-[#1687bb]',
    amber: 'from-[#f0a83a] to-[#d7791f]',
    green: 'from-[#39a97b] to-[#21845f]',
  }
  const cardContent = (
    <Card className={`relative overflow-hidden ${href ? 'transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md cursor-pointer border-slate-300/80 group' : ''}`}>
      <div className={`absolute inset-x-0 top-0 h-1 bg-gradient-to-r ${tones[tone]}`} />
      <CardContent className="py-5">
        <div className="flex items-center justify-between gap-3">
          <div className="text-sm font-medium text-slate-500 group-hover:text-slate-700">{label}</div>
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-slate-50 text-brand-600 group-hover:bg-brand-50">
            <Icon className="h-4 w-4" />
          </span>
        </div>
        <div className="mt-2 text-3xl font-semibold tracking-tight text-slate-900 tabular-nums">{value}</div>
        <p className="mt-1 text-xs text-slate-400">{note}</p>
      </CardContent>
    </Card>
  )
  if (href) {
    return (
      <Link href={href} className="block focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded-xl">
        {cardContent}
      </Link>
    )
  }
  return cardContent
}

export function WorkspaceDataDashboard({ mode }: { mode: WorkspaceMode }) {
  const router = useRouter()
  const { filter, setFilter, scope, scopeError, scopeLoading, generation, switching } = useWorkspace()
  const [data, setData] = useState<DashboardData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const [currentSemester, setCurrentSemester] = useState<CurrentSemester | null>(null)
  const requestRef = useRef(0)

  useEffect(() => {
    homeworkCurrentSemester()
      .then((semester) => {
        setCurrentSemester(semester)
        if (filter.academic_year_id !== semester.academic_year_id) {
          setFilter({
            academic_year_id: semester.academic_year_id,
            term_id: undefined,
            ...(mode === 'teaching'
              ? { teaching_class_id: 'all' as const }
              : { class_id: undefined }),
          })
        }
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : '当前学期加载失败')
      })
  }, [mode, nonce, filter.academic_year_id, setFilter])

  const analysisQ = useMemo(() => {
    const q = analysisScopeQuery(filter)
    if (!currentSemester) return q
    const sameYear = filter.academic_year_id === currentSemester.academic_year_id
    return {
      ...q,
      academic_year_id: currentSemester.academic_year_id,
      term_id: undefined,
      class_id: sameYear ? q.class_id : undefined,
      teaching_class_id: sameYear ? q.teaching_class_id : undefined,
    }
  }, [filter, currentSemester])
  const homeworkQ = useMemo(() => {
    const q = homeworkScopeQuery(filter)
    if (!currentSemester) return q
    const sameYear = filter.academic_year_id === currentSemester.academic_year_id
    return {
      ...q,
      academic_year_id: currentSemester.academic_year_id,
      class_id: sameYear ? q.class_id : undefined,
      teaching_class_id: sameYear ? q.teaching_class_id : undefined,
    }
  }, [filter, currentSemester])

  useEffect(() => {
    const request = ++requestRef.current
    if (!currentSemester) return
    const semester = currentSemester
    setData(null)
    setError(null)

    async function load() {
      // 成绩与作业是两个独立证据源：当前学年没有考试时也必须加载作业，
      // 不能让考试空态吞掉已经迁移的缺交历史。
      const [examResult, hw, warning] = await Promise.all([
        listExams(mode, analysisQ),
        homeworkDashboard(mode, homeworkQ, 'month').catch(() => ({ groups: [] })),
        homeworkWarnings(mode, { ...homeworkQ, min_missing: 1, min_streak: 2 }).catch(() => ({ students: [], quality: [], forgot: [] })),
      ])
      if (request !== requestRef.current) return
      const exams = sortExamsDesc(examResult.exams ?? []).filter((exam) => examIsInSemester(exam.exam_date, semester))
      const homeworkData = (hw.groups ?? []).map((group) => ({
        label: group.label.slice(0, 7),
        assignments: group.assignments,
      }))
      const warningData = (warning.students ?? []).slice(0, 6).map((student) => ({
        id: String(student.person_id),
        name: student.name ?? '未命名学生',
        kind: 'missing' as const,
        summary: `累计缺交 ${String(student.missing_count)} 次 · 连续 ${String(student.current_streak ?? 0)} 次`,
        missing: student.missing_count,
        streak: student.current_streak,
        streakBasis: student.streak_basis,
        streakLabel: student.streak_subject ?? student.streak_homework_type ?? null,
      }))
      const qualityWarnings = (warning.quality ?? []).map((student) => ({
        id: `quality:${String(student.person_id)}`,
        name: student.name ?? '未命名学生',
        kind: 'quality' as const,
        summary: `连续负面评价 ${String(student.count)} 次`,
        missing: 0, streak: null, streakBasis: 'quality', streakLabel: null,
      }))
      const forgotWarnings = (warning.forgot ?? []).map((student) => ({
        id: `forgot:${String(student.person_id)}`,
        name: student.name ?? '未命名学生',
        kind: 'forgot' as const,
        summary: `忘带 ${String(student.count)} 次`,
        missing: 0, streak: null, streakBasis: 'forgot', streakLabel: null,
      }))
      const allWarnings = [...warningData, ...qualityWarnings, ...forgotWarnings]

      if (exams.length === 0) {
        setData({
          ...EMPTY_DATA,
          exams: [],
          homework: homeworkData,
          warnings: allWarnings,
          warningTotal: allWarnings.length,
        })
        return
      }

      const latest = exams[0]
      const recent = exams.slice(0, 7).reverse()
      const [latestStats, latestStudents, latestFocus, compare, ...historyStats] = await Promise.all([
        mode === 'homeroom'
          ? fetchHomeroomStats(latest.exam_name, analysisQ)
          : fetchTeachingStats(latest.exam_name, analysisQ),
        mode === 'teaching'
          ? fetchTeachingStudents(latest.exam_name, analysisQ)
          : Promise.resolve(null),
        mode === 'homeroom'
          ? fetchHomeroomFocus(latest.exam_name, analysisQ).catch(() => null)
          : Promise.resolve(null),
        mode === 'teaching'
          ? fetchTeachingClassCompare(latest.exam_name, analysisQ).catch(() => ({ classes: [] }))
          : Promise.resolve({ classes: [] }),
        ...recent.map((exam) =>
          mode === 'homeroom'
            ? fetchHomeroomStats(exam.exam_name, analysisQ).catch(() => null)
            : fetchTeachingStats(exam.exam_name, analysisQ).catch(() => null),
        ),
      ])
      if (request !== requestRef.current) return

      let focus: FocusStudent[] = []
      if (mode === 'homeroom') {
        focus = (latestFocus?.students ?? []).slice(0, 8).map((student) => ({
          id: String(student.person_id),
          name: student.name ?? '未命名学生',
          value: student.xueji_rank == null ? '—' : `第 ${String(student.xueji_rank)} 名`,
          reason: student.issues.join('、'),
        }))
      } else {
        focus = (latestStudents as TeachingStudentsResponse).students
          .slice()
          .sort((a, b) => (a.score == null ? -1 : b.score == null ? 1 : a.score - b.score))
          .slice(0, 6)
          .map((student) => ({
            id: String(student.person_id),
            name: student.name ?? '未命名学生',
            value: student.score == null ? '—' : formatScore(student.score),
            reason: student.score == null ? '缺考/无有效成绩' : `班内第 ${String(student.rank ?? '—')} 名`,
          }))
      }

      const trend = recent.map((exam, index) => {
        const stats = historyStats[index]
        let value: number | null = null
        if (stats && 'totals' in stats) value = stats.totals[0]?.avg ?? average(stats.subjects.map((s) => s.avg))
        if (stats && 'subject' in stats) value = stats.avg
        return { exam: exam.exam_name, date: shortDate(exam.exam_date), value }
      })

      setData({
        exams,
        latestHomeroom: mode === 'homeroom' && 'totals' in latestStats ? latestStats : null,
        latestTeaching: mode === 'teaching' && 'subject' in latestStats ? latestStats : null,
        trend,
        focus,
        compare: compare.classes ?? [],
        homework: homeworkData,
        warnings: allWarnings,
        warningTotal: allWarnings.length,
      })
    }

    load().catch((reason: unknown) => {
      if (request !== requestRef.current) return
      setData(null)
      setError(reason instanceof Error ? reason.message : '数据看板加载失败')
    })
    return () => {
      requestRef.current += 1
    }
  }, [mode, analysisQ, homeworkQ, generation, nonce, currentSemester])

  const latest = data?.exams[0] ?? null
  const homeroomTotal = data?.latestHomeroom?.totals[0] ?? null
  const teachingStats = data?.latestTeaching ?? null
  const subjectRows = data?.latestHomeroom?.subjects ?? []
  const loading = data == null && error == null
  const empty = data != null && data.exams.length === 0
  const title = mode === 'homeroom' ? '班主任数据看板' : '教学数据看板'
  const scoreHref = `/${mode}/scores`
  const openExam = (exam: string) => router.push(`${scoreHref}?exam=${encodeURIComponent(exam)}`)
  const comparableTrend = data?.trend.filter((item) => item.value != null) ?? []
  const homeworkAssignmentCount = data?.homework.reduce((sum, item) => sum + item.assignments, 0) ?? 0

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{title}</h1>
          <p className="mt-1 text-sm text-slate-500">
            {mode === 'homeroom'
              ? '从成绩趋势、学科表现与作业预警掌握本班重点'
              : '对比任教班级，跟进物理成绩与作业风险'}
            {switching ? ' · 正在切换…' : ''}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => setNonce((value) => value + 1)} disabled={loading}>
          <RefreshCw className="h-4 w-4" /> 刷新数据
        </Button>
      </div>

      <Card className="print:hidden">
        <CardContent className="flex flex-col gap-3 py-4 sm:flex-row sm:items-end">
          {mode === 'teaching' ? (
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-500">教学班</label>
              <ClassScopePicker />
            </div>
          ) : null}
          <div className="pb-1 text-xs text-slate-500">
            {scopeLoading && !data
              ? '正在解析范围…'
              : `${currentSemester?.academic_year_name ?? '—'} · ${currentSemester?.name ?? '当前学期'} · 当前范围 ${String(scope?.cohort_size ?? data?.latestHomeroom?.cohort_size ?? data?.latestTeaching?.cohort_size ?? '—')} 人`}
          </div>
        </CardContent>
      </Card>

      {scopeError && data == null ? (
        <Card><CardContent className="flex items-center gap-3 py-8 text-sm text-amber-700"><AlertCircle className="h-5 w-5" />{scopeError.detail || '当前工作台范围不可用'}</CardContent></Card>
      ) : error ? (
        <Card><CardContent className="flex items-center justify-between gap-3 py-8 text-sm text-amber-700"><span className="flex items-center gap-2"><AlertCircle className="h-5 w-5" />{error}</span><Button variant="outline" size="sm" onClick={() => setNonce((v) => v + 1)}>重试</Button></CardContent></Card>
      ) : loading ? (
        <div className="grid gap-4 md:grid-cols-4">{Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-32" />)}</div>
      ) : empty ? (
        <>
          <Card>
            <CardContent className="flex flex-col items-center justify-center gap-3 py-8 text-center">
              <CalendarDays className="h-10 w-10 text-slate-300" />
              <div><p className="font-medium text-slate-700">当前学期还没有考试数据</p><p className="mt-1 text-sm text-slate-400">成绩为空不影响下方作业与缺交历史展示。</p></div>
              <div className="flex flex-wrap justify-center gap-2">
                <Button asChild variant="outline"><Link href="/upload">上传成绩</Link></Button>
              </div>
            </CardContent>
          </Card>
          {mode === 'homeroom' ? (
            <WeeklyFocusCard scopeQuery={analysisQ} />
          ) : null}
          <Card>
              <CardHeader className="flex-row items-start justify-between space-y-0"><div><CardTitle>重点关注</CardTitle><CardDescription>缺交、负面评价与忘带汇总，没有考试也会显示</CardDescription></div><Button asChild variant="ghost" size="sm"><Link href={`/${mode}/homework?tab=warnings`}>查看明细 <ArrowRight className="h-4 w-4" /></Link></Button></CardHeader>
            <CardContent>
              {homeworkAssignmentCount > 0 ? <div className="h-32"><ResponsiveContainer width="100%" height="100%"><BarChart data={data.homework}><CartesianGrid strokeDasharray="3 3" stroke="#eef4f8" vertical={false} /><XAxis dataKey="label" tick={{ fontSize: 10, fill: '#7890a8' }} /><YAxis allowDecimals={false} tick={{ fontSize: 10, fill: '#7890a8' }} /><Tooltip /><Bar dataKey="assignments" name="作业批次" fill="#35b9e9" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer></div> : <div className="flex h-20 items-center justify-center text-sm text-slate-400">暂无可统计的作业批次</div>}
              <ul className="mt-3 space-y-2">{data.warnings.slice(0, 6).map((student) => <li key={student.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs"><span className="font-medium text-amber-900">{student.name}</span><span className="text-amber-700">{student.summary}</span></li>)}</ul>
              {data.warnings.length === 0 ? <div className="mt-3 flex items-center gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-700"><CheckCircle2 className="h-4 w-4" />当前筛选下暂无重点作业预警</div> : null}
            </CardContent>
          </Card>
        </>
      ) : data ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard label="最近考试" value={latest?.exam_name ?? '—'} note={latest?.exam_date ? latest.exam_date.slice(0, 10) : '日期未知'} icon={BookOpenCheck} />
            {mode === 'homeroom' ? (
              <MetricCard label={homeroomTotal?.total_type ?? '综合均分'} value={formatScore(homeroomTotal?.avg)} note={`有效 ${String(homeroomTotal?.valid_count ?? 0)} 人`} icon={TrendingUp} tone="cyan" />
            ) : (
              <MetricCard label={`${teachingStats?.subject ?? '任教学科'}均分`} value={formatScore(teachingStats?.avg)} note={`${teachingStats?.score_basis === 'grade' ? '等级分' : '原始分'}口径`} icon={TrendingUp} tone="cyan" />
            )}
            {mode === 'homeroom' ? (
              <MetricCard
                label="年级名次范围"
                value={
                  homeroomTotal?.rank_min != null && homeroomTotal?.rank_max != null
                    ? `${homeroomTotal.rank_min}–${homeroomTotal.rank_max}`
                    : '—'
                }
                note="最近一次考试"
                icon={FileSpreadsheet}
                tone="green"
              />
            ) : (
              <MetricCard
                label="有效 / 缺考"
                value={`${String(teachingStats?.valid_count ?? 0)} / ${String(teachingStats?.missing_count ?? 0)}`}
                note="按当前学年与班级范围"
                icon={Users}
                tone="green"
              />
            )}
            <MetricCard
              label="作业预警"
              value={`${String(data.warningTotal)} 项`}
              note="缺交、负面评价和忘带独立统计"
              icon={ClipboardCheck}
              tone={data.warningTotal ? 'amber' : 'green'}
              href={`/${mode}/homework?tab=warnings`}
            />
          </div>

          {mode === 'homeroom' ? (
            <WeeklyFocusCard scopeQuery={analysisQ} />
          ) : null}

          <div className="grid gap-4 xl:grid-cols-[1.45fr_1fr]">
            <Card>
              <CardHeader><CardTitle>成绩趋势</CardTitle><CardDescription>{mode === 'homeroom' ? '各次考试综合均分' : `各次${teachingStats?.subject ?? '任教学科'}平均分`}，点击数据点查看该次考试</CardDescription></CardHeader>
              <CardContent className="h-64">
                {comparableTrend.length >= 2 ? <ResponsiveContainer width="100%" height="100%"><LineChart data={data.trend} margin={{ left: -12, right: 12, top: 8, bottom: 4 }} onClick={(state) => { const exam = state?.activePayload?.[0]?.payload?.exam; if (exam) openExam(String(exam)) }}><CartesianGrid strokeDasharray="3 3" stroke="#e8f1f8" /><XAxis dataKey="date" tick={{ fontSize: 11, fill: '#7890a8' }} /><YAxis tick={{ fontSize: 11, fill: '#7890a8' }} domain={['auto', 'auto']} /><Tooltip formatter={(value) => [`${String(value)} 分`, '均分']} labelFormatter={(_, payload) => payload?.[0]?.payload?.exam ?? ''} /><Line type="monotone" dataKey="value" stroke="#1f7fd6" strokeWidth={3} dot={(props) => <ClickableTrendDot {...props} onOpen={openExam} />} activeDot={{ r: 7, cursor: 'pointer' }} connectNulls={false} /></LineChart></ResponsiveContainer> : <div className="flex h-full flex-col items-center justify-center text-center"><TrendingUp className="h-8 w-8 text-slate-300" /><p className="mt-3 text-sm font-medium text-slate-600">{comparableTrend.length === 1 ? '仅 1 场考试有可比均分' : '暂无可比均分'}</p><p className="mt-1 text-xs text-slate-400">{comparableTrend.length === 1 ? `${comparableTrend[0].exam}：${formatScore(comparableTrend[0].value)} 分，尚不足以形成趋势` : '缺失值保持为空，不按 0 分绘制'}</p></div>}
              </CardContent>
            </Card>

            <Card>
              <CardHeader><CardTitle>{mode === 'homeroom' ? '最近一次学科表现' : '教学班对比'}</CardTitle><CardDescription>{mode === 'homeroom' ? '学科均分横向对照' : '班级样本均分，不冒充官方年级排名'}</CardDescription></CardHeader>
              <CardContent className="space-y-3">
                {(mode === 'homeroom' ? subjectRows.map((row) => ({ label: row.subject, value: row.avg, count: row.valid_count })) : data.compare.map((row) => ({ label: row.class_label, value: row.subject_avg, count: row.member_count }))).map((row) => (
                  <div key={row.label} className="grid grid-cols-[5rem_1fr_3rem] items-center gap-2 text-xs"><span className="truncate text-slate-600" title={row.label}>{row.label}</span><div className="h-2.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-gradient-to-r from-[#35b9e9] to-[#1f7fd6]" style={{ width: `${String(Math.max(0, Math.min(100, row.value ?? 0)))}%` }} /></div><span className="text-right tabular-nums text-slate-600">{formatScore(row.value)}</span></div>
                ))}
                {(mode === 'homeroom' ? subjectRows : data.compare).length === 0 ? <p className="py-8 text-center text-sm text-slate-400">暂无可对比数据</p> : null}
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <Card>
              <CardHeader className="flex-row items-start justify-between space-y-0"><div><CardTitle>需关注学生</CardTitle><CardDescription>{mode === 'homeroom' ? '进退步、波动、名次段、偏科与稳定优秀综合标注' : '根据最近考试排序，供教师进一步核查'}</CardDescription></div><Button asChild variant="ghost" size="sm"><Link href={scoreHref}>查看成绩 <ArrowRight className="h-4 w-4" /></Link></Button></CardHeader>
              <CardContent><ul className="divide-y divide-slate-100">{data.focus.map((student) => <li key={student.id} className="flex items-center gap-3 py-2.5"><span className="grid h-8 w-8 place-items-center rounded-full bg-slate-50 text-brand-600"><UserRoundSearch className="h-4 w-4" /></span><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium text-slate-700">{student.name}</p><p className="text-xs text-slate-400">{student.reason}</p></div><span className="text-sm font-semibold tabular-nums text-slate-700">{student.value}</span></li>)}</ul>{data.focus.length === 0 ? <p className="py-8 text-center text-sm text-slate-400">暂无可分析的学生成绩</p> : null}</CardContent>
            </Card>

            <Card>
              <CardHeader className="flex-row items-start justify-between space-y-0"><div><CardTitle>重点关注</CardTitle><CardDescription>缺交、负面评价与忘带汇总，没有考试也会显示</CardDescription></div><Button asChild variant="ghost" size="sm"><Link href={`/${mode}/homework?tab=warnings`}>查看明细 <ArrowRight className="h-4 w-4" /></Link></Button></CardHeader>
              <CardContent>
                {homeworkAssignmentCount > 0 ? <div className="h-32"><ResponsiveContainer width="100%" height="100%"><BarChart data={data.homework}><CartesianGrid strokeDasharray="3 3" stroke="#eef4f8" vertical={false} /><XAxis dataKey="label" tick={{ fontSize: 10, fill: '#7890a8' }} /><YAxis allowDecimals={false} tick={{ fontSize: 10, fill: '#7890a8' }} /><Tooltip /><Bar dataKey="assignments" name="作业批次" fill="#35b9e9" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer></div> : <div className="flex h-24 flex-col items-center justify-center text-center"><p className="text-sm text-slate-500">暂无可统计的作业批次</p><p className="mt-1 text-xs text-slate-400">可从作业看板开始录入。</p></div>}
                <ul className="mt-3 space-y-2">{data.warnings.slice(0, 3).map((student) => <li key={student.id} className="flex items-center justify-between rounded-lg bg-amber-50 px-3 py-2 text-xs"><span className="font-medium text-amber-900">{student.name}</span><span className="text-amber-700">{student.summary}</span></li>)}</ul>
                {data.warnings.length === 0 ? <div className="mt-3 flex items-center gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-700"><CheckCircle2 className="h-4 w-4" />当前筛选下暂无重点作业预警</div> : null}
              </CardContent>
            </Card>
          </div>
        </>
      ) : null}
    </div>
  )
}
