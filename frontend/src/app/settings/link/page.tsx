'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  CalendarRange,
  CheckCircle2,
  Info,
  Link2,
  RefreshCw,
  X,
} from 'lucide-react'
import {
  cancelLink,
  fetchClasses,
  fetchSharedConfig,
  listLinks,
  type ClassesCatalog,
  type ClassesCatalogHomeroom,
  type ClassesCatalogTeaching,
  type LinkSummary,
  type SharedConfig,
} from '@/lib/api-v1'
import { formatClassLabel } from '@/lib/labels'

import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'

import { CreateLinkPanel } from '@/components/link/CreateLinkPanel'
import { LinkShareScopePanel } from '@/components/link/LinkShareScopePanel'
import { LinkStudentsPanel } from '@/components/link/LinkStudentsPanel'
import { LinkTable } from '@/components/link/LinkTable'
import { apiErrorMessage } from '@/components/link/error-text'

interface YearOption {
  id: number
  name: string
}

/** 学年选项按名称倒序（"2025-2026" 这类名称天然可排序），名称缺失时按 id 兜底。 */
function distinctYearOptions(links: LinkSummary[] | undefined): YearOption[] {
  const map = new Map<number, string>()
  for (const link of links ?? []) {
    if (!map.has(link.academic_year_id)) {
      map.set(link.academic_year_id, link.academic_year_name || String(link.academic_year_id))
    }
  }
  return Array.from(map, ([id, name]) => ({ id, name })).sort((a, b) => {
    if (a.name !== b.name) return a.name < b.name ? 1 : -1
    return b.id - a.id
  })
}

/**
 * 无任何历史关联时，默认学年取 config 的 current_academic_year
 * （契约 §1.1：服务端解析的最新学年；无学年为 null → “学年未确定”引导态）。
 */

