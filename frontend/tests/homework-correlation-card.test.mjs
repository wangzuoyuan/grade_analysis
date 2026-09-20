// 「作业提交率 × 成绩相关性」卡挂载契约测试
// （契约 docs/contracts/p5-homework.md §4/§7 相关性散点；p6-ai-mcp.md §2 工具表）。
// 风格沿用 tests/homeroom-compare.test.mjs：直接读源码断言关键结构，不启动浏览器。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const workspace = readFileSync(
  new URL('../src/components/homework/HomeworkWorkspace.tsx', import.meta.url), 'utf8')
const card = readFileSync(
  new URL('../src/components/homework/CorrelationCard.tsx', import.meta.url), 'utf8')
const shared = readFileSync(
  new URL('../src/components/homework/shared.ts', import.meta.url), 'utf8')
const apiV1 = readFileSync(new URL('../src/lib/api-v1.ts', import.meta.url), 'utf8')
const homeroomPage = readFileSync(
  new URL('../src/app/homeroom/homework/page.tsx', import.meta.url), 'utf8')
const teachingPage = readFileSync(
  new URL('../src/app/teaching/homework/page.tsx', import.meta.url), 'utf8')
const homeroomScores = readFileSync(
  new URL('../src/components/scores/HomeroomScores.tsx', import.meta.url), 'utf8')
const teachingScores = readFileSync(
  new URL('../src/components/scores/TeachingScores.tsx', import.meta.url), 'utf8')
const chatTools = readFileSync(
  new URL('../../backend/app/api/chat_tools.py', import.meta.url), 'utf8')

test('CorrelationCard 挂入两域成绩分析页（作业页移除）', () => {
  assert.match(homeroomScores, /import \{ CorrelationCard \} from '@\/components\/homework\/CorrelationCard'/,
    'HomeroomScores 应导入 CorrelationCard')
  assert.match(homeroomScores, /<CorrelationCard mode="homeroom" scopeQ=\{hwScopeQ\} scopeSubject=\{scopeSubject\} generation=\{generation\} preferredExamName=\{selectedExam\} \/>/,
    '班主任成绩页应挂载 CorrelationCard（作业域作用域+考试跟随顶部选择）')
  assert.match(teachingScores, /import \{ CorrelationCard \} from '@\/components\/homework\/CorrelationCard'/,
    'TeachingScores 应导入 CorrelationCard')
  assert.match(teachingScores, /<CorrelationCard mode="teaching" scopeQ=\{hwScopeQ\} scopeSubject=\{scopeSubject\} generation=\{generation\} preferredExamName=\{selectedExam\} \/>/,
    '教学成绩页应挂载 CorrelationCard（作业域作用域+考试跟随顶部选择）')
  assert.doesNotMatch(workspace, /CorrelationCard/, '作业跟进页不再挂载相关性卡')
  // 两域路由都渲染同一容器（mode 由路径注入），作业域作用域映射不变
  assert.match(homeroomPage, /HomeworkWorkspace mode="homeroom"/)
  assert.match(teachingPage, /HomeworkWorkspace mode="teaching"/)
})

test('相关性卡读 v1 端点并区分双域口径（r=null 绝不编造）', () => {
  assert.match(card, /homeworkCorrelation\(mode, \{/, '卡片必须走 v1 相关性端点封装')
  assert.match(apiV1, /\/homework\/correlation/, 'api-v1 应有相关性端点')
  assert.match(card, /corr\.r == null/, 'r=null 必须显式渲染「不可计算」态')
  assert.match(card, /相关系数不可计算（样本不足或零方差）/, '不可计算态文案固定')
  assert.match(card, /不构成因果结论/, '必须渲染免责声明（相关≠因果）')
  assert.match(card, /班主任口径：Y = 指定总分口径的年级名次（默认主三门）/, '班主任口径说明')
  assert.match(card, /教学口径：Y = 该科单科本班名次（同分同名次）/, '教学口径说明')
  assert.match(shared, /export function correlationDirectionLabel/, '方向文案助手存在')
})

test('教学域学科钉死会话任教学科，班主任域自由输入', () => {
  assert.match(card, /const teaching = mode === 'teaching'/, '按 mode 区分双域')
  assert.match(card, /const effectiveSubject = teaching \? \(scopeSubject \?\? ''\) : applied\.subject/,
    'teaching 用 scopeSubject（固定），homeroom 用输入值')
  assert.match(card, /teaching && applied\.homeworkType !== ''/, 'homework_type 筛选仅教学侧传')
})

test('AI 工具注册表含 get_homework_correlation（两域可用，薄封装 v1）', () => {
  assert.match(chatTools, /def _tool_get_homework_correlation/, '工具 handler 存在')
  const specIdx = chatTools.indexOf('name="get_homework_correlation"')
  assert.ok(specIdx > 0, '注册表应有 get_homework_correlation 条目')
  const specBlock = chatTools.slice(specIdx, specIdx + 2400)
  assert.match(specBlock, /domains=\("homeroom", "teaching"\)/, '两域可用')
  assert.match(specBlock, /exam_name/, 'schema 含 exam_name')
  assert.match(chatTools, /from app\.api\.homework import homework_correlation/,
    '薄封装 v1 correlation service（A01 同源，不另写查询）')
})
