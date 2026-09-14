"""P7 Q06/Q07 交付回归（ 三轮审核反例，独立于 run_rehearsal 管线）。

Q06（一致性备份与完整恢复）：
- WAL 场景：已提交但仍在 -wal 的数据，经交付的备份/恢复函数后必须
  1→1（ 反例是 1→0：旧实现直接 zip 主文件丢 WAL）；
- 增量以备份点时间戳（manifest.backup_taken_at）为界，覆盖新增/修改/
  删除且含非 score 业务表，不依赖演示专用 source 字符串；
- 资产全集（raw 上传原件/导出/应用内备份目录）恢复并逐文件核对 sha256。

Q07（可启动候选）：
- 按 Dockerfile COPY 范围复刻目录布局（app + alembic.ini，无 alembic/），
  已 head 的库 ensure_app_schema 必须成功（ 复现脚本退出 1 的场景）；
- 空库 + 无迁移目录必须响亮失败（不能静默半初始化）；
- ALEMBIC_HEAD 常量与迁移链一致（漂移守门）；
- compose config 校验（含 build 配置），本机无 docker 时如实 SKIP。

红线：全部产物在临时目录（系统 tempdir），绝不触碰 ~/.exam-tracker 与
.sources/；不依赖 scripts/migration 的其他脚本（并行返工互斥）。
"""

import importlib.util
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
BACKEND_DIR = Path(__file__).resolve().parents[2]
_SCRIPT = REPO / "scripts" / "migration" / "rehearse_backup_restore.py"
_DEPLOY = REPO / "deploy"

# 测试只调用本模块的标准库实现，不触发其 main()（不碰演练根目录）
rehearse = importlib.util.spec_from_file_location("rehearse_backup_restore", _SCRIPT)
rb = importlib.util.module_from_spec(rehearse)
rehearse.loader.exec_module(rb)


def _alembic_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _ensure_schema_subprocess(data_dir: Path, pythonpath: Path,
                              cwd: Path = BACKEND_DIR) -> subprocess.CompletedProcess:
    """子进程跑 ensure_app_schema（参照 test_f01 模式：显式独立数据目录）。
    python -c 的 sys.path[0] 是 cwd、优先于 PYTHONPATH——docker-layout 用例
    必须把 cwd 切到布局目录，否则 app 会从真实 backend 解析（测不到镜像布局）。"""
    env = dict(os.environ)
    env["EXAM_TRACKER_DIR"] = str(data_dir)
    env["EXAM_TRACKER_BACKUP_DIR"] = str(data_dir / "backups")
    env["PYTHONPATH"] = str(pythonpath)
    return subprocess.run(
        [sys.executable, "-c",
         "from app.db.schema import ensure_app_schema; ensure_app_schema()"],
        cwd=str(cwd), env=env, capture_output=True, text=True, timeout=600,
    )


def _build_head_db(data_dir: Path) -> None:
    """用真实 backend 布局把全新数据目录初始化到 head。"""
    data_dir.mkdir(parents=True, exist_ok=True)  # sqlite 不自动建父目录
    proc = _ensure_schema_subprocess(data_dir, BACKEND_DIR)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _sql(db: Path, statement: str, *params) -> None:
    con = sqlite3.connect(str(db))
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute(statement, params)
        con.commit()
    finally:
        con.close()


def _fetch(db: Path, sql: str, *params):
    con = sqlite3.connect(str(db))
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _after(boundary: str) -> str:
    """严格晚于备份点的时间戳（SQLite DateTime 同构格式）：让变更用例
    与时钟精度解耦，确定性落在边界之后。"""
    t = datetime.strptime(boundary, "%Y-%m-%d %H:%M:%S.%f") + timedelta(seconds=1)
    return t.strftime("%Y-%m-%d %H:%M:%S.%f")


PAST = "2026-01-01 00:00:00.000000"


# ─────────────────────────── Q06 ───────────────────────────


