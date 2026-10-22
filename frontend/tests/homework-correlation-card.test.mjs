// 「作业提交率 × 成绩相关性」卡挂载契约测试（P2-C1 起改接
// /api/v1/{域}/diagnosis/correlation：Pearson + Spearman 双指标分层，
// 契约 docs/diagnosis-roadmap/p2-contracts.md §2）。
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

test('CorrelationCard 仅挂入教学工作台成绩页（2026-10-22 班主任成绩页移除，个人作业与成绩分析并入学生档案）', () => {
  assert.doesNotMatch(homeroomScores, /CorrelationCard/,
    'HomeroomScores 不再挂载相关性卡（「个人作业与成绩分析」并入学生档案页）')
  assert.doesNotMatch(homeroomScores, /homeworkScopeQuery/,
    '随卡卸载作业域作用域映射（班主任成绩页不再需要）')
  assert.match(teachingScores, /import \{ CorrelationCard \} from '@\/components\/homework\/CorrelationCard'/,
    'TeachingScores 应导入 CorrelationCard')
  assert.match(teachingScores, /<CorrelationCard mode="teaching" scopeQ=\{hwScopeQ\} scopeSubject=\{scopeSubject\} generation=\{generation\} preferredExamName=\{selectedExam\} \/>/,
    '教学成绩页应挂载 CorrelationCard（作业域作用域+考试跟随顶部选择）')
  assert.doesNotMatch(workspace, /CorrelationCard/, '作业跟进页不再挂载相关性卡')
  // 两域路由都渲染同一容器（mode 由路径注入），作业域作用域映射不变
  assert.match(homeroomPage, /HomeworkWorkspace mode="homeroom"/)
  assert.match(teachingPage, /HomeworkWorkspace mode="teaching"/)
})

test('个人卡走独立逐人端点，拒绝班级系数与迟到响应', () => {
  assert.match(card, /studentHomeworkAnalysis\(mode, \{/)
  assert.match(apiV1, /diagnosis\/homework-students/)
  assert.doesNotMatch(card, /diagnosisCorrelation\(|corr\.layers|LayerRow|全班/)
  assert.match(card, /cancelled = true/)
  assert.match(card, /result\?\.key === key/)
  assert.match(card, /preferredExamName/)
  assert.match(card, /student\.timeline/)
})

test('个人分析显示年级固定口径、配对分母与评价覆盖', () => {
  assert.match(card, /高一用年级百分位，高二、高三用等级分/)
  assert.match(card, /不含考试当日与考后作业/)
  assert.match(card, /提交率 × 较上次进步/)
  assert.match(card, /负面评价率 × 成绩水平/)
  assert.match(card, /evaluation_coverage/)
  assert.match(card, /unknown_batches/)
  assert.match(card, /Kendall τb为主/)
  assert.match(card, /不是置信区间/)
  assert.match(card, /tau_without_one/)
})

test('教学学科固定，个人选择可搜索，缺失趋势不补连', () => {
  assert.match(card, /const subject = teaching \? scopeSubject \?\? '' : subjectDraft/)
  assert.match(card, /搜索学生姓名或学号/)
  assert.match(card, /aria-pressed=/)
  assert.match(card, /connectNulls=\{false\}/)
  assert.match(card, /没有评价不代表优良/)
})

test('AI 工具注册表含 get_homework_correlation（两域可用，薄封装 P2 诊断模块）', () => {
  assert.match(chatTools, /def _tool_get_homework_correlation/, '工具 handler 存在')
  const specIdx = chatTools.indexOf('name="get_homework_correlation"')
  assert.ok(specIdx > 0, '注册表应有 get_homework_correlation 条目')
  const specBlock = chatTools.slice(specIdx, chatTools.indexOf('handler=_tool_get_homework_correlation', specIdx))
  assert.match(specBlock, /domains=\("homeroom", "teaching"\)/, '两域可用')
  assert.match(specBlock, /exam_name/, 'schema 含 exam_name')
  assert.match(specBlock, /person_id/, '单生必须传身份ID')
  assert.match(chatTools, /from app\.diagnosis\.student_homework import student_homework_analysis/)
  assert.match(chatTools, /from app\.diagnosis\.correlation import exam_homework_correlation/,
    '薄封装 P2 诊断相关性模块（A01 同源，不另写计算）')
})
