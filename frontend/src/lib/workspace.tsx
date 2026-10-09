'use client'

/**
 * 双工作台上下文（契约 docs/contracts/p1-api.md §3，v2.1 F04）。
 *
 * - mode 的唯一事实源是 URL：事实源顺序 = 路径前缀（/homeroom|/teaching）→ 公共页
 *   `?ws=homeroom|teaching` → lastMode（仅无任何信号时的兜底，并立即回写 URL 修正）。
 *   setMode 只写 URL（任何页面切换均跳目标工作台根路径，公共页不再停留），由页面层响应
 *   路由变化，不做 window.location.reload。刷新/复制 URL/前进后退均不得漂移工作台。
 * - 两模式各自维护筛选记忆，localStorage 键 `workspace-scope:<mode>`。
 * - generation 世代号：setMode 与筛选变化即 +1，所有异步回包先比对世代，
 *   落后即丢弃（ADR-009 迟到响应防线）。
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  Suspense,
  type ReactNode,
} from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { cn } from '@/lib/utils'
import { ApiV1Error, fetchClasses, fetchScope, fetchSharedConfig, type ScopeQuery, type ScopeState, type WorkspaceMode } from './api-v1'
import { hasAnyLinkDraft } from './link-draft'

const STORAGE_PREFIX = 'workspace-scope:'

/** 各工作台的筛选记忆（学年/学期/班或教学班选择；undefined 字段 = 用后端默认）。 */
export interface WorkspaceFilter {
  /** 学年整数 id（学年名称只作显示，不作 ID 传递）。 */
  academic_year_id?: number
  term_id?: number
  /** homeroom：行政班 id（首版行政班来自教师配置绑定，只读展示，不由前端选择）。 */
  class_id?: number
  /** teaching：教学班 id；'all' 或缺省 = 全部所教班并集。 */
  teaching_class_id?: number | 'all'
}

export interface WorkspaceContextValue {
  mode: WorkspaceMode
  setMode: (mode: WorkspaceMode) => void
  filter: WorkspaceFilter
  setFilter: (patch: Partial<WorkspaceFilter>) => void
  /** 当前学期切换成功后专用：把两个工作台的筛选同时同步到新学年。 */
  applyCurrentSemesterFilter: (academicYearId: number) => void
  scope: ScopeState | null
  scopeError: ApiV1Error | null
  scopeLoading: boolean
  refreshScope: () => void
  generation: number
  switching: boolean
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null)

function modeFromPathname(pathname: string | null): WorkspaceMode | null {
  if (!pathname) return null
  if (pathname === '/homeroom' || pathname.startsWith('/homeroom/')) return 'homeroom'
  if (pathname === '/teaching' || pathname.startsWith('/teaching/')) return 'teaching'
  return null
}

/** ?ws 查询参数 → 工作台模式；缺失或非法值一律返回 null（视为未指定，回落 lastMode）。 */
function modeFromSearch(ws: string | null): WorkspaceMode | null {
  return ws === 'homeroom' || ws === 'teaching' ? ws : null
}

/**
 * 公共页链接补 `?ws=<当前模式>`（契约 v2.1 §3，F04）：侧栏/顶栏/页内入口统一走这里，
 * 保证从任何页面进入公共页都带着明确工作台。工作台前缀页本身即事实源，不加参数
 * （避免留下与路径矛盾的陈旧 ws 值）。
 */
export function workspaceHref(href: string, mode: WorkspaceMode): string {
  if (href.startsWith('/homeroom') || href.startsWith('/teaching')) return href
  return href.includes('?') ? `${href}&ws=${mode}` : `${href}?ws=${mode}`
}

/** 存储值 → 有限整数（兼容早期写入的数字字符串），非法值丢弃。 */
function toFiniteInt(value: unknown): number | undefined {
  const n = typeof value === 'number' ? value : typeof value === 'string' && value !== '' ? Number(value) : NaN
  return Number.isFinite(n) ? n : undefined
}

