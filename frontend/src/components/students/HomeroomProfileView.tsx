'use client'

/**
 * 班主任学生档案全景组件。
 *
 * 核心功能：
 * 1. 按当前绑定的行政班加载学生名册，支持顶部即时搜索与快速切换学生（URL ?person_id= 同步）；
 * 2. 学生基本资料卡（姓名、当前学号、座号、状态、学号演进历史）；
 * 3. 核心 KPI（最新总分、历次考试、成长记录条数）；
 * 4. 总分历场成绩趋势折线图与历次各科成绩明细表（缺考严格显示「—」不转 0）；
 * 5. 班主任专属成长档案（谈话/家访/沟通，支持即时记录与跟进）；
 * 6. 作业缺交记录卡片（HomeworkCard，homeroom 域）；
 * 7. 快捷打印家长会一页纸报告入口。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import {
  AlertCircle,
  ArrowDownRight,
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  FileText,
  LineChart as LineChartIcon,
  Minus,
  NotebookPen,
  Printer,
  Search,
  User,
  Users,
} from 'lucide-react'
import {
  LineChart,
  Line,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis as RXAxis,
  YAxis as RYAxis,
} from 'recharts'

import {
  fetchClasses,
  fetchSharedConfig,
  fetchStudents,
  getStudentReport,
  type StudentReportResponse,
  type WorkspaceStudent,
} from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { useWorkspace, WorkspaceSwitcher } from '@/lib/workspace'
import { analysisScopeQuery } from '@/components/scores/shared'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
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
import HomeworkCard from '@/components/HomeworkCard'
import StudentNotes from '@/components/StudentNotes'
import { cn } from '@/lib/utils'

const DASH = '—'

function fmtDate(v: string | null | undefined): string {
  return v ? v.slice(0, 10) : DASH
}

function fmtScore(v: number | null | undefined): string {
  if (v == null) return DASH
  return Number.isInteger(v) ? String(v) : v.toFixed(1)
}

function nameInitial(name: string | null): string {
  if (!name) return '?'
  return name.trim().charAt(0)
}

function statusLabel(s: string | null): string {
  if (s == null || s === 'active' || s === '') return '在班'
  if (s === 'transferred') return '已转出'
  if (s === 'graduated') return '已毕业'
  return s
}

interface TrendPoint {
  exam_name: string
  exam_date: string | null
  score: number | null
}

export function HomeroomProfileView() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { filter, scope, scopeError, generation, switching, mode } = useWorkspace()
  const scopeQ = useMemo(() => analysisScopeQuery(filter), [filter])

  // 名册状态
  const [students, setStudents] = useState<WorkspaceStudent[] | null>(null)
  const [rosterError, setRosterError] = useState<string | null>(null)

  // 选中的学生
  const [selectedPersonId, setSelectedPersonId] = useState<string | null>(() => {
    return searchParams.get('person_id') || searchParams.get('student_id') || null
  })
  const [searchKw, setSearchKw] = useState('')

  // 档案详情数据
  const [report, setReport] = useState<StudentReportResponse | null>(null)
  const [reportLoading, setReportLoading] = useState(false)
  const [reportError, setReportError] = useState<string | null>(null)

  const rosterReqRef = useRef(0)
  const reportReqRef = useRef(0)

  // 1. 加载当前行政班学生名册
  const loadRoster = useCallback(() => {
    const req = ++rosterReqRef.current
    setStudents(null)
    setRosterError(null)

    if (scopeError != null) {
      setStudents([])
      setRosterError(`工作台范围解析失败：${apiErrorMessage(scopeError)}`)
      return
    }
    if (scope == null) return

    const resolveRoster = async () => {
      let academicYearId = scopeQ.academic_year_id
      if (academicYearId === undefined) {
        const config = await fetchSharedConfig()
        academicYearId = config.current_academic_year?.id
      }
      if (academicYearId === undefined) {
        throw new Error('未配置当前学年，无法确定行政班名册范围')
      }
      const catalog = await fetchClasses(academicYearId)
      if (catalog.homeroom == null) {
        throw new Error(`学年 ${catalog.academic_year_name} 未找到本人绑定的行政班`)
      }
      return fetchStudents('homeroom', {
        ...scopeQ,
        academic_year_id: academicYearId,
        class_id: catalog.homeroom.class_id,
      })
    }

    void resolveRoster()
      .then((r) => {
        if (req !== rosterReqRef.current) return
        const list = r.students ?? []
        setStudents(list)
        // 优先匹配传入的 person_id 或 student_id，否则默认选中第一个在班学生
        setSelectedPersonId((prev) => {
          const targetId = prev || searchParams.get('person_id') || searchParams.get('student_id')
          if (targetId && list.some((s) => String(s.person_id) === targetId)) {
            const matched = list.find((s) => String(s.person_id) === targetId)
            return matched ? String(matched.person_id) : targetId
          }
          const activeFirst = list.find((s) => s.status === 'active' || !s.status)
          return activeFirst ? String(activeFirst.person_id) : (list[0] ? String(list[0].person_id) : null)
        })
      })
      .catch((err: unknown) => {
        if (req !== rosterReqRef.current) return
        setStudents([])
        setRosterError(apiErrorMessage(err))
      })
  }, [scope, scopeError, scopeQ, searchParams])

  useEffect(() => {
    loadRoster()
  }, [loadRoster, generation])

  // 同步 URL 参数中的 person_id 或 student_id
  useEffect(() => {
    const p = searchParams.get('person_id') || searchParams.get('student_id')
    if (p && p !== selectedPersonId) {
      setSelectedPersonId(p)
    }
  }, [searchParams, selectedPersonId])

  // 选人时替换 URL 参数
  const selectStudent = useCallback(
    (id: string) => {
      setSelectedPersonId(id)
      const url = new URL(window.location.href)
      url.searchParams.set('person_id', id)
      url.searchParams.delete('student_id')
      window.history.replaceState(null, '', url.toString())
    },
    [],
  )

  // 2. 加载选中学生的档案数据
  useEffect(() => {
    if (!selectedPersonId) {
      setReport(null)
      return
    }
    const req = ++reportReqRef.current
    setReportLoading(true)
    setReportError(null)

    getStudentReport(Number(selectedPersonId))
      .then((data) => {
        if (req !== reportReqRef.current) return
        setReport(data)
        setReportLoading(false)
      })
      .catch((err: unknown) => {
        if (req !== reportReqRef.current) return
        setReport(null)
        setReportError(apiErrorMessage(err))
        setReportLoading(false)
      })
  }, [selectedPersonId, generation])

  // 过滤后的学生列表（按拼音/姓名/学号）
  const filteredStudents = useMemo(() => {
    if (!students) return []
    const kw = searchKw.trim()
    if (!kw) return students
    return students.filter(
      (s) =>
        (s.name ?? '').includes(kw) ||
        (s.alias ?? '').includes(kw),
    )
  }, [students, searchKw])

  // 当前选中的学生对象
  const currentStudent = useMemo(() => {
    if (!students || !selectedPersonId) return null
    return students.find((s) => String(s.person_id) === selectedPersonId) ?? null
  }, [students, selectedPersonId])

  // 上一位 / 下一位
  const currentIndex = useMemo(() => {
    if (!students || !selectedPersonId) return -1
    return students.findIndex((s) => String(s.person_id) === selectedPersonId)
  }, [students, selectedPersonId])

  const goPrev = () => {
    if (currentIndex > 0 && students) {
      selectStudent(String(students[currentIndex - 1].person_id))
    }
  }

  const goNext = () => {
    if (students && currentIndex >= 0 && currentIndex < students.length - 1) {
      selectStudent(String(students[currentIndex + 1].person_id))
    }
  }

  // 总分折线图数据
  const totalTrend = useMemo<TrendPoint[]>(() => {
    if (!report || !report.totals || report.totals.length === 0) return []
    const totalGroup = report.totals[0]
    const exams = totalGroup?.exams ?? []
    return exams.map((e) => ({
      exam_name: e.exam_name,
      exam_date: e.exam_date ?? null,
      score: e.score,
    }))
  }, [report])

  // 历次考试明细整合（各场考试与各科成绩）
  const examMatrix = useMemo(() => {
    if (!report) return []
    const examMap = new Map<
      string,
      {
        exam_name: string
        exam_date: string | null
        total_score: number | null
        subjects: Record<string, number | null>
      }
    >()

    // 填充总分
    if (report.totals && report.totals.length > 0) {
      for (const t of report.totals[0].exams) {
        if (!examMap.has(t.exam_name)) {
          examMap.set(t.exam_name, {
            exam_name: t.exam_name,
            exam_date: t.exam_date ?? null,
            total_score: t.score,
            subjects: {},
          })
        } else {
          examMap.get(t.exam_name)!.total_score = t.score
        }
      }
    }

    // 填充各科成绩
    for (const sub of report.subjects ?? []) {
      for (const e of sub.exams) {
        if (!examMap.has(e.exam_name)) {
          examMap.set(e.exam_name, {
            exam_name: e.exam_name,
            exam_date: e.exam_date ?? null,
            total_score: null,
            subjects: { [sub.subject]: e.score },
          })
        } else {
          const entry = examMap.get(e.exam_name)!
          entry.subjects[sub.subject] = e.score
          if (!entry.exam_date && e.exam_date) entry.exam_date = e.exam_date
        }
      }
    }

    const list = Array.from(examMap.values())
    list.sort((a, b) => (a.exam_date ?? '').localeCompare(b.exam_date ?? '') || a.exam_name.localeCompare(b.exam_name))
    return list
  }, [report])

  // 全部出现的学科名
  const allSubjects = useMemo(() => {
    if (!report?.subjects) return []
    return report.subjects.map((s) => s.subject)
  }, [report])

  // 最新一场总分与变化
  const latestTotal = totalTrend.length > 0 ? totalTrend[totalTrend.length - 1] : null
  const prevTotal = totalTrend.length > 1 ? totalTrend[totalTrend.length - 2] : null
  const totalDiff = latestTotal?.score != null && prevTotal?.score != null ? latestTotal.score - prevTotal.score : null

  // 门禁：若当前处于 teaching 工作台，引导切换
  if (mode === 'teaching') {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">学生档案</h1>
          <p className="mt-1 text-sm text-slate-500">按行政班全科组织，属班主任工作台</p>
        </div>
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-10 text-center">
            <Users className="h-8 w-8 text-slate-300" />
            <p className="text-sm text-slate-600">
              全科与成长档案属班主任工作台；教学工作台请在「学生档案」查看任教学科学生。
            </p>
            <Button asChild>
              <Link href="/student">进入教学学生档案</Link>
            </Button>
            <WorkspaceSwitcher />
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* 顶部标题与操作栏 */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">学生档案</h1>
          <p className="mt-1 text-sm text-slate-500">
            行政班学情全貌：历次全科与总分、作业表现、成长记录与家长会一页纸{switching ? ' · 正在切换…' : ''}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {selectedPersonId && (
            <Button variant="outline" size="sm" asChild>
              <Link href={`/homeroom/students/${encodeURIComponent(selectedPersonId)}/report`} target="_blank">
                <Printer className="h-4 w-4" aria-hidden="true" />
                打印家长会一页纸
              </Link>
            </Button>
          )}
          <Button variant="outline" size="sm" asChild>
            <Link href="/homeroom/students">
              <FileText className="h-4 w-4" aria-hidden="true" />
              学生信息
            </Link>
          </Button>
        </div>
      </div>

      {/* 学生切换与筛选栏 */}
      <Card>
        <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-1 flex-col gap-2 sm:flex-row sm:items-center">
            <div className="relative w-full sm:w-56">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
              <Input
                placeholder="搜索学生姓名 / 学号"
                value={searchKw}
                onChange={(e) => setSearchKw(e.target.value)}
                className="h-9 pl-9 text-xs"
              />
            </div>
            <div className="w-full sm:w-60">
              <Select
                value={selectedPersonId ?? undefined}
                onValueChange={selectStudent}
                disabled={!students || students.length === 0}
              >
                <SelectTrigger className="h-9 text-xs">
                  <SelectValue placeholder={students ? '选择学生' : '加载名册中…'} />
                </SelectTrigger>
                <SelectContent className="max-h-72">
                  {filteredStudents.map((s) => (
                    <SelectItem key={String(s.person_id)} value={String(s.person_id)}>
                      {s.name ?? '（未命名）'} {s.alias ? `(${s.alias})` : ''}{' '}
                      {s.status && s.status !== 'active' ? `[${statusLabel(s.status)}]` : ''}
                    </SelectItem>
                  ))}
                  {filteredStudents.length === 0 && (
                    <div className="p-2 text-center text-xs text-slate-400">无匹配学生</div>
                  )}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="flex items-center justify-end gap-1.5 text-xs text-slate-500">
            <span>
              共 {students?.length ?? 0} 人
              {currentIndex >= 0 ? ` (第 ${currentIndex + 1} 位)` : ''}
            </span>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 px-2"
              onClick={goPrev}
              disabled={currentIndex <= 0}
              title="上一位学生"
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 px-2"
              onClick={goNext}
              disabled={!students || currentIndex >= students.length - 1}
              title="下一位学生"
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 异常或空状态 */}
      {rosterError ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-10 text-center">
            <AlertCircle className="h-8 w-8 text-amber-400" />
            <p className="text-sm text-slate-600">{rosterError}</p>
            <Button variant="outline" size="sm" onClick={loadRoster}>
              重试
            </Button>
          </CardContent>
        </Card>
      ) : !selectedPersonId ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-2 py-12 text-center">
            <User className="h-10 w-10 text-slate-300" />
            <p className="text-base font-medium text-slate-700">请选择学生查看档案</p>
            <p className="text-xs text-slate-400">可在上方选择器中快速切换或按姓名查找行政班学生。</p>
          </CardContent>
        </Card>
      ) : reportLoading && !report ? (
        <div className="space-y-4">
          <Skeleton className="h-24 w-full" />
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
          <Skeleton className="h-64 w-full" />
        </div>
      ) : reportError ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-10 text-center">
            <AlertCircle className="h-8 w-8 text-amber-400" />
            <p className="text-sm text-slate-600">{reportError}</p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setSelectedPersonId((id) => (id ? String(id) : null))}
            >
              重试
            </Button>
          </CardContent>
        </Card>
      ) : report ? (
        <>
          {/* 学生基本信息卡 */}
          <Card>
            <CardContent className="flex flex-col gap-4 p-6 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-center gap-4">
                <Avatar className="h-16 w-16 border border-brand-200 bg-brand-50 text-xl font-bold text-brand-700">
                  <AvatarFallback className="bg-brand-50 text-brand-700">
                    {nameInitial(report.person.name)}
                  </AvatarFallback>
                </Avatar>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-xl font-bold text-slate-900">
                      {report.person.name ?? '（未命名）'}
                    </h2>
                    {currentStudent?.status && currentStudent.status !== 'active' ? (
                      <Badge variant="secondary">{statusLabel(currentStudent.status)}</Badge>
                    ) : (
                      <Badge variant="success">在班</Badge>
                    )}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
                    <span>学号：<strong className="font-mono text-slate-700">{report.roster.alias ?? currentStudent?.alias ?? DASH}</strong></span>
                    {report.roster.seat_no != null && (
                      <span>座号：<strong className="text-slate-700">{report.roster.seat_no}</strong></span>
                    )}
                    {report.roster.class_label && (
                      <span>班级：<strong className="text-slate-700">{report.roster.class_label}</strong></span>
                    )}
                    {report.roster.academic_year_name && (
                      <span>学年：<strong className="text-slate-700">{report.roster.academic_year_name}</strong></span>
                    )}
                  </div>
                  {report.person.aliases && report.person.aliases.length > 1 && (
                    <div className="mt-1 text-[11px] text-slate-400">
                      学号演进历史：
                      {report.person.aliases
                        .map(
                          (a) =>
                            `${a.alias_value} (${fmtDate(a.valid_from)} ~ ${
                              a.valid_to ? fmtDate(a.valid_to) : '至今'
                            })`,
                        )
                        .join('、')}
                    </div>
                  )}
                </div>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" asChild>
                  <Link href={`/homeroom/students/${encodeURIComponent(selectedPersonId)}/report`}>
                    <Printer className="mr-1 h-3.5 w-3.5" />
                    一览打印视图
                  </Link>
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* 核心学情 KPI */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Card>
              <CardContent className="space-y-1 py-5">
                <div className="text-xs font-medium text-slate-500">最新考试总分</div>
                <div className="flex items-baseline gap-2">
                  <span className="text-2xl font-bold tracking-tight text-slate-900">
                    {latestTotal?.score != null ? fmtScore(latestTotal.score) : DASH}
                  </span>
                  {totalDiff != null && (
                    <span
                      className={cn(
                        'inline-flex items-center text-xs font-medium',
                        totalDiff > 0
                          ? 'text-success-600'
                          : totalDiff < 0
                          ? 'text-danger-600'
                          : 'text-slate-500',
                      )}
                    >
                      {totalDiff > 0 ? (
                        <ArrowUpRight className="h-3 w-3" />
                      ) : totalDiff < 0 ? (
                        <ArrowDownRight className="h-3 w-3" />
                      ) : (
                        <Minus className="h-3 w-3" />
                      )}
                      {totalDiff > 0 ? `+${totalDiff.toFixed(1)}` : totalDiff.toFixed(1)}
                    </span>
                  )}
                </div>
                <div className="text-[11px] text-slate-400">
                  {latestTotal ? `考试：${latestTotal.exam_name}` : '暂无总分记录'}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="space-y-1 py-5">
                <div className="text-xs font-medium text-slate-500">历次考试记录</div>
                <div className="text-2xl font-bold tracking-tight text-slate-900">
                  {examMatrix.length} <span className="text-xs font-normal text-slate-500">场</span>
                </div>
                <div className="text-[11px] text-slate-400">
                  涵盖 {allSubjects.length} 门学科的历次成绩
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="space-y-1 py-5">
                <div className="text-xs font-medium text-slate-500">班主任成长记录</div>
                <div className="text-2xl font-bold tracking-tight text-slate-900">
                  {report.notes_summary.count}{' '}
                  <span className="text-xs font-normal text-slate-500">条</span>
                </div>
                <div className="text-[11px] text-slate-400">谈话、家访与观察跟进记录</div>
              </CardContent>
            </Card>
          </div>

          {/* 总分走势折线图 */}
          {totalTrend.length > 0 && totalTrend.some((t) => t.score != null) && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <LineChartIcon className="h-4 w-4" />
                  总分历次走势
                </CardTitle>
                <CardDescription>
                  展示学生在各场考试中的总分变化曲线（缺考点不连线）
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="h-64 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={totalTrend} margin={{ top: 10, right: 20, left: 0, bottom: 20 }}>
                      <RXAxis
                        dataKey="exam_name"
                        tick={{ fontSize: 11, fill: '#64748b' }}
                        interval={0}
                        angle={-15}
                        textAnchor="end"
                      />
                      <RYAxis
                        tick={{ fontSize: 11, fill: '#64748b' }}
                        domain={['dataMin - 10', 'dataMax + 10']}
                      />
                      <RTooltip
                        formatter={(val: unknown) => [
                          val != null ? `${Number(val).toFixed(1)} 分` : DASH,
                          '总分',
                        ]}
                        labelFormatter={(label) => `考试：${label}`}
                      />
                      <Line
                        type="monotone"
                        dataKey="score"
                        stroke="#1f7fd6"
                        strokeWidth={2.5}
                        dot={{ r: 4, fill: '#1f7fd6' }}
                        activeDot={{ r: 6 }}
                        connectNulls={false}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </CardContent>
            </Card>
          )}

          {/* 历次考试成绩全科明细表 */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">历次考试各科明细</CardTitle>
              <CardDescription>
                缺考严格显示为「—」，不折算为 0；带警告标注的项代表教学域录入分与班主任域不一致。
              </CardDescription>
            </CardHeader>
            <CardContent>
              {examMatrix.length === 0 ? (
                <div className="py-8 text-center text-sm text-slate-400">暂无成绩数据</div>
              ) : (
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="whitespace-nowrap text-xs">考试名称</TableHead>
                        <TableHead className="whitespace-nowrap text-xs">考试日期</TableHead>
                        <TableHead className="whitespace-nowrap text-right text-xs">总分</TableHead>
                        {allSubjects.map((sub) => (
                          <TableHead key={sub} className="whitespace-nowrap text-right text-xs">
                            {sub}
                          </TableHead>
                        ))}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {examMatrix.map((row) => (
                        <TableRow key={row.exam_name}>
                          <TableCell className="whitespace-nowrap font-medium text-slate-800">
                            {row.exam_name}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-xs text-slate-500">
                            {fmtDate(row.exam_date)}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-right font-semibold text-slate-900">
                            {fmtScore(row.total_score)}
                          </TableCell>
                          {allSubjects.map((sub) => (
                            <TableCell key={sub} className="whitespace-nowrap text-right tabular-nums text-slate-700">
                              {fmtScore(row.subjects[sub])}
                            </TableCell>
                          ))}
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>

          {/* 作业缺交记录组件 */}
          <HomeworkCard
            mode="homeroom"
            personId={Number(selectedPersonId)}
            scopeQ={{
              academic_year_id: scopeQ.academic_year_id,
            }}
          />

          {/* 班主任专属成长档案（谈话/家访/沟通管理） */}
          <StudentNotes
            mode="homeroom"
            personId={Number(selectedPersonId)}
            scopeQ={{
              academic_year_id: scopeQ.academic_year_id,
            }}
          />
        </>
      ) : null}
    </div>
  )
}
