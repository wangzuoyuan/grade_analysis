# API 兼容性差异清单：教学版前端（T）↔ 班主任版后端（H）

- 生成日期：2026-09-11（P0 基线，纯静态阅读，未运行任何服务）
- 对照对象：
  - H = 班主任版后端 `.sources/homeroom/backend/app`（FastAPI `version="2.2.4"`，`main.py:32`）
  - T = 教学版后端 `.sources/teaching/backend/app`（FastAPI `version="2.0.15"`，`main.py:33`）
  - F = 教学版前端 `.sources/teaching/frontend/src`
- 路径缩写：下文 `H:`、`T:`、`F:` 均指上述三个根目录内的相对 `file:line`。
- 判定口径（针对 F 的每个「方法 + 路径」调用；R3 修订后区分两层）：
  - ✅ 字段与两版行为均一致、可直接直连（均为无学生范围语义的全局操作）；
  - ❌ H 完全没有该路径（主要是 `/api/teaching/*` 与三个教学版聚合端点）；
  - ⚠️ 路径存在但存在任一差异：参数/响应结构不同，**或作用域/业务行为契约分叉**（H 行政班语义 vs T 教学范围语义、前置校验不同、聚合口径不同），或两版一致地缺少合并版必需的范围校验（附 file:line 证据）。
  - 判定只描述「T 前端直连 H 后端」的兼容性，不代表满足合并版隔离设计；✅/⚠️ 项的范围语义逐项见 B9。
- 修订记录：2026-09-11 R3（按 审核意见）：#32/#33/#34/#40/#41/#44–#47 由 ✅ 改判 ⚠️，计数 19/17/18 → **10/17/27**；新增 B9 节记录原 ✅ 项的作用域复核。

---

## A) 摘要

| 计数项 | 数值 |
|---|---|
| F 发起的唯一「方法 + 路径」API 调用 | **54** |
| ✅ 一致（R3 修订后） | **10** |
| ❌ H 不存在 | **17** |
| ⚠️ 路径存在但契约差异（含作用域分叉，R3 修订后） | **27** |
| H 后端 HTTP 端点总数（不含 MCP 挂载） | **87** |
| 其中被 F 调用 | 37 |
| 其中 F 不调用（见 C 节） | 50 |
| T 后端 HTTP 端点总数（不含 MCP 挂载） | **82** |
| 其中 T 独有（H 无，见 D 节） | 21 |

结构性结论：

1. ❌ 的 17 个全部集中在 `/api/teaching/*`（14 个）加 `GET /api/dashboard/overview`、`GET /api/homework/dashboard`、`POST /api/homework/smart-input`。H 没有任何教学班概念。
2. ⚠️ 的 18 个几乎全部源于同一根因：**作用域体系不同**——H 用「年级 grade + 行政班 class_num（教师绑定）」，T 用「任教学科 subject + 教学班 teaching_class_id（后端解析）」，并因此连带响应结构（全科/总分 vs 单科）全面分叉。
3. ✅ 的 10 个（R3 修订后）仅剩鉴权（status/login）、教师改名、学期列表三件套（semesters 列表/新增/设当前）与备份四件套——全部是无学生范围语义的全局操作。原判 ✅ 的 notes CRUD、学期读写（semester）、作业删除与 toggle-excluded 经作用域复核改判 ⚠️（见 B9）：字段同形不等于范围/业务行为兼容。
4. H、T 各自条件挂载只读 MCP 服务于 `/mcp`（`H: mcp_server.py:48`、`T: mcp_server.py:40`，需 `MCP_ENABLED` 环境变量，默认关闭）；F 不调用 MCP。

---

## B) T 前端 ↔ H 后端对照总表（主交付物）

### B1. 布局与鉴权

| # | F 调用（方法 路径） | F 位置 | H 端点位置 | 判定 | 说明 |
|---|---|---|---|---|---|
| 1 | GET `/api/auth/status` | `F: components/AuthGate.tsx:24` | `H: auth_router.py:33` | ✅ | 两文件 diff 完全一致 |
| 2 | POST `/api/login` | `F: components/AuthGate.tsx:41` | `H: auth_router.py:46` | ✅ | 同上；cookie 会话机制一致（`H: main.py:57-68` vs `T: main.py:67-78` 逐行同构） |
| 3 | GET `/api/teacher` | `F: components/layout/Shell.tsx:16`、`F: app/settings/classes/page.tsx:1016` | `H: main.py:82` | ⚠️ | H 返回 `{id, name, target_class_high1/2/3, has_pending_rollover, active_grade}`（`H: main.py:157-165`）；T 返回 `{id, name, subject, subject_configured, current_teaching_class_id, class_count, target_class_high1/2/3}`（`T: teaching/subject.py:60-76`）。F 在 `settings/classes/page.tsx:1020` 读 `data?.subject` 配置任教学科，直连 H 拿到 `undefined`，学科配置流程失效 |
| 4 | PATCH `/api/teacher` | `F: components/layout/Sidebar.tsx:102`（body 仅 `{name}`） | `H: main.py:167` | ✅ | H 只接受 `name`（`H: main.py:175-184`）；F 恰好只发 name。T 版还接受 `subject`（`T: main.py:104-138`），F 未用 |

### B2. 教学班与范围（lib/class-scope + 设置页）——整组 ❌

