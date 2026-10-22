import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'

// 执行页面中的真实表格组件，验证合并格和数据位置，而不是检查源码写法。
const source = readFileSync(new URL('../src/components/scores/HomeroomScores.tsx', import.meta.url), 'utf8')
const ast = ts.createSourceFile('HomeroomScores.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
const names = new Set(['subjectColumnGroups', 'summaryNumber', 'ClassAveragesTable'])
const extracted = ast.statements.filter(node => ts.isFunctionDeclaration(node) && names.has(node.name?.text))
assert.equal(extracted.length, names.size)
const code = ts.transpileModule(extracted.map(node => node.getText(ast)).join('\n'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2020 },
}).outputText
const tags = {
  Card: 'section', CardContent: 'div', CardHeader: 'div', CardTitle: 'h2', CardDescription: 'p',
  Table: 'table', TableBody: 'tbody', TableHeader: 'thead', TableRow: 'tr', TableCell: 'td', TableHead: 'th',
  Button: 'button', Download: 'span', FileSpreadsheet: 'span',
}
const scope = {
  React, Fragment: React.Fragment,
  cn: (...args) => args.filter(Boolean).join(' '),
  tableStyles: Object.fromEntries(['table', 'classType', 'classNumber', 'currentClassNumber', 'summaryGroup', 'summaryLabel'].map(key => [key, key])),
  formatScore: value => value == null ? '—' : Number.isInteger(value) ? String(value) : value.toFixed(1),
  ...tags,
}
vm.runInNewContext(code, scope)

test('平行班汇总紧随本组成绩，实验班汇总紧随实验班，合并格与各列数值对应', () => {
  const makeRow = (class_num, class_type, score) => ({
    class_num, class_type, teacher_name: '合成教师',
    subjects: { '语文': score, '物理_原始': score, '物理_等级': score },
    // 后端仍下发五指标包；组件应只取 均分，率值/标准差一律不渲染（2026-10-22 拍板）
    totals: { '3+3': { '均分': score, '及格率': 80, '高分率': null, '低分率': 5, '标准差': 10 } },
    total_ranks: { '3+3': 1 },
  })
  const markup = renderToStaticMarkup(React.createElement(scope.ClassAveragesTable, { data: {
    exam_name: '合成布局测评', current_class_num: 2,
    subjects: ['语文', '物理_原始', '物理_等级'], total_types: ['3+3'],
    rows: [makeRow(1, '平行班', 60), makeRow(2, '平行班', 80), makeRow(3, '实验班', 90)],
  } }))
  const body = markup.match(/<tbody>([\s\S]*?)<\/tbody>/)[1]
  const rows = [...body.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/g)].map(match => match[1])
  assert.equal(rows.length, 9)
  assert.match(rows[0], /平行班/)
  assert.match(rows[1], /本班/)
  assert.match(rows[2], /rowspan="3"[^>]*>平行班汇总/)
  assert.match(rows[2], /colspan="2"[^>]*>平均/i)
  assert.match(rows[2], />70<\/td>/, '平均值来自本组两班')
  assert.match(rows[3], />80<\/td>/, '最高值来自本组')
  assert.match(rows[4], />60<\/td>/, '最低值来自本组')
  assert.match(rows[5], /实验班/)
  assert.match(rows[6], /rowspan="3"[^>]*>实验班汇总/)
  assert.match(rows[6], />90<\/td>/)
  assert.equal((body.match(/汇总<\/td>/g) ?? []).length, 2, '每组名称仅渲染一次')
  assert.doesNotMatch(body, /及格率|高分率|低分率|标准差|80%|>10<\/td>/, '总分率值/标准差列已删除：后端五指标包在场也不渲染')
})
