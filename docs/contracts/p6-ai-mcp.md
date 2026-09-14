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
| get_class_comparison(exam_name) | teaching | class-compare service |

- 每工具声明 `domains: [...]`；会话构建时按 scope.data_domain 投影注册表；未知/越权工具调用 →
  工具层直接返回错误文本（模型不可见异常栈）。
- 工具入参中的 person_id/exam_name 等一律再过作用域校验（越界 → 该工具返回"不在当前范围"）。
- 系统提示按域生成（homeroom 全科班主任视角 / teaching 单科教师视角），不含成员明单（按需查）。

## 3. 模型与配置

- 沿用既有 chat/config 探测（环境变量供 key，不写库不进报告）；无 key → 会话创建 409
  workspace_not_configured（detail 引导配置），不半开。
- 流式实现沿用 H 版 SSE 帧格式；生成中工作台切换/关联撤销由前端中止流 + 后端下一请求 409 兜底。

## 4. MCP（只读，A01 一致性）

- `MCP_ENABLED` 挂载沿用现状；新增模式参数：每个 MCP 工具调用必传 `mode`（缺省 422）；服务端
  以 mode + 会话 scope 重新解析，再走 §2 同一注册表（即 MCP 与 AI 聊天同工具同结果）。
- MCP 工具清单 = §2 注册表的稳定子集（首版：search_students / get_student_profile /
  get_exam_stats / get_scores_table）；写操作工具一律不注册。
- scope 快照机制同聊天（MCP 会话同样存 chat_session，type='mcp'）。

## 5. 验收映射

A01（页面/API/AI/MCP 同查询一致；只读工具不能写入/扩大范围）→ 四路径同源测试 + 工具越界测试；
A02（切换工作台/撤销关联后旧上下文失效）→ 快照漂移 409 测试（link cancel 后同一 session 发消息）。

## 6. 前端（P6-FE 波次）

- ChatDrawer 改造：按当前工作台 mode 创建会话（顶栏切换器切换即提示"新会话/继续旧会话"二选一，
  旧会话属另一域时明确标注）；流式渲染沿用；工具调用卡片（ToolCallCard）展示工具名与摘要。
- 移动端抽屉可用；SSE 中断重连提示。
