/**
 * /api/v1 前端客户端（契约 v2 + P3 导入/分析扩展 + P4 学生管理扩展，2026-09-11）。
 *
 * 事实源：docs/contracts/p1-api.md §1/§3、docs/contracts/p3-imports-analysis.md §1/§2、
 * docs/contracts/p4-students.md §1-§4。
 * 导出清单与类型不得自行偏离契约；修改须先由集成者修订契约再同步本文件。
 * v2 增量：§1.2.1 学生配对、§1.2.2 share-scope、§1.4.1 shared_conflict（均为追加，不改既有导出）。
 * P3 增量：§1 导入（multipart preview + confirm/revise，替换 P1 骨架语义）、§1.4 考试清单、
 * §2 两域分析端点；P1 骨架的 importsPreview/importsConfirm（恒空清单）无人使用，按 P3 契约删除。
 * P4 增量：§1 学年管理、§2 名册/换届/打印、§3 教学班成员、§4 档案（全部 JSON，无 multipart）。
 *
 * 基础路径沿用既有页面的写法：相对路径 `/api/...`（本地 dev 由 next.config.js
 * rewrites 代理到后端，生产由反代按路径分流），因此这里同样使用相对 `/api/v1`。
 */

/** 工作台模式：班主任（行政班域）或教学（任教学科域）。 */
export type WorkspaceMode = 'homeroom' | 'teaching'

/** 数据域（与后端 student_identity.data_domain 对应）。 */
export type DataDomain = 'homeroom' | 'teaching'

/** 成绩来源域：原始录入所在的工作台域。 */
export type SourceDomain = 'homeroom' | 'teaching'

/** 人员标识：域内身份主键（后端为整数，此处宽松以兼容迁移期投影）。 */
export type PersonId = number | string

/** 业务错误（后端统一 JSON {"error": code, "detail"?: ...}）。 */
export class ApiV1Error extends Error {
  /** HTTP 状态码；网络层失败时为 0。 */
  readonly status: number
  /** 业务错误码（workspace_not_configured / invalid_scope_param / resource_out_of_scope / link_version_conflict / …）。 */
  readonly code: string
  /** 后端可选的人类可读中文说明。 */
  readonly detail?: string
  /** 错误响应原文（P3 起携带）：imports/confirm 409 的 conflicts 列表等扩展字段从这里读。 */
  readonly body?: Record<string, unknown>

  constructor(status: number, code: string, detail?: string, body?: Record<string, unknown>) {
    super(detail || code)
    this.name = 'ApiV1Error'
    this.status = status
    this.code = code
    this.detail = detail
    this.body = body
  }
}

/* ------------------------------------------------------------------ */
/* §1.1 配置与范围                                                     */
/* ------------------------------------------------------------------ */

/** 班级关联摘要（HomeroomTeachingLink）。 */
export interface LinkSummary {
  id: number
  admin_class_id: number
  teaching_class_id: number
  academic_year_id: number
  /** 学年名称（如 2025-2026），仅作显示，不作 ID 传递。 */
  academic_year_name: string
  subject: string
  /** active / cancelled / …（契约未穷举，按字符串比较，页面判断 'active'）。 */
  status: string
  version: number
  valid_from: string | null
  valid_to: string | null
  /** 共享类别（契约 §1.2.2；后端可选返回，缺省按默认 roster,current_subject_score 展示）。 */
  share_categories?: string[]
  /** 显式历史授权日期（可早于 valid_from）；null = 不开放关联生效前历史。 */
  share_history_from?: string | null
}

export interface SharedConfig {
  teacher: { id: PersonId; name: string | null }
  homeroom: { configured: boolean; grade: number | null; class_num: number | null }
  teaching: { configured: boolean; subject: string | null }
  /** 服务端解析的最新学年（start_date 最大者）；无学年为 null。前端以此为默认学年，不从日期推导。 */
  current_academic_year: { id: number; name: string } | null
  links: LinkSummary[]
}

/** GET /api/v1/shared/classes：班级目录（契约补丁新增）。 */
export interface ClassesCatalogHomeroom {
  class_id: number
  grade: number
  class_num: number
  label: string
}

export interface ClassesCatalogTeaching {
  class_id: number
  label: string
  subject: string
}

export interface ClassesCatalog {
  academic_year_id: number
  /** 学年名称（如 2025-2026），仅作显示。 */
  academic_year_name: string
  /** 该学年教师绑定的行政班；未绑定为 null。 */
  homeroom: ClassesCatalogHomeroom | null
  /** 该学年教师任教学科下的全部教学班（含未关联班，如 T8）。 */
  teaching: ClassesCatalogTeaching[]
}

/** GET /api/v1/shared/scope 的查询参数（缺省项由后端按当前时期/绑定解析）。 */
export interface ScopeQuery {
  mode: WorkspaceMode
  academic_year_id?: number
  term_id?: number
  /** homeroom：行政班；缺省为教师绑定的行政班。 */
  class_id?: number
  /** teaching：教学班；缺省表示“全部所教班”（同学年同学科成员并集）。 */
  teaching_class_id?: number
  subject?: string
}

/** GET /api/v1/shared/scope 的响应。空成员范围是合法空态（member_person_ids: []）。 */
export interface ScopeState {
  mode: WorkspaceMode
  data_domain: DataDomain
  subject: string | null
  member_person_ids: PersonId[]
  cohort_size: number
  link_id: number | null
  link_version: number | null
  as_of: string
}

/* ------------------------------------------------------------------ */
/* §1.2 班级关联                                                       */
/* ------------------------------------------------------------------ */

/** 关联两端学生的简要身份（同名同号不自动配对）。 */
export interface StudentBrief {
  person_id: PersonId
  name: string | null
  alias: string | null
}

export interface RosterDiff {
  both: StudentBrief[]
  homeroom_only: StudentBrief[]
  teaching_only: StudentBrief[]
}

export interface LinkPreviewRequest {
  admin_class_id: number
  teaching_class_id: number
  academic_year_id: number
  subject: string
}

export interface LinkPreview {
  token: string
  expires_at: string
  roster_diff: RosterDiff
  warning: string | null
}

export interface LinkConfirm {
  link_id: number
  version: number
  linked_count: number
}

export interface LinkCancelResult {
  success: boolean
  status: string
}

/* ------------------------------------------------------------------ */
/* §1.2.1 学生配对（契约 v2 新增——R6）                                 */
/* ------------------------------------------------------------------ */

/** 既有配对（LinkedStudent）：双域身份均为显式 person_id，绝不按同名/同号推导。 */
export interface LinkPairEntry {
  linked_id: number
  homeroom_person_id: PersonId
  teaching_person_id: PersonId
  homeroom_name: string | null
  teaching_name: string | null
  confirm_basis: string | null
}

export interface LinkStudentsList {
  link_id: number
  pairs: LinkPairEntry[]
}

export interface LinkStudentsRequest {
  /** 一次 1..N 对，整批事务；契约禁止按同名/同号自动配对，只接受显式 person_id 对。 */
  pairs: Array<{ homeroom_person_id: PersonId; teaching_person_id: PersonId }>
  confirm_basis?: string
}

export interface LinkStudentsResult {
  created: number
  skipped: number
  pairs?: LinkPairEntry[]
}

/* ------------------------------------------------------------------ */
/* §1.2.2 共享范围（契约 v2 明确——R3）                                 */
/* ------------------------------------------------------------------ */

/** share_categories 仅识别 roster / current_subject_score / current_subject_homework；省略字段 = 不修改该项。 */
export interface LinkShareScopeRequest {
  share_categories?: string[]
  /** ISO 日期（YYYY-MM-DD）；传 null 表示清除历史授权。 */
  share_history_from?: string | null
}

export interface LinkShareScope {
  link_id: number
  version: number
  share_categories: string[]
  /** 显式历史授权日期（可早于 valid_from）；null = 不开放关联生效前历史。 */
  share_history_from: string | null
}

/* ------------------------------------------------------------------ */
/* §1.3 学生（域隔离读 + 关联投影）                                    */
/* ------------------------------------------------------------------ */

/** 所有 200 数据响应携带的元数据。 */
export interface ResponseMetadata {
  mode: WorkspaceMode
  subject: string | null
  scope: {
    academic_year_id?: number
    term_id?: number
    class_id?: number
    teaching_class_id?: number
    link_id?: number | null
    link_version?: number | null
  }
  cohort_size: number
  data_revision: number | string
}

/** 关联投影出来的任教学科最近一场成绩（仅“已确认关联且在有效期”的学生携带）。 */
export interface SharedSubjectScore {
  subject: string
  /** 缺考为 null，不得转 0。 */
  score: number | null
  exam_name: string
  source_domain: 'teaching'
}

/**
 * 跨域冲突提示（契约 §1.4.1）：两域同场次事实值不一致时由本域行携带；
 * teaching_score 可为 null（对方域缺考）。绝不静默采用对方值。
 */
export interface LinkSharedConflict {
  teaching_score: number | null
}

export interface WorkspaceStudent {
  person_id: PersonId
  name: string | null
  seat_no: string | null
  alias: string | null
  status: string | null
  linked_teaching_class_id?: number | null
  shared_subject_score?: SharedSubjectScore | null
  /** 两域分数不一致时由后端可选携带（契约 §1.4.1）。 */
  shared_conflict?: LinkSharedConflict | null
}

export interface StudentsQuery {
  academic_year_id?: number
  term_id?: number
  /** homeroom 模式使用。 */
  class_id?: number
  /** teaching 模式使用。 */
  teaching_class_id?: number
}

export interface StudentsResponse {
  metadata: ResponseMetadata
  students: WorkspaceStudent[]
}

