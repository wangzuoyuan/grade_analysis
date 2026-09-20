'use client'

/**
 * 学生检索（教学工作台，P8-UXFIX 重接 v1）。
 *
 * 数据全部来自 /api/v1：teaching/students（名册）、shared/exams（考试清单）、
 * teaching/analysis/exams/{exam}/students（最新一场成绩/名次）。班主任域请使用
 * /homeroom/students（本页在班主任工作台显示指引卡，不混域读数据）。
 * 缺考/无名次显示「—」，不转 0；迟到回包按请求序号丢弃（F11）。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import {
  AlertTriangle,
  ChevronRight,
  GraduationCap,
  Search,
  TrendingUp,
  Upload,
  Users,
} from 'lucide-react'

import {
  fetchStudents,
  fetchTeachingStudents,
  listExams,
  type ExamSummary,
  type PersonId,
  type StudentsQuery,
  type TeachingStudentRow,
  type WorkspaceStudent,
} from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { useWorkspace, workspaceHref, WorkspaceSwitcher } from '@/lib/workspace'
import { ClassScopePicker } from '@/components/ClassScopePicker'
import { useClassScope } from '@/lib/class-scope'
import { ScoreYearPicker } from '@/components/scores/ScoreYearPicker'
import { analysisScopeQuery, sortExamsDesc } from '@/components/scores/shared'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { displayStudentId } from '@/lib/student-id'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

interface StudentRow {
  person_id: PersonId
  name: string | null
  alias: string | null
  class_label: string | null
  score: number | null
  grade_score: number | null
  rank: number | null
  source_domain: string
}

function formatInt(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—'
  return String(Math.round(Number(n)))
}

function formatScoreText(v: number | null | undefined): string {
  if (v == null) return '—'
  return Number.isInteger(v) ? String(v) : v.toFixed(1)
}

export default function StudentSearchPage() {
  const { filter, generation, switching, mode } = useWorkspace()
  const { classes: teachingClasses, loading: scopeLoading } = useClassScope()

  const [roster, setRoster] = useState<WorkspaceStudent[] | null>(null)
  const [exams, setExams] = useState<ExamSummary[] | null>(null)
  const [latestExam, setLatestExam] = useState<ExamSummary | null>(null)
  const [scores, setScores] = useState<TeachingStudentRow[]>([])
  const [error, setError] = useState<string | null>(null)
  /** R04：>0 表示有班级名册失败、当前名单为部分结果；null = 完整。 */
  const [partialFailed, setPartialFailed] = useState<number | null>(null)
  const [rosterNonce, setRosterNonce] = useState(0)

  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')

  const reqRef = useRef(0)
  const scopeQ = useMemo(() => analysisScopeQuery(filter), [filter])

  // v1 /teaching/students 要求显式教学班（空范围不退化）：
  // 选了具体班直接查；「全部」按班级目录并行取各班名册，前端按 person 去重合并。
  const classIdsKey = useMemo(
    () =>
      typeof filter.teaching_class_id === 'number'
        ? ''
        : teachingClasses.map((c) => c.id).join(','),
    [filter.teaching_class_id, teachingClasses],
  )

  // 搜索框防抖
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query.trim()), 250)
    return () => clearTimeout(t)
  }, [query])

  // 名册 + 考试清单 + 最新一场成绩（单束请求，同序号同命运）。
  // R03：班主任工作台不发任何教学域业务请求（页面显示指引卡）。
  useEffect(() => {
    if (mode !== 'teaching') return
    const req = ++reqRef.current
    setRoster(null)
    setExams(null)
    setLatestExam(null)
    setScores([])
    setError(null)
    setPartialFailed(null)
    const rosterQ: StudentsQuery = {}
    if (typeof scopeQ.academic_year_id === 'number') rosterQ.academic_year_id = scopeQ.academic_year_id
    if (typeof scopeQ.teaching_class_id === 'number') rosterQ.teaching_class_id = scopeQ.teaching_class_id
    const rosterQs: StudentsQuery[] =
      rosterQ.teaching_class_id != null
        ? [rosterQ]
        : classIdsKey === ''
          ? []
          : classIdsKey.split(',').map((id) => ({ ...rosterQ, teaching_class_id: Number(id) }))
    if (rosterQs.length === 0 && rosterQ.teaching_class_id == null) {
      return // 班级目录未就绪：等 classIdsKey 变化后再取
    }
    Promise.all([
      // R04：逐班独立捕获失败——全部失败走错误卡；部分失败进入显式部分名单态
      Promise.all(
        rosterQs.map((q) =>
          fetchStudents('teaching', q)
            .then((res) => ({ ok: true as const, students: res.students ?? [] }))
            .catch(() => ({ ok: false as const, students: [] })),
        ),
      ),
      listExams('teaching', scopeQ).then((r) => sortExamsDesc(r.exams ?? [])),
    ])
      .then(async ([rosResults, exs]) => {
        const failed = rosResults.filter((r) => !r.ok).length
        if (failed === rosResults.length) {
          throw new Error('ROSTER_FAILED')
        }
        const latest = exs[0] ?? null
        const scoreRows = latest
          ? ((await fetchTeachingStudents(latest.exam_name, scopeQ)).students ?? [])
          : []
        const merged = new Map<string, WorkspaceStudent>()
        for (const r of rosResults) {
          for (const s of r.students) {
            if (!merged.has(String(s.person_id))) merged.set(String(s.person_id), s)
          }
        }
        return { ros: [...merged.values()], exs, latest, scoreRows, failed }
      })
      .then(({ ros, exs, latest, scoreRows, failed }) => {
        if (req !== reqRef.current) return
        setRoster(ros)
        setExams(exs)
        setLatestExam(latest)
        setScores(scoreRows)
        setPartialFailed(failed > 0 ? failed : null)
      })
      .catch((err: unknown) => {
        if (req !== reqRef.current) return
        setRoster([])
        setExams([])
        setPartialFailed(null)
        setError(
          err instanceof Error && err.message === 'ROSTER_FAILED'
            ? '教学班名册加载失败，请重试'
            : apiErrorMessage(err),
        )
      })
  }, [mode, scopeQ, generation, classIdsKey, rosterNonce])

  const loading = (roster == null && error == null) || scopeLoading

  const students = useMemo<StudentRow[]>(() => {
    if (roster == null) return []
    const byPid = new Map(scores.map((s) => [s.person_id, s]))
    const rows: StudentRow[] = roster.map((r) => {
      const s = byPid.get(r.person_id)
      return {
        person_id: r.person_id,
        name: r.name,
        alias: r.alias,
        class_label: s?.class_label ?? null,
        score: s?.score ?? null,
        grade_score: s?.grade_score ?? null,
        rank: s?.rank ?? null,
        source_domain: s?.source_domain ?? 'teaching',
      }
    })
    const kw = debouncedQuery
    const filtered = kw
      ? rows.filter(
          (r) =>
            (r.name ?? '').includes(kw) ||
            (r.alias ?? '').includes(kw) ||
            displayStudentId(r.alias).includes(kw),
        )
      : rows
    filtered.sort((a, b) => {
      if (a.rank != null && b.rank == null) return -1
      if (a.rank == null && b.rank != null) return 1
      if (a.rank != null && b.rank != null && a.rank !== b.rank) return a.rank - b.rank
      return (a.name ?? '').localeCompare(b.name ?? '', 'zh-CN')
    })
    return filtered
  }, [roster, scores, debouncedQuery])

  const count = students.length
  const rankedCount = students.filter((s) => s.rank != null).length
  const subject = latestExam?.subjects?.[0] ?? null
  const rankColumnLabel =
    typeof filter.teaching_class_id === 'number' ? '班内排名' : '名次（全部所教班）'

  // 班主任工作台：教学域学生档案按任教学科教学班组织，指引到班主任工作台
  if (mode === 'homeroom') {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">学生档案</h1>
          <p className="mt-1 text-sm text-slate-500">按任教学科教学班组织，属教学工作台</p>
        </div>
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-10 text-center">
            <Users className="h-8 w-8 text-slate-300" />
            <p className="text-sm text-slate-600">
              此处为任教学科教学班学生档案；班主任工作台请进入「学生档案」或「学生信息」查看行政班学生。
            </p>
            <div className="flex gap-2">
              <Button asChild>
                <Link href="/homeroom/profile">班主任学生档案</Link>
              </Button>
              <Button variant="outline" asChild>
                <Link href="/homeroom/students">进入学生信息</Link>
              </Button>
            </div>
            <WorkspaceSwitcher />
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            学生档案
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            {subject ? `当前学科：${subject} · ` : ''}按姓名或学号查找学生档案，支持按教学班与学年筛选
            {switching ? ' · 正在切换…' : ''}
          </p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <ScoreYearPicker />
          <ClassScopePicker />
          <Button asChild>
            <Link href={workspaceHref('/upload', 'teaching')}>
              <Upload className="h-4 w-4" />
              上传新成绩
            </Link>
          </Button>
        </div>
      </div>

      {error ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-8 text-center">
            <AlertTriangle className="h-8 w-8 text-amber-400" />
            <p className="text-sm text-slate-600">{error}</p>
            <Button variant="outline" size="sm" onClick={() => setRosterNonce((n) => n + 1)}>
              重试
            </Button>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* R04：部分班级名册失败时显式标注，部分名单绝不冒充完整人数 */}
          {partialFailed != null && (
            <div
              role="alert"
              className="flex flex-col items-start justify-between gap-2 rounded-lg border border-warning-300 bg-warning-50 px-4 py-3 text-sm text-warning-700 sm:flex-row sm:items-center print:hidden"
            >
              <span>
                有 {String(partialFailed)} 个教学班名册加载失败，下方为部分名单（{String(count)}{' '}
                人），不代表完整人数。
              </span>
              <Button variant="outline" size="sm" onClick={() => setRosterNonce((n) => n + 1)}>
                重试
              </Button>
            </div>
          )}
          <div className="grid gap-4 md:grid-cols-3">
            <SummaryCard
              icon={<Users className="h-4 w-4" />}
              label="学生数"
              value={loading ? '…' : String(count)}
            />
            <SummaryCard
              icon={<TrendingUp className="h-4 w-4" />}
              label="有排名记录"
              value={loading ? '…' : String(rankedCount)}
            />
            <SummaryCard
              icon={<GraduationCap className="h-4 w-4" />}
              label="最近考试覆盖"
              value={loading ? '…' : latestExam?.exam_name ?? '—'}
            />
          </div>

          <Card>
            <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <CardTitle>学生名单</CardTitle>
                <CardDescription>
                  默认按最新一场考试的名次排序，点击姓名进入学生单科趋势页；缺考显示「—」。
                </CardDescription>
              </div>
              <div className="relative w-full sm:w-80">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <Input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="按姓名 / 学号搜索"
                  className="pl-9"
                />
              </div>
            </CardHeader>
            <CardContent>
              {loading ? (
                <div className="space-y-2">
                  {Array.from({ length: 8 }).map((_, i) => (
                    <Skeleton key={i} className="h-12 w-full" />
                  ))}
                </div>
              ) : students.length === 0 ? (
                <EmptyState hasExams={(exams?.length ?? 0) > 0} />
              ) : (
                <>
                  {/* 桌面宽表 */}
                  <div className="hidden overflow-x-auto md:block">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-28">学号</TableHead>
                          <TableHead>姓名</TableHead>
                          <TableHead className="w-28">教学班</TableHead>
                          <TableHead className="w-24 text-right">原始分</TableHead>
                          <TableHead className="w-24 text-right">等级分</TableHead>
                          <TableHead className="w-28 text-right">{rankColumnLabel}</TableHead>
                          <TableHead className="w-12" />
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {students.map((student) => (
                          <TableRow key={student.person_id} className="hover:bg-slate-50">
                            <TableCell className="font-mono text-xs text-slate-600">
                              {student.alias ? displayStudentId(student.alias) : '待补学号'}
                            </TableCell>
                            <TableCell>
                              <Link
                                href={`/student/${student.person_id}`}
                                className="inline-flex items-center gap-1.5 font-medium text-slate-900 hover:text-brand-600"
                              >
                                {student.name ?? '（未命名）'}
                                {student.source_domain === 'homeroom' && (
                                  <Badge variant="outline" className="text-[10px]">
                                    班主任域投影
                                  </Badge>
                                )}
                              </Link>
                            </TableCell>
                            <TableCell>
                              {student.class_label ? (
                                <Badge variant="secondary">{student.class_label}</Badge>
                              ) : (
                                <span className="text-slate-400">—</span>
                              )}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {formatScoreText(student.score)}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {formatScoreText(student.grade_score)}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {formatInt(student.rank)}
                            </TableCell>
                            <TableCell>
                              <Link
                                href={`/student/${student.person_id}`}
                                aria-label={`查看${student.name ?? ''}`}
                                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-400 hover:bg-slate-100 hover:text-slate-900"
                              >
                                <ChevronRight className="h-4 w-4" />
                              </Link>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>

                  {/* 移动端卡片 */}
                  <div className="space-y-2 md:hidden">
                    {students.map((student) => (
                      <Link
                        key={student.person_id}
                        href={`/student/${student.person_id}`}
                        className="block rounded-lg border border-slate-200 bg-white p-3 transition hover:border-brand-300 hover:bg-brand-50/40"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <div className="font-medium text-slate-900">{student.name ?? '（未命名）'}</div>
                            <div className="mt-0.5 font-mono text-xs text-slate-500">
                              {student.alias ? displayStudentId(student.alias) : '待补学号'}
                            </div>
                          </div>
                          {student.class_label ? (
                            <Badge variant="secondary">{student.class_label}</Badge>
                          ) : null}
                        </div>
                        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-600">
                          <span>
                            原始分：
                            <span className="tabular-nums text-slate-900">
                              {formatScoreText(student.score)}
                            </span>
                          </span>
                          <span>
                            {rankColumnLabel}：
                            <span className="tabular-nums text-slate-900">
                              {formatInt(student.rank)}
                            </span>
                          </span>
                        </div>
                      </Link>
                    ))}
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}

function SummaryCard({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode
  label: string
  value: string
}) {
  return (
    <Card>
      <CardContent className="py-5">
        <div className="flex items-center gap-2 text-sm text-slate-500">
          {icon}
          {label}
        </div>
        <div className="mt-2 truncate text-2xl font-semibold text-slate-900">
          {value}
        </div>
      </CardContent>
    </Card>
  )
}

function EmptyState({ hasExams }: { hasExams: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
      <Users className="h-10 w-10 text-slate-300" />
      <p className="text-sm text-slate-500">{hasExams ? '没有匹配的学生' : '当前范围暂无学生'}</p>
      {!hasExams && (
        <Button asChild variant="outline" size="sm">
          <Link href={workspaceHref('/upload', 'teaching')}>
            <Upload className="h-4 w-4" />
            前往上传
          </Link>
        </Button>
      )}
    </div>
  )
}
