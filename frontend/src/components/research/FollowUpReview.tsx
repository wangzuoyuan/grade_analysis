'use client'
import {useEffect, useRef, useState} from 'react'
import Link from 'next/link'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '@/components/ui/table'
import {FOLLOW_UPS_ENDPOINT, researchGet, formatValue, formatChange, profileHref, type FollowUps, type ScopeQuery, type FollowUpRecord} from './focus-api'

const pendingLabels: Record<string, string> = {
  no_target_metric: '未设定复查目标，请在学生档案中补充。',
  no_baseline: '没有可用基线，请在学生档案中核对或补充。',
  baseline_incomparable: '基线数值或单位与目标不一致，请核对后再复查。',
  no_comparable_exam: '开始跟进之后尚无可比考试，或相关学科缺考／缺指标。',
  not_due_yet: '尚未到复查日，也没有新的可比考试。',
  transferred_out: '已离班，保留跟进记录供查阅。',
  unsupported_metric: '原目标不适用于当前学段，请在学生档案中核对。',
}
function ReviewValues({record}: {record: FollowUpRecord}) {
  const c = record.contrast
  if (c.status !== 'ready') return <div><span className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-600">暂不可评价</span><p className="mt-2 max-w-sm text-xs leading-5 text-slate-500">{pendingLabels[c.reason ?? ''] ?? '证据不足，请查看学生档案中的复查详情。'}</p></div>
  const factor = c.unit === 'percentile' ? 100 : 1
  return <div className="space-y-2">
    <p className="font-medium">{formatValue(c.baseline!.value * factor, c.unit!)} → {formatValue(c.latest!.value * factor, c.unit!)}</p>
    <p className="text-xs text-slate-500">基线：{c.baseline!.exam_name ?? '教师手填'} · {c.baseline!.exam_date ?? '未填写日期'}</p>
    <p className="text-xs text-slate-500">新结果：{c.latest!.exam_name} · {c.latest!.exam_date}</p>
    <p className="text-xs font-medium text-brand-700">{formatChange(c.change!.value * factor, c.unit!)}</p>
  </div>
}
export function FollowUpReview({scopeQ}: {scopeQ: ScopeQuery}) {
  const [data, setData] = useState<FollowUps | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState('actionable')
  const [reload, setReload] = useState(0)
  const seq = useRef(0)
  useEffect(() => {
    const current = ++seq.current
    const abort = new AbortController()
    setData(null); setError(null)
    researchGet<FollowUps>(FOLLOW_UPS_ENDPOINT, scopeQ, abort.signal).then(result => {
      if (current === seq.current) setData(result)
    }).catch(reason => {
      if (!abort.signal.aborted && current === seq.current) setError(reason instanceof Error ? reason.message : '复查加载失败')
    })
    return () => {seq.current += 1; abort.abort()}
  }, [scopeQ.academic_year_id, scopeQ.class_id, reload])
  const records = data?.records.filter(record => filter === 'all' || (filter === 'actionable' ? record.record_status === 'open' && (record.due || record.contrast.status === 'ready') : record.record_status === 'open')) ?? []
  return <div className="space-y-4" data-testid="follow-up-review">
    <p className="text-sm leading-6 text-slate-500">每条跟进按自己的开始日期、目标指标与基线复查。记录已关闭与成绩是否变化分别展示。</p>
    {error ? <div role="alert" className="rounded-lg bg-amber-50 p-4 text-sm text-amber-800">{error}<button onClick={() => setReload(r => r + 1)} className="ml-3 underline">重试</button></div> : !data ? <p role="status" className="py-6 text-sm text-slate-500">正在读取跟进记录…</p> : <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[['全部记录',data.summary.total_n],['到期未关闭',data.summary.due_n],['未关闭且有可比结果',data.summary.ready_n],['未关闭且暂不可评价',data.summary.pending_n]].map(([label,value]) => <div key={String(label)} className="rounded-xl border border-slate-100 px-4 py-3"><p className="text-xs text-slate-500">{label}</p><p className="mt-1 text-2xl font-semibold">{value}<span className="ml-1 text-xs font-normal text-slate-500">条</span></p></div>)}</div>
      <div className="flex flex-wrap items-center gap-2" aria-label="跟进记录筛选">{[['actionable','到期／有新结果'],['open','全部未关闭'],['all','全部记录']].map(([value,label]) => <button key={value} type="button" onClick={() => setFilter(value)} aria-pressed={filter === value} className={`rounded-full px-3 py-1.5 text-xs ${filter === value ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600'}`}>{label}</button>)}<button type="button" onClick={() => setReload(r => r + 1)} className="ml-auto text-xs text-brand-700">刷新复查结果</button></div>
      {records.length === 0 ? <div className="rounded-lg border border-dashed border-sky-200 bg-sky-50/50 p-6 text-sm text-slate-500">{data.records.length === 0 ? '暂无已建档的跟进。可以在学生档案中记录问题、措施和复查目标。' : '当前筛选下暂无记录，可查看全部未关闭或全部记录。'}<Link href="/homeroom/profile" className="mt-3 block text-brand-700 hover:underline">前往学生档案</Link></div> : <Table>
        <TableHeader><TableRow><TableHead>学生与关注问题</TableHead><TableHead>措施与时间</TableHead><TableHead>目标与事实对照</TableHead><TableHead>记录状态</TableHead><TableHead>操作</TableHead></TableRow></TableHeader>
        <TableBody>{records.map(record => <TableRow key={record.id}>
          <TableCell className="min-w-[160px] align-top">{record.in_current_roster ? <Link href={profileHref(record.person_id)} className="font-semibold text-brand-700 hover:underline">{record.name ?? `学生 ${record.person_id}`}</Link> : <span className="font-semibold text-slate-600">{record.name ?? `学生 ${record.person_id}`}</span>}<p className="mt-1 text-xs text-slate-500">{record.problem || '未填写关注问题'}</p><p className="mt-2 text-xs text-slate-400">{record.subject || '未限定学科'}</p></TableCell>
          <TableCell className="min-w-[180px] align-top"><p className="text-sm">{record.measures || '未填写措施'}</p><p className="mt-2 text-xs text-slate-500">开始：{record.start_date}</p><p className="mt-1 text-xs text-slate-500">复查：{record.review_date || '未设定'}</p></TableCell>
          <TableCell className="min-w-[260px] align-top"><p className="mb-2 text-xs text-slate-500">{record.metric_label}</p><ReviewValues record={record}/></TableCell>
          <TableCell className="min-w-[150px] align-top"><span className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-600">{record.record_status === 'open' ? '跟进中' : record.record_status === 'done' ? '教师已关闭' : '教师已取消'}</span>{record.record_status === 'open' && record.due && <p className="mt-2 text-xs font-medium text-amber-700">已到计划复查日</p>}<p className="mt-2 text-xs text-slate-500">{record.contrast.status === 'ready' ? '已有可比结果' : '待补充可比证据'}</p></TableCell>
          <TableCell className="min-w-[115px] align-top">{record.in_current_roster ? <Link href={profileHref(record.person_id)} className="text-xs font-medium text-brand-700 hover:underline">查看／记录观察</Link> : <span className="text-xs text-slate-500">已离班，保留记录</span>}</TableCell>
        </TableRow>)}</TableBody>
      </Table>}
      <p className="text-xs leading-5 text-slate-400">“到期”与“有可比结果”可能重叠，按记录计数，不按人数。系统提供事实对照，由教师判断是否继续跟进。</p>
    </>}
  </div>
}
