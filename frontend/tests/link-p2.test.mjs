// P2 收口契约测试：学生配对 / 共享范围 / S07 草稿不丢 / U01 响应式与打印。
// 风格沿用 tests/ui-contracts.test.mjs：直接读源码断言关键结构，不启动浏览器。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const apiV1 = readFileSync(new URL('../src/lib/api-v1.ts', import.meta.url), 'utf8')
const linkPage = readFileSync(new URL('../src/app/settings/link/page.tsx', import.meta.url), 'utf8')
const studentsPanel = readFileSync(new URL('../src/components/link/LinkStudentsPanel.tsx', import.meta.url), 'utf8')
const shareScopePanel = readFileSync(new URL('../src/components/link/LinkShareScopePanel.tsx', import.meta.url), 'utf8')
const linkTable = readFileSync(new URL('../src/components/link/LinkTable.tsx', import.meta.url), 'utf8')
const linkDraft = readFileSync(new URL('../src/lib/link-draft.ts', import.meta.url), 'utf8')
const workspace = readFileSync(new URL('../src/lib/workspace.tsx', import.meta.url), 'utf8')
const globalsCss = readFileSync(new URL('../src/app/globals.css', import.meta.url), 'utf8')
const sidebar = readFileSync(new URL('../src/components/layout/Sidebar.tsx', import.meta.url), 'utf8')

test('api-v1 导出学生配对与共享范围封装及 shared_conflict 类型（契约 §1.2.1/§1.2.2/§1.4.1）', () => {
  for (const fn of ['listLinkStudents', 'createLinkStudents', 'deleteLinkStudent', 'updateLinkShareScope']) {
    assert.match(apiV1, new RegExp(`export function ${fn}\\(`), `应导出 ${fn}`)
  }
  for (const iface of [
    'LinkPairEntry',
    'LinkStudentsList',
    'LinkStudentsRequest',
    'LinkStudentsResult',
    'LinkShareScopeRequest',
    'LinkShareScope',
    'LinkSharedConflict',
  ]) {
    assert.match(apiV1, new RegExp(`export interface ${iface}\\b`), `应导出类型 ${iface}`)
  }
  assert.match(
    apiV1,
    /\/shared\/links\/\$\{encodeURIComponent\(linkId\)\}\/students/,
    '学生配对端点应挂在 /shared/links/{link_id}/students',
  )
  assert.match(apiV1, /\/share-scope/, '共享范围端点应挂在 /shared/links/{link_id}/share-scope')
  const conflictCount = (apiV1.match(/shared_conflict\?: LinkSharedConflict \| null/g) ?? []).length
  assert.ok(
    conflictCount >= 3,
    `ScoreRow/ProfileExam/WorkspaceStudent 三个类型都应带可选 shared_conflict，实际 ${String(conflictCount)} 处`,
  )
})

test('学生配对面板只显式建对，绝不按同名/同号自动配对或预选', () => {
  assert.match(linkPage, /<LinkStudentsPanel/, '关联配置页应渲染学生配对面板')
  assert.match(linkPage, /<LinkShareScopePanel/, '关联配置页应渲染共享范围面板')
  assert.match(
    studentsPanel,
    /createLinkStudents\(\s*link\.id,\s*\{\s*pairs:/,
    '建立配对必须提交显式 person_id 对',
  )
  assert.doesNotMatch(studentsPanel, /\.name\s*===?\s*\w+\.name/, '不得按姓名相等自动配对或预选')
  assert.doesNotMatch(studentsPanel, /\.person_id\s*===?\s*\w+\.person_id/, '不得按编号相等自动配对或预选')
  assert.match(studentsPanel, /不会自动配对/, '界面必须声明同名同号不自动配对')
  assert.match(studentsPanel, /配对后两工作台共享该生的任教学科数据/, '确认文案须说明共享后果')
  assert.match(studentsPanel, /deleteLinkStudent\(link\.id, /, '既有配对必须可撤销')
  assert.match(studentsPanel, /两域分数不一致待人工核对/, '冲突行必须有人工核对标注')
})

test('共享范围面板覆盖三类共享与历史授权，收紧需确认且立即生效', () => {
  assert.match(shareScopePanel, /value: 'roster'/, '须包含名册共享选项')
  assert.match(shareScopePanel, /value: 'current_subject_score'/, '须包含任教学科成绩选项')
  assert.match(shareScopePanel, /value: 'current_subject_homework'/, '须包含任教学科作业选项')
  assert.match(shareScopePanel, /window\.confirm\('取消成绩共享后立即生效/, '收紧共享前必须确认')
  assert.match(shareScopePanel, /updateLinkShareScope\(link\.id, \{/, '保存必须调用共享范围端点')
  assert.match(shareScopePanel, /share_history_from: historyFrom/, '历史授权日期随表单提交')
  assert.match(shareScopePanel, /beforeunload/, '未保存修改时离开页面需提示')
})

test('关联草稿按 link 存 sessionStorage，提交成功/显式取消后清除（S07）', () => {
  assert.match(linkDraft, /'link-draft:'/, '草稿键名必须以 link-draft: 为前缀')
  assert.match(linkDraft, /sessionStorage/, '草稿存放 sessionStorage')
  assert.match(linkDraft, /export function clearLinkDraftSection/, '应提供分节清除函数')
  assert.match(linkDraft, /export function hasAnyLinkDraft/, '应提供草稿存在性判断')
  assert.match(studentsPanel, /clearLinkDraftSection\(link\.id, 'pairSelection'\)/, '配对提交成功/清除选择后清草稿')
  assert.match(shareScopePanel, /clearLinkDraftSection\(link\.id, 'shareScope'\)/, '共享范围保存成功/放弃修改后清草稿')
  assert.match(workspace, /hasAnyLinkDraft\(\)/, '工作台切换器在有草稿时需先确认')
})

test('打印与响应式基线存在（U01）', () => {
  assert.match(globalsCss, /@media print/, 'globals.css 需含全局打印样式')
  assert.match(globalsCss, /aside,\s*header,\s*button\s*\{\s*display: none !important/, '打印时隐藏侧栏/顶栏/操作按钮')
  assert.match(globalsCss, /overflow: visible !important/, '打印时展开横向滚动容器防裁切')
  assert.match(globalsCss, /width: 100% !important/, '打印时表格全宽')
  assert.match(studentsPanel, /overflow-x-auto/, '配对表需横向滚动容器')
  assert.match(linkTable, /overflow-x-auto/, '关联列表宽表需横向滚动容器')
  assert.match(sidebar, /hidden md:flex/, '侧栏在 <768 隐藏，由移动端抽屉承接')
})
