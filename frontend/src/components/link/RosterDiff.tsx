'use client'

import type { LinkPreview } from '@/lib/api-v1'
import { AlertTriangle, CheckCircle2, School, UserPlus } from 'lucide-react'

import { Badge } from '@/components/ui/badge'

type RosterDiff = LinkPreview['roster_diff']
type StudentBrief = RosterDiff['both'][number]

interface RosterDiffProps {
  diff: RosterDiff
}

function studentLabel(s: StudentBrief): { name: string; alias: string | null; id: string } {
  return {
    name: s.name ?? '（未命名）',
    alias: s.alias && s.alias !== s.name ? s.alias : null,
    id: String(s.person_id),
  }
}

function StudentList({ students }: { students: StudentBrief[] }) {
  if (students.length === 0) {
    return <p className="px-3 py-6 text-center text-sm text-slate-400">无</p>
  }
  return (
    <ul className="divide-y divide-slate-100">
      {students.map((s) => {
        const info = studentLabel(s)
        return (
          <li key={info.id} className="flex items-baseline justify-between gap-2 px-3 py-2">
            <div className="min-w-0">
              <span className="text-sm font-medium text-slate-900">{info.name}</span>
              {info.alias ? (
                <span className="ml-1.5 text-xs text-slate-500">（{info.alias}）</span>
              ) : null}
            </div>
            <code className="shrink-0 font-mono text-xs text-slate-400">{info.id}</code>
          </li>
        )
      })}
    </ul>
  )
}

function DiffColumn({
  icon,
  title,
  tone,
  students,
  emptyText,
}: {
  icon: React.ReactNode
  title: string
  tone: 'success' | 'brand' | 'warning'
  students: StudentBrief[]
  emptyText: string
}) {
  const toneClass =
    tone === 'success'
      ? 'bg-success-50 text-success-600'
      : tone === 'brand'
        ? 'bg-brand-50 text-brand-600'
        : 'bg-warning-50 text-warning-600'
  return (
    <div className="flex min-w-0 flex-col rounded-lg border border-slate-200 bg-white">
      <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-3 py-2.5">
        <div className="flex min-w-0 items-center gap-1.5 text-sm font-medium text-slate-700">
          <span aria-hidden="true" className={`inline-flex h-5 w-5 items-center justify-center rounded ${toneClass}`}>
            {icon}
          </span>
          <span className="truncate">{title}</span>
        </div>
        <Badge variant="secondary" className="shrink-0 tabular-nums">
          {students.length}
        </Badge>
      </div>
      <div className="max-h-64 flex-1 overflow-y-auto">
        {students.length === 0 ? (
          <p className="px-3 py-6 text-center text-sm text-slate-400">{emptyText}</p>
        ) : (
          <StudentList students={students} />
        )}
      </div>
    </div>
  )
}

/**
 * 关联预览的名册差异三列：both（已确认配对）/ homeroom_only / teaching_only。
 * 契约：同名同号不自动配对，both 可能为空，此时 UI 表达“暂无已确认配对”。
 */
export function RosterDiffView({ diff }: RosterDiffProps) {
  const both = diff?.both ?? []
  const homeroomOnly = diff?.homeroom_only ?? []
  const teachingOnly = diff?.teaching_only ?? []
  const noConfirmedPairs = both.length === 0

  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-3">
        <DiffColumn
          icon={<CheckCircle2 className="h-3.5 w-3.5" />}
          title="两边都有（已确认配对）"
          tone="success"
          students={both}
          emptyText="暂无已确认配对"
        />
        <DiffColumn
          icon={<School className="h-3.5 w-3.5" />}
          title="仅班主任班"
          tone="brand"
          students={homeroomOnly}
          emptyText="无"
        />
        <DiffColumn
          icon={<UserPlus className="h-3.5 w-3.5" />}
          title="仅教学班"
          tone="warning"
          students={teachingOnly}
          emptyText="无"
        />
      </div>
      {noConfirmedPairs ? (
        <div
          role="status"
          className="flex items-start gap-2 rounded-lg border border-warning-300 bg-warning-50 p-3 text-sm text-warning-700"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <p>
            暂无已确认配对。同名同号学生<b>不会自动配对</b>，需逐人确认后才会计入两边都有；
            两侧独有的学生暂不共享数据。
          </p>
        </div>
      ) : null}
    </div>
  )
}
