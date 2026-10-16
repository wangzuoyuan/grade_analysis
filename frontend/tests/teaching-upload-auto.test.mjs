// 教学平台导入「按学号自动分班」契约测试（2026-10-16 多班合并文件需求）。
// 前端：默认选自动分班、auto_assign 表单字段与指定班级互斥；
// 后端（跨栈读源码，风格沿用 homework-correlation-card.test.mjs）：
// auto_assignments 唯一归属判定 + confirm 按分班写 class_ref_id。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const upload = readFileSync(new URL('../src/components/upload/TeachingPlatformUpload.tsx', import.meta.url), 'utf8')
const backend = readFileSync(new URL('../../backend/app/api/teaching_platform.py', import.meta.url), 'utf8')

test('上传页默认「按学号自动分到各教学班」，auto 与指定班级互斥发字段', () => {
  assert.match(upload, /useState\('auto'\)/, '默认值必须是 auto')
  assert.match(upload, /<option value="auto">按学号自动分到各教学班<\/option>/, '必须有自动分班选项')
  assert.match(upload, /<option value="">仅质量表可不选<\/option>/, '纯质量表入口保留')
  assert.match(upload, /form\.set\('auto_assign','true'\)/, 'auto 时发送 auto_assign=true')
  assert.match(upload, /form\.set\('teaching_class_id',target\)/, '指定班级时仍发送 teaching_class_id')
})

test('后端按学号唯一归属分班：无法归属逐个报错、整批零写入、事实写入各自班级', () => {
  assert.match(backend, /def auto_assignments\(/, '必须存在按学号分班实现')
  assert.match(backend, /唯一命中才归属/, '唯一归属语义必须在实现处注明')
  assert.match(backend, /不在你任教的任何教学班名册/, '无归属学生要有可读错误')
  assert.match(backend, /同时属于/, '多班命中学生要有可读错误')
  assert.match(backend, /指定教学班与按学号自动分班不能同时选择/, 'auto 与指定班级互斥')
  assert.match(backend, /class_ref_id=p\["assignments"\]\.get\(row\["alias"\]\) if s\.get\("auto_assign"\) else tc\.id/, '事实必须按分班写 class_ref_id')
})
