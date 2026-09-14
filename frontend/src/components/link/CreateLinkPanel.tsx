'use client'

import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Loader2,
  RefreshCw,
  ShieldQuestion,
} from 'lucide-react'
import {
  confirmLink,
  previewLink,
  type ClassesCatalogHomeroom,
  type ClassesCatalogTeaching,
  type LinkConfirm,
  type LinkPreview,
} from '@/lib/api-v1'
import { formatClassLabel } from '@/lib/labels'

import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

import { apiErrorMessage, isApiErrorCode } from './error-text'
import { RosterDiffView } from './RosterDiff'

type WizardStep = 'select' | 'preview' | 'done'

function formatDateTime(v: string | null | undefined): string {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  return d.toLocaleString('zh-CN', { hour12: false })
}

/** 契约中 admin_class_id / teaching_class_id 为整数 ID；手动输入统一按数字解析。 */
function parseClassId(raw: string): number | null {
  const t = raw.trim()
  if (t === '') return null
  const n = Number(t)
  if (!Number.isInteger(n)) return null
  return n
}

const STEP_LABELS = ['选择班级', '预览差异', '确认完成']

function StepIndicator({ step }: { step: WizardStep }) {
  const current = step === 'select' ? 0 : step === 'preview' ? 1 : 2
  return (
    <ol className="flex flex-wrap items-center gap-2 text-sm" aria-label="新建关联步骤">
      {STEP_LABELS.map((label, i) => {
        const state = i < current ? 'done' : i === current ? 'current' : 'todo'
        return (
          <li key={label} className="flex items-center gap-2" aria-current={state === 'current' ? 'step' : undefined}>
            <span
              aria-hidden="true"
              className={
                state === 'current'
                  ? 'inline-flex h-6 w-6 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold text-white'
                  : state === 'done'
                    ? 'inline-flex h-6 w-6 items-center justify-center rounded-full bg-success-500 text-xs font-semibold text-white'
                    : 'inline-flex h-6 w-6 items-center justify-center rounded-full bg-slate-100 text-xs font-semibold text-slate-400'
              }
            >
              {state === 'done' ? <CheckCircle2 className="h-3.5 w-3.5" /> : i + 1}
            </span>
            <span className={state === 'current' ? 'font-medium text-slate-900' : 'text-slate-500'}>{label}</span>
            {i < STEP_LABELS.length - 1 ? (
              <span aria-hidden="true" className="h-px w-6 bg-slate-200" />
            ) : null}
          </li>
        )
      })}
    </ol>
  )
}

interface ClassOption {
  id: number
  display: string
  sub?: string
}

/**
 * 班级取值控件：候选取自 fetchClasses 班级目录（契约补丁端点）。
 * 仅当目录为空或请求失败时回退为手动输入数字班级 ID；越权项由后端拦截。
 */
