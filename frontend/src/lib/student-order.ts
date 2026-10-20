import { displayStudentId } from './student-id'

interface NamedStudent {
  person_id: number | string
  name?: string | null
}

const collator = new Intl.Collator('zh-Hans-CN-u-co-pinyin', { numeric: true })

/** 姓名按拼音升序；同名以身份 ID 稳定排序，缺姓名排最后。 */
export function compareStudentNames(a: NamedStudent, b: NamedStudent): number {
  const an = a.name?.trim() ?? ''
  const bn = b.name?.trim() ?? ''
  if (Boolean(an) !== Boolean(bn)) return an ? -1 : 1
  return collator.compare(an, bn) || collator.compare(String(a.person_id), String(b.person_id))
}

/** 按显示学号自然升序；无学号排最后，再按姓名拼音排序。 */
export function compareStudentIds(
  a: NamedStudent & { alias?: string | null },
  b: NamedStudent & { alias?: string | null },
): number {
  const aid = displayStudentId(a.alias?.trim())
  const bid = displayStudentId(b.alias?.trim())
  if (Boolean(aid) !== Boolean(bid)) return aid ? -1 : 1
  return collator.compare(aid, bid) || compareStudentNames(a, b)
}
