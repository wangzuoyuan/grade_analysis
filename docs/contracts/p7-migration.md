# P7 接口契约：双库迁移演练 / 冲突 / 部署回退（v2，2026-09-12）

v2 依评审意见 Q04–Q08 修订；v1 其余条款继续有效。

依 `docs/planning/04-migration.md` 与 `05-acceptance.md`（M01/M02/D01）起草。
**范围声明（如实登记，不得夸大）**：本仓库无真实生产双库与 NAS 访问；P7 交付 = 可复跑的
**合成双库演练管线** + 冲突/核对报告 + 部署候选配置与回退演练（合成数据）。真实库实测
属上线前现场步骤，由用户在候选验收后执行；本契约产物即其工具与清单。

## 1. 合成源库构造（scripts/migration/ 下）

- `build_synthetic_sources.py`：构造两个独立 SQLite 源库（放 .test-data/migration-rehearsal/，
  绝不触碰 ~/.exam-tracker 与 .sources）：
  - **H 源库**：用本仓 backend 既有 legacy 模型（Exam/SubjectScore/TotalScore/ClassAverage/
    ClassRoster/StudentAlias/HomeworkRecord/StudentNote 等）造两个学年数据，含边界样本：同名不同人、
    跨届复用裸学号（G1::/g1- 前缀各一）、前缀学号 `_anon:`/TMP、仅姓名无学号成员、转班（花名册
    多行）、无成绩成员、同日两份作业、手工历史成绩、撤销快照 JSON（RosterImportBatch/RolloverConfirmBatch
    形态各一）。
  - **T 源库**：按 `.sources/teaching`（f9ab60f）的实际表结构（只读对照其 models）镜像建表造数：
    教学班（两个学年同名班）、成员（字符串标签、成员来源）、单科成绩（含被旧链路丢弃的其他学科列
    与停写的总分列——验证不恢复）、HomeworkRecord（含 submission_status/evaluation）、Teacher（学科配置）。
  - 两库造数后计算并登记文件摘要（sha256）入 MigrationRun.source_fingerprint。
- 幂等：重跑先清空输出目录；样本全部虚构（沿用 tests/v1 命名规则），不提交任何二进制库文件。

## 2. 迁移管线（`run_rehearsal.py`，分阶段可断点续跑）

阶段（每阶段一个事务 + MigrationRun 阶段记录，失败回滚当前阶段、重试从上一致点继续）：
1. `snapshot`：登记源库摘要/schema/行数；目标库为全新 EXAM_TRACKER_DIR（显式独立目录）。
2. `import_homeroom`：H 源 → ws homeroom 域。Exam/成绩 → AcademicYear/Term 推导 + ScoreFact
   （source='migration:h'）；ClassRoster → AdministrativeClass/Enrollment（有效期按学年推导，未知
   不伪造精确日）；StudentAlias → WsStudentAlias（**保留原值含前缀，不剥壳**）；每来源行写 SourceMap
   `(fingerprint, table, pk) → target`；学号撞号（同名不同人）→ 隔离待核实（identity 各自独立，不合并）。
3. `import_teaching`：T 源 → ws teaching 域，同规则；**被丢弃的其他学科/总分列不恢复**（计入
   报告 lost_columns 统计）。
4. `link_suggest`：按行政班↔教学班标签给出 link 候选与 LinkedStudent 候选（同号同名仅作 suggestion，
   **不自动建**；合成演练按"声明表"确认：synthetic 声明甲乙为同一人 → 写入 confirm_basis='rehearsal-declared'）。
5. `conflict_report`：关联班同场考试同学科两域值比对 → 冲突清单（不静默覆盖，M02 成绩侧）。
6. `undo_audit`：H 源撤销快照 JSON 逐项映射验证——能安全转换的标记 `convertible`；不能的（引用
   已漂移/无法映射）保留只读审计标记 `read-only`（M02 撤销侧）。
7. `verify`：对账（§3）。

重复跑相同源摘要 → **零新增业务行**（SourceMap 命中即跳过）；源摘要变化 → 新 MigrationRun 批次。

## 3. 数据验收（verify 阶段断言，写入报告）

- 三分类互斥完备：每来源业务行 = 已映射保留 / 经确认共享 / 隔离待核实，合计 = 源行数，遗漏 0。
- 目标库 `PRAGMA integrity_check`、`foreign_key_check` 通过。
- H 全科/总分逐字段保留（抽样全比对）；T 其他教学班数据只在 teaching 域；声明共享的关联班同科
  原值一致或入冲突清单。
- 名册/缺交条数对账；统计口径变化逐条解释（报告附 diff 说明，不为对齐旧口径改数据）。
- M01 用例：整管线连跑两次 → 第二次零新增；人为在 import_teaching 中途 kill 进程 → 重跑成功且
  无重复行（MigrationRun 阶段续跑验证）。

## 4. 部署候选与回退（D01，合成演练）

