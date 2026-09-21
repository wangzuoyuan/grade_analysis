'use client'

/**
 * 作业录入面板（契约 p5-homework.md §1，两段式 preview → confirm）。
 *
 * 约束：
 * - 教学工作台 subject 固定任教学科（只读，来自工作台作用域）；班主任工作台可自由输入学科；
 * - 作业种类仅教学工作台采集；班主任工作台 UI 不出现该概念，录入固定代填
 *   HOMEROOM_HOMEWORK_TYPE（后端契约仍要求非空，幂等查重不受影响）；
 * - 三模式（full=全交+例外 / names=名单 / detailed=逐行明细）切换即清空对应输入行，
 *   不同模式的行结构不同，混带必出错；
 * - preview 零写入；响应里的 existing_batches（同日同科同种类既有批次）必须醒目提示
 *   「编辑既有或新建」，绝不静默叠加（H02）；
 * - confirm 409（token 单次消费/成员漂移/范围变化）→ 中文提示 + 重新预览，
 *   绝不拿旧 token 重试；preview 422 同名歧义 → 列候选引导用学号消歧；
 * - 未提交表单走 page-draft 草稿（键按工作台分路由），confirm 成功后清除。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertCircle, CheckCircle2, ChevronDown, ChevronUp, ListPlus, Plus, RefreshCw, Trash2 } from 'lucide-react'

import {
  ApiV1Error,
  homeworkConfirm,
  homeworkPreview,
  readHomeworkAmbiguityCandidates,
  type HomeworkAmbiguityCandidate,
  type HomeworkConfirmResponse,
  type HomeworkInputKind,
  type HomeworkInputSpec,
  type HomeworkPreviewResponse,
  type HomeworkRowInput,
  type HomeworkStatus,
  type WorkspaceMode,
} from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { homeworkPreviewErrorText } from './preview-error'
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { clearPageDraft, loadPageDraft, savePageDraft } from '@/lib/page-draft'
import { cn } from '@/lib/utils'
import { splitHomeroomHomeworkText, type HomeroomSubjectGroup } from './homeroom-smart-input'
import {
  ATTENDANCE_WORDS as SMART_ATTENDANCE,
  FORGOT_WORDS as SMART_FORGOT,
  QUALITY_WORDS as SMART_QUALITY,
  STATUS_OF_ACTION as SMART_STATUS,
  FULL_PHRASE,
  pureEvaluation,
} from './homework-vocab'
import { HOMEWORK_STATUS_OPTIONS, HOMEROOM_HOMEWORK_TYPE, homeworkStatusLabel } from './shared'

const DRAFT_KEY = 'entry'

/** 一个待确认的预览批次（班主任整段粘贴多科 = 多个批次同时待确认）。 */
interface PreviewBatch {
  /** 批次键：subject + homeworkType（同科不同作业内容分列为独立批次）。 */
  key: string
  subject: string
  homeworkType?: string | null
  resp: HomeworkPreviewResponse
}

/** 未提交录入表单（草稿形状；loadPageDraft 按此收窄，损坏/不符当作无草稿）。 */
interface EntryFormState {
  subject: string
  homeworkType: string
  assignedDate: string
  dueDate: string
  kind: HomeworkInputKind
  /** 默认主入口：教师粘贴/输入的自然文本。 */
  smartText: string
  /** names 模式：每行一个姓名/学号。 */
  namesText: string
  /** full 模式例外行。 */
  exceptions: HomeworkRowInput[]
  /** detailed 模式逐人行。 */
  rows: HomeworkRowInput[]
}

function todayISO(): string {
  const now = new Date()
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 10)
}

const EMPTY_FORM: EntryFormState = {
  subject: '',
  homeworkType: '',
  assignedDate: todayISO(),
  dueDate: '',
  kind: 'full',
  smartText: '',
  namesText: '',
  exceptions: [],
  rows: [],
}

function isRowInput(v: unknown): v is HomeworkRowInput {
  if (typeof v !== 'object' || v === null) return false
  return typeof (v as Record<string, unknown>).status === 'string'
}

function isEntryForm(v: unknown): v is EntryFormState {
  if (typeof v !== 'object' || v === null) return false
  const o = v as Record<string, unknown>
  if (typeof o.subject !== 'string' || typeof o.homeworkType !== 'string') return false
  if (typeof o.assignedDate !== 'string' || typeof o.dueDate !== 'string') return false
  if (o.kind !== 'full' && o.kind !== 'names' && o.kind !== 'detailed') return false
  if (o.smartText != null && typeof o.smartText !== 'string') return false
  if (typeof o.namesText !== 'string') return false
  if (!Array.isArray(o.exceptions) || !o.exceptions.every(isRowInput)) return false
  if (!Array.isArray(o.rows) || !o.rows.every(isRowInput)) return false
  return true
}

// 教学端名单行允许空格分隔多个姓名/学号（homeroom 学生优先行的学科项不切空格，故本地保留）
const TOKEN_SPLIT = /[、，,；;\s]+/

function isSmartAction(text: string): boolean {
  return (
    SMART_ATTENDANCE.test(text) ||
    SMART_FORGOT.test(text) ||
    SMART_QUALITY.test(text) ||
    SMART_STATUS.some((item) => item.pattern.test(text))
  )
}

function extractSmartActionText(text: string): string | null {
  return (
    SMART_ATTENDANCE.exec(text)?.[0] ??
    SMART_STATUS.find((item) => item.pattern.test(text))?.pattern.exec(text)?.[0] ??
    SMART_FORGOT.exec(text)?.[0] ??
    SMART_QUALITY.exec(text)?.[0] ??
    null
  )
}

function canonicalHomeworkType(raw: string): string | null {
  if (raw.includes('校本')) return '校本作业'
  if (raw.includes('订正')) return '试卷订正'
  const cleaned = raw
    .replace(/(?:未交|缺交|请假|免交|未知|未记录|不确定|已交|交了|完成|全交|全员已交|全部已交)/g, '')
    .replace(SMART_ATTENDANCE, '')
    .replace(SMART_QUALITY, '')
    .replace(SMART_FORGOT, '')
    .trim()
  return /(?:作业|练习|默写|试卷)/.test(cleaned) ? cleaned : null
}

