/**
 * 班主任工作台的整段多学科录入解析（ADR-024）。
 *
 * 班主任一天要收全科作业，习惯整段粘贴：
 *   物理：秦三，秦二
 *   数学：全交
 *   语文：王五 请假
 *   数学订正：秦一，秦三   （「学科+作业名：名单」→ 拆为 数学·订正 批次）
 *
 * 语义（与教学版 smart 文本的关键差异）：
 * - 「学科：名单」里的裸姓名 = 该科缺交例外（班主任只记谁没交，默认已交，
 *   沿用老班主任版按作业种类录入的例外口径）；带状态的 token 按状态登记。
 * - 「学科：全交」= 该科无缺交（full 台账）。
 * - 冒号左侧不含学科词、也非纯种类前缀的行（如「听力第四周：张三」）不建假学科
 *   批次（防呆）：归入 unmarkedGroups 待定学科组，学科由面板默认学科补齐。
 * - 没有「学科：」前缀的行不走本模块的缺交默认，交回上层沿用既有
 *   parseSmartHomeworkText 语义（裸姓名=已交、动作行按动作），保证
 *   两种输入格式的既有行为互不干扰。
 * - 「李四：请假」这类"姓名：状态"行不会被误认成学科（右侧剥掉状态词后
 *   无残留，或左侧含动作/作业种类词 → 不属于学科行）。
 * - 身份零猜测：姓名逐字透传，不做模糊匹配；确认由后端名册裁决。
 */

import type { HomeworkInputSpec, HomeworkRowInput } from '@/lib/api-v1'
import {
  ATTENDANCE_WORDS,
  FORGOT_WORDS,
  FULL_PHRASE,
  NAME_SPLIT,
  QUALITY_WORDS,
  STATUS_OF_ACTION,
  TOKEN_SPLIT,
  lineHasAction,
} from './homework-vocab'

export interface HomeroomSubjectGroup {
  subject: string
  homeworkType?: string | null
  input: HomeworkInputSpec
}

/** 待定学科组：冒号左侧不含学科词的行（如「听力第四周：张三」），作业名保留、学科由面板默认学科补。 */
export interface HomeroomUnmarkedGroup {
  /** 清理后的作业名（冒号左侧，去首尾分隔符）。 */
  homeworkType: string
  /** 原始整行，供报错文案引用。 */
  line: string
  input: HomeworkInputSpec
}

export interface HomeroomSmartSplit {
  /** 带学科前缀或由按人多科展开的行组（每科一批次）。 */
  subjectGroups: HomeroomSubjectGroup[]
  /** 左侧无学科词、又非纯种类前缀的行组：绝不自建假学科批次（防呆），学科由面板默认学科补齐。 */
  unmarkedGroups: HomeroomUnmarkedGroup[]
  /** 无学科前缀且未归入学科的原始行（沿用既有 parseSmartHomeworkText 语义处理）。 */
  plainLines: string[]
  error: string | null
}

export const KNOWN_SUBJECTS: Array<{ canonical: string; keywords: string[] }> = [
  { canonical: '语文', keywords: ['语文', '作文', '周记', '默写'] },
  { canonical: '数学', keywords: ['数学', '高数', '代数', '几何', '大本', '小本'] },
  { canonical: '英语', keywords: ['英语', '粉书', '报纸', '单词'] },
  { canonical: '物理', keywords: ['物理'] },
  { canonical: '化学', keywords: ['化学'] },
  { canonical: '生物', keywords: ['生物'] },
  { canonical: '历史', keywords: ['历史'] },
  { canonical: '地理', keywords: ['地理'] },
  { canonical: '政治', keywords: ['政治', '道法', '道德与法治', '思品'] },
  { canonical: '全科', keywords: ['全科', '所有科目', '各科'] },
  { canonical: '考勤', keywords: ['考勤', '出勤', '考勤记录'] },
  { canonical: '科学', keywords: ['科学'] },
  { canonical: '信息', keywords: ['信息', '信息技术', '电脑', '微机'] },
  { canonical: '体育', keywords: ['体育'] },
  { canonical: '音乐', keywords: ['音乐'] },
  { canonical: '美术', keywords: ['美术'] },
  { canonical: '通用技术', keywords: ['通用技术', '劳技', '劳动'] },
  { canonical: '心理', keywords: ['心理'] },
]

