# P0-A1 对照清单：新旧分析口径统一（进退步 / 波动 / 偏科 / 段位与名次区间 / 跨学年趋势）

状态：P0-A1 已实施。日期：2026-09-29。
分支：`task/p0-a1-definitions`。

两条分析路径：

- **旧路径**：`backend/app/analysis/router.py`，挂载在 `/api`（`backend/app/main.py:274`
  `app.include_router(analysis_router, prefix="/api")`），数据源为旧库
  `SubjectScore` / `TotalScore` / `Exam`（`backend/app/db/models.py:73,91`）。
- **新路径**：`backend/app/api/analysis.py`，挂载在 `/api/v1`（`backend/app/main.py:282`），
  数据源为 `ScoreFact`（`backend/app/db/workspace_models.py:353`），经
  `_queries.readable_facts` 统一取数（`backend/app/api/_queries.py:922`）。

## 0. 统一实现位置

P0-A1 新建 **`backend/app/analysis/definitions.py`** 作为唯一判定实现，
新旧两条路径全部调用它；各路由只保留取数与响应组装：

| 概念 | 共享实现（唯一口径） | 旧路径调用点 | 新路径调用点 |
|---|---|---|---|
| 名次解析 | `definitions.resolve_year_rank`（definitions.py:89） | router.py:166,335,371,378,390,410,429,469,486,569；rank_metrics.py:124,150 | api/analysis.py:369,656,749,815,882,907,1029,1125 |
| 进退步 | `definitions.progress_issue`（definitions.py:103） | trends.py:64（经趋势标签） | api/analysis.py:916（homeroom_exam_focus 内） |
| 波动 | `definitions.volatility_issue`（definitions.py:119） | trends.py:61 | api/analysis.py:919（homeroom_exam_focus 内） |
| 偏科 | `definitions.subject_weakness_subjects`（definitions.py:135） | router.py:592,1397 | api/analysis.py:923（homeroom_exam_focus 内） |
| 段位 | `definitions.band_flags` / `band_issues`（definitions.py:157,169） | router.py:166,429,589 | api/analysis.py:921（focus）、1135（bands） |
| 班内名次 | `definitions.min_ranks`（definitions.py:181，同分同名次 1,2,2,4） | router.py:784；rank_metrics.py:61 | api/analysis.py:266（`_rank_map` 薄委托） |
| 百分位/名次/等级分分箱 | `definitions.percentile_bin` / `rank_bin` / `grade_score_bin`（definitions.py:212,197,224） | rank_metrics.py（频次统计） | api/analysis.py:291,296（薄委托） |
| 指标选项 | `definitions.metric_options` / `metric_meta`（definitions.py:234,266） | rank_metrics.py:22 | api/analysis.py:272,277 |
| 名次分箱档位 | `RANK_BIN_WIDTH=40`、`rank_bucket_start`（definitions.py:68,192） | router.py:489 | api/analysis.py:296（经 rank_bin） |

阈值常量仍以 `backend/app/analysis/config.py` 为源：进退步
`PROGRESS_RANK_THRESHOLD = 80`（config.py:2）、波动
`VOLATILITY_RANK_THRESHOLD = 120`（config.py:3）、偏科
`SUBJECT_WEAKNESS_PCT_DIFF = 0.20`（config.py:34）；段位阈值运行时读
`AnalysisConfig`（`get_band_config`，config.py:12，出厂 80/400/500/501，
models.py:107-110）。

## 1. 进退步（明显进步 / 明显退步）

**统一后定义**：`rank_change = 上一次有效名次 − 本次有效名次`（名次数值变小
= 正数 = 进步）；`rank_change ≥ 80` 判「明显进步」，`≤ −80` 判「明显退步」；
任一场名次不可得（None）不判。实现：definitions.py:103-116。

**改动前差异**：

