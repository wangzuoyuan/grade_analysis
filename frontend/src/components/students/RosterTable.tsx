'use client'

/**
 * 班主任名册管理页主体（契约 docs/contracts/p4-students.md §2.1/§6）。
 *
 * 数据：GET /api/v1/homeroom/students（工作台筛选映射作用域，后端解析）。
 * 操作：新建（弹窗）、行内编辑姓名/座号（PATCH）、离班/恢复在班（archive，绝不物理删）、
 * 追加新学号（展开别名历史面板）、学生画像打印入口（/homeroom/students/{id}/report）。
 * 红线：不提供删除/合并入口（契约：P4 不提供）；缺考/冲突沿用 P3 提示语义；
 * 迟到回包按世代号（reqRef）丢弃。
 */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import {
  AlertCircle,
  Hash,
  Loader2,
  Pencil,
  Printer,
  RefreshCw,
  RotateCcw,
  UserPlus,
  Users,
  X,
} from 'lucide-react'

import {
  archiveHomeroomStudent,
  fetchStudents,
  patchHomeroomStudent,
  type WorkspaceStudent,
} from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { useWorkspace } from '@/lib/workspace'
import { analysisScopeQuery } from '@/components/scores/shared'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

import { AliasHistoryPanel } from './AliasHistoryPanel'
import { StudentFormDialog } from './StudentFormDialog'

/** 在班状态中文标签（Enrollment.status；契约未穷举，未知值原样展示）。 */
function statusLabel(s: string | null): string {
  if (s == null || s === 'active' || s === '') return '在班'
  if (s === 'transferred') return '已转出'
  if (s === 'graduated') return '已毕业'
  return s
}

/** 是否当期在班（null 视为在班：历史数据可能未写 status）。 */
function isActive(s: string | null): boolean {
  return s == null || s === 'active' || s === ''
}

