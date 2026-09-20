'use client'

/**
 * 作业跟进工作台容器（契约 p5-homework.md §7）。
 *
 * mode 感知：由页面路径前缀注入（/homeroom/homework → homeroom；/teaching/homework →
 * teaching），作用域参数从 useWorkspace 的 filter 映射，后端解析、空范围不退化。
 * 预警和按期汇总合并到“作业管理”，录入保留独立页签；旧 warnings 深链兼容映射到管理。
 * ?tab= 是标签事实源（P8-UXFIX：同页深链/前进后退均跟随，点击标签 replace 回写；
 * useSearchParams 需页面级 Suspense，见两个 page.tsx）。
 */

import { useEffect, useMemo, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'

import type { WorkspaceMode } from '@/lib/api-v1'
import { useWorkspace } from '@/lib/workspace'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { AssignmentTable } from './AssignmentTable'
import { HomeworkEntryPanel } from './HomeworkEntryPanel'
import { StatsExclusionCard } from './StatsExclusionCard'
import { WarningsPanel } from './WarningsPanel'
import { homeworkDomainNote, homeworkScopeQuery } from './shared'
import { ClassScopePicker } from '@/components/ClassScopePicker'

const TAB_VALUES = ['overview', 'entry', 'batches'] as const
type TabValue = (typeof TAB_VALUES)[number]

function isTabValue(v: string | null): v is TabValue {
  return (TAB_VALUES as readonly string[]).includes(v ?? '')
}

function tabFromParam(v: string | null): TabValue {
  if (v === 'warnings') return 'overview'
  return isTabValue(v) ? v : 'batches'
}

export function HomeworkWorkspace({ mode }: { mode: WorkspaceMode }) {
  const { filter, generation, switching, scope } = useWorkspace()
  const scopeQ = useMemo(() => homeworkScopeQuery(filter), [filter])
  const scopeSubject = scope?.subject ?? null
  const searchParams = useSearchParams()
  const router = useRouter()

  const [tab, setTab] = useState<TabValue>('batches')
  const [entryNonce, setEntryNonce] = useState(0)

  // UX05：URL ?tab= 是标签唯一事实源。旧 warnings 深链进入合并后的“作业管理”；
  // entry 仍直达独立录入页签，缺失/非法值回到默认“作业记录”。
  const tabParam = searchParams.get('tab')
  useEffect(() => {
    setTab(tabFromParam(tabParam))
  }, [tabParam])

  // 点击标签回写 URL（replace 不堆历史），保证分享/刷新与选中态一致；
  // 保留其余既有参数，草稿在 sessionStorage（S07），切标签不丢。
  function selectTab(v: string) {
    if (!isTabValue(v)) return
    setTab(v)
    const params = new URLSearchParams(searchParams.toString())
    params.set('tab', v)
    router.replace(`?${params.toString()}`, { scroll: false })
  }

  const domainNote = homeworkDomainNote(mode, scopeSubject)

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">作业跟进</h1>
          <p className="text-sm text-slate-500">
            {domainNote}
            {switching ? ' · 正在切换…' : ''}
          </p>
        </div>
        {mode === 'teaching' ? (
          <div className="print:hidden">
            <label className="mb-1 block text-xs font-medium text-slate-500">录入教学班</label>
            <ClassScopePicker className="w-[220px]" />
            {typeof filter.teaching_class_id !== 'number' ? (
              <p className="mt-1 text-[11px] text-amber-600">录入前请选择具体班级</p>
            ) : null}
          </div>
        ) : null}
      </div>

      <Tabs value={tab} onValueChange={selectTab}>
        <TabsList className="flex-wrap print:hidden">
          <TabsTrigger value="batches">作业记录</TabsTrigger>
          <TabsTrigger value="overview">作业管理</TabsTrigger>
          <TabsTrigger value="entry">作业录入</TabsTrigger>
        </TabsList>
        <TabsContent value="overview">
          <div className="space-y-6">
            <WarningsPanel mode={mode} scopeQ={scopeQ} scopeSubject={scopeSubject} generation={generation} refreshKey={entryNonce} />
            <AssignmentTable mode={mode} scopeQ={scopeQ} generation={generation} refreshKey={entryNonce} summaryOnly />
          </div>
        </TabsContent>
        <TabsContent value="entry">
          {/* 录入确认成功后刷新记录、预警与汇总。 */}
          <HomeworkEntryPanel
            mode={mode}
            scopeSubject={scopeSubject}
            teachingClassId={typeof filter.teaching_class_id === 'number' ? filter.teaching_class_id : undefined}
            academicYearId={typeof filter.academic_year_id === 'number' ? filter.academic_year_id : undefined}
            onConfirmed={() => setEntryNonce((n) => n + 1)}
          />
        </TabsContent>
        <TabsContent value="batches">
          <div className="space-y-6">
            <AssignmentTable mode={mode} scopeQ={scopeQ} generation={generation} refreshKey={entryNonce} hideSummary />
            {/* ADR-023 排除统计：缺交不计入看板/排行/预警；相关性与明细保留 */}
            <StatsExclusionCard
              mode={mode}
              scopeQ={scopeQ}
              generation={generation}
              hasTeachingClass={typeof filter.teaching_class_id === 'number'}
            />
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}
