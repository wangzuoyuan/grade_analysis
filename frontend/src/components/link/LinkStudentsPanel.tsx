'use client'

/**
 * 学生配对面板（契约 §1.2.1，P2）。
 *
 * 数据源：previewLink 的 roster_diff（homeroom_only / teaching_only 为候选列，
 * both 即已确认配对）+ listLinkStudents 的既有配对（撤销需 linked_id）；
 * 冲突标注读 /homeroom/students 的 WorkspaceStudent.shared_conflict（契约 §1.4.1）。
 *
 * 纪律：绝不按同名/同号自动配对或预选，两侧必须逐人显式点选后提交；
 * 同名列出时以别名（alias）与编号辅助人工判断。未提交选择经
 * lib/link-draft 落 sessionStorage（S07 草稿不丢）。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, Unlink } from 'lucide-react'
import {
  createLinkStudents,
  deleteLinkStudent,
  fetchStudents,
  listLinkStudents,
  previewLink,
  type LinkPairEntry,
  type LinkPreview,
  type LinkSharedConflict,
  type LinkSummary,
  type StudentBrief,
} from '@/lib/api-v1'
import {
  clearLinkDraftSection,
  loadLinkDraft,
  saveLinkDraftSection,
} from '@/lib/link-draft'

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
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

import { apiErrorMessage } from './error-text'

function candidateAlias(s: StudentBrief): string | null {
  return s.alias && s.alias !== s.name ? s.alias : null
}

function CandidateColumn({
  title,
  subtitle,
  radioGroup,
  students,
  selected,
  onSelect,
}: {
  title: string
  subtitle: string
  radioGroup: string
  students: StudentBrief[]
  selected: string | null
  onSelect: (personId: string) => void
}) {
  return (
    <div className="flex min-w-0 flex-col rounded-lg border border-slate-200 bg-white">
      <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-3 py-2.5">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-slate-700">{title}</p>
          <p className="text-xs text-slate-400">{subtitle}</p>
        </div>
        <Badge variant="secondary" className="shrink-0 tabular-nums">
          {students.length}
        </Badge>
      </div>
      {students.length === 0 ? (
        <p className="px-3 py-6 text-center text-sm text-slate-400">无可选学生</p>
      ) : (
        <ul className="max-h-64 divide-y divide-slate-100 overflow-y-auto">
          {students.map((s) => {
            const id = String(s.person_id)
            const alias = candidateAlias(s)
            return (
              <li key={id}>
                <label className="flex cursor-pointer items-baseline justify-between gap-2 px-3 py-2 hover:bg-slate-50">
                  <span className="flex min-w-0 items-baseline gap-1.5">
                    <input
                      type="radio"
                      className="mt-0.5 h-4 w-4 shrink-0 accent-[#1f7fd6]"
                      name={radioGroup}
                      checked={selected === id}
                      onChange={() => onSelect(id)}
                    />
                    <span className="min-w-0">
                      <span className="text-sm font-medium text-slate-900">{s.name ?? '（未命名）'}</span>
                      {alias ? <span className="ml-1.5 text-xs text-slate-500">（{alias}）</span> : null}
                    </span>
                  </span>
                  <code className="shrink-0 font-mono text-xs text-slate-400">#{id}</code>
                </label>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

interface LinkStudentsPanelProps {
  link: LinkSummary
  /** 配对增删后通知页面刷新关联摘要（版本/共享字段可能联动变化）。 */
  onPairsChanged?: () => void
}

