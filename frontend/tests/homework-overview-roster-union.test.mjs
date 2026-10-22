// 作业跟进「全科透视」名册口径契约测试（2026-10-22 用户反馈：10-21 透视只有
// 23 人）。根因：名册只取当日首个批次明细——教学域共享投影批次只含关联生
// （高二6班↔物B3 投影 23 人），不等于全班。修复：名册=当日全部有效批次明细
// 「应交∪有记录」名单的并集；个别批次明细失败不拖垮整份透视；单科明细视图
// 仍展示该批次自身名单不变。后端口径跨栈断言（读 backend 源码）。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const table = readFileSync(new URL('../src/components/homework/AssignmentTable.tsx', import.meta.url), 'utf8')
const backend = readFileSync(new URL('../../backend/app/api/homework.py', import.meta.url), 'utf8')

test('全科透视名册改为当日全部有效批次的并集', () => {
  assert.match(table, /Promise\.allSettled\(/, '必须并发拉取当日各批次明细')
  assert.match(
    table,
    /activeAssignments\.map\(\(a\) => homeworkAssignmentDetail\(a\.assignment_id, mode, scopeQ\)\)/,
    '取数对象必须是当日全部有效批次，而非首个批次'
  )
  assert.match(table, /if \(activeAssignments\.length === 0\)/, '当日无批次空态必须保留')
})

test('并集去重取首次、按 personId 稳定排序、部分失败容错', () => {
  assert.match(table, /if \(!byId\.has\(pid\)\) byId\.set\(pid/, '同一学生跨批次必须去重（首次为准）')
  assert.match(table, /roster\.sort\(\(a, b\) => a\.personId - b\.personId\)/, '并集结果必须按 personId 稳定排序')
  assert.match(table, /if \(fulfilled\.length === 0\)/, '仅当全部批次明细失败时才整块报错')
  assert.match(table, /透视只把名单当人员轴，/, '必须注明状态来自列表项例外 ID、不依赖明细状态')
})

test('旧「取首个批次当全班名单」口径不得残留', () => {
  assert.doesNotMatch(table, /homeworkAssignmentDetail\(first\.assignment_id/, '旧单批次名册取数必须删除')
  assert.doesNotMatch(table, /取当日任一有效批次的全班名单/, '旧口径注释必须删除')
  assert.match(table, /任一批次都可能\s*\* 是部分名单/, '组件注释必须写明部分名单成因')
})

test('单科明细视图仍展示该批次自身名单，不受并集改造影响', () => {
  const hits = table.match(/homeworkAssignmentDetail\(expandedId, mode, scopeQ\)/g) ?? []
  assert.ok(hits.length >= 2, `学科明细视图的自身批次取数必须保留（实际 ${hits.length} 处）`)
})

test('跨栈：并集元素口径=后端明细 submissions（应交∪有记录）', () => {
  assert.match(
    backend,
    /reader_ids = sorted\(set\(editable_reader\) \| set\(counted_ids\)\)/,
    '后端明细 submissions 必须是应交名单∪有状态记录学生'
  )
})
