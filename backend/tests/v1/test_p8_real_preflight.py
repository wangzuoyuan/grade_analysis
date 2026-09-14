"""P8 real-migration CLI: real-schema-shaped, PII-free fixture tests."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import importlib.util


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SCRIPT = os.path.join(REPO, "scripts", "migration", "run_real_migration.py")


def _make_source(path, teaching=False):
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE exam (id INTEGER PRIMARY KEY, name TEXT, grade INTEGER, semester TEXT, exam_date TEXT, exam_type TEXT, source_files TEXT, created_at TEXT);
    CREATE TABLE student_identity (id INTEGER PRIMARY KEY, display_name TEXT);
    CREATE TABLE student_alias (id INTEGER PRIMARY KEY, identity_id INTEGER, student_id TEXT, grade INTEGER);
    CREATE TABLE subject_score (id INTEGER PRIMARY KEY, exam_id INTEGER, student_id TEXT, class_num INTEGER, class_label TEXT, xueji TEXT, name TEXT, subject TEXT, raw_score REAL, grade_score REAL, grade_percentile REAL);
    CREATE TABLE total_score (id INTEGER PRIMARY KEY, exam_id INTEGER, student_id TEXT, total_type TEXT, total_score REAL);
    CREATE TABLE homework_record (id INTEGER PRIMARY KEY, student_id TEXT, date TEXT, subject TEXT, content TEXT, remark TEXT, submission_status TEXT, evaluation TEXT);
    CREATE TABLE upload (id INTEGER PRIMARY KEY, file_path TEXT);
    CREATE TABLE special_record (id INTEGER PRIMARY KEY, student_id TEXT, date TEXT, type TEXT, note TEXT);
    CREATE TABLE homework_setting (key TEXT PRIMARY KEY, value TEXT);
    """)
    if teaching:
        db.executescript("""
        CREATE TABLE teaching_class (id INTEGER PRIMARY KEY, grade INTEGER, label TEXT, subject TEXT);
        CREATE TABLE teaching_class_member (id INTEGER PRIMARY KEY, teaching_class_id INTEGER REFERENCES teaching_class(id), student_id TEXT, source TEXT, name TEXT);
        CREATE TABLE teacher (id INTEGER PRIMARY KEY, subject TEXT);
        CREATE TABLE class_roster (student_id TEXT PRIMARY KEY, name TEXT);
        """)
        db.execute("INSERT INTO teacher VALUES (1, 'physics')")
        # Deliberate orphan exercises anonymised FK classification.
        db.execute("INSERT INTO teaching_class VALUES (1,1,'1','physics')")
        db.execute("INSERT INTO teaching_class_member VALUES (1,1,'valid','import','synthetic')")
        db.execute("INSERT INTO teaching_class_member VALUES (2,99,'orphan','import','synthetic')")
    else:
        db.executescript("""
        CREATE TABLE class_roster (student_id TEXT PRIMARY KEY, name TEXT, class_num INTEGER, seat_no INTEGER, gender TEXT, excluded INTEGER, grade INTEGER, status TEXT);
        CREATE TABLE homework_collection (id INTEGER PRIMARY KEY, date TEXT, subject TEXT, grade INTEGER, class_num INTEGER);
        CREATE TABLE student_change_log (id INTEGER PRIMARY KEY, op_type TEXT);
        """)
    db.execute("INSERT INTO exam VALUES (1,'synthetic',1,'up','2025-09','monthly','[]','now')")
    db.execute("INSERT INTO homework_setting VALUES ('fixture-key','fixture-value')")
    db.commit(); db.close()


def test_real_preflight_month_precision_fk_and_no_target_write(tmp_path):
    h, t, target = tmp_path / "h.sqlite", tmp_path / "t.sqlite", tmp_path / "target"
    _make_source(h); _make_source(t, teaching=True)
    result = subprocess.run([sys.executable, SCRIPT, "--homeroom-db", str(h), "--teaching-db", str(t), "--target-root", str(target), "--preflight-only"], capture_output=True, text=True, cwd=REPO)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["manifest"]["homeroom"]["exam_date_lengths"] == {"7": 1}
    assert payload["manifest"]["teaching"]["foreign_key_violations"] == {"teaching_class_member": 1}
    assert not target.exists()


