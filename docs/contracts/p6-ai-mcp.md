# P6 接口契约：AI 对话 / 只读 MCP（v2，2026-09-12）

v2 依评审意见 Q01–Q03/Q09 修订；v1 其余条款继续有效。

**2026-10-23 排名区间按指标类型分口径（v2.2）**：`get_rank_range` 与其封装的
`/api/v1/homeroom/analysis/exams/{exam}/rank-range` 同步——指标清单按年级扩全
（高一补九门总分、高二/三 range 补六选科等级分指标），rank_min/rank_max 单位随
metric_kind（总分=名次、语数英/高一全科=年级前% 0–100、选考=等级分 40–70），
响应 students[] 增 `metric_value`；工具描述与参数说明同步（详见
p3-imports-analysis.md §2.1 v2.7）。

**2026-10-23 AI 覆盖补齐（v2.1）**：注册表 25→28——新增 `get_rank_bands`
（homeroom，读随考试导入的名次段表）、`get_teaching_score_bands`（teaching，
任教学科分数段分析）、`get_homework_student_subjects`（homeroom，单人全科
作业×成绩总览）三个薄封装，并补登既有工具 `get_diagnosis_summary`（P1-B4，
此前漏出表格）；口径描述修正——`get_class_averages` 写明「选科均分 0=无人选、
不得计入平均/比较/当最低分」与「total_ranks=班型内排名」，`get_exam_students`
删跨域冲突注记（Q10 已退役）改为百分位宽表口径；系统提示词规则 4 扩为
「数字口径」四子项（见 §2 表后注）；`get_diagnosis_summary /
get_homework_assignments / get_homework_assignment_detail` 的 input_schema 补
`year_offset`（§2.1 时间语义）；考试存在性门 `_exam_exists_in_domain`（homeroom）
对齐 exam_events 可见性——班级均分表/名次段表导入也算考试存在，纯导入考试的
精确名解析与走该门的读端点不再误判「考试不存在」（原 404 改判 200 合法空态）；旧 `POST /api/chat` 与 `GET /api/chat/config`
HTTP 端点下线（404，主守卫 tests/test_mcp.py，冒烟 tests/v1/test_p6_ai_coverage.py）。

依 `docs/planning/03-architecture.md` §6、`05-acceptance.md`（A01/A02）与
`docs/baseline/api-diff.md`（B8/E1）起草；实现不得自行偏离。基础约定沿用 p1-api.md §0。

## 0. 核心红线

- 聊天会话绑定**不可变 scope 快照**（mode、data_domain、academic_year_id、class_ids、subject、
  link_id、link_version、member_person_ids、as_of）；**每次请求服务端重新解析并校验**，绝不信任
  客户端提交的成员列表/学科。快照与当前库不一致（link 撤销/版本变/成员漂移）→ 409
  link_version_conflict，旧上下文结果不得继续作答（A02）。
- 工具全部**只读**：无法经工具参数触发表写入；工具投影按当前会话域裁剪（teaching 会话看不到
  全科/总分工具，homeroom 会话看不到单科教学班对比工具之外的 teaching 专属工具）。
- 同一查询经页面 / API / AI 工具 / MCP 四路径必须同源同果：全部走 /api/v1 的同一 service 层
  （A01），工具不得另写查询逻辑绕过投影门。
- 旧 `/api/chat` HTTP 端点（POST /api/chat、GET /api/chat/config）已于 2026-10-23 下线
  （404，读已冻结 legacy 旧库、口径过时）；`app/chat/tools.py`/`config.py` 模块与
  mcp_server.py 的旧工具 MCP 兼容路径（读旧表）保留至 P7。新版 AI 聊天唯一入口
  = `/api/v1/chat`（本契约）+ MCP（§4）。

## 0.1 v2 增补（Q01/Q02/Q03/Q09）

- **Q01 工具范围收窄**：工具执行必须使用**会话快照冻结的完整 WorkspaceContext**
  （mode/class_ids/subject/member_person_ids），单班会话绝不扩大为并集；service 层
  （_queries/readable_facts 等）按快照 class_ids 显式传参，聚合前限制班级，而非取回后裁剪。
  单班/并集/跨班（T6 会话不得见 T8 学生）逐一测试。
