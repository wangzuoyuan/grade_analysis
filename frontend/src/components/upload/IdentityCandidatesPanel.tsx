'use client'

/**
 * 身份候选确认面板（契约 p3-imports-analysis.md §1.1 v2.1，F06）。
 *
 * 历史学年 alias 命中绝不自动接续——无法区分同名新生/学号回收/跨届重号，
 * 必须逐别名显式选择「接续某个历史身份」或「新建学生」；存在未决候选时整批禁止确认。
 * 数据来源两处：preview items 的 identity_candidates，以及 confirm 409 体的
 * candidates（宽容读取），由页面归并后传入。
 */

import { AlertTriangle } from 'lucide-react'

import type { ImportIdentityCandidate } from '@/lib/api-v1'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

/** 一个待确认别名：文件内姓名 + 历史候选清单（已按 person_id 去重）。 */
export interface IdentityCandidateGroup {
  alias: string
  name: string
  options: ImportIdentityCandidate[]
}

/** 单个别名的选择结果：历史候选的 person_id（整数），或 'new' = 按新学生建档。 */
export type IdentityPick = number | 'new'

export function IdentityCandidatesPanel({
  groups,
  picks,
  onPick,
}: {
  groups: IdentityCandidateGroup[]
  picks: Record<string, IdentityPick>
  onPick: (alias: string, pick: IdentityPick) => void
}) {
  if (groups.length === 0) return null
  return (
    <Card className="border-warning-300 bg-warning-50/50 print:hidden">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <AlertTriangle className="h-4 w-4 text-warning-500" aria-hidden="true" />
          发现跨学年学号候选，请先确认身份接续
        </CardTitle>
        <CardDescription>
          以下学号命中了历史学年的学生档案。为防同名新生、学号回收或跨届重号混档，
          系统不会自动接续：请逐人为其选择「接续历史身份」或「新建学生」，全部选择后才能确认导入。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {groups.map((g) => {
          const picked = picks[g.alias]
          return (
            <div key={g.alias} className="rounded-lg border border-warning-200 bg-white px-3 py-2.5">
              <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
                <span className="font-medium text-slate-900">学号 {g.alias}</span>
                <span className="text-slate-500">姓名 {g.name || '—'}</span>
              </div>
              <div className="mt-1.5 space-y-1" role="radiogroup" aria-label={`学号 ${g.alias} 的身份确认`}>
                {g.options.map((c) => {
                  // 契约：*_id 均为整数；非整数的候选无法安全提交，直接不渲染该选项
                  const pid = typeof c.person_id === 'number' ? c.person_id : Number(c.person_id)
                  if (!Number.isFinite(pid)) return null
                  const checked = picked === pid
                  return (
                    <label key={String(pid)} className="flex cursor-pointer items-start gap-2 text-xs text-slate-600">
                      <input
                        type="radio"
                        name={`identity-${g.alias}`}
                        className="mt-0.5 accent-[#1f7fd6]"
                        checked={checked}
                        onChange={() => onPick(g.alias, pid)}
                      />
                      <span>
                        接续：<span className="font-medium text-slate-800">{c.name || '（无姓名）'}</span>
                        （{c.academic_year_name || '未知学年'}
                        {c.basis ? ` · 依据：${c.basis}` : ''}）
                      </span>
                    </label>
                  )
                })}
                <label className="flex cursor-pointer items-start gap-2 text-xs text-slate-600">
                  <input
                    type="radio"
                    name={`identity-${g.alias}`}
                    className="mt-0.5 accent-[#1f7fd6]"
                    checked={picked === 'new'}
                    onChange={() => onPick(g.alias, 'new')}
                  />
                  <span>新建学生（不接续任何历史档案）</span>
                </label>
              </div>
            </div>
          )
        })}
      </CardContent>
    </Card>
  )
}
