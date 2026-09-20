"""Preserve legacy rank and percentile metrics for homeroom focus analysis."""

import json

import sqlalchemy as sa
from alembic import op


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def _source_id(raw: str):
    try:
        pairs = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(pairs, list):
        return None
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        key, value = pair
        if key == "id":
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def upgrade():
    op.add_column("score_fact", sa.Column("grade_percentile", sa.Float(), nullable=True))
    op.add_column("score_fact", sa.Column("xueji_rank", sa.Integer(), nullable=True))
    op.add_column("score_fact", sa.Column("grade_rank", sa.Integer(), nullable=True))

    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if not {"source_projection_map", "subject_score", "total_score"}.issubset(tables):
        return

    mappings = bind.execute(
        sa.text(
            "SELECT source_table, source_pk, target_id FROM source_projection_map "
            "WHERE data_domain='homeroom' AND target_table='score_fact' "
            "AND source_table IN ('subject_score','total_score')"
        )
    ).mappings()
    for mapping in mappings:
        source_id = _source_id(mapping["source_pk"])
        if source_id is None:
            continue
        if mapping["source_table"] == "subject_score":
            source = bind.execute(
                sa.text("SELECT grade_percentile FROM subject_score WHERE id=:id"),
                {"id": source_id},
            ).mappings().first()
            if source is not None:
                bind.execute(
                    sa.text(
                        "UPDATE score_fact SET grade_percentile=:pct "
                        "WHERE id=:target_id AND grade_percentile IS NULL"
                    ),
                    {"pct": source["grade_percentile"], "target_id": mapping["target_id"]},
                )
        else:
            source = bind.execute(
                sa.text(
                    "SELECT grade_percentile,xueji_rank,grade_rank "
                    "FROM total_score WHERE id=:id"
                ),
                {"id": source_id},
            ).mappings().first()
            if source is not None:
                bind.execute(
                    sa.text(
                        "UPDATE score_fact SET grade_percentile=:pct,xueji_rank=:xr,grade_rank=:gr "
                        "WHERE id=:target_id"
                    ),
                    {
                        "pct": source["grade_percentile"],
                        "xr": source["xueji_rank"],
                        "gr": source["grade_rank"],
                        "target_id": mapping["target_id"],
                    },
                )


def downgrade():
    op.drop_column("score_fact", "grade_rank")
    op.drop_column("score_fact", "xueji_rank")
    op.drop_column("score_fact", "grade_percentile")
