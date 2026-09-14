'use client'

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts'

export interface BandTrendPoint {
  exam_id: number
  exam_name: string
  exam_date?: string | null
  high_score: number
  critical: number
  weak: number
}

interface BandLabels {
  high_score: string
  critical: string
  weak: string
}

// 与 RankBandStackedBar 配色一致：高分段=brand-500, 临界段=warning-500, 薄弱段=danger-500
const COLORS = {
  high_score: '#1f7fd6',
  critical: '#e9a23b',
  weak: '#e15a5a',
}

const DEFAULT_LABELS: BandLabels = {
  high_score: '高分段',
  critical: '临界段',
  weak: '薄弱段',
}

export default function BandTrendChart({
  data,
  labels,
}: {
  data: BandTrendPoint[]
  labels?: BandLabels
}) {
  const l = labels ?? DEFAULT_LABELS
  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
        <CartesianGrid stroke="#dcecf8" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="exam_name"
          tick={{ fontSize: 11, fill: '#58789b' }}
          stroke="#dcecf8"
          interval="preserveStartEnd"
        />
        <YAxis allowDecimals={false} tick={{ fontSize: 12, fill: '#58789b' }} stroke="#dcecf8" />
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
        />
        <Legend wrapperStyle={{ fontSize: 12, color: '#58789b' }} />
        <Line
          type="monotone"
          dataKey="high_score"
          stroke={COLORS.high_score}
          name={l.high_score}
          strokeWidth={2}
          dot={{ r: 3 }}
        />
        <Line
          type="monotone"
          dataKey="critical"
          stroke={COLORS.critical}
          name={l.critical}
          strokeWidth={2}
          dot={{ r: 3 }}
        />
        <Line
          type="monotone"
          dataKey="weak"
          stroke={COLORS.weak}
          name={l.weak}
          strokeWidth={2}
          dot={{ r: 3 }}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
