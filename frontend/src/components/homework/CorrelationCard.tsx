'use client'

/**
 * 作业提交率 × 成绩相关性卡（P2-C1 契约 docs/diagnosis-roadmap/p2-contracts.md §2；
 * 取数改走 /api/v1/{域}/diagnosis/correlation，Pearson r + Spearman rho 双指标分层）。
 *
 * 约束：
 * - r/rho=null（n<8、零方差、分母未知占比>50%、结构性不可算）→ 显示
 *   「不可计算」+ 后端原因，绝不编造数值；
 * - Y 是成绩分数（数值越大越好），散点 y 轴不再反转；方向文案由后端
 *   direction 字段驱动（submit_up_score_up / submit_up_score_down）；
 * - 窗口 [考试日−window_days, 考试日)：不含考试当日与考后作业；忘带/请假/
 *   出勤不计入分子分母；分母不可用的批次已被后端整批剔除并在 caveats 注明；
 * - 分层展示：该场学校段位（高分/临界/薄弱）+ 全班，每层 n/r/rho 或不可算原因；
 * - 页面固定渲染免责声明：描述统计关联，不构成因果结论，也不构成提分保证。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { AlertCircle, RefreshCw } from 'lucide-react'

import {
  diagnosisCorrelation,
  listExams,
  type DiagnosisCorrelationLayer,
  type DiagnosisCorrelationResponse,
  type ExamSummary,
  type HomeworkScopeQuery,
  type WorkspaceMode,
} from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
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
import { correlationDirectionLabel } from './shared'

/** 班主任侧 Y 成绩指标选项（value 与 definitions.metric_options 同名；
 *  默认主三门总分，与 AI 工具/后端缺省统一——2026-09-29 口径对齐）。
 *  年级不适用的选项由后端 422 如实拒绝（错误文案直出）。 */
const METRIC_OPTIONS = [
  { value: 'total:主三门', label: '主三门总分' },
  { value: 'total:3+3', label: '3+3 总分' },
  { value: 'total:五门', label: '五门总分' },
  { value: 'subject:语文', label: '语文分数' },
  { value: 'subject:数学', label: '数学分数' },
  { value: 'subject:英语', label: '英语分数' },
  { value: 'subject:物理', label: '物理分数' },
  { value: 'subject:化学', label: '化学分数' },
  { value: 'subject:生物', label: '生物分数' },
]

/** 分层不可算原因 → 中文说明（后端 reason 词表）。 */
function layerReasonLabel(reason: string | null): string {
  if (reason === 'n_too_small') return '样本不足（n<8）'
  if (reason === 'zero_variance') return '提交率或成绩零方差'
  if (reason === 'denominator_unknown_over_half') return '分母未知学生过半'
  if (reason === 'band_unavailable') return '教学域无总分行，段位不可用'
  if (reason === 'exam_date_missing') return '考试日期缺失'
  if (reason === 'no_valid_exam_score') return '无有效成绩'
  return reason ? reason : '—'
}

/** 单层系数摘要：可算显示 r/rho，不可算显示原因（null 绝不显示数值）。 */
function LayerRow({ layer }: { layer: DiagnosisCorrelationLayer }) {
  return (
    <tr className="border-b border-slate-100 last:border-0">
      <td className="py-1.5 pr-3 text-slate-700">{layer.label}</td>
      <td className="py-1.5 pr-3 tabular-nums text-slate-600">{String(layer.n)}</td>
      <td className="py-1.5 pr-3 tabular-nums text-slate-600">
        {layer.status === 'ok' ? Number(layer.r).toFixed(4) : '不可计算'}
      </td>
      <td className="py-1.5 pr-3 tabular-nums text-slate-600">
        {layer.status === 'ok' ? Number(layer.rho).toFixed(4) : '不可计算'}
      </td>
      <td className="py-1.5 text-xs text-slate-400">
        {layer.status === 'ok' ? `排除 ${String(layer.excluded_no_homework)}` : layerReasonLabel(layer.reason)}
      </td>
    </tr>
  )
}

