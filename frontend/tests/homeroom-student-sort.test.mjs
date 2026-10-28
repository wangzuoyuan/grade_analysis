import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createRequire } from 'node:module'

const nativeRequire = createRequire(import.meta.url)
function load(file) {
  const url = new URL('../src/' + file, import.meta.url)
  const source = readFileSync(url, 'utf8')
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText
  const module = { exports: {} }
  vm.runInNewContext(code, {
    module, exports: module.exports, Intl,
    require: id => id.startsWith('./') ? load('lib/' + id.slice(2) + '.ts') :
      id === '@/lib/utils' ? { cn: (...args) => args.filter(Boolean).join(' ') } : nativeRequire(id),
  })
  return module.exports
}
const { sortHomeroomStudents: sort, studentSubjectGroups: groups } = load('lib/homeroom-score-sort.ts')
const { ScoreSortButton } = load('components/scores/ScoreSortButton.tsx')
const ids = rows => Array.from(rows, row => row.person_id)
const subjects = ['语文', '数学', '英语', '物理', '化学', '生物', '政治', '历史', '地理']
const student = (id, value) => ({
  person_id: id, alias: 'G2::SYN' + id, name: '合成学生',
  scores: Object.fromEntries(subjects.map(subject => [subject, value])),
  grade_scores: { 物理: value == null ? null : 100 - value },
  totals: Object.fromEntries(['+3', '主三门', '3+3', '五门', '九门'].map(total => [total, value])),
  ranks: { '主三门': id },
})

test('默认按显示学号自然升序，可从成绩序恢复，不改原行及原校名次', () => {
  const rows = [student(10, 90), student(2, 80), student(1, null)]
  const snapshot = JSON.stringify(rows)
  const sorted = sort(rows, { kind: 'totals', key: '主三门' })
  assert.deepEqual(ids(sorted), [10, 2, 1])
  assert.deepEqual(ids(sort(sorted, { kind: 'id' })), [1, 2, 10])
  assert.equal(JSON.stringify(rows), snapshot)
  assert.equal(sorted[0], rows[0], '保留原学生记录，成绩与排名不重算')
})

test('九科原始分都可单独降序，0 分有效、缺值置底，同分按学号自然序', () => {
  const rows = [student(10, 80), student(3, null), student(2, 80), student(1, 0)]
  for (const key of subjects) assert.deepEqual(ids(sort(rows, { kind: 'scores', key })), [2, 10, 1, 3], key)
})

test('选科等级分单独排序，绝不以原始分兜底或按原始分排序', () => {
  const rows = [student(1, 90), student(2, 70), student(3, 80)]
  rows[2].grade_scores = {}
  assert.deepEqual(ids(sort(rows, { kind: 'scores', key: '物理' })), [1, 3, 2])
  assert.deepEqual(ids(sort(rows, { kind: 'grade_scores', key: '物理' })), [2, 1, 3])
  for (const key of subjects.slice(3)) {
    rows[0].grade_scores[key] = 61
    rows[1].grade_scores[key] = 67
    rows[2].grade_scores[key] = null
    assert.deepEqual(ids(sort(rows, { kind: 'grade_scores', key })), [2, 1, 3], key)
  }
})

test('两套年级所有总分口径均可独立降序，取导入总分而非学科求和', () => {
  const rows = [student(1, 0), student(3, 80), student(2, 90), student(4, null)]
  for (const key of ['+3', '主三门', '3+3', '五门', '九门']) {
    rows[0].scores.语文 = 999
    assert.deepEqual(ids(sort(rows, { kind: 'totals', key })), [2, 3, 1, 4], key)
  }
})

test('全缺失字段和非有限值稳定回退学号序，缺学号按原有姓名规则置底', () => {
  const rows = [student(3, NaN), student(2, undefined), student(1, Infinity), student(4, null)]
  rows[0].alias = null
  assert.deepEqual(ids(sort(rows, { kind: 'scores', key: '数学' })), [1, 2, 4, 3])
  assert.deepEqual(ids(sort(rows, { kind: 'grade_scores', key: '化学' })), [1, 2, 4, 3])
})

test('高二/三普通学科键必须展开六选科原始分/等级分，即使部分或全部等级分缺失', () => {
  for (const grade of [2, 3]) {
    const result = groups(subjects, [student(1, null)], grade)
    assert.deepEqual(Array.from(result, group => group.subject), subjects)
    assert.deepEqual(Array.from(result, group => group.paired), [false, false, false, true, true, true, true, true, true])
    // 2026-10-28：组恒 2 子列——选科=原始分/等级分，语数英=分数/百分位（学校源表仅语数英带百分位）
    assert.deepEqual(Array.from(result, group => group.showPercentile), [true, true, true, false, false, false, false, false, false])
  }
})

test('高一不展开等级分；未知年级仅按实际等级分展开，不猜年级或补值', () => {
  const grade1 = groups(subjects, [student(1, 90)], 1)
  assert(grade1.every(group => !group.paired))
  // 2026-10-28：高一全科都带年级百分位（学校源表 9 科各一列），全部显示 分数/百分位
  assert(grade1.every(group => group.showPercentile))
  const result = groups(subjects, [student(1, 90)], null)
  assert.deepEqual(Array.from(result.filter(group => group.paired), group => group.subject), ['物理'])
  assert.equal(groups(['物理_原始', '物理_等级'], [student(1, 90)], 2).length, 1)
})

test('排序入口为原生按钮，有明确分数字段标签，学号入口方向独立', () => {
  const render = props => renderToStaticMarkup(React.createElement(ScoreSortButton, props, '等级分'))
  const html = render({ label: '物理等级分', active: true, onClick: () => {} })
  assert.match(html, /type="button"/)
  assert.match(html, /aria-label="物理等级分，从高到低排序"/)
  assert.match(html, /focus-visible:outline/)
  assert.match(render({ label: '学号', active: true, ascending: true, onClick: () => {} }), /aria-label="学号，按学号升序"/)
})