export default function LinkSettingsPage() {
  const [config, setConfig] = useState<SharedConfig | null>(null)
  const [years, setYears] = useState<YearOption[]>([])
  const [year, setYear] = useState<number | null>(null)
  const [links, setLinks] = useState<LinkSummary[]>([])
  const [classes, setClasses] = useState<ClassesCatalog | null>(null)
  const [classesLoading, setClassesLoading] = useState(false)
  const [classesFailed, setClassesFailed] = useState(false)
  const [loading, setLoading] = useState(true)
  const [listLoading, setListLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  /** 关联管理（学生配对/共享范围）当前操作的目标 link；null = 回退到第一个 active link。 */
  const [managedLinkId, setManagedLinkId] = useState<number | null>(null)

  const classesReqRef = useRef(0)

  const refreshList = useCallback(async (targetYear: number) => {
    setListLoading(true)
    setListError(null)
    try {
      const res = await listLinks(targetYear)
      setLinks(res.links ?? [])
    } catch (err) {
      setLinks([])
      setListError(apiErrorMessage(err))
    } finally {
      setListLoading(false)
    }
  }, [])

  /** 学年切换时重新拉取班级目录（向导候选以此为准）。带请求世代号防迟到响应。 */
  const refreshClasses = useCallback(async (targetYear: number) => {
    const req = ++classesReqRef.current
    setClassesLoading(true)
    setClassesFailed(false)
    try {
      const catalog = await fetchClasses(targetYear)
      if (req !== classesReqRef.current) return
      setClasses(catalog)
    } catch {
      if (req !== classesReqRef.current) return
      setClasses(null)
      setClassesFailed(true)
    } finally {
      if (req === classesReqRef.current) setClassesLoading(false)
    }
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    setListError(null)
    try {
      const cfg = await fetchSharedConfig()
      setConfig(cfg)
      const yearOptions = distinctYearOptions(cfg.links)
      setYears(yearOptions)
      let y0: number | null = null
      if (yearOptions.length > 0) {
        y0 = yearOptions[0].id
      } else {
        y0 = cfg.current_academic_year?.id ?? null
      }
      setYear(y0)
      if (y0 != null) {
        await Promise.all([
          yearOptions.length > 0 ? refreshList(y0) : Promise.resolve(),
          refreshClasses(y0),
        ])
      }
    } catch (err) {
      setConfig(null)
      setLinks([])
      setYears([])
      setYear(null)
      setLoadError(apiErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [refreshList, refreshClasses])

  useEffect(() => {
    load()
  }, [load])

  const refreshAll = useCallback(async () => {
    try {
      const cfg = await fetchSharedConfig()
      setConfig(cfg)
      setYears(distinctYearOptions(cfg.links))
    } catch {
      // 列表刷新失败不阻塞主流程，下一次操作会再次拉取。
    }
    if (year != null) {
      await Promise.all([refreshList(year), refreshClasses(year)])
    }
  }, [refreshList, refreshClasses, year])

  function handleYearChange(next: string) {
    const nextId = Number(next)
    if (year == null || nextId === year) return
    setYear(nextId)
    void refreshList(nextId)
    void refreshClasses(nextId)
  }

  async function handleCancelLink(link: LinkSummary) {
    await cancelLink(link.id)
    setNotice('已停止共享')
    await refreshAll()
  }

  const selectedYearName = useMemo(() => {
    if (years.length > 0) {
      return years.find((y) => y.id === year)?.name ?? String(year ?? '—')
    }
    return (
      config?.current_academic_year?.name ??
      classes?.academic_year_name ??
      (year != null ? `学年 #${year}` : '未确定')
    )
  }, [years, year, config, classes])

  const teachingSubject = config?.teaching?.subject ?? null
  const homeroomLabel = config?.homeroom?.configured
    ? formatClassLabel(config.homeroom.grade, config.homeroom.class_num)
    : null
  const totallyEmpty = !loading && !loadError && years.length === 0

  const adminClassOption: ClassesCatalogHomeroom | null = classes?.homeroom ?? null
  const teachingClassOptions: ClassesCatalogTeaching[] = classes?.teaching ?? []

  // 仅 active link 才能配对/改共享范围；用户显式选择优先，否则回退第一个
  const activeLinks = useMemo(() => links.filter((l) => l.status === 'active'), [links])
  const managedLink = activeLinks.find((l) => l.id === managedLinkId) ?? activeLinks[0] ?? null

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">班级关联配置</h1>
          <p className="mt-1 text-sm text-slate-500">
            管理班主任班与教学班的数据共享关联；共享范围仅限双方班级成员交集与当前任教学科。
          </p>
          <p className="mt-1 text-xs text-slate-400">
            班主任绑定：{homeroomLabel ?? '未配置'} · 任教学科：{teachingSubject ?? '未配置'}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {years.length > 0 ? (
            <div className="w-44">
              <Select value={year != null ? String(year) : undefined} onValueChange={handleYearChange}>
                <SelectTrigger aria-label="选择学年">
                  <CalendarRange className="h-4 w-4 text-slate-400" aria-hidden="true" />
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {years.map((y) => (
                    <SelectItem key={y.id} value={String(y.id)}>
                      {y.name} 学年
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : (
            <span className="inline-flex h-10 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-500">
              <CalendarRange className="h-4 w-4 text-slate-400" aria-hidden="true" />
              {year == null ? '学年未确定' : `${selectedYearName}（默认当前学年）`}
            </span>
          )}
          <Button
            type="button"
            variant="outline"
            onClick={() => void refreshAll()}
            disabled={loading || listLoading}
          >
            <RefreshCw className={`h-4 w-4${listLoading ? ' animate-spin' : ''}`} aria-hidden="true" />
            刷新
          </Button>
        </div>
      </div>

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
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      ) : null}

      {loading ? (
        <div className="space-y-6">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-80 w-full" />
        </div>
      ) : loadError ? (
        <Card>
          <CardContent className="py-12">
            <div className="flex flex-col items-center justify-center gap-3 text-center">
              <AlertCircle className="h-10 w-10 text-amber-400" aria-hidden="true" />
              <p className="text-sm text-slate-600">{loadError}</p>
              <Button type="button" variant="outline" size="sm" onClick={() => void load()}>
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
                重新加载
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : (
        <>
          {totallyEmpty ? (
            <Card>
              <CardContent className="py-10">
                <div className="flex flex-col items-center justify-center gap-3 text-center">
                  <Link2 className="h-10 w-10 text-slate-300" aria-hidden="true" />
                  <p className="text-base font-medium text-slate-900">还没有任何班级关联</p>
                  <p className="max-w-md text-sm text-slate-500">
                    仅本人班主任班与对应教学班可建立关联，其他教学班不进入班主任工作台。
                    建立关联后，双方班级成员交集内、当前任教学科的教学数据会受控共享给班主任侧。
                  </p>
                  <Button
                    type="button"
                    size="sm"
                    onClick={() =>
                      document.getElementById('create-link-panel')?.scrollIntoView({ behavior: 'smooth' })
                    }
                  >
                    开始新建关联
                  </Button>
                </div>
              </CardContent>
            </Card>
          ) : null}

          {listError ? (
            <div
              role="alert"
              className="flex items-start gap-2 rounded-lg border border-danger-300 bg-danger-50 p-3 text-sm text-danger-600"
            >
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <p>{listError}</p>
            </div>
          ) : null}

          {!totallyEmpty ? (
            <LinkTable
              links={links}
              academicYearLabel={selectedYearName}
              loading={listLoading}
              onCancel={handleCancelLink}
            />
          ) : null}

          {/* 关联管理：学生配对 + 共享范围（仅 active link；草稿经 sessionStorage 暂存，S07） */}
          {!totallyEmpty && managedLink ? (
            <section className="space-y-6" aria-label="关联管理">
              {activeLinks.length > 1 ? (
                <div className="flex flex-wrap items-center gap-2 print:hidden">
                  <span className="text-sm text-slate-500">管理对象</span>
                  <div className="w-72">
                    <Select value={String(managedLink.id)} onValueChange={(v) => setManagedLinkId(Number(v))}>
                      <SelectTrigger aria-label="选择要管理的关联">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {activeLinks.map((l) => (
                          <SelectItem key={l.id} value={String(l.id)}>
                            行政班 #{String(l.admin_class_id)} ↔ 教学班 #{String(l.teaching_class_id)} ·{' '}
                            {l.subject || '—'}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              ) : null}
              <LinkStudentsPanel link={managedLink} onPairsChanged={() => void refreshAll()} />
              <LinkShareScopePanel link={managedLink} onUpdated={() => void refreshAll()} />
            </section>
          ) : null}

          <div id="create-link-panel" className="scroll-mt-24">
            <CreateLinkPanel
              academicYear={year}
              academicYearName={selectedYearName}
              subject={teachingSubject}
              adminClassOption={adminClassOption}
              teachingClassOptions={teachingClassOptions}
              classesLoading={classesLoading}
              classesFailed={classesFailed}
              onCreated={() => void refreshAll()}
            />
          </div>

          <Card>
            <CardContent className="flex items-start gap-2 py-4 text-sm text-slate-500">
              <Info className="mt-0.5 h-4 w-4 shrink-0 text-brand-500" aria-hidden="true" />
              <p>
                关联仅共享名册与当前任教学科的成绩/作业；班主任私密档案（如家访、谈话记录）默认不共享。
                取消关联即时生效，双方各自的原有数据保留。
              </p>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
