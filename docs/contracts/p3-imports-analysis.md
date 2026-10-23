> 2026-10-21 教学展示调整：教学学生明细响应增可空 `grade`（实际考试年级；未知或冲突为 null），学生行增 `grade_label`、`xueji_rank`、`grade_percentile`（0–1）。等级标签按导入等级分的 11 档精确映射，排名继续为当前请求范围的后端 min-rank。前端合并所选管理班为一张表，不再分班重算，去来源列；高一显示来源学籍排名和百分比，高二三显示等级分和等级，年级未知显示指标并集。个人画像 ProfileExam 增来源 `grade_percentile`，教学趋势仅用该百分比，0% 在顶部，缺考/缺指标断开，分数 KPI 不变。质量对比原表与参照规则见 teaching-platform.md。班主任界面保持原样。

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
- 响应：`{token, expires_at, mode, items: [{filename, kind: student_scores|class_averages|rank_bands|unknown,
  parsed_ok, message?, exam_name, exam_date, subject?, class_label?, row_count,
  known_students, new_students: [{name, alias, xueji?}], identity_candidates?: [{alias, name, person_id,
  academic_year_id, academic_year_name, basis}], warnings: [str]}]}`
- warnings 至少覆盖：同学号不同姓名（**本学年在册命中**的 H 撞号防呆语义，确认时整文件拒绝；
  跨学年/未标学年历史同号异名出候选清单而非撞号警告）、同文件重复学号、
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
    持有该 alias 的历史登记（伪造/错配 422）；`identity_new_aliases` 中的 alias 必须出现在预览候选
    清单里（伪造/未在预览出现 → 422 声明无效）；同名不能消除歧义。**撞号仅指本学年在册命中
    （含 NULL 学年但有效期明确覆盖文件日期的命中）且姓名不同 → 整文件 409 拒绝**；跨学年/未标
    学年的历史同号即便姓名不同也归候选（学号被新一届复用的正常形态），可经上述二选一显式消歧，
    新建时历史学生记录不变、新身份独立建档。
    未标学年（academic_year_id 为 NULL）的 alias 登记**不得视为已证明的本学年身份（v2.2，G06）**：
    默认生成候选；仅当该 alias 行自身有效期（valid_from/valid_to）明确覆盖目标文件日期
    （可证实该号事发时仍在册）方可直接命中为 known。任何路径确认接续后均按 F07 补学籍，
    不存在"只补 alias 不补学籍"的例外。
    **v2.3（2026-10-19 重名事故扩展）**：未标学年且无有效期证据的 legacy 行（如 migration:h
    迁移行），凭「同学号 + 同名（display_name）+ 本班在册 Enrollment」三重证据直接命中为
    known（homeroom 模式）；任一证据缺失仍走候选。三重证据是 G06 判据的收窄扩展——
    2026-10 生产事故中此类行永远无法通过有效期判据，导致同班同名同学号的旧身份被绕开、
    导入新建第二身份（名册重名 + 旧身份全「—」）。
    **v2.4（2026-10-20）学籍随导入更新**：明细表文件「学籍」列有值即**覆盖**身份 xueji
    （学籍可变，以最新文件为准；文件未带值不动既有登记）。
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
    **同学号不同姓名（本学年在册命中）→ 整文件 409 拒绝**（防两人成绩混档），不自动改名合并；
    跨学年历史同号异名走候选消歧（§1.1）。
  - 批内去重按**解析后的 identity_id**（未建档新学号才按 alias）：同一学生同批以新旧两个
    学号出现时同键同值合并为一行；数值不同 → 409 列出学号对与两值（revise 不能消解批内
    自相矛盾），整批零写入。
  - homeroom 模式：ScoreFact 写全科 + 总分（total_type 行），`class_ref_id=行政班 id`，`source='import:<filename>'`。
  - teaching 模式：ScoreFact 仅任教学科（其他学科列丢弃并计入 warnings），`class_ref_id=教学班 id`；
    文件中学生补入 TeachingClassMember（成员同步）。
  - `class_averages` 文件：写入独立的 `workspace_class_average` 官方年级班均事实，按与库唯一约束
    `uq_workspace_class_average_natural_key` 一致的自然键 `(domain, academic_year_id, event_or_name,
    grade, class_num)` 定位（事件归并后按 exam_event_id 集合内全部行；legacy 按 event IS NULL +
    exam_name；**不用 exam_name/exam_date 过滤**——改名/改日期/归并集多行时旧口径会漏查或一对多）：
    同值幂等 skip、异值 revise 统一更新集合内行或 409 可读冲突，绝不撞 IntegrityError/
    MultipleResultsFound；保留班级类型、班主任、各科均分与各总分口径。它与学生明细可以在同一次
    多文件 preview/confirm 中提交，并与整批共享原子性、日期一致性、
    幂等和 `revise` 修订规则；不得从当前行政班的 `ScoreFact` 样本反推或冒充全年级班均表。
    **v2.3（2026-10-19）总分指标包**：高二/高三均分表的总分子列（均分/及格率/高分率/低分率/标准差）
    与顶级「3+3校名次/主三门校名次」列全部入库——`total_averages` JSON 由裸数值升级为
    指标包 dict（及格率等 0<v≤1 自动 ×100 统一存百分数；旧数据读端归一化为 `{均分: v}`）；
    校名次仅入库备查、读接口不下发（展示用应用计算的班型内排名）。
  - `rank_bands` 文件（v2.3，2026-10-19）：学校《六门/三门总分名次段表》导入，判型为
    row2=「班级/班主任/段列…」且段列含「-」。落独立表 `workspace_rank_band`（每行一个班，
    `bands: [{label, lo, hi, count}]` 与文件列序一致），自然键
    `(domain, academic_year_id, event_or_name, grade, total_type, class_num)`，幂等 skip/
    revise/409 语义与均分表同一套；口径由标题+文件名判定（「六门」→3+3，「三门/主三」→主三门）；
    非数字班号行忽略；无考试名，须由表单 `exam_name`/考试选择流程挂事件（纯名次段考试的
    事件可见性由 exam_events.candidates 纳入 rank_band 行支撑，重导幂等不另建事件）。
  - 幂等/冲突（按自然键 `(domain, academic_year_id, exam_name, identity, subject_key, total_key)` 与库内比对）：
    同值 → skipped；无键 → 插入；**不同值 → revise=false 时整批 409 + `conflicts`
    列表（person/subject/exam_name/库内值/新值）零写入；revise=true 时覆写并 `data_revision+1`（E05 修订语义）**。
