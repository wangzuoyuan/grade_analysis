# P3 教研统计接口契约（主控定稿，Wave D 唯一任务依据）

承接 p1/p2 契约通用规则（共享定义、作用域隔离、缺失纪律、符号约定、合成测试）。版本号 `p3-v1`。
产品目标：回答「此前关注的学生后来怎样」——带时间戳汇总诊断、行动与复查数据；报告前后变化、适用人群及局限；**无对照设计，绝不输出因果效果结论**。

## 1. 文件归属（单任务 D1）

独占：`backend/app/diagnosis/research.py`、`research_router.py`、`app/api/__init__.py`（仅追加 include）、`backend/tests/v2/test_p3_d1_research.py`；前端 `frontend/src/app/homeroom/research/page.tsx`、`frontend/src/components/research/*`、侧栏导航组件（定位后独占该文件、只加一个入口）、`frontend/tests/p3-research.test.mjs`。
禁止修改：其余 diagnosis 既有文件（只读消费）、chat_tools.py、mcp_server.py、analysis/。

## 2. 端点

1. `GET /api/v1/{域}/diagnosis/research/cohorts?academic_year_id=`——可用队列清单：
   - `interventions`：有过干预建档的学生（按 subject_scope/problem 分组，含起止、status 分布）；
   - `type:<类型名>@<exam_name>`：该场考试按 B2 类型回算的队列。2026-09-29 升级：B1 `class_features(anchor_exam=)` 已支持考试锚点（时间线截断至该场、作业/关注窗口锚定该场日期），membership_basis=exam_anchor；考试日期仅月精度/缺失时回退当前时点口径（membership_basis=current_time_point + limitation 标注）。成员为当前名册。
2. `GET /api/v1/{域}/diagnosis/research/outcome?cohort=&from_exam=&to_exam=&academic_year_id=&metric=`
   - 成员固定可比集合（两场均有效）；每人变化复用 B3 `student_change_decomposition`（禁止另算）；
   - 聚合：`improved_n / flat_n / declined_n`（阈值=TREND_DIRECTION_MIN_CHANGE 同口径）、`median_change`、`excluded`（缺考/无数据/已离班）；
   - 干预队列另附 C4 `review_contrast` 摘要（ready 条数/pending 条数）；
   - 响应必含：`rules_version`（"p1-v1/p2-v1/p3-v1" 组合标注）、`limitations`（固定文案：回顾性队列、无对照组，前后变化不构成干预效果因果结论）、各计数。

## 3. 前端

教研统计页：队列选择（类型/干预）+ 两场考试选择 + 指标选择 → 聚合卡（三段计数/中位变化/排除数）+ 学生明细表（person_id+变化值，点击走既有学生页）+ 醒目局限声明。缺失态如实显示。

## 4. 交付与门禁

全量 backend/tests + `npm run test:ui`（脚本门禁）；测试覆盖：缺考排除、空队列、跨学年 404、教学域隔离、干预队列含 pending 复查、limitations 文案存在、聚合与 B3 单生分解一致（同源断言）。提交信息 `feat(p3-d1): <摘要>`。

## 5. 2026-10-07 关注回看实用化（新端点 p3-focus-v1）

旧 §2 端点与计算行为保持兼容，新页面导航名「关注回看」。不改 B1/B2 类型规则，不迁移数据。

- 班主任 `research/focus/cohorts`：当前与考试起点名单分列，主类型或次标签命中即入选；历史日期可证明才返回 `focus:<问题>@<起点>`，日期不足只提供 `current-focus:<问题>`。成员仍为当前名册。
- 班主任 `research/focus/outcome`：历史名单锚点必须等于 from_exam；终点只用于比较，不重新筛掉改善者。响应保留所有入选者的依据、起终值、单人事实及缺失原因，分为 students 与 excluded_students。
- 偏科默认比较起点原短板的单科百分位和同场主三门百分位差；差距缩小但单科退步提示核查，不报改善。部分学科缺失逐科保留，不报告全部改善。
- 作业默认分别比较两考试截至当日近30天窗口，显示缺交次数、按既有算法连缺天数、有效应交批次与学科构成。无覆盖或旧例外记录缺分母时暂不可比；不根据缺交下降自动判断改善，也不补造提交率。
- 普通指标变化复用 B3，展示阈值按单位：名次读取全局诊断方向阈值（默认80名）、百分点5、等级分3。后两者为未校准的暂定展示阈值，页面明确注明；实际变化不因低于阈值隐藏。前百分位显示0–100，B3变化本身已为百分点；C4基线与变化0–1仅在展示时乘100。
- `{homeroom|teaching}/research/follow-ups`：在既有范围内逐条复用 C4 自己的目标、开始日、基线对照；教师 open/done/dismissed 与 ready/pending 分别呈现，到期和可比记录计数允许重叠。离班记录只保留合法档案，不跨当前名册读取成绩。
- 页面提供问题回看／跟进复查两个流程，链接可编辑学生档案；筛选或范围变化清空旧结果并取消在途请求；无成绩不转0，类型回看不代替因果评价。
- 合成验收覆盖：固定起点、次标签、日期降级、偏科伪改善、缺考残留值、单位边界、作业覆盖、逐条C4对照、跨域/空范围/越界。浏览器使用独立合成库与端口，不读写生产数据。
