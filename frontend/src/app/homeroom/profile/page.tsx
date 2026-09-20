import { Suspense } from 'react'
import { HomeroomProfileView } from '@/components/students/HomeroomProfileView'
import { Skeleton } from '@/components/ui/skeleton'

export const metadata = {
  title: '学生档案 · 班主任工作台',
}

function ProfileLoading() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-10 w-48" />
      <Skeleton className="h-20 w-full" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
      <Skeleton className="h-64 w-full" />
    </div>
  )
}

export default function HomeroomProfilePage() {
  return (
    <Suspense fallback={<ProfileLoading />}>
      <HomeroomProfileView />
    </Suspense>
  )
}