export function matchCanonicalSubject(text: string): string | null {
  const t = text.trim()
  if (!t) return null
  for (const group of KNOWN_SUBJECTS) {
    for (const kw of group.keywords) {
      if (t.includes(kw)) {
        return group.canonical
      }
    }
  }
  return null
}

export function isPureSubjectName(text: string): boolean {
  const t = text.trim()
  if (!t) return false
  return KNOWN_SUBJECTS.some((g) => g.canonical === t || g.keywords.some((kw) => kw === t))
}

// 作业种类词：只拦「无学科的纯种类前缀」（如「练习册：张三」——防止「练习册」被当成学科名）；
// 「数学订正」「化学练习册」这类「学科+作业名」前缀因含学科词不受此守卫影响（见判定 2）。
const TYPE_KEYWORD = /(?:作业|练习|默写|试卷|校本|订正)/

/**
 * 学科行右侧的姓名 token 展开：多个姓名用空格分隔时（如「物理：秦三 秦二」）
 * 拆为独立姓名；带状态/出勤词的 token（如「秦三 请假」）保持原样交给逐 token 解析。
 */
function expandNameToken(token: string): string[] {
  const text = token.trim()
  if (!text) return []
  if (lineHasAction(text)) return [text]
  return text.split(/\s+/).filter(Boolean)
}

/** 剥掉全部状态/出勤/评价词与分隔符后的残留（非空 → 有姓名或学科内容）。 */
function nameResidue(text: string): string {
  return text
    .replace(
      /未交|缺交|请假|免交|已交|交了|完成|没来|迟到|早退|旷课|缺课|缺席|忘带|没带|未带|优秀|良好|合格|不合格|不认真|马虎|潦草|敷衍|不工整|退步|作业乱|作业没做|错误率高|差|[、，,；;:\s（）()]/g,
      '',
    )
    .trim()
}

/** 单个 token（姓名 或 姓名+状态/出勤/评价）→ 逐人行（学科行右侧名单使用）。 */
function tokenToRow(token: string): HomeworkRowInput | null {
  const text = token.trim()
  if (!text || FULL_PHRASE.test(text)) return null
  const attendanceMatch = text.match(ATTENDANCE_WORDS)
  if (attendanceMatch) {
    const name = text.slice(0, attendanceMatch.index ?? 0).replace(/[（(].*?[）)]/g, '').trim()
    if (!name) return null
    // 迟到、没来等出勤异常不计入已交，按缺交例外登记并携带出勤标记
    return { name_or_alias: name, status: 'missing', attendance: attendanceMatch[0] }
  }
  const action = STATUS_OF_ACTION.find((item) => item.pattern.test(text))
  if (action) {
    const match = action.pattern.exec(text)
    const name = text.slice(0, match?.index ?? 0).replace(/[（(].*?[）)]/g, '').trim()
    if (!name) return null
    const evaluation = text.slice((match?.index ?? 0) + (match?.[0].length ?? 0)).replace(/^[\s，,：:;（）()-]+/, '').trim()
    return { name_or_alias: name, status: action.status, evaluation: evaluation || null }
  }
  const forgotMatch = text.match(FORGOT_WORDS)
  if (forgotMatch) {
    const name = text.slice(0, forgotMatch.index ?? 0).replace(/[（(].*?[）)]/g, '').trim()
    if (!name) return null
    // 忘带/没带不计入已交，按缺交例外登记并携带忘带评价
    return { name_or_alias: name, status: 'missing', evaluation: text.slice(forgotMatch.index ?? 0).replace(/[（）()]/g, '').trim() }
  }
  const qualityMatch = text.match(QUALITY_WORDS)
  if (qualityMatch) {
    const name = text.slice(0, qualityMatch.index ?? 0).replace(/[（(].*?[）)]/g, '').trim()
    if (!name) return null
    return { name_or_alias: name, status: 'submitted', evaluation: text.slice(qualityMatch.index ?? 0).replace(/[（）()]/g, '').trim() }
  }
  // 学科行内的裸姓名 = 缺交例外（班主任只记谁没交）
  return { name_or_alias: text.replace(/[（）()]/g, '').trim(), status: 'missing' }
}