| # | F 调用 | F 位置 | H | 判定 | 说明 |
|---|---|---|---|---|---|
| 5 | GET `/api/teaching/classes` | `F: lib/class-scope.tsx:44` | — | ❌ | H 无 `/api/teaching` 模块 |
| 6 | GET `/api/teaching/current` | `F: lib/class-scope.tsx:56` | — | ❌ | |
| 7 | PATCH `/api/teaching/current` | `F: lib/class-scope.tsx:73`、`F: app/settings/classes/page.tsx:125` | — | ❌ | |
| 8 | POST `/api/teaching/classes` | `F: app/settings/classes/page.tsx:1046`（body 含 `subject`，见 1049-1054） | — | ❌ | |
| 9 | PUT `/api/teaching/classes/{tc_id}` | `F: app/settings/classes/page.tsx:162,167,1198` | — | ❌ | |
| 10 | DELETE `/api/teaching/classes/{tc_id}` | `F: app/settings/classes/page.tsx:143` | — | ❌ | |
| 11 | GET `/api/teaching/classes/{tc_id}/members` | `F: app/settings/classes/page.tsx:103` | — | ❌ | |
| 12 | POST `/api/teaching/classes/{tc_id}/members` | `F: app/settings/classes/page.tsx:636,761,965` | — | ❌ | |
| 13 | PATCH `/api/teaching/classes/{tc_id}/members/{student_id}` | `F: app/settings/classes/page.tsx:1303-1306` | — | ❌ | |
| 14 | DELETE `/api/teaching/classes/{tc_id}/members/{student_id}` | `F: app/settings/classes/page.tsx:204,1303` | — | ❌ | |
| 15 | POST `/api/teaching/classes/{tc_id}/members/import` | `F: app/settings/classes/page.tsx:823` | — | ❌ | |
| 16 | POST `/api/teaching/classes/{tc_id}/sync-by-class-num` | `F: app/settings/classes/page.tsx:185` | — | ❌ | |
| 17 | GET `/api/teaching/candidate-classes` | `F: app/settings/classes/page.tsx:1006` | — | ❌ | |
| 18 | GET `/api/teaching/name-candidates` | `F: app/settings/classes/page.tsx:721` | — | ❌ | |

后果：`ClassScopeProvider` 初始化即失败（`class-scope.tsx:44-58` 拿到 `{classes: []}`），全站默认回落 `all` 范围。

### B3. 仪表盘（app/page.tsx）

| # | F 调用 | F 位置 | H 端点位置 | 判定 | 说明 |
|---|---|---|---|---|---|
| 19 | GET `/api/exams?teaching_class_id=` | `F: app/page.tsx:195`、`F: app/compare/page.tsx:105`、`F: app/exam/page.tsx:135` | `H: analysis/router.py:193` | ⚠️ | H 只接受 `grade` 参数（`H: analysis/router.py:194`），`teaching_class_id` 被忽略；H 返回库内全部考试（不做学科/成员过滤），响应无 `subject` 键（`H: analysis/router.py:203-212`）。T 接受 `teaching_class_id` 并只返回当前学科有真实分数的考试，响应含 `subject`（`T: analysis/router.py:572-638`）。F 在 `exam/page.tsx:148` 读 `examsRes.subject` |
| 20 | GET `/api/exams/{exam_id}?teaching_class_id=` | `F: app/page.tsx:214,269`、`F: app/exam/page.tsx:153`、`F: app/exam/[id]/page.tsx:192`、`F: components/layout/Topbar.tsx:78` | `H: analysis/router.py:260` | ⚠️ | 参数被 H 忽略；响应结构全面分叉：H 的 `stats` 为总分口径 `{avg_main_total, max_total, min_total, by_total_type, main_total}`（`H: analysis/router.py:497-521`，stats 块 513-521），`students[]` 为全科行（`subject_scores`/`subject_grade_scores`/`total_scores`/`total_score`/`grade_rank`，`H: analysis/router.py:282-299`）；T 的 `stats` 为单科 `{avg, max, min, rank_min, rank_max, score_basis}`（`T: analysis/router.py:849-858`），`students[]` 为单科行（`raw_score`/`grade_score`/`rank`/`class_label`，`T: analysis/router.py:800-827`），响应顶层多 `subject`/`teaching_class_id`（`T: analysis/router.py:945-946`）。F 首页 KPI 直接读 `r?.stats`/`r?.subject`（`F: app/page.tsx:269-272`） |
| 21 | GET `/api/focus-list/{exam_id}?teaching_class_id=` | `F: app/page.tsx:207,247` | `H: analysis/router.py:529` | ⚠️ | H 基于 `TotalScore(主三门)`，行字段 `{student_id, name, class_num, xueji_rank, total_score, issues}`（`H: analysis/router.py:598-605`），且只支持 `class_num` 过滤（`H: analysis/router.py:530`）；T 为单科 `{teaching_subject, focus_list:[{..., class_label, raw_score, grade_score, subject_rank, issues}]}`，`class_num` 传参直接 400（`T: analysis/router.py:968-1041`，400 见 989-990）。F 首页只取 `focus_list.length`（`page.tsx:207`），列表页消费 `subject_rank` 等字段 |
| 22 | GET `/api/dashboard/overview` | `F: app/page.tsx:285` | — | ❌ | H 无此端点（T: `T: analysis/router.py:1901`，单科教学班总览） |

### B4. 学生检索与画像