| 路径 | 改动前实现 | 与新路径的差异 |
|---|---|---|
| 新 `homeroom_exam_focus` | api/analysis.py（改动前 963-970）：`ranks[-2] − ranks[-1]`，±80 阈值 | 基准实现 |
| 旧 `compute_student_trend` | app/analysis/trends.py（改动前 42-44）：`first_rank − last_rank`（**首末两场**差，跨学年合并时间线上可能跨越多场） | 同一学生、同一组考试可能得出与「相邻两场」不同的结论 |
| 旧 chat 工具 `classify_trend` | app/chat/tools.py:895-911（连续/总体进步退步分类） | 另一套相邻步进分类，文件不在本任务允许范围（见 §6） |

**迁移与兼容**：trends.py 已改为相邻两场口径（trends.py:54
`rank_change = previous_rank - last_rank`），与新路径 focus 端点一致；
「波动优先于进退步」的标签优先级保持不变（trends.py:61-70，与改动前一致）。
接口影响：chat 工具 `student_trend`（app/chat/tools.py:315,335 调用
`compute_student_trend`，签名未变）返回的 `rank_change` 语义由「首末两场差」
变为「相邻两场差」，多场数据时数值会不同；`ranks` 序列仍完整返回，AI 可自行
推导整体跨度。测试佐证：
`tests/v1/test_p0_a1_definitions.py::test_compute_student_trend_unified_criteria`。

## 2. 波动（波动风险 / 波动较大）

**统一后定义**：有效名次**极差**（max − min）≥ 120 且有效名次 ≥ 3 场判
「波动」；None 名次不参与极差。实现：definitions.py:119-133。

**改动前差异**：

| 路径 | 改动前实现 | 与新路径的差异 |
|---|---|---|
| 新 `homeroom_exam_focus` | api/analysis.py（改动前 971-976）：极差 `max−min ≥ 120` 且 ≥3 场 | 基准实现 |
| 旧 `compute_student_trend` | trends.py（改动前 47-50）：名次**标准差** `> 120` | 同一数据两条路径可得出不同波动结论（如名次 [100, 220, 100]：极差 120 触发、标准差约 55.8 不触发） |

**迁移与兼容**：trends.py 的 `volatility` 返回值已由标准差改为极差
（trends.py:58），标签判定改走共享 `volatility_issue`（trends.py:61）。
接口影响：chat 工具 `student_trend` 的 `volatility` 字段数值含义变化
（标准差 → 极差）；现仓库内无任何测试或调用方以数值方式依赖旧标准差
（已核对 tests/test_chat_tools*.py 仅断言 `ranks` 序列）。

## 3. 偏科（严重偏科）

**统一后定义**：`单科年级百分位 − 主三门年级百分位 ≥ 0.20`；任一侧百分位
缺失不判；命中学科按名排序。实现：definitions.py:135-155。

**改动前差异**：三条实现口径一致（差值 ≥ 0.20），但各自手写比较循环——
旧 focus-list（router.py 改动前 588-595）、旧 subject-weakness
（router.py 改动前 1394-1409）、新 focus（api/analysis.py 改动前 983-995，
注意旧 focus-list 与新 focus 的文案分别为 `严重偏科(物理)` /
`严重偏科（物理）`，括号全半角是既有的展示差异，保持不变）。
另有 chat 工具两处手写比较（app/chat/tools.py:682,714，不在本任务范围）。

**迁移与兼容**：三处全部改为调用共享实现，输出（命中学科、顺序、文案）
不变；chat 工具读同一常量 `SUBJECT_WEAKNESS_PCT_DIFF`，数值同源。
测试佐证：
`tests/v1/test_p0_a1_definitions.py::test_subject_weakness_threshold_and_missing`。

## 4. 段位 / 名次区间

### 4.1 段位（高分段 / 临界段 / 薄弱段）

**统一后定义**：`band_flags(rank, config)`（definitions.py:157-167）——
高分段 `1 ≤ rank ≤ high_score_max`、临界段 `critical_min ≤ rank ≤ critical_max`、
薄弱段 `rank ≥ weak_min`；**rank 为 None 时三段全 False（不落段）**。

**改动前差异**：

