// P2-C3 诊断版学生报告契约测试（契约 docs/diagnosis-roadmap/p2-contracts.md §4/§6）。
// 风格沿用 p4-report.test.mjs / diagnosis-card.test.mjs：readFileSync+assert 读源码断言。
//
// 覆盖点：
// - 同源取数：诊断报告页只走 GET /{域}/diagnosis/report（B1/B2 同源生成的四节+建议），
//   前端绝不复算任何指标/类型/建议；
// - 三层分节标注：事实/规则判断/建议 徽标 + layer_legend 图例渲染；
// - 建议纪律：generated=true、「值得关注」措辞与免责说明来自响应、教师编辑仅前端本地
//   （无任何 POST/PATCH 写入调用）、编辑区 print:hidden 不影响打印正文；
// - 打印友好：沿用现有报告打印样式（window.print / print:hidden / 页脚 print:fixed）；
// - 双工作台：homeroom / teaching 两个路由页固定各自域；
// - 事实版报告保留不动：事实版页面不 import 诊断组件（并列入口，互不改动）。
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import test from 'node:test'

const reportView = readFileSync(
  new URL('../src/components/student/DiagnosisReport.tsx', import.meta.url),
  'utf8',
)
const homeroomPage = existsSync(
  new URL('../src/app/homeroom/students/[id]/diagnosis-report/page.tsx', import.meta.url),
)
  ? readFileSync(
      new URL('../src/app/homeroom/students/[id]/diagnosis-report/page.tsx', import.meta.url),
      'utf8',
    )
  : ''
const teachingPage = existsSync(
  new URL('../src/app/student/[id]/diagnosis-report/page.tsx', import.meta.url),
)
  ? readFileSync(
      new URL('../src/app/student/[id]/diagnosis-report/page.tsx', import.meta.url),
      'utf8',
    )
  : ''

test('P2-C3：诊断报告同源取数只走 /{域}/diagnosis/report 端点（四节+建议由后端生成）', () => {
  assert.match(
    reportView,
    /\/api\/v1\/\$\{mode\}\/diagnosis\/report/,
    '取数路径必须是 GET /api/v1/{mode}/diagnosis/report（契约 §4）',
  )
  assert.match(reportView, /person_id/, '查询参数含 person_id')
  assert.match(reportView, /academic_year_id/, '查询参数含可选 academic_year_id')
  assert.match(reportView, /p2-v1|calc_version/, '展示口径版本（p2-v1）')
  // 前端不复算：组件内不得出现对指标/类型的重新计算（只允许展示辅助）
  assert.doesNotMatch(reportView, /classifyStudent|studentFeatures\(/, '不得在前端复算类型/特征')
})

test('P2-C3：事实/规则判断/建议三层分节标注齐备（layer 徽标 + layer_legend 图例）', () => {
  assert.match(reportView, /LAYER_LABELS/, '层级中文标签映射')
  for (const label of ['事实', '规则判断', '建议']) {
    assert.match(reportView, new RegExp(label), `三层标注含「${label}」`)
  }
  assert.match(reportView, /layer_legend/, '渲染响应自带的 layer_legend 图例')
  assert.match(reportView, /LAYER_ORDER = \['fact', 'rule_judgment', 'suggestion'\]/, '三层顺序固定')
  assert.match(reportView, /SectionHeading/, '每节标题携带层级徽标的分节组件')
  for (const section of ['学习状态', '各科表现', '作业行为', '教师观察摘录', '值得关注事项']) {
    assert.match(reportView, new RegExp(section), `四节+建议标题齐备：${section}`)
  }
  // 响应契约类型逐字段（三层 layer 字段在类型声明中）
  assert.match(reportView, /layer: string/, '各节携带 layer 标注字段')
})

test('P2-C3：建议节——generated=true、「值得关注」措辞与免责说明、based_on 依据展示', () => {
  assert.match(reportView, /generated/, '建议节展示 generated 标注')
  assert.match(reportView, /String\(sug\.generated\)/, 'generated 以布尔字面量展示')
  assert.match(reportView, /值得关注/, '建议措辞「值得关注」由响应提供并展示')
  assert.match(reportView, /disclaimer/, '免责说明原样展示（不构成因果结论或提分保证）')
  assert.match(reportView, /based_on/, '每条建议展示 based_on 依据（可追溯）')
  assert.match(reportView, /sug\.items\.map/, '建议逐条渲染（1–3 条由后端保证）')
})

test('P2-C3：教师编辑仅前端本地不入库（无任何写入请求；编辑区打印隐藏）', () => {
  // 编辑只写本地 state：组件内不得出现 POST/PATCH/PUT/fetch 写入调用
  assert.doesNotMatch(reportView, /method:\s*'(POST|PATCH|PUT)'/, '不得向后端写入建议')
  assert.doesNotMatch(reportView, /createNote|updateNote|saveSuggestion/, '不得调用任何保存接口')
  assert.match(reportView, /drafts/, '编辑文本只存本地 drafts state')
  assert.match(reportView, /textarea/, '编辑用本地 textarea')
  assert.match(reportView, /恢复原文/, '可一键恢复生成原文')
  assert.match(reportView, /不入库/, '界面注明「不入库」纪律')
  assert.match(reportView, /print:hidden/, '编辑/操作控件打印隐藏（不影响打印正文）')
})

test('P2-C3：打印友好沿用现有报告样式基线', () => {
  assert.match(reportView, /window\.print\(\)/, '打印按钮')
  assert.match(reportView, /max-w-3xl/, '与事实版报告一致的白底窄栏版式')
  assert.match(reportView, /print:p-0/, '打印去页边距（沿用事实版报告样式）')
  assert.match(reportView, /print:fixed print:bottom-2/, '页脚固定（沿用事实版报告样式）')
})

test('P2-C3：缺失纪律（缺考/缺输入显示「—」不转 0）', () => {
  assert.match(reportView, /const DASH = '—'/, '缺失值显示「—」')
  assert.doesNotMatch(reportView, /\?\? 0|\|\| 0/, '不得把缺失值折算为 0')
  assert.match(reportView, /missing_reason/, '缺失原因如实展示')
})

test('P2-C3：双工作台路由页固定各自域（范围与 P1 一致）', () => {
  assert.ok(homeroomPage.length > 0, '班主任域路由页存在：/homeroom/students/[id]/diagnosis-report')
  assert.match(homeroomPage, /mode="homeroom"/, '班主任页固定 homeroom 域')
  assert.match(homeroomPage, /DiagnosisReportView/, '渲染共享诊断报告组件')
  assert.ok(teachingPage.length > 0, '教学域路由页存在：/student/[id]/diagnosis-report')
  assert.match(teachingPage, /mode="teaching"/, '教学页固定 teaching 域（仅任教学科）')
  assert.match(teachingPage, /DiagnosisReportView/, '渲染共享诊断报告组件')
})

test('P2-C3：事实版报告保留不动（事实版页面不依赖诊断组件，并列入口）', () => {
  const homeroomFact = readFileSync(
    new URL('../src/app/homeroom/students/[id]/report/page.tsx', import.meta.url),
    'utf8',
  )
  const teachingFact = readFileSync(
    new URL('../src/app/student/[id]/report/page.tsx', import.meta.url),
    'utf8',
  )
  assert.doesNotMatch(homeroomFact, /DiagnosisReport/, '班主任事实版报告页未被诊断版改动')
  assert.doesNotMatch(teachingFact, /DiagnosisReport/, '教学事实版报告页未被诊断版改动')
})
