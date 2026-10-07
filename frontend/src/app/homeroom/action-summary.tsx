'use client'

/**
 * P2-C2 教师行动首页卡（契约 docs/diagnosis-roadmap/p2-contracts.md §3）。
 *
 * 同源：全部数据 = GET /api/v1/homeroom/diagnosis/action-summary（后端从
 * B1 class_features + B2 classify_student 生成，前端绝不复算任何指标、
 * 类型或排序——只做契约 JSON 形状的展示）。
 *
 * 版式（契约 §3）：优先关注列表置顶（姓名 + 理由 + 直达学生页），随后
 * 趋势 / 结构 / 作业 / 待办四段摘要。
 *
 * 缺失纪律（契约 §0.3 / P1 §0）：数据缺失态（端点未部署/请求失败/范围无
 * 学生）如实显示，绝不伪造空分布或 0 填充；优先关注为空时如实说明
 * 「当前无对应类型学生」。待办 due_this_week 按计划复查日（review_date
 * 缺省回落档案日期，2026-09-29 起生效）落在近 7 天窗口的未关闭跟进计数
 * （后端 action.py 同一口径注释）。
 */

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { CardFoldToggle, useCardFold } from '@/components/dashboard/card-fold'
import { MoreToggle } from '@/components/ui/more-toggle'
import {
  Activity,
  AlertTriangle,
  ClipboardList,
  LayoutDashboard,
  ListChecks,
  TrendingUp,
} from 'lucide-react'

import type { WorkspaceMode } from '@/lib/api-v1'
import { useWorkspace } from '@/lib/workspace'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

/* ------------------------------------------------------------------ */
/* 契约 §3 JSON 形状                                                    */
/* ------------------------------------------------------------------ */

/** GET /api/v1/homeroom/diagnosis/action-summary 的响应（契约 §3）。 */
export interface ActionSummary {
  calc_version: string
  as_of: string
  priority_persons: Array<{
    person_id: number
    /** 契约形状的追加键：B1 class_features 同源名单，供「姓名+理由」展示。 */
    name: string | null
    reasons: string[]
    evidence_ref: { types: boolean; features: boolean }
  }>
  sections: {
    trend_changes: { improving_n: number; declining_n: number }
    structure: { band_counts: { high_score: number; critical: number; weak: number } }
    homework: { risk_n: number; missing_30d_total: number }
    follow_ups: { open_n: number; due_this_week: number }
  }
}

export const ACTION_SUMMARY_ENDPOINT = '/api/v1/homeroom/diagnosis/action-summary'

/** 取数助手（同源唯一入口；失败抛错由调用方如实显示缺失态）。 */
export async function fetchActionSummary(
  q: { academic_year_id?: number; class_id?: number } = {},
): Promise<ActionSummary> {
  const params = new URLSearchParams()
  if (typeof q.academic_year_id === 'number') params.set('academic_year_id', String(q.academic_year_id))
  if (typeof q.class_id === 'number') params.set('class_id', String(q.class_id))
  const res = await fetch(`${ACTION_SUMMARY_ENDPOINT}?${params.toString()}`, {
    headers: { Accept: 'application/json' },
  })
  const body = (await res.json().catch(() => null)) as Record<string, unknown> | null
  if (!res.ok) {
    throw new Error(
      typeof body?.detail === 'string' ? body.detail : `行动摘要请求失败（HTTP ${res.status}）`,
    )
  }
  return body as unknown as ActionSummary
}

/* ------------------------------------------------------------------ */
/* 四段摘要行（纯函数，输入=契约 §3 sections，便于契约测试直测）        */
/* ------------------------------------------------------------------ */

export interface ActionSectionRow {
  key: 'trend_changes' | 'structure' | 'homework' | 'follow_ups'
  label: string
  value: string
}

const DASH = '—'

