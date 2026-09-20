// P4-FE 收口契约测试：学生管理 / 换届 / 教学班成员 / 打印画像（契约 docs/contracts/p4-students.md §2/§3/§4/§6）。
// 风格沿用 tests/scores-p3.test.mjs：直接读源码断言关键结构，不启动浏览器。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const apiV1 = readFileSync(new URL('../src/lib/api-v1.ts', import.meta.url), 'utf8')
const rosterPage = readFileSync(new URL('../src/app/homeroom/students/page.tsx', import.meta.url), 'utf8')
const rolloverPage = readFileSync(new URL('../src/app/homeroom/rollover/page.tsx', import.meta.url), 'utf8')
const membersPage = readFileSync(new URL('../src/app/teaching/members/page.tsx', import.meta.url), 'utf8')
const reportPage = readFileSync(
  new URL('../src/app/homeroom/students/[id]/report/page.tsx', import.meta.url),
  'utf8',
)
const profilePage = readFileSync(new URL('../src/app/homeroom/profile/page.tsx', import.meta.url), 'utf8')
const profileView = readFileSync(new URL('../src/components/students/HomeroomProfileView.tsx', import.meta.url), 'utf8')
const rosterTable = readFileSync(new URL('../src/components/students/RosterTable.tsx', import.meta.url), 'utf8')
const studentDialog = readFileSync(new URL('../src/components/students/StudentFormDialog.tsx', import.meta.url), 'utf8')
const aliasPanel = readFileSync(new URL('../src/components/students/AliasHistoryPanel.tsx', import.meta.url), 'utf8')
const rolloverWizard = readFileSync(new URL('../src/components/rollover/RolloverWizard.tsx', import.meta.url), 'utf8')
const membersManager = readFileSync(
  new URL('../src/components/teaching-members/TeachingMembersManager.tsx', import.meta.url),
  'utf8',
)
const pageDraft = readFileSync(new URL('../src/lib/page-draft.ts', import.meta.url), 'utf8')
const topbar = readFileSync(new URL('../src/components/layout/Topbar.tsx', import.meta.url), 'utf8')
const sidebar = readFileSync(new URL('../src/components/layout/Sidebar.tsx', import.meta.url), 'utf8')
const uploadPage = readFileSync(new URL('../src/app/upload/page.tsx', import.meta.url), 'utf8')
const previewTable = readFileSync(new URL('../src/components/upload/ImportsPreviewTable.tsx', import.meta.url), 'utf8')