export interface ProfileExam {
  exam_name: string
  exam_date: string | null
  /** 缺考为 null，不得转 0。 */
  score: number | null
  /** 后端画像条目携带的等级分（E03）；旧来源无等级分时为 null。 */
  grade_score?: number | null
  source_domain: SourceDomain
  /** 两域分数不一致时可选携带（契约 §1.4.1）。 */
  shared_conflict?: LinkSharedConflict | null
}

export interface ProfileSubject {
  subject: string
  exams: ProfileExam[]
}

export interface ProfileTotal {
  total_type: string
  exams: ProfileExam[]
}

export interface StudentProfile {
  metadata: ResponseMetadata
  person: { person_id: PersonId; name: string | null; domain: DataDomain }
  subjects: ProfileSubject[]
  /** 仅 homeroom 可见总分；teaching 模式不返回。 */
  totals?: ProfileTotal[]
}

/* ------------------------------------------------------------------ */
/* §1.4 成绩查询                                                       */
/* ------------------------------------------------------------------ */

export interface ScoresQuery {
  mode: WorkspaceMode
  academic_year_id?: number
  exam_name?: string
}

export interface ScoreRow {
  person_id: PersonId
  name: string | null
  subject: string | null
  /** 缺考为 null，不得转 0。 */
  score: number | null
  total_type: string | null
  source_domain: SourceDomain
  /** 两域分数不一致时由 homeroom 行可选携带（契约 §1.4.1）。 */
  shared_conflict?: LinkSharedConflict | null
}

export interface ScoresResponse {
  metadata: ResponseMetadata
  rows: ScoreRow[]
}

/* ------------------------------------------------------------------ */
/* §1.5 导入（P3 契约 docs/contracts/p3-imports-analysis.md §1）        */
/* ------------------------------------------------------------------ */

/** preview 条目中未匹配到既有身份、确认时将新建的学生（契约 §1.1）。 */
export interface ImportPreviewNewStudent {
  name: string
  alias: string | null
}

/**
 * 历史学年 alias 命中的身份候选（契约 p3 §1.1 v2.1，F06）：
 * 历史命中绝不自动接续（无法区分同名新生/学号回收/跨届重号），确认前必须逐别名显式选择；
 * 选「新建学生」的别名不进 identity_confirmations，由后端按新学生建档。
 */
export interface ImportIdentityCandidate {
  /** 触发命中的学号（alias_value）。 */
  alias: string
  name: string
  /** 候选接续的历史身份 person_id。 */
  person_id: PersonId
  academic_year_id: number
  /** 学年名称（如 2024-2025），仅作展示，不作 ID 传递。 */
  academic_year_name: string
  /** 命中依据（如「学号+姓名」），仅作展示。 */
  basis: string
}

/** 文件识别类型（契约 §1.1：学生分数表 / 班级均分表 / 未识别）。 */
export type ImportPreviewKind = 'student_scores' | 'class_averages' | 'unknown'

/** preview 单文件解析结果；缺考计数等风险信息在 warnings（契约 §1.1）。 */
export interface ImportPreviewItem {
  filename: string
  kind: ImportPreviewKind
  parsed_ok: boolean
  message?: string
  exam_name: string
  exam_date: string | null
  subject?: string | null
  class_label?: string | null
  row_count: number
  known_students: number
  new_students: ImportPreviewNewStudent[]
  /** 历史学年命中候选（F06）：非空时确认前必须逐别名选择「接续」或「新建」。 */
  identity_candidates?: ImportIdentityCandidate[]
  warnings: string[]
}

/** POST /api/v1/imports/preview（multipart）的响应：零业务写入，仅 import_batch 台账。 */
export interface ImportsPreviewResult {
  token: string
  expires_at: string
  mode: WorkspaceMode
  items: ImportPreviewItem[]
}

/** confirm 409 时随错误体返回的冲突条目（契约 §1.2：person/subject/exam/库内值/新值）。 */
export interface ImportConfirmConflict {
  person: string
  subject: string | null
  exam_name: string
  /** 库内已有分数；缺考为 null。 */
  existing_score: number | null
  /** 本次上传分数；缺考为 null。 */
  new_score: number | null
}

/** POST /api/v1/imports/confirm 的响应（契约 §1.2）。 */
export interface ImportsConfirmResult {
  imported: number
  skipped: number
  revised: number
  exams: Array<{ exam_name: string; exam_date: string | null }>
  students_created: number
  members_synced: number
}

/* ------------------------------------------------------------------ */
/* §1.4 考试清单 + §2 分析（P3 契约）                                  */
/* ------------------------------------------------------------------ */

/** 已导入考试摘要（GET /shared/exams，按考试日期降序）。 */
export interface ExamSummary {
  exam_name: string
  exam_date: string | null
  row_count: number
  /** homeroom 为本班出现的学科并集；teaching 恒仅任教学科。 */
  subjects: string[]
}

/** 分析端点的作用域查询参数（从 useWorkspace 的 filter 映射，后端解析，空范围不退化）。 */
export interface AnalysisScopeQuery {
  academic_year_id?: number
  term_id?: number
  /** homeroom 使用。 */
  class_id?: number
  /** teaching 使用；缺省表示全部所教班并集。 */
  teaching_class_id?: number
}

/** 班主任单科统计（契约 §2.1 stats.subjects）。 */
export interface HomeroomSubjectStat {
  subject: string
  avg: number | null
  max: number | null
  min: number | null
  valid_count: number
  missing_count: number
  score_basis: string
  /** valid_count < 5 时后端标注（契约 §2.3）；前端同时按 valid_count 本地兜底。 */
  small_sample?: boolean
}

/** 班主任总分统计（契约 §2.1 stats.totals；总分无缺考计数）。 */
export interface HomeroomTotalStat {
  total_type: string
  avg: number | null
  max: number | null
  min: number | null
  valid_count: number
  small_sample?: boolean
}

/** GET /api/v1/homeroom/analysis/exams/{exam_name}/stats 的响应。 */
export interface HomeroomStatsResponse {
  metadata: ResponseMetadata
  subjects: HomeroomSubjectStat[]
  totals: HomeroomTotalStat[]
  cohort_size: number
  small_sample?: boolean
}

/**
 * 学生行的任教学科冲突集合（契约 §2.1 students.shared_conflicts）：
 * 键为学科名，值为对方（教学域）记录分。后端也可能返回数组（条目带 subject），
 * 页面统一经 normalizeSharedConflicts 归一为 Map 使用。
 */
export type SharedConflictsValue =
  | Record<string, LinkSharedConflict>
  | Array<LinkSharedConflict & { subject?: string }>

/** 班主任学生行（契约 §2.1）；scores/totals 值缺考为 null，绝不转 0。 */
export interface HomeroomStudentRow {
  person_id: PersonId
  name: string | null
  alias: string | null
  scores: Record<string, number | null>
  totals: Record<string, number | null>
  shared_conflicts?: SharedConflictsValue | null
}

/** GET /api/v1/homeroom/analysis/exams/{exam_name}/students 的响应。 */
export interface HomeroomStudentsResponse {
  metadata: ResponseMetadata
  students: HomeroomStudentRow[]
}

/** 段位查询（契约 §2.1 bands）：metric=score 时须带 subject（学科名）。 */
export interface HomeroomBandsQuery extends AnalysisScopeQuery {
  exam_name: string
  metric: 'score' | 'total'
  subject?: string
}

/** 单个段位；students 为该段位 person_id 清单（前端仅用 count 画条形）。 */
export interface HomeroomBand {
  label: string
  count: number
  students: PersonId[]
}

/** GET /api/v1/homeroom/analysis/bands 的响应。 */
export interface HomeroomBandsResponse {
  bands: HomeroomBand[]
  total_type?: string | null
}

/** 趋势单场数据点（契约 §2.1 trends；E03 等级分可选）。 */
export interface HomeroomTrendPoint {
  exam_name: string
  exam_date: string | null
  score: number | null
  grade_score?: number | null
}

/** 一个学年的分段（结构上防止跨年连算，E03）。 */
export interface HomeroomTrendYear {
  academic_year_id: number
  academic_year_name: string
  subjects: Record<string, HomeroomTrendPoint[]>
  totals: Record<string, HomeroomTrendPoint[]>
}

/** GET /api/v1/homeroom/analysis/trends?person_id= 的响应（无 metadata，按学年分组）。 */
export interface HomeroomTrendsResponse {
  person_id: PersonId
  name: string | null
  years: HomeroomTrendYear[]
}

/** GET /api/v1/teaching/analysis/exams/{exam_name}/stats 的响应（契约 §2.2，单科）。 */
export interface TeachingStatsResponse {
  metadata: ResponseMetadata
  subject: string
  avg: number | null
  max: number | null
  min: number | null
  valid_count: number
  missing_count: number
  /** 名次范围（本教学班内，min-rank）；不可计算时为 null（契约 §2.3 不编造）。 */
  rank_min: number | null
  rank_max: number | null
  score_basis: 'raw' | 'grade'
  cohort_size: number
  small_sample?: boolean
}

/** 教学学生行（契约 §2.2）；rank 为本班内名次，score 缺考为 null。 */
export interface TeachingStudentRow {
  person_id: PersonId
  name: string | null
  class_label: string | null
  score: number | null
  grade_score?: number | null
  rank: number | null
  /** 反向投影的班主任域事实参与名次计算并标注来源域。 */
  source_domain: SourceDomain
}

/** GET /api/v1/teaching/analysis/exams/{exam_name}/students 的响应。 */
export interface TeachingStudentsResponse {
  metadata: ResponseMetadata
  students: TeachingStudentRow[]
}

/** 班级对比条目（契约 §2.2：本班样本均分恒标 estimated，绝不冒充官方口径）。 */
export interface TeachingClassCompareItem {
  teaching_class_id: number
  class_label: string
  member_count: number
  subject_avg: number | null
  score_basis: 'raw' | 'grade'
  source: 'estimated'
}