export function RosterTable() {
  const { filter, generation, switching } = useWorkspace()
  const scopeQ = useMemo(() => analysisScopeQuery(filter), [filter])

  const [students, setStudents] = useState<WorkspaceStudent[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [reloadNonce, setReloadNonce] = useState(0)
  const reqRef = useRef(0)

  // 行内编辑（姓名/座号）
  const [editing, setEditing] = useState<{ personId: string; name: string; seatNo: string } | null>(null)
  const [editBusy, setEditBusy] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)

  // 离班 / 恢复在班
  const [archiving, setArchiving] = useState<WorkspaceStudent | null>(null)
  const [archiveStatus, setArchiveStatus] = useState<'transferred' | 'graduated'>('transferred')
  const [archiveValidTo, setArchiveValidTo] = useState('')
  const [archiveBusy, setArchiveBusy] = useState(false)
  const [archiveError, setArchiveError] = useState<string | null>(null)
  const [restoring, setRestoring] = useState<WorkspaceStudent | null>(null)
  const [restoreBusy, setRestoreBusy] = useState(false)
  const [restoreError, setRestoreError] = useState<string | null>(null)

  // 新建学生弹窗 + 别名历史展开
  const [createOpen, setCreateOpen] = useState(false)
  const [expandedPerson, setExpandedPerson] = useState<string | null>(null)

  const load = useCallback(() => {
    const req = ++reqRef.current
    setStudents(null)
    setLoadError(null)
    setEditing(null)
    fetchStudents('homeroom', scopeQ)
      .then((r) => {
        if (req !== reqRef.current) return
        setStudents(r.students ?? [])
      })
      .catch((err: unknown) => {
        if (req !== reqRef.current) return
        setStudents([])
        setLoadError(apiErrorMessage(err))
      })
  }, [scopeQ])

  // 工作台筛选/世代变化即重拉（迟到回包按代丢弃）
  useEffect(() => {
    load()
  }, [load, generation, reloadNonce])

  function startEdit(s: WorkspaceStudent) {
    setEditError(null)
    setExpandedPerson(null)
    setEditing({ personId: String(s.person_id), name: s.name ?? '', seatNo: s.seat_no ?? '' })
  }

  async function saveEdit() {
    if (!editing) return
    setEditBusy(true)
    setEditError(null)
    try {
      await patchHomeroomStudent(editing.personId, {
        name: editing.name.trim(),
        seat_no: editing.seatNo.trim(),
      })
      setEditing(null)
      setReloadNonce((n) => n + 1)
    } catch (err) {
      setEditError(apiErrorMessage(err))
    } finally {
      setEditBusy(false)
    }
  }

  function openArchive(s: WorkspaceStudent) {
    setArchiveError(null)
    setArchiveStatus('transferred')
    // 默认生效到今天（离班日）；可改
    setArchiveValidTo(new Date().toISOString().slice(0, 10))
    setArchiving(s)
  }

  async function submitArchive() {
    if (!archiving) return
    if (archiveValidTo.trim() === '') {
      setArchiveError('请选择离班日期')
      return
    }
    setArchiveBusy(true)
    setArchiveError(null)
    try {
      await archiveHomeroomStudent(archiving.person_id, {
        status: archiveStatus,
        valid_to: archiveValidTo.trim(),
      })
      setArchiving(null)
      setReloadNonce((n) => n + 1)
    } catch (err) {
      setArchiveError(apiErrorMessage(err))
    } finally {
      setArchiveBusy(false)
    }
  }

  async function submitRestore() {
    if (!restoring) return
    setRestoreBusy(true)
    setRestoreError(null)
    try {
      // 恢复在班：status=active 且 valid_to 置空（契约 §2.1）
      await archiveHomeroomStudent(restoring.person_id, { status: 'active', valid_to: null })
      setRestoring(null)
      setReloadNonce((n) => n + 1)
    } catch (err) {
      setRestoreError(apiErrorMessage(err))
    } finally {
      setRestoreBusy(false)
    }
  }

  const loading = students == null && loadError == null

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">学生管理</h1>
          <p className="mt-1 text-sm text-slate-500">
            行政班名册：新建、编辑、离班与换号接续{switching ? ' · 正在切换…' : ''}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
            刷新名册
          </Button>
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <UserPlus className="h-4 w-4" aria-hidden="true" />
            新建学生
          </Button>
        </div>
      </div>

      {loadError ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-10 text-center">
            <AlertCircle className="h-8 w-8 text-amber-400" aria-hidden="true" />
            <p className="text-sm text-slate-600">{loadError}</p>
            <Button variant="outline" size="sm" onClick={load}>
              重试
            </Button>
          </CardContent>
        </Card>
      ) : students == null ? (
        <Card>
          <CardContent className="space-y-2 py-6">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-2/3" />
          </CardContent>
        </Card>
      ) : students.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-2 py-10 text-center">
            <Users className="h-8 w-8 text-slate-300" aria-hidden="true" />
            <p className="text-sm text-slate-600">当前学年行政班名册为空</p>
            <p className="text-xs text-slate-400">
              可点「新建学生」逐人录入；换届历史名册请用「换届」向导整批接续。
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>名册</CardTitle>
            <CardDescription>
              共 {String(students.length)} 人 · 离班只写状态与截止日期，绝不物理删除；点击「追加学号」可展开学号历史。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="whitespace-nowrap text-xs">姓名</TableHead>
                    <TableHead className="whitespace-nowrap text-xs">学号（别名）</TableHead>
                    <TableHead className="whitespace-nowrap text-xs">座号</TableHead>
                    <TableHead className="whitespace-nowrap text-xs">状态</TableHead>
                    <TableHead className="whitespace-nowrap text-xs">冲突提示</TableHead>
                    <TableHead className="w-56 whitespace-nowrap text-right text-xs print:hidden">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {students.map((s) => {
                    const key = String(s.person_id)
                    const editingRow = editing?.personId === key
                    const expanded = expandedPerson === key
                    return (
                      <Fragment key={key}>
                        <TableRow>
                          <TableCell className="whitespace-nowrap text-sm font-medium text-slate-900">
                            {editingRow ? (
                              <Input
                                aria-label="编辑姓名"
                                value={editing.name}
                                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                                className="h-8 w-28"
                                maxLength={30}
                                disabled={editBusy}
                              />
                            ) : (
                              s.name ?? '（未命名）'
                            )}
                          </TableCell>
                          <TableCell className="whitespace-nowrap font-mono text-xs text-slate-600">
                            {s.alias ?? '—'}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-sm text-slate-600">
                            {editingRow ? (
                              <Input
                                aria-label="编辑座号"
                                value={editing.seatNo}
                                onChange={(e) => setEditing({ ...editing, seatNo: e.target.value })}
                                className="h-8 w-16"
                                maxLength={20}
                                disabled={editBusy}
                              />
                            ) : (
                              s.seat_no ?? '—'
                            )}
                          </TableCell>
                          <TableCell className="whitespace-nowrap">
                            {isActive(s.status) ? (
                              <Badge variant="success" className="text-xs">
                                在班
                              </Badge>
                            ) : (
                              <Badge variant="secondary" className="text-xs">
                                {statusLabel(s.status)}
                              </Badge>
                            )}
                          </TableCell>
                          <TableCell className="whitespace-nowrap">
                            {s.shared_conflict ? (
                              <span
                                className="inline-flex items-center whitespace-nowrap rounded-md border border-warning-300 bg-warning-50 px-2 py-0.5 text-xs text-warning-700"
                                title={`教学域记录分数：${s.shared_conflict.teaching_score == null ? '缺考' : `${String(s.shared_conflict.teaching_score)} 分`}，两域分数不一致待人工核对`}
                              >
                                两域分数不一致待人工核对
                              </span>
                            ) : (
                              <span className="text-xs text-slate-300">—</span>
                            )}
                          </TableCell>
                          <TableCell className="text-right print:hidden">
                            <div className="flex justify-end gap-1">
                              {editingRow ? (
                                <>
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="sm"
                                    className="h-7 px-2 text-xs"
                                    onClick={() => setEditing(null)}
                                    disabled={editBusy}
                                  >
                                    <X className="h-3.5 w-3.5" aria-hidden="true" />
                                    取消
                                  </Button>
                                  <Button
                                    type="button"
                                    size="sm"
                                    className="h-7 px-2 text-xs"
                                    onClick={() => void saveEdit()}
                                    disabled={editBusy || editing.name.trim() === ''}
                                  >
                                    {editBusy ? (
                                      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                                    ) : null}
                                    保存
                                  </Button>
                                </>
                              ) : (
                                <>
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="sm"
                                    className="h-7 px-2 text-xs"
                                    aria-label={`编辑 ${s.name ?? ''}`}
                                    onClick={() => startEdit(s)}
                                  >
                                    <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                                    编辑
                                  </Button>
                                  {isActive(s.status) ? (
                                    <Button
                                      type="button"
                                      variant="ghost"
                                      size="sm"
                                      className="h-7 px-2 text-xs"
                                      aria-label={`离班 ${s.name ?? ''}`}
                                      onClick={() => openArchive(s)}
                                    >
                                      离班
                                    </Button>
                                  ) : (
                                    <Button
                                      type="button"
                                      variant="ghost"
                                      size="sm"
                                      className="h-7 px-2 text-xs"
                                      aria-label={`恢复在班 ${s.name ?? ''}`}
                                      onClick={() => {
                                        setRestoreError(null)
                                        setRestoring(s)
                                      }}
                                    >
                                      <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                                      恢复在班
                                    </Button>
                                  )}
                                  <Button
                                    type="button"
                                    variant={expanded ? 'secondary' : 'ghost'}
                                    size="sm"
                                    className="h-7 px-2 text-xs"
                                    aria-expanded={expanded}
                                    aria-label={`学号历史与追加学号 ${s.name ?? ''}`}
                                    onClick={() => setExpandedPerson(expanded ? null : key)}
                                  >
                                    <Hash className="h-3.5 w-3.5" aria-hidden="true" />
                                    追加学号
                                  </Button>
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="sm"
                                    className="h-7 px-2 text-xs"
                                    asChild
                                    aria-label={`打印学生画像 ${s.name ?? ''}`}
                                  >
                                    <Link href={`/homeroom/students/${encodeURIComponent(key)}/report`}>
                                      <Printer className="h-3.5 w-3.5" aria-hidden="true" />
                                      打印
                                    </Link>
                                  </Button>
                                </>
                              )}
                            </div>
                          </TableCell>
                        </TableRow>
                        {editingRow && editError ? (
                          <TableRow className="hover:bg-transparent">
                            <TableCell colSpan={6} className="py-2">
                              <p role="alert" className="text-sm text-danger-500">
                                {editError}
                              </p>
                            </TableCell>
                          </TableRow>
                        ) : null}
                        {expanded ? (
                          <TableRow className="hover:bg-transparent">
                            <TableCell colSpan={6} className="bg-slate-50/70 p-4">
                              <AliasHistoryPanel personId={s.person_id} name={s.name} />
                            </TableCell>
                          </TableRow>
                        ) : null}
                      </Fragment>
                    )
                  })}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      )}

      <StudentFormDialog open={createOpen} onOpenChange={setCreateOpen} onCreated={load} />

      {/* 离班弹窗：写 status + valid_to，档案保留 */}
      <Dialog
        open={archiving !== null}
        onOpenChange={(v) => {
          if (!v && !archiveBusy) setArchiving(null)
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>确认离班</DialogTitle>
            <DialogDescription>
              {archiving?.name ?? '—'}：写离班状态与截止日期，档案与历史成绩全部保留。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <p className="text-xs font-medium text-slate-500">离班状态</p>
              <Select
                value={archiveStatus}
                onValueChange={(v) => setArchiveStatus(v === 'graduated' ? 'graduated' : 'transferred')}
              >
                <SelectTrigger aria-label="离班状态">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="transferred">转出（转学/转班）</SelectItem>
                  <SelectItem value="graduated">毕业</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <label htmlFor="archive-valid-to" className="text-xs font-medium text-slate-500">
                离班日期
              </label>
              <Input
                id="archive-valid-to"
                type="date"
                value={archiveValidTo}
                onChange={(e) => setArchiveValidTo(e.target.value)}
                disabled={archiveBusy}
              />
            </div>
            {archiveError ? (
              <p role="alert" className="rounded-md border border-danger-300 bg-danger-50 p-2 text-sm text-danger-600">
                {archiveError}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={archiveBusy} onClick={() => setArchiving(null)}>
              取消
            </Button>
            <Button variant="destructive" disabled={archiveBusy} onClick={() => void submitArchive()}>
              {archiveBusy ? '提交中…' : '确认离班'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 恢复在班弹窗：status=active 且 valid_to 置空 */}
      <Dialog
        open={restoring !== null}
        onOpenChange={(v) => {
          if (!v && !restoreBusy) setRestoring(null)
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>确认恢复在班</DialogTitle>
            <DialogDescription>
              {restoring?.name ?? '—'}：恢复为当期在班成员（清空离班截止日期），共享范围即时按在班成员重新计算。
            </DialogDescription>
          </DialogHeader>
          {restoreError ? (
            <p role="alert" className="rounded-md border border-danger-300 bg-danger-50 p-2 text-sm text-danger-600">
              {restoreError}
            </p>
          ) : null}
          <DialogFooter>
            <Button variant="outline" disabled={restoreBusy} onClick={() => setRestoring(null)}>
              取消
            </Button>
            <Button disabled={restoreBusy} onClick={() => void submitRestore()}>
              {restoreBusy ? '提交中…' : '确认恢复'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
