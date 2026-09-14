"""P1-R10：作业批次表 homework_assignment / homework_submission。

契约 §2.1（docs/contracts/p1-api.md）冻结的最小作业数据契约：
batch_token 幂等键、应交成员快照、同人同批次唯一的逐人状态；
完整录入/预警规则属 P5，不在此迁移扩展。

建表顺序按外键依赖：assignment 先（FK academic_year），submission 后
（FK homework_assignment 与 ws_student_identity）；downgrade 逆序 DROP
（外键在迁移连接真实开启，必须先子后父）。

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 作业布置批次 ─────────────────────────────────────────
    op.create_table(
        "homework_assignment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("data_domain", sa.String(length=16), nullable=False),
        # class_ref_id 多态引用（homeroom→administrative_class.id，
        # teaching→teaching_class.id），同 score_fact 语义，不建数据库外键
        sa.Column("class_ref_id", sa.Integer(), nullable=False),
        sa.Column(
            "academic_year_id",
            sa.Integer(),
            sa.ForeignKey("academic_year.id"),
            nullable=False,
        ),
        sa.Column("subject", sa.String(length=32), nullable=False),
        sa.Column("homework_type", sa.String(length=32), nullable=False),
        sa.Column("assigned_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        # batch_token 幂等键：重试同 token 不新增批次
        sa.Column("batch_token", sa.String(length=64), nullable=False),
        # 应交成员快照（person_id 列表 + 快照生成时点），提交率分母以此为准
        sa.Column("expected_members_json", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="active"
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "data_domain IN ('homeroom', 'teaching')",
            name="ck_homework_assignment_domain",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')", name="ck_homework_assignment_status"
        ),
        sa.UniqueConstraint("batch_token", name="uq_homework_assignment_token"),
    )
    op.create_index(
        "idx_homework_assignment_scope",
        "homework_assignment",
        ["data_domain", "class_ref_id"],
    )

    # ── 逐人作业状态 ─────────────────────────────────────────
    op.create_table(
        "homework_submission",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "assignment_id",
            sa.Integer(),
            sa.ForeignKey("homework_assignment.id"),
            nullable=False,
        ),
        sa.Column(
            "person_id",
            sa.Integer(),
            sa.ForeignKey("ws_student_identity.id"),
            nullable=False,
        ),
        sa.Column("submission_status", sa.String(length=16), nullable=False),
        # 评价另列：不得从评价缺失推断缺交
        sa.Column("evaluation", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "submission_status IN ('submitted', 'missing', 'excused', 'unknown')",
            name="ck_homework_submission_status",
        ),
        sa.UniqueConstraint(
            "assignment_id", "person_id", name="uq_homework_submission"
        ),
    )
    op.create_index(
        "idx_homework_submission_assignment", "homework_submission", ["assignment_id"]
    )


def downgrade() -> None:
    # 逆序 DROP（外键已真实开启，必须先子后父）；只删本迁移新建的表
    op.drop_index(
        "idx_homework_submission_assignment", table_name="homework_submission"
    )
    op.drop_table("homework_submission")
    op.drop_index(
        "idx_homework_assignment_scope", table_name="homework_assignment"
    )
    op.drop_table("homework_assignment")