function ClassField({
  label,
  hint,
  options,
  value,
  onChange,
  idPrefix,
  loading = false,
  failed = false,
}: {
  label: string
  hint: string
  options: ClassOption[]
  value: string
  onChange: (v: string) => void
  idPrefix: string
  loading?: boolean
  failed?: boolean
}) {
  const manual = failed || (!loading && options.length === 0)
  const selectValue = options.some((o) => String(o.id) === value) ? value : undefined

  return (
    <div className="space-y-1.5">
      <label htmlFor={manual ? `${idPrefix}-input` : `${idPrefix}-select`} className="text-sm font-medium text-slate-700">
        {label}
      </label>
      {manual ? (
        <Input
          id={`${idPrefix}-input`}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="输入班级 ID（数字）"
          autoComplete="off"
        />
      ) : (
        <Select value={selectValue} onValueChange={onChange}>
          <SelectTrigger id={`${idPrefix}-select`} aria-label={label} disabled={loading}>
            <SelectValue placeholder={loading ? '班级目录加载中…' : '请选择'} />
          </SelectTrigger>
          <SelectContent>
            {options.map((o) => (
              <SelectItem key={o.id} value={String(o.id)}>
                <span className="flex items-baseline gap-1.5">
                  <span className="truncate">{o.display}</span>
                  {o.sub ? <span className="shrink-0 text-xs text-slate-400">{o.sub}</span> : null}
                  <span className="shrink-0 font-mono text-xs text-slate-400">#{o.id}</span>
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      <p className="text-xs text-slate-400">{hint}</p>
    </div>
  )
}

interface CreateLinkPanelProps {
  /** 当前选中学年（整型 ID）；null 表示尚未确定。 */
  academicYear: number | null
  /** 学年显示名（如 2025-2026），优先取班级目录返回的 academic_year_name。 */
  academicYearName: string | null
  subject: string | null
  adminClassOption: ClassesCatalogHomeroom | null
  teachingClassOptions: ClassesCatalogTeaching[]
  classesLoading: boolean
  classesFailed: boolean
  onCreated: () => void
}

/**
 * 新建关联三步向导：选择班级 → previewLink 名册差异 → confirmLink 落库。
 * 409 link_version_conflict 时提示重新预览并停留在预览步。
 */
export function CreateLinkPanel({
  academicYear,
  academicYearName,
  subject,
  adminClassOption,
  teachingClassOptions,
  classesLoading,
  classesFailed,
  onCreated,
}: CreateLinkPanelProps) {
  const [step, setStep] = useState<WizardStep>('select')
  const [adminClassId, setAdminClassId] = useState('')
  const [teachingClassId, setTeachingClassId] = useState('')
  const [preview, setPreview] = useState<LinkPreview | null>(null)
  const [previewStale, setPreviewStale] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [result, setResult] = useState<LinkConfirm | null>(null)
  const [error, setError] = useState<string | null>(null)

  // 学年由页面级选择决定；学年变化后旧选择与旧预览口径不再可信，整体回退到第一步。
  useEffect(() => {
    setStep('select')
    setAdminClassId('')
    setTeachingClassId('')
    setPreview(null)
    setPreviewStale(false)
    setResult(null)
    setError(null)
  }, [academicYear])

  const subjectReady = subject != null && subject !== ''
  const adminId = parseClassId(adminClassId)
  const teachingId = parseClassId(teachingClassId)
  const idMalformed =
    (adminClassId.trim() !== '' && adminId === null) ||
    (teachingClassId.trim() !== '' && teachingId === null)
  const canPreview =
    academicYear != null &&
    subjectReady &&
    adminId !== null &&
    teachingId !== null &&
    !previewing &&
    !confirming

  const adminOptions = useMemo<ClassOption[]>(() => {
    if (!adminClassOption) return []
    const gradeLabel = formatClassLabel(adminClassOption.grade, adminClassOption.class_num)
    return [
      {
        id: adminClassOption.class_id,
        display: gradeLabel ? `${gradeLabel} · ${adminClassOption.label}` : adminClassOption.label,
      },
    ]
  }, [adminClassOption])

  const teachingOptions = useMemo<ClassOption[]>(
    () =>
      teachingClassOptions.map((c) => ({
        id: c.class_id,
        display: c.label,
        sub: c.subject,
      })),
    [teachingClassOptions],
  )

  const diff = useMemo(() => preview?.roster_diff ?? null, [preview])
  const yearLabel = academicYearName ?? (academicYear != null ? `学年 #${academicYear}` : '未确定')

  function resetWizard() {
    setStep('select')
    setAdminClassId('')
    setTeachingClassId('')
    setPreview(null)
    setPreviewStale(false)
    setResult(null)
    setError(null)
  }

  async function runPreview() {
    if (academicYear == null) {
      setError('未能确定学年，请使用页面顶部“刷新”重试')
      return
    }
    if (!subjectReady) {
      setError('请先完成工作台配置（缺少任教学科）')
      return
    }
    if (adminId === null || teachingId === null) {
      setError(idMalformed ? '班级 ID 需为数字' : '请先选择行政班与教学班')
      return
    }
    setPreviewing(true)
    setError(null)
    setPreviewStale(false)
    try {
      const p = await previewLink({
        admin_class_id: adminId,
        teaching_class_id: teachingId,
        academic_year_id: academicYear,
        subject,
      })
      setPreview(p)
      setStep('preview')
    } catch (err) {
      setError(apiErrorMessage(err))
    } finally {
      setPreviewing(false)
    }
  }

  async function runConfirm() {
    if (!preview || previewStale) return
    setConfirming(true)
    setError(null)
    try {
      const r = await confirmLink(preview.token)
      setResult(r)
      setStep('done')
      onCreated()
    } catch (err) {
      if (isApiErrorCode(err, 'link_version_conflict')) {
        // 契约：token 过期或范围变化 → 回到预览步重新生成。
        setPreviewStale(true)
        setError('预览已过期或范围变化，请重新预览')
      } else {
        setError(apiErrorMessage(err))
      }
    } finally {
      setConfirming(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>新建关联</CardTitle>
        <CardDescription>
          仅支持本人班主任班与对应教学班建立关联；其他教学班不会进入班主任工作台。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <StepIndicator step={step} />

        {error ? (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-lg border border-danger-300 bg-danger-50 p-3 text-sm text-danger-600"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <p>{error}</p>
          </div>
        ) : null}

        {classesFailed ? (
          <div
            role="status"
            className="flex items-start gap-2 rounded-lg border border-warning-300 bg-warning-50 p-3 text-sm text-warning-700"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <p>班级目录加载失败，已切换为手动输入班级 ID；可使用页面顶部“刷新”重试。</p>
          </div>
        ) : null}

        {academicYear == null ? (
          <div
            role="status"
            className="flex items-start gap-2 rounded-lg border border-warning-300 bg-warning-50 p-3 text-sm text-warning-700"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <p>未能确定默认学年，暂无法新建关联；请使用页面顶部“刷新”重试。</p>
          </div>
        ) : null}

        {!subjectReady ? (
          <div className="flex items-start gap-2 rounded-lg border border-warning-300 bg-warning-50 p-3 text-sm text-warning-700">
            <ShieldQuestion className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <p>教学工作台尚未配置任教学科，请先完成工作台配置后再建立关联。</p>
          </div>
        ) : null}

        {step === 'select' ? (
          <div className="space-y-5">
            <div className="grid gap-5 md:grid-cols-2">
              <ClassField
                idPrefix="link-admin"
                label="行政班（班主任绑定班）"
                hint={
                  classesFailed
                    ? '目录加载失败，请手动输入行政班 ID（数字）。'
                    : adminOptions.length === 0
                      ? '本学年未获取到绑定的行政班，可手动输入其 ID（数字）。'
                      : '取自本学年班主任绑定的行政班。'
                }
                options={adminOptions}
                value={adminClassId}
                onChange={setAdminClassId}
                loading={classesLoading}
                failed={classesFailed}
              />
              <ClassField
                idPrefix="link-teaching"
                label={`教学班（同学年 · ${subject ?? '任教学科'}）`}
                hint={
                  classesFailed
                    ? '目录加载失败，请手动输入教学班 ID（数字）。'
                    : teachingOptions.length === 0
                      ? '本学年未获取到任教学科的教学班，可手动输入其 ID（数字）。'
                      : '取自本学年任教学科下的全部教学班。'
                }
                options={teachingOptions}
                value={teachingClassId}
                onChange={setTeachingClassId}
                loading={classesLoading}
                failed={classesFailed}
              />
            </div>
            <div className="space-y-1.5">
              <span className="text-sm font-medium text-slate-700">学科</span>
              <div className="flex h-10 items-center gap-2">
                <span className="inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold">
                  {subject ?? '未配置'}
                </span>
                <span className="text-xs text-slate-400">首版固定为当前任教学科，不可修改</span>
              </div>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-xs text-slate-400">
                {idMalformed ? (
                  <span className="text-warning-600">班级 ID 需为数字，请检查输入。</span>
                ) : (
                  <>学年：{yearLabel}（在页面顶部切换）</>
                )}
              </p>
              <Button type="button" onClick={runPreview} disabled={!canPreview}>
                {previewing ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
                {previewing ? '生成预览中…' : '生成名册预览'}
              </Button>
            </div>
          </div>
        ) : null}

        {step === 'preview' && preview ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-slate-600">
              <span>
                行政班 <code className="font-mono">{adminClassId}</code> × 教学班{' '}
                <code className="font-mono">{teachingClassId}</code>
              </span>
              <span className="inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold">
                {subject}
              </span>
              <span className="text-xs text-slate-400">
                {yearLabel} 学年 · 预览有效期至 {formatDateTime(preview.expires_at)}
              </span>
            </div>

            {preview.warning ? (
              <div
                role="alert"
                className="flex items-start gap-2 rounded-lg border border-warning-300 bg-warning-50 p-3 text-sm text-warning-700"
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <div>
                  <p className="font-medium">{preview.warning}</p>
                  <p>同名同号学生不会自动配对，需逐人确认后才计入关联。</p>
                </div>
              </div>
            ) : null}

            {previewStale ? (
              <div
                role="status"
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warning-300 bg-warning-50 p-3 text-sm text-warning-700"
              >
                <span>当前预览已失效，确认按钮已停用，请重新生成预览。</span>
                <Button type="button" variant="secondary" size="sm" onClick={runPreview} disabled={previewing}>
                  <RefreshCw className="h-4 w-4" aria-hidden="true" />
                  重新预览
                </Button>
              </div>
            ) : null}

            {diff ? <RosterDiffView diff={diff} /> : null}

            <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setStep('select')
                  setPreview(null)
                  setPreviewStale(false)
                  setError(null)
                }}
                disabled={confirming}
              >
                <ArrowLeft className="h-4 w-4" aria-hidden="true" />
                返回修改
              </Button>
              {!previewStale ? (
                <Button type="button" variant="secondary" onClick={runPreview} disabled={previewing || confirming}>
                  <RefreshCw className="h-4 w-4" aria-hidden="true" />
                  重新预览
                </Button>
              ) : null}
              <Button type="button" onClick={runConfirm} disabled={previewStale || confirming}>
                {confirming ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
                {confirming ? '确认中…' : '确认建立关联'}
              </Button>
            </div>
          </div>
        ) : null}

        {step === 'done' && result ? (
          <div className="space-y-4">
            <div className="flex items-start gap-3 rounded-lg border border-success-300 bg-success-50 p-4">
              <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-success-500" aria-hidden="true" />
              <div className="space-y-1 text-sm text-success-600">
                <p className="font-semibold text-success-600">关联已建立，按上述范围开始共享教学数据。</p>
                <p>
                  关联 ID <code className="font-mono">{String(result.link_id)}</code> · 版本{' '}
                  <span className="font-mono">v{String(result.version)}</span> · 已配对学生{' '}
                  <span className="font-mono tabular-nums">{String(result.linked_count)}</span> 人
                </p>
                <p className="text-xs text-success-600/80">
                  名册差异中“仅单侧”的学生需在学生管理中逐人确认后才会计入关联共享。
                </p>
              </div>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
              <Button type="button" variant="outline" onClick={resetWizard}>
                收起
              </Button>
              <Button type="button" onClick={resetWizard}>
                再建一条关联
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