| 位置 | 改动前行为 | 问题 |
|---|---|---|
| 旧 focus-list | router.py（改动前 559）`rank = t.xueji_rank or t.grade_rank or 9999`，9999 ≥ weak_min(501) → **缺名次学生被伪造落进薄弱段** | 与新路径（无名次不落段，api/analysis.py 改动前 977-980）直接冲突 |
| 新 `homeroom_exam_focus` | 无名次 → 不落段 | 基准实现 |
| 旧 band-trend / 考试详情 rank_bands | router.py（改动前 164-172、425-434）无名次跳过 | 口径与新路径一致，仅未共享实现 |

**迁移与兼容**：旧 focus-list 已改用 `resolve_year_rank` + `band_issues`
（router.py:569,589），缺名次学生不再落段，响应中 `xueji_rank` 可为
`null`（此前为哨兵 9999），排序改为名次缺失置末（router.py:606）。
这是**有意的行为修正**：缺名次学生此前被错误归入薄弱段。
chat 工具 `focus_list` 仍有同型哨兵 `or 999999`（app/chat/tools.py:668，
不在本任务允许范围，见 §6）。

段位分布端点（新 `homeroom_bands`）继续维持 v2.1/F10 红线：段位阈值口径为
**年级名次**，当前范围无真实名次事实时返回 409
（api/analysis.py:1084-1141，`BandsNotComputable` 在 1130-1132），绝不把名次
阈值当分数比较；内部计数改走共享 `band_flags`（api/analysis.py:1134-1136）。

### 4.2 名次区间筛选（rank-range）——伪名次推算移除（任务书第 3 项）

**改动前**（两路径同病）：单科没有真实年级名次时，用
`year_rank = max(1, ceil(percentile × cohort_size))` 把百分位换算成伪名次
再筛区间：

- 新路径：api/analysis.py（改动前 784-792 取 cohort_size、805-807 推算，
  任务书指认的约第 807 行）、文案见改动前 837
  「单科使用年级百分位按该场主三门名次范围换算」。
- 旧路径：app/analysis/rank_metrics.py（改动前 173-177 `_percentile_to_rank`、
  改动前 153-170 `_cohort_sizes`、改动前 234,239 调用）、文案改动前 263。

**为什么必须删**：百分位是相对位置（0–1），cohort_size 取「主三门最大名次」
或单科行数（改动前 rank_metrics.py:234 的回退），两者相乘既不是学籍名次也
不是年级名次——同一学生在两条路径会得到两个互相冲突的「名次」，且与真实
名次体系不可比，属于编造数据。

**统一后**：两路径一律 `resolve_year_rank(xueji_rank, grade_rank)`（rank_metrics.py:124,150；
api/analysis.py:749）；名次不可得的学生**不进筛选结果**，`year_rank` 缺失
语义为「不可得」；`metric_note` 两路径同一文案（rank_metrics.py:176-179、
api/analysis.py:779-782）：「只按真实学籍/年级名次筛选；名次不可得的学生
不进名单（不按百分位推算名次），可靠百分位见排名频次的百分位分箱。」

**兼容边界**：

- 新路径契约字段 `HomeroomRankRangeStudent.year_rank: Optional[int]`
  （analysis_schemas.py:178-184）不变——缺失即 `null`，没有新增字段
  （analysis_schemas.py 不在本任务文件范围，契约结构冻结）。
- 旧库 `SubjectScore` 没有 grade_rank 列（models.py:73-86），因此**旧路径
  单科 rank-range 在现库结构下恒返回空名单 + 说明文案**；旧总分 rank-range
  行为不变（tests/test_api.py::test_rank_range_endpoint 验证）。已核对
  仓库内没有断言「单科伪名次筛选出人」的既有测试。
- `import math`（仅为 ceil 服务）已从 api/analysis.py 与 rank_metrics.py 移除。
- 接口影响：旧 chat 工具 `rank_range_filter_tool`（app/chat/tools.py:1274-1290
  调 `rank_metrics.rank_range_filter`，签名未变）单科指标下从「伪名次名单」
  变为空名单 + 新文案；`/api/rank-range` 路由透传同一实现（router.py:26-46）。

