# 上线切换与回退手册（SWITCHOVER）——合并版 performance_unified

> 范围声明：本手册与 `deploy/` 配置在**合成环境**验证（P7 演练管线 +
> `rehearse_backup_restore.py` 回退演练）；带【现场】标记的步骤必须在
> 上线当天于真实 NAS/真实双库上执行。真实切换完成前，**不得宣称历史
> 数据合并完成**。

## 0. 前置（候选验收门槛）

- [ ] P7 合成演练全绿：`run_rehearsal.py` 七阶段通过、verify 三分类完备、
      M01 幂等（同摘要重跑零新增）、M02 冲突/撤销清单可解析。
- [ ] 备份回退演练通过：备份 → 恢复 → 抽样一致；「上线后新写入」路径
      演练过（增量清单导出 + 只读回退 + 增量待回放留痕）。
- [ ] 【现场】真实双库只读预检完成：逐表数量、重复学号、前缀学号、
      空学科、名册与教学成员差异（报告只提交脱敏统计）。
- [ ] 【现场】用真实库的**隔离迁移副本**（非生产原件）重跑迁移管线并核对。

## 1. 停写检查清单（切换窗口开始时）

- [ ] 【现场】旧班主任版应用停写：确认无上传/录入进行中（查看活动会话）。
- [ ] 【现场】旧教学版应用停写：同上；两个旧应用**同时**停写，禁止
      三站并存写入后再猜哪个最新。
- [ ] 【现场】各自生成最终一致快照：用 SQLite backup API（或确认停写后
      完整复制 db.sqlite），**不直接复制正在写入的 db 文件**。
- [ ] 【现场】对两份快照计算 sha256 并登记；以快照为源重跑迁移/核对
      （源摘要变化 → 管线自动新建 MigrationRun 批次）。
- [ ] 【现场】核对通过才允许切流量：分区保留量、共享量、冲突量、
      待核实量、缺失来源达到验收门槛。

## 2. 部署与入口切换

- [ ] 【现场】`bash deploy/check-ports.sh <端口...>` 实测 NAS 端口占用
      （候选 8081，旧站 8080 不动），确定最终 `CADDY_PORT`。
- [ ] 【现场】准备独立目录：合并版 data 目录、备份目录，**不复用旧 data 卷**；
      `cd deploy && cp compose.env.example compose.env && cp backend.env.example backend.env`
      按注释现场填写（compose.env=端口/镜像/数据目录；backend.env=凭据，不入文档）。
- [ ] 【现场】构建并启动完整候选（backend+frontend 均可从仓库构建，X02 闭环）：
      ```
      cd deploy
      docker compose --env-file compose.env build          # 或 up -d --build 一步完成
      docker compose --env-file compose.env up -d
      curl http://127.0.0.1:${CADDY_PORT}/api/health        # 经 Caddy 入口验证后端路由
      ```
      （项目名已固定为 performance_unified；`--env-file` 指向上一步填写的 compose.env，
      缺省时 compose 变量回落 .env 同目录文件——候选目录没有 .env，必须显式携带。）
- [ ] 【现场】以隔离迁移副本作为初始数据先行验证候选站（登录、两工作台、
      关联班共享、其他教学班隔离、上传、作业、AI/MCP、下载、打印，
      以及绕过代理的本地健康检查与公网访问）。
- [ ] 【现场】核对通过 → 切换入口（DSM 反代/路由指向新端口）。旧站
      **保持可访问**（只读快照形态），不删除旧容器/卷/目录。

## 3. 旧站共存原则

- 旧班主任版、旧教学版的容器、卷、目录一律保留；何时下线由用户在
  候选验收后决定。
- 旧站仅作只读回退目标：发现合并版异常时，先停**新站**写入再回退，
  不在旧站恢复写入后继续让新站收数据。

## 4. 回退步骤（含「上线后已有新写入」路径）

1. **停写新站**（compose stop 或切断入口流量）。
2. **备份新库**：对合并版 data 目录执行完整备份
   （应用内备份 API 或停写后打包 db.sqlite + homework_exports）。
3. **导出增量清单**：以备份点时间戳为界的新增/修改行（created_at/updated_at
   越界，覆盖全部业务表）与删除行（主键清单）
   （演练实现见 `scripts/migration/rehearse_backup_restore.py`：
   `export_incremental` 按 manifest 备份点比对，输出 JSON 留存
   `incremental.json`；现场对真实库执行时按同一口径用停写快照比对）。
