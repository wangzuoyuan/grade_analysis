'use client'

/**
 * 学生画像打印视图（契约 docs/contracts/p4-students.md §2.3/§4/§6）。
 *
 * GET /api/v1/homeroom/students/{person_id}/report：基本信息（person.aliases
 * 学号史/当期座号）+ 各科各场成绩 + 总分 + 档案摘要（notes_summary={count,recent}，
 * 本域可见档案，默认域隔离）。类型逐字段对齐后端响应（G05）。
 * 本页即班主任打印页，档案编辑区固定 mode='homeroom'（N01：只读写本域）。
 * 打印复用全局 @media print 基线（侧栏/顶栏/按钮/档案编辑区隐藏）；
 * 缺考显示「—」不转 0。越界（resource_out_of_scope 404）与加载失败进错误态，不白屏。
 */

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { ChevronLeft, Printer, Trash2 } from 'lucide-react'

import {
  createNote,
  deleteNote,
  getStudentReport,
  listStudentNotes,
  type StudentNote,
  type StudentReportResponse,
} from '@/lib/api-v1'
import { apiErrorMessage, isApiErrorCode } from '@/components/link/error-text'
import { useWorkspace } from '@/lib/workspace'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

const DASH = '—'

// 契约 §4 枚举（与后端 NOTE_CATEGORIES 一致）
const NOTE_CATEGORIES = ['谈话', '观察', '家访', '家长沟通', '奖惩', '其他'] as const

function fmtDate(v: string | null | undefined): string {
  return v ? v.slice(0, 10) : DASH
}

function fmtScore(v: number | null | undefined): string {
  if (v == null) return DASH
  return Number.isInteger(v) ? String(v) : v.toFixed(1)
}

