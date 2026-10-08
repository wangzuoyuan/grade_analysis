'use client'

import {useCallback, useEffect, useState} from 'react'
import {AlertCircle, Pencil, RefreshCw, RotateCcw, TrendingUp} from 'lucide-react'
import {Badge} from '@/components/ui/badge'
import {Button} from '@/components/ui/button'
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from '@/components/ui/card'
import {Input} from '@/components/ui/input'
import {Skeleton} from '@/components/ui/skeleton'

type Values = {direction_rank_change: number; streak_rank_change: number}
type Config = Values & {defaults: Values; is_default: boolean}
const ENDPOINT = '/api/diagnosis-threshold-config'

async function errorText(res: Response): Promise<string> {
  const body = await res.json().catch(() => null) as {detail?: string} | null
  return body?.detail || `请求失败（HTTP ${res.status}）`
}

export function DiagnosisThresholdSettingsCard() {
  const [config, setConfig] = useState<Config | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({direction_rank_change: '', streak_rank_change: ''})
  const [nonce, setNonce] = useState(0)
  const reload = useCallback(() => setNonce(n => n + 1), [])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    fetch(ENDPOINT, {signal: controller.signal, headers: {Accept: 'application/json'}})
      .then(async res => {
        if (!res.ok) throw new Error(await errorText(res))
        return res.json() as Promise<Config>
      })
      .then(value => {if (!controller.signal.aborted) setConfig(value)})
      .catch(reason => {if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '读取阈值失败')})
      .finally(() => {if (!controller.signal.aborted) setLoading(false)})
    return () => controller.abort()
  }, [nonce])

  function startEdit() {
    if (!config) return
    setForm({direction_rank_change: String(config.direction_rank_change), streak_rank_change: String(config.streak_rank_change)})
    setError(null); setNotice(null); setEditing(true)
  }

  async function save() {
    const direction = Number(form.direction_rank_change)
    const streak = Number(form.streak_rank_change)
    if (!Number.isInteger(direction) || !Number.isInteger(streak) || direction < 1 || streak < 1 || direction > 1000 || streak > 1000) {
      setError('进退步阈值须为 1–1000 的整数')
      return
    }
    setBusy(true); setError(null)
    try {
      const res = await fetch(ENDPOINT, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({direction_rank_change: direction, streak_rank_change: streak})})
      if (!res.ok) throw new Error(await errorText(res))
      setConfig(await res.json() as Config)
      setEditing(false); setNotice('诊断阈值已保存，刷新诊断与关注回看页面即可查看新口径')
    } catch (reason) {setError(reason instanceof Error ? reason.message : '保存失败')}
    finally {setBusy(false)}
  }

  async function restore() {
    if (!window.confirm('恢复进退步默认阈值（净变化 80 名、连续每次 50 名）？')) return
    setBusy(true); setError(null)
    try {
      const res = await fetch(ENDPOINT, {method: 'DELETE'})
      if (!res.ok) throw new Error(await errorText(res))
      setConfig(await res.json() as Config)
      setEditing(false); setNotice('已恢复进退步默认阈值')
    } catch (reason) {setError(reason instanceof Error ? reason.message : '恢复失败')}
    finally {setBusy(false)}
  }

  return <Card>
    <CardHeader className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <CardTitle className="flex items-center gap-2"><TrendingUp className="h-4 w-4 text-brand-500"/>进退步诊断阈值</CardTitle>
        <CardDescription>全局生效于主三门名次趋势、学生类型、关注回看与教研统计。按每年级约 620 人，默认 80 名约为 13%、50 名约为 8%；名次数值变小为进步，实际变化始终完整显示。</CardDescription>
      </div>
      <div className="flex items-center gap-2 print:hidden">
        {!editing && <Button type="button" variant="outline" size="sm" onClick={startEdit} disabled={loading || busy || !config}><Pencil className="h-4 w-4"/>修改</Button>}
        <Button type="button" variant="outline" size="sm" onClick={() => void restore()} disabled={loading || busy || !config || config.is_default}><RotateCcw className="h-4 w-4"/>恢复默认</Button>
        <Button type="button" variant="ghost" size="icon" aria-label="刷新诊断阈值" onClick={reload} disabled={busy}><RefreshCw className={`h-4 w-4${loading ? ' animate-spin' : ''}`}/></Button>
      </div>
    </CardHeader>
    <CardContent className="space-y-4">
      {loading ? <Skeleton className="h-24 w-full"/> : config ? <>
        {editing ? <div className="rounded-lg border border-brand-200 bg-brand-50/40 px-3 py-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-xs text-slate-600">近三次净变化／回看明显变化（名）
              <Input type="number" min={1} max={1000} value={form.direction_rank_change} onChange={e => setForm({...form, direction_rank_change: e.target.value})}/>
            </label>
            <label className="space-y-1 text-xs text-slate-600">连续进退步每次至少变化（名）
              <Input type="number" min={1} max={1000} value={form.streak_rank_change} onChange={e => setForm({...form, streak_rank_change: e.target.value})}/>
            </label>
          </div>
          <p className="mt-2 text-xs text-slate-500">“持续”需连续至少两次，每次都达到右侧阈值；前后两场回看使用左侧阈值。</p>
          <div className="mt-3 flex gap-2"><Button type="button" size="sm" onClick={() => void save()} disabled={busy}>保存</Button><Button type="button" variant="outline" size="sm" onClick={() => {setEditing(false); setError(null)}} disabled={busy}>取消</Button></div>
        </div> : <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm">近三次净变化／回看：<strong className="tabular-nums">{config.direction_rank_change} 名</strong></div>
          <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm">连续每次：<strong className="tabular-nums">{config.streak_rank_change} 名</strong></div>
        </div>}
        <div className="flex flex-wrap items-center gap-2"><Badge variant={config.is_default ? 'secondary' : 'outline'}>{config.is_default ? '系统默认' : '已自定义'}</Badge><span className="text-xs text-slate-500">默认：净变化 {config.defaults.direction_rank_change} 名，连续每次 {config.defaults.streak_rank_change} 名</span></div>
      </> : null}
      {error && <p role="alert" className="flex items-center gap-2 text-sm text-amber-800"><AlertCircle className="h-4 w-4"/>{error} {!config && <Button variant="outline" size="sm" onClick={reload}>重试</Button>}</p>}
      {notice && <p role="status" className="text-sm text-success-600">{notice}</p>}
    </CardContent>
  </Card>
}
