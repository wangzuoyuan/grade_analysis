"""Backfill archived homeroom collection rows into the homework timeline.

Fresh real migrations already project ``homework_collection``.  This data-only
upgrade gives databases migrated before UX-HW02 the same recoverable path,
without reopening or modifying either legacy source database.
"""
from __future__ import annotations

import json
from datetime import date, datetime

from alembic import op
from sqlalchemy import text

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def _year_name(day: date) -> str:
    start = day.year if day.month >= 9 else day.year - 1
    return f"{start}-{start + 1}"


def upgrade():
    bind = op.get_bind()
    rows = bind.execute(text("""
        SELECT id, source_fingerprint, source_pk, payload_json
        FROM source_archive_record
        WHERE data_domain='homeroom' AND source_table='homework_collection'
        ORDER BY id
    """)).mappings().all()
    for row in rows:
        already = bind.execute(text("""
            SELECT 1 FROM source_projection_map
            WHERE data_domain='homeroom' AND source_fingerprint=:fp
              AND source_table='homework_collection' AND source_pk=:pk
              AND target_table='homework_assignment'
              AND projection_kind='collection_timeline'
            LIMIT 1
        """), {"fp": row["source_fingerprint"], "pk": row["source_pk"]}).first()
        if already:
            continue
        try:
            payload = json.loads(row["payload_json"])
            assigned = date.fromisoformat(str(payload["date"]))
            grade = int(payload["grade"])
            class_num = int(payload["class_num"])
            subject = str(payload["subject"]).strip()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if not subject:
            continue

        # 手工学期窗口优先；否则沿用正式迁移的 9 月学年边界。
        academic = bind.execute(text("""
            SELECT y.id FROM ws_homework_semester s
            JOIN academic_year y ON y.id=s.academic_year_id
            WHERE :day BETWEEN s.start_date AND s.end_date
            ORDER BY s.is_current DESC, s.id LIMIT 1
        """), {"day": assigned.isoformat()}).first()
        if academic is None:
            academic = bind.execute(
                text("SELECT id FROM academic_year WHERE name=:name LIMIT 1"),
                {"name": _year_name(assigned)},
            ).first()
        if academic is None:
            continue
        class_row = bind.execute(text("""
            SELECT id FROM administrative_class
            WHERE academic_year_id=:ay AND grade=:grade AND class_num=:class_num
            LIMIT 1
        """), {"ay": academic[0], "grade": grade, "class_num": class_num}).first()
        if class_row is None:
            continue

        token = (
            "migration:h:collection:a:"
            f"{row['source_fingerprint'][-8:]}:{row['source_pk']}:{class_row[0]}"
        )
        assignment = bind.execute(
            text("SELECT id FROM homework_assignment WHERE batch_token=:token LIMIT 1"),
            {"token": token},
        ).first()
        if assignment is None:
            now = datetime.utcnow()
            bind.execute(text("""
                INSERT INTO homework_assignment(
                    data_domain,class_ref_id,academic_year_id,subject,homework_type,
                    assigned_date,due_date,batch_token,expected_members_json,revision,
                    status,created_at,updated_at
                ) VALUES (
                    'homeroom',:class_id,:ay,:subject,'legacy',:day,NULL,:token,'[]',1,
                    'active',:now,:now
                )
            """), {
                "class_id": class_row[0], "ay": academic[0], "subject": subject,
                "day": assigned.isoformat(), "token": token, "now": now,
            })
            assignment = bind.execute(text("SELECT id FROM homework_assignment WHERE batch_token=:token"), {"token": token}).first()
        bind.execute(text("""
            INSERT OR IGNORE INTO source_projection_map(
                data_domain,source_fingerprint,source_table,source_pk,target_table,
                target_id,projection_kind,status,reason,created_at
            ) VALUES (
                'homeroom',:fp,'homework_collection',:pk,'homework_assignment',
                :target,'collection_timeline','projected',NULL,:now
            )
        """), {
            "fp": row["source_fingerprint"], "pk": row["source_pk"],
            "target": assignment[0], "now": datetime.utcnow(),
        })


def downgrade():
    # 历史证据只增不删；降级结构不应物理删除已恢复的时间轴。
    pass