def test_decision_digest_drift_refuses_before_target_write(tmp_path):
    h, t, target, decision = tmp_path / "h.sqlite", tmp_path / "t.sqlite", tmp_path / "target", tmp_path / "decision.json"
    _make_source(h); _make_source(t, teaching=True)
    decision.write_text(json.dumps({"source_sha256": {"homeroom": "stale", "teaching": "stale"}}), encoding="utf-8")
    result = subprocess.run([sys.executable, SCRIPT, "--homeroom-db", str(h), "--teaching-db", str(t), "--target-root", str(target), "--decisions", str(decision), "--preflight-only"], capture_output=True, text=True, cwd=REPO)
    assert result.returncode == 2
    assert not target.exists()


def test_real_archive_end_to_end_is_idempotent_and_uses_stable_keys(tmp_path):
    h, t, target = tmp_path / "h.sqlite", tmp_path / "t.sqlite", tmp_path / "target"
    _make_source(h); _make_source(t, teaching=True)
    command = [sys.executable, SCRIPT, "--homeroom-db", str(h), "--teaching-db", str(t), "--target-root", str(target)]
    first = subprocess.run(command, capture_output=True, text=True, cwd=REPO)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["status"] == "completed"
    db = sqlite3.connect(target / "data" / "db.sqlite")
    source_rows = 0
    for source in (h, t):
        src = sqlite3.connect(source)
        source_rows += sum(r[0] for r in src.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")) * 0
        for (table,) in src.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            source_rows += src.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        src.close()
    assert db.execute("SELECT COUNT(*) FROM source_archive_record").fetchone()[0] == source_rows
    assert db.execute("SELECT COUNT(*) FROM source_archive_record WHERE source_table='homework_setting' AND source_pk LIKE ?", ('[["key","fixture-key"]]',)).fetchone()[0] == 2
    reasons = dict(db.execute("SELECT archive_reason, COUNT(*) FROM source_archive_record WHERE source_table='teaching_class_member' GROUP BY archive_reason"))
    assert reasons == {"foreign_key_orphan_pending_review": 1, "source_preserved_pending_projection": 1}
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("SELECT COUNT(*) FROM pragma_foreign_key_check").fetchone()[0] == 0
    db.close()
    second = subprocess.run(command + ["--resume"], capture_output=True, text=True, cwd=REPO)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["added"] == 0


def test_score_fact_keys_still_sync_immediately_after_archive_model_added():
    from app.db.workspace_models import ScoreFact
    fact = ScoreFact(subject="physics", total_type=None)
    assert fact.subject_key == "physics"
    assert fact.total_key == ""


def _actual_shape_source(path, teaching=False):
    """Small, PII-free source that has the columns used by the real projector."""
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE exam (id INTEGER PRIMARY KEY, name TEXT, grade INTEGER, exam_date TEXT);
    CREATE TABLE student_identity (id INTEGER PRIMARY KEY, display_name TEXT);
    CREATE TABLE student_alias (id INTEGER PRIMARY KEY, identity_id INTEGER, student_id TEXT);
    CREATE TABLE subject_score (id INTEGER PRIMARY KEY, exam_id INTEGER, student_id TEXT, class_num INTEGER, name TEXT, subject TEXT, raw_score REAL, grade_score REAL);
    CREATE TABLE total_score (id INTEGER PRIMARY KEY, exam_id INTEGER, student_id TEXT, total_type TEXT, total_score REAL);
    CREATE TABLE homework_record (id INTEGER PRIMARY KEY, student_id TEXT, date TEXT, subject TEXT, content TEXT, remark TEXT);
    CREATE TABLE special_record (id INTEGER PRIMARY KEY, student_id TEXT, date TEXT, type TEXT, note TEXT);
    CREATE TABLE homework_semester (id INTEGER PRIMARY KEY, name TEXT, start_date TEXT, end_date TEXT, is_current INTEGER);
    CREATE TABLE homework_setting (key TEXT PRIMARY KEY, value TEXT);
    """)
    db.execute("INSERT INTO homework_setting VALUES ('active_grade', '2')")
    if teaching:
        db.executescript("""
        CREATE TABLE teaching_class (id INTEGER PRIMARY KEY, grade INTEGER, label TEXT, subject TEXT, sort_order INTEGER);
        CREATE TABLE teaching_class_member (id INTEGER PRIMARY KEY, teaching_class_id INTEGER, student_id TEXT, source TEXT, name TEXT);
        CREATE TABLE teacher (id INTEGER PRIMARY KEY, subject TEXT);
        -- T 花名册行与 teaching_class_member 同号异名：专供补齐模式验证
        -- 「多候选跳过并列入 candidates」，投影本身不消费该表。
        CREATE TABLE class_roster (student_id TEXT PRIMARY KEY, name TEXT);
        """)
        # Overlaps the H semester inside the same academic year: exercises the
        # union merge (T row keeps the wider period as the name donor).
        db.execute("INSERT INTO homework_semester VALUES (1, '2032学年一学期', '2032-09-01', '2033-01-25', 1)")
        db.execute("INSERT INTO teacher VALUES (1, 'physics')")
        # T 的作业源列含 submission_status/evaluation（H 源没有这两列）。
        db.execute("DROP TABLE homework_record")
        db.execute("CREATE TABLE homework_record (id INTEGER PRIMARY KEY, student_id TEXT, date TEXT, subject TEXT, content TEXT, remark TEXT, submission_status TEXT NOT NULL, evaluation TEXT)")
        db.execute("INSERT INTO teaching_class VALUES (1, 2, 'current', 'physics', 3)")
        db.execute("INSERT INTO teaching_class_member VALUES (1, 1, 't-member-only', 'import', '合成员')")
        db.execute("INSERT INTO class_roster VALUES ('t-member-only', '另一名')")
        db.execute("INSERT INTO exam VALUES (1, 'old-teaching', 1, '2031-10')")
        db.execute("INSERT INTO subject_score VALUES (1, 1, 't-score-only', 7, '合成分T', 'physics', 88, 88)")
        db.execute("INSERT INTO homework_record VALUES (1, 't-member-only', '2032-10-01', '日常作业', NULL, NULL, '缺交', NULL)")
        db.execute("INSERT INTO special_record VALUES (1, 't-member-only', '2032-10-02', '晚到', NULL)")
        db.execute("INSERT INTO special_record VALUES (2, 't-ghost', '2032-10-03', '比赛', 'extra')")
    else:
        db.executescript("""
        CREATE TABLE class_roster (student_id TEXT PRIMARY KEY, name TEXT, class_num INTEGER, seat_no INTEGER, grade INTEGER, status TEXT);
        """)
        db.execute("INSERT INTO homework_semester VALUES (1, '2032第一学期', '2032-09-01', '2033-01-20', 1)")
        db.execute("INSERT INTO student_identity VALUES (1, 'synthetic')")
        db.execute("INSERT INTO student_alias VALUES (1, 1, 'h-alias')")
        # h-alias 的名册行故意异名：验证既有名不被覆盖，只计数冲突。
        db.execute("INSERT INTO class_roster VALUES ('h-alias', '旧链异名', 6, 1, 2, 'active')")
        db.execute("INSERT INTO class_roster VALUES ('h-roster-only', '合成名册', 6, 2, 1, 'active')")
        db.execute("INSERT INTO exam VALUES (1, 'old-homeroom', 1, '2031-10')")
        db.execute("INSERT INTO subject_score VALUES (1, 1, 'h-score-only', 6, '合成分', 'physics', 91, 91)")
        db.execute("INSERT INTO homework_record VALUES (1, 'h-alias', '2032-09-15', '物理', '未带', '病假')")
        db.execute("INSERT INTO special_record VALUES (1, 'h-alias', '2032-09-16', '请假', NULL)")
    db.commit(); db.close()


def test_real_projector_derives_years_members_and_one_to_many_maps(tmp_path):
    h, t = tmp_path / "h.sqlite", tmp_path / "t.sqlite"
    _actual_shape_source(h); _actual_shape_source(t, teaching=True)
    spec = importlib.util.spec_from_file_location("p8_real", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    from app.db.models import engine

    target = engine.url.database
    first = module.project_workspace_facts(h, t, target)
    db = sqlite3.connect(target)
    # The current class comes from the 2032 semester anchor; historical scores use
    # their own 2031-10 exam date rather than a fixed migration-era calendar.
    years = {row[0] for row in db.execute("SELECT name FROM academic_year")}
    assert {"2031-2032", "2032-2033"} <= years
    assert db.execute("SELECT COUNT(*) FROM enrollment").fetchone()[0] == 3
    assert db.execute("SELECT COUNT(*) FROM teaching_class_member").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM score_fact").fetchone()[0] == 2
    maps = db.execute("SELECT source_table, projection_kind, COUNT(*) FROM source_projection_map GROUP BY source_table, projection_kind").fetchall()
    assert ("student_identity", "identity", 1) in maps
    assert ("student_alias", "alias", 1) in maps
    assert ("class_roster", "roster_enrollment", 2) in maps
    assert ("teaching_class_member", "member", 1) in maps
    assert ("subject_score", "score", 2) in maps
    before = db.execute("SELECT COUNT(*) FROM source_projection_map").fetchone()[0]
    db.close()
    second = module.project_workspace_facts(h, t, target)
    db = sqlite3.connect(target)
    assert db.execute("SELECT COUNT(*) FROM source_projection_map").fetchone()[0] == before
    assert db.execute("SELECT COUNT(*) FROM score_fact").fetchone()[0] == 2
    assert first["h_roster"] == second["h_roster"] == 2
    assert first["t_score"] == second["t_score"] == 1
    db.close()


def _alias_names(db_path):
    db = sqlite3.connect(db_path)
    try:
        return dict(db.execute(
            "SELECT a.alias_value, i.display_name FROM ws_student_alias a "
            "JOIN ws_student_identity i ON i.id = a.identity_id"
        ).fetchall())
    finally:
        db.close()


def test_projector_preserves_source_row_names_ux01(tmp_path):
    """UX01：来源行姓名必须进入 display_name；既有名不被异名覆盖，只计数冲突。"""
    h, t = tmp_path / "h.sqlite", tmp_path / "t.sqlite"
    _actual_shape_source(h); _actual_shape_source(t, teaching=True)
    spec = importlib.util.spec_from_file_location("p8_real", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    from app.db.models import engine

    counts = module.project_workspace_facts(h, t, engine.url.database)
    names = _alias_names(engine.url.database)
    assert names.get("t-member-only") == "合成员"
    assert names.get("t-score-only") == "合成分T"
    assert names.get("h-roster-only") == "合成名册"
    assert names.get("h-score-only") == "合成分"
    # h-alias 走旧别名链（identity 表名 synthetic），名册行异名不得覆盖。
    assert names.get("h-alias") == "synthetic"
    assert counts.get("identity_name_conflict", 0) >= 1


def test_backfill_display_names_fills_null_lists_candidates_ux01(tmp_path):
    """UX01 补齐模式：只填空名；多候选跳过并列举；绝不覆盖非空姓名；幂等。"""
    h, t = tmp_path / "h.sqlite", tmp_path / "t.sqlite"
    _actual_shape_source(h); _actual_shape_source(t, teaching=True)
    spec = importlib.util.spec_from_file_location("p8_real", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    from app.db.models import engine

    target = engine.url.database
    module.project_workspace_facts(h, t, target)
    db = sqlite3.connect(target)
    # 模拟旧代码产物：score-only 身份全部无名。
    db.execute("UPDATE ws_student_identity SET display_name = NULL WHERE note = 'legacy score-only identity'")
    db.commit()
    db.close()
    assert _alias_names(target).get("t-score-only") is None

    result = module.backfill_display_names(h, t, target)
    names = _alias_names(target)
    assert names.get("t-score-only") == "合成分T"
    assert names.get("h-roster-only") == "合成名册"
    assert names.get("h-score-only") == "合成分"
    # t-member-only 在 T 源有两个不同姓名 → 候选待人工裁决，保持 NULL。
    assert names.get("t-member-only") is None
    teaching_cands = [c for c in result["candidates"] if c["data_domain"] == "teaching"]
    assert len(teaching_cands) == 1
    assert sorted(teaching_cands[0]["names"]) == ["另一名", "合成员"]
    assert result["filled"] == 3
    # h-alias 旧链名 synthetic 与名册异名并存：计数 name_mismatch，不写库。
    assert result["name_mismatch"] == 1
    assert result["already_named"] == 0

    # 幂等：重跑零新增填充，已具名保持。
    second = module.backfill_display_names(h, t, target)
    assert second["filled"] == 0
    assert _alias_names(target).get("t-score-only") == "合成分T"


def _run_cli(tmp_path, h, t, target_root, *extra):
    return subprocess.run(
        [sys.executable, SCRIPT, "--homeroom-db", str(h), "--teaching-db", str(t), "--target-root", str(target_root), *extra],
        capture_output=True, text=True, cwd=REPO,
    )


def _make_full_cli_target(tmp_path):
    """完整 CLI 迁移目标（marker + migration_run completed），供补齐校验测试。"""
    h, t = tmp_path / "h.sqlite", tmp_path / "t.sqlite"
    _actual_shape_source(h); _actual_shape_source(t, teaching=True)
    target = tmp_path / "target"
    first = _run_cli(tmp_path, h, t, target)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["status"] == "completed"
    return h, t, target


def test_backfill_cli_binds_marker_and_source_digests_r02(tmp_path):
    """R02：补齐必须校验 marker 摘要/迁移身份/源边界；任一不符零写入退出。"""
    h, t, target = _make_full_cli_target(tmp_path)
    db_path = target / "data" / "db.sqlite"
    before = db_path.read_bytes()

    def backfill(h_src=h, t_src=t, root=target):
        result = _run_cli(tmp_path, h_src, t_src, root, "--backfill-display-names")
        return result.returncode, json.loads(result.stdout)

    # 正确快照：ok（幂等，重跑零新增）
    code, payload = backfill()
    assert code == 0 and payload["ok"] is True, payload
    code, payload2 = backfill()
    assert code == 0 and payload2["ok"] is True
    assert payload2["backfill_display_names"]["filled"] == 0

    # 错误 H 源：改一个姓名后摘要不符 → 拒绝且目标库字节零变化
    bad_h = tmp_path / "h-bad.sqlite"
    shutil.copy(h, bad_h)
    db = sqlite3.connect(bad_h)
    db.execute("UPDATE class_roster SET name = '篡改名' WHERE student_id = 'h-roster-only'")
    db.commit(); db.close()
    code, payload = backfill(h_src=bad_h)
    assert code == 2 and payload["ok"] is False
    assert "digest mismatch" in payload["error"]
    assert db_path.read_bytes() == before

    # 错误 T 源：同样拒绝
    bad_t = tmp_path / "t-bad.sqlite"
    shutil.copy(t, bad_t)
    db = sqlite3.connect(bad_t)
    db.execute("UPDATE teaching_class_member SET name = '篡改名T' WHERE student_id = 't-member-only'")
    db.commit(); db.close()
    code, payload = backfill(t_src=bad_t)
    assert code == 2 and "digest mismatch" in payload["error"]
    assert db_path.read_bytes() == before

    # 缺 marker：拒绝
    (target / ".p8-real-marker.json").unlink()
    code, payload = backfill()
    assert code == 2 and "requires an existing migrated target" in payload["error"]
    assert db_path.read_bytes() == before

    # 源在 target-root 内：拒绝（不读源、不写目标）
    marker = target / ".p8-real-marker.json"
    marker.write_text(json.dumps({"run_token": "x", "sources": {"homeroom": "x", "teaching": "x"}}), encoding="utf-8")
    inside = target / "inside-h.sqlite"
    shutil.copy(h, inside)
    code, payload = backfill(h_src=inside)
    assert code == 2 and "must not be inside target-root" in payload["error"]
    assert db_path.read_bytes() == before
