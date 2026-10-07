# P2 行动、报告与复查接口契约（主控定稿，Wave C 四任务的唯一依据）

承接 `p1-contracts.md`（§0 通用规则继续适用：共享定义、作用域隔离、缺失纪律、合成测试、`calc_version`）。
本波版本号 `p2-v1`。符号与单位沿用 P1 §9 裁决：变化值=本次−上次（负=改善）；diff_pct_point=单科−总体（正=更弱）。

## 1. 文件归属（并行边界）

| 任务 | 独占文件（backend） | 独占文件（frontend） | 附带测试 |
|---|---|---|---|
| C1 相关性 | `app/diagnosis/correlation.py`、`correlation_router.py`、`app/api/__init__.py`（仅追加 include）、`app/api/chat_tools.py`（仅 get_homework_correlation 内部重接） | 成绩分析页相关性卡（定位后独占该组件文件） | `tests/v1/test_p2_c1_correlation.py` 等 |
| C2 行动首页 | `app/diagnosis/action.py`、`action_router.py`、`app/api/__init__.py`（仅追加 include） | `src/app/homeroom/page.tsx` 及其行动卡子组件 | `tests/v1/test_p2_c2_action.py` + 前端契约 |
| C3 诊断报告 | `app/diagnosis/report.py`、`report_router.py`、`app/api/__init__.py`（仅追加 include） | 诊断版报告页/打印组件（学生报告区域） | `tests/v1/test_p2_c3_report.py` + 前端契约 |
| C4 干预复查 | `app/diagnosis/review.py`、`review_router.py`、notes 模型/路由扩展 + Alembic 迁移、`app/api/__init__.py`（仅追加 include） | 学生档案干预卡组件 | `tests/v2 见下` |

`app/api/__init__.py` 四行 include 邻近冲突由主控合并解决。禁止改 `mcp_server.py`、`analysis/definitions.py`、B1/B2/B3 的既有文件（消费其服务函数可以）。

## 2. C1 作业×成绩相关性（Pearson + Spearman）

服务函数（冻结）：

```python
def exam_homework_correlation(db, scope, exam_name, window_days=14, metric="total:主三门") -> dict  # 2026-09-29 默认与 AI 工具/前端统一（原 subject:语文）
```

`HTTP`：`GET /api/v1/{域}/diagnosis/correlation?exam_name=&window_days=14|30&metric=`

计算规则：

1. 样本构造：该场考试有有效成绩的学生 × 其在 `[exam_date − window_days, exam_date)` 窗口内**有有效批次**的作业提交率（缺交数/应交数；例外登记口径，忘带/请假/出勤不计入分子分母；无有效批次或分母未知 → 该生不入样本，绝不默认已交）。
2. 双指标：Pearson r 与 Spearman rho（自实现或 scipy——requirements-lock 已有 scipy 则用之，无则纯 Python 实现，二选一并在 docstring 声明）。x=提交率（0–1），y=成绩指标（单科分数或总分，metric 沿用 definitions.metric_meta）。
3. 分层：按该场学校段位（high/critical/weak，AnalysisConfig）+ 全班整体；每层输出 n、r、rho、不可计算原因（n<8 / 零方差 / 分母未知占比>50%）。
4. 响应必含：`window_days`、窗口起止日、`sample`（各层 n 与排除数）、`note`（方向与指标含义解释 +「相关性不构成因果或提分保证」）、`calc_version: "p2-v1"`。
5. 重接同源：`app/api/chat_tools.py` 的 `get_homework_correlation` handler 改调本模块（对外参数与错误语义保持，返回体可加新字段不删旧键）；前端成绩分析页相关性卡的取数改走新端点（展示保持既有风格，可加 rho 与分层）。

## 3. C2 教师行动首页

`GET /api/v1/{域}/diagnosis/action-summary?academic_year_id=&class_id=`

响应（冻结形状）：

```jsonc
{
  "calc_version": "p2-v1", "as_of": "2026-09-29",
  "priority_persons": [           // 优先关注（有理由、可追溯）
    {"person_id": 9, "reasons": ["综合风险型", "连续缺交 3 天"],
     "evidence_ref": {"types": true, "features": true}}   // 详情走既有端点
  ],
  "sections": {
    "trend_changes": {"improving_n": 3, "declining_n": 2},
    "structure": {"band_counts": {"high_score": 5, "critical": 8, "weak": 4}},
    "homework": {"risk_n": 3, "missing_30d_total": 21},
    "follow_ups": {"open_n": 4, "due_this_week": 1}      // 只读既有 follow_up 字段
  }
}
```

