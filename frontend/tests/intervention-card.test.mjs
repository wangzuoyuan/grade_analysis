// P2-C4 轻量干预与自动复查：前端契约测试（契约 docs/diagnosis-roadmap/p2-contracts.md §5）。
// 风格沿用 tests/diagnosis-card.test.mjs：readFileSync + assert 断言关键结构，不启动浏览器。
// 断言：
// - 干预卡取数只走 P2-C4 契约端点（notes 域内 CRUD + diagnosis/review-contrast）；
// - 创建/查看/关闭三态齐备，防重复录入提示（409 duplicate_follow_up → existing 明细 + force 二次确认）；
// - 复查对照 ready/pending 两态如实渲染，pending 展示原因；
// - 绝不出现成功/失败判定措辞（后端不判定，前端不演绎）；
// - 装配点：班主任学生档案 + 教学学生页各挂一处；首页（C2 领地）不动；
// - api-v1 类型/请求形状与后端契约字段逐一对齐。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const card = readFileSync(new URL('../src/components/student/InterventionCard.tsx', import.meta.url), 'utf8')
const apiV1 = readFileSync(new URL('../src/lib/api-v1.ts', import.meta.url), 'utf8')
const profileView = readFileSync(new URL('../src/components/students/HomeroomProfileView.tsx', import.meta.url), 'utf8')
const teachingStudentPage = readFileSync(new URL('../src/app/student/[id]/page.tsx', import.meta.url), 'utf8')
const homeroomPage = readFileSync(new URL('../src/app/homeroom/page.tsx', import.meta.url), 'utf8')

test('P2-C4：干预卡取数只走契约端点（notes 域内 + review-contrast）', () => {
  assert.match(card, /createFollowUp/, '创建走 createFollowUp（干预建档端点）')
  assert.match(card, /patchFollowUp/, '关闭走 patchFollowUp（status 关闭路径）')
  assert.match(card, /listStudentNotes/, '查看走域内档案列表（N01 同源）')
  assert.match(card, /fetchReviewContrast/, '复查对照走 fetchReviewContrast')
  assert.match(apiV1, /diagnosis\/review-contrast/, 'review-contrast 端点路径与契约 §5.2 一致')
  assert.match(apiV1, /follow_up_id: followUpId/, '查询参数 follow_up_id')
  assert.match(apiV1, /p2-v1/, '口径版本 p2-v1 注记')
})

test('P2-C4：创建/查看/关闭三态齐备（契约 §5.1/§5.3）', () => {
  // §5.1 全部可选字段在创建表单齐备
  for (const field of ['problem', 'subject_scope', 'measures', 'target_metric', 'baseline_value', 'start_date', 'review_date']) {
    assert.match(card, new RegExp(`${field}:`), `创建请求携带 ${field}`)
  }
  assert.match(card, /新建干预/, '创建入口')
  assert.match(card, /标记完成并关闭/, '关闭入口（done）')
  assert.match(card, /不再跟进/, '关闭入口（dismissed）')
  assert.match(apiV1, /status\?: 'open' \| 'done' \| 'dismissed'/, 'status 三态类型')
})

test('P2-C4：防重复录入提示（409 duplicate_follow_up → existing + force 二次确认）', () => {
  assert.match(card, /duplicate_follow_up/, '识别重复错误码')
  assert.match(card, /该生同科目已有进行中的干预/, '面向教师的重复提示文案')
  assert.match(card, /该生已有进行中的同科干预/, 'existing 明细标题')
  assert.match(card, /确认知情，仍要创建/, 'force 二次确认入口')
  assert.match(card, /submit\(true\)/, '确认知情后带 force 重发')
  assert.match(apiV1, /force\?: boolean/, 'force 参数进请求体')
})

test('P2-C4：复查对照两态如实渲染，绝不判定成功/失败', () => {
  assert.match(card, /contrast\.status === 'pending'/, 'pending 分支')
  assert.match(card, /暂不可对照（pending）/, 'pending 如实标注')
  assert.match(card, /contrast\.note\}/, 'pending 展示后端 reason/note（缺考/无可比原因）')
  assert.match(card, /contrast\.baseline\?\.value/, 'ready 展示基线')
  assert.match(card, /contrast\.latest\?\.value/, 'ready 展示最新')
  assert.match(card, /changeText/, 'change 方向措辞走纯函数（只照读后端口径）')
  assert.match(card, /smaller_is_better/, '方向含义来自后端 smaller_is_better（不二次演绎）')
  // 绝不判定：卡片中不得出现成功/失败/见效/提分承诺（消极语境的纪律说明除外）
  assert.doesNotMatch(card, /已见效|提分|确保提升|承诺/, '前端不得承诺见效或提分')
  assert.match(card, /不自动判定干预成功或失败/, '明示判定纪律')
})

