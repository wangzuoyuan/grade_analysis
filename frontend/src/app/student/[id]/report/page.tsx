'use client'

/**
 * 家长会学生情况表（教学工作台，P8-UXFIX 重接 v1）。
 *
 * 数据来自 /api/v1：teaching/students/{person_id}（画像 + 等级分）、
 * teaching/analysis/exams/{exam}/students（逐场教学班名次，与画像页同口径，R05）、
 * homework/students/{person_id}（作业事件）、teaching/students/{id}/notes（档案）。
 * v1 无年级百分位列；班主任工作台显示指引卡、不发任何教学域请求（R03）。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { AlertTriangle, ChevronLeft, Printer } from 'lucide-react'

import {
  fetchStudent,
  fetchTeachingStudents,
  homeworkStudentEvents,
  listStudentNotes,
  type StudentNote,
  type StudentProfile,
} from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { useWorkspace, WorkspaceSwitcher } from '@/lib/workspace'
import { analysisScopeQuery } from '@/components/scores/shared'
import { Card, CardContent } from '@/components/ui/card'
import { MoreToggle } from '@/components/ui/more-toggle'

const DASH = '—'

interface TrendPoint {
  exam_name: string
  exam_date: string | null
  subject: string
  score: number | null
  grade_score: number | null
  source_domain: string
  rank: number | null
}

function num(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v
  return null
}

function scoreText(v: number | null | undefined): string {
  if (v == null) return DASH
  return Number.isInteger(v) ? String(v) : v.toFixed(1)
}

export default function StudentReportPage() {
  const params = useParams<{ id: string }>()
  const rawId = Array.isArray(params?.id) ? params?.id[0] : params?.id
  const personId = rawId != null && /^\d+$/.test(rawId) ? Number(rawId) : null
  const { filter, generation, mode } = useWorkspace()
  const scopeQ = useMemo(() => analysisScopeQuery(filter), [filter])

  const [profile, setProfile] = useState<StudentProfile | null>(null)
  const [ranks, setRanks] = useState<Map<string, number | null>>(new Map())
  const [missingCount, setMissingCount] = useState<number | null>(null)
  const [excusedCount, setExcusedCount] = useState<number>(0)
  const [notes, setNotes] = useState<StudentNote[]>([])
  const [notesExpanded, setNotesExpanded] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const reqRef = useRef(0)

  useEffect(() => {
    // R03：班主任工作台不发任何教学域请求（页面显示指引卡）
    if (mode !== 'teaching') return
    if (personId == null) {
      setLoading(false)
      setError('学生编号无效')
      return
    }
    const req = ++reqRef.current
    setLoading(true)
    setError(null)
    setProfile(null)
    setRanks(new Map())
    setMissingCount(null)
    setExcusedCount(0)
    setNotes([])
    fetchStudent('teaching', personId, scopeQ)
      .then(async (p) => {
        const examNames = Array.from(
          new Set((p.subjects ?? []).flatMap((s) => (s.exams ?? []).map((e) => e.exam_name))),
        )
        const [rankRows, hw, ns] = await Promise.all([
          // R05：逐场教学班名次，与画像页同学年/班级/考试口径；单场失败按「—」不中断报告
          Promise.all(
            examNames.map((name) =>
              fetchTeachingStudents(name, scopeQ)
                .then((r) => [name, r.students ?? []] as const)
                .catch(() => [name, []] as const),
            ),
          ),
          homeworkStudentEvents(personId, 'teaching', scopeQ).catch(() => null),
          listStudentNotes('teaching', personId, scopeQ)
            .then((r) => r.notes ?? [])
            .catch(() => [] as StudentNote[]),
        ])
        return { p, rankRows, hw, ns }
      })
      .then(({ p, rankRows, hw, ns }) => {
        if (req !== reqRef.current) return
        const rankMap = new Map<string, number | null>()
        for (const [name, students] of rankRows) {
          rankMap.set(name, students.find((s) => s.person_id === personId)?.rank ?? null)
        }
        setProfile(p)
        setRanks(rankMap)
        setNotes(ns)
        if (hw != null) {
          const events = hw.events ?? []
          setMissingCount(events.filter((e) => e.status === 'missing').length)
          setExcusedCount(events.filter((e) => e.status === 'excused').length)
        }
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (req !== reqRef.current) return
        setError(apiErrorMessage(err))
        setLoading(false)
      })
  }, [mode, personId, scopeQ, generation])

  const trend = useMemo<TrendPoint[]>(() => {
    const points: TrendPoint[] = []
    for (const group of profile?.subjects ?? []) {
      for (const e of group.exams ?? []) {
        points.push({
          exam_name: e.exam_name,
          exam_date: e.exam_date,
          subject: group.subject,
          score: e.score,
          grade_score: e.grade_score ?? null,
          source_domain: e.source_domain,
          rank: ranks.get(e.exam_name) ?? null,
        })
      }
    }
    points.sort((a, b) => (a.exam_date ?? '').localeCompare(b.exam_date ?? ''))
    return points
  }, [profile, ranks])

  // R03：班主任工作台指引（不取数、不显示教学域学生）
  if (mode === 'homeroom') {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">家长会学生情况表</h1>
          <p className="mt-1 text-sm text-slate-500">按任教学科教学班组织，属教学工作台</p>
        </div>
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-10 text-center">
            <AlertTriangle className="h-8 w-8 text-slate-300" />
            <p className="text-sm text-slate-600">
              本报告按教学班任教学科生成，属教学工作台；班主任工作台请在「学生管理」使用行政班学生报告。
            </p>
            <WorkspaceSwitcher />
          </CardContent>
        </Card>
      </div>
    )
  }

  if (loading) {
    return <div className="p-8 text-sm text-slate-400">加载中…</div>
  }

  if (error || !profile) {
    return (
      <div className="mx-auto max-w-3xl space-y-4 bg-white p-6 text-slate-900 print:p-0">
        <Link
          href={personId != null ? `/student/${personId}` : '/student'}
          className="inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900 print:hidden"
        >
          <ChevronLeft className="h-4 w-4" />
          返回学生页
        </Link>
        <p className="py-6 text-sm text-slate-500">{error ?? '加载失败，请稍后重试。'}</p>
      </div>
    )
  }

  const subject = profile.subjects?.[0]?.subject ?? null
  const latest = trend[trend.length - 1]
  // 名次变化对比基准：第一个有名次的考试（首场缺考无名次时从首个有效名次算起）
  const firstRanked = trend.find((p) => p.rank != null) ?? null

  return (
    <div className="mx-auto max-w-3xl space-y-5 bg-white p-6 text-slate-900 print:p-0">
      {/* 顶部操作（打印时隐藏） */}
      <div className="flex items-center justify-between print:hidden">
        <Link
          href={personId != null ? `/student/${personId}` : '/student'}
          className="inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900"
        >
          <ChevronLeft className="h-4 w-4" />
          返回学生页
        </Link>
        <button
          onClick={() => window.print()}
          className="inline-flex items-center gap-1.5 rounded-md bg-brand-600 px-3 py-1.5 text-sm text-white hover:bg-brand-700"
        >
          <Printer className="h-4 w-4" />
          打印 / 存为 PDF
        </button>
      </div>

      {/* 抬头 */}
      <div className="border-b border-slate-300 pb-3">
        <h1 className="text-xl font-bold">家长会学生情况表</h1>
        <p className="mt-1 text-sm text-slate-600">
          {profile.person?.name ?? DASH}
          {subject ? ` · ${subject}` : ''}
          <span className="ml-3 text-slate-400">生成日期 {new Date().toISOString().slice(0, 10)}</span>
        </p>
      </div>

      {/* 成绩概况：当前学科历次 */}
      <section>
        <h2 className="mb-2 text-base font-semibold">
          一、{subject ?? '学科'}成绩概况
        </h2>
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-slate-500">
              <th className="py-1.5 font-normal">考试</th>
              <th className="py-1.5 font-normal text-right">原始分</th>
              <th className="py-1.5 font-normal text-right">等级分</th>
              <th className="py-1.5 font-normal text-right">教学班排名</th>
            </tr>
          </thead>
          <tbody>
            {trend.map((p, i) => (
              <tr key={`${p.exam_name}-${i}`} className="border-b border-slate-100">
                <td className="py-1.5">{p.exam_name}</td>
                <td className="py-1.5 text-right tabular-nums">{scoreText(p.score)}</td>
                <td className="py-1.5 text-right tabular-nums">{scoreText(p.grade_score)}</td>
                <td className="py-1.5 text-right tabular-nums">{p.rank != null ? String(p.rank) : DASH}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {trend.length === 0 && <p className="text-sm text-slate-400">暂无考试记录</p>}
        {latest && num(latest.grade_score ?? latest.score) != null && num(firstRanked?.grade_score ?? firstRanked?.score) != null && firstRanked != null && (
          <p className="mt-2 text-sm text-slate-600">
            {num(latest.grade_score) != null ? '等级分' : '原始分'}从{' '}
            {num(firstRanked.grade_score ?? firstRanked.score)} 变化到 {num(latest.grade_score ?? latest.score)}。
          </p>
        )}
        {latest && firstRanked != null && firstRanked.rank != null && latest.rank != null && latest !== firstRanked && (
          <p className="mt-1 text-sm text-slate-600">
            教学班排名从 {String(firstRanked.rank)} 变化到 {String(latest.rank)}
            （{firstRanked.rank >= latest.rank ? '前进' : '后退'} {String(Math.abs(firstRanked.rank - latest.rank))} 名）。
          </p>
        )}
      </section>

      {/* 作业 */}
      <section>
        <h2 className="mb-2 text-base font-semibold">二、作业完成情况</h2>
        {missingCount != null ? (
          <p className="text-sm text-slate-700">
            共缺交 <span className="font-semibold">{missingCount}</span> 次
            {excusedCount > 0 && `；请假 ${excusedCount} 次`}。
            <span className="text-slate-400">注：按收交事件逐次统计，仅含缺交/请假，不代表完成质量。</span>
          </p>
        ) : (
          <p className="text-sm text-slate-400">无作业记录</p>
        )}
      </section>

      {/* 谈话摘要 */}
      <section>
        <h2 className="mb-2 text-base font-semibold">三、近期沟通摘要</h2>
        {notes.length > 0 ? (
          <>
            <ul className="space-y-1.5 text-sm">
              {(notesExpanded ? notes : notes.slice(0, 4)).map((n) => (
                <li key={n.id} className="text-slate-700">
                  <span className="text-slate-400">{n.date}</span>{' '}
                  <span className="font-medium">[{n.category}]</span> {n.content}
                </li>
              ))}
            </ul>
            <MoreToggle
              hiddenCount={notes.length - 4}
              unit="条记录"
              expanded={notesExpanded}
              onToggle={() => setNotesExpanded((v) => !v)}
            />
          </>
        ) : (
          <p className="text-sm text-slate-400">暂无记录</p>
        )}
      </section>

      <div className="border-t border-slate-300 pt-3 text-xs text-slate-400 print:fixed print:bottom-2">
        本表由学情追踪系统生成，仅供家校沟通参考。
      </div>
    </div>
  )
}
