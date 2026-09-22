// 折叠列表契约测试：8 处「超过 N 条静默截断」的列表统一改为
// 「默认条数不变 + 底部 MoreToggle 展开/收起」。
// 风格沿用 ux-dashboard-entry.test.mjs：直接读组件源码做字符串/正则断言。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const moreToggle = readFileSync(new URL('../src/components/ui/more-toggle.tsx', import.meta.url), 'utf8')
const weeklyFocus = readFileSync(new URL('../src/components/WeeklyFocusCard.tsx', import.meta.url), 'utf8')
const dashboard = readFileSync(new URL('../src/components/dashboard/WorkspaceDataDashboard.tsx', import.meta.url), 'utf8')
const homeworkCard = readFileSync(new URL('../src/components/HomeworkCard.tsx', import.meta.url), 'utf8')
const backupCard = readFileSync(new URL('../src/components/BackupCard.tsx', import.meta.url), 'utf8')
const studentReport = readFileSync(new URL('../src/app/student/[id]/report/page.tsx', import.meta.url), 'utf8')
const homeroomReport = readFileSync(new URL('../src/app/homeroom/students/[id]/report/page.tsx', import.meta.url), 'utf8')

test('统一折叠控件 MoreToggle：print:hidden、ChevronDown/Up、展开/收起文案、hiddenCount<=0 不渲染', () => {
  assert.match(moreToggle, /'use client'/)
  assert.match(moreToggle, /print:hidden/, '打印时折叠按钮必须隐藏')
  assert.match(moreToggle, /ChevronDown/, '收起态须用向下箭头')
  assert.match(moreToggle, /ChevronUp/, '展开态须用向上箭头')
  assert.match(moreToggle, /点击展开/, '收起态文案须含「点击展开」')
  assert.match(moreToggle, /收起/, '展开态文案为「收起」')
  assert.match(moreToggle, /unit = '条'/, '量词默认「条」')
  assert.match(moreToggle, /if \(hiddenCount <= 0\) return null/, '无隐藏条目时控件不渲染')
  assert.match(moreToggle, /aria-label=\{expanded \? '收起' : `展开其余 \$\{hiddenCount\} 项`\}/, '无障碍标签')
  assert.match(moreToggle, /export function MoreToggle/, '统一命名导出供各处复用')
})

test('WeeklyFocusCard：删「另有」死文字，前 8 人 + MoreToggle（人 · 需关注）', () => {
  assert.doesNotMatch(weeklyFocus, /另有/, '不得再有静默截断的死文字')
  assert.match(weeklyFocus, /showAllStudents \? students : students\.slice\(0, 8\)/, '收起默认仍显示前 8 人')
  assert.match(weeklyFocus, /<MoreToggle/, '须接入统一折叠控件')
  assert.match(weeklyFocus, /hiddenCount=\{students\.length - 8\}/, '隐藏人数按全量计算')
  assert.match(weeklyFocus, /unit="人"/, '')
  assert.match(weeklyFocus, /suffix="需关注"/, '')
  assert.match(weeklyFocus, /setShowAllStudents\(\(v\) => !v\)/, '点击在展开/收起间切换')
})

