'use client'

/**
 * 作业提交率 × 成绩名次相关性卡（契约 p5-homework.md §4）。
 *
 * 约束：
 * - r=null（n<5 或零方差）→ 显示「不可计算（样本不足或零方差）」，绝不编造数值；
 * - Y 是名次（数值越小越好），散点 y 轴反转让"名次好"朝上；方向文案由后端
 *   direction 字段驱动（submit_up_rank_up / submit_up_rank_down）；
 * - 分母不可用的批次已被后端剔除并在 caveats 注明；
 * - 页面固定渲染免责声明：描述统计关联，绝不表述为因果。
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
  homeworkCorrelation,
  listExams,
  type ExamSummary,
  type HomeworkCorrelationResponse,
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

/** 散点（recharts，参照 SubjectScatter 用法；y 轴 reversed = 名次越小越靠上）。 */
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
          name="名次（小=好）"
          reversed
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
  const [applied, setApplied] = useState<{ subject: string; homeworkType: string }>({
    subject: '',
    homeworkType: '',
  })

  // 考试下拉（与成绩页共用 shared/exams）
  const [exams, setExams] = useState<ExamSummary[] | null>(null)
  const [examsError, setExamsError] = useState<string | null>(null)
  const [examName, setExamName] = useState<string | null>(null)
  const examsReqRef = useRef(0)

  const [corr, setCorr] = useState<HomeworkCorrelationResponse | null>(null)
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

  // 相关性：班主任 Y=总分名次（默认主三门口径由后端决定），教学 Y=单科本班名次；
  // 请求序号只比对 corrReqRef，绝不作废考试清单回包
  useEffect(() => {
    if (examName == null || effectiveSubject === '') return
    const req = ++corrReqRef.current
    setCorr(null)
    setCorrError(null)
    homeworkCorrelation(mode, {
      ...scopeQ,
      subject: effectiveSubject,
      exam_name: examName,
      // 班主任工作台无作业种类概念（UI 不采集也不筛选），只传教学侧的筛选值
      homework_type: teaching && applied.homeworkType !== '' ? applied.homeworkType : undefined,
    })
      .then((r) => {
        if (req === corrReqRef.current) setCorr(r)
      })
      .catch((err: unknown) => {
        if (req !== corrReqRef.current) return
        setCorr(null)
        setCorrError(apiErrorMessage(err))
      })
  }, [mode, scopeQ, generation, examName, effectiveSubject, applied.homeworkType, corrNonce])

  const scatterData = useMemo(
    () =>
      (corr?.pairs ?? []).map((p) => ({
        x: p.x,
        y: p.y,
        name: `${p.name ?? '（未命名）'}（名次 ${String(p.y)}）`,
      })),
    [corr],
  )

  const canQuery = effectiveSubject !== '' && examName != null

  return (
    <Card>
      <CardHeader>
        <CardTitle>作业提交率 × 成绩相关性</CardTitle>
        <CardDescription>
          {mode === 'homeroom'
            ? '班主任口径：Y = 指定总分口径的年级名次（默认主三门），X = 作业提交率'
            : '教学口径：Y = 该科单科本班名次（同分同名次），X = 作业提交率'}
          ；提交率分母不可用的批次已被剔除并注明。
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
            onClick={() => {
              setApplied({ subject: subjectDraft, homeworkType })
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
            {/* r 结果区：null 必须显示不可计算态，绝不显示数值 */}
            {corr.r == null ? (
              <div className="flex items-start gap-2 rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-3">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                <div>
                  <p className="text-sm font-medium text-slate-600">
                    相关系数不可计算（样本不足或零方差）
                  </p>
                  <p className="mt-0.5 text-xs text-slate-400">
                    配对样本 n={String(corr.n)}；n&lt;5 或提交率/名次零方差时不给出 r 值。
                  </p>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                <span className="num-display text-xl font-semibold text-slate-900">
                  r = {corr.r.toFixed(4)}
                </span>
                <span className="text-slate-600">{correlationDirectionLabel(corr.direction)}</span>
                <span className="text-xs text-slate-400">配对样本 n={String(corr.n)}</span>
              </div>
            )}

            {scatterData.length > 0 ? (
              <CorrelationScatter pairs={scatterData} />
            ) : (
              <p className="py-6 text-center text-sm text-slate-500">
                暂无可配对的学生（作业提交率与考试名次需同时存在）
              </p>
            )}

            {corr.caveats.length > 0 ? (
              <ul className="list-disc space-y-0.5 pl-5 text-xs text-slate-500">
                {corr.caveats.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            ) : null}

            <p className="text-[10px] text-slate-400">
              免责声明：相关性仅描述统计关联，不构成因果结论——不能仅凭提交率与名次相关就认定
              交作业导致成绩变化（或相反）；请结合个别谈话与作业质量综合判断。
            </p>
          </>
        )}
      </CardContent>
    </Card>
  )
}
