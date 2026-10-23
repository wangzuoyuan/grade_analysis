// 2026-10-21 用户改为当前范围合并学生表，替代此前的分班展示契约；
// 2026-10-22 用户拍板：/compare 两张质量表合并单卡 + 新增各班平均分柱状图 + 样本估算视图退役。
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
      return load(target + (['lib/utils','lib/teaching-score-display','components/scores/band-colors'].includes(target) ? '.ts' : '.tsx'))
    }
    return nativeRequire(id)
  })
  return exports
}
const { TeachingStudentDetails } = load('components/scores/TeachingStudentDetails.tsx')
const { TeachingQualityTables } = load('components/scores/TeachingQualityTables.tsx')
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
  // 2026-10-23 冻结表头 z 层级：冻结列 z-30 必须高于滑动表头 z-20（z 相同按 DOM 序绘制会被盖住）
  assert.match(html,/class="[^"]*sticky left-0 z-30[^"]*"[^>]*>姓名</)
  assert.match(html,/class="[^"]*sticky left-20 z-30[^"]*"[^>]*>班级</)
})

test('高一显示来源学籍排名与百分比，未知年级显示指标并集，HTML 转义',()=>{
  const html=render(TeachingStudentDetails,{grade:1,rows:[{...student,name:'<script>合成</script>'}]})
  assert.match(html,/>学籍排名</);assert.match(html,/>年级百分比</);assert.match(html,/>2\.3%</)
  assert.doesNotMatch(html,/>等级分<|>等级<|<script>/)
  const unknown=render(TeachingStudentDetails,{grade:null,rows:[student]})
  for(const h of ['等级分','等级','学籍排名','年级百分比']) assert(unknown.includes(`>${h}<`))
})

const cls=(label,kind='administrative',source='official')=>({id:kind+':'+label,class_kind:kind,class_label:label,teacher_name:null,class_type:null,source,raw_avg:123,grade_avg:55,managed_class_id:kind==='teaching'?7:null,
  metrics:{expected_count:0,actual_count:0,raw_avg:0,grade_avg:55,stddev:0,chinese_avg:91.2,math_avg:0,english_avg:null,main_three_avg:280.7,bands:[{label:'C+',count:0,ratio:.833}]},raw_rank:1,raw_diff:null})
const comparison={exam:{subject:'物理'},metadata:{data_revision:1},overall:{raw:null,grade:null},warnings:[],classes:[cls('10'),cls('2'),cls('合成A2','teaching'),cls('合成A10','teaching'),cls('样本','teaching','estimated')]}

test('合并单卡质量表（2026-10-22）：行政/教学同表带类型徽标、自然序、官方值独立于估算，空教师和零实考不隐藏；语数英三大门四列已删',()=>{
  const html=render(TeachingQualityTables,{data:comparison,grade:2})
  assert.equal((html.match(/<table /g)??[]).length,1,'行政班与教学班不再分两张卡')
  for(const h of ['班级质量分析','行政班与教学班合并展示','应考','实考','原始','等级','A+','E'])assert(html.includes(h))
  assert.doesNotMatch(html,/行政班质量分析|教学班质量分析/,'旧两卡标题不得残留')
  assert.equal((html.match(/>行政班<\/span>/g)??[]).length,2,'行政班徽标 ×2（10、2）')
  assert.equal((html.match(/>教学班<\/span>/g)??[]).length,2,'教学班徽标 ×2（合成A2、合成A10）')
  assert.doesNotMatch(html,/>语文<|>数学<|>英语<|>三大门<|>280\.7<|>91\.2</,'2026-10-21 用户拍板：两表删除语文/数学/英语/三大门列')
  assert.match(html,/colspan="22"/i)
  assert(html.indexOf('>2<span')<html.indexOf('>10<span'),'班级列自然序 2 < 10')
  assert(html.indexOf('>合成A2<span')<html.indexOf('>合成A10<span'),'教学班合成名自然序 A2 < A10')
  assert.match(html,/aria-label="质量分析表"/)
  assert.match(html,/>83\.3%/)
  assert.doesNotMatch(html,/>123<|>样本<|学生成绩明细|均差/)
  assert.equal((html.match(/>0<\/td>/g)??[]).length,20,'应考/实考/原始/标准差 + C+ 人数共 5 个零 × 4 行（估算班不入表）；缺失等级人数为“—”')
})

