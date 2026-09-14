# P1 接口与前端契约（v2，2026-09-11）

本文件是 P1 实现（后端 /api/v1、测试、前端外壳）与 P2 前端工作的唯一契约事实源。
v2 依评审意见（R3/R4/R5/R6/R10 ）修订；
v1 其余条款未列出者继续有效。变更须经集成者修订本文件并同步所有实现方；实现不得自行偏离。

## 0. 通用约定

- 所有 `*_id` 参数与字段均为**整数**（数据库主键）；`2025-2026` 这类是学年**名称**（`academic_year_name`），只作显示，不作 ID 传递。
- 所有新端点挂载在 FastAPI 应用（app.main:app），前缀 `/api/v1`。
- 业务错误统一 JSON：`{"error": "<error_code>"}`，可选 `detail` 字段（人类可读中文）。
  - `workspace_not_configured` → HTTP 409（工作台未配置）
  - `invalid_scope_param` → HTTP 422（参数非法）
  - `resource_out_of_scope` → HTTP 404（合法格式但不在允许范围）
  - `link_version_conflict` → HTTP 409（token 过期/范围或版本变化/乐观锁冲突）
- 所有 200 数据响应含 `metadata`：`{mode, subject, scope, cohort_size, data_revision}`；
  `scope` 为对象 `{academic_year_id, term_id, class_id | teaching_class_id, link_id?, link_version?}`。
- 缺考成绩 `score: null`，不得转为 0。
- 空成员范围是合法空态：200 + `member_person_ids: []` / `students: []` / `rows: []`，绝不回退全年级。

## 1. 端点明细

### 1.1 配置与范围

- `GET /api/v1/shared/config`
  响应：`{teacher: {id, name}, homeroom: {configured, grade, class_num}, teaching: {configured, subject}, current_academic_year: {id, name} | null, links: [LinkSummary]}`
  `LinkSummary = {id, admin_class_id, teaching_class_id, academic_year_id, academic_year_name, subject, status, version, valid_from, valid_to}`
  `current_academic_year` 为服务端解析的最新学年（start_date 最大者；无学年为 null），前端以此为默认学年，不用从日期推导。

- `GET /api/v1/shared/classes?academic_year_id=`
  响应：`{academic_year_id, academic_year_name, homeroom: {class_id, grade, class_num, label} | null, teaching: [{class_id, label, subject}]}`
  `homeroom` 为该学年教师绑定的行政班（未绑定为 null）；`teaching` 为该学年教师任教学科下的全部教学班。前端关联配置向导的班级下拉以此为准，不再需要手动输入 ID。

- `GET /api/v1/shared/scope?mode=homeroom|teaching&academic_year_id=&term_id=&class_id=`（homeroom）
  或 `?mode=teaching&academic_year_id=&term_id=&teaching_class_id=&subject=`（teaching；不传 teaching_class_id 表示"全部所教班"，取同学年同学科成员并集）
  响应：`{mode, data_domain, subject, member_person_ids, cohort_size, link_id, link_version, as_of}`

### 1.2 班级关联（HomeroomTeachingLink）

- `GET /api/v1/shared/links?academic_year_id=` → `{links: [LinkSummary]}`
- `POST /api/v1/shared/links/preview`
  请求：`{admin_class_id, teaching_class_id, academic_year_id, subject}`
  响应：`{token, expires_at, roster_diff: {both: [StudentBrief], homeroom_only: [StudentBrief], teaching_only: [StudentBrief]}, warning}`
  `StudentBrief = {person_id, name, alias}`；无任何业务写入（仅 import_batch 台账行）。
  `both` 仅列出**已确认 LinkedStudent** 对应的双侧成员（同对班已存在 link 时）；同名同号绝不自动配对；
  `homeroom_only`/`teaching_only` 须扣除已配对成员（候选集合与计数一致）。
  快照必须保存双侧成员 person_id 集合（`homeroom_member_ids` / `teaching_member_ids`），confirm 时逐项比对。
  **v2.1（F02）**：目标班对已存在 link 时，快照记录 `link_id + link_version + link_status`；
  confirm 校验绑定与当前一致——已取消 / 版本变化（含 share-scope 修改）→ 409，必须重新 preview。
  全新配对（无既有 link）无此绑定；取消后重建一律走取消之后的新 preview。
