import { SemesterSettingsCard } from '@/components/homework/SemesterSettingsCard'
import { RankBandsSettingsCard } from '@/components/settings/RankBandsSettingsCard'
import { DiagnosisThresholdSettingsCard } from '@/components/settings/DiagnosisThresholdSettingsCard'
import BackupCard from '@/components/BackupCard'

export default function SemesterSettingsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">学期设置</h1>
        <p className="mt-1 text-sm text-slate-500">班主任和教学工作台共用同一套学期日期与当前学期。长期名次分段与进退步阈值也在此统一设置。</p>
      </div>
      <SemesterSettingsCard />
      <RankBandsSettingsCard />
      <DiagnosisThresholdSettingsCard />
      {/* 2026-11-10：备份/恢复入口接线（此前 BackupCard 为无引用死组件，
          合并版前端一直没有备份 UI 入口）；恢复走 /api/restore，开启登录后
          须确认密码（v1.0.13 加固），恢复成功会话失效需重登一次。 */}
      <BackupCard />
    </div>
  )
}
