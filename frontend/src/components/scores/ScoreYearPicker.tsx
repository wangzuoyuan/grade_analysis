'use client'

import { useEffect, useState } from 'react'

import { listAcademicYears, type AcademicYear } from '@/lib/api-v1'
import { useWorkspace, type WorkspaceFilter } from '@/lib/workspace'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

const YEAR_DEFAULT = '__latest__'

/** 成绩页自己的学年入口：真实迁移后历史考试不能依赖“先去首页切学年”才能发现。 */
export function ScoreYearPicker() {
  const { mode, filter, setFilter } = useWorkspace()
  const [years, setYears] = useState<AcademicYear[]>([])
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let stale = false
    listAcademicYears()
      .then((result) => {
        if (stale) return
        setYears(result.years ?? [])
        setFailed(false)
      })
      .catch(() => {
        if (stale) return
        setYears([])
        setFailed(true)
      })
    return () => {
      stale = true
    }
  }, [])

  const changeYear = (value: string) => {
    const patch: Partial<WorkspaceFilter> = {
      academic_year_id: value === YEAR_DEFAULT ? undefined : Number(value),
      term_id: undefined,
    }
    // 班级 id 属于具体学年；换年时必须回到该学年的默认绑定/全部所教班，
    // 不能携带上一学年的 class id 造成 404 或空页面。
    if (mode === 'teaching') patch.teaching_class_id = 'all'
    else patch.class_id = undefined
    setFilter(patch)
  }

  return (
    <div className="space-y-1">
      <label className="text-xs font-medium text-slate-500">学年</label>
      <Select
        value={filter.academic_year_id != null ? String(filter.academic_year_id) : YEAR_DEFAULT}
        onValueChange={changeYear}
      >
        <SelectTrigger className="h-9 w-[170px] text-sm" aria-label="成绩学年">
          <SelectValue placeholder="选择学年" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={YEAR_DEFAULT}>当前学年（默认）</SelectItem>
          {years.map((year) => (
            <SelectItem key={year.id} value={String(year.id)}>
              {year.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {failed ? <p className="text-[10px] text-amber-600">学年列表加载失败，可刷新后重试</p> : null}
    </div>
  )
}
