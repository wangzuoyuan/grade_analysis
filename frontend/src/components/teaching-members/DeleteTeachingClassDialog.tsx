'use client'

import { useEffect, useState } from 'react'
import { AlertCircle, Loader2 } from 'lucide-react'
import { confirmTeachingClassDelete, previewTeachingClassDelete,
  type ClassesCatalogTeaching, type TeachingClassDeletePreview } from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'

export function DeleteTeachingClassDialog({ item, onClose, onDeleted }: {
  item: ClassesCatalogTeaching
  onClose: () => void
  onDeleted: (classes: ClassesCatalogTeaching[], label: string) => void
}) {
  const [preview, setPreview] = useState<TeachingClassDeletePreview | null>(null)
  const [confirmName, setConfirmName] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  useEffect(() => {
    let cancelled = false
    setPreview(null); setConfirmName(''); setError('')
    previewTeachingClassDelete(item.class_id).then(data => {
      if (!cancelled) setPreview(data)
    }).catch((err: unknown) => { if (!cancelled) setError(apiErrorMessage(err)) })
    return () => { cancelled = true }
  }, [item.class_id, reload])

  async function remove() {
    if (!preview || busy || confirmName !== preview.impact.label) return
    setBusy(true); setError('')
    try {
      const result = await confirmTeachingClassDelete(item.class_id, { token: preview.token, confirm_name: confirmName })
      onDeleted(result.classes, result.impact.label)
    } catch (err) {
      setError(apiErrorMessage(err))
      setPreview(null)
    } finally { setBusy(false) }
  }

  const impact = preview?.impact
  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}>
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg" onEscapeKeyDown={e => { if (busy) e.preventDefault() }} onPointerDownOutside={e => { if (busy) e.preventDefault() }}>
      <DialogHeader>
        <DialogTitle>删除教学班「{impact?.label ?? item.label}」</DialogTitle>
        <DialogDescription>永久删除教学班及其专属数据，此操作无法在页面中撤销。有数据的班级也可以删除，请仔细核对。</DialogDescription>
      </DialogHeader>
      <div className="space-y-4 text-sm">
        {!preview && !error ? <p role="status" className="flex items-center gap-2 text-slate-500"><Loader2 className="h-4 w-4 animate-spin" />正在核对删除影响…</p> : null}
        {impact ? <>
          <p className="text-slate-600">{impact.academic_year_name} · {impact.subject} · {impact.label}</p>
          <div className="rounded-lg border border-danger-200 bg-danger-50 p-3 text-danger-700">
            <p className="flex items-center gap-2 font-medium"><AlertCircle className="h-4 w-4 shrink-0" />以下数据将一并删除</p>
            <ul className="mt-2 list-inside list-disc space-y-1">
              <li>成员关系 {impact.membership_count} 条（{impact.member_count} 人，含历史成员）</li>
              <li>教学成绩 {impact.score_count} 条</li>
              <li>作业批次 {impact.homework_count} 份、逐人记录 {impact.submission_count} 条</li>
              <li>作业统计排除设置 {impact.exclusion_count} 条</li>
              <li>班级关联 {impact.link_count} 条、学生配对 {impact.pair_count} 条</li>
            </ul>
          </div>
          <p className="text-xs leading-6 text-slate-600">学生身份、学号和个人档案保留；其他班级、其他学年和班主任工作台的数据保留。全年级考试及官方质量参照表保留，仅解除其中 {impact.reference_count} 处管理班引用。相关操作凭据 {impact.invalidated_operations} 个将失效（含相关换届撤销），相关聊天 {impact.closed_sessions} 个将关闭。</p>
          <label className="block space-y-2"><span>请输入完整班级名称 <strong className="break-all">{impact.label}</strong> 确认删除</span><Input aria-label="确认删除教学班名称" value={confirmName} onChange={e => setConfirmName(e.target.value)} disabled={busy} autoComplete="off" /></label>
        </> : null}
        {error ? <p role="alert" className="whitespace-pre-wrap text-danger-600">{error}</p> : null}
      </div>
      <DialogFooter>
        <Button variant="outline" disabled={busy} onClick={onClose}>取消</Button>
        {!preview && error ? <Button variant="outline" onClick={() => setReload(n => n + 1)}>重新核对</Button> : null}
        <Button variant="destructive" disabled={!preview || confirmName !== impact?.label || busy} onClick={() => void remove()}>{busy ? '正在删除…' : '永久删除'}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}
