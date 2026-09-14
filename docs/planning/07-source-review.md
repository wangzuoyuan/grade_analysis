# 源码审查证据

本次直接从用户指定的 GitHub 仓库克隆，未使用可能有未提交修改的旧本地工作副本。固定提交保证后续可复核。

| 来源 | 固定提交 | 后端测试文件数（不是已通过数量） |
|---|---|---|
| homeroom | [dabb45c](https://github.com/wangzuoyuan/-Exam-Performance-Analysis/commit/dabb45ce2742d264681ce1dd81bf7a46de8d68ac) | 29 |
| teaching | [f9ab60f](https://github.com/wangzuoyuan/Performance-Analysis-Teaching/commit/f9ab60ff878d4636ed99cab87a580e14b5481ddd) | 34 |

## 已定位的核心实现

| 来源 | 证据 | 规划用途 |
|---|---|---|
| homeroom | [backend/app/db/models.py:71 · TotalScore](https://github.com/wangzuoyuan/-Exam-Performance-Analysis/blob/dabb45ce2742d264681ce1dd81bf7a46de8d68ac/backend/app/db/models.py#L71) | 全科与总分保留 |
| homeroom | [backend/app/db/models.py:268 · RosterImportBatch](https://github.com/wangzuoyuan/-Exam-Performance-Analysis/blob/dabb45ce2742d264681ce1dd81bf7a46de8d68ac/backend/app/db/models.py#L268) | 名册导入批次快照 |
| homeroom | [backend/app/db/models.py:289 · ImportedHistory](https://github.com/wangzuoyuan/-Exam-Performance-Analysis/blob/dabb45ce2742d264681ce1dd81bf7a46de8d68ac/backend/app/db/models.py#L289) | 个人手工历史独立存储 |
| homeroom | [backend/app/ingest/router.py:145 · parse_and_store](https://github.com/wangzuoyuan/-Exam-Performance-Analysis/blob/dabb45ce2742d264681ce1dd81bf7a46de8d68ac/backend/app/ingest/router.py#L145) | 全科写入、撞号守门 |
| homeroom | [backend/app/homework/service.py:293 · warnings](https://github.com/wangzuoyuan/-Exam-Performance-Analysis/blob/dabb45ce2742d264681ce1dd81bf7a46de8d68ac/backend/app/homework/service.py#L293) | 全交台账进入预警时间轴 |
| teaching | [backend/app/ingest/router.py:84 · parse_and_store](https://github.com/wangzuoyuan/Performance-Analysis-Teaching/blob/f9ab60ff878d4636ed99cab87a580e14b5481ddd/backend/app/ingest/router.py#L84) | 只存任教学科、不写总分；合并后只保留在独立教学导入策略 |
| teaching | [backend/app/db/models.py:251 · TeachingClassMember](https://github.com/wangzuoyuan/Performance-Analysis-Teaching/blob/f9ab60ff878d4636ed99cab87a580e14b5481ddd/backend/app/db/models.py#L251) | 教学班成员及来源 |
| teaching | [backend/app/db/models.py:149 · HomeworkRecord](https://github.com/wangzuoyuan/Performance-Analysis-Teaching/blob/f9ab60ff878d4636ed99cab87a580e14b5481ddd/backend/app/db/models.py#L149) | 已交/缺交状态及评价 |
| teaching | [backend/app/homework/service.py:137 · _PersonScope](https://github.com/wangzuoyuan/Performance-Analysis-Teaching/blob/f9ab60ff878d4636ed99cab87a580e14b5481ddd/backend/app/homework/service.py#L137) | 最新版本读时按人聚合 |
| teaching | [backend/app/homework/service.py:602 · dashboard](https://github.com/wangzuoyuan/Performance-Analysis-Teaching/blob/f9ab60ff878d4636ed99cab87a580e14b5481ddd/backend/app/homework/service.py#L602) | 作业类型与评价看板 |

## 配套入口

- homeroom: [backend/app/db/sid_space.py](https://github.com/wangzuoyuan/-Exam-Performance-Analysis/blob/dabb45ce2742d264681ce1dd81bf7a46de8d68ac/backend/app/db/sid_space.py)
- homeroom: [backend/app/student_management/service.py](https://github.com/wangzuoyuan/-Exam-Performance-Analysis/blob/dabb45ce2742d264681ce1dd81bf7a46de8d68ac/backend/app/student_management/service.py)
- homeroom: [backend/app/rollover/service.py](https://github.com/wangzuoyuan/-Exam-Performance-Analysis/blob/dabb45ce2742d264681ce1dd81bf7a46de8d68ac/backend/app/rollover/service.py)
- teaching: [backend/app/analysis/exam_context.py](https://github.com/wangzuoyuan/Performance-Analysis-Teaching/blob/f9ab60ff878d4636ed99cab87a580e14b5481ddd/backend/app/analysis/exam_context.py)
- teaching: [backend/app/analysis/single_subject_metrics.py](https://github.com/wangzuoyuan/Performance-Analysis-Teaching/blob/f9ab60ff878d4636ed99cab87a580e14b5481ddd/backend/app/analysis/single_subject_metrics.py)
- teaching: [frontend/src/components/layout/Shell.tsx](https://github.com/wangzuoyuan/Performance-Analysis-Teaching/blob/f9ab60ff878d4636ed99cab87a580e14b5481ddd/frontend/src/components/layout/Shell.tsx)
- teaching: [frontend/src/app/globals.css](https://github.com/wangzuoyuan/Performance-Analysis-Teaching/blob/f9ab60ff878d4636ed99cab87a580e14b5481ddd/frontend/src/app/globals.css)
- teaching: [frontend/tailwind.config.js](https://github.com/wangzuoyuan/Performance-Analysis-Teaching/blob/f9ab60ff878d4636ed99cab87a580e14b5481ddd/frontend/tailwind.config.js)
- teaching: [.github/workflows/ci.yml](https://github.com/wangzuoyuan/Performance-Analysis-Teaching/blob/f9ab60ff878d4636ed99cab87a580e14b5481ddd/.github/workflows/ci.yml)

## 审查注意事项

- T 的基础 `analysis/scope.py:resolve_scope(None)` 仍表示不限定；业务层的 exam_context/single_subject_context 才有合法教学班约束。合并版必须调用严格上下文，不能仅复用底层同名 helper。
- T 最新 smart-input 确认分支会先收集全交与个人例外；旧 records 入口仍是另一条流程。因此旧“全交”问题只列为双入口回归用例，本次不声称当前版本已复现同一 bug。
- H 的学生号命名空间兼容 G1:: 与 g1-，但年级不等于长期唯一届别；新模型不能把旧兼容标记提升为永久身份键。
- 源码自带 AGENTS.md 的端点/测试/部署说明存在历史描述；已以当前文件确认技术栈和关键行为。
- 本次没有运行后端完整测试、前端构建和生产迁移。PLAN 完成只表示规划文件完成；P0 必须独立建立运行基线。
- 用户提供的参考对话用于理解仓库规则/交接模式；没有直接采用其中 React+PostgreSQL 等示例作为真实项目技术栈。
