'use client'
import Link from 'next/link'
import {ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip} from 'recharts'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '@/components/ui/table'
import {type FocusOutcome, type FocusStudent, formatValue, formatChange, profileHref} from './focus-api'

function tone(direction: string) {
  if (/退步|扩大|需核查/.test(direction)) return 'bg-amber-50 text-amber-800'
  if (/进步|有所改善/.test(direction)) return 'bg-emerald-50 text-emerald-800'
  return 'bg-slate-100 text-slate-600'
}
function Performance({student, side, outcome}: {student: FocusStudent; side: 'from' | 'to'; outcome: FocusOutcome}) {
  if (outcome.metric === 'problem:imbalance') return <div className="space-y-2">{student.imbalance.map(row => <div key={row.subject}>
    <div className="font-medium">{row.subject} · {formatValue(side === 'from' ? row.from_percentile : row.to_percentile, 'percentile')}</div>
    <div className="text-xs text-slate-500">总体 {formatValue(side === 'from' ? row.from_overall_percentile : row.to_overall_percentile, 'percentile')}</div>
    <div className="text-xs text-slate-500">短板差距 {side === 'from' ? row.from_gap ?? '—' : row.to_gap ?? '—'} 个百分点</div>
  </div>)}</div>
  if (outcome.metric === 'problem:homework') {
    const h = student.homework?.[side]
    return h ? <div><div className="font-medium">缺交 {h.missing} 次 · 连缺 {h.streak_days} 天</div><div className="mt-1 text-xs text-slate-500">{h.valid_batches} 个有效批次 · {h.subjects.join('、') || '无学科'}</div><div className="text-xs text-slate-500">{h.start} 至 {h.end}</div>{h.legacy_batches > 0 && <div className="text-xs text-amber-700">另有 {h.legacy_batches} 个批次缺应交名单</div>}</div> : <span>—</span>
  }
  return <span className="font-medium tabular-nums">{formatValue(side === 'from' ? student.from_value : student.to_value, outcome.metric_unit)}</span>
}
function StudentRow({student, outcome, excluded = false}: {student: FocusStudent; outcome: FocusOutcome; excluded?: boolean}) {
  return <TableRow className={excluded ? 'bg-slate-50/70' : undefined}>
    <TableCell className="min-w-[185px] align-top">
      <Link href={profileHref(student.person_id)} className="font-semibold text-brand-700 hover:underline">{student.name ?? `学生 ${student.person_id}`}</Link>
      <p className="mt-1 max-w-[270px] text-xs leading-5 text-slate-500">{student.reason}</p>
      {student.is_secondary && <p className="mt-1 text-xs text-sky-700">次标签入选 · 主类型：{student.main_type ?? '数据不足'}</p>}
    </TableCell>
    <TableCell className="min-w-[180px] align-top"><Performance student={student} side="from" outcome={outcome}/></TableCell>
    <TableCell className="min-w-[180px] align-top"><Performance student={student} side="to" outcome={outcome}/></TableCell>
    <TableCell className="min-w-[210px] align-top">
      <span className={`inline-block rounded-md px-2 py-1 text-xs font-medium ${tone(student.direction)}`}>{student.direction}</span>
      {student.change != null && <p className="mt-2 font-medium">{formatChange(student.change, outcome.metric_unit)}</p>}
      {student.imbalance.map(row => <p key={row.subject} className="mt-2 text-xs leading-5 text-slate-500">{row.subject}：{row.verdict}</p>)}
      {student.homework && <p className="mt-2 max-w-[260px] text-xs leading-5 text-slate-500">{student.homework.reason}</p>}
      {student.missing_reason && <p className="mt-2 text-xs leading-5 text-slate-500">{student.missing_reason}</p>}
      {student.timeline.length > 0 && <details className="mt-3"><summary className="cursor-pointer text-xs text-brand-700">历次走势与证据</summary>
        <div className="mt-2 h-36 w-64" role="img" aria-label={`${student.name}所选区间历次成绩走势，缺考保留断点`}>
          <ResponsiveContainer width="100%" height="100%"><LineChart data={student.timeline} margin={{top: 8, right: 10, bottom: 0, left: 0}}>
            <XAxis dataKey="exam_name" tick={{fontSize: 10}} interval="preserveStartEnd"/><YAxis reversed={outcome.metric_unit !== 'grade_score'} tick={{fontSize: 10}} width={38}/>
            <Tooltip formatter={(v: number) => formatValue(v, outcome.metric_unit)}/><Line dataKey="value" name="成绩" stroke="#2189db" strokeWidth={2} connectNulls={false} dot={{r: 3}} isAnimationActive={false}/>
          </LineChart></ResponsiveContainer>
        </div>
        <ul className="mt-2 space-y-1 text-xs text-slate-500">{student.timeline.map(point => <li key={point.exam_name}>{point.exam_name}：{formatValue(point.value, outcome.metric_unit)}{point.value == null ? '（缺考或缺指标）' : ''}</li>)}</ul>
      </details>}
    </TableCell>
    <TableCell className="min-w-[120px] align-top"><div className="text-xs text-slate-500">{student.follow_up_count > 0 ? `${student.follow_up_count} 条跟进记录` : '尚未建档跟进'}</div>
      <Link href={profileHref(student.person_id)} className="mt-3 inline-block rounded-md border border-sky-200 px-2 py-1.5 text-xs font-medium text-brand-700 hover:bg-sky-50">{student.follow_up_count > 0 ? '查看／继续跟进' : '查看证据／建立跟进'}</Link>
    </TableCell>
  </TableRow>
}
export function FocusResults({outcome}: {outcome: FocusOutcome}) {
  return <div className="space-y-4" data-testid="focus-results">
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[
      ['入选学生', outcome.selected_n], ['可比学生', outcome.comparable_n], ['需要进一步核查', outcome.summary.needs_check_n], ['暂不可比', outcome.excluded_n],
    ].map(([label,value]) => <div key={String(label)} className="rounded-xl border border-slate-100 bg-white px-4 py-3"><p className="text-xs text-slate-500">{label}</p><p className="mt-1 text-2xl font-semibold text-slate-800">{value}<span className="ml-1 text-xs font-normal text-slate-500">人</span></p></div>)}</div>
    <div className="text-xs leading-6 text-slate-500">
      <p>{outcome.membership_basis === 'exam_anchor' ? `按 ${outcome.from_exam} 当时的主／次标签固定名单` : `按 ${outcome.as_of} 当前主／次标签选人，回看历史走势`} · 名单范围为当前在册学生</p>
      <p>{outcome.from_exam}（{outcome.from_exam_date ?? '日期不足'}）→ {outcome.to_exam}（{outcome.to_exam_date ?? '日期不足'}）</p>
      {outcome.threshold != null && <p>变化阈值：{outcome.threshold} {outcome.metric_unit === 'rank' ? '名' : outcome.metric_unit === 'percentile' ? '个百分点' : '分'}；小于阈值仍展示实际变化。{outcome.threshold_provisional ? '此阈值暂定，尚未用历史分布校准。' : ''}</p>}
      {outcome.metric === 'problem:imbalance' && <p>短板差距＝单科年级前百分位－本人主三门百分位；差距缩小时，还需核对单科是否退步。</p>}
    </div>
    {outcome.selected_n === 0 ? <p className="rounded-lg bg-slate-50 p-6 text-sm text-slate-500">该问题在所选时点暂无学生，可切换问题或起点考试。</p> : <Table>
      <TableHeader><TableRow><TableHead>学生与入选依据</TableHead> <TableHead>原来表现</TableHead><TableHead>后来表现</TableHead><TableHead>变化与核查事项</TableHead><TableHead>跟进</TableHead></TableRow></TableHeader>
      <TableBody>{outcome.students.map(student => <StudentRow key={student.person_id} student={student} outcome={outcome}/>)}</TableBody>
    </Table>}
    {outcome.excluded_students.length > 0 && <details className="rounded-lg border border-slate-200 bg-slate-50/50 p-3" open>
      <summary className="cursor-pointer text-sm font-medium text-slate-600">暂不可比名单 · {outcome.excluded_n} 人（保留原因供核查）</summary>
      <Table><TableBody>{outcome.excluded_students.map(student => <StudentRow key={student.person_id} student={student} outcome={outcome} excluded/>)}</TableBody></Table>
    </details>}
    <p className="text-xs text-slate-400">“需要进一步核查”是提示清单，不是风险概率。小样本以逐人事实为主。</p>
  </div>
}