/** GET /api/v1/teaching/analysis/class-compare 的响应。 */
export interface TeachingClassCompareResponse {
  classes: TeachingClassCompareItem[]
  /** 任一班有效样本 <5 时后端标注（契约 §2.3）。 */
  small_sample?: boolean
}

/* ------------------------------------------------------------------ */
/* 请求基础设施                                                        */
/* ------------------------------------------------------------------ */

const API_V1_BASE = '/api/v1'

type QueryValue = string | number | undefined | null

function withQuery(path: string, query: Record<string, QueryValue>): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${path}?${qs}` : path
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init?.body != null ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
    })
  } catch {
    throw new ApiV1Error(0, 'network_error', '网络请求失败，请检查连接后重试')
  }
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as Record<string, unknown> | null
    throw new ApiV1Error(
      res.status,
      typeof body?.error === 'string' ? body.error : `http_${res.status}`,
      typeof body?.detail === 'string' ? body.detail : undefined,
      body ?? undefined,
    )
  }
  return (await res.json()) as T
}

/**
 * multipart 请求（imports/preview 专用）：不手工设 Content-Type，
 * boundary 必须由浏览器随 FormData 自动生成。
 */
async function requestMultipart<T>(path: string, form: FormData): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, { method: 'POST', headers: { Accept: 'application/json' }, body: form })
  } catch {
    throw new ApiV1Error(0, 'network_error', '网络请求失败，请检查连接后重试')
  }
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as Record<string, unknown> | null
    throw new ApiV1Error(
      res.status,
      typeof body?.error === 'string' ? body.error : `http_${res.status}`,
      typeof body?.detail === 'string' ? body.detail : undefined,
      body ?? undefined,
    )
  }
  return (await res.json()) as T
}

/* ------------------------------------------------------------------ */
/* §3 冻结导出的函数                                                   */
/* ------------------------------------------------------------------ */

/** GET /api/v1/shared/config */
export function fetchSharedConfig(): Promise<SharedConfig> {
  return request<SharedConfig>(`${API_V1_BASE}/shared/config`)
}

/** GET /api/v1/shared/classes?academic_year_id=（班级目录：行政班绑定 + 任教学科全部教学班） */
export function fetchClasses(academicYearId: number): Promise<ClassesCatalog> {
  return request<ClassesCatalog>(
    withQuery(`${API_V1_BASE}/shared/classes`, { academic_year_id: academicYearId }),
  )
}

/** GET /api/v1/shared/scope */
export function fetchScope(q: ScopeQuery): Promise<ScopeState> {
  return request<ScopeState>(withQuery(`${API_V1_BASE}/shared/scope`, { ...q }))
}

/** GET /api/v1/shared/links?academic_year_id= */
export function listLinks(academicYearId?: number): Promise<{ links: LinkSummary[] }> {
  return request<{ links: LinkSummary[] }>(
    withQuery(`${API_V1_BASE}/shared/links`, { academic_year_id: academicYearId }),
  )
}

/** POST /api/v1/shared/links/preview（无任何写入） */
export function previewLink(req: LinkPreviewRequest): Promise<LinkPreview> {
  return request<LinkPreview>(`${API_V1_BASE}/shared/links/preview`, {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

/** POST /api/v1/shared/links/confirm（token 过期/范围或版本变化 → link_version_conflict 409） */
export function confirmLink(token: string): Promise<LinkConfirm> {
  return request<LinkConfirm>(`${API_V1_BASE}/shared/links/confirm`, {
    method: 'POST',
    body: JSON.stringify({ token }),
  })
}

/** POST /api/v1/shared/links/{link_id}/cancel（取消即时生效，域内原生数据保留） */
export function cancelLink(id: number): Promise<LinkCancelResult> {
  return request<LinkCancelResult>(`${API_V1_BASE}/shared/links/${encodeURIComponent(id)}/cancel`, {
    method: 'POST',
  })
}

/** GET /api/v1/shared/links/{link_id}/students（契约 §1.2.1，v2） */
export function listLinkStudents(linkId: number): Promise<LinkStudentsList> {
  return request<LinkStudentsList>(`${API_V1_BASE}/shared/links/${encodeURIComponent(linkId)}/students`)
}

/** POST /api/v1/shared/links/{link_id}/students（整批事务；同名/同号绝不自动配对，只接受显式 person_id 对） */
export function createLinkStudents(linkId: number, req: LinkStudentsRequest): Promise<LinkStudentsResult> {
  return request<LinkStudentsResult>(`${API_V1_BASE}/shared/links/${encodeURIComponent(linkId)}/students`, {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

/** DELETE /api/v1/shared/links/{link_id}/students/{linked_id}（撤销配对后即时停止该生跨域共享） */
export function deleteLinkStudent(linkId: number, linkedId: number): Promise<{ success: boolean }> {
  return request<{ success: boolean }>(
    `${API_V1_BASE}/shared/links/${encodeURIComponent(linkId)}/students/${encodeURIComponent(linkedId)}`,
    { method: 'DELETE' },
  )
}

/** POST /api/v1/shared/links/{link_id}/share-scope（契约 §1.2.2：仅 active link 可改；收紧即时生效） */
export function updateLinkShareScope(linkId: number, req: LinkShareScopeRequest): Promise<LinkShareScope> {
  return request<LinkShareScope>(`${API_V1_BASE}/shared/links/${encodeURIComponent(linkId)}/share-scope`, {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

/** GET /api/v1/{mode}/students */
export function fetchStudents(mode: WorkspaceMode, q: StudentsQuery = {}): Promise<StudentsResponse> {
  return request<StudentsResponse>(withQuery(`${API_V1_BASE}/${mode}/students`, { ...q }))
}

/** 画像作用域查询（与检索一致：班级/学年由后端解析，越界 → 404）。 */
export interface StudentProfileQuery {
  academic_year_id?: number
  term_id?: number
  class_id?: number
  teaching_class_id?: number
}

/** GET /api/v1/{mode}/students/{person_id}（越界 → resource_out_of_scope 404） */
export function fetchStudent(
  mode: WorkspaceMode,
  personId: PersonId,
  q: StudentProfileQuery = {},
): Promise<StudentProfile> {
  return request<StudentProfile>(
    withQuery(`${API_V1_BASE}/${mode}/students/${encodeURIComponent(String(personId))}`, { ...q }),
  )
}

/** GET /api/v1/scores（teaching 模式仅任教学科，不含 total_type 行） */
export function fetchScores(q: ScoresQuery): Promise<ScoresResponse> {
  return request<ScoresResponse>(withQuery(`${API_V1_BASE}/scores`, { ...q }))
}

/* ------------------------------------------------------------------ */
/* P3 冻结导出的函数（契约 p3-imports-analysis.md §1/§2）              */
/* ------------------------------------------------------------------ */

/**
 * POST /api/v1/imports/preview（multipart/form-data，契约 p3 §1.1）。
 * 零业务写入；form 字段 mode/files(/academic_year_id/class_id/teaching_class_id…) 由页面组装。
 */
export function importsPreviewMultipart(form: FormData): Promise<ImportsPreviewResult> {
  return requestMultipart<ImportsPreviewResult>(`${API_V1_BASE}/imports/preview`, form)
}

/**
 * POST /api/v1/imports/confirm（契约 p3 §1.2）。
 * revise=false 遇不同值整批 409 + conflicts 零写入；revise=true 覆写并 data_revision+1。
 * identityConfirmations（F06）：alias_value → 接续的历史 person_id 显式确认映射；
 * 选「新建学生」的别名不进 map，由后端按新学生建档；存在历史命中未确认 → 409 + 候选清单。
 */
export function importsConfirmP3(
  token: string,
  revise = false,
  identityConfirmations?: Record<string, number>,
  identityNewAliases?: string[],
): Promise<ImportsConfirmResult> {
  return request<ImportsConfirmResult>(`${API_V1_BASE}/imports/confirm`, {
    method: 'POST',
    body: JSON.stringify({
      token,
      revise,
      ...(identityConfirmations != null ? { identity_confirmations: identityConfirmations } : {}),
      ...(identityNewAliases != null && identityNewAliases.length > 0
        ? { identity_new_aliases: identityNewAliases }
        : {}),
    }),
  })
}

/**
 * 从 importsConfirmP3 的 409 错误读冲突列表（契约 §1.2）。
 * 形状不符/无条目返回 null，由页面回退到通用错误文案，不阻塞提示。
 */
export function readImportConflicts(err: unknown): ImportConfirmConflict[] | null {
  if (!(err instanceof ApiV1Error) || err.body == null) return null
  const raw: unknown = err.body.conflicts
  if (!Array.isArray(raw)) return null
  const list: ImportConfirmConflict[] = []
  for (const item of raw) {
    if (typeof item !== 'object' || item === null) continue
    const o = item as Record<string, unknown>
    if (typeof o.person !== 'string' || typeof o.exam_name !== 'string') continue
    list.push({
      person: o.person,
      subject: typeof o.subject === 'string' ? o.subject : null,
      exam_name: o.exam_name,
      existing_score: typeof o.existing_score === 'number' ? o.existing_score : null,
      new_score: typeof o.new_score === 'number' ? o.new_score : null,
    })
  }
  return list.length > 0 ? list : null
}

/**
 * 从 imports/confirm 的 409 错误体宽容读取身份候选清单（契约 §1.2 v2.1，F06：
 * 存在历史 alias 命中未确认 → 409 + candidates，零写入）。形状不符/无条目返回 null，
 * 由页面回退到通用错误文案；readImportConflicts 同款宽容模式。
 */
export function readImportIdentityCandidates(err: unknown): ImportIdentityCandidate[] | null {
  if (!(err instanceof ApiV1Error) || err.body == null) return null
  const raw: unknown = err.body.candidates
  if (!Array.isArray(raw)) return null
  const list: ImportIdentityCandidate[] = []
  for (const item of raw) {
    if (typeof item !== 'object' || item === null) continue
    const o = item as Record<string, unknown>
    if (typeof o.alias !== 'string' || o.alias === '') continue
    // 无 person_id 的候选无法作为接续选项（0/空串绝不能当 ID 提交），整条跳过
    if (typeof o.person_id !== 'number' && !(typeof o.person_id === 'string' && o.person_id !== '')) {
      continue
    }
    list.push({
      alias: o.alias,
      name: typeof o.name === 'string' ? o.name : '',
      person_id: o.person_id,
      academic_year_id: typeof o.academic_year_id === 'number' ? o.academic_year_id : 0,
      academic_year_name: typeof o.academic_year_name === 'string' ? o.academic_year_name : '',
      basis: typeof o.basis === 'string' ? o.basis : '',
    })
  }
  return list.length > 0 ? list : null
}

/**
 * 把学生行的 shared_conflicts（契约 §2.1）归一为「学科 → 冲突」映射。
 * 兼容映射与数组两种形状（后端实现未冻结到字段级，此处宽容收窄）。
 */
export function normalizeSharedConflicts(value: unknown): Map<string, LinkSharedConflict> {
  const map = new Map<string, LinkSharedConflict>()
  if (typeof value !== 'object' || value === null) return map
  if (Array.isArray(value)) {
    for (const item of value) {
      if (typeof item !== 'object' || item === null) continue
      const o = item as Record<string, unknown>
      if (typeof o.subject !== 'string') continue
      if (typeof o.teaching_score !== 'number' && o.teaching_score !== null) continue
      map.set(o.subject, { teaching_score: o.teaching_score })
    }
    return map
  }
  for (const [subject, item] of Object.entries(value)) {
    if (typeof item !== 'object' || item === null) continue
    const score: unknown = (item as Record<string, unknown>).teaching_score
    if (typeof score !== 'number' && score !== null) continue
    map.set(subject, { teaching_score: score })
  }
  return map
}

/** GET /api/v1/shared/exams（契约 p3 §1.4：该域该学年当前班级范围已导入考试，日期降序） */
export function listExams(
  mode: WorkspaceMode,
  q: AnalysisScopeQuery = {},
): Promise<{ exams: ExamSummary[] }> {
  return request<{ exams: ExamSummary[] }>(withQuery(`${API_V1_BASE}/shared/exams`, { mode, ...q }))
}

/** GET /api/v1/homeroom/analysis/exams/{exam_name}/stats（契约 p3 §2.1） */
export function fetchHomeroomStats(
  examName: string,
  q: AnalysisScopeQuery = {},
): Promise<HomeroomStatsResponse> {
  return request<HomeroomStatsResponse>(
    withQuery(`${API_V1_BASE}/homeroom/analysis/exams/${encodeURIComponent(examName)}/stats`, { ...q }),
  )
}

/** GET /api/v1/homeroom/analysis/exams/{exam_name}/students（契约 p3 §2.1） */
export function fetchHomeroomStudents(
  examName: string,
  q: AnalysisScopeQuery = {},
): Promise<HomeroomStudentsResponse> {
  return request<HomeroomStudentsResponse>(
    withQuery(`${API_V1_BASE}/homeroom/analysis/exams/${encodeURIComponent(examName)}/students`, { ...q }),
  )
}

/** GET /api/v1/homeroom/analysis/bands（契约 p3 §2.1；阈值读既有 AnalysisConfig 口径） */
export function fetchHomeroomBands(q: HomeroomBandsQuery): Promise<HomeroomBandsResponse> {
  return request<HomeroomBandsResponse>(withQuery(`${API_V1_BASE}/homeroom/analysis/bands`, { ...q }))
}

/** GET /api/v1/homeroom/analysis/trends?person_id=（契约 p3 §2.1；按学年分组，不跨年连算） */
export function fetchHomeroomTrends(
  personId: PersonId,
  q: AnalysisScopeQuery = {},
): Promise<HomeroomTrendsResponse> {
  return request<HomeroomTrendsResponse>(
    withQuery(`${API_V1_BASE}/homeroom/analysis/trends`, { person_id: String(personId), ...q }),
  )
}

/** GET /api/v1/teaching/analysis/exams/{exam_name}/stats（契约 p3 §2.2，单科） */
export function fetchTeachingStats(
  examName: string,
  q: AnalysisScopeQuery = {},
): Promise<TeachingStatsResponse> {
  return request<TeachingStatsResponse>(
    withQuery(`${API_V1_BASE}/teaching/analysis/exams/${encodeURIComponent(examName)}/stats`, { ...q }),
  )
}

/** GET /api/v1/teaching/analysis/exams/{exam_name}/students（契约 p3 §2.2） */
export function fetchTeachingStudents(
  examName: string,
  q: AnalysisScopeQuery = {},
): Promise<TeachingStudentsResponse> {
  return request<TeachingStudentsResponse>(
    withQuery(`${API_V1_BASE}/teaching/analysis/exams/${encodeURIComponent(examName)}/students`, { ...q }),
  )
}

/** GET /api/v1/teaching/analysis/class-compare?exam_name=（契约 p3 §2.2；均分恒标 estimated） */
export function fetchTeachingClassCompare(
  examName: string,
  q: AnalysisScopeQuery = {},
): Promise<TeachingClassCompareResponse> {
  return request<TeachingClassCompareResponse>(
    withQuery(`${API_V1_BASE}/teaching/analysis/class-compare`, { exam_name: examName, ...q }),
  )
}

/* ------------------------------------------------------------------ */
/* P4 冻结导出（契约 docs/contracts/p4-students.md §1-§4）              */
/* ------------------------------------------------------------------ */

/* ---- §1 学年管理（P1 遗留项补齐） ---- */

/** 学年条目（GET /shared/academic-years，start_date 降序）。 */
export interface AcademicYear {
  id: number
  name: string
  start_date: string
  end_date: string
}

/** GET /api/v1/shared/academic-years */
export function listAcademicYears(): Promise<{ years: AcademicYear[] }> {
  return request<{ years: AcademicYear[] }>(`${API_V1_BASE}/shared/academic-years`)
}

/** POST /api/v1/shared/academic-years 的请求体：重名 422；建年后不自动建班。 */
export interface AcademicYearCreateRequest {
  name: string
  start_date: string
  end_date: string
}

/** POST /api/v1/shared/academic-years */
export function createAcademicYear(req: AcademicYearCreateRequest): Promise<AcademicYear> {
  return request<AcademicYear>(`${API_V1_BASE}/shared/academic-years`, {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

/* ---- §2.1 名册 CRUD（ws 域，作用域 = 绑定行政班 + 学年） ---- */

/** POST /api/v1/homeroom/students 的请求体：alias 同学年同域已属他人 → 422（列冲突人）。 */
export interface HomeroomStudentCreateRequest {
  name: string
  alias?: string
  seat_no?: string
}

/** PATCH /api/v1/homeroom/students/{person_id} 的请求体：仅本班当期在班成员，越界 404。 */
export interface HomeroomStudentPatchRequest {
  name?: string
  seat_no?: string
}

/** 在班状态（Enrollment.status）；active 恢复需 valid_to 置空。 */
export type EnrollmentStatus = 'transferred' | 'graduated' | 'active'

/** POST /api/v1/homeroom/students/{person_id}/archive 的请求体：离班写 valid_to + status，绝不物理删。 */
export interface HomeroomArchiveRequest {
  status: EnrollmentStatus
  /** ISO 日期；status=active（恢复在班）时传 null 置空。 */
  valid_to: string | null
}

/**
 * P4 变更类端点的响应（契约未冻结字段；页面成功后一律重拉名册，
 * 不依赖响应体渲染，因此按宽松索引签名收窄、绝不使用 any）。
 */
export interface HomeroomMutationResult {
  [key: string]: unknown
}

/** 422 学号冲突时后端可选返回的冲突人条目（形状未冻结，前端宽容读取）。 */
export interface AliasConflictEntry {
  alias?: string | null
  name?: string | null
  [key: string]: unknown
}

/** POST /api/v1/homeroom/students：新建身份 + 本学年别名 + 在班 Enrollment（单事务）。 */
export function createHomeroomStudent(req: HomeroomStudentCreateRequest): Promise<HomeroomMutationResult> {
  return request<HomeroomMutationResult>(`${API_V1_BASE}/homeroom/students`, {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

/** PATCH /api/v1/homeroom/students/{person_id}：仅改姓名/座号。 */
export function patchHomeroomStudent(
  personId: PersonId,
  req: HomeroomStudentPatchRequest,
): Promise<HomeroomMutationResult> {
  return request<HomeroomMutationResult>(
    `${API_V1_BASE}/homeroom/students/${encodeURIComponent(String(personId))}`,
    { method: 'PATCH', body: JSON.stringify(req) },
  )
}

/** POST /api/v1/homeroom/students/{person_id}/archive：离班/恢复（影响共享时即时生效）。 */
export function archiveHomeroomStudent(
  personId: PersonId,
  req: HomeroomArchiveRequest,
): Promise<HomeroomMutationResult> {
  return request<HomeroomMutationResult>(
    `${API_V1_BASE}/homeroom/students/${encodeURIComponent(String(personId))}/archive`,
    { method: 'POST', body: JSON.stringify(req) },
  )
}

/** POST /api/v1/homeroom/students/{person_id}/alias：追加新学号（换号接续，S08），旧 alias 保留。 */
export function addStudentAlias(
  personId: PersonId,
  req: { alias: string; valid_from: string },
): Promise<HomeroomMutationResult> {
  return request<HomeroomMutationResult>(
    `${API_V1_BASE}/homeroom/students/${encodeURIComponent(String(personId))}/alias`,
    { method: 'POST', body: JSON.stringify(req) },
  )
}

/** 别名历史条目（契约 §2.1 冻结字段）。 */
export interface StudentAliasEntry {
  id: number
  alias_value: string
  valid_from: string | null
  valid_to: string | null
}

/** GET /api/v1/homeroom/students/{person_id}/aliases */
export function listStudentAliases(personId: PersonId): Promise<{ aliases: StudentAliasEntry[] }> {
  return request<{ aliases: StudentAliasEntry[] }>(
    `${API_V1_BASE}/homeroom/students/${encodeURIComponent(String(personId))}/aliases`,
  )
}

/* ---- §2.2 换届（rollover，I01） ---- */

/** 换届预览里的学年引用。 */
export interface RolloverYearRef {
  id: number
  name: string
}

/** 换届预览单人条目：next_alias 仅是建议，确认时逐人可改。 */
export interface RolloverPreviewStudent {
  person_id: PersonId
  name: string | null
  current_alias: string | null
  next_alias?: string | null
  note?: string | null
}

/** GET /homeroom/rollover/preview 的响应（token 来自 preview，R4 同语义：pending/未过期/无成员漂移）。 */
export interface RolloverPreview {
  token: string
  from_year: RolloverYearRef
  to_year: RolloverYearRef
  students: RolloverPreviewStudent[]
}

/** POST /api/v1/homeroom/rollover 的请求体：aliases 为 person_id → 新学年学号。 */
export interface RolloverConfirmRequest {
  token: string
  aliases: Record<string, string>
}

/** POST /api/v1/homeroom/rollover 的响应（字段未冻结，宽松读取；页面展示主要来自请求数据）。 */
export interface RolloverConfirmResult {
  [key: string]: unknown
}

/** 撤销换届时的冲突学生：该生已有新数据（新 alias 上出现成绩/档案/作业行），跳过撤销、保留现状。 */
export interface RolloverUndoConflict {
  person_id?: PersonId
  name?: string | null
  current_alias?: string | null
  [key: string]: unknown
}

/** POST /homeroom/rollover/{token}/undo 的响应（字段未冻结，宽松读取）。 */
export interface RolloverUndoResult {
  [key: string]: unknown
}

/** GET /api/v1/homeroom/rollover/preview?from_academic_year_id=（无新学年 → 409 workspace_not_configured）。 */
export function rolloverPreview(fromAcademicYearId: number): Promise<RolloverPreview> {
  return request<RolloverPreview>(
    withQuery(`${API_V1_BASE}/homeroom/rollover/preview`, {
      from_academic_year_id: fromAcademicYearId,
    }),
  )
}

/** POST /api/v1/homeroom/rollover：单事务建新学年班级 + Enrollment + 新学段 alias；重复确认同 token 409。 */
export function rolloverConfirm(req: RolloverConfirmRequest): Promise<RolloverConfirmResult> {
  return request<RolloverConfirmResult>(`${API_V1_BASE}/homeroom/rollover`, {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

/** POST /api/v1/homeroom/rollover/{token}/undo：快照回滚；conflicted 学生逐人保留现状，不静默覆盖。 */
export function rolloverUndo(token: string): Promise<RolloverUndoResult> {
  return request<RolloverUndoResult>(
    `${API_V1_BASE}/homeroom/rollover/${encodeURIComponent(token)}/undo`,
    { method: 'POST' },
  )
}

/** 从 422 学号冲突错误里宽容读取冲突人列表（形状不符返回 null，页面回退到 detail 文案）。 */
export function readAliasConflicts(err: unknown): AliasConflictEntry[] | null {
  if (!(err instanceof ApiV1Error) || err.body == null) return null
  const raw: unknown = err.body.conflicts
  if (!Array.isArray(raw)) return null
  const list: AliasConflictEntry[] = []
  for (const item of raw) {
    if (typeof item !== 'object' || item === null) continue
    list.push(item as AliasConflictEntry)
  }
  return list.length > 0 ? list : null
}

/* ---- §2.3 打印（学生画像 + 档案摘要） ---- */
/* 类型逐字段对齐 backend/app/api/students_mgmt.py 的 student_report 响应
 * （G05 教训：此前把 notes_summary={count,recent} 误声明成数组导致白屏，
 * 且 alias 位置读错。禁止用宽容索引签名掩盖联调差异。） */

/** 打印视图的成绩点（后端 ProfileExam）：缺考 score=null，展示层显示「—」不转 0。 */
export interface StudentReportExam {
  exam_name: string
  exam_date?: string | null
  score: number | null
  grade_score?: number | null
  source_domain: 'homeroom' | 'teaching'
  shared_conflict?: { teaching_score?: number } | null
}

/** 打印视图单科历场成绩。 */
export interface StudentReportSubject {
  subject: string
  exams: StudentReportExam[]
}

/** 打印视图总分口径（仅班主任域可见）。 */
export interface StudentReportTotal {
  total_type: string
  exams: StudentReportExam[]
}

/** 别名（学号）史条目（后端 AliasItem）。 */
export interface StudentReportAlias {
  id: number
  alias_value: string
  valid_from: string | null
  valid_to: string | null
}

/** 当期名册位：座号/状态/当期学号 + 班级与学年展示名（打印抬头用）。 */
export interface StudentReportRoster {
  class_id: number
  seat_no: number | null
  status: string | null
  alias: string | null
  class_label?: string | null
  academic_year_name?: string | null
}

/** 档案摘要条目（后端 NoteItem，契约 §4：仅本域可见档案进入摘要）。 */
export interface StudentReportNote {
  id: number
  person_id: PersonId
  date: string
  category: string
  content: string
  follow_up: string | null
  follow_up_done: number
  created_at: string | null
}

/** GET /api/v1/homeroom/students/{person_id}/report 的响应（形状冻结）。 */
export interface StudentReportResponse {
  metadata: {
    mode: string
    subject?: string | null
    scope: {
      academic_year_id: number
      term_id: number | null
      class_id: number
      teaching_class_id: number | null
      link_id: number | null
      link_version: number | null
    }
    cohort_size: number
    data_revision: number
  }
  person: {
    person_id: PersonId
    name: string | null
    domain: string
    aliases: StudentReportAlias[]
  }
  roster: StudentReportRoster
  subjects: StudentReportSubject[]
  totals: StudentReportTotal[] | null
  notes_summary: { count: number; recent: StudentReportNote[] }
}

/** GET /api/v1/homeroom/students/{person_id}/report（越界 → resource_out_of_scope 404）。 */
export function getStudentReport(personId: PersonId): Promise<StudentReportResponse> {
  return request<StudentReportResponse>(
    `${API_V1_BASE}/homeroom/students/${encodeURIComponent(String(personId))}/report`,
  )
}

/* ---- §3 教学班成员管理（T 语义） ---- */

/** 教学班成员条目（契约 §3 冻结字段；valid_to 为 null 表示当期在班）。 */
export interface TeachingMember {
  person_id: PersonId
  name: string | null
  alias: string | null
  valid_from: string | null
  valid_to: string | null
  source: string | null
}

/**
 * GET /teaching/classes/{id}/members 的响应：契约允许「当期+历史分列（active/left）」
 * 或「扁平 members 数组（valid_to 判空）」两种形状，页面统一经 normalizeTeachingMembers 归一。
 */
export interface TeachingMembersResponse {
  members?: TeachingMember[]
  active?: TeachingMember[]
  left?: TeachingMember[]
  [key: string]: unknown
}

/** 归一为 { active, left }：优先显式分组，回退按 valid_to 判空拆分。 */
export function normalizeTeachingMembers(resp: TeachingMembersResponse): {
  active: TeachingMember[]
  left: TeachingMember[]
} {
  const asArray = (v: unknown): TeachingMember[] => (Array.isArray(v) ? (v as TeachingMember[]) : [])
  if (Array.isArray(resp.active) || Array.isArray(resp.left)) {
    return { active: asArray(resp.active), left: asArray(resp.left) }
  }
  const active: TeachingMember[] = []
  const left: TeachingMember[] = []
  for (const m of asArray(resp.members)) {
    if (m.valid_to == null) active.push(m)
    else left.push(m)
  }
  return { active, left }
}

/** GET /api/v1/teaching/classes/{teaching_class_id}/members */
export function listTeachingMembers(teachingClassId: number): Promise<TeachingMembersResponse> {
  return request<TeachingMembersResponse>(
    `${API_V1_BASE}/teaching/classes/${encodeURIComponent(String(teachingClassId))}/members`,
  )
}

/** POST /api/v1/teaching/classes/{teaching_class_id}/members：建 teaching 域 identity + member(source='manual')；关联班 409 引导。 */
export function addTeachingMember(
  teachingClassId: number,
  req: { name: string; alias?: string },
): Promise<HomeroomMutationResult> {
  return request<HomeroomMutationResult>(
    `${API_V1_BASE}/teaching/classes/${encodeURIComponent(String(teachingClassId))}/members`,
    { method: 'POST', body: JSON.stringify(req) },
  )
}

/** DELETE /api/v1/teaching/classes/{teaching_class_id}/members/{person_id}：写 valid_to（不物理删）。 */
export function removeTeachingMember(teachingClassId: number, personId: PersonId): Promise<HomeroomMutationResult> {
  return request<HomeroomMutationResult>(
    `${API_V1_BASE}/teaching/classes/${encodeURIComponent(String(teachingClassId))}/members/${encodeURIComponent(String(personId))}`,
    { method: 'DELETE' },
  )
}

/** 文本批量导入预览的单行解析结果（形状未冻结，宽容读取）。 */
export interface TeachingImportLine {
  raw?: string | null
  name?: string | null
  alias?: string | null
  /** match=匹配既有身份；new=确认时新建；invalid=无法解析。 */
  kind?: string | null
  [key: string]: unknown
}

/** POST /members/import 第一段（预览）的响应：import_batch token，R4 语义。 */
export interface TeachingImportPreview {
  token: string
  expires_at?: string | null
  lines?: TeachingImportLine[]
  new_students?: Array<{ name?: string | null; alias?: string | null }>
  [key: string]: unknown
}

/** POST /members/import 第二段（确认）的响应（字段未冻结，宽松读取）。 */
export interface TeachingImportResult {
  [key: string]: unknown
}

/** POST /api/v1/teaching/classes/{id}/members/import {text}：零写入预览，返回 token。 */
export function importTeachingMembersPreview(
  teachingClassId: number,
  text: string,
): Promise<TeachingImportPreview> {
  return request<TeachingImportPreview>(
    `${API_V1_BASE}/teaching/classes/${encodeURIComponent(String(teachingClassId))}/members/import`,
    { method: 'POST', body: JSON.stringify({ text }) },
  )
}

/** POST /api/v1/teaching/classes/{id}/members/import {token}：按预览确认入库。 */
export function importTeachingMembersConfirm(
  teachingClassId: number,
  token: string,
): Promise<TeachingImportResult> {
  return request<TeachingImportResult>(
    `${API_V1_BASE}/teaching/classes/${encodeURIComponent(String(teachingClassId))}/members/import`,
    { method: 'POST', body: JSON.stringify({ token }) },
  )
}

/** sync-from-homeroom 差异条目（形状未冻结，宽容读取）。 */
export interface TeachingSyncDiffEntry {
  name?: string | null
  alias?: string | null
  [key: string]: unknown
}

/** sync-from-homeroom 预览的响应（字段未冻结，宽容读取）。 */
export interface TeachingSyncPreview {
  to_add?: TeachingSyncDiffEntry[]
  already_synced?: TeachingSyncDiffEntry[]
  [key: string]: unknown
}

/** sync-from-homeroom 确认的响应（字段未冻结，宽松读取）。 */
export interface TeachingSyncResult {
  [key: string]: unknown
}

/** POST /api/v1/teaching/classes/{id}/sync-from-homeroom（不带 confirm）：仅 active link 可用，差异预览先行。 */
export function syncFromHomeroomPreview(teachingClassId: number): Promise<TeachingSyncPreview> {
  return request<TeachingSyncPreview>(
    `${API_V1_BASE}/teaching/classes/${encodeURIComponent(String(teachingClassId))}/sync-from-homeroom`,
    { method: 'POST', body: JSON.stringify({ confirm: false }) },
  )
}

/** POST /api/v1/teaching/classes/{id}/sync-from-homeroom {confirm:true}：按 LinkedStudent 交集同步（source='link_projection'）。 */
export function syncFromHomeroomConfirm(teachingClassId: number): Promise<TeachingSyncResult> {
  return request<TeachingSyncResult>(
    `${API_V1_BASE}/teaching/classes/${encodeURIComponent(String(teachingClassId))}/sync-from-homeroom`,
    { method: 'POST', body: JSON.stringify({ confirm: true }) },
  )
}

/* ---- §4 档案（notes，N01 隔离红线：默认按域隔离，绝不跨域读取） ---- */

/** 档案条目（契约 §4 冻结字段）。 */
export interface StudentNote {
  id: number
  data_domain: DataDomain
  person_id: PersonId
  date: string
  category: string
  content: string
  follow_up?: string | null
  follow_up_done?: number | boolean
  source?: string | null
  created_at?: string | null
}

/** 档案作用域查询（班级/学年由后端解析；缺省按当前默认作用域）。 */
export interface NoteScopeQuery {
  academic_year_id?: number
  term_id?: number
  class_id?: number
  teaching_class_id?: number
  subject?: string
}

/** GET /api/v1/{mode}/students/{person_id}/notes：person 须在该 mode 作用域，越界 404。 */
export function listStudentNotes(
  mode: WorkspaceMode,
  personId: PersonId,
  q: NoteScopeQuery = {},
): Promise<{ notes: StudentNote[] }> {
  return request<{ notes: StudentNote[] }>(
    withQuery(`${API_V1_BASE}/${mode}/students/${encodeURIComponent(String(personId))}/notes`, { ...q }),
  )
}

/** POST /api/v1/{mode}/students/{person_id}/notes。 */
export function createNote(
  mode: WorkspaceMode,
  personId: PersonId,
  req: { date: string; category: string; content: string; follow_up?: string | null },
  q: NoteScopeQuery = {},
): Promise<StudentNote> {
  return request<StudentNote>(
    withQuery(`${API_V1_BASE}/${mode}/students/${encodeURIComponent(String(personId))}/notes`, { ...q }),
    { method: 'POST', body: JSON.stringify(req) },
  )
}

/** PATCH /api/v1/{mode}/notes/{note_id}：note 必须属于当前 mode 域；follow_up_done 为 0/1。 */
export function patchNote(
  mode: WorkspaceMode,
  noteId: number,
  req: { date?: string; category?: string; content?: string; follow_up?: string | null; follow_up_done?: number },
  q: NoteScopeQuery = {},
): Promise<StudentNote> {
  return request<StudentNote>(withQuery(`${API_V1_BASE}/${mode}/notes/${encodeURIComponent(String(noteId))}`, { ...q }), {
    method: 'PATCH',
    body: JSON.stringify(req),
  })
}

/** DELETE /api/v1/{mode}/notes/{note_id}：note 必须属于当前 mode 域。 */
export function deleteNote(
  mode: WorkspaceMode,
  noteId: number,
  q: NoteScopeQuery = {},
): Promise<{ success: boolean }> {
  return request<{ success: boolean }>(
    withQuery(`${API_V1_BASE}/${mode}/notes/${encodeURIComponent(String(noteId))}`, { ...q }),
    { method: 'DELETE' },
  )
}

/* ------------------------------------------------------------------ */
/* P5 冻结导出（契约 docs/contracts/p5-homework.md §1-§5）              */
/* 字段名以 backend/app/api/homework_schemas.py 为唯一事实源。           */
/* ------------------------------------------------------------------ */

/** 交作业状态四值（契约 §0：无记录不推断已交；unknown 不冒充连续也不断言已交）。 */
export type HomeworkStatus = 'submitted' | 'missing' | 'excused' | 'unknown'

/** 录入模式（契约 §1.1）：full=全交台账+例外 / names=名单 / detailed=逐行明细。 */
export type HomeworkInputKind = 'full' | 'names' | 'detailed'

/** 作业端点共用作用域参数（mode 由函数首参显式携带——后端缺 mode 直接 422，绝不退化全年级）。 */
export interface HomeworkScopeQuery {
  academic_year_id?: number
  /** homeroom 模式使用；缺省 = 教师绑定班。 */
  class_id?: number
  /** teaching 模式使用；缺省 = 全部所教班并集（写入路径多班时后端 422 要求显式）。 */
  teaching_class_id?: number
}

/** 逐人行/例外行：person_id 与 name_or_alias 二选一（同班同名必须用学号或 person_id 消歧）。 */
export interface HomeworkRowInput {
  person_id?: PersonId
  name_or_alias?: string | null
  status: HomeworkStatus | string
  evaluation?: string | null
}

/** 三模式录入规格（契约 §1.1；full 的例外在全员展开后整体覆盖，行顺序不影响结果）。 */
export interface HomeworkInputSpec {
  kind: HomeworkInputKind | string
  names?: string[]
  rows?: HomeworkRowInput[]
  all_submitted?: boolean
  exceptions?: HomeworkRowInput[]
}

/** POST /homework/preview 的请求体（零写入解析）。 */
export interface HomeworkPreviewRequest {
  mode: WorkspaceMode
  class_id?: number
  teaching_class_id?: number
  academic_year_id?: number
  subject: string
  homework_type: string
  /** ISO 日期（YYYY-MM-DD）。 */
  assigned_date: string
  due_date?: string
  input: HomeworkInputSpec
}

export interface HomeworkPersonBrief {
  person_id: PersonId
  name: string | null
}

export interface HomeworkSubmissionBrief {
  person_id: PersonId
  name: string | null
  status: string
  evaluation?: string | null
}

/** 同日同科同种类已有批次（契约 §1.1 H02：提示「编辑既有或新建」，绝不自动叠加）。 */
export interface HomeworkExistingBatch {
  assignment_id: number
  batch_token: string
  revision: number
}

export interface HomeworkPreviewAssignment {
  subject: string
  homework_type: string
  assigned_date: string
  due_date?: string | null
  expected_members: HomeworkPersonBrief[]
  submissions: HomeworkSubmissionBrief[]
  warnings: string[]
}

export interface HomeworkPreviewResponse {
  token: string
  expires_at: string
  assignment: HomeworkPreviewAssignment
  existing_batches: HomeworkExistingBatch[]
}

/** POST /homework/confirm 的响应（同 token 重试幂等返回既有批次统计）。 */
export interface HomeworkConfirmResponse {
  assignment_id: number
  revision: number
  submitted: number
  missing: number
  excused: number
  unknown: number
}

/** PATCH /homework/assignments/{id} 的请求体（revision 乐观锁必带）。 */
export interface HomeworkPatchRequest {
  revision: number
  rows?: HomeworkRowInput[]
  due_date?: string | null
}

export interface HomeworkPatchResponse {
  assignment_id: number
  revision: number
  updated: number
}

export interface HomeworkDeleteResponse {
  success: boolean
  assignment_id: number
  status: string
  revision: number
}

/**
 * 计数与提交率（契约 §0 H03 红线）：分母 = 应交快照（确认时冻结）− excused；
 * 快照为空 → submission_rate 为 null 且 rate_unavailable=true，前端绝不显示百分比。
 */
export interface HomeworkRateStats {
  expected_count: number
  submitted: number
  missing: number
  excused: number
  unknown: number
  submission_rate: number | null
  rate_unavailable: boolean
}

export interface HomeworkAssignmentListItem extends HomeworkRateStats {
  assignment_id: number
  data_domain: DataDomain
  subject: string
  homework_type: string
  assigned_date: string
  due_date?: string | null
  revision: number
  status: string
}

export interface HomeworkAssignmentListResponse {
  metadata: ResponseMetadata
  total: number
  items: HomeworkAssignmentListItem[]
}

export interface HomeworkSubmissionOut extends HomeworkSubmissionBrief {}

export interface HomeworkAssignmentDetail extends HomeworkRateStats {
  metadata: ResponseMetadata
  assignment_id: number
  data_domain: DataDomain
  academic_year_id: number
  subject: string
  homework_type: string
  assigned_date: string
  due_date?: string | null
  revision: number
  status: string
  expected_members: HomeworkPersonBrief[]
  submissions: HomeworkSubmissionOut[]
}

export interface HomeworkDashboardGroup extends HomeworkRateStats {
  /** 周聚合 = 该周周一 ISO 日期；月聚合 = 月首 ISO 日期。 */
  label: string
  assignments: number
}

export interface HomeworkDashboardResponse {
  metadata: ResponseMetadata
  group_by: string
  /** 日维度聚合口径（后端固定 'day'；区别于预警的事件口径）。 */
  basis: string
  groups: HomeworkDashboardGroup[]
}

export interface HomeworkStudentEvent {
  assignment_id: number
  assigned_date: string
  subject: string
  homework_type: string
  status: string
  evaluation?: string | null
}

/**
 * 学生连续缺交（契约 §2）：遇 unknown → current_missing_streak 置 null 且
 * streak_basis='unknown'（不冒充连续，也不断言已交）；前端必须标注「连续性未知」。
 */
export interface HomeworkStudentStreaks {
  current_missing_streak: number | null
  streak_basis: string
  longest_missing_streak: number
}

export interface HomeworkStudentResponse {
  metadata: ResponseMetadata
  person_id: PersonId
  name: string | null
  events: HomeworkStudentEvent[]
  streaks: HomeworkStudentStreaks
}

export interface HomeworkRecentMissing {
  assignment_id: number
  assigned_date: string
  subject: string
  homework_type: string
}

export interface HomeworkWarningStudent {
  person_id: PersonId
  name: string | null
  missing_count: number
  current_streak: number | null
  streak_basis: string
  recent_missing: HomeworkRecentMissing[]
}

/** 预警响应（basis='events'：按收交事件逐次统计，非按日折算）。 */
export interface HomeworkWarningsResponse {
  metadata: ResponseMetadata
  basis: string
  min_missing: number
  students: HomeworkWarningStudent[]
}

export interface HomeworkCorrelationPair {
  person_id: PersonId
  name: string | null
  /** x = 作业提交率（0..1）。 */
  x: number
  /** y = 名次（数值越小名次越好）。 */
  y: number
}

export interface HomeworkCorrelationResponse {
  metadata: ResponseMetadata
  pairs: HomeworkCorrelationPair[]
  n: number
  /** n<5 或零方差 → null（不可计算），caveats 注明；绝不编造数值。 */
  r: number | null
  /** submit_up_rank_up / submit_up_rank_down；r 为 null 时为 null。 */
  direction: string | null
  caveats: string[]
}

export interface HomeworkSemesterEntry {
  /** auto 推导条目无 id（null）：只读展示，不可编辑/设当前。 */
  id: number | null
  name: string
  start_date: string
  end_date: string
  is_current: boolean
  /** auto = 按日期自动推导；manual = 手工维护。 */
  mode: string
}

export interface HomeworkSemestersResponse {
  academic_year_id: number
  academic_year_name: string
  /** true = 该学年无手工数据，列表为自动推导（条目 id=null）。 */
  auto: boolean
  semesters: HomeworkSemesterEntry[]
}

/** POST /semesters/{id}/restore-auto 的响应：丢弃手工日期前后的对比。 */
export interface HomeworkSemesterRestoreResponse {
  restored: boolean
  before: HomeworkSemesterEntry
  after: HomeworkSemesterEntry[]
}

export interface HomeworkSemesterCurrentResponse {
  id: number
  academic_year_id: number
  is_current: boolean
}

/** DELETE 409 冲突清单条目：该批次上有后续评价编辑的人。 */
export interface HomeworkRevokeConflict {
  person_id: PersonId
  name: string | null
  submission_status: string
  updated_at: string | null
}

/** preview 422（同班同名歧义）候选条目：用学号或 person_id 消歧。 */
export interface HomeworkAmbiguityCandidate {
  person_id: PersonId
  name: string | null
  alias: string | null
}

/** POST /api/v1/homework/preview（零写入解析，token 化；existing_batches 提示同日既有批次）。 */
export function homeworkPreview(req: HomeworkPreviewRequest): Promise<HomeworkPreviewResponse> {
  return request<HomeworkPreviewResponse>(`${API_V1_BASE}/homework/preview`, {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

/** POST /api/v1/homework/confirm（token 单次消费 + 成员漂移校验；同 token 重试幂等）。 */
export function homeworkConfirm(token: string): Promise<HomeworkConfirmResponse> {
  return request<HomeworkConfirmResponse>(`${API_V1_BASE}/homework/confirm`, {
    method: 'POST',
    body: JSON.stringify({ token }),
  })
}

/** GET /api/v1/homework/assignments（assigned_date 降序分页；含 rate_unavailable 标注）。 */
export function homeworkAssignmentsList(
  mode: WorkspaceMode,
  q: HomeworkScopeQuery & {
    subject?: string
    homework_type?: string
    from_date?: string
    to_date?: string
    limit?: number
    offset?: number
  } = {},
): Promise<HomeworkAssignmentListResponse> {
  return request<HomeworkAssignmentListResponse>(
    withQuery(`${API_V1_BASE}/homework/assignments`, { mode, ...q }),
  )
}

/** GET /api/v1/homework/assignments/{id}（详情；跨域共享批次返回读域投影成员）。 */
export function homeworkAssignmentDetail(
  assignmentId: number,
  mode: WorkspaceMode,
  q: HomeworkScopeQuery = {},
): Promise<HomeworkAssignmentDetail> {
  return request<HomeworkAssignmentDetail>(
    withQuery(`${API_V1_BASE}/homework/assignments/${encodeURIComponent(String(assignmentId))}`, {
      mode,
      ...q,
    }),
  )
}

/** PATCH /api/v1/homework/assignments/{id}（逐行 upsert + revision 乐观锁；旧版 409）。 */
export function homeworkPatchAssignment(
  assignmentId: number,
  mode: WorkspaceMode,
  req: HomeworkPatchRequest,
  q: HomeworkScopeQuery = {},
): Promise<HomeworkPatchResponse> {
  return request<HomeworkPatchResponse>(
    withQuery(`${API_V1_BASE}/homework/assignments/${encodeURIComponent(String(assignmentId))}`, {
      mode,
      ...q,
    }),
    { method: 'PATCH', body: JSON.stringify(req) },
  )
}

/** DELETE /api/v1/homework/assignments/{id}（软撤销；有后续评价编辑 → 409 conflicts 清单）。 */
export function homeworkDeleteAssignment(
  assignmentId: number,
  mode: WorkspaceMode,
  q: HomeworkScopeQuery = {},
): Promise<HomeworkDeleteResponse> {
  return request<HomeworkDeleteResponse>(
    withQuery(`${API_V1_BASE}/homework/assignments/${encodeURIComponent(String(assignmentId))}`, {
      mode,
      ...q,
    }),
    { method: 'DELETE' },
  )
}

/** GET /api/v1/homework/dashboard（按周/月聚合；仅计有可靠分母的批次）。 */
export function homeworkDashboard(
  mode: WorkspaceMode,
  q: HomeworkScopeQuery & { subject?: string; homework_type?: string } = {},
  groupBy: 'week' | 'month' = 'month',
): Promise<HomeworkDashboardResponse> {
  return request<HomeworkDashboardResponse>(
    withQuery(`${API_V1_BASE}/homework/dashboard`, { mode, group_by: groupBy, ...q }),
  )
}

/** GET /api/v1/homework/students/{person_id}（学生事件流 + streaks，画像页消费；越界 404）。 */
export function homeworkStudentEvents(
  personId: PersonId,
  mode: WorkspaceMode,
  q: HomeworkScopeQuery = {},
): Promise<HomeworkStudentResponse> {
  return request<HomeworkStudentResponse>(
    withQuery(`${API_V1_BASE}/homework/students/${encodeURIComponent(String(personId))}`, {
      mode,
      ...q,
    }),
  )
}

/** GET /api/v1/homework/warnings（缺交预警时间轴，事件口径 basis='events'）。 */
export function homeworkWarnings(
  mode: WorkspaceMode,
  q: HomeworkScopeQuery & { min_missing?: number; subject?: string; homework_type?: string } = {},
): Promise<HomeworkWarningsResponse> {
  return request<HomeworkWarningsResponse>(withQuery(`${API_V1_BASE}/homework/warnings`, { mode, ...q }))
}

/** GET /api/v1/homework/correlation（成绩 × 作业 Pearson 描述统计；绝不表述为因果）。 */
export function homeworkCorrelation(
  mode: WorkspaceMode,
  q: HomeworkScopeQuery & {
    subject: string
    exam_name: string
    homework_type?: string
    /** 仅 homeroom 口径生效：Y = 指定 total_type 总分名次（默认主三门）。 */
    total_type?: string
  },
): Promise<HomeworkCorrelationResponse> {
  return request<HomeworkCorrelationResponse>(withQuery(`${API_V1_BASE}/homework/correlation`, { mode, ...q }))
}

/** GET /api/v1/homework/semesters（auto=true 时条目为按学年日期二分的推导值，id=null）。 */
export function homeworkSemesters(academicYearId?: number): Promise<HomeworkSemestersResponse> {
  return request<HomeworkSemestersResponse>(
    withQuery(`${API_V1_BASE}/homework/semesters`, { academic_year_id: academicYearId }),
  )
}

/** POST /api/v1/homework/semesters（同学年重名/日期重叠 → 422 中文 detail）。 */
export function homeworkCreateSemester(req: {
  academic_year_id: number
  name: string
  start_date: string
  end_date: string
}): Promise<HomeworkSemestersResponse> {
  return request<HomeworkSemestersResponse>(`${API_V1_BASE}/homework/semesters`, {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

/** PUT /api/v1/homework/semesters/{id}（改过即转手工模式；可经 restore-auto 回自动）。 */
export function homeworkUpdateSemester(
  semesterId: number,
  req: { name?: string; start_date?: string; end_date?: string },
): Promise<HomeworkSemestersResponse> {
  return request<HomeworkSemestersResponse>(
    `${API_V1_BASE}/homework/semesters/${encodeURIComponent(String(semesterId))}`,
    { method: 'PUT', body: JSON.stringify(req) },
  )
}

/** POST /api/v1/homework/semesters/{id}/restore-auto（丢弃手工日期回自动推导）。 */
export function homeworkRestoreAutoSemester(semesterId: number): Promise<HomeworkSemesterRestoreResponse> {
  return request<HomeworkSemesterRestoreResponse>(
    `${API_V1_BASE}/homework/semesters/${encodeURIComponent(String(semesterId))}/restore-auto`,
    { method: 'POST' },
  )
}

/** PUT /api/v1/homework/semesters/{id}/current（重复设同一条 → 422，不 500）。 */
export function homeworkSetCurrentSemester(semesterId: number): Promise<HomeworkSemesterCurrentResponse> {
  return request<HomeworkSemesterCurrentResponse>(
    `${API_V1_BASE}/homework/semesters/${encodeURIComponent(String(semesterId))}/current`,
    { method: 'PUT' },
  )
}

/**
 * 从 DELETE 批次的 409 错误体读撤销冲突清单（形状不符/无条目返回 null，
 * 由页面回退通用文案；readImportConflicts 同款宽容模式）。
 */
export function readHomeworkRevokeConflicts(err: unknown): HomeworkRevokeConflict[] | null {
  if (!(err instanceof ApiV1Error) || err.body == null) return null
  const raw: unknown = err.body.conflicts
  if (!Array.isArray(raw)) return null
  const list: HomeworkRevokeConflict[] = []
  for (const item of raw) {
    if (typeof item !== 'object' || item === null) continue
    const o = item as Record<string, unknown>
    if (typeof o.person_id !== 'number' && !(typeof o.person_id === 'string' && o.person_id !== '')) continue
    list.push({
      person_id: o.person_id,
      name: typeof o.name === 'string' ? o.name : null,
      submission_status: typeof o.submission_status === 'string' ? o.submission_status : '',
      updated_at: typeof o.updated_at === 'string' ? o.updated_at : null,
    })
  }
  return list.length > 0 ? list : null
}

/**
 * 从 preview 的 422 错误体宽容读取同名歧义候选清单（契约 §1.1：同班同名 → 422 列候选，
 * 学号优先消歧）。形状不符/无条目返回 null。
 */
export function readHomeworkAmbiguityCandidates(err: unknown): HomeworkAmbiguityCandidate[] | null {
  if (!(err instanceof ApiV1Error) || err.body == null) return null
  const raw: unknown = err.body.candidates
  if (!Array.isArray(raw)) return null
  const list: HomeworkAmbiguityCandidate[] = []
  for (const item of raw) {
    if (typeof item !== 'object' || item === null) continue
    const o = item as Record<string, unknown>
    if (typeof o.person_id !== 'number' && !(typeof o.person_id === 'string' && o.person_id !== '')) continue
    list.push({
      person_id: o.person_id,
      name: typeof o.name === 'string' ? o.name : null,
      alias: typeof o.alias === 'string' ? o.alias : null,
    })
  }
  return list.length > 0 ? list : null
}

/* ------------------------------------------------------------------ */
/* P6 冻结导出（契约 docs/contracts/p6-ai-mcp.md §1）                  */
/* ------------------------------------------------------------------ */

/**
 * POST /api/v1/chat/sessions 请求体：只携带 mode + 资源 ID（契约 §0 红线：
 * scope 由服务端重新解析冻结，绝不信任客户端提交的成员/学科清单）。
 * subject 缺省由服务端按教师任教配置推导；多学科教学时后端 422 要求显式。
 */
export interface ChatSessionCreateRequest {
  mode: WorkspaceMode
  academic_year_id?: number
  /** homeroom：行政班；缺省 = 教师绑定班（服务端解析）。 */
  class_id?: number
  /** teaching：教学班；缺省 = 全部所教班并集。 */
  teaching_class_id?: number
  /** teaching：任教学科；仅多学科教师需显式携带。 */
  subject?: string
}

/**
 * 会话作用域快照的对外投影（契约 §1）：含 cohort_size 人数摘要，**绝不含
 * member_person_ids 成员明单**——名单明细只能由模型经 search_students 等
 * 只读工具按需查询，接口层不泄露名单。
 */
export interface ChatScopeInfo {
  mode: WorkspaceMode
  data_domain: DataDomain
  academic_year_id?: number | null
  class_ids: number[]
  subject?: string | null
  link_id?: number | null
  link_version?: number | null
  /** 快照冻结时间（服务端生成；不参与漂移判定）。 */
  as_of: string
  cohort_size: number
}

export interface ChatSessionResponse {
  session_id: number
  scope: ChatScopeInfo
}

export interface ChatCloseResponse {
  session_id: number
  status: string
}

/** POST /api/v1/chat/sessions（无模型 Key → 409 workspace_not_configured，不半开） */
export function createChatSession(req: ChatSessionCreateRequest): Promise<ChatSessionResponse> {
  return request<ChatSessionResponse>(`${API_V1_BASE}/chat/sessions`, {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

/** GET /api/v1/chat/sessions/{id}（快照核对用；对外投影不含成员明单） */
export function getChatSession(sessionId: number): Promise<ChatSessionResponse> {
  return request<ChatSessionResponse>(
    `${API_V1_BASE}/chat/sessions/${encodeURIComponent(String(sessionId))}`,
  )
}

/** POST /api/v1/chat/sessions/{id}/close（显式关闭；重复关闭幂等） */
export function closeChatSession(sessionId: number): Promise<ChatCloseResponse> {
  return request<ChatCloseResponse>(
    `${API_V1_BASE}/chat/sessions/${encodeURIComponent(String(sessionId))}/close`,
    { method: 'POST' },
  )
}

/* ------------------------------------------------------------------ */
/* Q10 跨域冲突规范值确认（契约 docs/contracts/p3-imports-analysis.md §1.6） */
/* ------------------------------------------------------------------ */

/** 单条当前冲突（H 侧视角；缺考为 null，展示层绝不转 0）。 */
export interface ScoreConflictItem {
  person_id: PersonId
  name: string | null
  subject: string
  exam_name: string
  exam_date: string | null
  homeroom_score: number | null
  teaching_score: number | null
}

/** GET /api/v1/shared/links/{link_id}/score-conflicts 的响应。 */
export interface ScoreConflictsResponse {
  conflicts: ScoreConflictItem[]
}

/** 规范值来源侧（契约 §1.6 v2.3）：必属两域之一。 */
export type CanonicalSide = 'homeroom' | 'teaching'

/**
 * 单条规范值确认（契约 §1.6 v2.3）：按"来源侧"选择——规范值 = 该侧
 * 当前值（可为 null=缺考：核实后缺考是合法规范结果，不得强制改成实分）；
 * 同一冲突一次只选一侧，本阶段不做人工改分。
 */
export interface CanonicalResolution {
  person_id: PersonId
  subject: string
  exam_name: string
  canonical_side: CanonicalSide
  /** 缺省 canonical_confirm:<日期>；审计由 source/data_revision 与回执承载。 */
  basis?: string
}

/** POST /api/v1/shared/links/{link_id}/canonical-scores 的响应回执。 */
export interface CanonicalResult {
  resolved: number
  skipped: number
  facts: Array<{
    person_id: PersonId
    subject: string
    exam_name: string
    score: number | null
    data_revision: number
  }>
}

/** GET /api/v1/shared/links/{link_id}/score-conflicts（link 非 active → 409）。 */
export function listScoreConflicts(linkId: number): Promise<ScoreConflictsResponse> {
  return request<ScoreConflictsResponse>(
    `${API_V1_BASE}/shared/links/${encodeURIComponent(String(linkId))}/score-conflicts`,
  )
}

/**
 * POST /api/v1/shared/links/{link_id}/canonical-scores：单事务写两域
 * （score = 所选侧现值，含 null=缺考 + data_revision+1 + source 追加
 * canonical 标记）；任一条不对应真实冲突 → 422 整批零写入；
 * 已一致的重复提交 → skipped。
 */
export function confirmCanonicalScores(
  linkId: number,
  resolutions: CanonicalResolution[],
): Promise<CanonicalResult> {
  return request<CanonicalResult>(
    `${API_V1_BASE}/shared/links/${encodeURIComponent(String(linkId))}/canonical-scores`,
    { method: 'POST', body: JSON.stringify({ resolutions }) },
  )
}

/**
 * 会话历史消息条目（契约 §0.1 Q03：服务端 ChatMessage 持久化，刷新恢复以
 * 服务端为准；role 限用户/助手两态，工具事件不在对外投影内）。
 */
export interface ChatMessageItem {
  id: number
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

/** GET /api/v1/chat/sessions/{id}/messages（服务端历史；范围漂移 → 409，历史保留只读禁复用） */
export function getChatMessages(sessionId: number): Promise<{ messages: ChatMessageItem[] }> {
  return request<{ messages: ChatMessageItem[] }>(
    `${API_V1_BASE}/chat/sessions/${encodeURIComponent(String(sessionId))}/messages`,
  )
}
