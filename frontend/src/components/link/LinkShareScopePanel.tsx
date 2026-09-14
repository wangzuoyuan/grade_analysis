'use client'

/**
 * 共享范围面板（契约 §1.2.2，P2）。
 *
 * share_categories 复选 + share_history_from 日期（可清空）；收紧（移除成绩共享/
 * 撤回历史授权）保存前需确认，保存后即时生效。表单未提交修改经 lib/link-draft
 * 落 sessionStorage（S07 草稿不丢），并在 dirty 时挂 beforeunload 提示。
 */

import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2 } from 'lucide-react'
import { updateLinkShareScope, type LinkShareScope, type LinkSummary } from '@/lib/api-v1'
import {
  clearLinkDraftSection,
  loadLinkDraft,
  saveLinkDraftSection,
} from '@/lib/link-draft'

import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'

import { apiErrorMessage } from './error-text'

/** 契约 §1.2.2：share_categories 仅识别这三类。 */
const SHARE_CATEGORY_OPTIONS: Array<{ value: string; label: string; hint: string }> = [
  { value: 'roster', label: '名册', hint: '双方班级成员交集' },
  {
    value: 'current_subject_score',
    label: '任教学科成绩',
    hint: '不勾选时班主任侧不做任何跨域成绩投影',
  },
  { value: 'current_subject_homework', label: '任教学科作业', hint: '作业提交情况' },
]

/** 契约默认值：roster,current_subject_score。 */
const DEFAULT_SHARE_CATEGORIES: string[] = ['roster', 'current_subject_score']

function sameCategories(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false
  const sa = [...a].sort()
  const sb = [...b].sort()
  return sa.every((v, i) => v === sb[i])
}

interface LinkShareScopePanelProps {
  link: LinkSummary
  /** 保存成功后通知页面刷新关联摘要。 */
  onUpdated?: () => void
}

