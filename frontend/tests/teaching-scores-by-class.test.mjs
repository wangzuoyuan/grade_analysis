// 2026-10-21 用户改为当前范围合并学生表，替代此前的分班展示契约。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'
const nativeRequire = createRequire(import.meta.url)
const root = path.resolve(fileURLToPath(new URL('../src', import.meta.url)))
const cache = new Map()
function load(file) {
  const resolved = path.join(root, file)
  if (cache.has(resolved)) return cache.get(resolved)
  const exports = {}
  cache.set(resolved, exports)
  const source = readFileSync(resolved, 'utf8')
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  }}).outputText
  new Function('exports', 'require', code)(exports, id => {
    if (id === 'next/link') return { __esModule: true, default: ({children,...props}) => React.createElement('a', props, children) }
    if (id === '@/lib/workspace') return { workspaceHref: (url, mode) => `${url}?ws=${mode}` }
    if (id.startsWith('@/') || id.startsWith('.')) {
      const target = id.startsWith('@/') ? id.slice(2) : path.relative(root, path.resolve(path.dirname(resolved), id))
      return load(target + (['lib/utils','lib/teaching-score-display'].includes(target) ? '.ts' : '.tsx'))
    }
    return nativeRequire(id)
  })
  return exports
}
const { TeachingStudentDetails } = load('components/scores/TeachingStudentDetails.tsx')
const { TeachingQualityTables, TeachingQualitySupplement } = load('components/scores/TeachingQualityTables.tsx')
const { percentileTrendValue } = load('lib/teaching-score-display.ts')
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props))
const student = {person_id:7,name:'合成甲',class_label:'合成B',score:0,grade_score:40,grade_label:'E',rank:3,xueji_rank:12,grade_percentile:.023,source_domain:'teaching'}

test('合并学生明细使用后端顺序和名次，零值、姓名链接及缺考正确，无来源列', () => {
  const html=render(TeachingStudentDetails,{grade:2, rows:[student,{...student,person_id:8,name:'合成乙',score:null,grade_score:null,grade_label:null,rank:null}]})
  for(const h of ['姓名','班级','原始分','等级分','等级','范围内名次']) assert(html.includes(`>${h}<`))
  assert.doesNotMatch(html,/来源|教学域|学籍排名|年级百分比/)
  assert.match(html,/href="\/student\/7\?ws=teaching"/)
  assert(html.indexOf('合成甲')<html.indexOf('合成乙'))
  assert.match(html,/>0<\/td>/)
  assert.match(html,/>3<\/td>/,'直接呈现给定的后端名次')
  assert.match(html,/>—<\/td>/)
})

test('高一显示来源学籍排名与百分比，未知年级显示指标并集，HTML 转义',()=>{
  const html=render(TeachingStudentDetails,{grade:1,rows:[{...student,name:'<script>合成</script>'}]})
  assert.match(html,/>学籍排名</);assert.match(html,/>年级百分比</);assert.match(html,/>2\.3%</)
  assert.doesNotMatch(html,/>等级分<|>等级<|<script>/)
  const unknown=render(TeachingStudentDetails,{grade:null,rows:[student]})
  for(const h of ['等级分','等级','学籍排名','年级百分比']) assert(unknown.includes(`>${h}<`))
})

const cls=(label,kind='administrative',source='official')=>({id:kind+':'+label,class_kind:kind,class_label:label,teacher_name:null,class_type:null,source,raw_avg:123,grade_avg:55,
  metrics:{expected_count:0,actual_count:0,raw_avg:0,grade_avg:55,stddev:0,chinese_avg:91.2,math_avg:0,english_avg:null,main_three_avg:280.7,bands:[{label:'C+',count:0,ratio:.833}]},raw_rank:1,raw_diff:null})
const comparison={exam:{subject:'物理'},metadata:{data_revision:1},overall:{raw:null,grade:null},warnings:[],classes:[cls('10'),cls('2'),cls('合成A2','teaching'),cls('合成A10','teaching'),cls('样本','teaching','estimated')]}

test('两张完整质量表分组表头、自然序及官方值独立于估算，空教师和零实考不隐藏',()=>{
  const html=render(TeachingQualityTables,{data:comparison,grade:2})
  assert.equal((html.match(/<table /g)??[]).length,2)
  for(const h of ['行政班质量分析','教学班质量分析','应考','实考','语文','数学','英语','三大门','A+','E'])assert(html.includes(h))
  assert.match(html,/colspan="22"/i)
  assert(html.indexOf('>2</td>')<html.indexOf('>10</td>'))
  assert(html.indexOf('合成A2</td>')<html.indexOf('合成A10</td>'))
  assert.match(html,/>83\.3%</);assert.match(html,/>280\.7</)
  assert.doesNotMatch(html,/>123<|>样本<|学生成绩明细|均差/)
  assert.equal((html.match(/>0<\/td>/g)??[]).length,24)
})

test('补充对比只保留一处总体、均分、名次与均差，无法核实总体时说明缺失',()=>{
  const html=render(TeachingQualitySupplement,{data:comparison,basis:'raw'})
  assert.match(html,/总体均分/);assert.match(html,/覆盖范围尚不能合并/);assert.match(html,/>名次</);assert.match(html,/>均差</)
  assert.match(html,/学生样本估算/);assert.match(html,/>123</)
  const available=render(TeachingQualitySupplement,{data:{...comparison,overall:{raw:{value:65,method:'matching_aggregate'}}},basis:'raw'})
  assert.match(available,/>65</);assert.match(available,/范围匹配的官方汇总/)
})

test('高一质量表保留原分数段与单科指标',()=>{
  const html=render(TeachingQualityTables,{data:comparison,grade:1})
  assert.match(html,/单科总分/);assert.match(html,/最高分/);assert.match(html,/最低分/)
  assert.doesNotMatch(html,/>语文<|>等级<|>A\+<|三大门/)
})

test('趋势仅使用导入百分比，0分及0%有效，缺考和无百分比均断开',()=>{
  assert.equal(percentileTrendValue({score:90,grade_percentile:.023}),2.3)
  assert.equal(percentileTrendValue({score:0,grade_percentile:0}),0)
  assert.equal(percentileTrendValue({score:1,grade_percentile:1}),100)
  for(const p of [null,undefined,NaN,Infinity,-.1,1.1])assert.equal(percentileTrendValue({score:90,grade_percentile:p}),null)
  assert.equal(percentileTrendValue({score:null,grade_percentile:.1}),null)
  const page=readFileSync(path.join(root,'app/student/[id]/page.tsx'),'utf8')
  assert.match(page,/domain=\{\[0, 100\]\} reversed/)
  assert.match(page,/ticks=\{\[0, 25, 50, 75, 100\]\} interval=\{0\}/)
  assert.match(page,/connectNulls=\{false\}/)
  assert.match(page,/value: percentileTrendValue\(p\)/)
  assert.doesNotMatch(page,/value: metricValue\(p\)/)
})

test('成绩页去除分班计算与重复对比，对比页不再读取或显示学生明细',()=>{
  const page=readFileSync(path.join(root,'components/scores/TeachingScores.tsx'),'utf8')
  assert.match(page,/<TeachingStudentDetails rows=\{students\} grade=\{examGrade\}/)
  assert.doesNotMatch(page,/classGroups|fetchTeachingClassCompare|ClassComparePanel/)
  const compare=readFileSync(path.join(root,'components/scores/TeachingPlatformCompare.tsx'),'utf8')
  assert.doesNotMatch(compare,/platformStudents|学生成绩明细/)
})
