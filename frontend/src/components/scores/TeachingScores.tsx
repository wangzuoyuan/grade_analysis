'use client'

/**
 * 教学工作台成绩分析页（契约 p3-imports-analysis.md §2.2/§3）。
 *
 * 数据全部来自 /api/v1：shared/exams（考试清单）、teaching/analysis/*（stats/students/class-compare）。
 * 计量红线（契约 §2.2/§2.3）：缺考（score NULL）一律显示「—」不转 0；rank 为本班内名次
 * （同分同名次），不可计算时缺省显示「—」；班级对比均分恒为班级样本估算（estimated），
 * 绝不冒充年级/官方口径；迟到回包按资源分离的序号丢弃（F11：清单与数据互不作废）。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { AlertCircle, BarChart3, BookOpen, Users } from 'lucide-react'

import {
  fetchTeachingClassCompare,
  fetchTeachingStats,
  fetchTeachingStudents,
  listExams,
  type ExamSummary,
  type TeachingClassCompareResponse,
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
import { CorrelationCard } from '@/components/homework/CorrelationCard'
import { homeworkScopeQuery } from '@/components/homework/shared'
import { ExamSelect } from './ExamSelect'
import { ScoreYearPicker } from './ScoreYearPicker'
import { analysisScopeQuery, formatScore, scoreBasisLabel, sortExamsDesc } from './shared'

/** 班级对比卡：各班样本均分条形 + estimated 角标（E04）。拉取失败由页面统一错误卡承接。 */
function ClassComparePanel({ compare }: { compare: TeachingClassCompareResponse | null }) {
  if (compare == null) {
    return <Skeleton className="h-24 w-full" />
  }
  const rows = compare.classes ?? []
  if (rows.length === 0) {
    return <p className="py-4 text-sm text-slate-500">暂无可对比的教学班样本。</p>
  }
  // 满宽基准取样本均分最大值（null 不参与）；条宽仅为可视化比例，分数展示仍走 formatScore（「—」）
  const avgs = rows.map((c) => c.subject_avg).filter((v): v is number => v != null)
  const maxAvg = Math.max(...avgs, 1)
  return (
    <ul className="space-y-2">
      {rows.map((c) => {
        // 样本均分缺省（不可算）时不出条，宽度按 0 处理，与分数格的「—」区分
        const width = c.subject_avg == null ? 0 : Math.round((c.subject_avg / maxAvg) * 100)
        return (
          <li key={c.teaching_class_id} className="flex items-center gap-3">
            <span className="w-28 shrink-0 truncate text-xs text-slate-500" title={c.class_label}>
              {c.class_label}
            </span>
            <div className="h-3 flex-1 overflow-hidden rounded-full bg-slate-100">
              <div
                className={cn(
                  'h-full rounded-full',
                  c.source === 'estimated'
                    ? 'bg-gradient-to-r from-[#35b9e9]/70 to-[#1f7fd6]/70'
                    : 'bg-gradient-to-r from-[#1f7fd6] to-[#35b9e9]',
                )}
                style={{ width: `${String(width)}%` }}
              />
            </div>
            <span className="w-12 shrink-0 text-right text-xs tabular-nums text-slate-600">
              {formatScore(c.subject_avg)}
            </span>
            <Badge variant="secondary" className="shrink-0 text-[10px]">
              {c.source === 'estimated' ? '估算' : c.source}
            </Badge>
            <span className="w-16 shrink-0 text-right text-[10px] text-slate-400">
              {String(c.member_count)} 人
            </span>
          </li>
        )
      })}
    </ul>
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

  // 当前考试的单科统计 / 学生表 / 班级对比
  const [stats, setStats] = useState<TeachingStatsResponse | null>(null)
  const [students, setStudents] = useState<TeachingStudentRow[] | null>(null)
  const [compare, setCompare] = useState<TeachingClassCompareResponse | null>(null)
  const [dataError, setDataError] = useState<string | null>(null)

  // 请求序号按资源分离（F11）：考试清单与 stats+students+对比各自比对各自序号，
  // 任一资源重拉绝不使另一资源的合法回包失效（否则切考试后骨架永久加载）
  const examsReqRef = useRef(0)
  const dataReqRef = useRef(0)

  const scopeQ = useMemo(() => analysisScopeQuery(filter), [filter])

  // 相关性卡走作业域作用域（homeworkScopeQuery，不带 term_id），考试跟随页面顶部选择
  const hwScopeQ = useMemo(() => homeworkScopeQuery(filter), [filter])
  const scopeSubject = scope?.subject ?? null

  // 考试清单：跟随工作台筛选/世代变化整体重置（仅考试列表自身状态；
  // stats/students/对比由数据 effect 的 scopeQ/考试依赖负责重置）
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
          setSelectedExam(sorted.some((exam) => exam.exam_name === requestedExam) ? requestedExam : sorted[0].exam_name)
        }
      })
      .catch((err: unknown) => {
        if (req !== examsReqRef.current) return
        setExams([])
        setExamsError(apiErrorMessage(err))
      })
  }, [scopeQ, generation, examNonce, requestedExam])

  // 当前考试的 stats + 学生表 + 班级对比（并行；任一失败进统一错误态；只比对 dataReqRef）
  useEffect(() => {
    if (selectedExam == null) return
    const req = ++dataReqRef.current
    setStats(null)
    setStudents(null)
    setCompare(null)
    setDataError(null)
    Promise.all([
      fetchTeachingStats(selectedExam, scopeQ),
      fetchTeachingStudents(selectedExam, scopeQ),
      fetchTeachingClassCompare(selectedExam, scopeQ),
    ])
      .then(([st, sRows, cmp]) => {
        if (req !== dataReqRef.current) return
        setStats(st)
        setStudents(sRows.students ?? [])
        setCompare(cmp)
      })
      .catch((err: unknown) => {
        if (req !== dataReqRef.current) return
        setStats(null)
        setStudents(null)
        setCompare(null)
        setDataError(apiErrorMessage(err))
      })
  }, [selectedExam, scopeQ])

  // 学生表按本班内名次升序；无名次（不可算/缺考）排后，其次按姓名
  const sortedStudents = useMemo(() => {
    const list = (students ?? []).slice()
    list.sort((a, b) => {
      if (a.rank != null && b.rank == null) return -1
      if (a.rank == null && b.rank != null) return 1
      if (a.rank != null && b.rank != null && a.rank !== b.rank) return a.rank - b.rank
      return (a.name ?? '').localeCompare(b.name ?? '', 'zh-CN')
    })
    return list
  }, [students])

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
            任教学科成绩、本班名次与教学班对比{switching ? ' · 正在切换…' : ''}
          </p>
        </div>
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
                          <p className="text-xs text-slate-400">名次范围（本班内）</p>
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
                        同分同名次（min-rank）；缺考不计入均分与名次分母；名次仅在本教学班成员内计算。
                      </p>
                    </>
                  )}
                </CardContent>
              </Card>

              {/* 学生表：rank/grade_score/source_domain */}
              <Card>
                <CardHeader>
                  <CardTitle>学生成绩表</CardTitle>
                  <CardDescription>
                    按本班内名次升序；「班主任域投影」行来自行政班导入的任教学科事实（反向投影参与名次计算）。
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
                    <div className="overflow-x-auto">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead className="text-xs">姓名</TableHead>
                            <TableHead className="text-xs">班级</TableHead>
                            <TableHead className="text-right text-xs">成绩</TableHead>
                            <TableHead className="text-right text-xs">等级分</TableHead>
                            <TableHead className="text-right text-xs">名次（本班内）</TableHead>
                            <TableHead className="text-xs">来源</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {sortedStudents.map((s) => (
                            <TableRow key={String(s.person_id)}>
                              <TableCell className="whitespace-nowrap text-sm font-medium text-slate-900">
                                {s.name ?? '（未命名）'}
                              </TableCell>
                              <TableCell className="whitespace-nowrap text-xs text-slate-500">
                                {s.class_label ?? '—'}
                              </TableCell>
                              <TableCell
                                className={cn(
                                  'text-right tabular-nums text-sm',
                                  s.score == null ? 'text-slate-300' : 'text-slate-900',
                                )}
                              >
                                {s.score == null ? '—' : formatScore(s.score)}
                              </TableCell>
                              <TableCell className="text-right tabular-nums text-sm text-slate-600">
                                {s.grade_score == null ? '—' : formatScore(s.grade_score)}
                              </TableCell>
                              <TableCell className="text-right tabular-nums text-sm text-slate-900">
                                {s.rank == null ? '—' : String(s.rank)}
                              </TableCell>
                              <TableCell>
                                {s.source_domain === 'homeroom' ? (
                                  <Badge variant="outline">班主任域投影</Badge>
                                ) : (
                                  <Badge variant="secondary">教学域</Badge>
                                )}
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* 班级对比：均为班级样本估算（E04），官方口径未入库前不提供 */}
              <Card>
                <CardHeader>
                  <CardTitle>教学班对比</CardTitle>
                  <CardDescription>
                    各班任教学科样本均分（同一考试）；数据为班级样本估算，非年级或官方口径，
                    样本人数较少时仅供参考。
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <ClassComparePanel compare={compare} />
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
