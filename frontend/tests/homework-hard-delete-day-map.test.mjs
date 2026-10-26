import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const root = new URL('..', import.meta.url)
const read = (p) => readFileSync(new URL(p, root), 'utf8')

test('作业批次硬删除（2026-10-26）：删除即物理删除，界面不再有撤销残留', () => {
  const table = read('src/components/homework/AssignmentTable.tsx')
  const apiV1 = read('src/lib/api-v1.ts')
  const backend = read('../backend/app/api/homework.py')

  // 1. 确认弹窗明确「彻底移除、不可恢复」，不再是「不再计入统计」的软撤销话术
  assert.match(table, /删除后记录连同逐人收交明细一起彻底移除，不可恢复/)
  assert.doesNotMatch(table, /撤销后不再计入统计/)

  // 2. 409 护栏文案改「删除被拒绝」，清单照常原样展示
  assert.match(table, /该批次存在后续评价编辑，删除被拒绝；请先核对以下冲突清单/)
  assert.doesNotMatch(table, /撤销被拒绝/)

  // 3. 天组类型与统计不再有 revoked 维度；界面零「已撤销/含撤销批次」残留
  assert.doesNotMatch(table, /hasRevoked|allRevoked/)
  assert.doesNotMatch(table, /已撤销|含撤销批次|批次已撤销/)
  assert.doesNotMatch(table, /status === 'revoked'/)

  // 4. api 层注释与后端口径一致：物理删除
  assert.match(apiV1, /2026-10-26 起物理删除，不留撤销记录/)

  // 5. 后端 DELETE 物理删行 + 删除衍生溯源笔记 + 删除批次本身
  assert.match(backend, /db\.query\(HomeworkSubmission\)\.filter\(\s*\n\s*HomeworkSubmission\.assignment_id == a\.id\s*\n\s*\)\.delete/)
  assert.match(backend, /WsStudentNote\.source == f"homework:\{a\.id\}"/)
  assert.match(backend, /db\.delete\(a\)/)
  assert.doesNotMatch(backend, /a\.status = "revoked"/)
  assert.match(backend, /status="deleted"/)
  // 409 编辑冲突护栏保留
  assert.match(backend, /批次存在后续评价编辑，删除被拒绝（请先确认冲突清单）/)
  // 列表只回 active（存量 revoked 不再下发）
  assert.match(backend, /for a, mapping in _visible_assignments\(db, ctx, active_only=True\):\n        if subject_f is not None and a\.subject != subject_f:/)
})

test('请假/出勤异常按天映射（2026-10-26）：后端单一实现，前端由列表 id 数组自动跟随', () => {
  const backend = read('../backend/app/api/homework.py')
  const table = read('src/components/homework/AssignmentTable.tsx')
  const studentHomework = read('../backend/app/diagnosis/student_homework.py')
  const correlation = read('../backend/app/diagnosis/correlation.py')
  const contract = read('../docs/contracts/p5-homework.md')

  // 1. 核心助手在场：按天聚合事实 + 临时行修正
  assert.match(backend, /def _day_exception_flags\(/)
  assert.match(backend, /def _adjusted_submission\(/)
  // 请假无条件覆盖（full 台账已交行同样翻转）；考勤批次只作来源不作目标
  assert.match(backend, /if flags\.get\("excused"\):\n        return HomeworkSubmission\(/)
  assert.match(backend, /a\.subject == "考勤"/)
  // 考勤缺勤行回退词必须含出勤关键词（下游 _attendance_of 可识别）
  assert.match(backend, /or "缺席"/)

  // 2. 消费方全部接入：计数/详情/看板/事件流/预警/相关性/逐科分析/确认回执/预览
  for (const marker of [
    'day_flags=day_flags.get(a.assigned_date)',
    '_assignment_counts(db, assignment, ctx)',
    'day_flags = _day_exception_flags(db, ctx, [a.assigned_date])',
  ]) {
    assert.ok(backend.includes(marker), `homework.py 须含 ${marker}`)
  }
  assert.match(studentHomework, /hw\._day_exception_flags\(/)
  assert.match(studentHomework, /hw\._adjusted_submission\(/)
  assert.match(correlation, /_day_exception_flags\(/)

  // 3. preview 装饰提示老师「当天其他批次的请假/出勤会映射过来」
  assert.match(backend, /当天其他批次已登记的请假\/出勤异常将自动映射到本批次显示/)

  // 4. 前端零映射逻辑：数据全部来自后端列表项 id 数组（既有链路），无撤销残留即可
  assert.doesNotMatch(table, /dayFlags|day_flags/)

  // 5. 契约文档已升版记载两条口径
  assert.match(contract, /按天映射（2026-10-26）/)
  assert.match(contract, /删除即物理删除/)
  assert.match(contract, /v1\.4，2026-10-26/)
})
