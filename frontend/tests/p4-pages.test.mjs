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
const scoreRadar = readFileSync(new URL('../src/components/students/StudentScoreRadar.tsx', import.meta.url), 'utf8')
const homeworkSubjectsCard = readFileSync(
  new URL('../src/components/students/StudentHomeworkSubjectsCard.tsx', import.meta.url),
  'utf8',
)
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
  // 问题8：名册页在原有解构上追加 refreshScope（新建学生后刷新全局范围），断言随之放宽
  assert.match(rosterTable, /const \{ filter, scope, scopeError, generation, switching(, refreshScope)? \} = useWorkspace\(\)/, '名册请求须读取已解析的工作台范围')
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

test('班级信息页头部常驻「班级关联配置」入口（2026-10-24 用户反馈 /settings/link 太隐蔽）', () => {
  const idx = membersManager.indexOf('班级关联配置')
  assert.ok(idx > 0, '头部须有「班级关联配置」常驻入口')
  const chunk = membersManager.slice(Math.max(0, idx - 600), idx + 100)
  assert.match(chunk, /href="\/settings\/link"/, '入口必须指向 /settings/link')
  assert.match(chunk, /<Link2 /, '入口带 Link2 图标（区别于 409 引导里的「前往关联配置」）')
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

test('档案页「个人作业与成绩分析」全科并列卡：挂作业卡上方、区间窗口（上次考试起，首场自开学）、无学科筛选（2026-10-23）', () => {
  assert.match(
    profileView,
    /import StudentHomeworkSubjectsCard from '@\/components\/students\/StudentHomeworkSubjectsCard'/,
    '档案视图导入全科作业并列卡',
  )
  const mountIdx = profileView.indexOf('<StudentHomeworkSubjectsCard')
  const hwIdx = profileView.indexOf('<HomeworkCard')
  assert.ok(mountIdx > 0 && hwIdx > 0 && mountIdx < hwIdx, '全科作业卡须挂在 HomeworkCard 上方')
  assert.match(
    profileView,
    /<StudentHomeworkSubjectsCard\s+personId=\{Number\(selectedPersonId\)\}\s+academicYearId=\{scopeQ\.academic_year_id\}\s*\/>/,
    '装配参数：person_id + 学年（mode 恒班主任域，不传）',
  )
  // 数据契约：单生全科一次请求，提交率区间=上一次考试至本次（首场自本学期开始），
  // 迟到回包按请求序号丢弃
  assert.match(
    homeworkSubjectsCard,
    /studentHomeworkSubjects\(\{ person_id: personId, academic_year_id: academicYearId, window_days: 14, window_mode: 'between_exams' \}\)/,
    '须调用 studentHomeworkSubjects 且 window_mode=between_exams',
  )
  assert.match(homeworkSubjectsCard, /if \(req !== reqRef\.current\) return/, '切换学生后的迟到回包须丢弃')
  assert.match(homeworkSubjectsCard, /个人作业与成绩分析/, '卡片标题')
  assert.match(
    homeworkSubjectsCard,
    /各学科作业提交率（上次考试后至本次考试前，首场自开学起）与成绩变化并列对比；语数英与高一用年级百分位（前%），高二\/高三六选科用等级分/,
    '口径描述（2026-10-23 区间口径）',
  )
  assert.match(homeworkSubjectsCard, /function examSpanLabel/, '悬浮标题助手（考试+统计起点）')
  assert.match(homeworkSubjectsCard, /labelFormatter=\{examSpanLabel\}/, '两个悬浮都带统计起点')
  assert.match(homeworkSubjectsCard, /window_start/, '使用窗口起点字段')
  assert.match(
    apiV1,
    /window_start: string \| null[\s\S]*?window_end: string \| null/,
    'API 类型补窗口起止字段',
  )
  assert.match(apiV1, /window_mode\?: 'fixed' \| 'between_exams'/, 'API 封装可传 window_mode')
  assert.match(homeworkSubjectsCard, /grid gap-3 md:grid-cols-2 xl:grid-cols-3/, '学科块三列网格')
  assert.match(homeworkSubjectsCard, /配对 \{series\.paired_n\} 场/, '配对场数徽标')
  assert.match(homeworkSubjectsCard, /该生暂无可配对的考试与作业记录/, 'subjects 为空的空态提示')
  // 双迷你图契约：提交率蓝 0–100%；成绩紫，百分位口径 reversed（越上越靠前）
  assert.match(homeworkSubjectsCard, /#0ea5e9/, '提交率线蓝色')
  assert.match(homeworkSubjectsCard, /#8b5cf6/, '成绩线紫色')
  assert.match(homeworkSubjectsCard, /domain=\{\[0, 100\]\}|domain=\[0, 100\]/, '提交率 Y 域 0–100')
  assert.match(homeworkSubjectsCard, /reversed=\{allPercentile\}/, '百分位口径 Y 轴 reversed')
  assert.match(homeworkSubjectsCard, /`前\$\{v\}%`/, '百分位口径 Y 轴刻度「前X%」')
  assert.match(homeworkSubjectsCard, /p\.exam_date != null && p\.exam_date !== ''/, '点只取考试日期非空者')
  assert.match(homeworkSubjectsCard, /connectNulls=\{false\}/, '缺考点断线')
  assert.match(homeworkSubjectsCard, /isAnimationActive=\{false\}/, '关闭动画')
  assert.match(homeworkSubjectsCard, /subjectAchievementLabel\(latest\)/, '最近成绩用共享格式化（student-homework.ts）')
  assert.doesNotMatch(homeworkSubjectsCard, /分析学科/, '全科并列，不得引入学科筛选下拉')
})

test('档案页历次成绩雷达卡在走势卡上方 + 走势图按 前%/等级分/名次 分口径（2026-10-25）', () => {
  // 雷达卡须在走势卡上方（用户拍板「个人页也添加雷达图在走势图上面」）
  const radarIdx = profileView.indexOf('历次成绩雷达')
  const trendIdx = profileView.indexOf('历次走势')
  assert.ok(radarIdx > 0 && trendIdx > 0 && radarIdx < trendIdx, '雷达卡在走势卡之前')
  assert.match(profileView, /<RadarIcon className="h-4 w-4" \/>\s*历次成绩雷达/, '雷达卡标题')
  assert.match(profileView, /<StudentScoreRadar rows=\{radarRows\} \/>/, '雷达组件接线（数据来自 examMatrix）')
  // 图A 按标准分图：语数英/全科=年级前%（0-100 反转、刻度前X%）、加三=等级分（40-70）
  assert.match(profileView, /图A1 · 各科年级前%走势/, '图A1 标题')
  assert.match(profileView, /图A2 · 加三等级分走势/, '图A2 标题（标准不同分图）')
  assert.match(profileView, /图B · 总分校名次走势/, '图B 标题（按校名次）')
  assert.match(profileView, /reversed\s+domain=\{\[0, 100\]\}\s+allowDecimals/, '图A1 Y 轴 0-100 反转')
  assert.match(profileView, /`前\$\{v\}%`/, '图A1 刻度「前X%」')
  assert.match(profileView, /domain=\{\[40, 70\]\}/, '图A2 等级分轴 40-70')
  assert.match(profileView, /reversed allowDecimals=\{false\} domain=\{\['auto', 'auto'\]\}/, '图B 名次轴反转')
  assert.match(profileView, /`前 \$\{Number\(val\)\}%（越小越靠前）`/, '图A1 悬浮文案')
  assert.match(profileView, /`等级分 \$\{fmtScore\(Number\(val\)\)\}`/, '图A2 悬浮文案')
  assert.match(profileView, /`第 \$\{Number\(val\)\} 名`/, '图B 悬浮文案')
  // 学科分组数据驱动（任一考试有等级分 → 等级组）；+3 无名次不进图B
  assert.match(profileView, /gradeSubjects = useMemo\([\s\S]{0,140}allSubjects\.filter\(\(s\) => examMatrix\.some\(\(r\) => r\.subjects\[s\]\?\.grade_score != null\)\)/, '等级分组=任一考试有等级分')
  assert.match(profileView, /percentileSubjects = useMemo\([\s\S]{0,120}allSubjects\.filter\(\(s\) => !gradeSubjects\.includes\(s\)\)/, '前%组=其余学科')
  assert.match(profileView, /PROFILE_RANKLESS_TOTALS/, '图B 排除无名次总分（+3）')
  // 图例筛选沿用：显隐状态 + 按钮切换 + 渲染前过滤
  assert.match(profileView, /hiddenSubjects/, '学科显隐状态')
  assert.match(profileView, /hiddenTotals/, '总分口径显隐状态')
  assert.match(profileView, /toggleSubject\(sub\)/, '图例按钮切换学科')
  assert.match(profileView, /toggleTotal\(t\)/, '图例按钮切换总分口径')
  assert.match(profileView, /\{visiblePercentileSubjects\.map\(\(sub\) => \(/, '图A1 按可见学科渲染')
  assert.match(profileView, /\{visibleGradeSubjects\.map\(\(sub\) => \(/, '图A2 按可见学科渲染')
  assert.match(profileView, /\{visibleRankTotals\.map\(\(t\) => \(/, '图B 按可见口径渲染')
  assert.match(profileView, /name=\{totalTypeLabel\(t\)\}/, '图B 线名用口径展示名')
  assert.match(profileView, /connectNulls=\{false\}/, '缺考点不连线')
  assert.match(profileView, /angle=\{-15\}/, 'X 轴考试名斜排')
  assert.doesNotMatch(profileView, /各科成绩走势（原始分）/, '旧原始分走势标题不得残留')
  assert.doesNotMatch(profileView, /visibleSubjects\.map|visibleTotals\.map/, '旧未分组渲染不得残留')
})

test('档案页历次明细表：总分组列 + 校名次小字 + 选修等级分小字 + 口径脚注（2026-10-22）', () => {
  assert.match(profileView, /totalTypeLabel\(t\)/, '总分列头走口径映射')
  assert.match(profileView, /import \{ StudentScoreRadar, totalTypeLabel, type RadarExamRow \} from '@\/components\/students\/StudentScoreRadar'/, '口径映射与雷达组件同源导入')
  for (const [k, label] of [
    ['+3', '+3总分（等级和）'],
    ['主三门', '三门总分'],
    ['3+3', '3+3总分'],
    ['五门', '五门总分'],
    ['九门', '九门总分'],
  ]) {
    assert.ok(scoreRadar.includes(`'${k}': '${label}',`), `口径 ${k} 应映射为「${label}」（StudentScoreRadar 单一来源）`)
  }
  assert.match(profileView, /cell\.rank != null && \(/, '校名次非空才显示')
  assert.match(profileView, / · 第\{cell\.rank\}名/, '名次小字「 · 第N名」')
  assert.match(profileView, /text-\[10px\] text-slate-400/, '等级分小字样式')
  assert.match(profileView, /等\{fmtScore\(cell\.grade_score\)\}/, '选修等级分小字「等N」')
  assert.match(
    profileView,
    /\+3总分为选修三门等级分之和（学校导出口径），与各科原始分列不直接相加；总分行的『第N名』为校名次。/,
    '脚注说明 +3 口径与校名次',
  )
  assert.doesNotMatch(profileView, /total_score/, '旧「总分」单列（total_score）已删除')
  // 列头顺序：考试名称 / 考试日期 / 各科（report.subjects 序）/ 总分列（report.totals 序）
  const nameIdx = profileView.indexOf('>考试名称<')
  const dateIdx = profileView.indexOf('>考试日期<')
  const subjectsHeadIdx = profileView.indexOf('{allSubjects.map((sub) => (')
  const totalsHeadIdx = profileView.indexOf('{totalTypes.map((t) => (')
  assert.ok(
    nameIdx > 0 && dateIdx > nameIdx && subjectsHeadIdx > dateIdx && totalsHeadIdx > subjectsHeadIdx,
    '列头顺序 = 考试名称→考试日期→各科→总分口径',
  )
})
