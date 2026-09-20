"""Keep teaching-class history while allowing classes to be deactivated."""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    # SQLite batch mode重建表会在真实库已有 teaching_class_member 外键时
    # 因 DROP TABLE 失败。ADD COLUMN 支持列级 CHECK，且无需搬表。
    op.add_column(
        "teaching_class",
        sa.Column(
            "status",
            sa.String(16),
            sa.CheckConstraint(
                "status IN ('active', 'inactive')",
                name="ck_teaching_class_status",
            ),
            nullable=False,
            server_default="active",
        ),
    )


def downgrade():
    with op.batch_alter_table("teaching_class") as batch:
        batch.drop_constraint("ck_teaching_class_status", type_="check")
        batch.drop_column("status")