/** 散点（recharts；y=成绩分数，越大越好 → 轴不再反转）。 */
function CorrelationScatter({
  pairs,
}: {
  pairs: Array<{ x: number; y: number; name: string }>
}) {
  return (
    <ResponsiveContainer width="100%" height={320}>
      <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
        <CartesianGrid stroke="#dcecf8" strokeDasharray="3 3" />
        <XAxis
          type="number"
          dataKey="x"
          name="作业提交率"
          domain={[0, 1]}
          ticks={[0, 0.25, 0.5, 0.75, 1]}
          tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
          tick={{ fontSize: 12, fill: '#58789b' }}
          stroke="#dcecf8"
        />
        <YAxis
          type="number"
          dataKey="y"
          name="成绩（大=好）"
          tick={{ fontSize: 12, fill: '#58789b' }}
          stroke="#dcecf8"
        />
        <Tooltip
          cursor={{ strokeDasharray: '3 3', stroke: '#cbe2f5' }}
          contentStyle={{
            backgroundColor: '#ffffff',
            border: '1px solid #dcecf8',
            borderRadius: 8,
            boxShadow: '0 1px 2px 0 rgb(0 0 0 / 0.05)',
            fontSize: 12,
          }}
          formatter={(value: number | string, name: string) =>
            name === '作业提交率'
              ? [`${Math.round(Number(value) * 100)}%`, name]
              : [String(value), name]
          }
        />
        <Scatter data={pairs} fill="#1f7fd6" />
      </ScatterChart>
    </ResponsiveContainer>
  )
}

