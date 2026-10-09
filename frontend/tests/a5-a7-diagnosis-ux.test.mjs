// A5/A6/A7 诊断展示改进契约测试（docs/diagnosis-roadmap 展示层三件套）：
// - A5：班主任首页类型分布卡下钻——分布行行内展开该类型学生名单、姓名走本班
//   名册映射（拿不到兜底「学生 {person_id}」绝不编造）、名单与优先关注均链接
//   /homeroom/students/{person_id}/report（带 /report 后缀）；
// - A6：学生页诊断卡六类指标的「偏科」处补口径短句（title 提示，一处即可）；
// - A7：missing_reason 机器码 → 中文映射（共享模块，两文件共用；含未知码兜底）
//   + 副标题汉化（去口径版本号 / 去英文端点表述 / 侧栏英文标语）。
// 风格沿用 diagnosis-card.test.mjs（源码正则断言）+ homeroom-entry-exclusion.test.mjs
// （ts.transpileModule + vm 执行真实源码做行为断言；组件渲染部分不触网不挂载）。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from 'typescript'

const card = readFileSync(new URL('../src/components/student/DiagnosisCard.tsx', import.meta.url), 'utf8')
const reportView = readFileSync(
  new URL('../src/components/student/DiagnosisReport.tsx', import.meta.url),
  'utf8',
)
const sidebar = readFileSync(new URL('../src/components/layout/Sidebar.tsx', import.meta.url), 'utf8')
const labelsSrc = readFileSync(new URL('../src/lib/diagnosis-labels.ts', import.meta.url), 'utf8')

/** 纯 TS 模块（无外部依赖）编译后进沙箱执行，返回其导出。 */
function loadModule(src) {
  const compiled = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText
  const module = { exports: {} }
  vm.runInNewContext(compiled, { module, exports: module.exports })
  return module.exports
}

/** 组件模块编译后以宽容 stub 加载（只取纯函数导出，不渲染、不触网）。 */
function loadCardModule() {
  const compiled = ts.transpileModule(card, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      jsx: ts.JsxEmit.React,
    },
  }).outputText
  const stub = new Proxy(function () {}, { get: () => stub, apply: () => stub })
  const module = { exports: {} }
  vm.runInNewContext(compiled, { module, exports: module.exports, require: () => stub })
  return module.exports
}

/* ------------------------------ A7：机器码中文映射 ------------------------------ */

test('A7：missing_reason 机器码→中文映射齐备（含未知码兜底与空值语义）', () => {
  const { DIAGNOSIS_MISSING_REASON_LABELS, diagnosisMissingReasonLabel } = loadModule(labelsSrc)
  assert.equal(DIAGNOSIS_MISSING_REASON_LABELS.no_main3_row, '本场无总分行')
  assert.equal(DIAGNOSIS_MISSING_REASON_LABELS.main3_absent, '本场缺考')
  assert.equal(DIAGNOSIS_MISSING_REASON_LABELS.no_main3_percentile, '总分无可读名次/百分位')
  assert.equal(DIAGNOSIS_MISSING_REASON_LABELS.not_computable, '不可计算')
  assert.equal(diagnosisMissingReasonLabel('no_main3_row'), '本场无总分行')
  assert.equal(diagnosisMissingReasonLabel('main3_absent'), '本场缺考')
  assert.equal(diagnosisMissingReasonLabel('no_main3_percentile'), '总分无可读名次/百分位')
  assert.equal(diagnosisMissingReasonLabel('not_computable'), '不可计算')
  // 未知码兜底：如实透出原始码，绝不吞信息
  assert.equal(diagnosisMissingReasonLabel('weird_new_code'), '数据不足（原始码：weird_new_code）')
  // null/undefined/空串 → null（由调用方决定是否渲染括号）
  assert.equal(diagnosisMissingReasonLabel(null), null)
  assert.equal(diagnosisMissingReasonLabel(undefined), null)
  assert.equal(diagnosisMissingReasonLabel(''), null)
})

test('A7：DiagnosisCard 与 DiagnosisReport 两处渲染共用同一中文映射模块', () => {
  for (const [name, src] of [
    ['DiagnosisCard', card],
    ['DiagnosisReport', reportView],
  ]) {
    assert.match(src, /@\/lib\/diagnosis-labels/, `${name} 从共享映射模块导入`)
    assert.match(src, /diagnosisMissingReasonLabel/, `${name} 渲染处须走映射而非裸机器码`)
  }
  // 旧写法（直接渲染英文机器码）不得回归
  assert.doesNotMatch(reportView, /（\$\{ls\.main3\.missing_reason\}\）/, 'main3 缺失原因不得直接渲染机器码')
  assert.doesNotMatch(reportView, /sp\.imbalance\.missing_reason \?\? '数据不足'/, '偏科缺失原因须经映射')
})

/* ------------------------------ A5：类型分布下钻 ------------------------------ */