test('补充对比卡与 TeachingQualitySupplement 已整体删除（2026-10-21 用户拍板）',()=>{
  const compare=readFileSync(path.join(root,'components/scores/TeachingPlatformCompare.tsx'),'utf8')
  assert.doesNotMatch(compare,/TeachingQualitySupplement|补充对比|setBasis|总体均分|均差|data\.warnings/)
  assert.match(compare,/return <TeachingQualityTables data=\{data\} grade=\{grade\} \/>/,'考试详情只渲染两张质量表')
  const tables=readFileSync(path.join(root,'components/scores/TeachingQualityTables.tsx'),'utf8')
  assert.doesNotMatch(tables,/TeachingQualitySupplement|sourceLabels|chinese_avg|math_avg|english_avg|main_three_avg|averages/)
})

const bandCls=(label,kind,counts,avg=70)=>({id:kind+':'+label,class_kind:kind,class_label:label,teacher_name:null,class_type:null,source:'official',raw_avg:avg,grade_avg:null,managed_class_id:kind==='teaching'?7:null,
  metrics:{expected_count:40,actual_count:40,raw_avg:avg,stddev:5,bands:Object.entries(counts).map(([l,count])=>({label:l,count,ratio:count/40}))},raw_rank:1})
const banded={...comparison,classes:[
  bandCls('1','administrative',{'A+':1,A:8,'B+':10}),
  bandCls('合成A2','teaching',{A:3,'B+':8}),
  bandCls('合成A10','teaching',{'A+':2,A:5,'B+':7},60),
]}

test('质量表卡内各班各等级人数堆叠条：每班一行、宽度按人数占比、图例只含非空等级、多班附合计行（均分按实考加权）',()=>{
  const html=render(TeachingQualityTables,{data:banded,grade:2})
  // 合并单卡：行政班 1 行 + 教学班 2 行 + 合计 = 4 条轨道
  assert.equal((html.match(/rounded-full bg-slate-100/g)??[]).length,4)
  assert.match(html,/title="1（19 人）"/);assert.match(html,/title="合成A2（11 人）"/);assert.match(html,/title="合计（44 人）"/)
  assert.match(html,/width:36\.3636/,'合计行 A 等级 16/44');assert.match(html,/width:56\.8181/,'合计行 B+ 25/44')
  assert.match(html,/width:72\.7272/,'合成A2 行 B+ 8/11')
  assert.doesNotMatch(html,/width:0%/,'零人数等级不占段')
  // 图例：单卡 A+/A/B+ 三项（B、B-…E 全零不进图例）
  assert.equal((html.match(/h-2 w-2 rounded-sm/g)??[]).length,3)
  assert.doesNotMatch(html,/>B-<\/span>|>E<\/span>/)
  // 右侧均分：班级行取原始均分，合计行按实考人数加权 (70×40+70×40+60×40)/120=66.67
  assert.match(html,/>均 70<\/span>/);assert.match(html,/>均 60<\/span>/);assert.match(html,/>均 66\.67<\/span>/)
  assert.doesNotMatch(html,/人 · /,'悬浮内容不在静态标记里（Radix Tooltip 关闭态不渲染）')
  // 表格仍在堆叠条下方
  assert(html.indexOf('rounded-full bg-slate-100')<html.indexOf('<table '))
})

