import { ApiV1Error } from '@/lib/api-v1'

// 契约 docs/contracts/p1-api.md §0：业务错误码 → 中文提示。
// 未列出的错误码回退到异常 message / 通用文案。
const API_ERROR_TEXT: Record<string, string> = {
  workspace_not_configured: '请先完成工作台配置',
  invalid_scope_param: '请求参数有误，请检查学年与班级的选择',
  resource_out_of_scope: '所选班级不在允许范围',
  // 契约 §0：token 过期/成员漂移/版本冲突统一 409（配对与共享范围面板同样适用）
  link_version_conflict: '预览已过期或成员已变化，请重新预览',
}

export function apiErrorMessage(err: unknown): string {
  if (err instanceof ApiV1Error) {
    const mapped = API_ERROR_TEXT[err.code]
    if (mapped) return mapped
    return err.message || `请求失败（HTTP ${err.status}）`
  }
  if (err instanceof Error && err.message) return err.message
  return '请求失败，请稍后重试'
}

export function isApiErrorCode(err: unknown, code: string): boolean {
  return err instanceof ApiV1Error && err.code === code
}
