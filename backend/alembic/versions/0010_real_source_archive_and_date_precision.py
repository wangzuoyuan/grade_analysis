"""P8 real-source migration support: date precision and durable archive.

Revision ID: 0010
Revises: 0009
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    with op.batch_alter_table("score_fact") as batch:
        batch.add_column(sa.Column("source_exam_date", sa.String(length=16), nullable=True))
        # Server default keeps raw SQL writers and backup/restore paths valid. Existing
        # complete dates are day precision; NULL dates are explicitly repaired below.
        batch.add_column(sa.Column("exam_date_precision", sa.String(length=8), nullable=False, server_default="day"))
    op.execute("UPDATE score_fact SET source_exam_date = CAST(exam_date AS TEXT) WHERE exam_date IS NOT NULL")
    op.execute("UPDATE score_fact SET exam_date_precision = 'unknown' WHERE exam_date IS NULL")
    op.create_table(
        "source_archive_record",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("source_table", sa.String(length=64), nullable=False),
        sa.Column("source_pk", sa.String(length=64), nullable=False),
        sa.Column("data_domain", sa.String(length=16), nullable=False),
        sa.Column("archive_reason", sa.String(length=128), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("data_domain IN ('homeroom', 'teaching')", name="ck_archive_domain"),
        sa.UniqueConstraint("source_fingerprint", "source_table", "source_pk", name="uq_archive_origin"),
    )
    op.create_index("idx_archive_origin", "source_archive_record", ["source_fingerprint", "source_table"])

def downgrade() -> None:
    op.drop_index("idx_archive_origin", table_name="source_archive_record")
    op.drop_table("source_archive_record")
    with op.batch_alter_table("score_fact") as batch:
        batch.drop_column("exam_date_precision")
        batch.drop_column("source_exam_date")
