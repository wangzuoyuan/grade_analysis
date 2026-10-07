// 仪表盘卡片级折叠（2026-09-30 用户反馈「每个板块长的有点厉害，多折叠一些」）：
// 每个顶层板块卡头有折叠开关（useCardFold 本地记忆），CardContent 随 folded 卸载；
// 行动首页优先关注列表默认 4 条、超出走 MoreToggle（沿用列表折叠惯例）。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8')
const fold = read('../src/components/dashboard/card-fold.tsx')
const actionSummary = read('../src/app/homeroom/action-summary.tsx')
const dashboard = read('../src/components/dashboard/WorkspaceDataDashboard.tsx')
const weekly = read('../src/components/WeeklyFocusCard.tsx')
const diagnosis = read('../src/components/student/DiagnosisCard.tsx')

test('折叠基础设施：useCardFold 本地记忆 + CardFoldToggle 无障碍语义', () => {
  assert.match(fold, /dashboard-fold:/, 'localStorage 键前缀统一')
  assert.match(fold, /setStored\(raw == null \? defaultFolded : raw === '1'\)/, '未记录时用默认值')
  assert.match(fold, /aria-expanded=\{!folded\}/, '折叠按钮带 aria-expanded')
  assert.match(fold, /print:hidden/, '折叠按钮不进打印')
})

test('七个板块卡全部接入折叠（键名按工作台区分）', () => {
  const expects = [
    [actionSummary, /useCardFold\('homeroom:action-summary'\)/, '行动首页'],
    [dashboard, /useCardFold\(`\$\{mode\}:trend`\)/, '成绩趋势'],
    [dashboard, /useCardFold\(`\$\{mode\}:subject-latest`\)/, '学科表现/教学班对比'],
    [dashboard, /useCardFold\(`\$\{mode\}:focus-students`\)/, '需关注学生'],
    [dashboard, /useCardFold\(`\$\{mode\}:homework-warnings`\)/, '重点关注（空态/常态共用一键）'],
    [weekly, /useCardFold\('homeroom:weekly-focus'\)/, '本周关注'],
    [diagnosis, /useCardFold\(\s*'homeroom:diagnosis-overview',?\s*\)/, '学情类型分布'],
  ]
  for (const [src, re, name] of expects) {
    assert.ok(re.test(src), `${name} 须接入 useCardFold`)
  }
  // 折叠时内容卸载：每处 CardFoldToggle 配套「folded ? null : (<CardContent」
  for (const [src, name] of [
    [actionSummary, '行动首页'],
    [dashboard, '数据看板'],
    [weekly, '本周关注'],
    [diagnosis, '类型分布'],
  ]) {
    const toggles = (src.match(/CardFoldToggle /g) ?? []).length
    const wraps = (src.match(/[Ff]olded \? null : \(/g) ?? []).length
    assert.ok(
      toggles >= 1 && wraps >= toggles,
      `${name}：每个开关都要有条件渲染（toggles=${toggles}, wraps=${wraps}）`,
    )
  }
})

test('行动首页优先关注默认 4 条，超出走 MoreToggle（沿用列表折叠惯例）', () => {
  assert.match(actionSummary, /priority\.slice\(0, 4\)/, '默认截断 4 人')
  assert.match(actionSummary, /hiddenCount=\{priority\.length - 4\}/, 'MoreToggle 报告隐藏数')
})