function rememberHomeworkType(current: string | null, next: string | null): string | null {
  if (!next) return current
  if (current && current !== next) return '__MIXED__'
  return next
}

interface SmartInputResult {
  homeworkType: string | null
  input: HomeworkInputSpec | null
  error: string | null
}

/**
 * 将旧页面的“直接输入内容”语义转成 v1 的单批次结构。
 * 支持：「校本作业：全交」、「迟到：张三、李四」、「张三 缺交」、「李四：请假」以及纯姓名已交名单。
 */
export function parseSmartHomeworkText(text: string): SmartInputResult {
  const rawLines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
  if (rawLines.length === 0) return { homeworkType: null, input: null, error: '请输入作业收交内容' }

  let homeworkType: string | null = null
  const expanded: string[] = []
  try {
    for (const line of rawLines) {
      if (FULL_PHRASE.test(line)) {
        expanded.push('全交')
        continue
      }
      const parts = line.split(/[:：]/, 2)
      if (parts.length === 2) {
        const left = parts[0].trim()
        const right = parts[1].trim()
        const rightIsFull = FULL_PHRASE.test(right)
        const leftHasAction = isSmartAction(left)
        const rightHasAction = isSmartAction(right)

        // 1. 右侧为全交（如「电阻率测量：全交」「校本作业：全交」）
        if (rightIsFull) {
          if (!leftHasAction) {
            homeworkType = rememberHomeworkType(homeworkType, canonicalHomeworkType(left) ?? left)
          }
          expanded.push('全交')
          continue
        }

        // 2. 左侧为状态动作词（如「迟到：秦五、宋彦瞳」「缺交：秦二十六、秦六」「校本缺交：李四」）
        if (leftHasAction && !rightHasAction) {
          const inferred = canonicalHomeworkType(left)
          if (inferred) homeworkType = rememberHomeworkType(homeworkType, inferred)
          const actionText = extractSmartActionText(left)
          if (!actionText) {
            return { homeworkType, input: null, error: `无法识别状态：${left}` }
          }
          const studentNames = right.split(TOKEN_SPLIT).filter(Boolean)
          for (const name of studentNames) {
            expanded.push(`${name} ${actionText}`)
          }
          continue
        }

        // 3. 学生在前，右侧为状态动作词（如「秦五：迟到」「宋彦瞳：没来」「秦二十六：缺交」）
        if (!leftHasAction && rightHasAction) {
          expanded.push(`${left} ${right}`)
          continue
        }

        // 4. 左侧为作业名称（如「电阻率测量：秦五 迟到」或「校本作业：张三」）
        if (/(?:作业|练习|默写|试卷|校本)/.test(left)) {
          homeworkType = rememberHomeworkType(homeworkType, canonicalHomeworkType(left) ?? left)
          expanded.push(right)
          continue
        }

        expanded.push(line)
        continue
      }

      // 单行无冒号：检查是否是状态在前 + 多个学生（如「迟到 秦五 宋彦瞳」「缺交 秦二十六 秦六」）
      const tokens = line.split(TOKEN_SPLIT).filter(Boolean)
      if (tokens.length >= 2) {
        const first = tokens[0]
        if (isSmartAction(first) && extractSmartActionText(first) === first) {
          const actionText = first
          for (const name of tokens.slice(1)) {
            expanded.push(`${name} ${actionText}`)
          }
          continue
        }
      }

      expanded.push(line)
    }
  } catch (err) {
    return { homeworkType, input: null, error: err instanceof Error ? err.message : '无法识别作业种类' }
  }

  const lines = expanded

  const hasFull = lines.some((line) => FULL_PHRASE.test(line))
  const rows: HomeworkRowInput[] = []
  const names: string[] = []

  for (const line of lines) {
    if (FULL_PHRASE.test(line)) continue

    // 1. 出勤异常（迟到、没来、早退等）：严格映射为 status: 'missing', attendance: '迟到'/'没来'，绝不计入已交！
    const attendanceMatch = line.match(SMART_ATTENDANCE)
    if (attendanceMatch) {
      const index = attendanceMatch.index ?? -1
      const name = line.slice(0, index).replace(/^[-•\s：:]+|[\s：:]+$/g, '').trim()
      const tail = line.slice(index + attendanceMatch[0].length).replace(/^[\s，,：:;-]+/, '').trim()
      const attendance = attendanceMatch[0]
      const evaluation = tail || null
      if (!name) return { homeworkType, input: null, error: `无法识别学生：${line}` }
      rows.push({
        name_or_alias: name,
        status: 'missing',
        attendance,
        evaluation,
      })
      continue
    }

    // 2. 忘带（忘带、没带、未带）：严格映射为 status: 'missing', evaluation: '忘带'，绝不计入已交！
    const forgotMatch = line.match(SMART_FORGOT)
    if (forgotMatch) {
      const index = forgotMatch.index ?? -1
      const nameAndType = line.slice(0, index).replace(/^[-•\s：:]+|[\s：:]+$/g, '').trim()
      const inferred = canonicalHomeworkType(nameAndType)
      if (inferred) homeworkType = rememberHomeworkType(homeworkType, inferred)
      const name = inferred
        ? nameAndType.replace(/(?:校本(?:作业)?|试卷订正|订正|作业|练习|默写|试卷).*$/, '').trim()
        : nameAndType
      if (!name) return { homeworkType, input: null, error: `无法识别学生：${line}` }
      rows.push({
        name_or_alias: name,
        status: 'missing',
        evaluation: line.slice(index).trim(),
      })
      continue
    }

    // 3. 请假、免交、缺交、已交状态
    const action = SMART_STATUS.find((item) => item.pattern.test(line))
    if (action) {
      const match = line.match(action.pattern)
      const index = match?.index ?? -1
      const nameAndType = line.slice(0, index).replace(/^[-•\s：:]+|[\s：:]+$/g, '').trim()
      const inferred = canonicalHomeworkType(nameAndType)
      if (inferred) homeworkType = rememberHomeworkType(homeworkType, inferred)
      const name = inferred
        ? nameAndType.replace(/(?:校本(?:作业)?|试卷订正|订正|作业|练习|默写|试卷).*$/, '').trim()
        : nameAndType
      const evaluation = line.slice(index + (match?.[0].length ?? 0)).replace(/^[\s，,：:;-]+/, '').trim()
      if (!name) {
        return { homeworkType, input: null, error: `无法识别学生：${line}` }
      }
      rows.push({
        name_or_alias: name,
        status: action.status,
        evaluation: evaluation || null,
      })
      continue
    }

    // 4. 质量评价（优秀、良好、差）
    const quality = line.match(SMART_QUALITY)
    if (quality) {
      const index = quality.index ?? -1
      const nameAndType = line.slice(0, index).trim()
      const inferred = canonicalHomeworkType(nameAndType)
      if (inferred) homeworkType = rememberHomeworkType(homeworkType, inferred)
      const name = inferred
        ? nameAndType.replace(/(?:校本(?:作业)?|试卷订正|订正|作业|练习|默写|试卷).*$/, '').trim()
        : nameAndType
      if (!name) return { homeworkType, input: null, error: `无法识别学生：${line}` }
      rows.push({
        name_or_alias: name,
        status: 'submitted',
        evaluation: line.slice(index).trim(),
      })
      continue
    }

    // 5. 纯姓名名单
    names.push(line.replace(/^[-•]\s*/, '').trim())
  }

  if (homeworkType === '__MIXED__') {
    return { homeworkType: null, input: null, error: '一次预览只能录入一种作业，请按作业种类分开录入' }
  }

  if (hasFull) {
    if (names.length > 0) {
      return { homeworkType, input: null, error: `全员已交后的例外须写明状态：${names[0]}` }
    }
    return { homeworkType, input: { kind: 'full', exceptions: rows }, error: null }
  }
  if (rows.length > 0 && names.length > 0) {
    return { homeworkType, input: null, error: '请不要混合“纯姓名名单”和“姓名+状态”，或补齐每人状态' }
  }
  if (rows.length > 0) return { homeworkType, input: { kind: 'detailed', rows }, error: null }
  return { homeworkType, input: { kind: 'names', names }, error: null }
}