export function actionSectionRows(sections: ActionSummary['sections'] | null): ActionSectionRow[] {
  if (!sections) {
    return (
      [
        ['trend_changes', '趋势'],
        ['structure', '结构'],
        ['homework', '作业'],
        ['follow_ups', '待办'],
      ] as Array<[ActionSectionRow['key'], string]>
    ).map(([key, label]) => ({ key, label, value: DASH }))
  }
  const trend = sections.trend_changes
  const bands = sections.structure.band_counts
  const homework = sections.homework
  const follow = sections.follow_ups
  return [
    {
      key: 'trend_changes',
      label: '趋势',
      value: `进步 ${trend.improving_n} 人 · 退步 ${trend.declining_n} 人（近方向）`,
    },
    {
      key: 'structure',
      label: '结构',
      value: `高分段 ${bands.high_score} · 临界 ${bands.critical} · 薄弱 ${bands.weak}`,
    },
    {
      key: 'homework',
      label: '作业',
      value: `作业风险 ${homework.risk_n} 人 · 30 天缺交合计 ${homework.missing_30d_total} 次`,
    },
    {
      key: 'follow_ups',
      label: '待办',
      value: `未关闭跟进 ${follow.open_n} 项 · 本周关注 ${follow.due_this_week} 项`,
    },
  ]
}

/* ------------------------------------------------------------------ */
/* 首页行动卡                                                          */
/* ------------------------------------------------------------------ */