test('WorkspaceDataDashboard：数据层去 slice 保留全量，渲染层 6/3/8 三处折叠', () => {
  // 数据层：三处 slice 全部拆除（其余映射逻辑不动）
  assert.match(dashboard, /const warningData = \(warning\.students \?\? \[\]\)\.map\(/, '缺交预警数据层保留全量')
  assert.match(dashboard, /focus = \(latestFocus\?\.students \?\? \[\]\)\.map\(/, '班主任关注学生数据层保留全量')
  assert.doesNotMatch(dashboard, /\.slice\(0, 6\)\s*\n\s*\.map\(/, '教学关注学生不得再在数据层截 6 条')
  assert.doesNotMatch(dashboard, /\.sort\(\(a, b\) => \(a\.score == null \? -1 : b\.score == null \? 1 : a\.score - b\.score\)\)\s*\n\s*\.slice\(/, '教学排序后直接全量映射，不再截断')
  // 渲染层：空态视图 warnings 折叠 6 条
  assert.match(dashboard, /warningsEmptyExpanded \? data\.warnings : data\.warnings\.slice\(0, 6\)/, '空态视图收起默认 6 条')
  assert.match(dashboard, /hiddenCount=\{data\.warnings\.length - 6\}/, '')
  // 渲染层：主视图 warnings 折叠 3 条
  assert.match(dashboard, /warningsExpanded \? data\.warnings : data\.warnings\.slice\(0, 3\)/, '主视图收起默认 3 条')
  assert.match(dashboard, /hiddenCount=\{data\.warnings\.length - 3\}/, '')
  // 渲染层：需关注学生统一折叠 8 人（班主任/教学一致）
  assert.match(dashboard, /focusExpanded \? data\.focus : data\.focus\.slice\(0, 8\)/, '需关注学生收起默认 8 人')
  assert.match(dashboard, /hiddenCount=\{data\.focus\.length - 8\}/, '')
  assert.match(dashboard, /unit="条"/, '')
  assert.match(dashboard, /unit="人"/, '')
  // 三个折叠状态统一提升到组件顶层（hooks 不进条件分支）
  const stateCount = (dashboard.match(/useState\(false\)/g) ?? []).length
  assert.ok(stateCount >= 3, `warnings/focus 折叠状态须为顶层 useState（发现 ${stateCount} 个）`)
  // 空态提示逻辑保持
  assert.match(dashboard, /当前筛选下暂无重点作业预警/)
})

test('HomeworkCard：近期明细收起 30 条，MoreToggle（条记录）', () => {
  assert.match(homeworkCard, /eventsExpanded \? displayEvents : displayEvents\.slice\(0, 30\)/, '收起默认 30 条')
  assert.match(homeworkCard, /hiddenCount=\{displayEvents\.length - 30\}/, '')
  assert.match(homeworkCard, /unit="条记录"/, '')
  assert.match(homeworkCard, /<MoreToggle/, '')
})

test('BackupCard：备份列表收起 6 份，MoreToggle（份备份）', () => {
  assert.match(backupCard, /expanded \? backups : backups\.slice\(0, 6\)/, '收起默认 6 份')
  assert.match(backupCard, /hiddenCount=\{backups\.length - 6\}/, '')
  assert.match(backupCard, /unit="份备份"/, '')
  assert.match(backupCard, /<MoreToggle/, '')
})

test('教学学生报告页：沟通摘要收起 4 条，MoreToggle（条记录）', () => {
  assert.match(studentReport, /notesExpanded \? notes : notes\.slice\(0, 4\)/, '收起默认 4 条')
  assert.match(studentReport, /hiddenCount=\{notes\.length - 4\}/, '')
  assert.match(studentReport, /unit="条记录"/, '')
  assert.match(studentReport, /<MoreToggle/, '')
})

test('班主任报告页：全量档案拉取（homeroom 域）+ 摘要收起 5 条，失败回退 recent', () => {
  const noteCalls = (homeroomReport.match(/listStudentNotes\('homeroom'/g) ?? []).length
  assert.ok(noteCalls >= 2, `摘要须额外拉一次全量档案（现有 ${noteCalls} 处 homeroom 域调用）`)
  assert.doesNotMatch(homeroomReport, /listStudentNotes\('teaching'/, '域隔离红线：只允许 homeroom 域')
  assert.match(homeroomReport, /setAllNotes\(r\.notes \?\? \[\]\)/, '全量档案落入摘要数据源')
  assert.match(homeroomReport, /\.catch\(\(\) => setAllNotes\(null\)\)/, '失败降级为 null 不阻塞报告主体')
  assert.match(homeroomReport, /allNotes \?\? recentNotes/, '回退口径为后端 notes_summary.recent')
  assert.match(homeroomReport, /summaryExpanded \? summaryNotes : summaryNotes\.slice\(0, 5\)/, '收起默认 5 条与后端 recent 口径一致')
  assert.match(homeroomReport, /hiddenCount=\{summaryNotes\.length - 5\}/, '')
  assert.match(homeroomReport, /unit="条记录"/, '')
  assert.match(homeroomReport, /<MoreToggle/, '')
  assert.match(homeroomReport, /暂无档案记录/, '空态保持')
})
