'use client'

import { useEffect, useMemo, useState } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { AlertCircle, RefreshCw, UserRound } from 'lucide-react'
import { studentHomeworkAnalysis, type HomeworkScopeQuery, type WorkspaceMode } from '@/lib/api-v1'
import { personalAchievement, personalReason, type PersonalCorrelation, type PersonalHomeworkStudent, type StudentHomeworkResponse } from '@/lib/student-homework'
import { displayStudentId } from '@/lib/student-id'
import { apiErrorMessage } from '@/components/link/error-text'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'

const selectClass = 'h-9 w-full min-w-0 rounded-md border border-slate-200 bg-white py-1 pl-2 text-sm'
const SUBJECTS = ['语文', '数学', '英语', '物理', '化学', '生物', '政治', '历史', '地理']
const percent = (v: number | null | undefined) => v == null ? '—' : `${(v * 100).toFixed(1)}%`
const signed = (v: number | null | undefined, scale = 1) => v == null ? '—' : `${v > 0 ? '+' : ''}${(v * scale).toFixed(1)}`

function Coefficient({ title, value, help }: { title: string; value: PersonalCorrelation; help: string }) {
  return <div className="min-w-0 rounded-lg border border-sky-100 bg-sky-50/40 p-3">
    <h4 className="text-sm font-medium text-slate-700">{title}</h4>
    <p className="mt-2 text-lg font-semibold tabular-nums text-slate-800">
      {value.rho == null ? '暂不能判断' : `ρ = ${value.rho.toFixed(3)}`}
    </p>
    <p className="mt-1 text-xs text-slate-500">{value.n} 场配对{value.r == null ? '' : ` · Pearson r = ${value.r.toFixed(3)}`}</p>
    {value.reason && <p className="mt-2 text-xs text-amber-700">{personalReason(value.reason)}</p>}
    {value.rho_without_one && <p className="mt-2 text-xs text-slate-500">删去任一场后 ρ：{value.rho_without_one.map(v => v.toFixed(2)).join(' ～ ')}{value.sensitive ? '（变化较大）' : ''}</p>}
    <p className="mt-2 text-xs leading-relaxed text-slate-500">{help}</p>
  </div>
}

