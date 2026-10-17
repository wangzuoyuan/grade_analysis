'use client'
import { useEffect, useRef, useState } from 'react'
import { previewMetricFill, confirmMetricFill, type MetricFillPreview, type WorkspaceMode } from '@/lib/api-v1'
import { useWorkspace } from '@/lib/workspace'
import { apiErrorMessage } from '@/components/link/error-text'
import { Button } from '@/components/ui/button'

export function MetricFill({ factId, mode }: {factId: number; mode: WorkspaceMode}) {
  const { filter, refreshScope } = useWorkspace()
  const [preview, setPreview] = useState<MetricFillPreview | null>(null)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const active = useRef(true)
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])
  async function run(confirm = false) {
    setBusy(true); setError('')
    try {
      if (confirm && preview) {
        await confirmMetricFill(mode, {token: preview.token, confirm_name: preview.exam_name})
        if (active.current) refreshScope()
      } else {
        const p = await previewMetricFill(mode, {academic_year_id: filter.academic_year_id,
          class_id: mode === 'homeroom' ? filter.class_id : undefined, fact_id: factId})
        if (active.current) setPreview(p)
      }
    } catch (e) { if (active.current) setError(apiErrorMessage(e)) }
    finally { if (active.current) setBusy(false) }
  }
  return <div className="my-2 space-y-2">
    <Button variant="outline" size="sm" disabled={busy} onClick={() => run()}>预览补齐缺失指标</Button>
    {preview && <div className="rounded border border-sky-200 p-2"><p>从{preview.source_domain === 'homeroom' ? '班主任' : '教学'}来源补齐：{Object.entries(preview.changes).map(([f, v]) => f === 'grade_score' ? `等级分 ${v}` : `年级百分位 ${(v <= 1 ? v * 100 : v).toFixed(1)}%`).join('、')}</p><p>原始分相同，只补本域空白指标并保留来源记录。</p><Button className="mt-2" size="sm" disabled={busy} onClick={() => run(true)}>确认补齐指标</Button></div>}
    {error && <p role="alert" className="text-rose-700">{error}</p>}
  </div>
}
