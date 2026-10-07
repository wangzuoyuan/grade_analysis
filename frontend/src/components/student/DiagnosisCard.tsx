'use client'

/**
 * P1-B4 纵向切片——诊断卡（三处同源）。
 *
 * 同一数据源 = B1/B2 诊断 HTTP 端点（契约 docs/diagnosis-roadmap/p1-contracts.md
 * §2/§3/§6）：前端绝不自行复算任何指标或类型，只做契约 JSON 形状的展示与聚合。
 *
 * - `DiagnosisCard`：学生页新卡（主类型 + 次标签 + 证据 + 六类指标摘要）。
 *   本分支尚无 B1/B2 实现：类型为组件内 mock 类型（契约形状），渲染逻辑由
 *   契约形状的 fixture 驱动；端点上线后零改动接入（同源取数路径已写死）。
 * - `HomeroomDiagnosisOverviewCard`：班主任首页类型分布卡（班级 types 分布
 *   top + 优先关注，附 evidence 一句）；分布行/优先关注可下钻到学生名单并
 *   链接其事实版报告页（A5），姓名取自本班名册端点、拿不到时如实兜底。
 *
 * 缺失纪律（契约 §0.3）：任何指标缺输入显示「—」并带 missing_reason
 * （机器码经 lib/diagnosis-labels 统一转中文展示）；
 * classification_status=insufficient_data 时如实显示「数据不足，不强行归类」；
 * 数据缺失态（端点未部署/请求失败）如实展示，绝不伪造空类型分布。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import {
  Activity,
  BookOpenCheck,
  ChevronDown,
  ClipboardCheck,
  Gauge,
  HeartHandshake,
  Info,
  Scale,
  Stethoscope,
  TrendingUp,
} from 'lucide-react'

import {
  fetchClasses,
  fetchSharedConfig,
  fetchStudents,
  type PersonId,
  type StudentsQuery,
  type WorkspaceMode,
} from '@/lib/api-v1'
import { diagnosisMissingReasonLabel } from '@/lib/diagnosis-labels'
import { useWorkspace } from '@/lib/workspace'
import { CardFoldToggle, useCardFold } from '@/components/dashboard/card-fold'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

/* ------------------------------------------------------------------ */
/* 契约形状 mock 类型（docs/diagnosis-roadmap/p1-contracts.md §2/§3）  */
/* ------------------------------------------------------------------ */

/** GET /{域}/diagnosis/features 的单生六类指标（§2 JSON 形状）。 */
export interface DiagnosisFeatures {
  person_id: number
  calc_version: string
  indicators: {
    current_level: {
      exam_name: string | null
      as_of: string | null
      main3: {
        rank: number | null
        percentile: number | null
        basis: string | null
        missing_reason: string | null
      } | null
      subjects: Array<{ subject: string; percentile: number | null; grade_score: number | null }>
      bands: { high_score: boolean; critical: boolean; weak: boolean }
    }
    trend: {
      last_change: { from: string; to: string; rank_change: number } | null
      direction_recent: string
      streak: { kind: string | null; count: number }
      long_term: string
      valid_exam_count: number
    }
    stability: {
      window_n: number
      statistic: string
      value: number | null
      label: string
      min_points: number
    }
    imbalance: {
      subjects: Array<{ subject: string; diff_pct_point: number | null; consecutive_exams: number | null }>
      severe: string[]
    }
    homework_behavior: {
      missing_7d: number | null
      missing_30d: number | null
      current_streak_days: number | null
      trend: string
      forgot_30d: number | null
      negative_notes_30d: number | null
      missing_by_subject: Record<string, number>
    }
    teacher_attention: {
      last_contact: { kind: string | null; days_ago: number | null } | null
      open_follow_ups: number | null
      done_follow_ups_30d: number | null
    }
  }
  data_quality: { valid_exam_count: number; notes: string[] }
}

/** GET /{域}/diagnosis/types 的类型判定（§3 JSON 形状）。 */
export interface DiagnosisTypes {
  person_id: number
  calc_version: string
  main_type: string | null
  secondary_tags: string[]
  evidence: Array<{ type: string; basis: string }>
  classification_status: 'classified' | 'insufficient_data'
}

/* ------------------------------------------------------------------ */
/* 契约形状 fixture（驱动渲染逻辑；端点上线前后同一套代码路径）         */
/* ------------------------------------------------------------------ */

