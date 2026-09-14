// P5-FE 契约测试：作业录入/批次/预警/相关性/学期（契约 docs/contracts/p5-homework.md §1-§5/§7）。
// 风格沿用 tests/p4-pages.test.mjs：直接读源码断言关键结构，不启动浏览器。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const apiV1 = readFileSync(new URL('../src/lib/api-v1.ts', import.meta.url), 'utf8')
const workspace = readFileSync(new URL('../src/components/homework/HomeworkWorkspace.tsx', import.meta.url), 'utf8')
const entryPanel = readFileSync(new URL('../src/components/homework/HomeworkEntryPanel.tsx', import.meta.url), 'utf8')
const assignmentTable = readFileSync(new URL('../src/components/homework/AssignmentTable.tsx', import.meta.url), 'utf8')
const warningsPanel = readFileSync(new URL('../src/components/homework/WarningsPanel.tsx', import.meta.url), 'utf8')
const correlationCard = readFileSync(new URL('../src/components/homework/CorrelationCard.tsx', import.meta.url), 'utf8')
const semesterCard = readFileSync(new URL('../src/components/homework/SemesterSettingsCard.tsx', import.meta.url), 'utf8')
const homeworkShared = readFileSync(new URL('../src/components/homework/shared.ts', import.meta.url), 'utf8')
const homeroomPage = readFileSync(new URL('../src/app/homeroom/homework/page.tsx', import.meta.url), 'utf8')
const teachingPage = readFileSync(new URL('../src/app/teaching/homework/page.tsx', import.meta.url), 'utf8')
const sidebar = readFileSync(new URL('../src/components/layout/Sidebar.tsx', import.meta.url), 'utf8')

test('api-v1 导出 P5 全部封装函数（契约 §1-§5）', () => {
  for (const fn of [
    // §1 录入两段式 + 编辑/撤销
    'homeworkPreview',
    'homeworkConfirm',
    'homeworkPatchAssignment',
    'homeworkDeleteAssignment',
    'homeworkAssignmentDetail',
    // §2 读取/看板/学生事件流
    'homeworkAssignmentsList',
    'homeworkDashboard',
    'homeworkStudentEvents',
    // §3 预警 / §4 相关性
    'homeworkWarnings',
    'homeworkCorrelation',
    // §5 学期
    'homeworkSemesters',
    'homeworkCreateSemester',
    'homeworkUpdateSemester',
    'homeworkSetCurrentSemester',
    'homeworkRestoreAutoSemester',
    // 宽容错误读取
    'readHomeworkRevokeConflicts',
    'readHomeworkAmbiguityCandidates',
  ]) {
    assert.match(apiV1, new RegExp(`export function ${fn}\\(`), `应导出 ${fn}`)
  }
})

