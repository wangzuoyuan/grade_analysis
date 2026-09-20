import { redirect } from 'next/navigation'

/** 保留旧深链，直达教学班管理。 */
export default function LegacyClassSettingsPage() {
  redirect('/teaching/members?manage=classes')
}
