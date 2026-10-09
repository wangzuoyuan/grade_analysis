'use client'

/**
 * P2-C3 诊断版学生报告（教学工作台入口，契约 docs/diagnosis-roadmap/p2-contracts.md §4）。
 *
 * 本页即教学域诊断报告页：mode 固定 'teaching'（仅任教学科口径，范围与
 * P1 一致——无总分行时总体类指标如实显示缺失，绝不跨域取数），与同目录
 * 事实版 report/page.tsx 并列；档案摘录遵守 N01 域隔离（只读教学域）。
 * 渲染逻辑与同源取数全部在 components/student/DiagnosisReport.tsx
 * （诊断报告页/打印组件，C3 独占）。入口：
 *   /student/[id]/diagnosis-report
 */

import { useParams } from 'next/navigation'

import { DiagnosisReportView } from '@/components/student/DiagnosisReport'

export default function TeachingDiagnosisReportPage() {
  const params = useParams<{ id: string }>()
  const rawId = Array.isArray(params?.id) ? params?.id[0] : params?.id
  const personId = rawId != null && /^\d+$/.test(rawId) ? Number(rawId) : null
  return <DiagnosisReportView mode="teaching" personId={personId} />
}
