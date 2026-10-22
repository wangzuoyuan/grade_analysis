'use client'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { platformExams, platformCompare, type PlatformExam, type PlatformCompare } from '@/lib/api-v1'
import { PlatformScope, usePlatformScope } from './PlatformScope'
import { apiErrorMessage } from '@/components/link/error-text'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { TeachingQualityTables } from './TeachingQualityTables'

function ExamDetails({ id, grade }: { id: number; grade: number }) {
  const [data, setData] = useState<PlatformCompare | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let stale = false
    setData(null); setError(null)
    platformCompare(id).then(r => { if (!stale) setData(r) })
      .catch(e => { if (!stale) setError(apiErrorMessage(e)) })
    return () => { stale = true }
  }, [id])
  if (error) return <p role="alert" className="rounded bg-red-50 p-4 text-red-700">{error}</p>
  if (!data) return <p role="status" className="p-8 text-center text-slate-500">正在读取考试快照…</p>
  return <TeachingQualityTables data={data} grade={grade} />
}

export function TeachingPlatformCompare({title='班级对比'}:{title?:string}) {
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
  return <div className="min-w-0 space-y-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-2xl font-semibold text-slate-900">{title}</h1><p className="mt-1 text-sm text-slate-500">按考试班级快照查看官方质量分析，只有汇总表也能对比。</p></div><div className="flex gap-2"><Button asChild variant="outline"><Link href="/upload?ws=teaching">导入 / 补录</Link></Button></div></div>
    <Card><CardContent className="space-y-4 pt-5"><PlatformScope value={scope}/><label className="block text-sm text-slate-600">考试<select aria-label="官方质量分析考试" className="ml-2 max-w-full rounded border bg-white py-2 pl-2" value={selected??''} onChange={e=>setSelected(Number(e.target.value))}><option value="" disabled>{loading?'加载中…':'选择考试'}</option>{exams.map(e=><option key={e.id} value={e.id}>{e.exam_name} · {e.exam_date}</option>)}</select></label></CardContent></Card>
    {error?<p role="alert" className="text-red-700">{error}</p>:null}
    {!loading&&!error&&!exams.length?<Card><CardContent className="space-y-3 py-10 text-center"><p className="font-medium text-slate-700">此学年、年级尚无教学平台考试</p><p className="text-sm text-slate-500">导入学生明细或质量分析表后即可查看。</p></CardContent></Card>:null}
    {selected?<ExamDetails key={`${scope.year}:${scope.grade}:${scope.subject}:${scope.generation}:${selected}`} id={selected} grade={scope.grade}/>:null}
  </div>
}
