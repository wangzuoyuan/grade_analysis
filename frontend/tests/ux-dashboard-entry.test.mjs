import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from 'typescript'

const dashboard = readFileSync(new URL('../src/components/dashboard/WorkspaceDataDashboard.tsx', import.meta.url), 'utf8')
const homeroom = readFileSync(new URL('../src/app/homeroom/overview.tsx', import.meta.url), 'utf8')
const teaching = readFileSync(new URL('../src/app/teaching/overview.tsx', import.meta.url), 'utf8')
const entry = readFileSync(new URL('../src/components/homework/HomeworkEntryPanel.tsx', import.meta.url), 'utf8')
const workspace = readFileSync(new URL('../src/components/homework/HomeworkWorkspace.tsx', import.meta.url), 'utf8')

const vocabSrc = readFileSync(new URL('../src/components/homework/homework-vocab.ts', import.meta.url), 'utf8')

function compileTs(src, jsx) {
  return ts.transpileModule(src, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: jsx ? ts.JsxEmit.ReactJSX : undefined,
      target: ts.ScriptTarget.ES2020,
    },
  }).outputText
}

function loadSmartParser() {
  // 共享词汇表先行编译，再作为 require 依赖注入面板沙箱
  const vocabModule = { exports: {} }
  vm.runInNewContext(compileTs(vocabSrc, false), {
    module: vocabModule, exports: vocabModule.exports, require: () => ({}), Date, console,
    setTimeout, clearTimeout,
  })
  const module = { exports: {} }
  vm.runInNewContext(compileTs(entry, true), {
    module, exports: module.exports,
    require: (id) => (String(id).includes('homework-vocab') ? vocabModule.exports : ({})),
    Date, console, setTimeout, clearTimeout,
  })
  return module.exports.parseSmartHomeworkText
}

test('两个工作台首页共用真实数据看板，不再渲染配置总览', () => {
  assert.match(homeroom, /WorkspaceDataDashboard mode="homeroom"/)
  assert.match(teaching, /WorkspaceDataDashboard mode="teaching"/)
  for (const fn of ['listExams', 'fetchHomeroomStats', 'fetchHomeroomFocus', 'fetchTeachingStats', 'fetchTeachingClassCompare', 'homeworkDashboard', 'homeworkWarnings']) {
    assert.match(dashboard, new RegExp(fn), `看板须使用 ${fn} 真实端点`)
  }
  assert.match(dashboard, /成绩趋势/)
  assert.match(dashboard, /需关注学生/)
  assert.match(dashboard, /student\.issues\.join\('、'\)/, '班主任首页须展示旧版临界、薄弱和偏科原因')
  assert.match(dashboard, /进退步、波动、名次段、偏科与稳定优秀/, '首页须概括完整重点关注维度')
  assert.match(dashboard, /作业预警/)
  assert.match(dashboard, /成绩为空不影响下方作业与缺交历史展示/)
  assert.match(dashboard, /const \[examResult, hw, warning\] = await Promise\.all/, '考试空态前须独立加载作业数据')
  assert.match(dashboard, /min_streak: 2/, '首页预警须筛选真实连续缺交，而不是累计缺交名单')
  assert.match(dashboard, /缺交、负面评价与忘带汇总/, '首页重点关注须覆盖三个独立预警维度')
  assert.match(dashboard, /tab=warnings/, '重点关注须可跳转预警明细')
  assert.match(dashboard, /homeworkCurrentSemester/, '两个看板须读取全局当前学期')
  assert.match(dashboard, /examIsInSemester/, '考试必须限定在当前学期日期内')
  assert.doesNotMatch(dashboard, /ScoreYearPicker/, '仪表盘不得再显示学年选择器')
  assert.doesNotMatch(dashboard, /cohort_size ·|active 状态的 HomeroomTeachingLink/)
})

test('作业录入默认为智能文本，全员已交零例外可预览', () => {
  assert.match(entry, /export function parseSmartHomeworkText/)
  assert.match(entry, /校本作业：全交/)
  assert.match(entry, /全员已交/)
  assert.match(entry, /kind: 'full', exceptions: rows/)
  assert.match(entry, /assignedDate: todayISO\(\)/, '布置日期须默认今天')
  assert.match(entry, /smartResult\?\.input \?\? buildInput\(form\)/, '智能识别后仍走同一 preview 契约')
  assert.match(entry, /<details/, '结构化编辑只作为备用入口')
})

