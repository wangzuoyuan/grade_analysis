// P1-B4 纵向切片契约测试：诊断卡三处同源（学生页 / 班主任首页 / AI 工具）。
// 前端侧断言（契约 docs/diagnosis-roadmap/p1-contracts.md §6）：
// - DiagnosisCard 按契约 §2/§3 JSON 形状开发（组件内 mock 类型 + fixture
//   驱动渲染逻辑），取数只走 B1/B2 诊断端点（同一数据源）；
// - 主类型+次标签+证据+六类指标摘要齐备；数据不足/缺失态如实显示；
// - 班主任首页类型分布卡 = 班级 types 分布 top + 优先关注（附 evidence 一句）；
// - 学生页装配点（班主任学生档案）与首页装配点（homeroom/page.tsx）到位。
// 后端侧「学生页端点 / 首页端点 / AI 工具」逐字段一致断言见
// backend/tests/v1/test_p1_b4_tool.py（透传零改写 + 注册表机制）。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const card = readFileSync(new URL('../src/components/student/DiagnosisCard.tsx', import.meta.url), 'utf8')
const homeroomPage = readFileSync(new URL('../src/app/homeroom/page.tsx', import.meta.url), 'utf8')
const profileView = readFileSync(new URL('../src/components/students/HomeroomProfileView.tsx', import.meta.url), 'utf8')

test('P1-B4：诊断卡取数只走 B1/B2 契约端点（三处同源的数据源唯一）', () => {
  assert.match(card, /\/api\/v1\/\$\{mode\}\/diagnosis\/features/, 'B1 特征层端点路径必须与契约 §2 一致')
  assert.match(card, /\/api\/v1\/\$\{mode\}\/diagnosis\/types/, 'B2 类型引擎端点路径必须与契约 §3 一致')
  assert.match(card, /person_id=.*academic_year_id|params\.set\('academic_year_id'/, '查询参数=person_id + 可选 academic_year_id（契约 §2/§3）')
  assert.match(card, /fetchDiagnosisFeatures/, 'features 取数走导出的同源助手')
  assert.match(card, /fetchDiagnosisTypes/, 'types 取数走导出的同源助手')
  assert.match(card, /calc_version/, '响应须携带口径版本')
  assert.match(card, /p1-v1/, '口径版本 p1-v1')
})

test('P1-B4：契约 §2/§3 JSON 形状的 mock 类型与 fixture 驱动渲染逻辑', () => {
  // §2 六类指标逐一有 mock 类型定义
  assert.match(card, /current_level:/, 'mock 类型含当前水平')
  assert.match(card, /trend:/, 'mock 类型含趋势')
  assert.match(card, /stability:/, 'mock 类型含稳定性')
  assert.match(card, /imbalance:/, 'mock 类型含偏科')
  assert.match(card, /homework_behavior:/, 'mock 类型含作业行为')
  assert.match(card, /teacher_attention:/, 'mock 类型含教师关注')
  assert.match(card, /missing_reason/, 'main3 契约字段 missing_reason')
  assert.match(card, /direction_recent/, 'trend 契约字段 direction_recent')
  assert.match(card, /current_streak_days/, '作业行为契约字段（按天连缺）')
  // §3 类型判定形状
  assert.match(card, /main_type: string \| null/, 'main_type 可空（数据不足不强行归类）')
  assert.match(card, /secondary_tags: string\[\]/, '次标签数组')
  assert.match(card, /classification_status: 'classified' \| 'insufficient_data'/, '分类状态二值')
  assert.match(card, /evidence: Array<\{ type: string; basis: string \}>/, '证据=类型+依据')
  // fixture 驱动：契约形状样例内置于组件（端点上线前后同一套渲染路径）
  assert.match(card, /DIAGNOSIS_FEATURES_FIXTURE/, 'B1 契约形状 fixture')
  assert.match(card, /DIAGNOSIS_TYPES_FIXTURE/, 'B2 契约形状 fixture')
  assert.match(card, /DIAGNOSIS_INSUFFICIENT_TYPES_FIXTURE/, '数据不足 fixture')
})

test('P1-B4：主类型+次标签+证据+六类指标摘要渲染齐备', () => {
  assert.match(card, /学情诊断/, '卡片标题')
  assert.match(card, /types\.main_type\}/, '主类型徽章')
  assert.match(card, /types\.secondary_tags\.map/, '次标签逐枚渲染')
  assert.match(card, /types\.evidence\.map/, '证据列表渲染')
  assert.match(card, /item\.basis\}/, '证据必须展示 basis（引用具体 B1 字段值）')
  assert.match(card, /diagnosisIndicatorRows/, '六类指标摘要行（纯函数驱动）')
  for (const label of ['当前水平', '趋势', '稳定性', '偏科', '作业行为', '教师关注']) {
    assert.match(card, new RegExp(label), `六类指标含「${label}」`)
  }
  assert.match(card, /百分位=年级相对位置/, '百分位带方向/单位解释')
})

