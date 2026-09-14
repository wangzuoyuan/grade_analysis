'use client'

import { useEffect } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { useWorkspace } from '@/lib/workspace'

type Destination = 'overview' | 'scores' | 'homework'

/** 退役旧无作用域页，保留深链但统一进入 v1 工作台页。 */
export function LegacyWorkspaceRedirect({ destination }: { destination: Destination }) {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { mode } = useWorkspace()
  const target = destination === 'overview' ? `/${mode}` : `/${mode}/${destination}`
  const urlMode = searchParams.get('ws')

  useEffect(() => {
    // 公共页首次进入时 WorkspaceProvider 会先把工作台写进 ?ws；等 URL
    // 成为明确事实源后再转，避免两个 replace 竞争而把用户留在旧页。
    if (urlMode !== mode) return
    router.replace(target)
  }, [router, target, urlMode, mode])

  return <p className="p-6 text-sm text-slate-500">正在进入{mode === 'homeroom' ? '班主任' : '教学'}工作台……</p>
}
