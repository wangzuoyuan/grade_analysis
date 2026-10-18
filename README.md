# 学情追踪

面向高中教师个人使用的学情管理与分析应用。一个登录入口内提供 **班主任、教学两个工作台**，管理成绩、作业、学生名册、成长记录和 AI 辅助分析。应用可以在本机或 Docker 主机运行，也支持 Vercel 前端连接持久化后端。

项目已投入日常使用。本文介绍 GitHub `main` 已包含的功能和配置；历史规划文档中的阶段状态、旧测试数量不代表当前版本。首版面向教师本人，不提供学校多用户账号与角色管理。

## 功能一览

| 模块 | 当前能力 |
|---|---|
| 双工作台 | 班主任侧管理行政班全科、总分及私密档案；教学侧聚焦本人任教学科与教学班。分别保存筛选范围，切换不迁移数据 |
| 成绩导入 | 保留旧模板，新增教学平台学生明细、教学班/行政班质量分析表；按年级下载空白 Excel 模板，先预览再确认 |
| 多班自动分配 | 多个教学班可合用一份学生明细，默认按学号匹配考试当日有效名册；找不到学生、未在名册或多班歧义会明确报错 |
| 成绩分析 | 切换教学班或查看全部所教班；学生成绩按班分组，同分同名次，缺考单列；姓名可进入个人页查看历次成绩与作业 |
| 分数段分析 | 按 5/10/20 分间隔展示各教学班及当前范围合计的分段人数、占比、累计人数，以及平均分、最高/最低分、标准差；悬浮显示段名与人数占比 |
| 官方班级对比 | 官方质量分析与本人学生样本分析分别展示；注明官方/估算、统计范围及覆盖情况，不把所教班样本冒充全年级 |
| 考试管理 | 两工作台均可管理考试；同名异日区分，历史重复考试先预览再关联，保留来源和冲突；删除需预览、完整名称确认及有效确认凭据 |
| 指标补录 | 补录高一年级百分位、学籍排名或高二/高三等级分；核对已有原始分，空白不清空已有字段 |
| 逐人纵向分析 | 将每位学生历次考试与考前作业窗口配对，区分当场信息、相邻变化和探索性相关，支持缺失原因及敏感性说明 |
| 诊断与跟进 | 六类诊断指标、可解释学生类型、学生/班级变化分解、优先跟进名单、干预记录、复查及教研统计回看；支持诊断阈值配置 |
| 作业跟踪 | 智能例外录入、请假/缺交/忘带和评价，按日汇总、全科透视与连续异常预警；支持人工解除、后续跟进 |
| 学生管理 | 行政班/教学班名册、学号自然排序、学号与别名演进、有效期、离班、历史成员和换届承接及撤销 |
| 成长档案 | 谈话、观察、家访、家长沟通、奖惩及后续跟进；提供学生报告与打印 |
| AI 与 MCP | 可选 Anthropic 或 OpenAI 兼容接口；应用内助手与只读 MCP 使用同源查询工具，后端校验工作台、学年、班级和学科范围 |
| 备份与发布 | SQLite 一致性备份、历史双库迁移工具、CI 验证、受控后端发布和回退流程 |

### 最近新增功能的使用方式

**导入多班成绩**：先维护教学班名册，在上传页选择年级和教学平台格式，保留默认“按学号自动分到各教学班”，上传合并明细后核对预览再确认。该功能不会自动创建学生身份或补齐班级成员。只传质量表时可选择不指定教学班；指标补录沿用学生明细模板。

**查看成绩与分数段**：进入教学工作台“成绩分析”，用页面班级下拉选择单班或全部。成绩表按班展示，分数段卡随范围更新，缺考不计作 0 分；分数段人数来自当前可见学生成绩。查看学校提供的官方质量表，请进入“班级对比”。

**查看个人作业与成绩变化**：点击学生姓名进入个人页。高一使用年级百分位，高二、高三使用等级分，缺少指标会提示补录，不用原始分冒充可比指标。1 场展示当场情况，2–3 场展示逐场及相邻变化，至少 4 场有效配对后展示探索性 Kendall tau-b，并以 Spearman 和删去单场后的结果辅助判断。样本少、指标缺失或零方差会给出原因；系数不代表作业导致了成绩变化。

**处理历史考试**：在考试管理中预览重复考试的关联结果，核对日期、学生、原始成绩及冲突后确认；不同原始分不会被静默覆盖。删除只处理当前工作台允许删除的原生成绩，不顺带删除学生、作业、档案或另一工作台的来源数据。

## 数据边界与统计口径

