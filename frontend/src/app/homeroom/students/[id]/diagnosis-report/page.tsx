'use client'

/**
 * P2-C3 诊断版学生报告（班主任工作台入口，契约 docs/diagnosis-roadmap/p2-contracts.md §4）。
 *
 * 本页即班主任域诊断报告页：mode 固定 'homeroom'（本班全科+总分口径，
 * 与同目录事实版 report/page.tsx 的「本页即班主任打印页」先例一致），
 * 档案摘录遵守 N01 域隔离。渲染逻辑与同源取数全部在
 * components/student/DiagnosisReport.tsx（诊断报告页/打印组件，C3 独占）。
 * 事实版报告页保留不动，本页为并列入口：
 *   /homeroom/students/[id]/diagnosis-report
 */

import { useParams } from 'next/navigation'

import { DiagnosisReportView } from '@/components/student/DiagnosisReport'

export default function HomeroomDiagnosisReportPage() {
  const params = useParams<{ id: string }>()
  const rawId = Array.isArray(params?.id) ? params?.id[0] : params?.id
  const personId = rawId != null && /^\d+$/.test(rawId) ? Number(rawId) : null
  return <DiagnosisReportView mode="homeroom" personId={personId} />
}
