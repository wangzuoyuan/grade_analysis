"""P8 one-to-many source projection audit map."""
from alembic import op
import sqlalchemy as sa
revision="0011"; down_revision="0010"; branch_labels=None; depends_on=None
def upgrade():
 op.create_table("source_projection_map",sa.Column("id",sa.Integer,primary_key=True),sa.Column("data_domain",sa.String(16),nullable=False),sa.Column("source_fingerprint",sa.String(128),nullable=False),sa.Column("source_table",sa.String(64),nullable=False),sa.Column("source_pk",sa.String(128),nullable=False),sa.Column("target_table",sa.String(64),nullable=False),sa.Column("target_id",sa.Integer,nullable=False),sa.Column("projection_kind",sa.String(32),nullable=False),sa.Column("status",sa.String(16),nullable=False,server_default="projected"),sa.Column("reason",sa.String(128)),sa.Column("created_at",sa.DateTime),sa.UniqueConstraint("data_domain","source_fingerprint","source_table","source_pk","target_table","target_id","projection_kind",name="uq_projection_origin_target"))
def downgrade(): op.drop_table("source_projection_map")
