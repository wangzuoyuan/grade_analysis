/**
 * 关联配置页的未提交草稿（S07 草稿不丢）。
 *
 * 事实源是 sessionStorage 键 `link-draft:<link_id>`（JSON，同一 link 的两个面板共用
 * 一个键、各占一个分节）：组件卸载（切换工作台/路由跳转）后重新进入时恢复；
 * 提交成功或用户显式取消后清除分节，全部清空时移除整个键。
 *
 * 不引入状态库；工作台切换器在点击时直读存储判断是否存在草稿——比经 props/全局
 * 事件传递更可靠（面板卸载后信号不丢失）。
 */

export const LINK_DRAFT_PREFIX = 'link-draft:'

/** 配对面板的未提交选择（null = 未选）。 */
export interface LinkPairSelectionDraft {
  homeroom_person_id: string | null
  teaching_person_id: string | null
}

/** 共享范围表单的未提交修改。 */
export interface LinkShareScopeDraft {
  share_categories: string[]
  share_history_from: string | null
}

export interface LinkDraftData {
  pairSelection?: LinkPairSelectionDraft
  shareScope?: LinkShareScopeDraft
}

function storage(): Storage | null {
  return typeof window === 'undefined' ? null : window.sessionStorage
}

function draftKey(linkId: number): string {
  return LINK_DRAFT_PREFIX + String(linkId)
}

export function loadLinkDraft(linkId: number): LinkDraftData {
  try {
    const raw = storage()?.getItem(draftKey(linkId))
    if (!raw) return {}
    const parsed = JSON.parse(raw) as LinkDraftData
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    // 损坏的草稿一律当作不存在
    return {}
  }
}

/** 只写入传入的分节，保留同 link 其他面板的分节。 */
export function saveLinkDraftSection(linkId: number, section: LinkDraftData): void {
  const s = storage()
  if (!s) return
  try {
    s.setItem(draftKey(linkId), JSON.stringify({ ...loadLinkDraft(linkId), ...section }))
  } catch {
    // 隐私模式等写入失败时静默：草稿是尽力而为的增强
  }
}

/** 清除指定分节；全部清空时移除整个键，保证“无草稿”判断准确。 */
export function clearLinkDraftSection(linkId: number, section: 'pairSelection' | 'shareScope'): void {
  const s = storage()
  if (!s) return
  try {
    const next: LinkDraftData = { ...loadLinkDraft(linkId) }
    delete next[section]
    if (Object.keys(next).length === 0) s.removeItem(draftKey(linkId))
    else s.setItem(draftKey(linkId), JSON.stringify(next))
  } catch {
    // 清除失败不阻塞主流程
  }
}

/** 是否存在任何关联草稿（工作台切换器切换前确认用）。 */
export function hasAnyLinkDraft(): boolean {
  const s = storage()
  if (!s) return false
  try {
    for (let i = 0; i < s.length; i += 1) {
      if (s.key(i)?.startsWith(LINK_DRAFT_PREFIX)) return true
    }
  } catch {
    return false
  }
  return false
}
