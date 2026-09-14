# P4 接口契约：学生管理 / 换届 / 教学成员 / 档案（v1，2026-09-11）

依 `docs/planning/03-architecture.md`、`05-acceptance.md`（I01/I02/N01/S08）与
`docs/baseline/api-diff.md`（C6/C7/D1 节）起草；实现不得自行偏离。基础约定（错误码/metadata/空态）
沿用 p1-api.md §0。旧 `/api/manage/*`、`/api/rollover/*`、`/api/teaching/*`、`/api/notes` 保留不动，
P7 前不删；合并版行为一律落 /api/v1。

## 1. 学年 / 学期管理（P1 遗留项补齐）

- `GET /api/v1/shared/academic-years` → `{years: [{id, name, start_date, end_date}]}`（start_date 降序）
- `POST /api/v1/shared/academic-years` `{name, start_date, end_date}`：重名 422；建年后**不自动**建行政班/教学班。
- `PATCH /api/v1/shared/academic-years/{id}` `{name?/start_date?/end_date?}`：已有业务数据引用时改日期 422（防期间错乱）。
- `GET /api/v1/shared/terms?academic_year_id=` → `{terms: [{id, academic_year_id, name, start_date, end_date}]}`
- `POST /api/v1/shared/terms` `{academic_year_id, name, start_date, end_date}`：同学年重名 422。

## 2. 班主任学生管理（ws 域，作用域 = 绑定行政班 + 学年）

### 2.1 名册 CRUD

- `GET /api/v1/homeroom/students`（P1 已有）。
- `POST /api/v1/homeroom/students` `{name, alias?, seat_no?}`：新建 WsStudentIdentity(homeroom) +
  WsStudentAlias(本学年) + Enrollment(active)。alias 同学年同域已属他人 → 422（列出冲突人）；不自动改名合并。
- `PATCH /api/v1/homeroom/students/{person_id}` `{name?, seat_no?}`：仅改本班当期在班成员；越界 404。
- `POST /api/v1/homeroom/students/{person_id}/archive` `{status: transferred|graduated|active, valid_to}`：
  离班写 Enrollment.valid_to + status（绝物理删除）；active 恢复需 valid_to 置空。影响共享时即时生效。
- `POST /api/v1/homeroom/students/{person_id}/alias` `{alias, valid_from}`：**追加新学号**（换号接续，S08）——
  旧 alias 保留（valid_to=前一日），identity 不变；同号新号已属他人 → 422 列冲突。
- `GET /api/v1/homeroom/students/{person_id}/aliases` → `{aliases: [{id, alias_value, valid_from, valid_to}]}`。
- 删除/合并：**P4 不提供**（H 版 merge/delete 语义重、依赖大量跨表事务；按 02-feature-decisions 延后到 P7
  迁移核对时按需裁决）。档案留存优先于删除。

### 2.2 换届（rollover，I01）

- `GET /api/v1/homeroom/rollover/preview?from_academic_year_id=`：绑定班旧学年名册 → 新学年（年级+1）映射
  预览：`{from_year, to_year, students: [{person_id, name, current_alias, next_alias?, note}]}`。
  next_alias 建议 = 去掉学年段前缀重编（仅建议，确认时逐人可改）；无新学年 → 409 workspace_not_configured
  （提示先建学年）。
- `POST /api/v1/homeroom/rollover` `{token, aliases: {person_id: alias}}`：token 来自 preview（import_batch
  台账，R4 同语义：pending/未过期/成员无漂移）。单事务：新学年 AdministrativeClass（绑定年级+1 的班号沿用
  绑定）+ 每生 Enrollment + 新学段 alias（旧 alias 收尾 valid_to=新学年 start_date 前一日）。新学年已存在
  同班 → 422。**重复确认同 token 409；再走需新 preview。**
- `POST /api/v1/homeroom/rollover/{token}/undo`：撤销本次换届（快照回滚：删新建 Enrollment/alias/班级行，
  恢复旧 alias valid_to）；**换届后已有新写入**（新 alias 上出现成绩/档案/作业行）的学生逐人列为
  `conflicted`（跳过撤销、保留现状），不静默覆盖后续合法数据。
  **v2（G04）**：撤销前必须对 confirm 创建/收尾的每一行做**快照一致性核验**——新建 Enrollment 的
  座号/状态/有效期、新建/收尾 alias 行、以及 LinkedStudent 等关联依赖，与 confirm 快照逐项比对；
  任一字段被后续操作改动（如改座号、改号、离班又恢复）→ 该生列入 conflicted 保留现状，
  绝不删除已被后续编辑过的行。仅凭 created_at 检测新写入不足以证明全部后续编辑安全。
