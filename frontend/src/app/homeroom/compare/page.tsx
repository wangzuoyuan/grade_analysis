import { Suspense } from 'react'
import { HomeroomCompare } from '@/components/scores/HomeroomCompare'
import { Skeleton } from '@/components/ui/skeleton'

export const metadata = {
  title: '班级对比 · 班主任工作台',
}

function CompareLoading() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-10 w-48" />
      <Skeleton className="h-20 w-full" />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        <Skeleton className="h-[420px] lg:col-span-3" />
        <Skeleton className="h-[420px] lg:col-span-2" />
      </div>
    </div>
  )
}

export default function HomeroomComparePage() {
  return (
    <Suspense fallback={<CompareLoading />}>
      <HomeroomCompare />
    </Suspense>
  )
}
