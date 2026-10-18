'use client'

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { fetchClasses as fetchV1Classes, fetchSharedConfig } from '@/lib/api-v1'
import { useWorkspace } from '@/lib/workspace'

/** 一个教学班（老师配置的班：高一=行政班数字，高二/三可为走班名如「物A1」）。 */
export interface TeachingClass {
  id: number
  /** 年级 1/2/3；后端未给出（未知）时为 0 哨兵，展示层按普通「教学班」处理。 */
  grade: number
  label: string
  subject?: string | null
  kind: string // 行政 / 教学
  note?: string | null
  sort_order: number
  member_count: number
  created_at?: string | null
  /** 未换届自动延续：本学年尚无教学班时，目录延续自该名称的更早学年。 */
  carried_from_academic_year_name?: string | null
}

type ScopeValue = number | 'all'

interface ClassScopeContextValue {
  classes: TeachingClass[]
  loading: boolean
  /** 当前选中的教学班 id，或 'all'（我教的所有班并集）。 */
  current: ScopeValue
  currentClass: TeachingClass | null
  setCurrent: (v: ScopeValue) => void
  refresh: () => void
}

const ClassScopeContext = createContext<ClassScopeContextValue | null>(null)

export function ClassScopeProvider({ children }: { children: ReactNode }) {
  const { filter, setFilter } = useWorkspace()
  const [classes, setClasses] = useState<TeachingClass[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshNonce, setRefreshNonce] = useState(0)
  const current: ScopeValue = typeof filter.teaching_class_id === 'number' ? filter.teaching_class_id : 'all'

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    const load = async () => {
      try {
        const config = await fetchSharedConfig()
        const academicYearId = filter.academic_year_id ?? config.current_academic_year?.id
        if (academicYearId == null) {
          if (!cancelled) setClasses([])
          return
        }
        const catalog = await fetchV1Classes(academicYearId)
        if (!cancelled) {
          setClasses(catalog.teaching.map((item, index) => ({
            id: item.class_id,
            // 透传后端真实年级（14.4）：未知年级保持 0 哨兵，展示层按普通
            // 「教学班」分组渲染，不猜测年级
            grade: item.grade ?? 0,
            label: item.label,
            subject: item.subject,
            kind: '教学',
            sort_order: index,
            member_count: 0,
            carried_from_academic_year_name: item.carried_from_academic_year_name ?? null,
          })))
        }
      } catch {
        if (!cancelled) setClasses([])
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => { cancelled = true }
  }, [filter.academic_year_id, refreshNonce])

  const setCurrent = useCallback((v: ScopeValue) => {
    setFilter({ teaching_class_id: v })
  }, [setFilter])

  const refresh = useCallback(() => {
    setRefreshNonce((value) => value + 1)
  }, [])

  const currentClass = current === 'all' ? null : classes.find((c) => c.id === current) ?? null

  const value = useMemo(() => (
    { classes, loading, current, currentClass, setCurrent, refresh }
  ), [classes, loading, current, currentClass, setCurrent, refresh])

  return (
    <ClassScopeContext.Provider
      value={value}
    >
      {children}
    </ClassScopeContext.Provider>
  )
}

export function useClassScope(): ClassScopeContextValue {
  const ctx = useContext(ClassScopeContext)
  if (!ctx) throw new Error('useClassScope must be used within ClassScopeProvider')
  return ctx
}

/** 把 {grade,label} 拼成展示串，如「高二·物A1」「高一·1」。 */
export function formatTeachingClass(tc: { grade: number; label: string } | null | undefined): string | null {
  if (!tc) return null
  if (![1, 2, 3].includes(tc.grade)) return tc.label
  const g = { 1: '高一', 2: '高二', 3: '高三' }[tc.grade]
  return `${g}·${tc.label}`
}

/** 列表/学生旁徽章用的简短标签。 */
export function formatClassChip(label: string | null | undefined): string | null {
  if (!label) return null
  return label
}

/** 延续展示后缀：如「· 延续自 2025-2026 学年」；无延续返回空串。 */
export function carriedSuffix(tc: { carried_from_academic_year_name?: string | null } | null | undefined): string {
  const name = tc?.carried_from_academic_year_name
  return name ? ` · 延续自 ${name} 学年` : ''
}
