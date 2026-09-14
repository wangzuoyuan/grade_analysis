'use client'

/**
 * 班主任工作台总览（P2 第一增量）。
 * 数据全部来自 /api/v1（shared/config、shared/classes、shared/links、shared/scope），
 * 不触碰旧 /api/*；行政班来自教师配置绑定，只读展示。
 */

import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, Link2, RefreshCw, School, Settings2, Users } from 'lucide-react'

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
import { formatGradeLabel } from '@/lib/labels'
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

function termLabel(termId: number | undefined): string {
  return TERM_OPTIONS.find((t) => t.value === termId)?.label ?? '当前学期'
}

function adminClassLabel(config: SharedConfig | null, classes: ClassesCatalog | null): string {
  // 优先用班级目录返回的 label；无目录（默认学年未显式选择/加载失败）时回退 grade+class_num
  const fromCatalog = classes?.homeroom?.label
  if (fromCatalog) return fromCatalog
  if (!config?.homeroom.configured) return '—'
  const grade = formatGradeLabel(config.homeroom.grade)
  const classNum = config.homeroom.class_num
  return classNum == null ? grade : `${grade}${classNum}班`
}

function validRangeLabel(link: LinkSummary): string {
  if (!link.valid_from && !link.valid_to) return '长期有效'
  return `${link.valid_from ?? '—'} ~ ${link.valid_to ?? '—'}`
}

export function HomeroomOverview() {
  const { filter, setFilter, scope, scopeError, scopeLoading, refreshScope, switching } = useWorkspace()

  const [config, setConfig] = useState<SharedConfig | null>(null)
  const [configError, setConfigError] = useState<ApiV1Error | null>(null)
  const [configNonce, setConfigNonce] = useState(0)
  const [links, setLinks] = useState<LinkSummary[] | null>(null)
  const [linksError, setLinksError] = useState<ApiV1Error | null>(null)
  const [classes, setClasses] = useState<ClassesCatalog | null>(null)

  // 工作台配置：教师身份 + 行政班绑定 + 学年关联清单
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

  // 班级目录：显式选择学年后拉取，用于行政班 label 显示；失败静默回退到 grade/class_num
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

  const activeLinks = useMemo(() => (links ?? []).filter((l) => l.status === 'active'), [links])

  const notConfigured =
    (config !== null && !config.homeroom.configured) ||
    scopeError?.code === 'workspace_not_configured'

  const emptyMembers = scope !== null && !scopeLoading && scope.member_person_ids.length === 0

  const yearLabel =
    filter.academic_year_id == null
      ? '当前学年'
      : (yearOptions.find((y) => y.id === filter.academic_year_id)?.label ??
        `学年 #${filter.academic_year_id}`)
  const scopeSummary = `${yearLabel} · ${termLabel(filter.term_id)} · ${adminClassLabel(config, classes)}`

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">班主任工作台</h1>
          <p className="mt-1 text-sm text-slate-500">
            行政班全科成绩、总分与班级关联总览{switching ? ' · 正在切换…' : ''}
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
            <CardTitle>班主任工作台尚未配置</CardTitle>
            <CardDescription>先绑定行政班，再开始使用班主任工作台</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm text-slate-600">
            <div className="flex items-start gap-3">
              <span className="icon-bubble shrink-0">
                <Settings2 className="h-5 w-5" />
              </span>
              <p>
                当前账号还没有绑定行政班（教师 → 行政班）。绑定后这里会显示本班名册、
                全科成绩与总分，并可与对应教学班建立班级关联。
              </p>
            </div>
            <p className="text-xs text-slate-400">
              未配置时不展示全校或其他任何班级的数据；可先用顶栏切换到「教学」工作台。
            </p>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* 范围栏：学年/学期选择 + 行政班（配置绑定，只读）；筛选控件不参与打印（U01） */}
          <Card className="print:hidden">
            <CardContent className="flex flex-col gap-3 py-4 sm:flex-row sm:items-end">
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
                <span className="block text-xs font-medium text-slate-500">行政班（教师绑定）</span>
                <div className="flex h-8 items-center gap-2 text-sm text-slate-900">
                  <School className="h-4 w-4 text-brand-500" />
                  {config || classes ? adminClassLabel(config, classes) : <Skeleton className="h-4 w-16" />}
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
                  班级人数
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
                  <School className="h-4 w-4" />
                  当前范围
                </div>
                <div className="mt-2 truncate text-lg font-semibold text-slate-900">{scopeSummary}</div>
                <p className="mt-1 text-xs text-slate-400">
                  {scope?.as_of ? `口径时点 ${scope.as_of}` : '未指定学年/学期时由系统按当前时期解析'}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="py-5">
                <div className="flex items-center gap-2 text-sm text-slate-500">
                  <Link2 className="h-4 w-4" />
                  关联教学班
                </div>
                <div className="mt-2 num-display text-2xl font-semibold text-slate-900">
                  {links == null ? '…' : activeLinks.length}
                </div>
                <p className="mt-1 text-xs text-slate-400">
                  {linksError ? '关联清单加载失败' : 'active 状态的 HomeroomTeachingLink'}
                </p>
              </CardContent>
            </Card>
          </div>

          {/* link 状态卡：行政班 ↔ 教学班关联 */}
          <Card>
            <CardHeader>
              <CardTitle>班级关联</CardTitle>
              <CardDescription>
                与本人行政班对应的教学班关联；仅共享双方班级成员交集的任教学科数据，私密档案不共享。
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
              ) : links.length === 0 ? (
                <div className="py-6 text-center">
                  <p className="text-sm text-slate-600">{yearLabel}尚未建立班级关联</p>
                  <p className="mt-1 text-xs text-slate-400">
                    建立关联后，班主任侧可查看交集学生在对应教学班的任教学科成绩（受控共享投影）。
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
                    onClick={() => setFilter({ academic_year_id: undefined, term_id: undefined })}
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
                  该学年/学期下行政班还没有有效名册；空范围是正常状态，不会扩展到全年级。
                </p>
              </CardContent>
            </Card>
          ) : null}
        </>
      )}
    </div>
  )
}
