// 教学工作台成绩分析「分班展示」契约测试（2026-10-16 用户需求：
// 成绩分析应分班展示）。要点：学生成绩表按教学班分组、名次在各班内
// 按同分同名次（min-rank）计算、组头带 人数/平均/名次范围/缺考，
// 不再把多班学生混排成一张表。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const page = readFileSync(new URL('../src/components/scores/TeachingScores.tsx', import.meta.url), 'utf8')

test('学生成绩表按教学班分组展示，组头带班级统计', () => {
  assert.match(page, /const classGroups = useMemo/, '必须存在分组计算')
  assert.match(page, /s\.class_label \?\? '未分班'/, '无班级归属的学生要有兜底分组')
  assert.match(page, /按教学班分组展示，名次在各班内按同分同名次计算/, '卡片说明须写明分班与班内名次口径')
  assert.match(page, /人 · 平均 /, '组头须有人数与平均分')
  assert.match(page, /缺考 /, '组头须提示缺考人数')
  assert.match(page, /名次（班内）/, '表列名必须是班内名次')
  assert.doesNotMatch(page, /const sortedStudents = useMemo/, '不得再保留多班混排的单一学生表排序')
})

test('班内名次为前端 min-rank：同分同名次、缺考无名次排后', () => {
  assert.match(page, /if \(prev == null \|\| r\.score !== prev\) rank = i \+ 1/, '同分同名次（min-rank）实现必须在场')
  assert.match(page, /r\.score != null[\s\S]*?sort\(\(x, y\) => \(y\.score as number\) - \(x\.score as number\)\)/, '名次只对有效分数计算')
  assert.match(page, /if \(ra != null && rb == null\) return -1[\s\S]*?if \(ra == null && rb != null\) return 1/, '无名次（缺考）排组尾')
})
