"""P2-C4 迁移 0017：ws_student_note 干预扩展列（follow_up 轻量干预）。

契约 docs/diagnosis-roadmap/p2-contracts.md §5.1：既有 follow_up/follow_up_done
之上新增可选列——problem / subject_scope / measures / target_metric /
baseline_value / start_date / review_date / status（open/done/dismissed）。

迁移纪律（契约 §5.4）：
- 全部列可空 → 旧数据完全兼容（旧行 status 为 NULL = 非干预档案，语义不变）；
- 不碰旧列语义：只用 ``ALTER TABLE ADD COLUMN``（SQLite 无需表重建，
  绝不触发 batch 重建，ck_ws_note_domain 等既有约束/索引原样保留）；
- downgrade 逆序 DROP COLUMN，旧列与既有数据不动。
- idx_ws_note_status 与 app.db.workspace_models 模型同步（create_all 新库
  与迁移旧库 schema 一致）；status 取值合法性由 API 层校验，不设 CHECK。

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (列名, 类型)；upgrade 按声明序 ADD，downgrade 严格逆序 DROP。
_INTERVENTION_COLUMNS: Sequence[tuple[str, sa.types.TypeEngine]] = (
    ("problem", sa.Text()),
    ("subject_scope", sa.String(length=32)),
    ("measures", sa.Text()),
    ("target_metric", sa.String(length=64)),
    ("baseline_value", sa.JSON()),
    ("start_date", sa.Date()),
    ("review_date", sa.Date()),
    ("status", sa.String(length=16)),
)


def upgrade() -> None:
    with op.batch_alter_table("ws_student_note") as batch:
        for name, col_type in _INTERVENTION_COLUMNS:
            batch.add_column(sa.Column(name, col_type, nullable=True))
    op.create_index("idx_ws_note_status", "ws_student_note", ["status"])


def downgrade() -> None:
    op.drop_index("idx_ws_note_status", table_name="ws_student_note")
    with op.batch_alter_table("ws_student_note") as batch:
        for name, _col_type in reversed(_INTERVENTION_COLUMNS):
            batch.drop_column(name)
