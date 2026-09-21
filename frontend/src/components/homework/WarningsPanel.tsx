'use client'

/**
 * 缺交预警时间轴（契约 p5-homework.md §3，H03 红线）。
 *
 * 口径（响应 basis='events'）：按收交事件（作业布置日期）逐人统计缺交次数，
 * 不按日折算；旧缺交记录与旧全交收交台账共同构成可证明的历史时间轴。
 * 连续口径按天（同日多种作业缺只计 1 天）：班主任按学科、教学按天
 * （不分作业种类）；无缺交/请假例外的批次按已交，会中断此前的连续缺交。
 */

import { useEffect, useRef, useState } from 'react'
import { AlertCircle, AlertTriangle, RefreshCw, Users } from 'lucide-react'

import {
  dismissHomeworkWarning,
  homeworkWarnings,
  type HomeworkScopeQuery,
  type HomeworkWarningsResponse,
  type PersonId,
  type WorkspaceMode,
} from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'

export function WarningsPanel({
  mode,
  scopeQ,
  scopeSubject,
  generation,
  refreshKey = 0,
}: {
  mode: WorkspaceMode
  scopeQ: HomeworkScopeQuery
  /** 教学工作台的任教学科（固定，只读）；班主任工作台可自由过滤。 */
  scopeSubject: string | null
  generation: number
  /** 录入确认后的统一刷新信号。 */
  refreshKey?: number
}) {
  const teaching = mode === 'teaching'
  // 输入草稿与已应用值分离：点「查询」才发请求
  const [minStreakDraft, setMinStreakDraft] = useState('2')
  const [subjectDraft, setSubjectDraft] = useState('')
  const [applied, setApplied] = useState<{ minStreak: number; subject: string }>({
    minStreak: 2,
    subject: '',
  })

  const [data, setData] = useState<HomeworkWarningsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const [dismissingId, setDismissingId] = useState<PersonId | null>(null)
  const reqRef = useRef(0)

  // 教学域 subject 固定任教学科：作用域解析完成前不发请求（空 subject 请求会被后端 422）
  const effectiveSubject = teaching ? (scopeSubject ?? '') : applied.subject

  useEffect(() => {
    const req = ++reqRef.current
    setData(null)
    setError(null)
    homeworkWarnings(mode, {
      ...scopeQ,
      min_missing: 1,
      min_streak: applied.minStreak,
      subject: effectiveSubject !== '' ? effectiveSubject : undefined,
    })
      .then((r) => {
        if (req === reqRef.current) setData(r)
      })
      .catch((err: unknown) => {
        if (req !== reqRef.current) return
        setData(null)
        setError(apiErrorMessage(err))
      })
  }, [mode, scopeQ, generation, refreshKey, nonce, applied.minStreak, effectiveSubject])

  function runQuery() {
    const n = Number(minStreakDraft)
    setApplied({ minStreak: Number.isFinite(n) && n >= 1 ? Math.floor(n) : 1, subject: subjectDraft })
    setNonce((v) => v + 1)
  }

  async function handleDismissQuality(personId: PersonId) {
    setDismissingId(personId)
    try {
      await dismissHomeworkWarning({
        mode,
        ...scopeQ,
        subject: effectiveSubject !== '' ? effectiveSubject : undefined,
        person_id: Number(personId),
        warning_kind: 'quality',
      })
      setData((prev) => {
        if (!prev) return prev
        return {
          ...prev,
          quality: (prev.quality ?? []).filter((q) => String(q.person_id) !== String(personId)),
        }
      })
    } catch (err) {
      alert(apiErrorMessage(err))
    } finally {
      setDismissingId(null)
    }
  }

  return (
    <div className="space-y-4">
      <Card className="print:hidden">
        <CardContent className="flex flex-col gap-3 py-4 sm:flex-row sm:items-end">
          <div className="space-y-1">
            <label className="text-xs font-medium text-slate-500" htmlFor="hw-warn-min">
              连续缺交下限（次）
            </label>
            <Input
              id="hw-warn-min"
              type="number"
              min={1}
              value={minStreakDraft}
              onChange={(e) => setMinStreakDraft(e.target.value)}
              className="sm:w-40"
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-slate-500" htmlFor="hw-warn-subject">
              学科{teaching ? '（任教学科，固定）' : ''}
            </label>
            <Input
              id="hw-warn-subject"
              value={teaching ? (scopeSubject ?? '解析中…') : subjectDraft}
              onChange={(e) => setSubjectDraft(e.target.value)}
              disabled={teaching}
              placeholder={teaching ? undefined : '全部学科'}
              className="sm:w-40"
            />
          </div>
          <Button type="button" size="sm" onClick={runQuery}>
            <RefreshCw className="h-4 w-4" /> 查询
          </Button>
          <p className="text-xs text-slate-400 sm:ml-auto sm:max-w-xs">
            缺交、负面评价和忘带分别统计；请假跳过，其他没有缺交记录的批次按已交处理。
          </p>
        </CardContent>
      </Card>

      {data != null ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>连续负面评价</CardTitle>
              <CardDescription>已交作业仍可同时记录质量问题；连续 2 次起提醒。跟进后可手动解除。</CardDescription>
            </CardHeader>
            <CardContent>
              {(data.quality ?? []).length === 0 ? (
                <p className="text-sm text-slate-500">暂无连续负面评价。</p>
              ) : (
                <ul className="space-y-2">
                  {(data.quality ?? []).map((item) => (
                    <li
                      key={String(item.person_id)}
                      className="flex items-start justify-between gap-2 rounded-lg bg-rose-50 px-3 py-2 text-sm"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-rose-900">{item.name ?? '（未命名）'}</span>
                          <span className="text-xs text-rose-700">连续 {String(item.count)} 次</span>
                        </div>
                        <p className="mt-1 text-xs text-rose-600">
                          {item.dates.join('、')} · {item.details.join('；')}
                        </p>
                      </div>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="h-7 shrink-0 border-rose-200 bg-white text-xs text-rose-700 hover:bg-rose-100 hover:text-rose-900"
                        disabled={dismissingId === item.person_id}
                        onClick={() => handleDismissQuality(item.person_id)}
                      >
                        {dismissingId === item.person_id ? '处理中…' : '已跟进解除'}
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>忘带与出勤预警</CardTitle>
              <CardDescription>记录忘带与迟到、没来等出勤异常情况，近 30 天累计 3 次起提醒。</CardDescription>
            </CardHeader>
            <CardContent>
              {(data.forgot ?? []).length === 0 ? (
                <p className="text-sm text-slate-500">暂无忘带或迟到、没来达到 3 次的学生。</p>
              ) : (
                <ul className="space-y-2">
                  {(data.forgot ?? []).map((item) => (
                    <li key={String(item.person_id)} className="rounded-lg bg-amber-50 px-3 py-2 text-sm">
                      <div className="flex items-center justify-between">
                        <div>
                          <span className="font-medium text-amber-900">{item.name ?? '（未命名）'}</span>
                          <span className="ml-2 text-amber-700">{String(item.count)} 次</span>
                        </div>
                      </div>
                      <p className="mt-1 text-xs text-amber-600">
                        {item.dates.join('、')}{item.details && item.details.length > 0 ? ` · ${item.details.join('；')}` : ''}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>缺交预警名单</CardTitle>
          <CardDescription>
            当前连续缺交达到下限的学生，按累计缺交次数降序；时间轴列出最近缺交事件（最多 5 条）。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {error ? (
            <div className="flex flex-col items-center gap-3 py-8 text-center">
              <AlertCircle className="h-8 w-8 text-amber-400" />
              <p className="text-sm text-slate-600">{error}</p>
              <Button variant="outline" size="sm" onClick={() => setNonce((v) => v + 1)}>
                重试
              </Button>
            </div>
          ) : data == null ? (
            <div className="space-y-2">
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-20 w-2/3" />
            </div>
          ) : data.students.length === 0 ? (
            <div className="py-8 text-center">
              <Users className="mx-auto h-8 w-8 text-slate-300" aria-hidden="true" />
              <p className="mt-2 text-sm text-slate-600">
                当前范围内暂无连续缺交 ≥ {String(data.min_streak ?? applied.minStreak)} 次的学生；其他预警请看上方分类
              </p>
              <p className="mt-1 text-xs text-slate-400">空范围不会扩展到其他班级或全年级。</p>
            </div>
          ) : (
            <ul className="space-y-3">
              {data.students.map((s) => (
                <li key={String(s.person_id)} className="rounded-lg border border-slate-200 bg-white px-4 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-slate-900">{s.name ?? '（未命名）'}</span>
                    <Badge variant="destructive">缺交 {String(s.missing_count)} 次</Badge>
                    {s.current_streak != null ? (
                      <Badge variant="warning">
                        {s.streak_subject ?? s.streak_homework_type ?? ''}
                        {(s.streak_subject ?? s.streak_homework_type) ? ' · ' : ''}
                        连续缺交 {String(s.current_streak)} 次
                      </Badge>
                    ) : null}
                  </div>
                  <p
                    className="mt-1 text-xs text-slate-500"
                    title={s.streak_basis === 'legacy_events' ? '依据历史已保留时间轴推算' : undefined}
                  >
                    当前连续缺交：
                    {`${String(s.current_streak ?? 0)} 次（${
                      s.streak_subject
                        ? `按${s.streak_subject}学科`
                        : '按天（不分作业种类）'
                    }）`}
                  </p>
                  {s.recent_missing.length > 0 ? (
                    <div className="mt-2">
                      <p className="text-[10px] font-medium uppercase tracking-wide text-slate-400">
                        最近缺交时间轴
                      </p>
                      <ol className="mt-1 space-y-0.5 border-l-2 border-slate-100 pl-3">
                        {s.recent_missing.map((m) => (
                          <li key={m.assignment_id} className="text-xs text-slate-600">
                            <span className="tabular-nums text-slate-500">{m.assigned_date}</span>
                            {' · '}
                            {m.subject}
                            {m.homework_type &&
                            m.homework_type !== '日常作业' &&
                            m.homework_type !== 'legacy'
                              ? ` · ${m.homework_type}`
                              : ''}
                          </li>
                        ))}
                      </ol>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 flex items-start gap-1.5 text-[10px] text-slate-400">
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
            连续缺交按天判定（同日多种作业缺只计 1 天）：班主任按学科、教学按天（不分作业种类）；
            已交打断，请假跳过。旧数据的全交收交台账会用于中断对应学生的缺交连续段。
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
