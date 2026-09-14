'use client'

import { useEffect, useState } from 'react'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'

type Status = { required: boolean; authed: boolean }

/**
 * 登录门禁：挂载时查 /api/auth/status。
 * - required=false（内网入口 / 未配密码）→ 直接放行，用户无感知。
 * - required=true 且未登录（外网域名入口）→ 整屏登录表单，验证通过再放行。
 * 真正的安全边界在后端中间件；本组件只负责体验。
 */
export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function refresh() {
    try {
      const res = await fetch('/api/auth/status', { cache: 'no-store' })
      setStatus(await res.json())
    } catch {
      // 后端不可达时不挡路，让页面自身的请求去暴露错误
      setStatus({ required: false, authed: true })
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError('')
    try {
      const res = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      if (res.ok) {
        setPassword('')
        await refresh()
      } else {
        setError('密码错误')
      }
    } catch {
      setError('网络错误，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  // 状态未知时先不渲染应用，避免内容闪现
  if (status === null) return null

  if (status.required && !status.authed) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <Card className="w-full max-w-sm p-6">
          <h1 className="mb-1 text-lg font-semibold text-slate-800">学情追踪 · 登录</h1>
          <p className="mb-4 text-sm text-slate-500">外网访问需要输入密码</p>
          <form onSubmit={onSubmit} className="space-y-3">
            <Input
              type="password"
              autoFocus
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="请输入访问密码"
            />
            {error && <p className="text-sm text-danger-600">{error}</p>}
            <Button type="submit" className="w-full" disabled={submitting || !password}>
              {submitting ? '登录中…' : '登录'}
            </Button>
          </form>
        </Card>
      </div>
    )
  }

  return <>{children}</>
}
