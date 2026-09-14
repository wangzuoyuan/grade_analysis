'use client'

/**
 * 教学工作台总览（P2 第一增量）。
 * 数据全部来自 /api/v1（shared/config、shared/classes、shared/links、shared/scope），
 * 不触碰旧 /api/*；任教学科来自教师配置，教学班选择写入两模式独立筛选记忆。
 */

import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, BookOpen, Link2, RefreshCw, Settings2, Users } from 'lucide-react'

import {
  ApiV1Error,
  fetchClasses,
  fetchSharedConfig,
  listLinks,
  type ClassesCatalog,
  type LinkSummary,
  type SharedConfig,
} from '@/lib/api-v1'
import { useWorkspace } from '@/lib/workspace'
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'

const YEAR_DEFAULT = 'default'
const TERM_DEFAULT = 'default'
const TERM_OPTIONS = [
  { value: 1, label: '上学期' },
  { value: 2, label: '下学期' },
]

const TEACHING_ALL = 'all'

function termLabel(termId: number | undefined): string {
  return TERM_OPTIONS.find((t) => t.value === termId)?.label ?? '当前学期'
}

function validRangeLabel(link: LinkSummary): string {
  if (!link.valid_from && !link.valid_to) return '长期有效'
  return `${link.valid_from ?? '—'} ~ ${link.valid_to ?? '—'}`
}

