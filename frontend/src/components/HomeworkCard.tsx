'use client'

import { useEffect, useState } from 'react'
import { CheckCircle2, History, NotebookPen, Sparkles } from 'lucide-react'

import {
  homeworkStudentEvents,
  type HomeworkScopeQuery,
  type HomeworkStudentEvent,
  type HomeworkStudentResponse,
  type WorkspaceMode,
} from '@/lib/api-v1'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { MoreToggle } from '@/components/ui/more-toggle'
import { homeworkStatusLabel } from '@/components/homework/shared'

function isAttendanceAnomaly(e: HomeworkStudentEvent): boolean {
  if (e.subject === '考勤') {
    return (
      e.status === 'missing' ||
      e.status === 'excused' ||
      Boolean(
        e.evaluation &&
          (e.evaluation.includes('迟到') ||
            e.evaluation.includes('没来') ||
            e.evaluation.includes('早退') ||
            e.evaluation.includes('缺席')),
      )
    )
  }
  return Boolean(
    e.evaluation &&
      (e.evaluation.includes('迟到') ||
        e.evaluation.includes('没来') ||
        e.evaluation.includes('早退') ||
        e.evaluation.includes('缺席')),
  )
}

function isAnomalyEvent(e: HomeworkStudentEvent): boolean {
  if (e.subject === '考勤') {
    return isAttendanceAnomaly(e)
  }
  if (e.status === 'missing' || e.status === 'excused') return true
  if (
    e.evaluation &&
    (e.evaluation.includes('迟到') ||
      e.evaluation.includes('没来') ||
      e.evaluation.includes('忘带') ||
      e.evaluation.includes('没带') ||
      e.evaluation.includes('早退') ||
      e.evaluation.includes('缺席'))
  ) {
    return true
  }
  return false
}

/**
 * 解析作业内容展示：
 * 1. 考勤批次显示为「出勤登记」；
 * 2. 若显式指定了具体作业名称（如电阻定律），显示具体名称；
 * 3. 若未指定种类（或为日常作业/legacy），但录入了具体内容（如试卷订正、作文、练习册），显示该内容；
 * 4. 普通作业且无特别说明：显示「—」，坚决不显示「日常作业」或「历史导入」。
 */
function resolveHomeworkContent(r: HomeworkStudentEvent): string {
  if (r.subject === '考勤') return '出勤登记'

  // 1. 检查是否有明确的作业种类（排除 legacy, 日常作业, —, 空值）
  const hasSpecificType = Boolean(
    r.homework_type &&
      r.homework_type !== 'legacy' &&
      r.homework_type !== '日常作业' &&
      r.homework_type !== '—',
  )
  if (hasSpecificType) {
    return r.homework_type
  }

  // 2. 检查是否有录入的具体作业内容（清洗掉纯出勤状态词）
  let cleanEval = (r.evaluation || '').trim()
  const pureAttendanceWords = ['迟到', '没来', '未到校', '请假', '正常', '早退', '缺席']
  if (pureAttendanceWords.includes(cleanEval)) {
    cleanEval = ''
  }

  if (cleanEval) {
    return cleanEval
  }

  return '—'
}

