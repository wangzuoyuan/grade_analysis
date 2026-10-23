import type { HomeroomStudentRow } from './api-v1'
import { compareStudentIds } from './student-order'

export type StudentScoreSort =
  | { kind: 'id' }
  | { kind: 'scores' | 'grade_scores' | 'totals'; key: string }

/** 排序只改变显示顺序；零分有效、缺值置底，同分沿用显示学号自然序。 */
export function sortHomeroomStudents(
  students: readonly HomeroomStudentRow[],
  sort: StudentScoreSort,
): HomeroomStudentRow[] {
  return [...students].sort((a, b) => {
    if (sort.kind === 'id') return compareStudentIds(a, b)
    const av = a[sort.kind]?.[sort.key]
    const bv = b[sort.kind]?.[sort.key]
    const aValid = typeof av === 'number' && Number.isFinite(av)
    const bValid = typeof bv === 'number' && Number.isFinite(bv)
    if (aValid !== bValid) return aValid ? -1 : 1
    return (aValid && bValid ? bv - av : 0) || compareStudentIds(a, b)
  })
}

const ELECTIVES = new Set(['物理', '化学', '生物', '政治', '历史', '地理'])

/** 明细接口学科键不带“_等级”后缀，须按考试年级/实际等级分展开选科双列。 */
export function studentSubjectGroups(
  subjects: readonly string[],
  students: readonly HomeroomStudentRow[],
  grade: number | null | undefined,
): Array<{ subject: string; paired: boolean }> {
  return Array.from(new Set(subjects.map(subject => subject.replace(/_(原始|等级)$/, ''))))
    .map(subject => ({
      subject,
      paired: grade !== 1 && ELECTIVES.has(subject) && (
        grade === 2 || grade === 3 ||
        students.some(student => student.grade_scores?.[subject] != null)
      ),
    }))
}
