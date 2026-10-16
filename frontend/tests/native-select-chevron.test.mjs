// 原生 <select> 箭头防重叠契约测试（2026-10-16 文字与下拉箭头重叠修复）。
// 背景：@tailwindcss/forms（base 策略，见 tailwind.config.js）为原生 select 注入
// appearance:none + 右侧 chevron 背景图 + padding-right:2.5rem。组件若再加
// p-N / px-N 工具类（类选择器特异性高于插件元素选择器），右内边距会被压回
// 8/12px，箭头背景图原位不动 → 选中文字压在箭头上（2026-10-16 用户截图事故）。
// 契约：原生 select 的 className 不得包含全向/横向 padding 工具类；右侧留白
// 一律交给插件默认（pl-N 控制左内边距是允许的）。

import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const srcRoot = fileURLToPath(new URL('../src', import.meta.url))

function listTsxFiles(dir) {
  const out = []
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) out.push(...listTsxFiles(full))
    else if (name.endsWith('.tsx')) out.push(full)
  }
  return out
}

/** 会压掉插件右内边距的工具类：p-2 / px-3 / p-[8px] / px-[10px] …（pr-N 单独放行，见下方阈值检查） */
const SIDE_PADDING_RE = /\bp(?:x)?-(?:\[[^\]]+\]|\d+)\b/g

/** 提取每个 <select …> 开标签全文：结束符是「前一个字符不是 = 的 >」，以免被属性里的 => 箭头函数截断 */
function selectTags(text) {
  const tags = []
  let start = text.indexOf('<select')
  while (start !== -1) {
    for (let i = start + 7; i < text.length; i++) {
      if (text[i] === '>' && text[i - 1] !== '=') {
        tags.push(text.slice(start, i + 1))
        break
      }
    }
    start = text.indexOf('<select', start + 1)
  }
  return tags
}

function effectiveClassNames(fileText, tagText) {
  // className="字面量" 直接取；className={表达式} 则解析其中引用的字符串常量
  const attr = tagText.match(/className=\{?["'`]([^"'`]+)["'`]\}?/)
  if (attr) return [attr[1]]
  const expr = tagText.match(/className=\{([^}]+)\}/)
  if (!expr) return []
  const names = []
  for (const ident of expr[1].match(/[A-Za-z_$][\w$]*/g) ?? []) {
    const decl = fileText.match(new RegExp(`const ${ident}\\s*=\\s*["'\\\`]([^"\\'\\\`]+)["'\\\`]`))
    if (decl) names.push(decl[1])
  }
  return names
}

test('tailwind.config.js 必须加载 @tailwindcss/forms（原生 select 箭头与右内边距的来源）', () => {
  const config = readFileSync(new URL('../tailwind.config.js', import.meta.url), 'utf8')
  assert.match(config, /@tailwindcss\/forms/, 'forms 插件被移除会让原生 select 退回浏览器默认外观')
})

test('全部原生 <select> 的 className 不得携带 p-N/px-N（会覆盖插件 padding-right:2.5rem 导致文字压箭头）', () => {
  const offenders = []
  let seen = 0
  for (const file of listTsxFiles(srcRoot)) {
    const text = readFileSync(file, 'utf8')
    for (const tag of selectTags(text)) {
      seen++
      for (const cls of effectiveClassNames(text, tag)) {
        const hit = cls.match(SIDE_PADDING_RE)
        if (hit) {
          offenders.push(
            `${file.replace(srcRoot, '')} → <select …> className 含 ${hit.join('、')}：「${cls}」`
          )
        }
        // 显式 pr-N 只放行足够容纳箭头区（右缘 8px 起、约 24px 宽）的档位
        const pr = cls.match(/\bpr-(\d+)\b/)
        if (pr && Number(pr[1]) < 9) {
          offenders.push(`${file.replace(srcRoot, '')} → pr-${pr[1]} 留白不足（需 ≥ pr-9）：「${cls}」`)
        }
      }
    }
  }
  assert.ok(seen >= 17, `扫描到的原生 select 数量异常（${seen} < 17），扫描逻辑可能失效`)
  assert.deepEqual(offenders, [], `发现会与箭头重叠的 select 内边距写法：\n${offenders.join('\n')}`)
})
