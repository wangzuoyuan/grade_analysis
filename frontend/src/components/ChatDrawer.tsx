'use client'

/**
 * AI 对话助手抽屉（P6-FE，契约 docs/contracts/p6-ai-mcp.md §0.1/§1/§6）。
 *
 * - 会话按当前工作台 mode 创建（POST /api/v1/chat/sessions），scope 由服务端
 *   解析冻结；抽屉只展示快照的公开投影（班级数/学科/人数摘要，绝无成员
 *   明单——名单明细只能由模型经 search_students 等只读工具按需查询）。
 * - 会话绑定创建时的范围关键集（Q01：mode + 学年 + 行政班/教学班）：任一
 *   与当前工作台选择不一致即提示「新会话（当前范围）/ 继续旧会话」二选一；
 *   继续旧会话仅提醒不阻断，范围真漂移（关联撤销/版本/成员变化）由后端
 *   每轮发消息/工具前的快照重验 409 link_version_conflict 兜底（A02/Q02），
 *   届时给出「新建会话」重建入口。
 * - 发消息 fetch + getReader 解析 SSE（text/tool_call/tool_result/tool_error/
 *   error/done 六种帧）；关闭抽屉或进入范围不一致即 abort 在途流，部分内容
 *   保留。流中 type=error 帧若为范围失效（detail 含"范围"）→ 冲突态 +
 *   「本流已终止」提示（配合后端工具层中止）。
 * - 历史（Q03）：以服务端 GET /chat/sessions/{id}/messages 为事实源，打开
 *   抽屉/刷新恢复时拉取渲染；sessionStorage（chat-p6:session）仅作离线兜底，
 *   不再作为恢复来源；历史端点 404/409 → 提示重建。新建会话即清空本地历史。
 */

import { useCallback, useEffect, useRef, useState, KeyboardEvent, PointerEvent } from 'react'
import { Bot, Send } from 'lucide-react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from '@/components/ui/sheet'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import ToolCallCard from '@/components/ToolCallCard'
import {
  ApiV1Error,
  closeChatSession,
  createChatSession,
  getChatMessages,
  getChatSession,
  type ChatScopeInfo,
  type ChatSessionCreateRequest,
  type WorkspaceMode,
} from '@/lib/api-v1'
import { useWorkspace, type WorkspaceFilter } from '@/lib/workspace'
import { cn } from '@/lib/utils'

/** 单次工具调用（SSE tool_call/tool_result/tool_error 帧按 call_id 归并）。 */
interface ToolCallEntry {
  call_id: string
  name: string
  input: Record<string, unknown>
  output?: unknown
  error?: string
}

/** 会话内消息条目：用户文本 / 助手文本段 / 工具调用组（按流内顺序排列）。 */
type ChatEntry =
  | { kind: 'user'; content: string }
  | { kind: 'assistant'; content: string }
  | { kind: 'tools'; calls: ToolCallEntry[] }

/**
 * 会话范围关键集（Q01）：创建请求时刻的 mode + 学年 + 班 缓存，用于与当前
 * 工作台选择比对。缺省值统一归一为 null/'all'，保证"会话侧"与"当前选择侧"
 * 两侧语义同构可比（不会因服务端解析出具体学年而误报）。
 */
interface SessionScopeKey {
  mode: WorkspaceMode
  academic_year_id: number | null
  /** homeroom：行政班 id（null = 教师绑定班缺省）；teaching：教学班 id 或 'all'（全部所教班并集）。 */
  class_ref: number | 'all' | null
}

/** 会话状态（sessionStorage 持久化；服务端为历史事实源，本地仅离线兜底）。 */
interface ChatSessionState {
  session_id: number
  scope: ChatScopeInfo
  /** 创建时的范围关键集缓存；比对失败即提示跨范围（Q01）。 */
  scope_key: SessionScopeKey
}

const CHAT_STORAGE_KEY = 'chat-p6:session'
const DEFAULT_DRAWER_WIDTH = 520
const MIN_DRAWER_WIDTH = 360
const MAX_DRAWER_WIDTH = 960
const PAGE_GUTTER = 96

const MODE_LABEL: Record<WorkspaceMode, string> = { homeroom: '班主任', teaching: '教学' }

