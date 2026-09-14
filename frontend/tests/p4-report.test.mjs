// G05/P4 打印页契约测试：report 响应形状消费 + 成长档案入口（契约 docs/contracts/p4-students.md §2.3/§4）。
// 风格沿用 p4-pages.test.mjs：直接读源码断言关键结构，不启动浏览器。
//
// 背景（ 二轮 G05）：后端 notes_summary={count,recent:[...]}、alias 位于
// person.aliases；旧前端把 notes_summary 声明成数组并直接 .slice(0,5)，正常
// 学生也白屏，且学号史从 roster.aliases 读不到。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const apiV1 = readFileSync(new URL('../src/lib/api-v1.ts', import.meta.url), 'utf8')
const reportPage = readFileSync(
  new URL('../src/app/homeroom/students/[id]/report/page.tsx', import.meta.url),
  'utf8',
)

test('api-v1 report 类型逐字段对齐后端：notes_summary 是 {count, recent} 对象（G05）', () => {
  // StudentReportResponse 内的 notes_summary 必须声明为对象形态，禁止数组声明
  const respStart = apiV1.indexOf('export interface StudentReportResponse')
  assert.ok(respStart > 0, '应导出 StudentReportResponse')
  const respBody = apiV1.slice(respStart, apiV1.indexOf('}', apiV1.indexOf('notes_summary', respStart)) + 1)
  assert.match(respBody, /notes_summary:\s*\{\s*count:\s*number;\s*recent:\s*StudentReportNote\[\]\s*\}/)
  assert.match(respBody, /aliases:\s*StudentReportAlias\[\]/, 'person 下必须有 aliases 数组')
  assert.match(respBody, /roster:\s*StudentReportRoster/, 'roster 为非可空对象（后端恒返回）')
  // roster 不再声明 aliases（G05：alias 史在 person，不在 roster）
  const rosterStart = apiV1.indexOf('export interface StudentReportRoster')
  const rosterBody = apiV1.slice(rosterStart, apiV1.indexOf('}', apiV1.indexOf('academic_year_name', rosterStart)) + 1)
  assert.doesNotMatch(rosterBody, /aliases/, 'StudentReportRoster 不得再声明 aliases 字段')
})

test('report 响应类型不得用宽容索引签名掩盖形状（G05 要求）', () => {
  const sectionStart = apiV1.indexOf('/* ---- §2.3 打印')
  const sectionEnd = apiV1.indexOf('/* ---- §3 教学班成员管理')
  const section = apiV1.slice(sectionStart, sectionEnd)
  for (const name of [
    'StudentReportExam',
    'StudentReportSubject',
    'StudentReportTotal',
    'StudentReportAlias',
    'StudentReportRoster',
    'StudentReportNote',
    'StudentReportResponse',
  ]) {
    const start = section.indexOf(`export interface ${name}`)
    assert.ok(start >= 0, `应导出类型 ${name}`)
    const body = section.slice(start, section.indexOf('}', start) + 1)
    assert.doesNotMatch(body, /\[key:\s*string\]:\s*unknown/, `${name} 不得带宽容索引签名`)
  }
})

test('打印页按正确位置消费：person.aliases 与 notes_summary.recent，无 .slice 于 notes（G05）', () => {
  assert.match(reportPage, /report\?\.person\.aliases/, '学号史必须从 person.aliases 读')
  assert.match(reportPage, /report\?\.notes_summary\.recent/, '档案摘要必须从 notes_summary.recent 读')
  assert.doesNotMatch(reportPage, /roster\?\.\s*aliases/, '不得再从 roster 读别名史')
  assert.doesNotMatch(reportPage, /notes_summary[\s\S]{0,80}\.slice\(/, 'notes 不得再走数组 .slice（旧崩溃点）')
  assert.doesNotMatch(reportPage, /notes_summary\s*\?\?/, 'notes_summary 类型冻结后无需再对它判空兜底')
})

test('打印页保留无档案学生也安全的空态与打印按钮', () => {
  assert.match(reportPage, /window\.print\(\)/, '打印按钮必须存在')
  assert.match(reportPage, /暂无档案记录/, '无档案学生须有空态文案，不崩溃')
  assert.match(reportPage, /aliases\.length > 0/, '别名史按有无记录分支渲染，空列表不崩溃')
})

test('档案区消费入口：listStudentNotes/createNote/deleteNote 且固定 homeroom 域（P4 边界裁决项）', () => {
  assert.match(reportPage, /listStudentNotes\('homeroom'/, '档案列表须走 listStudentNotes 且固定 homeroom')
  assert.match(reportPage, /createNote\('homeroom'/, '新增档案须走 createNote 且固定 homeroom')
  assert.match(reportPage, /deleteNote\('homeroom'/, '删除档案须走 deleteNote 且固定 homeroom')
  assert.match(reportPage, /NOTE_CATEGORIES/, '档案类别须用契约 §4 枚举')
  assert.match(reportPage, /window\.confirm\(/, '删除前必须有确认弹窗')
  assert.match(reportPage, /print:hidden/, '档案编辑区须在打印时隐藏')
  // mode 固定：不得出现 teaching 域调用（报告页即班主任视图）
  assert.doesNotMatch(reportPage, /(listStudentNotes|createNote|deleteNote)\('teaching'/, '不得读写 teaching 域档案')
})
