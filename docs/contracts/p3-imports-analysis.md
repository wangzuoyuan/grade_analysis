# P3 接口契约：分区上传与成绩分析（v1，2026-09-11）

依 `docs/planning/03-architecture.md`、`05-acceptance.md`（E01–E05/S02–S05）与
`docs/baseline/api-diff.md` 起草；实现不得自行偏离。基础约定（错误码/metadata/空态）沿用 p1-api.md §0。

## 1. 导入（替换 P1 骨架语义；旧 /api/uploads 保留不动，P7 前不删）

### 1.1 `POST /api/v1/imports/preview`（multipart/form-data）

- form 字段：`mode`（homeroom|teaching）、`files`（.xlsx 多文件）、`academic_year_id?`、
  `class_id?`（homeroom；缺省教师绑定班，非绑定班 404）、`teaching_class_id?`（teaching；
  缺省该学科唯一教学班，多班必须显式）、`subject?`（teaching 显式学科）、`exam_name?`/`exam_date?`（ISO，覆盖文件名解析）。
- 行为零业务写入（仅 import_batch 台账行）：解析文件（homeroom 复用 H 解析器全科；
  teaching 复用 T 解析器并**只保留任教学科列**——延续教学版"主动过滤其他学科"行为），
  解析结果 JSON + 范围/成员快照存 `import_batch.scope_json`，`content_digest` 存文件哈希串。
- 响应：`{token, expires_at, mode, items: [{filename, kind: student_scores|class_averages|unknown,
  parsed_ok, message?, exam_name, exam_date, subject?, class_label?, row_count,
  known_students, new_students: [{name, alias}], identity_candidates?: [{alias, name, person_id,
  academic_year_id, academic_year_name, basis}], warnings: [str]}]}`
- warnings 至少覆盖：同学号不同姓名（H 撞号防呆语义，确认时整文件拒绝）、同文件重复学号、
  缺考(score=NULL)计数、被过滤/忽略的列。
- **v2.1（考试身份冲突，审核边界裁决）**：同批次多文件之间、或与库内已有事实，
  出现同 `exam_name` 不同 `exam_date` → preview 计入 warnings 且 **confirm 整批 409 拒绝**
  （明确报错，不静默合并、不当作修订）；确需重导同场考试须先修正来源或显式重命名。
- **身份解析（v2.1，F06/F07）**：
  - 本学年 alias 命中 → 直接解析为该 identity（known）。
  - **历史学年 alias 命中 → 仅生成 `identity_candidates` 候选**（含 person_id/学年/依据），
    绝不自动接续——不能区分同名新生/学号回收/跨届重号；confirm 请求须对每个候选做出显式决定：
    `identity_confirmations: {alias_value: person_id}`（接续）或 `identity_new_aliases: [alias_value]`
    （按新学生建档）；存在历史命中但两者皆未覆盖 → **409 + 候选清单，零写入**；确认的 person 须确实
    持有该 alias 的历史登记（伪造/错配 422）；同名不能消除歧义；同号不同名仍按撞号整文件拒绝。
    未标学年（academic_year_id 为 NULL）的 alias 登记**不得视为已证明的本学年身份（v2.2，G06）**：
    默认生成候选；仅当该 alias 行自身有效期（valid_from/valid_to）明确覆盖目标文件日期
    （可证实该号事发时仍在册）方可直接命中为 known。任何路径确认接续后均按 F07 补学籍，
    不存在"只补 alias 不补学籍"的例外。
  - 无匹配 → 新建（new_students）。
  - 成员有效期（F07）：新建成员 `valid_from = max(学年 start_date, 文件日期)`，未知文件日期时
    用学年 start_date——**绝不用"今天"冒充历史**；经确认接续的既有人：补登本学年 alias，且
    homeroom 模式须补建目标行政班 Enrollment（teaching 模式补 TeachingClassMember），
    有效期同上规则——"导入成功却看不到"属缺陷。

### 1.2 `POST /api/v1/imports/confirm`

- 请求：`{token, revise?: bool = false, identity_confirmations?: {alias_value: person_id}}`（v2.1/F06）
- 校验（全部失败 409 link_version_conflict、零写入，batch 保持 pending 可重试）：
  1. token 存在未过期且 `status='pending'`（已消费/复活一律拒绝，R4 语义）；
  2. 范围（班级/学年/学科）与当前库一致；双侧成员集合与快照一致；
  3. **整批健康检查（F05）：任一文件 `parsed_ok=false` 或 kind 未知 → 整批 409 拒绝**
     （明确报错列出失败文件），绝不静默跳过失败文件导入其余；
  4. 考试身份一致性（F-边界1）：同 exam_name 不同 exam_date（批次内或与库内）→ 409；
  5. 身份确认完备性（F06）：存在历史 alias 候选但 `identity_confirmations` 未逐项确认 → 409 附候选清单。
