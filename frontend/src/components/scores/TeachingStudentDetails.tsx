import Link from 'next/link'
import type { TeachingStudentRow } from '@/lib/api-v1'
import { workspaceHref } from '@/lib/workspace'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { qualityNumber, qualityPercent } from '@/lib/teaching-score-display'

export function TeachingStudentDetails({ rows, grade }: { rows: TeachingStudentRow[]; grade: number | null }) {
  const showPercentile = grade === 1 || grade == null
  const showGrade = grade !== 1
  return <Table className="whitespace-nowrap" aria-label="学生成绩明细">
    <TableHeader><TableRow>
      <TableHead>姓名</TableHead><TableHead>班级</TableHead><TableHead className="text-right">原始分</TableHead>
      {showGrade && <><TableHead className="text-right">等级分</TableHead><TableHead>等级</TableHead></>}
      {showPercentile && <><TableHead className="text-right">学籍排名</TableHead><TableHead className="text-right">年级百分比</TableHead></>}
      <TableHead className="text-right">范围内名次</TableHead>
    </TableRow></TableHeader>
    <TableBody>{rows.map(s => <TableRow key={String(s.person_id)}>
      <TableCell className="font-medium"><Link href={workspaceHref(`/student/${encodeURIComponent(String(s.person_id))}`, 'teaching')}
        className="rounded-sm text-brand-700 underline decoration-brand-200 underline-offset-4 hover:text-brand-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
        title="查看个人档案与成绩趋势">{s.name ?? '（未命名）'}</Link></TableCell>
      <TableCell>{s.class_label ?? '—'}</TableCell><TableCell className="text-right tabular-nums">{qualityNumber(s.score)}</TableCell>
      {showGrade && <><TableCell className="text-right tabular-nums">{qualityNumber(s.grade_score)}</TableCell><TableCell>{s.grade_label ?? '—'}</TableCell></>}
      {showPercentile && <><TableCell className="text-right tabular-nums">{qualityNumber(s.xueji_rank, 0)}</TableCell><TableCell className="text-right tabular-nums">{qualityPercent(s.grade_percentile)}</TableCell></>}
      <TableCell className="text-right tabular-nums">{qualityNumber(s.rank, 0)}</TableCell>
    </TableRow>)}</TableBody>
  </Table>
}
