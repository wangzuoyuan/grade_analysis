// F04/F11 修复 + F06 前端身份候选确认的契约测试（契约 p1-api v2.1 §3、p3-imports-analysis v2.1 §1.1/§1.2）。
// 风格沿用 tests/scores-p3.test.mjs：直接读源码断言关键结构，不启动浏览器（联调由集成者执行）。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const workspace = readFileSync(new URL('../src/lib/workspace.tsx', import.meta.url), 'utf8')
const apiV1 = readFileSync(new URL('../src/lib/api-v1.ts', import.meta.url), 'utf8')
const sidebar = readFileSync(new URL('../src/components/layout/Sidebar.tsx', import.meta.url), 'utf8')
const topbar = readFileSync(new URL('../src/components/layout/Topbar.tsx', import.meta.url), 'utf8')
const uploadPage = readFileSync(new URL('../src/app/upload/page.tsx', import.meta.url), 'utf8')
const identityPanel = readFileSync(new URL('../src/components/upload/IdentityCandidatesPanel.tsx', import.meta.url), 'utf8')
const homeroomScores = readFileSync(new URL('../src/components/scores/HomeroomScores.tsx', import.meta.url), 'utf8')
const teachingScores = readFileSync(new URL('../src/components/scores/TeachingScores.tsx', import.meta.url), 'utf8')

test('F04：mode 事实源 = 路径前缀 → ?ws → lastMode，公共页读 useSearchParams', () => {
  assert.match(workspace, /useSearchParams\(\)/, 'Provider 必须读 URL 查询参数')
  assert.match(workspace, /\.get\('ws'\)/, '查询参数键必须是 ws')
  // 事实源顺序：路径前缀 ?? wsMode ?? lastMode
  assert.match(workspace, /pathMode \?\? wsMode \?\? lastMode/, 'mode 事实源顺序必须为 路径 → ?ws → lastMode')
  // 非法 ws 值一律视为未指定（不得当 teaching）
  assert.match(workspace, /ws === 'homeroom' \|\| ws === 'teaching' \? ws : null/, 'ws 仅接受 homeroom|teaching，其余视为未指定')
  // 公共页缺/非法 ?ws 时必须回写 URL（lastMode 只在内存，刷新即丢）
  assert.match(workspace, /params\.set\('ws', mode\)/, '缺失/非法 ws 必须以当前解析值修正 URL')
  assert.match(workspace, /Suspense/, 'useSearchParams 必须有最近 Suspense 边界（静态预渲染构建约束）')
})

