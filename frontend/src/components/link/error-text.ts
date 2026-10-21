import { ApiV1Error } from '@/lib/api-v1'

// 契约 docs/contracts/p1-api.md §0：具体原因优先，业务码只作缺失说明时的回退。
const API_ERROR_TEXT: Record<string, string> = {
  workspace_not_configured: '请先完成工作台配置',
  invalid_scope_param: '请求参数有误，请检查学年与班级的选择',
  resource_out_of_scope: '所选班级不在允许范围',
  link_version_conflict: '操作存在数据或版本冲突，请核对后重试',
}

const ERROR_FIELD_TEXT: Record<string, string> = {
  entity: '对象', person: '对象', person_id: '人员编号', identity_id: '人员编号',
  alias: '学号', aliases: '学号', student_id: '学号', name: '姓名', class_label: '班级', class_num: '班号',
  field: '字段', subject: '学科', exam_name: '考试', exam_date: '考试日期', dates: '日期',
  existing: '原值', incoming: '本次值', before: '原值', after: '当前值',
  library_value: '库内值', file_value: '本次上传值', old_value: '原值', new_value: '本次值',
  existing_score: '原分数', new_score: '本次分数',
  existing_name: '名册姓名', incoming_name: '文件姓名', message: '原因', reason: '原因',
  filename: '文件', file: '文件', sheet: '工作表', row: '行号', source: '来源',
  score: '原始分', grade_score: '等级分', grade_percentile: '年级百分位', xueji_rank: '学籍排名',
  raw_avg: '原始均分', grade_avg: '等级均分', expected_count: '应考', actual_count: '实考',
  bands: '人数及比率', count: '人数', ratio: '比率', scope: '范围', drift: '变化',
  academic_year_id: '学年编号', academic_year_name: '学年', valid_from: '生效日期', valid_to: '结束日期',
  teaching_class_id: '教学班编号', admin_class_id: '行政班编号', class_kind: '班级类别',
}

function errorValue(value: unknown, field = ''): string {
  if (value == null) return '—'
  if (typeof value === 'number' && (field.endsWith('ratio') || field === 'grade_percentile')) {
    return `${Number((value * 100).toFixed(4))}%`
  }
  if (Array.isArray(value)) return value.map(v => errorValue(v, field)).join('；') || '无'
  if (typeof value === 'object') {
    const row = value as Record<string, unknown>
    return Object.entries(row).map(([key, v]) => {
      const itemField = ['existing', 'incoming', 'before', 'after'].includes(key) && typeof row.field === 'string' ? row.field : key
      const text = key === 'field' && typeof v === 'string'
        ? v.split('.').map(k => ERROR_FIELD_TEXT[k] ?? k).join(' · ')
        : errorValue(v, itemField)
      return `${ERROR_FIELD_TEXT[key] ?? key}：${text}`
    }).join('；') || '无'
  }
  return String(value)
}

function conflictDetails(body: ApiV1Error['body']): string[] {
  const sections: string[] = []
  for (const [key, label] of [
    ['conflicts', '具体冲突'], ['drift', '范围或版本变化'],
    ['identity_candidates', '待确认身份'], ['roster_changes', '班级参照变化'],
  ]) {
    const value = body?.[key]
    if (value == null) continue
    const items = Array.isArray(value) ? value : [value]
    if (!items.length) continue
    sections.push(`${label}（${items.length} 项）：`, ...items.map((item, i) => `${i + 1}. ${errorValue(item)}`))
  }
  return sections
}

export function apiErrorMessage(err: unknown): string {
  if (err instanceof ApiV1Error) {
    const detail = err.detail?.trim()
    const summary = (detail && detail !== err.code ? detail : undefined)
      || API_ERROR_TEXT[err.code] || err.message || `请求失败（HTTP ${err.status}）`
    return [summary, ...conflictDetails(err.body)].join('\n')
  }
  if (err instanceof Error && err.message) return err.message
  return '请求失败，请稍后重试'
}

export function isApiErrorCode(err: unknown, code: string): boolean {
  return err instanceof ApiV1Error && err.code === code
}
