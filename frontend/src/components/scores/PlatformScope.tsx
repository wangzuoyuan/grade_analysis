'use client'
import { useCallback, useEffect, useState } from 'react'
import { listAcademicYears, fetchSharedConfig, fetchClasses, type AcademicYear, type ClassesCatalogTeaching } from '@/lib/api-v1'
import { useWorkspace } from '@/lib/workspace'
import { apiErrorMessage } from '@/components/link/error-text'

export function usePlatformScope() {
  const { filter, setFilter, generation } = useWorkspace()
  const [years, setYears] = useState<AcademicYear[]>([])
  const [year, setYear] = useState<number | null>(filter.academic_year_id ?? null)
  const [grade, setGrade] = useState(filter.grade ?? 2)
  const [subject, setSubject] = useState('')
  const [classes, setClasses] = useState<ClassesCatalogTeaching[]>([])
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let stale = false
    Promise.all([listAcademicYears(), fetchSharedConfig()]).then(([ys, cfg]) => {
      if (stale) return
      setYears(ys.years)
      setYear(filter.academic_year_id ?? cfg.current_academic_year?.id ?? null)
      setGrade(filter.grade ?? 2)
      setSubject(cfg.teaching.subject ?? '')
      setError(null)
    }).catch(e => { if (!stale) setError(apiErrorMessage(e)) })
    return () => { stale = true }
  }, [filter.academic_year_id, filter.grade, generation])
  useEffect(() => {
    let stale = false
    setClasses([])
    if (year == null) return
    fetchClasses(year).then(r => { if (!stale) { setClasses(r.teaching); setError(null) } })
      .catch(e => { if (!stale) setError(apiErrorMessage(e)) })
    return () => { stale = true }
  }, [year])
  const chooseYear=useCallback((id:number)=>{
    setYear(id)
    if(filter.academic_year_id!==id)setFilter({academic_year_id:id,term_id:undefined,teaching_class_id:'all'})
  },[filter.academic_year_id,setFilter])
  const chooseGrade=useCallback((g:number)=>{
    setGrade(g)
    if(filter.grade!==g)setFilter({grade:g,teaching_class_id:'all'})
  },[filter.grade,setFilter])
  return { years, year, chooseYear, grade, chooseGrade, subject, classes:classes.filter(c=>c.grade==null||c.grade===grade), error, generation, teachingClassId:typeof filter.teaching_class_id==='number'?filter.teaching_class_id:undefined }
}
export function PlatformScope({ value }: {value: ReturnType<typeof usePlatformScope>}) {
  return <div className="flex flex-wrap gap-4">
    <label className="text-sm text-slate-600">学年<select aria-label="导入与对比学年" className="ml-2 rounded-md border bg-white p-2" value={value.year ?? ''} onChange={e => value.chooseYear(Number(e.target.value))}>
      <option value="" disabled>选择学年</option>{value.years.map(y => <option key={y.id} value={y.id}>{y.name}</option>)}
    </select></label>
    <label className="text-sm text-slate-600">年级<select aria-label="导入与对比年级" className="ml-2 rounded-md border bg-white p-2" value={value.grade} onChange={e => value.chooseGrade(Number(e.target.value))}>
      <option value={1}>高一</option><option value={2}>高二</option><option value={3}>高三</option>
    </select></label>
    <p className="self-center text-sm text-slate-600">任教学科：<strong>{value.subject || '尚未配置'}</strong></p>
    {value.error ? <p role="alert" className="text-sm text-red-600">{value.error}</p> : null}
  </div>
}
