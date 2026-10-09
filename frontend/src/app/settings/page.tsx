import { SemesterSettingsCard } from '@/components/homework/SemesterSettingsCard'
import { RankBandsSettingsCard } from '@/components/settings/RankBandsSettingsCard'
import { DiagnosisThresholdSettingsCard } from '@/components/settings/DiagnosisThresholdSettingsCard'

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
    </div>
  )
}
