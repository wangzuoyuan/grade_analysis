"""P4/P5 契约表：ws_student_note（档案，N01 域隔离）+ ws_homework_semester（作业学期）。

只 CREATE 新表（含唯一约束、外键、CHECK），不碰既有表；downgrade 逆序 DROP。
骨架由集成者随 P3 批次预建，端点实现属 P4-BE/P5-BE 波次。

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ws_student_note",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("data_domain", sa.String(length=16), nullable=False),
        sa.Column(
            "person_id",
            sa.Integer(),
            sa.ForeignKey("ws_student_identity.id"),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("follow_up", sa.Text(), nullable=True),
        sa.Column("follow_up_done", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("data_domain IN ('homeroom', 'teaching')", name="ck_ws_note_domain"),
    )
    op.create_index("idx_ws_note_person", "ws_student_note", ["data_domain", "person_id"])
    op.create_index("idx_ws_note_date", "ws_student_note", ["date"])

    op.create_table(
        "ws_homework_semester",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "academic_year_id",
            sa.Integer(),
            sa.ForeignKey("academic_year.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("is_current", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("mode IN ('auto', 'manual')", name="ck_ws_semester_mode"),
        sa.UniqueConstraint("academic_year_id", "name", name="uq_ws_semester_year_name"),
    )
    op.create_index("idx_ws_semester_year", "ws_homework_semester", ["academic_year_id"])


def downgrade() -> None:
    op.drop_index("idx_ws_semester_year", table_name="ws_homework_semester")
    op.drop_table("ws_homework_semester")
    op.drop_index("idx_ws_note_date", table_name="ws_student_note")
    op.drop_index("idx_ws_note_person", table_name="ws_student_note")
    op.drop_table("ws_student_note")