/** names 文本 → 每行一个姓名/学号（空行丢弃）。 */
function parseNames(text: string): string[] {
  return text
    .split('\n')
    .map((s) => s.trim())
    .filter((s) => s !== '')
}

/** 把表单组装成 preview 的 input 规格：空引用行丢弃，评价空串归 null。 */
function buildInput(form: EntryFormState): HomeworkInputSpec {
  if (form.kind === 'full') {
    return {
      kind: 'full',
      exceptions: form.exceptions
        .filter((r) => (r.name_or_alias ?? '').trim() !== '' || r.person_id != null)
        .map((r) => ({
          name_or_alias: (r.name_or_alias ?? '').trim() || null,
          status: r.status,
          attendance: (r.attendance ?? '').trim() || null,
          evaluation: (r.evaluation ?? '').trim() || null,
        })),
    }
  }
  if (form.kind === 'names') {
    return { kind: 'names', names: parseNames(form.namesText) }
  }
  return {
    kind: 'detailed',
    rows: form.rows
      .filter((r) => (r.name_or_alias ?? '').trim() !== '' || r.person_id != null)
      .map((r) => ({
        name_or_alias: (r.name_or_alias ?? '').trim() || null,
        status: r.status,
        attendance: (r.attendance ?? '').trim() || null,
        evaluation: (r.evaluation ?? '').trim() || null,
      })),
  }
}

/** 状态选择（原生 select：行编辑器里逐行渲染，Radix Select 过重）。 */
function StatusSelect({
  value,
  onChange,
  label,
}: {
  value: string
  onChange: (v: HomeworkStatus) => void
  label: string
}) {
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value as HomeworkStatus)}
      className="h-9 rounded-md border border-slate-200 bg-white px-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {HOMEWORK_STATUS_OPTIONS.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  )
}

const KIND_OPTIONS: Array<{ value: HomeworkInputKind; label: string; hint: string }> = [
  { value: 'full', label: '全交台账', hint: '默认全员已交，只补记缺交或请假例外' },
  { value: 'names', label: '交名单', hint: '只列出的学生记为已交；其余不写行、不推断缺交' },
  { value: 'detailed', label: '逐行明细', hint: '逐人录入已交、缺交、请假与评价' },
]

function displayEvaluation(evaluation: string | null | undefined, attendance: string | null | undefined): string {
  return pureEvaluation(evaluation, attendance) || '—'
}