test('A5：分桶下钻与分布计数同一口径（数据不足/单人请求失败同桶）', () => {
  const { aggregateTypeDistribution, groupPersonIdsByType, rosterStudentName } = loadCardModule()
  const mk = (pid, mainType, status = 'classified') => ({
    person_id: pid,
    calc_version: 'p1-v1',
    main_type: mainType,
    secondary_tags: [],
    evidence: [],
    classification_status: status,
  })
  // 4 人：1 综合风险、1 持续进步、1 数据不足（insufficient_data）、1 请求失败（null 占位）
  const memberIds = [101, 102, 103, 104]
  const typesList = [mk(101, '综合风险型'), mk(102, '持续进步型'), mk(103, null, 'insufficient_data'), null]
  const groups = groupPersonIdsByType(memberIds, typesList)
  // 沙箱与宿主分属不同 realm，数组断言用 JSON 序列化比对（避免原型不相等）
  assert.equal(JSON.stringify(groups.get('综合风险型')), JSON.stringify([101]))
  assert.equal(JSON.stringify(groups.get('持续进步型')), JSON.stringify([102]))
  assert.equal(
    JSON.stringify(groups.get(null)),
    JSON.stringify([103, 104]),
    '数据不足与请求失败进同一桶（与聚合计数口径一致）',
  )
  // 每个分布条目的 count 与下钻名单人数一致（下钻绝不虚报/漏报）
  for (const entry of aggregateTypeDistribution(typesList)) {
    assert.equal(entry.count, (groups.get(entry.type) ?? []).length, `桶 ${entry.type ?? '数据不足'} 人数一致`)
  }
  // 名册兜底：拿不到名字显示「学生 {person_id}」，绝不编造
  assert.equal(rosterStudentName(new Map([[101, '张三']]), 101), '张三')
  assert.equal(rosterStudentName(null, 42), '学生 42')
  assert.equal(rosterStudentName(new Map(), 42), '学生 42')
})

test('A5：分布行渲染可展开交互与 /report 链接（行内展开，不新开页面）', () => {
  assert.match(card, /groupPersonIdsByType/, '类型桶→学生名单纯函数')
  assert.match(card, /toggleExpandedType/, '类型行点击 toggle 展开/收起')
  assert.match(card, /expandedTypes\.has\(bucketKey\)/, '展开态按类型桶键记录')
  assert.match(card, /aria-expanded=\{expanded\}/, '展开态带无障碍标注')
  assert.match(card, /<button[\s\S]*?onClick=\{\(\) => toggleExpandedType\(entry\.type\)\}/, '分布行为原生按钮可点击')
  assert.match(card, /members\.map\(\(pid\) =>/, '展开后逐生渲染名单')
  assert.match(card, /rosterStudentName\(rosterNames, pid\)/, '名单姓名走名册映射')
  // 链接：班主任域学生事实版报告页，必须带 /report 后缀
  assert.match(card, /\/homeroom\/students\/\$\{personId\}\/report/, '链接助手带 /report 后缀')
  assert.match(card, /homeroomStudentReportHref\(pid\)/, '名单项链接报告页')
  // 名册：既有 students 端点拉一次本班名册做映射（types 端点无姓名字段）
  assert.match(card, /fetchStudents\('homeroom', \{/, '名册走既有 fetchStudents（homeroom）')
  assert.match(card, /`学生 \$\{personId\}`/, '无名兜底「学生 {person_id}」')
  // 优先关注行补姓名并链接同上报告页
  assert.match(card, /rosterStudentName\(rosterNames, item\.person_id\)/, '优先关注补学生姓名')
  assert.match(card, /homeroomStudentReportHref\(item\.person_id\)/, '优先关注链接报告页')
  // 不新增依赖：仍是 next/link + 原生 button，无新 import 的新第三方库
  assert.match(card, /import Link from 'next\/link'/, '链接用 next/link')
})

/* ------------------------------ A6：偏科口径短句 ------------------------------ */

test('A6：六类指标「偏科」处补口径短句（title 提示，一处即可）', () => {
  assert.match(
    card,
    /＝该科年级百分位 − 本人总分年级百分位（同场比较，单位百分点）/,
    '口径短句原文：该科年级百分位 − 本人总分年级百分位',
  )
  assert.match(card, /row\.key === 'imbalance'/, '口径提示只挂在偏科行')
  assert.match(card, /title="＝该科年级百分位/, '以 title 提示呈现')
})

/* ------------------------------ A7：副标题汉化 ------------------------------ */

test('A7：副标题汉化（去口径版本号 / 去英文端点表述 / 侧栏英文标语）', () => {
  assert.match(
    card,
    /班级诊断类型分布与优先关注（与学生页诊断卡、AI 助手同一数据源）/,
    '首页分布卡副标题去版本号',
  )
  assert.doesNotMatch(card, /口径 p1-v1/, '副标题不得再对教师露出口径版本号')
  assert.doesNotMatch(card, /features \+ types 端点/, '不得露出英文端点技术表述')
  assert.match(reportView, /与学生页诊断卡、AI 助手同一数据源/, '诊断报告页副标题汉化')
  assert.match(sidebar, /学情数据分析/, '侧栏副标题改「学情数据分析」')
  assert.doesNotMatch(sidebar, /Performance Analysis/, '不得残留英文标语')
  assert.doesNotMatch(sidebar, /uppercase/, 'uppercase 对中文无效，须去掉以免布局异常')
})
