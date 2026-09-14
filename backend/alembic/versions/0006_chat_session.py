"""P6 AI 会话表：chat_session（不可变作用域快照，A02 红线）。

只 CREATE 新表；downgrade DROP。骨架由集成者预建，端点实现属 P6-BE 波次。

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_session",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type", sa.String(length=8), nullable=False),
        sa.Column("scope_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("type IN ('chat', 'mcp')", name="ck_chat_session_type"),
        sa.CheckConstraint("status IN ('open', 'closed')", name="ck_chat_session_status"),
    )


def downgrade() -> None:
    op.drop_table("chat_session")
