// P2-C2 教师行动首页契约测试（契约 docs/diagnosis-roadmap/p2-contracts.md §3/§6）。
// 前端侧断言：
// - homeroom/page.tsx 装配行动首页卡且置顶（优先关注列表在页首位置）；
// - 行动卡取数只走 action-summary 契约端点（后端从 B1 class_features +
//   B2 classify_student 同源生成；前端绝不复算指标/类型/排序）；
// - 优先关注（姓名+理由+直达学生页）+ 趋势/结构/作业/待办四段摘要齐备；
// - 数据缺失态（请求失败/无学生/无优先关注）如实显示，绝不伪造。
// 后端侧（排序/形状/同源/作用域隔离）见 backend/tests/v1/test_p2_c2_action.py。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const card = readFileSync(new URL('../src/app/homeroom/action-summary.tsx', import.meta.url), 'utf8')
const homeroomPage = readFileSync(new URL('../src/app/homeroom/page.tsx', import.meta.url), 'utf8')
// 去注释源码（负向断言只针对代码，不误伤说明文档里的口径引用）
const cardCode = card.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')

test('P2-C2：行动首页卡取数只走 action-summary 契约端点（同源，前端不复算）', () => {
  assert.match(card, /\/api\/v1\/homeroom\/diagnosis\/action-summary/, '端点路径必须与契约 §3 一致')
  assert.match(card, /academic_year_id/, '查询参数=可选 academic_year_id（契约 §3）')
  assert.match(card, /class_id/, '查询参数=可选 class_id（契约 §3）')
  assert.match(card, /summary\?\.calc_version/, '读取响应口径版本（A7：正文不直出，收进 title）')
  assert.doesNotMatch(
    cardCode.replace(/title=\{[^}]*\}/g, ''),
    /口径 \{summary\?\.calc_version/,
    '口径版本号不得再出现在正文副标题（A7 汉化裁决）',
  )
  // 同源红线：前端不做第二套计算——不请求 features/types 端点、无类型判定逻辑
  assert.doesNotMatch(cardCode, /diagnosis\/types/, '前端不得直连 B2 types 端点（摘要同源唯一入口）')
  assert.doesNotMatch(cardCode, /diagnosis\/features/, '前端不得直连 B1 features 端点（摘要同源唯一入口）')
  assert.doesNotMatch(cardCode, /classify|main_type|direction_recent|band_flags/, '前端不得复算类型/指标')
})

test('P2-C2：契约 §3 冻结形状（响应类型 + sections 四段键）', () => {
  for (const key of ['calc_version', 'as_of', 'priority_persons', 'sections']) {
    assert.match(card, new RegExp(key), `响应顶层键 ${key}`)
  }
  for (const key of [
    'reasons',
    'evidence_ref',
    'trend_changes',
    'improving_n',
    'declining_n',
    'structure',
    'band_counts',
    'high_score',
    'critical',
    'weak',
    'homework',
    'risk_n',
    'missing_30d_total',
    'follow_ups',
    'open_n',
    'due_this_week',
  ]) {
    assert.match(card, new RegExp(key), `契约 §3 形状键 ${key}`)
  }
})

test('P2-C2：优先关注置顶（姓名+理由+直达学生页）', () => {
  assert.match(card, /优先关注/, '优先关注区块')
  assert.match(card, /item\.name/, '姓名展示（契约 §3：姓名+理由）')
  assert.match(card, /item\.reasons\.map/, '理由逐条展示（有理由、可追溯）')
  assert.match(card, /\/homeroom\/students\/\$\{encodeURIComponent\(String\(item\.person_id\)\)\}/, '直达学生页链接')
  assert.match(card, /查看档案/, '直达入口文案')
})

test('P2-C2：趋势/结构/作业/待办四段摘要齐备', () => {
  for (const label of ['趋势', '结构', '作业', '待办']) {
    assert.match(card, new RegExp(`'${label}'`), `摘要段「${label}」`)
  }
  assert.match(card, /actionSectionRows/, '摘要行纯函数（契约形状驱动渲染）')
  assert.match(card, /进步 \$\{trend\.improving_n\} 人 · 退步 \$\{trend\.declining_n\} 人/, '趋势摘要值')
  assert.match(card, /高分段 \$\{bands\.high_score\} · 临界 \$\{bands\.critical\} · 薄弱 \$\{bands\.weak\}/, '结构摘要值')
  assert.match(card, /作业风险 \$\{homework\.risk_n\} 人 · 30 天缺交合计 \$\{homework\.missing_30d_total\} 次/, '作业摘要值')
  assert.match(card, /未关闭跟进 \$\{follow\.open_n\} 项 · 本周关注 \$\{follow\.due_this_week\} 项/, '待办摘要值')
})

test('P2-C2：数据缺失态如实显示（不伪造、不估算）', () => {
  assert.match(card, /行动摘要暂不可用（数据缺失）/, '请求失败缺失态')
  assert.match(card, /不做估算填充/, '缺失态不填充承诺')
  assert.match(card, /当前范围暂无学生，无法生成行动摘要/, '范围无学生空态')
  assert.match(card, /当前无综合风险\/持续下滑\/临界下滑\/作业风险学生/, '优先关注为空空态')
  assert.match(card, /不构成因果或提分保证/, '免责措辞（红线：不表述为因果/保证）')
})

test('P2-C2：homeroom 首页装配行动卡且置顶（优先关注列表在页首）', () => {
  assert.match(homeroomPage, /import \{ HomeroomActionSummaryCard \} from '\.\/action-summary'/, '行动卡导入')
  assert.match(homeroomPage, /<HomeroomActionSummaryCard \/>/, '行动卡装配')
  const summaryIdx = homeroomPage.indexOf('<HomeroomActionSummaryCard />')
  const overviewIdx = homeroomPage.indexOf('<HomeroomOverview />')
  const diagnosisIdx = homeroomPage.indexOf('<HomeroomDiagnosisOverviewCard />')
  assert.ok(summaryIdx !== -1 && summaryIdx < overviewIdx, '行动卡在总览卡之前（置顶）')
  assert.ok(diagnosisIdx === -1 || summaryIdx < diagnosisIdx, '行动卡在类型分布卡之前（置顶）')
})
