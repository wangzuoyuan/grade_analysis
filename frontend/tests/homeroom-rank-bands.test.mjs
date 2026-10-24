// 班主任工作台「名次段分布」契约测试（2026-10-19 用户需求：段位分布改读
// 导入的《3+3总分名次段表》《主三门名次段表》，参考学校名次段表格样式 +
// 教学工作台分数段堆叠条；班级均分表表头按学校导出重制、排名按平行班/
// 实验班分别计算；明细表表头按学校导出重制；两域成绩不互通）。
// 跨栈断言读 backend 源码。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const page = readFileSync(new URL('../src/components/scores/HomeroomScores.tsx', import.meta.url), 'utf8')
const bandColors = readFileSync(new URL('../src/components/scores/band-colors.ts', import.meta.url), 'utf8')
const api = readFileSync(new URL('../src/lib/api-v1.ts', import.meta.url), 'utf8')
const backend = readFileSync(new URL('../../backend/app/api/analysis.py', import.meta.url), 'utf8')
const importsPy = readFileSync(new URL('../../backend/app/api/imports.py', import.meta.url), 'utf8')
const wsParse = readFileSync(new URL('../../backend/app/ingest/ws_parse.py', import.meta.url), 'utf8')

test('api-v1 提供名次段分布类型与 fetch（total_type 参数）', () => {
  assert.match(api, /export interface HomeroomRankBandsResponse/, '响应类型必须在场')
  assert.match(api, /available_total_types: string\[\]/, '可用口径清单（空态判定依据）')
  assert.match(api, /grade_bands: HomeroomRankBandItem\[\]/, '年级行合计字段')
  assert.match(api, /export function fetchHomeroomRankBands/, 'fetch 函数必须在场')
  assert.match(api, /total_type: totalType/, '请求须携带口径参数')
})

test('名次段分布面板：堆叠条 + 学校表格样式 + 本班高亮 + 年级行', () => {
  assert.match(page, /function RankBandsPanel/, '独立面板组件')
  assert.match(page, /bandBarColor\(i, c\.bands\.length, INFERNO_ANCHORS\)/, '段色按段绝对位置取 Inferno 色（2026-10-21 拍板）')
  assert.match(page, /\{String\(b\.count\)\} 人 · \{|\{String\(b\.count\)\} 人 ·\{' '\}/, '悬浮须显示人数')
  assert.match(page, /\(\(b\.count \/ c\.total\) \* 100\)\.toFixed\(1\)\}%/, '悬浮须显示占班内比例')
  assert.match(page, /名/s, '悬浮段名带「名」语义')
  // 学校表格：行=班级+班主任、列=段+合计、末行全年级
  assert.match(page, /全年级/, '表格须有全年级合计行')
  assert.match(page, /（本班）/, '本班行须标注')
  assert.match(page, /collapsedFrom/, '全零尾段折叠（图例与表格一致）')
  // 空态：classes 为空 → 上传引导，绝不编造分段
  assert.match(page, /rankBands\.classes\.length === 0/, '空态分支按 classes 判定')
  assert.match(page, /尚未导入名次段表/, '空态文案必须在场')
})

