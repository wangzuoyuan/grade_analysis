// 问题 14（前端交互四子项）契约测试：
//   14.1 DiagnosisReport「完成编辑」不清空建议草稿（仅退出编辑态，继续展示修改内容）
//   14.2 AssignmentTable 考勤批次表体列结构与 3 列表头一致（学生/出勤状态/说明·备注）
//   14.3 upload 导入成功、RolloverWizard 换届确认/撤销成功后刷新全局范围与班级目录
//   14.4 class-scope 透传后端真实年级，删除无调用的 grade 死辅助（scopeParam/classesForGrade）
// 风格沿用 tests/*.test.mjs：读源码字符串断言，不启动浏览器。

import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const diagnosis = readFileSync(new URL('../src/components/student/DiagnosisReport.tsx', import.meta.url), 'utf8')
const assignment = readFileSync(new URL('../src/components/homework/AssignmentTable.tsx', import.meta.url), 'utf8')
const uploadPage = readFileSync(new URL('../src/app/upload/page.tsx', import.meta.url), 'utf8')
const rollover = readFileSync(new URL('../src/components/rollover/RolloverWizard.tsx', import.meta.url), 'utf8')
const classScope = readFileSync(new URL('../src/lib/class-scope.tsx', import.meta.url), 'utf8')
const workspace = readFileSync(new URL('../src/lib/workspace.tsx', import.meta.url), 'utf8')

/** 递归收集 src 下全部 .ts/.tsx 源码（供全局性断言）。 */
function collectSources(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) collectSources(full, out)
    else if (/\.(ts|tsx)$/.test(name)) out.push(full)
  }
  return out
}
const srcFiles = collectSources(fileURLToPath(new URL('../src', import.meta.url)))

// ---------- 14.1 完成编辑不清空草稿 ----------

test('14.1 DiagnosisReport「完成编辑」仅切换编辑态，不清空教师草稿', () => {
  const onClick = diagnosis.match(/onClick=\{\(\) => \{[\s\S]*?setEditing\(\(v\) => !v\)[\s\S]*?\}\}/)
  assert.ok(onClick, '应存在「完成编辑」按钮的 onClick 切换')
  assert.doesNotMatch(diagnosis, /if \(editing\) setDrafts\(\{\}\)/, '完成编辑不得清空草稿')
  // 草稿展示路径保留：非编辑态渲染 drafts[item.id] ?? item.text
  assert.match(diagnosis, /const text = drafts\[item\.id\] \?\? item\.text/, '展示文本应优先教师草稿')
  // 重新拉取报告时的整体重置保留（新数据覆盖旧草稿，语义正确）
  assert.match(diagnosis, /setReport\(null\)\s*\n\s*setDrafts\(\{\}\)/, '重新请求报告时仍应整体重置草稿')
  // 单条「恢复原文」入口保留
  assert.match(diagnosis, /恢复原文/, '编辑态内单条恢复原文入口保留')
})

// ---------- 14.2 考勤批次表头表体列对齐 ----------

