import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const compile = (source, jsx) => ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: jsx ? ts.JsxEmit.React : undefined },
}).outputText
const api = {}
new Function('exports', compile(read('../src/lib/api-v1.ts')))(api)
const errors = {}
new Function('exports', 'require', compile(read('../src/components/link/error-text.ts')))(errors, id => {
  assert.equal(id, '@/lib/api-v1')
  return api
})
const { ApiV1Error } = api
const { apiErrorMessage } = errors

test('每类业务错误均优先显示真实原因，过期提示不会遮住姓名或字段冲突', () => {
  for (const [status, code, detail] of [
    [409, 'link_version_conflict', '学号姓名冲突，整批拒绝'],
    [409, 'link_version_conflict', '存在不同值，请核对差异后确认修订'],
    [409, 'link_version_conflict', '预览后的数据或范围已变化，请重新预览'],
    [422, 'invalid_scope_param', '考试日期不在目标学年内'],
    [404, 'resource_out_of_scope', '目标教学班的学年不匹配'],
    [409, 'workspace_not_configured', '当前学年尚未配置任教学科'],
  ]) assert.equal(apiErrorMessage(new ApiV1Error(status, code, detail)), detail)
})

test('预览的字符串冲突完整逐项展示，不截断、不显示 JSON', () => {
  const conflicts = Array.from({length: 25}, (_, i) => `同学号不同姓名：SYN${i}（库内「合成${i}」≠ 文件「合成${i}06」）`)
  const text = apiErrorMessage(new ApiV1Error(409, 'link_version_conflict', '学号姓名冲突，整批拒绝', { conflicts }))
  assert.match(text, /^学号姓名冲突，整批拒绝\n具体冲突（25 项）：\n1\./)
  for (const conflict of conflicts) assert(text.includes(conflict))
  assert.match(text, /25\. 同学号不同姓名：SYN24/)
  assert.doesNotMatch(text, /预览已过期|\["|\[object Object\]/)
})

test('确认失败的结构化冲突显示字段、原值、新值、原因及来源，0 与空值独立', () => {
  const text = apiErrorMessage(new ApiV1Error(409, 'link_version_conflict', '整批校验失败，零写入', { conflicts: [
    { alias:'SYN001', field:'score', existing:0, incoming:null, message:'补录不能修改原始分', source:{filename:'合成明细.xlsx',sheet:'Sheet1',row:7} },
    { entity:'合成甲', field:'grade_percentile', existing:.25, incoming:.5 },
    { class_label:'合成教学班', field:'exam_date', existing:'2030-10-01', incoming:'2030-11-01' },
    { person:'合成乙', subject:'物理', aliases:['SYN003','SYN004'], existing_score:0, new_score:70 },
  ] }))
  for (const detail of ['学号：SYN001', '字段：原始分', '原值：0', '本次值：—', '原因：补录不能修改原始分',
    '文件：合成明细.xlsx', '行号：7', '原值：25%', '本次值：50%', '班级：合成教学班', '2030-11-01',
    '学号：SYN003；SYN004', '原分数：0', '本次分数：70']) assert(text.includes(detail), detail)
  assert.doesNotMatch(text, /\{"|\[object Object\]/)
})

test('版本变化和未决身份/参照保留具体证据，不混同预览过期', () => {
  const text = apiErrorMessage(new ApiV1Error(409, 'link_version_conflict', '请先核对范围', {
    drift: { teaching_class_id: { before:1, after:2 } },
    identity_candidates:[{alias:'SYN001',name:'合成甲',person_id:3,academic_year_name:'合成学年'}],
    roster_changes:[{class_label:'合成教学班',class_kind:'teaching'}],
  }))
  for (const detail of ['范围或版本变化（1 项）', '教学班编号', '原值：1', '当前值：2', '待确认身份（1 项）', '人员编号：3', '合成学年', '班级参照变化（1 项）']) assert(text.includes(detail), detail)
})

test('缺失说明时使用通用文案，网络错误、空明细和未知错误正常回退', () => {
  assert.equal(apiErrorMessage(new ApiV1Error(409,'link_version_conflict')), '操作存在数据或版本冲突，请核对后重试')
  assert.equal(apiErrorMessage(new ApiV1Error(422,'invalid_scope_param','   ',{conflicts:[]})), '请求参数有误，请检查学年与班级的选择')
  assert.equal(apiErrorMessage(new ApiV1Error(404,'resource_out_of_scope','resource_out_of_scope')), '所选班级不在允许范围')
  assert.equal(apiErrorMessage(new ApiV1Error(500,'unknown','具体故障')), '具体故障')
  assert.equal(apiErrorMessage(new Error('网络连接失败')), '网络连接失败')
  assert.equal(apiErrorMessage(null), '请求失败，请稍后重试')
})

test('预览错误表同时显示字段与原因，文件错误保留文件名，值及 HTML 安全渲染', () => {
  const source = read('../src/components/upload/TeachingPlatformUpload.tsx')
  const ast = ts.createSourceFile('upload.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const names = new Set(['fieldNames','show','flatten','PlatformChanges'])
  const nodes = ast.statements.filter(node => ts.isFunctionDeclaration(node) ? names.has(node.name?.text)
    : ts.isVariableStatement(node) && node.declarationList.declarations.some(d=>names.has(d.name.getText(ast))))
  assert.equal(nodes.length,4)
  const components = {}
  new Function('exports','React',compile(nodes.map(n=>n.getText(ast)).join('\n'),true))(components,React)
  const html = renderToStaticMarkup(React.createElement(components.PlatformChanges,{rows:[
    {alias:'SYN001',field:'score',existing:0,incoming:null,message:'补录不能修改原始分'},
    {file:'合成文件.xlsx',message:'<script>无效表头</script>'},
  ]}))
  for(const value of ['SYN001','原始分','补录不能修改原始分','>0<','合成文件.xlsx','&lt;script&gt;']) assert(html.includes(value),value)
  assert(!html.includes('<script>'))
})