/**
 * 学生优先行里的单个学科 token 解析（如 "语文作文", "数学 请假", "物理 迟到", "化学(忘带)"）。
 */
interface ParsedSubjectItem {
  subject: string
  homeworkType?: string | null
  status: HomeworkRowInput['status']
  attendance?: string | null
  evaluation?: string | null
}

function parseStudentSubjectItem(token: string): ParsedSubjectItem | null {
  const clean = token.trim()
  if (!clean) return null

  let attendance: string | null = null
  let status: HomeworkRowInput['status'] = 'missing'
  let evaluation: string | null = null

  // 1. 提取出勤（迟到、没来等不计入已交，保持 missing 并携带出勤）
  const attMatch = clean.match(ATTENDANCE_WORDS)
  if (attMatch) {
    attendance = attMatch[0]
    status = 'missing'
  }

  // 2. 提取状态动作
  const action = STATUS_OF_ACTION.find((item) => item.pattern.test(clean))
  if (action) {
    status = action.status
  }

  // 3. 提取忘带 / 质量（忘带不计入已交，保持 missing 并携带忘带评价）
  const forgotMatch = clean.match(FORGOT_WORDS)
  if (forgotMatch) {
    status = 'missing'
    evaluation = forgotMatch[0]
  }
  const qualityMatch = clean.match(QUALITY_WORDS)
  if (qualityMatch) {
    evaluation = qualityMatch[0]
  }

  // 4. 剥离状态和括号，提取学科/作业内容
  const subjectPart = clean
    .replace(
      /未交|缺交|请假|免交|已交|交了|完成|没来|迟到|早退|旷课|缺课|缺席|忘带|没带|未带|优秀|良好|合格|不合格|不认真|马虎|潦草|敷衍|不工整|退步|作业乱|作业没做|错误率高|差|[（）()]/g,
      '',
    )
    .trim()

  if (!subjectPart) {
    // 纯状态词（如「请假」、「迟到」）归入全科
    return { subject: '全科', homeworkType: null, status, attendance, evaluation }
  }

  const canonical = matchCanonicalSubject(subjectPart)
  const finalSubject = canonical ?? subjectPart

  // 5. 提取具体作业内容：剥离学科名（如"英语练习册"剥离"英语"得到"练习册"；"英语听力"剥离得到"听力"）
  let homeworkType: string | null = null
  if (canonical) {
    const residue = subjectPart.replace(canonical, '').replace(/^[:：\s-]+|[:：\s-]+$/g, '').trim()
    if (residue) {
      homeworkType = residue
    }
  } else if (subjectPart !== finalSubject) {
    homeworkType = subjectPart
  }

  // 作业内容只存在于批次的 homework_type（学科：练习册 → 批次作业名），
  // 绝不写入 evaluation；evaluation 仅承载真正的质量评价（ADR-031）。

  return {
    subject: finalSubject,
    homeworkType,
    status,
    attendance,
    evaluation,
  }
}

/**
 * 拆分整段文本：
 * 1. 支持「学科：姓名 姓名」与「学科：全交」；
 * 2. 支持「姓名：学科作业，学科作业，学科」（如「秦一：语文作文，数学」）；
 * 3. 支持请假、迟到、忘带等状态描述，并自动按学科反转归组；
 * 4. 左侧无学科词的行组（如「听力第四周：张三」）返回 unmarkedGroups（待定学科），
 *    纯种类前缀（如「练习册：张三」）与其他未识别行原样返回 plainLines。
 */