test('api-v1 导出 P5 类型；rate_unavailable 与可空 r 显式入型（契约 §0/§2/§4）', () => {
  for (const t of [
    'HomeworkStatus',
    'HomeworkInputKind',
    'HomeworkPreviewRequest',
    'HomeworkPreviewResponse',
    'HomeworkExistingBatch',
    'HomeworkConfirmResponse',
    'HomeworkRateStats',
    'HomeworkAssignmentListItem',
    'HomeworkAssignmentDetail',
    'HomeworkDashboardResponse',
    'HomeworkStudentResponse',
    'HomeworkWarningsResponse',
    'HomeworkWarningStudent',
    'HomeworkCorrelationResponse',
    'HomeworkSemestersResponse',
    'HomeworkSemesterRestoreResponse',
    'HomeworkRevokeConflict',
  ]) {
    assert.match(apiV1, new RegExp(`export (interface|type) ${t}\\b`), `应导出类型 ${t}`)
  }
  // H03 红线：分母不可用必须显式入型，前端据此显示「无法计算」
  assert.match(apiV1, /rate_unavailable: boolean/, 'rate_unavailable 必须为显式布尔字段')
  assert.match(apiV1, /submission_rate: number \| null/, 'submission_rate 必须可空')
  // 相关性 r 不可计算态：null 显式入型，绝不编造数值
  assert.match(apiV1, /r: number \| null/, '相关性 r 必须可空（样本不足或零方差）')
  // 预警/学生流：连续缺交遇 unknown 置 null
  assert.match(apiV1, /current_streak: number \| null/, '预警 current_streak 必须可空')
  assert.match(apiV1, /current_missing_streak: number \| null/, '学生流 current_missing_streak 必须可空')
  // 端点路径抽查
  assert.match(apiV1, /homework\/preview/, '录入预览端点')
  assert.match(apiV1, /homework\/confirm/, '录入确认端点')
  assert.match(apiV1, /homework\/assignments/, '批次端点')
  assert.match(apiV1, /homework\/dashboard/, '看板端点')
  assert.match(apiV1, /homework\/warnings/, '预警端点')
  assert.match(apiV1, /homework\/correlation/, '相关性端点')
  assert.match(apiV1, /homework\/semesters/, '学期端点')
  assert.match(apiV1, /restore-auto/, '恢复自动端点')
  assert.match(apiV1, /\/current/, '设当前学期端点')
  // 写路径必须显式携带 mode（后端缺 mode 422，绝不退化全年级）
  assert.match(apiV1, /export function homeworkPatchAssignment\(\s*\n?\s*assignmentId: number,\s*\n?\s*mode: WorkspaceMode/, 'PATCH 必须显式接收 mode')
  assert.match(apiV1, /export function homeworkDeleteAssignment\(\s*\n?\s*assignmentId: number,\s*\n?\s*mode: WorkspaceMode/, 'DELETE 必须显式接收 mode')
})

test('两工作台作业页存在且 mode 感知（契约 §7）', () => {
  assert.match(homeroomPage, /HomeworkWorkspace mode="homeroom"/, '班主任作业页必须以 homeroom 域渲染容器')
  assert.match(teachingPage, /HomeworkWorkspace mode="teaching"/, '教学作业页必须以 teaching 域渲染容器')
  assert.match(workspace, /useWorkspace\(\)/, '容器必须读工作台上下文')
  assert.match(workspace, /generation/, '容器必须消费工作台世代号')
  assert.match(workspace, /homeworkScopeQuery/, '作用域参数须经 homeworkScopeQuery 从 filter 映射')
  // 预警/相关性并入标签页（旧 /homework/warnings 路由被旧版占用不删不改）
  for (const tab of ['作业录入', '批次列表', '缺交预警', '相关性', '学期设置']) {
    assert.match(workspace, new RegExp(tab), `应包含「${tab}」标签`)
  }
  assert.match(workspace, /\?tab=/, '应支持 ?tab= 深链直达标签')
  // 教学域显式班选择透传给录入面板（teaching_class_id 为 'all'/缺省时不传，由后端解析）
  assert.match(workspace, /typeof filter\.teaching_class_id === 'number' \? filter\.teaching_class_id : undefined/, '容器须把显式教学班 id 传给录入面板')
})

test('录入面板：三模式、existing_batches 提示、两段式与 409 重预览（契约 §1）', () => {
  // 三模式与模式切换清空
  for (const kind of ["'full'", "'names'", "'detailed'"]) {
    assert.match(entryPanel, new RegExp(kind), `应支持录入模式 ${kind}`)
  }
  assert.match(entryPanel, /switchKind/, '应有模式切换逻辑')
  assert.match(entryPanel, /exceptions: \[\], rows: \[\], namesText: ''/, '模式切换必须清空各模式输入行')
  // 例外行（full）/ 名单 textarea（names）/ 逐行明细（detailed）
  assert.match(entryPanel, /添加例外/, 'full 模式须有例外行编辑')
  assert.match(entryPanel, /已交名单/, 'names 模式须有名单输入')
  assert.match(entryPanel, /逐行明细/, 'detailed 模式须有行编辑器')
  // 教学域 subject 只读固定任教学科
  assert.match(entryPanel, /disabled=\{teaching\}/, '教学域学科输入必须只读')
  // 两段式 + 幂等说明
  assert.match(entryPanel, /homeworkPreview/, '预览须走 homeworkPreview')
  assert.match(entryPanel, /homeworkConfirm/, '确认须走 homeworkConfirm')
  assert.match(entryPanel, /token/, '确认必须携带 preview 颁发的 token')
  assert.match(entryPanel, /重新预览/, '409 须提供重新预览')
  // existing_batches 醒目提示：编辑既有或新建，绝不静默叠加（H02）
  assert.match(entryPanel, /existing_batches/, '须消费 existing_batches 字段')
  assert.match(entryPanel, /绝不自动叠加/, '既有批次提示必须声明不自动叠加')
  // 同名歧义候选（学号消歧）
  assert.match(entryPanel, /readHomeworkAmbiguityCandidates/, '422 同名歧义须读候选清单')
  assert.match(entryPanel, /学号/, '候选提示须引导用学号消歧')
  // 教学域多班：显式 teaching_class_id 随请求携带（写入绝不往并集写），多班 422 引导先选班
  assert.match(entryPanel, /teaching_class_id: teachingClassId/, '教学域 preview 须携带显式教学班 id')
  assert.match(entryPanel, /请先在工作台顶部选择具体班级/, '多班 422 须引导先选班')
  // names/detailed 空输入前端先行拦截
  assert.match(entryPanel, /请填写至少一名学生/, 'names 空名单须前端拦截')
  assert.match(entryPanel, /请至少添加一行明细/, 'detailed 空行须前端拦截')
})

test('录入/批次表单走 page-draft 草稿，按工作台分键，确认成功清除', () => {
  assert.match(entryPanel, /savePageDraft/, '录入表单须暂存草稿')
  assert.match(entryPanel, /loadPageDraft/, '重新进入须恢复草稿')
  assert.match(entryPanel, /clearPageDraft/, '确认成功后须清除草稿')
  assert.match(entryPanel, /\/\$\{mode\}\/homework/, '草稿键必须按工作台分路由（防两工作台互串）')
})

test('批次列表：rate 不可计算文案、乐观锁 409、撤销冲突清单（契约 §1.3/§2）', () => {
  // H03 红线文案在 shared.ts 统一，批次表与看板都用它
  assert.match(homeworkShared, /无法计算（无可靠分母）/, '必须使用「无法计算（无可靠分母）」文案')
  // formatSubmissionRate 函数体：不可计算分支必须先于百分比计算，绝不落入 0%
  const rateFn = homeworkShared.match(/export function formatSubmissionRate[\s\S]*?\n}/)?.[0] ?? ''
  assert.match(rateFn, /unavailable \|\| rate == null/, '不可计算分支必须覆盖 unavailable 与 null')
  assert.match(rateFn, /无法计算（无可靠分母）/, '不可计算分支返回固定文案')
  assert.match(rateFn, /toFixed\(1\)\}%/, '可计算分支才显示百分比')
  assert.match(assignmentTable, /formatSubmissionRate/, '提交率展示必须走统一格式化')
  assert.match(assignmentTable, /rate_unavailable/, '须消费 rate_unavailable 字段')
  assert.match(assignmentTable, /overflow-x-auto/, '批次宽表须横向滚动')
  // 编辑：PATCH 乐观锁，409 重新拉详情（绝不拿旧 revision 死重试）
  assert.match(assignmentTable, /homeworkPatchAssignment/, '编辑须走 PATCH')
  assert.match(assignmentTable, /revision: detail\.revision/, 'PATCH 必须携带当前 revision（乐观锁）')
  assert.match(assignmentTable, /版本冲突/, '409 须提示版本冲突')
  assert.match(assignmentTable, /reloadDetail/, '409 后须重新拉取详情取最新 revision')
  // 撤销：confirm + 409 冲突清单原样展示
  assert.match(assignmentTable, /window\.confirm/, '撤销前必须 confirm')
  assert.match(assignmentTable, /homeworkDeleteAssignment/, '撤销须走 DELETE')
  assert.match(assignmentTable, /readHomeworkRevokeConflicts/, '409 须解析撤销冲突清单')
  assert.match(assignmentTable, /该行有后续评价编辑/, '冲突清单须标注后续评价编辑')
  // 看板：按周/月聚合 + 分母口径说明
  assert.match(assignmentTable, /homeworkDashboard/, '看板须走 dashboard 端点')
  assert.match(assignmentTable, /groupBy/, '看板须支持按周/月切换')
})

