# P5 接口契约：作业批次 / 双向共享 / 预警 / 相关性 / 学期（v1，2026-09-11）

依 `docs/planning/03-architecture.md` §4、`05-acceptance.md`（H01–H06）、
ADR-008 与 `docs/baseline/api-diff.md`（B5/D3）起草；表结构沿用 p1-api.md v2 §2.1（R10 已落地，
迁移 0003）。基础约定沿用 p1-api.md §0。

## 0. 核心语义（全端点统一执行，违反即返工）

- `subject`（学科）与 `homework_type`（作业种类）分列；同日同科同种类多份作业 = 多个
  assignment（不同 batch_token），绝不合并。
- 应交分母 = assignment.expected_members_json（确认时快照），不是当天班级人数；快照按规则
  扣除 excused 后为有效分母。旧迁移批次按原班级与事件日有效成员恢复名单。
- 状态：submitted/missing/excused。采用例外登记：名单中没有缺交或请假记录的人默认已交；
  旧 unknown 仅作存储兼容，业务读取归一为 submitted，不再向老师展示。
- 连续缺交按收交事件（assignment.assigned_date）排序：submitted 打断连续，excused 跳过。
- 双向共享：assignment 归属一域（data_domain + class_ref_id）；对侧经 **active link + 期满 +
  LinkedStudent 交集 + share_categories 含 current_subject_homework** 才可读/写同一事实（读走
  _queries 同风格的门；写走带 link 校验的端点）。其他教学班/其他学科绝不互见。
- 修订：编辑/撤销递增 assignment.revision（乐观锁，旧版改 409）；撤销（status='revoked'）前有
  后续依赖（其上有评价编辑）→ 409 列冲突，不覆盖。
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
  assigned_date, due_date?, input: {kind: full|names|detailed, names?: [str], rows?: [{name_or_alias,
  status: submitted|missing|excused, evaluation?}], all_submitted?: bool, exceptions?: [{name_or_alias,
  status, evaluation?}]}}`
- 解析（零写入，token 化）：
  - `full`（全交台账）：展开当期应交成员快照为 submitted，再应用 exceptions（明确个人例外）；
    **先解析全批次再应用例外，行顺序不影响结果**（H01）。
  - `names`：仅列出的学生为 submitted（其余不写行——不推断缺交）。
  - `detailed`：逐人行。姓名歧义（同班同名）→ 422 列候选；学号优先消歧。
  - 未列为缺交或请假的应交成员默认已交；不提供 unknown 录入。
  - 同日同科同种类已有批次：preview 返回 `existing_batches: [{assignment_id, batch_token, revision}]`
    提示"编辑既有批次或新建"；**不自动叠加**（H02）。
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
- `DELETE /api/v1/homework/assignments/{id}`：软撤销 status='revoked'；其上有**晚于创建的**评价编辑
  → 409 列冲突清单；否则成功。撤销后指标即时重算。
- `GET /api/v1/homework/assignments/{id}` → 详情（含 submissions 与 expected_members）。

## 2. 读取 / 看板

- `GET /api/v1/homework/assignments?mode=&class_id|teaching_class_id=&academic_year_id=&subject?
  &homework_type?&from_date?&to_date?` → 分页列表（含 revision/status/submitted_count/missing_count/
  submission_rate|null 分母标注 `rate_unavailable: true`）。
- `GET /api/v1/homework/dashboard?mode=&class_id|teaching_class_id=&group_by=day|week|month` → 按期聚合：
  `{groups: [{label, assignments, submitted, missing, expected_count, rate?, rate_unavailable?}]}`
  （v2：expected_count 必须返回组内批次应交快照的真实合计；不可计算时明确标注，不得恒 0 误导）。
- 学生维度（画像页消费）：`GET /api/v1/homework/students/{person_id}?mode=` → 该生事件流
  `{events: [{assignment_id, assigned_date, subject, homework_type, status, evaluation?}], streaks:
  {current_missing_streak: int, longest_missing_streak}}`。

## 3. 预警时间轴（H03 红线）

- `GET /api/v1/homework/warnings?mode=&class_id|teaching_class_id=&min_missing=2&subject?` →
  `{students: [{person_id, name, missing_count, current_streak, streak_basis: 'events'|'legacy_events',
  recent_missing: [{assigned_date, subject, homework_type}]}]}`。
- 连续缺交按天口径（与画像端点 streaks 同一实现）：同日多批次先合并成天（任一缺交该天计 1、
  已交清零停止、请假/出勤异常跳过）；班主任按学科分线取最大，教学按天单线（不分作业种类，
  `streak_homework_type` 恒 None）。
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

- `/homeroom/homework` 与 `/teaching/homework`：录入（智能输入框 + 全交/名单/明细三模式 + 例外）、
  批次列表（rate/无法计算标注）、撤销冲突展示。
- 预警时间轴页（events 口径标注）；相关性散点卡挂载于两域成绩分析页底部
  （作业跟进页不再挂载；r 不可计算态，考试跟随成绩页顶部选择）。
- 学期设置卡（自动/手工切换、重复编辑错误提示）。
- 沿用 P2/P3/P4 组件与风格；旧 /homework 系列页迁移替换。
