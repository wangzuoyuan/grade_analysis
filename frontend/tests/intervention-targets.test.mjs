import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from 'typescript'
const source = readFileSync(new URL('../src/components/student/intervention-targets.ts', import.meta.url), 'utf8')
const exports = {}
vm.runInNewContext(ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText, {exports})
const {buildTargetPayload} = exports

test('multi targets: mixed units and valid zero are independent', () => {
  const result = buildTargetPayload([
    {metric:'subject:语文',baseline:'40'}, {metric:'subject:数学',baseline:'0'},
    {metric:'total:主三门',baseline:'300'}, {metric:'subject_grade:物理',baseline:'75'},
  ])
  assert.deepEqual(Array.from(result, r => r.baseline_value.value), [.4,0,300,75])
  assert.deepEqual(Array.from(result, r => r.baseline_value.unit), ['percentile','percentile','rank','grade_score'])
})
test('editing and closing preserve baseline by omission, new metrics capture on server', () => {
  const result = buildTargetPayload([{metric:'subject:语文',baseline:'',original:{status:'done',baseline_value:{value:.4}}},
    {metric:'subject:数学',baseline:''}])
  assert.equal(result[0].status, 'done')
  assert.equal(result[1].status, 'open')
  assert.equal('baseline_value' in result[0], false)
  assert.equal('baseline_value' in result[1], false)
})
test('invalid rank, percentile, and nonnumeric input cannot be submitted', () => {
  for (const [metric, baseline] of [['total:主三门','1.5'],['total:主三门','0'],['subject:数学','101'],['subject:语文','-1'],['subject_grade:物理','abc']]) {
    assert.throws(() => buildTargetPayload([{metric,baseline}]))
  }
})
