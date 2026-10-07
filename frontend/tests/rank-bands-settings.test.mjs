import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

// P0-A3：长期分段（名次区间）配置入口。长期业务分段、临时查询区间、
// 诊断阈值三者分开管理；学校默认三段保留为默认值；临时任意区间不得写改
// 长期配置。
const settingsPage = readFileSync(new URL('../src/app/settings/page.tsx', import.meta.url), 'utf8')
const rankBandsCard = readFileSync(
  new URL('../src/components/settings/RankBandsSettingsCard.tsx', import.meta.url),
  'utf8',
)
const homeroomScores = readFileSync(new URL('../src/components/scores/HomeroomScores.tsx', import.meta.url), 'utf8')
const teachingScores = readFileSync(new URL('../src/components/scores/TeachingScores.tsx', import.meta.url), 'utf8')

test('设置页提供长期名次分段的查看与修改入口（P0-A3）', () => {
  assert.match(settingsPage, /RankBandsSettingsCard/, '设置页须挂载长期名次分段设置卡')
  assert.match(rankBandsCard, /长期名次分段/, '设置卡须明确标识「长期名次分段」')
  // 读入口：GET /api/analysis-config 渲染当前三段
  assert.match(rankBandsCard, /fetch\('\/api\/analysis-config'/, '须读取长期配置端点')
  assert.match(rankBandsCard, /高分段：第 1–\$\{values\.high_score_max\} 名/, '须展示高分段区间')
  assert.match(rankBandsCard, /临界段：第 \$\{values\.critical_min\}–\$\{values\.critical_max\} 名/, '须展示临界段区间')
  assert.match(rankBandsCard, /薄弱段：第 \$\{values\.weak_min\} 名及以后/, '须展示薄弱段起点')
  // 写入口：PUT 保存自定义阈值
  assert.match(rankBandsCard, /method: 'PUT'/, '须提供保存（PUT）入口')
  assert.match(rankBandsCard, /排名阈值必须为正整数/, '前端校验须与后端 400 文案一致')
  assert.match(rankBandsCard, /临界段下界不能大于上界/, '临界段区间校验文案须与后端一致')
})

test('学校默认三段保留为默认值并可一键恢复（P0-A3）', () => {
  assert.match(rankBandsCard, /method: 'DELETE'/, '恢复默认须走 DELETE /api/analysis-config')
  assert.match(rankBandsCard, /恢复默认/, '须提供「恢复默认」按钮')
  assert.match(rankBandsCard, /is_default/, '须区分「学校默认」与「已自定义」状态')
  assert.match(rankBandsCard, /学校默认三段/, '默认态须明示为学校默认三段')
  // 出厂默认兜底与后端 analysis/config.py 一致：80 / 400 / 500 / 501
  assert.match(rankBandsCard, /high_score_max: 80/, '默认兜底须为高分 1–80')
  assert.match(rankBandsCard, /critical_min: 400/, '默认兜底须为临界 400 起')
  assert.match(rankBandsCard, /critical_max: 500/, '默认兜底须为临界 500 止')
  assert.match(rankBandsCard, /weak_min: 501/, '默认兜底须为薄弱 501 起')
  // 恢复默认是破坏性展示动作，须先确认
  assert.match(rankBandsCard, /window\.confirm/, '恢复默认前须二次确认')
})

test('临时查询区间与长期分段隔离：查询页只读参数不写配置（P0-A3）', () => {
  // 临时区间入口：成绩分析页的排名区间筛选（v1 fetchHomeroomRankRange，
  // 区间 rank_min/rank_max 以查询参数按次传入，api-v1.ts:1129）
  assert.match(homeroomScores, /fetchHomeroomRankRange\(/, '成绩分析页的排名区间筛选是临时查询入口')
  assert.match(homeroomScores, /rangeMin, rangeMax/, '筛选区间是按次传参的临时值')
  assert.doesNotMatch(homeroomScores, /analysis-config/, '临时查询页不得读写长期分段配置')
  assert.doesNotMatch(teachingScores, /analysis-config/, '教学工作台成绩页同样不得写长期配置')
  // 长期配置的 PUT/DELETE 只允许出现在设置卡
  assert.match(rankBandsCard, /method: 'PUT'/, '长期配置写入口只应在设置卡')
})

test('设置卡沿用设置页既有交互约束，不引入工作台筛选（P0-A3）', () => {
  assert.doesNotMatch(rankBandsCard, /useWorkspace/, '设置卡不得跟随工作台学年筛选')
  assert.doesNotMatch(settingsPage, /useWorkspace|academicYearId=\{filter\./, '设置页既有约束保持不变')
  assert.match(rankBandsCard, /apiErrorMessage/, '错误文案复用既有 helper')
})
