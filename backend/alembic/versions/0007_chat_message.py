"""P6 服务端会话历史表 chat_message（契约 p6-ai-mcp v2 §0.1 Q03）。

只 CREATE 新表；downgrade 逆序 DROP。外键在迁移连接真实开启。

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id", sa.Integer(), sa.ForeignKey("chat_session.id"), nullable=False
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_events_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_chat_message_session", "chat_message", ["session_id"])


def downgrade() -> None:
    op.drop_index("idx_chat_message_session", table_name="chat_message")
    op.drop_table("chat_message")
