/**
 * 页面表单的未提交草稿（P4 简化版 link-draft 模式）。
 *
 * 事实源是 sessionStorage 键 `page-draft:<路由>:<标识>`：用户填写到一半的表单在
 * 组件卸载（切工作台/跳路由）后不丢，重新进入时恢复；提交成功或显式取消后清除。
 * 与 link-draft 一样"尽力而为"：隐私模式写入失败静默，损坏数据当作不存在。
 */

export const PAGE_DRAFT_PREFIX = 'page-draft:'

function storage(): Storage | null {
  return typeof window === 'undefined' ? null : window.sessionStorage
}

function draftKey(route: string, key: string): string {
  return `${PAGE_DRAFT_PREFIX}${route}:${key}`
}

export function savePageDraft<T>(route: string, key: string, value: T): void {
  const s = storage()
  if (!s) return
  try {
    s.setItem(draftKey(route, key), JSON.stringify(value))
  } catch {
    // 隐私模式等写入失败时静默：草稿是尽力而为的增强
  }
}

/** 读取草稿；无草稿/数据损坏/形状不符时返回 null，由调用方回退表单默认值。 */
export function loadPageDraft<T>(route: string, key: string, isValid: (v: unknown) => v is T): T | null {
  const s = storage()
  if (!s) return null
  try {
    const raw = s.getItem(draftKey(route, key))
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    return isValid(parsed) ? parsed : null
  } catch {
    // 损坏的草稿一律当作不存在
    return null
  }
}

export function clearPageDraft(route: string, key: string): void {
  const s = storage()
  if (!s) return
  try {
    s.removeItem(draftKey(route, key))
  } catch {
    // 清除失败不阻塞主流程
  }
}
