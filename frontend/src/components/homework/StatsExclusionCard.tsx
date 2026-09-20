'use client'

/**
 * 统计排除卡（ADR-023，老教学版「花名册 · 排除统计」的合并版实现）。
 *
 * 语义：
 * - 打开排除 = 该生缺交不计入看板、排行与缺交预警；相关性、个人明细与
 *   批次明细仍保留其记录（用户 2026-09-15 裁定：相关性不排除）；
 * - 排除按班登记（班主任=行政班、教学=所选教学班），不跨班传播；
 * - 关闭 = 删除排除行，幂等；作业业务记录永不删除；
 * - 教学工作台未选具体班时显示引导（并集作用域无法定位单班名册）。
 */

import { useEffect, useRef, useState } from 'react'
import { AlertCircle, RefreshCw, UserX } from 'lucide-react'

import {
  homeworkStatsExclusion,
  homeworkStatsExclusionSet,
  type HomeworkStatsExclusionEntry,
  type HomeworkStatsExclusionResponse,
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
import { Skeleton } from '@/components/ui/skeleton'
import { type HomeworkScopeQuery } from '@/lib/api-v1'

export function StatsExclusionCard({
  mode,
  scopeQ,
  generation,
  hasTeachingClass,
}: {
  mode: WorkspaceMode
  /** 工作台作用域（学年/班），由 HomeworkWorkspace 从 filter 组装。 */
  scopeQ: HomeworkScopeQuery
  /** 工作台世代号：切换班/学年作废在途请求。 */
  generation: number
  /** 教学域是否已显式选择具体教学班（未选时本卡不可用）。 */
  hasTeachingClass: boolean
}) {
  const [data, setData] = useState<HomeworkStatsExclusionResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<PersonId | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const reqRef = useRef(0)

  useEffect(() => {
    if (mode === 'teaching' && !hasTeachingClass) {
      setData(null)
      setError(null)
      return
    }
    const req = ++reqRef.current
    setData(null)
    setError(null)
    setActionError(null)
    homeworkStatsExclusion(mode, scopeQ)
      .then((resp) => {
        if (req === reqRef.current) setData(resp)
      })
      .catch((err) => {
        if (req === reqRef.current) setError(apiErrorMessage(err))
      })
  }, [mode, scopeQ, generation, hasTeachingClass])

  async function toggle(entry: HomeworkStatsExclusionEntry) {
    setBusyId(entry.person_id)
    setActionError(null)
    try {
      const resp = await homeworkStatsExclusionSet({
        mode,
        ...scopeQ,
        person_id: entry.person_id,
        excluded: !entry.excluded,
      })
      setData(resp)
    } catch (err) {
      setActionError(apiErrorMessage(err))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <UserX className="h-4 w-4" /> 排除统计
        </CardTitle>
        <CardDescription>
          打开后，该学生的作业缺交不计入看板、排行与缺交预警；相关性、个人明细与批次明细仍完整保留。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {mode === 'teaching' && !hasTeachingClass ? (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            <p className="flex items-start gap-2">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              请先在页面上方选择一个具体教学班，再管理排除名单。
            </p>
          </div>
        ) : error != null ? (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            <p className="flex items-start gap-2">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              {error}
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="mt-2"
              onClick={() => {
                const req = ++reqRef.current
                setError(null)
                homeworkStatsExclusion(mode, scopeQ)
                  .then((resp) => {
                    if (req === reqRef.current) setData(resp)
                  })
                  .catch((err) => {
                    if (req === reqRef.current) setError(apiErrorMessage(err))
                  })
              }}
            >
              <RefreshCw className="h-4 w-4" /> 重试
            </Button>
          </div>
        ) : data == null ? (
          <Skeleton className="h-32 w-full" />
        ) : (
          <>
            <p className="text-xs text-slate-500">
              当前排除 {String(data.entries.filter((e) => e.excluded).length)} 人；
              开关只影响统计展示，不删除任何作业记录。
            </p>
            <div className="max-h-72 space-y-1 overflow-y-auto">
              {data.entries.map((entry) => (
                <div
                  key={String(entry.person_id)}
                  className="flex items-center justify-between gap-2 rounded-md border border-slate-100 px-2.5 py-1.5"
                >
                  <span className="min-w-0 truncate text-sm text-slate-700">
                    {entry.name ?? '（未命名）'}
                    {entry.alias ? <span className="ml-1 text-xs text-slate-400">{entry.alias}</span> : null}
                  </span>
                  <div className="flex shrink-0 items-center gap-2">
                    {entry.excluded ? <Badge variant="outline">已排除</Badge> : null}
                    <Button
                      type="button"
                      variant={entry.excluded ? 'outline' : 'ghost'}
                      size="sm"
                      disabled={busyId != null}
                      onClick={() => toggle(entry)}
                    >
                      {busyId === entry.person_id
                        ? '处理中…'
                        : entry.excluded
                          ? '恢复统计'
                          : '排除统计'}
                    </Button>
                  </div>
                </div>
              ))}
              {data.entries.length === 0 ? (
                <p className="py-4 text-center text-sm text-slate-400">当前班级没有在册学生</p>
              ) : null}
            </div>
          </>
        )}
        {actionError != null ? (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            <p className="flex items-start gap-2">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              {actionError}
            </p>
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