- 写入（**单事务，E05 原子**：任一步失败全量回滚）：
  - 身份解析按 §1.1 v2.1 规则：本学年命中直取；历史命中凭 confirm 的显式确认接续
    （补登本学年 alias + 目标班成员行，F07 有效期规则）；无匹配新建
    `WsStudentIdentity + WsStudentAlias(+本学年) + Enrollment/TeachingClassMember`
    （valid_from=max(学年 start_date, 文件日期)，source='import'）。
    **同学号不同姓名 → 整文件 409 拒绝**（防两人成绩混档），不自动改名合并。
  - homeroom 模式：ScoreFact 写全科 + 总分（total_type 行），`class_ref_id=行政班 id`，`source='import:<filename>'`。
  - teaching 模式：ScoreFact 仅任教学科（其他学科列丢弃并计入 warnings），`class_ref_id=教学班 id`；
    文件中学生补入 TeachingClassMember（成员同步）。
  - 幂等/冲突（按自然键 `(domain, academic_year_id, exam_name, identity, subject_key, total_key)` 与库内比对）：
    同值 → skipped；无键 → 插入；**不同值 → revise=false 时整批 409 + `conflicts`
    列表（person/subject/exam_name/库内值/新值）零写入；revise=true 时覆写并 `data_revision+1`（E05 修订语义）**。
- 响应：`{imported, skipped, revised, exams: [{exam_name, exam_date}], students_created, members_synced}`

### 1.3 ScoreFact 增列（迁移 0004）

- `grade_score Float NULL`：等级分/赋分列（E03；原始分仍在 `score`，缺考两者皆 NULL）。
  teaching 上传含等级分列时一并入库；homeroom 含则同规则。读取响应在 ScoreRow/ProfileExam 增加可选
  `grade_score`。

### 1.4 `GET /api/v1/shared/exams?mode=&academic_year_id=&class_id=|teaching_class_id=`

- 该域该学年、当前班级范围内已导入的考试：`{exams: [{exam_name, exam_date, row_count,
  subjects: [str]}]}`，按考试日期降序。homeroom 的 subjects 为本班出现的学科并集；teaching 恒仅任教学科。
- teaching 缺省 teaching_class_id = 同学年同学科全部教学班**并集**（集成者修订：与 /api/v1/scores
  教学模式"全部所教班"缺省一致；仅 §1.1 导入 preview/confirm 在多教学班时要求显式选择）。
- **v2.1（F09）统一可读事实口径**：`/shared/exams`、`/api/v1/scores`、`{mode}/students` 列表/画像、
  §2 全部分析端点必须基于**同一**可读事实查询（本域事实 + 经 p1-api §1.4.1 五条件门的对侧投影 +
  冲突规则）。考试列表按此口径列考试：H-only 事实经门可读时，teaching 模式必须能列出该考试；
  T-only 事实同理。各端点不得自建第二套投影/过滤逻辑。

### 1.4.1 考试维度成员口径（v2.1，F08；v2.2 修订 G07）

- 考试维度端点（`exams/{exam_name}/stats|students`、`bands`、trends 的各场数据）按**考试发生时名册**
  解析成员：homeroom = Enrollment 有效期覆盖 `exam_date`（**不按当前 status 过滤**——status 描述的是
  当下在班状态，考后 transferred/graduated 不得改写历史考试人群，G07）；teaching =
  TeachingClassMember 有效期覆盖 `exam_date`。考后离班/考后入班不得改写历史考试人群。
- 非考试维度（dashboard/看板/预警/相关性/当前名册/共享配对候选）沿用当前名册（查询时点 + 当期
  status 口径）。
- 上述两类响应 metadata 均注明 `membership_basis: "exam" | "current"`。教师访问历史班仍受
  绑定/任课作用域校验（同既有规则）。

### 1.6 跨域冲突规范值确认（v2.2，Q10——R5 收口）

- `GET /api/v1/shared/links/{link_id}/score-conflicts` → `{conflicts: [{person_id, name,
  subject, exam_name, exam_date, homeroom_score, teaching_score}]}`：当前 link 下按
  §1.4.1 规则仍处冲突状态的事实清单（两域均有值且不同；H 侧视角）。
- `POST /api/v1/shared/links/{link_id}/canonical-scores`
  请求 `{resolutions: [{person_id, subject, exam_name, canonical_side: 'homeroom'|'teaching', basis?}]}`：
  - **v2.3（V04）：按"来源侧"选择**，规范值 = 该侧当前值（**可为 NULL=缺考**——核实后缺考
    是合法规范结果，不得强制改成实分）；canonical_side 必属两域之一；同一冲突一次只选一侧；
    basis 缺省 `canonical_confirm:<日期>`；
  - 校验：link active；每条 resolution 对应当前真实冲突（人/科/考试匹配且两域值不同）；
  - 写入（单事务）：**两域** ScoreFact 的 score 均更新为所选侧现值（含 NULL——两域同置
    缺考，后续读取不计有效分/排名，§2.3 缺考红线不变），`data_revision+1`，source 追加标记
    `canonical:<原来源域>`；两域值相等后 §1.4.1 冲突自然消失（各侧保留本域行，读取一致）；
  - 幂等：已不构成冲突的重复提交 → skipped；审计：SourceMap 之外以
    `migration_run` 形态不适用——写入 audit 记入 assignment 之外由 score_fact 的
    source/data_revision 与响应回执承载。
  - 响应：`{resolved: n, skipped: n, facts: [{person_id, subject, exam_name, score,
    data_revision}]}`（score 可为 null）。
