'use client'

import { usePathname } from 'next/navigation'
import Link from 'next/link'
import { Menu, MessageSquare, ChevronRight, Settings } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetTrigger } from '@/components/ui/sheet'
import { SidebarContent, type TeacherSummary } from './Sidebar'
import { ClassScopePicker } from '@/components/ClassScopePicker'
import { useWorkspace, workspaceHref, WorkspaceSwitcher } from '@/lib/workspace'

const SEGMENT_LABELS: Record<string, string> = {
  '': '仪表盘',
  homeroom: '班主任工作台',
  teaching: '教学工作台',
  scores: '成绩分析',
  profile: '学生档案',
  students: '学生信息',
  rollover: '换届',
  members: '班级信息',
  upload: '数据上传',
  compare: '班级对比',
  exam: '考试列表',
  student: '学生档案',
  homework: '作业跟进',
  settings: '学期设置',
  classes: '班级配置',
  manage: '记录管理',
  warnings: '缺交预警',
  correlation: '缺交 × 成绩',
  report: '家长会一页纸',
}

// 这些段仅作为父级路径、没有自己的页面，面包屑里不可点击（避免跳 404）
const NO_PAGE_SEGMENTS = new Set(['settings'])

interface Crumb {
  label: string
  href?: string
}

function buildCrumbs(pathname: string, dynamicLabels: Record<string, string> = {}): Crumb[] {
  const segments = pathname.split('/').filter(Boolean)
  if (segments.length === 0) {
    return [{ label: '仪表盘' }]
  }
  const crumbs: Crumb[] = []
  let acc = ''
  segments.forEach((seg, i) => {
    acc += '/' + seg
    const isDynamicId = i > 0 && /^[\w-]+$/.test(seg) && SEGMENT_LABELS[seg] === undefined
    if (isDynamicId) {
      const parentLabel = SEGMENT_LABELS[segments[i - 1]]
      const dynamicLabel = dynamicLabels[acc]
      // /exam/[id] -> 考试 #id, /student/[id] -> 学生 #id
      let label = dynamicLabel || `#${seg}`
      if (segments[i - 1] === 'exam') label = `考试 #${seg}`
      else if (segments[i - 1] === 'student') label = `学生 #${seg}`
      else if (parentLabel) label = `${parentLabel} #${seg}`
      if (dynamicLabel) label = dynamicLabel
      crumbs.push({ label })
    } else {
      const label = SEGMENT_LABELS[seg] ?? seg
      const isLast = i === segments.length - 1
      crumbs.push({ label, href: isLast || NO_PAGE_SEGMENTS.has(seg) ? undefined : acc })
    }
  })
  return crumbs
}

export function Topbar({ teacher }: { teacher: TeacherSummary | null }) {
  const pathname = usePathname() || '/'
  // 契约 v2.1 §3（F04）：面包屑可点击链接同样携带当前工作台，公共页刷新/直达不漂移
  const { mode } = useWorkspace()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [dynamicLabels, setDynamicLabels] = useState<Record<string, string>>({})
  const crumbs = buildCrumbs(pathname, dynamicLabels)
  const settingsActive = pathname === '/settings' || pathname.startsWith('/settings/')

  useEffect(() => {
    const match = pathname.match(/^\/exam\/(\d+)/)
    if (!match) {
      setDynamicLabels({})
      return
    }
    let cancelled = false
    const href = `/exam/${match[1]}`
    fetch(`/api/exams/${match[1]}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled) {
          const name = data?.exam?.name
          setDynamicLabels(name ? { [href]: name } : {})
        }
      })
      .catch(() => {
        if (!cancelled) setDynamicLabels({})
      })
    return () => {
      cancelled = true
    }
  }, [pathname])

  const openChat = () => {
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('open-chat'))
    }
  }

  return (
    <header className="topbar-deco sticky top-0 z-20 flex h-14 items-center justify-between border-b border-[#cbe2f5] bg-white/75 px-4 backdrop-blur md:px-6 print:hidden">
      <div className="flex min-w-0 items-center gap-3">
        {/* Mobile hamburger */}
        <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
          <SheetTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="md:hidden"
              aria-label="打开菜单"
            >
              <Menu className="h-5 w-5" />
            </Button>
          </SheetTrigger>
          <SheetContent side="left" className="w-60 p-0 border-0">
            <SidebarContent teacher={teacher} />
          </SheetContent>
        </Sheet>

        {/* Breadcrumbs */}
        <nav className="hidden min-w-0 items-center gap-1 text-sm sm:flex">
          {crumbs.map((c, i) => (
            <span key={i} className="flex items-center gap-1">
              {i > 0 && <ChevronRight className="h-3.5 w-3.5 text-slate-400" />}
              {c.href ? (
                <Link href={workspaceHref(c.href, mode)} className="text-slate-500 hover:text-slate-900">
                  {c.label}
                </Link>
              ) : (
                <span className={i === crumbs.length - 1 ? 'text-slate-900 font-medium' : 'text-slate-500'}>
                  {c.label}
                </span>
              )}
            </span>
          ))}
        </nav>
      </div>

      <div className="flex shrink-0 items-center gap-1 sm:gap-2">
        {/* 双工作台切换器：班主任 / 教学（键盘 tab + enter 可操作） */}
        <WorkspaceSwitcher />
        {/* 旧教学班选择器只服务旧教学页面；新工作台路由有自己的范围栏 */}
        {!/^\/(homeroom|teaching)(\/|$)/.test(pathname) && (
          <div className="hidden sm:block">
            <ClassScopePicker compact />
          </div>
        )}
        <Button
          asChild
          variant={settingsActive ? 'secondary' : 'ghost'}
          size="sm"
          className="px-2 sm:px-3"
        >
          <Link
            href={workspaceHref('/settings', mode)}
            aria-label="学期设置"
            title="学期设置"
          >
            <Settings className="h-5 w-5" />
            <span className="hidden sm:inline">学期设置</span>
          </Link>
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={openChat}
          aria-label="打开对话助手"
          title="对话助手"
        >
          <MessageSquare className="h-5 w-5" />
        </Button>
      </div>
    </header>
  )
}
