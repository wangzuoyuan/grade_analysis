'use client'

/**
 * P2-C3 诊断版学生报告（契约 docs/diagnosis-roadmap/p2-contracts.md §4）。
 *
 * 数据源 = GET /api/v1/{域}/diagnosis/report（B1 features + B2 classify_student
 * 同源生成，本组件绝不复算任何指标/类型/建议，只做契约 JSON 的展示）。
 * 响应自带三层分节标注（fact=事实 / rule_judgment=规则判断 / suggestion=建议，
 * layer_legend 图例），本页逐节渲染 layer 徽标，不自行推断层级。
 *
 * 建议编辑纪律（契约 §4）：教师编辑仅在前端本地（edit_scope=frontend_local_only，
 * 不入库）——编辑只写本组件 state，无任何 POST/PATCH 调用；打印输出跟随
 * 当前显示文本。建议措辞「值得关注：」由后端生成；免责说明（不构成因果
 * 结论或提分保证）由响应 disclaimer 提供，原样展示。
 *
 * 打印友好：沿用事实版报告页样式基线（max-w-3xl 白底、操作区 print:hidden、
 * 页脚 print:fixed print:bottom-2）+ 全局 @media print 基线（侧栏/顶栏/按钮
 * 隐藏）。缺失值显示「—」不转 0（缺失纪律）。
 *
 * 双工作台：mode 由路由页固定（/homeroom/students/[id]/diagnosis-report →
 * homeroom；/student/[id]/diagnosis-report → teaching），范围与 P1 一致
 * （班主任=本班全科+总分；教学=仅任教学科），越界 404 如实显示错误态。
 * 事实版报告页（同目录 report/page.tsx）保留不动，本页为其诊断版并列入口。
 */

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import {
  Activity,
  ChevronLeft,
  Info,
  Pencil,
  Printer,
  RotateCcw,
  Scale,
  Stethoscope,
} from 'lucide-react'

import type { WorkspaceMode } from '@/lib/api-v1'
import { diagnosisMissingReasonLabel } from '@/lib/diagnosis-labels'
import { useWorkspace } from '@/lib/workspace'
import { apiErrorMessage } from '@/components/link/error-text'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

const DASH = '—'

/* ------------------------------------------------------------------ */
/* 契约形状类型（对齐 GET /{域}/diagnosis/report 响应，逐字段）          */
/* ------------------------------------------------------------------ */

export interface DiagnosisReportSuggestion {
  id: string
  text: string
  based_on: string[]
}

export interface DiagnosisReportData {
  person_id: number
  name: string | null
  academic_year_id: number
  scope_mode: WorkspaceMode
  data_domain: string
  calc_version: string
  as_of: string
  exam_name: string | null
  exam_name_source: string
  layer_legend: Record<string, string>
  learning_state: {
    layer: string
    summary_source: string
    main_type: string | null
    secondary_tags: string[]
    classification_status: 'classified' | 'insufficient_data'
    evidence: Array<{ type: string; basis: string }>
    latest_exam_name: string | null
    latest_exam_as_of: string | null
    main3: {
      rank: number | null
      percentile: number | null
      basis: string | null
      missing_reason: string | null
    } | null
    bands: { high_score: boolean; critical: boolean; weak: boolean }
    trend: {
      status?: string
      missing_reason?: string | null
      last_change: { from: string; to: string; rank_change: number } | null
      direction_recent: string
      streak: { kind: string | null; count: number }
      long_term: string
      valid_exam_count: number
    }
    stability: {
      status?: string
      missing_reason?: string | null
      window_n: number
      statistic: string
      value: number | null
      label: string
      min_points: number
    }
  }
  subject_performance: {
    layer: string
    exam_name: string | null
    subjects: Array<{ subject: string; percentile: number | null; grade_score: number | null }>
    imbalance: {
      layer: string
      status: string
      missing_reason: string | null
      subjects: Array<{
        subject: string
        diff_pct_point: number | null
        consecutive_exams: number | null
      }>
      severe: string[]
    }
  }
  behavior: {
    layer: string
    window_note: string
    missing_7d: number | null
    missing_30d: number | null
    current_streak_days: number | null
    trend: string
    forgot_30d: number | null
    negative_notes_30d: number | null
    missing_by_subject: Record<string, number>
  }
  teacher_observations: {
    layer: string
    visible_domain: string
    categories: string[]
    limit: number
    total: number
    truncated: boolean
    items: Array<{ id: number; date: string; category: string; content: string }>
  }
  suggestions: {
    layer: string
    generated: boolean
    edit_scope: string
    disclaimer: string
    items: DiagnosisReportSuggestion[]
  }
  data_quality: { valid_exam_count: number; notes: string[] }
}

