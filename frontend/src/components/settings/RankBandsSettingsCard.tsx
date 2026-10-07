'use client'

/**
 * 长期名次分段设置卡（P0-A3）。
 *
 * 三类区间分开管理，本卡只管「长期业务分段」（AnalysisConfig 全局单行）：
 * - 临时查询区间（成绩分析页的排名区间筛选/排名频次）按次传参，只影响
 *   当次查询，绝不回写长期配置；
 * - 诊断阈值（偏科/进退步等）另有常量口径，不在本卡修改；
 * - 学校默认三段（高分 1–80、临界 400–500、薄弱 501+）保留为默认值：
 *   「恢复默认」删除自定义行，读取侧回落默认值。
 *
 * 后端入口：GET/PUT/DELETE /api/analysis-config（旧 /api 前缀，错误为
 * FastAPI 的 {"detail": "..."} 形态，与 /api/v1 的 {"error","detail"} 不同，
 * 因此这里用直接 fetch 而不走契约化的 api-v1 客户端）。
 */

import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, Info, Pencil, RefreshCw, RotateCcw, SlidersHorizontal } from 'lucide-react'

import { apiErrorMessage } from '@/components/link/error-text'
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

export interface BandConfigValues {
  high_score_max: number
  critical_min: number
  critical_max: number
  weak_min: number
}

interface BandConfigResponse extends BandConfigValues {
  defaults?: BandConfigValues
  is_default?: boolean
}

/** 后端未返回 defaults 时的兜底（与 analysis/config.py 出厂值一致）。 */
const FALLBACK_DEFAULTS: BandConfigValues = {
  high_score_max: 80,
  critical_min: 400,
  critical_max: 500,
  weak_min: 501,
}

const BAND_FIELDS: Array<{ key: keyof BandConfigValues; label: string; hint: string }> = [
  { key: 'high_score_max', label: '高分段上界', hint: '高分段：第 1 名至上界' },
  { key: 'critical_min', label: '临界段起始名次', hint: '临界段下界' },
  { key: 'critical_max', label: '临界段结束名次', hint: '临界段上界' },
  { key: 'weak_min', label: '薄弱段起始名次', hint: '薄弱段：该名次及以后' },
]

/** 旧 /api 错误体是 {"detail": string}；提取中文说明，失败回退通用文案。 */
async function oldApiError(res: Response): Promise<string> {
  const body = (await res.json().catch(() => null)) as { detail?: unknown } | null
  if (body && typeof body.detail === 'string' && body.detail !== '') {
    return body.detail
  }
  return `请求失败（HTTP ${res.status}）`
}

function networkError(): string {
  return '网络请求失败，请检查连接后重试'
}

