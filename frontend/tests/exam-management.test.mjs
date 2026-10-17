import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
const read = path => readFileSync(new URL(path,import.meta.url),'utf8')
const page = read('../src/components/scores/ExamManagement.tsx')
const api = read('../src/lib/api-v1.ts')

test('两个工作台都有独立考试管理路由和侧栏入口',()=>{
  for(const mode of ['homeroom','teaching']) {
    assert.match(read(`../src/app/${mode}/exams/page.tsx`),new RegExp(`ExamManagement mode="${mode}"`))
    assert.match(read('../src/components/layout/Sidebar.tsx'),new RegExp(`href: '/${mode}/exams'`))
  }
  assert.match(read('../src/components/layout/Topbar.tsx'),/exams: '考试管理'/)
})

test('删除只能经预览和完整名称确认，不调用旧栈删除接口',()=>{
  assert.match(page,/previewExamDelete\(mode/)
  assert.match(page,/confirmExamDelete\(mode/)
  assert.match(page,/confirmName !== activePreview.exam.exam_name/)
  assert.match(page,/!exam.can_delete/)
  assert.match(page,/另一工作台的数据保留/)
  assert.match(page,/pending_imports/)
  assert.match(page,/refreshScope\(\)/)
  assert.doesNotMatch(page,/\/api\/exams/)
  assert.match(api,/exams\/delete-preview/)
  assert.match(api,/exams\/delete-confirm/)
})

test('范围切换立即隐藏旧删除凭据并拒绝迟到预览',()=>{
  assert.match(page,/preview\?\.key === key/)
  assert.match(page,/currentKey.current === requestKey/)
  assert.match(page,/cancelled = true/)
  assert.match(page,/全部教学班/)
})