- 换届不改变 LinkedStudent（按人而非按班号；新学年共享需新 link 或延长有效期——架构 §7）。

### 2.3 打印

- `GET /api/v1/homeroom/students/{person_id}/report` → `{person, roster, subjects, totals, notes_summary}`：
  画像（含跨学年 trends 同构数据）+ 档案摘要（见 §4 可见性）。前端打印页复用 @media print。

## 3. 教学班成员管理（T 语义，作用域 = 任教学科教学班）

- `GET /api/v1/teaching/classes/{teaching_class_id}/members` → `{members: [{person_id, name, alias,
  valid_from, valid_to, source}]}`（当期+历史分列：`active`/`left` 数组或 valid_to 判空）。
- `POST /api/v1/teaching/classes/{teaching_class_id}/members` `{name, alias?}`：建 teaching 域 identity +
  member(source='manual')；**关联班（active link 覆盖该班）不在此建 identity**——H 名册为权威来源，
  应答 409 + 提示到班主任侧添加或先建配对（架构 §7 名册行）。
- `DELETE /api/v1/teaching/classes/{teaching_class_id}/members/{person_id}`：写 valid_to（不物理删）。
- `POST /api/v1/teaching/classes/{teaching_class_id}/members/import` `{text}`：文本批量（每行"学号 姓名"或
  "姓名"），预览/确认两段（import_batch token，R4 语义）；关联班同样拒绝直导（409 引导 H 侧或配对）。
- `POST /api/v1/teaching/classes/{teaching_class_id}/sync-from-homeroom`：仅 active link 存在时可用——
  按 LinkedStudent 交集把 H 名册成员同步进教学班（source='link_projection'）；差异预览先行
  （preview 参数 confirm）。非关联班 409。

## 4. 档案（notes，N01 隔离红线）

新表（迁移 0005）：`ws_student_note`：
`id, data_domain('homeroom'|'teaching'), person_id(FK ws_student_identity), date, category
(谈话/观察/家访/家长沟通/奖惩/其他), content, follow_up?, follow_up_done(0/1), source?, created_at`。
**默认按域隔离**：homeroom 域档案（含家访/谈话私密内容）绝不进入 teaching 工作台；反向亦然。
关联班**不共享档案**（ADR-005 白名单无 notes；未来如需共享须显式决策追加，本契约不做）。

- `GET /api/v1/{mode}/students/{person_id}/notes` → `{notes: [...]}`（person 须在该 mode 作用域，越界 404；
  按 person 聚合其全部 alias 的档案——ws 域 person 即身份，无学号聚合问题）。
- `POST /api/v1/{mode}/students/{person_id}/notes` `{date, category, content, follow_up?}`。
- `PATCH /api/v1/{mode}/notes/{note_id}` / `DELETE ...`：note 必须属当前 mode 域且 person 在作用域。

## 5. 验收映射

I01（名册创建/替换学号/撤销导入一致性；后续编辑冲突不被回滚覆盖）→ §2.1/§2.2 测试；
I02（历史班成员/当前成员/无成绩成员）→ 名册与分析/作业读取的 membership_basis 一致性测试；
N01（H 家访谈话 + T 教学观察默认各域独立）→ §4 跨域读取 404/空测试；
S08（关联学生改号/同名身份确认，历史接续）→ §2.1 alias 追加 + 画像跨学年接续测试。

## 6. 前端（P4-FE 波次）

- `/homeroom/students`：名册管理页（表格 + 新建/编辑/离班/追加学号 + 别名历史展开 + 打印入口）。
- `/homeroom/rollover`：换届向导（preview 表格逐人改号 → 确认 → 撤销入口 + 冲突清单展示）。
- `/teaching/classes`（或 settings/classes 并轨）：教学班成员管理（当期/历史分列、文本导入、
  关联班引导提示、sync-from-homeroom）。
- `/homeroom/students/{id}/report`：打印视图。
- 旧 /student、/settings/classes 页迁移替换；沿用教学风格与 P2/P3 模式。
