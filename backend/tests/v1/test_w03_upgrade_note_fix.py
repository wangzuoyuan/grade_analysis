"""W03/R08 收口反例：0008 旧库 → ensure_app_schema 升级 → 0010 回填可用。

 要求"补 0008→0009 的真实旧库升级/档案读取/PATCH/备份增量反例，不能用最新
metadata.create_all 证明升级通过"。本用例全程走真实路径：
1. 子进程 alembic upgrade 0008（停在档案表尚无 updated_at 的版本）；
2. sqlite3 直插一条合法档案行（带 FK 父行）；
3. 子进程调用 ensure_app_schema() —— 必须经迁移链升到当前 head 而非 create_all；
4. 升级后旧行完整可读、updated_at 为 NULL（历史行不伪造时间）；
5. ORM 修改 content（模拟 PATCH 落库路径，onupdate 触碰 updated_at）；
6. 用演练备份函数：修改前 create_backup → 修改后 export_incremental 必须包含该行
   （V03 修复在真实升级库上生效，增量可回放）。
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO = BACKEND_DIR.parent
_SCRIPT = REPO / "scripts" / "migration" / "rehearse_backup_restore.py"


def _run(cmd, env, cwd=BACKEND_DIR):
    proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, f"{cmd} failed:\n{proc.stdout}\n{proc.stderr}"
    return proc


def test_w03_0008_library_upgrades_and_note_fix_becomes_available():
    with tempfile.TemporaryDirectory(prefix="v1-w03-") as tmp:
        env = dict(os.environ)
        env["EXAM_TRACKER_DIR"] = tmp
        env["EXAM_TRACKER_BACKUP_DIR"] = os.path.join(tmp, "backups")
        db = Path(tmp) / "db.sqlite"

        # 1) 真实停在 0008（ws_student_note 存在、updated_at 尚不存在）
        _run([sys.executable, "-m", "alembic", "upgrade", "0008"], env)
        con = sqlite3.connect(str(db))
        cols = {r[1] for r in con.execute("PRAGMA table_info(ws_student_note)").fetchall()}
        assert "updated_at" not in cols
        # 合法父行 + 一条档案
        con.execute("INSERT INTO academic_year (name, start_date, end_date) VALUES ('2025-2026','2025-09-01','2026-07-15')")
        con.execute("INSERT INTO ws_student_identity (data_domain, display_name) VALUES ('homeroom','W03学生')")
        con.execute(
            "INSERT INTO score_fact (data_domain, academic_year_id, exam_name, exam_date, class_ref_id, "
            "identity_id, subject, total_type, subject_key, total_key, score, source, data_revision) "
            "VALUES ('homeroom',1,'W03旧考试','2025-10-06',NULL,1,'物理',NULL,'物理','',88,'fixture',1)"
        )
        con.execute(
            "INSERT INTO ws_student_note (data_domain, person_id, date, category, content, follow_up_done) "
            "VALUES ('homeroom', 1, '2025-10-01', '谈话', '旧库时期档案', 0)"
        )
        con.execute(
            "INSERT INTO teaching_class (academic_year_id,subject,label,sort_order) "
            "VALUES (1,'物理','已有成员班',1)"
        )
        con.execute(
            "INSERT INTO teaching_class_member "
            "(teaching_class_id,identity_id,valid_from,source) VALUES (1,1,'2025-09-01','fixture')"
        )
        con.commit()
        con.close()

        # 先停在 0012，模拟“早期已完成真实迁移、但尚未投影 H 全交台账”的日常库。
        _run([sys.executable, "-m", "alembic", "upgrade", "0012"], env)
        con = sqlite3.connect(str(db))
        con.execute(
            "INSERT INTO administrative_class (academic_year_id,grade,class_num,label) "
            "VALUES (1,2,11,'高二11班')"
        )
        con.execute(
            "INSERT INTO source_archive_record "
            "(source_fingerprint,source_table,source_pk,data_domain,archive_reason,payload_json) "
            "VALUES (?,?,?,?,?,?)",
            ("h:test-backfill", "homework_collection", "77", "homeroom", "legacy_archive",
             json.dumps({"id": 77, "date": "2025-10-03", "subject": "化学", "grade": 2, "class_num": 11}, ensure_ascii=False)),
        )
        con.commit()
        con.close()

        # 2) 应用启动路径升级（不许 create_all 证明——ensure_app_schema 内部走迁移链）
        _run(
            [sys.executable, "-c", "from app.db.schema import ensure_app_schema; ensure_app_schema()"],
            env,
        )
        con = sqlite3.connect(str(db))
        version = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        from app.db.schema import ALEMBIC_HEAD
        assert version == ALEMBIC_HEAD, f"应升至当前 head {ALEMBIC_HEAD}，实际 {version}"
        assert con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='source_projection_map'").fetchone()
        projected = con.execute(
            "SELECT a.subject,a.assigned_date,a.expected_members_json FROM homework_assignment a "
            "JOIN source_projection_map m ON m.target_id=a.id AND m.target_table='homework_assignment' "
            "WHERE m.source_fingerprint='h:test-backfill' AND m.source_table='homework_collection'"
        ).fetchone()
        assert projected == ("化学", "2025-10-03", "[]")
        row = con.execute("SELECT id, content, updated_at FROM ws_student_note").fetchone()
        assert row is not None and row[1] == "旧库时期档案"
        assert row[2] is None  # 历史行不伪造时间
        note_id = row[0]
        score_cols = {r[1] for r in con.execute("PRAGMA table_info(score_fact)")}
        assert {"source_exam_date", "exam_date_precision"} <= score_cols
        assert con.execute("SELECT source_exam_date, exam_date_precision FROM score_fact").fetchone() == ("2025-10-06", "day")
        assert con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='source_archive_record'").fetchone()
        con.close()

        # 3) 修改前备份 → ORM 修改（PATCH 落库路径，onupdate 触碰）→ 增量必须含该行
        sys.path.insert(0, str(REPO / "scripts" / "migration"))
        os.environ["EXAM_TRACKER_DIR"] = tmp  # 备份脚本红线自检需要
        os.environ["EXAM_TRACKER_BACKUP_DIR"] = env["EXAM_TRACKER_BACKUP_DIR"]
        try:
            import importlib

            br = importlib.import_module("rehearse_backup_restore")
            backup_dir = Path(env["EXAM_TRACKER_BACKUP_DIR"])
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_zip = Path(br.create_backup(str(db), str(backup_dir), "w03-pre-edit"))

            code = (
                "from app.db.models import SessionLocal\n"
                "from app.db.workspace_models import WsStudentNote\n"
                "db = SessionLocal()\n"
                f"note = db.get(WsStudentNote, {note_id})\n"
                "note.content = '升级后修改的档案'\n"
                "db.commit()\n"
                "print(note.updated_at is not None)\n"
            )
            proc = _run([sys.executable, "-c", code], env)
            assert "True" in proc.stdout, f"ORM 修改后 updated_at 应被触碰:\n{proc.stdout}"

            incremental = br.export_incremental(str(db), str(backup_zip))
            tables = incremental.get("upserts", {})
            assert "ws_student_note" in tables, f"增量必须包含档案修改，实际表: {sorted(tables)}"
            assert any(r.get("content") == "升级后修改的档案" for r in tables["ws_student_note"])
            payload = json.dumps(incremental, ensure_ascii=False)
            assert "升级后修改的档案" in payload
        finally:
            sys.path.pop(0)
