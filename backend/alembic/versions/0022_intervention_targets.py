"""Independent metrics for an intervention; preserve legacy baselines."""
import sqlalchemy as sa
from alembic import op
revision = "0022"
down_revision = "0021"
branch_labels = depends_on = None

def upgrade():
    table = op.create_table("ws_intervention_target",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("note_id", sa.Integer(), sa.ForeignKey("ws_student_note.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("baseline_value", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("note_id", "metric", name="uq_intervention_target_metric"),
        sa.CheckConstraint("status IN ('open','done','dismissed')", name="ck_intervention_target_status"))
    conn = op.get_bind()
    notes = sa.table("ws_student_note", sa.column("id"), sa.column("target_metric"),
        sa.column("baseline_value", sa.JSON()), sa.column("status"))
    for row in conn.execute(sa.select(notes).where(notes.c.target_metric.is_not(None),
            notes.c.status.in_(["open", "done", "dismissed"]))).mappings():
        if row["target_metric"].strip():
            conn.execute(table.insert().values(note_id=row["id"], metric=row["target_metric"],
                baseline_value=row["baseline_value"], status=row["status"]))

def downgrade():
    op.drop_table("ws_intervention_target")
