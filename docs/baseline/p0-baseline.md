# P0 基线报告：可复现的工程起点（R1/R2/R3 修订版）

日期：2026-09-11。来源与许可证见 `SOURCES.md`。
本版补齐 R1 依赖复现、R2 四组合来源基线、R3 接口清单作用域修正，并更正首版报告的两处不准确描述（测试数据目录、告警分类）。

## 1. 交付内容

| 项 | 位置 | 说明 |
|---|---|---|
| Git 仓库 | 本仓库 | 规划基线根提交 `580bfc8`；`.sources` 不入库 |
| H 后端 | `backend/` | 班主任版 `dabb45c` 原样复制（app、tests、pyproject.toml、Dockerfile）+ `LICENSE`（MIT） |
| T 前端 | `frontend/` | 教学版 `f9ab60f` 原样复制（src、tests、package-lock.json、配置）+ `LICENSE`（MIT） |
| 依赖锁 | `backend/requirements-lock.txt` | **实际生效**的安装约束：在 Python 3.11（基准运行时）解析生成，含运行+测试依赖（pytest/httpx）共 55 项；README、CI 安装命令均带 `--constraint`，pip 缓存键同指此文件 |
| CI | `.github/workflows/ci.yml` | 教学版 ci.yml 蓝本：后端 pytest（Python 3.11 + 锁约束安装）+ 前端 tsc/test:ui/build（Node 20） |
| 接口差异清单 | `docs/baseline/api-diff.md` | T 前端 54 个调用 × H/T 后端端点对照（R3 修订：✅10 / ❌17 / ⚠️27，含作用域语义复核 B9 节） |
| 安装/检查命令 | `README.md` | 与 CI 同一命令集；后端/前端为独立可执行命令块 |

来源基线核对：`.sources/homeroom` HEAD = `dabb45ce…`、`.sources/teaching` HEAD = `f9ab60ff…`，与 `docs/planning/source-baseline.json` 一致，两克隆工作区干净、未被修改。

## 2. 四组合来源基线（R2 补齐）

本机：macOS arm64（darwin 27.0.0）、Node v26.7.0、npm 11.19.0；基准 Python **3.11.15**（与 `backend/Dockerfile` `python:3.11-slim`、来源 CI 一致；另在 3.14.6 上验证锁文件同样成立）。CI 固定 Python 3.11 / Node 20。日志存 `.test-data/logs/`（本地保留，不入库）。

### 结果矩阵

| # | 组合 | 运行位置与环境 | 命令 | 结果 |
|---|---|---|---|---|
| 1 | H 后端 | 合并工程 `backend/`，干净 `.venv311-verify`（仅按锁 `--constraint` 安装） | `pytest tests/ -q` | **294 通过 / 9 跳过 / 0 失败**（5.96s） |
| 2 | T 后端 | `.test-data/src-teaching/`（本地完整克隆，保持 `backend/`+`frontend/`+`.git` 布局），专属 `.venv-t311`（editable 指向 T 副本，同锁约束） | `pytest tests/ -q` | **641 通过 / 3 失败 / 1 告警**（143.53s），失败登记见下 |
| 3 | H 前端 | `.test-data/src-homeroom/`（隔离副本，`frontend/`+兄弟 `backend/` 源码） | `npm run test:ui`；`npx tsc --noEmit`；`npm run build` | test:ui 全过（node --test 16 + vitest 5/2/8/25）；tsc 0 错误；build 成功 |
| 4 | T 前端 | 合并工程 `frontend/` | 同上 | test:ui 10/10；tsc 0 错误；build 成功（Next.js 14.2.35） |

### T 后端 3 个既有失败登记（锁定依赖版本下）

| 测试 | 落点 | 现象 |
|---|---|---|
| `test_homework_identity.py::test_roster_record_count_person_total` | `tests/test_homework_identity.py:193` | `sqlite3.IntegrityError: UNIQUE constraint failed: class_roster.student_id`（批量插入 tapi-s1/tapi-s2 触发） |
| `test_teaching_router.py::test_upsert_overwrites_by_name` | `tests/test_teaching_router.py:210` | `assert 0 == 1`（按名覆盖后计数不符预期） |
| `test_teaching_router.py::test_anon_reassign_does_not_leak_across_classes` | `tests/test_teaching_router.py:258` | `assert '7100066' == '_anon:1:王某'`（匿名改派后学号不符合预期） |

登记说明：P0 不修来源业务缺陷；这三个失败是迁移教学班/成员/身份逻辑（P1/P4）时区分"来源已有问题 vs 迁移引入回归"的基线。两源仓库均不锁定 Python 依赖，本结果基于 2026-09-11 锁定版本；这些用例在来源仓库历史依赖版本下是否同样失败未验证、无法追溯。