- 前端：homeroom 成绩页冲突格（shared_conflict）提供"确认规范值"入口——弹窗列两域值与
  来源，**任一侧均可选（含缺考侧，标注"确认为缺考"）**；确认后冲突标记消失、两工作台读值一致。

## 2. 分析端点（读 ScoreFact；作用域解析/metadata/空态沿用 §1.3/§1.4 既有规则）

### 2.1 homeroom（/api/v1/homeroom/analysis/*）

- `GET exams/{exam_name}/stats` → `{metadata, subjects: [{subject, avg, max, min, valid_count,
  missing_count, score_basis}], totals: [{total_type, avg, max, min, valid_count}], cohort_size}`
- `GET exams/{exam_name}/students` → `{metadata, students: [{person_id, name, alias, scores:
  {subject: score|null}, totals: {total_type: score|null}, shared_conflicts?}]}`
  （linked 学生的任教学科冲突按 §1.4.1 标注 shared_conflict，供前端展示"待人工核对"）
- `GET trends?person_id=` → `{person_id, name, years: [{academic_year_id, academic_year_name,
  subjects: {subject: [{exam_name, exam_date, score, grade_score?}]}, totals: {...}}]}`
  （E03：跨学年分段展示，不跨年直接比较九科总分；响应按学年分组即结构上防止跨年连算）
- `GET bands?exam_name=&subject=&metric=` → 段位分布 `{bands: [{label, count, students: [person_id]}],
  total_type?}`。**v2.1（F10）**：段位阈值口径为**年级名次**（AnalysisConfig 的 400/500 是名次阈值，
  绝不可以原始分镜像比较）。当前 ws 域不存年级名次事实，因此 bands 一律返回 409
  invalid_scope_param（detail=「段位阈值口径为年级名次，当前范围无名次数据，不可计算」），
  直至引入合法名次来源或显式的分数段配置（独立于名次阈值、按学科/分制定义）。
  前端显示"段位不可计算（无名次口径）"，不得呈现错误分段。

### 2.2 teaching（/api/v1/teaching/analysis/*）

- `GET exams/{exam_name}/stats?teaching_class_id=` → `{metadata, subject, avg, max, min,
  valid_count, missing_count, rank_min, rank_max, score_basis: raw|grade, cohort_size}`
- `GET exams/{exam_name}/students?teaching_class_id=` → `{metadata, students: [{person_id, name,
  class_label, score: raw|null, grade_score?, rank, source_domain}]}`（rank 为**本班内**名次；
  反向投影的 H 域事实参与名次计算并标 source_domain）
- `GET class-compare?exam_name=` → `{classes: [{teaching_class_id, class_label, member_count,
  subject_avg, score_basis, source: estimated}]}`（E04：本班样本均分一律标 `estimated`；
  官方班均表未入库前不提供 `official`，绝不拿班内样本冒充年级/官方口径）

### 2.3 计量规则（E02/E04 红线，两域统一）

- 缺考（score NULL）：不计入均分/名次分母，单独计 `missing_count`；显示"—"，不转 0。
- 同分同名次（min-rank，如 1,2,2,4）；名次只在本班/本教学班成员内计算。
- `valid_count < 5` → 响应附 `small_sample: true`；不输出百分比排位等不稳定指标。
- 未知/不可计算（如无年级数据时的 grade_rank）：字段缺省并在 metadata 注明，不编造。
- 仅百分位无原始分的行（H 旧数据形态）不进入本域分析（P3 域内事实必有原始分或 NULL）。

## 3. 前端页面（P3-FE 波次）

- `/homeroom/scores`：考试选择（/shared/exams）→ stats 卡 + 学生表（全科+总分+冲突标注）+ 段位分布 + 趋势入口。
- `/teaching/scores`：考试选择 → 单科 stats + 学生行（rank/grade_score/source_domain）+ 班级对比卡。
- `/upload`（重构）：mode 感知（当前工作台决定导入域与范围参数），preview 表格展示 items/warnings，
  confirm 含 revise 开关（冲突时提示）；沿用既有拖拽/文件选择 UI 模式。
- 旧 /exam、/student 页暂不迁移（P4 处理），但顶部工作台切换器保持可用。

## 4. 验收映射

E01（同输入 H 全科/T 单科一致且 T 无其他科）、E02（NULL/0 区分、同分同名次）、E05（幂等/冲突/回滚）
→ imports 端点测试；E03（跨学年分段）、E04（estimated 标注/small_sample）→ 分析端点测试；
S02/S03/S05 → 复用既有投影门（实现时不得绕过 _queries.gated_*）。
