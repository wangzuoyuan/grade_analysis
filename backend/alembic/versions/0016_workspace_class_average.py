"""Persist imported grade-wide class-average workbooks."""

import sqlalchemy as sa
from alembic import op


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workspace_class_average",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("data_domain", sa.String(length=16), nullable=False),
        sa.Column("academic_year_id", sa.Integer(), nullable=False),
        sa.Column("exam_name", sa.String(length=128), nullable=False),
        sa.Column("exam_date", sa.Date(), nullable=True),
        sa.Column("grade", sa.Integer(), nullable=False),
        sa.Column("class_type", sa.String(length=32), nullable=True),
        sa.Column("class_num", sa.Integer(), nullable=False),
        sa.Column("teacher_name", sa.String(length=64), nullable=True),
        sa.Column("subject_averages", sa.JSON(), nullable=False),
        sa.Column("total_averages", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("data_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "data_domain IN ('homeroom', 'teaching')",
            name="ck_workspace_class_average_domain",
        ),
        sa.ForeignKeyConstraint(["academic_year_id"], ["academic_year.id"]),
        sa.UniqueConstraint(
            "data_domain", "academic_year_id", "exam_name", "grade", "class_num",
            name="uq_workspace_class_average_natural_key",
        ),
    )
    op.create_index(
        "idx_workspace_class_average_scope",
        "workspace_class_average",
        ["data_domain", "academic_year_id", "exam_name", "grade"],
    )

    # 旧班主任库已经保存过的班级均分表同步进入工作台表。学年只从同名
    # homeroom score_fact 的唯一学年反查；无法唯一归属的历史行保持原表，
    # 不猜学年、不跨域复制。
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if not {"class_average", "exam", "score_fact"}.issubset(tables):
        return
    legacy_rows = bind.execute(
        sa.text(
            "SELECT e.name AS exam_name,e.grade,e.exam_date,c.class_type,c.class_num,"
            "c.teacher_name,c.subject_averages,c.total_averages "
            "FROM class_average c JOIN exam e ON e.id=c.exam_id"
        )
    ).mappings()
    for row in legacy_rows:
        years = bind.execute(
            sa.text(
                "SELECT DISTINCT academic_year_id FROM score_fact "
                "WHERE data_domain='homeroom' AND exam_name=:exam_name"
            ),
            {"exam_name": row["exam_name"]},
        ).scalars().all()
        if len(years) != 1:
            continue
        raw_date = row["exam_date"]
        exam_date = (
            f"{raw_date}-01" if raw_date and len(str(raw_date)) == 7 else raw_date
        )
        bind.execute(
            sa.text(
                "INSERT OR IGNORE INTO workspace_class_average "
                "(data_domain,academic_year_id,exam_name,exam_date,grade,class_type,"
                "class_num,teacher_name,subject_averages,total_averages,source,"
                "data_revision,created_at,updated_at) VALUES "
                "('homeroom',:academic_year_id,:exam_name,:exam_date,:grade,:class_type,"
                ":class_num,:teacher_name,:subject_averages,:total_averages,"
                "'legacy:class_average',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ),
            {
                "academic_year_id": years[0],
                "exam_name": row["exam_name"],
                "exam_date": exam_date,
                "grade": row["grade"],
                "class_type": row["class_type"],
                "class_num": row["class_num"],
                "teacher_name": row["teacher_name"],
                "subject_averages": row["subject_averages"] or "{}",
                "total_averages": row["total_averages"] or "{}",
            },
        )


def downgrade():
    op.drop_index(
        "idx_workspace_class_average_scope", table_name="workspace_class_average"
    )
    op.drop_table("workspace_class_average")