function StudentDetail({ student, mode }: { student: PersonalHomeworkStudent; mode: WorkspaceMode }) {
  const latest = student.latest_change
  const chart = student.timeline.filter(p => p.exam_date != null).map(p => ({
    name: p.exam_name, date: p.exam_date, submission: p.homework?.submission_rate == null ? null : p.homework.submission_rate * 100,
    achievement: p.achievement == null ? null : p.grade === 1 && p.percentile != null ? p.percentile * 100 : p.achievement,
  }))
  const grades = new Set(student.timeline.map(p => p.grade).filter(g => g != null))
  const mixed = grades.size > 1
  const high1 = grades.size === 1 && grades.has(1)
  const scoreLabel = high1 ? '年级百分位（前%，越小越好）' : '等级分'
  const missingMeasures = ['grade_score_missing', 'percentile_missing', 'grade_missing', 'grade_conflict'].filter(reason => student.excluded_reasons[reason])
  return <div className="min-w-0 space-y-4" aria-label="个人分析详情" data-testid="personal-detail">
    <div>
      <h3 className="flex items-center gap-2 text-lg font-semibold text-slate-800"><UserRound className="h-5 w-5 text-sky-600" />{student.name ?? '未命名学生'}</h3>
      <p className="mt-1 text-xs text-slate-500">{student.class_labels.join(' / ')} · 学号 {displayStudentId(student.alias) || '—'} · 只使用该生的考试与作业记录</p>
    </div>
    {missingMeasures.length > 0 && <div className="rounded-lg border border-amber-100 bg-amber-50 p-3 text-xs leading-relaxed text-amber-800">
      <p>{missingMeasures.map(reason => `${personalReason(reason)} ${student.excluded_reasons[reason]}场`).join('；')}。对应记录未参与相关计算，原始分仅供参考。</p>
      <a href={`/upload?ws=${mode}`} className="mt-1 inline-block font-medium underline underline-offset-2">前往成绩上传补录相应指标，并核对考试与年级</a>
    </div>}
    <div className="grid grid-cols-2 gap-2 xl:grid-cols-4">
      {[
        ['最近成绩', personalAchievement(latest)],
        ['较上次进步', `${signed(latest?.gain)}${latest?.gain == null ? '' : high1 ? ' 百分点' : ' 等级分'}`],
        ['考前提交率', percent(latest?.homework?.submission_rate)],
        ['有效配对', `${student.paired_n} 场 / 共 ${student.timeline.length} 场`],
      ].map(([label, value]) => <div key={label} className="rounded-lg bg-slate-50 p-3">
        <p className="text-xs text-slate-500">{label}</p><p className="mt-1 break-words text-sm font-semibold tabular-nums text-slate-800">{value}</p>
      </div>)}
    </div>
    {student.timeline.length === 0 && <p className="rounded-lg bg-slate-50 p-4 text-sm text-slate-500">该生在当前条件下暂无同科考试记录，可调整学科或截止范围。</p>}
    {mixed ? <p className="text-sm text-amber-700">记录包含多个年级，请先选择单一年级查看趋势与相关系数。</p> : chart.length > 0 && <div className="min-w-0">
      <h4 className="text-sm font-medium text-slate-700">个人逐场变化</h4>
      <p className="mt-1 text-xs text-slate-500">按同一考试顺序分别查看收交与成绩变化；系数与逐场明细用于核对关系。</p>
      <div className="mt-3 grid min-w-0 gap-3 sm:grid-cols-2">
        {([{ field: 'submission', label: '考前提交率', color: '#0ea5e9' },
          { field: 'achievement', label: scoreLabel, color: '#8b5cf6' }] as const).map(series =>
          <div key={series.field} className="min-w-0 rounded-lg border border-slate-100 p-2">
            <p className="text-xs font-medium" style={{ color: series.color }}>{series.label}</p>
            <div className="mt-2 h-48 w-full min-w-0" role="img" aria-label={`${student.name ?? '该生'}的${series.label}趋势，完整数值见下方逐场明细`}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chart} margin={{ top: 5, right: 8, left: -20, bottom: 0 }}>
                  <CartesianGrid stroke="#dcecf8" strokeDasharray="3 3" />
                  <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={v => String(v).slice(5)} minTickGap={20} />
                  <YAxis domain={series.field === 'submission' || high1 ? [0, 100] : ['auto', 'auto']} reversed={high1 && series.field === 'achievement'} tick={{ fontSize: 10 }} tickFormatter={v => series.field === 'submission' ? `${v}%` : high1 ? `前${v}%` : String(v)} />
                  <Tooltip labelFormatter={(_, items) => `${items[0]?.payload?.name ?? ''} · ${items[0]?.payload?.date ?? ''}`} formatter={(v: number) => [series.field === 'submission' ? `${v.toFixed(1)}%` : high1 ? `前${v.toFixed(1)}%` : v.toFixed(1), series.label]} />
                  <Line type="linear" dataKey={series.field} name={series.label} stroke={series.color} strokeWidth={2} connectNulls={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>)}
      </div>
      {high1 && <p className="text-xs text-slate-500">高一百分位图向上代表更靠前；系数使用100−前百分比，使正相关统一表示提交率较高时表现更好。进步以减少的百分位百分点表示。</p>}
    </div>}
    <div className="grid gap-2 xl:grid-cols-3">
      <Coefficient title="提交率 × 成绩水平" value={student.correlations.submission} help="ρ为正表示该生提交率较高的考试，表现也更好；高一对应前百分比更小，高二三对应等级分更高。相关不表示作业带来了提分。" />
      <Coefficient title="提交率 × 较上次进步" value={student.correlations.progress} help="用本次考前提交率配对相邻两次考试的进步；缺考、指标缺失或跨年级时不补连。" />
      <Coefficient title="负面评价率 × 成绩水平" value={student.correlations.negative} help="ρ为负表示已评价作业中负面评价较多时表现较差。仅纳入有评价≥3份且覆盖已交作业≥50%的窗口。" />
    </div>
    <div className="rounded-lg border border-sky-100 bg-sky-50 p-3 text-sm leading-relaxed text-slate-700"><span className="font-medium">跟进建议：</span>{student.advice}</div>
    {student.caveats.length > 0 && <ul className="list-disc space-y-1 pl-4 text-xs leading-relaxed text-amber-800">{student.caveats.map(text => <li key={text}>{text}</li>)}</ul>}
    <details open className="min-w-0">
      <summary className="cursor-pointer text-sm font-medium text-slate-700">逐场配对明细（{student.timeline.length} 场）</summary>
      <div className="mt-2 max-w-full overflow-x-auto rounded-lg border border-slate-100" tabIndex={0} role="region" aria-label="逐场配对明细，可横向滚动">
        <table className="w-full min-w-[760px] text-left text-xs">
          <thead className="bg-sky-50 text-slate-600"><tr>{['考试 / 考前窗口', '成绩 / 进步', '提交率 / 分母', '评价 / 覆盖率', '配对说明'].map(h => <th key={h} className="whitespace-nowrap p-2 font-medium">{h}</th>)}</tr></thead>
          <tbody>{student.timeline.map(p => <tr key={p.exam_key} className="border-t border-slate-100 align-top text-slate-600">
            <td className="p-2"><p className="font-medium">{p.exam_name}</p><p>{p.exam_date ?? '日期不完整'}</p><p className="mt-1 text-slate-400">{p.window_start && p.window_end ? `${p.window_start} ～ ${p.window_end}` : '没有有效考前窗口'}</p></td>
            <td className="p-2 tabular-nums"><p>{personalAchievement(p)}</p><p className="mt-1">进步 {signed(p.gain)}{p.gain == null ? '' : p.grade === 1 ? ' 百分点' : ' 分'}</p><p className="text-slate-400">原始分 {p.raw_score == null ? '—' : p.raw_score.toFixed(1)}（参考）</p></td>
            <td className="p-2 tabular-nums"><p>{percent(p.homework?.submission_rate)}</p><p>{p.homework ? `${p.homework.submitted}/${p.homework.eligible} 份 · ${p.homework.days} 天` : '无有效分母'}</p><p className="text-slate-400">较上次 {signed(p.submission_change, 100)} 百分点</p>{p.homework && (p.homework.excused + p.homework.ignored > 0) && <p>排除请假/忘带/出勤 {p.homework.excused + p.homework.ignored} 份</p>}{!!p.homework?.unknown_batches && <p className="text-amber-700">分母不明 {p.homework.unknown_batches} 批</p>}</td>
            <td className="p-2 tabular-nums"><p>负面 {p.homework?.negative ?? 0} / 有评价 {p.homework?.evaluated ?? 0}</p><p>覆盖 {percent(p.homework?.evaluation_coverage)}</p><p className="text-slate-400">负面率 {percent(p.homework?.negative_rate)}</p></td>
            <td className={`p-2 ${p.pair_reason ? 'text-amber-700' : 'text-sky-700'}`}>{personalReason(p.pair_reason)}</td>
          </tr>)}</tbody>
        </table>
      </div>
    </details>
  </div>
}

