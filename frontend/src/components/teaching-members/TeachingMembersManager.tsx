'use client'

/**
 * 教学班成员管理（契约 docs/contracts/p4-students.md §3/§6）。
 *
 * 作用域 = 任教学科教学班：当期/历史分列（valid_to 判空或 active/left 分组，经
 * normalizeTeachingMembers 归一）、添加成员（teaching 域 identity）、移除（写 valid_to，
 * 不物理删）、文本批量导入（preview/confirm 两段，import_batch token）。
 * 关联班红线：active link 覆盖的班不由教学侧建身份——后端 409 拒绝时展示
 * 「行政班名册为权威来源」引导（到班主任侧添加或先建配对）；sync-from-homeroom
 * 仅关联班可用（差异预览先行）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import {
  AlertCircle,
  ArrowRightLeft,
  Loader2,
  RefreshCw,
  Settings2,
  UserMinus,
  UserPlus,
  Users,
} from 'lucide-react'

import {
  ApiV1Error,
  addTeachingMember,
  createTeachingClass,
  fetchClasses,
  fetchSharedConfig,
  importTeachingMembersConfirm,
  importTeachingMembersPreview,
  listTeachingMembers,
  listManagedTeachingClasses,
  normalizeTeachingMembers,
  removeTeachingMember,
  syncFromHomeroomConfirm,
  syncFromHomeroomPreview,
  updateTeachingClass,
  type ClassesCatalog,
  type LinkSummary,
  type SharedConfig,
  type TeachingImportLine,
  type TeachingImportPreview,
  type TeachingMember,
  type ClassesCatalogTeaching,
  type TeachingMembersResponse,
  type TeachingSyncPreview,
} from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { useWorkspace } from '@/lib/workspace'
import { clearPageDraft, loadPageDraft, savePageDraft } from '@/lib/page-draft'
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

const DRAFT_ROUTE = '/teaching/members'

interface AddMemberDraft {
  name: string
  alias: string
}

function isAddMemberDraft(v: unknown): v is AddMemberDraft {
  if (typeof v !== 'object' || v === null) return false
  const o = v as Record<string, unknown>
  return typeof o.name === 'string' && typeof o.alias === 'string'
}

function isTextDraft(v: unknown): v is string {
  return typeof v === 'string'
}

function dateLabel(v: string | null): string {
  return v ? v.slice(0, 10) : '—'
}

/** 关联班 409 的统一引导：行政班名册为权威来源（契约 §3，架构 §7 名册行）。 */
function LinkGuidance({ detail }: { detail?: string | null }) {
  return (
    <div role="alert" className="space-y-1 rounded-lg border border-warning-300 bg-warning-50 p-3 text-sm text-warning-700">
      <p>
        行政班名册为权威来源，该班成员不由教学侧直接添加：请到班主任侧「学生管理」添加，
        或先建立班级关联配对后用「从行政班同步」。
      </p>
      {detail ? <p className="text-xs text-warning-600">{detail}</p> : null}
      <Link href="/settings/link" className="inline-flex items-center gap-1 text-xs font-medium text-brand-600 underline">
        前往关联配置
      </Link>
    </div>
  )
}

