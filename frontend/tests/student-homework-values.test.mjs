import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
const source = readFileSync(new URL('../src/lib/student-homework.ts', import.meta.url), 'utf8')
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const scope = { exports: {} }
vm.runInNewContext(output, scope)
const { personalAchievement, personalReason, subjectAchievementLabel } = scope.exports

test('个人成绩格式不把缺考变0，保留真实0等级分和百分位', () => {
  assert.equal(personalAchievement(null), '—')
  assert.equal(personalAchievement({ achievement: null, basis: 'grade_score' }), '—')
  assert.equal(personalAchievement({ achievement: 0, basis: 'grade_score' }), '0.0 等级分')
  assert.equal(personalAchievement({ achievement: 100, basis: 'grade_percentile', percentile: 0 }), '前 0.0%')
  assert.equal(personalAchievement({ achievement: 80, basis: 'grade_percentile', percentile: .2 }), '前 20.0%')
})

test('缺失指标、未知分母和不变作业给出不同解释', () => {
  assert.match(personalReason('grade_score_missing'), /等级分/)
  assert.match(personalReason('percentile_missing'), /年级百分位/)
  assert.match(personalReason('denominator_unknown_over_half'), /分母/)
  assert.match(personalReason('homework_unchanged'), /没有变化/)
  assert.equal(personalReason('future_reason'), 'future_reason')
})

test('subjectAchievementLabel：全科并列卡成绩口径——「前X%」取 percentile 本身（年级位置），等级分带单位，缺值不转 0（2026-10-22）', () => {
  assert.equal(subjectAchievementLabel(null), '—')
  assert.equal(subjectAchievementLabel(undefined), '—')
  assert.equal(subjectAchievementLabel({ basis: 'grade_percentile', achievement: null }), '—')
  // grade_percentile 口径：「前X%」= percentile×100（库存口径 = 年级位置 p≈rank/N，
  // 越小越好）；achievement 是换算后的 100×(1−p)，不得直接当「前%」展示
  assert.equal(subjectAchievementLabel({ basis: 'grade_percentile', achievement: 2, percentile: 0.98 }), '前98%')
  assert.equal(subjectAchievementLabel({ basis: 'grade_percentile', achievement: 87.5, percentile: 0.125 }), '前12.5%')
  // percentile 缺失时按 100−achievement 反推（achievement=100×(1−p)）
  assert.equal(subjectAchievementLabel({ basis: 'grade_percentile', achievement: 2 }), '前98%')
  // grade_score 口径：真实 0 等级分原样保留
  assert.equal(subjectAchievementLabel({ basis: 'grade_score', achievement: 0 }), '0.0 等级分')
  assert.equal(subjectAchievementLabel({ basis: 'grade_score', achievement: 46 }), '46.0 等级分')
  // basis 未知（null）：按等级分口径兜底展示数值
  assert.equal(subjectAchievementLabel({ basis: null, achievement: 88 }), '88.0 等级分')
})