test('api-v1 导出 P4 名册/换届/打印/成员/档案封装与类型（契约 §1-§4）', () => {
  for (const fn of [
    // §1 学年
    'listAcademicYears',
    'createAcademicYear',
    // §2.1 名册
    'createHomeroomStudent',
    'patchHomeroomStudent',
    'archiveHomeroomStudent',
    'addStudentAlias',
    'listStudentAliases',
    // §2.2 换届
    'rolloverPreview',
    'rolloverConfirm',
    'rolloverUndo',
    // §2.3 打印
    'getStudentReport',
    // §3 教学班成员
    'listTeachingMembers',
    'addTeachingMember',
    'removeTeachingMember',
    'importTeachingMembersPreview',
    'importTeachingMembersConfirm',
    'syncFromHomeroomPreview',
    'syncFromHomeroomConfirm',
    // §4 档案（mode 参数）
    'listStudentNotes',
    'createNote',
    'patchNote',
    'deleteNote',
  ]) {
    assert.match(apiV1, new RegExp(`export function ${fn}\\(`), `应导出 ${fn}`)
  }
  for (const t of [
    'AcademicYear',
    'EnrollmentStatus',
    'StudentAliasEntry',
    'RolloverPreview',
    'RolloverPreviewStudent',
    'RolloverConfirmRequest',
    'RolloverUndoConflict',
    'StudentReportResponse',
    'TeachingMember',
    'TeachingMembersResponse',
    'TeachingImportPreview',
    'TeachingSyncPreview',
    'StudentNote',
  ]) {
    assert.match(apiV1, new RegExp(`export (interface|type) ${t}\\b`), `应导出类型 ${t}`)
  }
  assert.match(apiV1, /\/shared\/academic-years/, '学年端点应挂在 /shared/academic-years')
  assert.match(apiV1, /homeroom\/rollover\/preview/, '换届预览端点应挂在 /homeroom/rollover/preview')
  assert.match(apiV1, /homeroom\/rollover\/\$\{[^}]+\}\/undo|rollover\/.*\/undo/, '撤销端点应带 token 路径段')
  assert.match(apiV1, /homeroom\/students\/\$\{[^}]+\}\/report/, '画像端点应挂在 /homeroom/students/{id}/report')
  assert.match(apiV1, /teaching\/classes\/\$\{[^}]+\}\/members/, '教学成员端点应挂在 /teaching/classes/{id}/members')
  assert.match(apiV1, /sync-from-homeroom/, '行政班同步端点应存在')
  assert.match(apiV1, /\/notes/, '档案端点应挂在 /notes')
  // 档案端点按 mode 分域（N01 隔离红线）
  assert.match(apiV1, /export function createNote\(\s*\n?\s*mode: WorkspaceMode/, 'createNote 必须显式接收 mode')
  assert.match(apiV1, /export function deleteNote\(\s*\n?\s*mode: WorkspaceMode/, 'deleteNote 必须显式接收 mode')
  // P4 全 JSON：名册/换届/成员封装不得引入 multipart
  for (const fn of ['createHomeroomStudent', 'rolloverConfirm', 'addTeachingMember']) {
    const start = apiV1.indexOf(`export function ${fn}(`)
    const chunk = apiV1.slice(start, start + 600)
    assert.doesNotMatch(chunk, /multipart/i, `${fn} 应为 JSON 请求`)
  }
})

test('四个 P4 页面存在、渲染组件且组件读 useWorkspace（契约 §6）', () => {
  assert.match(rosterPage, /RosterTable/, '学生管理页应渲染 RosterTable')
  assert.match(rolloverPage, /RolloverWizard/, '换届页应渲染 RolloverWizard')
  assert.match(membersPage, /TeachingMembersManager/, '教学成员页应渲染 TeachingMembersManager')
  for (const [src, name] of [
    [rosterTable, '学生管理页'],
    [rolloverWizard, '换届页'],
    [membersManager, '教学成员页'],
    [reportPage, '打印画像页'],
  ]) {
    assert.match(src, /useWorkspace\(\)/, `${name}必须读工作台上下文`)
    assert.match(src, /generation/, `${name}必须响应工作台世代号`)
  }
  assert.match(reportPage, /window\.print\(\)/, '打印画像页必须有打印按钮')
})

test('名册页：新建/行内编辑/离班/追加学号齐全，且无删除、无合并入口（契约 §2.1）', () => {
  assert.match(rosterTable, /新建学生/, '名册页须有新建学生入口')
  assert.match(rosterTable, /离班/, '名册页须有离班入口')
  assert.match(rosterTable, /恢复在班/, '离班须可恢复（status=active，valid_to 置空）')
  assert.match(rosterTable, /追加学号/, '名册页须有追加学号入口')
  assert.match(aliasPanel, /追加学号/, '别名历史面板须有追加学号表单')
  assert.match(aliasPanel, /listStudentAliases/, '别名历史须读 aliases 端点')
  assert.match(aliasPanel, /valid_from/, '追加学号须带生效日期')
  assert.match(studentDialog, /createHomeroomStudent/, '新建学生须走 POST /homeroom/students')
  assert.match(studentDialog, /readAliasConflicts/, '撞号 422 须解析冲突人列表')
  assert.match(aliasPanel, /readAliasConflicts/, '追加学号撞号 422 须解析冲突人列表')
  assert.match(rosterTable, /patchHomeroomStudent/, '行内编辑须走 PATCH')
  assert.match(rosterTable, /archiveHomeroomStudent/, '离班/恢复须走 archive 端点')
  assert.match(rosterTable, /两域分数不一致待人工核对/, '冲突提示沿用 P3 语义')
  assert.match(rosterTable, /overflow-x-auto/, '名册宽表须横向滚动')
  // 红线：P4 不提供删除/合并（H 版语义重，延后 P7 裁决）；文案须正向声明「不自动合并」
  for (const [src, name] of [
    [rosterTable, '名册页'],
    [studentDialog, '新建学生弹窗'],
    [aliasPanel, '别名历史面板'],
  ]) {
    assert.doesNotMatch(src, /Trash2/, `${name}不得出现删除图标`)
    assert.doesNotMatch(src, /method: 'DELETE'/, `${name}不得发起 DELETE 请求`)
  }
  assert.match(studentDialog, /不自动合并/, '撞号文案须声明不自动合并')
  assert.match(aliasPanel, /不自动合并/, '追加学号撞号文案须声明不自动合并')
})

