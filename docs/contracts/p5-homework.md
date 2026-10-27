# P5 接口契约：作业批次 / 双向共享 / 预警 / 相关性 / 学期（v1.5，2026-10-27 按天映射跨学科扩展；v1.4=2026-10-26 删除改物理删除 + 按天映射；v1.3=2026-10-25 冲突配对收窄到同作业种类）

依 `docs/planning/03-architecture.md` §4、`05-acceptance.md`（H01–H06）、
ADR-008 与 `docs/baseline/api-diff.md`（B5/D3）起草；表结构沿用 p1-api.md v2 §2.1（R10 已落地，
迁移 0003）。基础约定沿用 p1-api.md §0。

## 0. 核心语义（全端点统一执行，违反即返工）

- `subject`（学科）与 `homework_type`（作业种类）分列；同日同科同种类多份作业 = 多个
  assignment（不同 batch_token），绝不合并。
- 应交分母 = assignment.expected_members_json（确认时快照），不是当天班级人数；快照按规则
  扣除 excused 后为有效分母。旧迁移批次按原班级与事件日有效成员恢复名单。
- 状态：submitted/missing/excused。采用例外登记：名单中没有缺交或请假记录的人默认已交；
  旧 unknown 仅作存储兼容，业务读取归一为 submitted，不再向老师展示。反向录入
  （submitted_only/names）在 preview 内把应打名单展开为显式缺交行后覆盖，读取口径不变。
- 连续缺交按收交事件（assignment.assigned_date）排序：submitted 打断连续，excused 跳过。
- 双向共享：assignment 归属一域（data_domain + class_ref_id）；对侧经 **active link + 期满 +
  LinkedStudent 交集 + share_categories 含 current_subject_homework** 才可读/写同一事实（读走
  _queries 同风格的门；写走带 link 校验的端点）。其他教学班/其他学科绝不互见。
- 修订：编辑递增 assignment.revision（乐观锁，旧版改 409）。
- **v1.4（2026-10-26）删除即物理删除**：`DELETE` 直接删除批次与全部逐人行
  （及其衍生的忘带/出勤溯源笔记 `WsStudentNote.source=homework:{id}`），不再写
  `status='revoked'` 撤销记录；删除前有**晚于批次创建的评价/状态编辑 → 409 列
  冲突清单**（防误删护栏与原撤销一致，无强制删除口）。G02 跨域边界不变（整批
  删除仅数据归属域可发起）。列表端点只返回 active 批次——存量 revoked 行保留
  在库但不再下发（不做破坏性迁移）。删除后同日同科同种类可直接重录。
- **v2（G01/G02/G03）跨域共享的事件时点门与写边界**：
  - 读与写统一按 `assignment.assigned_date` 逐批次核验：link active 且 assigned_date 在
    [valid_from, valid_to] 内、assigned_date >= max(valid_from, share_history_from ?? valid_from)、
    双侧成员有效期覆盖 assigned_date（事件时点交集，历史 status 不参与，G07 口径）。
  - 跨域可写成员集 = 上述事件时点授权成员 **∩ 该批次 expected_members_json 快照**；
    不在快照内的人不得经跨域 PATCH 增添记录（快照变更须另行显式修订操作）。
  - 跨域 DELETE 只允许作用于**共享成员的事实**：源域独有成员存在后续编辑冲突时，跨域撤销
    409（错误信息不得反向泄露源域私有学生明细）；整批撤销（status=revoked）仅源域可发起，
    且须对全批次（含非共享成员）做后续依赖检查。
- 全部写路径两段式（preview token → confirm；R4 语义：pending/未过期/成员无漂移/单次消费）；
  同 token 重试不新增数据（batch_token 幂等）。

## 1. 录入（H 版全交台账思想 + T 版智能输入，统一两段式）

### 1.1 `POST /api/v1/homework/preview`（JSON）

- 请求：`{mode, class_id(homeroom)|teaching_class_id(teaching), academic_year_id?, subject, homework_type,
  assigned_date, due_date?, input: {kind: full|names|detailed|submitted_only, names?: [str], rows?: [{name_or_alias,
  status: submitted|missing|excused, evaluation?}], all_submitted?: bool, exceptions?: [{name_or_alias,
  status, evaluation?}]}}`