- 两工作台默认隔离。只有明确配置行政班—教学班关联及学生配对，才共享交集学生在有效期内的任教学科成绩、作业和必要名册信息；班主任私密档案默认不共享。
- 姓名、裸学号、年级或班号相同，不足以自动认定为同一人。换届与跨域关联必须保留身份和时间边界。
- 班级、成员、学年、学科范围均由后端校验；空范围不会扩大为全年级。AI 与 MCP 也遵守相同边界。
- 原始缺考显示“—”，真实 0 分仍是 0 分。同分同名次；班内名次、来源学籍排名和全年级指标分别标注。
- 作业采用例外登记：**在已知应交名单和有效批次内**，未登记缺交/请假等例外的人默认已交；没有批次、成员或可靠分母时，不凭“无记录”推断已交或计算提交率。
- 诊断或分层所需数据不在当前工作台内时，显示不可计算及原因；教学独立来源不额外存储全科或总分来凑统计。

## 本地启动

需要 Python 3.11、Node.js 20 和 npm。在仓库根目录安装依赖：

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e backend pytest httpx --constraint backend/requirements-lock.txt
cd frontend
npm ci
cd ..
```

macOS 可双击 `启动应用.command`，服务就绪后自动打开 `http://localhost:3000`；双击 `停止应用.command` 停止。默认数据、备份和日志分别位于 `.test-data/dev/` 下，不使用旧应用目录。

也可以分别启动。后端在一个终端中运行：

