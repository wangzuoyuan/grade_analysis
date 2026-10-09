'use client'

/**
 * 仪表盘卡片级折叠（2026-09-30 用户反馈「每个板块长的有点厉害，多折叠一些」）。
 *
 * - useCardFold(key)：折叠状态本地记忆（localStorage `dashboard-fold:<key>`），
 *   未记录时用 defaultFolded；SSR/首帧按默认渲染，挂载后读存储再校正，
 *   绝不产生 hydration 不匹配。
 * - CardFoldToggle：卡头右侧的收起/展开按钮（chevron 旋转 + aria-expanded）。
 *
 * 仅仪表盘类看板使用；打印类页面（报告/家长会一页纸）不引入——折叠只在
 * 屏幕交互层生效，CardContent 卸载即不进打印。
 */

import { useEffect, useState } from 'react'
import { ChevronDown } from 'lucide-react'

import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

const STORAGE_PREFIX = 'dashboard-fold:'

export function useCardFold(key: string, defaultFolded = false) {
  // null = 尚未从 localStorage 读到（首帧按默认渲染）
  const [stored, setStored] = useState<boolean | null>(null)

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE_PREFIX + key)
      setStored(raw == null ? defaultFolded : raw === '1')
    } catch {
      setStored(defaultFolded)
    }
  }, [key, defaultFolded])

  const folded = stored ?? defaultFolded
  const toggle = () => {
    const next = !folded
    setStored(next)
    try {
      window.localStorage.setItem(STORAGE_PREFIX + key, next ? '1' : '0')
    } catch {
      // 存储不可用（隐私模式等）：仅本次会话内生效
    }
  }
  return { folded, toggle }
}

export function CardFoldToggle({
  folded,
  onToggle,
}: {
  folded: boolean
  onToggle: () => void
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className="h-7 w-7 shrink-0 p-0 print:hidden"
      onClick={onToggle}
      aria-expanded={!folded}
      title={folded ? '展开该板块' : '收起该板块'}
    >
      <ChevronDown
        className={cn('h-4 w-4 text-slate-400 transition-transform', !folded && 'rotate-180')}
        aria-hidden="true"
      />
    </Button>
  )
}