- **Q02 教学会话绑定关联版本**：快照冻结**所有可见关联**的 `{link_id, version, status}` 及
  影响共享的成员映射（teaching 上下文不再只存空 link_id）；任一关联取消/版本变/历史授权收紧/
  配对变更 → 漂移 409。**多轮工具执行期间**每轮工具调用前重验快照一致性，失效即中止并
  返回范围失效帧（不继续用旧上下文执行）。
- **Q03 服务端会话历史**：新增 `ChatMessage` 表（迁移 0007：session_id FK、role
  (user/assistant)、content、tool_events JSON、created_at）；messages 端点组装
  `历史（合理截断，默认最近 20 条）+ 本次输入` 请求模型；GET /chat/sessions/{id}/messages
  返回历史（刷新恢复不依赖浏览器）；范围漂移后历史禁止复用（409 语义不变，历史保留只读）。
- **Q09 tools/list 暴露**：MCP tools/list 必须发布 §2 注册表全量只读工具（2026-10-23
  起为 28 个；首版曾为四工具子集）；更新 tests/test_mcp.py 冻结断言；发现→调用全流程客户端测试。

## 1. 会话与鉴权

- `POST /api/v1/chat/sessions` `{mode, academic_year_id?, class_id?|teaching_class_id?, subject?}` →
  `{session_id, scope}`（服务端解析快照入库，新表 `chat_session`：id, scope_json, created_at,
  status('open'|'closed'), 见迁移编号顺延）。
- `GET /api/v1/chat/sessions/{id}` → 快照（核对用，不泄露成员外的数据）。
- `POST /api/v1/chat/sessions/{id}/close`：显式关闭（撤销关联后的清理路径）。
- SSE：`POST /api/v1/chat/sessions/{id}/messages` `{content}` → text/event-stream（沿用 H 版
  fetch+getReader 帧；流前先做快照校验，失败 409 JSON 而非流）。
- 流内 error 帧可带可选 `code` 字段（前端优先认 code，旧文字匹配仅兜底）：
  `scope_drift`（会话范围失效）、`key_not_configured`（模型 Key 未配置）、
  `provider_failed`（模型调用失败/重试耗尽）、`tool_rounds_exceeded`（工具轮次超限）；
  message 文案不变，其余帧格式不变。

## 2. 工具注册（公共注册表 + 域投影）

只读工具清单（28 个，全部薄封装 /api/v1 service）：