| # | F 调用 | F 位置 | H 端点位置 | 判定 | 说明 |
|---|---|---|---|---|---|
| 23 | GET `/api/students?q=&teaching_class_id=` | `F: app/student/page.tsx:108-117` | `H: analysis/router.py:988` | ⚠️ | H 参数名为 `search`（`H: analysis/router.py:989`），`q`/`teaching_class_id` 均被忽略（返回全库学生，按班主任绑定班过滤 roster-only）；H 行字段 `{student_id, name, current_grade, class_num, history, latest_exam_name, latest_main_score, latest_main_rank}`（`H: analysis/router.py:990-997`）。T 参数 `q`+`teaching_class_id`，行字段 `{student_id, name, class_label, teaching_class_id, grades, latest_exam, raw_score, grade_score, grade_percentile, scope_rank}` + 顶层 `teaching_subject`（`T: analysis/router.py:1569-1594`）。F 读 `res.teaching_subject`/`res.latest_exam`/`s.scope_rank`（`F: app/student/page.tsx:121-140`） |
| 24 | GET `/api/students/{student_id}?teaching_class_id=` | `F: app/student/[id]/page.tsx:220-223`、`F: app/student/[id]/report/page.tsx:62` | `H: analysis/router.py:613` | ⚠️ | H 为全科画像：多套总分趋势（`main_totals/five_totals/nine_totals/plus3_totals/san3_totals`，`H: analysis/router.py:683-710`）+ 全部单科；T 为单科画像：仅 `teaching_subject` + 单一 `score_trend`，显式删除总分字段（`T: analysis/router.py:1045-1062`），并要求 `teaching_class_id` 范围校验（`T: analysis/router.py:1097-1099`，越界 404）。H 忽略该参数 |

### B5. 作业（homework 各页 + 卡片）

| # | F 调用 | F 位置 | H 端点位置 | 判定 | 说明 |
|---|---|---|---|---|---|
| 25 | GET `/api/homework/dashboard?teaching_class_id=&group_by=` | `F: app/homework/page.tsx:188`、`F: components/HomeworkOverviewCard.tsx:34` | — | ❌ | H 无（H 对应能力拆在 `kpi`+`trend`，`H: homework/router.py:85,98`）。T: `T: homework/router.py:139-149` |
| 26 | POST `/api/homework/smart-input` | `F: app/homework/page.tsx:217,235`（body 含 `teaching_class_id`/`confirm`） | — | ❌ | H 无智能录入。T: `T: homework/router.py:412` |
| 27 | POST `/api/homework/records` | `F: app/homework/page.tsx:235-242`（body 含 `teaching_class_id`） | `H: homework/router.py:230` | ⚠️ | H 的 `RecordsPayload` 无 `teaching_class_id` 字段（`H: homework/router.py:214-217`），多出的字段被 pydantic 忽略；H 按 `_validated_scope` 解析到行政班并在该班 `ClassRoster` 内按姓名找学生（`H: homework/router.py:239,220-227`）。T payload 含 `teaching_class_id` 并按教学班成员解析、支持学号消歧（`T: homework/router.py:243-247,256-263`）。录入会写错范围 |
| 28 | GET `/api/homework/student/{student_id}` | `F: app/homework/page.tsx:268`、`F: app/student/[id]/report/page.tsx:63`、`F: components/HomeworkCard.tsx:36` | `H: homework/router.py:179` | ⚠️ | H 要求该生在行政班 `ClassRoster(grade=active_grade, class_num=绑定班)` 中，否则 404「当前班级中不存在该学生」（`H: homework/router.py:184-193`）；T 校验的是当前学科教学范围 `_ensure_student_in_homework_scope`（`T: homework/router.py:206-212`）。教学班成员不在 H 绑定行政班时全部 404 |
| 29 | GET `/api/homework/warnings?teaching_class_id=` | `F: app/homework/warnings/page.tsx:49-50` | `H: homework/router.py:137` | ⚠️ | H 只接受 `class_num`（`H: homework/router.py:138`），`teaching_class_id` 被忽略 → 恒返回行政班预警；T 接受 `teaching_class_id`（`T: homework/router.py:123-136`） |
| 30 | GET `/api/homework/correlation?teaching_class_id=` | `F: app/homework/correlation/page.tsx:51-52` | `H: homework/router.py:150` | ⚠️ | H 参数 `class_num/exam_id/total_type="主三门"/subject`（`H: homework/router.py:151-152`）；T 用 `teaching_class_id`，且 `class_num` 或 `total_type` 非空直接 400（`T: homework/router.py:152-172`，400 见 169-172）。Y 轴也不同：H 用总分排名，T 用单科 `subject_rank`（`T: homework/router.py:160-167`） |
| 31 | GET `/api/homework/roster?teaching_class_id=` | `F: app/homework/settings/page.tsx:68-69`（query 构造见 64） | `H: homework/router.py:531` | ⚠️ | H 只接受 `class_num`（`H: homework/router.py:532`），返回行政班花名册，行含 `grade`（`H: homework/router.py:546-551`）；T 按 `teaching_class_id` 取教学范围成员并含 `has_student_id`（`T: homework/router.py:780-848`，字段见 839-847）。F 花名册设置页的范围切换失效 |
| 32 | PUT `/api/homework/roster/{student_id}/toggle-excluded` | `F: app/homework/settings/page.tsx:133` | `H: homework/router.py:641` | ⚠️ | **R3 改判**：响应同形 `{success, excluded}`，但作用域契约分叉——H 经 `_scoped_roster_row` 限定班主任绑定年级/行政班名册，否则 404（`H: homework/router.py:48-75,641-650`）；T 经 `_ensure_student_in_homework_scope` 校验当前学科教学范围成员（`T: homework/router.py:55-74,958-966`）。函数级复现：仅在教学班的学生 H 侧 404、T 侧成功，正是合并版必须保留的核心边界 |
| 33 | GET `/api/homework/semester` | `F: app/homework/settings/page.tsx:87` | `H: homework/router.py:661` | ⚠️ | **R3 改判**：H 入口先 `_validated_scope`，教师未绑定行政班即 409「当前年级尚未绑定班级」（`H: homework/router.py:48-59,661-667`）；T 无此前置（`T: homework/router.py:989-996`）。直连 H 时"只配置教学、未绑班"场景学期读取即失败 |
| 34 | PUT `/api/homework/semester` | `F: app/homework/settings/page.tsx:97` | `H: homework/router.py:671` | ⚠️ | **R3 改判**：同 #33 的 409 前置差异（`H: homework/router.py:672-678` vs `T: homework/router.py:999-1005`） |
| 35 | GET `/api/homework/semesters` | `F: app/homework/settings/page.tsx:82` | `H: homework/router.py:691` | ✅ | `T: homework/router.py:1007-1014` |
| 36 | POST `/api/homework/semesters` | `F: app/homework/settings/page.tsx:113` | `H: homework/router.py:700` | ✅ | `T: homework/router.py:1016-1028` |
| 37 | PUT `/api/homework/semesters/{id}/current` | `F: app/homework/settings/page.tsx:125` | `H: homework/router.py:714` | ✅ | `T: homework/router.py:1030-1040` |
| 38 | GET `/api/homework/manage/records?...&teaching_class_id=` | `F: app/homework/manage/page.tsx:72` | `H: homework/router.py:409` | ⚠️ | H 参数 `class_num`（`H: homework/router.py:412`），`teaching_class_id` 被忽略 → 只看行政班记录；响应行 H 只有 `{id, name, date, subject, content, remark, is_special}`（`H: homework/router.py:449-460`），T 行多 `student_id/class_labels/submission_status/evaluation/created_at/updated_at`（`T: homework/router.py:708-726`）。F 管理页按 T 契约渲染状态列 |
| 39 | PUT `/api/homework/manage/records/{record_id}` | `F: app/homework/manage/page.tsx:94-98` | `H: homework/router.py:473` | ⚠️ | H 的 `UpdateRecordPayload` 仅 `subject/content/remark`（`H: homework/router.py:467-470`）；T 多 `submission_status/evaluation`（`T: homework/router.py:733-738`）。F 提交含状态/评价字段时 H 静默丢弃 |
| 40 | DELETE `/api/homework/manage/records/{record_id}` | `F: app/homework/manage/page.tsx:87-88` | `H: homework/router.py:501` | ⚠️ | **R3 改判**：均返回 `{success:true}`，但作用域契约分叉——H join ClassRoster 限定记录属班主任绑定行政班（`H: homework/router.py:501-524`）；T 取记录后 `_ensure_student_in_homework_scope` 校验教学范围（`T: homework/router.py:761-779`） |
| 41 | DELETE `/api/homework/special-records/{record_id}` | `F: app/homework/manage/page.tsx:86-88`（特殊记录走此路径） | `H: homework/router.py:383` | ⚠️ | **R3 改判**：同 #40 的作用域分叉（H join 绑定行政班 `H: homework/router.py:383-403` vs T 教学范围校验 `T: homework/router.py:647-663`） |

