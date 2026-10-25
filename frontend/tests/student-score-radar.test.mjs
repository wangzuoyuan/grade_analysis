// 2026-10-25 用户拍板：点开姓名的展开区/档案页走势图上方新增「历次成绩雷达」——
// 每场考试一个多边形重叠可筛选；轴值统一换算 0-100 位次分（语数英/全科=100−前%、
// 加三=(等级分−40)÷30×100、总分轴按总分年级百分位定位悬浮显示第N名）；轴数>7 拆
// 主科+选科两张（高一九门场景）。源码契约 + recharts SSR 真渲染（沿用 2026-10-25 先例）。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'
const nativeRequire = createRequire(import.meta.url)
const root = path.resolve(fileURLToPath(new URL('../src', import.meta.url)))
const cache = new Map()
function load(file) {
  const resolved = path.join(root, file)
  if (cache.has(resolved)) return cache.get(resolved)
  const exports = {}
  cache.set(resolved, exports)
  const source = readFileSync(resolved, 'utf8')
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  }}).outputText
  new Function('exports', 'require', code)(exports, id => {
    if (id === 'recharts') {
      // SSR 下 ResponsiveContainer 无尺寸不出 SVG——注入固定宽高（沿用 2026-10-25
      // BarChart SSR 先例的思路：静态渲染必须给显式 width/height）
      const rc = nativeRequire(id)
      return {
        ...rc,
        ResponsiveContainer: (props) => React.cloneElement(React.Children.only(props.children), { width: 420, height: 320 }),
      }
    }
    if (id.startsWith('@/') || id.startsWith('.')) {
      const target = id.startsWith('@/') ? id.slice(2) : path.relative(root, path.resolve(path.dirname(resolved), id))
      return load(target + (['lib/utils'].includes(target) ? '.ts' : '.tsx'))
    }
    return nativeRequire(id)
  })
  return exports
}
const source = readFileSync(path.join(root, 'components/students/StudentScoreRadar.tsx'), 'utf8')
const { StudentScoreRadar, percentileToRadarScore, gradeScoreToRadarScore } = load('components/students/StudentScoreRadar.tsx')
const render = (props) => renderToStaticMarkup(React.createElement(StudentScoreRadar, props))

test('归一化公式：前%→100−p、等级分(40-70)→0-100，越外越好', () => {
  assert.equal(percentileToRadarScore(12.5), 87.5)
  assert.equal(percentileToRadarScore(0), 100)
  assert.equal(percentileToRadarScore(100), 0)
  assert.equal(gradeScoreToRadarScore(40), 0)
  assert.equal(gradeScoreToRadarScore(70), 100)
  assert.equal(gradeScoreToRadarScore(64), 80)
  assert.match(source, /return 100 - p/, '前% 公式')
  assert.match(source, /return \(\(g - GRADE_SCORE_MIN\) \/ \(GRADE_SCORE_MAX - GRADE_SCORE_MIN\)\) \* 100/, '等级分公式')
})

test('源码契约：总分轴口径优先级/排除+3、轴数>7 拆分、筛选 chips、原值悬浮', () => {
  assert.match(source, /RADAR_EXCLUDED_TOTALS = new Set\(\['\+3'\]\)/, '+3（等级和）无名次不进总分轴')
  assert.match(source, /TOTAL_PRIORITY = \['3\+3', '九门', '主三门', '五门'\]/, '总分口径优先级')
  assert.match(source, /RADAR_MAX_AXES = 7/, '拆分阈值')
  assert.match(source, /主科雷达（语数英 \+ 总分）/, '拆分 caption：主科')
  assert.match(source, /选科雷达/, '拆分 caption：选科')
  assert.match(source, /CORE_SUBJECTS = \['语文', '数学', '英语'\]/, '主科定义')
  assert.match(source, /SUBJECT_ORDER = \['语文', '数学', '英语', '物理', '化学', '生物', '政治', '历史', '地理'\]/, '规范学科序')
  assert.match(source, /const TOTAL_TYPE_LABELS: Record<string, string>/, '口径标签单一来源（档案页同源导入）')
  // 筛选：自定义图例按钮 + Radar hide（沿用走势图 chips 范式）
  assert.match(source, /aria-pressed=\{!hidden\}/, '考试 chips 按压态')
  assert.match(source, /hide=\{hiddenExams\.has\(row\.exam_key\)\}/, 'Radar hide 受控')
  // 悬浮原值不造数
  assert.match(source, /`第 \$\{cell\.rank\} 名`/, '总分悬浮显示名次')
  assert.match(source, /`等级分 \$\{cell\.grade_score\}`/, '加三悬浮显示等级分')
  assert.match(source, /`前 \$\{cell\.percentile\}%`/, '前%悬浮')
  // 径向刻度统一 0-100
  assert.match(source, /domain=\{\[0, 100\]\}/, 'PolarRadiusAxis 0-100')
  assert.match(source, /connectNulls=\{false\}/, '缺考点不连多边形')
})

