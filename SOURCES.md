# 来源说明（Source Attribution）

本仓库为合并版工程起点，代码来自两个既有仓库的固定提交，复制时未做源码修改（仅各新增一份对应 LICENSE）。
基线以 `docs/planning/source-baseline.json` 为准；本文件记录 P0 实际带入的内容。

| 来源 | 仓库 | 固定提交 | 带入路径 | 携带许可证 |
|---|---|---|---|---|
| 班主任版（H） | https://github.com/wangzuoyuan/-Exam-Performance-Analysis.git | `dabb45ce2742d264681ce1dd81bf7a46de8d68ac`（dabb45c） | `backend/` ← `.sources/homeroom/backend/`（app、tests、pyproject.toml、Dockerfile） | `backend/LICENSE` |
| 教学版（T） | https://github.com/wangzuoyuan/Performance-Analysis-Teaching.git | `f9ab60ff878d4636ed99cab87a580e14b5481ddd`（f9ab60f） | `frontend/` ← `.sources/teaching/frontend/`（src、tests、配置与 package-lock.json） | `frontend/LICENSE` |

## 复制核对

- 复制后 `diff -rq` 核对：`backend/`、`frontend/` 与对应源目录内容一致，唯一差异为新增的 LICENSE 文件。
- `.sources/` 为只读参考克隆，未修改（`git status` 干净），且被根 `.gitignore` 忽略、不进入本仓库。
- 未核对 GitHub 远端是否出现新提交（避免"追最新覆盖规划"）；后续调整基线前先在本地变更记录中留痕。

## 未带入的内容（按阶段另行处理）

- 两仓库根目录的启动/初始化脚本、`docker-compose*.yml`、`Caddyfile`、NAS 升级脚本：部署形态属于 P7，合并版部署配置将另行编写，不直接复用单应用旧脚本。
- H 的 `frontend/`（班主任版旧界面）：界面以教学版为唯一基准，按 P2 起用教学版设计系统重建班主任页面。
- T 的 `backend/`（教学版后端）：其能力按 P1–P5 逐模块以模型/服务形式迁入 H 后端工程，不做整目录覆盖。
- 两仓库各自的 `CHANGELOG.md`、`plan/`、`design-preview/` 等文档资产：需要时到 `.sources` 对照查阅。

## 工程起点声明

P0 阶段 `backend/` 为 H 原版后端、`frontend/` 为 T 原版前端，两者 API 尚不兼容（差异清单见 `docs/baseline/api-diff.md`），
本阶段不宣称产品可用；联调与契约冻结在 P1/P2 进行。
