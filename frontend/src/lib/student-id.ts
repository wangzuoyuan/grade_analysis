// 命名空间化学号：跨届撞号迁移会给离场届整体加 G{1|2|3}:: 前缀（历史格式
// g1-9900629），见 backend/app/db/sid_space.py。显示与排序都剥前缀还原原学号。
export function displayStudentId(sid: string | null | undefined): string {
  if (!sid) return ''
  return sid.replace(/^G[123]::/, '').replace(/^g\d+-/, '')
}