### B6. 班级对比与周关注

| # | F 调用 | F 位置 | H 端点位置 | 判定 | 说明 |
|---|---|---|---|---|---|
| 42 | GET `/api/class/compare`（可带 `?exam_id=`） | `F: app/compare/page.tsx:123-125` | `H: analysis/router.py:1250` | ⚠️ | H 的 `classes[]` 行是 `{class_num, class_type, main_total_avg, five_total_avg, nine_total_avg, plus3_avg, total_avg}`（总分均分，`H: analysis/router.py:1270-1293`）；T 行是 `{teaching_class_id, class_label, member_count, subject_avg, score_basis, source}`（单科，`T: analysis/router.py:1417-1424`），顶层多 `teaching_subject`（`T: analysis/router.py:1368`）。F 对比页按单科契约渲染（`F: app/compare/page.tsx:119` 注释「当前学科契约」） |
| 43 | GET `/api/weekly-focus?teaching_class_id=` | `F: components/WeeklyFocusCard.tsx:36-38` | `H: homework/router.py:201` | ⚠️ | H 只接受 `class_num`（`H: homework/router.py:202`）；T 接受 `teaching_class_id`、`class_num` 直接 400，且响应含 `teaching_subject`/`teaching_class_id`、理由只用单科临界/薄弱（`T: homework/router.py:217-238`） |

### B7. 档案（notes）与备份（backup）

| # | F 调用 | F 位置 | H 端点位置 | 判定 | 说明 |
|---|---|---|---|---|---|
| 44 | GET `/api/notes/{student_id}` | `F: components/StudentNotes.tsx:47`、`F: app/student/[id]/report/page.tsx:64` | `H: notes/router.py:29` | ⚠️ | **R3 改判**：响应同构（数组），但聚合口径不同——H 按身份链 `person_ids` 聚合同人多学号（`H: notes/router.py:29-41`）；T 精确匹配单学号（`T: notes/router.py:29-41`）。且两版均不校验请求学生属于任何合法范围；合并版档案按域隔离（ADR-005/N01）要求显式范围校验，不能按 ✅ 直连 |
| 45 | POST `/api/notes` | `F: components/StudentNotes.tsx:59` | `H: notes/router.py:54` | ⚠️ | **R3 改判**：两版行为一致（直接按 payload.student_id 写入、无范围校验，`H: notes/router.py:54-83` ≈ `T: notes/router.py:52-81`），但"一致地缺少范围校验"不满足合并版档案隔离前提（N01），列入 P1 契约裁决 |
| 46 | PUT `/api/notes/{note_id}` | `F: components/StudentNotes.tsx:82` | `H: notes/router.py:85` | ⚠️ | **R3 改判**：同 #45，无范围校验（`H: notes/router.py:85-107` ≈ `T: notes/router.py:83-106`） |
| 47 | DELETE `/api/notes/{note_id}` | `F: components/StudentNotes.tsx:92` | `H: notes/router.py:109` | ⚠️ | **R3 改判**：同 #45，无范围校验（`H: notes/router.py:109-128` ≈ `T: notes/router.py:107-127`） |
| 48 | GET `/api/backups` | `F: components/BackupCard.tsx:27` | `H: backup/router.py:53` | ✅ | 文件级 diff 仅差数据目录初始化方式 |
| 49 | POST `/api/backup` | `F: components/BackupCard.tsx:39` | `H: backup/router.py:46` | ✅ | |
| 50 | POST `/api/restore` | `F: components/BackupCard.tsx:60` | `H: backup/router.py:83` | ✅ | |
| 51 | GET `/api/backup/{filename}/download` | `F: components/BackupCard.tsx:107`（`<a href>` 直链） | `H: backup/router.py:71` | ✅ | |