- 解析（零写入，token 化）：
  - `full`（全交台账）：展开当期应交成员快照为 submitted，再应用 exceptions（明确个人例外）；
    **先解析全批次再应用例外，行顺序不影响结果**（H01）。
  - `names`（交名单）：仅列出的学生为 submitted，**其余应交成员展开为 missing 行**
    （v1.1，2026-10-20：原"其余不写行"在例外登记读取下等于全班已交，与提示文案矛盾，已修正）。
  - `submitted_only`（已交台账，v1.1）：full 的镜像——展开应交成员快照为 missing，再应用
    exceptions 覆盖（裸姓名行 status=submitted；请假等状态行照常）。适用于只记谁交了的场景。
  - `detailed`：逐人行。姓名歧义（同班同名）→ 422 列候选；学号优先消歧。
  - 未列为缺交或请假的应交成员默认已交（full/detailed 语义；submitted_only/names 是
    反向展开，落库后全员有显式行）；不提供 unknown 录入。
  - 同日同科同种类已有批次：preview 返回 `existing_batches: [{assignment_id, batch_token, revision}]`
    提示"编辑既有批次或新建"；**不自动叠加**（H02）。
  - **v1.2（2026-10-24）录入跨侧冲突阻断（单账本）**：`current_subject_homework` 共享开启时，
    对侧关联班（link.subject == 录入学科）**同日同作业种类**已有 active 批次 → preview 直接
    **422** `invalid_scope_param`，details 带 `conflict_batches: [{assignment_id, homework_type,
    assigned_date, side, class_label}]` 与 `conflict_side`；不签发 token，confirm 无从提交。
    confirm 在漂移校验后**复查**同一条件（关掉"preview 后对侧才录"的 TTL 竞态，零写入）。
    同日判定不含 ±1 天；改既有批次（补例外/修订）不受影响；共享关闭时两域互不可见、
    不阻断。
  - **v1.3（2026-10-25）阻断收窄到同种类（用户拍板，同日两份作业的真实案例（匿名））**：同日同学科**不同作业
    种类**（如班主任侧《日常作业》+ 教学侧《周末卷订正》）= 两份不同作业，本来就该各记
    各的，**放行不阻断**（v1.2 会拦下并逼老师「调整收交日期重录」，属误伤）。种类比较取
    strip 后精确相等。文案改为「同种类应为同一份作业，到对侧批次编辑登记；确有两份不同
    作业请用不同作业种类区分」。
- 响应：`{token, expires_at, assignment: {subject, homework_type, assigned_date, expected_members:
  [{person_id, name}], submissions: [{person_id, name, status, evaluation?}], warnings}}`
  （expected_members = 当期有效成员快照；关联班含 LinkedStudent 交集内对侧投影成员——共享批次两侧
  名单一致）。

### 1.2 `POST /api/v1/homework/confirm` `{token}`

- 校验后单事务写入 HomeworkAssignment（batch_token、expected_members_json、status='active'）+
  HomeworkSubmission 逐人行；同 batch_token 重试 → 幂等返回既有结果（不新增）。
- 响应：`{assignment_id, revision, submitted, missing, excused}`。

### 1.3 编辑 / 撤销

- `PATCH /api/v1/homework/assignments/{id}` `{revision, rows?: [...], due_date?}`：revision 不符 409；
  逐行 upsert submission（唯一键 (assignment_id, person_id)）；行内矛盾（同人同批两种状态）422。
- `DELETE /api/v1/homework/assignments/{id}`：**物理删除**（2026-10-26）：逐人行、
  批次与 `homework:{id}` 溯源笔记一并删除；其上有**晚于创建的**评价编辑 → 409 列冲突清单；
  否则成功，响应 `status="deleted"`。指标即时重算；对侧详情随之 404。
- `GET /api/v1/homework/assignments/{id}` → 详情（含 submissions 与 expected_members）。
  教学工作台展开批次的逐人明细在前端按姓名拼音升序（同姓再按名字，同名按 person_id），
  全部/缺交/已交等筛选及行编辑沿用同一排序；不改 API 顺序或班主任作业名单的展示顺序。

## 1.4 请假/出勤异常按天映射（2026-10-26）

当天任何**可见批次**（本域班 + 经作业共享门的关联班批次、仅事件时点授权成员）
上登记的请假（`excused`）与出勤异常（评价含 迟到/没来/早退/旷课/缺课/缺席，
含考勤批次缺勤行）是该生当天的出勤事实，读取时自动映射到当天其他可见批次：

- **请假**：该生当天非考勤批次一律按 `excused`（无条件覆盖，full 台账展开的
  已交行同样翻转——请假了就不可能有已交；认为确已交付应修正请假记录本身）。
  分母扣除、缺交计数剔除、连缺跳过。
