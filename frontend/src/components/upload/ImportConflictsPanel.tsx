'use client'

/**
 * 导入冲突面板（契约 p3 §1.2）。
 *
 * confirm 409 + conflicts（person/subject/exam/库内值/新值）时展示：整批零写入，
 * 默认保持库内值；「以本次上传为准（修订）」以 revise=true 重试，覆写并 data_revision+1。
 */

import { AlertTriangle } from 'lucide-react'

import type { ImportConfirmConflict } from '@/lib/api-v1'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

function conflictValue(v: number | null): string {
  return v == null ? '—（缺考）' : String(v)
}

export function ImportConflictsPanel({
  conflicts,
  revising,
  onRevise,
  onCancel,
}: {
  conflicts: ImportConfirmConflict[]
  revising: boolean
  onRevise: () => void
  onCancel: () => void
}) {
  return (
    <Card className="border-warning-300 bg-warning-50/50">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <AlertTriangle className="h-4 w-4 text-warning-500" aria-hidden="true" />
          发现成绩冲突，本次未写入任何数据
        </CardTitle>
        <CardDescription>
          以下学生在库内已有同场次的不同分数（自然键冲突，409 整批拒绝、零写入）。
          默认保持库内值；确认修订才会以本次上传覆写并留下修订记录。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="overflow-x-auto rounded-lg border border-warning-200 bg-white">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs">学生</TableHead>
                <TableHead className="text-xs">学科</TableHead>
                <TableHead className="text-xs">考试</TableHead>
                <TableHead className="text-right text-xs">库内值</TableHead>
                <TableHead className="text-right text-xs">本次上传</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {conflicts.map((c, i) => (
                <TableRow key={`${c.person}-${c.subject ?? ''}-${String(i)}`}>
                  <TableCell className="whitespace-nowrap text-sm text-slate-900">{c.person}</TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-slate-600">
                    {c.subject ?? '—'}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-slate-600">
                    {c.exam_name}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-sm text-slate-900">
                    {conflictValue(c.existing_score)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-sm font-medium text-warning-700">
                    {conflictValue(c.new_score)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <div className="flex flex-wrap justify-end gap-2 print:hidden">
          <Button variant="outline" onClick={onCancel} disabled={revising}>
            保持库内值（放弃本次）
          </Button>
          <Button onClick={onRevise} disabled={revising}>
            {revising ? '修订中…' : '以本次上传为准（修订）'}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
