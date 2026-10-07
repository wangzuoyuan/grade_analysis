# P1 诊断特征层接口契约（主控定稿，Wave B 各任务的唯一依据）

本文件由主控（ZCode / GLM-5.3）在 Wave B 派发前定稿。**五个任务并行开发，接口以本契约为准；契约即接口冻结，实现中发现契约不可行须在交付说明中列出，不得私自变更接口形状。**

## 0. 通用规则（全部任务适用）

1. 计算语义一律复用 `backend/app/analysis/definitions.py`（P0-A1 共享定义）：名次解析 `resolve_year_rank`、进退步 `progress_issue`、波动 `volatility_issue`、偏科 `subject_weakness_subjects`、段位 `band_flags/band_issues`、分箱 `percentile_bin/rank_bin`。禁止在诊断模块内重新实现任何已有共享定义。
2. 作用域隔离：所有服务函数接收已解析的 scope（复用 `app/api/chat_tools.py` 的 `resolve_scope_snapshot` 同源机制或其等价物）。班主任域=本班全科+总分；教学域=仅任教学科。缺总体指标时输出 `status: "not_computable"` 与原因，**绝不跨域取数**。
3. 缺失纪律（与 P0-A1 一致）：缺考不转 0、不进分母、不残留上次值；任何指标缺输入→该指标 `null` + `missing_reason`，聚合层如实标注有效样本数。
   - **score=NULL 行的残留指标一律不取**（2026-09-29）：缺考行内残留的名次/百分位/等级分绝不冒充成绩；当前水平 main3 增加专属 `missing_reason=main3_absent`（区别于 rank_missing=有分无名次）。
   - **连续进步跨缺考保留**（用户 2026-09-29 裁决）：缺考场次不进名次序列（本就跳过），300→200→缺考→100 判「连续进步 2 次」——缺考跳过、连击延续是产品口径，回归测试 test_wave_e_review_fixes 已钉死，改动须先过用户。
   - B3 学生分解 2026-09-29 增补 `totals` 全口径名次变化（主三门/五门/3+3 各读各的，教研结局聚合按所选 metric 取用；`main3` 既有键保留兼容）。
4. 作业口径：沿用既有例外登记语义与按天连缺算法（班主任按学科、教学不分作业种类）；无有效批次/分母未知→不给提交率，只给计数类特征。
5. 所有输出携带 `calc_version: "p1-v1"`；阈值常量集中在 `backend/app/diagnosis/thresholds.py`（本契约新建），命名与默认值见 §4，改动阈值不改版本号。
6. 测试一律合成姓名（沿用 tests 现有合成风格）；真实数据只允许主控冒烟使用。
7. 时间窗口（天）一律按自然日回溯，窗口边界含当日；「考前」窗口不含考后作业（P2 用，本波仅预留字段）。

## 1. 文件归属（并行边界，越界即违规）

| 任务 | 独占文件 | 附带测试 |
|---|---|---|
| B1 特征层 | `app/diagnosis/__init__.py`、`app/diagnosis/thresholds.py`、`app/diagnosis/features.py`、`app/diagnosis/router.py`、`app/api/__init__.py`（仅追加 include 一行） | `backend/tests/v1/test_p1_b1_features.py` |
| B2 类型引擎 | `app/diagnosis/types.py`、`app/diagnosis/types_router.py`、`app/api/__init__.py`（仅追加 include 一行） | `backend/tests/v1/test_p1_b2_types.py` |
| B3 变化分解 | `app/diagnosis/changes.py`、`app/diagnosis/changes_router.py`、`app/api/__init__.py`（仅追加 include 一行） | `backend/tests/v1/test_p1_b3_changes.py` |
| B4 纵向切片 | `frontend/src/components/student/DiagnosisCard.tsx`、`frontend/src/app/homeroom/page.tsx` 与学生页装配点、`app/api/chat_tools.py`（仅新增 1 个 WsToolSpec） | `frontend/tests/diagnosis-card.test.mjs`、`backend/tests/v1/test_p1_b4_tool.py` |
| B5 旧判定收编 | `app/chat/tools.py`（仅判定逻辑段） | 既有测试调整 + `backend/tests/test_chat_tools_union.py` 补例 |

`app/api/__init__.py` 会被 B1/B2/B3 各追加一行 include —— 预期合并时相邻行冲突由主控解决，各任务自行运行全量测试时其余两行的缺失不影响自身。

## 2. B1 特征层：`features.py` 与端点

服务函数（B2/B3/B4 依赖此签名，冻结）：

```python
def student_features(db, scope, person_id: int, academic_year_id: int) -> dict
def class_features(db, scope, academic_year_id: int) -> dict  # 每生 features 的列表 + 班级级汇总
```

`HTTP`：`GET /api/v1/{mode 域前缀}/diagnosis/features?person_id=&academic_year_id=`（域前缀与现有 v1 路由一致：homeroom / teaching）。

返回 JSON（单生）：