function clampDrawerWidth(width: number) {
  if (typeof window === 'undefined') return width

  // 移动端（<640px）抽屉恒占满屏宽，保证小屏可用
  if (window.innerWidth < 640) return window.innerWidth

  const maxWidth = Math.max(
    MIN_DRAWER_WIDTH,
    Math.min(MAX_DRAWER_WIDTH, window.innerWidth - PAGE_GUTTER)
  )
  const minWidth = Math.min(MIN_DRAWER_WIDTH, maxWidth)
  return Math.min(Math.max(width, minWidth), maxWidth)
}

/** 恢复的消息数组逐条校验：损坏/异形条目丢弃，绝不整块信任存储内容。 */
function sanitizeEntries(raw: unknown): ChatEntry[] {
  if (!Array.isArray(raw)) return []
  const out: ChatEntry[] = []
  for (const item of raw) {
    if (typeof item !== 'object' || item === null) continue
    const o = item as Record<string, unknown>
    if ((o.kind === 'user' || o.kind === 'assistant') && typeof o.content === 'string' && o.content.trim()) {
      out.push({ kind: o.kind, content: o.content })
      continue
    }
    if (o.kind === 'tools' && Array.isArray(o.calls)) {
      const calls: ToolCallEntry[] = []
      for (const c of o.calls) {
        if (typeof c !== 'object' || c === null) continue
        const cc = c as Record<string, unknown>
        if (typeof cc.name !== 'string' || cc.name === '') continue
        calls.push({
          call_id: typeof cc.call_id === 'string' ? cc.call_id : '',
          name: cc.name,
          input:
            cc.input && typeof cc.input === 'object' && !Array.isArray(cc.input)
              ? (cc.input as Record<string, unknown>)
              : {},
          output: cc.output,
          error: typeof cc.error === 'string' ? cc.error : undefined,
        })
      }
      if (calls.length > 0) out.push({ kind: 'tools', calls })
    }
  }
  return out
}

function loadStoredChat(): (ChatSessionState & { entries: ChatEntry[] }) | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.sessionStorage.getItem(CHAT_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Record<string, unknown>
    const sessionId = typeof parsed.session_id === 'number' ? parsed.session_id : NaN
    const scope =
      parsed.scope && typeof parsed.scope === 'object'
        ? (parsed.scope as unknown as ChatScopeInfo | null)
        : null
    // scope.mode/cohort_size 是后续一切展示与判定的最小依赖，缺失即视为无效
    if (!Number.isFinite(sessionId) || !scope || typeof scope.mode !== 'string') return null
    if (typeof scope.cohort_size !== 'number' || !Array.isArray(scope.class_ids)) return null
    return {
      session_id: sessionId,
      scope,
      // 旧存储格式无 scope_key：由服务端 scope 投影反推兜底
      scope_key: parseScopeKey(parsed.scope_key, scope),
      entries: sanitizeEntries(parsed.entries),
    }
  } catch {
    // 损坏的存储一律当作无会话
    return null
  }
}

/** 会话创建请求：只带 mode + 筛选里的资源 ID（学年/班级）。契约 §0：绝不提交
 * 成员或学科清单；subject 由服务端按教师任教配置解析，多学科时后端 422 引导。 */
function buildCreateRequest(mode: WorkspaceMode, filter: WorkspaceFilter): ChatSessionCreateRequest {
  const req: ChatSessionCreateRequest = { mode }
  if (typeof filter.academic_year_id === 'number') req.academic_year_id = filter.academic_year_id
  if (mode === 'homeroom' && typeof filter.class_id === 'number') req.class_id = filter.class_id
  if (mode === 'teaching' && typeof filter.teaching_class_id === 'number') {
    req.teaching_class_id = filter.teaching_class_id
  }
  return req
}

/** scope 摘要（公开投影：班级数/学科/人数；契约 §1 不泄露成员明单）。 */
function describeScope(scope: ChatScopeInfo): string {
  const count = `${scope.class_ids.length} 个${scope.mode === 'teaching' ? '教学班' : '行政班'} · ${scope.cohort_size} 人`
  if (scope.mode === 'teaching') {
    return `${MODE_LABEL.teaching}工作台 · ${scope.subject ?? '任教学科'} · ${count}`
  }
  const link = scope.link_id != null ? ' · 已关联教学班（授权学科投影）' : ''
  return `${MODE_LABEL.homeroom}工作台 · 全科与总分 · ${count}${link}`
}

