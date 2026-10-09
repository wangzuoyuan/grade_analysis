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

test('相关性卡读 P2 诊断端点并区分双域口径（r/rho=null 绝不编造）', () => {
  assert.match(card, /diagnosisCorrelation\(mode, \{/, '卡片必须走 P2 诊断相关性端点封装')
  assert.match(apiV1, /\/diagnosis\/correlation/, 'api-v1 应有 P2 诊断相关性端点')
  assert.match(apiV1, /export interface DiagnosisCorrelationResponse/, 'P2 响应类型已导出')
  assert.match(card, /corr\.r == null/, 'r=null 必须显式渲染「不可计算」态')
  assert.match(card, /相关系数不可计算（样本不足或零方差）/, '不可计算态文案固定')
  assert.match(card, /不构成因果结论/, '必须渲染免责声明（相关≠因果）')
  assert.match(card, /提分保证/, '免责声明须含「不构成提分保证」（P2 §2）')
  assert.match(card, /班主任口径：Y = 所选成绩指标（默认主三门总分，与 AI 助手一致）/, '班主任口径说明（y=分数；2026-09-29 与 AI 统一默认）')
  assert.match(card, /hw-corr-metric/, '班主任侧 Y 成绩指标选择器（口径对齐入口）')
  assert.match(card, /metric: teaching \? undefined : applied\.metric/, 'Y 指标显式传后端（教学域钉任教学科不传）')
  assert.match(card, /'total:主三门', label: '主三门总分'/, '默认指标 = 主三门总分')
  assert.match(card, /教学口径：Y = 任教学科单科成绩分数/, '教学口径说明（y=分数）')
  assert.match(shared, /export function correlationDirectionLabel/, '方向文案助手存在')
  assert.match(shared, /submit_up_score_up/, '方向值域随 P2 改为分数口径')
})

test('P2 新增：窗口起止展示、Spearman rho、分层表（契约 §2.3/§2.4）', () => {
  assert.match(card, /window_days/, '窗口天数选择器')
  assert.match(card, /window_start/, '窗口起止展示')
  assert.match(card, /不含考试当日与考后作业/, '窗口语义说明')
  assert.match(card, /ρ = /, 'rho 结果展示（与 r 并列）')
  assert.match(card, /Spearman ρ/, '分层表含 rho 列')
  assert.match(card, /corr\.layers\[key\]/, '分层渲染（all/段位）')
  assert.match(card, /layerReasonLabel/, '不可算层显示原因（n<8/零方差/分母未知过半）')
  assert.match(card, /corr\.note/, '后端 note（方向与指标含义）原样展示')
})

test('教学域学科钉死会话任教学科，班主任域自由输入', () => {
  assert.match(card, /const teaching = mode === 'teaching'/, '按 mode 区分双域')
  assert.match(card, /const effectiveSubject = teaching \? \(scopeSubject \?\? ''\) : applied\.subject/,
    'teaching 用 scopeSubject（固定），homeroom 用输入值')
  assert.match(card, /teaching && applied\.homeworkType !== ''/, 'homework_type 筛选仅教学侧传')
})

test('A3 修复：班主任域「计算」门控用学科草稿，不再死锁', () => {
  // 旧逻辑 canQuery = effectiveSubject !== ''（班主任域 = applied.subject，
  // 而 applied.subject 只能由本按钮 onClick 写入）→ 永远 disabled 的死锁，必须移除
  assert.doesNotMatch(card, /const canQuery = effectiveSubject !== ''/,
    '不得再用 applied 值做班主任域门控（死锁根因）')
  // homeroom 模式下草稿非空（且已选考试）即可点：门控看 subjectDraft；教学域仍看 scopeSubject
  assert.match(card, /const canQuery = \(teaching \? \(scopeSubject \?\? ''\) : subjectDraft\)\.trim\(\) !== '' && examName != null/,
    '班主任域门控须用学科草稿（教学域语义不变）')
  // 草稿为空时 disabled，并在学科输入框下就地渲染那句提示（仅班主任域）
  assert.match(card, /!teaching && subjectDraft\.trim\(\) === ''/,
    '「输入后可计算」提示仅在班主任域且草稿为空时渲染')
  assert.match(card, /输入作业学科（如：物理）后可计算/, '提示文案固定')
  // 禁用态按钮须用 title 悬浮说明不可点原因（未填学科 / 未选考试）
  assert.match(card, /title=\{disabledReason\}/, 'title 挂载禁用原因')
  assert.match(card, /不可计算：尚未填写作业学科/, '原因须区分未填学科')
  assert.match(card, /不可计算：尚未选择考试/, '原因须区分未选考试')
  // 查询仍由 applied/effectiveSubject 驱动：onClick（setApplied + nonce）语义不变
  assert.match(card, /const effectiveSubject = teaching \? \(scopeSubject \?\? ''\) : applied\.subject/,
    '查询 effect 仍用 applied 值（点击即 setApplied 触发查询）')
  assert.match(card, /setApplied\(\{ subject: subjectDraft, homeworkType, windowDays, metric: metricDraft \}\)/,
    'onClick 写入草稿快照的行为不变（homeworkType/metric/windowDays 关系不动）')
})

test('AI 工具注册表含 get_homework_correlation（两域可用，薄封装 P2 诊断模块）', () => {
  assert.match(chatTools, /def _tool_get_homework_correlation/, '工具 handler 存在')
  const specIdx = chatTools.indexOf('name="get_homework_correlation"')
  assert.ok(specIdx > 0, '注册表应有 get_homework_correlation 条目')
  const specBlock = chatTools.slice(specIdx, specIdx + 2600)
  assert.match(specBlock, /domains=\("homeroom", "teaching"\)/, '两域可用')
  assert.match(specBlock, /exam_name/, 'schema 含 exam_name')
  assert.match(chatTools, /from app\.diagnosis\.correlation import exam_homework_correlation/,
    '薄封装 P2 诊断相关性模块（A01 同源，不另写计算）')
})
