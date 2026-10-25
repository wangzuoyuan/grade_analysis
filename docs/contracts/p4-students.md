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
  **v3（2026-10-18 修复 13.4/9）**：名册取样时点 = `min(今天, 源学年结束日)`——源学年 valid_to 已在学年末
  截止的补做换届不再漏人；响应新增 `roster_as_of`/`roster_basis`（取样口径说明）与 `excluded_students`
  （取样时点不在班的成员逐人注明原因：已离班不随届 / 延续期新建学籍由承接迁入）。同时新增
  `carryover`（延续期写入承接清单：`score_facts`/`homework_assignments`/`stray_enrollments` 计数 +
  考试名/学科/学生名清单）——未换届延续期写入的 `academic_year_id=目标学年、class_ref_id=来源班` 的
  成绩/作业批次，以及延续期 create_student 误入来源班的学籍（valid_from 落在目标学年）。教学班换届
  preview（`GET /api/v1/teaching/rollover/preview`）同口径。
- `POST /api/v1/homeroom/rollover` `{token, aliases: {person_id: alias}}`：token 来自 preview（import_batch
  台账，R4 同语义：pending/未过期/成员无漂移，漂移取样时点同 preview）。单事务：新学年 AdministrativeClass（绑定年级+1 的班号沿用
  绑定）+ 每生 Enrollment + 新学段 alias（旧 alias 收尾 valid_to=新学年 start_date 前一日）。新学年已存在
  同班 → 422。**重复确认同 token 409；再走需新 preview。**
  **v3（问题9）**：confirm 在同一事务内把延续期写入迁入新学年对应班（`class_ref_id`/`admin_class_id` 归位，
  `academic_year_id` 不动；来源学年正常数据绝不迁移），逐行记入 undo 快照 `carryover_moves`；响应新增
  `carryover_moved`。教学班换届 confirm 逐班同样承接（来源班 → 同标签新班）。
- `POST /api/v1/homeroom/rollover/{token}/undo`：撤销本次换届（快照回滚：删新建 Enrollment/alias/班级行，
  恢复旧 alias valid_to）；**换届后已有新写入**（新 alias 上出现成绩/档案/作业行）的学生逐人列为
  `conflicted`（跳过撤销、保留现状），不静默覆盖后续合法数据。
  **v2（G04）**：撤销前必须对 confirm 创建/收尾的每一行做**快照一致性核验**——新建 Enrollment 的
  座号/状态/有效期、新建/收尾 alias 行、以及 LinkedStudent 等关联依赖，与 confirm 快照逐项比对；
  任一字段被后续操作改动（如改座号、改号、离班又恢复）→ 该生列入 conflicted 保留现状，
  绝不删除已被编辑过的行。仅凭 created_at 检测新写入不足以证明全部后续编辑安全。
  **v3（2026-10-18 修复 13.1/13.2/9）**：①新写入检测按**域+目标学年+目标班**收窄——旧学年补录
  （成绩/作业/档案日期在旧学年）与另一工作台写入不再误阻撤销；目标班上的真实新写入（成绩/挂在目标班
  批次上的提交/本域日期在目标学年的档案）仍阻断。②教学班撤销只删 `created_class_ids`——confirm
  复用的既有同标签班**绝不删除**（旧契约会把换届前已存在的班物理删除），保留名单在响应
  `kept_reused_class_labels` 如实返回；两域响应新增 `carryover_restored`/`carryover_kept`：延续期迁移行
  仅在对应新班被本次撤销删除时回迁来源班（回到换届前延续态、数据仍可读）；班级保留（复用班/有残留）
  则迁移保留，数据始终挂在存活班级上。
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
- `DELETE /api/v1/teaching/classes/{teaching_class_id}/members/{person_id}`：写 valid_to=生效日前一天
  （2026-10-18 修复：同日入班再移除得到空区间，当天即不在班；更早入班者语义不变）。
- `POST /api/v1/teaching/classes/{teaching_class_id}/members/import` `{text}`：文本批量（每行"学号 姓名"或
  "姓名"），预览/确认两段（import_batch token，R4 语义）；关联班同样拒绝直导（409 引导 H 侧或配对）。
  确认段与单人添加均可带可选 `effective_on`（默认今天；须在学年内，越界 422 附合法范围）；
  已结束的成员行原地重开（响应含 `restored_count`），不新建重复行。
- `POST /api/v1/teaching/classes/{teaching_class_id}/sync-from-homeroom`：仅 active link 存在时可用——
  按 LinkedStudent 交集把 H 名册成员同步进教学班（source='link_projection'）；差异预览先行
  （preview 参数 confirm）。非关联班 409。2026-10-18 起支持可选 `effective_on` 与
  未配对生处理（打破"入班需先配对/配对需先入班"死锁）：预览新增 `unpaired` 清单（H 在册、
  未配对且不在教学班名册的学生，每人附教学域**候选**身份——仅提示，绝不自动认定）；确认段
  `new_students` 逐生携带 `new`（新建教学身份）/`pair`+`teaching_person_id`（显式选定）/缺省跳过，
  服务端先全量校验后单事务写入（成员行 + LinkedStudent 配对同生，失败零残留）。

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