### B8. 上传与聊天

| # | F 调用 | F 位置 | H 端点位置 | 判定 | 说明 |
|---|---|---|---|---|---|
| 52 | POST `/api/uploads/preview`（multipart `files`） | `F: app/upload/page.tsx:226` | `H: ingest/router.py:303` | ⚠️ | 请求与响应字段同构（`token/filename/grade/semester/exam_type/year/month/canonical_name/is_xlsx`），但 `token` 语义不同：H 是服务端生成的存储 token（`H: ingest/router.py:312-317`），T 直接用文件名（`T: ingest/router.py:306-311`）。F 只透传 token（`F: app/upload/page.tsx:233,268`），表面兼容 |
| 53 | POST `/api/uploads/commit`（JSON `items[]`） | `F: app/upload/page.tsx:263-275`（item 不含 `filename`） | `H: ingest/router.py:345` | ⚠️ | H 的 `CommitItem` 多一个可选 `filename`（`H: ingest/router.py:331-333` vs `T: ingest/router.py:325-331`），F 不传、无碍；行为差异：H 解析全科成绩并在检测到更高年级时返回 `suggest_rollover`（`H: ingest/router.py:391-404`），T 单科解析并返回 `detected_classes{class_nums,class_labels}`（`T: ingest/router.py:372-383`）。F 读 `results/detected_class/detected_class`（`F: app/upload/page.tsx:280-283`），不读两者差异字段。**推测**：同一 xlsx 在 H 会入库全科+总分，教学读接口将返回无关学科，P3 做关联投影时必须实测 |
| 54 | POST `/api/chat`（SSE） | `F: components/ChatDrawer.tsx:224` | `H: chat/session.py:444` | ⚠️ | 方法/路径/`text/event-stream` 一致（`H: chat/session.py:450-453`、`T: chat/session.py:544-552`），但请求体 `context` 契约不兼容：F 发送 `{page, student_id, exam_id, scope_mode, teaching_class_id}`（`F: components/ChatDrawer.tsx:121-135`）；H 的 `resolve_chat_scope` 强制要求 `grade` + `class_num` 并校验班主任绑定班，缺省直接 409「尚未配置班级作用域，请先绑定班级」（`H: chat/session.py:97-124`）。**直连 H 时聊天功能 100% 失败**。两侧系统提示与工具集也完全不同（`H: chat/session.py:11-40` 班主任全科 vs `T: chat/session.py:11-25` 任课单科） |

---

### B9. 原 ✅ 项的作用域复核（R3 修正，2026-09-11）

审核指出 #32 仅凭「响应同形」误判兼容；据此逐项复核全部原 ✅ 接口，把「字段兼容」与「范围/业务行为兼容」分开：

| # | 端点 | 复核结论 | 证据 |
|---|---|---|---|
| 1,2 | auth status / login | 无学生范围语义，两版文件级一致 → **维持 ✅** | `H: auth_router.py` ≡ `T: auth_router.py` |
| 4 | PATCH /api/teacher | 仅改教师姓名，无学生范围 → **维持 ✅** | `H: main.py:167-184`（只收 name，F 恰好只发 name） |
| 32 | toggle-excluded | **改判 ⚠️**：H `_scoped_roster_row` 限班主任绑定行政班名册；T `_ensure_student_in_homework_scope` 校验教学范围成员。函数级复现：仅在教学班的学生 H 404 / T 成功 | `H: homework/router.py:48-75,641-650` vs `T: homework/router.py:55-74,958-966` |
| 33,34 | GET/PUT semester | **改判 ⚠️**：H 入口先 `_validated_scope`，未绑定行政班即 409；T 无此前置 | `H: homework/router.py:48-59,661-678` vs `T: homework/router.py:989-1005` |
| 35,36,37 | semesters 列表/新增/设当前 | 两版逐行一致、无范围语义 → **维持 ✅** | `H: homework/router.py:691-730` ≡ `T: homework/router.py:1007-1040` |
| 40 | DELETE manage record | **改判 ⚠️**：H join ClassRoster 限绑定行政班；T 校验记录属教学范围 | `H: homework/router.py:501-524` vs `T: homework/router.py:761-779` |
| 41 | DELETE special record | **改判 ⚠️**：同上 | `H: homework/router.py:383-403` vs `T: homework/router.py:647-663` |
| 44 | GET notes | **改判 ⚠️**：H 按身份链 person_ids 聚合同人多学号、T 精确匹配；且两版均不校验学生属于任何范围 | `H: notes/router.py:29-41` vs `T: notes/router.py:29-41` |
| 45,46,47 | POST/PUT/DELETE notes | **改判 ⚠️**：两版行为一致（直接按 student_id/note_id 写删、无范围校验）——「一致地缺范围」不满足合并版档案隔离（ADR-005 / 验收 N01），不能按 ✅ 原样直连，列入 P1 契约裁决 | `H: notes/router.py:54-128` ≈ `T: notes/router.py:52-127` |
| 48–51 | backup 四件套 | 全应用级操作，无学生范围语义 → **维持 ✅**（备份/恢复本就是全应用操作，见 03-architecture §2） | `H: backup/router.py` ≈ `T: backup/router.py`（文件级 diff 一致） |

---

## C) H 后端存在但 T 前端不调用的端点（50 个）

这些是班主任工作台的服务，合并版将在 P2+ 用教学风格界面重建班主任侧时使用；P0 阶段不需要任何改动。