- 响应：`{imported, skipped, revised, exams: [{exam_name, exam_date}], students_created, members_synced}`

### 1.3 ScoreFact 增列（迁移 0004）

- `grade_score Float NULL`：等级分/赋分列（E03；原始分仍在 `score`，缺考两者皆 NULL）。
  teaching 上传含等级分列时一并入库；homeroom 含则同规则。读取响应在 ScoreRow/ProfileExam 增加可选
  `grade_score`。
  **2026-10-22**：ProfileExam 增可选 `rank`（校名次，resolve_year_rank 口径：学籍名次优先、
  其次年级名次，缺考/无名次 null）——仅画像与打印报告的**总分行**考试点有值，单科行恒 null
  （档案页总分行显示「第N名」）。

### 1.4 `GET /api/v1/shared/exams?mode=&academic_year_id=&class_id=|teaching_class_id=`

- 该域该学年、当前班级范围内已导入的考试：`{exams: [{exam_name, exam_date, row_count,
  subjects: [str]}]}`，按考试日期降序。homeroom 的 subjects 为本班出现的学科并集；teaching 恒仅任教学科。
- teaching 缺省 teaching_class_id = 同学年同学科全部教学班**并集**（集成者修订：与 /api/v1/scores
  教学模式"全部所教班"缺省一致；仅 §1.1 导入 preview/confirm 在多教学班时要求显式选择）。
