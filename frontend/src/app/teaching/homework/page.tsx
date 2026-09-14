import { Suspense } from 'react'

import { HomeworkWorkspace } from '@/components/homework/HomeworkWorkspace'

export const metadata = {
  title: '作业跟进 · 教学工作台',
}

// UX05：HomeworkWorkspace 以 useSearchParams 跟随 ?tab=（同页深链/前进后退），
// 静态预渲染需要页面级 Suspense 边界。
export default function TeachingHomeworkPage() {
  return (
    <Suspense fallback={null}>
      <HomeworkWorkspace mode="teaching" />
    </Suspense>
  )
}
