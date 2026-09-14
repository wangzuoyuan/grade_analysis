# 相似功能取舍表

H = 班主任版 dabb45c；T = 教学版 f9ab60f。以下是源码审查后的选型，须通过合并版验收才能认定实现正确。
证据路径均相对各自仓库根目录，固定提交链接见 [源码证据](07-source-review.md)。

| 功能 | 采用方案 | 源码依据与理由 | 合并时必须处理 |
|---|---|---|---|
| 工程基础 | H 后端 + T 前端 | 同为 FastAPI/SQLAlchemy/SQLite、Next 14/React 18；H 保留全科和学生管理 | 保留来源和许可证，逐模块迁移；不继承两份同名路由 |
| 布局/主题 | T | `frontend/src/components/layout/*`、`globals.css`、`tailwind.config.js` 已有完整浅蓝主题 | 班主任语义重写；不复制 T 的班级范围到班主任页面 |
| Excel 导入 | H 保存全科与总分 + T 教学班标签解析、事务同步 | H `ingest/router.py` 有撞号防护；T `excel_parser.py` 支持 class_label，上传事务覆盖成员同步 | 统一 preview/commit、整批冲突处理；H 分区保留全科；T 独立导入保留单科过滤；关联班通过投影共享当前学科，不导入其他教学班数据到 H |
| 考试去重 | 新增公共服务 | 两库独立 exam/upload ID 无法直接拼接，不能依赖考试名称相同 | 先按分区保存，仅关联班对明确同一考试做映射和共享；跨区同名不自动合并 |
| 全科/总分/偏科 | H | `analysis/router.py`、`rank_metrics.py`、`chat/tools.py` 支持总分类型和多学科画像 | 提取实际 router/tools 中活跃逻辑；不能误迁早期未接线的 trends 等模块 |
| 单科/等级分/教学班排名 | T | `analysis/single_subject_metrics.py`、`exam_context.py`、相关测试 | 限定合法成员；统一同分排名、有效成绩、样本数，不重新创造第三套算法 |
| 班级对比 | 分别保留 | H 行政班全科，T 所教教学班单科且可标估算 | 区分行政班/教学班和官方/估算；不得用子集样本重算后声称年级排名 |
| 段位/频次/趋势 | 共用指标定义，保留两策略 | 两版 `analysis/config.py`、`rank_metrics.py`，T 有单科显式 metric 校验 | 阈值按工作台/学科存；API、导出和 AI 共用口径 |
| 人的主档案、改号、合并、删除 | H 主导 | `student_management/service.py`、`db/sid_space.py`、`StudentChangeLog`，有专门测试 | 扩大事务覆盖 teaching membership、作业批次及关联快照；不能仅修改 H 原表 |
| 换届、名册导入撤销 | H | `rollover/service.py`、`RosterImportBatch`、`RolloverConfirmBatch`、`test_roster_undo.py` | 跨源 ID 映射后旧撤销快照须验证，不能原封不动执行旧 JSON |
| 教学班/走班/成员名单 | T | `teaching/service.py`、`TeachingClassMember` 支持字符串标签、成员来源 | 名单关联域内 person/alias，关联班才跨域映射；增加学年有效期，保留手工变更来源 |
| 学生画像 | H 全科 + T 单科，域内 person 聚合，关联班经显式 person link 映射 | H 支持 ImportedHistory；T 无成绩合法成员仍有画像、同一人多个学号展开 | 不把无成绩成员隐藏；旧姓名为考试快照；历史手工分数只展示不进排名 |
| 作业快速输入、已交/缺交、评价 | T 为输入体验起点 | `homework/parser.py`、`HomeworkEntryPreview.tsx`、router 的 smart input | subject/作业种类拆分；关联班按学科共享；预览错误零写入；日期+班+科+批次+人幂等 |
| “全交”台账和预警时间轴 | H 的收交事件思想 + T 的逐人状态 | H `HomeworkCollection` 记录全交日；T 展开个人已交并统计评价 | 新建可撤销批次及应交成员快照，不只复制任一表；同一天多次作业不合并 |
| 作业统计与历史跟人 | T `_PersonScope` 为起点 | T 最新 `homework/service.py` 已按身份读时展开旧学号 | 加上事件发生时班级/学科范围，身份展开不扩大教学权限，跨班去重 |
| 作业成绩相关性 | 公共 Pearson + 两指标策略 | H 全科/单科年级口径，T 当前科教学班内排名 | 报样本数、排名方向、时间窗；零方差/少样本返回不可计算；不表述成因果 |
| 学期管理 | 公共服务重整 | 两版均有 HomeworkSemester、自动推算和人工配置 | 自动模式始终可选；重复编辑明确 4xx；保留原配置，不固定旧日期 |
| 谈话/成长档案 | 共用服务、按域隔离的存储视图 | 两版 `notes/router.py` 与 StudentNotes | 增加来源、适用工作台；默认迁入各自来源范围，共享需显式选择 |
| 本周关注、打印 | 公共卡片/打印框架 + 两聚合策略 | 两版 weekly_focus、`student/[id]/report` | 班主任有偏科，教学仅单科；避免共用标题却返回不同口径 |
| AI 对话 | 合并注册机制，策略工具分开 | 两版 TOOL_REGISTRY；T 有 scope hardening 和工具投影测试 | 工具不能自行扩展范围，跨班分析也只在已配置合法班内 |
| MCP | 复用认证、只读注册表结构 | 两版 `mcp_server.py`、`test_mcp.py` | mode 必须显式；两个兼容入口可过渡，但不依赖连接名称/工具重命名隔离 |
| 登录/备份/启动器 | 公共整合，两版逐项比较 | `auth.py`、`backup/router.py`、`run.py`、部署文件 | 单用户并非多用户权限系统；统一数据目录、Cookie、恢复版本和备份覆盖范围 |
| CI/容器发布 | T workflow 框架 + 两版测试 | T `.github/workflows/ci.yml`、`docker.yml`，H 有更细交互测试 | 固定依赖后跑两域；Compose 使用合并版独立项目名；NAS 实际端口预检 |

## 必须丢弃的旧假设

1. 全应用并库不是全应用数据互通。班主任数据分区保留全科，教学分区保留单科；只有显式关联班允许受控共享。
2. `ClassRoster.student_id` 不能永久代表一个人，也不能同时承担多个时期的班级成员关系。
3. `grade=1/2/3` 不等于入学届别；命名空间 `G1::` / `g1-` 仅是旧 ID 的兼容形式。
4. 作业 `subject` 在两个来源语义不同；绝不可只按列名映射。
5. 浏览器记住的当前班和当前 mode 不是服务端数据权限；同一个教师身份也不能绕过工作台隔离。
6. 旧代码注释和 AGENTS 中端点清单可能落后于代码；以实际函数、调用链和测试为准。

## 选择标准

按业务适配、数据完整性、作用域可靠性、可撤销性和可验证性比较；不以版本号、代码新旧或行数作唯一依据。
发现候选实现失败时先记录复现与影响，再替换局部逻辑并更新决策。原版已有测试是起点，不是合并后免检证明。