排序：综合风险型 > 持续下滑型 > 临界下滑型 > 作业风险（次标签）> 短期下滑型；同级按 evidence 数与最近变化幅度。摘要全部从 B1 `class_features` + B2 `classify_student` 生成（同源，禁止另算）；前端首页改造：优先关注列表置顶（姓名+理由+直达学生页），随后趋势/结构/作业/待办摘要；数据缺失态如实显示。

## 4. C3 诊断版学生报告

`GET /api/v1/{域}/diagnosis/report?person_id=&academic_year_id=&exam_name=<可选，缺省最近一场>`

结构：`learning_state`（类型+趋势+稳定性摘要，出自 B1/B2）、`subject_performance`（各科百分位与偏科）、`behavior`（作业行为窗口数据）、`teacher_observations`（档案中「观察/谈话」类摘录，遵守可见范围）、`suggestions`（1–3 条，由 evidence 生成的「值得关注事项」措辞，标注 `generated: true`；教师可编辑字段在前端本地，不入库）。事实/规则判断/建议三层明确分节标注；打印友好（沿用现有报告打印样式）；双工作台数据范围与 P1 一致；事实版报告保留不动。

## 5. C4 轻量干预与自动复查

1. follow_up 扩展（Alembic 迁移 + 模型）：既有 `follow_up`/`follow_up_done` 之上新增可选列——`problem`（问题）、`subject_scope`（任教学科）、`measures`（措施）、`target_metric`（目标指标，如 `total:主三门`）、`baseline_value`（基线值+口径）、`start_date`、`review_date`、`status`（open/done/dismissed）。旧数据完全兼容（全可空）；避免重复录入（创建时提示同人同科未关闭干预）。
2. 自动复查对照 `GET /api/v1/{域}/diagnosis/review-contrast?follow_up_id=`：到达 `review_date` **或** 该生在 start_date 之后有新可比考试时返回 `{baseline, latest, change, note}`；缺考/无可比考试 → `{status: "pending", reason}`，绝不自动标成功/失败。
3. 前端：学生档案干预卡（创建/查看/关闭，字段如上）+ 到期提醒入口（首页由 C2 的 follow_ups.due_this_week 呈现，C4 不改首页）。
4. 迁移纪律：Alembic 新版本文件 + downgrade；不碰旧列语义；`follow_up_done` 关闭路径回归。

## 6. 交付与门禁

同 P1 §8：各任务全量 backend/tests 通过（有前端改动另加 `npm run test:ui`）；测试覆盖契约 §8（P1）边界 + 本波新增：窗口不含考后作业、分层小样本不可计算、零方差、建议不越界成因果表述、迁移升降级、复查缺考 pending。提交信息 `feat(p2-cN): <摘要>`；偏差写 `interfaceImpacts`。

## 7. Wave C 合并裁决（主控 2026-09-29）

- **考试日期月精度回退（C1）**：真实库 score_fact.exam_date 全空、source_exam_date 为 "YYYY-MM" 月文本。相关性窗口锚点回退为「该月最后一天」，响应 caveats 显式标注 `exam_date_month_precision`；日精度数据出现后自动回到精确窗口。完全无日期 → 维持 not_computable。
- **review-contrast 崩溃修复（C4）**：`_is_comparable_after` 对月精度字符串 fromisoformat 抛 ValueError（真实数据 500）→ 设防为「不可解析日期不构成可比证据」，补回归测试。
- **真实数据上相关性 not_computable 属诚实纪律**：旧系统导入的作业批次 expected_members_json 均为空数组（无应交名单 → 无分母），旧实现同样整批剔除；应用内新录入作业自带应交名单后相关性逐步可算。
- C2 due_this_week 2026-09-29 起按计划复查日（review_date 缺省回落档案日期）落在近 7 天窗口计数；C3 建议确认上限 3 条由 items 承载。
- C4 基线锚点（2026-09-29）：自动捕获只取 start_date（缺省回落档案日期）**之前**的可比点，补录过去开始的干预不取干预后成绩；PATCH 改 target_metric / 改期致基线失效时自动按新口径重取，复查对照另对基线 metric 不符兜底 pending/baseline_incomparable。
