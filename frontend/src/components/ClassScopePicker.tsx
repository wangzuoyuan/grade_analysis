'use client'

import { useClassScope, carriedSuffix, type TeachingClass, formatTeachingClass } from '@/lib/class-scope'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { formatGradeLabel } from '@/lib/labels'

interface Props {
  /** 限定年级：只列出该年级的教学班；不传则列出全部年级。 */
  grade?: number
  className?: string
  /** 宽度/紧凑模式（顶栏用）。 */
  compact?: boolean
}

/** 班级范围选择器：全部（我教的班）+ 每个教学班。选中即更新全局 ClassScope。 */
export function ClassScopePicker({ grade, className, compact }: Props) {
  const { classes, current, setCurrent, loading } = useClassScope()
  const list = grade == null ? classes : classes.filter((c) => c.grade === grade)

  const byGrade = new Map<number, TeachingClass[]>()
  list.forEach((c) => {
    const arr = byGrade.get(c.grade) ?? []
    arr.push(c)
    byGrade.set(c.grade, arr)
  })
  const grades = Array.from(byGrade.keys()).sort()

  // 当前值是否在可选项里；不在则回退显示 placeholder（避免空白）
  const value = String(current)
  const inList = current === 'all' || list.some((c) => c.id === current)

  if (!loading && list.length === 0 && grade != null) {
    // 该年级尚无教学班：不渲染选择器（页面可自行引导去配置）
    return null
  }

  return (
    <Select value={inList ? value : undefined} onValueChange={(v) => setCurrent(v === 'all' ? 'all' : Number(v))}>
      <SelectTrigger className={cn(compact ? 'h-8 w-[150px] text-xs' : 'h-9 w-[170px] text-sm', className)}>
        <SelectValue placeholder={grade != null ? `${formatGradeLabel(grade)}·全部` : '班级范围'} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="all">全部（我教的班）</SelectItem>
        {grades.map((g) => (
          <SelectGroup key={g}>
            <SelectLabel>{g >= 1 && g <= 3 ? formatGradeLabel(g) : '教学班'}</SelectLabel>
            {byGrade.get(g)!.map((c) => (
              <SelectItem key={c.id} value={String(c.id)}>
                {c.label}
                {c.member_count ? `（${c.member_count}人）` : ''}
                {carriedSuffix(c)}
                {c.kind === '行政' ? '' : ''}
              </SelectItem>
            ))}
          </SelectGroup>
        ))}
      </SelectContent>
    </Select>
  )
}

/** 当前教学班的展示串（供侧栏/顶栏静态展示），如「高二·物A1」。 */
export function currentScopeLabel(current: number | 'all', classes: TeachingClass[]): string {
  if (current === 'all') {
    const names = new Set(classes.map((c) => c.carried_from_academic_year_name ?? null))
    const carried = names.size === 1 ? Array.from(names)[0] : null
    return `全部（${classes.length}个班）${carried ? ` · 延续自 ${carried} 学年` : ''}`
  }
  const tc = classes.find((c) => c.id === current)
  return `${formatTeachingClass(tc) ?? '—'}${carriedSuffix(tc)}`
}
