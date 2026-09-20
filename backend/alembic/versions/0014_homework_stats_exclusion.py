"""Homework statistics exclusion table (ADR-023).

Row presence marks a student excluded from homework aggregates (dashboard,
rankings, warnings) for one class; correlation, per-student timelines and
batch details keep their records. Data-only semantics live in app code —
this migration only creates the table.
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "homework_stats_exclusion",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("data_domain", sa.String(16), nullable=False),
        sa.Column("class_ref_id", sa.Integer(), nullable=False),
        sa.Column(
            "identity_id",
            sa.Integer(),
            sa.ForeignKey("ws_student_identity.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "data_domain IN ('homeroom', 'teaching')",
            name="ck_hw_stats_exclusion_domain",
        ),
        sa.UniqueConstraint(
            "data_domain", "class_ref_id", "identity_id",
            name="uq_hw_stats_exclusion",
        ),
    )
    op.create_index(
        "idx_hw_stats_exclusion_class",
        "homework_stats_exclusion",
        ["data_domain", "class_ref_id"],
    )


def downgrade():
    op.drop_index(
        "idx_hw_stats_exclusion_class", table_name="homework_stats_exclusion"
    )
    op.drop_table("homework_stats_exclusion")