## 3.1 教学班永久删除（2026-10-24）

班级管理在用、停用教学班均提供「删除」。有数据也允许删除：先显示学年、学科、班名与实际影响，再输入完整班名确认；页面不可撤销。停用/恢复沿用保留历史语义。

- `POST /api/v1/teaching/classes/{id}/delete-preview`：返回 `token/expires_at/impact`；30 分钟有效，预览只写操作台账，不改业务数据。
- `POST /api/v1/teaching/classes/{id}/delete-confirm` `{token, confirm_name}`：凭据绑定班 ID、完整名称和全行指纹；获取 SQLite 写锁后重算范围，漂移/过期/消费/错班返回 409，名称不符 422，未知班 404。一次事务完成，异常全量回滚；成功返回 `success/classes/impact`，classes 为该班实际所属学年同学科剩余列表（允许空列表）。
- 删除：目标班全部当前/历史成员关系；仅 `data_domain=teaching` 且 `class_ref_id=目标班ID` 的成绩、作业（包括撤销批次）及逐人记录、作业统计排除；目标班关联及其配对。不从成员身份反推扩大成绩/作业删除范围。
- 保留：学生身份、学号、个人档案，其他班级/学年及班主任域数据，整场考试与官方全年级质量/统计参照。参照 JSON 仅把明确命中本班 `managed_class_id` 改空，不改变统计值或其他班引用。
- 相关未确认操作失效（跨班批次包含目标班时整批失效）；相关已确认教学换届撤销凭据失效，普通已确认导入审计保留；关闭引用目标班的教学聊天和依赖其关联的聊天。来源映射标为已删除/不再有效。删除快照及变更前参照留本机 `ImportBatch`，不返回前端、不入 Git。
- 已删除班 ID 不重用：删除事务保存 `HomeworkSetting.teaching_class_id_high_watermark`，`TeachingClass` ORM 创建统一原子分配后续编号（覆盖手动、导入、换届创建），不需 schema 迁移。删除后前端清理旧班选择、刷新目录/工作台；迟到预览和切范围前的凭据不再显示。
- `impact`：`class_id/label/subject/academic_year_id/academic_year_name`，`member_count/membership_count/score_count/homework_count/submission_count/exclusion_count/link_count/pair_count/reference_count/invalidated_operations/closed_sessions`。成员人数去重，成员关系含历史多行。


## 6.1 历次成绩雷达 + 走势图分口径（2026-10-25）

用户拍板三项口径（AskUserQuestion 逐项确认）：

- **雷达图**（新组件 `StudentScoreRadar`，两处共用：成绩分析页学生明细表行展开区 TrendPanel、
  档案页「历次走势」卡上方独立卡）：每场考试一个半透明多边形重叠，图例按钮点选筛选考试；
  轴值统一换算 0–100「位次分」，**越靠外越好**——年级前% 轴 = 100 − 前%，等级分轴 =
  （等级分 − 40）÷ 30 × 100，悬浮提示显示原值（「前 x%」「等级分 xx」「3+3总分 · 第 N 名」）。
- **总分轴**：位置按总分行的年级百分位换算（trends 端点为此新增 `percentile` 字段，见
  p3 v2.9），悬浮显示校名次。口径优先级 3+3 / 九门 → 主三门 / 五门 → 第一个有名次的；
  +3（等级分之和）无名次恒排除。
- **轴数 > 7 拆两张**（高一九门场景）：主科雷达 = 语数英 + 总分，选科雷达 = 其余学科；
  轴数 ≤ 7（高二/三 6 学科 + 总分）一张。全部数据驱动，不依赖年级配置。

档案页走势图同步换口径（图A/图B 原始分折线退役，原始分仍在明细表）：

- 图A1 各科年级前%走势：语数英/高一全科（任一考试有等级分的学科归 A2，数据驱动分组），
  Y 轴 0–100 反转、刻度「前X%」、悬浮「前 x%（越小越靠前）」。
- 图A2 加三等级分走势：Y 轴 40–70，悬浮「等级分 xx」。两组都非空（高二/三）分图展示，
  仅前%组（高一）单图。
- 图B 总分校名次走势：Y 轴反转、悬浮「第 N 名」，+3 不参与；多总分口径多线 + 图例筛选。
- 缺考/缺指标不画点不连线（connectNulls=false 同口径）；examMatrix cell 补存
  grade_percentile（0–1→0–100 百分数），report 端点无改动。