- **v2.1（F09）统一可读事实口径**：`/shared/exams`、`/api/v1/scores`、`{mode}/students` 列表/画像、
  §2 全部分析端点必须基于**同一**可读事实查询。各端点不得自建第二套投影/过滤逻辑。
  **v2.3（2026-10-19 用户裁决）两域成绩不互通**：该统一口径的「对侧投影」分支整体退役——
  readable_facts 只读本域事实+去重，跨域冲突标记不再产生（同 (人,学科,考试) 键的同域重复
  来源注记保留，仅供考试管理「字段差异」展示与指标补齐 metric-fill 消费）；`/shared/exams`
  与各域考试目录只列本域事件（按事件直查的考试存在性仍放宽为任一域存在 → 200 空态）。
  名册/作业共享与 HomeroomTeachingLink 不受影响。**2026-10-20 Q10 清理**：§1.6 冲突/
  规范值端点与各响应 shared_conflict/shared_conflicts/shared_subject_score 字段已删除。

### 1.4.1 考试维度成员口径（v2.1，F08；v2.2 修订 G07）

- 考试维度端点（`exams/{exam_name}/stats|students`、`bands`、trends 的各场数据）按**考试发生时名册**
  解析成员：homeroom = Enrollment 有效期覆盖 `exam_date`（**不按当前 status 过滤**——status 描述的是
  当下在班状态，考后 transferred/graduated 不得改写历史考试人群，G07）；teaching =
  TeachingClassMember 有效期覆盖 `exam_date`。考后离班/考后入班不得改写历史考试人群。
- 非考试维度（dashboard/看板/预警/相关性/当前名册/共享配对候选）沿用当前名册（查询时点 + 当期
  status 口径）。
- 上述两类响应 metadata 均注明 `membership_basis: "exam" | "current"`。教师访问历史班仍受
  绑定/任课作用域校验（同既有规则）。

### 1.6 跨域冲突规范值确认（已退役，2026-10-20 删除）

> **状态**：v2.3 两域成绩不互通裁决后，`GET /shared/links/{link_id}/score-conflicts` 与
> `POST /shared/links/{link_id}/canonical-scores` 两端点恒空/不可达；2026-10-20 Q10 清理
> 随「冲突清单」按钮与弹窗一并删除（前端入口、后端端点、`score_conflict_pairs` 判定与
> 前端规范值交互全部移除）。两域分数不一致属预期（各自上传、各自显示本域值），不再有
> 跨域冲突语义。同域重复来源的字段差异提示改由考试管理（exam-events 契约）承载。


## 2. 分析端点（读 ScoreFact；作用域解析/metadata/空态沿用 §1.3/§1.4 既有规则）

### 2.0 学科/总分列序规范（2026-10-22 用户拍板；码点字母序退役）

- stats 的 `subjects`/`totals` 列表序、students 的 `scores`/`grade_scores`/`percentiles`/
  `totals`/`ranks` 键序、画像与打印报告的 `subjects`/`totals` 分组序，统一按
  `app.analysis.definitions.order_subjects/order_totals` 输出，不再按 Python sorted()
  字母序（旧码点序会把「化学、历史、地理、政治、数学…」排在前面）：
  - 学科：语文、数学、英语、物理、化学、生物、政治、历史、地理（仅列有数据的）；
    未知学科排已知之后（按名称排序）。
  - 总分：高一 = 主三门、五门、九门；高二/高三 = +3、主三门、3+3；未知口径排已知之后。
  - 年级来源：stats/students 取该考试事件的 grade（`_exam_grade_of_facts`，同场年级
    混杂或不可得 → None）；画像/报告取该生最新一场可定位年级的考试事件
    （`_report_grade_of`，两处共用保持画像页与打印页一致）。年级不可得（legacy 事实
    无考试事件）→ 总分保持既有字母序回退，学科序不受影响。
- class-averages 与 rank-bands 维持各自既有 preferred 顺序，不在本规则范围。

### 2.1 homeroom（/api/v1/homeroom/analysis/*）

