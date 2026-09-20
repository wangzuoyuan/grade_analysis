// 回归：班主任整段多学科录入（ADR-024）+ 排除统计（ADR-023）。
// 解析器走真实源码（ts.transpileModule + vm）执行测试；面板/卡片为源断言。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from 'typescript'

const entryPanel = readFileSync(new URL('../src/components/homework/HomeworkEntryPanel.tsx', import.meta.url), 'utf8')
const workspace = readFileSync(new URL('../src/components/homework/HomeworkWorkspace.tsx', import.meta.url), 'utf8')
const exclusionCard = readFileSync(new URL('../src/components/homework/StatsExclusionCard.tsx', import.meta.url), 'utf8')
const apiV1 = readFileSync(new URL('../src/lib/api-v1.ts', import.meta.url), 'utf8')
const splitSrc = readFileSync(new URL('../src/components/homework/homeroom-smart-input.ts', import.meta.url), 'utf8')

const vocabSrc = readFileSync(new URL('../src/components/homework/homework-vocab.ts', import.meta.url), 'utf8')

function loadSplitter() {
  const compile = (src) =>
    ts.transpileModule(src, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    }).outputText
  // 先编译共享词汇表，再把它作为 require 依赖注入解析器沙箱
  const vocabModule = { exports: {} }
  vm.runInNewContext(compile(vocabSrc), {
    module: vocabModule,
    exports: vocabModule.exports,
    require: () => ({}),
  })
  const module = { exports: {} }
  vm.runInNewContext(compile(splitSrc), {
    module,
    exports: module.exports,
    require: (id) => (String(id).includes('homework-vocab') ? vocabModule.exports : ({})),
  })
  return module.exports.splitHomeroomHomeworkText
}

const USER_INPUT = [
  '物理：秦三，秦二',
  '语文：秦三，秦十七，秦二十九，秦十五，秦十三，秦二',
  '数学：全交',
  '化学：全交',
  '英语：秦三，秦十，秦十三，秦二',
  '生物：秦三',
  '地理：秦十二，秦十，秦二十八，秦十三',
].join('\n')

test('班主任整段粘贴：逐科分组，裸姓名=缺交，全交=无例外', () => {
  const split = loadSplitter()(USER_INPUT)
  assert.equal(split.error, null)
  assert.equal(split.plainLines.length, 0, '全部行都应带学科前缀')
  const bySubject = Object.fromEntries(split.subjectGroups.map((g) => [g.subject, g.input]))
  assert.equal(
    JSON.stringify(split.subjectGroups.map((g) => g.subject)),
    JSON.stringify(['物理', '语文', '数学', '化学', '英语', '生物', '地理']),
  )
  // 物理：两人裸姓名 → detailed 缺交行
  assert.equal(bySubject['物理'].kind, 'detailed')
  assert.equal(JSON.stringify(bySubject['物理'].rows.map((r) => [r.name_or_alias, r.status])),
    JSON.stringify([['秦三', 'missing'], ['秦二', 'missing']]))
  // 数学/化学：全交 → full 零例外
  assert.equal(bySubject['数学'].kind, 'full')
  assert.equal(JSON.stringify(bySubject['数学'].exceptions), '[]')
  assert.equal(bySubject['化学'].kind, 'full')
  // 语文 6 人、英语 4 人、生物 1 人、地理 4 人
  for (const [s, n] of [['语文', 6], ['英语', 4], ['生物', 1], ['地理', 4]]) {
    assert.equal(bySubject[s].rows.length, n, s)
    assert.ok(bySubject[s].rows.every((r) => r.status === 'missing'), `${s} 裸姓名应为缺交`)
  }
})

test('学科行内带状态 token 按状态登记；姓名逐字透传不做模糊匹配', () => {
  const split = loadSplitter()('物理：李四 请假，王五 忘带，赵六 缺交\n地理：钱七 迟到')
  const rows = split.subjectGroups[0].input.rows
  assert.equal(JSON.stringify(rows.map((r) => [r.name_or_alias, r.status])),
    JSON.stringify([['李四', 'excused'], ['王五', 'missing'], ['赵六', 'missing']]))
  assert.ok(rows[1].evaluation && rows[1].evaluation.includes('忘带'))
  const geo = split.subjectGroups[1].input.rows[0]
  assert.equal(geo.attendance, '迟到')
  assert.equal(geo.status, 'missing')
  // 无模糊匹配
  assert.ok(!/fuzzy|相似|levenshtein/i.test(splitSrc))
})