test('智能录入按旧教学用语解析姓名、作业、评价、出勤和全交', () => {
  const parse = loadSmartParser()
  const one = parse('张三校本优秀')
  assert.equal(one.homeworkType, '校本作业')
  assert.equal(JSON.stringify(one.input), JSON.stringify({
    kind: 'detailed', rows: [{ name_or_alias: '张三', status: 'submitted', evaluation: '优秀' }],
  }))

  const quality = parse('校本差：吴六、赵七')
  assert.equal(quality.homeworkType, '校本作业')
  assert.deepEqual(Array.from(quality.input.rows, (row) => `${row.name_or_alias}:${row.status}:${row.evaluation}`), [
    '吴六:submitted:差', '赵七:submitted:差',
  ])

  const missing = parse('订正缺交：李四、王五')
  assert.equal(missing.homeworkType, '试卷订正')
  assert.deepEqual(Array.from(missing.input.rows, (row) => `${row.name_or_alias}:${row.status}`), [
    '李四:missing', '王五:missing',
  ])

  const attendance = parse('王五没来')
  assert.equal(attendance.input.rows[0].attendance, '没来')
  assert.equal(attendance.input.rows[0].status, 'missing')

  const full = parse('周末作业：全交')
  assert.equal(full.homeworkType, '周末作业')
  assert.equal(JSON.stringify(full.input), JSON.stringify({ kind: 'full', exceptions: [] }))

  const forgot = parse('张三忘带作业本')
  assert.equal(forgot.input.rows[0].status, 'missing')
  assert.equal(forgot.input.rows[0].evaluation, '忘带作业本')

  const legacyNegative = parse('李四作业乱')
  assert.equal(legacyNegative.input.rows[0].status, 'submitted')
  assert.equal(legacyNegative.input.rows[0].evaluation, '作业乱')

  // 教学工作台冒号多学生出勤：迟到：秦五、宋彦瞳
  const multiAtt = parse('迟到：秦五、宋彦瞳')
  assert.equal(multiAtt.input.rows.length, 2)
  assert.equal(multiAtt.input.rows[0].name_or_alias, '秦五')
  assert.equal(multiAtt.input.rows[0].status, 'missing', '迟到绝不能计入已交')
  assert.equal(multiAtt.input.rows[0].attendance, '迟到')
  assert.equal(multiAtt.input.rows[1].name_or_alias, '宋彦瞳')
  assert.equal(multiAtt.input.rows[1].status, 'missing', '迟到绝不能计入已交')
  assert.equal(multiAtt.input.rows[1].attendance, '迟到')

  // 学生在前冒号出勤：秦五：迟到、宋彦瞳：没来
  const studentFirstAtt = parse('秦五：迟到\n宋彦瞳：没来')
  assert.equal(studentFirstAtt.input.rows.length, 2)
  assert.equal(studentFirstAtt.input.rows[0].name_or_alias, '秦五')
  assert.equal(studentFirstAtt.input.rows[0].status, 'missing')
  assert.equal(studentFirstAtt.input.rows[0].attendance, '迟到')
  assert.equal(studentFirstAtt.input.rows[1].name_or_alias, '宋彦瞳')
  assert.equal(studentFirstAtt.input.rows[1].status, 'missing')
  assert.equal(studentFirstAtt.input.rows[1].attendance, '没来')

  // 无冒号状态在前多人：迟到 秦五 宋彦瞳
  const noColonAtt = parse('迟到 秦五 宋彦瞳')
  assert.equal(noColonAtt.input.rows.length, 2)
  assert.equal(noColonAtt.input.rows[0].name_or_alias, '秦五')
  assert.equal(noColonAtt.input.rows[0].status, 'missing')
  assert.equal(noColonAtt.input.rows[0].attendance, '迟到')
  assert.equal(noColonAtt.input.rows[1].name_or_alias, '宋彦瞳')
  assert.equal(noColonAtt.input.rows[1].status, 'missing')
  assert.equal(noColonAtt.input.rows[1].attendance, '迟到')

  // 完整真实用例：作业种类 + 全交 + 迟到例外 + 缺交例外
  const fullWithAtt = parse('电阻率测量：全交\n迟到：秦五、宋彦瞳\n缺交：秦二十六、秦六')
  assert.equal(fullWithAtt.homeworkType, '电阻率测量')
  assert.equal(fullWithAtt.input.kind, 'full')
  assert.equal(fullWithAtt.input.exceptions.length, 4)
  const excMap = Object.fromEntries(fullWithAtt.input.exceptions.map((r) => [r.name_or_alias, r]))
  assert.equal(excMap['秦五'].status, 'missing', '全交下的迟到例外绝不能是已交')
  assert.equal(excMap['秦五'].attendance, '迟到')
  assert.equal(excMap['宋彦瞳'].status, 'missing', '全交下的没来/迟到例外绝不能是已交')
  assert.equal(excMap['宋彦瞳'].attendance, '迟到')
  assert.equal(excMap['秦二十六'].status, 'missing')
  assert.equal(excMap['秦六'].status, 'missing')
})