- `POST /api/v1/shared/links/confirm`
  请求：`{token}`；校验（全部失败统一 `link_version_conflict` 409，零业务写入）：
  1. token 存在且未过期；`import_batch.status == 'pending'`（已 confirmed/consumed 的 token 再次提交 → 409，不复活任何状态）；
  2. 班级/学年/学科与当前库一致；
  3. 双侧当前成员集合与 preview 快照完全一致（成员漂移 → 409，要求重新预览）；
  4. 同学年同学科无其他 active link 占用该行政班。
  成功：创建（或复活）link，`import_batch.status='confirmed'`，响应 `{link_id, version, linked_count}`。
  被取消的同对班 link 只能经**新 preview token** 复活（version+1）；旧 token 一律 409。
- `POST /api/v1/shared/links/{link_id}/cancel`
  响应：`{success: true, status: "cancelled"}`；取消即时生效，域内原生数据保留。

#### 1.2.1 学生配对（LinkedStudent 管理，v2 新增——R6）

- `GET /api/v1/shared/links/{link_id}/students` → `{link_id, pairs: [{linked_id, homeroom_person_id, teaching_person_id, homeroom_name, teaching_name, confirm_basis}]}`
- `POST /api/v1/shared/links/{link_id}/students`
  请求：`{pairs: [{homeroom_person_id, teaching_person_id}], confirm_basis?: str}`（一次 1..N 对，整批事务）
  逐对校验，任一失败整批 422/404 且零写入：
  - link 存在且 `status='active'` 且在有效期内；
  - homeroom 人属于该 link 行政班当期有效 enrollment（active）；
  - teaching 人属于该 link 教学班当期有效 membership；
  - 一一映射：本请求内及库中既有 LinkedStudent 均不得出现同一 homeroom 人配多个 teaching 人（或反之）；
  - 禁止按同名/同号自动配对（本端点只接受显式 person_id 对）。
  幂等：已存在完全相同的对 → 跳过不报错；同 h 不同 t（或反之）→ 422 `invalid_scope_param`。
  响应：`{created: n, skipped: n, pairs: [...]}`。
- `DELETE /api/v1/shared/links/{link_id}/students/{linked_id}` → `{success: true}`；越界 404。撤销配对即时停止该生跨域共享。

#### 1.2.2 共享范围语义（v2 明确——R3）

- `HomeroomTeachingLink.share_categories`（逗号分隔）仅识别：`roster`、`current_subject_score`、`current_subject_homework`。
  默认 `roster,current_subject_score`；不含 `current_subject_score` 时，成绩读接口不得做任何跨域成绩投影。
- `share_history_from`（Date，可空）：显式历史授权日期，**可以早于 valid_from**（用于重新开放关联生效前的历史）。有效共享下限 = `share_history_from ?? valid_from`；考试日期早于该下限的事实不得共享。为空时等于 valid_from（默认不开放关联生效前历史）。考试日期未知（exam_date IS NULL）的事实**不得共享**（不得默认放开）。
- `POST /api/v1/shared/links/{link_id}/share-scope`
  请求：`{share_categories?: [str], share_history_from?: date | null}`；仅 active link 可改；响应 `{link_id, version, share_categories, share_history_from}`。
  收紧（移除成绩共享/撤销历史授权）即时生效。

### 1.3 学生（域隔离读 + 关联投影）

- `GET /api/v1/homeroom/students?academic_year_id=&class_id=`
  `{metadata, students: [{person_id, name, seat_no, alias, status, linked_teaching_class_id?, shared_subject_score?}]}`
  `shared_subject_score` 仅出现在"已确认关联且在有效期"的学生上（当前任教学科、来自 teaching 域投影，`{subject, score, exam_name, source_domain: "teaching"}` 或多场则最近一场）。
  **v2.1（F03）**：名册行的姓名/座号投影用查询时点；但任何共享分数字段（`shared_subject_score`、
  `shared_conflict`）必须逐 fact 通过 §1.4.1 五条件门（**含按考试时点的双侧成员交集**），
  不得以查询时点成员校验替代。入班日晚于考试日的成绩不得出现在名册响应中。
- `GET /api/v1/teaching/students?academic_year_id=&teaching_class_id=`
  同构；关联班成员的 `name/seat_no` 来自 H 名册投影；非关联教学班仅 teaching 域数据。
- `GET /api/v1/homeroom/students/{person_id}` / `GET /api/v1/teaching/students/{person_id}`
  画像：`{metadata, person: {person_id, name, domain}, subjects: [{subject, exams: [{exam_name, exam_date, score, source_domain}]}], totals?: [{total_type, exams: [...]}]}`
  homeroom 可见全科 + 总分；teaching 仅当前任教学科；跨域只经确认关联读取允许学科；越界 `resource_out_of_scope` 404。

