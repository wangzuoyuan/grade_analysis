/**
 * 作业录入/展示的共享词汇表（唯一事实源）。
 *
 * 班主任整段解析（homeroom-smart-input）与教学端智能解析
 * （HomeworkEntryPanel.parseSmartHomeworkText）原先各维护一套
 * 状态/出勤/忘带/评价正则，且分隔符口径漂移（教学端支持空格、
 * 班主任端不支持）导致行为不一致——现统一收敛到本模块。
 */

import type { HomeworkStatus } from '@/lib/api-v1'

/** 出勤异常词（与后端 _ATTENDANCE_WORDS 同步）。 */
export const ATTENDANCE_WORDS = /(?:没来|迟到|早退|旷课|缺课|缺席)/
/** 忘带词（与后端 _FORGOT_WORDS 同步）。 */
export const FORGOT_WORDS = /(?:忘带|没带|未带)/
/** 质量评价词（录入解析用；负面判定以后端 _evaluation_tone 为准）。 */
export const QUALITY_WORDS = /(?:优秀|良好|合格|不合格|不认真|马虎|潦草|敷衍|不工整|退步|作业乱|作业没做|错误率高|差)/
/** 整段即状态的收交动作词。 */
export const STATUS_OF_ACTION: Array<{ pattern: RegExp; status: HomeworkStatus }> = [
  { pattern: /(?:未交|缺交)/, status: 'missing' },
  { pattern: /(?:请假|免交)/, status: 'excused' },
  { pattern: /(?:已交|交了|完成)/, status: 'submitted' },
]
/** 「全交」短语。 */
export const FULL_PHRASE = /^(?:全交|全员已交|全部已交|全齐|齐了|交齐|都交|都交齐)$/
/** 列表分隔：顿号/逗号/分号（学生优先行的学科项里空格可能是状态的一部分，不切）。 */
export const TOKEN_SPLIT = /[、，,；;]+/
/** 纯姓名名单分隔：额外允许空格（「迟到：刘雨琪 王五」）。 */
export const NAME_SPLIT = /[、，,；;\s]+/
/** 整段只是一个出勤词。 */
export const ATTENDANCE_ONLY = /^(?:没来|迟到|早退|旷课|缺课|缺席)$/

export function lineHasAction(text: string): boolean {
  return (
    ATTENDANCE_WORDS.test(text) ||
    FORGOT_WORDS.test(text) ||
    QUALITY_WORDS.test(text) ||
    STATUS_OF_ACTION.some((item) => item.pattern.test(text))
  )
}

/**
 * 评价纯文本：剥离出勤前缀（兼容存储形如「迟到｜评价」）、纯出勤段与忘带段，
 * 只留真正的质量评价；无评价返回空串。展示层用 `pureEvaluation(x) || '—'`。
 */
export function pureEvaluation(evaluation: string | null | undefined, attendance: string | null | undefined): string {
  let raw = (evaluation ?? '').trim()
  if (!raw) return ''
  const att = (attendance ?? '').trim()
  if (att) {
    if (raw === att) return ''
    if (raw.startsWith(`${att}｜`)) {
      raw = raw.slice(att.length + 1).trim()
      if (!raw) return ''
    }
  }
  if (ATTENDANCE_ONLY.test(raw)) return ''
  if (FORGOT_WORDS.test(raw)) {
    const parts = raw
      .split('｜')
      .map((p) => p.trim())
      .filter((p) => p && !FORGOT_WORDS.test(p) && !ATTENDANCE_ONLY.test(p))
    return parts.join('｜')
  }
  return raw
}
