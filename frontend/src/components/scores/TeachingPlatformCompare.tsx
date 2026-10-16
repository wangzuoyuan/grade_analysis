'use client'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { platformExams, platformCompare, platformStudents, type PlatformExam, type PlatformCompare, type PlatformStudent } from '@/lib/api-v1'
import { PlatformScope, usePlatformScope } from './PlatformScope'
import { apiErrorMessage } from '@/components/link/error-text'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'

const num=(n: number | null | undefined, digits=2) => n==null?'—':n.toLocaleString('zh-CN',{maximumFractionDigits:digits})
const pct=(n: number | null | undefined) => n==null?'—':`${num(n*100)}%`
const sourceLabel: Record<string,string>={official:'官方',estimated:'学生样本估算',unavailable:'尚无统计'}

function ExamDetails({id,grade,classId}: {id:number;grade:number;classId?:number}) {
  const [data,setData]=useState<PlatformCompare | null>(null)
  const [rows,setRows]=useState<PlatformStudent[] | null>(null)
  const [error,setError]=useState<string | null>(null)
  const [studentError,setStudentError]=useState<string | null>(null)
  const [basis,setBasis]=useState<'raw'|'grade'>('raw')
  const [sort,setSort]=useState<'name'|'score'>('score')
  useEffect(()=>{
    let stale=false
    platformCompare(id).then(r=>{if(!stale)setData(r)}).catch(e=>{if(!stale)setError(apiErrorMessage(e))})
    platformStudents(id,classId).then(r=>{if(!stale)setRows(r.students)}).catch(e=>{if(!stale)setStudentError(apiErrorMessage(e))})
    return()=>{stale=true}
  },[id,classId])
  if(error)return <p role="alert" className="rounded bg-red-50 p-4 text-red-700">{error}</p>
  if(!data)return <p role="status" className="p-8 text-center text-slate-500">正在读取考试快照…</p>
  const classes=[...data.classes].sort((a,b)=>sort==='name'?a.class_label.localeCompare(b.class_label,'zh-CN'):(basis==='raw'?b.raw_avg??-1:b.grade_avg??-1)-(basis==='raw'?a.raw_avg??-1:a.grade_avg??-1))
  const highest=Math.max(1,...classes.map(c=>(basis==='raw'?c.raw_avg:c.grade_avg)??0))
  const overall=data.overall[basis]
  return <>
    <div className="grid gap-4 sm:grid-cols-3">
      <Card><CardContent className="pt-5"><p className="text-sm text-slate-500">考试班级快照</p><p className="mt-2 text-2xl font-semibold">{classes.length}<span className="ml-2 text-sm font-normal text-slate-400">个参照班</span></p></CardContent></Card>
      <Card><CardContent className="pt-5"><p className="text-sm text-slate-500">{basis==='raw'?'原始':'等级'}总体均分</p><p className="mt-2 text-2xl font-semibold text-sky-700">{num(overall?.value)}</p><p className="mt-1 text-xs text-slate-500">{overall?overall.method==='matching_aggregate'?'范围匹配的官方汇总':'同范围官方班级按实考人数加权':'覆盖范围尚不能合并'}</p></CardContent></Card>
      <Card><CardContent className="pt-5"><p className="text-sm text-slate-500">数据依据</p><p className="mt-2 text-sm font-medium">{data.exam.subject} · {data.exam.exam_date}</p><p className="mt-2 text-xs text-slate-500">按考试当时快照 · 修订 {data.metadata.data_revision}</p></CardContent></Card>
    </div>
    {data.warnings.length?<div className="space-y-1 rounded-lg border border-amber-200 bg-amber-50/50 p-3 text-sm text-amber-800">{data.warnings.map(w=><p key={w}>{w}</p>)}</div>:null}
    <Card><CardHeader><div className="flex flex-wrap items-center justify-between gap-3"><CardTitle>班级均分对比</CardTitle><div className="flex gap-2"><Button size="sm" variant={basis==='raw'?'default':'outline'} onClick={()=>setBasis('raw')}>原始分</Button>{grade!==1?<Button size="sm" variant={basis==='grade'?'default':'outline'} onClick={()=>setBasis('grade')}>等级分</Button>:null}<Button variant="ghost" size="sm" onClick={()=>setSort(sort==='score'?'name':'score')}>{sort==='score'?'按班名排序':'按均分排序'}</Button></div></div></CardHeader><CardContent className="space-y-3">
      {classes.length?classes.map(c=>{const avg=basis==='raw'?c.raw_avg:c.grade_avg;return <div key={c.id} className="flex items-center gap-3 text-sm"><span className="w-24 shrink-0 break-words sm:w-32">{c.class_label}<span className="ml-1 text-xs text-slate-400">{c.class_kind==='teaching'?'教学':'行政'}</span></span><div className="h-4 flex-1 rounded bg-slate-100"><div className={`h-full rounded ${c.source==='estimated'?'bg-slate-400':'bg-sky-500'}`} style={{width:`${avg==null?0:Math.max(0,avg/highest*100)}%`}}/></div><span className="w-14 text-right tabular-nums">{num(avg)}</span><span className="hidden w-24 text-xs text-slate-500 sm:block">{sourceLabel[c.source]}</span></div>}):<p className="text-sm text-slate-500">本场尚未导入质量表。学生明细已保存，可在下方查看。</p>}
    </CardContent></Card>
    {classes.length?<Card><CardHeader><CardTitle>官方质量指标</CardTitle><p className="text-sm text-slate-500">名次按同来源、同分值类型排序；均差仅使用覆盖一致的总体。空值显示“—”。</p></CardHeader><CardContent><div className="overflow-x-auto"><table className="w-full whitespace-nowrap text-left text-sm"><thead className="bg-slate-50"><tr>{['班级','班型','任课教师','来源','应考','实考','原始均分','等级均分','标准差','名次','均差',...(grade===1?['单科总分','最高分','最低分']:[])].map(h=><th key={h} className="px-3 py-3 font-medium text-slate-500">{h}</th>)}</tr></thead><tbody>{classes.map(c=><tr key={c.id} className="border-t"><td className="px-3 py-3 font-medium">{c.class_label}</td><td className="px-3">{c.class_type??'—'}</td><td className="px-3">{c.teacher_name??'—'}</td><td className="px-3 text-xs">{sourceLabel[c.source]}</td><td className="px-3 tabular-nums">{num(c.metrics.expected_count,0)}</td><td className="px-3 tabular-nums">{num(c.metrics.actual_count,0)}</td><td className="px-3 tabular-nums">{num(c.raw_avg)}</td><td className="px-3 tabular-nums">{num(c.grade_avg)}</td><td className="px-3 tabular-nums" title={c.metrics.stddev_basis==='unspecified'?'官方未注明原始分/等级分口径':'原始分标准差'}>{num(c.metrics.stddev)}{c.metrics.stddev_basis==='unspecified'?<span className="ml-1 text-xs text-slate-400">口径未注明</span>:null}</td><td className="px-3">{num(basis==='raw'?c.raw_rank:c.grade_rank,0)}</td><td className="px-3 tabular-nums">{num(basis==='raw'?c.raw_diff:c.grade_diff)}</td>{grade===1?<><td className="px-3">{num(c.metrics.score_sum)}</td><td className="px-3">{num(c.metrics.max_score)}</td><td className="px-3">{num(c.metrics.min_score)}</td></>:null}</tr>)}</tbody></table></div></CardContent></Card>:null}
    {classes.some(c=>c.metrics.bands?.length)?<Card><CardHeader><CardTitle>{grade===1?'分数段人数及比率':'A+ 至 E 等级人数及比率'}</CardTitle>{grade!==1?<p className="text-sm text-slate-500">横向滚动可查看全部等级指标。</p>:null}</CardHeader><CardContent><div className="overflow-x-auto"><table className="w-full whitespace-nowrap text-left text-sm"><thead className="bg-slate-50"><tr><th className="p-3">班级</th>{(classes.find(c=>c.metrics.bands?.length)?.metrics.bands??[]).map(b=><th key={b.label} className="p-3 text-right">{b.label}</th>)}</tr></thead><tbody>{classes.map(c=><tr key={c.id} className="border-t"><td className="p-3">{c.class_label}</td>{(classes.find(r=>r.metrics.bands?.length)?.metrics.bands??[]).map(b=>{const band=c.metrics.bands?.find(v=>v.label===b.label);return <td key={b.label} className="p-3 text-right tabular-nums"><span>{band?.count==null?'—':`${num(band.count,0)} 人`}</span><span className="ml-2 text-xs text-slate-400">{pct(band?.ratio)}</span></td>})}</tr>)}</tbody></table></div></CardContent></Card>:null}
    <Card><CardHeader><CardTitle>学生成绩明细</CardTitle><p className="text-sm text-slate-500">仅本人管理班与合法关联成员；名次为本次所选管理班范围内排序，学籍排名与年级百分位来自导入文件。</p></CardHeader><CardContent>
      {studentError?<p role="alert" className="text-sm text-red-700">{studentError}</p>:rows==null?<p className="text-sm text-slate-500">正在读取学生明细…</p>:!rows.length?<p className="rounded bg-slate-50 p-6 text-sm text-slate-500">本场尚无可读学生明细。官方质量分析已可独立对比。</p>:<div className="overflow-x-auto"><table className="w-full whitespace-nowrap text-left text-sm"><thead className="bg-slate-50"><tr>{['姓名','班级','原始分',...(grade===1?['学籍排名','年级百分位']:['等级分','等级']),'范围内名次','来源'].map(h=><th key={h} className="p-3">{h}</th>)}</tr></thead><tbody>{rows.map(r=><tr key={r.person_id} className="border-t"><td className="p-3">{r.name??'—'}</td><td className="p-3">{r.class_label??'—'}</td><td className="p-3">{num(r.score)}</td>{grade===1?<><td className="p-3">{num(r.xueji_rank,0)}</td><td className="p-3">{pct(r.grade_percentile)}</td></>:<><td className="p-3">{num(r.grade_score)}</td><td className="p-3">{r.grade_label??'—'}</td></>}<td className="p-3">{num(r.rank,0)}</td><td className="p-3">{r.source_domain==='homeroom'?'班主任域投影':'教学域'}</td></tr>)}</tbody></table></div>}
    </CardContent></Card>
  </>
}