def test_q06_wal_committed_data_survives_backup_restore():
    """WAL 场景（ 反例 1→0 必须变 1→1）：写入后保持连接打开（数据在
    -wal），create_backup/restore_to 后行数与内容一致。"""
    with tempfile.TemporaryDirectory(prefix="q06-wal-") as tmp:
        tmp = Path(tmp)
        data, backups, restored = tmp / "data", tmp / "backups", tmp / "restored"
        _build_head_db(data)
        db = data / "db.sqlite"

        con = sqlite3.connect(str(db))
        try:
            con.execute("PRAGMA journal_mode=WAL")  # WAL 是持久属性，随库落盘
            con.execute(
                "INSERT INTO academic_year (name, start_date, end_date, created_at) "
                "VALUES ('2025-2026', '2025-09-01', '2026-06-30', ?)", (PAST,))
            con.commit()
            # 不 close、不 checkpoint：已提交数据只存在于 -wal
            assert (data / "db.sqlite-wal").exists(), "前置失效：数据应仍在 WAL"
        finally:
            pass  # 连接故意保持打开，贯穿备份动作（复刻活跃写入场景）

        zip_path = rb.create_backup(str(db), str(backups), "walcase")
        con.close()  # 备份完成后才释放（备份发生在连接存活期间）

        rb.restore_to(zip_path, str(restored))
        rows = _fetch(restored / "db.sqlite", "SELECT name FROM academic_year")
        assert rows == [("2025-2026",)], "已提交但仍在 WAL 的数据在恢复后丢失"
        manifest = rb.read_manifest(zip_path)
        by_arc = {f["arcname"]: f for f in manifest["files"]}
        assert "db.sqlite" in by_arc and len(by_arc["db.sqlite"]["sha256"]) == 64


