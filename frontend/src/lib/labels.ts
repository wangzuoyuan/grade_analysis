const GRADE_LABELS: Record<number, string> = {
  1: '高一',
  2: '高二',
  3: '高三',
}

export function formatGradeLabel(grade: number | null | undefined): string {
  if (grade == null) return '—'
  return GRADE_LABELS[grade] ?? `高${grade}`
}

export function formatClassLabel(
  grade: number | null | undefined,
  classNum: number | null | undefined,
): string | null {
  if (grade == null || classNum == null) return null
  return `${formatGradeLabel(grade)}${classNum}班`
}
