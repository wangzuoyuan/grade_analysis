// 命名空间化学号（g1-9900629）：跨届撞号迁移改写的历史学号，见
// backend/app/db/migrate_student_ids.py。显示时还原为原学号。
export function displayStudentId(sid: string | null | undefined): string {
  if (!sid) return ''
  return sid.replace(/^g\d+-/, '')
}
