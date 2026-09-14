'use client'

/**
 * 缺交预警时间轴（契约 p5-homework.md §3，H03 红线）。
 *
 * 口径（响应 basis='events'）：按收交事件（作业布置日期）逐人统计缺交次数，
 * 不按日折算；仅缺交历史（无应交分母）的批次同样计入缺交次数，但提交率类
 * 指标本端点一律不算。current_streak 为 null 且 streak_basis='unknown' 时
 * 标注「连续性未知」（unknown 不冒充连续，也不断言已交），绝不显示成 0。
 */

import { useEffect, useRef, useState } from 'react'
import { AlertCircle, AlertTriangle, RefreshCw, Users } from 'lucide-react'

import {
  homeworkWarnings,
  type HomeworkScopeQuery,
  type HomeworkWarningsResponse,
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
}: {
  mode: WorkspaceMode
  scopeQ: HomeworkScopeQuery
  /** 教学工作台的任教学科（固定，只读）；班主任工作台可自由过滤。 */
  scopeSubject: string | null
  generation: number
}) {
  const teaching = mode === 'teaching'
  // 输入草稿与已应用值分离：点「查询」才发请求
  const [minMissingDraft, setMinMissingDraft] = useState('2')
  const [subjectDraft, setSubjectDraft] = useState('')
  const [applied, setApplied] = useState<{ minMissing: number; subject: string }>({
    minMissing: 2,
    subject: '',
  })

  const [data, setData] = useState<HomeworkWarningsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const reqRef = useRef(0)

  // 教学域 subject 固定任教学科：作用域解析完成前不发请求（空 subject 请求会被后端 422）
  const effectiveSubject = teaching ? (scopeSubject ?? '') : applied.subject

  useEffect(() => {
    const req = ++reqRef.current
    setData(null)
    setError(null)
    homeworkWarnings(mode, {
      ...scopeQ,
      min_missing: applied.minMissing,
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
  }, [mode, scopeQ, generation, nonce, applied.minMissing, effectiveSubject])

  function runQuery() {
    const n = Number(minMissingDraft)
    setApplied({ minMissing: Number.isFinite(n) && n >= 1 ? Math.floor(n) : 1, subject: subjectDraft })
    setNonce((v) => v + 1)
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
              value={minMissingDraft}
              onChange={(e) => setMinMissingDraft(e.target.value)}
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
            事件口径（basis={data?.basis ?? 'events'}）：按作业布置日期逐次统计，不按日折算；
            仅缺交历史（无应交分母）的批次同样计入缺交次数，但不参与提交率类指标。
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>缺交预警名单</CardTitle>
          <CardDescription>
            缺交次数达到下限的学生，按缺交次数降序；时间轴列出最近缺交事件（最多 5 条）。
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
                当前范围与下限（缺交 ≥ {String(data.min_missing)} 次）内暂无预警学生
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
                      <Badge variant="warning">连续缺交 {String(s.current_streak)} 次</Badge>
                    ) : null}
                    {s.streak_basis === 'unknown' ? (
                      <Badge variant="outline">连续性未知（其间存在未记录状态的作业）</Badge>
                    ) : null}
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    当前连续缺交：
                    {s.current_streak == null
                      ? '未知（streak_basis=unknown，不冒充连续，也不断言已交）'
                      : `${String(s.current_streak)} 次`}
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
                            {m.subject} · {m.homework_type}
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
            连续缺交按收交事件排序判定：已交/请假打断连续，未记录（unknown）中断连续段——
            连续性未知时绝不把未记录当作缺交或已交补齐。
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
