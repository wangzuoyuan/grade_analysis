"""Rank band imports (workspace_rank_band) and identity xueji code."""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if not conn.connection.driver_connection.in_transaction:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
    conn.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
    op.create_table("workspace_rank_band",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("data_domain", sa.String(16), nullable=False),
        sa.Column("academic_year_id", sa.Integer(), sa.ForeignKey("academic_year.id"), nullable=False),
        sa.Column("exam_name", sa.String(128), nullable=False),
        sa.Column("exam_event_id", sa.Integer(), sa.ForeignKey("workspace_exam_event.id", name="fk_workspacerankband_event")),
        sa.Column("exam_date", sa.Date(), nullable=True),
        sa.Column("grade", sa.Integer(), nullable=False),
        sa.Column("total_type", sa.String(32), nullable=False),
        sa.Column("class_num", sa.Integer(), nullable=False),
        sa.Column("teacher_name", sa.String(64), nullable=True),
        sa.Column("bands", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(255), nullable=False, server_default="import"),
        sa.Column("data_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.current_timestamp()),
        sa.CheckConstraint("data_domain IN ('homeroom', 'teaching')", name="ck_workspace_rank_band_domain"))
    # 自然键与班级均分表同构：考试按事件优先、无事件回退名称（0020 口径）。
    op.create_index("uq_workspace_rank_band_natural_key", "workspace_rank_band",
                    ["data_domain", "academic_year_id",
                     sa.text("CASE WHEN exam_event_id IS NULL THEN 'name:' || exam_name ELSE 'event:' || exam_event_id END"),
                     "grade", "total_type", "class_num"], unique=True)
    op.create_index("idx_workspace_rank_band_scope", "workspace_rank_band",
                    ["data_domain", "academic_year_id", "exam_name", "grade"])
    with op.batch_alter_table("ws_student_identity") as batch:
        batch.add_column(sa.Column("xueji", sa.Integer(), nullable=True))
    if conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("Rank band migration would break foreign keys")
    conn.exec_driver_sql("PRAGMA defer_foreign_keys=OFF")


def downgrade():
    conn = op.get_bind()
    if not conn.connection.driver_connection.in_transaction:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
    conn.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
    with op.batch_alter_table("ws_student_identity") as batch:
        batch.drop_column("xueji")
    op.drop_index("idx_workspace_rank_band_scope", table_name="workspace_rank_band")
    op.drop_index("uq_workspace_rank_band_natural_key", table_name="workspace_rank_band")
    op.drop_table("workspace_rank_band")
    if conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("Rank band rollback would break foreign keys")
    conn.exec_driver_sql("PRAGMA defer_foreign_keys=OFF")