test('P2-C4：装配点=班主任学生档案 + 教学学生页；首页（C2 领地）不动', () => {
  assert.match(profileView, /import InterventionCard from '@\/components\/student\/InterventionCard'/, '班主任档案导入干预卡')
  assert.match(profileView, /<InterventionCard\s+mode="homeroom"/, '班主任档案挂载干预卡')
  assert.match(teachingStudentPage, /import InterventionCard from '@\/components\/student\/InterventionCard'/, '教学学生页导入干预卡')
  assert.match(teachingStudentPage, /<InterventionCard mode="teaching"/, '教学学生页挂载干预卡')
  assert.doesNotMatch(homeroomPage, /InterventionCard/, '首页不得被 C4 改动（C2 领地；到期提醒由 C2 follow_ups 呈现）')
})

test('Wave E 修复：创建与复查请求必须透传作用域（scopeQ），报告页入口齐备', () => {
  // Codex 审查 #4：创建/复查不传 scopeQ → 跨学年拿不到基线或报范围错误
  assert.match(card, /\}, scopeQ\)/, '创建干预请求携带 scopeQ')
  assert.match(card, /fetchReviewContrast\(mode, noteId, scopeQ\)/, '复查对照请求携带 scopeQ')
  assert.match(card, /<ContrastPanel mode=\{mode\} noteId=\{n\.id\} scopeQ=\{scopeQ\} \/>/, '对照面板接收并下传 scopeQ')
})

test('Wave E 修复：诊断报告入口（事实版 ↔ 诊断版互切 + 学生页直达）', () => {
  const factReportHomeroom = readFileSync(new URL('../src/app/homeroom/students/[id]/report/page.tsx', import.meta.url), 'utf8')
  const factReportTeaching = readFileSync(new URL('../src/app/student/[id]/report/page.tsx', import.meta.url), 'utf8')
  const diagnosisReport = readFileSync(new URL('../src/components/student/DiagnosisReport.tsx', import.meta.url), 'utf8')
  assert.match(factReportHomeroom, /diagnosis-report/, '班主任事实版报告 → 诊断版入口')
  assert.match(factReportTeaching, /diagnosis-report/, '教学事实版报告 → 诊断版入口')
  assert.match(diagnosisReport, /查看事实版报告/, '诊断版 → 事实版入口')
  assert.match(profileView, /diagnosis-report/, '班主任学生档案 → 诊断报告入口')
  assert.match(teachingStudentPage, /diagnosis-report/, '教学学生页 → 诊断报告入口')
})

test('P2-C4：api-v1 类型与后端契约字段逐一对齐', () => {
  for (const field of ['problem', 'subject_scope', 'measures', 'target_metric', 'baseline_value', 'start_date', 'review_date', 'status']) {
    assert.match(apiV1, new RegExp(`StudentNote`), 'StudentNote 类型存在')
    assert.match(apiV1, new RegExp(`\\b${field}\\?:`), `干预字段 ${field} 进入 api-v1 类型`)
  }
  assert.match(apiV1, /ReviewContrast/, 'ReviewContrast 类型')
  assert.match(apiV1, /status: 'ready' \| 'pending'/, '对照两态类型（后端契约 §5.2）')
  assert.match(apiV1, /reason\?: string/, 'pending reason 字段')
  assert.match(apiV1, /smaller_is_better: boolean/, 'change 方向口径字段')
  assert.match(apiV1, /calc_version: string/, '口径版本字段')
  assert.match(apiV1, /p2-v1/, 'p2-v1 口径版本注记（本波版本）')
})

test('Codex 复审 #3：名次手填基线拒绝非整数（前端第一道防线）', () => {
  const card = readFileSync(new URL('../src/components/student/InterventionCard.tsx', import.meta.url), 'utf8')
  assert.match(
    card,
    /!Number\.isInteger\(baselineNum\)/,
    '名次基线须做整数校验',
  )
  assert.match(card, /名次为整数（如 300），不支持小数。/, '非整数给出中文提示')
})