### 1.4 成绩查询

- `GET /api/v1/scores?mode=&academic_year_id=&exam_name=`
  `{metadata, rows: [{person_id, name, subject, score, total_type, source_domain, shared_conflict?}]}`
  - homeroom 模式：本班全科+总分（source_domain:"homeroom"）；active link 下关联交集学生的任教学科事实按 §1.4.1 规则投影（source_domain:"teaching"）；与 teaching 域值冲突的 H 行附 `shared_conflict: {teaching_score}`。
  - teaching 模式：仅任教学科；不含 total_type 行、不含其他学科。
  - 无关教学班（如 T8）的行在任何 homeroom 响应中不可见。

#### 1.4.1 跨域成绩投影规则（v2 明确——R2/R3/R5，所有读接口统一执行）

设 homeroom 响应要投影 teaching 域的任教学科事实（或 teaching 响应要投影 homeroom 域同学科事实），
必须先经**统一投影入口**逐条（逐 fact）满足全部条件，缺一不可：

1. link `status='active'`，且查询时点 as_of 在 [valid_from, valid_to] 内；
2. `current_subject_score ∈ share_categories`；
3. 该 fact 的 `exam_date` 非空，且 `exam_date >= (share_history_from ?? valid_from)`（显式历史授权可早于关联生效日；未授权时默认不开放生效前历史）；
4. **双方成员交集**（考试时点）：LinkedStudent 的 homeroom 侧人在该行政班有 status='active' 且有效期覆盖 `exam_date` 的 enrollment；teaching 侧人在该教学班有有效期覆盖 `exam_date` 的 membership。只验证查询时点、不验证考试时点，视为不满足。
5. 事实落在对方班级范围（class_ref_id 匹配对方班级）；无关教学班（如 T8）的行永不可见。

冲突规则（R5，P1 读接口行为；"确认规范值"的写入机制属 P3 导入）：
- 同一 (person, link.subject, exam_name) 两域均有事实时：
  - 值不同（含一方 NULL）：**各自保留本域值，互不投影、互不删除**；homeroom 侧行附提示字段
    `shared_conflict: {teaching_score}`（teaching 侧不投影 H 值、不附字段）。绝不静默采用对方值。
  - 值相同：视为已一致的规范值，各自保留本域行。
- 仅一侧有事实：按上述 5 条 gating 投影到另一侧（source_domain 标注来源域，person_id 仍为**响应所在域**的身份 ID）。
- 替换式投影（删除本域行再取对方值）一律禁止。

### 1.5 导入（P1 骨架，P3 完整实现）

- `POST /api/v1/imports/preview`
  请求：`{mode, files: [{filename, content_digest}]}`
  响应：`{token, expires_at, items: []}`（P1 恒空清单）。
- `POST /api/v1/imports/confirm`
  请求：`{token}`；合法 → `{imported: 0}`；过期/范围变化/成员漂移/已消费 token → `link_version_conflict` 409。
  preview 快照必须保存成员 person_id 集合，confirm 逐项比对（与 §1.2 confirm 同一规则）；P1 零业务写入（仅 import_batch 台账状态）。

## 2. 数据模型契约（实现模块：app/db/workspace_models.py）

表清单（详细列定义见模型实现，以下为契约要点）：
`academic_year, term, cohort, student_identity(data_domain ∈ homeroom|teaching), student_alias(域+学年有效范围), administrative_class, enrollment(成员有效期+状态), teaching_class, teaching_class_member(有效期+来源), homeroom_teaching_link(行政班+教学班+学年+学科+有效期+share_categories+share_history_from+status+version), linked_student(link_id+双域 identity+confirm_basis), source_map(来源指纹→目标 唯一有效映射), migration_run, score_fact(域+考试+人+学科/总分类型 唯一，score 可 NULL), import_batch(token+范围+状态)`

### 2.1 作业批次契约（v2 新增——R10；完整录入/预警逻辑属 P5）

`homework_assignment`（布置批次）：
- `id, data_domain('homeroom'|'teaching'), class_ref_id`（多态班级引用，同 score_fact 语义）
- `academic_year_id, subject`（学科）, `homework_type`（作业种类；与 subject 分列，绝不混用）
- `assigned_date`（布置日期）, `due_date?`
- `batch_token`（**UNIQUE**：幂等键，重试同 token 不新增批次；一天同科同种类多份作业各自持不同 token）
- `expected_members_json`（应交成员快照：person_id 列表 + 快照生成时点；分母以此为准）
- `revision`（默认 1；编辑递增，乐观锁）, `status('active'|'revoked')`, `created_at, updated_at`

