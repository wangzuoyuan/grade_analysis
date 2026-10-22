// 问题 3（前端）契约测试：考试标识统一 exam_key（"event:N" 稳定编号）定位、
// exam_name/日期展示。背景：ExamSelect 的 option value 已是 exam_key ?? exam_name，
// 但部分页面的初始 state 或取数仍用 exam_name —— 下拉初值空白、同名不同场考试
// 无法区分。本文件锁定各页面的 exam_key 口径，防止回退。
// 风格沿用 tests/homeroom-compare.test.mjs：读源码字符串断言，不启动浏览器。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const examSelect = readFileSync(new URL('../src/components/scores/ExamSelect.tsx', import.meta.url), 'utf8')
const shared = readFileSync(new URL('../src/components/scores/shared.ts', import.meta.url), 'utf8')
const homeroomCompare = readFileSync(new URL('../src/components/scores/HomeroomCompare.tsx', import.meta.url), 'utf8')
const dashboard = readFileSync(new URL('../src/components/dashboard/WorkspaceDataDashboard.tsx', import.meta.url), 'utf8')
const studentPage = readFileSync(new URL('../src/app/student/page.tsx', import.meta.url), 'utf8')
const comparePage = readFileSync(new URL('../src/app/compare/page.tsx', import.meta.url), 'utf8')
const homeroomScores = readFileSync(new URL('../src/components/scores/HomeroomScores.tsx', import.meta.url), 'utf8')
const teachingScores = readFileSync(new URL('../src/components/scores/TeachingScores.tsx', import.meta.url), 'utf8')
const apiV1 = readFileSync(new URL('../src/lib/api-v1.ts', import.meta.url), 'utf8')

test('ExamSelect option value 用 exam_key ?? exam_name（稳定编号优先）', () => {
  assert.match(examSelect, /value=\{e\.exam_key \?\? e\.exam_name\}/, '选项 value 必须是 exam_key ?? exam_name')
  assert.match(apiV1, /exam_key\?: string/, 'ExamSummary 类型须含可选 exam_key')
})

test('HomeroomCompare 初始选中按 exam_key 定位，展示用人类可读考试名', () => {
  // 初始 state：?exam= 匹配 key 或名称，选中值统一 canonical 化为 exam_key ?? exam_name
  assert.match(
    homeroomCompare,
    /\(exam\.exam_key \?\? exam\.exam_name\) === requestedExam \|\| exam\.exam_name === requestedExam/,
    'requestedExam 必须同时按 exam_key 与 exam_name 匹配',
  )
  assert.match(
    homeroomCompare,
    /\?\.exam_key \?\? sorted\[0\]\.exam_key \?\? sorted\[0\]\.exam_name/,
    '选中值必须 canonical 化为 exam_key ?? exam_name（默认最新一场同口径）',
  )
  // 取数：均分表按 selectedExam（键）请求
  assert.match(homeroomCompare, /fetchHomeroomClassAverages\(selectedExam/, '均分表按 selectedExam（键口径）请求')
  // 展示：卡片描述不得直接输出内部编号
  assert.match(homeroomCompare, /selectedExamLabel/, '应有展示用的 selectedExamLabel 反查')
  assert.match(
    homeroomCompare,
    /exam\.exam_key \?\? exam\.exam_name\) === selectedExam\)\?\.exam_name \?\? selectedExam/,
    '展示名必须从考试清单反查 exam_name，回退 selectedExam',
  )
  assert.doesNotMatch(
    homeroomCompare,
    /\{selectedExam\} · 来自官方班级均分表/,
    '卡片描述不得直接渲染 selectedExam（可能是 event:N 内部编号）',
  )
})

