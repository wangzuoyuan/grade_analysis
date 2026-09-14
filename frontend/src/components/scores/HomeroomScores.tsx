'use client'

/**
 * 班主任工作台成绩分析页（契约 p3-imports-analysis.md §2.1/§3）。
 *
 * 数据全部来自 /api/v1：shared/exams（考试清单）、homeroom/analysis/*（stats/students/bands/trends）。
 * 计量红线（契约 §2.3）：缺考（score NULL）一律显示「—」不转 0；名次不在本页展示，
 * 表格按总分降序仅为浏览排序；shared_conflict 格子警示并注明教学域分数待人工核对；
 * 趋势按学年分段展示，不跨年连线；迟到回包按资源分离的序号丢弃（F11：任一资源重拉
 * 绝不使其他资源的合法回包失效），bands 无名次口径的 409 呈现「不可计算」提示卡（F10）。
 */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle, ChevronDown, ClipboardList, ListChecks, TrendingUp, Users } from 'lucide-react'

import {
  ApiV1Error,
  confirmCanonicalScores,
  fetchHomeroomBands,
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { ExamSelect } from './ExamSelect'
import { ScoreYearPicker } from './ScoreYearPicker'
import { analysisScopeQuery, formatScore, scoreBasisLabel, sortExamsDesc } from './shared'

/** 段位/趋势条目里同科多场的展示文案；缺考显示「—」。 */
function trendPointLabel(pt: { exam_name: string; exam_date: string | null; score: number | null; grade_score?: number | null }): string {
  const date = pt.exam_date ? pt.exam_date.slice(0, 10) : '日期未知'
  const score = pt.score == null ? '—' : formatScore(pt.score)
  const grade = pt.grade_score == null ? '' : `（等级 ${formatScore(pt.grade_score)}）`
  return `${pt.exam_name} ${date}：${score}${grade}`
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
        const lines = Object.entries({ ...year.subjects, ...year.totals })
        return (
          <div key={year.academic_year_id} className="rounded-lg border border-slate-100 bg-white px-3 py-2">
            <p className="text-xs font-semibold text-slate-500">{year.academic_year_name}</p>
            {lines.length === 0 ? (
              <p className="mt-1 text-xs text-slate-400">本学年暂无成绩</p>
            ) : (
              <ul className="mt-1 space-y-1">
                {lines.map(([key, points]) => (
                  <li key={key} className="text-xs text-slate-600">
                    <span className="font-medium text-slate-700">{key}</span>
                    <span className="ml-2 text-slate-500">
                      {points.map((pt) => trendPointLabel(pt)).join(' · ')}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )
      })}
      <p className="text-[10px] text-slate-400">跨学年成绩分段展示，不同学年总分口径不直接比较。</p>
    </div>
  )
}

export function HomeroomScores() {
  const { filter, generation, switching, scope } = useWorkspace()
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
  const [dataError, setDataError] = useState<string | null>(null)

  // 段位分布
  const [bandMetric, setBandMetric] = useState<string>('__total__')
  const [bands, setBands] = useState<HomeroomBandsResponse | null>(null)
  const [bandsError, setBandsError] = useState<string | null>(null)
  // F10/F11：后端 bands 对无名次口径一律 409（确定性不可算），区别于网络/瞬时错误展示
  const [bandsUnavailable, setBandsUnavailable] = useState(false)
  const [bandsNonce, setBandsNonce] = useState(0)

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
  const trendsReqRef = useRef(0)
  const conflictsReqRef = useRef(0)

  // Q10：确认成功后 +1，触发 stats/students 重拉（冲突标记随之消失）
  const [dataNonce, setDataNonce] = useState(0)

  const scopeQ = useMemo(() => analysisScopeQuery(filter), [filter])

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
        if (sorted.length > 0) setSelectedExam(sorted[0].exam_name)
      })
      .catch((err: unknown) => {
        if (req !== examsReqRef.current) return
        setExams([])
        setExamsError(apiErrorMessage(err))
      })
  }, [scopeQ, generation, examNonce])

  // 当前考试的 stats + 学生表（并行拉取，任一失败进统一错误态；只比对 dataReqRef）
  useEffect(() => {
    if (selectedExam == null) return
    const req = ++dataReqRef.current
    setStats(null)
    setStudents(null)
    setDataError(null)
    setExpandedPerson(null)
    setTrends(null)
    setTrendsError(null)
    Promise.all([
      fetchHomeroomStats(selectedExam, scopeQ),
      fetchHomeroomStudents(selectedExam, scopeQ),
    ])
      .then(([s, st]) => {
        if (req !== dataReqRef.current) return
        setStats(s)
        setStudents(st.students ?? [])
      })
      .catch((err: unknown) => {
        if (req !== dataReqRef.current) return
        setStats(null)
        setStudents(null)
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
              {/* stats 卡片组：每科一卡 + 总分卡；样本<5 时角标（契约 §2.3） */}
              {stats == null ? (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 print:grid-cols-4">
                  {Array.from({ length: 4 }).map((_, i) => (
                    <Card key={i}>
                      <CardContent className="py-5">
                        <Skeleton className="h-4 w-16" />
                        <Skeleton className="mt-3 h-8 w-20" />
                      </CardContent>
                    </Card>
                  ))}
                </div>
              ) : (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 print:grid-cols-4">
                  {stats.subjects.map((s) => (
                    <Card key={s.subject}>
                      <CardContent className="py-4">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-sm font-medium text-slate-600">{s.subject}</span>
                          {(s.small_sample === true || s.valid_count < 5) && (
                            <Badge variant="warning">样本&lt;5</Badge>
                          )}
                        </div>
                        <div className="mt-2 num-display text-2xl font-semibold text-slate-900">
                          {formatScore(s.avg)}
                        </div>
                        <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-slate-400">
                          <span>最高 {formatScore(s.max)}</span>
                          <span>最低 {formatScore(s.min)}</span>
                          <span>有效 {s.valid_count} 人</span>
                          <span>缺考 {s.missing_count} 人</span>
                        </div>
                        <p className="mt-1 text-[10px] text-slate-400">
                          均分口径 {scoreBasisLabel(s.score_basis)}；缺考不计入均分
                        </p>
                      </CardContent>
                    </Card>
                  ))}
                  {stats.totals.map((t) => (
                    <Card key={t.total_type} className="border-brand-100">
                      <CardContent className="py-4">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-sm font-medium text-brand-700">
                            总分（{t.total_type}）
                          </span>
                          {(t.small_sample === true || t.valid_count < 5) && (
                            <Badge variant="warning">样本&lt;5</Badge>
                          )}
                        </div>
                        <div className="mt-2 num-display text-2xl font-semibold text-slate-900">
                          {formatScore(t.avg)}
                        </div>
                        <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-slate-400">
                          <span>最高 {formatScore(t.max)}</span>
                          <span>最低 {formatScore(t.min)}</span>
                          <span>有效 {t.valid_count} 人</span>
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              )}

              {/* 学生成绩表：行点击展开跨学年趋势 */}
              <Card>
                <CardHeader>
                  <CardTitle>学生成绩表</CardTitle>
                  <CardDescription>
                    {stats ? `${selectedExam} · 范围 ${String(stats.cohort_size)} 人 · ` : ''}
                    按总分降序仅为浏览排序，非正式名次；点击行可展开跨学年趋势。
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
