import { HomeroomOverview } from './overview'
import { HomeroomActionSummaryCard, HomeroomActionSummaryProvider } from './action-summary'
import { HomeroomDiagnosisOverviewCard } from '@/components/student/DiagnosisCard'

export const metadata = {
  title: '班主任工作台 · 学情追踪',
}

export default function HomeroomPage() {
  return (
    <HomeroomActionSummaryProvider>
      <div className="space-y-6">
        {/* P2-C2 行动首页：优先关注列表置顶（姓名+理由+直达学生页），随后趋势/结构/作业/待办摘要 */}
        <HomeroomActionSummaryCard />
        <HomeroomOverview />
        {/* P1-B4 纵向切片：班级学情类型分布卡（与学生页诊断卡、AI 工具同一数据源） */}
        <HomeroomDiagnosisOverviewCard />
      </div>
    </HomeroomActionSummaryProvider>
  )
}
