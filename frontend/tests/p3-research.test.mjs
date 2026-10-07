// 新关注回看的 UI 契约；实际交互另由合成库浏览器验收。
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import test from 'node:test'
import ts from 'typescript'
const read = p => readFileSync(new URL(p, import.meta.url), 'utf8')
const view = read('../src/components/research/ResearchView.tsx')
const results = read('../src/components/research/FocusResults.tsx')
const reviews = read('../src/components/research/FollowUpReview.tsx')
const api = read('../src/components/research/focus-api.ts')
const compiled = ts.transpileModule(api, {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText
const exported = {}
new Function('exports', compiled)(exported)

test('关注回看：一个班主任入口与两个独立工作流', () => {
  assert.match(read('../src/app/homeroom/research/page.tsx'), /<ResearchView/)
  const sidebar = read('../src/components/layout/Sidebar.tsx')
  assert.equal((sidebar.match(/href: '\/homeroom\/research'/g) ?? []).length, 1)
  assert.match(sidebar, /label: '关注回看'/)
  assert.match(view, /问题学生回看/)
  assert.match(view, /已建档跟进复查/)
  assert.match(view, /mode !== 'homeroom'/)
  assert.match(view, /role="tablist"/)
})

test('关注回看：名次/百分位单位正确，缺值保留破折号', () => {
  assert.equal(exported.formatValue(null, 'rank'), '—')
  assert.equal(exported.formatValue(undefined, 'percentile'), '—')
  assert.equal(exported.formatValue(66, 'percentile'), '前 66%')
  assert.equal(exported.formatValue(0, 'percentile'), '前 0%')
  assert.equal(exported.formatChange(-24, 'percentile'), '相对位置上升 24 个百分点')
  assert.equal(exported.formatChange(4, 'percentile'), '相对位置下降 4 个百分点')
  assert.equal(exported.formatChange(120, 'rank'), '后退 120 名')
  assert.equal(exported.formatChange(-25, 'rank'), '前进 25 名')
  assert.equal(exported.formatChange(3, 'grade_score'), '上升 3 分')
  assert.equal(exported.formatChange(0, 'rank'), '无变化')
})

test('关注回看：连接可编辑学生档案，不会直链不存在的画像父路径', () => {
  assert.equal(exported.profileHref(2), '/homeroom/profile?person_id=2')
  assert.match(read('../src/components/students/HomeroomProfileView.tsx'), /person_id/)
  assert.match(results, /查看证据／建立跟进/)
  assert.match(reviews, /查看／记录观察/)
})

test('关注回看：按后端时点选人，问题专用指标保留主次标签证据', () => {
  assert.match(api, /research\/focus\/cohorts/)
  assert.match(api, /research\/focus\/outcome/)
  assert.match(api, /research\/follow-ups/)
  assert.match(view, /当时的问题学生/)
  assert.match(view, /现在的问题学生/)
  assert.match(view, /主类型和次标签都纳入/)
  assert.match(view, /historical_available/)
  assert.match(view, /problem:imbalance/)
  assert.match(view, /problem:homework/)
  assert.match(results, /次标签入选/)
  assert.match(results, /student.reason/)
})

test('关注回看：缺考与不可比名单可查，不把差距缩小一律当改善', () => {
  assert.match(results, /差距缩小时，还需核对单科是否退步/)
  assert.match(results, /暂不可比名单/)
  assert.match(results, /excluded_students.map/)
  assert.match(results, /student.missing_reason/)
  assert.match(results, /connectNulls=\{false\}/)
  assert.match(results, /此阈值暂定/)
  assert.match(view, /缺考、缺科不转零/)
  assert.match(view, /不能证明某项措施有效或无效/)
})

test('关注回看：作用域与筛选变化取消旧请求，允许重算', () => {
  assert.match(view, /academic_year_id: academicYearId, class_id: classId/)
  assert.match(view, /generation, reload/)
  assert.match(view, /outcomeReqRef.current \+= 1/)
  assert.match(view, /abortOutcome.current\?\.abort/)
  assert.match(view, /setLoading\(false\)/)
  assert.match(api, /fetch\([^\n]+signal/)
  assert.match(reviews, /scopeQ.academic_year_id, scopeQ.class_id, reload/)
  assert.match(reviews, /abort.abort\(\)/)
})

test('跟进复查：每条自己的基线、目标、实际变化与教师状态分列', () => {
  assert.match(reviews, /c.baseline!.value \* factor/)
  assert.match(reviews, /c.latest!.value \* factor/)
  assert.match(reviews, /c.change!.value \* factor/)
  assert.match(reviews, /暂不可评价/)
  assert.match(reviews, /no_baseline/)
  assert.match(reviews, /教师已关闭/)
  assert.match(reviews, /到期.*有可比结果.*可能重叠/)
  assert.match(view, /<summary[^>]+>口径与版本<\/summary>/)
})

test('跟进入口：可选纪要留空按问题摘要提交，不能锁死创建按钮', () => {
  const card = read('../src/components/student/InterventionCard.tsx')
  assert.match(card, /content: form.content.trim\(\) \|\| form.problem.trim\(\)/)
  assert.doesNotMatch(card, /if \(!form.problem.trim\(\) \|\| !form.content.trim\(\)\)/)
  assert.doesNotMatch(card, /disabled=\{saving \|\| !form.problem.trim\(\) \|\| !form.content.trim\(\)\}/)
})
