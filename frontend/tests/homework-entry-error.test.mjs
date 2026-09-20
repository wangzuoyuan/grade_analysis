// 回归：作业录入 preview 错误必须区分「学生不在名册 / 同名歧义 / 未选班 / 真正的
// 作用域参数错误 / 网络错误」，且 preview 请求携带与教学班同一上下文的学年。
// 复现来源：物A1 输入"刘佳瑞"（名册实为"秦二十三"）被泛化成"检查学年与班级"。
// 风格：error-text 消费链走真实源码（ts.transpileModule + vm），面板/容器为源断言。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from 'typescript'

const entryPanel = readFileSync(new URL('../src/components/homework/HomeworkEntryPanel.tsx', import.meta.url), 'utf8')
const workspace = readFileSync(new URL('../src/components/homework/HomeworkWorkspace.tsx', import.meta.url), 'utf8')
const previewErrorSrc = readFileSync(new URL('../src/components/homework/preview-error.ts', import.meta.url), 'utf8')
const errorTextSrc = readFileSync(new URL('../src/components/link/error-text.ts', import.meta.url), 'utf8')

/** 与 api-v1 的 ApiV1Error 同形状（status/code/detail/body），供 vm 内 instanceof。 */
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

function compile(rel) {
  const src = readFileSync(new URL(rel, import.meta.url), 'utf8')
  return ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText
}

/** 加载真实的 error-text + preview-error 消费链（仅 api-v1 用 Fake 类替身）。
 * 两个模块放同一 vm realm：网络错误分支的 `instanceof Error` 需同 realm 成立。 */
function loadPreviewErrorModule() {
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
  cache.errorText = makeModule(compile('../src/components/link/error-text.ts'))
  const fn = makeModule(compile('../src/components/homework/preview-error.ts')).homeworkPreviewErrorText
  return { fn, context }
}

const vocabSrc = readFileSync(new URL('../src/components/homework/homework-vocab.ts', import.meta.url), 'utf8')

function loadSmartParser() {
  // 共享词汇表先行编译，再作为 require 依赖注入面板沙箱
  const vocabModule = { exports: {} }
  vm.runInNewContext(
    ts.transpileModule(vocabSrc, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    }).outputText,
    { module: vocabModule, exports: vocabModule.exports, require: () => ({}), Date, console, setTimeout, clearTimeout },
  )
  const compiled = ts.transpileModule(entryPanel, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
      target: ts.ScriptTarget.ES2020,
    },
  }).outputText
  const module = { exports: {} }
  vm.runInNewContext(compiled, {
    module,
    exports: module.exports,
    require: (id) => (String(id).includes('homework-vocab') ? vocabModule.exports : ({})),
    Date,
    console,
    setTimeout,
    clearTimeout,
  })
  return module.exports.parseSmartHomeworkText
}

test('场景 A：姓名未命中名册 → 点名学生并引导核对/学号，不再误报学年班级', () => {
  const { fn } = loadPreviewErrorModule()
  const text = fn(
    new FakeApiV1Error(422, 'invalid_scope_param', 'name not found in current roster', {
      name_or_alias: '刘佳瑞',
    }),
    '教学班',
  )
  assert.match(text, /未在当前教学班找到学生「刘佳瑞」/, '必须点名未命中的学生')
  assert.match(text, /核对姓名/, '必须引导核对姓名')
  assert.match(text, /学号/, '必须引导改用学号录入')
  assert.doesNotMatch(text, /学年与班级/, '不得再显示"检查学年与班级的选择"')
})

test('同名/同号歧义 → 专属主提示（候选清单由组件另行渲染），不误报学年班级', () => {
  const { fn } = loadPreviewErrorModule()
  const text = fn(
    new FakeApiV1Error(422, 'invalid_scope_param', '姓名歧义', {
      name_or_alias: '张三',
      candidates: [
        { person_id: 1, name: '张三', alias: '99000001' },
        { person_id: 2, name: '张三', alias: '99000002' },
      ],
    }),
    '教学班',
  )
  assert.match(text, /「张三」在当前教学班存在多名同名或同号学生/, '歧义须点名并说明原因')
  assert.match(text, /学号/, '歧义须引导学号消歧')
  assert.doesNotMatch(text, /学年与班级/)
})