export function TeachingPlatformCompare({onLegacy,title='班级对比'}:{onLegacy:()=>void;title?:string}) {
  const scope=usePlatformScope()
  const params=useSearchParams()
  const [exams,setExams]=useState<PlatformExam[]>([])
  const [selected,setSelected]=useState<number | null>(null)
  const [error,setError]=useState<string | null>(null)
  const [loading,setLoading]=useState(true)
  const queryGrade=params.get('grade'),queryYear=params.get('academic_year_id'),queryExam=params.get('exam_id')
  const appliedQuery=useRef('')
  useEffect(()=>{
    const key=`${queryGrade}:${queryYear}`
    if(!scope.years.length||appliedQuery.current===key)return
    appliedQuery.current=key
    if(queryGrade&&[1,2,3].includes(Number(queryGrade)))scope.chooseGrade(Number(queryGrade))
    if(queryYear&&scope.years.some(y=>y.id===Number(queryYear)))scope.chooseYear(Number(queryYear))
  },[queryGrade,queryYear,scope.years,scope.chooseGrade,scope.chooseYear])
  useEffect(()=>{
    let stale=false
    setExams([]);setSelected(null);setError(null);setLoading(true)
    if(!scope.year||!scope.subject){setLoading(false);return}
    platformExams(scope.year,scope.grade,scope.subject).then(r=>{if(!stale){setExams(r.exams);setSelected(r.exams.some(e=>e.id===Number(queryExam))?Number(queryExam):r.exams[0]?.id??null)}})
      .catch(e=>{if(!stale)setError(apiErrorMessage(e))}).finally(()=>{if(!stale)setLoading(false)})
    return()=>{stale=true}
  },[scope.year,scope.grade,scope.subject,scope.generation,queryExam])
  return <div className="space-y-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-2xl font-semibold text-slate-900">{title}</h1><p className="mt-1 text-sm text-slate-500">按考试班级快照查看官方质量分析，只有汇总表也能对比。</p></div><div className="flex gap-2"><Button asChild variant="outline"><Link href="/upload?ws=teaching">导入 / 补录</Link></Button><Button variant="ghost" onClick={onLegacy}>旧模板成绩与样本对比</Button></div></div>
    <Card><CardContent className="space-y-4 pt-5"><PlatformScope value={scope}/><label className="block text-sm text-slate-600">考试<select aria-label="官方质量分析考试" className="ml-2 max-w-full rounded border bg-white p-2" value={selected??''} onChange={e=>setSelected(Number(e.target.value))}><option value="" disabled>{loading?'加载中…':'选择考试'}</option>{exams.map(e=><option key={e.id} value={e.id}>{e.exam_name} · {e.exam_date}</option>)}</select></label></CardContent></Card>
    {error?<p role="alert" className="text-red-700">{error}</p>:null}
    {!loading&&!error&&!exams.length?<Card><CardContent className="space-y-3 py-10 text-center"><p className="font-medium text-slate-700">此学年、年级尚无教学平台考试</p><p className="text-sm text-slate-500">导入学生明细或质量分析表后即可查看。已有旧模板成绩可通过右上入口查看。</p></CardContent></Card>:null}
    {selected?<ExamDetails key={`${scope.year}:${scope.grade}:${scope.subject}:${scope.generation}:${selected}`} id={selected} grade={scope.grade} classId={scope.teachingClassId}/>:null}
  </div>
}