test('预警时间轴：streak_basis=unknown 标注、事件口径说明（契约 §3，H03）', () => {
  assert.match(warningsPanel, /homeworkWarnings/, '预警须走 warnings 端点')
  assert.match(warningsPanel, /min_missing/, '须暴露 min_missing 参数')
  assert.match(warningsPanel, /streak_basis === 'unknown'/, '须按 streak_basis=unknown 分支标注')
  assert.match(warningsPanel, /连续性未知/, '须标注「连续性未知」')
  assert.match(warningsPanel, /不冒充连续，也不断言已交/, '口径说明须声明 unknown 双不语义')
  assert.match(warningsPanel, /basis=/, '须展示响应的 basis 口径（events）')
  assert.match(warningsPanel, /不按日折算/, '事件口径须说明不按日折算')
  assert.match(warningsPanel, /recent_missing/, '须渲染最近缺交时间轴')
  assert.match(warningsPanel, /disabled=\{teaching\}/, '教学域学科过滤须只读固定')
})

test('相关性卡：r=null 不可计算态、方向文案、免责声明（契约 §4）', () => {
  assert.match(correlationCard, /homeworkCorrelation/, '相关性须走 correlation 端点')
  assert.match(correlationCard, /corr\.r == null/, '须对 r=null 分支')
  assert.match(correlationCard, /相关系数不可计算（样本不足或零方差）/, '须显示不可计算文案')
  assert.match(correlationCard, /correlationDirectionLabel/, '方向文案须由 direction 驱动')
  assert.match(correlationCard, /ScatterChart/, '须用 recharts 散点')
  assert.match(correlationCard, /reversed/, 'y 轴反转：名次小（好）朝上')
  assert.match(correlationCard, /不构成因果/, '必须渲染免责声明')
  assert.match(correlationCard, /caveats/, '后端 caveats 必须逐条展示')
})

