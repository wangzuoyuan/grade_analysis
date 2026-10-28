// P3-FE 收口契约测试：分区上传 + 两工作台成绩分析页（契约 docs/contracts/p3-imports-analysis.md §1/§2/§3）。
// 风格沿用 tests/link-p2.test.mjs：直接读源码断言关键结构，不启动浏览器。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const apiV1 = readFileSync(new URL('../src/lib/api-v1.ts', import.meta.url), 'utf8')
const homeroomPage = readFileSync(new URL('../src/app/homeroom/scores/page.tsx', import.meta.url), 'utf8')
const teachingPage = readFileSync(new URL('../src/app/teaching/scores/page.tsx', import.meta.url), 'utf8')
const homeroomScores = readFileSync(new URL('../src/components/scores/HomeroomScores.tsx', import.meta.url), 'utf8')
const teachingScores = readFileSync(new URL('../src/components/scores/TeachingScores.tsx', import.meta.url), 'utf8')
const scoreYearPicker = readFileSync(new URL('../src/components/scores/ScoreYearPicker.tsx', import.meta.url), 'utf8')
const scoresShared = readFileSync(new URL('../src/components/scores/shared.ts', import.meta.url), 'utf8')
const uploadPage = readFileSync(new URL('../src/app/upload/page.tsx', import.meta.url), 'utf8')
const previewTable = readFileSync(new URL('../src/components/upload/ImportsPreviewTable.tsx', import.meta.url), 'utf8')
const conflictsPanel = readFileSync(new URL('../src/components/upload/ImportConflictsPanel.tsx', import.meta.url), 'utf8')
const sidebar = readFileSync(new URL('../src/components/layout/Sidebar.tsx', import.meta.url), 'utf8')

