// P8-UXFIX 契约测试：学生检索/画像/报告与班级对比改接 v1、教学成员迟到回包、
// 学生检索/画像/报告与班级对比改接 v1、教学成员迟到回包、作业页深链标签回归。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const studentList = readFileSync(new URL('../src/app/student/page.tsx', import.meta.url), 'utf8')
const studentDetail = readFileSync(new URL('../src/app/student/[id]/page.tsx', import.meta.url), 'utf8')
const studentReport = readFileSync(new URL('../src/app/student/[id]/report/page.tsx', import.meta.url), 'utf8')
const compare = readFileSync(new URL('../src/app/compare/page.tsx', import.meta.url), 'utf8')
const membersManager = readFileSync(
  new URL('../src/components/teaching-members/TeachingMembersManager.tsx', import.meta.url),
  'utf8',
)
const homeworkWorkspace = readFileSync(
  new URL('../src/components/homework/HomeworkWorkspace.tsx', import.meta.url),
  'utf8',
)
const homeworkCard = readFileSync(new URL('../src/components/HomeworkCard.tsx', import.meta.url), 'utf8')
const studentNotes = readFileSync(new URL('../src/components/StudentNotes.tsx', import.meta.url), 'utf8')

test('UX02：学生检索按 v1 作用域解析，搜索/学年/班级齐全，不接旧接口', () => {
  // v1 /teaching/students 要求显式教学班（空范围不退化）：具体班直查，
  // 「全部」按班级目录并行取各班名册，前端按 person 去重合并。
  assert.match(studentList, /fetchStudents\('teaching', q\)/, '名册须走 v1 teaching/students（工作台/学年/班级解析）')
  assert.match(studentList, /teaching_class_id: Number\(id\)/, '「全部」须按班级目录逐班取数，不得向 v1 传空范围')
  assert.match(studentList, /merged\.set\(String\(s\.person_id\), s\)/, '多班名册须按 person 去重合并')
  assert.match(studentList, /listExams\('teaching', scopeQ\)/, '考试清单须走 v1 shared/exams')
  assert.match(studentList, /fetchTeachingStudents\(latest\.exam_name, scopeQ\)/, '最新成绩/名次须走 v1 教学分析')
  assert.match(studentList, /debouncedQuery/, '搜索须有防抖')
  assert.match(studentList, /ScoreYearPicker/, '须提供学年切换')
  assert.match(studentList, /ClassScopePicker/, '须提供教学班筛选')
  assert.match(studentList, /if \(mode === 'homeroom'\)/, '班主任域不得混读教学班数据，须指引到 /homeroom/students')
  assert.match(studentList, /href="\/homeroom\/students"/, '指引卡须链接 v1 学生管理')
  assert.doesNotMatch(studentList, /\/api\/students/, '不得再调用旧 /api/students')
})

