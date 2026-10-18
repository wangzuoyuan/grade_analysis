// 教学工作台成绩分析「分数段分析」卡契约测试（2026-10-18 用户需求：
// 把学校《分数段分析(物理)》表的分析维度整合进成绩分析，仅本班口径、
// 不做全年级）。要点：本段/累计/累计比例与 人数/最值/均分/σ，段口
// 左闭右开，缺考不计入段，全零尾段折叠，段宽 5/10/20，合并列仅在
// 多班范围出现；后端口径跨栈断言（读 backend 源码）。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const page = readFileSync(new URL('../src/components/scores/TeachingScores.tsx', import.meta.url), 'utf8')
const api = readFileSync(new URL('../src/lib/api-v1.ts', import.meta.url), 'utf8')
const backend = readFileSync(new URL('../../backend/app/api/analysis.py', import.meta.url), 'utf8')
const backendSchema = readFileSync(new URL('../../backend/app/api/analysis_schemas.py', import.meta.url), 'utf8')

test('api-v1 提供 score-bands 类型与 fetch（band_width 参数）', () => {
  assert.match(api, /export interface TeachingScoreBandsResponse/, '响应类型必须在场')
  assert.match(api, /cumulative_pct: number \| null/, '累计比例字段（null 不编造）必须在场')
  assert.match(api, /stddev: number \| null/, '标准差字段必须在场')
  assert.match(api, /export function fetchTeachingScoreBands/, 'fetch 函数必须在场')
  assert.match(api, /score-bands`?, \{\s*\.\.\.q,\s*band_width: bandWidth,/, '请求须携带 band_width')
})

test('成绩分析页挂载分数段分析卡，位于统计卡与学生成绩表之间', () => {
  const statsCard = page.indexOf('单科 stats 卡')
  const bandsCard = page.indexOf('分数段分析卡（官方《分数段分析》表同构的本班口径')
  const studentsCard = page.indexOf('学生表：分班展示')
  assert.ok(statsCard > -1 && bandsCard > -1 && studentsCard > -1, '三个卡片的挂载点都必须在场')
  assert.ok(statsCard < bandsCard && bandsCard < studentsCard, '分数段分析卡必须排在统计卡之后、学生成绩表之前')
  assert.match(page, /fetchTeachingScoreBands\(selectedExam, scopeQ, bandWidth\)/, '取数必须跟随考试/范围/段宽')
  assert.match(page, /const req = \+\+bandsReqRef\.current/, '独立请求序号（F11：迟到回包不覆盖新请求）')
})

test('本段/累计/比例三列 + 汇总行 + 段宽与显示切换', () => {
  assert.match(page, /本段[\s\S]*?累计[\s\S]*?比例%/, '明细模式必须有三列子表头')
  assert.match(page, /\['有效人数'[\s\S]*?\['最高分'[\s\S]*?\['最低分'[\s\S]*?\['平均分'[\s\S]*?\['标准差 σ'/, '汇总五行必须在场')
  assert.match(page, /仅人数/, '必须提供仅人数的显示切换')
  assert.match(page, /<option value="10">10 分<\/option>[\s\S]*?<option value="5">5 分<\/option>[\s\S]*?<option value="20">20 分<\/option>/, '段宽 5/10/20 三个选项')
})

test('合并列仅在多班范围出现；全零尾段折叠；缺考红线文案', () => {
  assert.match(page, /const showMerged = bands\.classes\.length > 1/, '单班范围不得出现重复的合并列')
  assert.match(page, /我的 \$\{String\(bands\.classes\.length\)\} 个班/, '合并列按班数动态命名')
  assert.match(page, /collapsedLow/, '必须计算全零尾段折叠下界')
  assert.match(page, /\(\*, \{String\(collapsedLow\)\}\)/, '折叠行标签形如 (*, 30)')
  assert.match(page, /缺考（原始分为空）不计入任何段与计量/, '缺考红线文案必须在场')
  assert.match(page, /不提供年级\/官方列/, '必须注明本班口径、不做年级列')
})

test('堆叠条段悬浮显示段名/人数/占比（Radix Tooltip，非原生 title）', () => {
  assert.match(page, /<TooltipProvider delayDuration=\{150\}>/, '堆叠条区必须包裹 TooltipProvider（150ms 即时悬浮）')
  assert.match(page, /<TooltipTrigger asChild>\s*<div\s+className="h-full cursor-default/, '每段须以 div 触发悬浮')
  assert.match(page, /\{String\(b\.count\)\} 人 ·\{' '\}/, '悬浮内容必须显示人数')
  assert.match(page, /\(\(b\.count \/ c\.valid_count\) \* 100\)\.toFixed\(1\)\}%/, '悬浮内容必须显示占班内比例')
  // 段元素不得再依赖原生 title（延迟约 1 秒、易被当成没显示）
  const segBlock = page.slice(page.indexOf('overflow-hidden rounded-full bg-slate-100'), page.indexOf('</TooltipProvider>'))
  assert.doesNotMatch(segBlock, /title=\{`\$\{bandLabel/, '段悬浮不得再走原生 title')
})

test('段位配色采用 Viridis 官方锚点（2026-10-18 用户拍板：不自配色）', () => {
  assert.match(page, /const VIRIDIS_ANCHORS = \[/, 'Viridis 锚点常量必须在场')
  // matplotlib 官方 8 点采样的首尾锚点（高分段亮端 → 低分段暗端）
  assert.match(page, /'#fde725'/, '锚点须含亮端 #fde725（高分段）')
  assert.match(page, /'#440154'/, '锚点须含暗端 #440154（低分段）')
  assert.match(page, /感知均匀[\s\S]*?色盲安全/, '须注明色系出处与特性')
  assert.match(page, /function hexToRgb/, '须有 hex→RGB 解析供插值')
  assert.match(page, /VIRIDIS_ANCHORS\.length - 2/, '插值须钳制在锚点区间内')
  assert.doesNotMatch(page, /hsl\(201 74%/, '不得再残留旧的自配 HSL 渐变')
})

test('跨栈：后端 score-bands 口径（左闭右开/累计自高向下/总体σ/段宽白名单/本班范围）', () => {
  assert.match(backend, /teaching\/analysis\/exams\/\{exam_name\}\/score-bands/, '端点路由必须在场')
  assert.match(backend, /SCORE_BAND_WIDTHS = \(5, 10, 20\)/, '段宽白名单')
  assert.match(backend, /if score >= edges\[0\]:\s*\n\s*counts\[0\] \+= 1/, '顶段左闭：90 入 [90,+∞)')
  assert.match(backend, /float\(int\(score \/\/ width\)\) \* width/, '其余段按 floor 下界归段（左闭右开）')
  assert.match(backend, /cumulative \+= counts\[i\]/, '累计自高分段向下')
  assert.match(backend, /sum\(\(s - mean\) \*\* 2 for s in valid\) \/ len\(valid\)/, '总体标准差 σ（除以 N）')
  assert.match(backend, /score is not None\]/, '缺考 NULL 不进段与计量')
  assert.match(backend, /_teaching_member_rows\(db, ctx, \[tc_id\], subject, exam_name, member_ids\)/, '成员口径与 stats/students 同源（F08/F09），绝不扩到其他班')
  assert.match(backendSchema, /class TeachingScoreBandsResponse/, '响应模型必须在场')
  assert.match(backendSchema, /class TeachingScoreBandClass/, '列模型（班级列/合并列）必须在场')
})
