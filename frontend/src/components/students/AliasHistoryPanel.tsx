'use client'

/**
 * 别名（学号）历史展开面板（契约 docs/contracts/p4-students.md §2.1，S08 换号接续）。
 *
 * GET /homeroom/students/{person_id}/aliases 列历史；POST .../alias {alias, valid_from}
 * 追加新学号：旧 alias 保留（valid_to=前一日），身份不变；同号新号已属他人 → 422 列冲突人。
 * 纪律：不提供删除/合并入口（契约：P4 不提供，档案留存优先）。
 * 追加表单未提交内容经 page-draft 暂存，提交成功清除。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle } from 'lucide-react'

import {
  addStudentAlias,
  listStudentAliases,
  readAliasConflicts,
  type AliasConflictEntry,
  type PersonId,
  type StudentAliasEntry,
} from '@/lib/api-v1'
import { apiErrorMessage } from '@/components/link/error-text'
import { clearPageDraft, loadPageDraft, savePageDraft } from '@/lib/page-draft'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

const DRAFT_ROUTE = '/homeroom/students'

interface AliasDraft {
  alias: string
  validFrom: string
}

function isAliasDraft(v: unknown): v is AliasDraft {
  if (typeof v !== 'object' || v === null) return false
  const o = v as Record<string, unknown>
  return typeof o.alias === 'string' && typeof o.validFrom === 'string'
}

function dateLabel(v: string | null): string {
  return v ? v.slice(0, 10) : '至今'
}

export function AliasHistoryPanel({ personId, name }: { personId: PersonId; name: string | null }) {
  const [aliases, setAliases] = useState<StudentAliasEntry[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [alias, setAlias] = useState('')
  const [validFrom, setValidFrom] = useState('')
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [conflicts, setConflicts] = useState<AliasConflictEntry[] | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const reqRef = useRef(0)
  const draftKey = `alias:${String(personId)}`

  const load = useCallback(() => {
    const req = ++reqRef.current
    setAliases(null)
    setLoadError(null)
    listStudentAliases(personId)
      .then((r) => {
        if (req !== reqRef.current) return
        setAliases(r.aliases ?? [])
      })
      .catch((err: unknown) => {
        if (req !== reqRef.current) return
        setAliases([])
        setLoadError(apiErrorMessage(err))
      })
  }, [personId])

  useEffect(() => {
    load()
    // 切换展开对象时按人回填草稿，避免把 A 的输入带进 B 的表单
    const d = loadPageDraft<AliasDraft>(DRAFT_ROUTE, draftKey, isAliasDraft)
    setAlias(d?.alias ?? '')
    setValidFrom(d?.validFrom ?? '')
    setFormError(null)
    setConflicts(null)
    setNotice(null)
    return () => {
      reqRef.current += 1
    }
  }, [load, draftKey])

  useEffect(() => {
    if (alias === '' && validFrom === '') return
    savePageDraft<AliasDraft>(DRAFT_ROUTE, draftKey, { alias, validFrom })
  }, [draftKey, alias, validFrom])

  async function submit() {
    if (alias.trim() === '') {
      setFormError('请填写新学号')
      return
    }
    if (validFrom.trim() === '') {
      setFormError('请选择新学号的生效日期')
      return
    }
    setBusy(true)
    setFormError(null)
    setConflicts(null)
    setNotice(null)
    try {
      await addStudentAlias(personId, { alias: alias.trim(), valid_from: validFrom.trim() })
      clearPageDraft(DRAFT_ROUTE, draftKey)
      setAlias('')
      setValidFrom('')
      setNotice('已追加新学号，旧学号保留（历史接续到同一人）')
      load()
    } catch (err) {
      setFormError(apiErrorMessage(err))
      setConflicts(readAliasConflicts(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50/60 p-3">
      <p className="text-xs font-semibold text-slate-500">
        学号（别名）历史 · {name ?? '（未命名）'}
        <span className="ml-2 font-normal text-slate-400">换号接续到同一人，历史档案不中断</span>
      </p>

      {loadError ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p role="alert" className="text-sm text-danger-500">
            {loadError}
          </p>
          <Button type="button" variant="outline" size="sm" onClick={load}>
            重试
          </Button>
        </div>
      ) : aliases == null ? (
        <Skeleton className="h-16 w-full" />
      ) : aliases.length === 0 ? (
        <p className="text-xs text-slate-400">暂无学号记录</p>
      ) : (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs">学号</TableHead>
                <TableHead className="text-xs">生效自</TableHead>
                <TableHead className="text-xs">失效至</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {aliases.map((a) => (
                <TableRow key={String(a.id)}>
                  <TableCell className="whitespace-nowrap font-mono text-xs text-slate-800">
                    {a.alias_value}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-slate-500">
                    {dateLabel(a.valid_from)}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-slate-500">
                    {dateLabel(a.valid_to)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {/* 追加新学号：生效日期决定旧学号的收尾日（valid_to=前一日，由后端写入） */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <div className="space-y-1">
          <label htmlFor={`alias-value-${String(personId)}`} className="text-xs font-medium text-slate-500">
            追加新学号
          </label>
          <Input
            id={`alias-value-${String(personId)}`}
            value={alias}
            onChange={(e) => setAlias(e.target.value)}
            placeholder="如 2026305"
            className="h-9 w-40 font-mono"
            maxLength={40}
            disabled={busy}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor={`alias-from-${String(personId)}`} className="text-xs font-medium text-slate-500">
            生效日期
          </label>
          <Input
            id={`alias-from-${String(personId)}`}
            type="date"
            value={validFrom}
            onChange={(e) => setValidFrom(e.target.value)}
            className="h-9 w-44"
            disabled={busy}
          />
        </div>
        <Button type="button" size="sm" onClick={() => void submit()} disabled={busy}>
          {busy ? '提交中…' : '追加学号'}
        </Button>
      </div>

      {notice ? <p role="status" className="text-xs text-success-600">{notice}</p> : null}
      {formError ? (
        <p role="alert" className="flex items-start gap-1.5 text-sm text-danger-500">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          {formError}
        </p>
      ) : null}
      {conflicts ? (
        <div className="rounded-md border border-warning-300 bg-warning-50 p-2 text-xs text-warning-700">
          <p className="font-medium">新学号已属于以下学生，未追加（同届不自动合并）：</p>
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
  )
}
