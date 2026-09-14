"""P1 工作台表：域隔离/班级关联/身份/时期/来源映射/迁移台账/成绩事实。

只 CREATE 新表（含唯一约束、外键、CHECK），不碰既有 H 表（teacher/exam/
subject_score 等）；downgrade 逆序 DROP，同样只删新表。

数据库外键在连接层真实开启（app.db.workspace_models 为 engine 注册
PRAGMA foreign_keys=ON），因此 DROP 顺序必须先子后父。

Revision ID: 0001
Revises:
Create Date: 2026-09-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 学年 / 学期 / 届别 ──────────────────────────────────
    op.create_table(
        "academic_year",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("name", name="uq_academic_year_name"),
    )
    op.create_table(
        "term",
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
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("academic_year_id", "name", name="uq_term_year_name"),
    )
    op.create_index("idx_term_academic_year", "term", ["academic_year_id"])
    op.create_table(
        "cohort",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_year", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("entry_year", name="uq_cohort_entry_year"),
    )

    # ── 域内身份（ws_ 前缀与既有 student_identity/student_alias 区分）─────
    op.create_table(
        "ws_student_identity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("data_domain", sa.String(length=16), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "data_domain IN ('homeroom', 'teaching')", name="ck_ws_identity_domain"
        ),
    )
    op.create_table(
        "ws_student_alias",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "identity_id",
            sa.Integer(),
            sa.ForeignKey("ws_student_identity.id"),
            nullable=False,
        ),
        sa.Column("alias_value", sa.Text(), nullable=False),
        sa.Column("data_domain", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.Column("school", sa.String(length=64), nullable=True),
        sa.Column(
            "academic_year_id",
            sa.Integer(),
            sa.ForeignKey("academic_year.id"),
            nullable=True,
        ),
        # alias_scope：SQLite 唯一约束把 NULL 视为互不相等，academic_year_id
        # 为 NULL 会绕过含 NULL 的唯一键，故用非空冗余列（学年 id 字符串或
        # 'none'）参与唯一约束，由应用层与 academic_year_id 同步维护。
        sa.Column(
            "alias_scope",
            sa.String(length=16),
            nullable=False,
            server_default="none",
        ),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "data_domain IN ('homeroom', 'teaching')", name="ck_ws_alias_domain"
        ),
        sa.UniqueConstraint(
            "identity_id", "alias_value", "data_domain", "alias_scope",
            name="uq_ws_alias_identity_value",
        ),
    )
    op.create_index("idx_ws_alias_identity", "ws_student_alias", ["identity_id"])

    # ── 行政班 / 学籍 ────────────────────────────────────────
    op.create_table(
        "administrative_class",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "academic_year_id",
            sa.Integer(),
            sa.ForeignKey("academic_year.id"),
            nullable=False,
        ),
        sa.Column("grade", sa.Integer(), nullable=False),
        sa.Column("class_num", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("academic_year_id", "grade", "class_num", name="uq_admin_class"),
    )
    op.create_index("idx_admin_class_year", "administrative_class", ["academic_year_id"])
    op.create_table(
        "enrollment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "admin_class_id",
            sa.Integer(),
            sa.ForeignKey("administrative_class.id"),
            nullable=False,
        ),
        sa.Column(
            "identity_id",
            sa.Integer(),
            sa.ForeignKey("ws_student_identity.id"),
            nullable=False,
        ),
        sa.Column("seat_no", sa.Integer(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="active"
        ),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("admin_class_id", "identity_id", "valid_from", name="uq_enrollment"),
    )
    op.create_index("idx_enrollment_identity", "enrollment", ["identity_id"])

    # ── 教学班 / 成员 ────────────────────────────────────────
    op.create_table(
        "teaching_class",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "academic_year_id",
            sa.Integer(),
            sa.ForeignKey("academic_year.id"),
            nullable=False,
        ),
        sa.Column("subject", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("academic_year_id", "subject", "label", name="uq_teaching_class"),
    )
    op.create_index("idx_teaching_class_year", "teaching_class", ["academic_year_id"])
    op.create_table(
        "teaching_class_member",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "teaching_class_id",
            sa.Integer(),
            sa.ForeignKey("teaching_class.id"),
            nullable=False,
        ),
        sa.Column(
            "identity_id",
            sa.Integer(),
            sa.ForeignKey("ws_student_identity.id"),
            nullable=False,
        ),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column(
            "source", sa.String(length=32), nullable=False, server_default="manual"
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "teaching_class_id", "identity_id", "valid_from", name="uq_teaching_member"
        ),
    )
    op.create_index("idx_teaching_member_identity", "teaching_class_member", ["identity_id"])

    # ── 行政班 ↔ 教学班 受控关联 ─────────────────────────────
    op.create_table(
        "homeroom_teaching_link",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "admin_class_id",
            sa.Integer(),
            sa.ForeignKey("administrative_class.id"),
            nullable=False,
        ),
        sa.Column(
            "teaching_class_id",
            sa.Integer(),
            sa.ForeignKey("teaching_class.id"),
            nullable=False,
        ),
        sa.Column(
            "academic_year_id",
            sa.Integer(),
            sa.ForeignKey("academic_year.id"),
            nullable=False,
        ),
        sa.Column("subject", sa.String(length=32), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column(
            "share_categories",
            sa.Text(),
            nullable=False,
            server_default="roster,current_subject_score",
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="active"
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "admin_class_id", "teaching_class_id", "academic_year_id", "subject",
            name="uq_htl_class_pair",
        ),
    )
    op.create_index("idx_htl_admin_class", "homeroom_teaching_link", ["admin_class_id"])
    op.create_index("idx_htl_teaching_class", "homeroom_teaching_link", ["teaching_class_id"])
    op.create_table(
        "linked_student",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "link_id",
            sa.Integer(),
            sa.ForeignKey("homeroom_teaching_link.id"),
            nullable=False,
        ),
        sa.Column(
            "homeroom_identity_id",
            sa.Integer(),
            sa.ForeignKey("ws_student_identity.id"),
            nullable=False,
        ),
        sa.Column(
            "teaching_identity_id",
            sa.Integer(),
            sa.ForeignKey("ws_student_identity.id"),
            nullable=False,
        ),
        sa.Column("confirm_basis", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "link_id", "homeroom_identity_id", "teaching_identity_id",
            name="uq_linked_student",
        ),
    )
    op.create_index("idx_linked_student_link", "linked_student", ["link_id"])

    # ── 迁移台账 / 来源映射 ──────────────────────────────────
    op.create_table(
        "migration_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_token", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stats_json", sa.Text(), nullable=True),
    )
    op.create_table(
        "source_map",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("source_table", sa.String(length=64), nullable=False),
        sa.Column("source_pk", sa.String(length=64), nullable=False),
        sa.Column("target_table", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("active", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "source_fingerprint", "source_table", "source_pk", name="uq_source_map_origin"
        ),
    )
    op.create_index("idx_source_map_target", "source_map", ["target_table", "target_id"])

    # ── 成绩事实 / 导入批次 ──────────────────────────────────
    # subject_key/total_key：subject/total_type 可空而 SQLite UNIQUE 对 NULL
    # 互不相等，故以非空冗余列（默认 ''）进入唯一约束，由应用层同步维护。
    op.create_table(
        "score_fact",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("data_domain", sa.String(length=16), nullable=False),
        sa.Column(
            "academic_year_id",
            sa.Integer(),
            sa.ForeignKey("academic_year.id"),
            nullable=False,
        ),
        sa.Column("exam_name", sa.String(length=128), nullable=False),
        sa.Column("exam_date", sa.Date(), nullable=True),
        # class_ref_id 多态引用（homeroom→administrative_class.id，
        # teaching→teaching_class.id），故不建数据库外键
        sa.Column("class_ref_id", sa.Integer(), nullable=True),
        sa.Column(
            "identity_id",
            sa.Integer(),
            sa.ForeignKey("ws_student_identity.id"),
            nullable=False,
        ),
        sa.Column("subject", sa.String(length=32), nullable=True),
        sa.Column("total_type", sa.String(length=32), nullable=True),
        sa.Column("subject_key", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("total_key", sa.String(length=32), nullable=False, server_default=""),
        # score 可空：缺考存 NULL，绝不写 0
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="p1_seed"),
        sa.Column("data_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "data_domain IN ('homeroom', 'teaching')", name="ck_score_fact_domain"
        ),
        sa.UniqueConstraint(
            "data_domain", "academic_year_id", "exam_name", "identity_id",
            "subject_key", "total_key",
            name="uq_score_fact_natural_key",
        ),
    )
    op.create_index(
        "idx_score_fact_scope", "score_fact",
        ["data_domain", "academic_year_id", "identity_id"],
    )
    op.create_index("idx_score_fact_class_ref", "score_fact", ["data_domain", "class_ref_id"])
    op.create_table(
        "import_batch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("data_domain", sa.String(length=16), nullable=False),
        sa.Column("scope_json", sa.Text(), nullable=True),
        sa.Column("content_digest", sa.String(length=128), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "data_domain IN ('homeroom', 'teaching')", name="ck_import_batch_domain"
        ),
        sa.UniqueConstraint("token", name="uq_import_batch_token"),
    )


def downgrade() -> None:
    # 逆序 DROP（外键已真实开启，必须先子后父）；只删本迁移新建的表
    op.drop_table("import_batch")
    op.drop_index("idx_score_fact_class_ref", table_name="score_fact")
    op.drop_index("idx_score_fact_scope", table_name="score_fact")
    op.drop_table("score_fact")
    op.drop_index("idx_source_map_target", table_name="source_map")
    op.drop_table("source_map")
    op.drop_table("migration_run")
    op.drop_index("idx_linked_student_link", table_name="linked_student")
    op.drop_table("linked_student")
    op.drop_index("idx_htl_teaching_class", table_name="homeroom_teaching_link")
    op.drop_index("idx_htl_admin_class", table_name="homeroom_teaching_link")
    op.drop_table("homeroom_teaching_link")
    op.drop_index("idx_teaching_member_identity", table_name="teaching_class_member")
    op.drop_table("teaching_class_member")
    op.drop_index("idx_teaching_class_year", table_name="teaching_class")
    op.drop_table("teaching_class")
    op.drop_index("idx_enrollment_identity", table_name="enrollment")
    op.drop_table("enrollment")
    op.drop_index("idx_admin_class_year", table_name="administrative_class")
    op.drop_table("administrative_class")
    op.drop_index("idx_ws_alias_identity", table_name="ws_student_alias")
    op.drop_table("ws_student_alias")
    op.drop_table("ws_student_identity")
    op.drop_table("cohort")
    op.drop_index("idx_term_academic_year", table_name="term")
    op.drop_table("term")
    op.drop_table("academic_year")
