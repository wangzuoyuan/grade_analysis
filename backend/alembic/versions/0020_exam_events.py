"""Stable exam events; preserve every historical source and score value."""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    # Keep referenced parent tables intact at transaction boundaries. SQLite
    # defers FK checks through the batch replacement, then checks on commit.
    conn = op.get_bind()
    if not conn.connection.driver_connection.in_transaction:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
    conn.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
    op.create_table("workspace_exam_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("academic_year_id", sa.Integer(), sa.ForeignKey("academic_year.id"), nullable=False),
        sa.Column("grade", sa.Integer(), nullable=True),
        sa.Column("exam_name", sa.String(128), nullable=False),
        sa.Column("merged_into_id", sa.Integer(), sa.ForeignKey("workspace_exam_event.id")),
        sa.Column("data_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp()))
    tables = [("score_fact", "scorefact", "uq_score_fact_natural_key",
               ["data_domain", "academic_year_id", "exam_scope_key", "identity_id", "subject_key", "total_key"]),
              ("teaching_exam_scope", "teachingexamscope", "uq_teaching_exam_scope",
               ["academic_year_id", "grade", "subject", "exam_scope_key"]),
              ("workspace_class_average", "workspaceclassaverage", "uq_workspace_class_average_natural_key",
               ["data_domain", "academic_year_id", "exam_scope_key", "grade", "class_num"])]
    for table, model, constraint, keys in tables:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("exam_event_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_" + model + "_event", "workspace_exam_event", ["exam_event_id"], ["id"])
            batch.drop_constraint(constraint, type_="unique")
        op.create_index(constraint, table, [sa.text("CASE WHEN exam_event_id IS NULL THEN 'name:' || exam_name ELSE 'event:' || exam_event_id END") if key == "exam_scope_key" else key for key in keys], unique=True)

    conn = op.get_bind()
    events = {}

    def event_id(key, year, grade, name):
        if key not in events:
            result = conn.execute(sa.text("INSERT INTO workspace_exam_event "
                "(academic_year_id, grade, exam_name) VALUES (:year, :grade, :name)"),
                dict(year=year, grade=grade, name=name))
            events[key] = result.lastrowid
        return events[key]

    platform = {}
    for r in conn.execute(sa.text("SELECT * FROM teaching_exam_scope")).mappings().all():
        eid = event_id(("platform", r["id"]), r["academic_year_id"], r["grade"], r["exam_name"])
        platform[r["id"]] = eid
        conn.execute(sa.text("UPDATE teaching_exam_scope SET exam_event_id=:eid WHERE id=:id"),
                     dict(eid=eid, key=f"event:{eid}", id=r["id"]))
    grades = {}
    for domain, table in [("homeroom", "administrative_class"), ("teaching", "teaching_class")]:
        for r in conn.execute(sa.text(f"SELECT id, grade FROM {table}")):
            grades[(domain, r.id)] = r.grade
    for table in ("score_fact", "workspace_class_average"):
        for r in conn.execute(sa.text(f"SELECT * FROM {table}")).mappings().all():
            grade = r.get("grade", grades.get((r["data_domain"], r.get("class_ref_id"))))
            key = (r["data_domain"], r["academic_year_id"], grade, r["exam_name"], str(r["exam_date"] or r.get("source_exam_date") or ""))
            eid = platform.get(r.get("teaching_exam_id")) or event_id(key, r["academic_year_id"], grade, r["exam_name"])
            conn.execute(sa.text(f"UPDATE {table} SET exam_event_id=:eid WHERE id=:id"),
                         dict(eid=eid, key=f"event:{eid}", id=r["id"]))

    if conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("Exam event migration would break foreign keys")
    # SQLite retains deferred DROP counters even after a parent is recreated.
    # The full FK check above proves the final graph before clearing those counters.
    conn.exec_driver_sql("PRAGMA defer_foreign_keys=OFF")


def downgrade():
    # Same-name distinct events cannot fit the old schema. Fail before any DDL.
    conn = op.get_bind()
    definitions = [("score_fact", "scorefact", "uq_score_fact_natural_key",
                    ["data_domain", "academic_year_id", "exam_name", "identity_id", "subject_key", "total_key"]),
                   ("teaching_exam_scope", "teachingexamscope", "uq_teaching_exam_scope",
                    ["academic_year_id", "grade", "subject", "exam_name"]),
                   ("workspace_class_average", "workspaceclassaverage", "uq_workspace_class_average_natural_key",
                    ["data_domain", "academic_year_id", "exam_name", "grade", "class_num"])]
    for table, _, _, keys in definitions:
        if conn.execute(sa.text(f"SELECT 1 FROM {table} GROUP BY {', '.join(keys)} HAVING COUNT(*) > 1 LIMIT 1")).first():
            raise RuntimeError("Distinct same-name exams require restoring the pre-migration backup for rollback")
    if not conn.connection.driver_connection.in_transaction:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
    conn.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
    for table, model, constraint, keys in definitions:
        op.drop_index(constraint, table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.create_unique_constraint(constraint, keys)
            batch.drop_constraint("fk_" + model + "_event", type_="foreignkey")
            batch.drop_column("exam_event_id")
    op.drop_table("workspace_exam_event")
    if conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("Exam event rollback would break foreign keys")
    conn.exec_driver_sql("PRAGMA defer_foreign_keys=OFF")
