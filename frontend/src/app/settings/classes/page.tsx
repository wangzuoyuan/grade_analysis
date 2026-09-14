import { redirect } from 'next/navigation'

/** 旧教学班配置页依赖已移除端点；保留深链并转到 v1 成员管理。 */
export default function LegacyClassSettingsPage() {
  redirect('/teaching/members')
}