| 工具 | 可用域 | 数据源 |
|---|---|---|
| search_students(q) | 两域 | students list service（域内成员） |
| get_student_profile(person_id) | 两域 | 画像 service（域内学科白名单） |
| get_exam_list() | 两域 | /shared/exams service |
| get_exam_stats(exam_name) | 两域 | analysis stats（homeroom 全科/teaching 单科两策略） |
| get_scores_table(exam_name, subject?) | 两域 | scores service（经 gated 投影门） |
| get_homework_summary(from, to) | 两域 | homework dashboard service |
| get_homework_student(person_id) | 两域 | 学生事件流 service |
| get_homework_correlation(exam_name, person_id?, subject?, homework_type?, total_type?) | 两域 | 指定学生必须传 person_id，薄封装 student_homework_analysis，与逐人页面同源；成绩口径按学科自适应：高一全科与高二/三语数英用年级百分位、高二/三六选科用等级分（student-homework-v2）。未传 person_id 兼容旧班级相关，不得用班级系数推断个人 |
| get_class_comparison(exam_name) | teaching | class-compare service |
| get_class_averages(exam_name) | homeroom | 官方全年级班级均分表（workspace_class_average 导入事实，ADR-025，非本班成绩反推）；total_ranks=班型内排名（平行班/实验班分组各排，非全年级名次）；选科均分 0=该班无人选该科，不是真实分数，不得计入平均/比较/当最低分 |
| get_weekly_focus() | homeroom | 本周关注四维信号加权（连续缺交/缺交激增/考试关注/谈话待办；天然当前） |
| get_exam_focus(exam_name) | homeroom | 单场考试重点关注（进退步/波动/偏科/临界） |
| get_student_trends(person_id) | homeroom | 跨学年分段趋势（响应按学年分组） |
| get_rank_metrics() | homeroom | 可用名次指标发现（frequency 模式） |
| get_rank_frequency(metric, exam_names) | homeroom | 名次/百分位/等第频次统计（exam_names 逗号分隔多场考试） |
| get_rank_range(exam_name, metric, rank_min?, rank_max?) | homeroom | 区间筛选名单（单位随指标：总分=名次、语数英/高一全科=年级前%（越小越靠前）、选考=等级分 40–70；响应含 metric_value；v2.7 2026-10-23） |
| get_rank_distribution(exam_name) | homeroom | 单场考试年级名次分布（每 40 名一档） |
| get_score_bands(exam_name, subject, metric?) | homeroom | 高分/临界/薄弱段位名单（阈值口径为年级名次；无名次数据 409 不做分数镜像） |
| get_homework_warnings(subject?, min_missing?, min_streak?, from?, to?) | 两域 | 作业缺交/连续缺交/负面/忘带预警（默认区间=当前学期） |
| get_homework_assignments(subject?, homework_type?, from?, to?, limit?) | 两域 | 作业批次分页列表（含 assignment_id；支持 year_offset/academic_year_id 跨学年） |
| get_homework_assignment_detail(assignment_id) | 两域 | 单批次逐人明细（归属校验 service 侧；支持 year_offset/academic_year_id，批次 id 属特定学年，指错学年自然 404 越界文本） |
| get_student_notes(person_id, 学年/学期参数) | 两域 | 成长/谈话档案（仅教师手动记录；学期参数按 ws_homework_semester 起止日期在工具层过滤档案 date） |
| get_academic_years() | 两域 | 学年+学期目录（is_current/year_offset/term_offset 标注；学期源=ws_homework_semester，挂不上学年目录的学期进顶层 unmatched_semesters；时间语义锚点） |
| get_exam_students(exam_name, subject?) | homeroom | 单场逐人分科+总分矩阵（宽表含选科等级分、年级百分位、学籍与各总分口径名次；跨域冲突注记已随 Q10 于 2026-10-20 退役；与 get_scores_table 分工） |
| get_diagnosis_summary(person_id?) | 两域 | 诊断学情摘要（P1-B4，口径版本 p1-v1）：传 person_id 返回该生六类特征（当前水平/趋势/稳定性/偏科/作业行为/教师关注）+ 类型判定（主类型/次标签/证据）；缺省=班级每生类型汇总；与学生页诊断卡、班主任首页类型分布卡同一 service |
| get_rank_bands(exam_name, total_type?) | homeroom | 全年级名次段表（读随考试导入的《总分名次段表》，不从学生名次现算；available_total_types 列可选口径；与 get_rank_distribution 现算口径分工） |
| get_teaching_score_bands(exam_name, band_width?) | teaching | 任教学科分数段分析（原始分 5/10/20 分一段、左闭右开）：每教学班一列+合并列，含本段/累计与人数/最值/均分/σ；仅本人教学班口径，不含年级或官方列 |
| get_homework_student_subjects(person_id, window_days?) | homeroom | 单个学生逐学科作业×成绩总览（全科并列）：每科按考试次序的时间线（口径按学科自适应：高一全科与高二/三语数英=年级百分位换算、高二/三六选科=等级分）+ 考前窗口提交率；无成绩事实学科自动跳过；window_days 限 14/30 |

- 每工具声明 `domains: [...]`；会话构建时按 scope.data_domain 投影注册表；未知/越权工具调用 →
  工具层直接返回错误文本（模型不可见异常栈）。
- 工具入参中的 person_id/exam_name 等一律再过作用域校验（越界 → 该工具返回"不在当前范围"）。
- **exam_name 模糊匹配**（工具层 `_resolve_exam_name` 统一解析，全部考试名工具共用；
  先解析学年参数、候选限定在所选学年，与 get_exam_list 完全同源）：精确命中直接用
  （既有行为不变，响应无注记）；无精确但**子串唯一命中**（用户片段是考试名的子串）→
  自动解析为该场，并在有数据返回的工具响应顶层 `exam_resolved` 注明全名；**多场子串命中** →
  `invalid_scope_param` 候选清单（每条考试名+考试日期、按日期降序、最多 10 条，注明选一场后
  重试）；**零命中** → 不在范围可读错误并列出该范围可用考试（最多 10 条）。get_rank_frequency
  的 `exam_names`（逗号分隔多场）逐项解析，任一项多命中/零命中报错并注明「第 N 项『X』」，
  含模糊项时响应顶层 `exam_resolved` 注记逐项解析结果（逗号连接，与入参同序）。
