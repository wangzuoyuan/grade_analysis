'use client'

/**
 * 数据上传（P3 重构为工作台感知，契约 p3-imports-analysis.md §1/§3）。
 *
 * 导入域由当前工作台决定（事实源 = URL 路径前缀或 ?ws，契约 p1-api v2.1 §3）：homeroom →
 * 全科+总分入班主任域；teaching → 仅任教学科（其他学科列被后端过滤并计入文件警告）。
 * 流程：选文件 → imports/preview（multipart，零业务写入）→ items 确认（含跨学年身份
 * 候选逐别名确认，F06）→ imports/confirm；409 冲突时展示冲突表并支持 revise=true
 * 修订重试，409 候选清单补选后重试。mode 切换或文件集合变化即作废旧 token（世代号），
 * 过期 token 一律拒绝。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import {
  AlertTriangle,
  BarChart3,
  Check,
  FileSpreadsheet,
  Loader2,
  RefreshCw,
  UploadCloud,
  X,
} from 'lucide-react'

import {
  importsConfirmP3,
  importsPreviewMultipart,
  readImportConflicts,
  readImportIdentityCandidates,
  type ImportConfirmConflict,
  type ImportIdentityCandidate,
  type ImportsConfirmResult,
  type ImportsPreviewResult,
} from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { useWorkspace } from '@/lib/workspace'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { ImportsPreviewTable } from '@/components/upload/ImportsPreviewTable'
import { ImportConflictsPanel } from '@/components/upload/ImportConflictsPanel'
import {
  IdentityCandidatesPanel,
  type IdentityCandidateGroup,
  type IdentityPick,
} from '@/components/upload/IdentityCandidatesPanel'
import { cn } from '@/lib/utils'

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

/** 步骤指示（沿用教学版两步样式：选文件 → 预览确认）。 */
function StepIndicator({ current }: { current: 1 | 2 }) {
  const steps = [
    { index: 1, title: '选择 Excel 文件' },
    { index: 2, title: '预览与确认' },
  ]
  return (
    <div className="flex items-center">
      {steps.map((step, i) => {
        const isDone = step.index < current
        const isActive = step.index === current
        return (
          <div key={step.index} className="flex flex-1 items-center">
            <div className="flex items-center gap-3">
              <div
                className={cn(
                  'flex h-9 w-9 items-center justify-center rounded-full text-sm font-semibold transition-colors',
                  isDone && 'bg-success-500 text-white',
                  isActive && 'bg-brand-500 text-white',
                  !isDone && !isActive && 'bg-slate-200 text-slate-500',
                )}
              >
                {isDone ? <Check className="h-5 w-5" /> : step.index}
              </div>
              <div className="hidden sm:block">
                <div
                  className={cn(
                    'text-sm font-medium',
                    isDone && 'text-success-500',
                    isActive && 'text-brand-700',
                    !isDone && !isActive && 'text-slate-500',
                  )}
                >
                  {step.title}
                </div>
              </div>
            </div>
            {i < steps.length - 1 && (
              <div
                className={cn(
                  'mx-4 h-px flex-1 border-t border-dashed',
                  isDone ? 'border-success-500' : 'border-slate-300',
                )}
              />
            )}
          </div>
        )
      })}
    </div>
  )
}

