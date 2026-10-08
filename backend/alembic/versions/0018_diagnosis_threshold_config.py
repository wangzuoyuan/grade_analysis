"""独立保存全局诊断名次阈值，不改变长期名次分段。

Revision ID: 0018
Revises: 0017
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "diagnosis_threshold_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("direction_rank_change", sa.Integer(), nullable=False),
        sa.Column("streak_rank_change", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("diagnosis_threshold_config")
