'use client'

import { useCallback, useEffect, useState } from 'react'
import { DatabaseBackup, Download } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { MoreToggle } from '@/components/ui/more-toggle'

interface BackupItem {
  filename: string
  size: number
  created: string
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export default function BackupCard() {
  const [backups, setBackups] = useState<BackupItem[]>([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)
  // 2026-11-09 P2 修复配套：恢复（整库覆盖）二次凭据。开启登录后后端要求
  // confirm_password 与登录密码一致；未开启登录时无需填写。
  const [restoreTarget, setRestoreTarget] = useState<string | null>(null)
  const [restorePassword, setRestorePassword] = useState('')

  const load = useCallback(async () => {
    const data = await fetch('/api/backups').then((r) => r.json())
    setBackups(Array.isArray(data) ? data : [])
  }, [])

  useEffect(() => {
    load().catch(() => {})
  }, [load])

  async function backup() {
    setBusy(true)
    setMsg(null)
    try {
      const res = await fetch('/api/backup', { method: 'POST' })
      const data = await res.json()
      if (data.success) {
        setMsg(`已备份：${data.filename}`)
        await load()
      }
    } finally {
      setBusy(false)
    }
  }

  async function restore(filename: string, confirmPassword: string) {
    setBusy(true)
    setMsg(null)
    try {
      const res = await fetch('/api/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename, confirm_password: confirmPassword }),
      })
      const data = await res.json()
      if (res.ok && data.success) {
        setMsg('已恢复。页面数据以该备份为准，建议刷新页面；开启登录时需重新登录一次。')
        setRestoreTarget(null)
        setRestorePassword('')
        await load()
      } else {
        // 401 = 确认密码错误或缺失（后端 detail 优先展示）；其他失败沿用通用文案
        setMsg(typeof data.detail === 'string' && data.detail ? data.detail : '恢复失败')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="flex items-center gap-2 text-base">
          <DatabaseBackup className="h-4 w-4" />
          数据备份
        </CardTitle>
        <Button size="sm" onClick={backup} disabled={busy}>
          立即备份
        </Button>
      </CardHeader>
      <CardContent className="space-y-2">
        {msg && <p className="text-sm text-success-600">{msg}</p>}
        {backups.length === 0 ? (
          <p className="text-sm text-slate-400">暂无备份。点「立即备份」生成一份。</p>
        ) : (
          <div className="space-y-1.5">
            {(expanded ? backups : backups.slice(0, 6)).map((b) => (
              <div key={b.filename} className="space-y-1.5">
                <div className="flex items-center justify-between rounded-md border border-slate-100 px-3 py-1.5 text-sm">
                  <div className="min-w-0">
                    <span className="font-mono text-xs text-slate-600">{b.filename}</span>
                    <span className="ml-2 text-xs text-slate-400">
                      {b.created.replace('T', ' ')} · {fmtSize(b.size)}
                    </span>
                  </div>
                  <div className="flex shrink-0 items-center gap-3">
                    <a
                      href={`/api/backup/${b.filename}/download`}
                      className="text-slate-400 hover:text-slate-700"
                      aria-label="下载"
                    >
                      <Download className="h-4 w-4" />
                    </a>
                    <button
                      onClick={() => {
                        setRestoreTarget(restoreTarget === b.filename ? null : b.filename)
                        setRestorePassword('')
                        setMsg(null)
                      }}
                      disabled={busy}
                      className="text-xs text-brand-700 hover:underline"
                      aria-expanded={restoreTarget === b.filename}
                    >
                      恢复
                    </button>
                  </div>
                </div>
                {restoreTarget === b.filename ? (
                  <div className="rounded-md border border-amber-200 bg-amber-50/60 px-3 py-2">
                    <p className="mb-1.5 text-xs text-slate-600">
                      用「{b.filename}」覆盖当前数据库。当前数据会先自动备份；恢复即时生效无需重启，开启登录时所有会话将失效（需重新登录一次）。
                    </p>
                    <div className="flex flex-wrap items-center gap-2">
                      <input
                        type="password"
                        value={restorePassword}
                        onChange={(e) => setRestorePassword(e.target.value)}
                        placeholder="确认密码（开启登录后必填）"
                        aria-label="恢复确认密码"
                        className="h-8 w-48 rounded-md border border-slate-200 px-2 text-xs"
                      />
                      <Button
                        size="sm"
                        onClick={() => restore(b.filename, restorePassword)}
                        disabled={busy}
                      >
                        {busy ? '恢复中…' : '确认恢复'}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          setRestoreTarget(null)
                          setRestorePassword('')
                        }}
                        disabled={busy}
                      >
                        取消
                      </Button>
                    </div>
                  </div>
                ) : null}
              </div>
            ))}
            <MoreToggle
              hiddenCount={backups.length - 6}
              unit="份备份"
              expanded={expanded}
              onToggle={() => setExpanded((v) => !v)}
            />
          </div>
        )}
        <p className="text-xs text-slate-400">
          备份存于数据目录旁的 backups/（本地默认 ~/.exam-tracker-backups，Docker 部署为 /data/backups）；恢复前会自动再快照一次当前库，误操作可回退。
        </p>
      </CardContent>
    </Card>
  )
}
