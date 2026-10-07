'use client'

/**
 * 学期设置卡（契约 p5-homework.md §5，H06）。
 *
 * 约束：
 * - auto=true 表示该学年尚无手工学期数据，列表为按学年日期二分的自动推导
 *   （条目 id=null，只读，须标注「自动推导」）；
 * - 手工编辑/新增/设当前/恢复自动成功后立即重拉；auto 推导条目（id=null）
 *   不提供编辑/设当前（后端对 null 行本来就没有资源）；
 * - 422（同学年重名/日期重叠/重复设当前）把后端中文 detail 原样展示，绝不白屏。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertCircle, CalendarRange, Pencil, RefreshCw, RotateCcw, Star } from 'lucide-react'

import {
  ApiV1Error,
  homeworkCreateSemester,
  homeworkRestoreAutoSemester,
  homeworkSemesters,
  homeworkSetCurrentSemester,
  homeworkUpdateSemester,
  createAcademicYear,
  listAcademicYears,
  type AcademicYear,
  type HomeworkSemestersResponse,
} from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

/** 把 422（重名/重叠/重复设当前）转成中文文案：后端 detail 已是中文，原样优先。 */
function semesterActionError(err: unknown): string {
  if (err instanceof ApiV1Error && err.status === 422) {
    return err.detail || '学期保存失败（同学年重名或日期重叠）'
  }
  return apiErrorMessage(err)
}