/** 单科/总分的历场成绩表；分数列为右对齐数字。 */
function ScoreTable({
  title,
  rows,
}: {
  title: string
  rows: Array<{ exam_name: string; exam_date?: string | null; score: number | null }>
}) {
  return (
    <section>
      <h2 className="mb-2 text-base font-semibold">{title}</h2>
      {rows.length === 0 ? (
        <p className="text-sm text-slate-500">暂无成绩记录</p>
      ) : (
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-slate-500">
              <th className="py-1.5 font-normal">考试</th>
              <th className="py-1.5 font-normal">日期</th>
              <th className="py-1.5 text-right font-normal">分数</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e, i) => (
              <tr key={`${e.exam_name}-${String(i)}`} className="border-b border-slate-100">
                <td className="py-1.5">{e.exam_name}</td>
                <td className="py-1.5 text-slate-600">{fmtDate(e.exam_date)}</td>
                <td className="py-1.5 text-right tabular-nums">{fmtScore(e.score)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="mt-1 text-[10px] text-slate-400">缺考显示「—」，不计入均分；不同学年口径不直接比较。</p>
    </section>
  )
}

/**
 * 成长档案管理区（仅屏幕显示，打印隐藏）：全部档案列表 + 新增 + 删除。
 * 打印页固定班主任域（本页即班主任视图），N01 红线由后端按域隔离兜底。
 */
function NotesPanel({
  personId,
  onChanged,
}: {
  personId: string
  onChanged: () => void
}) {
  const [notes, setNotes] = useState<StudentNote[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [category, setCategory] = useState<string>('谈话')
  const [content, setContent] = useState('')
  const [followUp, setFollowUp] = useState('')

  const load = useCallback(() => {
    setNotes(null)
    setLoadError(null)
    listStudentNotes('homeroom', personId)
      .then((r) => setNotes(r.notes ?? []))
      .catch((err: unknown) => {
        setNotes([])
        setLoadError(apiErrorMessage(err))
      })
  }, [personId])

  useEffect(() => {
    load()
  }, [load])

  function resetForm() {
    setDate(new Date().toISOString().slice(0, 10))
    setCategory('谈话')
    setContent('')
    setFollowUp('')
  }

  async function submit() {
    if (content.trim() === '') {
      setFormError('请填写档案内容')
      return
    }
    setBusy(true)
    setFormError(null)
    setNotice(null)
    try {
      await createNote('homeroom', personId, {
        date,
        category,
        content: content.trim(),
        follow_up: followUp.trim() === '' ? undefined : followUp.trim(),
      })
      resetForm()
      setNotice('已记录档案')
      load()
      onChanged()
    } catch (err) {
      setFormError(apiErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  async function remove(noteId: number) {
    if (!window.confirm('删除这条档案记录？删除后不可恢复。')) return
    setBusy(true)
    setFormError(null)
    setNotice(null)
    try {
      await deleteNote('homeroom', noteId)
      setNotice('已删除档案')
      load()
      onChanged()
    } catch (err) {
      setFormError(apiErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="print:hidden">
      <h2 className="mb-2 text-base font-semibold">成长档案（班主任专用，不进入打印页）</h2>
      {loadError ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p role="alert" className="text-sm text-danger-500">
            {loadError}
          </p>
          <Button variant="outline" size="sm" onClick={load}>
            重试
          </Button>
        </div>
      ) : notes == null ? (
        <Skeleton className="h-16 w-full" />
      ) : notes.length === 0 ? (
        <p className="text-sm text-slate-500">暂无档案记录</p>
      ) : (
        <ul className="space-y-1.5 text-sm">
          {notes.map((n) => (
            <li key={String(n.id)} className="flex items-start justify-between gap-3 border-b border-slate-100 pb-1.5">
              <div>
                <span className="text-slate-400">{fmtDate(n.date)}</span>{' '}
                <span className="font-medium">[{n.category}]</span> {n.content}
                {n.follow_up ? (
                  <span className={n.follow_up_done ? 'ml-2 text-xs text-slate-400 line-through' : 'ml-2 text-xs text-warning-600'}>
                    跟进：{n.follow_up}
                  </span>
                ) : null}
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-slate-400 hover:text-danger-500"
                disabled={busy}
                onClick={() => void remove(n.id)}
                aria-label={`删除 ${fmtDate(n.date)} 的档案`}
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      {/* 新增档案：date/category/content/follow_up */}
      <div className="mt-4 flex flex-col gap-2 rounded-lg border border-slate-200 bg-slate-50/60 p-3 sm:flex-row sm:items-end">
        <div className="space-y-1">
          <label htmlFor="note-date" className="text-xs font-medium text-slate-500">
            日期
          </label>
          <Input id="note-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} className="h-9 w-40" disabled={busy} />
        </div>
        <div className="space-y-1">
          <label htmlFor="note-category" className="text-xs font-medium text-slate-500">
            类别
          </label>
          <Select value={category} onValueChange={setCategory} disabled={busy}>
            <SelectTrigger id="note-category" aria-label="档案类别" className="h-9 w-28">
              <SelectValue placeholder="类别" />
            </SelectTrigger>
            <SelectContent>
              {NOTE_CATEGORIES.map((c) => (
                <SelectItem key={c} value={c}>
                  {c}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <label htmlFor="note-content" className="text-xs font-medium text-slate-500">
            内容
          </label>
          <Input id="note-content" value={content} onChange={(e) => setContent(e.target.value)} placeholder="如：月考后状态波动，已谈心" className="h-9 w-64" maxLength={500} disabled={busy} />
        </div>
        <div className="space-y-1">
          <label htmlFor="note-follow-up" className="text-xs font-medium text-slate-500">
            跟进（可选）
          </label>
          <Input id="note-follow-up" value={followUp} onChange={(e) => setFollowUp(e.target.value)} placeholder="如：一周后回访" className="h-9 w-44" maxLength={200} disabled={busy} />
        </div>
        <Button size="sm" onClick={() => void submit()} disabled={busy}>
          {busy ? '提交中…' : '记录档案'}
        </Button>
      </div>

      {notice ? <p role="status" className="mt-2 text-xs text-success-600">{notice}</p> : null}
      {formError ? <p role="alert" className="mt-2 text-sm text-danger-500">{formError}</p> : null}
    </section>
  )
}

export default function StudentReportPrintPage() {
  const params = useParams<{ id: string }>()
  const personId = Array.isArray(params?.id) ? params?.id[0] : params?.id
  // 世代号：工作台范围刷新（如名册/共享变化）后重拉画像，避免打印到过期数据
  const { generation } = useWorkspace()

  const [report, setReport] = useState<StudentReportResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  const load = useCallback(() => {
    if (personId == null || personId === '') return
    setReport(null)
    setError(null)
    getStudentReport(personId)
      .then(setReport)
      .catch((err: unknown) => {
        setError(
          isApiErrorCode(err, 'resource_out_of_scope')
            ? '该学生不在班主任作用域内，无法查看画像'
            : apiErrorMessage(err),
        )
      })
  }, [personId])

  useEffect(() => {
    load()
  }, [load, nonce, generation])

  const roster = report?.roster ?? null
  // G05 修正：别名史在 person.aliases；档案摘要是 {count, recent} 对象
  const aliases = report?.person.aliases ?? []
  const recentNotes = report?.notes_summary.recent ?? []

  return (
    <div className="mx-auto max-w-3xl space-y-5 bg-white p-6 text-slate-900 print:p-0">
      {/* 顶部操作（打印时按钮被全局 @media print 隐藏） */}
      <div className="flex items-center justify-between print:hidden">
        <Link
          href="/homeroom/students"
          className="inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900"
        >
          <ChevronLeft className="h-4 w-4" aria-hidden="true" />
          返回学生管理
        </Link>
        <Button size="sm" onClick={() => window.print()}>
          <Printer className="h-4 w-4" aria-hidden="true" />
          打印 / 存为 PDF
        </Button>
      </div>

      {error ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-10 text-center">
            <p className="text-sm text-slate-600">{error}</p>
            <Button variant="outline" size="sm" onClick={() => setNonce((n) => n + 1)}>
              重试
            </Button>
          </CardContent>
        </Card>
      ) : report == null ? (
        <div className="space-y-3 py-4">
          <Skeleton className="h-10 w-1/2" />
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : (
        <>
          {/* 抬头 + 基本信息卡 */}
          <div className="border-b border-slate-300 pb-3">
            <h1 className="text-xl font-bold">学生情况一览</h1>
            <p className="mt-1 text-sm text-slate-600">
              {report.person.name ?? '（未命名）'}
              {roster?.class_label ? ` · ${roster.class_label}` : ''}
              {roster?.academic_year_name ? ` · ${roster.academic_year_name}` : ''}
              {roster?.seat_no != null ? ` · 座号 ${roster.seat_no}` : ''}
              <span className="ml-3 text-slate-400">生成日期 {new Date().toISOString().slice(0, 10)}</span>
            </p>
            {aliases.length > 0 ? (
              <p className="mt-1 text-xs text-slate-500">
                学号（别名）史：
                {aliases
                  .map((a) => `${a.alias_value}（${fmtDate(a.valid_from)} ~ ${a.valid_to == null ? '至今' : fmtDate(a.valid_to)}）`)
                  .join('、')}
              </p>
            ) : null}
          </div>

          {/* 各科历场成绩 */}
          {(report.subjects ?? []).map((s) => (
            <ScoreTable key={s.subject} title={s.subject} rows={s.exams} />
          ))}
          {(report.subjects ?? []).length === 0 ? (
            <p className="text-sm text-slate-500">暂无学科成绩记录</p>
          ) : null}

          {/* 总分（仅班主任域可见） */}
          {(report.totals ?? []).map((t) => (
            <ScoreTable key={t.total_type} title={`总分（${t.total_type}）`} rows={t.exams} />
          ))}

          {/* 档案摘要（最近 5 条；域隔离红线：仅本域可见档案进入摘要） */}
          <section>
            <h2 className="mb-2 text-base font-semibold">近期档案摘要</h2>
            {recentNotes.length === 0 ? (
              <p className="text-sm text-slate-500">暂无档案记录</p>
            ) : (
              <ul className="space-y-1.5 text-sm">
                {recentNotes.map((n) => (
                  <li key={String(n.id)} className="text-slate-700">
                    <span className="text-slate-400">{fmtDate(n.date)}</span>{' '}
                    <span className="font-medium">[{n.category}]</span> {n.content}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <div className="border-t border-slate-300 pt-3 text-xs text-slate-400 print:fixed print:bottom-2">
            本表由学情追踪系统生成，仅供家校沟通与班级管理参考。
          </div>

          {/* 成长档案编辑区：仅屏幕显示，档案新增/删除即时反映到上面的摘要 */}
          {personId != null && personId !== '' ? (
            <NotesPanel personId={personId} onChanged={() => setNonce((n) => n + 1)} />
          ) : null}
        </>
      )}
    </div>
  )
}
