// Build the public, data-free import templates with the bundled artifact-tool.
// Run from a scratch directory with artifact-tool available in node_modules.
import fs from 'node:fs/promises'
import path from 'node:path'
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool'

const output = process.argv[2]
const previews = process.argv[3]
if (!output || !previews) throw new Error('Provide explicit template and preview output directories')
await fs.mkdir(output, { recursive: true })
await fs.mkdir(previews, { recursive: true })

const subjects = ['语文', '数学', '英语', '物理', '化学', '生物', '政治', '历史', '地理']
const bands = ['A+', 'A', 'B+', 'B', 'B-', 'C+', 'C', 'C-', 'D+', 'D', 'E']
const specs = []
const col = n => {
  let label = ''
  for (; n > 0; n = Math.floor((n - 1) / 26)) label = String.fromCharCode(65 + (n - 1) % 26) + label
  return label
}
function spec(id, rows, { merges = [], percentages = [], texts = [], integers = [], headerRows = rows.length, frozenColumns = 1 } = {}) {
  specs.push({ id, rows, merges, percentages, texts, integers, headerRows, frozenColumns })
}
function pair(top, sub, merges, label, children, row = 1) {
  const first = top.length + 1
  top.push(label, ...children.slice(1).map(() => null))
  sub.push(...children)
  if (children.length > 1) merges.push(`${col(first)}${row}:${col(top.length)}${row}`)
}
function vertical(merges, columns, row = 1) {
  for (const c of columns) merges.push(`${col(c)}${row}:${col(c)}${row + 1}`)
}

spec('teaching-g1-students', [['学号', '姓名', '分数', '学籍排名', '年级百分位']], { texts: [1, 2], percentages: [5], integers: [4] })
spec('teaching-g23-students', [['学号', '姓名', '原始分数', '等级分数', '年级百分比']], { texts: [1, 2], integers: [4], percentages: [5] })

for (const [id, g1, administrative] of [
  ['teaching-g1-quality', true, true],
  ['teaching-g23-teaching-quality', false, false],
  ['teaching-g23-admin-quality', false, true],
]) {
  const top = administrative ? ['班级类型', '班级', '任课教师'] : ['班级', '任课教师']
  const sub = top.map(() => null), merges = [], percentages = [], integers = []
  const identities = top.length
  vertical(merges, Array.from({ length: identities }, (_, i) => i + 1))
  pair(top, sub, merges, '人数', ['应考', '实考'])
  integers.push(identities + 1, identities + 2)
  if (g1) {
    top.push('平均分', '标准差', '总分', '最高分', '最低分'); sub.push(null, null, null, null, null)
    vertical(merges, [identities + 3, identities + 4, identities + 5, identities + 6, identities + 7])
    for (const band of ['优秀人数', '良好人数', '合格人数', '低分人数']) {
      pair(top, sub, merges, band, ['人数', '比率'])
      integers.push(top.length - 1); percentages.push(top.length)
    }
  } else {
    pair(top, sub, merges, '平均分', ['原始', '等级'])
    top.push('标准差'); sub.push(null); vertical(merges, [top.length])
    for (const label of ['语文', '数学', '英语', '三大门']) {
      top.push(label); sub.push(null); vertical(merges, [top.length])
    }
    const start = top.length + 1
    for (const band of bands) {
      top.push(top.length + 1 === start ? '等级人数及比率' : null, null)
      sub.push(band, '比率'); integers.push(top.length - 1); percentages.push(top.length)
    }
    merges.push(`${col(start)}1:${col(top.length)}1`)
  }
  spec(id, [top, sub], { merges, percentages, integers, texts: Array.from({ length: identities }, (_, i) => i + 1), frozenColumns: identities })
}