```jsonc
{
  "person_id": 9, "calc_version": "p1-v1",
  "indicators": {
    "current_level": {          // 当前水平（最近一场有数据考试）
      "exam_name": "…", "as_of": "2026-05-01",
      "main3": {"rank": 123, "percentile": 0.21, "basis": "school", "missing_reason": null},
      "subjects": [{"subject": "语文", "percentile": 0.34, "grade_score": null}],
      "bands": {"high_score": false, "critical": false, "weak": false}
    },
    "trend": {                   // 趋势（学年内）
      // rank_change = 本次名次 − 上次名次（路线图全局规则：负值 = 名次数值变小 = 相对位置上升）
      "last_change": {"from": "…", "to": "…", "rank_change": -12},
      "direction_recent": "进步|退步|持平|数据不足",   // 近 2-3 次
      "streak": {"kind": "进步|退步|null", "count": 2},
      "long_term": "上升|下降|平稳|数据不足",
      "valid_exam_count": 5
    },
    "stability": {               // 稳定性（主三门百分位）
      "window_n": 5, "statistic": "range",     // 极差；版本化常量
      "value": 0.18, "label": "稳定|中等波动|高波动|数据不足",
      "min_points": 3
    },
    "imbalance": {               // 偏科（同场可比）
      // diff_pct_point = 单科百分位 − 总体百分位（百分点）；正值 = 该科弱于总体（路线图公式）
      "subjects": [{"subject": "英语", "diff_pct_point": 22.0, "consecutive_exams": 3}],
      "severe": ["英语"]         // 复用 subject_weakness_subjects 口径
    },
    "homework_behavior": {       // 作业行为（7/30 天窗口）
      "missing_7d": 2, "missing_30d": 5,
      "current_streak_days": 3,  // 按天连缺（复用既有算法）
      "trend": "恶化|改善|持平|无数据",
      "forgot_30d": 1, "negative_notes_30d": 0,
      "missing_by_subject": {"数学": 3}
    },
    "teacher_attention": {       // 教师关注（follow_up/note 域内）
      "last_contact": {"kind": "谈话|家访|家长沟通|null", "days_ago": 12},
      "open_follow_ups": 1, "done_follow_ups_30d": 2
    }
  },
  "data_quality": {"valid_exam_count": 5, "notes": []}
}
```

实现要点：`current_level.main3` 名次走 `resolve_year_rank`；百分位与名次同点输出；`subjects.grade_score` 仅选考等级分；`homework_behavior` 从既有作业服务/查询取数，不改作业代码；`teacher_attention` 从 `follow_up`/`student_note` 读，空数据如实输出 null。

## 3. B2 类型引擎：`types.py`

```python
def classify_student(features: dict) -> dict   # 输入= B1 单生 features JSON
```

`HTTP`：`GET /api/v1/{域}/diagnosis/types?person_id=&academic_year_id=`。

输出：

```jsonc
{
  "person_id": 9, "calc_version": "p1-v1",
  "main_type": "持续进步型",           // 或 null（数据不足不强行归类）
  "secondary_tags": ["作业风险"],
  "evidence": [{"type": "持续进步型", "basis": "近 3 场主三门名次 -12/-15/-9，连续进步 3 次"},
               {"type": "作业风险", "basis": "近 30 天缺交 5 次，当前连缺 3 天"}],
  "classification_status": "classified|insufficient_data"
}
```

11 类型判定规则（输入全为 B1 字段；阈值见 §4；规则按序首个命中为主类型，其余命中为次标签）：

1. **稳定优秀型**：最近一场 `bands.high_score=true` 且 `stability.label∈{稳定}` 且无严重偏科且近 3 次 `direction_recent≠退步`
2. **高位波动型**：`bands.high_score=true` 且 `stability.label=高波动`
3. **持续进步型**：`trend.streak.kind=进步` 且 `streak.count≥2`
4. **短期下滑型**：`direction_recent=退步` 且 `streak.count<2`
5. **持续下滑型**：`trend.streak.kind=退步` 且 `streak.count≥2`
6. **稳定临界型**：`bands.critical=true` 且 `stability.label=稳定` 且 `direction_recent∈{持平,数据不足}`
7. **临界上升型**：`bands.critical=true` 且 `direction_recent=进步`
8. **临界下滑型**：`bands.critical=true` 且 `direction_recent=退步`
9. **明显偏科型**：`imbalance.severe` 非空（连续 ≥2 场）
10. **作业风险型**（仅次标签或数据充分可主）：`homework_behavior.missing_30d≥3` 或 `current_streak_days≥2`——描述已观察到的作业异常，**不预测成绩**
11. **综合风险型**：命中≥2 个风险面（{5,8}∪`bands.weak`∪`imbalance.severe`∪作业风险）

顺序：3/4/5（趋势类）→ 1/2（高位类）→ 6/7/8（临界类）→ 9 → 11 → 10；`valid_exam_count<2` → `insufficient_data`。规则可解释：每条 evidence 必须引用具体 B1 字段值。

