'use client'

import { useCallback, useEffect, useState } from 'react'
import { MessageSquarePlus, Trash2 } from 'lucide-react'

import {
  createNote,
  deleteNote,
  listStudentNotes,
  patchNote,
  type NoteScopeQuery,
  type WorkspaceMode,
} from '@/lib/api-v1'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface Note {
  id: number
  date: string
  category: string
  content: string
  follow_up: string | null
  follow_up_done: number | boolean | null
}

const CATEGORIES = ['谈话', '观察', '家访', '家长沟通', '奖惩', '其他']

const CATEGORY_STYLE: Record<string, string> = {
  谈话: 'bg-brand-50 text-brand-700',
  观察: 'bg-slate-100 text-slate-600',
  家访: 'bg-success-50 text-success-600',
  家长沟通: 'bg-warning-50 text-warning-700',
  奖惩: 'bg-danger-50 text-danger-600',
  其他: 'bg-slate-100 text-slate-500',
}

function todayStr() {
  return new Date().toISOString().slice(0, 10)
}

/** 学生档案（P8-UXFIX：改接 /api/v1/{mode}/students/{person_id}/notes，域内按 person 存取）。 */
export default function StudentNotes({
  mode,
  personId,
  scopeQ = {},
}: {
  mode: WorkspaceMode
  personId: number
  scopeQ?: NoteScopeQuery
}) {
  const [notes, setNotes] = useState<Note[]>([])
  const [showForm, setShowForm] = useState(false)

  const [date, setDate] = useState(todayStr())
  const [category, setCategory] = useState('谈话')
  const [content, setContent] = useState('')
  const [followUp, setFollowUp] = useState('')
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    const data = await listStudentNotes(mode, personId, scopeQ)
    setNotes((data.notes ?? []) as Note[])
  }, [mode, personId, scopeQ])

  useEffect(() => {
    load().catch(() => setNotes([]))
  }, [load])

  async function save() {
    if (!content.trim()) return
    setSaving(true)
    try {
      await createNote(
        mode,
        personId,
        { date, category, content: content.trim(), follow_up: followUp || null },
        scopeQ,
      )
      setContent('')
      setFollowUp('')
      setShowForm(false)
      await load()
    } finally {
      setSaving(false)
    }
  }

  async function toggleFollowUp(note: Note) {
    const done = note.follow_up_done === 1 || note.follow_up_done === true
    await patchNote(mode, note.id, { follow_up_done: done ? 0 : 1 }, scopeQ)
    await load()
  }

  async function remove(note: Note) {
    if (!confirm('删除这条档案记录？')) return
    await deleteNote(mode, note.id, scopeQ)
    await load()
  }

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="text-base flex items-center gap-2">
          <MessageSquarePlus className="h-4 w-4" />
          成长 / 谈话档案
        </CardTitle>
        <Button variant="outline" size="sm" onClick={() => setShowForm((v) => !v)}>
          {showForm ? '取消' : '新增记录'}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {showForm && (
          <div className="space-y-2 rounded-md border border-slate-200 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <input
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                className="rounded-md border border-slate-200 px-2 py-1 text-sm"
              />
              <div className="flex flex-wrap gap-1">
                {CATEGORIES.map((c) => (
                  <button
                    key={c}
                    onClick={() => setCategory(c)}
                    className={cn(
                      'rounded px-2.5 py-1 text-xs',
                      category === c ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600'
                    )}
                  >
                    {c}
                  </button>
                ))}
              </div>
            </div>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={3}
              placeholder="记录谈话内容 / 观察 / 家访情况…"
              className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-brand-500"
            />
            <input
              value={followUp}
              onChange={(e) => setFollowUp(e.target.value)}
              placeholder="跟进事项（可选，例如：一周后再谈一次）"
              className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm"
            />
            <div className="flex justify-end">
              <Button onClick={save} disabled={saving || !content.trim()} size="sm">
                {saving ? '保存中…' : '保存'}
              </Button>
            </div>
          </div>
        )}

        {notes.length === 0 ? (
          <p className="py-6 text-center text-sm text-slate-400">暂无档案记录</p>
        ) : (
          <div className="space-y-3">
            {notes.map((n) => (
              <div key={n.id} className="rounded-md border border-slate-100 p-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Badge className={cn('border-transparent', CATEGORY_STYLE[n.category] || CATEGORY_STYLE['其他'])}>
                      {n.category}
                    </Badge>
                    <span className="text-xs text-slate-400">{n.date}</span>
                  </div>
                  <button
                    onClick={() => remove(n)}
                    className="text-slate-300 hover:text-danger-500"
                    aria-label="删除"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
                <p className="mt-2 whitespace-pre-wrap text-sm text-slate-700">{n.content}</p>
                {n.follow_up && (
                  <label className="mt-2 flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={n.follow_up_done === 1 || n.follow_up_done === true}
                      onChange={() => toggleFollowUp(n)}
                      className="h-3.5 w-3.5"
                    />
                    <span className={cn(n.follow_up_done ? 'text-slate-400 line-through' : 'text-warning-700')}>
                      跟进：{n.follow_up}
                    </span>
                  </label>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