export default function UploadPage() {
  const { mode, filter, switching } = useWorkspace()

  const [files, setFiles] = useState<File[]>([])
  const [isDragging, setIsDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // P3 遗留补齐：考试名/考试日期覆盖输入（可选填；解析出的值优先级低于显式覆盖）
  const [examNameOverride, setExamNameOverride] = useState('')
  const [examDateOverride, setExamDateOverride] = useState('')

  const [previewing, setPreviewing] = useState(false)
  const [preview, setPreview] = useState<ImportsPreviewResult | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)

  const [confirming, setConfirming] = useState(false)
  const [conflicts, setConflicts] = useState<ImportConfirmConflict[] | null>(null)
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [result, setResult] = useState<ImportsConfirmResult | null>(null)

  // F06 身份候选：preview items 的候选 + confirm 409 体补发的候选，归并后逐别名确认
  const [identityPicks, setIdentityPicks] = useState<Record<string, IdentityPick>>({})
  const [confirmCandidates, setConfirmCandidates] = useState<ImportIdentityCandidate[] | null>(null)

  // 世代号：mode 切换或文件集合/覆盖输入变化即 +1 并丢弃旧 token（契约 §1.2：token 与快照绑定）
  const reqRef = useRef(0)
  // 作废键 = 文件集合 + 考试名/日期覆盖输入：任一变化都使旧预览 token 失效
  const filesKey = useMemo(
    () =>
      files.map((f) => `${f.name}::${String(f.size)}`).join('|') +
      `::exam=${examNameOverride.trim()}::date=${examDateOverride.trim()}`,
    [files, examNameOverride, examDateOverride],
  )
  const prevModeRef = useRef(mode)

  useEffect(() => {
    const modeChanged = prevModeRef.current !== mode
    prevModeRef.current = mode
    reqRef.current += 1
    setPreview(null)
    setPreviewError(null)
    setConflicts(null)
    setConfirmError(null)
    setIdentityPicks({})
    setConfirmCandidates(null)
    if (modeChanged) {
      // 切换导入域后旧文件列表、覆盖输入与结果都无意义，整体重置
      setFiles([])
      setExamNameOverride('')
      setExamDateOverride('')
      setResult(null)
    }
  }, [mode, filesKey])

  function addFiles(incoming: FileList | File[]) {
    const next = Array.from(incoming).filter((f) => f.name.toLowerCase().endsWith('.xlsx'))
    if (next.length === 0) return
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}::${String(f.size)}`))
      const merged = [...prev]
      for (const f of next) {
        const key = `${f.name}::${String(f.size)}`
        if (!seen.has(key)) {
          merged.push(f)
          seen.add(key)
        }
      }
      return merged
    })
  }

  function removeFile(idx: number) {
    setFiles((prev) => prev.filter((_, i) => i !== idx))
  }

  /** 组装 preview 的 multipart 表单：mode 恒显式；作用域参数从当前工作台筛选映射。 */
  function buildForm(): FormData {
    const form = new FormData()
    form.set('mode', mode)
    for (const f of files) form.append('files', f)
    if (typeof filter.academic_year_id === 'number') {
      form.set('academic_year_id', String(filter.academic_year_id))
    }
    if (mode === 'homeroom' && typeof filter.class_id === 'number') {
      form.set('class_id', String(filter.class_id))
    }
    if (mode === 'teaching' && typeof filter.teaching_class_id === 'number') {
      form.set('teaching_class_id', String(filter.teaching_class_id))
    }
    // 覆盖输入仅在非空时传递，交由后端覆盖文件内解析出的考试名/日期
    if (examNameOverride.trim() !== '') form.set('exam_name', examNameOverride.trim())
    if (examDateOverride.trim() !== '') form.set('exam_date', examDateOverride.trim())
    return form
  }

  async function handlePreview() {
    if (files.length === 0) return
    const req = ++reqRef.current
    setPreviewing(true)
    setPreviewError(null)
    setConflicts(null)
    setConfirmError(null)
    try {
      const r = await importsPreviewMultipart(buildForm())
      if (req !== reqRef.current) return
      setPreview(r)
      // 新预览 = 新候选集：旧的确认选择与 409 候选一律作废
      setIdentityPicks({})
      setConfirmCandidates(null)
    } catch (err) {
      if (req !== reqRef.current) return
      setPreview(null)
      setPreviewError(apiErrorMessage(err))
    } finally {
      if (req === reqRef.current) setPreviewing(false)
    }
  }

  async function handleConfirm(revise: boolean) {
    const token = preview?.token
    if (token == null) return
    const req = ++reqRef.current
    setConfirming(true)
    setConfirmError(null)
    setConflicts(null)
    try {
      // F06：alias_value → 接续的历史 person_id 显式映射；选「新建学生」的
      // 别名进 identity_new_aliases（完备性 = 每个候选二选一，后端缺任一即 409）
      const confirmations: Record<string, number> = {}
      const newAliases: string[] = []
      for (const [alias, pick] of Object.entries(identityPicks)) {
        if (typeof pick === 'number') confirmations[alias] = pick
        else if (pick === 'new') newAliases.push(alias)
      }
      const r = await importsConfirmP3(
        token,
        revise,
        Object.keys(confirmations).length > 0 ? confirmations : undefined,
        newAliases.length > 0 ? newAliases : undefined,
      )
      if (req !== reqRef.current) return
      setResult(r)
      setPreview(null)
      setFiles([])
      setIdentityPicks({})
      setConfirmCandidates(null)
    } catch (err) {
      if (req !== reqRef.current) return
      // 409 + conflicts：展示冲突表供人工决策（保持库内值或修订重试），零写入
      const list = readImportConflicts(err)
      setConflicts(list)
      // 409 + candidates（F06）：存在历史命中未确认，补发候选清单供补选后重试
      const cands = readImportIdentityCandidates(err)
      if (cands) setConfirmCandidates(cands)
      setConfirmError(apiErrorMessage(err))
    } finally {
      if (req === reqRef.current) setConfirming(false)
    }
  }

  function resetAll() {
    reqRef.current += 1
    setFiles([])
    setPreview(null)
    setPreviewError(null)
    setConflicts(null)
    setConfirmError(null)
    setResult(null)
    setIdentityPicks({})
    setConfirmCandidates(null)
  }

  const modeHint =
    mode === 'homeroom'
      ? '当前工作台：班主任。每场考试可同时选择学生成绩明细表和班级均分表；前者导入本班成绩（全科+总分入班主任域），后者导入全年级各班均分与班级排名。'
      : '当前工作台：教学。仅导入任教学科，其他学科列将被过滤（计入文件警告）。'

  const teachingAllClasses = mode === 'teaching' && typeof filter.teaching_class_id !== 'number'

  const anyParseFailed = (preview?.items ?? []).some((it) => !it.parsed_ok)

  // F06：归并 preview 候选与 409 补发候选，按别名分组去重（person_id + 学年）
  const identityGroups = useMemo<IdentityCandidateGroup[]>(() => {
    const byAlias = new Map<string, { name: string; options: ImportIdentityCandidate[] }>()
    const add = (c: ImportIdentityCandidate) => {
      if (typeof c?.alias !== 'string' || c.alias === '') return
      const entry = byAlias.get(c.alias) ?? { name: '', options: [] }
      if (!entry.name && c.name) entry.name = c.name
      if (!entry.options.some((o) => o.person_id === c.person_id && o.academic_year_id === c.academic_year_id)) {
        entry.options.push(c)
      }
      byAlias.set(c.alias, entry)
    }
    for (const it of preview?.items ?? []) for (const c of it.identity_candidates ?? []) add(c)
    for (const c of confirmCandidates ?? []) add(c)
    return Array.from(byAlias.entries())
      .map(([alias, e]) => ({ alias, name: e.name, options: e.options }))
      .sort((a, b) => a.alias.localeCompare(b.alias, 'zh-CN'))
  }, [preview, confirmCandidates])

  // 存在候选但未逐别名选择（含「新建」）时整批禁止确认——后端对未决候选一律 409 零写入
  const identityAllPicked =
    identityGroups.length === 0 || identityGroups.every((g) => identityPicks[g.alias] !== undefined)

  function pickIdentity(alias: string, pick: IdentityPick) {
    setIdentityPicks((prev) => ({ ...prev, [alias]: pick }))
  }

  const scoresHref = mode === 'homeroom' ? '/homeroom/scores' : '/teaching/scores'
  const step: 1 | 2 = preview != null || result != null ? 2 : 1

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">数据上传</h1>
          <p className="mt-1 text-sm text-slate-500">
            上传 .xlsx 成绩表并确认入库{switching ? ' · 正在切换工作台…' : ''}
          </p>
        </div>
        <Badge variant={mode === 'homeroom' ? 'default' : 'secondary'}>
          {mode === 'homeroom' ? '导入到班主任域' : '导入到教学域'}
        </Badge>
      </div>

      <Card>
        <CardContent className="py-5">
          <StepIndicator current={step} />
        </CardContent>
      </Card>

      {/* 工作台感知说明：导入域与范围 */}
      <Card className="print:hidden">
        <CardContent className="space-y-1 py-4 text-sm text-slate-600">
          <p>{modeHint}</p>
          {teachingAllClasses ? (
            <p className="text-xs text-warning-600">
              当前未选择具体教学班：仅当该学科只有一个教学班时可省略；
              若带多个教学班，请先在「教学工作台」总览选择班级再上传。
            </p>
          ) : null}
          <p className="text-xs text-slate-400">
            预览阶段不写入任何成绩；确认入库为单事务，任一文件失败将整批回滚。
          </p>
        </CardContent>
      </Card>

      {result != null ? (
        <Card className="border-success-300 bg-success-50/40">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Check className="h-4 w-4 text-success-500" aria-hidden="true" />
              导入完成
            </CardTitle>
            <CardDescription>
              共入库 {String(result.imported)} 条 · 跳过同值 {String(result.skipped)} 条 · 修订{' '}
              {String(result.revised)} 条；新建学生 {String(result.students_created)} 人 ·
              教学班成员同步 {String(result.members_synced)} 条。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {result.exams.length > 0 ? (
              <p className="text-xs text-slate-500">
                涉及考试：
                {result.exams
                  .map((e) => `${e.exam_name}（${e.exam_date ? e.exam_date.slice(0, 10) : '日期未知'}）`)
                  .join('、')}
              </p>
            ) : null}
            <div className="flex flex-wrap gap-2 print:hidden">
              <Button asChild variant="outline" size="sm">
                <Link href={scoresHref}>
                  <BarChart3 className="h-4 w-4" />
                  查看成绩分析
                </Link>
              </Button>
              <Button variant="ghost" size="sm" onClick={resetAll}>
                <UploadCloud className="h-4 w-4" />
                继续上传
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {/* Step 1：选择文件（拖拽/多选，沿用既有 UI 模式） */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Step 1 · 选择 Excel 文件</CardTitle>
          <CardDescription>支持一次拖入多份 .xlsx；按当前工作台解析入库</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div
            onDragOver={(e) => {
              e.preventDefault()
              setIsDragging(true)
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={(e) => {
              e.preventDefault()
              setIsDragging(false)
              addFiles(e.dataTransfer.files)
            }}
            onClick={() => inputRef.current?.click()}
            className={cn(
              'flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-10 text-center transition-colors',
              isDragging ? 'border-brand-500 bg-brand-50' : 'border-slate-300 hover:border-brand-500 hover:bg-brand-50',
            )}
          >
            <UploadCloud
              className={cn('h-12 w-12 transition-colors', isDragging ? 'text-brand-500' : 'text-slate-400')}
            />
            <p className="mt-3 text-sm text-slate-600">
              拖入 Excel 文件，或
              <span className="ml-1 font-medium text-brand-600">点击选择文件</span>
            </p>
            <p className="mt-1 text-xs text-slate-400">仅支持 .xlsx，可多选</p>
            <input
              ref={inputRef}
              type="file"
              multiple
              accept=".xlsx"
              className="hidden"
              onChange={(e) => {
                if (e.target.files) addFiles(e.target.files)
                e.target.value = ''
              }}
            />
          </div>

          {files.length > 0 ? (
            <div className="space-y-2">
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
                已选择 {String(files.length)} 个文件
              </div>
              <div className="space-y-2">
                {files.map((f, i) => (
                  <Card key={`${f.name}-${String(i)}`} className="border-slate-200">
                    <CardContent className="flex items-center gap-3 py-3">
                      <div className="flex h-9 w-9 items-center justify-center rounded-md bg-brand-50 text-brand-600">
                        <FileSpreadsheet className="h-5 w-5" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium text-slate-800">{f.name}</div>
                        <div className="text-xs text-slate-500">{formatBytes(f.size)}</div>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={(e) => {
                          e.stopPropagation()
                          removeFile(i)
                        }}
                        disabled={previewing || confirming}
                        aria-label="移除"
                      >
                        <X className="h-4 w-4 text-slate-500" />
                      </Button>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          ) : null}

          {/* 考试名/日期覆盖（可选）：留空则按文件内解析值入库 */}
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <label htmlFor="upload-exam-name" className="text-xs font-medium text-slate-500">
                考试名覆盖（可选）
              </label>
              <Input
                id="upload-exam-name"
                value={examNameOverride}
                onChange={(e) => setExamNameOverride(e.target.value)}
                placeholder="如 高二上期中考试"
                disabled={previewing || confirming}
              />
            </div>
            <div className="space-y-1">
              <label htmlFor="upload-exam-date" className="text-xs font-medium text-slate-500">
                考试日期覆盖（可选，YYYY-MM-DD）
              </label>
              <Input
                id="upload-exam-date"
                type="date"
                value={examDateOverride}
                onChange={(e) => setExamDateOverride(e.target.value)}
                disabled={previewing || confirming}
              />
            </div>
          </div>

          {previewError ? (
            <Card className="border-danger-500 bg-danger-50">
              <CardContent className="flex flex-wrap items-start justify-between gap-2 py-3">
                <div className="flex items-start gap-2 text-sm text-danger-500">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  {previewError}
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void handlePreview()}
                  disabled={previewing || files.length === 0}
                >
                  <RefreshCw className="h-4 w-4" />
                  重试
                </Button>
              </CardContent>
            </Card>
          ) : null}

          <div className="flex items-center justify-end gap-2 print:hidden">
            <Button
              variant="outline"
              onClick={resetAll}
              disabled={files.length === 0 || previewing || confirming}
            >
              清空
            </Button>
            <Button onClick={() => void handlePreview()} disabled={files.length === 0 || previewing}>
              {previewing ? (
                <>
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  解析中
                </>
              ) : (
                <>
                  <UploadCloud className="mr-1 h-4 w-4" />
                  下一步：解析预览
                </>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Step 2：预览与确认（token 一次性，过期/成员变化需重新预览） */}
      {preview != null ? (
        <>
          {conflicts != null && conflicts.length > 0 ? (
            <ImportConflictsPanel
              conflicts={conflicts}
              revising={confirming}
              onRevise={() => void handleConfirm(true)}
              onCancel={resetAll}
            />
          ) : null}

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Step 2 · 预览与确认</CardTitle>
              <CardDescription>
                预览令牌有效期至 {preview.expires_at ? preview.expires_at.slice(0, 19).replace('T', ' ') : '—'}；
                切换工作台或改动文件列表后需重新预览。
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <ImportsPreviewTable items={preview.items} />

              {/* F06：跨学年身份候选确认区（preview 候选 + 409 补发候选共用一套 UI） */}
              <IdentityCandidatesPanel groups={identityGroups} picks={identityPicks} onPick={pickIdentity} />
              {identityGroups.length > 0 && !identityAllPicked ? (
                <p className="text-sm text-warning-600">
                  存在未决的跨学年身份候选：请为每位学生选择「接续历史身份」或「新建学生」后再确认导入。
                </p>
              ) : null}

              {anyParseFailed ? (
                <p className="text-sm text-danger-500">
                  存在解析失败的文件：确认入库为单事务，请先移除失败文件后重新预览。
                </p>
              ) : null}

              {confirmError && (conflicts == null || conflicts.length === 0) ? (
                <Card className="border-danger-500 bg-danger-50">
                  <CardContent className="flex items-start gap-2 py-3">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-danger-500" />
                    <div className="text-sm text-danger-500">{confirmError}</div>
                  </CardContent>
                </Card>
              ) : null}

              <div className="flex flex-wrap items-center justify-between gap-2 print:hidden">
                <p className="text-xs text-slate-400">
                  同名同场次同值会自动跳过；不同值默认整批拒绝（见冲突表）。
                </p>
                <div className="flex gap-2">
                  <Button variant="outline" onClick={resetAll} disabled={confirming}>
                    取消
                  </Button>
                  <Button
                    onClick={() => void handleConfirm(false)}
                    disabled={confirming || preview.items.length === 0 || anyParseFailed || !identityAllPicked}
                  >
                    {confirming ? (
                      <>
                        <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                        入库中
                      </>
                    ) : (
                      '确认导入'
                    )}
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </>
      ) : null}
    </div>
  )
}