- `GET exams/{exam_name}/stats` → `{metadata, subjects: [{subject, avg, max, min, valid_count,
  missing_count, score_basis}], totals: [{total_type, avg, max, min, valid_count}], cohort_size}`
- `GET exams/{exam_name}/students` → `{metadata, students: [{person_id, name, alias, xueji?, class_num?,
  scores: {subject: score|null}, grade_scores?: {subject: grade_score|null}, percentiles?: {subject_or_total:
  pct|null}, totals: {total_type: score|null}, ranks?: {total_type: school_rank|int}]}`
  （v2.3，2026-10-19 明细表按学校导出重制：xueji 为身份学籍代码、class_num 为行政班号、
  grade_scores 为选科等级分、percentiles 为年级百分位（0-1）、ranks 为各总分口径校名次——
  resolve_year_rank 口径，缺考 null。2026-10-20 起 shared_conflicts 字段随 Q10 退役删除）
  前端成绩明细按显示学号自然升序（剥跨届前缀，数字段按数值），无学号者按姓名拼音排末尾；
  同学号/同名以 person_id 稳定排序。此行序与成绩无关，班名次继续按本班有效总分独立计算。
- `GET exams/{exam_name}/class-averages` → `{metadata, grade, subjects, total_types,
  current_class_num, rows: [{class_type, class_num, teacher_name, subjects, totals, total_ranks}]}`。
  数据只读自已确认导入的官方班级均分表。**v2.3（2026-10-19）**：`totals` 为口径→指标包
  `{均分, 及格率, 高分率, 低分率, 标准差}`（旧数据归一化为 `{均分}`，无数据口径 null；文件自带
  校名次入库备查不下发）；`total_ranks` 为**班型内排名**——按 class_type 分组（平行班/实验班/
  未分类各自）同口径均分降序、同分同名次计算。某总分口径某组全为 0 时视为来源未提供，
  排名返回空，不制造并列第一。`current_class_num` 为当前班主任绑定行政班班号（前端班级
  对比页用于高亮本班；无绑定班时为 null）。
  前端按班型逐组展示“各班成绩→平均/最高/最低汇总→下一班型”；每组汇总名称纵向合并三行，
  统计类型横跨班级与班主任两列。班主任均分表及学生明细采用 Ant Design 官方 Blue/Gray
  色板：白/blue-1 隔行、平均 blue-2、最高 blue-3、最低 gray-4，所有汇总指标列同一行同色。
  **v2.5（2026-10-22 用户裁决）**：接口仍下发五指标包（AI 工具链等消费方不变），但前端
  总分区块只展示 均分+班型排名 两列——率值/标准差在导入数据中恒为空，不再渲染（表头
  colSpan=2、表体/汇总/导出 CSV 同步收窄）。
  **v2.6（2026-10-23 汇总口径）**：文件自带的「平行班汇总/实验班汇总」行不入库（班级列
  非数字被解析器跳过），页面的 平均/最高/最低 由前端按班型现算；选科列的 **0 = 该班无人
  选此科**，不算成绩——除数与最高/最低只统计非零班（与学校汇总行口径一致；修复前 0 计入
  除数把平均拉低、最低恒为 0）。某列某组全为 0 时汇总显示「—」。**班级对比页
  （/homeroom/compare）同口径**：较年级均差的年级平均、单科名次同样只统计非零班，
  无人选（0）的班不作差、无名次（均分列仍显示文件原值 0 供核对）。
- `GET trends?person_id=` → `{person_id, name, years: [{academic_year_id, academic_year_name,
  subjects: {subject: [{exam_name, exam_date, score, grade_score?}]}, totals: {...}}]}`
  （E03：跨学年分段展示，不跨年直接比较九科总分；响应按学年分组即结构上防止跨年连算）
