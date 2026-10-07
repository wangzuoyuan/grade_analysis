/**
 * 诊断缺失原因机器码 → 中文展示（A7 汉化）。
 *
 * 后端 B1/B2 诊断端点的 missing_reason 字段携带稳定机器码（如
 * no_main3_row），直接渲染会对教师露出英文黑话。本模块只做展示层
 * 映射，不改任何判定口径；未知码如实带原始码兜底，绝不吞掉信息。
 *
 * 供 DiagnosisCard.tsx 与 DiagnosisReport.tsx 共用（两处渲染同一套码表）。
 */

/** 机器码 → 中文短句（码表以后端契约为准，仅展示层翻译）。 */
export const DIAGNOSIS_MISSING_REASON_LABELS: Record<string, string> = {
  no_main3_row: '本场无总分行',
  main3_absent: '本场缺考',
  no_main3_percentile: '总分无可读名次/百分位',
  not_computable: '不可计算',
}

/**
 * 机器码转中文展示：null/空串返回 null（由调用方决定是否渲染括号）；
 * 未知码兜底「数据不足（原始码：xxx）」，如实透出原始码便于排查。
 */
export function diagnosisMissingReasonLabel(code: string | null | undefined): string | null {
  if (code == null || code === '') return null
  return DIAGNOSIS_MISSING_REASON_LABELS[code] ?? `数据不足（原始码：${code}）`
}