/** 由服务端 scope 投影反推关键集（旧存储无 scope_key 的兜底：单班→具体班，多班→并集）。 */
function scopeKeyFromScope(scope: ChatScopeInfo): SessionScopeKey {
  const single = scope.class_ids.length === 1 ? scope.class_ids[0] : null
  return {
    mode: scope.mode,
    academic_year_id: scope.academic_year_id ?? null,
    class_ref: scope.mode === 'homeroom' ? single : (single ?? 'all'),
  }
}

/** 存储的关键集逐字段校验；形状不符或缺席（旧格式）一律由 scope 反推兜底。 */
function parseScopeKey(raw: unknown, scope: ChatScopeInfo): SessionScopeKey {
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    const o = raw as { mode?: unknown; academic_year_id?: unknown; class_ref?: unknown }
    const mode = o.mode
    const year = o.academic_year_id
    const ref = o.class_ref
    if (
      (mode === 'homeroom' || mode === 'teaching') &&
      (ref === 'all' || ref === null || typeof ref === 'number') &&
      (year === null || typeof year === 'number')
    ) {
      return { mode, academic_year_id: year, class_ref: ref }
    }
  }
  return scopeKeyFromScope(scope)
}

/** 当前工作台选择的关键集：与创建请求（buildCreateRequest）字段语义同构，
 * 缺省同样归一为 null/'all'——两侧语义一致才不会因服务端解析细节误报。 */
function scopeKeyOfSelection(mode: WorkspaceMode, filter: WorkspaceFilter): SessionScopeKey {
  return {
    mode,
    academic_year_id: typeof filter.academic_year_id === 'number' ? filter.academic_year_id : null,
    class_ref:
      mode === 'homeroom'
        ? typeof filter.class_id === 'number'
          ? filter.class_id
          : null
        : typeof filter.teaching_class_id === 'number'
          ? filter.teaching_class_id
          : 'all',
  }
}

/** 关键集整体相等：mode + 学年 + 班 任一不同即跨范围（Q01，非仅比 mode）。 */
function sameScopeKey(a: SessionScopeKey, b: SessionScopeKey): boolean {
  return a.mode === b.mode && a.academic_year_id === b.academic_year_id && a.class_ref === b.class_ref
}

/** 旧范围明细（学年/班；前端无班名缓存，ID 直显不猜名称）。 */
function describeSessionRange(key: SessionScopeKey): string {
  const year = key.academic_year_id != null ? `${key.academic_year_id} 学年` : '默认学年'
  if (key.mode === 'teaching') {
    return `${year} · ${key.class_ref === 'all' ? '全部所教班' : `教学班 ${key.class_ref}`}`
  }
  return `${year} · ${key.class_ref != null ? `行政班 ${key.class_ref}` : '教师绑定班'}`
}

const markdownComponents: Components = {
  h1: ({ children }) => (
    <h1 className="mb-2 mt-3 text-base font-semibold leading-snug text-slate-950 first:mt-0">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-2 mt-3 text-sm font-semibold leading-snug text-slate-950 first:mt-0">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mb-1.5 mt-3 text-sm font-semibold leading-snug text-slate-900 first:mt-0">
      {children}
    </h3>
  ),
  p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold text-slate-950">{children}</strong>,
  em: ({ children }) => <em className="text-slate-700">{children}</em>,
  ul: ({ children }) => (
    <ul className="my-2 list-disc space-y-1 pl-5 first:mt-0 last:mb-0">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="my-2 list-decimal space-y-1 pl-5 first:mt-0 last:mb-0">{children}</ol>
  ),
  li: ({ children }) => <li className="pl-0.5">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-brand-500 pl-3 text-slate-700">
      {children}
    </blockquote>
  ),
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="font-medium text-brand-700 underline underline-offset-2"
    >
      {children}
    </a>
  ),
  code: ({ children, className }) => (
    <code
      className={cn(
        'rounded bg-slate-200 px-1 py-0.5 font-mono text-[0.8em] text-slate-900',
        className
      )}
    >
      {children}
    </code>
  ),
  pre: ({ children }) => (
    <pre className="my-2 overflow-x-auto rounded-md bg-slate-900 p-3 text-xs leading-relaxed text-slate-50">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto rounded-md border border-slate-200 bg-white">
      <table className="min-w-full border-collapse text-left text-xs">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-slate-50 text-slate-700">{children}</thead>,
  th: ({ children }) => (
    <th className="border-b border-slate-200 px-2 py-1.5 font-semibold">{children}</th>
  ),
  td: ({ children }) => <td className="border-b border-slate-100 px-2 py-1.5">{children}</td>,
}

