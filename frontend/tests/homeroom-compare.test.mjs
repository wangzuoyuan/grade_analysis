// 班主任工作台「班级对比」页契约测试（契约 docs/contracts/p3-imports-analysis.md §2.1 class-averages）。
// 风格沿用 tests/scores-p3.test.mjs：直接读源码断言关键结构，不启动浏览器。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const apiV1 = readFileSync(new URL('../src/lib/api-v1.ts', import.meta.url), 'utf8')
const sidebar = readFileSync(new URL('../src/components/layout/Sidebar.tsx', import.meta.url), 'utf8')
const topbar = readFileSync(new URL('../src/components/layout/Topbar.tsx', import.meta.url), 'utf8')
const comparePage = readFileSync(new URL('../src/app/compare/page.tsx', import.meta.url), 'utf8')
const homeroomComparePage = readFileSync(new URL('../src/app/homeroom/compare/page.tsx', import.meta.url), 'utf8')
const component = readFileSync(new URL('../src/components/scores/HomeroomCompare.tsx', import.meta.url), 'utf8')

test('侧栏有班主任「班级对比」入口（仅班主任域，插在成绩分析之后）', () => {
  const idx = sidebar.indexOf("href: '/homeroom/compare'")
  assert.ok(idx > 0, '侧栏应有 /homeroom/compare 条目')
  const ctx = sidebar.slice(idx, idx + 320)
  assert.match(ctx, /label: '班级对比'/, '条目文案为「班级对比」')
  assert.match(ctx, /match: \(p\) => p\.startsWith\('\/homeroom\/compare'\)/, '条目匹配 /homeroom/compare 前缀')
  assert.match(ctx, /modes: \['homeroom'\]/, '条目仅班主任域可见')
  assert.match(ctx, /icon: BarChart3/, '沿用 BarChart3 图标')
  const scoresIdx = sidebar.indexOf("href: '/homeroom/scores'")
  assert.ok(scoresIdx > 0 && scoresIdx < idx, '班主任班级对比应插在「成绩分析」条目之后')
  // 教学侧 /compare 条目不动
  const teachIdx = sidebar.indexOf("href: '/compare'")
  assert.ok(teachIdx > 0, '教学侧 /compare 条目保留')
  const teachCtx = sidebar.slice(teachIdx, teachIdx + 260)
  assert.match(teachCtx, /modes: \['teaching'\]/, '教学侧 /compare 仍仅教学域可见')
  // 面包屑：compare 路径段有「班级对比」标签（两端共用）
  assert.match(topbar, /compare: '班级对比'/, 'Topbar compare 段标签应为「班级对比」')
})

test('班主任班级对比页存在并渲染 HomeroomCompare', () => {
  assert.match(homeroomComparePage, /HomeroomCompare/, '页面应渲染 HomeroomCompare 组件')
  assert.match(homeroomComparePage, /班级对比 · 班主任工作台/, 'metadata 标题应为「班级对比 · 班主任工作台」')
  assert.match(homeroomComparePage, /Suspense/, '页面应有 Suspense 边界（useSearchParams 预渲染）')
})

test('组件读取官方班级均分表与 homeroom 考试清单，绝不触教学域', () => {
  assert.match(component, /fetchHomeroomClassAverages/, '必须读取官方班级均分表端点')
  assert.match(component, /listExams\('homeroom'/, '必须按 homeroom 模式拉考试清单')
  assert.doesNotMatch(component, /teaching/, '班主任班级对比组件不得出现任何教学域请求')
  assert.match(apiV1, /current_class_num\?: number \| null/, 'api-v1 类型应含 current_class_num 可空字段')
})

test('柱状图 Y 轴上限取整消除小数顶刻度', () => {
  assert.match(
    component,
    /Math\.ceil\(\(dataMax \+ 20\) \/ 10\) \* 10/,
    'Y 轴上限应为 Math.ceil((dataMax + 20) / 10) * 10',
  )
  assert.match(component, /domain=\{\[0, yMax\]\}/, 'YAxis domain 应使用取整后的上限')
})

test('本班高亮使用 current_class_num，本班柱不透明其他班灰色半透明', () => {
  assert.match(component, /current_class_num/, '必须消费 current_class_num')
  assert.match(component, /row\.class_num === currentClassNum/, '本班判断须按 class_num 与 current_class_num 相等')
  assert.match(component, /entry\.isCurrent \? CURRENT_BAR_COLOR : OTHER_BAR_COLOR/, '柱色须逐格区分本班与其他班')
  assert.match(component, /formatClassLabel/, '本班徽章须用规范年级标签（如「高二6班」），不得裸拼数字年级')
})

test('缺数据显示「—」不转 0；口径由接口返回动态生成', () => {
  assert.match(component, /value\.toFixed\(1\) : '—'/, '均分缺值单元格应渲染「—」而非折算 0')
  assert.match(component, /diff == null \? \(\s*'—'/, '较年级均差缺值单元格应渲染「—」')
  assert.doesNotMatch(component, /\?\? 0/, '不得把缺值转 0')
  assert.match(component, /totals\.includes\('主三门'\)/, '默认口径优先「主三门」')
  assert.match(component, /averages\?\.total_types/, 'total 口径下拉须来自接口返回')
  assert.match(component, /averages\?\.subjects/, '单科口径下拉须来自接口返回')
  // 总分口径名次直接用后端官方排名；单科口径前端同分同名次计算
  assert.match(component, /total_ranks\[key\]/, '总分口径名次须直接用后端官方排名')
  assert.match(component, /deriveSubjectRanks/, '单科口径名次须前端按值降序同分同名次计算')
})

test('/compare 在 homeroom 模式直达 /homeroom/compare，teaching 行为不变', () => {
  assert.match(comparePage, /router\.replace\('\/homeroom\/compare'\)/, 'homeroom 模式必须 replace 到 /homeroom/compare')
  assert.match(comparePage, /if \(mode === 'homeroom'\) router\.replace/, '跳转须以 homeroom 模式为条件')
  assert.match(comparePage, /fetchTeachingClassCompare/, 'teaching 模式的教学班对比取数必须保留')
  assert.match(comparePage, /listExams\('teaching'/, 'teaching 模式的考试清单取数必须保留')
  assert.doesNotMatch(comparePage, /WorkspaceSwitcher/, 'homeroom 分支不再渲染切换指引卡')
})