## 5. 百分位的百分数表示（任务书第 4 项，保留）

新路径跨学年趋势点的单科名次输出：`rank = round(percentile × 100, 2)`、
`rank_basis = "grade_percentile"`（api/analysis.py:1026-1036，任务书指认的
约 1096-1097 行改动前位置）——这是**百分位的百分数表示**（0–100）并明确
标注了依据，不是伪名次，**原样保留**；本次仅把归一化逻辑换成共享
`definitions.normalized_percentile`（definitions.py:79-86，与改动前逐行为
等价：>1 视为百分数除以 100、截断到 [0,1]）。总分行仍用真实名次
`rank_basis="school"`（api/analysis.py:1029-1030）。区分依据：该字段带
`rank_basis` 标注，消费方可识别口径；而 rank-range 的伪名次既无标注也不可
从百分位+人数可靠复原，故一个保留、一个移除。

## 6. 跨学年趋势

**统一后定义**：跨学年数据**按学年/年级分组陈列，不做跨学年连算**——
不输出任何跨学年同比/连算的进退步、波动结论；进退步与波动只在同一年度
时间线内、相邻有效考试间判定。

| 路径 | 实现 | 口径 |
|---|---|---|
| 新 `/homeroom/analysis/trends` | api/analysis.py:987-1080（响应按学年分组 `TrendYearGroup`，E03） | 结构上阻断跨年连算；单科点 rank=百分位×100（rank_basis=grade_percentile），总分点 rank=真实名次（rank_basis=school），缺失=null |
| 旧 `/api/students/{student_id}`（画像） | router.py:615-1000（`has_cross_year` 标记 + 按年级排序的趋势序列） | 陈列不连算；主三门另算班内 min-rank（router.py:784，共享 min_ranks） |
| 旧 `compute_student_trend`（chat 工具底层） | trends.py:17 | 跨学年合并时间线（person_ids），但进退步/波动判定已统一为相邻两场/极差口径（§1/§2）；「数据不足」<2 场、「无数据」0 场的语义保持 |
| 旧 `compute_cross_year_trend` | app/analysis/cross_year.py:1（仅主三门+语数英，`has_cross_year`） | 仅陈列；未被任何路由调用（仅 `app/analysis/__init__.py:4` 导出），保留不动 |

**迁移与兼容**：旧画像端点响应结构不变（旧前端依赖）；chat 工具
`student_trend` 的 ranks 合并行为不变（tests/test_chat_tools_union.py::test_student_trend_spans_both_grades
继续通过）。

## 7. 缺考处理规则（任务书第 5 项）

**规则（两路径统一）**：

1. **不转 0**：缺考 `score=NULL`（导入侧保证：ws_parse.py:17、
   workspace_models.py:358「缺考存 NULL，绝不写 0」；旧解析器对「原始分与
   等级分/百分位全空」的行直接不入库，excel_parser.py:181,201）。
2. **不进分母**：均分/名次/分箱统计跳过 NULL（contract p3 §2.3）。
3. **不残留、不伪造连续性**：每场考试的百分位/名次只来自该场自身的数据行；
   缺考那一场不产生趋势点、不落任何分箱、不落段。检查结论：
   - 新趋势 `_point`（api/analysis.py:1026-1041）：每点读自己的 fact，
     缺考 fact 无名次/百分位 → `rank=null, rank_basis=null`，不借用上一场值。
   - 新 focus（api/analysis.py:849-）：名次序列过滤 None（ranks 列表推导），
     缺考场不充当「0 分/0 名次」参与极差或进退步。
   - 新/旧频次分箱：`percentile_bin(None) → None`（definitions.py:212-222）、
     `grade_score_bin(None) → None`（definitions.py:224-231），缺考行不入箱。
   - 旧 band-trend / rank_bands / focus-list：无名次 → 不落段（§4.1，
     本次修复了旧 focus-list 的 9999 哨兵正是这类「伪造」）。
   - 旧画像班内名次：本人缺考（total_score NULL）→ `class_rank=None`
     （router.py:766-769），分母只取非 NULL 同班分（router.py:770-778）。
