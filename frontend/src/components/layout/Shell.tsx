'use client'

import { useEffect, useState } from 'react'
import { Sidebar, type TeacherSummary } from './Sidebar'
import { Topbar } from './Topbar'

interface RawTeacher {
  name?: string | null
}

export function Shell({ children }: { children: React.ReactNode }) {
  const [teacher, setTeacher] = useState<TeacherSummary | null>(null)

  useEffect(() => {
    let aborted = false
    fetch('/api/teacher')
      .then((r) => (r.ok ? r.json() : null))
      .then((data: RawTeacher | null) => {
        if (!aborted) setTeacher(data ? { name: data.name ?? null } : null)
      })
      .catch(() => {
        if (!aborted) setTeacher(null)
      })
    return () => {
      aborted = true
    }
  }, [])

  function handleNameChange(name: string) {
    setTeacher((prev) => (prev ? { ...prev, name: name || null } : prev))
  }

  return (
    <div className="min-h-screen print:min-h-0 print:bg-white">
      <Sidebar teacher={teacher} onNameChange={handleNameChange} />
      <div className="md:pl-60 print:pl-0">
        <Topbar teacher={teacher} />
        <main>
          <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8 print:max-w-none print:px-0 print:py-0">{children}</div>
        </main>
      </div>
    </div>
  )
}