- 系统提示按域生成（homeroom 全科班主任视角 / teaching 单科教师视角），不含成员明单（按需查）；
  **规则 4「数字口径」**（2026-10-23 扩为四子项；2026-10-24 c 子项改为学籍选列口径，
  chat.py build_system_prompt）：
  a) 缺考为 null 绝不转 0，排名只在本班人群内解释；b) 年级百分位（0–1）=「年级前
  百分之几」，越小越靠前，需要越大越好的口径用 100 − 百分位×100 换算；c) 名次口径
  （2026-10-24 起）：学籍=1（闵中学籍）取学籍排名（缺时用年级排名补）、学籍非1
  （借读类）只取年级排名、学籍未采集取学籍排名——排名分布/排名频次/名次区间/段位
  分布按此名次统计**全部学生**，回答名次类数字须带口径；d) 班级均分表选科均分
  0=无人选（不计入平均/比较/最低分）、total_ranks=班型内排名（非全年级名次）。
  相关 metric_note（rank_frequency/rank_range/rank_distribution）同步带学籍选列文案。
  规则含第 7 条「自主分析」（恒定注入）：没有专门工具的分析问题（进步/退步对比、排序、分布、
  两场考试对比等）先用 get_scores_table / get_exam_students / get_homework_assignments 等
  拿全量真实数据再自行计算，**不以"没有对应工具"为由拒绝回答**；计算纪律：缺考 null 不当 0
  （不计入分母与均值），数字多时先整理成表格逐步算并给出关键中间结果；用户没说清哪场考试时
  先用 get_exam_list（配合考试名模糊匹配）确认最近一场或列出让用户挑，不瞎猜。

### 2.1 跨学年/学期参数（时间语义）

- 除 get_academic_years 外全部查数工具的 input_schema 追加可选
  `academic_year_id`（int，直传）与 `year_offset`（int，0=本学年，-1=上学年，
  -2=上上学年），**二选一**（同传 → 422 invalid_scope_param 可读错误）；
  都不传 = 快照学年（与既有行为完全兼容）。get_student_notes 另收
  `term_id`/`term_offset`（0=本学期，-1=上一学期；全学期按 start_date 排平，
  跨学年自动落到上一学年第二学期；**假期条目（名称含 暑假/寒假/假期）不占
  偏移编号**——目录照列并标 `vacation: true`/`term_offset: null`，term_id 仍
  可直传直达，避免「上学期」解析到假期真空区间）。
- 解析在服务端唯一实现（chat_tools `_resolve_year_arg`/`_resolve_term_arg`）：
  学年目录经 shared list service；**学期目录源 = ws_homework_semester**
  （学期设置页/作业看板同一事实源；P1 Term 表真实部署无数据且无维护入口，
  不再作为学期来源），按 start_date 排序、以 is_current=1 的学期（无则含
  快照 as_of 的学期，再无则最新学期）为锚换算；`term_id` 即作业学期 id。
  作业学期挂的 academic_year_id 对不上学年目录时，`get_academic_years`
  顶层输出 `unmatched_semesters`（绝不静默丢弃）。get_student_notes 的
  学期参数不再透传 P1 term_id，改为按学期起止日期在工具层过滤档案 date。
  越界返回可读错误并列出可用学年/学期名称。`get_academic_years` 输出带
  is_current/偏移标注的目录（学期名即用户维护名称，如「2026学年第一学期」），
  供模型把「上学年/上学期」等相对时间翻译成参数、并取学期起止日期换算
  作业区间 from/to；系统提示含时间锚点（当前日期/学年/学期名/当前年级——
  年级仅班主任会话，按快照行政班 grade 换算高一/高二/高三，查不到则不写）
  与翻译规则：学年命名约定（「2026学年」= 2026 年 9 月开学的 2026-2027 学年）、
  相对偏移（year_offset/term_offset）、年级→学年换算（当前是高二：高一=
  year_offset -1、高二=0、高三=+1；「高一第二学期」先定学年再按学期名匹配）、
  学期名按名称直接匹配。