export function TeachingOverview() {
  const { filter, setFilter, scope, scopeError, scopeLoading, refreshScope, switching } = useWorkspace()

  const [config, setConfig] = useState<SharedConfig | null>(null)
  const [configError, setConfigError] = useState<ApiV1Error | null>(null)
  const [configNonce, setConfigNonce] = useState(0)
  const [links, setLinks] = useState<LinkSummary[] | null>(null)
  const [linksError, setLinksError] = useState<ApiV1Error | null>(null)
  const [classes, setClasses] = useState<ClassesCatalog | null>(null)

  // 工作台配置：教师身份 + 任教学科 + 学年关联清单
  useEffect(() => {
    let cancelled = false
    fetchSharedConfig()
      .then((c) => {
        if (cancelled) return
        setConfig(c)
        setConfigError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setConfig(null)
        setConfigError(
          err instanceof ApiV1Error ? err : new ApiV1Error(0, 'network_error', '工作台配置加载失败'),
        )
      })
    return () => {
      cancelled = true
    }
  }, [configNonce])

  // 关联清单：跟随学年筛选变化重新拉取（迟到回包由 cleanup 取消）
  useEffect(() => {
    let cancelled = false
    setLinks(null)
    setLinksError(null)
    listLinks(filter.academic_year_id)
      .then((r) => {
        if (cancelled) return
        setLinks(r.links ?? [])
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setLinks([])
        setLinksError(
          err instanceof ApiV1Error ? err : new ApiV1Error(0, 'network_error', '班级关联加载失败'),
        )
      })
    return () => {
      cancelled = true
    }
  }, [filter.academic_year_id])

  // 班级目录：显式选择学年后拉取，教学班下拉以目录为准（含未关联班）；
  // 未选学年或加载失败时回退到 links 派生的关联班清单。
  useEffect(() => {
    if (filter.academic_year_id == null) {
      setClasses(null)
      return
    }
    let cancelled = false
    setClasses(null)
    fetchClasses(filter.academic_year_id)
      .then((c) => {
        if (!cancelled) setClasses(c)
      })
      .catch(() => {
        if (!cancelled) setClasses(null)
      })
    return () => {
      cancelled = true
    }
  }, [filter.academic_year_id])

  // 学年候选：links 里的 (id, name) 去重；无名称时显示「学年 #id」
  const yearOptions = useMemo(() => {
    const byId = new Map<number, string>()
    for (const link of config?.links ?? []) {
      if (!byId.has(link.academic_year_id)) byId.set(link.academic_year_id, link.academic_year_name)
    }
    return Array.from(byId.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([id, name]) => ({ id, label: name || `学年 #${id}` }))
  }, [config])

  // 教学班选项：有班级目录时用目录全部教学班；否则回退到已有关联的教学班
  const teachingClassOptions = useMemo(() => {
    if (classes) {
      return classes.teaching
        .slice()
        .sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))
        .map((c) => ({ value: String(c.class_id), label: `${c.label} · ${c.subject}` }))
    }
    const seen = new Map<number, LinkSummary>()
    for (const link of config?.links ?? []) {
      if (!seen.has(link.teaching_class_id)) seen.set(link.teaching_class_id, link)
    }
    return Array.from(seen.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([id, link]) => ({ value: String(id), label: `教学班 #${id} · ${link.subject}` }))
  }, [classes, config])

  const activeLinks = useMemo(() => (links ?? []).filter((l) => l.status === 'active'), [links])

  const selectedTeachingClassId = typeof filter.teaching_class_id === 'number' ? filter.teaching_class_id : null
  const linkForSelected =
    selectedTeachingClassId == null
      ? null
      : activeLinks.find((l) => l.teaching_class_id === selectedTeachingClassId) ?? null

  const notConfigured =
    (config !== null && !config.teaching.configured) ||
    scopeError?.code === 'workspace_not_configured'

  const emptyMembers = scope !== null && !scopeLoading && scope.member_person_ids.length === 0

  const yearLabel =
    filter.academic_year_id == null
      ? '当前学年'
      : (yearOptions.find((y) => y.id === filter.academic_year_id)?.label ??
        `学年 #${filter.academic_year_id}`)
  const selectedClassLabel =
    selectedTeachingClassId == null
      ? '全部所教班'
      : (classes?.teaching.find((c) => c.class_id === selectedTeachingClassId)?.label ??
        `教学班 #${selectedTeachingClassId}`)
  const scopeSummary = `${config?.teaching.subject ?? '任教学科'} · ${yearLabel} · ${termLabel(filter.term_id)} · ${selectedClassLabel}`

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">教学工作台</h1>
          <p className="mt-1 text-sm text-slate-500">
            任教学科成绩、教学班名册与班级关联总览{switching ? ' · 正在切换…' : ''}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={refreshScope} disabled={scopeLoading}>
          <RefreshCw className="h-4 w-4" />
          刷新范围
        </Button>
      </div>

      {configError ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-10 text-center">
            <AlertCircle className="h-8 w-8 text-amber-400" />
            <p className="text-sm text-slate-600">
              {configError.detail || `工作台配置加载失败（${configError.code}）`}
            </p>
            <Button variant="outline" size="sm" onClick={() => setConfigNonce((n) => n + 1)}>
              重试
            </Button>
          </CardContent>
        </Card>
      ) : notConfigured ? (
        <Card>
          <CardHeader>
            <CardTitle>教学工作台尚未就绪</CardTitle>
            <CardDescription>需要先有任教学科与教学班配置</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm text-slate-600">
            <div className="flex items-start gap-3">
              <span className="icon-bubble shrink-0">
                <Settings2 className="h-5 w-5" />
              </span>
              <p>
                当前账号还没有可用的任教学科/教学班配置（含历史数据迁移）。完成后这里会显示
                你的教学班名册与任教学科成绩；仅任教班级的数据会出现在本工作台。
              </p>
            </div>
            <p className="text-xs text-slate-400">
              未配置时不展示全校或其他学科的任何数据；可先用顶栏切换到「班主任」工作台。
            </p>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* 范围栏：学年/学期选择 + 教学班下拉 + 任教学科（只读）；筛选控件不参与打印（U01） */}
          <Card className="print:hidden">
            <CardContent className="flex flex-col gap-3 py-4 sm:flex-row sm:items-end sm:flex-wrap">
              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-500">学年</label>
                <Select
                  value={
                    filter.academic_year_id != null ? String(filter.academic_year_id) : YEAR_DEFAULT
                  }
                  onValueChange={(v) =>
                    setFilter({ academic_year_id: v === YEAR_DEFAULT ? undefined : Number(v) })
                  }
                >
                  <SelectTrigger className="h-8 w-[150px] text-xs">
                    <SelectValue placeholder="当前学年" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={YEAR_DEFAULT}>当前学年（默认）</SelectItem>
                    {yearOptions.map((y) => (
                      <SelectItem key={y.id} value={String(y.id)}>
                        {y.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-500">学期</label>
                <Select
                  value={filter.term_id != null ? String(filter.term_id) : TERM_DEFAULT}
                  onValueChange={(v) => setFilter({ term_id: v === TERM_DEFAULT ? undefined : Number(v) })}
                >
                  <SelectTrigger className="h-8 w-[130px] text-xs">
                    <SelectValue placeholder="当前学期" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={TERM_DEFAULT}>当前学期（默认）</SelectItem>
                    {TERM_OPTIONS.map((t) => (
                      <SelectItem key={t.value} value={String(t.value)}>
                        {t.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-500">教学班</label>
                <Select
                  value={
                    typeof filter.teaching_class_id === 'number'
                      ? String(filter.teaching_class_id)
                      : TEACHING_ALL
                  }
                  onValueChange={(v) =>
                    setFilter({
                      teaching_class_id: v === TEACHING_ALL ? 'all' : Number(v),
                    })
                  }
                >
                  <SelectTrigger className="h-8 w-[190px] text-xs">
                    <SelectValue placeholder="全部所教班" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={TEACHING_ALL}>全部所教班（并集）</SelectItem>
                    {teachingClassOptions.map((c) => (
                      <SelectItem key={c.value} value={c.value}>
                        {c.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {!classes && (
                  <p className="text-[10px] text-slate-400">
                    当前仅显示已建立关联的教学班；选择具体学年后显示全部教学班
                  </p>
                )}
              </div>
              <div className="space-y-1">
                <span className="block text-xs font-medium text-slate-500">任教学科（教师绑定）</span>
                <div className="flex h-8 items-center gap-2 text-sm text-slate-900">
                  <BookOpen className="h-4 w-4 text-brand-500" />
                  {config ? config.teaching.subject || '—' : <Skeleton className="h-4 w-12" />}
                  <Badge variant="secondary">只读</Badge>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* 班级概况卡（打印时保持三列，U01） */}
          <div className="grid gap-4 md:grid-cols-3 print:grid-cols-3">
            <Card>
              <CardContent className="py-5">
                <div className="flex items-center gap-2 text-sm text-slate-500">
                  <Users className="h-4 w-4" />
                  覆盖学生数
                </div>
                <div className="mt-2 num-display text-2xl font-semibold text-slate-900">
                  {scopeLoading && !scope ? '…' : (scope?.cohort_size ?? '—')}
                </div>
                <p className="mt-1 text-xs text-slate-400">cohort_size · 当前范围成员数</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="py-5">
                <div className="flex items-center gap-2 text-sm text-slate-500">
                  <BookOpen className="h-4 w-4" />
                  当前范围
                </div>
                <div className="mt-2 truncate text-lg font-semibold text-slate-900">{scopeSummary}</div>
                <p className="mt-1 text-xs text-slate-400">
                  {scope?.as_of ? `口径时点 ${scope.as_of}` : '教学域仅含任教学科，不含其他学科与总分'}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="py-5">
                <div className="flex items-center gap-2 text-sm text-slate-500">
                  <Link2 className="h-4 w-4" />
                  关联状态
                </div>
                <div className="mt-2 text-lg font-semibold text-slate-900">
                  {selectedTeachingClassId == null
                    ? links == null
                      ? '…'
                      : `${activeLinks.length} 个关联班`
                    : linkForSelected
                      ? '属于关联班'
                      : '非关联教学班'}
                </div>
                <p className="mt-1 text-xs text-slate-400">
                  {selectedTeachingClassId == null
                    ? '全部所教班中 active 关联的数量'
                    : linkForSelected
                      ? `与行政班 #${linkForSelected.admin_class_id} 共享交集成员`
                      : '本班数据仅在教学工作台可见'}
                </p>
              </CardContent>
            </Card>
          </div>

          {/* link 状态卡：所选教学班是否属于关联班 */}
          <Card>
            <CardHeader>
              <CardTitle>班级关联</CardTitle>
              <CardDescription>
                关联班向班主任侧共享双方成员交集的任教学科成绩；非关联教学班的数据不出现在班主任工作台。
              </CardDescription>
            </CardHeader>
            <CardContent>
              {links == null ? (
                <Skeleton className="h-16 w-full" />
              ) : linksError ? (
                <div className="flex flex-col items-center gap-2 py-6 text-center">
                  <AlertCircle className="h-6 w-6 text-amber-400" />
                  <p className="text-sm text-slate-600">
                    {linksError.detail || `关联清单加载失败（${linksError.code}）`}
                  </p>
                </div>
              ) : linkForSelected ? (
                <div className="space-y-2">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-[#dbeaf7] bg-[#f7fbff]/60 px-3 py-2 text-sm">
                    <span className="font-medium text-slate-900">
                      教学班 #{linkForSelected.teaching_class_id} 属于关联班
                    </span>
                    <Badge variant="outline">{linkForSelected.subject}</Badge>
                    <Badge variant="success">active</Badge>
                    <span className="text-xs text-slate-500">版本 v{linkForSelected.version}</span>
                    <span className="ml-auto text-xs text-slate-400">
                      {validRangeLabel(linkForSelected)}
                    </span>
                  </div>
                  <p className="text-xs text-slate-400">
                    行政班 #{linkForSelected.admin_class_id} 的班主任可见本班交集学生的
                    {linkForSelected.subject}成绩（来自教学域投影，仅此学科）。
                  </p>
                </div>
              ) : selectedTeachingClassId != null ? (
                <div className="py-6 text-center">
                  <p className="text-sm text-slate-600">教学班 #{selectedTeachingClassId} 不是关联班</p>
                  <p className="mt-1 text-xs text-slate-400">
                    本班名册与成绩仅在教学工作台使用，不会进入任何班主任侧视图。
                  </p>
                </div>
              ) : links.length === 0 ? (
                <div className="py-6 text-center">
                  <p className="text-sm text-slate-600">{yearLabel}暂无任何班级关联</p>
                  <p className="mt-1 text-xs text-slate-400">
                    未关联不影响教学功能；关联由班主任侧发起并经双方确认。
                  </p>
                </div>
              ) : (
                <ul className="space-y-2">
                  {links.map((link) => (
                    <li
                      key={link.id}
                      className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-[#dbeaf7] bg-[#f7fbff]/60 px-3 py-2 text-sm"
                    >
                      <span className="font-medium text-slate-900">
                        教学班 #{link.teaching_class_id}
                      </span>
                      <span className="text-xs text-slate-500">
                        ↔ 行政班 #{link.admin_class_id}
                      </span>
                      <Badge variant="outline">{link.subject}</Badge>
                      {link.status === 'active' ? (
                        <Badge variant="success">active</Badge>
                      ) : (
                        <Badge variant="secondary">{link.status}</Badge>
                      )}
                      <span className="text-xs text-slate-500">版本 v{link.version}</span>
                      <span className="ml-auto text-xs text-slate-400">{validRangeLabel(link)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          {/* 范围加载错误（workspace_not_configured 之外的） */}
          {scopeError && !notConfigured ? (
            <Card>
              <CardContent className="flex flex-col items-center justify-center gap-3 py-8 text-center">
                <AlertCircle className="h-8 w-8 text-amber-400" />
                <p className="text-sm text-slate-600">
                  {scopeError.detail || `范围加载失败（${scopeError.code}）`}
                </p>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={refreshScope}>
                    重试
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setFilter({ academic_year_id: undefined, term_id: undefined, teaching_class_id: 'all' })
                    }
                  >
                    重置筛选
                  </Button>
                </div>
              </CardContent>
            </Card>
          ) : null}

          {/* 空成员范围：合法空态，绝不回退全年级 */}
          {emptyMembers && !scopeError ? (
            <Card>
              <CardContent className="flex flex-col items-center justify-center gap-2 py-8 text-center">
                <Users className="h-8 w-8 text-slate-300" />
                <p className="text-sm text-slate-600">当前范围暂无成员</p>
                <p className="text-xs text-slate-400">
                  该学年/学期下所选教学班还没有有效名册；空范围是正常状态，不会扩展到全年级。
                </p>
              </CardContent>
            </Card>
          ) : null}
        </>
      )}
    </div>
  )
}
