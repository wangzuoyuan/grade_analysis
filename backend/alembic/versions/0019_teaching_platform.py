"""教学单科考试与独立官方质量参照。"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("teaching_exam_scope",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("academic_year_id", sa.Integer(), sa.ForeignKey("academic_year.id"), nullable=False),
        sa.Column("grade", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(32), nullable=False),
        sa.Column("exam_name", sa.String(128), nullable=False),
        sa.Column("exam_date", sa.Date(), nullable=False),
        sa.Column("class_snapshot", sa.JSON(), nullable=False),
        sa.Column("aggregates", sa.JSON(), nullable=False),
        sa.Column("data_revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("academic_year_id", "grade", "subject", "exam_name", name="uq_teaching_exam_scope"),
        sa.CheckConstraint("grade IN (1,2,3)", name="ck_teaching_exam_grade"))
    op.create_table("teaching_comparison_roster",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("academic_year_id", sa.Integer(), sa.ForeignKey("academic_year.id"), nullable=False),
        sa.Column("grade", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_exam_date", sa.Date(), nullable=False),
        sa.Column("classes", sa.JSON(), nullable=False),
        sa.UniqueConstraint("academic_year_id", "grade", "subject", "version", name="uq_teaching_roster_version"))
    op.create_table("teaching_class_quality",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exam_id", sa.Integer(), sa.ForeignKey("teaching_exam_scope.id"), nullable=False),
        sa.Column("class_kind", sa.String(16), nullable=False),
        sa.Column("class_label", sa.String(64, collation="BINARY"), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("source", sa.JSON(), nullable=False),
        sa.Column("data_revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("exam_id", "class_kind", "class_label", name="uq_teaching_quality"),
        sa.CheckConstraint("class_kind IN ('teaching','administrative')", name="ck_teaching_quality_kind"))
    op.add_column("teaching_class", sa.Column("grade", sa.Integer(), nullable=True))
    with op.batch_alter_table("score_fact") as batch:
        batch.add_column(sa.Column("teaching_exam_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("field_sources", sa.JSON(), nullable=True))
        batch.create_foreign_key("fk_score_teaching_exam", "teaching_exam_scope", ["teaching_exam_id"], ["id"])


def downgrade():
    with op.batch_alter_table("score_fact") as batch:
        batch.drop_constraint("fk_score_teaching_exam", type_="foreignkey")
        batch.drop_column("field_sources")
        batch.drop_column("teaching_exam_id")
    op.drop_column("teaching_class", "grade")
    op.drop_table("teaching_class_quality")
    op.drop_table("teaching_comparison_roster")
    op.drop_table("teaching_exam_scope")