```bash
cd backend
EXAM_TRACKER_DIR="$PWD/../.test-data/dev/exam-tracker" \
EXAM_TRACKER_BACKUP_DIR="$PWD/../.test-data/dev/backups" \
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

前端在另一个终端中运行：

```bash
cd frontend
npm run dev
```

Next.js 默认将 `/api/*` 转发到 `http://localhost:8000`；需要连接其他后端时，在启动前设置 `BACKEND_INTERNAL_URL`。不设置 `APP_PASSWORD` 时应用关闭登录校验，适合本机开发；公网部署必须设置密码。

## Docker 部署与内外网访问

使用 Docker Compose v2，需支持 `env_file.required`。Compose 项目名固定为 `performance_unified`，包含 FastAPI、Next.js、Caddy 三个服务。

### 1. 创建私有配置

```bash
cd deploy
cp compose.env.example compose.env
cp backend.env.example backend.env
```

`compose.env` 用于 Compose 的端口、镜像和挂载变量。示例：

```dotenv
IMAGE_TAG=latest
CADDY_PORT=8081
LAN_PORT=8090
DATA_DIR=/absolute/path/to/independent-data
```

将 `DATA_DIR` 改为宿主机上的独立绝对路径；不要指向旧应用的数据目录。源码构建可保留默认镜像名；有预构建镜像时，按模板填写 `BACKEND_IMAGE`、`FRONTEND_IMAGE` 和对应 `IMAGE_TAG`。

`backend.env` 注入后端及 Caddy 的运行环境，至少填写：

```dotenv
APP_PASSWORD=replace-with-a-strong-password
AUTH_LAN_BYPASS_TOKEN=replace-with-generated-random-token
```

用 `openssl rand -hex 32` 生成随机值，替换第二项。配置文件含凭据，不要提交 Git。

### 2. 区分两个入口

| 入口 | 默认访问地址 | 登录行为 | 接入方式 |
|---|---|---|---|
| 内网入口 | `http://<宿主机内网IP>:8090` | 配好免登密钥后免登录 | 仅允许可信局域网访问，不做公网端口映射 |
| 公网入口 | `https://<你的域名>`，反代到宿主 `8081` | 需要应用密码 | HTTPS 反向代理指向 `CADDY_PORT`，不要指向 8090 |

内网入口由 Caddy 注入内部免登凭据；公网入口清除客户端提交的同名头。修改域名或伪造 Host 不会获得免登录。两个入口共用同一后端和数据。

当前 Compose 将 `LAN_PORT` 发布到宿主机所有地址，**端口名本身不提供内外网隔离**：需通过宿主防火墙、路由器和反向代理确保该端口仅可信内网可达。宿主有公网地址或存在隧道时同样要限制来源。`LAN_BIND_IP` 尚不属于当前主线配置，不能仅设置该变量就认为已限制监听地址。

Docker Desktop 的端口转发可能把不同客户端改写为相同网桥地址。本部署使用两个入口及内部凭据区分访问，`AUTH_BYPASS_NETWORKS`、`AUTH_TRUSTED_PROXY_NETWORKS` 保持未设置。`PUBLIC_HOST` 是兼容旧配置的变量，已不参与鉴权。

只需要所有入口都登录时，保留 `APP_PASSWORD`、不设置 `AUTH_LAN_BYPASS_TOKEN`。会话签名密钥 `SESSION_SECRET` 可留空，由程序在数据目录生成并持久化；如自行设置，应使用固定强随机值。HTTPS 反代应正确传递 `X-Forwarded-Proto`，以便应用设置安全 Cookie。

### 3. 构建、启动与检查

```bash
docker compose --env-file compose.env config --quiet
docker compose --env-file compose.env up -d --build
docker compose --env-file compose.env ps
curl --fail http://127.0.0.1:8081/api/health
```

以上是首次部署步骤。修改 `backend.env` 后需重新创建使用该配置的服务，单纯重启进程不会加载新的容器环境。验收时分别检查：内网业务 API 无需登录、公网未登录业务 API 返回 401、公网登录后可用。`/api/health` 是公开健康检查，返回 200 不能证明业务鉴权正确。

**已有生产环境更新**请沿用原有镜像覆盖文件及发布流程。仅更换前端时必须使用 `--no-deps`，避免 Compose 连带重建后端；安装了受控部署器的环境，还需使用其实际的 Compose 配置文件组合，不能照首次部署命令覆盖运行中的服务。详细步骤见 [部署切换与回退手册](deploy/SWITCHOVER.md)。

## 可选 AI 配置

不配置模型 Key 时，成绩、作业、名册等功能照常可用。使用 AI 时，选定提供商后填写对应配置：

| 配置项 | 用途 |
|---|---|
| `CHAT_PROVIDER` | `anthropic`（默认）或 `openai` |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | Anthropic 接口的密钥与模型名 |
| `ANTHROPIC_BASE_URL` | 可选兼容接口地址，留空使用提供商默认地址 |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | OpenAI 兼容接口的密钥与模型名 |
| `OPENAI_BASE_URL` | 可选兼容地址，按服务要求包含 `/v1` |
| `CHAT_MAX_TOKENS` | Anthropic 输出上限，默认 16384；OpenAI 分支不使用该配置 |

本地 AI 配置可将 [backend/.env.example](backend/.env.example) 复制为 `backend/.env` 后填写；**仅 AI 配置读取此文件**，登录、MCP 等配置仍需注入进程环境。Docker 部署把所选配置添加到 `deploy/backend.env` 后重新创建后端容器。不要同时保留另一套非空 `backend/.env` 配置造成覆盖：AI 配置中，文件内非空值优先于同名进程环境变量。

AI 工具只读，但发送给模型的提示和工具结果可能包含当前范围内的教学信息；启用前应确认所选服务的数据处理方式。

## 可选只读 MCP

MCP 默认关闭，启用后挂载 `/mcp`，采用独立 Bearer Token，并校验 Host、Origin。它与网页登录口令、内网免登密钥分别配置。

在 `deploy/backend.env` 中添加以下配置，替换占位符后重新创建后端容器：

```dotenv
MCP_ENABLED=true
MCP_BEARER_TOKEN=replace-with-a-separate-random-token
MCP_ALLOWED_HOSTS=school.example.com,school.example.com:*
MCP_ALLOWED_ORIGINS=https://school.example.com
```

Token 用另一次 `openssl rand -hex 32` 生成；启用时空值、弱占位符或短于 32 字符会导致后端拒绝启动。客户端连接 `https://school.example.com/mcp`，请求携带 `Authorization: Bearer <token>`。部署代理需转发 `/mcp` 及其子路径，不能落到前端页面。当前默认 Caddyfile 只转发 `/api/*`；启用 MCP 时，需在要开放 MCP 的站点块中、前端兜底 `handle` 之前加入以下配置，并重新加载 Caddy：

```caddyfile
@mcp path /mcp /mcp/*
handle @mcp {
    reverse_proxy backend:8000
}
```

如需两个入口都提供 MCP，分别在两个站点块中添加；只需要公网 MCP 时仅添加到 `:8080` 站点块。

每次工具调用需明确 `mode`（`homeroom` 或 `teaching`）及相应范围，服务端重新校验。非浏览器客户端不带 Origin 时不受 Origin 列表限制，仍须通过 Token 和 Host 校验。`MCP_LEGACY_TOOLS` 默认关闭，旧客户端确需兼容时才临时启用。详见 [AI / MCP 契约](docs/contracts/p6-ai-mcp.md) 与 [服务端实现说明](backend/app/mcp_server.py)。

## Vercel 前端与持久化后端

Vercel 项目的 Root Directory 设置为 `frontend`，安装命令 `npm ci`；现有受控发布使用构建命令：

```bash
node scripts/wait-for-backend.cjs && npm run build
```

为对应环境配置 `BACKEND_INTERNAL_URL=https://<后端HTTPS域名>`，使 `/api/*` 转发到持久化后端。Production 使用正式后端；Preview 使用独立候选后端、数据目录和凭据。数据库、备份和上传文件保存在后端主机，不能放入 Vercel 临时文件系统。

受控发布中，GitHub Actions 测试通过后生成 `backend-release` 工件，后端主机的控制器校验、构建并切换版本。`main` 等受控分支的前端构建等待后端 `/api/deployment` 返回同一提交 SHA 且 `ready=true`，最长 20 分钟。**首次使用上述构建命令前需先安装配置宿主控制器及 Caddy 状态入口**；仅上传前端不会建立这套发布链路。

Vercel 发布、Docker 后端发布、Docker 前端镜像更新是不同步骤。Vercel 构建成功不会自动更新宿主上的 Docker 前端；仅文档或前端变化且后端构建指纹不变时，控制器可推进部署标记而不重启后端。完整安装、分支候选和回退流程见 [部署手册](deploy/SWITCHOVER.md#7-受控后端自动发布)。

## 备份、恢复与历史迁移

- SQLite 备份使用一致性备份接口，覆盖 WAL 中已提交的数据；不要直接复制正在写入的 `db.sqlite` 作为完整备份。
- 恢复或版本回退前，应让全部入口停写、停止相关后台任务并关闭数据库连接，保留恢复前的新数据，再验证恢复结果。不要把页面恢复操作当作可在并发写入时使用的热恢复机制。
- 两套旧库迁移以独立一致性快照为输入，不直接修改来源数据库；保留来源追踪、身份冲突和不可投影内容的只读归档。
- 备份、导出、上传文件和会话签名文件都属于私有运行资料，不提交 Git。生产数据不作为公开模板或测试夹具。

参考：[迁移方案](docs/planning/04-migration.md) · [切换与回退](deploy/SWITCHOVER.md)。

## 验证

测试使用显式独立目录，不访问生产库或默认个人数据目录。在仓库根目录运行：

```bash
TEST_ROOT="$(mktemp -d)"
EXAM_TRACKER_DIR="$TEST_ROOT/data" \
EXAM_TRACKER_BACKUP_DIR="$TEST_ROOT/backups" \
.venv/bin/python -m pytest backend/tests -q

.venv/bin/python -m unittest discover -s deploy/tests -p 'test_*.py'
node --test deploy/tests/backend-gate.test.cjs
```

前端：

```bash
cd frontend
npx tsc --noEmit
npm run test:ui
npm run build
```

这些是验证命令，不代表每次文档修订都运行全部测试；具体提交结果以对应 CI 为准。

## 技术架构与目录

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11、FastAPI、SQLAlchemy 2、Alembic、SQLite |
| 前端 | Next.js 14、React 18、Radix UI、Tailwind CSS、Recharts |
| 服务入口 | Caddy：API / MCP → backend，其余请求 → frontend；内网与公网入口分别处理鉴权 |
| 部署 | Docker Compose；可选 Vercel 前端；GitHub Actions 与宿主受控发布器 |

```text
backend/    后端、数据库迁移、环境配置示例与测试
frontend/   前端、空白导入模板、构建脚本与 UI 测试
deploy/     Docker Compose、Caddy、部署器、环境示例与回退手册
scripts/    历史迁移、备份及核对工具
docs/       功能契约、架构、规划和验收文档
```

## 文档与隐私

- [教学平台导入与官方对比](docs/contracts/teaching-platform.md)
- [逐人作业与成绩分析](docs/contracts/student-homework-analysis.md)
- [AI / MCP 契约](docs/contracts/p6-ai-mcp.md)
- [总体规划（含历史阶段状态）](docs/planning/01-master-plan.md)
- [功能取舍](docs/planning/02-feature-decisions.md) · [架构](docs/planning/03-architecture.md)
- [验收标准](docs/planning/05-acceptance.md)

Git 不跟踪数据库、账号、令牌、生产备份及本地环境文件。`AGENTS.md` 等内部协作交接记录仅保留在本地；公开源码、模板和测试使用合成示例。启用外部 AI 或 MCP 时，应仅向可信服务/客户端授予所需范围的访问。

## 许可证

[MIT](LICENSE)。后端与前端分别源自班主任版和教学版仓库的固定提交，来源及许可证继承关系见 [SOURCES.md](SOURCES.md)。