### C1. main（3）

| 方法 路径 | H 位置 | 用途 |
|---|---|---|
| GET `/` | `main.py:78` | 根信息 |
| GET `/api/health` | `main.py:74` | 健康检查（版本号） |
| POST `/api/teacher/bind-class` | `main.py:186` | 绑定行政班（grade 1-3 + class_num）。注意 T 版同名端点已是弃用空操作（`T: main.py:140-144`），合并版需决定语义 |

### C2. auth（1）

| 方法 路径 | H 位置 | 用途 |
|---|---|---|
| POST `/api/logout` | `auth_router.py:66` | 登出（F 未做登出入口） |

### C3. analysis（8）

| 方法 路径 | H 位置 | 用途 |
|---|---|---|
| GET `/api/rank-metrics` | `analysis/router.py:16` | 排名指标趋势 |
| GET `/api/rank-range` | `analysis/router.py:26` | 排名区间筛选名单 |
| GET `/api/rank-frequency` | `analysis/router.py:49` | 排名频次统计 |
| GET `/api/analysis-config` | `analysis/router.py:72` | 段位口径配置 |
| PUT `/api/analysis-config` | `analysis/router.py:79` | 更新段位口径 |
| GET `/api/band-trend` | `analysis/router.py:113` | 段位人数趋势 |
| DELETE `/api/exams/{exam_id}` | `analysis/router.py:214` | 删除考试及关联数据（F 无删除入口） |
| GET `/api/subject-weakness/{exam_id}` | `analysis/router.py:1357` | 单科薄弱名单（全科版） |

### C4. homework（9）

| 方法 路径 | H 位置 | 用途 |
|---|---|---|
| GET `/api/homework/kpi` | `homework/router.py:85` | 作业 KPI（T 前端改用 `/homework/dashboard`） |
| GET `/api/homework/trend` | `homework/router.py:98` | 作业趋势 |
| GET `/api/homework/subjects` | `homework/router.py:111` | 作业科目列表 |
| GET `/api/homework/rankings` | `homework/router.py:124` | 缺交排行 |
| GET `/api/homework/correlation/subjects` | `homework/router.py:166` | 科目相关性排行 |
| POST `/api/homework/special-records` | `homework/router.py:320` | 特殊记录录入 |
| GET `/api/homework/special-records` | `homework/router.py:358` | 当日特殊记录 |
| POST `/api/homework/roster` | `homework/router.py:566` | 花名册加学生（行政班版） |
| DELETE `/api/homework/roster/{student_id}` | `homework/router.py:621` | 花名册删学生 |

### C5. ingest（2）

| 方法 路径 | H 位置 | 用途 |
|---|---|---|
| POST `/api/uploads` | `ingest/router.py:410` | 旧版一步式上传（向后兼容） |
| GET `/api/uploads` | `ingest/router.py:456` | 上传历史列表 |

### C6. rollover 换届/身份接续（12）——P4「H 换届/撤销」直接依赖

| 方法 路径 | H 位置 | 用途 |
|---|---|---|
| GET `/api/rollover/preview` | `rollover/router.py:56` | 换届预览（grade+class_num） |
| POST `/api/rollover/roster` | `rollover/router.py:85` | 写入新学年名册 |
| GET `/api/rollover/roster/last-import` | `rollover/router.py:104` | 最近一次未撤销导入批次 |
| POST `/api/rollover/roster/{batch_id}/undo` | `rollover/router.py:114` | 撤销名册导入（快照回滚） |
| POST `/api/rollover/link` | `rollover/router.py:189` | 单个学号挂链 |
| POST `/api/rollover/link-batch` | `rollover/router.py:202` | 批量挂链 |
| DELETE `/api/rollover/link/{student_id}` | `rollover/router.py:222` | 拆链 |
| POST `/api/rollover/confirm-batch` | `rollover/router.py:251` | 批量确认身份接续 |
| POST `/api/rollover/confirm-batch/{batch_id}/undo` | `rollover/router.py:269` | 撤销确认批次 |
| POST `/api/rollover/crosswalk` | `rollover/router.py:298` | 新旧学号对照导入 |
| POST `/api/rollover/import-history` | `rollover/router.py:349` | 手工历史成绩导入（隔离于排名） |
| PATCH `/api/rollover/active-grade` | `rollover/router.py:373` | 切换当前活动年级 |

### C7. student_management `/api/manage/*`（14）——P4「H 学生管理」直接依赖

| 方法 路径 | H 位置 | 用途 |
|---|---|---|
| GET `/api/manage/students` | `student_management/router.py:41` | 学生管理列表 |
| GET `/api/manage/students/{student_id}` | `student_management/router.py:51` | 学生管理详情（主档+花名册+历史学号） |
| POST `/api/manage/students` | `student_management/router.py:69` | 新建学生（花名册+主档+alias） |
| PUT `/api/manage/students/{student_id}` | `student_management/router.py:92` | 编辑学生 |
| POST `/api/manage/students/{student_id}/correct-sid` | `student_management/router.py:114` | 纠正录错学号（单事务迁移） |
| POST `/api/manage/students/{student_id}/new-year-sid` | `student_management/router.py:127` | 新增学年学号（alias+花名册行） |
| GET `/api/manage/students/{student_id}/delete-preview` | `student_management/router.py:144` | 删除影响预览 |
| DELETE `/api/manage/students/{student_id}` | `student_management/router.py:155` | 删除学生（需 body payload + confirm） |
| POST `/api/manage/students/{student_id}/archive` | `student_management/router.py:169` | 在班状态（transferred/graduated/active） |
| POST `/api/manage/students/merge-preview` | `student_management/router.py:184` | 合并重复学生预览 |
| POST `/api/manage/students/merge` | `student_management/router.py:200` | 事务性合并 |
| GET `/api/manage/backfill-preview` | `student_management/router.py:216` | 未建主档学生预览 |
| POST `/api/manage/backfill-identities` | `student_management/router.py:223` | 批量补建 identity |
| GET `/api/manage/change-log` | `student_management/router.py:233` | 学生变更日志 |