def test_q06_incremental_captures_edits_inserts_deletes_across_tables():
    """增量按备份点时间戳：旧行修改 + 作业编辑 + 新增（含非 score 表）进
    upserts；删除行进主键清单；未动行绝不混入（不依赖 source 字符串）。"""
    with tempfile.TemporaryDirectory(prefix="q06-incr-") as tmp:
        tmp = Path(tmp)
        data, backups = tmp / "data", tmp / "backups"
        _build_head_db(data)
        db = data / "db.sqlite"

        # 外键父行先行（显式 id=1，供子表引用）
        _sql(db, "INSERT INTO academic_year (id, name, start_date, end_date, "
                 "created_at) VALUES (1, '2025-2026', '2025-09-01', '2026-06-30', ?)",
             PAST)
        _sql(db, "INSERT INTO administrative_class (id, academic_year_id, grade, "
                 "class_num, created_at, updated_at) VALUES (1, 1, 2, 6, ?, ?)",
             PAST, PAST)
        _sql(db, "INSERT INTO ws_student_identity (id, data_domain, display_name, "
                 "created_at, updated_at) VALUES (1, 'homeroom', '测甲', ?, ?)",
             PAST, PAST)
        _sql(db, "INSERT INTO score_fact (data_domain, academic_year_id, exam_name, "
                 "identity_id, subject, subject_key, total_key, score, source, "
                 "data_revision, created_at, updated_at) "
                 "VALUES ('homeroom', 1, '2026期中', 1, '物理', '物理', '', 90.0, "
                 "'rehearsal', 1, ?, ?)", PAST, PAST)
        _sql(db, "INSERT INTO score_fact (data_domain, academic_year_id, exam_name, "
                 "identity_id, subject, subject_key, total_key, score, source, "
                 "data_revision, created_at, updated_at) "
                 "VALUES ('homeroom', 1, '2026期中', 1, '化学', '化学', '', 80.0, "
                 "'rehearsal', 1, ?, ?)", PAST, PAST)
        _sql(db, "INSERT INTO homework_assignment (data_domain, class_ref_id, "
                 "academic_year_id, subject, homework_type, assigned_date, "
                 "batch_token, expected_members_json, revision, status, "
                 "created_at, updated_at) "
                 "VALUES ('homeroom', 1, 1, '物理', '日常作业', '2026-01-05', "
                 "'q06-token', '[]', 1, 'active', ?, ?)", PAST, PAST)
        _sql(db, "INSERT INTO homework_submission (assignment_id, person_id, "
                 "submission_status, revision, created_at, updated_at) "
                 "VALUES (1, 1, 'missing', 1, ?, ?)", PAST, PAST)
        _sql(db, "INSERT INTO enrollment (admin_class_id, identity_id, valid_from, "
                 "status, created_at, updated_at) VALUES (1, 1, '2025-09-01', "
                 "'active', ?, ?)", PAST, PAST)

        zip_path = rb.create_backup(str(db), str(backups), "incrbase")
        boundary = rb.read_manifest(zip_path)["backup_taken_at"]
        later = _after(boundary)

        # 备份点之后的变化：修改旧行（成绩订正）、作业编辑、新增笔记
        # （非 score 表）、新增成绩、删除一条备份点已有的成绩
        _sql(db, "UPDATE score_fact SET score=95.0, updated_at=? WHERE subject='物理'",
             later)
        _sql(db, "UPDATE homework_submission SET submission_status='submitted', "
                 "evaluation='Q06 编辑', updated_at=? WHERE id=1", later)
        _sql(db, "INSERT INTO ws_student_note (data_domain, person_id, date, "
                 "category, content, follow_up_done, created_at) "
                 "VALUES ('homeroom', 1, '2026-09-12', '谈话', '增量用例', 0, ?)", later)
        _sql(db, "INSERT INTO score_fact (data_domain, academic_year_id, exam_name, "
                 "identity_id, subject, subject_key, total_key, score, source, "
                 "data_revision, created_at, updated_at) "
                 "VALUES ('homeroom', 1, '2026期末', 1, '物理', '物理', '', 88.0, "
                 "'rehearsal', 1, ?, ?)", later, later)
        _sql(db, "DELETE FROM score_fact WHERE subject='化学'")

        incr = rb.export_incremental(str(db), zip_path)

        assert incr["boundary"] == boundary, "增量边界必须取自备份 manifest"
        assert "alembic_version" not in incr["tables_scanned"]
        up = incr["upserts"]
        # 修改的旧行（score=95）与新增行都在成绩清单里
        fact_rows = {r["id"]: r for r in up["score_fact"]}
        edited = _fetch(db, "SELECT id FROM score_fact WHERE subject='物理' "
                            "AND exam_name='2026期中'")[0][0]
        assert fact_rows[edited]["score"] == 95.0, "旧行修改必须按 updated_at 越界进入清单"
        assert any(r["exam_name"] == "2026期末" for r in fact_rows.values())
        # 作业编辑进清单（非 score 域修改）
        assert [r["submission_status"] for r in up["homework_submission"]] == ["submitted"]
        assert [r["content"] for r in up["ws_student_note"]] == ["增量用例"]
        # 删除清单：备份点有、当前无的主键
        base_ids = set(_fetch(db, "SELECT id FROM score_fact"))
        assert incr["deletes"]["score_fact"] and incr["deletes"]["score_fact"][0] not in base_ids
        # 未动的 enrollment 行绝不混入
        assert "enrollment" not in up and "enrollment" not in incr["deletes"]
        totals = incr["totals"]
        assert totals["upserts"] == (len(up["score_fact"])
                                     + len(up["homework_submission"])
                                     + len(up["ws_student_note"]))
        assert totals["deletes"] == 1


