import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

function loadSource(name, dependencies = {}) {
  const source = readFileSync(new URL('../src/lib/' + name + '.ts', import.meta.url), 'utf8')
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText
  const module = { exports: {} }
  vm.runInNewContext(output, {
    module, exports: module.exports, Intl,
    require: id => {
      assert(id in dependencies, 'Unexpected dependency: ' + id)
      return dependencies[id]
    },
  })
  return module.exports
}

const { compareStudentIds, compareStudentNames } = loadSource('student-order', {
  './student-id': loadSource('student-id'),
})
const ordered = (rows, compare) => rows.slice().sort(compare).map(row => row.person_id)

test('成绩明细按显示学号自然升序，忽略成绩高低并剥除跨届前缀', () => {
  const rows = [
    { person_id: 1, alias: '100', totals: { '3+3': 600 } },
    { person_id: 2, alias: 'G1::2', totals: { '3+3': null } },
    { person_id: 3, alias: 'g2-10', totals: { '3+3': 0 } },
  ]
  assert.deepEqual(ordered(rows, compareStudentIds), [2, 3, 1])
  assert.deepEqual(rows.map(row => row.person_id), [1, 2, 3], '不修改接口数组')
})

test('无学号排最后，空串与空白等同缺失，组内按姓名拼音排序', () => {
  const rows = [
    { person_id: 1, alias: ' ', name: '张乙' },
    { person_id: 2, alias: null, name: '陈乙' },
    { person_id: 3, alias: 'SYN9', name: '赵甲' },
    { person_id: 4, alias: '', name: '王甲' },
  ]
  assert.deepEqual(ordered(rows, compareStudentIds), [3, 2, 4, 1])
})

test('长数字学号保持精度，字母数字学号使用自然序', () => {
  const rows = [
    { person_id: 1, alias: '9007199254740993' },
    { person_id: 2, alias: '9007199254740992' },
    { person_id: 3, alias: 'SYN10' },
    { person_id: 4, alias: 'SYN2' },
  ]
  const result = ordered(rows, compareStudentIds)
  assert(result.indexOf(2) < result.indexOf(1))
  assert(result.indexOf(4) < result.indexOf(3))
})

test('教学作业姓名按姓氏拼音排序，同姓再按名字拼音', () => {
  const rows = [
    { person_id: 1, name: '张乙' },
    { person_id: 2, name: '王乙' },
    { person_id: 3, name: '陈乙' },
    { person_id: 4, name: '张甲' },
    { person_id: 5, name: '李甲' },
  ]
  assert.deepEqual(ordered(rows, compareStudentNames), [3, 5, 2, 4, 1])
})

test('同名和同学号以身份 ID 稳定排序，缺姓名排在最后', () => {
  const rows = [
    { person_id: '10', name: '陈甲', alias: '02' },
    { person_id: '2', name: '陈甲', alias: '2' },
    { person_id: '3', name: null, alias: '2' },
  ]
  assert.deepEqual(ordered(rows, compareStudentIds), ['2', '10', '3'])
  assert.deepEqual(ordered(rows, compareStudentNames), ['2', '10', '3'])
})

test('筛选与修改收交状态不改变同一组姓名的相对顺序', () => {
  const rows = [
    { person_id: 1, name: '张甲', status: 'missing' },
    { person_id: 2, name: '陈甲', status: 'submitted' },
    { person_id: 3, name: '李甲', status: 'missing' },
  ]
  assert.deepEqual(ordered(rows.filter(row => row.status === 'missing'), compareStudentNames), [3, 1])
  rows[1].status = 'missing'
  assert.deepEqual(ordered(rows.filter(row => row.status === 'missing'), compareStudentNames), [2, 3, 1])
})