### C8. chat（1）

| 方法 路径 | H 位置 | 用途 |
|---|---|---|
| GET `/api/chat/config` | `chat/session.py:456` | 聊天模型配置探测（F 未调用） |

### C9. MCP（条件挂载）

- `MCP_ENABLED` 开启时挂载只读 MCP（Streamable HTTP）于 `/mcp`（`H: mcp_server.py:48`、`T: mcp_server.py:40`；挂载逻辑 `H: main.py:13-18,34-35`）。F 不调用；P6（AI/MCP 注册与上下文隔离）才会消费。

---

## D) T 后端独有端点（21 个，H 无对应物）

教学工作台 P1/P2 重建范围状态、P3 成绩投影与画像闭环、P5 作业模型的核心依赖。

### D1. teaching 教学班 CRUD 与范围（14 个，F 全部在用）

| 方法 路径 | T 位置 | 用途 | 合并版相关性 |
|---|---|---|---|
| GET `/api/teaching/classes` | `teaching/router.py:119` | 列当前学科教学班 | P1 域隔离/班级关联；P2 范围状态 |
| POST `/api/teaching/classes` | `teaching/router.py:153` | 建教学班（含 subject 首设） | P1 |
| PUT `/api/teaching/classes/{tc_id}` | `teaching/router.py:190` | 编辑/排序 | P1 |
| DELETE `/api/teaching/classes/{tc_id}` | `teaching/router.py:219` | 删教学班 | P1 |
| GET `/api/teaching/classes/{tc_id}/members` | `teaching/router.py:237` | 成员列表 | P4「T 教学成员」 |
| POST `/api/teaching/classes/{tc_id}/members` | `teaching/router.py:250` | 加成员（学号/姓名） | P4 |
| PATCH `/api/teaching/classes/{tc_id}/members/{student_id}` | `teaching/router.py:265` | 改成员 | P4 |
| DELETE `/api/teaching/classes/{tc_id}/members/{student_id}` | `teaching/router.py:286` | 移除成员 | P4 |
| POST `/api/teaching/classes/{tc_id}/members/import` | `teaching/router.py:312` | 文本批量导入成员 | P4 |
| POST `/api/teaching/classes/{tc_id}/sync-by-class-num` | `teaching/router.py:325` | 按行政班号同步成员 | P4（与 HomeroomTeachingLink 共享投影直接相关） |
| GET `/api/teaching/candidate-classes` | `teaching/router.py:343` | 候选行政班（grade） | P2 关联配置预览 |
| GET `/api/teaching/current` | `teaching/router.py:352` | 当前选中教学班 | P2 范围状态（工作台切换） |
| PATCH `/api/teaching/current` | `teaching/router.py:362` | 切换当前教学班 | P2 |
| GET `/api/teaching/name-candidates` | `teaching/router.py:386` | 同名候选（跨学段） | P1 身份；P3 画像 |

### D2. teaching 身份链（4 个，F 当前未调用，属 T 后端 API 面）

| 方法 路径 | T 位置 | 用途 | 合并版相关性 |
|---|---|---|---|
| POST `/api/teaching/link` | `teaching/router.py:398` | 学号挂链建身份 | P1 身份模型；P3 画像闭环 |
| DELETE `/api/teaching/alias/{student_id}` | `teaching/router.py:407` | 拆链 | P1 |
| POST `/api/teaching/import-crosswalk` | `teaching/router.py:417` | 新旧学号对照导入 | P1/P4（与 H `/api/rollover/crosswalk` 功能重叠，合并版需二选一或统一） |
| GET `/api/teaching/identity/{student_id}` | `teaching/router.py:422` | 学号全集（学段履历） | P3 画像展示 |

### D3. 单科聚合端点（3 个）

| 方法 路径 | T 位置 | 用途 | 合并版相关性 |
|---|---|---|---|
| GET `/api/dashboard/overview` | `analysis/router.py:1901` | 教学班总览（单科均分+focus_count） | P3「两种分析」教学侧 |
| GET `/api/homework/dashboard` | `homework/router.py:139` | 作业看板（group_by week/month，含状态机统计） | P5 作业批次模型 |
| POST `/api/homework/smart-input` | `homework/router.py:412` | 智能录入（预览/确认两段式，学号消歧、全交展开） | P5 作业录入 |

另注（非端点差异但同一路径的契约差异）：`PATCH /api/teacher` 在 T 还接受 `subject` 并校验冲突（`T: main.py:104-138`）；`GET /api/teacher` 响应结构差异见 B1#3。

---

## E) 风险与未知（每条附证据）

