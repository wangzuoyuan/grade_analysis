// A1 修复回归测试：仪表盘「查看档案」点开 404。
// 根因：/homeroom/students/[id] 层级历史上无 page.tsx（仅 report/ 与
// diagnosis-report/ 子路由），行动卡两处链接直指该层级 → 404。
// 修复断言：
// - 行动卡「优先关注」的姓名链接与「查看档案」链接均指向 /report 子路由；
// - 不再存在指向无页面层级的旧链接；
// - 新增 [id] 层级重定向兜底页（./report），且目标路由文件确实存在。
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'

const card = readFileSync(new URL('../src/app/homeroom/action-summary.tsx', import.meta.url), 'utf8')

test('A1：行动卡姓名链接与「查看档案」链接均以 /report 结尾（直达学生画像打印页）', () => {
  assert.match(card, /查看档案/, '直达入口文案')
  const hrefs = card.match(/href=\{`\/homeroom\/students\/\$\{encodeURIComponent\(String\(item\.person_id\)\)\}\/report`\}/g) ?? []
  assert.equal(hrefs.length, 2, '姓名链接 + 查看档案链接共 2 处，均需 /report 后缀')
})

test('A1：行动卡不再存在指向无页面层级 /homeroom/students/[id] 的旧链接', () => {
  assert.doesNotMatch(
    card,
    /href=\{`\/homeroom\/students\/\$\{encodeURIComponent\(String\(item\.person_id\)\)\}`\}/,
    '不带子路由的 href 即 404 链接，必须清除',
  )
})

test('A1：[id] 层级重定向兜底页存在，且用含 id 的绝对路径（相对 ./report 会丢动态段循环重定向）', () => {
  const indexPageUrl = new URL('../src/app/homeroom/students/[id]/page.tsx', import.meta.url)
  assert.ok(existsSync(indexPageUrl), '兜底页文件必须存在')
  const indexPage = readFileSync(indexPageUrl, 'utf8')
  assert.match(indexPage, /import \{ redirect \} from 'next\/navigation'/, '服务端重定向')
  assert.match(
    indexPage,
    /redirect\(`\/homeroom\/students\/\$\{encodeURIComponent\(params\.id\)\}\/report`\)/,
    '重定向必须拼绝对路径并携带 params.id',
  )
  assert.doesNotMatch(
    indexPage,
    /redirect\('\.\/report'\)/,
    '相对 ./report 运行时解析为 /homeroom/students/report（丢动态段 → 循环重定向），禁止回退',
  )
})

test('A1：链接目标路由 /homeroom/students/[id]/report 确实存在（链接↔路由对账）', () => {
  assert.ok(
    existsSync(new URL('../src/app/homeroom/students/[id]/report/page.tsx', import.meta.url)),
    'report 子路由文件必须存在，否则链接再次 404',
  )
})
