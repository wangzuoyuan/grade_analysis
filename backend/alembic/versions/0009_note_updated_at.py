"""Q06 迁移 0009：ws_student_note 补 updated_at（V03 回退增量完整性）。

增量导出按 updated_at 越界发现"已存在行的修改"；ws_student_note 是
可修改业务表（PATCH 档案内容/跟进状态）却只有 created_at，导致备份点
之后的档案修改静默漏出增量清单。SQLite 不支持单列 ADD COLUMN 以外的
受限变更时需要表重建，统一走 batch_alter_table；downgrade 逆序删列。

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ws_student_note") as batch:
        batch.add_column(
            sa.Column("updated_at", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("ws_student_note") as batch:
        batch.drop_column("updated_at")