/* ------------------------------------------------------------------ */
/* 展示辅助（纯函数；不触网、不复算口径）                               */
/* ------------------------------------------------------------------ */

const LAYER_LABELS: Record<string, string> = {
  fact: '事实',
  rule_judgment: '规则判断',
  suggestion: '建议',
}

const LAYER_ORDER = ['fact', 'rule_judgment', 'suggestion'] as const

/** 主类型徽章色调（风险红/橙、进步绿、优秀蓝、中性灰；仅展示映射）。 */
function reportTypeTone(
  mainType: string | null,
): 'default' | 'secondary' | 'destructive' | 'success' | 'warning' {
  if (mainType == null) return 'secondary'
  if (mainType === '综合风险型' || mainType === '持续下滑型' || mainType === '临界下滑型') {
    return 'destructive'
  }
  if (mainType === '短期下滑型' || mainType === '作业风险型' || mainType === '高位波动型') {
    return 'warning'
  }
  if (mainType === '持续进步型' || mainType === '临界上升型') return 'success'
  if (mainType === '稳定优秀型') return 'default'
  return 'secondary'
}

function formatPercent(v: number | null | undefined): string {
  if (v == null) return DASH
  return `${(Math.round(v * 1000) / 10).toFixed(1)}%`
}

function formatSigned(v: number | null | undefined): string {
  if (v == null) return DASH
  return v > 0 ? `+${v}` : String(v)
}

function formatScore(v: number | null | undefined): string {
  if (v == null) return DASH
  return Number.isInteger(v) ? String(v) : v.toFixed(1)
}

/** 分节标题：序号 + 名称 + 层级徽标（三层标注的展示载体）。 */
function SectionHeading({
  index,
  title,
  layer,
}: {
  index: string
  title: string
  layer: string
}) {
  return (
    <h2 className="mb-2 flex flex-wrap items-center gap-2 text-base font-semibold">
      <span>
        {index}、{title}
      </span>
      <Badge variant="outline" className="text-[10px]">
        {LAYER_LABELS[layer] ?? layer}
      </Badge>
    </h2>
  )
}

/* ------------------------------------------------------------------ */
/* 同源取数                                                            */
/* ------------------------------------------------------------------ */

export async function fetchDiagnosisReport(
  mode: WorkspaceMode,
  personId: number,
  academicYearId?: number,
): Promise<DiagnosisReportData> {
  const params = new URLSearchParams()
  params.set('person_id', String(personId))
  if (typeof academicYearId === 'number') params.set('academic_year_id', String(academicYearId))
  const res = await fetch(`/api/v1/${mode}/diagnosis/report?${params.toString()}`, {
    headers: { Accept: 'application/json' },
  })
  const body = (await res.json().catch(() => null)) as Record<string, unknown> | null
  if (!res.ok) {
    throw new Error(
      typeof body?.detail === 'string' ? body.detail : `诊断报告请求失败（HTTP ${res.status}）`,
    )
  }
  return body as unknown as DiagnosisReportData
}

/* ------------------------------------------------------------------ */
/* 报告视图                                                            */
/* ------------------------------------------------------------------ */