4. **只读回退旧站**：恢复旧站只读快照供日常使用（旧站入口重新指向）。
5. **如实声明**：回退 ≠ 无损——增量清单**待回放**。两条路线二选一：
   - 修复合并版问题后，在新库上回放增量清单，重新核对再切换；
   - 或将增量人工合并进旧站（仅限极少量增量），全程留痕。
6. 回退后登记：备份文件名、增量清单、回退时间、决策人。

## 5. 现场实测待办清单（上线前逐项打勾）

| # | 事项 | 说明 |
|---|------|------|
| 1 | 真实双库快照与摘要 | 停写后 SQLite backup API 快照 + sha256 登记 |
| 2 | 真实库迁移演练 | 隔离副本上重跑管线，核对三分类/冲突/撤销 |
| 3 | NAS 端口实测 | check-ports.sh + DSM 反代指向 |
| 4 | 镜像可用性 | GHCR 镜像拉取或现场构建（amd64/arm64） |
| 5 | 目录规划 | 合并版 data/备份目录独立于旧栈，磁盘余量确认 |
| 6 | 停写窗口演练 | 双旧站同时停写 → 快照 → 重跑核对 → 切入口 |
| 7 | 切换后功能清单 | 登录/两工作台/关联共享/隔离/上传/作业/AI/导出打印 |
| 8 | 本地健康 + 公网访问 | 绕过代理直连健康端点；公网域名入口鉴权 |
| 9 | 回退预演 | 实际恢复一份合并版备份并验证查询（非纸面） |
| 10 | 旧站只读留存 | 快照、最终切换点、下线决策待用户验收后定 |

## 6. Vercel 前端与分支后端候选

Vercel 项目从 GitHub 构建 `frontend/`，`main` 为 Production，其他分支/PR 为 Preview。
Production 的 `BACKEND_INTERNAL_URL` 指向正式持久化后端；Preview 必须单独配置
与分支接口/数据库版本一致的候选后端地址。SQLite、备份和上传文件保留在持久化主机，
不得提交数据库或将其放入 Vercel 的临时文件系统。

分支后端使用 `deploy/docker-compose.preview.yml`，项目名 `grade_analysis_preview`，
镜像名 `grade-analysis-preview-backend`，与正式 Compose 相互独立。启动前显式设置
`PREVIEW_IMAGE_TAG`、`PREVIEW_ENV_FILE`、`PREVIEW_DATA_DIR`、`PREVIEW_BACKUP_DIR`；
数据与备份路径必须是独立候选目录，禁止填正式挂载目录。凭据文件必须含独立
`APP_PASSWORD` 和 `SESSION_SECRET`，不入 Git。正式库使用 SQLite Backup API 取得
一致性副本，完整性及外键校验后在副本上迁移，不直接复制正在写入的数据库文件。

```bash
docker compose -f deploy/docker-compose.preview.yml config --quiet
docker compose -f deploy/docker-compose.preview.yml build backend
docker compose -f deploy/docker-compose.preview.yml up -d
curl http://127.0.0.1:8082/api/health
```

候选缺省仅监听 `127.0.0.1:8082`，`PUBLIC_HOST` 为空，所有业务请求均需登录。
NAS 与后端宿主不在同一台机器时，显式设置 `PREVIEW_BIND_HOST` 为宿主的 LAN
地址；NAS 的独立 HTTPS 主机反代到该地址及 `PREVIEW_API_PORT`。保留原有主线
反代规则；先备份反代配置，验证候选健康、TLS 和鉴权后再接入 Vercel。
公网独立反代与 TLS 就绪、未登录业务 API 返回 401 后，再把该地址设置到对应
Vercel Preview 分支。主线前端部署不代表 NAS 后端自动发布；合并含后端变更时，
仍需备份、迁移验证、后端发布及回退检查。PR 测试继续在 GitHub 的独立合成数据上运行。

## 7. 受控后端自动发布

`CI` 对 `main` 和 `codex/diagnosis-roadmap` 的 push 运行后端、前端和部署器测试。
仅本仓库的这两个分支在全部测试通过后产生 `backend-release` artifact（保留7天），
内容只有允许的后端构建源码、Git SHA 和 SHA256，不包含数据库、env、备份或部署凭据。
其他 PR 只测试，不产生可上线发布包。