export const DIAGNOSIS_FEATURES_FIXTURE: DiagnosisFeatures = {
  person_id: 9,
  calc_version: 'p1-v1',
  indicators: {
    current_level: {
      exam_name: '2025期中',
      as_of: '2025-11-06',
      main3: { rank: 123, percentile: 0.21, basis: 'school', missing_reason: null },
      subjects: [{ subject: '语文', percentile: 0.34, grade_score: null }],
      bands: { high_score: false, critical: false, weak: false },
    },
    trend: {
      last_change: { from: '2025期初', to: '2025期中', rank_change: -12 },
      direction_recent: '进步',
      streak: { kind: '进步', count: 2 },
      long_term: '上升',
      valid_exam_count: 5,
    },
    stability: { window_n: 5, statistic: 'range', value: 0.18, label: '中等波动', min_points: 3 },
    imbalance: {
      subjects: [{ subject: '英语', diff_pct_point: -22.0, consecutive_exams: 3 }],
      severe: ['英语'],
    },
    homework_behavior: {
      missing_7d: 2,
      missing_30d: 5,
      current_streak_days: 3,
      trend: '恶化',
      forgot_30d: 1,
      negative_notes_30d: 0,
      missing_by_subject: { 数学: 3 },
    },
    teacher_attention: {
      last_contact: { kind: '谈话', days_ago: 12 },
      open_follow_ups: 1,
      done_follow_ups_30d: 2,
    },
  },
  data_quality: { valid_exam_count: 5, notes: [] },
}

export const DIAGNOSIS_TYPES_FIXTURE: DiagnosisTypes = {
  person_id: 9,
  calc_version: 'p1-v1',
  main_type: '持续进步型',
  secondary_tags: ['作业风险', '明显偏科型'],
  evidence: [
    { type: '持续进步型', basis: '近 3 场主三门名次 -12/-15/-9，连续进步 3 次' },
    { type: '作业风险', basis: '近 30 天缺交 5 次，当前连缺 3 天' },
  ],
  classification_status: 'classified',
}

export const DIAGNOSIS_INSUFFICIENT_TYPES_FIXTURE: DiagnosisTypes = {
  person_id: 10,
  calc_version: 'p1-v1',
  main_type: null,
  secondary_tags: [],
  evidence: [],
  classification_status: 'insufficient_data',
}

/* ------------------------------------------------------------------ */
/* 纯渲染辅助（fixture 驱动；不触网、无副作用）                         */
/* ------------------------------------------------------------------ */

export const DIAGNOSIS_CALC_VERSION = 'p1-v1'

/** 优先关注类型（契约 §6.2：综合风险/持续下滑/临界下滑）。 */
export const PRIORITY_ATTENTION_TYPES = ['综合风险型', '持续下滑型', '临界下滑型'] as const

const DASH = '—'

function formatPercent(v: number | null | undefined): string {
  if (v == null) return DASH
  return `${(Math.round(v * 1000) / 10).toFixed(1)}%`
}

function formatSigned(v: number | null | undefined): string {
  if (v == null) return DASH
  return v > 0 ? `+${v}` : String(v)
}

/** 主类型徽章色调（风险类红/橙、进步绿、优秀蓝、中性灰）。 */
export function diagnosisTypeTone(mainType: string | null): 'default' | 'secondary' | 'destructive' | 'success' | 'warning' {
  if (mainType == null) return 'secondary'
  if ((PRIORITY_ATTENTION_TYPES as readonly string[]).includes(mainType)) return 'destructive'
  if (mainType === '短期下滑型' || mainType === '作业风险型' || mainType === '高位波动型') return 'warning'
  if (mainType === '持续进步型' || mainType === '临界上升型') return 'success'
  if (mainType === '稳定优秀型') return 'default'
  return 'secondary'
}

export interface DiagnosisIndicatorRow {
  key: 'current_level' | 'trend' | 'stability' | 'imbalance' | 'homework_behavior' | 'teacher_attention'
  label: string
  value: string
  /** 契约 §0.3：缺输入=缺失态，如实带原因显示。 */
  missing: boolean
}