### 基线方法论（复现要点）

- **不能共享 venv 跑两个后端**：editable 安装会互相污染 `app` 包解析（实测：用 H-editable venv 跑 T 测试时，`test_seed_cleanup` 子进程导入到 H 的 models 报 `TeachingClass` ImportError）。每个后端用专属 venv。
- **来源测试假定仓库布局**：H 前端契约测试直接读兄弟目录 `../backend/app` 源码；T 后端 SourceGuard 测试读 `../frontend/src` 源码、`test_tools_projection` 执行 `git show HEAD:backend/...`。隔离副本必须保持 `backend/`+`frontend/`(+`.git`) 兄弟布局，否则产生 ENOENT/错库失败（首跑 H 前端 16 败、T 后端 9 败均为布局所致，修正布局后消除，不计入基线）。
- **数据目录说明（更正首版表述）**：H 后端 `tests/conftest.py` 会在导入 app 前把 `EXAM_TRACKER_DIR`/`EXAM_TRACKER_BACKUP_DIR` **无条件覆盖**为系统临时目录（`tempfile.mkdtemp`），外部环境变量只是导入前的兜底；T 后端 conftest 在外部显式设置时沿用（本次落点 `.test-data/t-backend/`）。两版测试均不触碰个人默认 `~/.exam-tracker`。
- **告警分类（更正首版"全部 utcnow"）**：3.14 运行下 356 条警告绝大多数为 `datetime.utcnow()` 弃用（该提示仅 3.12+ 出现），另含 Starlette `anyio.abc.BlockingPortal` 弃用；3.11 基准运行下仅 1 条（BlockingPortal）。
- R1 验证链：3.11 干净 venv `pip install -e backend pytest httpx --constraint backend/requirements-lock.txt` → `pip check` 无冲突 → 解析版本与锁逐项 diff 一致 → 完整 pytest 通过。锁在 3.14 下亦解析出同一版本集（两版 freeze 逐字节一致），无需平台条件约束；linux/CI 环境的最终确认待远程 CI 首跑（当前无远程仓库）。

## 3. 已知限制与不做的事

- `backend/`（H）与 `frontend/`（T）**API 不兼容**，前后端尚不能联调，本阶段不宣称产品可用。静态对照（R3 修订后）：T 前端 54 个唯一调用中 ✅ 一致 10（均为无学生范围语义的全局操作）、❌ H 不存在 17（`/api/teaching/*` 全部 14 个 + 3 个聚合端点）、⚠️ 契约差异 27（含响应结构分叉与**作用域契约分叉**：同一端点 H 按行政班、T 按教学范围校验，及两版一致缺少范围校验的 notes CRUD）。最致命的是聊天作用域契约（直连必然 409）与 `teaching_class_id` 被静默忽略导致的范围泄漏。详见 `docs/baseline/api-diff.md`（判定口径与 B9 复核）。
- T 后端 3 个既有失败见 §2 登记；H 后端无失败，9 个 skip 为"本地库无真实数据"守卫（设计使然）。
- T 的 `test:ui` 为 Node 契约测试；H 前端的 Vitest 交互套件属班主任旧界面，P2 重建班主任侧时按验收 U01/S07 决定沿用方式。
- 两源仓库根目录的启动脚本、docker-compose、Caddyfile、NAS 脚本未带入；合并版部署配置在 P7 另行编写，CI 暂不含 compose 校验 job（原属 T 的部署链）。
- 未初始化远程仓库、未推送；未读取任何真实学生数据或生产数据库；CI 尚未在 GitHub Actions 实跑。

## 4. 下一步：P1 范围（供领取人参考）


1. 冻结接口契约：WorkspaceContext、data_domain、HomeroomTeachingLink、LinkedStudent、学年/届别、成员有效期、SourceMap、迁移版本表。
2. 数据模型骨架与带版本迁移（Alembic 初始迁移），落实唯一键/外键/域分区约束；移除"导入 models 即自动迁移"的隐式副作用。
3. OpenAPI 覆盖配置、范围、关联预览/确认/取消、学生列表/画像、成绩查询、导入预览/确认，含 H6/T6/T8 样例。
4. 基础用例 S01–S06/M01 的测试骨架（合成数据，显式测试目录；沿用本报告 §2 的隔离方法论）。
5. 作业表仅冻结关键外键与批次契约，完整规则留 P5。
6. 契约裁决输入：`docs/baseline/api-diff.md` B9/E11 的"同形不同范围"清单（toggle-excluded、作业删除、semester 前置、notes 范围缺失）与 T 后端 3 个既有失败涉及的 class_roster 唯一约束语义。