test('名次段卡 2026-10-21 拍板：Inferno 配色 + 白色细分隔线 + 悬浮联动高亮同段', () => {
  assert.match(bandColors, /export const INFERNO_ANCHORS = \[/, 'Inferno 锚点常量必须在场（官方 8 点采样）')
  assert.match(bandColors, /'#fcffa4'/, 'Inferno 亮端锚点（高名次段）')
  assert.match(bandColors, /'#000004'/, 'Inferno 暗端锚点（低名次段）')
  assert.match(page, /import \{ bandBarColor, INFERNO_ANCHORS \} from '\.\/band-colors'/, '名次段卡须引用 Inferno 配色')
  assert.match(page, /bandBarColor\(i, bandCount, INFERNO_ANCHORS\)/, '图例同用 Inferno')
  assert.doesNotMatch(page, /'#fde725'/, '名次段卡不得残留 Viridis 亮端锚点')
  // 白色细分隔线：段与段之间 1px（首段除外，由兄弟选择器保证）
  assert.match(page, /\[\&>\*\+\*\]:border-l \[\&>\*\+\*\]:border-white\/90/, '段间须有 1px 白色分隔线')
  // 悬浮联动：同一段在全部班级行高亮，其余降暗
  assert.match(page, /const \[hoverBand, setHoverBand\] = useState<number \| null>\(null\)/, '联动高亮状态')
  assert.match(page, /onMouseEnter=\{\(\) => setHoverBand\(i\)\}/, '悬浮记录当前段序')
  assert.match(page, /onMouseLeave=\{\(\) => setHoverBand\(null\)\}/, '离开堆叠条区清除高亮')
  assert.match(page, /hoverBand != null && hoverBand !== i && 'opacity-25'/, '其余段降暗')
  assert.match(page, /hoverBand === i && 'ring-1 ring-inset ring-slate-900\/50'/, '同段高亮描边')
})

test('跨栈：后端 rank-bands 端点只读导入数据、空态不 404', () => {
  assert.match(backend, /homeroom\/analysis\/exams\/\{exam_name\}\/rank-bands/, '端点路由必须在场')
  assert.match(backend, /数据只来自 workspace_rank_band[\s\S]*?不从学生[\s\S]*?名次现算/s, '须声明只读导入表')
  assert.match(backendSchemaRt(), /class HomeroomRankBandsResponse/, '响应模型必须在场')
})

function backendSchemaRt() {
  return readFileSync(new URL('../../backend/app/api/analysis_schemas.py', import.meta.url), 'utf8')
}

test('跨栈：名次段导入链路（判型/口径/幂等）', () => {
  assert.match(importsPy, /WorkspaceRankBand/, 'confirm 须落专用表')
  assert.match(importsPy, /rank_band_inserts/, '预检插入计划')
  assert.match(importsPy, /rank_band_skips/, '幂等 skip 计数')
  assert.match(wsParse, /kind == "rank_bands"/, 'ws_parse 须透传名次段分支')
  assert.match(wsParse, /rank_bands: Optional\[dict\]/, 'ParsedImportFile 须有 rank_bands 字段')
})

test('班级均分表：班型合并单元格 + 总分仅均分/班型排名 + 班型内排名', () => {
  assert.match(page, /subjectColumnGroups/, '学科列分组（语数英单列 + 选科原始/等级）')
  assert.match(page, /typeSpans/, '班级类型列同值合并 rowSpan 计算')
  assert.match(page, /班型排名/, '排名列名标注班型内口径')
  // 2026-10-22 拍板：总分没有及格率/高分率/低分率/标准差指标（导入数据恒为空），四列不再展示
  assert.doesNotMatch(page, /及格率|高分率|低分率|标准差/, '总分区块只留 均分+班型排名，率值/标准差列不得残留')
  assert.match(page, /colSpan=\{2\} className="whitespace-nowrap border-r text-center text-xs font-semibold">\{totalType\}总分/, '总分区块表头只跨 均分+班型排名 两列')
  assert.match(page, /排名按平行班、实验班分别计算/, '说明文案注明分组口径')
  assert.match(backend, /ranks_by_total\[total_type\]\.update\(_rank_map\(pairs\)\)/, '后端按班型分组各排')
  assert.match(backend, /type_groups\.setdefault\(record\.class_type or "未分类"/, '分组键为 class_type')
})

test('明细表表头按学校导出：学号/班级/学籍/姓名 + 选科原始/等级 + 名次列', () => {
  assert.match(page, /学籍/, '学籍列')
  assert.match(page, /\{`\$\{tt\}-school`[\s\S]{0,200}名次<\/TableHead>/, '名次列（2026-10-24 起按学籍代码选列）')
  assert.match(page, /班名次/, '班名次列（本班口径前端计算）')
  assert.match(page, /grade_scores/, '须消费等级分扩展字段')
  assert.match(page, /percentiles/, '须消费百分位扩展字段')
  assert.match(backend, /grade_scores_by_person/, '后端须聚合等级分')
  assert.match(backend, /ranks_by_person/, '后端须聚合名次')
})
