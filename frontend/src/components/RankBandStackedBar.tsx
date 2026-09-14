'use client'

import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid } from 'recharts'

interface RankBandData {
  class_num: number
  high_score: number
  critical: number
  weak: number
}

// 配色对齐设计 token：高分段=brand-500, 临界段=warning-500, 薄弱段=danger-500
const COLORS = {
  high_score: '#1f7fd6', // brand-500
  critical: '#e9a23b',   // warning-500
  weak: '#e15a5a',       // danger-500
}

interface BandLabels {
  high_score: string
  critical: string
  weak: string
}

const DEFAULT_LABELS: BandLabels = {
  high_score: '高分段(1-80)',
  critical: '临界段(400-500)',
  weak: '薄弱段(>500)',
}

export default function RankBandStackedBar({
  data,
  labels,
}: {
  data: RankBandData[]
  labels?: BandLabels
}) {
  const l = labels ?? DEFAULT_LABELS
  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
        <CartesianGrid stroke="#dcecf8" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="class_num"
          tick={{ fontSize: 12, fill: '#58789b' }}
          stroke="#dcecf8"
          label={{ value: '班级', position: 'insideBottom', offset: -5, fill: '#58789b', fontSize: 12 }}
        />
        <YAxis tick={{ fontSize: 12, fill: '#58789b' }} stroke="#dcecf8" />
        <Tooltip
          contentStyle={{
            backgroundColor: '#ffffff',
            border: '1px solid #dcecf8',
            borderRadius: 8,
            boxShadow: '0 1px 2px 0 rgb(0 0 0 / 0.05)',
            fontSize: 12,
          }}
          labelStyle={{ color: '#16324a', fontWeight: 500 }}
          itemStyle={{ color: '#16324a' }}
          cursor={{ fill: '#eaf5fd' }}
        />
        <Legend wrapperStyle={{ fontSize: 12, color: '#58789b' }} />
        <Bar dataKey="high_score" fill={COLORS.high_score} name={l.high_score} />
        <Bar dataKey="critical" fill={COLORS.critical} name={l.critical} />
        <Bar dataKey="weak" fill={COLORS.weak} name={l.weak} />
      </BarChart>
    </ResponsiveContainer>
  )
}