export function DiagnosisReportView({
  mode,
  personId,
}: {
  mode: WorkspaceMode
  personId: number | null
}) {
  const { filter, generation } = useWorkspace()
  const academicYearId = typeof filter.academic_year_id === 'number' ? filter.academic_year_id : undefined

  const [report, setReport] = useState<DiagnosisReportData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  // 建议本地编辑：drafts[itemId] = 教师改后的文本；绝不回传后端（不入库）
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [editing, setEditing] = useState(false)
  const reqRef = useRef(0)

  useEffect(() => {
    if (personId == null) {
      setLoading(false)
      setError('学生编号无效')
      return
    }
    const req = ++reqRef.current
    setLoading(true)
    setError(null)
    setReport(null)
    setDrafts({})
    setEditing(false)
    fetchDiagnosisReport(mode, personId, academicYearId)
      .then((data) => {
        if (req !== reqRef.current) return
        setReport(data)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (req !== reqRef.current) return
        setError(apiErrorMessage(err))
        setLoading(false)
      })
    return () => {
      reqRef.current += 1
    }
  }, [mode, personId, academicYearId, generation, nonce])

  const backHref = mode === 'homeroom' ? '/homeroom/students' : personId != null ? `/student/${personId}` : '/student'
  const backLabel = mode === 'homeroom' ? '返回学生管理' : '返回学生页'

  const factReportHref =
    mode === 'homeroom'
      ? personId != null
        ? `/homeroom/students/${personId}/report`
        : '/homeroom/students'
      : personId != null
        ? `/student/${personId}/report`
        : '/student'

  const actionBar = (
    <div className="flex items-center justify-between print:hidden">
      <Link
        href={backHref}
        className="inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900"
      >
        <ChevronLeft className="h-4 w-4" aria-hidden="true" />
        {backLabel}
      </Link>
      <div className="flex items-center gap-2">
        <Link
          href={factReportHref}
          className="inline-flex items-center rounded-md border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
        >
          查看事实版报告
        </Link>
        <Button size="sm" onClick={() => window.print()}>
          <Printer className="h-4 w-4" aria-hidden="true" />
          打印 / 存为 PDF
        </Button>
      </div>
    </div>
  )

  if (loading) {
    return (
      <div className="mx-auto max-w-3xl space-y-4 bg-white p-6 text-slate-900 print:p-0">
        {actionBar}
        <div className="space-y-3 py-4">
          <Skeleton className="h-10 w-1/2" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      </div>
    )
  }

  if (error || !report) {
    return (
      <div className="mx-auto max-w-3xl space-y-4 bg-white p-6 text-slate-900 print:p-0">
        {actionBar}
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-10 text-center">
            <p className="text-sm text-slate-600">{error ?? '加载失败，请稍后重试。'}</p>
            <Button variant="outline" size="sm" onClick={() => setNonce((n) => n + 1)}>
              重试
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  const ls = report.learning_state
  const sp = report.subject_performance
  const bh = report.behavior
  const obs = report.teacher_observations
  const sug = report.suggestions
  const insufficient = ls.classification_status === 'insufficient_data'

  return (
    <div className="mx-auto max-w-3xl space-y-5 bg-white p-6 text-slate-900 print:p-0">
      {actionBar}

      {/* 抬头 */}
      <div className="border-b border-slate-300 pb-3">
        <h1 className="text-xl font-bold">诊断版学生报告</h1>
        <p className="mt-1 text-sm text-slate-600">
          {report.name ?? '（未命名）'}
          <span className="ml-2 text-slate-400">
            {mode === 'homeroom' ? '班主任工作台（本班全科+总分）' : '教学工作台（仅任教学科）'}
          </span>
          <span className="ml-3 text-slate-400">数据截至 {report.as_of}</span>
        </p>
        <p className="mt-1 text-xs text-slate-500">
          各科表现锚定考试：{sp.exam_name ?? DASH}
          {sp.exam_name != null
            ? report.exam_name_source === 'explicit'
              ? '（指定）'
              : '（最近一场）'
            : ''}
          ；学习状态与作业行为为学年窗口口径 · 与学生页诊断卡、AI 助手同一数据源
        </p>
      </div>

      {/* 三层图例（事实/规则判断/建议的标注说明，随报告打印） */}
      <section aria-label="层级图例">
        <div className="space-y-1 rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2.5">
          {LAYER_ORDER.map((key) => (
            <p key={key} className="text-xs text-slate-600">
              <span className="mr-1.5 inline-block rounded border border-slate-300 px-1.5 py-0.5 font-medium">
                {LAYER_LABELS[key]}
              </span>
              {report.layer_legend?.[key] ?? ''}
            </p>
          ))}
        </div>
      </section>

      {/* 一、学习状态（规则判断层） */}
      <section>
        <SectionHeading index="一" title="学习状态" layer={ls.layer} />
        <p className="mb-2 text-xs text-slate-400">{ls.summary_source}</p>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          {insufficient ? (
            <Badge variant="secondary">数据不足，不强行归类</Badge>
          ) : (
            <Badge variant={reportTypeTone(ls.main_type)}>{ls.main_type ?? DASH}</Badge>
          )}
          {ls.secondary_tags.map((tag) => (
            <Badge key={tag} variant="outline">
              {tag}
            </Badge>
          ))}
        </div>
        {ls.evidence.length > 0 ? (
          <ul className="mb-3 space-y-1.5">
            {ls.evidence.map((item) => (
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
          <p className="mb-3 text-xs text-slate-400">暂无可引用的判定证据。</p>
        )}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="rounded-lg border border-slate-100 px-3 py-2.5">
            <div className="flex items-center gap-1.5 text-xs font-medium text-slate-500">
              <Activity className="h-3.5 w-3.5" aria-hidden="true" />
              最近一场（{ls.latest_exam_name ?? DASH}）
            </div>
            <div className="mt-1 text-sm font-medium text-slate-800">
              {ls.main3 && (ls.main3.rank != null || ls.main3.percentile != null)
                ? [
                    ls.main3.rank != null ? `年级第 ${ls.main3.rank} 名` : null,
                    ls.main3.percentile != null ? `百分位 ${formatPercent(ls.main3.percentile)}` : null,
                  ]
                    .filter(Boolean)
                    .join(' · ')
                : DASH}
              {ls.main3?.missing_reason
                ? `（${diagnosisMissingReasonLabel(ls.main3.missing_reason)}）`
                : ''}
            </div>
          </div>
          <div className="rounded-lg border border-slate-100 px-3 py-2.5">
            <div className="flex items-center gap-1.5 text-xs font-medium text-slate-500">
              <Stethoscope className="h-3.5 w-3.5" aria-hidden="true" />
              趋势 / 稳定性
            </div>
            <div className="mt-1 text-sm font-medium text-slate-800">
              {[
                ls.trend.direction_recent !== '数据不足'
                  ? [
                      ls.trend.direction_recent,
                      ls.trend.streak?.kind ? `连续${ls.trend.streak.kind} ${ls.trend.streak.count} 次` : null,
                      ls.trend.last_change?.rank_change != null
                        ? `上场 ${formatSigned(ls.trend.last_change.rank_change)} 名（负值=名次提升）`
                        : null,
                    ]
                      .filter(Boolean)
                      .join(' · ')
                  : null,
                ls.stability.label !== '数据不足'
                  ? `${ls.stability.label}${
                      ls.stability.value != null ? `（主三门百分位极差 ${formatPercent(ls.stability.value)}）` : ''
                    }`
                  : null,
              ]
                .filter(Boolean)
                .join(' ｜ ') || DASH}
            </div>
          </div>
        </div>
      </section>

      {/* 二、各科表现（事实层；偏科子块=规则判断） */}
      <section>
        <SectionHeading index="二" title="各科表现" layer={sp.layer} />
        {sp.subjects.length === 0 ? (
          <p className="text-sm text-slate-400">暂无学科成绩记录</p>
        ) : (
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-slate-500">
                <th className="py-1.5 font-normal">科目</th>
                <th className="py-1.5 text-right font-normal">百分位</th>
                <th className="py-1.5 text-right font-normal">等级分</th>
              </tr>
            </thead>
            <tbody>
              {sp.subjects.map((row) => (
                <tr key={row.subject} className="border-b border-slate-100">
                  <td className="py-1.5">{row.subject}</td>
                  <td className="py-1.5 text-right tabular-nums">{formatPercent(row.percentile)}</td>
                  <td className="py-1.5 text-right tabular-nums">{formatScore(row.grade_score)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="mt-1 text-[10px] text-slate-400">
          百分位=年级相对位置（数值越小名次越靠前）；缺考显示「—」不转 0、不计入分母。
        </p>
        <div className="mt-2 rounded-lg border border-slate-100 px-3 py-2.5">
          <div className="flex items-center gap-1.5 text-xs font-medium text-slate-500">
            <Scale className="h-3.5 w-3.5" aria-hidden="true" />
            偏科
            <Badge variant="outline" className="text-[10px]">
              {LAYER_LABELS[sp.imbalance.layer] ?? sp.imbalance.layer}
            </Badge>
          </div>
          <div className="mt-1 text-sm text-slate-800">
            {sp.imbalance.status === 'ok' ? (
              sp.imbalance.severe.length > 0 ? (
                `严重偏科：${sp.imbalance.severe.join('、')}（连续 ${sp.imbalance.subjects
                  .filter((row) => sp.imbalance.severe.includes(row.subject))
                  .map((row) => row.consecutive_exams ?? DASH)
                  .join('、')} 场弱于总体）`
              ) : sp.imbalance.subjects.length > 0 ? (
                sp.imbalance.subjects
                  .map((row) => `${row.subject} ${formatSigned(row.diff_pct_point)} 个百分点`)
                  .join('；')
              ) : (
                '无'
              )
            ) : (
              <span className="text-slate-400">
                偏科不可计算（{diagnosisMissingReasonLabel(sp.imbalance.missing_reason) ?? '数据不足'}）
              </span>
            )}
          </div>
          <p className="mt-1 text-[10px] text-slate-400">
            偏科口径：单科百分位 − 总体百分位（正值=该科更弱，单位=百分点）。
          </p>
        </div>
      </section>

      {/* 三、作业行为（事实层） */}
      <section>
        <SectionHeading index="三" title="作业行为" layer={bh.layer} />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {(
            [
              ['近 7 天缺交', bh.missing_7d],
              ['近 30 天缺交', bh.missing_30d],
              ['当前连缺天数', bh.current_streak_days],
              ['近 30 天忘带', bh.forgot_30d],
              ['近 30 天负面评价', bh.negative_notes_30d],
            ] as Array<[string, number | null]>
          ).map(([label, value]) => (
            <div key={label} className="rounded-lg border border-slate-100 px-3 py-2.5">
              <div className="text-xs text-slate-500">{label}</div>
              <div className="mt-1 text-sm font-semibold tabular-nums text-slate-800">
                {value ?? DASH}
              </div>
            </div>
          ))}
          <div className="rounded-lg border border-slate-100 px-3 py-2.5">
            <div className="text-xs text-slate-500">近期走势</div>
            <div className="mt-1 text-sm font-semibold text-slate-800">{bh.trend || DASH}</div>
          </div>
        </div>
        {Object.keys(bh.missing_by_subject ?? {}).length > 0 ? (
          <p className="mt-2 text-sm text-slate-700">
            缺交分布：
            {Object.entries(bh.missing_by_subject)
              .map(([subject, count]) => `${subject} ${count} 次`)
              .join('；')}
          </p>
        ) : null}
        <p className="mt-1 text-[10px] text-slate-400">{bh.window_note}</p>
      </section>

      {/* 四、教师观察摘录（事实层；域内可见范围） */}
      <section>
        <SectionHeading index="四" title="教师观察摘录" layer={obs.layer} />
        {obs.items.length === 0 ? (
          <p className="text-sm text-slate-500">暂无记录</p>
        ) : (
          <>
            <ul className="space-y-1.5 text-sm">
              {obs.items.map((n) => (
                <li key={n.id} className="text-slate-700">
                  <span className="text-slate-400">{n.date}</span>{' '}
                  <span className="font-medium">[{n.category}]</span> {n.content}
                </li>
              ))}
            </ul>
            {obs.truncated ? (
              <p className="mt-1.5 text-xs text-slate-400">
                共 {obs.total} 条{obs.categories.join('/')}记录，仅显示最近 {obs.limit} 条。
              </p>
            ) : null}
          </>
        )}
        <p className="mt-1 text-[10px] text-slate-400">
          仅含「{obs.categories.join('」「')}」类档案（
          {obs.visible_domain === 'homeroom' ? '班主任域' : '教学域'}可见范围），系统辅助行不入摘录。
        </p>
      </section>

      {/* 五、值得关注事项（建议层；generated=true；编辑仅本地） */}
      <section>
        <SectionHeading index="五" title="值得关注事项" layer={sug.layer} />
        <p className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <Badge variant="outline" className="text-[10px]">
            generated: {String(sug.generated)}
          </Badge>
          <span>由证据自动生成；教师编辑仅保存在本页（前端本地，不入库），打印输出跟随当前文本。</span>
        </p>
        <div className="flex justify-end print:hidden">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setEditing((v) => !v)
              if (editing) setDrafts({})
            }}
          >
            {editing ? (
              '完成编辑'
            ) : (
              <>
                <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                编辑建议（仅本页生效）
              </>
            )}
          </Button>
        </div>
        <ul className="space-y-2">
          {sug.items.map((item) => {
            const text = drafts[item.id] ?? item.text
            return (
              <li key={item.id} className="rounded-lg border border-slate-100 px-3 py-2.5">
                {editing ? (
                  <div className="space-y-1.5">
                    <textarea
                      aria-label={`编辑建议 ${item.id}`}
                      className="w-full rounded-md border border-slate-300 p-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-brand-500"
                      rows={2}
                      maxLength={500}
                      value={text}
                      onChange={(e) => setDrafts((prev) => ({ ...prev, [item.id]: e.target.value }))}
                    />
                    <div className="flex items-center justify-between print:hidden">
                      <span className="text-[10px] text-slate-400">依据：{item.based_on.join('、')}</span>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-slate-400"
                        onClick={() =>
                          setDrafts((prev) => {
                            const next = { ...prev }
                            delete next[item.id]
                            return next
                          })
                        }
                      >
                        <RotateCcw className="h-3 w-3" aria-hidden="true" />
                        恢复原文
                      </Button>
                    </div>
                  </div>
                ) : (
                  <>
                    <p className="text-sm text-slate-800">{text}</p>
                    <p className="mt-1 text-[10px] text-slate-400">依据：{item.based_on.join('、')}</p>
                  </>
                )}
              </li>
            )
          })}
        </ul>
        <p className="mt-2 text-xs text-slate-500">{sug.disclaimer}</p>
      </section>

      {/* 数据质量备注（缺考/缺名次等如实呈现） */}
      {(report.data_quality?.notes ?? []).length > 0 ? (
        <section>
          <h2 className="mb-2 flex items-center gap-1.5 text-base font-semibold">
            <Info className="h-4 w-4" aria-hidden="true" />
            数据质量备注
          </h2>
          <ul className="space-y-1 text-xs text-slate-500">
            {report.data_quality.notes.map((note) => (
              <li key={note}>· {note}</li>
            ))}
          </ul>
        </section>
      ) : null}

      <div className="border-t border-slate-300 pt-3 text-xs text-slate-400 print:fixed print:bottom-2">
        本报告由学情追踪系统基于数据与规则生成（口径 {report.calc_version}）；
        「规则判断」与「建议」为分节标注的非事实内容，不构成因果结论或提分保证，仅供教师参考。
      </div>
    </div>
  )
}