test('WorkspaceDataDashboard 取数与趋势点跳转用 exam_key，趋势轴展示保留考试名', () => {
  assert.match(dashboard, /function examKeyOf\(exam: ExamSummary\): string/, '应有统一的 examKeyOf 辅助')
  assert.match(dashboard, /return exam\.exam_key \?\? exam\.exam_name/, 'examKeyOf = exam_key ?? exam_name')
  // 各分析端点按键取数
  assert.match(dashboard, /fetchHomeroomStats\(examKeyOf\(latest\)/, 'homeroom stats 按 examKeyOf 取数')
  assert.match(dashboard, /fetchTeachingStats\(examKeyOf\(latest\)/, 'teaching stats 按 examKeyOf 取数')
  assert.match(dashboard, /fetchHomeroomFocus\(examKeyOf\(latest\)/, 'homeroom focus 按 examKeyOf 取数')
  assert.match(dashboard, /fetchTeachingClassCompare\(examKeyOf\(latest\)/, 'teaching compare 按 examKeyOf 取数')
  assert.match(dashboard, /fetchHomeroomStats\(examKeyOf\(exam\)/, '历史趋势各场同样按键取数')
  assert.doesNotMatch(dashboard, /fetchHomeroomStats\(latest\.exam_name/, '不得再用 exam_name 定位 stats')
  assert.doesNotMatch(dashboard, /fetchTeachingStats\(latest\.exam_name/, '不得再用 exam_name 定位 stats')
  // 趋势数据：examKey 跳转 / exam 名展示
  assert.match(dashboard, /examKey: examKeyOf\(exam\)/, '趋势点应携带 examKey')
  assert.match(dashboard, /exam: exam\.exam_name, examKey: examKeyOf\(exam\)/, '展示名保留 exam_name、定位另带 examKey')
  assert.match(dashboard, /onOpen\(payload\.examKey\)/, '趋势圆点点击跳转用 examKey')
  assert.match(dashboard, /payload\?\.examKey/, '图表 onClick 跳转用 examKey')
  assert.match(dashboard, /labelFormatter=\{\(_, payload\) => payload\?\.\[0\]\?\.payload\?\.exam \?\? ''\}/, 'tooltip 标签仍展示考试名')
})

test('student 搜索页最新一场成绩按 exam_key 请求', () => {
  assert.match(
    studentPage,
    /fetchTeachingStudents\(latest\.exam_key \?\? latest\.exam_name, scopeQ\)/,
    '最新一场成绩必须按 exam_key ?? exam_name 请求',
  )
})

test('compare 页样本估算视图退役（2026-10-22），旧模板 exam_key 初始选中断言随之退役', () => {
  assert.match(comparePage, /<TeachingPlatformCompare \/>/, '教学模式渲染官方质量分析主视图')
  assert.doesNotMatch(comparePage, /setSelectedExam\(sorted\[0\]\.exam_key/, '旧模板视图已删除，本页不再有 listExams 初始选中逻辑')
})

test('两成绩页（已修基线，防回退）：初始 state 用 exam_key', () => {
  for (const [name, src] of [['HomeroomScores', homeroomScores], ['TeachingScores', teachingScores]]) {
    assert.match(
      src,
      /sorted\[0\]\.exam_key \?\? sorted\[0\]\.exam_name/,
      `${name} 默认选中应为 exam_key ?? exam_name`,
    )
    assert.match(
      src,
      /\(exam\.exam_key \?\? exam\.exam_name\) === requestedExam/,
      `${name} 的 ?exam= 匹配应按 exam_key`,
    )
  }
})

test('后端 filter_exam 契约（跨栈只读核对）：event:/legacy: 选择器与名称回退共存', () => {
  const backend = readFileSync(new URL('../../backend/app/api/exam_events.py', import.meta.url), 'utf8')
  assert.match(backend, /selector\.startswith\("event:"\)/, 'filter_exam 认 event:N 选择器')
  assert.match(backend, /selector\.startswith\("legacy:"\)/, 'filter_exam 认 legacy:name|date 选择器')
  assert.match(backend, /return query\.filter\(model\.exam_name == selector\)/, '裸名称作为兼容回退')
})

test('共享工具不把 exam_key 当展示名（examOptionLabel 仍用 exam_name+日期）', () => {
  assert.match(shared, /`\$\{e\.exam_name\}（\$\{examDateLabel\(e\)}）`/, '下拉文案保持「考试名（日期）」')
})
