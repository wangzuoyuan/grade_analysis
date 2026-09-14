'use client'

/**
 * 作业跟进工作台容器（契约 p5-homework.md §7）。
 *
 * mode 感知：由页面路径前缀注入（/homeroom/homework → homeroom；/teaching/homework →
 * teaching），作用域参数从 useWorkspace 的 filter 映射，后端解析、空范围不退化。
 * 预警/相关性并入本页标签页（旧 /homework/warnings、/homework/correlation 路由被
 * 旧版页面占用，不删不改）；?tab= 是标签事实源（P8-UXFIX：同页深链/前进后退均
 * 跟随，点击标签 replace 回写；useSearchParams 需页面级 Suspense，见两个 page.tsx）。
 */

import { useEffect, useMemo, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'

import type { WorkspaceMode } from '@/lib/api-v1'
import { useWorkspace } from '@/lib/workspace'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { AssignmentTable } from './AssignmentTable'
import { CorrelationCard } from './CorrelationCard'
import { HomeworkEntryPanel } from './HomeworkEntryPanel'
import { WarningsPanel } from './WarningsPanel'
import { SemesterSettingsCard } from './SemesterSettingsCard'
import { homeworkDomainNote, homeworkScopeQuery } from './shared'

const TAB_VALUES = ['entry', 'batches', 'warnings', 'correlation', 'semesters'] as const
type TabValue = (typeof TAB_VALUES)[number]

function isTabValue(v: string | null): v is TabValue {
  return (TAB_VALUES as readonly string[]).includes(v ?? '')
}

export function HomeworkWorkspace({ mode }: { mode: WorkspaceMode }) {
  const { filter, generation, switching, scope } = useWorkspace()
  const scopeQ = useMemo(() => homeworkScopeQuery(filter), [filter])
  const scopeSubject = scope?.subject ?? null
  const searchParams = useSearchParams()
  const router = useRouter()

  const [tab, setTab] = useState<TabValue>('entry')
  const [entryNonce, setEntryNonce] = useState(0)

  // UX05：URL ?tab= 是标签唯一事实源——侧栏同页深链（如「缺交预警」）、前进/后退
  // 都跟随当前 query（缺失/非法回默认「作业录入」），而不是只在首挂载读一次。
  const tabParam = searchParams.get('tab')
  useEffect(() => {
    setTab(isTabValue(tabParam) ? tabParam : 'entry')
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
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">作业跟进</h1>
        <p className="text-sm text-slate-500">
          {domainNote}
          {switching ? ' · 正在切换…' : ''}
        </p>
      </div>

      <Tabs value={tab} onValueChange={selectTab}>
        <TabsList className="flex-wrap print:hidden">
          <TabsTrigger value="entry">作业录入</TabsTrigger>
          <TabsTrigger value="batches">批次列表</TabsTrigger>
          <TabsTrigger value="warnings">缺交预警</TabsTrigger>
          <TabsTrigger value="correlation">相关性</TabsTrigger>
          <TabsTrigger value="semesters">学期设置</TabsTrigger>
        </TabsList>
        <TabsContent value="entry">
          {/* 录入确认成功后 bump 列表信号：批次/预警等由各自的资源序号自行重拉 */}
          <HomeworkEntryPanel
            mode={mode}
            scopeSubject={scopeSubject}
            teachingClassId={typeof filter.teaching_class_id === 'number' ? filter.teaching_class_id : undefined}
            onConfirmed={() => setEntryNonce((n) => n + 1)}
          />
        </TabsContent>
        <TabsContent value="batches">
          <AssignmentTable mode={mode} scopeQ={scopeQ} generation={generation} refreshKey={entryNonce} />
        </TabsContent>
        <TabsContent value="warnings">
          <WarningsPanel mode={mode} scopeQ={scopeQ} scopeSubject={scopeSubject} generation={generation} />
        </TabsContent>
        <TabsContent value="correlation">
          <CorrelationCard mode={mode} scopeQ={scopeQ} scopeSubject={scopeSubject} generation={generation} />
        </TabsContent>
        <TabsContent value="semesters">
          <SemesterSettingsCard academicYearId={filter.academic_year_id} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
