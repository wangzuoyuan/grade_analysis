'use client'

/**
 * 导入预览表（契约 p3 §1.1）。
 *
 * 展示 imports/preview 的 items：文件/类型/考试/学科/成绩条目/已知与新学生；
 * warnings 与新学生明细折叠在状态格里（缺考计数、被过滤列、撞号防呆等风险提示）。
 * 解析失败的行整批不可入库（契约 §1.2：任一文件失败 → 全量回滚），由页面据此禁用确认。
 */

import { AlertTriangle, FileSpreadsheet } from 'lucide-react'

import type { ImportPreviewItem } from '@/lib/api-v1'
import { Badge } from '@/components/ui/badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

const KIND_LABEL: Record<string, string> = {
  student_scores: '学生分数表',
  class_averages: '班级均分表',
  unknown: '未识别',
}

function examCell(it: ImportPreviewItem): string {
  const date = it.exam_date ? it.exam_date.slice(0, 10) : '日期未知'
  return `${it.exam_name}（${date}）`
}

export function ImportsPreviewTable({ items }: { items: ImportPreviewItem[] }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="whitespace-nowrap text-xs">文件</TableHead>
            <TableHead className="whitespace-nowrap text-xs">类型</TableHead>
            <TableHead className="whitespace-nowrap text-xs">考试</TableHead>
            <TableHead className="whitespace-nowrap text-xs">学科 / 班级</TableHead>
            <TableHead className="whitespace-nowrap text-right text-xs">成绩条目</TableHead>
            <TableHead className="whitespace-nowrap text-right text-xs">已知 / 新学生</TableHead>
            <TableHead className="text-xs">解析与警告</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((it, i) => (
            <TableRow key={`${it.filename}-${String(i)}`}>
              <TableCell className="whitespace-nowrap text-sm">
                <span className="flex items-center gap-1.5">
                  <FileSpreadsheet className="h-4 w-4 shrink-0 text-brand-600" />
                  <span className="max-w-[220px] truncate" title={it.filename}>
                    {it.filename}
                  </span>
                </span>
              </TableCell>
              <TableCell className="whitespace-nowrap text-xs text-slate-600">
                {KIND_LABEL[it.kind] ?? it.kind}
              </TableCell>
              <TableCell className="whitespace-nowrap text-xs text-slate-600">
                {examCell(it)}
              </TableCell>
              <TableCell className="whitespace-nowrap text-xs text-slate-600">
                {it.subject ?? '全科'} · {it.class_label ?? '—'}
              </TableCell>
              <TableCell className="text-right tabular-nums text-sm text-slate-700">
                {String(it.row_count)}
              </TableCell>
              <TableCell className="text-right tabular-nums text-sm text-slate-700">
                {String(it.known_students)} / {String(it.new_students.length)}
              </TableCell>
              <TableCell>
                <div className="flex items-center gap-1.5">
                  {it.parsed_ok ? (
                    <Badge variant="success">解析成功</Badge>
                  ) : (
                    <Badge variant="destructive">解析失败</Badge>
                  )}
                  {it.warnings.length > 0 && (
                    <Badge variant="warning" className="gap-1">
                      <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                      警告 {String(it.warnings.length)}
                    </Badge>
                  )}
                </div>
                {it.message ? (
                  <p className={it.parsed_ok ? 'mt-1 text-xs text-slate-500' : 'mt-1 text-xs text-danger-500'}>
                    {it.message}
                  </p>
                ) : null}
                {it.warnings.length > 0 || it.new_students.length > 0 ? (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-xs text-brand-600">
                      展开明细（警告 {String(it.warnings.length)} 条 · 新学生{' '}
                      {String(it.new_students.length)} 人）
                    </summary>
                    <div className="mt-1 space-y-1">
                      {it.warnings.length > 0 ? (
                        <ul className="list-disc space-y-0.5 pl-4 text-xs text-warning-700">
                          {it.warnings.map((w, j) => (
                            <li key={`${w}-${String(j)}`}>{w}</li>
                          ))}
                        </ul>
                      ) : null}
                      {it.new_students.length > 0 ? (
                        <p className="text-xs text-slate-500">
                          确认后将新建学生：
                          {it.new_students
                            .map((s) =>
                              s.alias && s.alias !== s.name ? `${s.name}（${s.alias}）` : s.name,
                            )
                            .join('、')}
                        </p>
                      ) : null}
                    </div>
                  </details>
                ) : null}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