1. **聊天上下文契约硬冲突（最致命）**：F 的 `buildPageContext` 发送 `scope_mode`/`teaching_class_id`（`F: components/ChatDrawer.tsx:121-135`），H 的 `resolve_chat_scope` 要求 `grade`+`class_num` 且校验教师绑定班，否则 409（`H: chat/session.py:97-124`）。P2/P6 前聊天对 H 完全不可用；且两侧系统提示/工具集语义相反（班主任全科 `H: chat/session.py:11-40` vs 任课单科 `T: chat/session.py:11-25`），不能靠改字段名打通。
2. **作用域参数静默失效（范围泄漏）**：H 端点普遍不认识 `teaching_class_id`（如 `H: analysis/router.py:194`、`H: homework/router.py:138,151-152,202,412`），FastAPI 对未知 query 参数不报错 → F 的教学班过滤被静默丢弃，`/api/exams`、`/api/homework/*` 等会返回行政班/全库数据。这与合并版「教学读接口不得返回无关学科」「空成员范围不得退化」的方向直接冲突（AGENTS.md）。反向亦然：T 端点把 `class_num` 显式 400（`T: analysis/router.py:989-990`、`T: homework/router.py:169-170,232-233`），班主任侧旧参数在 T 语义下会被拒。
3. **成绩响应结构整体分叉**：`GET /api/exams/{id}` 的 `stats`、`students[]`、`rank_bands`、`rank_distribution`、`class_averages` 五块字段两侧几乎无交集（H 总分口径 `H: analysis/router.py:282-299,497-521` vs T 单科口径 `T: analysis/router.py:800-827,849-858,945-963`）；`/api/students`、`/api/students/{id}`、`/api/focus-list`、`/api/class/compare` 同理（B3/B4/B6 各行）。F 页面拿不到字段时多为静默空白而非报错，联调时容易漏判。
4. **上传 token 与入库语义差异**：H `preview` 返回服务端生成 token（`H: ingest/router.py:312-317`），T 返回文件名作 token（`T: ingest/router.py:306-311`）；H `commit` 额外有 `suggest_rollover` 换届联动（`H: ingest/router.py:391-404`），T 返回 `detected_classes`（`T: ingest/router.py:372-383`）。**推测**：H 解析器按全科入库（含 TotalScore），教学读接口会因此返回无关学科；P3 做关联成绩投影前必须实测 Excel 双方解析结果。
5. **作业数据模型缺列**：T 的 `HomeworkRecord` 带 `submission_status`/`evaluation`/`created_at`/`updated_at` 并有配套迁移（`T: db/migrate_homework_dashboard.py:1`（「作业仪表盘结构迁移（幂等、保留旧记录）」）、`T: main.py:160-162`（「旧缺交记录原样保留并补齐状态/时间戳」）、`T: homework/router.py:708-715,733-738`），H 的记录管理响应无这些字段（`H: homework/router.py:449-460,467-470`）。P5 作业批次模型要给 H 库补列，F 管理页的状态/评价列在 H 上会显示为空。
6. **`/api/teacher` 流程断裂**：H 响应无 `subject`/`subject_configured`/`class_count`（`H: main.py:157-165` vs `T: teaching/subject.py:60-76`），F 学科配置流程读 `data?.subject`（`F: app/settings/classes/page.tsx:1020`）。同时 H 的 `bind-class` 是真绑定（`H: main.py:186-224`）、T 是弃用空操作（`T: main.py:140-144`）——合并版要显式定义这一端点的语义。
7. **鉴权/会话本身风险低**：`auth_router.py` 两侧文件级一致（diff 验证），登录中间件逐行同构（`H: main.py:54-68` vs `T: main.py:64-78`），均 cookie 会话 + `PUBLIC_HOST` 内网放行。风险只在部署层：合并后两工作台共用同一会话域，范围解析必须全部后端化（AGENTS.md 原则）。
8. **SSE 流式细节**：两侧 `POST /api/chat` 均为 `StreamingResponse(text/event-stream)`（`H: chat/session.py:450-453`、`T: chat/session.py:547-551`）；F 用 `fetch`+`getReader()` 手工解析 SSE 帧（`F: components/ChatDrawer.tsx:224-251`），生产走同源相对路径、dev 直连 `:8000` 绕过 Next 代理 30s 超时（`F: components/ChatDrawer.tsx:214-222`）。合并版反代（Caddy）需保留不缓冲、无超时配置。
9. **同名端点异义清单**（P1 冻结契约时必须逐个裁决）：`/api/students`（搜索参数 `search` vs `q`）、`/api/focus-list/*`（`class_num` vs `teaching_class_id`）、`/api/class/compare`（行政班总分 vs 教学班单科）、`/api/homework/correlation`（`total_type` 支持vs 400）、`POST /api/teacher/bind-class`（有效 vs 弃用）、`POST /api/homework/records`（行政班解析 vs 教学班解析）。证据见 B 表对应行。
10. **未知项**：T 前端未调用但 T 后端存在的 `/api/homework/kpi|trend|subjects|rankings|correlation/subjects|special-records(POST/GET)` 是否在合并版继续保留，取决于 P5 作业看板是否沿用 `/homework/dashboard` 聚合口径（本清单不做决策）。MCP 工具集差异（`H: chat/tools.py` vs `T: chat/tools.py`）未逐工具比对，属 P6 范围。
11. **同形不同范围（R3 新增）**：`toggle-excluded`（#32）、`DELETE manage/special records`（#40/#41）响应或表面行为同形，但 H 一律以「班主任绑定行政班」为作用域、T 以「当前学科教学范围」为作用域；`GET/PUT semester`（#33/#34）在 H 另有未绑班 409 前置；notes CRUD（#44–#47）两版一致地缺少学生范围校验。合并版必须逐一裁决（P1 契约 + N01 档案隔离），不能因字段兼容直连。证据见 B9。

---

## 附：本清单的证据基线

- 端点枚举方式：`grep -rn -E "@(router|app)\.(get|post|put|delete|patch|websocket)\(|APIRouter\("` 于两侧 `app/` 全目录；router 前缀以 `include_router(..., prefix="/api")`（`H: main.py:255-263`、`T: main.py:189-196`）与各 `APIRouter(prefix=...)`（chat `/chat`、teaching `/teaching`、manage `/manage`）拼出完整路径。
- 前端调用枚举方式：`grep -rn "/api/"` 于 `F/` 全部 `.ts/.tsx`，含模板字符串拼接路径；SSE/流式检查覆盖 `EventSource|XMLHttpRequest|axios|getReader`。
- 未运行任何 npm/pip/服务器；未修改 `.sources/` 下任何文件。