test('无学科前缀的行：纯作业缺交归入手填学科，考勤行（如请假/迟到）独立单列考勤批次', () => {
  // 「李四：请假」为全天考勤 → 独立归入「考勤」批次单列
  // 「张三 缺交」为作业缺交 → 无学科前缀，归入 plainLines 由手填默认学科接收
  const split = loadSplitter()('李四：请假\n张三 缺交')
  assert.equal(split.subjectGroups.length, 1)
  assert.equal(split.subjectGroups[0].subject, '考勤')
  assert.equal(split.subjectGroups[0].input.exceptions[0].name_or_alias, '李四')
  assert.equal(split.subjectGroups[0].input.exceptions[0].status, 'excused')
  assert.equal(split.plainLines.length, 1)
  assert.equal(split.plainLines[0], '张三 缺交')

  // 学科行右侧必须有姓名内容；「物理：」空行丢弃
  const split2 = loadSplitter()('物理：')
  assert.equal(split2.subjectGroups.length, 0)
  assert.equal(split2.plainLines.length, 0)
})

test('用户真实痛点：单写「迟到：刘雨琪」自动单列考勤批次（不按学科计入，无需手填学科）', () => {
  const split = loadSplitter()('迟到：刘雨琪')
  assert.equal(split.error, null)
  assert.equal(split.plainLines.length, 0)
  assert.equal(split.subjectGroups.length, 1, '应独立生成 1 个「考勤」单列批次')
  assert.equal(split.subjectGroups[0].subject, '考勤')
  assert.equal(split.subjectGroups[0].input.kind, 'full')
  assert.equal(split.subjectGroups[0].input.exceptions.length, 1)
  assert.equal(split.subjectGroups[0].input.exceptions[0].name_or_alias, '刘雨琪')
  assert.equal(split.subjectGroups[0].input.exceptions[0].status, 'missing')
  assert.equal(split.subjectGroups[0].input.exceptions[0].attendance, '迟到')

  // 形式 B：学生在前（刘雨琪：迟到）
  const splitB = loadSplitter()('刘雨琪：迟到')
  assert.equal(splitB.subjectGroups.length, 1)
  assert.equal(splitB.subjectGroups[0].subject, '考勤')
  assert.equal(splitB.subjectGroups[0].input.exceptions[0].name_or_alias, '刘雨琪')

  // 形式 C：空格无冒号（刘雨琪 迟到）
  const splitC = loadSplitter()('刘雨琪 迟到')
  assert.equal(splitC.subjectGroups.length, 1)
  assert.equal(splitC.subjectGroups[0].subject, '考勤')
  assert.equal(splitC.subjectGroups[0].input.exceptions[0].name_or_alias, '刘雨琪')

  // 纯请假（请假：王五）
  const splitExc = loadSplitter()('请假：王五')
  assert.equal(splitExc.subjectGroups.length, 1)
  assert.equal(splitExc.subjectGroups[0].subject, '考勤')
  assert.equal(splitExc.subjectGroups[0].input.exceptions[0].name_or_alias, '王五')
  assert.equal(splitExc.subjectGroups[0].input.exceptions[0].status, 'excused')
})