test('教学作业录入要求显式教学班，不往全部所教班并集写入', () => {
  assert.match(workspace, /<ClassScopePicker/)
  assert.match(workspace, /录入前请选择具体班级/)
  assert.match(entry, /hasTeachingClass/)
  assert.match(entry, /请先在页面上方选择一个具体教学班/)
})

test('批次列表单列出勤异常和负面评价，并隐藏应交、提交率和版本', () => {
  const table = readFileSync(new URL('../src/components/homework/AssignmentTable.tsx', import.meta.url), 'utf8')
  assert.match(table, />出勤异常</)
  assert.match(table, />负面评价</)
  assert.match(table, /item\.attendance_count/)
  assert.match(table, /item\.negative_count/)
  assert.doesNotMatch(table, /<TableHead[^>]*>应交</)
  assert.doesNotMatch(table, /<TableHead[^>]*>提交率</)
  assert.doesNotMatch(table, /<TableHead[^>]*>版本</)
})

test('批次列表数字支持独立点击筛选对应人员，展开明细提供人员筛选控制栏', () => {
  const table = readFileSync(new URL('../src/components/homework/AssignmentTable.tsx', import.meta.url), 'utf8')
  assert.match(table, /function NumberFilterCell/, '数字单元格必须组件化（主行与平铺共用一个实现）')
  assert.match(table, /filter="missing"/, '缺交数字必须支持独立点击筛选')
  assert.match(table, /filter="excused"/, '请假数字必须支持独立点击筛选')
  assert.match(table, /filter="submitted"/, '已交数字必须支持独立点击筛选')
  assert.match(table, /人员筛选：/, '展开明细必须提供人员筛选切换选项')
  assert.match(table, /filteredSubmissions/, '明细表格必须按筛选条件过滤呈现')
})

test('按期汇总表格展示负面评价列', () => {
  const table = readFileSync(new URL('../src/components/homework/AssignmentTable.tsx', import.meta.url), 'utf8')
  assert.match(table, /<CardTitle>按期汇总<\/CardTitle>[\s\S]*<TableHead[^>]*>负面评价<\/TableHead>/, '按期汇总表头须包含负面评价')
  assert.match(table, /g\.negative_count/, '按期汇总数据行须渲染 negative_count 字段')
})

test('班主任工作台作业记录支持按日合并展示多学科，并提供全科透视与视图切换', () => {
  const table = readFileSync(new URL('../src/components/homework/AssignmentTable.tsx', import.meta.url), 'utf8')
  assert.match(table, /viewMode === 'by_date'/, '班主任作业记录须支持按日合并模式')
  assert.match(table, /DayGroupOverview/, '按日合并须提供全科透视组件')
  assert.match(table, /全科透视/, '展开明细须包含全科透视标签')
  assert.match(table, /按日合并/, '界面须提供按日合并切换控制')
  assert.match(table, /单科平铺/, '界面须提供单科平铺切换控制')
  assert.match(table, /formatDayOfWeek/, '合并模式须计算星期')
  assert.match(table, /studentIssues/, '全科透视须聚合异常学生')
})

test('作业记录编辑按钮唤起批次编辑弹窗，而非折叠展开', () => {
  const table = readFileSync(new URL('../src/components/homework/AssignmentTable.tsx', import.meta.url), 'utf8')
  assert.match(table, /EditAssignmentModal/, '必须包含批次编辑弹窗组件')
  assert.match(table, /编辑作业批次/, '弹窗标题必须为编辑作业批次')
  assert.match(table, /aria-label="编辑批次信息"/, '铅笔按钮必须为编辑批次信息')
  assert.match(table, /onClick=\{onEdit\}/, '平铺列表铅笔按钮必须触发 onEdit 唤起弹窗')
  assert.match(table, /修改批次信息/, '明细展开区标题须提供修改批次信息按钮')
})