test('名册页：等待工作台范围成功，并按所选学年解析行政班后请求学生（历史学年不串班）', () => {
  assert.match(rosterTable, /const \{ filter, scope, scopeError, generation, switching \} = useWorkspace\(\)/, '名册请求须读取已解析的工作台范围')
  assert.match(rosterTable, /if \(scopeError != null\)/, '工作台范围失败时须停止名册请求并显示错误')
  assert.match(rosterTable, /if \(scope == null\) return/, 'scope 尚未解析完成时不得提前请求学生')
  assert.match(rosterTable, /scopeQ\.academic_year_id/, '须优先使用用户所选学年')
  assert.match(rosterTable, /current_academic_year\?\.id/, '未选学年时才可回落当前学年')
  assert.match(rosterTable, /fetchClasses\(academicYearId\)/, '须按目标学年重新解析行政班目录')
  assert.match(rosterTable, /class_id: catalog\.homeroom\.class_id/, '学生请求须携带该学年行政班 ID')
  assert.match(rosterTable, /fetchStudents\('homeroom', \{\s*\.\.\.scopeQ,\s*academic_year_id: academicYearId,\s*class_id:/s, '学生请求须同时携带学年、学期与行政班范围')
  assert.match(rosterTable, /if \(req !== reqRef\.current\) return/, '切换学年后的迟到响应仍须丢弃')
})

test('换届向导：预览逐人改号 → 确认 → 撤销 + 冲突保留现状（契约 §2.2，I01）', () => {
  assert.match(rolloverWizard, /listAcademicYears/, '来源学年须走学年清单端点')
  assert.match(rolloverWizard, /rolloverPreview/, '预览须走 rollover/preview')
  assert.match(rolloverWizard, /next_alias/, '新学年学号默认取后端建议值')
  assert.match(rolloverWizard, /rolloverConfirm/, '确认须走 POST /homeroom/rollover')
  assert.match(rolloverWizard, /token/, '确认必须携带 preview 颁发的 token')
  assert.match(rolloverWizard, /rolloverUndo/, '撤销须走 undo 端点')
  assert.match(rolloverWizard, /重新预览/, 'token 过期/漂移 409 须提供重新预览')
  assert.match(rolloverWizard, /window\.confirm/, '撤销按钮须带 confirm 确认')
  assert.match(rolloverWizard, /该生已有新数据，保留现状/, 'conflicted 学生须单独展示「保留现状」')
  assert.match(rolloverWizard, /请先创建下一学年/, '无新学年 409 须提示先建学年')
})

test('教学班成员页：当期/历史分列、文本导入两段、关联班 409 引导、sync 卡片（契约 §3）', () => {
  assert.match(membersManager, /listTeachingMembers/, '成员须走教学成员端点')
  assert.match(membersManager, /normalizeTeachingMembers/, 'active/left 或扁平 members 须先归一')
  assert.match(membersManager, /当期成员/, '须分列当期成员')
  assert.match(membersManager, /历史成员/, '须分列历史成员')
  assert.match(membersManager, /addTeachingMember/, '添加成员须走 POST members')
  assert.match(membersManager, /removeTeachingMember/, '移除须走 DELETE（后端写 valid_to）')
  assert.match(membersManager, /写离班截止日期/, '移除确认须说明写 valid_to 不物理删')
  assert.match(membersManager, /importTeachingMembersPreview/, '导入须先预览')
  assert.match(membersManager, /importTeachingMembersConfirm/, '导入确认两段式的第二段')
  assert.match(membersManager, /权威来源/, '关联班 409 须展示「行政班名册为权威来源」引导')
  assert.match(membersManager, /\/settings\/link/, '引导须带跳转关联配置链接')
  assert.match(membersManager, /syncFromHomeroomPreview/, '行政班同步须差异预览先行')
  assert.match(membersManager, /syncFromHomeroomConfirm/, '行政班同步须确认写入')
  assert.match(membersManager, /status === 'active' && l\.teaching_class_id === selectedClassId/, 'sync 卡片仅关联班（active link 覆盖该班）渲染')
  assert.match(membersManager, /overflow-x-auto/, '成员宽表须横向滚动')
})

test('表单未提交内容走 page-draft 草稿，提交成功清除（简化版 link-draft）', () => {
  assert.match(pageDraft, /page-draft:/, '草稿键前缀须为 page-draft:')
  assert.match(studentDialog, /savePageDraft/, '新建学生表单须暂存草稿')
  assert.match(studentDialog, /clearPageDraft/, '提交成功后须清除草稿')
  assert.match(aliasPanel, /savePageDraft/, '追加学号表单须暂存草稿')
  assert.match(aliasPanel, /clearPageDraft/, '提交成功后须清除草稿')
  assert.match(membersManager, /savePageDraft/, '添加成员/导入文本须暂存草稿')
  assert.match(membersManager, /clearPageDraft/, '提交成功后须清除草稿')
  assert.match(rolloverWizard, /savePageDraft/, '换届确认 token 须暂存草稿（撤销入口用）')
})

test('Topbar 面包屑补齐工作台/子路径中文标签；Sidebar 补导航入口（P3/P4 完善）', () => {
  for (const seg of ['homeroom', 'teaching', 'scores', 'students', 'rollover', 'members', 'profile']) {
    assert.match(topbar, new RegExp(`^\\s*${seg}:`, 'm'), `Topbar SEGMENT_LABELS 须包含 ${seg}`)
  }
  assert.match(sidebar, /\/homeroom\/profile/, '侧栏须有班主任学生档案入口')
  assert.match(sidebar, /\/homeroom\/students/, '侧栏须有学生信息入口')
  assert.match(sidebar, /\/homeroom\/rollover/, '侧栏须有换届入口')
  assert.match(sidebar, /\/teaching\/members/, '侧栏须有班级信息入口')
  assert.match(profilePage, /HomeroomProfileView/, '学生档案页应渲染 HomeroomProfileView')
  assert.match(profileView, /useWorkspace\(\)/, '学生档案视图必须读工作台上下文')
  assert.match(rosterTable, /\/homeroom\/profile\?person_id=/, '学生信息名册姓名须链接至学生档案')
})

test('上传页 P3 遗留：考试名/日期覆盖输入；row_count 标签改「成绩条目」', () => {
  assert.match(uploadPage, /examNameOverride/, '上传页须有考试名覆盖状态')
  assert.match(uploadPage, /examDateOverride/, '上传页须有考试日期覆盖状态')
  assert.match(uploadPage, /form\.set\('exam_name'/, '覆盖考试名须传入 preview 表单')
  assert.match(uploadPage, /form\.set\('exam_date'/, '覆盖考试日期须传入 preview 表单')
  assert.match(uploadPage, /\[files, examNameOverride, examDateOverride\]/, '覆盖输入变化须参与 token 作废键')
  assert.doesNotMatch(previewTable, />行数</, '预览表不得再用「行数」标签')
  assert.match(previewTable, /成绩条目/, 'row_count 标签应为「成绩条目」')
})
