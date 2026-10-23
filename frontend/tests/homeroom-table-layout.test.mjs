import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'

// 执行页面中的真实表格组件，验证合并格和数据位置，而不是检查源码写法。
const source = readFileSync(new URL('../src/components/scores/HomeroomScores.tsx', import.meta.url), 'utf8')
const tableCss = readFileSync(new URL('../src/components/scores/homeroom-tables.module.css', import.meta.url), 'utf8')
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
  tableStyles: Object.fromEntries(['table', 'classType', 'classNumber', 'currentClassNumber', 'summaryGroup', 'summaryLabel', 'stickyCol'].map(key => [key, key])),
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
  // 2026-10-22 尺寸改造：限高双轴滚动 + 两级粘性表头（module.css 统一注入 top/z/行高）+ 冻结前两列
  assert.match(markup, /max-h-\[70vh\]/, '均分表容器限高 70vh')
  assert.match(markup, /overflow-auto/, '容器双轴滚动')
  assert.match(tableCss, /thead tr:first-child th\s*\{\s*position: sticky;\s*top: 0;\s*z-index: 30;/, '第一级表头吸顶（含 rowSpan 跨行格）')
  assert.match(tableCss, /thead tr:nth-child\(2\) th\s*\{\s*position: sticky;\s*top: 2rem;/, '第二级表头吸在第一级正下方（第一级行高 2rem）')
  assert.match(tableCss, /height: 2rem;/, '两级表头固定行高保证偏移对齐')
  assert.match(tableCss, /border-collapse: separate/, 'separate 边框：collapse 下粘性格边框不随定位移动')
  assert.match(tableCss, /td\.stickyCol\s*\{\s*background: inherit/, '冻结列背景继承所在行（斑马纹/汇总/悬浮同步，防滚动透字）')
  assert.match(markup, /sticky left-0 w-24/, '班级类型表头冻结在最左')
  assert.match(markup, /sticky left-24 w-16/, '班级表头冻结在类型列右侧（left=类型列宽 6rem）')
  assert.match(body, /sticky left-0 z-10 w-24/, '数据行班级类型格冻结')
  assert.match(body, /sticky left-24 z-10 w-16/, '数据行班级格冻结')
  assert.match(body, /stickyCol/, '冻结格带不透明背景类')
})

test('2026-10-23 汇总口径：0 = 该班无人选，平均/最高/最低只统计非零班（与学校汇总行一致）', () => {
  // 复现真实场景：平行班部分班选科列为 0（无人选）。学校表汇总排除 0 班；
  // 修复前 0 被当真实分计入除数（平均被拉低）且 最低 恒为 0。
  const makeRow = (class_num, chinese, physics) => ({
    class_num, class_type: '平行班', teacher_name: '合成教师',
    subjects: { '语文': chinese, '物理_原始': physics, '物理_等级': physics, '化学_原始': 0 },
    totals: { '3+3': { '均分': physics } },
    total_ranks: { '3+3': 1 },
  })
  const markup = renderToStaticMarkup(React.createElement(scope.ClassAveragesTable, { data: {
    exam_name: '合成汇总口径测评', current_class_num: 1,
    subjects: ['语文', '物理_原始', '物理_等级', '化学_原始'], total_types: ['3+3'],
    rows: [makeRow(1, 90, 60), makeRow(2, 88, 0), makeRow(3, 92, 80)],
  } }))
  const body = markup.match(/<tbody>([\s\S]*?)<\/tbody>/)[1]
  const rows = [...body.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/g)].map(match => match[1])
  assert.equal(rows.length, 6, '3 数据行 + 3 汇总行')
  assert.match(rows[3], /colspan="2"[^>]*>平均/i)
  // 物理/3+3：非零两班 60、80 → 平均 70（修复前 (60+0+80)/3=46.7）
  assert.match(rows[3], />70<\/td>/, '平均只除以非零班数')
  // 语文三班全有 → 平均 90（全有值科目不受影响）
  assert.match(rows[3], />90<\/td>/, '全有值列照常平均')
  assert.match(rows[4], />80<\/td>/, '最高为非零最高')
  // 最低 60（修复前 Math.min 含 0 恒为 0）
  assert.match(rows[5], />60<\/td>/, '最低排除 0 班')
  assert.doesNotMatch(rows[5], />0<\/td>/, '最低不得再显示 0')
  // 全组皆 0 的列（无班选）汇总显示 —
  for (const idx of [3, 4, 5]) {
    const cells = [...rows[idx].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map(match => match[1])
    assert.ok(cells.includes('—'), `第 ${idx} 行全零列应显示 —`)
  }
})

test('2026-10-22 明细表尺寸：限高容器+粘性表头+冻结学号/姓名列+3+3口径脚注', () => {
  const studentsIdx = source.indexOf('学生成绩明细表')
  const studentsTabIdx = source.indexOf('value="students"')
  assert.ok(studentsIdx > 0 && studentsTabIdx > 0, '应能定位学生成绩明细表板块')
  // 明细表容器限高 + 内层 Table 包装交还滚动容器（sticky 绑定外层）
  const containerIdx = source.indexOf('max-h-[70vh] overflow-auto rounded-lg border border-slate-200', studentsTabIdx)
  assert.ok(containerIdx > 0, '明细表容器应限高 70vh 双轴滚动')
  assert.match(source.slice(studentsTabIdx, containerIdx + 400), /wrapperClassName="overflow-visible"/, '内层 Table 包装须 overflow-visible（否则 sticky 被内层滚动容器截胡）')
  // 冻结学号（w-24）/姓名（left-24，紧贴学号列右缘）两列：表头 + 数据格
  // （表头吸顶由 module.css 注入，JSX 侧只断言冻结列的 left 偏移与列宽）
  assert.match(source, /sticky left-0 w-24 whitespace-nowrap border-r text-center text-xs">学号/, '学号表头冻结最左')
  assert.match(source, /sticky left-24 w-20 whitespace-nowrap border-r text-center text-xs">姓名/, '姓名表头冻结在学号右侧')
  assert.match(source, /sticky left-0 z-10 w-24 whitespace-nowrap border-r text-xs tabular-nums/, '学号数据格冻结')
  assert.match(source, /sticky left-24 z-10 w-20 whitespace-nowrap border-r text-sm font-medium/, '姓名数据格冻结')
  // +3 口径脚注
  assert.match(source, /\+3总分为选修三门等级分之和（学校导出口径），与各科原始分列不直接相加。/, '+3 口径脚注必须在场')
})