export function RankBandsSettingsCard() {
  const [config, setConfig] = useState<BandConfigResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState<Record<keyof BandConfigValues, string>>({
    high_score_max: '',
    critical_min: '',
    critical_max: '',
    weak_min: '',
  })
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const reload = useCallback(() => setNonce((v) => v + 1), [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setLoadError(null)
    fetch('/api/analysis-config', { headers: { Accept: 'application/json' } })
      .then(async (res) => {
        if (!res.ok) throw new Error(await oldApiError(res))
        return (await res.json()) as BandConfigResponse
      })
      .then((data) => {
        if (cancelled) return
        setConfig(data)
        setEditing(false)
      })
      .catch(() => {
        if (cancelled) return
        setConfig(null)
        setLoadError(networkError())
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [nonce])

  function startEdit() {
    if (!config) return
    setForm({
      high_score_max: String(config.high_score_max),
      critical_min: String(config.critical_min),
      critical_max: String(config.critical_max),
      weak_min: String(config.weak_min),
    })
    setActionError(null)
    setEditing(true)
  }

  function validateForm(): BandConfigValues | null {
    const parsed = {} as Record<keyof BandConfigValues, number>
    for (const field of BAND_FIELDS) {
      const value = Number(form[field.key])
      if (!Number.isInteger(value) || value < 1) {
        setActionError('排名阈值必须为正整数')
        return null
      }
      parsed[field.key] = value
    }
    if (parsed.critical_min > parsed.critical_max) {
      setActionError('临界段下界不能大于上界')
      return null
    }
    return parsed
  }

  async function saveEdit() {
    const payload = validateForm()
    if (payload == null) return
    setBusy(true)
    setActionError(null)
    try {
      const res = await fetch('/api/analysis-config', {
        method: 'PUT',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!res.ok) {
        setActionError(await oldApiError(res))
        return
      }
      setEditing(false)
      setNotice('名次分段已保存，名次段计算与对话助手即时采用新口径')
      reload()
    } catch {
      setActionError(networkError())
    } finally {
      setBusy(false)
    }
  }

  async function restoreDefaults() {
    if (
      !window.confirm(
        '确认恢复学校默认三段（高分 1–80、临界 400–500、薄弱 501+）？当前自定义阈值将被丢弃。',
      )
    ) {
      return
    }
    setBusy(true)
    setActionError(null)
    try {
      const res = await fetch('/api/analysis-config', { method: 'DELETE' })
      if (!res.ok) {
        setActionError(await oldApiError(res))
        return
      }
      setEditing(false)
      setNotice('已恢复学校默认三段')
      reload()
    } catch {
      setActionError(networkError())
    } finally {
      setBusy(false)
    }
  }

  const defaults = config?.defaults ?? FALLBACK_DEFAULTS
  const isDefault = config?.is_default ?? true

  function bandRows(values: BandConfigValues): string[] {
    return [
      `高分段：第 1–${values.high_score_max} 名`,
      `临界段：第 ${values.critical_min}–${values.critical_max} 名`,
      `薄弱段：第 ${values.weak_min} 名及以后`,
    ]
  }

  return (
    <Card>
      <CardHeader className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <CardTitle className="flex items-center gap-2">
            <SlidersHorizontal className="h-4 w-4 text-brand-500" aria-hidden="true" />
            长期名次分段
          </CardTitle>
          <CardDescription>
            重点关注段位与名次段计算的长期口径（全局生效，对话助手同口径）；
            学校默认三段为高分 1–80、临界 400–500、薄弱 501+。
          </CardDescription>
        </div>
        <div className="flex items-center gap-2 print:hidden">
          {!editing ? (
            <Button type="button" variant="outline" size="sm" onClick={startEdit} disabled={loading}>
              <Pencil className="h-4 w-4" /> 修改
            </Button>
          ) : null}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={restoreDefaults}
            disabled={busy || loading || isDefault}
            title={isDefault ? '当前已是学校默认三段' : '恢复学校默认三段'}
          >
            <RotateCcw className="h-4 w-4" /> 恢复默认
          </Button>
          <Button type="button" variant="ghost" size="icon" aria-label="刷新名次分段" onClick={reload} disabled={busy}>
            <RefreshCw className={`h-4 w-4${loading ? ' animate-spin' : ''}`} />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading ? (
          <Skeleton className="h-24 w-full" />
        ) : loadError ? (
          <div className="flex flex-col items-center gap-3 py-6 text-center">
            <AlertCircle className="h-8 w-8 text-amber-400" />
            <p className="text-sm text-slate-600">{loadError}</p>
            <Button variant="outline" size="sm" onClick={reload}>
              重试
            </Button>
          </div>
        ) : (
          <>
            {notice ? (
              <div
                role="status"
                className="rounded-lg border border-success-300 bg-success-50 px-3 py-2 text-sm text-success-600"
              >
                {notice}
              </div>
            ) : null}

            {editing && config ? (
              <div className="rounded-lg border border-brand-200 bg-brand-50/40 px-3 py-3">
                <p className="text-xs font-medium text-slate-500">修改长期名次分段（名次口径：年级名次）</p>
                <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  {BAND_FIELDS.map((field) => (
                    <div key={field.key} className="space-y-1">
                      <label className="text-xs text-slate-500" htmlFor={`band-${field.key}`}>
                        {field.label}
                      </label>
                      <Input
                        id={`band-${field.key}`}
                        type="number"
                        min={1}
                        className="h-9"
                        value={form[field.key]}
                        aria-label={field.label}
                        onChange={(e) => setForm({ ...form, [field.key]: e.target.value })}
                      />
                      <p className="text-[10px] text-slate-400">{field.hint}</p>
                    </div>
                  ))}
                </div>
                <div className="mt-3 flex gap-2">
                  <Button type="button" size="sm" onClick={() => void saveEdit()} disabled={busy}>
                    保存
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setEditing(false)
                      setActionError(null)
                    }}
                    disabled={busy}
                  >
                    取消
                  </Button>
                </div>
              </div>
            ) : null}

            {!editing && config ? (
              <ul className="space-y-2">
                {bandRows(config).map((row) => (
                  <li
                    key={row}
                    className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm"
                  >
                    <span className="font-medium text-slate-900">{row.split('：')[0]}</span>
                    <span className="tabular-nums text-slate-600">{row.split('：')[1]}</span>
                    <span className="flex-1" />
                  </li>
                ))}
              </ul>
            ) : null}

            {!editing ? (
              <div className="flex items-center gap-2">
                <Badge variant={isDefault ? 'secondary' : 'outline'}>
                  {isDefault ? '学校默认三段' : '已自定义'}
                </Badge>
                {!isDefault ? (
                  <span className="text-xs text-slate-400 tabular-nums">
                    默认值：{bandRows(defaults).join('；')}
                  </span>
                ) : null}
              </div>
            ) : null}

            {actionError ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                <p className="flex items-start gap-2">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  {actionError}
                </p>
              </div>
            ) : null}

            <div className="rounded-lg border border-dashed border-slate-200 px-3 py-3">
              <p className="flex items-start gap-2 text-xs text-slate-500">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand-500" aria-hidden="true" />
                成绩分析里的「排名区间筛选」等临时查询可任填区间，仅作用于当次查询，
                不会写入此长期配置；诊断阈值（偏科、进退步等）另有独立口径，不在本卡修改。
              </p>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