test('F04：setMode 切换工作台统一跳 /{mode} 根路径（含公共页），公共页不再停留', () => {
  const setModeIdx = workspace.indexOf('const setMode = useCallback')
  assert.ok(setModeIdx > 0, '应能定位 setMode')
  const setModeBody = workspace.slice(setModeIdx, workspace.indexOf('const setFilter =', setModeIdx))
  assert.match(setModeBody, /if \(pathMode === next\) return/, '工作台页点击当前工作台 = 无操作守卫必须保留')
  assert.match(setModeBody, /bumpGeneration\(\)/, '点击即作废在途响应')
  assert.match(setModeBody, /setScope\(null\)/, '必须丢弃旧工作台范围，防止跨模式闪现')
  assert.match(setModeBody, /router\.push\(`\/\$\{next\}`\)/, '任何页面切换均跳 /{mode} 根路径（仪表盘）')
  // 公共页停留分支已废除：不得再写 ?ws=next，也不得 replace 停留当前页
  assert.doesNotMatch(setModeBody, /params\.set\('ws', next\)/, '公共页分支已删除，不得再写 ?ws=next 停留')
  assert.doesNotMatch(setModeBody, /router\.replace\(/, 'setMode 不得再 replace 停留当前页')
})

test('F04：切换器高亮与 Provider 同一 mode 值，点击已高亮项不再提前 return', () => {
  const switcherIdx = workspace.indexOf('export function WorkspaceSwitcher')
  assert.ok(switcherIdx > 0, '应能定位 WorkspaceSwitcher')
  const switcherBody = workspace.slice(switcherIdx)
  assert.match(switcherBody, /const \{ mode, setMode, switching \} = useWorkspace\(\)/, '高亮必须用 Provider 的 mode')
  assert.doesNotMatch(switcherBody, /\? 'homeroom' : 'teaching'/, '不得再按 pathname 猜测高亮（与 Provider 不一致即 F04 根因）')
  assert.doesNotMatch(switcherBody, /if \(next === current\) return/, '不得按高亮提前 return（公共页需能补齐 ?ws）')
})

test('F04：侧栏/顶栏入口链接携带当前工作台（?ws=）', () => {
  assert.match(workspace, /export function workspaceHref\(/, 'workspace.tsx 应导出 workspaceHref 助手')
  assert.match(workspace, /if \(href\.startsWith\('\/homeroom'\) \|\| href\.startsWith\('\/teaching'\)\) return href/, '工作台前缀页不加 ws，公共页目标必须补')
  assert.match(sidebar, /workspaceHref\(item\.href, mode\)/, '侧栏导航链接必须携带 ws')
  assert.match(sidebar, /mode === 'homeroom' \? '\/homeroom\/students' : '\/teaching\/members'/, '侧栏管理入口须随工作台进入对应的成员页')
  assert.match(topbar, /workspaceHref\(c\.href, mode\)/, '顶栏面包屑可点击链接必须携带 ws')
  // 上传页提交 multipart 的 mode 取 Provider（= URL 事实源）
  assert.match(uploadPage, /const \{ mode, filter, switching \} = useWorkspace\(\)/, '上传页 mode 必须取自 Provider')
  assert.match(uploadPage, /form\.set\('mode', mode\)/, '提交表单必须显式携带该 mode')
})

test('F11：两成绩页请求序号按资源分离，切考试不再永久骨架', () => {
  assert.match(homeroomScores, /const examsReqRef = useRef\(0\)/, '班主任页考试清单须独立序号')
  assert.match(homeroomScores, /const dataReqRef = useRef\(0\)/, '班主任页 stats/students 须独立序号')
  assert.match(homeroomScores, /const bandsReqRef = useRef\(0\)/, '班主任页段位须独立序号')
  assert.match(homeroomScores, /const trendsReqRef = useRef\(0\)/, '班主任页趋势须独立序号')
  assert.match(teachingScores, /const examsReqRef = useRef\(0\)/, '教学页考试清单须独立序号')
  assert.match(teachingScores, /const dataReqRef = useRef\(0\)/, '教学页 stats/students 须独立序号')
  // 单一共享 reqRef 必须已从两页消失
  assert.doesNotMatch(homeroomScores, /const reqRef = useRef\(0\)/, '班主任页不得再有共享单一序号')
  assert.doesNotMatch(teachingScores, /const reqRef = useRef\(0\)/, '教学页不得再有共享单一序号')
  // 数据 effect 的守卫只看 dataReqRef
  const dataEffectIdx = homeroomScores.indexOf('fetchHomeroomStats(')
  const dataCtx = homeroomScores.slice(Math.max(0, dataEffectIdx - 500), dataEffectIdx)
  assert.match(dataCtx, /\+\+dataReqRef\.current/, 'stats/students effect 必须递增 dataReqRef')
})

test('F06：契约类型与 confirm 显式身份确认参数（p3 v2.1 §1.1/§1.2）', () => {
  assert.match(apiV1, /export interface ImportIdentityCandidate\b/, '应导出 ImportIdentityCandidate 类型')
  for (const f of ['alias', 'person_id', 'academic_year_id', 'academic_year_name', 'basis']) {
    assert.match(apiV1, new RegExp(`\\b${f}\\b`), `候选类型应含字段 ${f}`)
  }
  assert.match(apiV1, /identity_candidates\?: ImportIdentityCandidate\[\]/, 'preview 条目应可选携带身份候选')
  assert.match(apiV1, /identityConfirmations\?: Record<string, number>/, 'confirm 应接受 alias_value → person_id 确认映射')
  assert.match(apiV1, /identity_confirmations: identityConfirmations/, 'confirm 请求体应携带 identity_confirmations')
  assert.match(apiV1, /export function readImportIdentityCandidates\(/, '应导出 409 候选清单宽容读取')
  assert.match(apiV1, /err\.body\.candidates/, '候选读取应走 409 错误体的 candidates 字段')
})

test('F06：上传页渲染候选确认 UI，未决候选禁用确认；选「新建」不进映射', () => {
  assert.match(uploadPage, /IdentityCandidatesPanel/, '上传页必须渲染候选确认面板')
  assert.match(uploadPage, /readImportIdentityCandidates\(err\)/, 'confirm 409 的候选清单必须补入 UI')
  assert.match(uploadPage, /it\.identity_candidates/, '预览 items 的候选必须进入确认区')
  assert.match(uploadPage, /!identityAllPicked/, '存在未决候选时确认按钮必须禁用')
  assert.match(uploadPage, /请为每位学生选择/, '未决候选必须有明确提示文案')
  assert.match(uploadPage, /setIdentityPicks\(\{\}\)/, '新预览/重置必须清空旧选择')
  // 「新建学生」选项存在；提交映射只收 number（'new' 不进 identity_confirmations）
  assert.match(identityPanel, /新建学生/, '面板必须提供「新建学生」选项')
  assert.match(uploadPage, /if \(typeof pick === 'number'\) confirmations\[alias\] = pick/, "选「新建」的别名不得进 identity_confirmations")
  assert.match(identityPanel, /role="radiogroup"/, '候选选择须为单选组（键盘可操作）')
})

test('F10/F11：bands 无名次口径 409 渲染「不可计算」提示卡而非骨架', () => {
  assert.match(homeroomScores, /bandsUnavailable/, 'bands 须区分 409 不可算与其他错误')
  assert.match(homeroomScores, /err instanceof ApiV1Error && err\.status === 409/, '409 判定须基于 ApiV1Error 状态码')
  assert.match(homeroomScores, /段位不可计算（无名次口径）/, '必须呈现契约固定文案「段位不可计算（无名次口径）」')
  assert.match(homeroomScores, /不呈现任何分段/, '须说明不呈现错误分段')
  const unavailableIdx = homeroomScores.indexOf('bandsUnavailable ?')
  assert.ok(unavailableIdx > 0, '应能定位不可算提示分支')
  const branch = homeroomScores.slice(unavailableIdx, homeroomScores.indexOf(') : bandsError ?', unavailableIdx))
  assert.doesNotMatch(branch, /Skeleton/, '不可算提示分支不得渲染骨架')
})