export default function ChatDrawer() {
  const { mode, filter } = useWorkspace()

  const [open, setOpen] = useState(false)
  const [hydrated, setHydrated] = useState(false)
  const [session, setSession] = useState<ChatSessionState | null>(null)
  const [entries, setEntries] = useState<ChatEntry[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [creating, setCreating] = useState(false)
  const [sessionError, setSessionError] = useState<string | null>(null)
  const [streamError, setStreamError] = useState<string | null>(null)
  const [scopeConflict, setScopeConflict] = useState(false)
  const [scopeMismatch, setScopeMismatch] = useState(false)
  const [drawerWidth, setDrawerWidth] = useState(DEFAULT_DRAWER_WIDTH)
  const [isResizing, setIsResizing] = useState(false)

  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const resizeCleanupRef = useRef<(() => void) | null>(null)

  // mode/filter/session 经 ref 供稳定回调读取最新值，避免把回调塞进 effect 依赖
  const wsRef = useRef({ mode, filter })
  wsRef.current = { mode, filter }
  const sessionRef = useRef<ChatSessionState | null>(session)
  sessionRef.current = session

  useEffect(() => {
    const handler = () => setOpen(true)
    window.addEventListener('open-chat', handler)
    return () => window.removeEventListener('open-chat', handler)
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [entries])

  useEffect(() => {
    const handleResize = () => setDrawerWidth(width => clampDrawerWidth(width))
    handleResize()
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  useEffect(() => {
    return () => resizeCleanupRef.current?.()
  }, [])

  const abortStream = useCallback(() => {
    // 中止在途流（关闭抽屉/范围切换/重建会话）；已产生的部分内容保留
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  /**
   * 拉取服务端会话历史并整体替换渲染（Q03：服务端为事实源，本地缓存仅离线
   * 兜底）。404 = 会话不存在/已关闭 → 清空本地残影并提示重建；409 = 范围
   * 漂移（历史保留只读、禁止复用作答）→ 冲突横幅给重建入口；其余失败静默
   * 保留本地渲染，由下次发消息的后端快照校验兜底。
   */
  const loadServerHistory = useCallback(async (targetSessionId: number) => {
    try {
      const resp = await getChatMessages(targetSessionId)
      // 会话已切换（新建/失效）时丢弃过期响应，绝不跨会话覆盖消息
      const cur = sessionRef.current
      if (cur && cur.session_id !== targetSessionId) return
      const history: ChatEntry[] = []
      for (const m of resp.messages) {
        if ((m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string' && m.content.trim()) {
          history.push({ kind: m.role, content: m.content })
        }
      }
      setEntries(history)
    } catch (err) {
      // 过期响应的失败同样丢弃，不污染已切换的新会话状态
      const cur = sessionRef.current
      if (cur && cur.session_id !== targetSessionId) return
      if (err instanceof ApiV1Error && err.status === 409) {
        setScopeConflict(true)
        return
      }
      if (err instanceof ApiV1Error && err.status === 404) {
        setSession(null)
        setEntries([])
        setSessionError('会话已不存在或已关闭，请新建会话')
      }
    }
  }, [])

  // 刷新后恢复旧会话：本地缓存先兜底渲染（离线可用），再以服务端为准——
  // GET 快照核对（契约 §1"核对用"）+ GET messages 拉取服务端历史整体替换
  // （Q03：刷新恢复不依赖浏览器存储）。会话已不存在（404）则丢弃本地残影。
  useEffect(() => {
    const stored = loadStoredChat()
    if (stored) {
      setSession({ session_id: stored.session_id, scope: stored.scope, scope_key: stored.scope_key })
      setEntries(stored.entries)
      // 核对是尽力而为：网络失败保留本地状态，真漂移由发消息时后端 409 兜底
      getChatSession(stored.session_id)
        .then(resp => {
          setSession(prev =>
            prev && prev.session_id === resp.session_id ? { ...prev, scope: resp.scope } : prev
          )
        })
        .catch((err: unknown) => {
          if (err instanceof ApiV1Error && err.status === 404) {
            setSession(null)
            setEntries([])
          }
        })
      // 服务端历史为事实源：成功即覆盖本地缓存渲染（Q03）
      void loadServerHistory(stored.session_id)
    }
    setHydrated(true)
  }, [loadServerHistory])

  // 会话绑定创建时的范围关键集（Q01：mode + 学年 + 行政班/教学班）：任一与
  // 当前工作台选择不一致（false→true 进入不一致）即中止在途流，给「新会话
  // （当前范围）/ 继续旧会话」二选一。继续旧会话不阻断——范围漂移由后端每轮
  // 快照重验 409 兜底（Q02）。
  const sessionScopeKey = session?.scope_key ?? null
  const scopeMismatchRef = useRef(false)
  useEffect(() => {
    const mismatched = sessionScopeKey
      ? !sameScopeKey(sessionScopeKey, scopeKeyOfSelection(mode, filter))
      : false
    if (mismatched && !scopeMismatchRef.current) abortStream()
    scopeMismatchRef.current = mismatched
    setScopeMismatch(mismatched)
  }, [mode, filter, sessionScopeKey, abortStream])

  // 会话/消息持久化：会话清空即移除键（新建会话 = 清空历史）；本地缓存仅作
  // 离线兜底，恢复以服务端 messages 端点为准（Q03）
  useEffect(() => {
    if (!hydrated || typeof window === 'undefined') return
    try {
      if (session) {
        window.sessionStorage.setItem(
          CHAT_STORAGE_KEY,
          JSON.stringify({
            session_id: session.session_id,
            scope: session.scope,
            scope_key: session.scope_key,
            entries,
          })
        )
      } else {
        window.sessionStorage.removeItem(CHAT_STORAGE_KEY)
      }
    } catch {
      // 隐私模式等写入失败静默：恢复是尽力而为的增强
    }
  }, [hydrated, session, entries])

  /** 新建会话（含旧会话显式关闭 + 本地历史清空）；无模型 Key → 409 引导配置。 */
  const startNewSession = useCallback(async () => {
    abortStream()
    setCreating(true)
    setSessionError(null)
    setStreamError(null)
    setScopeConflict(false)
    setScopeMismatch(false)
    const current = sessionRef.current
    if (current) {
      // 旧会话显式关闭（close 幂等；失败不阻塞新会话创建）
      void closeChatSession(current.session_id).catch(() => {})
    }
    const { mode: m, filter: f } = wsRef.current
    try {
      const resp = await createChatSession(buildCreateRequest(m, f))
      // 关键集在请求时刻缓存：后续与当前工作台选择比对用（Q01）
      setSession({
        session_id: resp.session_id,
        scope: resp.scope,
        scope_key: scopeKeyOfSelection(m, f),
      })
      setEntries([])
    } catch (err) {
      // 创建失败不留半开状态：旧会话已关闭、本地历史一并清空，只留引导信息
      setSession(null)
      setEntries([])
      setSessionError(
        err instanceof ApiV1Error ? err.detail || '会话创建失败，请稍后重试' : '网络异常，会话创建失败，请稍后重试'
      )
    } finally {
      setCreating(false)
    }
  }, [abortStream])

  // 打开抽屉且无会话时按当前工作台 mode 自动创建
  useEffect(() => {
    if (!open || !hydrated || session || creating || sessionError) return
    void startNewSession()
  }, [open, hydrated, session, creating, sessionError])

  // 打开抽屉即以服务端历史为准刷新渲染（Q03：恢复来源是 messages 端点，
  // 会话不存在/漂移由 loadServerHistory 统一处理为重建提示）
  useEffect(() => {
    if (!open || !hydrated) return
    const cur = sessionRef.current
    if (cur) void loadServerHistory(cur.session_id)
  }, [open, hydrated, loadServerHistory])

  const sendMessage = async () => {
    const content = input.trim()
    if (!content || streaming || !session || scopeConflict) return

    const currentSession = session
    const baseEntries: ChatEntry[] = [...entries, { kind: 'user', content }]
    setEntries(baseEntries)
    setInput('')
    setStreaming(true)
    setStreamError(null)

    const controller = new AbortController()
    abortRef.current = controller

    // 流内容按帧顺序累积到 collected（user 消息之后的部分），逐帧同步进 entries
    const collected: ChatEntry[] = []
    const syncView = () => setEntries([...baseEntries, ...collected])

    const appendText = (delta: string) => {
      const last = collected[collected.length - 1]
      if (last && last.kind === 'assistant') last.content += delta
      else collected.push({ kind: 'assistant', content: delta })
    }

    const findCall = (callId: unknown): ToolCallEntry | null => {
      if (typeof callId !== 'string' && typeof callId !== 'number') return null
      const id = String(callId)
      for (let i = collected.length - 1; i >= 0; i--) {
        const e = collected[i]
        if (e.kind !== 'tools') continue
        const hit = e.calls.find(c => c.call_id === id)
        if (hit) return hit
      }
      return null
    }

    const processEvent = (eventText: string) => {
      const dataLine = eventText.split('\n').find(line => line.startsWith('data:'))
      if (!dataLine) return
      try {
        const frame = JSON.parse(dataLine.slice(5).trim()) as Record<string, unknown>
        if (frame.type === 'text') {
          // 增量文本：并入最后一段助手文本（无则新开一段）
          if (typeof frame.delta === 'string' && frame.delta) appendText(frame.delta)
        } else if (frame.type === 'tool_call') {
          // 先插 loading 态工具卡，tool_result/tool_error 帧按 call_id 回填
          const call: ToolCallEntry = {
            call_id: String(frame.call_id ?? ''),
            name: String(frame.name ?? ''),
            input:
              frame.input && typeof frame.input === 'object' && !Array.isArray(frame.input)
                ? (frame.input as Record<string, unknown>)
                : {},
          }
          const last = collected[collected.length - 1]
          if (last && last.kind === 'tools') last.calls.push(call)
          else collected.push({ kind: 'tools', calls: [call] })
        } else if (frame.type === 'tool_result') {
          const call = findCall(frame.call_id)
          if (call) call.output = frame.output
        } else if (frame.type === 'tool_error') {
          const call = findCall(frame.call_id)
          if (call) call.error = typeof frame.error === 'string' ? frame.error : '工具执行失败'
        } else if (frame.type === 'error') {
          // detail 优先（Q02 范围失效帧走 detail），兼容旧 message 字段
          const msg =
            typeof frame.detail === 'string' && frame.detail
              ? frame.detail
              : typeof frame.message === 'string' && frame.message
                ? frame.message
                : '模型返回错误'
          if (/范围/.test(msg)) {
            // 工具层中止的范围失效帧：本流终止，旧上下文不得继续作答 →
            // 冲突态禁言 + 重建入口（配合后端每轮工具前的快照重验）
            setScopeConflict(true)
            setStreamError('会话范围已变化，本流已终止')
          } else if (/未配置/.test(msg)) {
            // "Key 未配置"是可自助修复的配置问题：追加配置引导文案
            setStreamError(
              `${msg} 配置方式：在 backend/.env 按 CHAT_PROVIDER 设置对应模型 Key（ANTHROPIC_API_KEY 或 OPENAI_API_KEY）后重试。`
            )
          } else {
            setStreamError(msg)
          }
        } else if (frame.type === 'done') {
          // 收尾帧：流自然结束，无额外处理
        }
        syncView()
      } catch {
        // 忽略无法解析的 SSE 帧
      }
    }

    try {
      // SSE 流沿用旧版策略绕过 Next dev 代理（~30s 超时 + 缓冲会打断长回复）：
      // dev 直连后端 8000，生产走同源相对路径（Caddy 按路径分流）。
      // 可设 NEXT_PUBLIC_CHAT_API_BASE 覆盖。
      const explicit = process.env.NEXT_PUBLIC_CHAT_API_BASE
      const isDev = process.env.NODE_ENV !== 'production'
      const chatHost = typeof window !== 'undefined' ? window.location.hostname : 'localhost'
      const chatBase = explicit ?? (isDev ? `http://${chatHost}:8000` : '')
      const res = await fetch(
        `${chatBase}/api/v1/chat/sessions/${encodeURIComponent(String(currentSession.session_id))}/messages`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
          body: JSON.stringify({ content }),
          signal: controller.signal,
        }
      )

      if (!res.ok) {
        // 流前 JSON 错误：409 link_version_conflict = 快照漂移（A02，含会话
        // 已关闭），旧上下文不得继续作答 → 显冲突横幅 + 重建入口，不叠加错误条
        const body = (await res.json().catch(() => null)) as Record<string, unknown> | null
        const code = typeof body?.error === 'string' ? body.error : `http_${res.status}`
        if (res.status === 409 && code === 'link_version_conflict') {
          setScopeConflict(true)
        }
        throw new ApiV1Error(
          res.status,
          code,
          typeof body?.detail === 'string' ? body.detail : undefined,
          body ?? undefined
        )
      }

      const reader = res.body?.getReader()
      if (!reader) throw new ApiV1Error(0, 'network_error', '当前浏览器不支持流式读取')

      const decoder = new TextDecoder()
      let finished = false
      let buffer = ''
      while (!finished) {
        const { value, done: d } = await reader.read()
        finished = d
        if (value) {
          buffer += decoder.decode(value, { stream: true })
          const events = buffer.split('\n\n')
          buffer = events.pop() || ''
          events.forEach(processEvent)
        }
      }
      if (buffer.trim()) processEvent(buffer)
    } catch (err) {
      if (controller.signal.aborted) {
        // 用户主动中止（关抽屉/切工作台/重建会话）：已产生的部分内容保留
      } else if (err instanceof ApiV1Error) {
        if (err.code !== 'link_version_conflict') {
          setStreamError(err.detail || '消息发送失败，请稍后重试')
        }
      } else {
        setStreamError('连接中断，本次回复未完成，请重试')
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      setStreaming(false)
      syncView()
    }
  }

  const handleOpenChange = (next: boolean) => {
    // 关闭抽屉即中止在途流；历史留在本地缓存兜底，重开以服务端历史为准
    if (!next) abortStream()
    setOpen(next)
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const startResize = (event: PointerEvent<HTMLButtonElement>) => {
    if (typeof window === 'undefined' || window.innerWidth < 640) return

    event.preventDefault()
    resizeCleanupRef.current?.()

    const startX = event.clientX
    const startWidth = drawerWidth
    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect

    setIsResizing(true)
    document.body.style.cursor = 'ew-resize'
    document.body.style.userSelect = 'none'

    const handlePointerMove = (moveEvent: globalThis.PointerEvent) => {
      setDrawerWidth(clampDrawerWidth(startWidth + startX - moveEvent.clientX))
    }

    const cleanup = () => {
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', cleanup)
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousUserSelect
      setIsResizing(false)
      resizeCleanupRef.current = null
    }

    resizeCleanupRef.current = cleanup
    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', cleanup, { once: true })
  }

  const renderBubble = (role: 'user' | 'assistant', content: string, key?: string | number) => {
    const isUser = role === 'user'
    return (
      <div
        key={key}
        className={cn('flex w-full items-start gap-2', isUser ? 'justify-end' : 'justify-start')}
      >
        {!isUser && (
          <Avatar className="h-8 w-8 shrink-0">
            <AvatarFallback className="bg-brand-50 text-brand-600">
              <Bot className="h-4 w-4" />
            </AvatarFallback>
          </Avatar>
        )}
        <div
          className={cn(
            'min-w-0 break-words px-3 py-2 text-sm leading-relaxed shadow-sm',
            isUser
              ? 'max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-tr-sm bg-brand-600 text-white'
              : 'max-w-[calc(100%-2.5rem)] flex-1 rounded-2xl rounded-tl-sm bg-slate-100 text-slate-900'
          )}
        >
          {isUser ? (
            content
          ) : (
            <div className="markdown-content">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                {content}
              </ReactMarkdown>
            </div>
          )}
        </div>
        {isUser && (
          <Avatar className="h-8 w-8 shrink-0">
            <AvatarFallback className="bg-brand-600 text-white">我</AvatarFallback>
          </Avatar>
        )}
      </div>
    )
  }

  const renderEntry = (entry: ChatEntry, key: string | number) => {
    if (entry.kind === 'tools') {
      // 工具调用组：ToolCallCard 自带 loading/结果/错误三态
      return (
        <div key={key} className="space-y-2 pl-10">
          {entry.calls.map((call, ci) => (
            <ToolCallCard
              key={call.call_id || `${call.name}-${ci}`}
              toolCall={{ name: call.name, input: call.input, output: call.output, error: call.error }}
            />
          ))}
        </div>
      )
    }
    return renderBubble(entry.kind === 'user' ? 'user' : 'assistant', entry.content, key)
  }

  const scopeSummary = session ? describeScope(session.scope) : null

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetContent
        side="right"
        className="print:hidden flex w-full max-w-none flex-col gap-0 p-0 sm:max-w-none"
        style={{ width: `min(100vw, ${drawerWidth}px)`, maxWidth: '100vw' }}
      >
        <button
          type="button"
          aria-label="调整对话助手宽度"
          onPointerDown={startResize}
          className={cn(
            'group absolute left-0 top-0 z-50 hidden h-full w-4 -translate-x-1/2 cursor-ew-resize items-center justify-center sm:flex',
            'focus:outline-none focus:ring-2 focus:ring-brand-500 focus:ring-offset-0',
            isResizing && 'bg-brand-500/10'
          )}
        >
          <span
            className={cn(
              'h-14 w-1 rounded-full bg-slate-300 transition-colors group-hover:bg-brand-500',
              isResizing && 'bg-brand-600'
            )}
          />
        </button>
        <SheetHeader className="border-b border-slate-200 px-5 py-4 text-left">
          <SheetTitle className="text-base font-semibold text-slate-900">AI 对话助手</SheetTitle>
          <SheetDescription className="text-xs text-slate-500">
            {scopeSummary ?? '按当前工作台范围回答成绩问题'}
          </SheetDescription>
        </SheetHeader>

        {/* 范围切换后的二选一（Q01）：mode/学年/班任一变化 → 新会话（当前范围）/
            继续旧会话（仅提醒不阻断，后端每轮快照重验兜底） */}
        {scopeMismatch && session && (
          <div role="alert" className="border-b border-warning-300 bg-warning-50 px-5 py-3 text-xs text-warning-700">
            <p>
              当前会话属于{MODE_LABEL[session.scope_key.mode]}工作台范围（{describeSessionRange(session.scope_key)}），与当前选择不一致。
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button type="button" size="sm" onClick={() => void startNewSession()}>
                新会话（当前范围）
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={() => setScopeMismatch(false)}>
                继续旧会话
              </Button>
            </div>
            <p className="mt-1.5 text-[10px] leading-relaxed">
              继续旧会话将在原范围内作答；若该范围已漂移（关联撤销/成员调整），后端会拒绝本次提问并要求新建。
            </p>
          </div>
        )}

        {/* 快照漂移 409（A02）：旧会话立即失效，只给重建出口 */}
        {scopeConflict && (
          <div role="alert" className="border-b border-danger-300 bg-danger-50 px-5 py-3 text-xs text-danger-600">
            <p>会话范围已变化（关联撤销/成员调整），旧会话不能继续作答。</p>
            <div className="mt-2">
              <Button type="button" size="sm" onClick={() => void startNewSession()}>
                新建会话
              </Button>
            </div>
          </div>
        )}

        <ScrollArea className="flex-1">
          <div className="space-y-4 px-5 py-4">
            {entries.length === 0 && !creating && (
              <div className="flex h-full items-center justify-center py-10 text-center text-xs text-slate-400">
                {sessionError
                  ? null
                  : session
                    ? '还没有对话，输入问题开始吧。'
                    : '打开抽屉时会按当前工作台范围自动创建会话。'}
              </div>
            )}
            {creating && (
              <div className="py-6 text-center text-xs text-slate-400">正在按当前工作台范围创建会话…</div>
            )}
            {sessionError && (
              <div role="alert" className="rounded-md bg-danger-50 px-3 py-2 text-xs text-danger-600">
                <p>{sessionError}</p>
                <div className="mt-2">
                  <Button type="button" size="sm" variant="outline" onClick={() => void startNewSession()}>
                    重试
                  </Button>
                </div>
              </div>
            )}
            {entries.map((entry, i) => renderEntry(entry, i))}
            <div ref={scrollRef} />
          </div>
        </ScrollArea>

        <div className="border-t border-slate-200 bg-white px-5 py-3">
          {streamError && (
            <div role="alert" className="mb-2 rounded-md bg-danger-50 px-3 py-2 text-xs text-danger-600">
              {streamError}
            </div>
          )}
          <div className="flex items-center gap-2">
            <Input
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                scopeConflict ? '会话已失效，请新建会话' : '问我任何关于当前范围成绩的问题...'
              }
              disabled={streaming || creating || !session || scopeConflict}
              className="flex-1 text-base"
            />
            <Button
              type="button"
              size="icon"
              onClick={sendMessage}
              disabled={streaming || creating || !session || scopeConflict || !input.trim()}
              aria-label="发送"
            >
              <Send className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  )
}