test('api-v1 导出 P3 导入/考试/分析封装与类型（契约 §1/§2）', () => {
  for (const fn of [
    'importsPreviewMultipart',
    'importsConfirmP3',
    'readImportConflicts',
    'listExams',
    'fetchHomeroomStats',
    'fetchHomeroomClassAverages',
    'fetchHomeroomStudents',
    'fetchHomeroomFocus',
    'fetchHomeroomRankMetrics',
    'fetchHomeroomRankFrequency',
    'fetchHomeroomRankRange',
    'fetchHomeroomRankDistribution',
    'fetchHomeroomBands',
    'fetchHomeroomTrends',
    'fetchTeachingStats',
    'fetchTeachingStudents',
    'fetchTeachingClassCompare',
  ]) {
    assert.match(apiV1, new RegExp(`export function ${fn}\\(`), `应导出 ${fn}`)
  }
  for (const t of [
    'ImportPreviewItem',
    'ImportPreviewNewStudent',
    'ImportPreviewKind',
    'ImportsPreviewResult',
    'ImportConfirmConflict',
    'ImportsConfirmResult',
    'ExamSummary',
    'AnalysisScopeQuery',
    'HomeroomStatsResponse',
    'HomeroomStudentRow',
    'HomeroomStudentsResponse',
    'HomeroomFocusResponse',
    'HomeroomRankMetric',
    'HomeroomRankFrequencyResponse',
    'HomeroomRankRangeResponse',
    'HomeroomRankDistributionResponse',
    'HomeroomBandsQuery',
    'HomeroomBandsResponse',
    'HomeroomTrendPoint',
    'HomeroomTrendYear',
    'HomeroomTrendsResponse',
    'TeachingStatsResponse',
    'TeachingStudentRow',
    'TeachingStudentsResponse',
    'TeachingClassCompareItem',
    'TeachingClassCompareResponse',
  ]) {
    assert.match(apiV1, new RegExp(`export (interface|type) ${t}\\b`), `应导出类型 ${t}`)
  }
  assert.match(apiV1, /\/shared\/exams/, '考试清单端点应挂在 /shared/exams')
  assert.match(apiV1, /\/imports\/preview/, '导入预览端点应挂在 /imports/preview')
  assert.match(apiV1, /\/imports\/confirm/, '导入确认端点应挂在 /imports/confirm')
  assert.match(apiV1, /homeroom\/analysis/, '班主任分析端点应挂在 /homeroom/analysis/*')
  assert.match(apiV1, /teaching\/analysis/, '教学分析端点应挂在 /teaching/analysis/*')
  assert.match(apiV1, /class-compare/, '班级对比端点应挂在 /teaching/analysis/class-compare')
  // multipart 专用请求函数：不得手工设 Content-Type（boundary 由浏览器生成）
  assert.match(apiV1, /async function requestMultipart</, 'multipart 请求须独立于 JSON request helper')
  const mpStart = apiV1.indexOf('async function requestMultipart')
  const mpEnd = apiV1.indexOf('export function fetchSharedConfig')
  const multipartBody = apiV1.slice(mpStart, mpEnd > mpStart ? mpEnd : mpStart + 800)
  assert.ok(mpStart >= 0 && multipartBody.length > 0, '应能定位 requestMultipart 函数体')
  assert.doesNotMatch(multipartBody, /Content-Type/, 'multipart 请求不得手工设置 Content-Type')
  // 旧 P1 骨架（恒空清单）应被 P3 语义替换
  assert.doesNotMatch(apiV1, /export function importsConfirm\(/, '旧 importsConfirm 骨架应删除或升级为 importsConfirmP3')
  assert.doesNotMatch(apiV1, /export function importsPreview\(/, '旧 importsPreview 骨架应删除或升级为 multipart 版本')
})

test('两成绩页存在、读 useWorkspace 并经 /shared/exams 取考试', () => {
  assert.match(homeroomPage, /HomeroomScores/, '班主任成绩页应渲染 HomeroomScores')
  assert.match(teachingPage, /TeachingScores/, '教学成绩页应渲染 TeachingScores')
  for (const [src, name, mode] of [
    [homeroomScores, '班主任成绩页', 'homeroom'],
    [teachingScores, '教学成绩页', 'teaching'],
  ]) {
    assert.match(src, /useWorkspace\(\)/, `${name}必须读工作台上下文`)
    assert.match(src, new RegExp(`listExams\\('${mode}'`), `${name}必须按 ${mode} 模式拉考试清单`)
    assert.match(src, /analysisScopeQuery\(filter\)/, `${name}查询参数必须从工作台筛选映射`)
    assert.match(src, /generation/, `${name}必须响应工作台世代号`)
  }
})

test('成绩页可切换历史学年且切年清理陈旧班级范围（P8-D）', () => {
  assert.match(scoreYearPicker, /listAcademicYears/, '学年选项必须来自后端目录')
  assert.match(scoreYearPicker, /academic_year_id: value === YEAR_DEFAULT \? undefined : Number\(value\)/, '切换须写入工作台学年筛选')
  assert.match(scoreYearPicker, /term_id: undefined/, '切年须清理旧学期')
  assert.match(scoreYearPicker, /patch\.teaching_class_id = 'all'/, '切年须清理旧教学班 id')
  assert.match(scoreYearPicker, /patch\.class_id = undefined/, '切年须清理旧行政班 id')
  assert.match(homeroomScores, /<ScoreYearPicker \/>/, '班主任成绩页应显示学年选择器')
  assert.match(teachingScores, /<ScoreYearPicker \/>/, '教学成绩页应显示学年选择器')
})

test('班主任成绩页支持 URL ?tab= 直达（2026-10-22：白名单作初始 tab，非法回退默认）', () => {
  assert.match(homeroomScores, /searchParams\.get\('tab'\)/, '读取 ?tab= 参数')
  assert.match(homeroomScores, /const SCORES_TABS = new Set\(\['averages', 'students', 'bands', 'duanwei', 'frequency', 'range', 'focus'\]/, '七个板块白名单')
  assert.match(homeroomScores, /SCORES_TABS\.has\(requestedTab\) \? requestedTab : 'averages'/, '非法/缺省回退班级均分表')
  assert.match(homeroomScores, /<Tabs defaultValue=\{initialTab\}/, '白名单值仅作初始值（非受控，页内切换照旧）')
})

test('缺考一律显示「—」，展示层绝不转 0（契约 §2.3）', () => {
  assert.match(scoresShared, /v == null\)\s*return '—'/, 'formatScore 对 null 返回「—」')
  for (const [src, name] of [
    [homeroomScores, '班主任成绩页'],
    [teachingScores, '教学成绩页'],
  ]) {
    assert.match(src, /\? '—'/, `${name}分数单元格缺考应渲染「—」`)
    assert.doesNotMatch(src, /\?\? 0/, `${name}不得把缺考转 0`)
  }
})

test('趋势按学年分段展示，不跨年连算（E03）', () => {
  assert.match(homeroomScores, /fetchHomeroomTrends/, '行展开须调用趋势端点')
  assert.match(homeroomScores, /academic_year_name/, '趋势必须按学年名分节')
  assert.match(homeroomScores, /跨学年成绩分段展示/, '须声明跨学年不直接比较')
})

test('班主任成绩页恢复旧版重点关注名单', () => {
  assert.match(homeroomScores, /fetchHomeroomFocus/, '须读取重点关注端点')
  assert.match(homeroomScores, /重点关注名单/, '须展示重点关注名单')
  assert.match(homeroomScores, /临界段、薄弱段/, '须说明名次段口径')
  assert.match(homeroomScores, /严重偏科/, '须展示单科百分位偏科标注')
  assert.match(homeroomScores, /明显进退步、波动/, '须展示旧版进退步与波动维度')
  assert.match(homeroomScores, /稳定优秀/, '须保留正向关注维度')
  assert.match(homeroomScores, /排名极差/, '须展示趋势标注的计算依据')
  assert.match(homeroomScores, /学籍排名/, '须展示旧版学籍排名')
})

test('班主任考试详情恢复旧版六板块与顶部排名分段图', () => {
  for (const label of [
    '班级均分表',
    '学生成绩明细表',
    '名次段分布',
    '排名频次统计',
    '排名区间筛选',
    '重点关注',
  ]) {
    assert.match(homeroomScores, new RegExp(label), `应恢复${label}`)
  }
  assert.match(homeroomScores, /<BarChart/, '顶部应使用柱状图展示排名分段')
  assert.match(homeroomScores, /rankDistribution\.series\.map/, '柱状图应按后端返回的总分口径生成图例')
  assert.match(homeroomScores, /高一展示主三门、五门、九门/, '高一须展示三个总分口径')
  assert.match(homeroomScores, /高二、高三展示主三门与 3\+3/, '高二高三须展示两个总分口径')
  assert.match(homeroomScores, /frequencyExamNames/, '排名频次须支持多选考试')
  // 2026-10-23 排名频次表头冻结：限高滚动容器在本层、内层 Table 包装 overflow-visible（sticky 才能绑到外层）、表头格 sticky 吸顶
  assert.match(
    homeroomScores,
    /2026-10-23 表头冻结[\s\S]*?<Table wrapperClassName="overflow-visible">[\s\S]*?sticky top-0 z-20 whitespace-nowrap">学生/,
    '排名频次表头须冻结吸顶（限高滚动 + overflow-visible 包装 + sticky 表头）',
  )
  assert.match(homeroomScores, /rangeMin/, '排名区间须支持起止名次')
  assert.match(apiV1, /fetchHomeroomClassAverages/, '班级均分表须读取专用端点')
  assert.match(homeroomScores, /全年级班级均分表/, '须展示全年级各班均分宽表')
  assert.match(homeroomScores, /total_ranks/, '须展示各总分口径班级排名')
  assert.match(homeroomScores, /平行班|groupName/, '须按班级类型分组')
  assert.match(homeroomScores, /平均.*最高.*最低/s, '每个班型须有平均最高最低汇总')
  // 2026-10-19 表头按学校导出重制：选科 原始分/等级分 子列、总分指标包、班型合并单元格
  assert.match(homeroomScores, /subjectColumnGroups/, '学科列须按 原始/等级 分组')
  assert.match(homeroomScores, /rowSpan=\{typeSpans\[idx\]\}/, '班级类型列须同值合并单元格')
  assert.match(homeroomScores, /班型排名/, '排名列须标注班型内口径')
  // 2026-10-22 拍板：总分没有及格率/高分率/低分率/标准差指标（导入数据恒为空），四列不再展示
  assert.doesNotMatch(homeroomScores, /及格率|高分率|低分率|标准差/, '总分区块只留 均分+班型排名，率值/标准差列不得残留')
  // 明细表表头按图2：学号/班级/学籍/姓名 + 校名次/班名次
  assert.match(homeroomScores, /学籍/, '明细表须有学籍列')
  assert.match(homeroomScores, /班名次/, '明细表须有班名次（本班口径前端计算）')
  assert.match(homeroomScores, /classRanksByTotal/, '班名次须由前端按本班总分计算')
  // 2026-10-28：学科分数旁加年级百分位（高一 9 科 + 高二/三语数英；percentiles 单科键接口本就下发）
  assert.match(homeroomScores, /s\.percentiles\?\.\[g\.subject\]/, '学科百分位取 percentiles 单科键')
  assert.match(uploadPage, /学生成绩明细表.*班级均分表.*名次段表/, '上传页须明确支持同场三类 Excel')
})

test('教学统计保留小样本提示，重复均分对比已收敛到班级对比页', () => {
  assert.match(homeroomScores, /样本&lt;5/)
  assert.match(teachingScores, /样本&lt;5/)
  assert.doesNotMatch(teachingScores, /fetchTeachingClassCompare|ClassComparePanel/)
})

test('上传页工作台感知 + revise 修订重试 + token 作废（契约 §1）', () => {
  assert.match(uploadPage, /useWorkspace\(\)/, '上传页必须读工作台 mode')
  assert.match(uploadPage, /importsPreviewMultipart/, '预览须走 multipart 端点')
  assert.match(uploadPage, /importsConfirmP3/, '确认须走 P3 confirm 端点')
  assert.match(uploadPage, /form\.set\('mode', mode\)/, 'form 必须显式携带当前工作台 mode')
  assert.match(uploadPage, /全科\+总分入班主任域/, '班主任域说明文案')
  assert.match(uploadPage, /其他学科列将被过滤/, '教学域说明文案')
  assert.match(uploadPage, /readImportConflicts/, '409 须解析冲突列表')
  assert.match(uploadPage, /\[mode, filesKey\]/, 'mode 切换或文件变化必须作废预览 token')
  assert.match(uploadPage, /void handleConfirm\(true\)/, '冲突面板须以 revise=true 重试')
  assert.match(uploadPage, /reqRef\.current/, '上传页须有世代号防迟到响应')
  assert.match(previewTable, /warnings/, '预览表必须展示警告明细')
  assert.match(previewTable, /new_students/, '预览表必须展示新学生明细')
  assert.match(conflictsPanel, /以本次上传为准（修订）/, '冲突面板须有修订按钮')
  assert.match(conflictsPanel, /库内值/, '冲突表必须展示库内值')
})

test('迟到响应按资源分离序号丢弃，失败进错误态不白屏（F11）', () => {
  // 共用单一序号时，切考试后同轮 bands/trends effect 的递增会把 stats/students 的
  // 合法回包当过期丢弃（骨架永久加载）；必须考试清单/数据/段位/趋势各自比对各自序号
  for (const [src, name] of [
    [homeroomScores, '班主任成绩页'],
    [teachingScores, '教学成绩页'],
  ]) {
    assert.match(src, /examsReqRef\.current/, `${name}考试清单须有独立请求序号`)
    assert.match(src, /dataReqRef\.current/, `${name}stats/students 须有独立请求序号`)
    assert.match(src, /if \(req !== dataReqRef\.current\) return/, `${name}迟到回包必须丢弃`)
    assert.match(src, /apiErrorMessage/, `${name}错误须经统一文案映射`)
  }
  assert.match(homeroomScores, /bandsReqRef\.current/, '名次段分布须有独立请求序号')
  assert.match(homeroomScores, /trendsReqRef\.current/, '趋势须有独立请求序号')
  // 段位 effect 递增的必须是段位自己的序号（F11 根因回归；2026-10-19 改读名次段表）
  const bandsCallIdx = homeroomScores.indexOf('fetchHomeroomRankBands(')
  assert.ok(bandsCallIdx > 0, '应能定位名次段分布请求调用')
  const bandsCtx = homeroomScores.slice(Math.max(0, bandsCallIdx - 400), bandsCallIdx)
  assert.match(bandsCtx, /\+\+bandsReqRef\.current/, '名次段分布 effect 必须递增 bandsReqRef')
  assert.doesNotMatch(bandsCtx, /dataReqRef\.current/, '名次段分布 effect 不得触碰 stats/students 序号')
})

test('导航入口与宽表横向滚动（U01）', () => {
  assert.match(sidebar, /\/homeroom\/scores/, '侧栏须有班主任成绩入口')
  assert.match(sidebar, /\/teaching\/scores/, '侧栏须有教学成绩入口')
  assert.match(homeroomScores, /overflow-x-auto/, '班主任成绩宽表须横向滚动')
  assert.match(teachingScores, /overflow-x-auto/, '教学成绩宽表须横向滚动')
  assert.match(previewTable, /overflow-x-auto/, '预览表须横向滚动')
  assert.match(conflictsPanel, /overflow-x-auto/, '冲突表须横向滚动')
})

test('预览表列宽固定：考试归属下拉不溢出卡片、警告列不竖排', () => {
  // auto 布局下 6 个 nowrap 列 + 「考试」列里 w-full 原生 select 的固有宽度，
  // 会把 select 顶出天空卡片右缘盖住「学科 / 班级」列，并把唯一可换行的
  // 「解析与警告」列挤成一个字宽（文字竖排）；必须 table-fixed + colgroup 定宽。
  assert.match(previewTable, /<Table className="table-fixed min-w-\[960px\]">/, '预览表须 table-fixed 并带最小宽度')
  const colMatches = previewTable.match(/<col className="w-\[\d+%\]" \/>/g) ?? []
  assert.equal(colMatches.length, 7, `colgroup 须固定 7 列宽度（实际 ${colMatches.length}）`)
  assert.match(previewTable, /<TableCell className="text-xs text-slate-600">\s*<span className="whitespace-nowrap">\{examCell/, '考试列单元格不得整体 nowrap（卡片内文案须可换行），考试名单行由 span 保证')
  assert.match(previewTable, /flex flex-wrap items-center gap-1\.5/, '解析与警告徽章行须允许换行')
})

test('排名区间筛选按指标类型自适应（2026-10-23 拍板）', () => {
  // 区间单位随指标 kind：总分=名次、语数英/高一全科=年级前%（0–100）、
  // 加三学科=等级分（40–70）；跨类型切换须重置区间默认值，末列随 kind 换表头与取值。
  assert.match(homeroomScores, /const RANGE_KIND_BOUNDS = \{/, '应有指标类型的区间单位配置')
  for (const label of ['起始名次', '起始百分位（前%）', '起始等级分']) {
    assert.match(homeroomScores, new RegExp(label), `应有「${label}」输入标签`)
  }
  assert.match(homeroomScores, /defMin: 0, defMax: 100/, '百分位默认区间 0–100')
  assert.match(homeroomScores, /defMin: 40, defMax: 70/, '等级分默认区间 40–70')
  // 跨类型切换重置：onValueChange 里对比新旧 kind 并写默认区间
  assert.match(homeroomScores, /nextKind !== rangeKind/, '切换指标跨类型须检测 kind 变化')
  assert.match(homeroomScores, /setRangeMin\(next\.defMin\)/, '跨类型切换须重置区间下限')
  assert.match(homeroomScores, /setRangeMax\(next\.defMax\)/, '跨类型切换须重置区间上限')
  // 末列自适应：百分位显示 metric_value 带 %、等级分显示 metric_value、总分显示 year_rank
  assert.match(homeroomScores, /rangeKind === 'subject_percentile' \? '年级前%' : rangeKind === 'subject_grade_score' \? '等级分' : '年级名次'/, '末列表头须随指标类型切换')
  assert.match(homeroomScores, /`\$\{student\.metric_value\}%`/, '百分位取值须显示为「前X%」')
  assert.match(homeroomScores, /\? \(student\.metric_value \?\? '—'\)/, '等级分取值须读 metric_value')
  // api 类型：students[] 须含 metric_value（单位随 metric_kind）
  assert.match(apiV1, /metric_value\?: number \| null/, 'HomeroomRankRangeResponse.students 须含 metric_value')
  // 三口径说明进卡片描述
  assert.match(homeroomScores, /按指标类型筛选/, '卡片描述须说明按指标类型筛选')
})
