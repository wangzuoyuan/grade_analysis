import { ResearchView } from '@/components/research/ResearchView'

export const metadata = {
  title: '关注回看 · 学情追踪',
}

/**
 * P3 关注回看（契约 docs/diagnosis-roadmap/p3-contracts.md §5）：
 * 按问题固定名单比较前后事实，或按每条跟进自身的基线复查。
 */
export default function HomeroomResearchPage() {
  return <ResearchView />
}