实际后端宿主运行 `deploy/auto_backend.py`，使用已登录的 `gh` 获取当前分支SHA对应的
成功CI发布包。它校验仓库、workflow、事件、ref、SHA、摘要和解包路径，再本地构建
固定SHA镜像；不执行发布包里的部署脚本，不需要公网SSH或GitHub自托管runner。
`deploy/package_backend.py` 与部署器需一同安装；宿主配置在私有目录，包含明确的
compose路径、项目名、backend容器、数据目录及Caddy容器，不入Git。

每次更新先在独立一致性副本上运行迁移、鉴权和配置读取。通过后Caddy返回API维护503，
确认停写门生效后停止旧backend，再备份SQLite和相关文件。新backend启动及验证成功，
先持久化提交点再开放入口；失败则在入口开放前恢复原镜像和备份。`journal.json`
支持进程中断后的恢复；提交点之后不能再自动还原数据库，避免覆盖用户新写入。
恢复失败时保留维护入口，按journal与备份人工处理，不能绕过门直接恢复写入。

`/api/deployment` 由Caddy提供已部署SHA和ready状态，不返回业务数据或凭据。
Vercel构建先运行 `node scripts/wait-for-backend.cjs`，对这两个分支等待同SHA的
后端就绪，最长20分钟，超时停止新版前端发布。仅前端变化且构建指纹不变时，
后端验证后推进SHA标记，不重启容器。Caddy重启后控制器下一次检查会恢复标记。

Mac mini 用户态LaunchAgent每120秒运行一次控制器，要求机器在线、Docker正常，
当前用户已用gh登录且可以下载本仓库Actions artifact。Docker启动及机器唤醒后会
在后续检查中补发；测试失败不会上线。部署器运行时文件与日志均在私有宿主目录，
数据库备份仅保留本机，需按现场备份策略定期转存和清理。

本地验证：
```bash
python3 -m unittest discover -s deploy/tests -p 'test_*.py' -v
node --test deploy/tests/backend-gate.test.cjs
python3 deploy/auto_backend.py --config /absolute/private/config.json
```

安装后的控制器代码独立于开发工作树；修改它需完成部署器测试及合成Docker演练后
再更新宿主副本。主要网页入口使用Vercel，旧NAS入口所用的Docker前端保留原版本。
不同后端版本仍使用独立数据库。

## 8. 云端分支自动刷新正式数据

用户授权候选数据随正式数据刷新时，在宿主私有配置中只为候选目标添加：
```json
"data_refresh": {"source_target": "production", "interval_seconds": 900}
```
source_target 必须指向 main，接受方只能是 codex/diagnosis-roadmap。配置检查拒绝
相同或互相嵌套的数据目录、正式目标接受刷新、循环同步；无该配置的目标不刷新。
控制器继续每120秒运行，候选约每15分钟取得正式一致性快照核对，候选代码发布后
立即核对；机器离线时暂停，恢复运行后补查。数据库或原始上传文件有变化才替换，
无变化不重启候选。源库在线只读，不停止正式服务，快照期间之后的新写入下轮补取。

流程为正式快照→当前候选镜像在隔离副本迁移/鉴权验证→候选API维护503→停止候选
→备份原候选库与文件→从停写后的备份保留候选 `diagnosis_threshold_config` 单行
→安装迁移后的完整快照（含已提交WAL内容）→同镜像启动验证
→持久化提交点→恢复入口。失败/中断使用同一journal恢复原候选镜像和数据；
迁移预检失败不会触碰在线候选。source_digest、checked_at、refreshed_at与对应
release_sha 保存在候选state.json，status.json记录本轮刷新状态，不公开业务内容。

这是单向周期快照，分支试验录入不会写回主线。下一次正式快照发生变化时，候选里的
试验录入会被替换，原内容在私有backups留存；需要长期保留的正式录入应在主线完成。
全局进退步阈值是候选站本地设置，刷新时仅这张单行配置表保留候选状态（包括恢复默认后的空行）；其他业务表仍来自正式快照，不把候选设置写回主线。
独立APP_PASSWORD/SESSION_SECRET文件位于data目录外，刷新保持候选登录凭据独立。
PR仍仅使用合成数据，正式数据库、快照、备份和宿主配置始终不入Git。