test('堆叠条交互契约：Radix 悬浮 150ms、同等级跨行联动高亮、白色细分隔线、Viridis 按等级总数取色',()=>{
  const tables=readFileSync(path.join(root,'components/scores/TeachingQualityTables.tsx'),'utf8')
  assert.match(tables,/<TooltipProvider delayDuration=\{150\}>/)
  assert.match(tables,/<TooltipTrigger asChild>\s*<div\s+className=\{cn\(/)
  assert.match(tables,/\[&>\*\+\*\]:border-l \[&>\*\+\*\]:border-white\/90/)
  assert.match(tables,/hoverBand != null && hoverBand !== i && 'opacity-25'/)
  assert.match(tables,/hoverBand === i && 'ring-1 ring-inset ring-slate-900\/50'/)
  assert.match(tables,/onMouseEnter=\{\(\) => setHoverBand\(i\)\}/);assert.match(tables,/onMouseLeave=\{\(\) => setHoverBand\(null\)\}/)
  assert.match(tables,/bandBarColor\(i, labels\.length\)/,'颜色按等级在全序列中的位置取，A+ 恒为亮端')
  assert.doesNotMatch(tables,/INFERNO_ANCHORS/,'教学侧维持 Viridis')
  assert.match(tables,/toFixed\(1\)\}%/)
})

test('等级人数全空或未导入时不出堆叠条',()=>{
  const html=render(TeachingQualityTables,{data:comparison,grade:2})
  assert.doesNotMatch(html,/rounded-full bg-slate-100|h-2 w-2 rounded-sm/)
  const noBands=render(TeachingQualityTables,{data:{...comparison,classes:comparison.classes.map(c=>({...c,metrics:{...c.metrics,bands:undefined}}))},grade:2})
  assert.doesNotMatch(noBands,/rounded-full bg-slate-100/)
  assert.match(noBands,/colspan="22"/i)
})

test('各班平均分柱状图（2026-10-22 用户拍板）：原始/等级双柱、我教班级主题色高亮、其余灰色半透明',()=>{
  const tables=readFileSync(path.join(root,'components/scores/TeachingQualityTables.tsx'),'utf8')
  assert.match(tables,/function ClassAvgBars\(\{ rows, grade \}/)
  assert.match(tables,/c\.is_managed \?\? c\.managed_class_id != null/,'我教班级按后端 is_managed 判定（教学班 label 命中或行政班任课教师同名），兼容旧快照 managed_class_id')
  assert.match(tables,/#1f7fd6/,'我教班级主题蓝');assert.match(tables,/#94a3b8/,'其他班级灰色')
  assert.equal((tables.match(/fill=\{d\.mine \? MINE_BAR_COLOR : OTHER_BAR_COLOR\} fillOpacity=\{d\.mine \? 0\.9 : 0\.35\}/g)??[]).length,2,'原始分/等级分两个 Bar 都逐格填 Cell')
  assert.match(tables,/name="原始分均分"/);assert.match(tables,/name="等级分均分"/)
  assert.match(tables,/maxBarSize=\{22\}/);assert.match(tables,/radius=\{\[3, 3, 0, 0\]\}/)
  assert.match(tables,/interval=\{0\}[\s\S]*?angle=\{-20\}[\s\S]*?textAnchor="end"/,'X 轴班级名斜排不省略')
  assert.match(tables,/\{grade !== 1 && <Legend/,'高一不渲染图例')
  assert.match(tables,/grade_avg: grade === 1 \? null : c\.metrics\.grade_avg \?\? null/,'高一无等级分柱')
  // 渲染层面：柱状图卡在堆叠条与表格之上；全部均分为空时整块不渲染
  const html=render(TeachingQualityTables,{data:banded,grade:2})
  for(const s of ['各班平均分对比','主题色为我教的教学班，灰色为其他班级','h-72']) assert(html.includes(s),`缺 ${s}`)
  assert(html.indexOf('各班平均分对比')<html.indexOf('rounded-full bg-slate-100'),'柱状图在堆叠条上方')
  const allNull=render(TeachingQualityTables,{data:{...comparison,classes:comparison.classes.map(c=>({...c,metrics:{...c.metrics,raw_avg:null,grade_avg:null}}))},grade:2})
  assert(!allNull.includes('各班平均分对比'),'全部均分为空不出柱状图')
})

test('样本估算视图退役（2026-10-22 用户拍板）：compare 页只渲染官方质量分析，无旧模板入口',()=>{
  const page=readFileSync(path.join(root,'app/compare/page.tsx'),'utf8')
  assert.doesNotMatch(page,/样本估算|LegacyComparePage|fetchTeachingClassCompare|fetchTeachingStats|deriveRanks|SortableHead|listExams/,'样本对比视图及其取数/排序代码整体删除')
  assert.match(page,/<TeachingPlatformCompare \/>/,'teaching 模式直接渲染官方质量分析主视图')
  assert.match(page,/router\.replace\('\/homeroom\/compare'\)/,'homeroom 重定向保留')
  const compare=readFileSync(path.join(root,'components/scores/TeachingPlatformCompare.tsx'),'utf8')
  assert.doesNotMatch(compare,/onLegacy|旧模板/,'onLegacy prop 与旧模板入口按钮删除')
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
  assert.doesNotMatch(compare,/platformStudents|学生成绩明细|TeachingQualitySupplement|补充对比/)
})
