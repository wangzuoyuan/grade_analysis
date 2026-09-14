"""P3 §1.3：score_fact 增列 grade_score Float NULL。

等级分/赋分列（E03）：原始分仍在 score，缺考两者皆 NULL；teaching
上传含等级分列时一并入库，homeroom 含则同规则。读取响应（ScoreRow/
ProfileExam）增加可选 grade_score 属 API 层，本迁移只动表结构。

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("score_fact") as batch_op:
        batch_op.add_column(sa.Column("grade_score", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("score_fact") as batch_op:
        batch_op.drop_column("grade_score")
