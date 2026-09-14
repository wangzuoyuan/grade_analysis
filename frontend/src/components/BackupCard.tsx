'use client'

import { useCallback, useEffect, useState } from 'react'
import { DatabaseBackup, Download } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'

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

  async function restore(filename: string) {
    if (
      !confirm(
        `确定用「${filename}」覆盖当前数据库吗？\n当前数据会先自动备份，但恢复后需要重启应用（run.py stop && start）。`
      )
    )
      return
    setBusy(true)
    setMsg(null)
    try {
      const res = await fetch('/api/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename }),
      })
      const data = await res.json()
      if (data.success) {
        setMsg('已恢复，请执行 run.py stop && start 重启应用。')
        await load()
      } else {
        setMsg('恢复失败')
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
            {backups.slice(0, 6).map((b) => (
              <div
                key={b.filename}
                className="flex items-center justify-between rounded-md border border-slate-100 px-3 py-1.5 text-sm"
              >
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
                    onClick={() => restore(b.filename)}
                    disabled={busy}
                    className="text-xs text-brand-700 hover:underline"
                  >
                    恢复
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
        <p className="text-xs text-slate-400">
          备份存于 ~/.exam-tracker-backups（不会被「初始化」清空）；初始化前也会自动快照。
        </p>
      </CardContent>
    </Card>
  )
}
