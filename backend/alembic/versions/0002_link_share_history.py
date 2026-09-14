"""P1-R3：homeroom_teaching_link 增加 share_history_from（显式历史授权日期）。

契约 §1.2.2：考试日期 < max(valid_from, share_history_from ?? valid_from)
的事实不得跨域共享；NULL 等于 valid_from。SQLite 上用 batch_alter_table
走安全的 ADD/DROP COLUMN 路径（表已可能有存量数据）。

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("homeroom_teaching_link") as batch:
        batch.add_column(sa.Column("share_history_from", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("homeroom_teaching_link") as batch:
        batch.drop_column("share_history_from")
