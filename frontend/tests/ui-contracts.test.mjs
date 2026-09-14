import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const dashboard = readFileSync(new URL('../src/app/page.tsx', import.meta.url), 'utf8')
const homework = readFileSync(new URL('../src/app/homework/page.tsx', import.meta.url), 'utf8')
const examDetail = readFileSync(new URL('../src/app/exam/[id]/page.tsx', import.meta.url), 'utf8')
const legacyRedirect = readFileSync(new URL('../src/components/LegacyWorkspaceRedirect.tsx', import.meta.url), 'utf8')
const classScope = readFileSync(new URL('../src/lib/class-scope.tsx', import.meta.url), 'utf8')
const homeworkSettings = readFileSync(new URL('../src/app/homework/settings/page.tsx', import.meta.url), 'utf8')
const weeklyFocus = readFileSync(new URL('../src/components/WeeklyFocusCard.tsx', import.meta.url), 'utf8')
const homeworkEntryPreview = readFileSync(new URL('../src/components/HomeworkEntryPreview.tsx', import.meta.url), 'utf8')
const classSettings = readFileSync(new URL('../src/app/settings/classes/page.tsx', import.meta.url), 'utf8')
const examList = readFileSync(new URL('../src/app/exam/page.tsx', import.meta.url), 'utf8')
const studentList = readFileSync(new URL('../src/app/student/page.tsx', import.meta.url), 'utf8')

test('旧无作用域入口统一重定向到当前 v1 工作台（P8-B/C）', () => {
  assert.match(dashboard, /destination="overview"/, '旧首页须进入当前工作台首页')
  assert.match(examList, /destination="scores"/, '旧考试列表须进入当前工作台成绩页')
  assert.match(examDetail, /destination="scores"/, '旧考试详情须进入当前工作台成绩页')
  assert.match(homework, /destination="homework"/, '旧作业看板须进入当前工作台作业页')
  assert.match(legacyRedirect, /router\.replace\(target\)/, '深链须使用替换导航，避免回退循环')
  assert.match(legacyRedirect, /`\/\$\{mode\}\/\$\{destination\}`/, '目标路径须显式携带工作台作用域')
})

test('weekly focus errors cannot crash the home dashboard', () => {
  assert.match(weeklyFocus, /if \(!r\.ok\) throw new Error/, '接口错误必须进入失败态')
  assert.match(weeklyFocus, /Array\.isArray\(\(payload as WeeklyFocus\)\.students\)/, '响应必须校验 students 数组')
})

test('兼容班级选择器改读 v1 目录，不再访问已移除旧端点（P8-C）', () => {
  assert.match(classScope, /fetchSharedConfig/, '须从 v1 配置解析默认学年')
  assert.match(classScope, /fetchV1Classes/, '须从 v1 班级目录取教学班')
  assert.match(classScope, /setFilter\(\{ teaching_class_id: v \}\)/, '班级选择须写入工作台筛选')
  assert.doesNotMatch(classScope, /\/api\/teaching\/classes/, '不得再调用旧班级端点')
  assert.doesNotMatch(classScope, /\/api\/teaching\/current/, '不得再调用旧全局当前班端点')
})

test('homework roster is a read-only view scoped to the selected teaching class', () => {
  assert.match(homeworkSettings, /useClassScope\(\)/, '作业设置应读取当前教学班')
  assert.match(homeworkSettings, /const rosterRequestIdRef = useRef\(0\)/, '设置页应跟踪最新花名册请求')
  assert.match(homeworkSettings, /requestId !== rosterRequestIdRef\.current/, '旧班花名册响应不得覆盖当前班')
  assert.match(homeworkSettings, /const visibleRoster = rosterScope === current \? roster : \[\]/, '花名册必须只渲染与当前选择器一致的范围')
  assert.match(homeworkSettings, /setRoster\(\[\]\)/, '切班请求开始时应清空旧花名册')
  assert.match(homeworkSettings, /setRosterError\(true\)/, '花名册失败时应进入显式错误态')
  assert.match(homeworkSettings, /toggle-excluded/, '排除统计开关保留在作业设置')
  assert.doesNotMatch(homeworkSettings, /addStudent|removeStudent/, '成员增删统一在班级配置页维护，作业设置不得再有成员增删')
  assert.match(homeworkSettings, /\/settings\/classes/, '作业设置应指引用户到班级配置页维护成员')
  assert.match(classSettings, /redirect\('\/teaching\/members'\)/, '旧班级配置深链须转到 v1 成员管理')
})

test('旧作业解析组件保留核心解析契约', () => {
  assert.match(homeworkEntryPreview, /\[:：\]/, '预览按中英冒号切分是核心解析规则')
  assert.match(homeworkEntryPreview, /行缺少冒号/, 'by_student/by_subject 模式必须对缺冒号的行给出警示')
  assert.match(homeworkEntryPreview, /mode === 'smart'/, 'smart 模式的无冒号行必须按「姓名+动作」智能识别而不是判错')
})

test('student list renders name-only roster members without fake profile links', () => {
  // P8-UXFIX：页面改接 v1（fetchStudents 名册 + 最新一场教学分析），学号来自
  // v1 alias（缺省显示「待补学号」）；画像链接以 person_id 为键，全员可进。
  assert.match(studentList, /student\.alias \? displayStudentId\(student\.alias\) : '待补学号'/, '仅姓名成员不得展示内部占位学号')
  assert.match(studentList, /fetchStudents\('teaching', q\)/, '名册须走 v1 teaching/students')
  assert.doesNotMatch(studentList, /fetch\('\/api\/students/, '不得再调用旧 /api/students 数组接口')
})