## 4. 阈值常量（`diagnosis/thresholds.py`，B1 建、B2/B3 import）

```python
STABILITY_WINDOW_N = 5            # 稳定性窗口场次
STABILITY_MIN_POINTS = 3
STABILITY_RANGE_LABELS = ((0.10, "稳定"), (0.25, "中等波动"))  # 极差>0.25→高波动
TREND_DIRECTION_MIN_CHANGE = 20   # 名次变化≥20 名才算 进步/退步 方向
HOMEWORK_RISK_30D = 3             # 30 天缺交次数阈值
HOMEWORK_RISK_STREAK_DAYS = 2     # 当前连缺天数阈值
IMBALANCE_MIN_CONSECUTIVE = 2     # 偏科连续场数
CHANGE_DECOMPOSITION_TOP_N = 3    # B3 主要变化科目数
CLASS_GROUP_MIN_SIZE = 3          # B3 班级分组最小样本
```

## 5. B3 变化分解：`changes.py`

```python
def student_change decomposition(db, scope, person_id, from_exam, to_exam) -> dict
def class_change_decomposition(db, scope, from_exam, to_exam) -> dict
```

`HTTP`：`GET /api/v1/{域}/diagnosis/changes/student?person_id=&from_exam=&to_exam=`；`GET /api/v1/{域}/diagnosis/changes/class?from_exam=&to_exam=`。

学生分解：按相同考试对列出各科 `percentile_change`（百分点，本次−上次，**负值=相对位置上升**，文案必须带方向解释）、主三门名次变化、等级分变化；输出 `top_movers`（|变化| 前 N 科）+ 每科缺失说明；禁止因果断言字段。

班级分解：固定可比集合=两场都有效的学生交集；按基期 `percentile_bin`/学校段位分组；组内输出可加指标（总分变化均值）与名次类指标的**仅变化描述**（不做贡献分解）；每组可下钻学生名单（`person_id` 列表，前端再取详情）；显式 `excluded` 计数（缺考/转入/转出）与 `comparable_n`。

## 6. B4 纵向切片：三处同源

同一数据源 = B1/B2 的 HTTP 端点。

1. 前端学生页新卡 `DiagnosisCard`：主类型+次标签+证据+六类指标摘要（数据不足如实显示），风格随现有学生档案卡。
2. 班主任首页卡片：班级 `types` 分布 top + 优先关注（综合风险/持续下滑/临界下滑前若干，附 evidence 一句）。
3. AI 工具：`app/api/chat_tools.py` 新增 `WsToolSpec` `get_diagnosis_summary`（参数：person_id 可选、academic_year_id 可选；返回单生或班级汇总；描述写明只读与口径版本）。**新工具会自动进入 MCP 目录（P0-A2 的注册表机制）——测试需断言 MCP 目录含第 25 个工具且同名同源。**

前端契约测试沿用 `readFileSync+assert` 模式；后端测试断言「学生页端点、首页端点、AI 工具」三者对同一 person_id 返回的 features/types 数据**逐字段一致**。

## 7. B5 旧判定收编：`app/chat/tools.py`

范围仅限判定逻辑：`classify_trend`（约 :895）、focus_list 的 999999 哨兵（约 :668）、band-trend 同型计数（约 :1162 起）——改为委托 `app/analysis/definitions.py` 的共享实现，保持函数对外行为与返回结构不变（数值口径已是同源，本次收编代码路径）；既有测试全部保持通过，另在 `test_chat_tools_union.py` 补「旧工具判定=definitions 直算」等价测试。禁止改 `app/api/chat_tools.py` 与 `mcp_server.py`。

## 8. 交付与门禁（各任务相同）

- 工作树内全量 `backend/tests -q` 通过（B4 另加前端 `npm run test:ui`）；新增测试覆盖：缺考、缺科、百分位方向与单位、同分、跨学年、范围隔离（教学域不读全科）、稀疏历史。
- 提交信息 `feat(p1-bN): <摘要>`；返回 TaskResult（同 Wave A 结构）。
- 本契约文件路径：`docs/diagnosis-roadmap/p1-contracts.md`；实现与契约的任何偏差写入 `interfaceImpacts`。


## 9. Wave B 合并裁决（主控 2026-09-29）

- `rank_change`（trend.last_change）导出符号 = 本次−上次（负=进步），内部计算保持共享口径（上次−本次）不变——features.py 已按此实现。
- `diff_pct_point` = 单科百分位 − 总体百分位（正值=该科更弱），B1 实现与路线图公式一致；§2 示例原 -22.0 系主控笔误，已改为 +22.0。
- B2 types 端点已接线 B1.student_features（临时最小提取器删除）；scope 双载体（WorkspaceContext / 快照 dict）经 _ScopeShim 等价（as_of 字符串自动转 date），与 changes.py 先例一致。
- B4 班级路径 classify 入参修正为 row.features（wrapper 形状），兼容裸 dict。