function loadStoredFilter(mode: WorkspaceMode): WorkspaceFilter {
  if (typeof window === 'undefined') return {}
  try {
    const raw = window.localStorage.getItem(STORAGE_PREFIX + mode)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as Partial<WorkspaceFilter>
    const filter: WorkspaceFilter = {}
    const academicYearId = toFiniteInt(parsed.academic_year_id)
    if (academicYearId !== undefined) filter.academic_year_id = academicYearId
    const termId = toFiniteInt(parsed.term_id)
    if (termId !== undefined) filter.term_id = termId
    if (typeof parsed.class_id === 'number') {
      filter.class_id = parsed.class_id
    }
    if (parsed.teaching_class_id === 'all' || typeof parsed.teaching_class_id === 'number') {
      filter.teaching_class_id = parsed.teaching_class_id
    }
    return filter
  } catch {
    return {}
  }
}

function persistFilter(mode: WorkspaceMode, filter: WorkspaceFilter) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(STORAGE_PREFIX + mode, JSON.stringify(filter))
  } catch {
    // 隐私模式等写入失败时静默：筛选记忆是尽力而为的增强
  }
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  // useSearchParams 要求最近的 Suspense 边界，否则静态预渲染直接构建失败；
  // 边界放在 Provider 内部即可覆盖全部子树，AuthGate 首帧本就返回 null，fallback 不可见。
  return (
    <Suspense fallback={null}>
      <WorkspaceProviderInner>{children}</WorkspaceProviderInner>
    </Suspense>
  )
}

