/** 只使用文件保存的百分比。空分数为缺考，0 分和 0% 都是有效值。 */
export function percentileTrendValue(point: { score: number | null; grade_percentile: number | null }): number | null {
  const p = point.grade_percentile
  return point.score != null && Number.isFinite(point.score) && p != null && Number.isFinite(p) && p >= 0 && p <= 1
    ? Math.round(p * 10000) / 100 : null
}

export const qualityNumber = (n: number | null | undefined, digits = 2) =>
  n == null ? '—' : n.toLocaleString('zh-CN', { maximumFractionDigits: digits })
export const qualityPercent = (n: number | null | undefined) => n == null ? '—' : `${qualityNumber(n * 100)}%`
