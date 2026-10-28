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

/** 明细表一个学科组恒两个子列：选科=原始分/等级分，其余=分数/百分位。 */
export interface StudentSubjectGroup {
  subject: string
  /** 高二/三六选科：原始分/等级分 双列（学校源表无百分位列）。 */
  paired: boolean
  /** 分数旁加「百分位」子列：高一全科 + 高二/三语数英（源表带年级百分位）。 */
  showPercentile: boolean
}

/** 明细接口学科键不带“_等级”后缀，须按考试年级/实际等级分展开选科双列。 */
export function studentSubjectGroups(
  subjects: readonly string[],
  students: readonly HomeroomStudentRow[],
  grade: number | null | undefined,
): StudentSubjectGroup[] {
  return Array.from(new Set(subjects.map(subject => subject.replace(/_(原始|等级)$/, ''))))
    .map(subject => {
      const paired = grade !== 1 && ELECTIVES.has(subject) && (
        grade === 2 || grade === 3 ||
        students.some(student => student.grade_scores?.[subject] != null)
      )
      return { subject, paired, showPercentile: !paired }
    })
}