test('用户真实用例：姓名：学科作业，学科（如 秦一：语文作文，数学；秦四：化学）', () => {
  const input = [
    '秦一：语文作文，数学',
    '秦四：化学',
  ].join('\n')
  const split = loadSplitter()(input)
  assert.equal(split.error, null)
  assert.equal(split.plainLines.length, 0)
  assert.equal(split.subjectGroups.length, 3, '应自动按学科反转归组为 语文、数学、化学 3 个独立批次')

  const chiGroup = split.subjectGroups.find((g) => g.subject === '语文')
  assert.ok(chiGroup)
  assert.equal(chiGroup.homeworkType, '作文', '剥离学科名后提取作业内容为 作文')
  assert.equal(chiGroup.input.kind, 'detailed')
  assert.equal(chiGroup.input.rows.length, 1)
  assert.equal(chiGroup.input.rows[0].name_or_alias, '秦一')
  assert.equal(chiGroup.input.rows[0].status, 'missing')
  assert.equal(chiGroup.input.rows[0].evaluation, undefined, '作业内容只存批次 homeworkType，绝不写入 evaluation')

  const mathGroup = split.subjectGroups.find((g) => g.subject === '数学')
  assert.ok(mathGroup)
  assert.equal(mathGroup.homeworkType, null, '只有学科信息录入时作业内容为 null')
  assert.equal(mathGroup.input.kind, 'detailed')
  assert.equal(mathGroup.input.rows.length, 1)
  assert.equal(mathGroup.input.rows[0].name_or_alias, '秦一')
  assert.equal(mathGroup.input.rows[0].status, 'missing')
  assert.equal(mathGroup.input.rows[0].evaluation, undefined)

  const chemGroup = split.subjectGroups.find((g) => g.subject === '化学')
  assert.ok(chemGroup)
  assert.equal(chemGroup.homeworkType, null)
  assert.equal(chemGroup.input.kind, 'detailed')
  assert.equal(chemGroup.input.rows.length, 1)
  assert.equal(chemGroup.input.rows[0].name_or_alias, '秦四')
  assert.equal(chemGroup.input.rows[0].status, 'missing')
})

test('按学生录入支持单科请假、迟到、忘带等状态描述（含空格与括号）', () => {
  const input = [
    '秦一：数学 请假，语文 迟到',
    '秦四：物理(忘带)，英语 缺交',
  ].join('\n')
  const split = loadSplitter()(input)
  assert.equal(split.error, null)
  assert.equal(split.plainLines.length, 0)

  const bySubject = Object.fromEntries(split.subjectGroups.map((g) => [g.subject, g.input]))
  // 数学：秦一 请假 (excused)
  assert.equal(bySubject['数学'].rows[0].name_or_alias, '秦一')
  assert.equal(bySubject['数学'].rows[0].status, 'excused')

  // 语文：秦一 迟到 (missing + attendance: 迟到，不计入已交)
  assert.equal(bySubject['语文'].rows[0].name_or_alias, '秦一')
  assert.equal(bySubject['语文'].rows[0].status, 'missing')
  assert.equal(bySubject['语文'].rows[0].attendance, '迟到')

  // 物理：秦四 忘带 (missing + evaluation: 忘带，不计入已交)
  assert.equal(bySubject['物理'].rows[0].name_or_alias, '秦四')
  assert.equal(bySubject['物理'].rows[0].status, 'missing')
  assert.equal(bySubject['物理'].rows[0].evaluation, '忘带')

  // 英语：秦四 缺交 (missing)
  assert.equal(bySubject['英语'].rows[0].name_or_alias, '秦四')
  assert.equal(bySubject['英语'].rows[0].status, 'missing')
})

test('混合录入：学科行（含全交）与学生行混写，考勤独立单列不污染学科作业', () => {
  const input = [
    '物理：全交',
    '化学：全交',
    '英语：张三、李四',
    '秦一：语文作文，数学',
    '秦四：化学',
    '迟到：刘雨琪',
    '王小明：请假',
  ].join('\n')
  const split = loadSplitter()(input)
  assert.equal(split.error, null)
  assert.equal(split.plainLines.length, 0)

  const bySubject = Object.fromEntries(split.subjectGroups.map((g) => [g.subject, g.input]))
  // 物理：全交（零例外，不被迟到/请假污染）
  assert.equal(bySubject['物理'].kind, 'full')
  assert.equal(bySubject['物理'].exceptions.length, 0)

  // 化学：秦四缺交（不混入刘雨琪/王小明）
  assert.equal(bySubject['化学'].kind, 'full')
  const chemNames = bySubject['化学'].exceptions.map((r) => r.name_or_alias)
  assert.equal(JSON.stringify(chemNames), JSON.stringify(['秦四']))

  // 英语：张三、李四
  const engNames = bySubject['英语'].rows.map((r) => r.name_or_alias)
  assert.equal(JSON.stringify(engNames), JSON.stringify(['张三', '李四']))

  // 语文：秦一
  const chiNames = bySubject['语文'].rows.map((r) => r.name_or_alias)
  assert.equal(JSON.stringify(chiNames), JSON.stringify(['秦一']))

  // 考勤（独立单列批次）：包含刘雨琪（迟到）与王小明（请假）
  assert.ok(bySubject['考勤'], '应独立生成「考勤」批次')
  assert.equal(bySubject['考勤'].kind, 'full')
  const attExceptions = bySubject['考勤'].exceptions
  const attMap = Object.fromEntries(attExceptions.map((r) => [r.name_or_alias, r]))
  assert.equal(attMap['刘雨琪'].status, 'missing')
  assert.equal(attMap['刘雨琪'].attendance, '迟到')
  assert.equal(attMap['王小明'].status, 'excused')
})