- `GET bands?exam_name=&subject=&metric=` → 段位分布 `{bands: [{label, count, students: [person_id]}],
  total_type?}`。**v2.1（F10）**：段位阈值口径为**年级名次**（AnalysisConfig 的 400/500 是名次阈值，
  绝不可以原始分镜像比较）。当前 ws 域不存年级名次事实，因此 bands 一律返回 409
  invalid_scope_param（detail=「段位阈值口径为年级名次，当前范围无名次数据，不可计算」），
  直至引入合法名次来源或显式的分数段配置（独立于名次阈值、按学科/分制定义）。
  （v2.3 注：本端点保留给 AI 工具/重点关注阈值口径；成绩分析页 UI 改用下方 rank-bands。）
  **v2.4（2026-10-20 用户裁决）**：段位分布卡以独立 tab 恢复（按明细表名次现算），本端点
  **只统计学校明细表学籍=1（闵中学籍）学生**（学籍存身份、随每次明细表导入按文件值覆盖
  更新，查询按当前值过滤——历史考试图表即时联动）；无名次的学籍=1 人口仍 409。
  **v2.5（2026-10-23）**：每段新增 `names: [姓名]`（段内**按名次升序**、与 students
  一一对应，display_name 缺失者跳过；AI 工具 get_score_bands 经 _dump 自动透传）。
  前端段位分布卡口径下拉**排除 +3**（选修等级分之和，无名次数据、选中恒 409 的废项；
  主三门/3+3 保留），悬浮分段条浮出「段名 · 人数 + 段内学生姓名名单」，count=0 的段
  不出浮层，旧后端（无 names）时浮层静默降级为仅人数。
- `GET exams/{exam_name}/rank-distribution`（v2.4）与 `GET rank-frequency`、
  `GET exams/{exam_name}/rank-range` 的 **total_rank 口径**同受学籍=1 过滤（频次/区间的
  单科百分位/等级分口径保留全部学生）；班主任工作台全域生效，教学侧不动。
- `GET rank-metrics?mode=` 与 `GET exams/{exam_name}/rank-range`（**v2.7，2026-10-23 用户
  拍板**）：指标清单按年级扩全且 frequency 与 range 同一份清单——高一=九学科
  （subject_percentile）+ 主三门/五门/九门总分；高二/三=语数英（subject_percentile）+
  六选科「{学科}等级分」（subject_grade_score，range 模式新增）+ 主三门/3+3 总分。
  rank-range 的 rank_min/rank_max **单位随 metric_kind**：total_rank=名次（≥1，学籍=1 过滤）、
  subject_percentile=年级前%（0–100，越小越靠前）、subject_grade_score=等级分（40–70），
  越界或下界大于上界 422；students[] 每行新增 `metric_value`（筛选依据值：名次/前%一位
  小数/等级分），单科口径 year_rank 仅在该科 fact 带真实名次时填；排序=总分名次升序/
  前%升序/等级分降序；metric_note 按口径三套文案。前端区间输入的标签/钳制/默认值
  （名次 1–100、前% 0–100、等级分 40–70）与表格末列（年级名次/年级前%/等级分）随 kind
  自适应，跨类型切换指标时重置区间为该类型默认值。
- `GET exams/{exam_name}/rank-bands?total_type=`（v2.3，2026-10-19 名次段分布）→ `{metadata,
  exam_name, grade, total_type, available_total_types, current_class_num, classes:
  [{class_num, teacher_name, total, bands: [{label, lo, hi, count}]}], grade_bands}`。
  数据只读 workspace_rank_band（homeroom 域/同学年/同年级），不从学生名次现算、不跨工作台取数；
  分段与人数完全来自导入的名次段表；`total_type` 缺省取第一个可用口径（preferred 顺序
  3+3→主三门）；无导入数据时 classes 为空列表（200，前端呈现上传引导空态，绝不 404、
  绝不回退全年级或编造分段）；`grade_bands` 为全年级各段合计。

### 2.2 teaching（/api/v1/teaching/analysis/*）

- `GET exams/{exam_name}/stats?teaching_class_id=` → `{metadata, subject, avg, max, min,
  valid_count, missing_count, rank_min, rank_max, score_basis: raw|grade, cohort_size}`
