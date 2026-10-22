'use client'

/**
 * 班级对比（教学工作台，官方质量分析主视图）。
 *
 * 教学模式渲染 TeachingPlatformCompare：按考试班级快照展示官方质量分析
 * （2026-10-22 起行政班与教学班合并为单卡，附各班平均分柱状图，突出
 * 我教的班级）。行政班全科对比在班主任工作台 /homeroom/compare（官方
 * 班级均分表口径），homeroom 模式进入本页时直接跳转过去，跳转期间只
 * 渲染极简加载态，不发起任何教学域业务请求（R03）。
 *
 * 旧「各教学班样本均分」对比视图已于 2026-10-22 退役删除；class-compare
 * 与 stats 两条取数仍供仪表盘（WorkspaceDataDashboard）与成绩分析页
 * （TeachingScores）使用，不在本页。
 */

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'

import { useWorkspace } from '@/lib/workspace'
import { TeachingPlatformCompare } from '@/components/scores/TeachingPlatformCompare'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

export default function ComparePage() {
  const { mode } = useWorkspace()
  const router = useRouter()

  // 班主任工作台：此页的教学班对比属教学工作台；homeroom 模式直达
  // 班主任「班级对比」页（同年级各行政班均分，官方班级均分表口径）
  useEffect(() => {
    if (mode === 'homeroom') router.replace('/homeroom/compare')
  }, [mode, router])

  // 跳转期间渲染极简加载态（不发起任何教学域数据请求）
  if (mode === 'homeroom') {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">班级对比</h1>
          <p className="mt-1 text-sm text-slate-500">正在前往班主任「班级对比」…</p>
        </div>
        <Card>
          <CardContent className="space-y-2 py-6">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-40 w-full" />
          </CardContent>
        </Card>
      </div>
    )
  }

  return <TeachingPlatformCompare />
}