{
  const top = ['学号', '班级', '学籍', '姓名'], sub = [null, null, null, null], merges = [], percentages = [], integers = [2, 3]
  vertical(merges, [1, 2, 3, 4], 2)
  for (const subject of subjects) {
    pair(top, sub, merges, subject, ['分数', '年级百分位'], 2); percentages.push(top.length)
  }
  for (const total of ['主三门', '五门', '九门']) {
    pair(top, sub, merges, total, ['总分', '年级百分位', '学籍排名', '年级排名'], 2)
    percentages.push(top.length - 2); integers.push(top.length - 1, top.length)
  }
  merges.push(`A1:${col(top.length)}1`)
  spec('homeroom-g1-students', [['高一全科成绩明细（第 4 行起填写）'], top, sub], { merges, texts: [1, 4], percentages, integers, frozenColumns: 4 })
}
{
  const top = ['学号', '班级', '学籍', '姓名', '语文', '数学', '英语'], sub = top.map(() => null), merges = [], percentages = [22, 25, 27, 28, 29]
  vertical(merges, [1, 2, 3, 4, 5, 6, 7], 2)
  for (const subject of subjects.slice(3)) pair(top, sub, merges, subject, ['原始分', '等级分'], 2)
  top.push('+3总分'); sub.push(null); vertical(merges, [20], 2)
  for (const total of ['主三门', '3+3']) pair(top, sub, merges, total, ['总分', '年级百分位', '学籍排名'], 2)
  top.push('语文', '数学', '英语'); sub.push('年级百分位', '年级百分位', '年级百分位')
  merges.push(`A1:${col(top.length)}1`)
  spec('homeroom-g23-students', [['高二 / 高三全科成绩明细（第 4 行起填写）'], top, sub], { merges, texts: [1, 4], percentages, integers: [2, 3, 9, 11, 13, 15, 17, 19, 23, 26], frozenColumns: 4 })
}
for (const g1 of [true, false]) {
  const top = ['班级类型', '班级', '班主任', ...subjects.slice(0, g1 ? 9 : 3)], sub = top.map(() => null), merges = []
  vertical(merges, Array.from({ length: top.length }, (_, i) => i + 1))
  if (g1) {
    for (const total of ['主三门', '五门', '九门']) pair(top, sub, merges, total, ['均分', '名次'])
  } else {
    for (const subject of subjects.slice(3)) pair(top, sub, merges, subject, ['原始', '等级'])
    top.push('+3总分', '主三门', '3+3总分'); sub.push(null, null, null)
    vertical(merges, [19, 20, 21])
  }
  spec(`homeroom-g${g1 ? '1' : '23'}-averages`, [top, sub], { merges, texts: [1, 3], integers: g1 ? [2, 14, 16, 18] : [2], frozenColumns: 3 })
}

for (const s of specs.filter(s => !process.env.TEMPLATE_IDS || process.env.TEMPLATE_IDS.split(',').includes(s.id))) {
  const wb = Workbook.create(), sheet = wb.worksheets.add('导入数据')
  const width = Math.max(...s.rows.map(r => r.length)), last = col(width)
  const body = sheet.getRange(`A1:${last}53`)
  sheet.showGridLines = false
  body.format.font = { name: 'Arial', size: 11, color: '#0F172A' }
  body.format.columnWidth = 16
  body.format.rowHeight = 25
  body.format.verticalAlignment = 'center'
  body.setNumberFormat('0.##')
  sheet.getRange(`A1:${last}${s.rows.length}`).values = s.rows.map(r => [...r, ...Array(width - r.length).fill(null)])
  const headers = sheet.getRange(`A1:${last}${s.headerRows}`)
  headers.format.fill = '#0C4A6E'; headers.format.font.color = '#FFFFFF'; headers.format.font.bold = true
  headers.format.horizontalAlignment = 'center'; headers.format.wrapText = true; headers.format.rowHeight = 36
  for (const range of s.merges) sheet.mergeCells(range)
  if (s.id.endsWith('students')) sheet.getRange('A1:A53').format.columnWidth = 22
  for (const c of s.texts) sheet.getRange(`${col(c)}${s.headerRows + 1}:${col(c)}53`).setNumberFormat('@')
  for (const c of s.percentages) sheet.getRange(`${col(c)}${s.headerRows + 1}:${col(c)}53`).setNumberFormat('0.00%')
  for (const c of s.integers) sheet.getRange(`${col(c)}${s.headerRows + 1}:${col(c)}53`).setNumberFormat('0')
  sheet.getRange(`A${s.headerRows + 1}:${last}53`).format.fill = '#F8FAFC'
  sheet.freezePanes.freezeRows(s.headerRows)
  sheet.freezePanes.freezeColumns(s.frozenColumns)
  wb.recalculate()
  await wb.inspect({ kind: 'table', range: `导入数据!A1:${last}${s.headerRows}`, include: 'values', tableMaxRows: 3, tableMaxCols: width, maxChars: 1600 })
  const image = await wb.render({ sheetName: '导入数据', range: `A1:${last}${s.headerRows + 2}`, scale: 1, format: 'png' })
  await fs.writeFile(path.join(previews, `${s.id}.png`), new Uint8Array(await image.arrayBuffer()))
  const xlsx = await SpreadsheetFile.exportXlsx(wb)
  await xlsx.save(path.join(output, `${s.id}.xlsx`))
  await fs.rm(path.join(output, `${s.id}.xlsx.inspect.ndjson`), { force: true })
  console.log(`${s.id}: ${width} columns; header rows ${s.headerRows}; blank body`)
}