export function LinkStudentsPanel({ link, onPairsChanged }: LinkStudentsPanelProps) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<LinkPreview | null>(null)
  const [pairs, setPairs] = useState<LinkPairEntry[]>([])
  const [conflictByPerson, setConflictByPerson] = useState<Map<string, LinkSharedConflict>>(new Map())
  const [selectedHomeroom, setSelectedHomeroom] = useState<string | null>(null)
  const [selectedTeaching, setSelectedTeaching] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [revoking, setRevoking] = useState<LinkPairEntry | null>(null)
  const [revokingBusy, setRevokingBusy] = useState(false)
  const [revokeError, setRevokeError] = useState<string | null>(null)
  const [refreshNonce, setRefreshNonce] = useState(0)

  const reqRef = useRef(0)

  /** 修改选择并同步草稿：两侧都清空时移除草稿分节（S07）。 */
  const applySelection = useCallback(
    (h: string | null, t: string | null) => {
      setSelectedHomeroom(h)
      setSelectedTeaching(t)
      if (h == null && t == null) clearLinkDraftSection(link.id, 'pairSelection')
      else saveLinkDraftSection(link.id, { pairSelection: { homeroom_person_id: h, teaching_person_id: t } })
    },
    [link.id],
  )

  // 草稿恢复（S07）：进入该 link 时只套用用户自己点过的选择，不做任何“看起来匹配”的预选
  useEffect(() => {
    const draft = loadLinkDraft(link.id).pairSelection
    setSelectedHomeroom(draft?.homeroom_person_id ?? null)
    setSelectedTeaching(draft?.teaching_person_id ?? null)
  }, [link.id])

  const loadAll = useCallback(async () => {
    const req = ++reqRef.current
    setLoading(true)
    setError(null)
    try {
      const [diff, pairList, homeroomStudents] = await Promise.all([
        previewLink({
          admin_class_id: link.admin_class_id,
          teaching_class_id: link.teaching_class_id,
          academic_year_id: link.academic_year_id,
          subject: link.subject,
        }),
        listLinkStudents(link.id),
        fetchStudents('homeroom', {
          academic_year_id: link.academic_year_id,
          class_id: link.admin_class_id,
        }),
      ])
      if (req !== reqRef.current) return
      setPreview(diff)
      setPairs(pairList.pairs ?? [])
      const conflicts = new Map<string, LinkSharedConflict>()
      for (const stu of homeroomStudents.students ?? []) {
        if (stu.shared_conflict != null) conflicts.set(String(stu.person_id), stu.shared_conflict)
      }
      setConflictByPerson(conflicts)
    } catch (err) {
      if (req !== reqRef.current) return
      setPreview(null)
      setPairs([])
      setConflictByPerson(new Map())
      setError(apiErrorMessage(err))
    } finally {
      if (req === reqRef.current) setLoading(false)
    }
  }, [link.id, link.admin_class_id, link.teaching_class_id, link.academic_year_id, link.subject])

  useEffect(() => {
    void loadAll()
  }, [loadAll, refreshNonce])

  const homeroomCandidates = preview?.roster_diff.homeroom_only ?? []
  const teachingCandidates = preview?.roster_diff.teaching_only ?? []

  // 恢复的草稿若已不在候选名单（名册变化/已在别处配对），清掉避免提交无效对
  useEffect(() => {
    if (loading || preview == null) return
    const hAlive =
      selectedHomeroom == null ||
      homeroomCandidates.some((s) => String(s.person_id) === selectedHomeroom)
    const tAlive =
      selectedTeaching == null ||
      teachingCandidates.some((s) => String(s.person_id) === selectedTeaching)
    if (!hAlive || !tAlive) {
      applySelection(hAlive ? selectedHomeroom : null, tAlive ? selectedTeaching : null)
    }
  }, [loading, preview, homeroomCandidates, teachingCandidates, selectedHomeroom, selectedTeaching, applySelection])

  async function handleCreatePair() {
    if (selectedHomeroom == null || selectedTeaching == null) return
    setCreating(true)
    setError(null)
    setNotice(null)
    try {
      const r = await createLinkStudents(link.id, { pairs: [{ homeroom_person_id: selectedHomeroom, teaching_person_id: selectedTeaching }] })
      // 提交成功：清除草稿分节并复位选择（S07）
      applySelection(null, null)
      setNotice(
        r.skipped > 0
          ? `已提交：新建 ${String(r.created)} 对，跳过已存在 ${String(r.skipped)} 对`
          : `已建立配对（新建 ${String(r.created)} 对）`,
      )
      setRefreshNonce((n) => n + 1)
      onPairsChanged?.()
    } catch (err) {
      setError(apiErrorMessage(err))
    } finally {
      setCreating(false)
    }
  }

  async function handleRevokePair() {
    if (!revoking) return
    setRevokingBusy(true)
    setRevokeError(null)
    try {
      await deleteLinkStudent(link.id, revoking.linked_id)
      setRevoking(null)
      setNotice('已撤销配对，该生跨域共享即时停止')
      setRefreshNonce((n) => n + 1)
      onPairsChanged?.()
    } catch (err) {
      setRevokeError(apiErrorMessage(err))
    } finally {
      setRevokingBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>学生配对</CardTitle>
        <CardDescription>
          教学班 <code className="font-mono">#{String(link.teaching_class_id)}</code>（
          {link.subject || '—'}）：同名同号不会自动配对，需逐人确认后才计入关联共享。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {error ? (
          <div
            role="alert"
            className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-danger-300 bg-danger-50 p-3 text-sm text-danger-600"
          >
            <span className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              {error}
            </span>
            <Button type="button" variant="outline" size="sm" onClick={() => setRefreshNonce((n) => n + 1)}>
              <RefreshCw className="h-4 w-4" aria-hidden="true" />
              重新加载
            </Button>
          </div>
        ) : null}

        {notice ? (
          <div
            role="status"
            className="flex items-center justify-between gap-3 rounded-lg border border-success-300 bg-success-50 p-3 text-sm text-success-600"
          >
            <span className="flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
              {notice}
            </span>
            <button
              type="button"
              aria-label="关闭提示"
              className="rounded p-1 text-success-600/70 hover:bg-success-50/60 hover:text-success-600"
              onClick={() => setNotice(null)}
            >
              ×
            </button>
          </div>
        ) : null}

        {loading ? (
          <div className="space-y-3 print:hidden">
            <Skeleton className="h-40 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : (
          <>
            {/* 两侧候选 + 显式建立配对（打印时只保留配对结果表） */}
            <div className="space-y-3 print:hidden">
              <div className="grid gap-3 md:grid-cols-2">
                <CandidateColumn
                  title="班主任班学生（未配对）"
                  subtitle="单选一人"
                  radioGroup={`pair-homeroom-${String(link.id)}`}
                  students={homeroomCandidates}
                  selected={selectedHomeroom}
                  onSelect={(id) => applySelection(id, selectedTeaching)}
                />
                <CandidateColumn
                  title="教学班学生（未配对）"
                  subtitle="单选一人"
                  radioGroup={`pair-teaching-${String(link.id)}`}
                  students={teachingCandidates}
                  selected={selectedTeaching}
                  onSelect={(id) => applySelection(selectedHomeroom, id)}
                />
              </div>
              <p className="text-xs text-slate-400">
                同名同号<b className="text-warning-600">不会自动配对</b>
                ；同名列出时请以别名（括号内）与编号人工核对。
              </p>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-xs text-slate-500">配对后两工作台共享该生的任教学科数据。</p>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => applySelection(null, null)}
                    disabled={(selectedHomeroom == null && selectedTeaching == null) || creating}
                  >
                    清除选择
                  </Button>
                  <Button
                    type="button"
                    onClick={() => void handleCreatePair()}
                    disabled={selectedHomeroom == null || selectedTeaching == null || creating}
                  >
                    {creating ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
                    {creating ? '提交中…' : '建立配对'}
                  </Button>
                </div>
              </div>
            </div>

            {/* 既有配对结果表：宽表横向滚动（打印时全局样式展开为全宽） */}
            {pairs.length === 0 ? (
              <div className="rounded-lg border border-dashed border-slate-200 py-8 text-center">
                <p className="text-sm text-slate-500">暂无已确认配对</p>
                <p className="mt-1 text-xs text-slate-400">
                  未配对的学生不参与跨域共享；两侧候选均为空表示学生已全部配对。
                </p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>班主任侧</TableHead>
                      <TableHead>教学侧</TableHead>
                      <TableHead>确认依据</TableHead>
                      <TableHead>冲突提示</TableHead>
                      <TableHead className="w-24 text-right print:hidden">操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {pairs.map((p) => {
                      const conflict = conflictByPerson.get(String(p.homeroom_person_id)) ?? null
                      return (
                        <TableRow key={String(p.linked_id)}>
                          <TableCell className="whitespace-nowrap text-sm">
                            {p.homeroom_name ?? '—'}
                            <code className="ml-1 font-mono text-xs text-slate-400">
                              #{String(p.homeroom_person_id)}
                            </code>
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-sm">
                            {p.teaching_name ?? '—'}
                            <code className="ml-1 font-mono text-xs text-slate-400">
                              #{String(p.teaching_person_id)}
                            </code>
                          </TableCell>
                          <TableCell className="text-xs text-slate-500">{p.confirm_basis ?? '—'}</TableCell>
                          <TableCell>
                            {conflict ? (
                              <span
                                className="inline-flex items-center whitespace-nowrap rounded-md border border-warning-300 bg-warning-50 px-2 py-0.5 text-xs text-warning-700"
                                title={`教学域记录分数：${conflict.teaching_score == null ? '缺考' : String(conflict.teaching_score)}`}
                              >
                                两域分数不一致待人工核对
                              </span>
                            ) : (
                              <span className="text-xs text-slate-400">—</span>
                            )}
                          </TableCell>
                          <TableCell className="text-right print:hidden">
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              className="text-danger-500 hover:bg-danger-50 hover:text-danger-600"
                              disabled={revokingBusy}
                              aria-label={`撤销 ${p.homeroom_name ?? ''} 与 ${p.teaching_name ?? ''} 的配对`}
                              onClick={() => {
                                setRevokeError(null)
                                setRevoking(p)
                              }}
                            >
                              <Unlink className="h-4 w-4" aria-hidden="true" />
                              撤销
                            </Button>
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </div>
            )}
          </>
        )}
      </CardContent>

      <Dialog
        open={revoking !== null}
        onOpenChange={(open) => {
          if (!open && !revokingBusy) {
            setRevoking(null)
            setRevokeError(null)
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认撤销这条配对？</DialogTitle>
            <DialogDescription>
              {revoking ? (
                <>
                  {revoking.homeroom_name ?? '—'}（班主任侧）与 {revoking.teaching_name ?? '—'}
                  （教学侧）的配对。
                </>
              ) : null}
            </DialogDescription>
          </DialogHeader>
          <p className="text-sm text-slate-600">撤销后即时生效：该生的任教学科数据立即停止跨域共享。</p>
          {revokeError ? (
            <p role="alert" className="rounded-md border border-danger-300 bg-danger-50 p-2 text-sm text-danger-600">
              {revokeError}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" disabled={revokingBusy} onClick={() => setRevoking(null)}>
              返回
            </Button>
            <Button type="button" variant="destructive" disabled={revokingBusy} onClick={() => void handleRevokePair()}>
              {revokingBusy ? '撤销中…' : '确认撤销'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
