/** 段位堆叠条共享配色：两工作台（教学分数段卡 / 班主任名次段分布卡）同源。
 * 2026-10-19 自 TeachingScores.tsx 抽出，行为不变；契约测试同步改为读本文件。 */

/** 堆叠条段位配色：Viridis 官方 8 点锚点（matplotlib 默认色图，感知均匀、
 * 全类型色盲安全；分数/名次为量纲——高分段取亮端、低分段取暗端），任意段数线性 RGB 插值。 */
export const VIRIDIS_ANCHORS = [
  '#fde725', '#a0da39', '#4ac16d', '#1fa187', '#277f8e', '#365c8d', '#46327e', '#440154',
]

export function hexToRgb(hex: string): [number, number, number] {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ]
}

export function bandBarColor(index: number, total: number): string {
  const t = total <= 1 ? 0 : index / (total - 1)
  const p = t * (VIRIDIS_ANCHORS.length - 1)
  const i = Math.min(Math.floor(p), VIRIDIS_ANCHORS.length - 2)
  const f = p - i
  const a = hexToRgb(VIRIDIS_ANCHORS[i])
  const b = hexToRgb(VIRIDIS_ANCHORS[i + 1])
  return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * f)).join(', ')})`
}