/** 六类指标摘要行：纯函数，输入=B1 features 契约形状。 */
export function diagnosisIndicatorRows(features: DiagnosisFeatures | null): DiagnosisIndicatorRow[] {
  const ind = features?.indicators ?? null
  if (ind == null) {
    return (
      [
        ['current_level', '当前水平'],
        ['trend', '趋势'],
        ['stability', '稳定性'],
        ['imbalance', '偏科'],
        ['homework_behavior', '作业行为'],
        ['teacher_attention', '教师关注'],
      ] as Array<[DiagnosisIndicatorRow['key'], string]>
    ).map(([key, label]) => ({ key, label, value: DASH, missing: true }))
  }
  const main3 = ind.current_level?.main3 ?? null
  const currentLevel = main3
    ? [
        main3.rank != null ? `年级第 ${main3.rank} 名` : null,
        main3.percentile != null ? `百分位 ${formatPercent(main3.percentile)}` : null,
        // 机器码（如 no_main3_row）转中文短句，未知码带原始码兜底
        diagnosisMissingReasonLabel(main3.missing_reason),
      ]
        .filter(Boolean)
        .join(' · ') || DASH
    : ind.current_level?.main3 === null
      ? (ind.current_level?.exam_name ?? DASH)
      : DASH
  const currentMissing = !main3 || (main3.rank == null && main3.percentile == null)
  const trend = ind.trend
  const trendValue = trend
    ? [
        trend.direction_recent,
        trend.streak?.kind ? `连续${trend.streak.kind} ${trend.streak.count} 次` : null,
        trend.last_change?.rank_change != null
          ? `上场${formatSigned(trend.last_change.rank_change)} 名`
          : null,
      ]
        .filter(Boolean)
        .join(' · ')
    : DASH
  const stability = ind.stability
  const stabilityValue = stability
    ? `${stability.label}${
        stability.value != null ? `（极差 ${formatPercent(stability.value)}）` : ''
      }`
    : DASH
  const imbalance = ind.imbalance
  const imbalanceValue =
    imbalance && (imbalance.severe?.length || imbalance.subjects?.length)
      ? (imbalance.severe?.length
          ? imbalance.severe.join('、')
          : imbalance.subjects
              .map((s) => `${s.subject}（${formatSigned(s.diff_pct_point)} 个百分点）`)
              .join('、'))
      : '无'
  const hw = ind.homework_behavior
  const hwValue = hw
    ? [
        hw.missing_30d != null ? `近 30 天缺交 ${hw.missing_30d} 次` : null,
        hw.current_streak_days != null ? `连缺 ${hw.current_streak_days} 天` : null,
        hw.trend && hw.trend !== '无数据' ? hw.trend : null,
      ]
        .filter(Boolean)
        .join(' · ') || '无作业记录'
    : DASH
  const attention = ind.teacher_attention
  const attentionValue = attention
    ? [
        attention.last_contact?.kind
          ? `${attention.last_contact.kind}${attention.last_contact.days_ago != null ? `（${attention.last_contact.days_ago} 天前）` : ''}`
          : null,
        attention.open_follow_ups != null && attention.open_follow_ups > 0
          ? `待跟进 ${attention.open_follow_ups} 项`
          : null,
      ]
        .filter(Boolean)
        .join(' · ') || '暂无记录'
    : DASH
  return [
    { key: 'current_level', label: '当前水平', value: currentLevel, missing: currentMissing },
    {
      key: 'trend',
      label: '趋势',
      value: trendValue,
      missing: !trend || trend.direction_recent === '数据不足',
    },
    {
      key: 'stability',
      label: '稳定性',
      value: stabilityValue,
      missing: !stability || stability.label === '数据不足',
    },
    { key: 'imbalance', label: '偏科', value: imbalanceValue, missing: false },
    { key: 'homework_behavior', label: '作业行为', value: hwValue, missing: hw == null },
    { key: 'teacher_attention', label: '教师关注', value: attentionValue, missing: attention == null },
  ]
}

export interface DiagnosisTypeDistributionEntry {
  /** main_type；null = 数据不足（不强行归类的学生，如实单列）。 */
  type: string | null
  count: number
}

/** 分布分桶键：数据不足（含单人请求失败）单列为 null，聚合与下钻共用同一键。 */
function diagnosisBucketKey(item: DiagnosisTypes | null): string | null {
  return item?.classification_status === 'insufficient_data' ? null : (item?.main_type ?? null)
}