`homework_submission`（逐人状态）：
- `id, assignment_id`（FK homework_assignment）
- `person_id`（FK ws_student_identity）
- `submission_status ∈ ('submitted','missing','excused','unknown')`——无记录不推断为已交；评价另列 `evaluation?`，不得从评价缺失推断缺交
- `submitted_at?, revision, created_at, updated_at`
- **UNIQUE (assignment_id, person_id)**：同人同批次唯一
- 数据库外键真实开启（应用连接生命周期内 PRAGMA foreign_keys=ON），孤儿 assignment_id/person_id 写入被拒绝

运行时：`app/core/context.py::WorkspaceContext`（teacher_id, mode, data_domain, link_id, link_version, academic_year_id, term_id, grade, class_ids, subject, member_person_ids, as_of）与 `resolve_workspace_context(db, teacher_id, mode, params)`。
错误类型：`app/core/errors.py`（DomainError 基类；WorkspaceNotConfigured/InvalidScopeParam/ResourceOutOfScope/LinkVersionConflict，含 to_http()）。

## 3. 前端契约（P2 使用）

- `frontend/src/lib/api-v1.ts`（FE-CORE 提供并冻结导出）：
  - `class ApiV1Error extends Error {status; code}`；`fetchSharedConfig(): Promise<SharedConfig>`
  - `fetchClasses(academicYearId): Promise<ClassesCatalog>`（§1.1 新端点的封装，类型 `{academic_year_id, homeroom: {class_id, grade, class_num, label} | null, teaching: [{class_id, label, subject}]}`）
  - `fetchScope(q: ScopeQuery): Promise<ScopeState>`
  - `listLinks(academicYearId): Promise<{links: LinkSummary[]}>`
  - `previewLink(req): Promise<LinkPreview>`；`confirmLink(token): Promise<LinkConfirm>`；`cancelLink(id): Promise<{success; status}>`
  - （v2）`listLinkStudents(linkId)`、`createLinkStudents(linkId, pairs, confirmBasis?)`、`deleteLinkStudent(linkId, linkedId)`、`updateLinkShareScope(linkId, {share_categories?, share_history_from?})`
  - `fetchStudents(mode, q)`、`fetchStudent(mode, personId)`、`fetchScores(q)`、`importsPreview/Confirm`
  - 类型与 §1 一致。
- `frontend/src/lib/workspace.tsx`（FE-CORE 提供）：
  - `WorkspaceProvider`：`mode: 'homeroom'|'teaching'`、`setMode`（写 URL 路径 `/homeroom|/teaching`，不重载）、两模式各自的筛选记忆（localStorage 键 `workspace-scope:<mode>`）、`scope: ScopeState|null`、`refreshScope()`、请求世代号 `generation`（切换即 +1，迟到响应按代丢弃）、`switching: boolean`。
- 路由：`/homeroom`（班主任总览）、`/teaching`（教学总览）为两工作台根；切换器在顶栏，支持键盘操作。
- **v2.1（F04）公共页工作台事实源**：不属于 `/homeroom|/teaching` 前缀的公共页（如 `/upload`）必须以 URL
  携带工作台：`?ws=homeroom|teaching`。WorkspaceProvider 的事实源顺序 = 路径前缀 → `?ws` 参数；
  切换器在公共页点击时更新 `?ws` 并保持/导航；侧栏与页内入口链接生成时携带当前 ws；上传等写入请求的
  mode 取自 Provider（即 URL）。刷新、复制 URL 到新标签页、前进后退均不得漂移导入域。
- 样式沿用教学版 token（globals.css / tailwind 菱形卡片、浅网格背景），不新增状态库。

## 4. 合成样本约定（测试与 OpenAPI 样例共用）

学年 `2025-2026`；行政班 H6=高二(grade=2)6班（教师绑定）；教学班 T6=物理「高二6班(教)」（与 H6 关联 active）、T8=物理「高二8班(教)」（无关联）。
学生：甲、乙（H6∩T6，已确认关联）；丙（仅 H6）；丁（仅 T6）；戊（仅 T8，与甲同名同裸号）；己（仅 T8）。
考试 `2025期中`：homeroom 域存甲乙丙的语数英物+主三门总分；teaching 域存甲乙丁（T6 物理）、戊己（T8 物理），其一 score=NULL（缺考）。
