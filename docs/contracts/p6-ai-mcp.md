# P6 接口契约：AI 对话 / 只读 MCP（v2，2026-09-12）

v2 依评审意见 Q01–Q03/Q09 修订；v1 其余条款继续有效。

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
- 旧 /api/chat 与 mcp_server.py（读旧表）保留不动，P7 前不删。

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
- **Q09 tools/list 暴露**：MCP tools/list 必须发布 ws 四工具（名称/描述/输入 schema，含 mode
  必填与范围参数说明）；更新 tests/test_mcp.py 冻结断言；发现→调用全流程客户端测试。

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

只读工具清单（首批，全部薄封装 /api/v1 service）：

| 工具 | 可用域 | 数据源 |
|---|---|---|
| search_students(q) | 两域 | students list service（域内成员） |
| get_student_profile(person_id) | 两域 | 画像 service（域内学科白名单） |
| get_exam_list() | 两域 | /shared/exams service |
| get_exam_stats(exam_name) | 两域 | analysis stats（homeroom 全科/teaching 单科两策略） |
| get_scores_table(exam_name, subject?) | 两域 | scores service（经 gated 投影门） |
| get_homework_summary(from, to) | 两域 | homework dashboard service |
| get_homework_student(person_id) | 两域 | 学生事件流 service |
| get_homework_correlation(exam_name, subject?, homework_type?, total_type?) | 两域 | correlation service（p5 §4；homeroom 传 subject，teaching 钉会话学科；r=null 不可计算态；描述统计不构成因果） |
| get_class_comparison(exam_name) | teaching | class-compare service |
| get_class_averages(exam_name) | homeroom | 官方全年级班级均分表（workspace_class_average 导入事实，ADR-025，非本班成绩反推） |
| get_weekly_focus() | homeroom | 本周关注四维信号加权（连续缺交/缺交激增/考试关注/谈话待办；天然当前） |
| get_exam_focus(exam_name) | homeroom | 单场考试重点关注（进退步/波动/偏科/临界） |
| get_student_trends(person_id) | homeroom | 跨学年分段趋势（响应按学年分组） |
| get_rank_metrics() | homeroom | 可用名次指标发现（frequency 模式） |
| get_rank_frequency(metric, exam_names) | homeroom | 名次/百分位/等第频次统计（exam_names 逗号分隔多场考试） |
| get_rank_range(exam_name, metric, rank_min?, rank_max?) | homeroom | 年级名次区间筛选名单 |
| get_rank_distribution(exam_name) | homeroom | 单场考试年级名次分布（每 40 名一档） |
| get_score_bands(exam_name, subject, metric?) | homeroom | 高分/临界/薄弱段位名单（阈值口径为年级名次；无名次数据 409 不做分数镜像） |
| get_homework_warnings(subject?, min_missing?, min_streak?, from?, to?) | 两域 | 作业缺交/连续缺交/负面/忘带预警（默认区间=当前学期） |
| get_homework_assignments(subject?, homework_type?, from?, to?, limit?) | 两域 | 作业批次分页列表（含 assignment_id） |
| get_homework_assignment_detail(assignment_id) | 两域 | 单批次逐人明细（归属校验 service 侧） |
| get_student_notes(person_id, 学年/学期参数) | 两域 | 成长/谈话档案（仅教师手动记录；学期参数按 ws_homework_semester 起止日期在工具层过滤档案 date） |
| get_academic_years() | 两域 | 学年+学期目录（is_current/year_offset/term_offset 标注；学期源=ws_homework_semester，挂不上学年目录的学期进顶层 unmatched_semesters；时间语义锚点） |
| get_exam_students(exam_name, subject?) | homeroom | 单场逐人分科+总分矩阵（宽表含冲突注记；与 get_scores_table 分工） |

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
- homeroom 跨年：class_id 恒钉快照行政班（行政班跨年延续=同一班行，查教师
  本人班级历史；该学年无延续班 → 服务端可读拒绝）。teaching 跨年：绝不把
  本学年教学班 id 传给其他学年——改由服务端显式解析**该学年同任教学科**
  教学班集合（subject 钉快照学科，绝不扩大到非任教学科；无班 → 可读错误）。

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
- MCP 工具清单 = §2 注册表的稳定子集（首版：search_students / get_student_profile /
  get_exam_stats / get_scores_table）；写操作工具一律不注册。
- scope 快照机制同聊天（MCP 会话同样存 chat_session，type='mcp'）。

## 5. 验收映射

A01（页面/API/AI/MCP 同查询一致；只读工具不能写入/扩大范围）→ 四路径同源测试 + 工具越界测试；
A02（切换工作台/撤销关联后旧上下文失效）→ 快照漂移 409 测试（link cancel 后同一 session 发消息）；
跨学年时间语义（year_offset/term_offset 二选一、缺省快照学年、teaching 跨年不扩学科）→
test_p6_time_semantics.py 专项 + 既有工具同源测试。

## 6. 前端（P6-FE 波次）

- ChatDrawer 改造：按当前工作台 mode 创建会话（顶栏切换器切换即提示"新会话/继续旧会话"二选一，
  旧会话属另一域时明确标注）；流式渲染沿用；工具调用卡片（ToolCallCard）展示工具名与摘要。
- 移动端抽屉可用；SSE 中断重连提示。
