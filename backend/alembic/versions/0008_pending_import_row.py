"""P7 Q04 迁移待核实区表 pending_import_row（隔离的真实持久层）。

被隔离的来源行（撞号同名/占位学号/未知学年/任教学科未配置等）原行
完整暂存于此，绝不进入业务表；确认导入后由迁移管线删除登记。
只 CREATE 新表；downgrade 逆序 DROP。

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pending_import_row",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("source_table", sa.String(length=64), nullable=False),
        sa.Column("source_pk", sa.String(length=64), nullable=False),
        sa.Column("data_domain", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=128), nullable=False),
        sa.Column("raw_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "data_domain IN ('homeroom', 'teaching')",
            name="ck_pending_import_domain",
        ),
    )
    op.create_index(
        "idx_pending_import_origin",
        "pending_import_row",
        ["source_fingerprint", "source_table", "source_pk"],
    )


def downgrade() -> None:
    op.drop_index("idx_pending_import_origin", table_name="pending_import_row")
    op.drop_table("pending_import_row")