def test_q06_restore_recovers_all_assets_with_sha256():
    """资产全集：raw 上传原件 / homework_exports / 应用内备份目录随库打包，
    恢复后逐文件 sha256 双重核对（restore_to 内置校验通过即断言）。"""
    with tempfile.TemporaryDirectory(prefix="q06-assets-") as tmp:
        tmp = Path(tmp)
        data, backups, restored = tmp / "data", tmp / "backups", tmp / "restored"
        _build_head_db(data)
        db = data / "db.sqlite"
        (data / "raw").mkdir()
        (data / "raw" / "upload.txt").write_text("上传原件", encoding="utf-8")
        (data / "homework_exports").mkdir()
        (data / "homework_exports" / "exp.csv").write_text("科目,分数\n物理,90\n",
                                                           encoding="utf-8")
        (data / "backups").mkdir()
        (data / "backups" / "old.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)

        zip_path = rb.create_backup(str(db), str(backups), "assets")
        manifest = rb.read_manifest(zip_path)
        arcs = {f["arcname"] for f in manifest["files"]}
        assert arcs == {"db.sqlite", os.path.join("raw", "upload.txt"),
                        os.path.join("homework_exports", "exp.csv"),
                        os.path.join("backups", "old.zip")}

        rb.restore_to(zip_path, str(restored))  # 摘要不符会在此抛 RuntimeError
        for entry in manifest["files"]:
            target = restored / entry["arcname"]
            assert target.exists(), entry["arcname"]
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            assert digest == entry["sha256"], entry["arcname"]
        assert _fetch(restored / "db.sqlite", "SELECT COUNT(*) FROM academic_year")[0][0] == 0

        # 篡改 zip 内容 → 恢复必须拒绝（摘要门真实生效）
        tampered = tmp / "tampered.zip"
        with zipfile.ZipFile(zip_path) as src, \
                zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                payload = src.read(info)
                if info.filename == "db.sqlite":
                    payload = payload + b"\x00"
                dst.writestr(info, payload)
        with pytest.raises(RuntimeError):
            rb.restore_to(str(tampered), str(tmp / "restored2"))


# ─────────────────────────── Q07 ───────────────────────────


def _docker_layout(tmp: Path) -> Path:
    """按 backend/Dockerfile COPY 范围复刻目录布局：app + alembic.ini，
    刻意不含 alembic/（ Q07 复现脚本的场景）。"""
    layout = tmp / "docker-layout"
    shutil.copytree(BACKEND_DIR / "app", layout / "app")
    shutil.copy2(BACKEND_DIR / "alembic.ini", layout / "alembic.ini")
    assert not (layout / "alembic").exists()
    return layout


def test_q07_docker_layout_head_db_boots_without_alembic_dir():
    """无 alembic/ 目录的最小布局：已 head 的库 ensure_app_schema 成功
    （head 短路零文件系统访问）。"""
    with tempfile.TemporaryDirectory(prefix="q07-layout-") as tmp:
        tmp = Path(tmp)
        data = tmp / "data"
        _build_head_db(data)          # 真实布局初始化到 head
        head = _alembic_head()
        assert _fetch(data / "db.sqlite",
                      "SELECT version_num FROM alembic_version") == [(head,)]

        layout = _docker_layout(tmp)
        proc = _ensure_schema_subprocess(data, layout, cwd=layout)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _fetch(data / "db.sqlite",
                      "SELECT version_num FROM alembic_version") == [(head,)]


def test_q07_docker_layout_empty_db_without_alembic_dir_fails_loudly():
    """空库 + 无迁移目录：无法初始化必须响亮失败，绝不静默半初始化。"""
    with tempfile.TemporaryDirectory(prefix="q07-empty-") as tmp:
        tmp = Path(tmp)
        data = tmp / "data"
        data.mkdir()
        layout = _docker_layout(tmp)
        proc = _ensure_schema_subprocess(data, layout, cwd=layout)
        assert proc.returncode != 0, "缺迁移目录时空库初始化不得假成功"
        assert "alembic" in (proc.stderr + proc.stdout).lower()


def test_q07_alembic_head_constant_matches_migration_chain():
    """ALEMBIC_HEAD 常量与迁移链 head 一致：常量落后只是退化（走完整
    路径），超前会把落后库误判为 head，绝不允许。"""
    from app.db.schema import ALEMBIC_HEAD

    assert ALEMBIC_HEAD == _alembic_head()


def test_q07_compose_config_valid_with_build_and_python_healthcheck():
    """compose config 校验（含 build 配置与占位 env）；本机无 docker 时
    如实 SKIP（不假报通过）。文本断言兜底：健康检查不得依赖 wget。"""
    compose_text = (_DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    assert "context: ../backend" in compose_text
    # 健康检查必须是镜像内必备的 python（wget/curl 不在镜像内）
    assert 'test: ["CMD", "python", "-c"' in compose_text
    assert "urllib" in compose_text

    if shutil.which("docker") is None:
        pytest.skip("本机无 docker：compose config 校验跳过（现场补跑）")
    with tempfile.TemporaryDirectory(prefix="q07-compose-") as tmp:
        tmp = Path(tmp)
        shutil.copy2(_DEPLOY / "docker-compose.yml", tmp / "docker-compose.yml")
        shutil.copy2(_DEPLOY / "Caddyfile", tmp / "Caddyfile")
        (tmp / "backend.env").write_text("", encoding="utf-8")
        (tmp / "backend").mkdir()  # build 上下文占位（config 要求路径存在）
        proc = subprocess.run(
            ["docker", "compose", "config", "--quiet"],
            cwd=str(tmp), capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
