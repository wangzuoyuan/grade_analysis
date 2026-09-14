'use client'

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { ChevronLeft, Pencil, Trash2 } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { useClassScope } from '@/lib/class-scope'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

interface ManageRecord {
  id: number
  name: string
  date: string
  subject: string
  content: string
  remark: string
  submission_status?: string
  evaluation?: string
  class_labels?: string[]
  is_special: boolean
}

export default function HomeworkManagePage() {
  const { current } = useClassScope()
  const [records, setRecords] = useState<ManageRecord[]>([])
  const [student, setStudent] = useState('')
  const [date, setDate] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [subject, setSubject] = useState('')
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState<ManageRecord | null>(null)
  // 先把 URL 上的 date/start_date/end_date/student/subject 读进来再触发加载，
  // 避免「无筛选请求」与「带筛选请求」竞争、前者后到把结果覆盖成全量。
  const [ready, setReady] = useState(false)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    setDate(params.get('date') || '')
    setStartDate(params.get('start_date') || '')
    setEndDate(params.get('end_date') || '')
    setStudent(params.get('student') || '')
    setSubject(params.get('subject') || '')
    setReady(true)
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    const params = new URLSearchParams()
    if (student) params.set('student', student)
    // 后端口径：start_date + end_date 同时给出时按区间筛，否则退回单日 date
    if (startDate && endDate) {
      params.set('start_date', startDate)
      params.set('end_date', endDate)
    } else if (date) {
      params.set('date', date)
    }
    if (subject) params.set('subject', subject)
    if (current !== 'all') params.set('teaching_class_id', String(current))
    try {
      const data = await fetch(`/api/homework/manage/records?${params}`).then((r) => r.json())
      setRecords(data)
    } finally {
      setLoading(false)
    }
  }, [student, date, startDate, endDate, subject, current])

  useEffect(() => {
    if (ready) load().catch(() => {})
  }, [ready, load])

  async function remove(rec: ManageRecord) {
    if (!confirm(`删除 ${rec.name} 的这条记录？`)) return
    const url = rec.is_special
      ? `/api/homework/special-records/${rec.id}`
      : `/api/homework/manage/records/${rec.id}`
    await fetch(url, { method: 'DELETE' })
    await load()
  }

  async function saveEdit() {
    if (!editing || editing.is_special) return
    await fetch(`/api/homework/manage/records/${editing.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        subject: editing.subject,
        content: editing.content,
        remark: editing.remark,
        submission_status: editing.submission_status || '缺交',
        evaluation: editing.evaluation || '',
      }),
    })
    setEditing(null)
    await load()
  }

  return (
    <div className="space-y-6">
      <Link
        href="/homework"
        className="inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900"
      >
        <ChevronLeft className="h-4 w-4" />
        返回作业看板
      </Link>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">记录管理</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={student}
              onChange={(e) => setStudent(e.target.value)}
              placeholder="按姓名筛选"
              className="rounded-md border border-slate-200 px-2 py-1 text-sm"
            />
            <input
              type="date"
              value={date}
              onChange={(e) => {
                setDate(e.target.value)
                setStartDate('')
                setEndDate('')
              }}
              className="rounded-md border border-slate-200 px-2 py-1 text-sm"
            />
            {startDate && endDate && (
              <Badge className="border-transparent bg-brand-50 text-brand-700">
                日期区间：{startDate} ~ {endDate}
              </Badge>
            )}
            {subject && (
              <Badge className="border-transparent bg-brand-50 text-brand-700">
                作业种类：{subject}
              </Badge>
            )}
            <Button variant="outline" size="sm" onClick={() => load()}>
              查询
            </Button>
            {(student || date || startDate || endDate || subject) && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setStudent('')
                  setDate('')
                  setStartDate('')
                  setEndDate('')
                  setSubject('')
                }}
              >
                清空
              </Button>
            )}
          </div>

          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>日期</TableHead>
                  <TableHead>姓名</TableHead>
                  <TableHead>班级</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>作业种类 / 情况</TableHead>
                  <TableHead>说明</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {records.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} className="py-10 text-center text-sm text-slate-400">
                      {loading ? '加载中…' : '暂无记录'}
                    </TableCell>
                  </TableRow>
                ) : (
                  records.map((rec) => (
                    <TableRow key={`${rec.is_special ? 's' : 'r'}-${rec.id}`} className="hover:bg-slate-50">
                      <TableCell className="text-slate-500">{rec.date}</TableCell>
                      <TableCell className="font-medium">{rec.name}</TableCell>
                      <TableCell>
                        {rec.class_labels?.map((label) => <Badge key={label} variant="outline" className="mr-1">{label}</Badge>)}
                      </TableCell>
                      <TableCell>
                        {rec.is_special ? (
                          <Badge className="border-transparent bg-warning-50 text-warning-700">特殊</Badge>
                        ) : rec.submission_status === '已交' ? (
                          <Badge className="border-transparent bg-success-50 text-success-700">已交</Badge>
                        ) : (
                          <Badge className="border-transparent bg-slate-100 text-slate-600">缺交</Badge>
                        )}
                      </TableCell>
                      <TableCell>{rec.is_special ? rec.remark : rec.subject}</TableCell>
                      <TableCell className="text-slate-500">{rec.evaluation || rec.content || rec.remark || '—'}</TableCell>
                      <TableCell className="text-right">
                        {!rec.is_special && (
                          <button onClick={() => setEditing({ ...rec })} className="mr-3 text-slate-400 hover:text-brand-600" aria-label="编辑">
                            <Pencil className="h-4 w-4" />
                          </button>
                        )}
                        <button
                          onClick={() => remove(rec)}
                          className="text-slate-400 hover:text-danger-500"
                          aria-label="删除"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      <Dialog open={!!editing} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>编辑作业记录</DialogTitle></DialogHeader>
          {editing && (
            <div className="space-y-3">
              <div className="text-sm text-slate-500">{editing.date} · {editing.name}</div>
              <label className="block text-sm">提交状态
                <select value={editing.submission_status || '缺交'} onChange={(e) => setEditing({ ...editing, submission_status: e.target.value })}
                  className="mt-1 w-full rounded-md border border-slate-200 px-2 py-2">
                  <option value="缺交">缺交</option><option value="已交">已交</option>
                </select>
              </label>
              <label className="block text-sm">作业种类
                <input value={editing.subject} onChange={(e) => setEditing({ ...editing, subject: e.target.value })}
                  className="mt-1 w-full rounded-md border border-slate-200 px-2 py-2" />
              </label>
              <label className="block text-sm">评价
                <input value={editing.evaluation || ''} onChange={(e) => setEditing({ ...editing, evaluation: e.target.value })}
                  placeholder="优秀、认真、马虎……" className="mt-1 w-full rounded-md border border-slate-200 px-2 py-2" />
              </label>
              <label className="block text-sm">说明
                <textarea value={editing.content} onChange={(e) => setEditing({ ...editing, content: e.target.value })}
                  rows={3} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-2" />
              </label>
              <label className="block text-sm">特殊备注
                <input value={editing.remark} onChange={(e) => setEditing({ ...editing, remark: e.target.value })}
                  className="mt-1 w-full rounded-md border border-slate-200 px-2 py-2" />
              </label>
              <Button onClick={saveEdit} className="w-full">保存修改</Button>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
