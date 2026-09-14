// Q10 收口契约测试：跨域冲突规范值确认（契约 docs/contracts/p3-imports-analysis.md §1.6 v2.3）。
// 风格沿用 scores-p3.test.mjs：直接读源码断言关键结构，不启动浏览器。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const apiV1 = readFileSync(new URL('../src/lib/api-v1.ts', import.meta.url), 'utf8')
const homeroomScores = readFileSync(new URL('../src/components/scores/HomeroomScores.tsx', import.meta.url), 'utf8')

test('api-v1 导出 canonical 端点封装与类型（契约 §1.6 v2.3）', () => {
  for (const fn of ['listScoreConflicts', 'confirmCanonicalScores']) {
    assert.match(apiV1, new RegExp(`export function ${fn}\\(`), `应导出 ${fn}`)
  }
  for (const t of ['ScoreConflictItem', 'ScoreConflictsResponse', 'CanonicalSide', 'CanonicalResolution', 'CanonicalResult']) {
    assert.match(apiV1, new RegExp(`export (interface|type) ${t}\\b`), `应导出类型 ${t}`)
  }
  assert.match(
    apiV1,
    /\/shared\/links\/\$\{encodeURIComponent\(String\(linkId\)\)\}\/score-conflicts/,
    'GET 端点应挂在 /shared/links/{link_id}/score-conflicts',
  )
  assert.match(
    apiV1,
    /\/shared\/links\/\$\{encodeURIComponent\(String\(linkId\)\)\}\/canonical-scores/,
    'POST 端点应挂在 /shared/links/{link_id}/canonical-scores',
  )
  // 请求体形状（v2.3）：resolutions 数组按来源侧选择（canonical_side）
  assert.match(apiV1, /JSON\.stringify\(\{ resolutions \}\)/, '确认请求体必须是 { resolutions }')
  assert.match(
    apiV1,
    /canonical_side: CanonicalSide/,
    '确认条目必须是 canonical_side（按域选择，废除 canonical_score）',
  )
  assert.doesNotMatch(apiV1, /canonical_score/, 'canonical_score 请求字段已废除，不得残留')
  // 冲突条目含两域现值（缺考 null），前端不转 0
  assert.match(apiV1, /homeroom_score: number \| null/, '冲突条目须有班主任域现值')
  assert.match(apiV1, /teaching_score: number \| null/, '冲突条目须有教学域现值')
})

test('成绩页冲突格提供确认入口：点击弹窗列两域值与来源（契约 §1.6）', () => {
  assert.match(homeroomScores, /listScoreConflicts/, '弹窗数据须来自 GET score-conflicts')
  assert.match(homeroomScores, /confirmCanonicalScores/, '提交须走 POST canonical-scores')
  assert.match(homeroomScores, /确认规范值/, '冲突格/弹窗须有规范值确认文案')
  assert.match(homeroomScores, /班主任域/, '弹窗须标注班主任域来源')
  assert.match(homeroomScores, /教学域/, '弹窗须标注教学域来源')
  assert.match(homeroomScores, /Dialog/, '确认入口必须是弹窗（Dialog）')
  // 冲突格点击不得触发行展开（stopPropagation）
  assert.match(homeroomScores, /stopPropagation/, '冲突格点击须阻止行展开冒泡')
})

test('确认成功后刷新 stats/students，冲突标记消失（契约 §1.6）', () => {
  assert.match(
    homeroomScores,
    /setDataNonce\(\(n\) => n \+ 1\)/,
    '确认成功后必须递增 dataNonce 触发 stats/students 重拉',
  )
  assert.match(
    homeroomScores,
    /\[selectedExam, scopeQ, dataNonce\]/,
    'stats/students effect 须依赖 dataNonce',
  )
})

test('按域选择（v2.3）：任一侧均可选，缺考侧标注「确认为缺考」', () => {
  // 单选行按域选择：每域一个 radio，选定来源侧而非具体数值
  assert.match(homeroomScores, /type="radio"/, '两域选择必须是按域单选')
  assert.doesNotMatch(homeroomScores, /type="number"/, '不得提供手工改分输入框')
  assert.match(
    homeroomScores,
    /checked=\{value === o\.domain\}/,
    '选中态必须按来源侧判定（value === o.domain）',
  )
  assert.match(
    homeroomScores,
    /确认为缺考/,
    '缺考侧必须可选并标注「确认为缺考」（null 是合法规范结果）',
  )
  assert.doesNotMatch(
    homeroomScores,
    /disabled=\{o\.score == null\}/,
    '缺考侧不得再被禁用（V04：缺考可确认为规范值）',
  )
  // 提交组装 canonical_side
  assert.match(
    homeroomScores,
    /canonical_side: side/,
    '提交必须组装 canonical_side（来源侧）',
  )
})

test('批量入口：工具栏冲突清单按钮 + 全量条目逐条确认', () => {
  assert.match(homeroomScores, /冲突清单/, '工具栏须有冲突清单批量入口')
  assert.match(homeroomScores, /全选班主任域值|全选教学域值/, '批量入口须有快捷全选')
})
