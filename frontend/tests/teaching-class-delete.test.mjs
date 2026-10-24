import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const dialog = read('../src/components/teaching-members/DeleteTeachingClassDialog.tsx')
const manager = read('../src/components/teaching-members/TeachingMembersManager.tsx')

test('删除教学班客户端实际提交预览与完整名称确认，传递后端冲突', async () => {
  const calls = []
  let conflict = false
  const module = { exports: {} }
  const source = read('../src/lib/api-v1.ts')
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, {
    module, exports: module.exports, URLSearchParams,
    fetch: async (url, init) => { calls.push({ url, init }); return { ok: !conflict, status: conflict ? 409 : 200,
      json: async () => conflict ? { error: 'link_version_conflict', detail: '班级数据已变化' } : { classes: [], token: 'synthetic' } } },
  })
  await module.exports.previewTeachingClassDelete(12)
  await module.exports.confirmTeachingClassDelete(12, { token: 'synthetic', confirm_name: '合成班' })
  assert.equal(calls[0].url, '/api/v1/teaching/classes/12/delete-preview')
  assert.equal(calls[0].init.method, 'POST')
  assert.equal(calls[1].url, '/api/v1/teaching/classes/12/delete-confirm')
  assert.deepEqual(JSON.parse(calls[1].init.body), { token: 'synthetic', confirm_name: '合成班' })
  conflict = true
  await assert.rejects(module.exports.confirmTeachingClassDelete(12, { token: 'synthetic', confirm_name: '合成班' }), e => e.status === 409 && e.detail === '班级数据已变化')
})

test('删除警示展示实际影响，完整名称匹配前按钮禁用，冲突后重新核对', () => {
  for (const key of ['membership_count','member_count','score_count','homework_count','submission_count','exclusion_count','link_count','pair_count','reference_count']) assert.ok(dialog.includes('impact.'+key))
  assert.match(dialog, /confirmName !== impact\?\.label \|\| busy/)
  assert.match(dialog, /学生身份、学号和个人档案保留/)
  assert.match(dialog, /全年级考试及官方质量参照表保留/)
  assert.match(dialog, /永久删除/)
  assert.match(dialog, /setPreview\(null\)/)
  assert.match(dialog, /重新核对/)
})

test('管理页所有教学班有删除入口，切范围丢弃凭据，删当前班后重选并刷新目录', () => {
  assert.match(manager, /aria-label=\{`删除教学班\$\{item.label\}`\}/)
  assert.match(manager, /deleteTarget\?\.key === classContextKey/)
  assert.match(manager, /currentClassContext.current === classContextKey/)
  assert.match(manager, /teaching_class_id: next \?\? 'all'/)
  assert.match(manager, /setClasses\(c => c \? \{ \.\.\.c, teaching: c.teaching.filter/)
  assert.match(dialog, /return \(\) => \{ cancelled = true \}/)
})
