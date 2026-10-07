// A2 契约测试：学生档案 getStudentReport 的学年口径（「历次考试各科明细/总分走势」查空的根因修复）。
// 根因：HomeroomProfileView 调 getStudentReport 不带 academic_year_id，后端按最新学年解析，
// 而历史学年（成绩事实所在学年）整页查空。契约：传 academicYearId → URL 必含 academic_year_id；
// 不传 → 不得携带该参数（保持后端默认语义，兼容既有调用方，如打印画像页）。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

// 编译真实源码后导入，兼容 CI 的 Node 20，同时验证实际请求 URL。
const compiledApi = ts.transpileModule(
  readFileSync(new URL('../src/lib/api-v1.ts', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } },
).outputText
const { getStudentReport } = await import(
  `data:text/javascript;base64,${Buffer.from(compiledApi).toString('base64')}`
)

const profileView = readFileSync(
  new URL('../src/components/students/HomeroomProfileView.tsx', import.meta.url),
  'utf8',
)

/** request() 只依赖 res.ok 与 res.json()，返回最小可解析的空 report 体即可 */
const emptyReport = {
  metadata: { mode: 'homeroom', scope: {}, cohort_size: 0, data_revision: 0 },
  person: { person_id: 1, name: null, domain: 'homeroom', aliases: [] },
  roster: { class_id: 1, seat_no: null, status: null, alias: null },
  subjects: [],
  totals: null,
  notes_summary: { count: 0, recent: [] },
}

const realFetch = globalThis.fetch

/** mock fetch：记录每次请求 URL 并返回 200 + 空 report 体；测试结束须还原真实 fetch */
function stubFetch() {
  const urls = []
  globalThis.fetch = async (input) => {
    urls.push(typeof input === 'string' ? input : String(input?.url ?? input))
    return { ok: true, json: async () => emptyReport }
  }
  return urls
}

test('getStudentReport 传 academicYearId 时请求 URL 含 academic_year_id', async () => {
  const urls = stubFetch()
  try {
    await getStudentReport(101, 1)
    assert.equal(urls.length, 1, '应恰好发起一次请求')
    assert.match(
      urls[0],
      /\/api\/v1\/homeroom\/students\/101\/report\?academic_year_id=1$/,
      '端点路径不变，且唯一查询参数为 academic_year_id=1',
    )
  } finally {
    globalThis.fetch = realFetch
  }
})

test('getStudentReport 不传 academicYearId 时请求 URL 不含该参数（无尾随问号）', async () => {
  const urls = stubFetch()
  try {
    await getStudentReport(101)
    await getStudentReport('S-2026-01', undefined)
    assert.equal(urls.length, 2)
    for (const url of urls) {
      assert.doesNotMatch(url, /academic_year_id/, '缺省调用不得携带 academic_year_id')
      assert.doesNotMatch(url, /\?$/, '不得留下空查询串')
    }
    assert.match(urls[0], /\/api\/v1\/homeroom\/students\/101\/report$/, '数字 personId 路径不变')
    assert.match(
      urls[1],
      /\/api\/v1\/homeroom\/students\/S-2026-01\/report$/,
      '字符串 personId 仍须 encodeURIComponent',
    )
  } finally {
    globalThis.fetch = realFetch
  }
})

test('HomeroomProfileView 把 scopeQ.academic_year_id 传给 getStudentReport（同页口径一致）', () => {
  assert.match(
    profileView,
    /getStudentReport\(Number\(selectedPersonId\), scopeQ\.academic_year_id\)/,
    '档案请求须复用名册/诊断卡的同一学年来源',
  )
  assert.match(
    profileView,
    /\[selectedPersonId, generation, scopeQ\.academic_year_id\]/,
    '学年变化须触发档案重拉（迟到响应仍由 reportReqRef 作废）',
  )
})

test('A2 补漏（Codex 复审 #1）：打印页档案写入（createNote/deleteNote）同样透传学年', () => {
  const printPage = readFileSync(
    new URL('../src/app/homeroom/students/[id]/report/page.tsx', import.meta.url),
    'utf8',
  )
  assert.match(
    printPage,
    /createNote\('homeroom', personId, \{[\s\S]*?\}, \{ academic_year_id: academicYearId \}\)/,
    'createNote 必须带 academic_year_id（历史学年学生档案可读也须可写）',
  )
  assert.match(
    printPage,
    /deleteNote\('homeroom', noteId, \{ academic_year_id: academicYearId \}\)/,
    'deleteNote 必须带 academic_year_id',
  )
  assert.match(
    printPage,
    /listStudentNotes\('homeroom', personId, \{ academic_year_id: academicYearId \}\)/,
    'listStudentNotes（页面级全量档案）保持学年透传',
  )
})