- `deploy/`：独立 Compose 项目配置 `performance_unified`（新数据目录/备份目录/端口经检查未占用——
  合成检查脚本 + docker compose config --quiet 验证；真实 NAS 端口预检留现场清单模板）。
- `rehearse_backup_restore.py`：对合成目标库执行 应用内备份 → 恢复到新目录 → 查询核对（含 ws 表与
  关联关系）；模拟"上线后已有新写入"场景：备份新库 → 导出新增/修订清单（SourceMap 之后的新行）→
  只读回退旧库 + 增量清单留存（不宣称无损，报告明确"增量待回放"）。
- 旧站共存：配置层面不删除旧容器/卷/目录（规划原文），切换脚本只做流量入口切换 + 停写检查清单。

## 4.1 v2 增补（Q04–Q08）

- **Q04 隔离待核实的真实门**：quarantined 不能只是报告标签——迁移为待核实事实引入持久
  状态位（如 score_fact/enrollment 增加 `pending_review` 标记或独立 pending 表），**业务读
  路径（名册/成绩/统计/作业/共享/AI 工具）全部排除**待核实行；仅专用待核实视图（迁移工具
  报告接口）可查原记录。迁移目标直接启动应用后的 API 反例必须验证（_anon/TMP 学生不出现在
  正式接口）。
- **Q05 对账母集 = 源业务行全集**：verify 覆盖**所有非零来源表**（手工历史/班均/阈值/学期
  配置/档案/审计等），逐表进入"已映射保留/受控共享/隔离待核实/显式不迁移（注明去向）"清单；
  无法转换的原行完整可追溯保存；"遗漏 0"以全子集合计断言。**共享分类按事实**（学科/时期/
  类别/关联范围）判定，不按人整体升级 shared。
- **Q06 一致性备份与完整恢复**：备份必须用 SQLite backup API（或停写 checkpoint）产出一致性
  快照（WAL 已提交数据不丢）；恢复覆盖全部约定资产（db + 备份目录 + 上传原件等）；增量导出
  以备份点时间戳追踪新增/修改/删除并覆盖相关业务表（不依赖演示专用 source 字符串）。
- **Q07 可构建可启动候选**：backend/Dockerfile 携带 alembic.ini + alembic/ 迁移目录（含锁定
  依赖）；ensure_app_schema 在已处 head 时**惰性跳过** ScriptDirectory 读取（无迁移目录环境
  不崩）；compose 提供 build: 配置（或可用镜像）；健康检查命令与镜像内可用工具一致；本地按
  Dockerfile 目录布局真实启动 + 旧版本库升级 + 健康验证。
- **Q08 T 源语义修正**：T 旧库 subject 列 = **作业种类**（校本作业/周末作业/试卷订正…），
  任教学科来自 Teacher/教学班配置——迁移时 `assignment.subject = 教师任教学科`、
  `homework_type = 旧 subject 原值`；未知科目 → 待核实不猜。合成样本必须按真实语义造数
  （多种类），逐字段对账。
- **路径保护统一**：三个脚本同等根目录约束（--out 不得 rmtree 演练根外任意目录；
  backup_restore 的 EXAM_TRACKER_DIR 同校验）；M01 中断测试覆盖真实事务中断
  （import_teaching 中途失败回滚后续跑）。

## 5. 报告与状态

## 6. P8 真实快照入口（v3，2026-09-12）

- `run_real_migration.py --homeroom-db H --teaching-db T --target-root ROOT` 是唯一正式入口；两源以 SQLite URI `mode=ro` 打开，源路径不得落在 target-root 内。`--preflight-only` 只输出脱敏 manifest，在任一校验失败前不得创建 target-root。
- manifest 固定登记两源 sha256、表/列/行数、完整性、FK 按子表计数和考试日期精度；任何人工关联或冲突决策文件必须带两份源 sha256，摘要漂移即拒绝。没有决策时绝不建立 link/LinkedStudent。
- `YYYY-MM` 保存为原字符串与 `month` 精度，`ScoreFact.exam_date` 保持 NULL；月精度事实默认不参与跨域共享，不能以月初/月末伪造考试时点。只有后续经摘要绑定、人工裁决且能证明整月成员交集的专用流程才可改变此状态。
- 不能安全业务化的来源行必须逐行写入 `source_archive_record`；归档与 `SourceMap`/待核实一样是持久去向，业务 API 不读取它。上传元数据单独标识原件状态；T 名册外作业/档案、其他学科/总分等不得删除或进入教学分析。

  lost_columns 统计、undo 审计分类、备份恢复演练结果、**现场实测待办清单**（真实双库/NAS/端口/停写）。
- P7 置 review 的条件：合成演练全绿 + 报告如实区分"已演练/待现场"；不宣称历史数据合并完成
  （规划红线：P7 前不能称为历史数据合并完成——真实切换完成前始终保持此表述）。