- **出勤异常**：该生缺交行补注出勤文本（考勤缺勤行无文本时回退「缺席」）；
  缺交计数保留、纯缺交/连缺跳过（与同批次缺交+迟到既有口径一致）。
- 优先级：请假 > 出勤异常 > 原状态；考勤批次只作映射来源、不作映射目标；
  已删除批次不参与，存量 revoked 批次不参与；作业共享关闭时不跨域映射。
- **跨学科共享（2026-10-27 扩展，用户拍板：其他学科老师不该被冤枉）**：跨域
  事实采集不再限定 link.subject——对侧关联班**任意学科**（尤其考勤批次）登记的
  请假/出勤异常，经 link 成员交集映射到本侧（班主任考勤记请假 → 教学侧物理
  批次同样显示请假免交，反向同理）；仍只摘取出勤事实本身（excused/出勤词/
  考勤缺勤行），对侧作业内容、名单、计数一律不外泄；作业共享关闭时依旧不跨域。
- **读取即映射、零数据迁移**，历史记录同样生效。单一实现
  `_day_exception_flags`（按天聚合事实）+ `_adjusted_submission`（临时行修正，
  绝不写库），消费方：列表项计数与 `*_ids` 数组、批次详情、看板聚合、
  confirm/patch 回执、preview 展示装饰（附提示语）、学生事件流/画像连缺、
  预警 missing_count、相关性提交率、逐科作业分析（诊断 features/attention
  经事件加载器自动继承），前端天级透视与胶囊由列表 id 数组自动跟随。

## 2. 读取 / 看板

- `GET /api/v1/homework/assignments?mode=&class_id|teaching_class_id=&academic_year_id=&subject?
  &homework_type?&from_date?&to_date?` → 分页列表（含 revision/status/submitted_count/missing_count/
  submission_rate|null 分母标注 `rate_unavailable: true`）。
- `GET /api/v1/homework/dashboard?mode=&class_id|teaching_class_id=&group_by=day|week|month` → 按期聚合：
  `{groups: [{label, assignments, submitted, missing, expected_count, rate?, rate_unavailable?}]}`
  （v2：expected_count 必须返回组内批次应交快照的真实合计；不可计算时明确标注，不得恒 0 误导）。
- 学生维度（画像页消费）：`GET /api/v1/homework/students/{person_id}?mode=` → 该生事件流
  `{events: [{assignment_id, assigned_date, subject, homework_type, status, evaluation?,
  data_domain?}], streaks: {current_missing_streak: int, longest_missing_streak},
  conflicts?: [{date, subject, batches: [{assignment_id, homework_type, data_domain, status}]}]}`。
  - **v1.2（2026-10-24）单账本口径**：`data_domain` 标批次归属域，与当前工作台不同 =
    跨域共享行（前端标「班主任侧/教学侧」）。`conflicts` 列两侧同日同学科登记打架
    （一侧 missing + 另一侧 submitted）的冲突日——该天连缺**已按已交处理**、缺交行
    不进计数，单列供人工核实修正其中一侧。
  - **v1.3（2026-10-25）冲突配对收窄到同作业种类**：只有**同种类**跨域一侧缺交、另一侧
    已交才算「同一份作业记岔了」（冲突日）；**不同种类 = 同日两份不同作业**，缺交照实
    进 missing_count/recent_missing、不判冲突、无琥珀提示（v1.2 会把真缺交按已交藏起来）。
    当天既有冲突对又有未被解释的真实缺交 → 该天按缺交处理、不列冲突日（真实缺交优先
    如实计数；冲突对两行保留在明细中人工核对）。存量数据无需迁移：两名涉事学生（匿名）
    2026-09-23《日常作业》缺交在新口径下自动回归真实缺交。

## 3. 预警时间轴（H03 红线）

- `GET /api/v1/homework/warnings?mode=&class_id|teaching_class_id=&min_missing=2&subject?` →
  `{students: [{person_id, name, missing_count, current_streak, streak_basis: 'events'|'legacy_events',
  recent_missing: [{assigned_date, subject, homework_type}], conflicts?: [{date, subject}]}]}`。
- 连续缺交按天口径（与画像端点 streaks 同一实现）：同日多批次先合并成天（任一缺交该天计 1、
  已交清零停止、请假/出勤异常跳过）；**v1.2（2026-10-24）起跨域合并——班主任按学科、
  教学按天单线（不分作业种类），同一学科两域批次（行政班 + 关联教学班）并成一条线，
  events 与批次轴取并集**；**v1.3（2026-10-25）起冲突日按（域, 作业种类）配对**：同日同学科
  **同种类**一侧缺交 + 另一侧已交 = **冲突日**：按已交处理（打断连缺）、该缺交行不进
  missing_count/recent_missing、单列 `conflicts: [{date, subject}]` 返回；不同种类 = 两份
  不同作业，缺交照实计数、不判冲突；同域内同天混合（如卷一已交+卷二缺交）不算冲突、
  维持 missing；两侧都缺 = missing。
  迁移的仅缺交历史批次无 expected_members 快照时，班主任线按旧版班级级日期轴计算
  （basis='legacy_events'）。