/** Mounted by both score pages; each result describes one student's repeated exams. */
export function CorrelationCard({ mode, scopeQ, scopeSubject, generation, preferredExamName }: {
  mode: WorkspaceMode; scopeQ: HomeworkScopeQuery; scopeSubject: string | null;
  generation: number; preferredExamName?: string | null;
}) {
  const teaching = mode === 'teaching'
  const [subjectDraft, setSubjectDraft] = useState(scopeSubject || '物理')
  const [windowDays, setWindowDays] = useState<14 | 30>(14)
  const [grade, setGrade] = useState('')
  const [cutoff, setCutoff] = useState('parent')
  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [nonce, setNonce] = useState(0)
  const [result, setResult] = useState<{ key: string; data: StudentHomeworkResponse } | null>(null)
  const [error, setError] = useState<{ key: string; message: string } | null>(null)
  const subject = teaching ? scopeSubject ?? '' : subjectDraft
  const key = JSON.stringify([mode, scopeQ, generation, subject, windowDays, grade, cutoff === 'parent' ? preferredExamName : null, nonce])
  useEffect(() => {
    let cancelled = false
    setResult(null)
    setError(null)
    if (!subject) return
    studentHomeworkAnalysis(mode, { ...scopeQ, subject: teaching ? undefined : subject, window_days: windowDays,
      grade: grade ? Number(grade) : undefined,
      end_exam_name: cutoff === 'parent' ? preferredExamName ?? undefined : undefined,
    }).then(data => { if (!cancelled) setResult({ key, data }) })
      .catch((err: unknown) => { if (!cancelled) setError({ key, message: apiErrorMessage(err) }) })
    return () => { cancelled = true }
  }, [key]) // Includes every scope/filter dependency; cleanup rejects late responses.
  const data = result?.key === key ? result.data : null
  const message = error?.key === key ? error.message : null
  const students = useMemo(() => (data?.students ?? []).filter(s =>
    `${s.name ?? ''} ${s.alias ?? ''} ${displayStudentId(s.alias)}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())), [data, search])
  const student = students.find(s => s.person_id === selectedId) ?? students[0] ?? null
  return <Card className="min-w-0" data-testid="student-homework-analysis">
    <CardHeader>
      <CardTitle>个人作业与成绩分析</CardTitle>
      <CardDescription>查看同一个学生的多次考试：考前作业是否与成绩、进步同步变化。高一用年级百分位，高二、高三用等级分。</CardDescription>
    </CardHeader>
    <CardContent className="min-w-0 space-y-5">
      <div className="grid items-end gap-3 sm:grid-cols-2 xl:grid-cols-[1fr_1fr_1fr_1.4fr_auto]">
        <label className="min-w-0 space-y-1 text-xs text-slate-600"><span>分析学科</span>{teaching ? <Input value={subject || '未配置任教学科'} disabled className="h-9" /> : <select aria-label="分析学科" value={subjectDraft} onChange={e => setSubjectDraft(e.target.value)} className={selectClass}>{SUBJECTS.map(s => <option key={s}>{s}</option>)}</select>}</label>
        <label className="min-w-0 space-y-1 text-xs text-slate-600"><span>年级与成绩口径</span><select aria-label="分析年级" value={grade} onChange={e => setGrade(e.target.value)} className={selectClass}><option value="">按各生年级</option><option value="1">高一 · 年级百分位</option><option value="2">高二 · 等级分</option><option value="3">高三 · 等级分</option></select></label>
        <label className="min-w-0 space-y-1 text-xs text-slate-600"><span>考前窗口上限</span><select aria-label="考前窗口上限" value={windowDays} onChange={e => setWindowDays(Number(e.target.value) as 14 | 30)} className={selectClass}><option value={14}>14天</option><option value={30}>30天</option></select></label>
        <label className="min-w-0 space-y-1 text-xs text-slate-600"><span>考试范围（当前学年）</span><select aria-label="个人分析考试范围" value={cutoff} onChange={e => setCutoff(e.target.value)} className={selectClass}><option value="parent">截至上方所选考试</option><option value="all">当前学年全部考试</option></select></label>
        <Button variant="outline" disabled={!subject} onClick={() => setNonce(n => n + 1)}><RefreshCw className="mr-1 h-4 w-4" />重新分析</Button>
      </div>
      <p className="text-xs leading-relaxed text-slate-500">一场考试算一次观察；不含考试当日与考后作业。与上一场考试次日截断，避免重复使用作业。每窗至少3份有效作业、至少6场配对才显示探索相关系数。</p>
      {!subject && <p className="text-sm text-amber-700">请先在设置中配置任教学科。</p>}
      {message && <div role="alert" className="flex items-start gap-2 rounded-lg bg-rose-50 p-3 text-sm text-rose-700"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /><span>{message}。可调整学科、年级或考试范围后重试。</span></div>}
      {subject && !data && !message && <div role="status" aria-label="正在分析个人记录"><Skeleton className="h-48 w-full" /></div>}
      {data && <>
        <div className="grid min-w-0 gap-5 lg:grid-cols-[260px_minmax(0,1fr)]">
          <div className="min-w-0 space-y-2">
            <label className="block text-sm font-medium text-slate-700" htmlFor="homework-student-search">选择学生（{data.students.length}人）</label>
            <Input id="homework-student-search" aria-label="搜索学生姓名或学号" placeholder="姓名或学号" value={search} onChange={e => setSearch(e.target.value)} />
            <div className="max-h-[540px] space-y-1 overflow-y-auto rounded-lg border border-slate-100 p-1" aria-label="逐人分析名单">
              {students.map(s => <button type="button" key={s.person_id} aria-pressed={s.person_id === student?.person_id} aria-label={`查看${s.name ?? '未命名学生'}的个人分析`} onClick={() => setSelectedId(s.person_id)} className={`w-full rounded-md p-3 text-left transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-600 ${s.person_id === student?.person_id ? 'bg-sky-100 text-sky-900' : 'text-slate-700 hover:bg-slate-50'}`}>
                <span className="block text-sm font-medium">{s.name ?? '未命名学生'} <span className="text-xs font-normal text-slate-500">{displayStudentId(s.alias)}</span></span>
                <span className="mt-1 block text-xs">{personalAchievement(s.latest_change)} · 配对{s.paired_n}场</span>
              </button>)}
              {students.length === 0 && <p className="p-3 text-sm text-slate-500">{data.students.length ? '没有匹配的学生，请调整搜索。' : '当前范围没有学生，请检查班级与年级选择。'}</p>}
            </div>
          </div>
          {student && <StudentDetail key={student.person_id} student={student} mode={mode} />}
        </div>
        <p className="border-t border-slate-100 pt-3 text-xs leading-relaxed text-slate-500">{data.note} Spearman ρ为主，Pearson r为辅。删一场后的范围是敏感性检查，不是置信区间。请假、忘带、出勤不计入提交率分子分母；没有评价不代表优良。这些观察不构成因果结论或提分保证。</p>
      </>}
    </CardContent>
  </Card>
}