/** 学生作业缺交卡（P8-UXFIX：改接 /api/v1/homework/students/{person_id} 事件流 + streaks）。 */
export default function HomeworkCard({
  mode,
  personId,
  scopeQ = {},
}: {
  mode: WorkspaceMode
  personId: number
  scopeQ?: HomeworkScopeQuery
}) {
  const [allHistory, setAllHistory] = useState(false)
  const [showAllEvents, setShowAllEvents] = useState(false)
  const [eventsExpanded, setEventsExpanded] = useState(false)
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null)
  const [data, setData] = useState<HomeworkStudentResponse | null>(null)
  const [loaded, setLoaded] = useState(false)

  // 切换人员或历史口径时重置筛选
  useEffect(() => {
    setSelectedCategory(null)
  }, [personId, allHistory])

  useEffect(() => {
    let cancelled = false
    // 契约基线：homeworkStudentEvents(personId, mode, scopeQ)
    const effectiveQ = allHistory ? { ...scopeQ, all_history: true } : scopeQ
    homeworkStudentEvents(personId, mode, effectiveQ)
      .then((r) => {
        if (!cancelled) setData(r)
      })
      .catch(() => {
        if (!cancelled) setData(null)
      })
      .finally(() => {
        if (!cancelled) setLoaded(true)
      })
    return () => {
      cancelled = true
    }
  }, [personId, mode, scopeQ, allHistory])

  if (loaded && (!data || !data.events || data.events.length === 0)) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base flex items-center gap-2">
              <NotebookPen className="h-4 w-4 text-slate-600" />
              作业缺交记录
            </CardTitle>
            <div className="flex items-center gap-1 bg-slate-100 p-0.5 rounded-lg text-xs">
              <button
                type="button"
                onClick={() => setAllHistory(false)}
                className={`px-2.5 py-1 rounded transition-colors ${
                  !allHistory ? 'bg-white text-slate-800 font-medium shadow-sm' : 'text-slate-500 hover:text-slate-800'
                }`}
              >
                本学年
              </button>
              <button
                type="button"
                onClick={() => setAllHistory(true)}
                className={`px-2.5 py-1 rounded transition-colors ${
                  allHistory ? 'bg-white text-slate-800 font-medium shadow-sm' : 'text-slate-500 hover:text-slate-800'
                }`}
              >
                全部历史
              </button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2.5 rounded-lg bg-emerald-50/80 border border-emerald-200/70 p-4 text-sm text-emerald-800">
            <CheckCircle2 className="h-5 w-5 text-emerald-600 flex-shrink-0" />
            <div>
              <div className="font-medium">
                {allHistory ? '该生无任何作业缺交历史记录' : '本学年作业表现优异，暂无缺交记录'}
              </div>
              <div className="text-xs text-emerald-700/80 mt-0.5">
                {allHistory ? '历史档案中所有作业均正常完成。' : '当前学年作业收交齐整，各项学科均无缺交。'}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    )
  }

  const events = data?.events ?? []
  // 排除考勤出勤批次，仅统计纯学科作业缺交
  const missing = events.filter((e) => e.status === 'missing' && e.subject !== '考勤')
  const excused = events.filter((e) => e.status === 'excused')
  const attendanceAnomalies = events.filter(isAttendanceAnomaly)

  // 胶囊分类：班主任端优先统计学科（语文、数学、英语...）；教学端按作业内容
  const categoryStats = new Map<string, number>()
  for (const e of missing) {
    let key = e.subject
    if (mode === 'teaching') {
      const content = resolveHomeworkContent(e)
      key = content === '—' ? '常规作业' : content
    }
    categoryStats.set(key, (categoryStats.get(key) ?? 0) + 1)
  }

  const streaks = data?.streaks
  const streakUnknown = streaks == null || streaks.current_missing_streak == null

  // 事件按日期降序（最新在最前）排列
  const sortedEvents = [...events].sort(
    (a, b) => b.assigned_date.localeCompare(a.assigned_date) || b.assignment_id - a.assignment_id,
  )

  // 根据选中的学科/分类过滤
  const filteredEvents = selectedCategory
    ? sortedEvents.filter((e) => {
        if (mode === 'homeroom') {
          return e.subject === selectedCategory
        }
        const content = resolveHomeworkContent(e)
        const key = content === '—' ? '常规作业' : content
        return key === selectedCategory || content === selectedCategory
      })
    : sortedEvents

  const anomaliesList = filteredEvents.filter(isAnomalyEvent)
  const displayEvents = showAllEvents ? filteredEvents : anomaliesList

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base flex items-center gap-2">
            <NotebookPen className="h-4 w-4 text-slate-700" />
            作业缺交记录
            {allHistory && (
              <Badge className="border-transparent bg-amber-50 text-amber-700 hover:bg-amber-100 text-[11px] font-normal">
                全部历史累计
              </Badge>
            )}
          </CardTitle>
          <div className="flex items-center gap-1 bg-slate-100 p-0.5 rounded-lg text-xs">
            <button
              type="button"
              onClick={() => {
                setAllHistory(false)
                setShowAllEvents(false)
              }}
              className={`px-2.5 py-1 rounded transition-colors ${
                !allHistory ? 'bg-white text-slate-800 font-medium shadow-sm' : 'text-slate-500 hover:text-slate-800'
              }`}
            >
              本学年
            </button>
            <button
              type="button"
              onClick={() => {
                setAllHistory(true)
                setShowAllEvents(false)
              }}
              className={`px-2.5 py-1 rounded transition-colors ${
                allHistory ? 'bg-white text-slate-800 font-medium shadow-sm' : 'text-slate-500 hover:text-slate-800'
              }`}
            >
              全部历史
            </button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* 本学年全勤良好卡片 */}
        {!allHistory && missing.length === 0 ? (
          <div className="flex items-center justify-between rounded-lg bg-emerald-50/80 border border-emerald-200/70 p-3.5 text-sm text-emerald-800">
            <div className="flex items-center gap-2.5">
              <CheckCircle2 className="h-5 w-5 text-emerald-600 flex-shrink-0" />
              <div>
                <span className="font-semibold text-emerald-900">本学年作业良好</span>
                <span className="ml-2 text-xs text-emerald-700">暂无任何学科作业缺交</span>
              </div>
            </div>
            {excused.length > 0 && (
              <span className="text-xs text-emerald-700 bg-emerald-100/60 px-2 py-0.5 rounded">
                请假免交 {excused.length} 次
              </span>
            )}
          </div>
        ) : (
          <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
            <div>
              <span className="text-3xl font-semibold text-slate-900">{missing.length}</span>
              <span className="ml-1 text-sm text-slate-500">次缺交</span>
            </div>
            {excused.length > 0 && (
              <div className="text-sm text-slate-500">请假 {excused.length} 次</div>
            )}
            {attendanceAnomalies.length > 0 && (
              <div className="text-sm text-amber-600">迟到/出勤异常 {attendanceAnomalies.length} 次</div>
            )}
            <div className="text-sm text-slate-500">
              {streakUnknown ? (
                <Badge className="border-transparent bg-slate-100 text-slate-500">连续性未知</Badge>
              ) : (
                <>
                  当前连续缺交{' '}
                  <span className="font-medium text-slate-800">
                    {streaks!.current_missing_streak}
                  </span>{' '}
                  次
                </>
              )}
            </div>
          </div>
        )}

        {categoryStats.size > 0 ? (
          <div className="flex flex-wrap items-center gap-2">
            {[...categoryStats.entries()].map(([key, count]) => {
              const isSelected = selectedCategory === key
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => setSelectedCategory((prev) => (prev === key ? null : key))}
                  title={isSelected ? '点击取消筛选' : `点击仅查看【${key}】缺交记录`}
                  className={`rounded-md px-2.5 py-1 text-xs flex items-center gap-1.5 transition-all cursor-pointer border ${
                    isSelected
                      ? 'bg-rose-50 border-rose-300 text-rose-900 font-medium shadow-sm ring-2 ring-rose-200/80'
                      : 'bg-slate-100 hover:bg-slate-200/80 border-slate-200/70 text-slate-700 hover:border-slate-300'
                  }`}
                >
                  <span>{key}</span>
                  <span
                    className={`font-semibold px-1.5 py-0.2 rounded text-[11px] ${
                      isSelected
                        ? 'bg-rose-600 text-white'
                        : 'text-rose-600 bg-rose-50'
                    }`}
                  >
                    缺 {count}
                  </span>
                </button>
              )
            })}
            {selectedCategory && (
              <button
                type="button"
                onClick={() => setSelectedCategory(null)}
                className="text-xs text-slate-500 hover:text-slate-800 underline px-1 py-0.5 ml-1 transition-colors"
              >
                清除筛选
              </button>
            )}
          </div>
        ) : !allHistory ? (
          <p className="text-xs text-slate-400">本学年暂无学科缺交记录</p>
        ) : (
          <p className="text-sm text-slate-400">暂无缺交记录</p>
        )}

        {/* 明细表格 */}
        {events.length > 0 && (
          <details className="border-t border-slate-100 pt-3" open>
            <summary className="cursor-pointer text-xs font-medium text-slate-600 flex items-center justify-between">
              <span className="flex items-center gap-2">
                <span>近期明细</span>
                <span className="text-slate-400 font-normal">
                  {selectedCategory && (
                    <span className="text-rose-600 font-medium mr-1">
                      已筛选【{selectedCategory}】
                    </span>
                  )}
                  {showAllEvents
                    ? `（显示全部 ${displayEvents.length} 条记录）`
                    : `（仅展示 ${displayEvents.length} 条异常记录）`}
                </span>
              </span>
              <button
                type="button"
                onClick={(e) => {
                  e.preventDefault()
                  setShowAllEvents((v) => !v)
                }}
                className="text-xs text-blue-600 hover:text-blue-700 underline font-normal"
              >
                {showAllEvents ? '仅看异常' : `查看全部(${filteredEvents.length}条)`}
              </button>
            </summary>

            {displayEvents.length === 0 ? (
              <div className="py-6 text-center text-xs text-slate-400 flex flex-col items-center justify-center gap-1.5">
                <span>{selectedCategory ? `【${selectedCategory}】暂无对应明细记录` : '当前分类下无异常事件'}</span>
                {selectedCategory && (
                  <button
                    type="button"
                    onClick={() => setSelectedCategory(null)}
                    className="text-blue-600 hover:text-blue-700 underline"
                  >
                    查看全部学科
                  </button>
                )}
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="mt-2 w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-slate-400 border-b border-slate-100">
                      <th className="py-1.5 font-normal">日期</th>
                      <th className="py-1.5 font-normal">学科</th>
                      <th className="py-1.5 font-normal">作业内容</th>
                      <th className="py-1.5 font-normal">状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(eventsExpanded ? displayEvents : displayEvents.slice(0, 30)).map((r, i) => {
                      const isMissing = r.status === 'missing' && r.subject !== '考勤'
                      const isExcused = r.status === 'excused'
                      const isAttendance = isAttendanceAnomaly(r)
                      const isNormalAttendance = r.subject === '考勤' && !isAttendance
                      const content = resolveHomeworkContent(r)

                      return (
                        <tr
                          key={`${r.assignment_id}-${i}`}
                          className={`border-t border-slate-50 transition-colors ${
                            isMissing ? 'bg-rose-50/30' : ''
                          }`}
                        >
                          <td className="py-1.5 text-slate-500 tabular-nums whitespace-nowrap text-xs">
                            {r.assigned_date}
                          </td>
                          <td className="py-1.5 font-medium text-slate-800 whitespace-nowrap">
                            {r.subject}
                          </td>
                          <td className="py-1.5 text-slate-600 whitespace-nowrap text-xs">
                            {content}
                          </td>
                          <td className="py-1.5 whitespace-nowrap">
                            {isMissing ? (
                              <span className="text-xs font-medium text-rose-600">缺交</span>
                            ) : isExcused ? (
                              <span className="text-xs text-slate-500">请假免交</span>
                            ) : isAttendance ? (
                              <span className="text-xs font-medium text-amber-600">
                                {r.evaluation || (r.status === 'missing' ? '未到校' : '迟到')}
                              </span>
                            ) : isNormalAttendance ? (
                              <span className="text-xs font-medium text-emerald-600">正常出勤</span>
                            ) : (
                              <span className="text-xs text-emerald-600">
                                {homeworkStatusLabel(r.status)}
                              </span>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <MoreToggle
              hiddenCount={displayEvents.length - 30}
              unit="条记录"
              expanded={eventsExpanded}
              onToggle={() => setEventsExpanded((v) => !v)}
            />
          </details>
        )}

        <div className="flex items-center justify-between text-xs text-slate-400 pt-1">
          <span>
            {allHistory
              ? '当前展示跨学年所有历史记录，按事件逐次统计。'
              : '默认仅统计当前学年记录；可切换「全部历史」查看往期累计。'}
          </span>
        </div>
      </CardContent>
    </Card>
  )
}