export function SemesterSettingsCard() {
  // 学期设置独立于工作台筛选，班主任和教学始终使用同一份学年清单。
  const [years, setYears] = useState<AcademicYear[] | null>(null)
  const [ayId, setAyId] = useState<number | null>(null)

  const [data, setData] = useState<HomeworkSemestersResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const semestersReqRef = useRef(0)
  // 学年下拉与学期列表是两个资源：请求序号分离（F11），互不作废
  const yearsReqRef = useRef(0)

  // 行内编辑
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editForm, setEditForm] = useState({ name: '', start: '', end: '' })
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // 新增手工学期
  const [createForm, setCreateForm] = useState({ name: '', start: '', end: '' })
  const [yearForm, setYearForm] = useState({ name: '', start: '', end: '' })

  // 学年清单
  const reloadYears = useCallback((selectId?: number) => {
    const req = ++yearsReqRef.current
    listAcademicYears()
      .then((r) => {
        if (req !== yearsReqRef.current) return
        const list = r.years ?? []
        setYears(list)
        setAyId((cur) => selectId ?? cur ?? list[0]?.id ?? null)
      })
      .catch(() => {
        if (req === yearsReqRef.current) setYears([])
      })
  }, [])

  useEffect(() => {
    reloadYears()
  }, [reloadYears])

  // 学期列表
  const reload = useCallback(() => setNonce((v) => v + 1), [])
  useEffect(() => {
    if (ayId == null) return
    const req = ++semestersReqRef.current
    setData(null)
    setError(null)
    homeworkSemesters(ayId)
      .then((r) => {
        if (req !== semestersReqRef.current) return
        setData(r)
        setEditingId(null)
      })
      .catch((err: unknown) => {
        if (req !== semestersReqRef.current) return
        setData(null)
        setError(apiErrorMessage(err))
      })
  }, [ayId, nonce])

  async function runAction(fn: () => Promise<unknown>) {
    setBusy(true)
    setActionError(null)
    try {
      await fn()
      reload()
    } catch (err) {
      setActionError(semesterActionError(err))
    } finally {
      setBusy(false)
    }
  }

  function startEdit(id: number, name: string, start: string, end: string) {
    setEditingId(id)
    setEditForm({ name, start, end })
    setActionError(null)
  }

  async function saveEdit() {
    if (editingId == null) return
    await runAction(() =>
      homeworkUpdateSemester(editingId, {
        name: editForm.name.trim() !== '' ? editForm.name.trim() : undefined,
        start_date: editForm.start !== '' ? editForm.start : undefined,
        end_date: editForm.end !== '' ? editForm.end : undefined,
      }),
    )
  }

  async function createSemester() {
    if (ayId == null) return
    if (createForm.name.trim() === '' || createForm.start === '' || createForm.end === '') {
      setActionError('请填写学期名称与起止日期')
      return
    }
    await runAction(() =>
      homeworkCreateSemester({
        academic_year_id: ayId,
        name: createForm.name.trim(),
        start_date: createForm.start,
        end_date: createForm.end,
      }),
    )
    setCreateForm({ name: '', start: '', end: '' })
  }

  async function createYear() {
    if (yearForm.name.trim() === '' || yearForm.start === '' || yearForm.end === '') {
      setActionError('请填写学年名称与起止日期')
      return
    }
    setBusy(true)
    setActionError(null)
    try {
      const created = await createAcademicYear({
        name: yearForm.name.trim(),
        start_date: yearForm.start,
        end_date: yearForm.end,
      })
      setYearForm({ name: '', start: '', end: '' })
      reloadYears(created.id)
    } catch (err) {
      setActionError(semesterActionError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <CardTitle>学期安排</CardTitle>
          <CardDescription>
            {data?.auto
              ? '当前为自动推导模式（按学年日期二分）：上/下学期边界自动计算，可新增手工学期或编辑后转手工。'
              : data == null
                ? '加载中…'
                : `手工模式：${data.academic_year_name} 的学期由手工维护；删除全部手工学期可回自动推导。`}
            {/* A8-2：作用范围澄清——学期只影响作业侧学期窗口，避免误以为能改成绩/诊断页 */}
            <span className="mt-0.5 block">
              学期设置仅作用于作业学期窗口（作业看板、相关性考前窗口等）；成绩与诊断类页面跟随顶栏学年选择器。
            </span>
          </CardDescription>
        </div>
        <div className="flex items-center gap-2 print:hidden">
          <select
            aria-label="学年"
            value={ayId ?? ''}
            onChange={(e) => {
              setAyId(e.target.value === '' ? null : Number(e.target.value))
            }}
            className="h-9 rounded-md border border-slate-200 bg-white px-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {years == null ? <option value="">加载中…</option> : null}
            {(years ?? []).map((y) => (
              <option key={y.id} value={String(y.id)}>
                {y.name}
              </option>
            ))}
          </select>
          <Button type="button" variant="outline" size="sm" onClick={reload} disabled={ayId == null}>
            <RefreshCw className="h-4 w-4" /> 刷新
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="rounded-lg border border-dashed border-slate-200 px-3 py-3 print:hidden">
          <p className="text-xs font-medium text-slate-500">新增学年</p>
          <div className="mt-2 flex flex-col gap-2 lg:flex-row lg:items-end">
            <Input className="h-9 lg:w-40" placeholder="如 2027-2028" value={yearForm.name} onChange={(e) => setYearForm({ ...yearForm, name: e.target.value })} aria-label="新学年名称" />
            <Input type="date" className="h-9" value={yearForm.start} onChange={(e) => setYearForm({ ...yearForm, start: e.target.value })} aria-label="新学年开始日期" />
            <Input type="date" className="h-9" value={yearForm.end} onChange={(e) => setYearForm({ ...yearForm, end: e.target.value })} aria-label="新学年结束日期" />
            <Button type="button" size="sm" onClick={createYear} disabled={busy}>创建学年</Button>
          </div>
          <p className="mt-1 text-[10px] text-slate-400">先创建学年，再在下方为它新增学期并设为当前。</p>
        </div>
        {error ? (
          <div className="flex flex-col items-center gap-3 py-6 text-center">
            <AlertCircle className="h-8 w-8 text-amber-400" />
            <p className="text-sm text-slate-600">{error}</p>
            <Button variant="outline" size="sm" onClick={reload}>
              重试
            </Button>
          </div>
        ) : data == null ? (
          <Skeleton className="h-28 w-full" />
        ) : (
          <>
            {data.auto ? (
              <div className="rounded-lg border border-brand-100 bg-brand-50/60 px-3 py-2 text-xs text-brand-700">
                <CalendarRange className="mr-1 inline h-3.5 w-3.5" aria-hidden="true" />
                以下条目为自动推导（尚无手工数据）：按学年起止日期二分为上/下学期，仅作展示；
                编辑其中日期将转为手工模式。
              </div>
            ) : null}

            <ul className="space-y-2">
              {data.semesters.map((s) => (
                <li
                  key={s.id ?? `auto-${s.name}`}
                  className={cn(
                    'rounded-lg border px-3 py-2',
                    s.is_current ? 'border-brand-200 bg-brand-50/50' : 'border-slate-200 bg-white',
                  )}
                >
                  {editingId != null && editingId === s.id ? (
                    <div className="flex flex-col gap-2 lg:flex-row lg:items-end">
                      <div className="space-y-1">
                        <label className="text-xs text-slate-500">名称</label>
                        <Input
                          className="h-9 lg:w-40"
                          value={editForm.name}
                          onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                          aria-label="学期名称"
                        />
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs text-slate-500">开始</label>
                        <Input
                          type="date"
                          className="h-9"
                          value={editForm.start}
                          onChange={(e) => setEditForm({ ...editForm, start: e.target.value })}
                          aria-label="开始日期"
                        />
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs text-slate-500">结束</label>
                        <Input
                          type="date"
                          className="h-9"
                          value={editForm.end}
                          onChange={(e) => setEditForm({ ...editForm, end: e.target.value })}
                          aria-label="结束日期"
                        />
                      </div>
                      <div className="flex gap-2">
                        <Button type="button" size="sm" onClick={saveEdit} disabled={busy}>
                          保存
                        </Button>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => setEditingId(null)}
                          disabled={busy}
                        >
                          取消
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-slate-900">{s.name}</span>
                      <span className="text-xs tabular-nums text-slate-500">
                        {s.start_date} ~ {s.end_date}
                      </span>
                      {s.is_current ? <Badge>当前学期</Badge> : null}
                      <Badge variant={s.mode === 'auto' ? 'secondary' : 'outline'}>
                        {s.mode === 'auto' ? '自动推导' : '手工'}
                      </Badge>
                      <span className="flex-1" />
                      {s.id != null ? (
                        <span className="flex gap-1 print:hidden">
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            aria-label={`编辑 ${s.name}`}
                            onClick={() => startEdit(s.id as number, s.name, s.start_date, s.end_date)}
                          >
                            <Pencil className="h-4 w-4" />
                          </Button>
                          {!s.is_current ? (
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              aria-label={`设 ${s.name} 为当前学期`}
                              disabled={busy}
                              onClick={() =>
                                runAction(() => homeworkSetCurrentSemester(s.id as number))
                              }
                            >
                              <Star className="h-4 w-4" />
                            </Button>
                          ) : null}
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            aria-label={`恢复 ${s.name} 为自动推导`}
                            disabled={busy}
                            onClick={() => {
                              if (
                                !window.confirm(
                                  `确认丢弃「${s.name}」的手工日期、恢复自动推导？该操作立即生效。`,
                                )
                              ) {
                                return
                              }
                              void runAction(() => homeworkRestoreAutoSemester(s.id as number))
                            }}
                          >
                            <RotateCcw className="h-4 w-4" />
                          </Button>
                        </span>
                      ) : null}
                    </div>
                  )}
                </li>
              ))}
            </ul>

            {actionError ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                <p className="flex items-start gap-2">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  {actionError}
                </p>
              </div>
            ) : null}

            {/* 新增手工学期（auto 推导条目只读；新增即落库为手工学期） */}
            <div className="rounded-lg border border-dashed border-slate-200 px-3 py-3 print:hidden">
              <p className="text-xs font-medium text-slate-500">新增学期（手工）</p>
              <div className="mt-2 flex flex-col gap-2 lg:flex-row lg:items-end">
                <Input
                  className="h-9 lg:w-40"
                  placeholder="名称，如 上学期"
                  value={createForm.name}
                  onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                  aria-label="新学期名称"
                />
                <Input
                  type="date"
                  className="h-9"
                  value={createForm.start}
                  onChange={(e) => setCreateForm({ ...createForm, start: e.target.value })}
                  aria-label="新学期开始日期"
                />
                <Input
                  type="date"
                  className="h-9"
                  value={createForm.end}
                  onChange={(e) => setCreateForm({ ...createForm, end: e.target.value })}
                  aria-label="新学期结束日期"
                />
                <Button type="button" size="sm" onClick={createSemester} disabled={busy || ayId == null}>
                  新增
                </Button>
              </div>
              <p className="mt-1 text-[10px] text-slate-400">
                同学年重名或日期与既有学期重叠会被拒绝（422 提示，不会静默保存）。
              </p>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
