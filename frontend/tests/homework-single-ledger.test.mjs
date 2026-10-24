// 2026-10-24 作业两域单账本契约：
// 1. preview 422 冲突阻断的错误文案（对侧同日同学科已录 → 引导去对侧批次登记）；
// 2. HomeworkCard 跨域共享行来源标签 + 冲突日提示块；
// 3. api-v1 类型透传（data_domain / conflicts）。
// 风格：preview-error 走真实源码（ts.transpileModule + vm），卡片为源断言。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from 'typescript'

const cardSrc = readFileSync(new URL('../src/components/HomeworkCard.tsx', import.meta.url), 'utf8')
const apiSrc = readFileSync(new URL('../src/lib/api-v1.ts', import.meta.url), 'utf8')
const previewErrorSrc = readFileSync(new URL('../src/components/homework/preview-error.ts', import.meta.url), 'utf8')
const entryPanel = readFileSync(new URL('../src/components/homework/HomeworkEntryPanel.tsx', import.meta.url), 'utf8')

class FakeApiV1Error extends Error {
  constructor(status, code, detail, body) {
    super(detail || code)
    this.name = 'ApiV1Error'
    this.status = status
    this.code = code
    this.detail = detail
    this.body = body
  }
}

function loadPreviewErrorFn() {
  const context = vm.createContext({})
  const cache = {}
  const makeModule = (compiled) => {
    const module = { exports: {} }
    const wrapper = vm.runInContext(
      `(function (module, exports, require) {\n${compiled}\n})`,
      context,
    )
    wrapper(module, module.exports, (id) => {
      if (id === '@/lib/api-v1') return { ApiV1Error: FakeApiV1Error }
      if (id === '@/components/link/error-text') return cache.errorText
      return {}
    })
    return module.exports
  }
  cache.errorText = makeModule(ts.transpileModule(
    readFileSync(new URL('../src/components/link/error-text.ts', import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } },
  ).outputText)
  const compiled = ts.transpileModule(previewErrorSrc, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText
  return makeModule(compiled).homeworkPreviewErrorText
}

test('跨侧冲突 422 → 引导去对侧批次登记，不误报参数错误', () => {
  const fn = loadPreviewErrorFn()
  const text = fn(
    new FakeApiV1Error(422, 'invalid_scope_param', '教学侧关联班级当天已录同学科批次', {
      param: 'assigned_date',
      conflict_side: '教学侧',
      conflict_batches: [
        { assignment_id: 665, homework_type: '10月考订正', assigned_date: '2026-10-21', side: '教学侧', class_label: '物B3' },
      ],
    }),
    '班级',
  )
  assert.match(text, /教学侧关联班级当天已录同学科作业《10月考订正》/, '必须点出对侧批次名')
  assert.match(text, /请直接在对侧批次上登记缺交/, '必须引导去对侧批次登记')
  assert.match(text, /调整收交日期/, '确属另一份作业的逃生口：改日期重录')
  assert.doesNotMatch(text, /检查学年|参数错误/, '不得落入通用参数错误文案')
})

test('HomeworkCard：跨域共享行按工作台标「班主任侧/教学侧」', () => {
  assert.match(cardSrc, /data_domain && r\.data_domain !== ownDomain/, '共享行判定走 data_domain')
  assert.match(cardSrc, /mode === 'teaching' \? '班主任侧' : '教学侧'/, '教学端标班主任侧、班主任端标教学侧')
  const uses = cardSrc.match(/<EventRowsTable rows=\{[^}]*\} mode=\{mode\} \/>/g) ?? []
  assert.equal(uses.length, 3, '三处明细表（全部/近14天/更早）都必须传 mode')
})

test('HomeworkCard：冲突日提示块（琥珀、逐天列两侧批次与状态）', () => {
  assert.match(cardSrc, /data\?\.conflicts\?\.length/, 'conflicts 存在才渲染提示块')
  assert.match(cardSrc, /两侧作业登记不一致（已按已交处理，请核实）/, '标题必须说明已按已交处理')
  assert.match(cardSrc, /b\.data_domain === 'homeroom' \? '班主任侧' : '教学侧'/, '冲突批次标侧名')
  assert.match(cardSrc, /《\$\{b\.homework_type\}》/, '批次名用书名号')
})

test('HomeworkCard：卡头缺交计数与分类胶囊剔除冲突日缺交行（明细保留）', () => {
  assert.match(cardSrc, /const conflictKeys = new Set\(/, '冲突键集合在场')
  assert.match(
    cardSrc,
    /e\.status === 'missing' && e\.subject !== '考勤' && !isConflictMissing\(e\)/,
    'missing 过滤须排除冲突日缺交行',
  )
  assert.match(cardSrc, /!allHistory && missing\.length === 0 && \(data\?\.conflicts\?\.length \?\? 0\) === 0/, '有冲突日不得展示全勤良好卡')
})

test('api-v1 类型：data_domain / conflicts / HomeworkConflictDay 透传', () => {
  assert.match(apiSrc, /data_domain\?: string \| null/, 'HomeworkStudentEvent.data_domain 可选')
  assert.match(apiSrc, /conflicts\?: HomeworkConflictDay\[\]/, 'HomeworkStudentResponse.conflicts 可选')
  assert.match(apiSrc, /interface HomeworkConflictBatch/, 'HomeworkConflictBatch 在场')
  assert.match(apiSrc, /conflicts\?: \{ date: string; subject: string \}\[\]/, 'HomeworkWarningStudent.conflicts 可选')
})

test('录入面板仍经 homeworkPreviewErrorText 呈现（阻断文案自动生效）', () => {
  assert.match(
    entryPanel,
    /homeworkPreviewErrorText\(err, teaching \? '教学班' : '班级'\)/,
    'preview catch 须走专属文案函数',
  )
})
