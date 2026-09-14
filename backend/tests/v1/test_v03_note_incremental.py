"""V03 回归（Q06/ 四轮审核反例）：档案 PATCH 修改必须进回退增量清单。

反例（修复前）：先建谈话档案并 create_backup，再经正式 PATCH
``/api/v1/homeroom/notes/{id}`` 修改 content 与 follow_up_done——API 200、
库中确已更新，但 ws_student_note 只有 created_at，export_incremental 返回
upserts=0；回退恢复旧档案且增量清单无内容可回放。

修复后（本用例冻结）：updated_at 越界必须捕获整行新值；回退库恢复旧值、
增量清单可回放出新值。全程真实 API + 交付的备份/恢复/增量函数，不用
演示 SQL 维护时间戳。
"""

import importlib.util
import sqlite3
import tempfile
from pathlib import Path

API = "/api/v1"

REPO = Path(__file__).resolve().parents[3]
_SCRIPT = REPO / "scripts" / "migration" / "rehearse_backup_restore.py"

# 只调用 rehearse 模块的标准库实现，不触发其 main()（不碰演练根目录）
_spec = importlib.util.spec_from_file_location("rehearse_backup_restore", _SCRIPT)
rb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rb)


def test_note_patch_edit_captured_in_incremental_and_replayable(client, v1_seed):
    """备份 → 正式 PATCH（content+follow_up_done）→ export_incremental 的
    upserts 含 ws_student_note 整行新值；restore_to 恢复旧值；增量清单
    可回放为新值。该行 created_at 在备份点之前——只有 updated_at 越界
    能发现它（V03 本质）。"""
    seed = v1_seed
    from app.db.models import engine

    db_path = engine.url.database
    assert db_path, "前置失效：必须在隔离 EXAM_TRACKER_DIR 下运行"

    # 1) 备份点之前：正式 API 建档案
    r = client.post(
        f"{API}/homeroom/students/{seed.jia_h_id}/notes",
        json={"date": "2026-09-01", "category": "谈话", "content": "V03 基线内容"},
    )
    assert r.status_code == 200, r.text
    note = r.json()

    with tempfile.TemporaryDirectory(prefix="v03-note-incr-") as tmp:
        tmp = Path(tmp)

        # 2) 一致性备份（时间上界取自 manifest.backup_taken_at）
        zip_path = rb.create_backup(db_path, str(tmp / "backups"), "v03")
        boundary = rb.read_manifest(zip_path)["backup_taken_at"]

        # 3) 备份点之后：正式 PATCH 修改 content + follow_up_done
        r = client.patch(
            f"{API}/homeroom/notes/{note['id']}",
            json={"content": "V03 修改后内容", "follow_up_done": 1},
        )
        assert r.status_code == 200, r.text

        con = sqlite3.connect(db_path)
        try:
            row = con.execute(
                "SELECT content, follow_up_done, created_at, updated_at "
                "FROM ws_student_note WHERE id=?",
                (note["id"],),
            ).fetchone()
        finally:
            con.close()
        assert row[0] == "V03 修改后内容" and row[1] == 1, "前置失效：PATCH 必须真实落库"
        assert row[2] is not None and row[3] is not None, "updated_at 必须随 PATCH 触碰"
        # created_at 不晚于备份点（行在快照里）→ 增量只能靠 updated_at 越界发现
        assert row[2] <= boundary < row[3]

        # 4) 增量：ws_student_note 整行新值必须进 upserts
        incr = rb.export_incremental(db_path, zip_path)
        assert "ws_student_note" in incr["tables_scanned"]
        up_notes = incr["upserts"].get("ws_student_note", [])
        hit = [x for x in up_notes if x["id"] == note["id"]]
        assert len(hit) == 1, f"档案修改必须进增量清单：{incr['counts']}"
        assert hit[0]["content"] == "V03 修改后内容"
        assert hit[0]["follow_up_done"] == 1
        assert incr["totals"]["upserts"] >= 1

        # 5) 只读回退：恢复备份点 → 旧值复原
        restored_db = rb.restore_to(zip_path, str(tmp / "restored"))
        con = sqlite3.connect(restored_db)
        try:
            old = con.execute(
                "SELECT content, follow_up_done FROM ws_student_note WHERE id=?",
                (note["id"],),
            ).fetchone()
        finally:
            con.close()
        assert old == ("V03 基线内容", 0), "回退后必须恢复备份点旧档案"

        # 6) 增量待回放：把 upsert 行应用回恢复库 → 修改找回（不宣称无损）
        con = sqlite3.connect(restored_db)
        try:
            con.execute(
                "UPDATE ws_student_note SET content=?, follow_up_done=?, updated_at=? "
                "WHERE id=?",
                (hit[0]["content"], hit[0]["follow_up_done"], hit[0]["updated_at"], note["id"]),
            )
            con.commit()
            replayed = con.execute(
                "SELECT content, follow_up_done FROM ws_student_note WHERE id=?",
                (note["id"],),
            ).fetchone()
        finally:
            con.close()
        assert replayed == ("V03 修改后内容", 1), "增量清单必须可回放出修改后档案"
