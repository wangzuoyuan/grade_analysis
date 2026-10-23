import type { ReactNode } from 'react'
import { ArrowDown, ArrowUp } from 'lucide-react'

import { cn } from '@/lib/utils'

export function ScoreSortButton({
  children, label, active, ascending = false, onClick,
}: {
  children: ReactNode
  label: string
  active: boolean
  ascending?: boolean
  onClick: () => void
}) {
  const Icon = ascending ? ArrowUp : ArrowDown
  return (
    <button
      type="button"
      aria-label={`${label}，${ascending ? '按学号升序' : '从高到低排序'}`}
      title={`${label} · ${ascending ? '按学号升序' : '从高到低'}`}
      onClick={onClick}
      className={cn(
        'inline-flex min-h-8 w-full items-center justify-center gap-1 rounded px-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600',
        active ? 'font-semibold' : 'hover:bg-white/30',
      )}
    >
      {children}
      <Icon aria-hidden="true" className={cn('h-3 w-3 shrink-0 print:hidden', !active && 'opacity-50')} />
    </button>
  )
}