function ActionSummarySkeleton() {
  return (
    <Card>
      <CardHeader>
        <Skeleton className="h-5 w-44" />
        <Skeleton className="h-3 w-72" />
      </CardHeader>
      <CardContent className="space-y-3">
        <Skeleton className="h-10 w-full" />
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

export function HomeroomActionSummaryCard() {
  const { mode, filter, scope, generation } = useWorkspace()
  const [summary, setSummary] = useState<ActionSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const reqRef = useRef(0)

  useEffect(() => {
    if (mode !== 'homeroom') return
    const req = ++reqRef.current
    setLoading(true)
    setError(null)
    setSummary(null)
    const q: { academic_year_id?: number; class_id?: number } = {}
    if (typeof filter.academic_year_id === 'number') q.academic_year_id = filter.academic_year_id
    if (typeof filter.class_id === 'number') q.class_id = filter.class_id
    fetchActionSummary(q)
      .then((data) => {
        if (req !== reqRef.current) return
        setSummary(data)
        setLoading(false)
      })
      .catch((reason: unknown) => {
        if (req !== reqRef.current) return
        setError(reason instanceof Error ? reason.message : '行动摘要加载失败')
        setLoading(false)
      })
    return () => {
      reqRef.current += 1
    }
  }, [mode, filter.academic_year_id, filter.class_id, scope, generation])

  if (mode !== 'homeroom') return null

  const rows = actionSectionRows(summary?.sections ?? null)
  const priority = summary?.priority_persons ?? []
  const cohortSize = scope?.member_person_ids?.length ?? 0
  // 卡片级折叠（状态本地记忆）；优先关注列表默认 4 条，超出走 MoreToggle
  const { folded, toggle } = useCardFold('homeroom:action-summary')
  const [priorityExpanded, setPriorityExpanded] = useState(false)

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div>
          <CardTitle className="flex items-center gap-2">
            <LayoutDashboard className="h-4 w-4 text-brand-600" />
            行动首页 · 优先关注与摘要
          </CardTitle>
          <CardDescription
            title={summary?.calc_version ? `口径版本 ${summary.calc_version}` : undefined}
          >
            优先关注 + 趋势/结构/作业/待办摘要（与学生页诊断卡、AI 助手同一数据源）
            {summary?.as_of ? ` · 截至 ${summary.as_of}` : ''}
          </CardDescription>
        </div>
        <CardFoldToggle folded={folded} onToggle={toggle} />
      </CardHeader>
      {folded ? null : (
      <CardContent className="space-y-4">
        {loading ? (
          <ActionSummarySkeleton />
        ) : error || !summary ? (
          /* 数据缺失态如实显示：端点未部署/请求失败，不伪造任何摘要 */
          <div className="flex flex-col items-start gap-1.5 rounded-lg border border-dashed border-slate-200 bg-slate-50 px-4 py-6">
            <span className="inline-flex items-center gap-1.5 text-sm text-slate-600">
              <AlertTriangle className="h-4 w-4" />
              行动摘要暂不可用（数据缺失）
            </span>
            {error && <span className="text-xs text-slate-400">{error}</span>}
            <span className="text-xs text-slate-400">
              行动摘要端点（/diagnosis/action-summary）尚未部署或当前范围暂无数据；不做估算填充。
            </span>
          </div>
        ) : cohortSize === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center">
            <p className="text-sm text-slate-500">当前范围暂无学生，无法生成行动摘要</p>
            <p className="mt-1 text-xs text-slate-400">数据缺失态如实显示，不做任何估算。</p>
          </div>
        ) : (
          <>
            {/* 优先关注（置顶）：姓名 + 理由 + 直达学生页 */}
            <div>
              <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-slate-500">
                <ListChecks className="h-3.5 w-3.5" />
                优先关注（{priority.length} 人）
              </div>
              {priority.length === 0 ? (
                <div className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
                  当前无综合风险/持续下滑/临界下滑/作业风险学生
                </div>
              ) : (
                <ul className="space-y-1.5">
                  {(priorityExpanded ? priority : priority.slice(0, 4)).map((item) => (
                    <li
                      key={item.person_id}
                      className="flex flex-wrap items-baseline gap-x-2 gap-y-1 rounded-lg bg-amber-50 px-3 py-2 text-xs"
                    >
                      <Link
                        href={`/homeroom/students/${encodeURIComponent(String(item.person_id))}/report`}
                        className="font-medium text-amber-900 underline-offset-2 hover:underline"
                      >
                        {item.name ?? `学生 ${item.person_id}`}
                      </Link>
                      {item.reasons.map((reason) => (
                        <span key={reason} className="text-amber-800">
                          {reason}
                        </span>
                      ))}
                      <Link
                        href={`/homeroom/students/${encodeURIComponent(String(item.person_id))}/report`}
                        className="ml-auto inline-flex items-center gap-0.5 whitespace-nowrap text-[11px] text-amber-700 underline-offset-2 hover:underline"
                      >
                        查看档案
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
              {priority.length > 4 ? (
                <MoreToggle
                  hiddenCount={priority.length - 4}
                  unit="人"
                  expanded={priorityExpanded}
                  onToggle={() => setPriorityExpanded((v) => !v)}
                />
              ) : null}
            </div>

            {/* 趋势 / 结构 / 作业 / 待办四段摘要 */}
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              {rows.map((row) => (
                <div key={row.key} className="rounded-lg border border-slate-100 px-3 py-2.5">
                  <div className="flex items-center gap-1.5 text-xs font-medium text-slate-500">
                    {row.key === 'trend_changes' ? (
                      <TrendingUp className="h-3.5 w-3.5" />
                    ) : row.key === 'structure' ? (
                      <Activity className="h-3.5 w-3.5" />
                    ) : (
                      <ClipboardList className="h-3.5 w-3.5" />
                    )}
                    {row.label}
                  </div>
                  <div className="mt-1 text-sm font-medium text-slate-800" title={row.value}>
                    {row.value}
                  </div>
                </div>
              ))}
            </div>

            <p className="text-[11px] text-slate-400">
              摘要与优先关注由班级诊断特征（B1）与学生类型（B2）同源生成；排序：综合风险 &gt; 持续下滑
              &gt; 临界下滑 &gt; 作业风险 &gt; 短期下滑。「本周关注」为近 7 天记录且未关闭的跟进（到期日字段上线前按记录日近似）。
              相关性观察与风险标注不构成因果或提分保证。
            </p>
          </>
        )}
      </CardContent>
      )}
    </Card>
  )
}