/** 班级 types 分布：按 main_type 计数、降序；数据不足单列不混入。 */
export function aggregateTypeDistribution(
  typesList: Array<DiagnosisTypes | null>,
): DiagnosisTypeDistributionEntry[] {
  const counts = new Map<string | null, number>()
  for (const item of typesList) {
    const key = diagnosisBucketKey(item)
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  const named = [...counts.entries()]
    .filter(([type]) => type != null)
    .sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0]), 'zh-CN'))
    .map(([type, count]) => ({ type, count }))
  const insufficient = counts.get(null) ?? 0
  return insufficient > 0 ? [...named, { type: null, count: insufficient }] : named
}

/**
 * A5 分布下钻：按与 aggregateTypeDistribution 相同的分桶键，返回每个类型桶
 * 的学生 person_id 列表。memberIds 与 typesList 按加载顺序一一对应（单人
 * 请求失败记 null 也保留占位，故数据不足桶可列出失败学生，与计数口径一致）。
 */
export function groupPersonIdsByType(
  memberIds: ReadonlyArray<PersonId>,
  typesList: ReadonlyArray<DiagnosisTypes | null>,
): Map<string | null, number[]> {
  const groups = new Map<string | null, number[]>()
  const n = Math.min(memberIds.length, typesList.length)
  for (let i = 0; i < n; i += 1) {
    const pid = Number(memberIds[i])
    if (!Number.isFinite(pid)) continue
    const key = diagnosisBucketKey(typesList[i] ?? null)
    const bucket = groups.get(key)
    if (bucket) bucket.push(pid)
    else groups.set(key, [pid])
  }
  return groups
}

/**
 * 名册 person_id → 姓名展示：名册缺失/无姓名时兜底「学生 {person_id}」，
 * 绝不编造姓名。
 */
export function rosterStudentName(
  names: ReadonlyMap<number, string> | null | undefined,
  personId: number,
): string {
  return names?.get(personId) ?? `学生 ${personId}`
}

export interface DiagnosisPriorityAttention {
  person_id: number
  main_type: string
  basis: string
}

/** 优先关注名单（综合风险/持续下滑/临界下滑），附 evidence 一句。 */
export function pickPriorityAttention(
  typesList: Array<DiagnosisTypes | null>,
): DiagnosisPriorityAttention[] {
  return typesList
    .filter(
      (item): item is DiagnosisTypes =>
        item != null &&
        item.classification_status === 'classified' &&
        item.main_type != null &&
        (PRIORITY_ATTENTION_TYPES as readonly string[]).includes(item.main_type),
    )
    .sort((a, b) => {
      const rank = (t: string) => PRIORITY_ATTENTION_TYPES.indexOf(t as (typeof PRIORITY_ATTENTION_TYPES)[number])
      const byPriority = rank(a.main_type!) - rank(b.main_type!)
      return byPriority !== 0 ? byPriority : a.person_id - b.person_id
    })
    .map((item) => ({
      person_id: item.person_id,
      main_type: item.main_type!,
      basis: item.evidence[0]?.basis ?? '',
    }))
}

/* ------------------------------------------------------------------ */
/* 同源取数：B1/B2 契约端点（与 AI 工具 get_diagnosis_summary 同一来源） */
/* ------------------------------------------------------------------ */

export type DiagnosisScopeQuery = { academic_year_id?: number }

function diagnosisQueryString(personId: number, q: DiagnosisScopeQuery): string {
  const params = new URLSearchParams()
  params.set('person_id', String(personId))
  if (typeof q.academic_year_id === 'number') params.set('academic_year_id', String(q.academic_year_id))
  return params.toString()
}

/** GET /api/v1/{域}/diagnosis/features（B1 特征层，单生）。 */
export async function fetchDiagnosisFeatures(
  mode: WorkspaceMode,
  personId: number,
  q: DiagnosisScopeQuery = {},
): Promise<DiagnosisFeatures> {
  const res = await fetch(`/api/v1/${mode}/diagnosis/features?${diagnosisQueryString(personId, q)}`, {
    headers: { Accept: 'application/json' },
  })
  const body = (await res.json().catch(() => null)) as Record<string, unknown> | null
  if (!res.ok) {
    throw new Error(
      typeof body?.detail === 'string' ? body.detail : `诊断特征请求失败（HTTP ${res.status}）`,
    )
  }
  return body as unknown as DiagnosisFeatures
}

