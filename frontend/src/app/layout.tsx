import type { Metadata, Viewport } from 'next'
import './globals.css'
import { ChatDrawer } from '../components'
import AuthGate from '@/components/AuthGate'
import { Shell } from '@/components/layout/Shell'
import { ClassScopeProvider } from '@/lib/class-scope'
import { WorkspaceProvider } from '@/lib/workspace'

export const metadata: Metadata = {
  title: '学情追踪',
  description: '高中成绩分析 Web App',
}

// 显式声明 viewport：device-width + 初始 1 倍，不锁 maximumScale，
// 保留用户在宽表上手动缩放的能力。
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="bg-slate-50">
        <AuthGate>
          <WorkspaceProvider>
            <ClassScopeProvider>
              <Shell>{children}</Shell>
              <ChatDrawer />
            </ClassScopeProvider>
          </WorkspaceProvider>
        </AuthGate>
      </body>
    </html>
  )
}
