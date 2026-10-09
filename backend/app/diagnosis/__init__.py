"""P1 诊断模块（Wave B，契约 docs/diagnosis-roadmap/p1-contracts.md）。

- B1 特征层（本任务）：``thresholds.py``（阈值常量，§4）、
  ``features.py``（六类指标服务，§2）、``router.py``（/api/v1/{域}/diagnosis/* 端点）。
- B2 类型引擎（types.py / types_router.py）与 B3 变化分解
  （changes.py / changes_router.py）由并行任务并入同一包；类型判定与
  变化分解一律 import 本包 thresholds 的常量，不得自带副本。
"""

from app.diagnosis.features import class_features, student_features  # noqa: F401
