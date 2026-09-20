/**
 * 作业预览 422 错误 → 教师可读提示（纯函数，便于回归测试）。
 *
 * 后端契约（backend/app/api/homework.py _resolve_roster_ref + core/errors.py
 * to_http 把 details 平铺进错误体顶层）：
 * - 422 invalid_scope_param + body.name_or_alias（无 candidates）= 姓名/学号
 *   不在当前名册——必须点名学生，绝不泛化成"学年与班级参数错误"；
 * - 422 invalid_scope_param + body.candidates = 同名或同号歧义（候选清单由
 *   readHomeworkAmbiguityCandidates 单独渲染，这里只给主提示）；
 * - 其余 invalid_scope_param（param=teaching_class_id/mode/… 等）才是真正的
 *   作用域参数错误，沿用 error-text 的通用映射。
 *
 * 只按 code/body 字段宽容识别（不 instanceof ApiV1Error）：本函数仅在
 * homeworkPreview 的 catch 中调用，入参形状与 ApiV1Error 一致，宽松读取
 * 与 readHomeworkRevokeConflicts 的防御风格相同。
 */

import { apiErrorMessage } from '@/components/link/error-text'

interface ApiErrorLike {
  code?: unknown
  body?: unknown
}

/** scopeNoun：teaching 传「教学班」、homeroom 传「班级」，仅影响措辞。 */
export function homeworkPreviewErrorText(err: unknown, scopeNoun: string): string {
  if (typeof err === 'object' && err !== null) {
    const e = err as ApiErrorLike
    const body = (typeof e.body === 'object' && e.body !== null ? e.body : {}) as Record<string, unknown>
    if (e.code === 'invalid_scope_param') {
      const name = typeof body.name_or_alias === 'string' ? body.name_or_alias.trim() : ''
      if (name !== '') {
        if (Array.isArray(body.candidates)) {
          return `「${name}」在当前${scopeNoun}存在多名同名或同号学生，请改用学号（或人员编号）录入后重试。`
        }
        return `未在当前${scopeNoun}找到学生「${name}」，请核对姓名，或改用学号录入。`
      }
    }
  }
  return apiErrorMessage(err)
}
