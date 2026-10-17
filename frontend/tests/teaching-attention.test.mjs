import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import ts from 'typescript'
import vm from 'node:vm'

const file = fs.readFileSync('src/lib/attention.ts','utf8')
const compiled = ts.transpileModule(file,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText
const box={exports:{}}
vm.runInNewContext(compiled,box)
test('attention filters preserve server order and upcoming reviews without duplicating people',()=>{
  const rows=[
    {person_id:1,active:true,categories:['learning','homework'],followups:[]},
    {person_id:2,active:false,categories:[],followups:[{id:9}]},
    {person_id:3,active:true,categories:['support'],followups:[]},
  ]
  const ids=tab=>Array.from(box.exports.attentionRows(rows,tab),s=>s.person_id)
  assert.deepEqual(ids('all'),[1,3])
  assert.deepEqual(ids('learning'),[1])
  assert.deepEqual(ids('homework'),[1])
  assert.deepEqual(ids('review'),[2])
  assert.deepEqual(ids('support'),[3])
})
test('teaching dashboard uses scoped actionable card in both exam and empty states',()=>{
  const dashboard=fs.readFileSync('src/components/dashboard/WorkspaceDataDashboard.tsx','utf8')
  const card=fs.readFileSync('src/components/dashboard/TeachingAttention.tsx','utf8')
  assert.match(dashboard,/TeachingAttention/)
  assert.doesNotMatch(dashboard,/a\.score - b\.score/)
  assert.match(card,/cancelled = true/)
  assert.match(card,/查看基线与当前情况/)
  assert.match(card,/evidence_key:action.student.evidence_key/)
  assert.match(card,/\/student\/\$\{student.person_id\}/)
})
