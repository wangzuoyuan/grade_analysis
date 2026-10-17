import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
const source = readFileSync(new URL('../src/lib/student-homework.ts', import.meta.url), 'utf8')
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const scope = { exports: {} }
vm.runInNewContext(output, scope)
const { personalAchievement, personalReason } = scope.exports

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