/** GET /api/v1/{域}/diagnosis/types（B2 类型引擎，单生）。 */
export async function fetchDiagnosisTypes(
  mode: WorkspaceMode,
  personId: number,
  q: DiagnosisScopeQuery = {},
): Promise<DiagnosisTypes> {
  const res = await fetch(`/api/v1/${mode}/diagnosis/types?${diagnosisQueryString(personId, q)}`, {
    headers: { Accept: 'application/json' },
  })
  const body = (await res.json().catch(() => null)) as Record<string, unknown> | null
  if (!res.ok) {
    throw new Error(
      typeof body?.detail === 'string' ? body.detail : `诊断类型请求失败（HTTP ${res.status}）`,
    )
  }
  return body as unknown as DiagnosisTypes
}

/* ------------------------------------------------------------------ */
/* 学生页诊断卡                                                        */
/* ------------------------------------------------------------------ */

const INDICATOR_ICONS = {
  current_level: BookOpenCheck,
  trend: TrendingUp,
  stability: Gauge,
  imbalance: Scale,
  homework_behavior: ClipboardCheck,
  teacher_attention: HeartHandshake,
} as const

function DiagnosisCardSkeleton() {
  return (
    <Card>
      <CardHeader>
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-3 w-64" />
      </CardHeader>
      <CardContent className="space-y-3">
        <Skeleton className="h-8 w-56" />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

export function DiagnosisCard({
  mode,
  personId,
  academicYearId,
}: {
  mode: WorkspaceMode
  personId: number
  /** 可选；缺省由后端按当前学年解析。 */
  academicYearId?: number
}) {
  const [features, setFeatures] = useState<DiagnosisFeatures | null>(null)
  const [types, setTypes] = useState<DiagnosisTypes | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const reqRef = useRef(0)

  useEffect(() => {
    const req = ++reqRef.current
    setLoading(true)
    setError(null)
    setFeatures(null)
    setTypes(null)
    const q: DiagnosisScopeQuery = {}
    if (typeof academicYearId === 'number') q.academic_year_id = academicYearId
    Promise.all([
      fetchDiagnosisFeatures(mode, personId, q),
      fetchDiagnosisTypes(mode, personId, q),
    ])
      .then(([f, t]) => {
        if (req !== reqRef.current) return
        setFeatures(f)
        setTypes(t)
        setLoading(false)
      })
      .catch((reason: unknown) => {
        if (req !== reqRef.current) return
        setError(reason instanceof Error ? reason.message : '诊断数据加载失败')
        setLoading(false)
      })
    return () => {
      reqRef.current += 1
    }
  }, [mode, personId, academicYearId])

  const rows = useMemo(() => diagnosisIndicatorRows(features), [features])

  if (loading) return <DiagnosisCardSkeleton />

  if (error || !types) {
    // 数据缺失态如实显示：端点未部署/无数据不伪造任何类型或指标
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Stethoscope className="h-4 w-4 text-brand-600" />
            学情诊断
          </CardTitle>
          <CardDescription>与 AI 助手同一数据源</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col items-start gap-2 rounded-lg border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-sm text-slate-500">
            <span className="inline-flex items-center gap-1.5 text-slate-600">
              <Info className="h-4 w-4" />
              诊断数据暂不可用（数据缺失）
            </span>
            {error && <span className="text-xs text-slate-400">{error}</span>}
            <span className="text-xs text-slate-400">
              诊断端点（/diagnosis/features、/diagnosis/types）尚未部署或该生暂无有效数据。
            </span>
          </div>
        </CardContent>
      </Card>
    )
  }

  const insufficient = types.classification_status === 'insufficient_data'
  const validExamCount = features?.data_quality?.valid_exam_count ?? null

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <Stethoscope className="h-4 w-4 text-brand-600" />
          学情诊断
          {insufficient ? (
            <Badge variant="secondary">数据不足，不强行归类</Badge>
          ) : (
            <Badge variant={diagnosisTypeTone(types.main_type)}>{types.main_type}</Badge>
          )}
          {types.secondary_tags.map((tag) => (
            <Badge key={tag} variant="outline">
              {tag}
            </Badge>
          ))}
        </CardTitle>
        <CardDescription>
          与诊断版学生报告、AI 助手同一数据源
          {validExamCount != null ? ` · 有效考试 ${validExamCount} 场` : ''}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* 证据（每条引用具体 B1 字段值；insufficient 时无证据，如实显示） */}
        {types.evidence.length > 0 ? (
          <ul className="space-y-1.5">
            {types.evidence.map((item) => (
              <li
                key={`${item.type}-${item.basis}`}
                className="flex flex-wrap items-baseline gap-x-2 rounded-lg bg-slate-50 px-3 py-2 text-xs"
              >
                <Badge variant="outline" className="text-[10px]">
                  {item.type}
                </Badge>
                <span className="text-slate-600">{item.basis}</span>
              </li>
            ))}
          </ul>
        ) : (
          <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 px-3 py-3 text-xs text-slate-400">
            {insufficient
              ? '有效考试不足 2 场（或关键指标缺失），不做类型归类；下方指标缺输入处显示「—」。'
              : '暂无可引用的判定证据。'}
          </div>
        )}

        {/* 六类指标摘要（缺输入=「—」+原因，绝不转 0、不残留上次值） */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {rows.map((row) => {
            const Icon = INDICATOR_ICONS[row.key]
            return (
              <div key={row.key} className="rounded-lg border border-slate-100 px-3 py-2.5">
                <div className="flex items-center gap-1.5 text-xs font-medium text-slate-500">
                  <Icon className="h-3.5 w-3.5" />
                  {row.key === 'imbalance' ? (
                    // A6 偏科口径短句：title 提示（虚线下划线示意可悬停）
                    <span
                      className="cursor-help border-b border-dotted border-slate-300"
                      title="＝该科年级百分位 − 本人总分年级百分位（同场比较，单位百分点）"
                    >
                      {row.label}
                    </span>
                  ) : (
                    row.label
                  )}
                </div>
                <div
                  className={cn(
                    'mt-1 text-sm',
                    row.missing ? 'text-slate-400' : 'font-medium text-slate-800',
                  )}
                  title={row.value}
                >
                  {row.value}
                </div>
              </div>
            )
          })}
        </div>
        <p className="text-[11px] text-slate-400">
          百分位=年级相对位置（数值越小名次越靠前）；名次变化负值=名次提升；缺考/缺科不折算、不计入分母。
        </p>
      </CardContent>
    </Card>
  )
}