4. **唯一残留写法（范围外，需后续任务收编）**：`/api/students` 学生列表的
   `latest_main_rank` 在无学籍名次时回退显示年级百分位
   （router.py:1215 `ts.xueji_rank if ... else ts.grade_percentile`）。
   这是旧前端的展示回退（该字段无 rank_basis 标注，直接置 null 会让旧列表
   大面积空白），不参与任何统计，数值不与其他路径冲突（同源 grade_percentile），
   故按兼容边界保留并在此登记。

**测试佐证**：`tests/v1/test_p0_a1_definitions.py`（缺考总分趋势点
rank/score 均 null、缺考行不入百分位箱、缺名次学生不落薄弱段、缺名次不进
rank-range 名单）。

## 8. 残留差异与范围外重复（需主控知悉 / 后续任务）

1. **app/chat/tools.py（旧 chat 工具）**——前三项已由 P1-B5 收编
   （分支 `task/p1-b5-legacy`，判定逻辑改走 `app/analysis/definitions`）：
   - `focus_list` 哨兵 `or 999999`（原 :668）→ **已收编**：改走
     `resolve_year_rank` + `band_issues` + `subject_weakness_subjects`；
     缺名次学生不再误入薄弱段（`xueji_rank` 为 null、排序置末，与旧路由
     focus-list 的 P0-A1 修法一致，属有意的行为修正，返回结构与文案不变）；
   - `custom_rank_band_trend`（原 :1162-）与 band-trend 同型段位计数
     → **已收编**：名次解析走 `resolve_year_rank`，`band_trend` 段位计数走
     `band_flags`（缺名次不落段、不计数）；
   - `classify_trend`（原 :895-911）第三套进退步分类 → **已收编**：整体
     进退步方向判定改走共享 `progress_issue`（阈值参数传 eps=1e-9，即
     「可忽略变化」语义）；相邻步进计数与「持续/总体/波动持平」标签组合
     为该工具的展示语义（无共享对应概念），对外标签不变；
   - `student_trend` 经 trends.py 自动继承统一口径（§1/§2，未改动）。
   等价性由 `tests/test_chat_tools_union.py` 的「旧工具判定 = definitions
   直算」用例钉住（focus_list / band_trend / custom_rank_band_trend /
   classify_trend 四工具）。
2. **app/api/chat_tools.py（任务书禁改）**：新 chat 工具直接调用本任务改造后的
   `homeroom_*` 服务函数（如 `_tool_get_rank_range` 调 `homeroom_rank_range`），
   签名未变，自动继承统一口径与缺失语义；工具↔端点逐字段一致性测试
   （tests/v1/test_p6_tools.py::test_a01_rank_range_matches_endpoint）通过。
3. **分箱展示文案**：旧路径名次档位 label 为「1-40名次数」、新路径为
   「1–40名」（definitions.rank_bin_label，definitions.py:205-210），分箱
   边界与 bin key 完全一致，仅文案代次不同——两代前端各按其契约消费。
4. **偏科文案**：旧路径 `严重偏科(物理)`（半角括号）、新路径
   `严重偏科（物理）`（全角括号），既有展示差异，保持各自前端契约。

## 9. 测试

- 新增 `backend/tests/v1/test_p0_a1_definitions.py`（16 个用例）：共享定义单测 +
  端点级名次缺失/缺考语义回归 + 旧路径哨兵名次移除回归（合成姓名
  秦甲/秦乙/秦丙/秦丁/秦戊/秦庚/秦辛/秦壬）。
- 调整 `backend/tests/test_api.py::test_grade_score_frequency_bins_are_exact_scores`：
  等级分箱常量改从共享 `app.analysis.definitions` 导入（原 rank_metrics 常量
  已收敛至共享模块）。
- 全量门禁以提交时的实际运行结果为准。