test('真正的作用域参数错误（无 name_or_alias）仍显示学年/班级通用提示', () => {
  const { fn } = loadPreviewErrorModule()
  // 后端 param=teaching_class_id（多班未显式）/ person_id 未命中 / 空白姓名 → 通用映射
  for (const body of [{ param: 'teaching_class_id' }, { person_id: 999 }, { name_or_alias: '   ' }, {}]) {
    const text = fn(new FakeApiV1Error(422, 'invalid_scope_param', 'invalid', body), '教学班')
    assert.equal(text, '请求参数有误，请检查学年与班级的选择', `body=${JSON.stringify(body)}`)
  }
})

test('网络错误与其他业务错误维持各自文案（apiErrorMessage 回退）', () => {
  const { fn, context } = loadPreviewErrorModule()
  // 网络层：非 ApiV1Error 的普通 Error → 原始 message（同 realm 构造以过 instanceof）
  assert.equal(fn(vm.runInContext('new Error("网络连接失败")', context), '教学班'), '网络连接失败')
  // 其他业务码 → error-text 映射或后端 detail
  assert.equal(
    fn(new FakeApiV1Error(409, 'workspace_not_configured', '请先完成工作台配置'), '教学班'),
    '请先完成工作台配置',
  )
})

test('录入面板：preview 错误经 homeworkPreviewErrorText；未选班/歧义候选路径保留', () => {
  assert.match(entryPanel, /homeworkPreviewErrorText\(err, teaching \? '教学班' : '班级'\)/, 'preview catch 须用专属文案')
  assert.match(entryPanel, /readHomeworkAmbiguityCandidates/, '422 同名歧义仍读候选清单')
  assert.match(entryPanel, /请先在页面上方选择一个具体教学班/, '未选班须前端拦截')
  assert.match(entryPanel, /请先在工作台顶部选择具体班级/, '多班 422 仍引导先选班')
  assert.match(entryPanel, /err\.body\?\.param === 'teaching_class_id'/, '多班 422 判定保留')
})

test('preview 请求携带 academic_year_id（与 teaching_class_id 同一选择上下文）', () => {
  assert.match(
    entryPanel,
    /typeof academicYearId === 'number' \? \{ academic_year_id: academicYearId \} : \{\}/,
    '请求须按 props 展开学年 id，不得用教学班 id 代替',
  )
  assert.match(entryPanel, /teaching_class_id: teachingClassId/, '教学域仍显式携带教学班 id')
})

test('独立录入页签的 HomeworkEntryPanel 从 filter.academic_year_id 传入学年', () => {
  const propPass = /academicYearId=\{typeof filter\.academic_year_id === 'number' \? filter\.academic_year_id : undefined\}/g
  assert.equal((workspace.match(propPass) || []).length, 1, '作业录入页签须透传学年')
  assert.match(workspace, /typeof filter\.teaching_class_id === 'number' \? filter\.teaching_class_id : undefined/, '教学班透传不变')
})

test('智能文本按原样传递姓名（前端不做模糊匹配，身份由后端名册判定）', () => {
  const parse = loadSmartParser()
  const result = parse('缺交：秦二十四，秦二十五，秦六，刘佳瑞，秦二十六，秦二十二')
  assert.equal(result.error, null)
  assert.equal(result.input.kind, 'detailed')
  assert.equal(result.input.rows.length, 6)
  // vm 跨 realm 数组原型不同，deepEqual 会误判：改按 JSON 字符串逐字比对
  assert.equal(
    JSON.stringify(result.input.rows.map((r) => r.name_or_alias)),
    JSON.stringify(['秦二十四', '秦二十五', '秦六', '刘佳瑞', '秦二十六', '秦二十二']),
    '姓名逐字透传，"刘佳瑞"不得被改写或静默替换',
  )
  assert.ok(result.input.rows.every((r) => r.status === 'missing'))
})

test('preview-error 源码不引入模糊匹配或自动归并', () => {
  assert.doesNotMatch(previewErrorSrc, /fuzzy|相似|近似匹配|levenshtein/i, '不得做相似名匹配')
  assert.doesNotMatch(errorTextSrc, /刘佳瑞|秦二十四/, '通用错误映射不得夹带具体学生信息')
})