test('方案A：同学生录入多项作业时拆分为独立批次，缺交记录累加（如英语练习册、英语听力）', () => {
  const input = [
    '秦十九: 数学, 英语练习册, 英语听力',
    '秦十: 英语练习册, 英语听力',
  ].join('\n')
  const split = loadSplitter()(input)
  assert.equal(split.error, null)
  // 拆分为：数学、英语(练习册)、英语(听力) 共 3 个独立批次
  assert.equal(split.subjectGroups.length, 3)

  const engWorkbook = split.subjectGroups.find((g) => g.subject === '英语' && g.homeworkType === '练习册')
  assert.ok(engWorkbook, '应生成【英语 · 练习册】独立批次')
  assert.equal(engWorkbook.input.rows.length, 2)
  const gWorkbook = engWorkbook.input.rows.find((r) => r.name_or_alias === '秦十九')
  assert.ok(gWorkbook)
  assert.equal(gWorkbook.evaluation, undefined, '作业内容不再塞入 evaluation（ADR-031 分家）')
  const wWorkbook = engWorkbook.input.rows.find((r) => r.name_or_alias === '秦十')
  assert.ok(wWorkbook)
  assert.equal(wWorkbook.evaluation, undefined, '作业内容不再塞入 evaluation（ADR-031 分家）')

  const engListening = split.subjectGroups.find((g) => g.subject === '英语' && g.homeworkType === '听力')
  assert.ok(engListening, '应生成【英语 · 听力】独立批次')
  assert.equal(engListening.input.rows.length, 2)
  const gListening = engListening.input.rows.find((r) => r.name_or_alias === '秦十九')
  assert.ok(gListening)
  assert.equal(gListening.evaluation, undefined, '作业内容不再塞入 evaluation（ADR-031 分家）')

  const mathGroup = split.subjectGroups.find((g) => g.subject === '数学')
  assert.ok(mathGroup)
  assert.equal(mathGroup.homeworkType, null)
  assert.equal(mathGroup.input.rows.length, 1)
  assert.equal(mathGroup.input.rows[0].name_or_alias, '秦十九')
  assert.equal(mathGroup.input.rows[0].evaluation, undefined)
})