export function HomeworkEntryPanel({
  mode,
  scopeSubject,
  teachingClassId,
  academicYearId,
  onConfirmed,
}: {
  mode: WorkspaceMode
  /** 教学工作台的任教学科（作用域解析）；班主任工作台传 null。 */
  scopeSubject: string | null
  /**
   * 教学工作台显式选择的教学班 id（写入路径绝不往"全部所教班并集"写批次）：
   * 未显式选择时不传，后端单班自动命中、多班 422 引导先选班。
   */
  teachingClassId?: number
  /**
   * 当前工作台选择的学年 id：preview/confirm 作用域必须与教学班同属一个
   * 选择上下文，历史学年录入不回退"最新学年"（不用教学班 ID 代替学年）。
   */
  academicYearId?: number
  /** confirm 成功后通知父容器刷新批次列表等资源。 */
  onConfirmed: () => void
}) {
  const teaching = mode === 'teaching'
  // 草稿键按工作台分路由：两工作台页共用本组件，绝不能互串草稿
  const draftRoute = `/${mode}/homework`

  const [form, setForm] = useState<EntryFormState>(EMPTY_FORM)
  const [previews, setPreviews] = useState<PreviewBatch[]>([])
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [candidates, setCandidates] = useState<HomeworkAmbiguityCandidate[] | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [confirmingAll, setConfirmingAll] = useState(false)
  const [confirmErrors, setConfirmErrors] = useState<Record<string, string>>({})
  const [confirmResults, setConfirmResults] = useState<Array<{ subject: string; result: HomeworkConfirmResponse }>>([])
  const [showAllRows, setShowAllRows] = useState<Record<string, boolean>>({})

  const restoredRef = useRef(false)
  useEffect(() => {
    if (restoredRef.current) return
    restoredRef.current = true
    const d = loadPageDraft<EntryFormState>(draftRoute, DRAFT_KEY, isEntryForm)
    if (d) {
      // 教学域 subject 固定任教学科：恢复草稿时以当前作用域为准，不复活旧值
      const restored = { ...EMPTY_FORM, ...d, smartText: d.smartText ?? '' }
      setForm(teaching ? { ...restored, subject: scopeSubject ?? restored.subject } : restored)
    }
  }, [draftRoute, teaching, scopeSubject])

  /** 任何表单修改即时落草稿（sessionStorage，尽力而为）。 */
  const patchForm = useCallback(
    (patch: Partial<EntryFormState>) => {
      setForm((prev) => {
        const next = { ...prev, ...patch }
        savePageDraft<EntryFormState>(draftRoute, DRAFT_KEY, next)
        return next
      })
    },
    [draftRoute],
  )

  function switchKind(kind: HomeworkInputKind) {
    if (kind === form.kind) return
    // 模式切换清空输入行（任务书 §录入面板）；旧预览不再代表当前输入，一并作废
    patchForm({ kind, exceptions: [], rows: [], namesText: '' })
    setPreviews([])
    setShowAllRows({})
    setPreviewError(null)
    setCandidates(null)
    setConfirmResults([])
    setConfirmErrors({})
  }

  /** 行编辑统一入口：index<0 表示追加新行。 */
  function updateRow(list: 'exceptions' | 'rows', index: number, patch: Partial<HomeworkRowInput>) {
    setForm((prev) => {
      const arr = list === 'exceptions' ? prev.exceptions : prev.rows
      const next =
        index < 0
          ? [...arr, { name_or_alias: '', status: 'missing' as HomeworkStatus, evaluation: '', ...patch }]
          : arr.map((r, i) => (i === index ? { ...r, ...patch } : r))
      const nextState: EntryFormState = { ...prev, [list]: next }
      savePageDraft<EntryFormState>(draftRoute, DRAFT_KEY, nextState)
      return nextState
    })
  }

  function removeRow(list: 'exceptions' | 'rows', index: number) {
    setForm((prev) => {
      const arr = (list === 'exceptions' ? prev.exceptions : prev.rows).filter((_, i) => i !== index)
      const nextState: EntryFormState = { ...prev, [list]: arr }
      savePageDraft<EntryFormState>(draftRoute, DRAFT_KEY, nextState)
      return nextState
    })
  }

  const effectiveSubject = teaching ? (scopeSubject ?? '') : form.subject.trim()
  // 班主任工作台不采集种类（UI 无此概念）：固定代填，满足后端非空契约与幂等查重
  const smart = form.smartText.trim() ? parseSmartHomeworkText(form.smartText) : null
  const effectiveHomeworkType = teaching
    ? form.homeworkType.trim() || smart?.homeworkType || ''
    : HOMEROOM_HOMEWORK_TYPE
  // 结构化备用入口也有有效输入（全交台账零例外同样合法）
  const hasInput =
    form.smartText.trim() !== '' ||
    form.kind === 'full' ||
    (form.kind === 'names' && parseNames(form.namesText).length > 0) ||
    (form.kind === 'detailed' && form.rows.some((r) => (r.name_or_alias ?? '').trim() !== ''))
  const hasTeachingClass = !teaching || typeof teachingClassId === 'number'
  // 班主任整段「学科：名单」：学科由文本携带，不强制手填学科字段
  // （左侧无学科词的待定学科行同理：允许进入预览流程，由 buildGroups 给出精准报错或按默认学科补齐）
  const homeroomSplit = !teaching && form.smartText.trim() ? splitHomeroomHomeworkText(form.smartText) : null
  const homeroomSubjectReady =
    teaching ||
    form.subject.trim() !== '' ||
    (homeroomSplit?.subjectGroups.length ?? 0) > 0 ||
    (homeroomSplit?.unmarkedGroups.length ?? 0) > 0
  const canPreview =
    homeroomSubjectReady &&
    (teaching ? effectiveSubject !== '' : true) &&
    effectiveHomeworkType !== '' &&
    form.assignedDate !== '' &&
    hasInput &&
    hasTeachingClass

  /**
   * 统一组装本次要预览的批次组：教学 = 单学科单批次；班主任 =
   * 「学科：」前缀行逐科分组 + 无前缀行归入手填学科（沿用既有语义）。
   */
  function buildGroups(): HomeroomSubjectGroup[] | { error: string } {
    if (teaching) {
      const smartResult = form.smartText.trim() ? parseSmartHomeworkText(form.smartText) : null
      if (smartResult?.error) return { error: smartResult.error }
      if (!smartResult && form.kind === 'names' && parseNames(form.namesText).length === 0) {
        return { error: '请填写至少一名学生（每行一个姓名或学号）' }
      }
      if (
        !smartResult && form.kind === 'detailed' &&
        !form.rows.some((r) => (r.name_or_alias ?? '').trim() !== '' || r.person_id != null)
      ) {
        return { error: '请至少添加一行明细' }
      }
      return [{ subject: effectiveSubject, input: smartResult?.input ?? buildInput(form) }]
    }
    const groups: HomeroomSubjectGroup[] = []
    if (homeroomSplit) {
      if (homeroomSplit.error) return { error: homeroomSplit.error }
      groups.push(...homeroomSplit.subjectGroups)
    }
    if (homeroomSplit && homeroomSplit.plainLines.length > 0) {
      if (form.subject.trim() === '') {
        return {
          error: `存在未标注学科的行（如「${homeroomSplit.plainLines[0]}」）：请在行首加「学科：」，或先在上方填写学科`,
        }
      }
      const plain = parseSmartHomeworkText(homeroomSplit.plainLines.join('\n'))
      if (plain.error) return { error: plain.error }
      if (plain.input) groups.push({ subject: form.subject.trim(), input: plain.input })
    }
    // 待定学科组（如「听力第四周：张三」）：作业名保留、学科由默认学科补，绝不自建假学科
    if (homeroomSplit && homeroomSplit.unmarkedGroups.length > 0) {
      if (form.subject.trim() === '') {
        return {
          error: `存在未标注学科的行（如「${homeroomSplit.unmarkedGroups[0].line}」）：请在行首加「学科：」，或先在上方填写学科`,
        }
      }
      for (const g of homeroomSplit.unmarkedGroups) {
        groups.push({ subject: form.subject.trim(), homeworkType: g.homeworkType, input: g.input })
      }
    }
    if (groups.length === 0) {
      if (form.subject.trim() === '') {
        return { error: '请先填写学科，或在输入中使用「学科：姓名」格式（如 物理：张三、李四）' }
      }
      return [{ subject: form.subject.trim(), input: buildInput(form) }]
    }
    return groups
  }

  async function runPreview() {
    if (teaching && typeof teachingClassId !== 'number') {
      setPreviewError('请先在页面上方选择一个具体教学班')
      return
    }
    if (!form.assignedDate) {
      setPreviewError('请先选择布置日期')
      return
    }
    if (!hasInput) {
      setPreviewError('请输入收交或考勤内容')
      return
    }
    if (!canPreview) {
      setPreviewError(
        teaching
          ? '请先填写学科、作业种类与布置日期'
          : '未识别到有效学科或考勤：请在下方「默认学科」填写学科，或在输入中使用「学科：姓名」格式',
      )
      return
    }
    const groups = buildGroups()
    if ('error' in groups) {
      setPreviewError(groups.error)
      return
    }
    setPreviewing(true)
    setPreviewError(null)
    setCandidates(null)
    setPreviews([])
    setConfirmResults([])
    setConfirmErrors({})
    const okBatches: PreviewBatch[] = []
    const failTexts: string[] = []
    try {
      for (const [groupIndex, g] of groups.entries()) {
        try {
          // 教学端批次作业名来自表单/智能识别；班主任端来自文本中的作业内容拆分
          const batchHomeworkType = g.homeworkType || (teaching ? effectiveHomeworkType : null)
          const resp = await homeworkPreview({
            mode,
            // 学年与教学班来自同一选择上下文：历史学年录入不得回退最新学年
            ...(typeof academicYearId === 'number' ? { academic_year_id: academicYearId } : {}),
            // 教学域显式班选择优先；未选班时交由后端单班自动命中 / 多班 422 引导
            ...(teaching && typeof teachingClassId === 'number' ? { teaching_class_id: teachingClassId } : {}),
            subject: g.subject,
            homework_type: batchHomeworkType || effectiveHomeworkType,
            assigned_date: form.assignedDate,
            due_date: form.dueDate !== '' ? form.dueDate : undefined,
            input: g.input,
          })
          // 键追加序号保证唯一：同日同科（学科行 + 默认学科无前缀行）可能并存多批
          const batchKey = `${batchHomeworkType ? `${g.subject} · ${batchHomeworkType}` : g.subject}#${groupIndex}`
          okBatches.push({ key: batchKey, subject: g.subject, homeworkType: batchHomeworkType, resp })
        } catch (err) {
          if (err instanceof ApiV1Error && err.body?.param === 'teaching_class_id') {
            // 多班 422 的通用文案不含引导：指明先在工作台顶栏选具体班
            failTexts.push('您教多个教学班，请先在工作台顶部选择具体班级后再录入')
          } else {
            const errCandidates = readHomeworkAmbiguityCandidates(err)
            if (errCandidates) setCandidates(errCandidates)
            // invalid_scope_param + name_or_alias 点名"该学生不在名册"，不再误报
            // 学年/班级参数错误；同名/同号歧义也在此给出专属主提示
            failTexts.push(
              `${groups.length > 1 ? `【${g.subject}】` : ''}${homeworkPreviewErrorText(err, teaching ? '教学班' : '班级')}`,
            )
          }
        }
      }
    } finally {
      setPreviewing(false)
    }
    if (okBatches.length > 0) setPreviews(okBatches)
    if (failTexts.length > 0) setPreviewError(failTexts.join('\n'))
  }

  /** 一键确认全部待确认批次：逐批消费各自 token；某批失败只记录该科错误，
   * 成功的批次从待确认中移除，全部成功才清草稿重置表单。 */
  async function runConfirmAll() {
    if (previews.length === 0 || confirmingAll) return
    setConfirmingAll(true)
    const newResults: Array<{ subject: string; result: HomeworkConfirmResponse }> = []
    const newErrors: Record<string, string> = {}
    for (const batch of previews) {
      try {
        const result = await homeworkConfirm(batch.resp.token)
        newResults.push({ subject: batch.subject, result })
      } catch (err) {
        // 409 = token 已消费/成员漂移/范围变化（R4 语义）：旧 token 绝不重试
        newErrors[batch.key] =
          err instanceof ApiV1Error && err.status === 409
            ? '预览已过期或班级成员已变化，请重新预览后再确认（原 token 已作废）'
            : apiErrorMessage(err)
      }
    }
    if (newResults.length > 0) {
      setConfirmResults((prev) => [...prev, ...newResults])
      onConfirmed()
    }
    if (Object.keys(newErrors).length === 0) {
      setPreviews([])
      setShowAllRows({})
      setConfirmErrors({})
      clearPageDraft(draftRoute, DRAFT_KEY)
      setForm({ ...EMPTY_FORM, assignedDate: todayISO() })
    } else {
      // 只留失败批次（成功的不重复确认，token 单次消费）；失败科重新预览即可
      setPreviews((prev) => prev.filter((b) => b.key in newErrors))
      setConfirmErrors(newErrors)
    }
    setConfirmingAll(false)
  }

  return (
    <div className="space-y-4">
      <Card className="print:hidden">
        <CardHeader>
          <CardTitle>作业录入</CardTitle>
          <CardDescription>
            两段式：先预览解析（零写入），确认后单事务入库；同批次重复确认不新增（幂等）。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2 rounded-xl border border-brand-200 bg-gradient-to-br from-brand-50/80 to-white p-4">
            <div>
              <label className="text-sm font-semibold text-slate-800" htmlFor="hw-smart-text">
                直接输入收交情况
              </label>
              <p className="mt-0.5 text-xs text-slate-500">
                {teaching
                  ? '系统自动识别全交、已交名单或逐人状态，先预览再入库。'
                  : '整段粘贴全科收交与考勤：支持「学科：姓名」、「姓名：学科」及「迟到：姓名」、「请假：姓名」等独立考勤单列。'}
              </p>
            </div>
            <textarea
              id="hw-smart-text"
              rows={7}
              value={form.smartText}
              onChange={(e) => {
                patchForm({ smartText: e.target.value })
                setPreviews([])
                setPreviewError(null)
              }}
              placeholder={
                teaching
                  ? '校本作业：全交\n张三 缺交 忘带\n李四：请假'
                  : '物理：秦三，秦二\n数学：全交\n秦一：语文作文，数学\n迟到：刘雨琪\n请假：王五'
              }
              className="w-full resize-y rounded-lg border border-brand-200 bg-white px-3 py-3 text-sm leading-6 shadow-inner focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
            />
            <p className="text-[11px] text-slate-500">
              {teaching
                ? '也可每行只写一个姓名/学号，表示这些人已交；未列出的人不会被推断为缺交。'
                : '支持按学科记（学科：名单）、按学生记（姓名：学科1，学科2），以及纯考勤记（迟到：姓名、请假：姓名）。考勤自动单列，不按学科计入缺交。'}
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-500" htmlFor="hw-subject">
                {teaching ? '学科（任教学科，固定）' : '默认学科（可选）'}
              </label>
              <Input
                id="hw-subject"
                value={effectiveSubject}
                onChange={(e) => patchForm({ subject: e.target.value })}
                disabled={teaching}
                placeholder={teaching ? '作用域解析中…' : '如 物理'}
              />
              {teaching ? (
                <p className="text-[10px] text-slate-400">教学工作台只能录入任教学科作业</p>
              ) : (
                <p className="text-[10px] text-slate-400">
                  仅用于输入里没写「学科：」前缀的行；写了前缀的行以行首学科为准
                </p>
              )}
            </div>
            {teaching ? (
              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-500" htmlFor="hw-type">
                  作业种类
                </label>
                <Input
                  id="hw-type"
                  value={form.homeworkType}
                  onChange={(e) => patchForm({ homeworkType: e.target.value })}
                  placeholder="如 家庭作业 / 练习册 / 默写"
                />
              </div>
            ) : null}
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-500" htmlFor="hw-assigned">
                布置日期
              </label>
              <Input
                id="hw-assigned"
                type="date"
                value={form.assignedDate}
                onChange={(e) => patchForm({ assignedDate: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-500" htmlFor="hw-due">
                截止日期（可选）
              </label>
              <Input
                id="hw-due"
                type="date"
                value={form.dueDate}
                onChange={(e) => patchForm({ dueDate: e.target.value })}
              />
            </div>
          </div>

          {/* 结构化编辑作为备用校正入口，不再抢占主流程。 */}
          <details className="rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2">
            <summary className="cursor-pointer text-sm font-medium text-slate-600">改用结构化录入</summary>
          <div className="mt-3 space-y-2">
            <div role="group" aria-label="录入模式" className="flex flex-wrap gap-2">
              {KIND_OPTIONS.map((o) => (
                <button
                  key={o.value}
                  type="button"
                  aria-pressed={form.kind === o.value}
                  onClick={() => switchKind(o.value)}
                  className={cn(
                    'rounded-md border px-3 py-1.5 text-sm font-medium transition-colors',
                    form.kind === o.value
                      ? 'border-brand-200 bg-brand-50 text-brand-700'
                      : 'border-slate-200 bg-white text-slate-500 hover:bg-slate-50',
                  )}
                >
                  {o.label}
                </button>
              ))}
            </div>
            <p className="text-xs text-slate-400">
              {KIND_OPTIONS.find((o) => o.value === form.kind)?.hint}
            </p>
          </div>

          {form.kind === 'full' ? (
            <div className="space-y-2">
              <p className="text-xs font-medium text-slate-500">
                例外行（默认全员已交；仅在此列出缺交或请假的人）
              </p>
              {form.exceptions.map((row, i) => (
                <div key={i} className="flex flex-col gap-2 sm:flex-row">
                  <Input
                    className="sm:w-44"
                    placeholder="姓名或学号"
                    value={row.name_or_alias ?? ''}
                    onChange={(e) => updateRow('exceptions', i, { name_or_alias: e.target.value })}
                    aria-label={`例外行 ${i + 1} 姓名/学号`}
                  />
                  <StatusSelect
                    label={`例外行 ${i + 1} 状态`}
                    value={row.status}
                    onChange={(v) => updateRow('exceptions', i, { status: v })}
                  />
                  <Input
                    className="sm:w-32"
                    placeholder="如：迟到、没来"
                    value={row.attendance ?? ''}
                    onChange={(e) => updateRow('exceptions', i, { attendance: e.target.value })}
                    aria-label={`例外行 ${i + 1} 出勤异常`}
                  />
                  <Input
                    className="sm:flex-1"
                    placeholder="评价（可选）"
                    value={row.evaluation ?? ''}
                    onChange={(e) => updateRow('exceptions', i, { evaluation: e.target.value })}
                    aria-label={`例外行 ${i + 1} 评价`}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="删除例外行"
                    onClick={() => removeRow('exceptions', i)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => updateRow('exceptions', -1, { status: 'missing' })}
              >
                <Plus className="h-4 w-4" /> 添加例外
              </Button>
            </div>
          ) : null}

          {form.kind === 'names' ? (
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-500" htmlFor="hw-names">
                已交名单（每行一个姓名或学号；学号优先消歧）
              </label>
              <textarea
                id="hw-names"
                rows={6}
                value={form.namesText}
                onChange={(e) => patchForm({ namesText: e.target.value })}
                placeholder={'张三\n138021\n李四'}
                className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              <p className="text-[10px] text-slate-400">
                仅名单内的人记为已交；名单外不写行、不推断缺交（无记录 ≠ 已交）。
              </p>
            </div>
          ) : null}

          {form.kind === 'detailed' ? (
            <div className="space-y-2">
              <p className="text-xs font-medium text-slate-500">逐行明细（同班同名请用学号消歧）</p>
              {form.rows.map((row, i) => (
                <div key={i} className="flex flex-col gap-2 sm:flex-row">
                  <Input
                    className="sm:w-44"
                    placeholder="姓名或学号"
                    value={row.name_or_alias ?? ''}
                    onChange={(e) => updateRow('rows', i, { name_or_alias: e.target.value })}
                    aria-label={`明细行 ${i + 1} 姓名/学号`}
                  />
                  <StatusSelect
                    label={`明细行 ${i + 1} 状态`}
                    value={row.status}
                    onChange={(v) => updateRow('rows', i, { status: v })}
                  />
                  <Input
                    className="sm:w-32"
                    placeholder="如：迟到、没来"
                    value={row.attendance ?? ''}
                    onChange={(e) => updateRow('rows', i, { attendance: e.target.value })}
                    aria-label={`明细行 ${i + 1} 出勤异常`}
                  />
                  <Input
                    className="sm:flex-1"
                    placeholder="评价（可选）"
                    value={row.evaluation ?? ''}
                    onChange={(e) => updateRow('rows', i, { evaluation: e.target.value })}
                    aria-label={`明细行 ${i + 1} 评价`}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="删除明细行"
                    onClick={() => removeRow('rows', i)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
              <Button type="button" variant="outline" size="sm" onClick={() => updateRow('rows', -1, {})}>
                <Plus className="h-4 w-4" /> 添加一行
              </Button>
            </div>
          ) : null}
          </details>

          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" onClick={runPreview} disabled={previewing || !hasInput}>
              {previewing ? '解析中…' : '预览解析'}
            </Button>
            {previewError ? (
              <Button type="button" variant="outline" size="sm" onClick={runPreview} disabled={previewing}>
                <RefreshCw className="h-4 w-4" /> 重新预览
              </Button>
            ) : null}
          </div>

          {previewError ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
              <p className="flex items-start gap-2 text-sm text-amber-800">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                {previewError}
              </p>
              {candidates != null ? (
                <div className="mt-2 space-y-1 pl-6">
                  <p className="text-xs font-medium text-amber-800">
                    同班存在多名同名学生，请改用学号（或让班主任提供人员编号）后重试：
                  </p>
                  <ul className="list-disc pl-4 text-xs text-amber-700">
                    {candidates.map((c) => (
                      <li key={String(c.person_id)}>
                        {c.name ?? '（未命名）'}
                        {c.alias ? ` · 学号 ${c.alias}` : ''} · person_id {String(c.person_id)}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}
        </CardContent>
      </Card>

      {/* 预览卡（零写入解析结果）：多学科合并为一张卡，一键确认全部批次 */}
      {previews.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>
              预览确认
              {previews.length > 1 ? `（${previews.length} 科）` : ''}
            </CardTitle>
            <CardDescription>
              {previews
                .map((b) =>
                  b.homeworkType && b.homeworkType !== HOMEROOM_HOMEWORK_TYPE
                    ? `${b.subject}·${b.homeworkType}`
                    : b.subject,
                )
                .join(' · ')}{' '}
              · {form.assignedDate}
              {form.dueDate ? `（截止 ${form.dueDate}）` : ''}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {previews.map((batch) => {
              const isAttendance = batch.subject === '考勤'
              const subs = batch.resp.assignment.submissions
              const counts = {
                submitted: subs.filter((s) => s.status === 'submitted' && !s.attendance).length,
                missing: subs.filter((s) => s.status === 'missing').length,
                excused: subs.filter((s) => s.status === 'excused').length,
                attendance: subs.filter((s) => Boolean(s.attendance)).length,
              }
              // 出勤异常 = 注明出勤异常的学生 + 未注明原因的缺勤（未到校）
              const attAnomaly = isAttendance
                ? counts.attendance + subs.filter((s) => s.status === 'missing' && !s.attendance).length
                : counts.attendance
              const abnormalSubs = subs.filter(
                (s) => s.status !== 'submitted' || Boolean(s.attendance) || Boolean(s.evaluation) || Boolean(s.special_note),
              )
              const normalSubs = subs.filter(
                (s) => s.status === 'submitted' && !s.attendance && !s.evaluation && !s.special_note,
              )
              const isExpanded = Boolean(showAllRows[batch.key])
              const hasExceptions = abnormalSubs.length > 0
              const displaySubs = isExpanded || !hasExceptions ? subs : abnormalSubs

              return (
                <div key={batch.key} className="space-y-2 rounded-lg border border-slate-200 p-3">
                  <p className="text-sm font-semibold text-slate-800">
                    {batch.subject}
                    {batch.homeworkType && batch.homeworkType !== HOMEROOM_HOMEWORK_TYPE ? (
                      <span className="ml-1.5 inline-flex items-center rounded bg-slate-100 px-1.5 py-0.5 text-xs font-normal text-slate-700">
                        {batch.homeworkType}
                      </span>
                    ) : null}
                    <span className="ml-2 font-normal text-xs text-slate-500">
                      {teaching ? `${batch.resp.assignment.homework_type} · ` : ''}
                      {isAttendance
                        ? `正常出勤 ${String(subs.length - counts.missing - counts.excused)} · 出勤异常 ${String(attAnomaly)} · 请假 ${String(counts.excused)}`
                        : subs.length > 0
                          ? `已交 ${String(counts.submitted)} · 缺交 ${String(counts.missing)} · 请假 ${String(counts.excused)}`
                          : '全员默认已交，无例外'}
                    </span>
                  </p>

                  {batch.resp.existing_batches.length > 0 ? (
                    <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                      <p className="flex items-start gap-2 font-medium">
                        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                        同日同科{teaching ? '同种类' : ''}已有 {String(batch.resp.existing_batches.length)} 个批次
                        （{batch.resp.existing_batches
                          .map((b) => `#${String(b.assignment_id)}`)
                          .join('、')}
                        ）
                      </p>
                      <p className="mt-1 pl-6 text-xs">
                        可到「作业记录」编辑既有批次；此时确认将新建独立批次——同日多份作业绝不自动叠加。
                      </p>
                    </div>
                  ) : null}

                  {batch.resp.assignment.warnings.map((w, i) => (
                    <div
                      key={i}
                      className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
                    >
                      <ListPlus className="mt-0.5 h-4 w-4 shrink-0" />
                      {w}
                    </div>
                  ))}

                  {subs.length > 0 ? (
                    !hasExceptions && !isExpanded ? (
                      <div className="flex items-center justify-between rounded-lg border border-emerald-200 bg-emerald-50/70 px-3.5 py-2.5 text-xs text-emerald-800">
                        <span className="font-medium">
                          ✓ 全班 {String(subs.length)} 名学生均{isAttendance ? '正常到校' : '默认已交'}，无缺交或异常
                        </span>
                        {normalSubs.length > 0 ? (
                          <button
                            type="button"
                            onClick={() => setShowAllRows((p) => ({ ...p, [batch.key]: true }))}
                            className="ml-2 text-xs font-normal text-emerald-700 underline hover:text-emerald-900"
                          >
                            查看全班名单 ({String(subs.length)}人)
                          </button>
                        ) : null}
                      </div>
                    ) : (
                      <div className="space-y-2">
                        <div className="overflow-x-auto">
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead className="text-xs">学生</TableHead>
                                <TableHead className="text-xs">{isAttendance ? '考勤状态' : '状态'}</TableHead>
                                {isAttendance ? (
                                  <TableHead className="text-xs">说明 / 备注</TableHead>
                                ) : (
                                  <>
                                    <TableHead className="text-xs">出勤异常</TableHead>
                                    <TableHead className="text-xs">作业内容</TableHead>
                                    <TableHead className="text-xs">评价</TableHead>
                                  </>
                                )}
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {displaySubs.map((s) => {
                                const isForgot = Boolean(
                                  (s.special_note && /(?:忘带|没带|未带)/.test(s.special_note)) ||
                                  /(?:忘带|没带|未带)/.test(s.evaluation ?? '')
                                )
                                return (
                                <TableRow key={String(s.person_id)}>
                                  <TableCell className="whitespace-nowrap text-sm font-medium text-slate-800">
                                    {s.name ?? '（未命名）'}
                                  </TableCell>
                                  <TableCell className="whitespace-nowrap text-sm">
                                    {isAttendance ? (
                                      s.attendance ? (
                                        <Badge variant="destructive">{s.attendance}</Badge>
                                      ) : s.status === 'excused' ? (
                                        <Badge variant="outline">请假</Badge>
                                      ) : s.status === 'missing' ? (
                                        <Badge variant="destructive">未到校</Badge>
                                      ) : (
                                        <Badge variant="success">正常出勤</Badge>
                                      )
                                    ) : (
                                      <div className="inline-flex items-center gap-1">
                                        <Badge
                                          variant={
                                            s.status === 'submitted'
                                              ? 'success'
                                              : s.status === 'missing'
                                                ? 'destructive'
                                                : 'outline'
                                          }
                                        >
                                          {homeworkStatusLabel(s.status)}
                                        </Badge>
                                        {isForgot ? (
                                          <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-700 text-xs px-1.5 py-0.5">
                                            忘带
                                          </Badge>
                                        ) : null}
                                      </div>
                                    )}
                                  </TableCell>
                                  {isAttendance ? (
                                    <TableCell className="whitespace-nowrap text-sm text-slate-500">
                                      {displayEvaluation(s.evaluation, s.attendance) !== '—'
                                        ? displayEvaluation(s.evaluation, s.attendance)
                                        : s.attendance
                                          ? `已记录${s.attendance}`
                                          : '—'}
                                    </TableCell>
                                  ) : (
                                    <>
                                      <TableCell className="whitespace-nowrap text-sm text-slate-500">
                                        {s.attendance ?? '—'}
                                      </TableCell>
                                      <TableCell className="whitespace-nowrap text-sm text-slate-500">
                                        {batch.homeworkType && batch.homeworkType !== HOMEROOM_HOMEWORK_TYPE
                                          ? batch.homeworkType
                                          : '—'}
                                      </TableCell>
                                      <TableCell className="whitespace-nowrap text-sm text-slate-500">
                                        {displayEvaluation(s.evaluation, s.attendance)}
                                      </TableCell>
                                    </>
                                  )}
                                </TableRow>
                              )})}
                            </TableBody>
                          </Table>
                        </div>

                        {normalSubs.length > 0 ? (
                          <div className="flex items-center justify-between rounded-md border border-slate-200/80 bg-slate-50 px-3 py-1.5 text-xs text-slate-600">
                            <span>
                              全班其余 <strong className="font-semibold text-slate-800">{String(normalSubs.length)}</strong> 名学生默认
                              {isAttendance ? '正常出勤' : '已交'}
                            </span>
                            <button
                              type="button"
                              onClick={() => setShowAllRows((p) => ({ ...p, [batch.key]: !isExpanded }))}
                              className="ml-2 inline-flex items-center gap-1 font-medium text-xs text-brand-600 hover:text-brand-800"
                            >
                              {isExpanded ? (
                                <>
                                  收起，仅看异常 ({String(abnormalSubs.length)}人) <ChevronUp className="h-3.5 w-3.5" />
                                </>
                              ) : (
                                <>
                                  展开查看全班名单 ({String(subs.length)}人) <ChevronDown className="h-3.5 w-3.5" />
                                </>
                              )}
                            </button>
                          </div>
                        ) : null}
                      </div>
                    )
                  ) : null}

                  {confirmErrors[batch.key] ? (
                    <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                      <p className="flex items-start gap-2">
                        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                        {confirmErrors[batch.key]}
                      </p>
                    </div>
                  ) : null}
                </div>
              )
            })}

            <div className="flex flex-wrap items-center gap-2">
              <Button type="button" onClick={runConfirmAll} disabled={confirmingAll}>
                {confirmingAll
                  ? '入库中…'
                  : previews.length > 1
                    ? `一键确认入库（${previews.length} 科）`
                    : '确认入库'}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={runPreview}
                disabled={previewing || confirmingAll}
              >
                <RefreshCw className="h-4 w-4" /> 重新预览
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}
      {previewing ? (
        <Card>
          <CardContent className="py-6">
            <Skeleton className="h-24 w-full" />
          </CardContent>
        </Card>
      ) : null}

      {confirmResults.length > 0 ? (
        <Card className="border-success-100">
          <CardContent className="flex items-start gap-3 py-5">
            <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-success-500" />
            <div className="space-y-2">
              {confirmResults.map((item) => (
                <div key={String(item.result.assignment_id)}>
                  <p className="text-sm font-medium text-slate-900">
                    {confirmResults.length > 1 ? `${item.subject}：` : ''}批次 #
                    {String(item.result.assignment_id)} 已入库
                  </p>
                  <p className="mt-1 text-sm text-slate-600">
                    已交 {String(item.result.submitted)} · 缺交 {String(item.result.missing)} · 请假{' '}
                    {String(item.result.excused)}
                  </p>
                </div>
              ))}
              <p className="mt-1 text-[10px] text-slate-400">
                同一批次重复确认不会新增数据。
              </p>
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}