export function TeachingMembersManager() {
  const { filter, setFilter, scope, generation, switching } = useWorkspace()

  // 配置（学年 + 关联）与班级目录
  const [config, setConfig] = useState<SharedConfig | null>(null)
  const [configError, setConfigError] = useState<string | null>(null)
  const [classes, setClasses] = useState<ClassesCatalog | null>(null)
  const [classesError, setClassesError] = useState<string | null>(null)
  const [selectedClassId, setSelectedClassId] = useState<number | null>(null)
  const [managedClasses, setManagedClasses] = useState<ClassesCatalogTeaching[] | null>(null)
  const [classLabel, setClassLabel] = useState('')
  const [classBusy, setClassBusy] = useState(false)
  const [classManageError, setClassManageError] = useState<string | null>(null)
  const reqRef = useRef(0)

  // 成员列表
  const [members, setMembers] = useState<{ active: TeachingMember[]; left: TeachingMember[] } | null>(null)
  const [membersError, setMembersError] = useState<string | null>(null)
  const [membersNonce, setMembersNonce] = useState(0)
  const [tab, setTab] = useState<'list' | 'add' | 'import'>('list')
  // UX04：成员请求世代号——切班后迟到的旧班回包按代丢弃，绝不覆盖新班名单
  const membersReqRef = useRef(0)

  // 添加成员
  const [addName, setAddName] = useState('')
  const [addAlias, setAddAlias] = useState('')
  const [adding, setAdding] = useState(false)
  const [addError, setAddError] = useState<string | null>(null)
  const [addRejectedByLink, setAddRejectedByLink] = useState(false)
  const [addNotice, setAddNotice] = useState<string | null>(null)

  // 移除成员（写 valid_to）
  const [removing, setRemoving] = useState<TeachingMember | null>(null)
  const [removeBusy, setRemoveBusy] = useState(false)
  const [removeError, setRemoveError] = useState<string | null>(null)

  // 文本批量导入（预览/确认两段）
  const [importText, setImportText] = useState('')
  const [importPreview, setImportPreview] = useState<TeachingImportPreview | null>(null)
  const [importing, setImporting] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)
  const [importRejectedByLink, setImportRejectedByLink] = useState(false)
  const [importResult, setImportResult] = useState<string | null>(null)

  // 从行政班同步（关联班专用）
  const [syncPreview, setSyncPreview] = useState<TeachingSyncPreview | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [syncError, setSyncError] = useState<string | null>(null)
  const [syncNotice, setSyncNotice] = useState<string | null>(null)

  const yearId = typeof filter.academic_year_id === 'number'
    ? filter.academic_year_id
    : config?.current_academic_year?.id ?? null

  const loadManagedClasses = useCallback(() => {
    if (yearId == null) return
    listManagedTeachingClasses(yearId, scope?.subject ?? undefined)
      .then((result) => setManagedClasses(result.classes ?? []))
      .catch((err: unknown) => setClassManageError(apiErrorMessage(err)))
  }, [yearId, scope?.subject])

  useEffect(() => { loadManagedClasses() }, [loadManagedClasses, generation])

  async function handleCreateClass() {
    if (yearId == null || classLabel.trim() === '') return
    setClassBusy(true); setClassManageError(null)
    try {
      const result = await createTeachingClass({ academic_year_id: yearId, label: classLabel.trim(), subject: scope?.subject ?? undefined })
      setManagedClasses(result.classes ?? []); setClassLabel(''); loadConfig()
    } catch (err) { setClassManageError(apiErrorMessage(err)) } finally { setClassBusy(false) }
  }

  async function handleRenameClass(item: ClassesCatalogTeaching) {
    const label = window.prompt('请输入新的教学班名称', item.label)?.trim()
    if (!label || label === item.label) return
    setClassBusy(true); setClassManageError(null)
    try { const result = await updateTeachingClass(item.class_id, { label }); setManagedClasses(result.classes ?? []); loadConfig() }
    catch (err) { setClassManageError(apiErrorMessage(err)) } finally { setClassBusy(false) }
  }

  async function handleToggleClass(item: ClassesCatalogTeaching) {
    setClassBusy(true); setClassManageError(null)
    try {
      const result = await updateTeachingClass(item.class_id, { status: item.status === 'inactive' ? 'active' : 'inactive' })
      setManagedClasses(result.classes ?? []); loadConfig()
    } catch (err) { setClassManageError(apiErrorMessage(err)) } finally { setClassBusy(false) }
  }

  const loadConfig = useCallback(() => {
    setConfigError(null)
    fetchSharedConfig()
      .then(setConfig)
      .catch((err: unknown) => setConfigError(apiErrorMessage(err)))
  }, [])

  useEffect(() => {
    loadConfig()
  }, [loadConfig, generation])

  // 班级目录跟随学年变化；迟到回包按代丢弃
  useEffect(() => {
    if (yearId == null) {
      setClasses(null)
      return
    }
    const req = ++reqRef.current
    setClassesError(null)
    fetchClasses(yearId)
      .then((c) => {
        if (req !== reqRef.current) return
        setClasses(c)
      })
      .catch((err: unknown) => {
        if (req !== reqRef.current) return
        setClasses(null)
        setClassesError(apiErrorMessage(err))
      })
  }, [yearId])

  // 页内选班与顶部/侧栏的全局工作台范围使用同一个 filter 事实源。
  useEffect(() => {
    if (classes == null || managedClasses == null) return
    const ids = classes.teaching.map((c) => c.class_id)
    const historicalIds = managedClasses.map((c) => c.class_id)
    if (ids.length === 0) {
      if (typeof filter.teaching_class_id === 'number' && historicalIds.includes(filter.teaching_class_id)) {
        setSelectedClassId(filter.teaching_class_id)
      } else {
        setSelectedClassId(null)
      }
    } else if (typeof filter.teaching_class_id === 'number' && historicalIds.includes(filter.teaching_class_id)) {
      setSelectedClassId(filter.teaching_class_id)
    } else if (selectedClassId == null || !ids.includes(selectedClassId)) {
      setSelectedClassId(ids[0])
      setFilter({ teaching_class_id: ids[0] })
    }
  }, [classes, managedClasses, selectedClassId, filter.teaching_class_id, setFilter])

  const loadMembers = useCallback(() => {
    if (selectedClassId == null) {
      membersReqRef.current += 1 // 作废在途请求
      setMembers(null)
      return
    }
    const req = ++membersReqRef.current
    setMembers(null)
    setMembersError(null)
    listTeachingMembers(selectedClassId)
      .then((r: TeachingMembersResponse) => {
        if (req !== membersReqRef.current) return
        setMembers(normalizeTeachingMembers(r))
      })
      .catch((err: unknown) => {
        if (req !== membersReqRef.current) return
        setMembers(null)
        setMembersError(apiErrorMessage(err))
      })
  }, [selectedClassId])

  useEffect(() => {
    loadMembers()
  }, [loadMembers, membersNonce])

  // UX04：切班清理——上一班的移除确认弹窗、导入/同步预览与提示属于旧班级事实，
  // 统一在 selectedClassId 变化时清空（含默认选班路径）；文本草稿按 S07 保留。
  const prevClassRef = useRef<number | null>(selectedClassId)
  useEffect(() => {
    if (prevClassRef.current === selectedClassId) return
    prevClassRef.current = selectedClassId
    setRemoving(null)
    setRemoveError(null)
    setAddError(null)
    setAddNotice(null)
    setAddRejectedByLink(false)
    resetImport()
    setSyncPreview(null)
    setSyncError(null)
    setSyncNotice(null)
  }, [selectedClassId])

  // 添加成员表单草稿（S07 同思路：切走不丢，提交成功清除）
  useEffect(() => {
    const d = loadPageDraft<AddMemberDraft>(DRAFT_ROUTE, 'add-member', isAddMemberDraft)
    setAddName(d?.name ?? '')
    setAddAlias(d?.alias ?? '')
  }, [])

  useEffect(() => {
    if (addName === '' && addAlias === '') return
    savePageDraft<AddMemberDraft>(DRAFT_ROUTE, 'add-member', { name: addName, alias: addAlias })
  }, [addName, addAlias])

  // 导入文本草稿
  useEffect(() => {
    const d = loadPageDraft<string>(DRAFT_ROUTE, 'import-text', isTextDraft)
    setImportText(d ?? '')
  }, [])

  useEffect(() => {
    if (importText === '') return
    savePageDraft<string>(DRAFT_ROUTE, 'import-text', importText)
  }, [importText])

  /** 选中的教学班是否为关联班（active link 覆盖）。 */
  const activeLink: LinkSummary | null = useMemo(() => {
    if (config == null || selectedClassId == null) return null
    return config.links.find((l) => l.status === 'active' && l.teaching_class_id === selectedClassId) ?? null
  }, [config, selectedClassId])

  const selectedClass = classes?.teaching.find((c) => c.class_id === selectedClassId)
    ?? managedClasses?.find((c) => c.class_id === selectedClassId)
    ?? null

  async function handleAdd() {
    if (selectedClassId == null || addName.trim() === '') return
    setAdding(true)
    setAddError(null)
    setAddRejectedByLink(false)
    setAddNotice(null)
    try {
      await addTeachingMember(selectedClassId, {
        name: addName.trim(),
        ...(addAlias.trim() !== '' ? { alias: addAlias.trim() } : {}),
      })
      clearPageDraft(DRAFT_ROUTE, 'add-member')
      setAddName('')
      setAddAlias('')
      setAddNotice('已添加成员')
      setMembersNonce((n) => n + 1)
    } catch (err) {
      // 关联班：后端 409 拒绝建教学域身份 → 引导到班主任侧/配对
      if (err instanceof ApiV1Error && err.status === 409) {
        setAddRejectedByLink(true)
      }
      setAddError(apiErrorMessage(err))
    } finally {
      setAdding(false)
    }
  }

  async function handleRemove() {
    if (selectedClassId == null || !removing) return
    setRemoveBusy(true)
    setRemoveError(null)
    try {
      await removeTeachingMember(selectedClassId, removing.person_id)
      setRemoving(null)
      setMembersNonce((n) => n + 1)
    } catch (err) {
      setRemoveError(apiErrorMessage(err))
    } finally {
      setRemoveBusy(false)
    }
  }

  async function handleImportPreview() {
    if (selectedClassId == null || importText.trim() === '') return
    setImporting(true)
    setImportError(null)
    setImportRejectedByLink(false)
    setImportResult(null)
    try {
      const p = await importTeachingMembersPreview(selectedClassId, importText)
      setImportPreview(p)
    } catch (err) {
      setImportPreview(null)
      if (err instanceof ApiV1Error && err.status === 409) {
        setImportRejectedByLink(true)
      }
      setImportError(apiErrorMessage(err))
    } finally {
      setImporting(false)
    }
  }

  async function handleImportConfirm() {
    if (selectedClassId == null || importPreview?.token == null) return
    setImporting(true)
    setImportError(null)
    try {
      const r = await importTeachingMembersConfirm(selectedClassId, importPreview.token)
      const o = r as Record<string, unknown>
      const added = typeof o.added_count === 'number' ? o.added_count : null
      setImportResult(added != null ? `导入完成：新增 ${String(added)} 人。` : '导入完成。')
      setImportPreview(null)
      clearPageDraft(DRAFT_ROUTE, 'import-text')
      setImportText('')
      setMembersNonce((n) => n + 1)
    } catch (err) {
      // token 过期/名册漂移：预览作废，需重新预览
      setImportPreview(null)
      setImportError(apiErrorMessage(err))
    } finally {
      setImporting(false)
    }
  }

  async function handleSyncPreview() {
    if (selectedClassId == null) return
    setSyncing(true)
    setSyncError(null)
    setSyncNotice(null)
    try {
      const p = await syncFromHomeroomPreview(selectedClassId)
      setSyncPreview(p)
    } catch (err) {
      setSyncPreview(null)
      setSyncError(apiErrorMessage(err))
    } finally {
      setSyncing(false)
    }
  }

  async function handleSyncConfirm() {
    if (selectedClassId == null) return
    setSyncing(true)
    setSyncError(null)
    try {
      await syncFromHomeroomConfirm(selectedClassId)
      setSyncPreview(null)
      setSyncNotice('已按行政班名册交集同步成员。')
      setMembersNonce((n) => n + 1)
    } catch (err) {
      setSyncError(apiErrorMessage(err))
    } finally {
      setSyncing(false)
    }
  }

  function resetImport() {
    setImportPreview(null)
    setImportError(null)
    setImportRejectedByLink(false)
    setImportResult(null)
  }

  const importLines: TeachingImportLine[] = importPreview?.lines ?? []

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">班级信息</h1>
          <p className="mt-1 text-sm text-slate-500">
            教学班当期与历史成员、文本导入、行政班同步{switching ? ' · 正在切换…' : ''}
          </p>
        </div>
        {classes != null && classes.teaching.length > 0 ? (
          <div className="w-72">
            <Select
              value={selectedClassId != null ? String(selectedClassId) : undefined}
              onValueChange={(v) => {
                const classId = Number(v)
                setSelectedClassId(classId)
                setFilter({ teaching_class_id: classId })
                setTab('list')
              }}
            >
              <SelectTrigger aria-label="选择教学班">
                <SelectValue placeholder="选择教学班" />
              </SelectTrigger>
              <SelectContent>
                {classes.teaching.map((c) => (
                  <SelectItem key={c.class_id} value={String(c.class_id)}>
                    {c.label}（{c.subject}）
                  </SelectItem>
                ))}
                {selectedClass?.status === 'inactive' ? (
                  <SelectItem value={String(selectedClass.class_id)}>
                    {selectedClass.label}（历史）
                  </SelectItem>
                ) : null}
              </SelectContent>
            </Select>
          </div>
        ) : null}
      </div>

      <Card className="print:hidden">
        <CardHeader><CardTitle className="flex items-center gap-2"><Settings2 className="h-4 w-4" />教学班管理</CardTitle><CardDescription>新增、改名、停用或恢复教学班。停用只从日常选择中隐藏，历史成员、成绩和作业均保留。</CardDescription></CardHeader>
        <CardContent className="space-y-3">
          {classManageError ? <p role="alert" className="text-sm text-danger-500">{classManageError}</p> : null}
          <div className="flex flex-col gap-2 sm:flex-row"><Input value={classLabel} onChange={(e) => setClassLabel(e.target.value)} placeholder="新教学班名称" className="sm:max-w-xs" /><Button onClick={() => void handleCreateClass()} disabled={classBusy || !classLabel.trim()}>新增教学班</Button></div>
          <div className="flex flex-wrap gap-2">{(managedClasses ?? []).map((item) => <div key={item.class_id} className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm"><span className={item.status === 'inactive' ? 'text-slate-400' : 'font-medium text-slate-800'}>{item.label}</span>{item.status === 'inactive' ? <Badge variant="outline">已停用</Badge> : null}{item.status === 'inactive' ? <Button variant="ghost" size="sm" onClick={() => { setSelectedClassId(item.class_id); setFilter({ teaching_class_id: item.class_id }) }} disabled={classBusy}>查看历史</Button> : null}<Button variant="ghost" size="sm" onClick={() => void handleRenameClass(item)} disabled={classBusy}>改名</Button><Button variant="ghost" size="sm" onClick={() => void handleToggleClass(item)} disabled={classBusy}>{item.status === 'inactive' ? '恢复' : '停用'}</Button></div>)}</div>
        </CardContent>
      </Card>

      {configError ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-8 text-center">
            <AlertCircle className="h-8 w-8 text-amber-400" aria-hidden="true" />
            <p className="text-sm text-slate-600">{configError}</p>
            <Button variant="outline" size="sm" onClick={loadConfig}>
              重试
            </Button>
          </CardContent>
        </Card>
      ) : classesError ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-8 text-center">
            <AlertCircle className="h-8 w-8 text-amber-400" aria-hidden="true" />
            <p className="text-sm text-slate-600">{classesError}</p>
            <Button variant="outline" size="sm" onClick={loadConfig}>
              重试
            </Button>
          </CardContent>
        </Card>
      ) : classes == null ? (
        <Card>
          <CardContent className="space-y-2 py-6">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-2/3" />
          </CardContent>
        </Card>
      ) : classes.teaching.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-2 py-10 text-center">
            <Users className="h-8 w-8 text-slate-300" aria-hidden="true" />
            <p className="text-sm text-slate-600">当前学年没有你的教学班</p>
            <p className="text-xs text-slate-400">
              教学班由教学版本配置流程生成；请先完成工作台配置或切换学年。
            </p>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* 关联班提示 + 从行政班同步（契约 §3：仅 active link 可用；非关联班不渲染） */}
          {activeLink ? (
            <Card className="border-brand-100">
              <CardHeader className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <CardTitle className="text-base">从行政班同步成员</CardTitle>
                  <CardDescription>
                    该班为关联班（与行政班 #{String(activeLink.admin_class_id)} 关联）：
                    成员按配对交集从班主任名册投影，差异预览后确认。
                  </CardDescription>
                </div>
                <Button variant="outline" size="sm" onClick={() => void handleSyncPreview()} disabled={syncing}>
                  <ArrowRightLeft className="h-4 w-4" aria-hidden="true" />
                  预览差异
                </Button>
              </CardHeader>
              {syncPreview != null || syncError != null || syncNotice != null ? (
                <CardContent className="space-y-3 border-t border-slate-100 pt-4">
                  {syncError ? <p role="alert" className="text-sm text-danger-500">{syncError}</p> : null}
                  {syncNotice ? <p role="status" className="text-sm text-success-600">{syncNotice}</p> : null}
                  {syncPreview != null ? (
                    <>
                      <div className="overflow-x-auto">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead className="text-xs">姓名</TableHead>
                              <TableHead className="whitespace-nowrap text-xs">学号</TableHead>
                              <TableHead className="text-xs">动作</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {(syncPreview.to_add ?? []).map((e, i) => (
                              <TableRow key={i}>
                                <TableCell className="whitespace-nowrap text-sm">{e.name ?? '—'}</TableCell>
                                <TableCell className="whitespace-nowrap font-mono text-xs text-slate-600">
                                  {e.alias ?? '—'}
                                </TableCell>
                                <TableCell className="text-xs text-slate-500">加入教学班</TableCell>
                              </TableRow>
                            ))}
                            {(syncPreview.to_add ?? []).length === 0 ? (
                              <TableRow>
                                <TableCell colSpan={3} className="text-xs text-slate-400">
                                  没有需要新增的成员，两边已一致。
                                </TableCell>
                              </TableRow>
                            ) : null}
                          </TableBody>
                        </Table>
                      </div>
                      <div className="flex justify-end gap-2">
                        <Button variant="outline" size="sm" onClick={() => setSyncPreview(null)} disabled={syncing}>
                          取消
                        </Button>
                        <Button size="sm" onClick={() => void handleSyncConfirm()} disabled={syncing}>
                          {syncing ? <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden="true" /> : null}
                          确认同步
                        </Button>
                      </div>
                    </>
                  ) : null}
                </CardContent>
              ) : null}
            </Card>
          ) : null}

          {/* Tab 切换（沿用教学版成员管理样式） */}
          <Card>
            <CardHeader>
              <CardTitle>
                成员管理{selectedClass ? ` · ${selectedClass.label}` : ''}
                {activeLink ? <Badge className="ml-2 text-xs">关联班</Badge> : null}
              </CardTitle>
              <CardDescription>
                当期成员可移除（写截止日期，不物理删）；关联班的添加/导入受行政班名册约束。
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex gap-1 overflow-x-auto print:hidden">
                {(['list', 'add', 'import'] as const).map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setTab(t)}
                    aria-pressed={tab === t}
                    className={`whitespace-nowrap rounded-md px-3 py-1.5 text-sm transition ${
                      tab === t
                        ? 'bg-brand-500 text-white'
                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                    }`}
                  >
                    {t === 'list' ? '成员列表' : t === 'add' ? '添加成员' : '批量导入'}
                  </button>
                ))}
              </div>

              {tab === 'list' ? (
                membersError ? (
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p role="alert" className="text-sm text-danger-500">
                      {membersError}
                    </p>
                    <Button variant="outline" size="sm" onClick={() => setMembersNonce((n) => n + 1)}>
                      重试
                    </Button>
                  </div>
                ) : members == null ? (
                  <div className="space-y-2">
                    <Skeleton className="h-8 w-full" />
                    <Skeleton className="h-8 w-full" />
                  </div>
                ) : (
                  <div className="space-y-5">
                    <div>
                      <p className="mb-2 text-xs font-semibold text-slate-500">
                        当期成员（{String(members.active.length)} 人）
                      </p>
                      {members.active.length === 0 ? (
                        <p className="rounded-lg border border-dashed border-slate-200 py-6 text-center text-sm text-slate-400">
                          暂无当期成员：可「添加成员」「批量导入」，关联班可用「从行政班同步」。
                        </p>
                      ) : (
                        <div className="overflow-x-auto">
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead className="text-xs">姓名</TableHead>
                                <TableHead className="whitespace-nowrap text-xs">学号</TableHead>
                                <TableHead className="whitespace-nowrap text-xs">入班日期</TableHead>
                                <TableHead className="w-20 text-right text-xs print:hidden">操作</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {members.active.map((m) => (
                                <TableRow key={String(m.person_id)}>
                                  <TableCell className="whitespace-nowrap text-sm font-medium text-slate-900">
                                    {m.name ?? '（未命名）'}
                                  </TableCell>
                                  <TableCell className="whitespace-nowrap font-mono text-xs text-slate-600">
                                    {m.alias ?? '—'}
                                  </TableCell>
                                  <TableCell className="whitespace-nowrap text-xs text-slate-500">
                                    {dateLabel(m.valid_from)}
                                  </TableCell>
                                  <TableCell className="text-right print:hidden">
                                    <Button
                                      type="button"
                                      variant="ghost"
                                      size="sm"
                                      className="h-7 px-2 text-xs text-danger-500 hover:bg-danger-50 hover:text-danger-600"
                                      aria-label={`移除 ${m.name ?? ''}`}
                                      onClick={() => {
                                        setRemoveError(null)
                                        setRemoving(m)
                                      }}
                                    >
                                      <UserMinus className="h-3.5 w-3.5" aria-hidden="true" />
                                      移除
                                    </Button>
                                  </TableCell>
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                        </div>
                      )}
                    </div>
                    <div>
                      <p className="mb-2 text-xs font-semibold text-slate-500">
                        历史成员（{String(members.left.length)} 人）
                      </p>
                      {members.left.length === 0 ? (
                        <p className="text-xs text-slate-400">暂无历史成员。</p>
                      ) : (
                        <div className="overflow-x-auto">
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead className="text-xs">姓名</TableHead>
                                <TableHead className="whitespace-nowrap text-xs">学号</TableHead>
                                <TableHead className="whitespace-nowrap text-xs">起止</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {members.left.map((m) => (
                                <TableRow key={`left-${String(m.person_id)}`}>
                                  <TableCell className="whitespace-nowrap text-sm text-slate-600">
                                    {m.name ?? '（未命名）'}
                                  </TableCell>
                                  <TableCell className="whitespace-nowrap font-mono text-xs text-slate-400">
                                    {m.alias ?? '—'}
                                  </TableCell>
                                  <TableCell className="whitespace-nowrap text-xs text-slate-400">
                                    {dateLabel(m.valid_from)} ~ {dateLabel(m.valid_to)}
                                  </TableCell>
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                        </div>
                      )}
                    </div>
                  </div>
                )
              ) : null}

              {tab === 'add' ? (
                <div className="space-y-3">
                  {addRejectedByLink ? <LinkGuidance detail={addError} /> : null}
                  {!addRejectedByLink && addError ? (
                    <p role="alert" className="text-sm text-danger-500">
                      {addError}
                    </p>
                  ) : null}
                  {addNotice ? <p role="status" className="text-sm text-success-600">{addNotice}</p> : null}
                  {activeLink ? (
                    <p className="text-xs text-warning-600">
                      关联班提示：正常情况下成员来自行政班名册；此处手动添加通常会被拒绝（409）。
                    </p>
                  ) : null}
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
                    <div className="space-y-1">
                      <label htmlFor="tm-add-name" className="text-xs font-medium text-slate-500">
                        姓名 *
                      </label>
                      <Input
                        id="tm-add-name"
                        value={addName}
                        onChange={(e) => setAddName(e.target.value)}
                        className="w-40"
                        maxLength={30}
                        disabled={adding}
                      />
                    </div>
                    <div className="space-y-1">
                      <label htmlFor="tm-add-alias" className="text-xs font-medium text-slate-500">
                        学号（可选）
                      </label>
                      <Input
                        id="tm-add-alias"
                        value={addAlias}
                        onChange={(e) => setAddAlias(e.target.value)}
                        className="w-44 font-mono"
                        maxLength={40}
                        disabled={adding}
                      />
                    </div>
                    <Button onClick={() => void handleAdd()} disabled={adding || addName.trim() === ''}>
                      {adding ? (
                        <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden="true" />
                      ) : (
                        <UserPlus className="mr-1 h-4 w-4" aria-hidden="true" />
                      )}
                      添加成员
                    </Button>
                  </div>
                </div>
              ) : null}

              {tab === 'import' ? (
                <div className="space-y-3">
                  {importRejectedByLink ? <LinkGuidance detail={importError} /> : null}
                  {!importRejectedByLink && importError ? (
                    <p role="alert" className="text-sm text-danger-500">
                      {importError}
                    </p>
                  ) : null}
                  {importResult ? <p role="status" className="text-sm text-success-600">{importResult}</p> : null}
                  {importPreview == null ? (
                    <>
                      <p className="text-xs text-slate-500">
                        每行一条，支持「学号 姓名」或单独姓名；先预览解析结果，确认后才写入。
                      </p>
                      <textarea
                        aria-label="批量导入文本"
                        className="min-h-[140px] w-full rounded-md border border-slate-200 p-2 text-sm"
                        placeholder={'如：\n2026301 张三\n2026302 李四\n王五'}
                        value={importText}
                        onChange={(e) => setImportText(e.target.value)}
                        disabled={importing}
                      />
                      <div className="flex justify-end gap-2">
                        <Button variant="outline" onClick={resetImport} disabled={importing || importText.trim() === ''}>
                          清空
                        </Button>
                        <Button onClick={() => void handleImportPreview()} disabled={importing || importText.trim() === ''}>
                          {importing ? <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden="true" /> : null}
                          预览解析
                        </Button>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="overflow-x-auto">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead className="text-xs">原文</TableHead>
                              <TableHead className="text-xs">姓名</TableHead>
                              <TableHead className="whitespace-nowrap text-xs">学号</TableHead>
                              <TableHead className="whitespace-nowrap text-xs">解析</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {importLines.map((l, i) => (
                              <TableRow key={i}>
                                <TableCell className="text-xs text-slate-500">{l.raw ?? '—'}</TableCell>
                                <TableCell className="whitespace-nowrap text-sm">{l.name ?? '—'}</TableCell>
                                <TableCell className="whitespace-nowrap font-mono text-xs text-slate-600">
                                  {l.alias ?? '—'}
                                </TableCell>
                                <TableCell className="whitespace-nowrap text-xs">
                                  {l.kind === 'new' ? (
                                    <Badge variant="warning" className="text-xs">将新建</Badge>
                                  ) : l.kind === 'invalid' ? (
                                    <Badge variant="destructive" className="text-xs">无法解析</Badge>
                                  ) : (
                                    <Badge variant="secondary" className="text-xs">匹配</Badge>
                                  )}
                                </TableCell>
                              </TableRow>
                            ))}
                            {importLines.length === 0 ? (
                              <TableRow>
                                <TableCell colSpan={4} className="text-xs text-slate-400">
                                  预览未返回逐行明细，请核对后确认。
                                </TableCell>
                              </TableRow>
                            ) : null}
                          </TableBody>
                        </Table>
                      </div>
                      <div className="flex justify-end gap-2">
                        <Button variant="outline" onClick={resetImport} disabled={importing}>
                          返回修改
                        </Button>
                        <Button onClick={() => void handleImportConfirm()} disabled={importing}>
                          {importing ? <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden="true" /> : null}
                          确认导入
                        </Button>
                      </div>
                    </>
                  )}
                </div>
              ) : null}
            </CardContent>
          </Card>
        </>
      )}

      {/* 移除确认：写 valid_to，历史保留 */}
      <Dialog
        open={removing !== null}
        onOpenChange={(v) => {
          if (!v && !removeBusy) setRemoving(null)
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>确认移除成员</DialogTitle>
            <DialogDescription>
              {removing?.name ?? '—'}：写离班截止日期后不再计入当期成员；历史成绩与记录保留。
            </DialogDescription>
          </DialogHeader>
          {removeError ? (
            <p role="alert" className="rounded-md border border-danger-300 bg-danger-50 p-2 text-sm text-danger-600">
              {removeError}
            </p>
          ) : null}
          <DialogFooter>
            <Button variant="outline" disabled={removeBusy} onClick={() => setRemoving(null)}>
              取消
            </Button>
            <Button variant="destructive" disabled={removeBusy} onClick={() => void handleRemove()}>
              {removeBusy ? '移除中…' : '确认移除'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
