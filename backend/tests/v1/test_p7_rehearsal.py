"""P7 合成双库迁移演练管线用例（M01/M02/D01 + verify 对账）。

被测对象是 scripts/migration/ 下的独立脚本（子进程执行），不经过应用
TestClient：迁移演练进程必须自带 EXAM_TRACKER_DIR 隔离与红线自检，
本文件只驱动脚本并核对产物（目标库 sqlite3 原生只读 + 报告 JSON）。

范围声明：全部产物落在 .test-data/migration-rehearsal/（git 忽略）；
断言数字来自 build_synthetic_sources.py 的固定合成样本（常量见该脚本），
非真实生产库。整个文件预算 <120s（合成数据量小，子进程管线 6 次内）。

四分类口径（契约 v2 §4.1 Q05）：对账母集 = 两侧源库**全部非零业务表**
（manifest 逐表行数），逐行 mapped / shared（按事实升级）/ quarantined
（PendingImportRow 持久隔离）/ not_migrated（显式登记原因），
Σ = 源行全集、遗漏 0。
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_SCRIPTS = os.path.join(_REPO, "scripts", "migration")
_ROOT = os.path.join(_REPO, ".test-data", "migration-rehearsal")
_TARGET_DATA = os.path.join(_ROOT, "target", "data")
_BACKEND = os.path.join(_REPO, "backend")

# 合成样本固定对账数字（build_synthetic_sources.py 的口径；四分类母集 =
# 两侧源库全部非零表）：
# H 母集 87 = teacher1 + exam4 + subject24 + total3 + class_average1
#   + analysis_config1 + roster14 + hw5 + homework_setting1
#   + homework_semester1 + identity11 + alias14 + rollover_batch1
#   + roster_import_batch2 + imported_history2 + student_note2；
#   去向 mapped65 + shared4 + quarantined6 + not_migrated12。
#   隔离 6 = roster{_anon:秦壬, TMP20250999} + subject_score{111 占位,
#   113 撞号同名, 114 占位} + hw304 占位。
# T 母集 36 = teacher1 + exam2 + subject6 + total1 + class_average1
#   + analysis_config1 + roster6 + hw4 + homework_semester1 + identity2
#   + alias3 + teaching_class2 + member6；
#   去向 mapped18 + shared4 + quarantined2 + not_migrated12。
#   隔离 2 = member504(_anon:夏) + subject_score604(_anon:夏)。
H_TOTAL, T_TOTAL = 87, 36
GRAND_TOTAL = H_TOTAL + T_TOTAL  # 123
MAPPED, SHARED, QUARANTINED, NOT_MIGRATED = 83, 8, 8, 24


def _run_script(script: str, *args: str, timeout: int = 90,
                env_extra: dict | None = None,
                tracker_dir: str | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = _BACKEND
    env["EXAM_TRACKER_DIR"] = tracker_dir or _TARGET_DATA
    env["EXAM_TRACKER_BACKUP_DIR"] = os.path.join(_ROOT, "target", "backups")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, os.path.join(_SCRIPTS, script), *args],
        capture_output=True, text=True, env=env, timeout=timeout, cwd=_REPO,
    )


def _target_db() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{_TARGET_DATA}/db.sqlite?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _scalar(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


def _latest_stats() -> dict:
    with _target_db() as conn:
        row = conn.execute(
            "SELECT stats_json FROM migration_run ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(row[0])


def _report(name: str) -> dict:
    with open(os.path.join(_ROOT, "reports", name), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def pipeline():
    """build → 中断模拟（--to import_homeroom）→ 续跑全阶段 → M01 强制重入
    → D01 备份回退演练。产物供本模块全部用例断言。"""
    r = _run_script("build_synthetic_sources.py")
    assert r.returncode == 0, r.stderr

    # 清掉手动实验残留（若有）：目标库与报告必须从全新状态跑，
    # 否则残留的 completed 台账会把首步 --to 降级成只跑 verify
    for stale in ("target", "reports"):
        shutil.rmtree(os.path.join(_ROOT, stale), ignore_errors=True)

    # 中断模拟：只跑到 import_homeroom（M01 断点续跑路径）
    r = _run_script("run_rehearsal.py", "--to", "import_homeroom")
    assert r.returncode == 0, r.stderr
    with _target_db() as conn:
        assert _scalar(conn, "SELECT COUNT(*) FROM source_map") > 0

    # 续跑：缺省 --from = 第一个未完成阶段（import_teaching 起）
    r = _run_script("run_rehearsal.py")
    assert r.returncode == 0, r.stderr

    # M01：同摘要整管线强制重入（--from snapshot）→ 零新增
    r = _run_script("run_rehearsal.py", "--from", "snapshot")
    assert r.returncode == 0, r.stderr

    # D01：备份 → 恢复核对 → 新写入/增量导出/只读回退
    r = _run_script("rehearse_backup_restore.py")
    assert r.returncode == 0, r.stderr


def test_verify_four_way_classification(pipeline):
    """verify 四分类互斥完备（Q05）：母集 = 源库全部非零业务表（与 manifest
    逐表行数独立复核），Σ(mapped+shared+quarantined+not_migrated) = 母集。"""
    stats = _latest_stats()
    total = stats["stages"]["verify"]["classification_total"]
    assert total["mapped"] == MAPPED
    assert total["shared"] == SHARED
    assert total["quarantined"] == QUARANTINED
    assert total["not_migrated"] == NOT_MIGRATED
    assert (total["mapped"] + total["shared"] + total["quarantined"]
            + total["not_migrated"] == GRAND_TOTAL)
    report = _report("verification.json")
    assert report["ok"] is True
    # 母集独立复核：verification 的源行数必须等于 manifest 全部非零表之和
    manifest = json.load(open(os.path.join(_ROOT, "sources_manifest.json"),
                              encoding="utf-8"))
    for tag, expected in (("h", H_TOTAL), ("t", T_TOTAL)):
        cats = report["checks"]["classification"]["per_source"][tag]["counts"]
        nonzero = sum(n for n in manifest["sources"][tag]["rows"].values() if n > 0)
        assert cats["total"] == nonzero == expected
        assert (cats["total"] == cats["mapped"] + cats["shared"]
                + cats["quarantined"] + cats["not_migrated"])
        # not_migrated 每一张表都有显式原因登记（不静默丢弃）
        for t, reason in report["checks"]["classification"]["per_source"][tag][
                "not_migrated_reasons"].items():
            assert reason, (tag, t)
        # quarantined 计数与 PendingImportRow 持久层 1:1（Q04 真实门）
        with _target_db() as conn:
            fp = "h" if tag == "h" else "t"
            n = _scalar(conn, "SELECT COUNT(*) FROM pending_import_row"
                             " WHERE source_fingerprint LIKE ?", f"{fp}:%")
        assert n == cats["quarantined"]


def test_q04_quarantined_rows_never_in_business_tables(pipeline):
    """Q04：隔离行绝不进业务表（持久层直查），且隔离原行在待核实视图可
    完整查阅（raw_json 保原值）。业务可见性反例另见 API 用例。"""
    with _target_db() as conn:
        # 每条登记的原行都完整（raw_json 含来源学号），且该来源行在业务表
        # 无映射（SourceMap 绝不指向被隔离行）
        rows = conn.execute(
            "SELECT source_fingerprint, source_table, source_pk, data_domain,"
            " reason, raw_json FROM pending_import_row ORDER BY id").fetchall()
        assert len(rows) == QUARANTINED
        by_table = {}
        for fp, table, pk, domain, reason, raw in rows:
            assert domain in ("homeroom", "teaching") and reason
            raw_row = json.loads(raw)
            assert raw_row is not None
            by_table.setdefault((fp.split(":", 1)[0], table), set()).add(pk)
            m = _scalar(conn, "SELECT COUNT(*) FROM source_map WHERE"
                             " source_fingerprint=? AND source_table=? AND source_pk=?",
                        fp, table, pk)
            assert m == 0, (table, pk)
        # 隔离清单恰为构造器声明的 8 行（撞号 1 + 占位/临时 7）
        assert by_table[("h", "class_roster")] == {"_anon:秦壬", "TMP20250999"}
        assert by_table[("h", "subject_score")] == {"111", "113", "114"}
        assert by_table[("h", "homework_record")] == {"304"}
        assert by_table[("t", "teaching_class_member")] == {"504"}
        assert by_table[("t", "subject_score")] == {"604"}
        # 撞号同名独立身份：秦坤（row 身份）业务行不存在，秦乾 60 分在
        kun = conn.execute(
            "SELECT COUNT(*) FROM score_fact f JOIN ws_student_identity i"
            " ON i.id=f.identity_id WHERE i.display_name='秦坤'").fetchone()[0]
        assert kun == 0


def _api_probe() -> dict:
    """迁移目标库直接启动正式 API（子进程：主 pytest 进程 engine 已绑定
    conftest 临时目录）。教师绑定按应用自身配置补齐（迁移不迁 teacher）。"""
    probe = r'''
import json, sqlite3, sys
sys.path.insert(0, sys.argv[1])
from fastapi.testclient import TestClient

DATA = sys.argv[2]
con = sqlite3.connect(f"{DATA}/db.sqlite")
con.execute("DELETE FROM teacher")
con.execute("INSERT INTO teacher (name, school, target_class_high1, created_at)"
            " VALUES ('合成班主任','合成中学',6,'2026-09-12 00:00:00')")
cid = con.execute(
    "SELECT a.id FROM administrative_class a JOIN academic_year y ON y.id=a.academic_year_id"
    " WHERE a.grade=1 AND a.class_num=6 ORDER BY y.name LIMIT 1").fetchone()[0]
con.commit(); con.close()

from app.main import app
out = {}
with TestClient(app, base_url="http://localhost") as client:
    r = client.get("/api/v1/homeroom/students",
                   params={"class_id": cid, "academic_year_id": 1})
    out["roster"] = {"status": r.status_code,
                     "names": [s["name"] for s in r.json().get("students", [])]}
    r = client.get("/api/v1/scores", params={"mode": "homeroom", "academic_year_id": 1})
    rows = r.json().get("rows", [])
    out["scores_h"] = {"status": r.status_code, "rows": [
        {"name": x["name"], "subject": x["subject"], "score": x["score"],
         "conflict": x.get("shared_conflict")} for x in rows]}
    r = client.get("/api/v1/scores", params={"mode": "teaching", "academic_year_id": 1})
    trows = r.json().get("rows", [])
    out["scores_t"] = {"status": r.status_code, "rows": [
        {"name": x["name"], "subject": x["subject"], "score": x["score"]}
        for x in trows]}
print(json.dumps(out, ensure_ascii=False))
'''
    r = subprocess.run(
        [sys.executable, "-c", probe, _BACKEND, _TARGET_DATA],
        capture_output=True, text=True, timeout=90, cwd=_REPO,
        env={**os.environ, "EXAM_TRACKER_DIR": _TARGET_DATA,
             "EXAM_TRACKER_BACKUP_DIR": os.path.join(_ROOT, "target", "backups")},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def test_q04_quarantined_invisible_to_official_api(pipeline):
    """Q04 API 反例：迁移目标直接启动正式应用后，考试学生/成绩接口不返回
    隔离行（_anon:秦壬、TMP 秦子、撞号秦坤、T 域 _anon:夏）；已映射正常行
    与冲突注记不受影响。审核反例：旧实现这些行曾以 cohort_size=7 出现。"""
    out = _api_probe()
    # 名册：占位/临时学号学生绝不在正式名册
    assert out["roster"]["status"] == 200
    assert out["roster"]["names"] == ["秦甲", "秦乙", "秦丙", "秦丁", "秦乾"]
    assert not ({"秦壬", "秦子"} & set(out["roster"]["names"]))
    # H 成绩：隔离 3 行（壬 55 / 子 NULL / 坤 62）不可见；乾 60（同学号
    # 正常行）可见；甲物理 90 与 T 域 91 冲突注记保留
    h = out["scores_h"]
    assert h["status"] == 200
    leaked = [x for x in h["rows"] if x["name"] in ("秦壬", "秦子", "秦坤")]
    assert leaked == []
    got = {(x["name"], x["subject"], x["score"]) for x in h["rows"]}
    assert ("秦乾", "物理", 60.0) in got
    conflicts = [(x["name"], x["subject"], x["conflict"]) for x in h["rows"]]
    assert ("秦甲", "物理", {"teaching_score": 91.0}) in conflicts
    # T 成绩：占位成员夏不可见；春/庚等已映射成员正常可见；仅任教学科
    t = out["scores_t"]
    assert t["status"] == 200
    assert {x["name"] for x in t["rows"]} == {"秦甲", "秦乙", "秦春"}
    assert all(x["subject"] == "物理" for x in t["rows"])


def test_integrity_and_foreign_keys(pipeline):
    report = _report("verification.json")
    assert report["checks"]["integrity"]["integrity_check"] == "ok"
    assert report["checks"]["integrity"]["fk_violations"] == 0


def test_h_scores_field_by_field(pipeline):
    """H 全科逐字段抽样比对（含缺考 NULL 与等级分），目标库直接核对。"""
    report = _report("verification.json")
    assert report["checks"]["h_field_compare"]["field_diffs"] == []
    assert report["checks"]["h_field_compare"]["compared_rows"] == 24  # subject21(隔离3)+total3
    with _target_db() as conn:
        def fact_of(alias_value, exam_name, subject=None, total_type=None):
            return conn.execute(
                "SELECT f.score, f.grade_score FROM score_fact f"
                " JOIN ws_student_alias a ON a.identity_id=f.identity_id"
                "  AND a.data_domain='homeroom'"
                " WHERE a.alias_value=? AND f.exam_name=?"
                "  AND f.subject IS ? AND f.total_type IS ?",
                (alias_value, exam_name, subject, total_type),
            ).fetchone()
        # 甲高一物理 90（与 T 域 91 构成冲突对）；丁B（110）缺考 NULL 不转 0
        assert fact_of("20240601", "2024期中", subject="物理")["score"] == 90.0
        assert fact_of("20240704", "2024期中", subject="物理")["score"] is None
        # 等级分逐字段保留（高二）
        row = fact_of("20250601", "2025期中", subject="物理")
        assert (row["score"], row["grade_score"]) == (86.0, 88.0)
        # 总分口径
        assert fact_of("20240601", "2024期中", total_type="主三门")["score"] == 270.0


def test_prefix_ids_kept_verbatim_and_not_merged(pipeline):
    """前缀学号原值保留、绝不剥壳：G1::/g1-/_anon:/TMP 各一，且与复用裸号
    不归并（G1::20230101 与 20230101 是两个身份）。"""
    with _target_db() as conn:
        def identity_of(alias_value):
            row = conn.execute(
                "SELECT identity_id FROM ws_student_alias WHERE alias_value=?"
                " AND data_domain='homeroom'", (alias_value,)).fetchone()
            return row[0] if row else None
        for kept in ("G1::20230101", "g1-20230105", "_anon:秦壬", "TMP20250999"):
            assert identity_of(kept) is not None, kept
        assert identity_of("G1::20230101") != identity_of("20230101")


def test_t_other_classes_only_in_teaching_domain(pipeline):
    """T 其他成员（春/夏/庚）只在 teaching 域；T 指纹成绩事实全在 teaching 域。
    春无源 alias → 独立行身份（按 display_name 定位），绝不冒认 H 域身份。"""
    report = _report("verification.json")
    assert report["checks"]["teaching_domain"]["wrong_domain"] == []
    with _target_db() as conn:
        spring = conn.execute(
            "SELECT id FROM ws_student_identity WHERE data_domain='teaching'"
            " AND display_name='秦春'").fetchone()[0]
        # 春在 homeroom 域零痕迹（无行政班别名/无选课/无成绩）
        assert conn.execute(
            "SELECT COUNT(*) FROM ws_student_alias WHERE identity_id=? AND data_domain='homeroom'",
            (spring,)).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM score_fact WHERE identity_id=? AND data_domain='homeroom'",
            (spring,)).fetchone()[0] == 0


def test_m01_repeat_import_zero_new(pipeline):
    """M01：同摘要强制重入 → imported=0、命中跳过=已映射源行全量
    （H=exam4+alias14+roster12+subject21+total3+hw4=58，隔离 6 行无映射
    不计入跳过；T=exam2+alias3+tclass2+member5+subject4+hw4=20）；无重复
    业务行，隔离登记也不翻倍。"""
    stats = _latest_stats()
    h = stats["stages"]["import_homeroom"]
    t = stats["stages"]["import_teaching"]
    link = stats["stages"]["link_suggest"]
    assert h["imported"] == 0 and h["skipped_mapped"] == 58 and h["quarantined"] == 6
    assert t["imported"] == 0 and t["skipped_mapped"] == 20 and t["quarantined"] == 2
    assert link["links"] == 0 and link["students"] == 0  # 关联已存在，不重复建
    assert link["auto_created"] == 0
    with _target_db() as conn:
        # 无重复行（中断重试 + 两次全跑后仍恰好一份）
        assert _scalar(conn, "SELECT COUNT(*) FROM enrollment") == 12  # 14 源行-2 隔离
        assert _scalar(conn, "SELECT COUNT(*) FROM score_fact") == 28  # H24(隔离3)+T4
        assert _scalar(conn, "SELECT COUNT(*) FROM homework_submission") == 8
        assert _scalar(conn, "SELECT COUNT(*) FROM homeroom_teaching_link") == 1
        assert _scalar(conn, "SELECT COUNT(*) FROM linked_student") == 2
        assert _scalar(conn, "SELECT COUNT(*) FROM pending_import_row") == 8
        # SourceMap = H(业务 58 + 身份 12=组11+撞号独立身份1)
        #           + T(业务 20 + 身份 5=组2+独立行身份3)
        assert _scalar(conn, "SELECT COUNT(*) FROM source_map") == 95


def test_m01_resume_after_interrupt(pipeline):
    """M01 断点续跑：--to import_homeroom 后再全跑，MigrationRun 记录各阶段
    且台账最终 completed。"""
    stats = _latest_stats()
    assert sorted(stats["completed_stages"]) == sorted([
        "snapshot", "import_homeroom", "import_teaching", "link_suggest",
        "conflict_report", "undo_audit", "verify"])
    with _target_db() as conn:
        run = conn.execute(
            "SELECT status FROM migration_run ORDER BY id DESC LIMIT 1").fetchone()
    assert run[0] == "completed"


def test_m02_undo_audit(pipeline):
    """M02 撤销侧：分类清单存在、可解析引用 convertible、幽灵引用 read-only。"""
    payload = _report("undo_audit.json")
    summary = payload["summary"]
    assert summary["batches"] == 3 and summary["items"] == 17
    assert summary["convertible"] == 14 and summary["read_only"] == 3
    readonly_refs = {i["student_id"] for i in payload["items"] if i["status"] == "read-only"}
    assert readonly_refs == {"20240699", "20230699", "20240101"}
    # convertible 引用全部能在目标库解析到 homeroom 身份
    with _target_db() as conn:
        for item in payload["items"]:
            if item["status"] == "convertible":
                assert conn.execute(
                    "SELECT COUNT(*) FROM ws_student_alias WHERE alias_value=?"
                    " AND data_domain='homeroom'", (item["student_id"],)).fetchone()[0] == 1


def test_m02_conflict_report(pipeline):
    """M02 成绩侧：同场同科两域不同值 → 冲突清单非空且不静默覆盖。"""
    payload = _report("conflicts.json")
    assert payload["compared"] == 2
    assert len(payload["conflicts"]) == 1
    conflict = payload["conflicts"][0]
    assert (conflict["homeroom_value"], conflict["teaching_value"]) == (90.0, 91.0)
    assert conflict["resolution"] == "kept-both-no-overwrite"
    # 两域事实都在（未覆盖）
    with _target_db() as conn:
        n = _scalar(conn,
            "SELECT COUNT(*) FROM score_fact f"
            " JOIN ws_student_alias a ON a.identity_id=f.identity_id"
            " AND a.data_domain=f.data_domain"
            " WHERE a.alias_value='20240601' AND f.subject='物理' AND f.exam_name='2024期中'")
        assert n == 2  # homeroom 90 与 teaching 91 并存


def test_lost_columns_and_reconciliation(pipeline):
    """被丢弃学科/总分行只统计不恢复；名册/缺交条数对账（源行 = 已映射
    + 隔离，隔离不冒充已导入）。"""
    lost = _report("lost_columns.json")
    assert lost["non_teaching_subjects"] == ["语文"]
    assert lost["non_teaching_subject_rows"] == 1 and lost["total_score_rows"] == 1
    with _target_db() as conn:
        # H 班主任全科合法保留语文（homeroom 域）；teaching 域绝不出现语文
        assert _scalar(conn, "SELECT COUNT(*) FROM score_fact"
                             " WHERE subject='语文' AND data_domain='homeroom'") > 0
        assert _scalar(conn, "SELECT COUNT(*) FROM score_fact"
                             " WHERE subject='语文' AND data_domain='teaching'") == 0
        assert _scalar(conn, "SELECT COUNT(*) FROM score_fact WHERE total_type IS NOT NULL"
                             " AND data_domain='teaching'") == 0
        assert _scalar(conn, "SELECT COUNT(*) FROM source_map WHERE source_fingerprint LIKE 't:%'"
                             " AND source_table='total_score'") == 0
    report = _report("verification.json")
    recon = report["checks"]["reconciliation"]
    assert recon["h_roster"] == {"source": 14, "enrollment": 12, "quarantined": 2}
    assert recon["h_homework"]["source_rows"] == 5
    assert recon["h_homework"]["submissions"] == 4
    assert recon["h_homework"]["quarantined"] == 1
    assert recon["h_homework"]["missing"] == 3 and recon["h_homework"]["excused"] == 1
    assert recon["t_homework"]["source_rows"] == 4
    assert recon["t_homework"]["submissions"] == 4
    assert recon["t_homework"]["quarantined"] == 0
    assert recon["t_homework"]["status_counts"]["submitted"] == 2
    assert recon["t_homework"]["status_counts"]["missing"] == 2


def test_q08_homework_semantics_field_by_field(pipeline):
    """Q08 逐字段语义：teaching 域 assignment.subject = 教师任教学科
    （Teacher 配置的「物理」），homework_type = 源旧 subject 列原值
    （校本作业/周末作业/试卷订正 = 作业种类，不是学科）；同日同种两行
    → 一个批次两个提交；状态原值映射保留。"""
    report = _report("verification.json")
    sem = report["checks"]["homework_semantics"]
    assert sem["teaching_subject"] == "物理"
    assert sem["source_homework_types"] == ["周末作业", "校本作业", "试卷订正"]
    assert sem["assignments"] == 3 and sem["bad"] == []
    with _target_db() as conn:
        assigns = conn.execute(
            "SELECT id, subject, homework_type, assigned_date FROM homework_assignment"
            " WHERE data_domain='teaching' ORDER BY assigned_date, homework_type").fetchall()
        # 3 个批次 = (2024-10-11 校本) + (10-18 周末) + (10-25 订正)
        assert [(a[1], a[2], a[3]) for a in assigns] == [
            ("物理", "校本作业", "2024-10-11"),
            ("物理", "周末作业", "2024-10-18"),
            ("物理", "试卷订正", "2024-10-25"),
        ]
        subs = conn.execute(
            "SELECT a.homework_type, s.submission_status, s.evaluation"
            " FROM homework_submission s JOIN homework_assignment a ON a.id=s.assignment_id"
            " WHERE a.data_domain='teaching' ORDER BY a.homework_type, s.submission_status").fetchall()
        # 甲缺交/乙已交同批；evaluation 原值保留（已订正）
        assert [tuple(s) for s in subs] == [
            ("周末作业", "missing", None),
            ("校本作业", "missing", None),
            ("校本作业", "submitted", "已订正"),
            ("试卷订正", "submitted", None),
        ]
        # H 域作业不适用 Q08 转换：种类占位 legacy、subject 仍是源学科原值
        legacy = conn.execute(
            "SELECT DISTINCT subject, homework_type FROM homework_assignment"
            " WHERE data_domain='homeroom'").fetchall()
        assert all(h[1] == "legacy" and h[0] in ("数学", "物理") for h in legacy)


def test_d01_backup_restore_and_rollback(pipeline):
    """D01（Q06 修订口径）：一致性备份恢复抽样逐项一致、资产按 manifest
    sha256 核对；增量以备份点时间戳为界覆盖新增/修改/删除并声明待回放。"""
    result = _report("backup_restore.json")
    assert result["restore_check"]["ok"] is True
    assert result["restore_check"]["spot_queries"] >= 12
    assert result["restore_check"]["link_sample"] == [
        {"subject": "物理", "grade": 1, "class_num": 6, "label": "6"}]
    # 资产全集（raw 上传原件 + 导出）随库打包、逐文件摘要核对
    assert result["restore_check"]["assets_restored"] >= 2
    assert (result["restore_check"]["assets_sha256_verified"]
            == result["manifest_summary"]["files"])
    rollback = result["rollback"]
    # WAL 已提交数据不丢（备份发生在连接存活期间， 反例 1→0 必须 1→1）
    assert rollback["wal_committed_data_in_backup"] is True
    assert rollback["target_matches_backup_point"] is True
    assert rollback["post_cutover_rows_gone"] is True
    assert rollback["edited_row_value_restored"] is True
    assert rollback["deleted_row_restored"] is True
    assert "不宣称无损" in rollback["declaration"]
    incremental = _report("incremental.json")
    # 增量边界取自备份 manifest；不依赖 source 字符串
    assert incremental["boundary"] == result["manifest_summary"]["backup_taken_at"]
    assert set(incremental["upserts"]) >= {"score_fact", "homework_submission"}
    assert incremental["totals"]["upserts"] >= 3  # 成绩新增 + 成绩订正 + 作业编辑
    deleted_id = rollback["post_write_changes"]["deleted_score_fact_id"]
    assert incremental["deletes"]["score_fact"] == [deleted_id]
    assert incremental["totals"]["deletes"] == 1
    # 只读回退后目标库回到备份点（无 post-cutover 行）
    with _target_db() as conn:
        assert _scalar(conn, "SELECT COUNT(*) FROM score_fact WHERE source='post-cutover-write'") == 0


def test_red_line_rejects_path_outside_root(pipeline):
    """红线自检：EXAM_TRACKER_DIR 指向演练根之外时脚本必须拒绝执行且零写入。"""
    outside = os.path.join(_REPO, ".test-data", "migration-rehearsal-redline", "data")
    r = subprocess.run(
        [sys.executable, os.path.join(_SCRIPTS, "run_rehearsal.py"), "--root", _ROOT,
         "--to", "snapshot"],
        capture_output=True, text=True, timeout=60, cwd=_REPO,
        env={**os.environ, "EXAM_TRACKER_DIR": outside, "PYTHONPATH": _BACKEND},
    )
    assert r.returncode == 2
    assert "红线" in (r.stderr + r.stdout)
    assert not os.path.exists(os.path.join(outside, "db.sqlite"))


def test_red_line_build_rejects_out_of_root(pipeline):
    """红线自检（build_synthetic_sources）：--out 在演练根之外必须 exit 2
    且不创建/不清空目标目录（脚本会 rmtree 输出目录，越界即任意目录删除）。"""
    outside = os.path.join(_REPO, ".test-data", "migration-rehearsal-redline", "sources")
    r = _run_script("build_synthetic_sources.py", "--out", outside)
    assert r.returncode == 2
    assert "红线" in (r.stderr + r.stdout)
    assert not os.path.exists(outside)


def test_red_line_backup_restore_rejects_real_path_root(pipeline):
    """红线自检（rehearse_backup_restore）：演练根含 ~/.exam-tracker 字样
    必须拒绝执行（root 校验先于任何文件访问）。"""
    bogus_root = os.path.join(_REPO, ".test-data", "mock-.exam-tracker-probe")
    r = _run_script("rehearse_backup_restore.py", "--root", bogus_root)
    assert r.returncode == 2
    assert "红线" in (r.stderr + r.stdout)
    assert not os.path.exists(bogus_root)


def test_m01_interrupt_rollback_and_rerun(pipeline):
    """M01 真实事务中断：import_teaching 阶段中途注入失败（成绩段已写、
    作业段未写时抛出）→ 阶段整体回滚（T 域业务行/映射/隔离登记零残留、
    H 阶段成果保留、run 标记 failed），重跑从断点续跑 → verify 全绿且
    与一次跑完的目标库逐表同构（无重复行）。"""
    irun = os.path.join(_ROOT, "interrupt-run")
    shutil.rmtree(irun, ignore_errors=True)
    os.makedirs(os.path.join(irun, "target"), exist_ok=True)
    # 同一合成源（复制 sources 与 manifest，run_token 随 sha256 一致派生）
    shutil.copytree(os.path.join(_ROOT, "sources"), os.path.join(irun, "sources"))
    shutil.copy2(os.path.join(_ROOT, "sources_manifest.json"),
                 os.path.join(irun, "sources_manifest.json"))
    itracker = os.path.join(irun, "target", "data")

    fail = _run_script("run_rehearsal.py", "--root", irun,
                       env_extra={"REHEARSAL_FAIL_INJECT": "import_teaching"},
                       tracker_dir=itracker)
    assert fail.returncode != 0
    assert "REHEARSAL_FAIL_INJECT" in (fail.stderr + fail.stdout)
    idb = sqlite3.connect(f"file:{itracker}/db.sqlite?mode=ro", uri=True)
    try:
        # T 阶段回滚零残留；H 阶段成果保留
        assert idb.execute(
            "SELECT COUNT(*) FROM score_fact WHERE data_domain='teaching'").fetchone()[0] == 0
        assert idb.execute("SELECT COUNT(*) FROM teaching_class_member").fetchone()[0] == 0
        assert idb.execute(
            "SELECT COUNT(*) FROM pending_import_row WHERE source_fingerprint LIKE 't:%'"
        ).fetchone()[0] == 0
        assert idb.execute(
            "SELECT COUNT(*) FROM score_fact WHERE data_domain='homeroom'").fetchone()[0] == 24
        run = idb.execute(
            "SELECT status, stats_json FROM migration_run ORDER BY id DESC LIMIT 1").fetchone()
        assert run[0] == "failed"
        assert "REHEARSAL_FAIL_INJECT" in json.loads(run[1])["stages"]["import_teaching"]["error"]
    finally:
        idb.close()

    # 重跑（无注入）：从第一个未完成阶段续跑 → 全绿
    rerun = _run_script("run_rehearsal.py", "--root", irun, tracker_dir=itracker)
    assert rerun.returncode == 0, rerun.stderr
    idb = sqlite3.connect(f"file:{itracker}/db.sqlite?mode=ro", uri=True)
    try:
        # 与一次跑完的主管线目标库逐表同构（中断重试零重复）
        for table, expected in (
            ("score_fact", 28), ("enrollment", 12), ("homework_submission", 8),
            ("homeroom_teaching_link", 1), ("linked_student", 2),
            ("pending_import_row", 8), ("source_map", 95),
        ):
            n = idb.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert n == expected, (table, n, expected)
        t_facts = idb.execute(
            "SELECT COUNT(*) FROM score_fact WHERE data_domain='teaching'").fetchone()[0]
        assert t_facts == 4
        status = idb.execute(
            "SELECT status FROM migration_run ORDER BY id DESC LIMIT 1").fetchone()[0]
        assert status == "completed"
    finally:
        idb.close()
    report = json.load(open(os.path.join(irun, "reports", "verification.json"),
                            encoding="utf-8"))
    assert report["ok"] is True
    assert report["checks"]["classification"]["total"]["total"] == GRAND_TOTAL
    shutil.rmtree(irun, ignore_errors=True)