test('P1-B4：数据不足与缺失态如实显示（缺失纪律：不转 0、不残留、不强行归类）', () => {
  assert.match(card, /insufficient_data/, '判定状态判断')
  assert.match(card, /数据不足，不强行归类/, 'insufficient 时主类型槽位如实显示')
  assert.match(card, /有效考试不足 2 场/, 'insufficient 原因说明（valid_exam_count<2）')
  assert.match(card, /missing/, '指标缺失态标记')
  assert.match(card, /const DASH = '—'/, '缺失值显示「—」不转 0')
  assert.match(card, /诊断数据暂不可用（数据缺失）/, '端点未部署/请求失败的数据缺失态如实显示')
  assert.match(card, /尚未部署或该生暂无有效数据/, '缺失态注明原因')
  assert.doesNotMatch(card, /score \?\? 0|\|\| 0\s*分|fallback.*0/, '不得把缺失值折算为 0')
})

test('P1-B4：班主任首页类型分布卡=班级 types 分布 top + 优先关注（附 evidence 一句）', () => {
  assert.match(card, /PRIORITY_ATTENTION_TYPES = \['综合风险型', '持续下滑型', '临界下滑型'\]/, '优先关注类型集合（契约 §6.2）')
  assert.match(card, /aggregateTypeDistribution/, '分布聚合纯函数')
  assert.match(card, /pickPriorityAttention/, '优先关注提取纯函数')
  assert.match(card, /entry\.type \?\? '数据不足'/, '数据不足学生在分布中单列如实显示')
  assert.match(card, /priority\.slice\(0, 6\)/, '优先关注取前若干名')
  assert.match(card, /item\.basis \|\| '暂无判定依据说明'/, '优先关注附 evidence 一句')
  assert.match(card, /useSharedActionSummary/, '首页分布复用行动摘要的班级 B2 结果')
  assert.match(card, /summary\.class_types/, '班级类型来自后端 B2 判定（前端绝不复算）')
  assert.doesNotMatch(card, /fetchDiagnosisTypes\('homeroom', Number\(pid\), q\)/, '首页不得再逐生请求 B2')
  assert.match(card, /failed === cohortSize/, '全员失败=数据缺失态判定')
  assert.match(card, /诊断数据暂不可用/, '首页卡数据缺失态如实显示')
  assert.match(card, /已计为数据不足；部分缺失如实标注，不代表完整人数/, '部分失败如实标注不冒充完整')
})

test('P1-B4：学生页装配点（班主任学生档案）与班主任首页装配点到位', () => {
  assert.match(
    profileView,
    /import \{ DiagnosisCard \} from '@\/components\/student\/DiagnosisCard'/,
    '学生档案视图导入诊断卡',
  )
  assert.match(
    profileView,
    /<DiagnosisCard\s+mode="homeroom"\s+personId=\{Number\(selectedPersonId\)\}\s+academicYearId=\{scopeQ\.academic_year_id\}\s*\/>/,
    '学生页装配：mode/person_id/学年参数齐备',
  )
  assert.match(
    homeroomPage,
    /import \{ HomeroomDiagnosisOverviewCard \} from '@\/components\/student\/DiagnosisCard'/,
    '班主任首页导入类型分布卡',
  )
  assert.match(homeroomPage, /<HomeroomDiagnosisOverviewCard \/>/, '班主任首页挂载类型分布卡')
  assert.match(homeroomPage, /<HomeroomOverview \/>/, '既有数据看板保持不动（追加式装配）')
})
