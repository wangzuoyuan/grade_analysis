'use client'
import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { platformPreview, platformConfirm, ApiV1Error, type PlatformPreview, type PlatformChange } from '@/lib/api-v1'
import { PlatformScope, usePlatformScope } from '@/components/scores/PlatformScope'
import { apiErrorMessage } from '@/components/link/error-text'
import { Button } from '@/components/ui/button'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { IdentityCandidatesPanel, type IdentityPick } from '@/components/upload/IdentityCandidatesPanel'
import { ExamResolutionSelect } from './ExamResolutionSelect'
import { ScoreImportTemplates } from '@/components/upload/ScoreImportTemplates'

const fieldNames: Record<string,string> = {score:'原始分',exam_date:'考试日期',grade_score:'等级分',xueji_rank:'学籍排名',grade_percentile:'年级百分位',raw_avg:'原始均分',grade_avg:'等级均分',expected_count:'应考',actual_count:'实考',stddev:'标准差',teacher_name:'任课教师',class_type:'班级类型',bands:'人数及比率',aggregate:'汇总指标',score_sum:'单科总分',max_score:'最高分',min_score:'最低分',stddev_basis:'标准差口径',band_scheme:'分布口径',class_snapshot:'考试参照名单',count:'人数',ratio:'比率'}
function show(value: unknown,field='') {
  if(value==null)return '—'
  if(typeof value==='number'&&(field.endsWith('ratio')||field==='grade_percentile'))return `${Number((value*100).toFixed(4))}%`
  const labels:Record<string,string>={raw:'原始分',grade:'等级分',unspecified:'来源未注明',score_range:'分数段',grade11:'A+ 至 E'}
  if(typeof value==='string')return labels[value]??value
  if(field==='class_snapshot'&&Array.isArray(value))return value.map(c=>`${c.class_kind==='teaching'?'教学':'行政'}·${c.class_label}`).join('、')
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}
function flatten(value: unknown,prefix=''):Record<string,unknown> {
  if(value==null)return {}
  if(Array.isArray(value))return Object.assign({},...value.map((v,i)=>flatten(v,`${prefix}${prefix?'.':''}${typeof v?.label==='string'?v.label:i}`)))
  if(typeof value==='object')return Object.assign({},...Object.entries(value).filter(([k])=>k!=='label').map(([k,v])=>flatten(v,`${prefix}${prefix?'.':''}${k}`)))
  return {[prefix]:value}
}
export function PlatformChanges({ rows }: {rows: PlatformChange[]}) {
  const expanded=rows.flatMap(r=>{
    if(r.field!=='bands'&&r.field!=='aggregate')return [r]
    const before=flatten(r.existing),after=flatten(r.incoming)
    return [...new Set([...Object.keys(before),...Object.keys(after)])].filter(k=>before[k]!==after[k]).map(k=>({...r,field:k,existing:before[k],incoming:after[k]}))
  })
  return <div className="max-h-72 overflow-auto rounded-lg border"><table className="w-full text-left text-sm"><thead className="sticky top-0 bg-slate-50"><tr><th className="p-2">学生 / 班级</th><th>字段</th><th>原值</th><th>本次值</th></tr></thead><tbody>
    {expanded.map((r,i) => <tr className="border-t" key={i}><td className="p-2">{r.entity ?? r.alias ?? '—'}</td><td>{r.field?.split('.').map(k=>fieldNames[k]??k).join(' · ')??r.message}</td><td className="max-w-64 break-words p-2">{show(r.existing,r.field)}</td><td className="max-w-64 break-words p-2">{show(r.incoming,r.field)}</td></tr>)}
  </tbody></table></div>
}
export function TeachingPlatformUpload() {
  const scope = usePlatformScope()
  const [name,setName] = useState('')
  const [date,setDate] = useState('')
  const [target,setTarget] = useState('auto')
  const [operation,setOperation] = useState('initial')
  const [replaceRoster,setReplaceRoster] = useState(false)
  const [files,setFiles] = useState<File[]>([])
  const [examChoice,setExamChoice] = useState<number | string>()
  const [preview,setPreview] = useState<PlatformPreview | null>(null)
  const [error,setError] = useState<string | null>(null)
  const [busy,setBusy] = useState(false)
  const [revise,setRevise] = useState(false)
  const [acceptRoster,setAcceptRoster] = useState(false)
  const [picks,setPicks] = useState<Record<string,IdentityPick>>({})
  const [result,setResult] = useState<Awaited<ReturnType<typeof platformConfirm>> | null>(null)
  const requestId=useRef(0)
  const fileId=files.map(f => `${f.name}:${f.size}:${f.lastModified}`).join('|')
  const scopeKey=`${scope.year}:${scope.grade}:${scope.subject}:${scope.generation}`
  useEffect(() => {
    requestId.current += 1
    setExamChoice(undefined); setPreview(null); setResult(null); setError(null); setBusy(false); setRevise(false); setAcceptRoster(false); setPicks({})
  }, [name,date,target,operation,replaceRoster,fileId,scopeKey])
  useEffect(() => { setTarget('auto') }, [scope.year,scope.grade])
  useEffect(() => () => { requestId.current += 1 }, [])
  async function runPreview() {
    const id=++requestId.current
    setBusy(true); setError(null); setPreview(null)
    const form=new FormData()
    form.set('academic_year_id',String(scope.year)); form.set('grade',String(scope.grade)); form.set('subject',scope.subject)
    form.set('exam_name',name); form.set('exam_date',date); form.set('operation',operation)
    form.set('replace_roster',String(replaceRoster))
    if (target === 'auto') form.set('auto_assign','true')
    else if (target) form.set('teaching_class_id',target)
    files.forEach(f => form.append('files',f))
    try { const p=await platformPreview(form); if(id===requestId.current) {setPreview(p);setExamChoice(undefined);setPicks({});setRevise(false);setAcceptRoster(false)} }
    catch(e) { if(id===requestId.current) setError(apiErrorMessage(e)) }
    finally { if(id===requestId.current) setBusy(false) }
  }
  async function runConfirm() {
    if (!preview) return
    const id=++requestId.current
    setBusy(true); setError(null)
    const identity_confirmations: Record<string,number>={}
    const identity_new_aliases: string[]=[]
    Object.entries(picks).forEach(([alias,pick]) => { if(pick==='new') identity_new_aliases.push(alias); else identity_confirmations[alias]=Number(pick) })
    try { const r=await platformConfirm({token:preview.token,exam_choice:examChoice,revise,accept_roster_changes:acceptRoster,identity_confirmations,identity_new_aliases}); if(id===requestId.current){setResult(r);setPreview(null)} }
    catch(e) { if(id===requestId.current) setError(e instanceof ApiV1Error && e.body?.conflicts ? `${apiErrorMessage(e)}：${JSON.stringify(e.body.conflicts)}` : apiErrorMessage(e)) }
    finally { if(id===requestId.current) setBusy(false) }
  }
  const aliases=[...new Set(preview?.identity_candidates.map(c => c.alias) ?? [])]
  const groups=aliases.map(alias => ({alias,name:preview?.identity_candidates.find(c => c.alias===alias)?.name ?? '',options:preview?.identity_candidates.filter(c => c.alias===alias) ?? []}))
  const examUnresolved = preview?.exam_resolution?.requires_choice && examChoice === undefined && preview.exam_resolution.selected == null
  const unresolved=aliases.some(a => picks[a]===undefined)
  const hasFatal=preview?.errors.some(e => !e.message?.startsWith('补录须先存在') || !preview.identity_candidates.some(c => c.alias===e.alias))
  return <div className="space-y-5">
    <div><h1 className="text-2xl font-semibold text-slate-900">教学成绩导入</h1><p className="mt-1 text-sm text-slate-500">学生明细与官方质量分析可一起导入，也可单独上传质量表。</p></div>
    <Card><CardHeader><CardTitle>选择考试与导入范围</CardTitle></CardHeader><CardContent><fieldset disabled={busy} className="space-y-5">
      <PlatformScope value={scope}/>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <label className="text-sm">考试名称<input aria-label="考试名称" className="mt-1 w-full rounded border p-2" value={name} onChange={e=>setName(e.target.value)} placeholder="明确填写本次考试名称"/></label>
        <label className="text-sm">考试日期<input aria-label="考试日期" type="date" className="mt-1 w-full rounded border p-2" value={date} onChange={e=>setDate(e.target.value)}/></label>
        <label className="text-sm">学生明细分班<select aria-label="学生明细分班" className="mt-1 w-full rounded border bg-white py-2 pl-2" value={target} onChange={e=>setTarget(e.target.value)}><option value="auto">按学号自动分到各教学班</option><option value="">仅质量表可不选</option>{scope.classes.map(c=><option key={c.class_id} value={c.class_id}>{c.label}</option>)}</select></label>
        <label className="text-sm">导入类型<select aria-label="导入类型" className="mt-1 w-full rounded border bg-white py-2 pl-2" value={operation} onChange={e=>setOperation(e.target.value)}><option value="initial">首次导入 / 明确修订</option><option value="supplement">{scope.grade===1?'只补学籍排名、年级百分位':'只补等级分'}</option></select></label>
      </div>
      <p className="text-sm text-slate-500">{scope.grade===1?'明细：学号、姓名、分数、学籍排名、年级百分位。排名和百分位可留空后补。':'明细：学号、姓名、原始分数、等级分数。等级分可留空后补。'} 多个教学班的学生合并在一个文件时选「按学号自动分到各教学班」，按各自名册归班。补录不修改原始分，空白保留已有值。</p>
      <ScoreImportTemplates mode="teaching" grade={scope.grade}/>
      <label className="flex items-start gap-2 text-sm text-slate-600"><input type="checkbox" className="mt-1" checked={replaceRoster} onChange={e=>setReplaceRoster(e.target.checked)}/><span>分班已变化：以本次质量表替换所提供类别的对比参照名单。未勾选时保留已有参照并补充新班；其他历史考试不变。</span></label>
      <label className="block rounded-lg border-2 border-dashed border-sky-200 bg-sky-50/40 p-6 text-sm">选择成绩明细、教学班或行政班质量分析（可多选）<input aria-label="选择教学平台成绩文件" className="mt-3 block w-full" type="file" multiple accept=".xlsx" onChange={e=>setFiles(Array.from(e.target.files??[]))}/></label>
      <p className="text-xs text-slate-500">质量表文件名包含「教学班」或「行政班」即可自动识别类别；官方统计值按表格读取，不反算比率。</p>
      {files.length ? <ul className="text-sm text-slate-600">{files.map((f,i)=><li key={`${f.name}-${i}`}>{f.name}</li>)}</ul> : null}
      <Button disabled={busy || !files.length || !scope.year || !scope.subject || !name.trim() || !date} onClick={runPreview}>{busy?'处理中…':'预览解析与差异'}</Button>
    </fieldset></CardContent></Card>
    {error?<p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</p>:null}
    {result?<Card><CardContent className="space-y-3 pt-6"><p role="status" className="font-medium text-emerald-700">导入成功：新增 {result.imported} 条，修订 {result.revised} 条，同值跳过 {result.skipped} 条。</p><Button asChild variant="outline"><Link href={`/compare?ws=teaching&exam_id=${result.exam_id}&grade=${scope.grade}&academic_year_id=${scope.year}`}>查看本次班级对比与成绩</Link></Button></CardContent></Card>:null}
    {preview?<Card><CardHeader><CardTitle>预览与确认</CardTitle></CardHeader><CardContent className="space-y-4">
      <ExamResolutionSelect resolution={preview.exam_resolution} value={examChoice} onChange={setExamChoice} disabled={busy}/>
      <p className="text-sm text-slate-500">预览未写入成绩。高一纳入全部真实班级；高二、高三纳入全部教学班及教师非空的行政班。新增参照不会创建管理班或成员。</p>
      {preview.items.map((f,i)=><div key={i} className="rounded border p-3 text-sm"><strong className="break-all">{f.filename}</strong><span className={f.parsed_ok?'ml-3 text-emerald-700':'ml-3 text-red-700'}>{f.parsed_ok?f.kind==='class_quality'?`${f.class_kind==='teaching'?'教学班质量分析':f.class_kind==='administrative'?'行政班质量分析':'质量分析'} · 纳入 ${f.classes?.length ?? 0} 个班级`:`学生明细 · ${f.row_count} 行`:f.message}</span>
        {f.excluded?.length?<details className="mt-2"><summary>查看排除的行（{f.excluded.length}）</summary><ul>{f.excluded.map((r,j)=><li key={j}>{r.class_label}：{r.reason}</li>)}</ul></details>:null}
        {f.warnings?.map((w,j)=><p key={j} className="mt-1 text-amber-700">{w}</p>)}
      </div>)}
      {preview.errors.length?<div role="alert" className="text-red-700"><p>需先处理以下问题：</p><PlatformChanges rows={preview.errors}/></div>:null}
      {preview.changes.length?<><h2 className="font-medium">字段差异（{preview.changes.length}）</h2><PlatformChanges rows={preview.changes}/></>:<p className="text-sm text-slate-500">未发现字段变更，重复导入将跳过。</p>}
      {groups.length?<IdentityCandidatesPanel groups={groups} picks={picks} onPick={(alias,pick)=>{if(!busy)setPicks(p=>({...p,[alias]:pick}))}}/>:null}
      {preview.new_students.length?<p className="text-sm text-slate-600">首次导入将新建 {preview.new_students.length} 名学生并加入所选教学班；补录不创建学生。</p>:null}
      {preview.roster_changes.length?<label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={acceptRoster} onChange={e=>setAcceptRoster(e.target.checked)}/><span>确认 {preview.roster_changes.length} 项班级对比参照变化：{preview.roster_changes.map(c=>`${c.action==='remove'?'移出':'纳入'} ${c.class_kind==='teaching'?'教学':'行政'}·${c.class_label}`).join('、')}。只用于对比。</span></label>:null}
      {preview.conflicts.length?<label className="flex items-center gap-2 text-sm text-amber-800"><input type="checkbox" checked={revise} onChange={e=>setRevise(e.target.checked)}/>已核对上述 {preview.conflicts.length} 项已有值差异，同意修订</label>:null}
      <Button disabled={busy || !!examUnresolved || !!hasFatal || preview.items.some(f=>!f.parsed_ok) || unresolved || (!!preview.roster_changes.length&&!acceptRoster) || (!!preview.conflicts.length&&!revise)} onClick={runConfirm}>确认整批入库</Button>
    </CardContent></Card>:null}
  </div>
}