export function LinkShareScopePanel({ link, onUpdated }: LinkShareScopePanelProps) {
  const [categories, setCategories] = useState<string[]>(DEFAULT_SHARE_CATEGORIES)
  const [historyFrom, setHistoryFrom] = useState<string | null>(null)
  /** 最近一次保存回执：后端 listLinks 未回传共享字段时以其为准，避免刷新把表单弹回默认值。 */
  const [serverValues, setServerValues] = useState<LinkShareScope | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  // 切换 link 或后端摘要字段更新时重置表单；有未提交草稿（S07）则优先恢复草稿
  useEffect(() => {
    const draft = loadLinkDraft(link.id).shareScope
    const baseCategories = link.share_categories ?? DEFAULT_SHARE_CATEGORIES
    const baseHistory = link.share_history_from ?? null
    if (draft) {
      setCategories(draft.share_categories ?? baseCategories)
      setHistoryFrom(draft.share_history_from ?? null)
    } else {
      setCategories(baseCategories)
      setHistoryFrom(baseHistory)
    }
    setServerValues(null)
    setError(null)
    setNotice(null)
  }, [link.id, link.share_categories, link.share_history_from])

  const initialCategories = serverValues?.share_categories ?? link.share_categories ?? DEFAULT_SHARE_CATEGORIES
  const initialHistoryFrom = serverValues ? serverValues.share_history_from : (link.share_history_from ?? null)
  const dirty = !sameCategories(categories, initialCategories) || historyFrom !== initialHistoryFrom

  /** 表单变化即同步草稿：与初始值一致时清掉分节，避免“无草稿”误报。 */
  const persistForm = useCallback(
    (nextCategories: string[], nextHistory: string | null) => {
      const dirtyNow = !sameCategories(nextCategories, initialCategories) || nextHistory !== initialHistoryFrom
      if (dirtyNow) {
        saveLinkDraftSection(link.id, {
          shareScope: { share_categories: nextCategories, share_history_from: nextHistory },
        })
      } else {
        clearLinkDraftSection(link.id, 'shareScope')
      }
    },
    [link.id, initialCategories, initialHistoryFrom],
  )

  // dirty 时挂 beforeunload：关闭/刷新标签页前提示（应用内路由切换由草稿恢复兜底）
  useEffect(() => {
    if (!dirty) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])

  function updateCategories(value: string, checked: boolean) {
    const next = checked
      ? categories.includes(value)
        ? categories
        : [...categories, value]
      : categories.filter((c) => c !== value)
    setCategories(next)
    persistForm(next, historyFrom)
  }

  function updateHistoryFrom(v: string) {
    const next = v === '' ? null : v
    setHistoryFrom(next)
    persistForm(categories, next)
  }

  function discardChanges() {
    setCategories(initialCategories)
    setHistoryFrom(initialHistoryFrom)
    clearLinkDraftSection(link.id, 'shareScope')
    setError(null)
  }

  async function handleSave() {
    // 收紧判定：移除既有共享类别，或历史授权起点改晚/清空——契约规定收紧即时生效，先确认
    const tightening =
      initialCategories.some((c) => !categories.includes(c)) ||
      (initialHistoryFrom != null && (historyFrom == null || historyFrom > initialHistoryFrom))
    if (tightening && !window.confirm('取消成绩共享后立即生效，确定要保存收紧后的共享范围吗？')) {
      return
    }
    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      const r = await updateLinkShareScope(link.id, {
        share_categories: categories,
        share_history_from: historyFrom,
      })
      setServerValues(r)
      clearLinkDraftSection(link.id, 'shareScope')
      setNotice(`已保存共享范围（版本 v${String(r.version)}）`)
      onUpdated?.()
    } catch (err) {
      setError(apiErrorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const categoryLabels = SHARE_CATEGORY_OPTIONS.filter((o) => categories.includes(o.value)).map((o) => o.label)

  return (
    <Card>
      <CardHeader>
        <CardTitle>共享范围</CardTitle>
        <CardDescription>
          教学班 <code className="font-mono">#{String(link.teaching_class_id)}</code>（
          {link.subject || '—'}）：仅影响双方成员交集的任教学科数据；收紧立即生效。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {error ? (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-lg border border-danger-300 bg-danger-50 p-3 text-sm text-danger-600"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <p>{error}</p>
          </div>
        ) : null}

        {notice ? (
          <div
            role="status"
            className="flex items-center gap-2 rounded-lg border border-success-300 bg-success-50 p-3 text-sm text-success-600"
          >
            <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
            {notice}
          </div>
        ) : null}

        <fieldset className="space-y-2 print:hidden">
          <legend className="text-sm font-medium text-slate-700">共享类别</legend>
          {SHARE_CATEGORY_OPTIONS.map((opt) => (
            <label
              key={opt.value}
              className="flex cursor-pointer items-start gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 hover:bg-slate-50"
            >
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 shrink-0 accent-[#1f7fd6]"
                checked={categories.includes(opt.value)}
                onChange={(e) => updateCategories(opt.value, e.target.checked)}
              />
              <span>
                <span className="text-sm font-medium text-slate-900">{opt.label}</span>
                <span className="ml-1.5 text-xs text-slate-400">{opt.hint}</span>
              </span>
            </label>
          ))}
          {link.share_categories == null && serverValues == null ? (
            <p className="text-xs text-slate-400">
              后端摘要未返回当前共享类别，先按契约默认（名册、任教学科成绩）展示；保存后以回执为准。
            </p>
          ) : null}
        </fieldset>

        <div className="space-y-1.5 print:hidden">
          <label
            htmlFor={`share-history-from-${String(link.id)}`}
            className="text-sm font-medium text-slate-700"
          >
            历史授权起点（可选）
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              id={`share-history-from-${String(link.id)}`}
              type="date"
              className="w-44"
              value={historyFrom ?? ''}
              onChange={(e) => updateHistoryFrom(e.target.value)}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => updateHistoryFrom('')}
              disabled={historyFrom == null}
            >
              清空
            </Button>
          </div>
          <p className="text-xs text-slate-400">
            可早于关联生效日（用于重新开放生效前的历史）；留空 = 不开放关联生效前历史；考试日期未知的成绩不共享。
          </p>
        </div>

        {/* 打印时仅保留当前生效值摘要（表单控件不参与打印） */}
        <div className="hidden text-sm text-slate-700 print:block">
          共享类别：{categoryLabels.length > 0 ? categoryLabels.join('、') : '（无）'}
          ；历史授权起点：{historyFrom ?? '未开放生效前历史'}。
        </div>

        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between print:hidden">
          <p className="text-xs text-slate-500">
            {dirty ? (
              <span className="text-warning-600">有未保存修改：离开页面会保留草稿，可返回继续。</span>
            ) : (
              '表单与当前生效值一致。'
            )}
          </p>
          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={discardChanges} disabled={!dirty || saving}>
              放弃修改
            </Button>
            <Button type="button" onClick={() => void handleSave()} disabled={!dirty || saving}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
              {saving ? '保存中…' : '保存共享范围'}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