test('学期设置卡：auto 推导标注、手工编辑、设当前/恢复自动、422 中文展示（契约 §5）', () => {
  for (const fn of [
    'homeworkSemesters',
    'homeworkCreateSemester',
    'homeworkUpdateSemester',
    'homeworkSetCurrentSemester',
    'homeworkRestoreAutoSemester',
  ]) {
    assert.match(semesterCard, new RegExp(fn), `学期卡须使用 ${fn}`)
  }
  assert.match(semesterCard, /自动推导/, 'auto 条目须标注自动推导')
  assert.match(semesterCard, /s\.id != null/, 'id=null 的 auto 推导条目不得提供编辑/设当前入口')
  assert.match(semesterCard, /status === 422/, '422 须单独处理')
  assert.match(semesterCard, /err\.detail/, '422 优先展示后端中文 detail（重名/重叠/重复设当前）')
  assert.match(semesterCard, /window\.confirm/, '恢复自动前必须 confirm')
  assert.match(semesterCard, /listAcademicYears/, '学年下拉须走学年清单端点')
})

test('请求序号按资源分离（F11）：列表/看板/详情/考试清单各比对其序号', () => {
  assert.match(assignmentTable, /listReqRef/, '批次列表须有独立请求序号')
  assert.match(assignmentTable, /dashReqRef/, '看板须有独立请求序号')
  assert.match(assignmentTable, /detailReqRef/, '行详情须有独立请求序号')
  assert.match(correlationCard, /examsReqRef/, '考试清单须有独立请求序号')
  assert.match(correlationCard, /corrReqRef/, '相关性须有独立请求序号')
  assert.match(warningsPanel, /reqRef/, '预警须有独立请求序号')
  // 回包必须比对序号后再落状态（防迟到回包覆盖新数据）
  assert.match(assignmentTable, /req !== listReqRef\.current/, '列表回包须比对序号')
  assert.match(assignmentTable, /req !== dashReqRef\.current/, '看板回包须比对序号')
  assert.match(assignmentTable, /req !== detailReqRef\.current/, '详情回包须比对序号')
})

test('Sidebar 导航：两工作台作业跟进 + 共享缺交预警入口', () => {
  assert.match(sidebar, /href: '\/homeroom\/homework'/, '须有班主任作业跟进入口')
  assert.match(sidebar, /href: '\/teaching\/homework'/, '须有教学作业跟进入口')
  assert.match(sidebar, /href: '\/homeroom\/homework\?tab=warnings'/, '须有缺交预警深链入口（带工作台路径）')
  assert.match(sidebar, /作业跟进（班主任）/, '班主任入口文案')
  assert.match(sidebar, /作业跟进（教学）/, '教学入口文案')
})