test('14.2 考勤批次表头为 3 列（学生/出勤状态/说明·备注）', () => {
  assert.match(
    assignment,
    /item\.subject === '考勤' \? '出勤状态' : '状态'/,
    '第二列表头考勤批次应为「出勤状态」',
  )
  assert.match(
    assignment,
    /item\.subject === '考勤' \? \(\s*<TableHead className="text-xs">说明 \/ 备注<\/TableHead>/,
    '考勤分支第三列表头应为「说明 / 备注」',
  )
})

test('14.2 考勤批次表体条件渲染 3 个单元格，行内编辑保持可用', () => {
  // 受控输入提取为常量，两个分支复用（不因列结构调整丢编辑能力）
  assert.match(assignment, /const attendanceInput = \(/, '出勤异常输入应为复用常量')
  assert.match(assignment, /const evaluationInput = \(/, '评价/备注输入应为复用常量')
  // 考勤分支：状态单元格之后仅渲染 1 个 TableCell，内含两组输入 + 负面徽章
  const attBranch = assignment.match(
    /\{item\.subject === '考勤' \? \([\s\S]*?\)\s*:\s*\(/,
  )
  assert.ok(attBranch, '表体应按考勤批次条件分支渲染')
  assert.match(
    assignment,
    /item\.subject === '考勤' \? \([\s\S]{0,900}?\{attendanceInput\}[\s\S]{0,400}?\{evaluationInput\}[\s\S]{0,200}?\{negativeBadge\}[\s\S]{0,200}?<\/TableCell>/,
    '考勤分支应将出勤异常与备注并入同一个「说明 / 备注」单元格',
  )
  // 非考勤分支：完整 3 列（出勤异常/作业内容/评价）保持原状
  assert.match(
    assignment,
    /\)\s*:\s*\([\s\S]{0,200}?<>[\s\S]{0,2200}?displayHomeworkContent\(item\.homework_type\)[\s\S]{0,900}?<\/TableCell>\s*<\/>\s*\)\}/,
    '非考勤批次应保持 出勤异常/作业内容/评价 三列不变',
  )
})

// ---------- 14.3 变更后刷新全局范围与班级目录 ----------

test('14.3 两个 hook 的刷新 API 存在（refreshScope / useClassScope().refresh）', () => {
  assert.match(workspace, /refreshScope: \(\) => void/, 'WorkspaceContextValue 应暴露 refreshScope')
  assert.match(classScope, /refresh: \(\) => void/, 'ClassScopeContextValue 应暴露 refresh')
})

test('14.3 上传导入成功后刷新全局范围与班级目录', () => {
  assert.match(uploadPage, /const \{ mode, filter, switching, refreshScope \} = useWorkspace\(\)/, '上传页应解构 refreshScope')
  assert.match(uploadPage, /useClassScope\(\)/, '上传页应接入班级范围上下文')
  const confirmCtx = uploadPage.match(/setResult\(r\)[\s\S]{0,400}?setPreview\(null\)/)
  assert.ok(confirmCtx, '应存在确认成功路径')
  assert.match(
    uploadPage,
    /setResult\(r\)\s*\n\s*\/\/ 全局范围与班级目录刷新[\s\S]{0,220}?refreshScope\(\)\s*\n\s*refreshClassScope\(\)/,
    '确认成功后应先刷新全局范围再刷新班级目录',
  )
})

test('14.3 换届确认与撤销成功后均刷新全局范围与班级目录', () => {
  assert.match(rollover, /const \{ filter, generation, switching, refreshScope \} = useWorkspace\(\)/, '换届向导应解构 refreshScope')
  assert.match(rollover, /useClassScope\(\)/, '换届向导应接入班级范围上下文')
  // 确认成功
  assert.match(
    rollover,
    /setSuccess\(outcome\)\s*\n\s*\/\/ 换届建了新学年班级[\s\S]{0,200}?refreshScope\(\)\s*\n\s*refreshClassScope\(\)/,
    '换届确认成功后应刷新全局范围与班级目录',
  )
  // 撤销成功
  assert.match(
    rollover,
    /setUndoOutcome\(\{ conflicted: readConflicted\(r\) \}\)\s*\n\s*\/\/ 撤销删掉了本次新建的班级行[\s\S]{0,200}?refreshScope\(\)\s*\n\s*refreshClassScope\(\)/,
    '换届撤销成功后应刷新全局范围与班级目录',
  )
})

// ---------- 14.4 教学班年级透传 + 死辅助清理 ----------

test('14.4 class-scope 透传后端真实年级，未知年级用 0 哨兵不猜测', () => {
  assert.doesNotMatch(classScope, /grade: 0,/, '不得再把全部教学班硬编码为 grade: 0')
  assert.match(classScope, /grade: item\.grade \?\? 0/, '应透传后端 grade，未知回退 0 哨兵')
  assert.match(classScope, /后端未给出（未知）时为 0 哨兵/, '接口注释应写明 0 哨兵语义')
})

test('14.4 无调用的年级死辅助（scopeParam/classesForGrade）已从源码清除', () => {
  const offenders = srcFiles.filter((f) => {
    if (f.endsWith('lib/class-scope.tsx')) return false
    const src = readFileSync(f, 'utf8')
    return /classesForGrade|\bscopeParam\b/.test(src)
  })
  assert.deepEqual(
    offenders.filter((f) => !f.endsWith('class-scope.tsx')),
    [],
    'src 下不得再有任何 scopeParam/classesForGrade 引用',
  )
  assert.doesNotMatch(classScope, /scopeParam/, 'class-scope 自身应删除 scopeParam 死辅助')
  assert.doesNotMatch(classScope, /classesForGrade/, 'class-scope 自身应删除 classesForGrade 死辅助')
})

test('14.4 班级目录按真实年级分组展示（ClassScopePicker 分组逻辑保留）', () => {
  const picker = readFileSync(new URL('../src/components/ClassScopePicker.tsx', import.meta.url), 'utf8')
  assert.match(picker, /classes\.filter\(\(c\) => c\.grade === grade\)/, '限定年级过滤依赖真实 grade')
  assert.match(picker, /g >= 1 && g <= 3 \? formatGradeLabel\(g\) : '教学班'/, '未知年级（0 哨兵）按普通「教学班」分组渲染')
})