- 仅缺交历史批次（无分母）的学生照常列出缺交计数（事件维度），但 submission_rate 类指标不计算。
- 日维度统计与事件维度预警分别标注口径（响应字段 `basis`）。

## 4. 相关性（成绩 × 作业，Pearson 公共实现 + 两域口径）

- `GET /api/v1/homework/correlation?mode=&class_id|teaching_class_id=&subject=&homework_type?
  &exam_name=`（v2/G08：成绩侧必须复用 P3 统一可读事实查询 readable_facts——含 H-only/T-only
  投影与冲突规则，不得直连本域 facts 致使合法投影样本丢失）：
  - homeroom 口径：Y = 指定 total_type 总分排名（参数 total_type，默认主三门）；X = 作业提交率
    （分母不可用的批次剔除并 warnings 注明）。
  - teaching 口径：Y = 该科 exam 单科班级内名次；X 同上。
  - 响应：`{pairs: [{person_id, name, x, y}], n, r, direction('submit_up_rank_up'|'submit_up_rank_down'),
  caveats: [str]}`；n<5 或零方差 → `r: null, caveats 注明不可计算`；绝不表述为因果。

## 5. 学期管理（H06）

- `GET /api/v1/homework/semesters?academic_year_id=` → `{semesters: [{id, name, start_date, end_date,
  is_current}], auto: bool}`（auto = 按日期自动推算模式，H 版语义）。
- `POST /api/v1/homework/semesters` `{academic_year_id, name, start_date, end_date}`：重叠/同名 422。
- `PUT /api/v1/homework/semesters/{id}`：手工改日期；auto 模式下改过即转手工（保留可回自动）。
- `POST /api/v1/homework/semesters/{id}/restore-auto`：回自动模式（手工日期丢弃前 preview diff）。
- `PUT /api/v1/homework/semesters/{id}/current`：设当前；重复设同一条 → 422（不 500）。
- 前端在设当前成功后，必须立即把班主任与教学两个工作台的筛选记忆同步到返回的
  `academic_year_id`，清空旧学期与跨学年班级/教学班选择并作废在途范围请求；不得依赖
  仪表盘挂载后的纠偏。设置页顶部的学年下拉仅切换本页浏览，不改变全局当前学期。
- 学期表：新表 `ws_homework_semester`（迁移 0005，与 p4 迁移合并发布：`id, academic_year_id FK,
  name, start_date, end_date, is_current, mode('auto'|'manual'), created_at, updated_at`，唯一键
  (academic_year_id, name)）。

## 6. 验收映射

H01（全交+个人例外、两种行顺序一致、同人同批唯一、矛盾拒绝）→ §1.1 解析测试；
H02（重试同批次不增、同日两份不合并）→ §1.2 batch_token 幂等 + existing_batches 测试；
H03（仅缺交历史/名单改变/未知状态）→ §2/§3 分母与 streak 测试；
H04（关联班从任一侧修改物理作业，双方同一规范事实；并发 revision 冲突明确）→ 共享读写 + 乐观锁测试；
H05（换号后按人聚合，历史跟人，身份展开不越域）→ 学生事件流按 person 聚合测试（配 P4 alias 链）；
H06（学期自动/手工/重复编辑 4xx）→ §5 测试。

## 7. 前端（P5-FE 波次）

- `/homeroom/homework` 与 `/teaching/homework`：录入（智能输入框 + 全交/交名单/明细三模式 + 例外；
  智能输入支持「已交：名单」/「学科 已交：名单」反向行——列出的记已交、其余记缺交，
  交名单模式同口径）、批次列表（rate/无法计算标注）、撤销冲突展示。
- 预警时间轴页（events 口径标注）；逐人作业与成绩分析挂载于两域成绩分析页底部
  （作业跟进页不挂载；旧班级相关接口保留兼容，新页面遵循 [逐人契约](student-homework-analysis.md)）。
- 学期设置卡（自动/手工切换、重复编辑错误提示）。
- 沿用 P2/P3/P4 组件与风格；旧 /homework 系列页迁移替换。