test('班主任工作台仪表盘包含本周关注卡片、年级名次范围与作业预警卡片跳转', () => {
  assert.match(dashboard, /<WeeklyFocusCard/, '仪表盘必须引入并渲染 WeeklyFocusCard')
  assert.match(dashboard, /label="年级名次范围"/, '班主任模式下 KPI 卡片须将班级人数改为年级名次范围')
  assert.match(dashboard, /homeroomTotal\?\.rank_min/, '名次范围须读取 rank_min 与 rank_max')
  assert.match(dashboard, /href=\{\`\/\$\{mode\}\/homework\?tab=warnings\`\}/, '作业预警卡片须支持点击跳转到预警页')

  const weeklyCard = readFileSync(new URL('../src/components/WeeklyFocusCard.tsx', import.meta.url), 'utf8')
  assert.match(weeklyCard, /fetchHomeroomWeeklyFocus/, '本周关注卡片须调用 fetchHomeroomWeeklyFocus 端点')
  assert.match(weeklyCard, /本周关注/, '必须包含本周关注标题')
  assert.match(weeklyCard, /\/homeroom\/profile\?person_id=/, '学生姓名须以 person_id 可点击直达学生档案')
})

test('作业缺交卡片支持点击学科胶囊进行筛选与联动', () => {
  const hwCard = readFileSync(new URL('../src/components/HomeworkCard.tsx', import.meta.url), 'utf8')
  assert.match(hwCard, /selectedCategory/, '作业缺交卡须维护 selectedCategory 筛选状态')
  assert.match(hwCard, /setSelectedCategory/, '学科胶囊须支持点击切换筛选')
  assert.match(hwCard, /清除筛选/, '筛选激活时须提供清除筛选功能')
  assert.match(hwCard, /已筛选【/, '明细摘要须显示当前选中的学科标签')
})

test('作业记录与全科透视对「考勤」出勤批次专属语义化胶囊呈现（不套用缺交学科模版）', () => {
  const table = readFileSync(new URL('../src/components/homework/AssignmentTable.tsx', import.meta.url), 'utf8')
  assert.match(table, /a\.subject === '考勤'/, '必须显式识别考勤出勤批次')
  assert.match(table, /📋 出勤/, '胶囊必须以专属「📋 出勤」标识呈现而非普通学科')
  assert.match(table, /出勤异常 \$\{abnormalCount\}/, '出勤异常统一显示「出勤异常 N」，不误写为迟到')
  assert.match(table, /attendance_ids\?\.length/, '出勤异常计数须以例外学生 ID 去重为准')
  assert.match(table, /全勤/, '全员到齐须显示全勤而非全齐')
  assert.match(table, /📋 出勤登记/, '全科透视网格与切换标签须显示为出勤登记')
  assert.match(table, /for \(const id of a\.missing_ids \?\? \[\]\)/, '全科透视异常归属必须来自列表自带例外 ID，不再逐批次拉明细')
  assert.match(table, /if \(!isAtt\) \{[\s\S]*?submittedCount\+\+/, '考勤批次绝不计入作业已交科数')
})

test('按日合并主行数字点击精准呈现筛选信息，仅最右侧箭头展开全景透视下拉菜单', () => {
  const table = readFileSync(new URL('../src/components/homework/AssignmentTable.tsx', import.meta.url), 'utf8')
  // 1. toggleDayGroup 精准区分数字筛选与全科透视收起/展开
  assert.match(table, /preferredFilter !== 'all'[\s\S]*activeFilter === preferredFilter/, '同一筛选数字再次点击须支持收起')
  assert.match(table, /preferredFilter === 'all'[\s\S]*activeFilter === 'all'[\s\S]*activeDaySubject === 'overview'/, '全景下拉展开时点箭头须支持收起')

  // 2. DayGroupOverview 接收 activeFilter 与 onFilterChange
  assert.match(table, /activeFilter\?: HomeworkDetailFilter/, 'DayGroupOverview 须接收 activeFilter 参数')
  assert.match(table, /onFilterChange\?: \(filter: HomeworkDetailFilter\) => void/, 'DayGroupOverview 须接收 onFilterChange 回调')

  // 3. 只有 activeFilter === 'all' 时才渲染顶部各学科概况卡片（重构后更紧凑，一行约 6 张）
  assert.match(table, /activeFilter === 'all' \? \(\s*<div className="grid grid-cols-3/, '仅在 activeFilter 为 all 时渲染学科概况卡片')

  // 4. 具体分类直接显示经过筛选的信息
  assert.match(table, /当日请假免交学生/, '筛选请假时须展示当日请假免交学生专属标题')
  assert.match(table, /当日作业负面评价学生/, '筛选负面评价时须展示当日作业负面评价学生专属标题')
  assert.match(table, /当日出勤异常学生/, '筛选出勤异常时须展示当日出勤异常学生专属标题')
  assert.match(table, /当日作业缺交学生/, '筛选缺交时须展示当日作业缺交学生专属标题')
  assert.match(table, /当日正常已交学生/, '筛选已交时须展示当日正常已交学生专属标题')

  // 5. 最右侧操作箭头文案
  assert.match(table, /展开全科透视下拉菜单/, '最右侧操作箭头须明确提示展开全科透视下拉菜单')
})

test('按日合并排除考勤计入作业缺交，且全面移除「科次」采用去重学生人数统计', () => {
  const table = readFileSync(new URL('../src/components/homework/AssignmentTable.tsx', import.meta.url), 'utf8')
  // 1. 考勤批次单独计入出勤异常，不计入 totalMissing 与 totalSubmitted
  assert.match(table, /if \(a\.subject !== '考勤'\)\s*\{[\s\S]*?rawMissingSum \+= a\.missing/, '考勤批次必须排除计入学科作业缺交')
  assert.match(table, /if \(a\.subject !== '考勤'\)\s*\{[\s\S]*?totalSubmitted \+= a\.submitted/, '考勤批次必须排除计入学科作业已交')

  // 2. 按日合并主行使用 Set 对学生 ID 去重统计人数（彻底避免1人记成6人）
  assert.match(table, /excusedSet\.add\(id\)/, '必须使用 Set 收集当日各学科请假学生 ID 消除重复')
  assert.match(table, /const totalExcused = hasIdInfo \? excusedSet\.size : rawExcusedSum/, 'totalExcused 必须按去重人数统计')

  // 3. 界面中彻底不出现「(科次)」与「人次」
  assert.doesNotMatch(table, / \(科次\)/, '界面所有表头与文本绝不包含 (科次)')
  assert.doesNotMatch(table, / 人次/, '展开区标题绝不出现 人次')

  // 4. DayGroupOverview 人员筛选按钮使用简洁纯人数显示
  assert.match(table, /function FilterChipBar/, '人员筛选胶囊必须组件化（明细与透视共用一个实现）')
  assert.match(table, /label: '请假'/, '筛选胶囊必须包含请假（纯人数）')
  assert.match(table, /label: '缺交'/, '筛选胶囊必须包含缺交（纯人数）')

  // 5. 请假统一显示为「请假」，绝不按学科拆分显示，也不显示「请假 X 科」
  assert.doesNotMatch(table, /请假:\s*\{ex\.subject\}/, '请假胶囊绝不得按学科拆分显示')
  assert.doesNotMatch(table, /请假\s*\{s\.excused\.length\}\s*科/, '绝不得显示请假X科')
  assert.match(table, /<span>请假<\/span>/, '请假胶囊统一显示为简洁请假标签（不带学科与备注）')

  // 6. 考勤绝不推入作业已交，全齐基于实际学科作业排除考勤
  assert.doesNotMatch(table, /item\.submitted\.push\(\{\s*subject:\s*isAttBatch/, '考勤批次绝不得推入已交列表')
  assert.match(table, /s\.submittedCount === homeworkAssignments\.length/, '已交全齐判断必须基于学科作业门数排除考勤')
})



test('口径对齐：缺交标份数、已交视图一人一条汇总胶囊', () => {
  const table = readFileSync(new URL('../src/components/homework/AssignmentTable.tsx', import.meta.url), 'utf8')
  // 缺交主行数字是方案A的份数累加：hover 与透视 Tab 措辞必须写「份」，不得误写「人」
  assert.match(table, /共 \$\{group\.totalMissing\} 份/, '缺交 hover 必须标明份数而非人数')
  // 学科页签行已删除（2026-09 重构），「📌 全科透视」页签及份数措辞随之移除
  assert.doesNotMatch(table, /📌 全科透视/, '全科透视改为小标题，不再有带份数的学科页签')
  // 已交筛选视图一人一条汇总，不再逐科铺「已交: 学科」胶囊
  assert.doesNotMatch(table, /已交: \$\{sub\.subject\}/, '已交视图不得逐科铺「已交: 学科」胶囊刷屏')
  assert.match(table, /已交 \{s\.submittedCount\}\/\{homeworkAssignments\.length\} 科/, '未全齐学生显示「已交 N/M 科」汇总胶囊')
  assert.match(table, /全齐（当日 \{homeworkAssignments\.length\} 科作业）/, '全齐学生显示单条汇总胶囊')
})

test('同科多批次显示具体作业名区分（英语 · 练习册 / 英语 · 听力）', () => {
  const table = readFileSync(new URL('../src/components/homework/AssignmentTable.tsx', import.meta.url), 'utf8')
  assert.match(table, /function subjectWithContent/, '须提供学科+作业名组合标签助手')
  // 主行胶囊、胶囊title、透视卡片、透视学生胶囊、明细标题全部接入（页签行删除后少一个接入点）
  const uses = (table.match(/subjectWithContent\(/g) || []).length
  assert.ok(uses >= 6, `学科标签组合助手须覆盖主行胶囊/title/卡片/聚合胶囊/明细标题（当前 ${uses} 处）`)
  const warningsPanel = readFileSync(new URL('../src/components/homework/WarningsPanel.tsx', import.meta.url), 'utf8')
  assert.doesNotMatch(warningsPanel, /\{teaching &&/, '预警时间轴的作业名标注不得再限定教学工作台')
})

test('作业跟进重构：展开区无学科页签，透视卡片直达缺交并带编辑/撤销入口', () => {
  const table = readFileSync(new URL('../src/components/homework/AssignmentTable.tsx', import.meta.url), 'utf8')
  const apiV1 = readFileSync(new URL('../src/lib/api-v1.ts', import.meta.url), 'utf8')

  // 1. 展开区学科页签行删除：全科透视只留小标题 + 灰色操作提示
  assert.doesNotMatch(table, /📌 全科透视/, '展开区学科页签行必须删除')
  assert.match(table, /点学科卡片进单科明细；点学生标签直达对应学科/, '全科透视小标题旁须有操作提示')

  // 2. 学科卡片整卡可点：进入该批次单科明细并默认筛选缺交
  assert.match(table, /onSelectSubject\(a\.assignment_id, 'missing'\)/, '学科卡片点击须直达该科明细并默认缺交筛选')

  // 3. 卡片右上角 ✏️/🗑 小图标：复用既有编辑弹窗与撤销流程，stopPropagation 防触发整卡点击
  assert.match(table, /编辑批次（改日期\/作业名\/学科）/, '卡片 ✏️ 须提示打开编辑批次弹窗')
  assert.match(table, /title="撤销批次"/, '卡片 🗑 须提示撤销批次')
  assert.match(table, /e\.stopPropagation\(\)\s*\n\s*onEdit\?\.\(a\)/, '编辑图标须阻止冒泡')
  assert.match(table, /e\.stopPropagation\(\)\s*\n\s*onRevoke\?\.\(a\)/, '撤销图标须阻止冒泡')

  // 4. 单科明细头部「← 返回全科透视」：仅按日合并且当日多批次时提供
  assert.match(table, /onBackToOverview\?: \(\) => void/, '明细视图须声明可选返回回调')
  assert.match(table, /&larr; 返回全科透视/, '明细头部须渲染返回全科透视链接')
  assert.match(table, /group\.assignments\.length > 1 \? \(\) => onSelectTab\('overview'\) : undefined/, '单批次日不提供返回链接（无透视可回）')

  // 5. 筛选胶囊三处变化：忘带类别、0 值隐藏、吸顶 + 只看提示
  assert.match(table, /filter: 'forgot', label: '忘带'/, '胶囊栏须在缺交后新增忘带类别')
  assert.match(table, /count === 0 && activeFilter !== c\.filter\) return null/, '0 人类别胶囊不渲染（当前选中的除外）')
  assert.ok((table.match(/sticky top-14 z-10/g) || []).length >= 2, '明细头部与全科透视筛选行均须吸顶')
  assert.match(table, /🔍 当前只看：/, '非 all 筛选时吸顶行右侧须显示只看提示')

  // 6. 忘带数据链路：后端列表项 forgot_ids → 透视逐学生忘带标签（点击直达该科缺交）
  assert.match(table, /for \(const id of a\.forgot_ids \?\? \[\]\)/, '全科透视须从列表项 forgot_ids 聚合忘带学生')
  assert.match(table, /忘带: \{f\.subject\}/, '学生须渲染琥珀色「忘带: 学科」标签')
  assert.match(apiV1, /forgot_ids\?: number\[\] \| null/, '列表项类型须补 forgot_ids 字段')
})

test('考勤胶囊与出勤登记卡片区分请假：有人请假不再误标「全勤」', () => {
  const table = readFileSync(new URL('../src/components/homework/AssignmentTable.tsx', import.meta.url), 'utf8')

  // 0. 请假人数与出勤异常同源：主行胶囊与透视卡片两处均以 excused_ids 去重为准
  const excusedDefs = table.match(/const excusedCount = isAtt \? \(a\.excused_ids\?\.length \?\? a\.excused\) : 0/g) || []
  assert.ok(excusedDefs.length >= 2, `按日合并主行与全科透视卡片均须单独计算考勤批次的请假人数（当前 ${excusedDefs.length} 处）`)

  // 1. 主行胶囊四态：仅请假时蓝色 pill（与筛选请假色系一致）且后缀为「请假 N」，绝不显示「全勤」
  assert.match(table, /hasExcused\s*\?\s*'bg-blue-50 text-blue-700 border-blue-200 font-medium hover:bg-blue-100'/, '仅请假考勤胶囊须用蓝色系（bg-blue-50）与筛选请假色一致')
  assert.match(table, /text-\[11px\] font-semibold text-blue-600">请假 \{excusedCount\}<\/span>/, '仅请假考勤胶囊须显示「请假 N」蓝色后缀')

  // 2. 混合态：琥珀 pill 不变，后缀合并为「出勤异常 N · 请假 M」
  assert.match(table, /出勤异常 \$\{abnormalCount\}\$\{hasExcused \? ` · 请假 \$\{excusedCount\}` : ''\}/, '混合态胶囊后缀须为「出勤异常 N · 请假 M」')
  assert.match(table, /出勤异常 \$\{abnormalCount\} · 请假 \$\{excusedCount\}/, '混合态胶囊 title 须同步「出勤异常 N · 请假 M」')

  // 3. 点击直达：excused-only 传 'excused'、混合传 'attendance'、真全勤传 'all'（学科分支不变）
  assert.match(table, /hasAbnormal \? 'attendance' : hasExcused \? 'excused' : 'all'/, '考勤胶囊点击须按 异常→请假→全勤 优先级直达对应筛选')

  // 4. 全科透视「📋 出勤登记」卡片同口径四态：仅请假时蓝色边框，底部小字含蓝色「请假 N」
  assert.match(table, /hasExcused\s*\?\s*'border-blue-200 bg-blue-50\/40 hover:border-blue-300'/, '仅请假出勤卡片须用蓝色边框（border-blue-200 bg-blue-50/40）')
  assert.match(table, /出勤异常 <span className="font-semibold text-amber-600">\{abnormalCount\}<\/span>\s*\{hasExcused \? \(\s*<> · 请假 <span className="font-semibold text-blue-600">\{excusedCount\}<\/span>/, '混合态卡片小字须为「出勤异常 N · 请假 M · 正常出勤」且请假数字用蓝色')
  assert.match(table, /font-semibold text-blue-600">请假 \{excusedCount\}<\/span> · 正常出勤 \{a\.submitted\}/, '仅请假卡片小字须为「请假 N · 正常出勤 M」')

  // 5. 卡片点击行为不变：出勤卡片点击仍统一 missing 筛选（此前有意设计）
  assert.match(table, /onClick=\{\(\) => onSelectSubject\(a\.assignment_id, 'missing'\)\}/, '透视卡片点击仍统一直达缺交筛选，不随请假态改变')
})
