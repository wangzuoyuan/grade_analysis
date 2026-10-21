/** 段位堆叠条共享配色：两工作台（教学分数段卡 / 班主任名次段分布卡）同源。
 *  2026-10-19 自 TeachingScores.tsx 抽出，行为不变；契约测试同步改为读本文件。
 *  2026-10-21 用户拍板：班主任名次段卡（16 段）换 Inferno，教学分数段卡（6-7 段）
 *  维持 Viridis；bandBarColor 第三参缺省 Viridis，两卡调用互不影响。 */

/** Viridis 官方 8 点锚点（matplotlib 默认色图，感知均匀、全类型色盲安全；
 * 分数/名次为量纲——高分段取亮端、低分段取暗端），任意段数线性 RGB 插值。 */
export const VIRIDIS_ANCHORS = [
  '#fde725', '#a0da39', '#4ac16d', '#1fa187', '#277f8e', '#365c8d', '#46327e', '#440154',
]

/** Inferno 官方 8 点锚点（matplotlib 感知均匀家族，与 Viridis 同门：黑紫→红→橙→亮黄，
 * 高名次段取亮端）。班主任名次段分布卡专用：40 名一段共 16 段的高频离散采样下，
 * 中段靠紫→红→橙的大幅色相变化区分，优于 Viridis 的绿青中段；色盲安全。 */
export const INFERNO_ANCHORS = [
  '#fcffa4', '#fac228', '#f57d15', '#d44842', '#9f2a63', '#65156e', '#280b53', '#000004',
]

export function hexToRgb(hex: string): [number, number, number] {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ]
}

export function bandBarColor(index: number, total: number, anchors: readonly string[] = VIRIDIS_ANCHORS): string {
  const t = total <= 1 ? 0 : index / (total - 1)
  const p = t * (anchors.length - 1)
  const i = Math.min(Math.floor(p), anchors.length - 2)
  const f = p - i
  const a = hexToRgb(anchors[i])
  const b = hexToRgb(anchors[i + 1])
  return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * f)).join(', ')})`
}
