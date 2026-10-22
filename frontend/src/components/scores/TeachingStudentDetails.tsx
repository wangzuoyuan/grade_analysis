import Link from 'next/link'
import type { TeachingStudentRow } from '@/lib/api-v1'
import { workspaceHref } from '@/lib/workspace'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { qualityNumber, qualityPercent } from '@/lib/teaching-score-display'

/** 表头吸顶 + 姓名/班级冻结列（2026-10-22 表格尺寸规划）：滚动交还外层
 *  max-h 容器（wrapperClassName=overflow-visible），本组件只负责粘性。 */
export function TeachingStudentDetails({ rows, grade }: { rows: TeachingStudentRow[]; grade: number | null }) {
  const showPercentile = grade === 1 || grade == null
  const showGrade = grade !== 1
  return <Table wrapperClassName="overflow-visible" className="whitespace-nowrap" aria-label="学生成绩明细">
    <TableHeader><TableRow>
      <TableHead className="sticky left-0 z-20 w-20 bg-white">姓名</TableHead><TableHead className="sticky left-20 z-20 w-24 bg-white">班级</TableHead><TableHead className="sticky top-0 z-20 bg-white text-right">原始分</TableHead>
      {showGrade && <><TableHead className="sticky top-0 z-20 bg-white text-right">等级分</TableHead><TableHead className="sticky top-0 z-20 bg-white">等级</TableHead></>}
      {showPercentile && <><TableHead className="sticky top-0 z-20 bg-white text-right">学籍排名</TableHead><TableHead className="sticky top-0 z-20 bg-white text-right">年级百分比</TableHead></>}
      <TableHead className="sticky top-0 z-20 bg-white text-right">范围内名次</TableHead>
    </TableRow></TableHeader>
    <TableBody>{rows.map(s => <TableRow key={String(s.person_id)}>
      <TableCell className="sticky left-0 z-10 w-20 bg-white font-medium"><Link href={workspaceHref(`/student/${encodeURIComponent(String(s.person_id))}`, 'teaching')}
        className="rounded-sm text-brand-700 underline decoration-brand-200 underline-offset-4 hover:text-brand-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
        title="查看个人档案与成绩趋势">{s.name ?? '（未命名）'}</Link></TableCell>
      <TableCell className="sticky left-20 z-10 w-24 bg-white">{s.class_label ?? '—'}</TableCell><TableCell className="text-right tabular-nums">{qualityNumber(s.score)}</TableCell>
      {showGrade && <><TableCell className="text-right tabular-nums">{qualityNumber(s.grade_score)}</TableCell><TableCell>{s.grade_label ?? '—'}</TableCell></>}
      {showPercentile && <><TableCell className="text-right tabular-nums">{qualityNumber(s.xueji_rank, 0)}</TableCell><TableCell className="text-right tabular-nums">{qualityPercent(s.grade_percentile)}</TableCell></>}
      <TableCell className="text-right tabular-nums">{qualityNumber(s.rank, 0)}</TableCell>
    </TableRow>)}</TableBody>
  </Table>
}
