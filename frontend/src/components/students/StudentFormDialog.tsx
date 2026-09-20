'use client'

/**
 * 新建学生弹窗（契约 docs/contracts/p4-students.md §2.1）。
 *
 * POST /api/v1/homeroom/students {name, alias?}：后端单事务新建身份 +
 * 本学年别名 + 在班 Enrollment。alias 同学年同域已属他人 → 422（列出冲突人），
 * 前端只展示冲突明细，绝不自动改名/合并（契约：P4 不提供删除/合并）。
 * 未提交内容经 lib/page-draft 落 sessionStorage，提交成功后清除（简化版 link-draft）。
 */

import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'

import { createHomeroomStudent, readAliasConflicts, type AliasConflictEntry } from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { clearPageDraft, loadPageDraft, savePageDraft } from '@/lib/page-draft'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'

const DRAFT_ROUTE = '/homeroom/students'
const DRAFT_KEY = 'new-student'

/** 未提交草稿的形状（loadPageDraft 按此收窄，损坏/不符一律当作无草稿）。 */
interface NewStudentDraft {
  name: string
  alias: string
}

function isNewStudentDraft(v: unknown): v is NewStudentDraft {
  if (typeof v !== 'object' || v === null) return false
  const o = v as Record<string, unknown>
  return typeof o.name === 'string' && typeof o.alias === 'string'
}

export function StudentFormDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  onCreated: () => void
}) {
  const [name, setName] = useState('')
  const [alias, setAlias] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [conflicts, setConflicts] = useState<AliasConflictEntry[] | null>(null)

  // 打开时恢复草稿；关闭由父组件控制，这里只负责回填
  useEffect(() => {
    if (!open) return
    const d = loadPageDraft<NewStudentDraft>(DRAFT_ROUTE, DRAFT_KEY, isNewStudentDraft)
    setName(d?.name ?? '')
    setAlias(d?.alias ?? '')
    setError(null)
    setConflicts(null)
  }, [open])

  // 有内容即暂存草稿（S07 同思路：中途切走不丢）
  useEffect(() => {
    if (!open) return
    if (name === '' && alias === '') return
    savePageDraft<NewStudentDraft>(DRAFT_ROUTE, DRAFT_KEY, { name, alias })
  }, [open, name, alias])

  function reset() {
    setName('')
    setAlias('')
    setError(null)
    setConflicts(null)
    clearPageDraft(DRAFT_ROUTE, DRAFT_KEY)
  }

  async function submit() {
    if (name.trim() === '') {
      setError('请填写姓名')
      return
    }
    setBusy(true)
    setError(null)
    setConflicts(null)
    try {
      await createHomeroomStudent({
        name: name.trim(),
        ...(alias.trim() !== '' ? { alias: alias.trim() } : {}),
      })
      reset()
      onCreated()
      onOpenChange(false)
    } catch (err) {
      setError(apiErrorMessage(err))
      setConflicts(readAliasConflicts(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v && !busy) onOpenChange(false)
      }}
    >
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>新建学生</DialogTitle>
          <DialogDescription>
            新建身份并加入本学年行政班名册；学号同年内全局唯一，撞号会被拒绝。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <label htmlFor="new-student-name" className="text-xs font-medium text-slate-500">
              姓名 *
            </label>
            <Input
              id="new-student-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={30}
              disabled={busy}
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="new-student-alias" className="text-xs font-medium text-slate-500">
              学号（可选）
            </label>
            <Input
              id="new-student-alias"
              value={alias}
              onChange={(e) => setAlias(e.target.value)}
              maxLength={40}
              disabled={busy}
            />
          </div>

          {error ? (
            <div role="alert" className="rounded-md border border-danger-300 bg-danger-50 p-2 text-sm text-danger-600">
              <span className="flex items-start gap-1.5">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                {error}
              </span>
            </div>
          ) : null}
          {conflicts ? (
            <div className="rounded-md border border-warning-300 bg-warning-50 p-2 text-xs text-warning-700">
              <p className="font-medium">该学号已属于以下学生（同届不自动合并）：</p>
              <ul className="mt-1 space-y-0.5">
                {conflicts.map((c, i) => (
                  <li key={i}>
                    {c.name ?? '—'}
                    {c.alias ? `（学号 ${c.alias}）` : ''}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={() => void submit()} disabled={busy || name.trim() === ''}>
            {busy ? '提交中…' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