export function splitHomeroomHomeworkText(text: string): HomeroomSmartSplit {
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
  if (lines.length === 0) {
    return { subjectGroups: [], unmarkedGroups: [], plainLines: [], error: '请输入作业收交内容' }
  }

  interface SubjectBucket {
    subject: string
    homeworkType: string | null
    full: boolean
    rows: HomeworkRowInput[]
  }

  function makeGroupKey(subject: string, homeworkType?: string | null): string {
    return homeworkType ? `${subject}::${homeworkType}` : subject
  }

  const grouped = new Map<string, SubjectBucket>()
  const plainLines: string[] = []
  // 待定学科组：按作业名归并（同作业名多行合并为一组），学科由面板默认学科补
  const unmarkedBuckets = new Map<string, { homeworkType: string; line: string; full: boolean; rows: HomeworkRowInput[] }>()
  const globalStudentExceptions: Array<{ name: string; status: HomeworkRowInput['status']; attendance?: string | null; evaluation?: string | null }> = []
  const attendanceRows: HomeworkRowInput[] = []

  // 预扫描：是否有明确的学科行或包含学科的按人行
  let hasAnySubjectContext = false
  for (const line of lines) {
    const parts = line.split(/[:：]/, 2)
    if (parts.length === 2) {
      const left = parts[0].trim()
      const right = parts[1].trim()
      if (isPureSubjectName(left) || matchCanonicalSubject(left) !== null) {
        hasAnySubjectContext = true
        break
      }
      const rightTokens = right.split(TOKEN_SPLIT).map((s) => s.trim()).filter(Boolean)
      if (rightTokens.some((t) => matchCanonicalSubject(t) !== null && matchCanonicalSubject(t) !== '全科')) {
        hasAnySubjectContext = true
        break
      }
    }
  }

  for (const line of lines) {
    const parts = line.split(/[:：]/, 2)
    let handled = false
    if (parts.length === 2) {
      const left = parts[0].trim()
      const right = parts[1].trim()
      const rightIsFull = FULL_PHRASE.test(right)
      const rightTokens = right.split(TOKEN_SPLIT).map((s) => s.trim()).filter(Boolean)

      const leftIsPureSubject = isPureSubjectName(left)
      const tokensWithConcreteSubject = rightTokens.filter(
        (t) => matchCanonicalSubject(t) !== null && matchCanonicalSubject(t) !== '全科' && matchCanonicalSubject(t) !== '考勤',
      )

      // 判定 0：考勤/出勤独立行（迟到、没来、请假、免交等单列，不按学科计入作业缺交）
      // 形式 A：状态在前（如「迟到：刘雨琪」「请假：王五、李四」「没来：赵六」）
      const leftIsAttendanceAction = ATTENDANCE_WORDS.test(left) || /(?:请假|免交)/.test(left)
      if (leftIsAttendanceAction && tokensWithConcreteSubject.length === 0 && rightTokens.length > 0) {
        const isExcused = /(?:请假|免交)/.test(left)
        const attText = ATTENDANCE_WORDS.exec(left)?.[0] ?? null
        for (const name of right.split(NAME_SPLIT).filter(Boolean)) {
          const cleanName = nameResidue(name) || name
          if (!cleanName) continue
          attendanceRows.push({
            name_or_alias: cleanName,
            status: isExcused ? 'excused' : 'missing',
            ...(attText ? { attendance: attText } : {}),
          })
        }
        handled = true
      }

      // 形式 B：学生在前，但右侧纯为考勤词（如「刘雨琪：迟到」「王五：请假」）
      const rightIsPureAttendance =
        !handled &&
        !leftIsPureSubject &&
        matchCanonicalSubject(left) === null &&
        tokensWithConcreteSubject.length === 0 &&
        rightTokens.length > 0 &&
        rightTokens.every((t) => ATTENDANCE_WORDS.test(t) || /(?:请假|免交)/.test(t))
      if (rightIsPureAttendance) {
        const studentName = left
        for (const token of rightTokens) {
          const isExcused = /(?:请假|免交)/.test(token)
          const attText = ATTENDANCE_WORDS.exec(token)?.[0] ?? null
          attendanceRows.push({
            name_or_alias: studentName,
            status: isExcused ? 'excused' : 'missing',
            ...(attText ? { attendance: attText } : {}),
          })
        }
        handled = true
      }

      // 判定 1：学生在前（姓名：学科作业，学科...）
      // 特征：
      // a. 右侧包含具体学科名（如「秦一：语文作文，数学」或「秦四：化学」），且左侧不是纯学科名；
      // b. 或整段文本有具体学科上下文，且当前行为学生状态行（如已出现物理全交，当前行是「李四：请假」）
      const looksByStudent =
        !handled &&
        left !== '' &&
        !leftIsPureSubject &&
        (tokensWithConcreteSubject.length > 0 || (hasAnySubjectContext && nameResidue(right) === ''))

      if (looksByStudent) {
        const studentName = left
        for (const token of rightTokens) {
          const parsed = parseStudentSubjectItem(token)
          if (!parsed) continue
          if (parsed.subject === '全科') {
            globalStudentExceptions.push({
              name: studentName,
              status: parsed.status,
              attendance: parsed.attendance,
              evaluation: parsed.evaluation,
            })
          } else {
            // 方案 A：不同具体作业内容（homeworkType）拆分为独立批次（如英语练习册与英语听力分列）
            const key = makeGroupKey(parsed.subject, parsed.homeworkType)
            const bucket = grouped.get(key) ?? {
              subject: parsed.subject,
              homeworkType: parsed.homeworkType ?? null,
              full: false,
              rows: [],
            }
            // 同学生同批次去重覆盖
            const existingIdx = bucket.rows.findIndex((r) => r.name_or_alias === studentName)
            const newRow: HomeworkRowInput = {
              name_or_alias: studentName,
              status: parsed.status,
              ...(parsed.attendance ? { attendance: parsed.attendance } : {}),
              ...(parsed.evaluation ? { evaluation: parsed.evaluation } : {}),
            }
            if (existingIdx >= 0) {
              const prev = bucket.rows[existingIdx]
              bucket.rows[existingIdx] = {
                name_or_alias: studentName,
                status: parsed.status === 'excused' || prev.status === 'excused' ? 'excused' : (prev.status || parsed.status),
                ...(prev.attendance || parsed.attendance ? { attendance: prev.attendance || parsed.attendance } : {}),
                ...(parsed.evaluation || prev.evaluation ? { evaluation: parsed.evaluation || prev.evaluation } : {}),
              }
            } else {
              bucket.rows.push(newRow)
            }
            grouped.set(key, bucket)
          }
        }
        handled = true
      } else if (!handled) {
        // 判定 2：学科在前（学科：姓名 姓名... 或 学科：全交）
        // 左侧含已知学科词（如「数学订正」「化学练习册」）即按学科行处理（剥离学科名后
        // 残留作为 homeworkType）；TYPE_KEYWORD 只拦「无学科的纯种类前缀」（如「练习册：张三」）。
        const leftHasSubject = matchCanonicalSubject(left) !== null
        const leftLooksSubject =
          left !== '' &&
          left.length <= 15 &&
          !lineHasAction(left) &&
          (leftHasSubject || !TYPE_KEYWORD.test(left))

        if (leftLooksSubject && (right === '' || rightIsFull || nameResidue(right) !== '')) {
          const canonical = matchCanonicalSubject(left)
          if (canonical === null) {
            // 防呆（真实事故）：「听力第四周：张三」这类左侧不含学科词的行，绝不把 left
            // 整个当成学科自建假学科批次（污染按学科归并的预警/画像/相关性）。改为待定
            // 学科组：作业名保留（清理方式与学科分支的 residue 一致），右侧名单语义与
            // 学科分支完全一致（全交/裸姓名=缺交/带状态 token），学科由面板默认学科补。
            const unmarkedType = left.replace(/^[:：\s-]+|[:：\s-]+$/g, '').trim() || left
            const bucket = unmarkedBuckets.get(unmarkedType) ?? {
              homeworkType: unmarkedType,
              line,
              full: false,
              rows: [],
            }
            if (rightIsFull) {
              bucket.full = true
            } else if (right !== '') {
              for (const token of rightTokens) {
                for (const name of expandNameToken(token)) {
                  const row = tokenToRow(name)
                  if (row) bucket.rows.push(row)
                }
              }
            }
            unmarkedBuckets.set(unmarkedType, bucket)
          } else {
            // 若包含标准学科词，规范为标准名（如"语文作文"归入"语文"）
            const finalSubject = canonical
            let homeworkType: string | null = null
            const residue = left.replace(canonical, '').replace(/^[:：\s-]+|[:：\s-]+$/g, '').trim()
            if (residue) homeworkType = residue
            const key = makeGroupKey(finalSubject, homeworkType)
            const bucket = grouped.get(key) ?? {
              subject: finalSubject,
              homeworkType,
              full: false,
              rows: [],
            }
            if (rightIsFull) {
              bucket.full = true
            } else if (right !== '') {
              for (const token of rightTokens) {
                for (const name of expandNameToken(token)) {
                  const row = tokenToRow(name)
                  if (row) bucket.rows.push(row)
                }
              }
            }
            grouped.set(key, bucket)
          }
          handled = true
        }
      }
    } else {
      // 单行无冒号：如「刘雨琪 迟到」「王五 请假」
      const clean = line.trim()
      const hasAtt = ATTENDANCE_WORDS.test(clean)
      const hasExc = /(?:请假|免交)/.test(clean)
      if ((hasAtt || hasExc) && matchCanonicalSubject(clean) === null) {
        const name = clean.replace(ATTENDANCE_WORDS, '').replace(/(?:请假|免交)/, '').replace(/[:：\s]/g, '').trim()
        if (name && name.length <= 10) {
          attendanceRows.push({
            name_or_alias: name,
            status: hasExc ? 'excused' : 'missing',
            ...(hasAtt ? { attendance: ATTENDANCE_WORDS.exec(clean)?.[0] ?? null } : {}),
          })
          handled = true
        }
      }
    }
    if (!handled) plainLines.push(line)
  }

  // 考勤独立单列：归入「考勤」独立批次，全员全勤已到，例外行登记迟到/请假
  if (attendanceRows.length > 0) {
    const bucket = grouped.get('考勤') ?? { subject: '考勤', homeworkType: null, full: true, rows: [] }
    for (const row of attendanceRows) {
      const existingIdx = bucket.rows.findIndex((r) => r.name_or_alias === row.name_or_alias)
      if (existingIdx >= 0) {
        bucket.rows[existingIdx] = row
      } else {
        bucket.rows.push(row)
      }
    }
    grouped.set('考勤', bucket)
  }

  // 若有全科请假/出勤学生（如「秦一：请假」），广播合并到所有已生成的具体学科批次
  if (globalStudentExceptions.length > 0 && grouped.size > 0) {
    for (const bucket of grouped.values()) {
      for (const exc of globalStudentExceptions) {
        const existingIdx = bucket.rows.findIndex((r) => r.name_or_alias === exc.name)
        const newRow: HomeworkRowInput = {
          name_or_alias: exc.name,
          status: exc.status,
          ...(exc.attendance ? { attendance: exc.attendance } : {}),
          ...(exc.evaluation ? { evaluation: exc.evaluation } : {}),
        }
        if (existingIdx < 0) {
          bucket.rows.push(newRow)
        }
      }
    }
  }

  const subjectGroups: HomeroomSubjectGroup[] = []
  for (const bucket of grouped.values()) {
    if (bucket.full) {
      subjectGroups.push({
        subject: bucket.subject,
        homeworkType: bucket.homeworkType,
        input: { kind: 'full', exceptions: bucket.rows },
      })
    } else if (bucket.rows.length > 0) {
      subjectGroups.push({
        subject: bucket.subject,
        homeworkType: bucket.homeworkType,
        input: { kind: 'detailed', rows: bucket.rows },
      })
    }
    // 既无名单也无全交的空学科行（如「物理：」）直接丢弃
  }

  const unmarkedGroups: HomeroomUnmarkedGroup[] = []
  for (const bucket of unmarkedBuckets.values()) {
    if (bucket.full) {
      unmarkedGroups.push({
        homeworkType: bucket.homeworkType,
        line: bucket.line,
        input: { kind: 'full', exceptions: bucket.rows },
      })
    } else if (bucket.rows.length > 0) {
      unmarkedGroups.push({
        homeworkType: bucket.homeworkType,
        line: bucket.line,
        input: { kind: 'detailed', rows: bucket.rows },
      })
    }
    // 既无名单也无全交的空行（如「听力第四周：」）直接丢弃
  }
  return { subjectGroups, unmarkedGroups, plainLines, error: null }
}