export function CorrelationCard({
  mode,
  scopeQ,
  scopeSubject,
  generation,
  preferredExamName,
}: {
  mode: WorkspaceMode
  scopeQ: HomeworkScopeQuery
  /** 教学工作台的任教学科（固定，只读）；班主任工作台可自由输入。 */
  scopeSubject: string | null
  generation: number
  /** 挂载页（成绩分析）当前选中考试：在清单里则默认选中并跟随；缺省/不在清单回退旧行为。 */
  preferredExamName?: string | null
}) {
  const teaching = mode === 'teaching'
  const [subjectDraft, setSubjectDraft] = useState('')
  const [homeworkType, setHomeworkType] = useState('')
  const [windowDays, setWindowDays] = useState<14 | 30>(14)
  const [metricDraft, setMetricDraft] = useState('total:主三门')
  const [applied, setApplied] = useState<{
    subject: string
    homeworkType: string
    windowDays: 14 | 30
    metric: string
  }>({ subject: '', homeworkType: '', windowDays: 14, metric: 'total:主三门' })

  // 考试下拉（与成绩页共用 shared/exams）
  const [exams, setExams] = useState<ExamSummary[] | null>(null)
  const [examsError, setExamsError] = useState<string | null>(null)
  const [examName, setExamName] = useState<string | null>(null)
  const examsReqRef = useRef(0)

  const [corr, setCorr] = useState<DiagnosisCorrelationResponse | null>(null)
  const [corrError, setCorrError] = useState<string | null>(null)
  const [corrNonce, setCorrNonce] = useState(0)
  const corrReqRef = useRef(0)

  const effectiveSubject = teaching ? (scopeSubject ?? '') : applied.subject

  // 考试清单：请求序号只比对 examsReqRef
  useEffect(() => {
    const req = ++examsReqRef.current
    setExams(null)
    setExamsError(null)
    setExamName(null)
    listExams(mode, scopeQ)
      .then((r) => {
        if (req !== examsReqRef.current) return
        const list = r.exams ?? []
        setExams(list)
        if (list.length > 0) {
          // 挂载页（成绩分析）当前考试在清单里则优先选中，否则维持选第一场的旧行为
          const preferred =
            preferredExamName != null && list.some((e) => e.exam_name === preferredExamName)
              ? preferredExamName
              : list[0].exam_name
          setExamName(preferred)
        }
      })
      .catch((err: unknown) => {
        if (req !== examsReqRef.current) return
        setExams([])
        setExamsError(apiErrorMessage(err))
      })
  }, [mode, scopeQ, generation])

  // 挂载页顶部切换考试时跟随：清单已加载且包含该名字才切，不在清单则不动（保持用户自选）
  useEffect(() => {
    if (preferredExamName == null) return
    if ((exams ?? []).some((e) => e.exam_name === preferredExamName)) {
      setExamName(preferredExamName)
    }
  }, [preferredExamName, exams])

  // 相关性（P2-C1 新端点）：班主任 Y=所选 metric 成绩分数（默认主三门总分，
  // 与 AI 工具统一）；教学 Y=任教学科单科分数；请求序号只比对 corrReqRef，
  // 绝不作废考试清单回包
  useEffect(() => {
    if (examName == null || effectiveSubject === '') return
    const req = ++corrReqRef.current
    setCorr(null)
    setCorrError(null)
    diagnosisCorrelation(mode, {
      ...scopeQ,
      exam_name: examName,
      window_days: applied.windowDays,
      // 班主任工作台无作业种类概念（UI 不采集也不筛选），只传教学侧的筛选值
      homework_type: teaching && applied.homeworkType !== '' ? applied.homeworkType : undefined,
      // X 侧作业学科过滤：班主任用输入值收窄，教学侧后端恒钉任教学科
      subject: teaching ? undefined : applied.subject,
      // Y 侧成绩指标：班主任显式传所选值（教学域后端恒钉任教学科，不传）
      metric: teaching ? undefined : applied.metric,
    })
      .then((r) => {
        if (req === corrReqRef.current) setCorr(r)
      })
      .catch((err: unknown) => {
        if (req !== corrReqRef.current) return
        setCorr(null)
        setCorrError(apiErrorMessage(err))
      })
  }, [mode, scopeQ, generation, examName, effectiveSubject, applied, corrNonce])

  const scatterData = useMemo(
    () =>
      (corr?.pairs ?? []).map((p) => ({
        x: p.x,
        y: p.y,
        name: `${p.name ?? '（未命名）'}（${corr?.metric_label ?? '成绩'} ${String(p.y)}）`,
      })),
    [corr],
  )

  // A3：门控改用「教学域 scopeSubject / 班主任域学科草稿」。旧逻辑班主任域误用
  // applied.subject，而 applied 只能由本按钮的 onClick 写入 → 永远 disabled 的死锁。
  // 查询 effect 仍由 applied/effectiveSubject 驱动，onClick 语义不变（点击即 setApplied）。
  const canQuery = (teaching ? (scopeSubject ?? '') : subjectDraft).trim() !== '' && examName != null
  // 禁用原因（title 悬浮说明，让用户知道按钮为何点不了：未填学科 / 未选考试）
  const disabledReason = !canQuery
    ? (teaching ? (scopeSubject ?? '') : subjectDraft).trim() === ''
      ? teaching
        ? '不可计算：任教学科尚未解析'
        : '不可计算：尚未填写作业学科'
      : '不可计算：尚未选择考试'
    : undefined
  const layerOrder = ['all', 'high_score', 'critical', 'weak'] as const

  return (
    <Card>
      <CardHeader>
        <CardTitle>作业提交率 × 成绩相关性</CardTitle>
        <CardDescription>
          {mode === 'homeroom'
            ? '班主任口径：Y = 所选成绩指标（默认主三门总分，与 AI 助手一致），X = 考前窗口作业提交率'
            : '教学口径：Y = 任教学科单科成绩分数，X = 考前窗口作业提交率'}
          ；窗口不含考试当日与考后作业，忘带/请假/出勤不计入分子分母。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* 查询条件（筛选控件不参与打印） */}
        <div className="flex flex-col gap-3 print:hidden lg:flex-row lg:items-end">
          <div className="space-y-1">
            <label className="text-xs font-medium text-slate-500" htmlFor="hw-corr-subject">
              学科{teaching ? '（任教学科，固定）' : ''}
            </label>
            <Input
              id="hw-corr-subject"
              value={teaching ? (scopeSubject ?? '解析中…') : subjectDraft}
              onChange={(e) => setSubjectDraft(e.target.value)}
              disabled={teaching}
              placeholder={teaching ? undefined : '如 物理'}
              className="lg:w-36"
            />
            {/* A3：班主任域草稿为空时「计算」不可点，就地说明原因（仅班主任域显示） */}
            {!teaching && subjectDraft.trim() === '' ? (
              <p className="text-[10px] text-slate-400">输入作业学科（如：物理）后可计算</p>
            ) : null}
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-slate-500" htmlFor="hw-corr-exam">
              考试
            </label>
            <select
              id="hw-corr-exam"
              value={examName ?? ''}
              onChange={(e) => setExamName(e.target.value === '' ? null : e.target.value)}
              className="h-10 rounded-md border border-slate-200 bg-white px-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {exams == null ? <option value="">加载中…</option> : null}
              {exams != null && exams.length === 0 ? <option value="">（无已导入考试）</option> : null}
              {(exams ?? []).map((e) => (
                <option key={e.exam_name} value={e.exam_name}>
                  {e.exam_name}
                  {e.exam_date ? `（${e.exam_date.slice(0, 10)}）` : ''}
                </option>
              ))}
            </select>
          </div>
          {!teaching ? (
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-500" htmlFor="hw-corr-metric">
                成绩指标（Y）
              </label>
              <select
                id="hw-corr-metric"
                value={metricDraft}
                onChange={(e) => setMetricDraft(e.target.value)}
                className="h-10 rounded-md border border-slate-200 bg-white px-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {METRIC_OPTIONS.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </div>
          ) : null}
          <div className="space-y-1">
            <label className="text-xs font-medium text-slate-500" htmlFor="hw-corr-window">
              考前窗口
            </label>
            <select
              id="hw-corr-window"
              value={String(windowDays)}
              onChange={(e) => setWindowDays(e.target.value === '30' ? 30 : 14)}
              className="h-10 rounded-md border border-slate-200 bg-white px-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <option value="14">14 天</option>
              <option value="30">30 天</option>
            </select>
          </div>
          {teaching ? (
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-500" htmlFor="hw-corr-type">
                作业种类（可选）
              </label>
              <Input
                id="hw-corr-type"
                value={homeworkType}
                onChange={(e) => setHomeworkType(e.target.value)}
                placeholder="全部种类"
                className="lg:w-36"
              />
            </div>
          ) : null}
          <Button
            type="button"
            size="sm"
            disabled={!canQuery}
            title={disabledReason}
            onClick={() => {
              setApplied({ subject: subjectDraft, homeworkType, windowDays, metric: metricDraft })
              setCorrNonce((v) => v + 1)
            }}
          >
            <RefreshCw className="h-4 w-4" /> 计算
          </Button>
          {examsError ? <p className="text-xs text-amber-600">{examsError}</p> : null}
        </div>

        {corrError ? (
          <div className="flex flex-col items-center gap-3 py-6 text-center">
            <AlertCircle className="h-8 w-8 text-amber-400" />
            <p className="text-sm text-slate-600">{corrError}</p>
            <Button variant="outline" size="sm" onClick={() => setCorrNonce((v) => v + 1)}>
              重试
            </Button>
          </div>
        ) : corr == null ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <>
            {/* 窗口与指标含义（后端 note 驱动） */}
            <p className="text-xs text-slate-500">
              考试日期 {corr.exam_date ?? '未知'}；窗口 {corr.window_start ?? '—'} 至{' '}
              {corr.window_end ?? '—'}（考前 {String(corr.window_days)} 天，不含考试当日与考后作业）；
              指标：{corr.metric_label}。
            </p>

            {/* r/rho 结果区：null 必须显示不可计算态，绝不显示数值 */}
            {corr.r == null ? (
              <div className="flex items-start gap-2 rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-3">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                <div>
                  <p className="text-sm font-medium text-slate-600">
                    相关系数不可计算（样本不足或零方差）
                  </p>
                  <p className="mt-0.5 text-xs text-slate-400">
                    配对样本 n={String(corr.n)}；n&lt;8、零方差或分母未知占比&gt;50% 时不给出系数。
                  </p>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                <span className="num-display text-xl font-semibold text-slate-900">
                  r = {corr.r.toFixed(4)}
                </span>
                <span className="num-display text-xl font-semibold text-slate-900">
                  ρ = {corr.rho == null ? '—' : corr.rho.toFixed(4)}
                </span>
                <span className="text-slate-600">{correlationDirectionLabel(corr.direction)}</span>
                <span className="text-xs text-slate-400">配对样本 n={String(corr.n)}</span>
              </div>
            )}

            {/* 分层表：该场学校段位 + 全班（不可算层显示原因，绝不显示数值） */}
            <div className="overflow-x-auto">
              <table className="w-full min-w-[420px] text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-left text-xs text-slate-400">
                    <th className="py-1.5 pr-3 font-medium">分层</th>
                    <th className="py-1.5 pr-3 font-medium">样本 n</th>
                    <th className="py-1.5 pr-3 font-medium">Pearson r</th>
                    <th className="py-1.5 pr-3 font-medium">Spearman ρ</th>
                    <th className="py-1.5 font-medium">说明</th>
                  </tr>
                </thead>
                <tbody>
                  {layerOrder.map((key) =>
                    corr.layers[key] ? <LayerRow key={key} layer={corr.layers[key]} /> : null,
                  )}
                </tbody>
              </table>
            </div>

            {scatterData.length > 0 ? (
              <CorrelationScatter pairs={scatterData} />
            ) : (
              <p className="py-6 text-center text-sm text-slate-500">
                暂无可配对的学生（作业提交率与考试成绩需同时存在）
              </p>
            )}

            {corr.caveats.length > 0 ? (
              <ul className="list-disc space-y-0.5 pl-5 text-xs text-slate-500">
                {corr.caveats.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            ) : null}

            <p className="text-[10px] text-slate-400">{corr.note}</p>
            <p className="text-[10px] text-slate-400">
              免责声明：相关性仅描述统计关联，不构成因果结论，也不构成提分保证——不能仅凭提交率与成绩相关就认定
              交作业导致成绩变化（或相反）；请结合个别谈话与作业质量综合判断。
            </p>
          </>
        )}
      </CardContent>
    </Card>
  )
}
