# 学情追踪

面向高中教师个人使用的本地化学情管理与分析应用。一个登录入口内提供“班主任”和“教学”两个工作台，统一管理成绩、作业、学生名册、成长记录与 AI 辅助分析，同时保持清晰的数据边界。首版是单教师自用工具，不是学校多用户管理平台。

## 应用简介

“学情追踪”由原班主任版和教学版合并而来：班主任工作台保留行政班全科成绩、总分、学生档案和班级管理能力；教学工作台聚焦教师所教学科及教学班。两边默认隔离，只有经过明确配置的行政班—教学班关联，才能共享本人班级的必要教学数据。

当前状态：开发与合成验收已经完成，并完成过一次真实历史快照的本地隔离迁移与验收；NAS 候选站部署、最终停写快照重迁、生产入口切换和现场回退验证尚未执行。最近一次完整验证为后端 638 项通过、9 项跳过，前端 71 项通过，生产构建成功。

## 主要功能

- 成绩管理：导入考试成绩，查看班级与学生成绩，保留缺考原貌并注明排名口径。
- 学情分析：提供趋势、分段、班级对比和个人画像；历史学年可直接切换查看。
- 作业跟踪：记录布置、提交、补交和订正，区分学科与作业种类，不把“无记录”误判为“已交”。
- 学生管理：维护行政班和教学班名册、学号变更、离班、换届与历史成员。
- 成长记录：保存谈话、观察、家访、家长沟通、奖惩与后续跟进。
- 双工作台：班主任侧保留合法全科与总分；教学侧只读取任教学科和授权范围。
- 受控共享：关联班通过明确的班级关系和学生配对，只共享名册、当前任教学科成绩和当前任教学科作业三类白名单数据。
- AI 助手：可选接入 Anthropic 或 OpenAI；会话和工具始终绑定当前工作台、学年、班级与科目。
- 只读 MCP：可选开启 `/mcp`，供外部工具在同一作用域规则下查询数据。
- 备份与迁移：支持 SQLite 备份、双旧库迁移演练、来源追踪、冲突保留和回退核对。

## 数据边界

- 班主任与教学数据默认隔离，复用代码不代表共享业务数据。
- 其他教学班的名册、成绩和作业不会进入班主任工作台。
- 班主任私密档案不向教学工作台共享。
- 同名、同学号或同班号都不会自动认定为同一人；跨域身份必须明确确认。
- 原始缺考显示为“—”，不会转换为 0 分。
- 所有班级、成员、学年和科目范围都由后端校验，空范围不会退化成全年级。

## 快速开始

### macOS 一键启动

首次使用先安装后端和前端依赖：

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e backend pytest httpx --constraint backend/requirements-lock.txt
cd frontend && npm ci && cd ..
```

随后双击仓库根目录的 `启动应用.command`。脚本会启动后端与前端，等待服务就绪后自动打开：

```text
http://localhost:3000
```

双击 `停止应用.command` 可停止本地服务。默认数据、备份和日志均位于 `.test-data/dev/`，不会读取旧应用或默认用户目录。也可在启动前覆盖：

```bash
EXAM_TRACKER_DIR=/path/to/data \
EXAM_TRACKER_BACKUP_DIR=/path/to/backups \
EXAM_TRACKER_LOG_DIR=/path/to/logs \
./启动应用.command
```

### 分别启动前后端

后端：

```bash
cd backend
EXAM_TRACKER_DIR="$PWD/../.test-data/dev/exam-tracker" \
EXAM_TRACKER_BACKUP_DIR="$PWD/../.test-data/dev/backups" \
../.venv/bin/uvicorn app.main:app --port 8000
```

前端：

```bash
cd frontend
npm run dev
```

本地开发时，Next.js 会把 `/api/*` 转发到 `http://localhost:8000`。

## 验证

后端全量测试必须使用独立数据目录：

```bash
TEST_ROOT="$(mktemp -d)"
EXAM_TRACKER_DIR="$TEST_ROOT/data" \
EXAM_TRACKER_BACKUP_DIR="$TEST_ROOT/backups" \
.venv/bin/python -m pytest backend/tests -q
```

前端契约测试与生产构建：

```bash
cd frontend
npm run test:ui
npm run build
```

## Docker 与 NAS 部署

生产候选由 FastAPI、Next.js 和 Caddy 三个服务组成，Compose 项目名固定为 `performance_unified`。

```bash
cd deploy
cp compose.env.example compose.env
cp backend.env.example backend.env
# 按文件注释填写端口、数据目录、登录口令和可选 AI 配置
docker compose --env-file compose.env up -d --build
```

部署前必须保证新数据目录和备份目录独立于两套旧应用。不要覆盖旧数据库、旧容器或旧挂载。完整的候选验证、停写、切换和回退步骤见 [部署切换手册](deploy/SWITCHOVER.md)。

## 历史数据迁移

真实迁移使用两套旧库的一致性快照，不直接修改来源数据库。无法安全进入新业务模型的内容仍会进入只读来源归档，迁移完成也必须分别核对来源追踪、成员覆盖、业务投影以及页面/API 可见性。

- [迁移方案](docs/planning/04-migration.md)
- [验收清单](docs/planning/05-acceptance.md)
- [NAS 切换与回退手册](deploy/SWITCHOVER.md)

## 技术架构

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11、FastAPI、SQLAlchemy 2、Alembic、SQLite |
| 前端 | Next.js 14、React 18、Radix UI、Tailwind CSS、Recharts |
| 服务入口 | Caddy：`/api/*` → backend，其余请求 → frontend |
| 部署 | Docker Compose，本地构建或指定预构建镜像 |
| 持续集成 | GitHub Actions：后端测试、前端契约测试与生产构建 |

## 目录结构

```text
backend/    FastAPI 后端、数据库迁移与测试
frontend/   Next.js 前端与 UI 契约测试
deploy/     Docker Compose、Caddy、环境示例和切换手册
scripts/    数据迁移、备份恢复与核对脚本
docs/       架构、契约、规划和验收文档
```

## 配置与隐私

- Git 不跟踪数据库、账号、令牌或生产备份；`data/`、`backups/`、`.test-data/` 和本地环境文件均被排除。
- 内部迁移与审核记录必须按敏感资料管理，公开发布前应检查并脱敏其中的人工身份裁决信息。
- 登录口令和 AI Key 通过运行环境或 `deploy/backend.env` 注入。
- AI Key 未配置时，成绩、作业、学生管理等核心功能仍可正常使用。
- MCP 默认关闭；公网启用时必须显式配置允许的 Host 与 Origin。

## 项目记录

以下规划与来源文档保留项目演进过程，其中部分阶段状态属于历史记录；当前完成情况以本 README、任务池和最新审核报告为准。

- [总体规划](docs/planning/01-master-plan.md)
- [功能取舍](docs/planning/02-feature-decisions.md)
- [架构与数据契约](docs/planning/03-architecture.md)
- [协作规则](docs/planning/06-collaboration.md)
- [来源代码说明](SOURCES.md)

## 许可证

[MIT](LICENSE)。后端与前端分别源自班主任版和教学版仓库的固定提交，详细来源和许可证继承关系见 [SOURCES.md](SOURCES.md)。