const grade23Row = (exam_name, date, pct, gs, totalPct, totalRank) => ({
  exam_key: `${exam_name}|${date}`,
  exam_name,
  exam_date: date,
  subjects: {
    语文: { percentile: pct[0], grade_score: null },
    数学: { percentile: pct[1], grade_score: null },
    英语: { percentile: pct[2], grade_score: null },
    物理: { percentile: null, grade_score: gs[0] },
    化学: { percentile: null, grade_score: gs[1] },
  },
  totals: { '3+3': { percentile: totalPct, rank: totalRank }, 主三门: { percentile: null, rank: totalRank + 1 }, '+3': { percentile: null, rank: null } },
})

test('SSR 真渲染：高二场景一张雷达——两场考试两个多边形、6学科+总分轴、+3不出现', () => {
  const html = render({
    rows: [
      grade23Row('10月考', '2026-10-13', [12.5, 30, 45.6], [64, 55], 21.9, 136),
      grade23Row('期中考', '2026-11-18', [8.3, 22.1, 40], [67, 61], 15.2, 95),
    ],
  })
  assert.equal((html.match(/recharts-radar-polygon/g) ?? []).length, 2, '每场考试一个多边形系列（recharts-radar-polygon ×2）')
  for (const label of ['语文', '数学', '英语', '物理', '化学', '3+3总分']) {
    assert(html.includes(`>${label}<`), `轴标签应含 ${label}`)
  }
  assert(!html.includes('主科雷达'), '轴数 ≤7 不拆分')
  assert(!html.includes('+3总分（等级和）'), '+3 不进雷达（其完整口径标签不得出现；3+3总分 是另一口径）')
  assert(!html.includes('>三门总分<'), '主三门优先级低于 3+3，不作总分轴')
  assert.match(html, /总分轴为3\+3总分口径，悬浮显示第 N 名/, '脚注写明总分口径')
  assert.match(html, /轴值已统一换算为 0–100 位次分/, '换算说明在场')
  // 考试筛选 chips
  assert.match(html, /aria-label="隐藏10月考雷达"/)
  assert.match(html, /aria-label="隐藏期中考雷达"/)
})

test('SSR 真渲染：高一九门场景拆两张——主科雷达（语数英+总分）+ 选科雷达', () => {
  const subjects = ['语文', '数学', '英语', '物理', '化学', '生物', '政治', '历史', '地理']
  const row = (name, date) => ({
    exam_key: `${name}|${date}`,
    exam_name: name,
    exam_date: date,
    subjects: Object.fromEntries(subjects.map((s) => [s, { percentile: 20, grade_score: null }])),
    totals: { 九门: { percentile: 30, rank: 200 }, 五门: { percentile: 28, rank: 180 } },
  })
  const html = render({ rows: [row('期末考', '2026-06-28'), row('期中考', '2026-04-20')] })
  assert(html.includes('主科雷达（语数英 + 总分）'), '拆分 caption：主科')
  assert(html.includes('选科雷达'), '拆分 caption：选科')
  assert(html.includes('>九门总分<'), '高一无 3+3，总分轴取九门')
  assert(!html.includes('>五门总分<'), '五门优先级低于九门')
  // 两张图各有一组轴：主科图含英语、选科图含地理
  assert(html.includes('>英语<'))
  assert(html.includes('>地理<'))
  const svgCount = (html.match(/<svg /g) ?? []).length
  assert.equal(svgCount, 2, '拆两张雷达两个 SVG')
})

test('SSR 真渲染：无可画数据时给空态提示，不渲染图表', () => {
  const html = render({
    rows: [
      {
        exam_key: 'x|2026-01-01', exam_name: 'x', exam_date: '2026-01-01',
        subjects: { 语文: { percentile: null, grade_score: null } },
        totals: {},
      },
    ],
  })
  assert(!html.includes('<svg'), '无有效顶点不渲染 SVG')
  assert(html.includes('暂无可画雷达的成绩数据'))
})