- `GET exams/{exam_name}/students?teaching_class_id=` → `{metadata, students: [{person_id, name,
  class_label, score: raw|null, grade_score?, rank, source_domain}]}`（rank 为**本班内**名次；
  反向投影的 H 域事实参与名次计算并标 source_domain）
- `GET class-compare?exam_name=` → `{classes: [{teaching_class_id, class_label, member_count,
  subject_avg, score_basis, source: estimated}]}`（E04：本班样本均分一律标 `estimated`；
  官方班均表未入库前不提供 `official`，绝不拿班内样本冒充年级/官方口径）
- `GET exams/{exam_name}/score-bands?band_width=&teaching_class_id=`（2026-10-18，官方《分数段
  分析》表同构的本班口径）→ `{metadata, subject, band_width, score_basis, classes: [...], merged,
  small_sample}`；每个班级列 `{teaching_class_id, class_label, valid_count, missing_count, max, min,
  avg, stddev, bands: [{low, high, count, cumulative, cumulative_pct}]}`，`merged` 为当前范围合并列
  （teaching_class_id=null）。段口左闭右开（90 入顶段 `[90,+∞)`、89.5 入 `[80,90)`、0 入 `[0,10)`），
  顶段下界=严格小于 100 的最大段宽整数倍；`band_width ∈ {5,10,20}`（缺省 10，非法 422）；
  累计自高分段向下、比例分母=valid_count（0 人时 null）；`stddev` 为总体标准差 σ；成员口径同
  stats/students（F08 考试时点名册 + F09 readable_facts 含 H 域反向投影行）；仅本班口径，
  绝不提供年级/官方列（官方班级均分走班级对比页）。

### 2.3 计量规则（E02/E04 红线，两域统一）

- 缺考（score NULL）：不计入均分/名次分母，单独计 `missing_count`；显示"—"，不转 0。
- 同分同名次（min-rank，如 1,2,2,4）；名次只在本班/本教学班成员内计算。
- `valid_count < 5` → 响应附 `small_sample: true`；不输出百分比排位等不稳定指标。
- 未知/不可计算（如无年级数据时的 grade_rank）：字段缺省并在 metadata 注明，不编造。
- 仅百分位无原始分的行（H 旧数据形态）不进入本域分析（P3 域内事实必有原始分或 NULL）。

## 3. 前端页面（P3-FE 波次）

- `/homeroom/scores`：考试选择（/shared/exams）→ 考试概览、完整年级班均表（表头按学校导出：班级类型
  同值合并单元格、语数英单列、六选科原始/等级子列、总分仅 均分+班型排名 两列（v2.5：率值/标准差
  不再展示），分组平均/最高/最低及导出）、
  学生成绩明细表（学号/班级/学籍/姓名 + 选科原始/等级 + 总分/百分位/校名次/班名次，班名次本班口径）、
  名次段分布（读导入名次段表：堆叠条 + 学校表格样式）、段位分布（v2.5：三段阈值按明细名次
  现算、口径下拉排除 +3、悬浮分段条浮出段内学生姓名名单）、排名频次、区间筛选、重点关注。
- `/teaching/scores`：考试选择 → 单科 stats + 分数段分析卡（本段/累计/累计比例 + 人数/最值/均分/σ，
  段宽 5/10/20，多班范围含合并列；全零尾段折叠）+ 学生行（rank/grade_score/source_domain，分班展示）
  + 班级对比卡。
- `/upload`（重构）：mode 感知（当前工作台决定导入域与范围参数），preview 表格展示 items/warnings，
  confirm 含 revise 开关（冲突时提示）；沿用既有拖拽/文件选择 UI 模式。
- 旧 /exam、/student 页暂不迁移（P4 处理），但顶部工作台切换器保持可用。

## 4. 验收映射

E01（同输入 H 全科/T 单科一致且 T 无其他科）、E02（NULL/0 区分、同分同名次）、E05（幂等/冲突/回滚）
→ imports 端点测试；E03（跨学年分段）、E04（estimated 标注/small_sample）→ 分析端点测试；
S02/S03/S05 → 复用既有投影门（实现时不得绕过 _queries.gated_*）。