test('用户真实 8 行整段粘贴：「学科+作业名：名单」前缀全量解析（数学订正/化学练习册不再误判为无学科行）', () => {
  const input = [
    '物理：秦一，秦三，秦十五',
    '地理：秦一，秦十一',
    '英语：秦三',
    '数学卷子：秦二十七',
    '数学订正：秦一，秦三，秦七，秦十八，秦九，秦三十，秦十七，秦十六，秦八，秦二十九，秦十二，秦十，秦十五，秦二十七，秦二十，秦十一',
    '化学练习册：秦三，秦二十一',
    '化学卷子：秦三，秦三十，秦二十一，秦十四，秦十一',
    '生物：秦三',
  ].join('\n')
  const split = loadSplitter()(input)
  assert.equal(split.error, null)
  assert.equal(split.plainLines.length, 0, '8 行全部应带学科前缀，不得落入 plainLines 报「未标注学科」')
  assert.equal(split.subjectGroups.length, 8, '应拆为 8 个批次')

  const findGroup = (subject, homeworkType) =>
    split.subjectGroups.find((g) => g.subject === subject && (g.homeworkType ?? null) === homeworkType)

  // 纯学科前缀：homeworkType 均为 null
  for (const [subject, n] of [['物理', 3], ['地理', 2], ['英语', 1], ['生物', 1]]) {
    const group = findGroup(subject, null)
    assert.ok(group, `应生成【${subject}】批次`)
    assert.equal(group.homeworkType, null, `${subject} 无作业内容`)
    assert.equal(group.input.kind, 'detailed')
    assert.equal(group.input.rows.length, n, `${subject} 名单人数`)
    assert.ok(group.input.rows.every((r) => r.status === 'missing'), `${subject} 裸姓名应为缺交`)
  }

  // 学科+作业名前缀：剥离学科名后残留作为 homeworkType，拆为独立批次
  const mathZhengli = findGroup('数学', '订正')
  assert.ok(mathZhengli, '应生成【数学 · 订正】批次')
  assert.equal(mathZhengli.input.kind, 'detailed')
  assert.equal(mathZhengli.input.rows.length, 16, '数学·订正 名单 16 人（原文该行 16 个名字）')
  assert.ok(mathZhengli.input.rows.every((r) => r.status === 'missing'), '数学·订正 裸姓名全部为缺交')
  for (const name of ['秦一', '秦十五', '秦十一']) {
    assert.ok(mathZhengli.input.rows.some((r) => r.name_or_alias === name), `数学·订正 名单应含 ${name}`)
  }
  assert.equal(mathZhengli.input.rows[0].evaluation, undefined, '作业内容只存批次 homeworkType，绝不写入 evaluation')

  const mathJuanzi = findGroup('数学', '卷子')
  assert.ok(mathJuanzi, '应生成【数学 · 卷子】批次')
  assert.equal(mathJuanzi.homeworkType, '卷子')
  assert.equal(mathJuanzi.input.rows.length, 1)
  assert.equal(mathJuanzi.input.rows[0].name_or_alias, '秦二十七')

  const chemLianxi = findGroup('化学', '练习册')
  assert.ok(chemLianxi, '应生成【化学 · 练习册】批次')
  assert.equal(chemLianxi.homeworkType, '练习册')
  assert.equal(chemLianxi.input.rows.length, 2)

  const chemJuanzi = findGroup('化学', '卷子')
  assert.ok(chemJuanzi, '应生成【化学 · 卷子】批次')
  assert.equal(chemJuanzi.homeworkType, '卷子')
  assert.equal(chemJuanzi.input.rows.length, 5)
})

test('纯作业种类前缀（无学科）仍走默认学科兜底：练习册/订正 行守卫不回归', () => {
  const split = loadSplitter()('练习册：秦三，秦二十一\n订正：秦二十七')
  assert.equal(split.error, null)
  assert.equal(split.subjectGroups.length, 0, '无学科的纯种类前缀不得被当成学科批次')
  assert.deepEqual([...split.plainLines], ['练习册：秦三，秦二十一', '订正：秦二十七'], '两行应原样落入 plainLines 由手填默认学科接收')
})