/* ------------------------------------------------------------------ */
/* 班主任首页类型分布卡                                                */
/* ------------------------------------------------------------------ */

const TYPES_BATCH_SIZE = 8

/** 数据不足桶的展开键（类型名均为中文，用英文哨兵避免冲突）。 */
const INSUFFICIENT_BUCKET_KEY = '__insufficient__'

/** A5 下钻/优先关注共用的学生事实版报告页链接（班主任域，带 /report 后缀）。 */
function homeroomStudentReportHref(personId: number): string {
  return `/homeroom/students/${personId}/report`
}

function OverviewCardSkeleton() {
  return (
    <Card>
      <CardHeader>
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-3 w-72" />
      </CardHeader>
      <CardContent className="space-y-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </CardContent>
    </Card>
  )
}

export function HomeroomDiagnosisOverviewCard() {
  const { mode, filter, scope, generation } = useWorkspace()
  const [typesList, setTypesList] = useState<Array<DiagnosisTypes | null> | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState<number | null>(null)
  const reqRef = useRef(0)
  // A5 下钻：本班名册 person_id→姓名（拉一次并缓存；失败置空映射走编号兜底）
  const [rosterNames, setRosterNames] = useState<Map<number, string> | null>(null)
  const [rosterFailed, setRosterFailed] = useState(false)
  const rosterReqRef = useRef(0)
  // 展开态：已展开的类型桶键集合（数据不足桶用哨兵键）
  const [expandedTypes, setExpandedTypes] = useState<Set<string>>(() => new Set())
  // 卡片级折叠（本地记忆）：分布卡整体可收起，长页面自行取舍
  const { folded: overviewFolded, toggle: toggleOverviewFold } = useCardFold(
    'homeroom:diagnosis-overview',
  )

  useEffect(() => {
    if (mode !== 'homeroom') return
    const req = ++rosterReqRef.current
    setRosterNames(null)
    setRosterFailed(false)
    const rosterQ: StudentsQuery = {}
    if (typeof filter.academic_year_id === 'number') rosterQ.academic_year_id = filter.academic_year_id

    async function loadRoster() {
      // 名册解析与 HomeroomProfileView 一致：缺省学年经 shared/config 解析，
      // homeroom students 端点要求显式 class_id（学年/行政班均由后端解析越界）。
      let academicYearId = rosterQ.academic_year_id
      if (academicYearId === undefined) {
        const config = await fetchSharedConfig()
        academicYearId = config.current_academic_year?.id
      }
      if (academicYearId === undefined) throw new Error('未配置当前学年，无法确定行政班名册范围')
      const catalog = await fetchClasses(academicYearId)
      if (catalog.homeroom == null) throw new Error(`学年 ${catalog.academic_year_name} 未找到本人绑定的行政班`)
      return fetchStudents('homeroom', {
        ...rosterQ,
        academic_year_id: academicYearId,
        class_id: catalog.homeroom.class_id,
      })
    }

    void loadRoster()
      .then((r) => {
        if (req !== rosterReqRef.current) return
        const names = new Map<number, string>()
        for (const s of r.students ?? []) {
          if (s.name) names.set(Number(s.person_id), s.name)
        }
        setRosterNames(names)
      })
      .catch(() => {
        if (req !== rosterReqRef.current) return
        // 名册失败不阻塞分布卡：名单展示走「学生 {person_id}」兜底，绝不编造
        setRosterNames(new Map())
        setRosterFailed(true)
      })
    return () => {
      rosterReqRef.current += 1
    }
  }, [mode, filter.academic_year_id, filter.class_id, generation])

  useEffect(() => {
    if (mode !== 'homeroom') return
    const memberIds = scope?.member_person_ids ?? []
    const req = ++reqRef.current
    if (memberIds.length === 0) {
      setTypesList([])
      setFailed(null)
      setLoading(false)
      return
    }
    setLoading(true)
    setFailed(null)
    setTypesList(null)
    const q: DiagnosisScopeQuery = {}
    if (typeof filter.academic_year_id === 'number') q.academic_year_id = filter.academic_year_id

    async function load() {
      // 逐生取 B2 types 端点（班级 types 分布 = 每生 classify 结果聚合，
      // 同一数据源、绝不在前端复算类型）；分批并发控请求量；单人失败
      // 记为缺失（null），不阻塞整卡。
      const results: Array<DiagnosisTypes | null> = []
      let failures = 0
      for (let i = 0; i < memberIds.length; i += TYPES_BATCH_SIZE) {
        const batch = memberIds.slice(i, i + TYPES_BATCH_SIZE)
        const batchResults = await Promise.all(
          batch.map((pid) =>
            fetchDiagnosisTypes('homeroom', Number(pid), q)
              .then((t) => t as DiagnosisTypes)
              .catch(() => {
                failures += 1
                return null
              }),
          ),
        )
        if (req !== reqRef.current) return
        results.push(...batchResults)
      }
      if (req !== reqRef.current) return
      setTypesList(results)
      setFailed(failures)
      setLoading(false)
    }

    void load()
    return () => {
      reqRef.current += 1
    }
  }, [mode, filter.academic_year_id, scope, generation])

  const distribution = useMemo(() => aggregateTypeDistribution(typesList ?? []), [typesList])
  const priority = useMemo(() => pickPriorityAttention(typesList ?? []), [typesList])
  // A5 下钻：类型桶 → 学生 person_id 列表（与分布计数同一分桶口径）
  const typeGroups = useMemo(
    () => groupPersonIdsByType(scope?.member_person_ids ?? [], typesList ?? []),
    [scope, typesList],
  )
  const cohortSize = scope?.member_person_ids?.length ?? 0

  const toggleExpandedType = (type: string | null) => {
    const key = type ?? INSUFFICIENT_BUCKET_KEY
    setExpandedTypes((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  // 数据缺失态：全员失败（端点未部署/请求失败）→ 如实显示，绝不伪造分布
  const allMissing = typesList != null && typesList.length > 0 && failed === typesList.length

  if (mode !== 'homeroom') return null

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-brand-600" />
            学情类型分布
          </CardTitle>
          <CardDescription>
            班级诊断类型分布与优先关注（与学生页诊断卡、AI 助手同一数据源）
          </CardDescription>
        </div>
        <CardFoldToggle folded={overviewFolded} onToggle={toggleOverviewFold} />
      </CardHeader>
      {overviewFolded ? null : (
      <CardContent className="space-y-4">
        {loading ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        ) : cohortSize === 0 || typesList!.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center">
            <p className="text-sm text-slate-500">当前范围暂无学生，无法生成类型分布</p>
            <p className="mt-1 text-xs text-slate-400">数据缺失态如实显示，不做任何估算。</p>
          </div>
        ) : allMissing ? (
          <div className="flex flex-col items-start gap-1.5 rounded-lg border border-dashed border-slate-200 bg-slate-50 px-4 py-6">
            <span className="inline-flex items-center gap-1.5 text-sm text-slate-600">
              <Info className="h-4 w-4" />
              诊断数据暂不可用（数据缺失）
            </span>
            <span className="text-xs text-slate-400">
              诊断类型端点（/diagnosis/types）尚未部署或当前范围暂无有效数据；不做估算填充。
            </span>
          </div>
        ) : (
          <>
            {/* 分布 top（含数据不足计数，绝不混入类型桶）；A5：行内展开该类型学生名单 */}
            <div className="space-y-2">
              {distribution.map((entry) => {
                const bucketKey = entry.type ?? INSUFFICIENT_BUCKET_KEY
                const expanded = expandedTypes.has(bucketKey)
                const members = typeGroups.get(entry.type ?? null) ?? []
                return (
                  <div key={bucketKey} className="space-y-1">
                    <button
                      type="button"
                      onClick={() => toggleExpandedType(entry.type)}
                      className="flex w-full cursor-pointer items-center gap-2 text-xs"
                      aria-expanded={expanded}
                      title={expanded ? '点击收起该类型学生名单' : '点击展开该类型学生名单'}
                    >
                      <span
                        className="w-28 shrink-0 truncate text-left text-slate-600"
                        title={entry.type ?? undefined}
                      >
                        {entry.type ?? '数据不足'}
                      </span>
                      <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                        <div
                          className={cn(
                            'h-full rounded-full',
                            entry.type == null
                              ? 'bg-slate-200'
                              : (PRIORITY_ATTENTION_TYPES as readonly string[]).includes(entry.type)
                                ? 'bg-gradient-to-r from-[#f0a83a] to-[#d7791f]'
                                : 'bg-gradient-to-r from-[#35b9e9] to-[#1f7fd6]',
                          )}
                          style={{ width: `${Math.round((entry.count / Math.max(1, cohortSize)) * 100)}%` }}
                        />
                      </div>
                      <span className="w-10 shrink-0 text-right tabular-nums text-slate-600">
                        {entry.count} 人
                      </span>
                      <ChevronDown
                        className={cn(
                          'h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform',
                          expanded && 'rotate-180',
                        )}
                        aria-hidden="true"
                      />
                    </button>
                    {expanded ? (
                      members.length === 0 ? (
                        <p className="pl-1 text-[11px] text-slate-400">该类型暂无可列出的学生</p>
                      ) : (
                        <ul className="flex flex-wrap gap-1.5 pl-1">
                          {members.map((pid) => (
                            <li key={pid}>
                              <Link
                                href={homeroomStudentReportHref(pid)}
                                className="inline-flex items-center rounded-md border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                              >
                                {rosterStudentName(rosterNames, pid)}
                              </Link>
                            </li>
                          ))}
                        </ul>
                      )
                    ) : null}
                  </div>
                )
              })}
            </div>

            {/* 优先关注（综合风险/持续下滑/临界下滑前若干，附 evidence 一句；
                A5：补学生姓名并链接其事实版报告页） */}
            <div>
              <div className="mb-1.5 text-xs font-medium text-slate-500">优先关注</div>
              {priority.length === 0 ? (
                <div className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
                  当前无综合风险/持续下滑/临界下滑学生
                </div>
              ) : (
                <ul className="space-y-1.5">
                  {priority.slice(0, 6).map((item) => (
                    <li
                      key={item.person_id}
                      className="flex flex-wrap items-baseline gap-x-2 rounded-lg bg-amber-50 px-3 py-2 text-xs"
                    >
                      <Badge variant="destructive" className="text-[10px]">
                        {item.main_type}
                      </Badge>
                      <Link
                        href={homeroomStudentReportHref(item.person_id)}
                        className="font-medium text-amber-900 underline-offset-2 hover:underline"
                      >
                        {rosterStudentName(rosterNames, item.person_id)}
                      </Link>
                      <span className="text-amber-800">{item.basis || '暂无判定依据说明'}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {rosterFailed ? (
              <p className="text-[11px] text-slate-400">
                本班名册加载失败，学生名单暂以编号显示；不影响类型分布本身。
              </p>
            ) : null}

            {failed != null && failed > 0 ? (
              <p className="text-[11px] text-slate-400">
                有 {failed} 名学生诊断数据加载失败，已计为数据不足；部分缺失如实标注，不代表完整人数。
              </p>
            ) : null}
          </>
        )}
      </CardContent>
      )}
    </Card>
  )
}
