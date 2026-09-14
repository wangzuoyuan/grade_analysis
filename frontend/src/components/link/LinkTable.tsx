'use client'

import { useState } from 'react'
import { Ban, Link2 } from 'lucide-react'
import type { LinkSummary } from '@/lib/api-v1'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

import { apiErrorMessage } from './error-text'

function formatPeriod(from: unknown, to: unknown): string {
  const f = from == null ? '—' : String(from)
  const t = to == null ? '—' : String(to)
  return `${f} ~ ${t}`
}

function StatusBadge({ status }: { status: string }) {
  if (status === 'active') {
    return (
      <Badge variant="success" className="border-transparent">
        共享中
      </Badge>
    )
  }
  return <Badge variant="secondary">已取消</Badge>
}

interface LinkTableProps {
  links: LinkSummary[]
  /** 学年显示名（如 2025-2026），仅作展示。 */
  academicYearLabel: string
  loading: boolean
  onCancel: (link: LinkSummary) => Promise<void>
}

/**
 * 关联列表：行政班 / 教学班 / 学科 / 有效期 / 状态 / 版本，操作为取消关联（确认弹窗）。
 */
export function LinkTable({ links, academicYearLabel, loading, onCancel }: LinkTableProps) {
  const [confirming, setConfirming] = useState<LinkSummary | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const [dialogError, setDialogError] = useState<string | null>(null)

  async function handleCancel() {
    if (!confirming) return
    setCancelling(true)
    setDialogError(null)
    try {
      await onCancel(confirming)
      setConfirming(null)
    } catch (err) {
      setDialogError(apiErrorMessage(err))
    } finally {
      setCancelling(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{academicYearLabel} 学年关联</CardTitle>
        <CardDescription>
          行政班与教学班建立关联后，双方班级成员交集内、当前任教学科的教学数据才会进入班主任工作台。
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <p className="py-8 text-center text-sm text-slate-400">加载中…</p>
        ) : links.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
            <Link2 className="h-8 w-8 text-slate-300" aria-hidden="true" />
            <p className="text-sm text-slate-500">本学年暂无关联记录</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>行政班</TableHead>
                  <TableHead>教学班</TableHead>
                  <TableHead>学科</TableHead>
                  <TableHead className="whitespace-nowrap">有效期</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead className="text-right">版本</TableHead>
                  <TableHead className="w-28 text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {links.map((link) => (
                  <TableRow key={String(link.id)} className="hover:bg-slate-50">
                    <TableCell className="font-mono text-sm">{String(link.admin_class_id)}</TableCell>
                    <TableCell className="font-mono text-sm">
                      {String(link.teaching_class_id)}
                    </TableCell>
                    <TableCell>{link.subject || '—'}</TableCell>
                    <TableCell className="whitespace-nowrap text-slate-600">
                      {formatPeriod(link.valid_from, link.valid_to)}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={link.status} />
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs text-slate-500 tabular-nums">
                      v{String(link.version)}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="text-danger-500 hover:bg-danger-50 hover:text-danger-600"
                        disabled={link.status !== 'active' || cancelling}
                        aria-label={`取消行政班 ${String(link.admin_class_id)} 与教学班 ${String(
                          link.teaching_class_id,
                        )} 的关联`}
                        onClick={() => {
                          setDialogError(null)
                          setConfirming(link)
                        }}
                      >
                        <Ban className="h-4 w-4" aria-hidden="true" />
                        取消关联
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>

      <Dialog
        open={confirming !== null}
        onOpenChange={(open) => {
          if (!open && !cancelling) {
            setConfirming(null)
            setDialogError(null)
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认取消关联？</DialogTitle>
            <DialogDescription>
              {confirming ? (
                <>
                  将停止行政班 <span className="font-mono">{String(confirming.admin_class_id)}</span> 与教学班{' '}
                  <span className="font-mono">{String(confirming.teaching_class_id)}</span>（
                  {confirming.subject || '—'}）之间的数据共享。
                </>
              ) : null}
            </DialogDescription>
          </DialogHeader>
          <p className="text-sm text-slate-600">
            取消后即时生效：班主任工作台不再展示该教学班的教学数据；双方各自的原有数据保留。
          </p>
          {dialogError ? (
            <p role="alert" className="rounded-md border border-danger-300 bg-danger-50 p-2 text-sm text-danger-600">
              {dialogError}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" disabled={cancelling} onClick={() => setConfirming(null)}>
              返回
            </Button>
            <Button type="button" variant="destructive" disabled={cancelling} onClick={handleCancel}>
              {cancelling ? '取消中…' : '确认取消关联'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