function WorkspaceProviderInner({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  // 契约 v2.1 §3（F04）：路径前缀优先；公共页读 ?ws（非法值视为未指定）；
  // lastMode 仅兜底，并在修正 URL 后让位于 URL 事实源。
  const searchParams = useSearchParams()
  const wsMode = modeFromSearch(searchParams.get('ws'))
  const pathMode = modeFromPathname(pathname)

  // 非工作台路由（/exam、/homework 等旧教学页面）沿用最近一次的工作台模式，默认教学
  const [lastMode, setLastMode] = useState<WorkspaceMode>('teaching')
  const mode: WorkspaceMode = pathMode ?? wsMode ?? lastMode

  const [filters, setFilters] = useState<Record<WorkspaceMode, WorkspaceFilter>>({
    homeroom: {},
    teaching: {},
  })
  const filtersRef = useRef(filters)
  const [ready, setReady] = useState(false)

  const [scope, setScope] = useState<ScopeState | null>(null)
  const [scopeError, setScopeError] = useState<ApiV1Error | null>(null)
  const [scopeLoading, setScopeLoading] = useState(false)
  const [generation, setGeneration] = useState(0)
  const generationRef = useRef(0)
  const [switching, setSwitching] = useState(false)
  const [refreshNonce, setRefreshNonce] = useState(0)

  const filter: WorkspaceFilter = filters[mode]

  const bumpGeneration = useCallback(() => {
    generationRef.current += 1
    setGeneration(generationRef.current)
  }, [])

  // 挂载后载入两模式各自的筛选记忆（仅客户端，避免 SSR 水合不一致）
  useEffect(() => {
    const stored: Record<WorkspaceMode, WorkspaceFilter> = {
      homeroom: loadStoredFilter('homeroom'),
      teaching: loadStoredFilter('teaching'),
    }
    filtersRef.current = stored
    setFilters(stored)
    setReady(true)
  }, [])

  // URL 进入任一工作台（含前进/后退直达）：同步 mode、结束 switching、作废在途响应。
  // 公共页 ?ws 变化（含前进/后退）同样同步 lastMode，保证之后进入工作台根时方向一致。
  // 声明在 scope 拉取之前，保证同一次提交里先 +1 再发请求。
  useEffect(() => {
    if (pathMode != null) {
      setLastMode(pathMode)
      setSwitching(false)
      bumpGeneration()
      return
    }
    if (wsMode != null) setLastMode(wsMode)
  }, [pathMode, wsMode, bumpGeneration])

  // 公共页 URL 必须明确携带工作台（契约 v2.1 §3）：缺 ?ws 或值非法时以当前解析值
  // 修正 URL——lastMode 只存于内存，刷新后重置，不能作为跨刷新的事实源。
  useEffect(() => {
    if (pathMode != null || wsMode === mode) return
    const params = new URLSearchParams(searchParams.toString())
    params.set('ws', mode)
    router.replace(`${pathname}?${params.toString()}`, { scroll: false })
  }, [pathMode, wsMode, mode, pathname, searchParams, router])

  const setMode = useCallback(
    (next: WorkspaceMode) => {
      setLastMode(next)
      // 工作台页点击当前工作台 = 无操作（维持原行为）
      if (pathMode === next) return
      bumpGeneration() // 点击即作废在途响应，不等路由跳转完成
      setSwitching(true)
      setScope(null) // 丢弃旧工作台范围，防止跨模式闪现
      setScopeError(null)
      // 公共页也不再停留：统一直达目标工作台根路径（仪表盘）。
      // 跳转后 pathMode 由 null 变为 next，上方 effect 会复位 switching。
      router.push(`/${next}`)
    },
    [pathMode, bumpGeneration, router],
  )

  const setFilter = useCallback(
    (patch: Partial<WorkspaceFilter>) => {
      const next: WorkspaceFilter = { ...filtersRef.current[mode], ...patch }
      filtersRef.current = { ...filtersRef.current, [mode]: next }
      persistFilter(mode, next)
      setFilters(filtersRef.current)
      bumpGeneration()
      setScope(null)
      setScopeError(null)
    },
    [mode, bumpGeneration],
  )

  /**
   * 当前学期设置成功后专用同步。普通 setFilter 只能改当前 mode，若只在此调用会导致
   * 另一个工作台仍携带旧 workspace-scope 学年，直到访问仪表盘才被看板纠偏。
   */
  const applyCurrentSemesterFilter = useCallback(
    (academicYearId: number) => {
      if (!Number.isInteger(academicYearId)) return
      const next: Record<WorkspaceMode, WorkspaceFilter> = {
        homeroom: {
          ...filtersRef.current.homeroom,
          academic_year_id: academicYearId,
          term_id: undefined,
          class_id: undefined,
        },
        teaching: {
          ...filtersRef.current.teaching,
          academic_year_id: academicYearId,
          term_id: undefined,
          teaching_class_id: 'all',
        },
      }
      filtersRef.current = next
      persistFilter('homeroom', next.homeroom)
      persistFilter('teaching', next.teaching)
      setFilters(next)
      bumpGeneration()
      setScope(null)
      setScopeError(null)
    },
    [bumpGeneration],
  )

  const refreshScope = useCallback(() => {
    bumpGeneration()
    setRefreshNonce((n) => n + 1)
  }, [bumpGeneration])

  // 范围拉取：仅在真正处于工作台路由时进行；回包按世代比对，落后即丢弃。
  // homeroom 无存储 class_id 时（首次进入），经 /shared/config + /shared/classes
  // 解析教师绑定行政班作为本次查询参数——契约 §1.1 要求 homeroom 显式
  // class_id，后端不回退默认班；解析结果不写回筛选记忆，避免与 setFilter
  // 的世代递增形成循环（用户显式选择后才持久化）。
  useEffect(() => {
    if (!ready || pathMode == null) return
    const gen = generationRef.current
    let cancelled = false
    const stale = () => cancelled || gen !== generationRef.current

    const f = filtersRef.current[mode]
    const q: ScopeQuery = { mode }
    if (typeof f.academic_year_id === 'number') q.academic_year_id = f.academic_year_id
    if (typeof f.term_id === 'number') q.term_id = f.term_id
    if (mode === 'homeroom' && typeof f.class_id === 'number') q.class_id = f.class_id
    if (mode === 'teaching' && typeof f.teaching_class_id === 'number') {
      q.teaching_class_id = f.teaching_class_id
    }

    const resolveQuery = async (): Promise<ScopeQuery> => {
      if (mode === 'homeroom' && q.class_id === undefined) {
        try {
          const cfg = await fetchSharedConfig()
          const ayId = q.academic_year_id ?? cfg.current_academic_year?.id
          if (ayId !== undefined) {
            const catalog = await fetchClasses(ayId)
            if (catalog.homeroom) q.class_id = catalog.homeroom.class_id
          }
        } catch {
          // 解析失败（如未配置学年/网络异常）：按原查询继续，由 scope
          // 错误态引导配置；若期间世代已作废，最终守卫会丢弃回包
        }
      }
      return q
    }

    setScopeLoading(true)
    setScopeError(null)
    resolveQuery()
      .then((query) => (stale() ? null : fetchScope(query)))
      .then((s) => {
        if (s == null || stale()) return
        setScope(s)
        setScopeLoading(false)
      })
      .catch((err: unknown) => {
        if (stale()) return
        setScope(null)
        setScopeError(
          err instanceof ApiV1Error ? err : new ApiV1Error(0, 'network_error', '网络异常，范围加载失败'),
        )
        setScopeLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [ready, pathMode, mode, filter, refreshNonce])

  const value = useMemo<WorkspaceContextValue>(
    () => ({
      mode,
      setMode,
      filter,
      setFilter,
      applyCurrentSemesterFilter,
      scope,
      scopeError,
      scopeLoading,
      refreshScope,
      generation,
      switching,
    }),
    [mode, setMode, filter, setFilter, applyCurrentSemesterFilter, scope, scopeError, scopeLoading, refreshScope, generation, switching],
  )

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}

export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext)
  if (!ctx) throw new Error('useWorkspace must be used within WorkspaceProvider')
  return ctx
}

/**
 * 顶栏/侧栏共用的工作台切换器：高亮与 Provider 用同一 mode 值（契约 v2.1 §3，F04）——
 * 工作台页按路径前缀、公共页按 ?ws，绝不按路径猜测导致高亮与实际导入域不一致。
 * 原生 button（tab + enter 可操作），aria-pressed 标记选中态。
 *
 * S07：存在未提交的关联草稿（sessionStorage `link-draft:*`）时切换前 window.confirm。
 * 草稿事实源在存储里，点击时直读比经 props/全局事件传递更可靠（面板卸载后信号不丢失）。
 */
export function WorkspaceSwitcher({ className, stretch }: { className?: string; stretch?: boolean }) {
  const { mode, setMode, switching } = useWorkspace()
  const items: Array<{ mode: WorkspaceMode; label: string }> = [
    { mode: 'homeroom', label: '班主任' },
    { mode: 'teaching', label: '教学' },
  ]
  function requestSwitch(next: WorkspaceMode) {
    if (hasAnyLinkDraft() && !window.confirm('关联配置有未提交的修改（已暂存为草稿），确定要切换工作台吗？')) {
      return
    }
    // 不按当前高亮提前 return（F04）：公共页点击已高亮项直达该工作台根（仪表盘）；
    // 工作台页的同模式点击由 setMode 内部拦截为无操作。
    setMode(next)
  }
  return (
    <div
      role="group"
      aria-label="切换工作台"
      className={cn('flex items-center rounded-lg border border-[#bfdcf3] bg-white/70 p-0.5', className)}
    >
      {items.map((it) => {
        const active = mode === it.mode
        return (
          <button
            key={it.mode}
            type="button"
            aria-pressed={active}
            disabled={switching}
            onClick={() => requestSwitch(it.mode)}
            className={cn(
              'rounded-md px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-wait disabled:opacity-60',
              stretch && 'flex-1 text-center',
              active
                ? 'bg-gradient-to-r from-[#1f7fd6]/95 to-[#35b9e9]/90 text-white shadow-[0_2px_8px_rgba(31,127,214,0.32)]'
                : 'text-[#46688c] hover:bg-[#eaf5fd] hover:text-[#0e5fa8]',
            )}
          >
            {it.label}
          </button>
        )
      })}
    </div>
  )
}