test('录入面板：多学科合并单卡、一键确认全部、默认学科框', () => {
  assert.match(entryPanel, /splitHomeroomHomeworkText/, '班主任分支须用整段解析器')
  assert.match(entryPanel, /PreviewBatch\[\]/, '预览结果应为批次数组')
  assert.match(entryPanel, /runConfirmAll/, '一键确认全部批次入口')
  assert.match(entryPanel, /一键确认入库（\$\{previews\.length\} 科）/, '多科时按钮注明科数')
  assert.match(entryPanel, /subject: g\.subject/, '每个批次按学科组发起 preview')
  assert.match(entryPanel, /【\$\{g\.subject\}】/, '多科时错误须注明学科')
  assert.match(entryPanel, /previews\.map\(\(batch\) => \{/, '多学科合并为单卡逐科分区渲染')
  assert.match(entryPanel, /previews\.length > 1 \? `（\$\{previews\.length\} 科\）`/, '卡片标题注明科数')
  assert.match(entryPanel, /已交 \$\{String\(counts\.submitted\)\}/, '逐科分区显示统计')
  assert.match(entryPanel, /默认学科（可选）/, '学科框标注为默认学科')
  // 预览确认精简展示与考勤语义适配
  assert.match(entryPanel, /abnormalSubs/, '须筛选异常或已登记学生')
  assert.match(entryPanel, /normalSubs/, '须区分全班其余默认正常学生')
  assert.match(entryPanel, /全班其余/, '默认折叠提示其余正常学生')
  assert.match(entryPanel, /展开查看全班名单/, '提供展开全班名单按钮')
  assert.match(entryPanel, /isAttendance[\s\S]*正常出勤/, '考勤批次使用正常出勤语义')
  assert.match(entryPanel, /Badge variant="destructive">\{s\.attendance\}/, '考勤批次迟到等出勤异常直接展示考勤标签')
  // 一键确认的部分失败语义：成功批次移除、失败科保留错误提示
  assert.match(entryPanel, /prev\.filter\(\(b\) => b\.key in newErrors\)/, '部分失败只留失败批次')
})

test('api-v1 封装与排除卡：GET/PUT stats-exclusion、相关性不排除的说明', () => {
  for (const fn of ['homeworkStatsExclusion', 'homeworkStatsExclusionSet']) {
    assert.match(apiV1, new RegExp(`export function ${fn}\\(`), `应导出 ${fn}`)
  }
  assert.match(apiV1, /export interface HomeworkStatsExclusionEntry/, '条目类型应导出')
  assert.match(exclusionCard, /homeworkStatsExclusionSet/, '卡片须走 PUT 端点')
  assert.match(exclusionCard, /相关性、个人明细与批次明细仍完整保留/, '卡片须说明排除边界（相关性保留）')
  assert.match(exclusionCard, /请先在页面上方选择一个具体教学班/, '教学域未选班须引导')
  assert.match(exclusionCard, /不删除任何作业记录/, '须声明记录不删除')
})

test('排除卡挂在作业记录底部，工作台传递作用域与世代号', () => {
  assert.match(workspace, /StatsExclusionCard/, '作业页应挂载排除卡')
  assert.ok(workspace.indexOf('<AssignmentTable mode={mode}') < workspace.indexOf('<StatsExclusionCard'), '排除卡须位于作业记录之后')
  assert.match(workspace, /hasTeachingClass=\{typeof filter\.teaching_class_id === 'number'\}/, '教学域选班状态须透传')
  assert.match(workspace, /scopeQ=\{scopeQ\}/, '作用域查询须透传')
})

test('交互修复：学科行与考勤行的空格分隔姓名可识别，单科日筛选数字可二次点击收起', () => {
  const split = loadSplitter()('物理：秦三 秦二\n迟到：刘雨琪 王五')
  assert.equal(split.error, null)
  const physics = split.subjectGroups.find((g) => g.subject === '物理')
  assert.ok(physics, '应生成物理批次')
  // 解析器在 vm 沙箱执行：产出数组须拷贝到本 realm 再做 deepEqual（原型不同会误判不等）
  assert.deepEqual(
    [...physics.input.rows.map((r) => r.name_or_alias)].sort(),
    ['秦二', '秦三'].sort(),
    '空格分隔的多个姓名必须拆开登记',
  )
  const attendance = split.subjectGroups.find((g) => g.subject === '考勤')
  assert.ok(attendance, '应生成考勤批次')
  assert.deepEqual(
    [...attendance.input.exceptions.map((r) => r.name_or_alias)].sort(),
    ['刘雨琪', '王五'],
    '考勤行空格分隔的多个姓名必须拆开登记',
  )
  // 带状态的 token 不受空格拆分影响：状态仍然挂在对应姓名上
  const mixed = loadSplitter()('物理：秦三 请假，秦二')
  const mixedPhysics = mixed.subjectGroups.find((g) => g.subject === '物理')
  const excusedRow = mixedPhysics.input.rows.find((r) => r.name_or_alias === '秦三')
  assert.equal(excusedRow.status, 'excused', '「姓名 请假」中的请假不得被空格拆分丢失')

  // 单科日二次点击收起：收起条件不再要求 overview 态
  const table = readFileSync(new URL('../src/components/homework/AssignmentTable.tsx', import.meta.url), 'utf8')
  assert.match(table, /resetExpansion/, '展开状态必须统一通过 resetExpansion 清理')
  assert.doesNotMatch(table, /preferredAssignmentId/, 'toggleDayGroup 的死参数必须移除')
})