test('UX02：画像与报告页改接 v1（person_id 键 + 逐场名次 + 作业事件 + 档案）', () => {
  assert.match(studentDetail, /fetchStudent\('teaching', personId, scopeQ\)/, '画像须走 v1 teaching/students/{person_id}')
  assert.match(studentDetail, /fetchTeachingStudents\(name, scopeQ\)/, '逐场名次须走 v1 教学分析')
  assert.match(studentDetail, /<HomeworkCard mode="teaching" personId=\{personId\} scopeQ=\{scopeQ\}/, '作业缺交卡须以 person_id 走 v1 事件流')
  assert.match(studentDetail, /<StudentNotes mode="teaching" personId=\{personId\} scopeQ=\{scopeQ\}/, '档案须走 v1 域内档案')
  assert.doesNotMatch(studentDetail, /\/api\/(students|homework\/student|notes)\//, '不得再调用旧画像/作业/档案接口')
  assert.match(studentReport, /listStudentNotes\('teaching', personId, scopeQ\)/, '报告页档案须走 v1')
  assert.match(studentReport, /homeworkStudentEvents\(personId, 'teaching', scopeQ\)/, '报告页作业须走 v1 学生事件流')
  assert.doesNotMatch(studentReport, /\/api\/(students|homework\/student|notes)\//, '报告页不得再调用旧接口')
  assert.match(homeworkCard, /homeworkStudentEvents\(personId, mode, scopeQ\)/, 'HomeworkCard 须按 person_id 取 v1 事件流')
  assert.match(homeworkCard, /homeworkStatusLabel\(r\.status\)/, '画像作业状态须复用全站中文标签，不得显示 submitted 等枚举值')
  assert.match(homeworkCard, /连续性未知/, 'streaks 未知时必须标注，不得冒充连续')
  assert.match(homeworkCard, /resolveHomeworkContent/, '须使用 resolveHomeworkContent 解析作业内容')
  assert.match(homeworkCard, /<th[^>]*>作业内容<\/th>/, '明细表格须使用精简4列：日期、学科、作业内容、状态')
  assert.doesNotMatch(homeworkCard, /<th[^>]*>说明<\/th>/, '明细表格不得保留冗余的独立说明列')
  assert.doesNotMatch(homeworkCard, /'历史导入'/, '明细表格不得将 legacy 显示为历史导入')
  assert.match(studentNotes, /listStudentNotes\(mode, personId, scopeQ\)/, 'StudentNotes 须走 v1 域内档案')
  assert.doesNotMatch(studentNotes, /\/api\/notes/, 'StudentNotes 不得再调用旧档案接口')
})

test('UX03：班级对比与教学成绩页同源（v1 class-compare + stats），不接旧总分接口', () => {
  assert.match(compare, /fetchTeachingClassCompare\(selectedExam, scopeQ\)/, '对比数据须走 v1 teaching/analysis/class-compare')
  assert.match(compare, /fetchTeachingStats\(selectedExam, scopeQ\)/, '学科/口径/总体均分须走 v1 stats')
  assert.match(compare, /listExams\('teaching', scopeQ\)/, '考试清单须走 v1 shared/exams')
  assert.match(compare, /样本估算/, '样本估算口径必须可见（E04），不冒充官方')
  assert.match(compare, /deriveRanks/, '名次为派生排序（同分同名次），不得编造')
  assert.match(compare, /examsReqRef/, '考试清单须有独立请求序号（F11）')
  assert.match(compare, /dataReqRef/, '对比数据须有独立请求序号（F11）')
  assert.match(compare, /if \(mode === 'homeroom'\)/, '班主任域不得混读教学班对比')
  assert.doesNotMatch(compare, /\/api\/(class\/compare|exams)/, '不得再调用旧 /api/class/compare 或 /api/exams')
})

test('UX04：教学班切换后迟到回包按代丢弃（A 慢 B 快的反向顺序不覆盖新班）', () => {
  assert.match(membersManager, /const membersReqRef = useRef\(0\)/, '成员请求须有独立世代号')
  assert.match(membersManager, /const req = \+\+membersReqRef\.current/, '发请求前必须先递增世代号')
  assert.match(
    membersManager,
    /\.then\(\(r: TeachingMembersResponse\) => \{\n\s+if \(req !== membersReqRef\.current\) return\n\s+setMembers\(normalizeTeachingMembers\(r\)\)/,
    '成功回包必须先比对世代号再写入名单（迟到旧班回包丢弃）',
  )
  assert.match(
    membersManager,
    /\.catch\(\(err: unknown\) => \{\n\s+if \(req !== membersReqRef\.current\) return/,
    '失败回包同样必须比对世代号',
  )
  assert.match(membersManager, /membersReqRef\.current \+= 1 \/\/ 作废在途请求/, '清空选择时须作废在途请求')
  // 切班清理：上一班的弹窗/预览/提示不得带进新班（含默认选班路径）
  assert.match(membersManager, /prevClassRef/, '切班清理须挂在 selectedClassId 变化上（非仅 Select 回调）')
  for (const cleanup of ['setRemoving(null)', 'resetImport()', 'setSyncPreview(null)', 'setAddNotice(null)']) {
    assert.ok(membersManager.includes(cleanup), `切班清理缺 ${cleanup}`)
  }
})

test('UX05：作业页标签跟随 URL ?tab=（同页深链/前进后退），点击回写不堆历史', () => {
  assert.match(homeworkWorkspace, /useSearchParams\(\)/, '标签事实源须是 URL query')
  assert.match(homeworkWorkspace, /const tabParam = searchParams\.get\('tab'\)/, '读取 ?tab=')
  assert.match(homeworkWorkspace, /\}, \[tabParam\]\)/, 'tab 变化须触发同步（不能只读首挂载）')
  assert.match(homeworkWorkspace, /router\.replace\(`\?\$\{params\.toString\(\)\}`/, '点击标签须 replace 回写并保留既有参数')
  assert.doesNotMatch(homeworkWorkspace, /window\.location\.search/, '不得再读一次性 window.location')
})

// ───────── 复审返工回归（R01–R05） ─────────

const reportPage = studentReport

test('R01：对比页筛选行常驻渲染，冷启动空态也能切学年', () => {
  // 学年选择器必须出现在「无考试空态」之前（源码顺序 = 渲染结构），
  // 否则当前学年无考试时学年切换不可达。
  const pickerIdx = compare.indexOf('<ScoreYearPicker />')
  const emptyIdx = compare.indexOf("exams != null && exams.length === 0")
  assert.ok(pickerIdx >= 0, '对比页须有学年选择器')
  assert.ok(emptyIdx >= 0, '对比页须保留无考试空态')
  assert.ok(pickerIdx < emptyIdx, '学年选择器须在空态分支之外常驻渲染')
})

test('R03：班主任工作台不发任何教学域业务请求（列表/画像/报告/对比）', () => {
  assert.match(studentList, /if \(mode !== 'teaching'\) return/, '列表数据 effect 须按工作台门控')
  assert.match(studentDetail, /if \(mode !== 'teaching'\) return/, '画像数据 effect 须按工作台门控')
  assert.match(reportPage, /if \(mode !== 'teaching'\) return/, '报告数据 effect 须按工作台门控')
  assert.match(reportPage, /if \(mode === 'homeroom'\)/, '报告页 H 模式须显示指引卡而非取数')
  assert.match(compare, /if \(mode !== 'teaching'\) return/, '对比页数据 effect 须按工作台门控')
})

test('R04：「全部所教班」部分失败显式标注 + 重试，不冒充完整名单', () => {
  assert.match(studentList, /const \[partialFailed, setPartialFailed\] = useState<number \| null>\(null\)/, '须有部分失败状态')
  assert.match(studentList, /failed === rosResults\.length/, '全部失败须走整体错误态')
  assert.match(studentList, /setPartialFailed\(failed > 0 \? failed : null\)/, '部分失败须记录失败班数')
  assert.match(studentList, /部分名单/, '部分结果必须显著标注')
  assert.match(studentList, /不代表完整人数/, '不得把部分名单当完整人数')
  assert.match(studentList, /setRosterNonce\(\(n\) => n \+ 1\)/, '须支持重试')
})

test('R05：打印报告逐场教学班名次与画像页同口径', () => {
  assert.match(reportPage, /fetchTeachingStudents\(name, scopeQ\)/, '报告页须逐场取名次')
  assert.match(reportPage, /rankMap\.set\(name, students\.find\(\(s\) => s\.person_id === personId\)\?\.rank \?\? null\)/, '名次须按同人匹配')
  assert.match(reportPage, /教学班排名/, '须有教学班排名列')
  assert.match(reportPage, /教学班排名从/, '须有名次变化结论')
})

test('R02：迁移补齐 CLI 绑定源快照（契约镜像，实现在后端 pytest）', () => {
  // 前端契约不含迁移脚本；此用例仅登记映射，真正断言在
  // backend/tests/v1/test_p8_real_preflight.py::test_backfill_cli_binds_marker_and_source_digests_r02
  const migrationScript = readFileSync(new URL('../../scripts/migration/run_real_migration.py', import.meta.url), 'utf8')
  assert.match(migrationScript, /source snapshot digest mismatch/, '补齐须校验 marker 源摘要')
  assert.match(migrationScript, /SELECT status FROM migration_run WHERE run_token=\?/, '补齐须校验目标迁移身份')
  assert.match(migrationScript, /must not be inside target-root/, '补齐须拒绝源在 target 内')
})