- homeroom 跨年（fix11）：**换届后新学年的行政班是新行（id 不同于旧学年），
  绝不把快照班 id 配其他学年传给核心解析**。服务端 `_homeroom_year_class` 按
  教师绑定班号（Teacher.target_class_high{1,2,3} + active_grade——班号是「本班」
  跨学年的稳定身份）解析目标学年行政班，回退序与 core.context 同源：
  ①（学年, 当届年级, 绑定班号）精确 → ②换届后回看：该学年内班号命中任一
  绑定班号的唯一实例 → ③未换届延续（最近更早学年沿绑定对的班，零写入）。
  解析不出（0 个）/歧义（多个且无映射可辨）→ 可读中文错误（错误类同核心解析
  的 workspace_not_configured；歧义列出候选班），绝不猜、绝不退化为全年级。
  该学年无考试数据仍照常返回空清单（合法空态）。
- teaching 跨年：绝不把本学年教学班 id 传给其他学年——改由服务端显式解析
  **该学年同任教学科**教学班集合（subject 钉快照学科，绝不扩大到非任教学科；
  无班 → 可读错误）。
- 跨年成员口径（fix11）：search_students 显式跨年查询时，成员收敛改用
  **目标学年名册**（同一服务端解析器算出的 member_person_ids——本人班级/
  本人任教学科班在该学年的成员；范围限制不放宽、绝不扩到全年级）；
  本学年查询行为不变（仍收敛快照成员）。get_diagnosis_summary 跨年按目标
  学年重建同源快照再调诊断层（`_check_year` 要求 scope 学年==目标学年），
  学生不在该学年名册 → 可读说明；快照学年行为零变化。

## 3. 模型与配置

- 沿用既有 chat/config 探测（环境变量供 key，不写库不进报告）；无 key → 会话创建 409
  workspace_not_configured（detail 引导配置），不半开。
- 回答长度上限 `CHAT_MAX_TOKENS`（缺省 16384，仅 Anthropic 分支作为 max_tokens 传入——
  该接口必填；OpenAI 分支不传 max_tokens、不设上限）。命中上限被截断时，text 帧末尾
  追加「（回答达到长度上限被截断，可继续追问让我接着说。）」提示。
- 流式实现沿用 H 版 SSE 帧格式；生成中工作台切换/关联撤销由前端中止流 + 后端下一请求 409 兜底。

## 4. MCP（只读，A01 一致性）

- `MCP_ENABLED` 挂载沿用现状；新增模式参数：每个 MCP 工具调用必传 `mode`（缺省 422）；服务端
  以 mode + 会话 scope 重新解析，再走 §2 同一注册表（即 MCP 与 AI 聊天同工具同结果）。
- MCP 工具清单 = §2 注册表全量只读工具（28 个，2026-10-23 起与 AI 聊天同目录；
  首版曾为四工具子集）；写操作工具一律不注册。
- scope 快照机制同聊天（MCP 会话同样存 chat_session，type='mcp'）。

## 5. 验收映射

A01（页面/API/AI/MCP 同查询一致；只读工具不能写入/扩大范围）→ 四路径同源测试 + 工具越界测试；
A02（切换工作台/撤销关联后旧上下文失效）→ 快照漂移 409 测试（link cancel 后同一 session 发消息）；
跨学年时间语义（year_offset/term_offset 二选一、缺省快照学年、teaching 跨年不扩学科）→
test_p6_time_semantics.py 专项 + 既有工具同源测试；
homeroom 跨年班级服务端解析（换届后新班 id 不再配旧学年、跨年诊断不 422、跨年搜索
成员口径、无班/歧义可读错误）→ test_fix11_chat_cross_year.py 专项；
2026-10-23 覆盖补齐（口径文案四锚点、三新工具注册与域投影、A01 同源、旧端点下线冒烟）
→ tests/v1/test_p6_ai_coverage.py 专项。

## 6. 前端（P6-FE 波次）

- ChatDrawer 改造：按当前工作台 mode 创建会话（顶栏切换器切换即提示"新会话/继续旧会话"二选一，
  旧会话属另一域时明确标注）；流式渲染沿用；工具调用卡片（ToolCallCard）展示工具名与摘要。
- 移动端抽屉可用；SSE 中断重连提示。
