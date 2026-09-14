"""正式双库本地迁移入口。

只接受明确传入的两份 SQLite 快照；源永远以 URI ``mode=ro`` 打开。
``--preflight-only`` 只向 stdout 输出脱敏 JSON，且在任何 target 目录创建前
完成 schema、完整性、FK 与决策摘要检查。正式运行将无法安全业务化的行写入
``source_archive_record``，使全集对账不以报告标签掩盖历史资料。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import shutil
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[2]
H_REQUIRED = {"exam", "class_roster", "student_identity", "student_alias", "subject_score", "total_score", "homework_record"}
T_REQUIRED = {"exam", "teaching_class", "teaching_class_member", "student_identity", "student_alias", "subject_score", "homework_record", "teacher"}
H_LEGACY_ORDER = ("teacher", "analysis_config", "homework_setting", "homework_semester", "exam", "student_identity", "student_alias", "class_roster", "homework_collection", "homework_record", "subject_score", "total_score", "class_average", "imported_history", "student_note", "special_record", "student_change_log", "upload", "roster_import_batch", "rollover_confirm_batch")

# 审计分类（projection_audit.json）：未投影/部分投影来源表的逐表说明。
# 口径承接 run_rehearsal.py NOT_MIGRATED_REASONS，改写为真实迁移语境；
# 完全投影的表 reason 为 null。homework_collection 的语义已对照旧应用
# 源码（src-homeroom HomeworkCollection docstring + 连续缺交预警用法）确认：
# 它是「某班某天某科收过作业（全交日）」的收交台账，只用于补全旧预警的
# 时间轴，不含逐人事实；目标 homework_assignment 需要应交成员分母快照，
# 源库没有该事实，重建批次会伪造提交，故整表归档不投影。
PROJECTION_REASONS = {
    ("h", "teacher"): "教师配置行：合并版教师绑定是目标应用自身配置，不迁移源行（归档可追溯）",
    ("h", "class_average"): "班均为派生统计：目标按需由 score_fact 重算，不迁移原行（归档可追溯）",
    ("h", "analysis_config"): "段位阈值配置：目标应用自身配置，不迁移（归档可追溯）",
    ("h", "homework_setting"): "作业键值配置：active_grade/学期锚点已消费用于学年推导，目标等价物由应用自管",
    ("h", "homework_collection"): "旧收交台账（某班某天某科收过作业/全交日）：仅用于旧连续缺交预警补全时间轴，无逐人事实；目标 homework_assignment 需应交成员分母快照，源无该事实，重建会伪造提交，不投影（归档可追溯）",
    ("h", "imported_history"): "手工历史成绩：目标等价模型本批次未建，不混入全年级排名（归档可追溯）",
    ("h", "student_note"): "班主任私密档案：档案隐私边界，不自动迁移（归档可追溯）",
    ("h", "student_change_log"): "学生名册变更过程日志：仅服务旧应用审计流，无目标等价（归档可追溯）",
    ("h", "upload"): "上传文件元数据：文件本体按 assets manifest 摘要复制，元数据行未验证不投影",
    ("h", "roster_import_batch"): "旧导入撤销快照批次：无目标等价撤销流，批次行不迁移（归档可追溯）",
    ("h", "rollover_confirm_batch"): "旧结转确认撤销快照批次：无目标等价撤销流，批次行不迁移（归档可追溯）",
    ("h", "exam"): "考试元数据：名称/日期已展开进 score_fact（exam_name/source_exam_date/精度），无独立目标表（归档可追溯）",
    ("t", "teacher"): "教师配置行：任教学科已用于作业/成绩投影语义转换，行本身无目标等价模型（归档可追溯）",
    ("t", "class_average"): "班均为派生统计：目标按需由 score_fact 重算，不迁移原行（归档可追溯）",
    ("t", "analysis_config"): "段位阈值配置：目标应用自身配置，不迁移（归档可追溯）",
    ("t", "homework_setting"): "作业键值配置：active_grade/学期锚点已消费用于学年推导，目标等价物由应用自管",
    ("t", "exam"): "考试元数据：名称/日期已展开进 score_fact（exam_name/source_exam_date/精度），无独立目标表（归档可追溯）",
    ("t", "class_roster"): "T 花名册：成员事实由 teaching_class_member 投影承载，花名册行不重复迁移（归档可追溯）",
    ("t", "upload"): "上传文件元数据：文件本体按 assets manifest 摘要复制，元数据行未验证不投影",
    ("t", "total_score"): "教学域遗留总分列：合并版教学域只存任教学科成绩，总分不迁移（归档可追溯）",
    ("t", "subject_score"): "非任教学科成绩行不迁移：教学域只投影任教学科成绩（余量归档可追溯）",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def source_db(path: Path) -> sqlite3.Connection:
    return sqlite3.connect("file:" + quote(str(path.resolve())) + "?mode=ro", uri=True)


def tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def primary_key_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return source-declared PK columns in ordinal order (including text/composite PKs)."""
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [r[1] for r in sorted(rows, key=lambda r: r[5]) if r[5] > 0]


def stable_source_key(row: sqlite3.Row, pk_columns: list[str]) -> str:
    if pk_columns:
        # JSON avoids delimiter ambiguity for composite/text keys and remains reproducible.
        return json.dumps([[name, row[name]] for name in pk_columns], ensure_ascii=False, default=str, separators=(",", ":"))
    return str(row["__p8_rowid__"])


def source_rows(conn: sqlite3.Connection, table: str, pk_columns: list[str]):
    # Keep physical rowid only for FK diagnosis; stable source keys still use declared PK.
    query = f'SELECT rowid AS __p8_rowid__, * FROM "{table}"'
    return conn.execute(query)


def fk_summary(conn: sqlite3.Connection) -> dict[str, int]:
    # SQLite 返回 (child-table,rowid,parent-table,fk-index)；只输出类别计数。
    return dict(sorted(Counter(row[0] for row in conn.execute("PRAGMA foreign_key_check")).items()))


def fk_rowids(conn: sqlite3.Connection) -> set[tuple[str, int]]:
    """Private per-row diagnosis; manifest exposes only aggregated table counts."""
    return {(str(row[0]), int(row[1])) for row in conn.execute("PRAGMA foreign_key_check")}


def preflight_one(tag: str, path: Path, required: set[str]) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"{tag} source is not a file")
    conn = source_db(path)
    try:
        names = tables(conn)
        missing = sorted(required - names)
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise ValueError(f"{tag} integrity_check failed")
        counts: dict[str, int] = {}
        columns: dict[str, list[str]] = {}
        for name in sorted(names):
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')]
            columns[name] = cols
            counts[name] = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        if missing:
            raise ValueError(f"{tag} missing required tables: {','.join(missing)}")
        date_precision = {}
        if "exam" in names:
            date_precision = dict(conn.execute("SELECT COALESCE(length(exam_date),0), COUNT(*) FROM exam GROUP BY length(exam_date)"))
            # Other formats are not safe to infer.
            if any(key not in (0, 7, 10) for key in date_precision):
                raise ValueError(f"{tag} unsupported exam_date precision")
        return {"sha256": sha256(path), "tables": counts, "columns": columns,
                "foreign_key_violations": fk_summary(conn), "exam_date_lengths": date_precision}
    finally:
        conn.close()


def preflight(h_path: Path, t_path: Path, decisions: Path | None) -> dict[str, Any]:
    if h_path.resolve() == t_path.resolve():
        raise ValueError("homeroom and teaching sources must differ")
    result = {"homeroom": preflight_one("homeroom", h_path, H_REQUIRED),
              "teaching": preflight_one("teaching", t_path, T_REQUIRED)}
    result["run_token"] = hashlib.sha256((result["homeroom"]["sha256"] + result["teaching"]["sha256"]).encode()).hexdigest()[:48]
    result["decisions"] = {"provided": bool(decisions), "accepted": False}
    if decisions:
        payload = json.loads(decisions.read_text(encoding="utf-8"))
        expected = payload.get("source_sha256", {})
        if expected != {"homeroom": result["homeroom"]["sha256"], "teaching": result["teaching"]["sha256"]}:
            raise ValueError("decision source_sha256 does not match snapshots")
        if not isinstance(payload.get("links", []), list) or not isinstance(payload.get("identity_resolutions", []), list):
            raise ValueError("decision links and identity_resolutions must be lists")
        decision_hash = sha256(decisions)
        result["decisions"]["accepted"] = True
        result["decisions"]["link_rows"] = len(payload.get("links", []))
        result["decisions"]["identity_resolution_rows"] = len(payload.get("identity_resolutions", []))
        result["decisions"]["excluded_rows"] = len(payload.get("exclusions", []))
        result["decisions"]["sha256"] = decision_hash
        result["run_token"] = hashlib.sha256((result["run_token"] + decision_hash).encode()).hexdigest()[:48]
    return result


def academic_year(raw: str | None) -> str | None:
    if not raw or len(raw) not in (7, 10):
        return None
    year, month = int(raw[:4]), int(raw[5:7])
    return f"{year}-{year + 1}" if month >= 8 else f"{year - 1}-{year}"


def parsed_exam_date(raw: str | None) -> tuple[date | None, str]:
    if raw and len(raw) == 10:
        return date.fromisoformat(raw), "day"
    if raw and len(raw) == 7:
        return None, "month"
    return None, "unknown"


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def mirror_homeroom_legacy(source_path: Path, target_db: Path) -> dict[str, int]:
    """Copy H legacy tables 1:1, refusing a schema that would lose any source column.

    This runs before any ws projection so existing H routes/configuration retain their
    original source rows. It uses an independent target transaction and never opens
    the source except in SQLite read-only mode.
    """
    src = source_db(source_path); src.row_factory = sqlite3.Row
    dst = sqlite3.connect(target_db)
    try:
        src_tables = tables(src)
        target_tables = tables(dst)
        ordered = [t for t in H_LEGACY_ORDER if t in src_tables] + sorted(src_tables - set(H_LEGACY_ORDER))
        copied: dict[str, int] = {}
        dst.execute("PRAGMA foreign_keys=ON")
        for table in ordered:
            if table not in target_tables:
                raise ValueError(f"target missing legacy H table: {table}")
            src_cols = [r[1] for r in src.execute(f"PRAGMA table_info({_q(table)})")]
            dst_info = dst.execute(f"PRAGMA table_info({_q(table)})").fetchall()
            dst_cols = {r[1]: r for r in dst_info}
            missing = [c for c in src_cols if c not in dst_cols]
            if missing:
                raise ValueError(f"legacy H column incompatible {table}: {','.join(missing)}")
            required_extra = [r[1] for r in dst_info if r[3] and r[4] is None and r[1] not in src_cols and not r[5]]
            if required_extra:
                raise ValueError(f"legacy H target requires absent columns {table}: {','.join(required_extra)}")
            existing = dst.execute(f"SELECT COUNT(*) FROM {_q(table)}").fetchone()[0]
            if existing:
                raise ValueError(f"legacy H target table not empty: {table}")
            rows = src.execute(f"SELECT * FROM {_q(table)}").fetchall()
            if rows:
                cols_sql = ",".join(_q(c) for c in src_cols)
                placeholders = ",".join("?" for _ in src_cols)
                dst.executemany(f"INSERT INTO {_q(table)} ({cols_sql}) VALUES ({placeholders})", ([r[c] for c in src_cols] for r in rows))
            copied[table] = len(rows)
        fk = list(dst.execute("PRAGMA foreign_key_check"))
        if fk:
            raise ValueError(f"legacy H mirror FK violations: {len(fk)}")
        dst.commit()
        return copied
    except Exception:
        dst.rollback()
        raise
    finally:
        dst.close(); src.close()


def project_workspace_facts(
    h_path: Path,
    t_path: Path,
    target_db: Path,
    decisions_payload: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Conservative H/T projections from already-validated legacy shapes.

    No cross-domain link is written. Month-only exams carry NULL event dates and are
    therefore excluded by the existing sharing gate. Rows without an alias/member
    proof remain archive-only (the caller archives every source row independently).
    """
    dst = sqlite3.connect(target_db); dst.row_factory = sqlite3.Row
    srcs = [("homeroom", "h", source_db(h_path)), ("teaching", "t", source_db(t_path))]
    counts = Counter()
    identity_resolutions = (decisions_payload or {}).get("identity_resolutions", [])
    try:
        dst.execute("PRAGMA foreign_keys=ON")

        def one(sql, args=()): return dst.execute(sql, args).fetchone()
        def ensure_year_name(name: str) -> int:
            row = one("SELECT id FROM academic_year WHERE name=?", (name,))
            if row: return row[0]
            start = int(name[:4])
            dst.execute("INSERT INTO academic_year(name,start_date,end_date) VALUES (?,?,?)", (name, f"{start}-09-01", f"{start + 1}-07-15"))
            return one("SELECT last_insert_rowid()")[0]
        def exam_year(raw: str | None) -> int:
            name = academic_year(raw)
            if not name: raise ValueError("exam_date is required to derive academic year")
            return ensure_year_name(name)
        def shifted_year(anchor: str, delta: int) -> int:
            start = int(anchor[:4]) + delta
            return ensure_year_name(f"{start}-{start + 1}")
        def source_key(src, table, row):
            return stable_source_key(row, primary_key_columns(src, table))
        def mapped(domain, fp, table, row, target_table, kind, src):
            return one("SELECT target_id FROM source_projection_map WHERE data_domain=? AND source_fingerprint=? AND source_table=? AND source_pk=? AND target_table=? AND projection_kind=?", (domain, fp, table, source_key(src, table, row), target_table, kind))
        def map_to(domain, fp, table, row, target_table, target_id, kind, src):
            dst.execute("INSERT OR IGNORE INTO source_projection_map(data_domain,source_fingerprint,source_table,source_pk,target_table,target_id,projection_kind,status) VALUES (?,?,?,?,?,?,?,?)", (domain, fp, table, source_key(src, table, row), target_table, target_id, kind, "projected"))
        def current_anchor(src):
            if "homework_semester" not in tables(src):
                raise ValueError("homework_semester is required to anchor current academic year")
            semesters = src.execute("SELECT * FROM homework_semester").fetchall()
            current_rows = [r for r in semesters if "is_current" in r.keys() and int(r["is_current"] or 0) == 1 and "start_date" in r.keys() and r["start_date"]]
            if len(current_rows) != 1:
                raise ValueError("exactly one current homework_semester with start_date is required")
            anchor = academic_year(current_rows[0]["start_date"])
            if not anchor:
                raise ValueError("current homework_semester start_date cannot derive academic year")
            values = {r["key"]: r["value"] for r in src.execute("SELECT * FROM homework_setting") if "key" in r.keys() and "value" in r.keys()}
            try: active_grade = int(values["active_grade"])
            except (KeyError, TypeError, ValueError) as exc: raise ValueError("homework_setting active_grade is required") from exc
            return anchor, active_grade
        def ensure_pending(data_domain, fp, table, row, reason, src):
            pk = source_key(src, table, row)
            if one("SELECT id FROM pending_import_row WHERE source_fingerprint=? AND source_table=? AND source_pk=?", (fp, table, pk)):
                return
            dst.execute("INSERT INTO pending_import_row(source_fingerprint,source_table,source_pk,data_domain,reason,raw_json) VALUES (?,?,?,?,?,?)",
                        (fp, table, pk, data_domain, reason, json.dumps({k: row[k] for k in row.keys()}, ensure_ascii=False, default=str)))
        def ensure_assignment(data_domain, class_ref_id, year_id, subject, homework_type, assigned, token):
            row = one("SELECT id FROM homework_assignment WHERE batch_token=?", (token,))
            if row: return row[0]
            dst.execute("INSERT INTO homework_assignment(data_domain,class_ref_id,academic_year_id,homework_type,assigned_date,batch_token,expected_members_json,revision,status,subject) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (data_domain, class_ref_id, year_id, homework_type, assigned, token, "[]", 1, "active", subject))
            return one("SELECT last_insert_rowid()")[0]
        def ensure_submission(assignment_id, person_id, status, evaluation):
            dst.execute("INSERT OR IGNORE INTO homework_submission(assignment_id,person_id,submission_status,evaluation,revision) VALUES (?,?,?,?,1)", (assignment_id, person_id, status, evaluation))
            return one("SELECT id FROM homework_submission WHERE assignment_id=? AND person_id=?", (assignment_id, person_id))[0]

        # ── 学期并集（用户指令：两版学期设置取并集避免冲突）──
        # 学年归属按 academic_year(start_date) 推导；同学年内日期重叠的源行
        # 合并为一行：周期取并集、名称取周期为超集（跨度最大）的那行，被合并
        # 行在 stats 里计数 semester_merged，绝不静默丢弃。全表恰好一行
        # is_current=1；mode 一律 manual（手工快照事实，不冒充自动推算）。
        semester_rows = []
        for semester_domain, semester_tag, semester_src in srcs:
            semester_src.row_factory = sqlite3.Row
            for r in semester_src.execute("SELECT * FROM homework_semester ORDER BY id"):
                ay_name = academic_year(r["start_date"])
                if not ay_name or not r["end_date"]:
                    raise ValueError("homework_semester row cannot derive academic year or lacks end_date")
                semester_rows.append({"domain": semester_domain, "tag": semester_tag, "src": semester_src, "row": r, "fp": f"{semester_tag}:{sha256(Path(h_path if semester_tag == 'h' else t_path))}",
                                      "ay": ay_name, "start": r["start_date"], "end": r["end_date"], "name": r["name"], "current": int(r["is_current"] or 0)})
        semester_by_year = defaultdict(list)
        for item in semester_rows:
            semester_by_year[item["ay"]].append(item)
        current_candidates = []
        for ay_name, items in sorted(semester_by_year.items()):
            year_id = ensure_year_name(ay_name)
            items.sort(key=lambda i: (i["start"], i["end"], i["tag"]))
            groups, open_group = [], None
            for item in items:
                if open_group is not None and item["start"] <= open_group["end"]:
                    open_group["members"].append(item)
                    open_group["end"] = max(open_group["end"], item["end"])
                    open_group["start"] = min(open_group["start"], item["start"])
                else:
                    open_group = {"start": item["start"], "end": item["end"], "members": [item]}
                    groups.append(open_group)
            for group in groups:
                # 名称取周期为超集的行（跨度最大；并列取更晚结束、更早开始、再按 tag 序，确定性）。
                donor = max(group["members"], key=lambda i: ((date.fromisoformat(i["end"]) - date.fromisoformat(i["start"])).days, i["end"], i["start"]))
                is_current = 1 if any(m["current"] for m in group["members"]) else 0
                existing = one("SELECT id,start_date,end_date FROM ws_homework_semester WHERE academic_year_id=? AND name=?", (year_id, donor["name"]))
                if existing:
                    if (existing["start_date"], existing["end_date"]) != (group["start"], group["end"]):
                        raise ValueError(f"ws_homework_semester drift for {ay_name}/{donor['name']}")
                    target_id = existing["id"]
                else:
                    dst.execute("INSERT INTO ws_homework_semester(academic_year_id,name,start_date,end_date,is_current,mode) VALUES (?,?,?,?,?,'manual')",
                                (year_id, donor["name"], group["start"], group["end"], 0))
                    target_id = one("SELECT last_insert_rowid()")[0]
                if is_current:
                    current_candidates.append((target_id, group["end"], group["start"]))
                for member in group["members"]:
                    kind = "semester" if member is donor else "semester_merged"
                    map_to(member["domain"], member["fp"], "homework_semester", member["row"], "ws_homework_semester", target_id, kind, member["src"])
                    counts[kind] += 1
        # 全表恰好一行 is_current=1：多候选时保留结束最晚的合并行（确定性），其余归零。
        if current_candidates:
            keeper = max(current_candidates, key=lambda c: (c[1], c[2]))[0]
            dst.execute("UPDATE ws_homework_semester SET is_current=0 WHERE is_current=1 AND id<>?", (keeper,))
            dst.execute("UPDATE ws_homework_semester SET is_current=1 WHERE id=?", (keeper,))
        # 1d：作业分年用「学期覆盖优先」——日期被某并集学期窗口覆盖 → 该学期
        # 所属学年（如 2026-08 落「暑假」→ AY2025-2026，尽管 month>=8 规则会误判
        # 新学年）；未被任何窗口覆盖 → 回退 month>=9（9 月起为新学年，其余归
        # 上学年）。窗口来自本次运行已投影的 ws_homework_semester（学期投影先于
        # 作业执行）。H/T 作业分年共用同一规则。
        semester_windows = [(row[0], row[1], row[2]) for row in dst.execute(
            "SELECT s.start_date, s.end_date, y.name FROM ws_homework_semester s JOIN academic_year y ON y.id=s.academic_year_id ORDER BY s.start_date, s.end_date")]
        def homework_year_name(raw: str | None) -> str | None:
            if not raw or len(raw) != 10:
                return None
            for start, end, name in semester_windows:
                if start <= raw <= end:
                    return name
            year, month = int(raw[:4]), int(raw[5:7])
            return f"{year}-{year + 1}" if month >= 9 else f"{year - 1}-{year}"

        for domain, tag, src in srcs:
            src.row_factory = sqlite3.Row
            fp = f"{tag}:{sha256(Path(h_path if tag == 'h' else t_path))}"
            anchor_year, active_grade = current_anchor(src)
            exams = {r["id"]: r for r in src.execute("SELECT * FROM exam")}
            identities, aliases = {}, {}
            for r in src.execute("SELECT * FROM student_identity"):
                old = mapped(domain, fp, "student_identity", r, "ws_student_identity", "identity", src)
                if old: ident = old[0]
                else:
                    dst.execute("INSERT INTO ws_student_identity(data_domain,display_name,note) VALUES (?,?,?)", (domain, r["display_name"], "legacy projection")); ident = one("SELECT last_insert_rowid()")[0]
                    map_to(domain, fp, "student_identity", r, "ws_student_identity", ident, "identity", src)
                identities[r["id"]] = ident
            for r in src.execute("SELECT * FROM student_alias"):
                ident = identities.get(r["identity_id"])
                if ident is None: continue
                old = mapped(domain, fp, "student_alias", r, "ws_student_alias", "alias", src)
                if old: alias_id = old[0]
                else:
                    dst.execute("INSERT OR IGNORE INTO ws_student_alias(identity_id,alias_value,data_domain,source,alias_scope) VALUES (?,?,?,?,?)", (ident, r["student_id"], domain, f"migration:{tag}", "none"))
                    alias_id = one("SELECT id FROM ws_student_alias WHERE identity_id=? AND alias_value=? AND data_domain=?", (ident, r["student_id"], domain))[0]
                    map_to(domain, fp, "student_alias", r, "ws_student_alias", alias_id, "alias", src)
                aliases[r["student_id"]] = ident
            # 域内人工身份裁决：只接受绑定快照的决策文件，源别名必须真实存在于
            # 指定来源表，目标别名必须唯一且姓名与确认值一致。它只接通本域既有
            # identity，不创建跨域同一人推断。
            for resolution in identity_resolutions:
                if resolution.get("domain") != domain:
                    continue
                source_table = resolution.get("source_table")
                source_alias = resolution.get("source_alias")
                target_alias = resolution.get("target_alias")
                expected_name = resolution.get("expected_name")
                if source_table not in tables(src) or not all(isinstance(v, str) and v for v in (source_alias, target_alias, expected_name)):
                    raise ValueError("invalid identity resolution")
                columns = {r[1] for r in src.execute(f"PRAGMA table_info({_q(source_table)})")}
                if "student_id" not in columns:
                    raise ValueError("identity resolution source table lacks student_id")
                source_count = src.execute(
                    f"SELECT COUNT(*) FROM {_q(source_table)} WHERE student_id=?", (source_alias,)
                ).fetchone()[0]
                target_ident = aliases.get(target_alias)
                if source_count == 0 or target_ident is None:
                    raise ValueError("identity resolution alias not found")
                target_name = one("SELECT display_name FROM ws_student_identity WHERE id=? AND data_domain=?", (target_ident, domain))
                if target_name is None or target_name[0] != expected_name:
                    raise ValueError("identity resolution expected_name mismatch")
                aliases[source_alias] = target_ident
                dst.execute(
                    "INSERT OR IGNORE INTO ws_student_alias(identity_id,alias_value,data_domain,source,alias_scope) VALUES (?,?,?,?,?)",
                    (target_ident, source_alias, domain, "manual_confirm:p8", "none"),
                )
                counts["identity_resolution"] += 1
            # UX01：来源行自带姓名时保留为 display_name（class_roster /
            # teaching_class_member / subject_score 有 name 列；total_score、
            # homework_record 无名列 → None）。身份无名时用来源姓名补齐（只填
            # 空，绝不覆盖既有名）；同身份跨表异名不自动并人，计数
            # identity_name_conflict 留候选。
            def source_row_name(source_row):
                if "name" not in source_row.keys(): return None
                value = source_row["name"]
                if isinstance(value, str) and value.strip(): return value.strip()
                return None
            identity_names: dict[int, str | None] = {}
            def identity_for(sid, source_table, source_row):
                if sid not in aliases:
                    old = mapped(domain, fp, source_table, source_row, "ws_student_identity", "score_only_identity", src)
                    if old:
                        ident = old[0]
                    else:
                        dst.execute("INSERT INTO ws_student_identity(data_domain,display_name,note) VALUES (?,?,?)", (domain, source_row_name(source_row), "legacy score-only identity")); ident = one("SELECT last_insert_rowid()")[0]
                        map_to(domain, fp, source_table, source_row, "ws_student_identity", ident, "score_only_identity", src)
                        dst.execute("INSERT INTO ws_student_alias(identity_id,alias_value,data_domain,source,alias_scope) VALUES (?,?,?,?,?)", (ident, sid, domain, f"migration:{tag}", "none"))
                    aliases[sid] = ident
                ident = aliases[sid]
                incoming = source_row_name(source_row)
                if incoming:
                    known = identity_names.get(ident)
                    if known is None:
                        row = one("SELECT display_name FROM ws_student_identity WHERE id=? AND data_domain=?", (ident, domain))
                        known = row["display_name"] if row else None
                        if known is not None: identity_names[ident] = known
                    if known is None:
                        identity_names[ident] = incoming
                        counts["identity_name_backfilled"] += 1
                        dst.execute("UPDATE ws_student_identity SET display_name=? WHERE id=? AND data_domain=? AND display_name IS NULL", (incoming, ident, domain))
                    elif known != incoming:
                        counts["identity_name_conflict"] += 1
                return ident
            def admin_class(year_id, grade, class_num):
                row = one("SELECT id FROM administrative_class WHERE academic_year_id=? AND grade=? AND class_num=?", (year_id, grade, class_num))
                if row: return row[0]
                dst.execute("INSERT INTO administrative_class(academic_year_id,grade,class_num) VALUES (?,?,?)", (year_id, grade, class_num)); return one("SELECT last_insert_rowid()")[0]
            def ensure_enrollment(cid, ident, seat_no=None, status="active"):
                start = one("SELECT start_date FROM academic_year WHERE id=?", (one("SELECT academic_year_id FROM administrative_class WHERE id=?", (cid,))[0],))[0]
                dst.execute("INSERT OR IGNORE INTO enrollment(admin_class_id,identity_id,seat_no,status,valid_from) VALUES (?,?,?,?,?)", (cid, ident, seat_no, status or "active", start))
                return one("SELECT id FROM enrollment WHERE admin_class_id=? AND identity_id=? AND valid_from=?", (cid, ident, start))[0]
            def teaching_class(year_id, subject, label, sort_order=0):
                row = one("SELECT id FROM teaching_class WHERE academic_year_id=? AND subject=? AND label=?", (year_id, subject, label))
                if row: return row[0]
                dst.execute("INSERT INTO teaching_class(academic_year_id,subject,label,sort_order) VALUES (?,?,?,?)", (year_id, subject, label, sort_order)); return one("SELECT last_insert_rowid()")[0]
            def ensure_member(cid, ident, source):
                ay = one("SELECT academic_year_id FROM teaching_class WHERE id=?", (cid,))[0]
                start = one("SELECT start_date FROM academic_year WHERE id=?", (ay,))[0]
                dst.execute("INSERT OR IGNORE INTO teaching_class_member(teaching_class_id,identity_id,valid_from,source) VALUES (?,?,?,?)", (cid, ident, start, source or "migration"))
                return one("SELECT id FROM teaching_class_member WHERE teaching_class_id=? AND identity_id=? AND valid_from=?", (cid, ident, start))[0]
            if domain == "homeroom":
                roster_facts = {}
                for r in src.execute("SELECT * FROM class_roster"):
                    ident = identity_for(r["student_id"], "class_roster", r)
                    # grade 是「该行记录时学生的年级」而非入学届别：active_grade 对应锚定
                    # 学年，低一年级行是上一学年的学籍事实（旧应用 rollover 语义），
                    # 因此学年 = 锚定学年 + (grade - active_grade)，绝不推未来学年。
                    delta = int(r["grade"]) - active_grade
                    ay_name = f"{int(anchor_year[:4]) + delta}-{int(anchor_year[:4]) + delta + 1}"
                    ay = shifted_year(anchor_year, delta)
                    roster_facts[r["student_id"]] = (ay_name, ay, int(r["grade"]), r["class_num"] or 0)
                    cid = admin_class(ay, r["grade"], r["class_num"] or 0)
                    eid = ensure_enrollment(cid, ident, r["seat_no"], r["status"])
                    map_to(domain, fp, "class_roster", r, "administrative_class", cid, "roster_class", src)
                    map_to(domain, fp, "class_roster", r, "enrollment", eid, "roster_enrollment", src); counts["h_roster"] += 1
                # subject_score 先于 total_score 迭代：同考生同场考试的科目行 class_num
                # 是总分行班级的最直接证据（1e 回填分支一）。
                subject_class_by_exam_sid = {}
                for table, valcol, subjectcol in (("subject_score", "raw_score", "subject"), ("total_score", "total_score", "total_type")):
                    for r in src.execute(f"SELECT * FROM {table}"):
                        ex = exams.get(r["exam_id"])
                        if not ex: continue
                        ident = identity_for(r["student_id"], table, r); ay = exam_year(ex["exam_date"]); exact, precision = parsed_exam_date(ex["exam_date"]); cid = None
                        if "class_num" in r.keys() and r["class_num"] is not None:
                            cid = admin_class(ay, ex["grade"], r["class_num"])
                            eid = ensure_enrollment(cid, ident)
                            map_to(domain, fp, table, r, "administrative_class", cid, "score_class", src); map_to(domain, fp, table, r, "enrollment", eid, "score_enrollment", src)
                        elif table == "total_score":
                            # 1e：H total_score 源无 class_num，class_ref 依可证明证据回填：
                            # ① 同生同考科目行的 class_num（同学同考同班）；② 无科目行 →
                            # 该生在考试学年的名册学籍行（与作业同学籍规则）；③ 都没有 →
                            # NULL 保留 + total_class_ref_null 计数，绝不猜班。
                            ay_name = academic_year(ex["exam_date"])
                            evidence = subject_class_by_exam_sid.get((r["exam_id"], r["student_id"]))
                            roster_entry = roster_facts.get(r["student_id"])
                            if evidence is not None:
                                cid = admin_class(ay, ex["grade"], evidence)
                            elif roster_entry is not None and roster_entry[0] == ay_name:
                                cid = admin_class(ay, roster_entry[2], roster_entry[3])
                            else:
                                counts["total_class_ref_null"] += 1
                            if cid is not None:
                                eid = ensure_enrollment(cid, ident)
                                map_to(domain, fp, table, r, "administrative_class", cid, "score_class", src); map_to(domain, fp, table, r, "enrollment", eid, "score_enrollment", src)
                        if table == "subject_score" and r["class_num"] is not None:
                            subject_class_by_exam_sid[(r["exam_id"], r["student_id"])] = r["class_num"]
                        subject = r[subjectcol] if table == "subject_score" else None; total = r[subjectcol] if table == "total_score" else None
                        dst.execute("INSERT OR IGNORE INTO score_fact(data_domain,academic_year_id,exam_name,exam_date,source_exam_date,exam_date_precision,class_ref_id,identity_id,subject,total_type,subject_key,total_key,score,grade_score,source,data_revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (domain, ay, ex["name"], exact, ex["exam_date"], precision, cid, ident, subject, total, subject or "", total or "", r[valcol], r["grade_score"] if table == "subject_score" else None, "migration:h", 1))
                        fact = one("SELECT id FROM score_fact WHERE data_domain=? AND academic_year_id=? AND exam_name=? AND identity_id=? AND subject_key=? AND total_key=?", (domain, ay, ex["name"], ident, subject or "", total or ""))[0]
                        map_to(domain, fp, table, r, "score_fact", fact, "score", src); counts["h_score"] += 1
                # H HomeworkRecord：每行=某生某天某科欠交一次；remark 非空≈请假。
                # 学年按 academic_year(row.date) 推导，班级由该学年的名册学籍行定位，
                # 找不到该学年学籍 → pending_import_row（归档另由主流程保全）。
                # 同日同科同班同人第 n 行 = 独立批次（token 含出现序 seq），绝不合并。
                hw_seq = {}
                for r in src.execute("SELECT * FROM homework_record ORDER BY id"):
                    # 1d：学期覆盖优先分年（真实 H 数据行为不变，仅边界语义统一）。
                    ay_name = homework_year_name(r["date"])
                    entry = roster_facts.get(r["student_id"])
                    if not ay_name:
                        ensure_pending(domain, fp, "homework_record", r, "作业日期无法推导学年", src); counts["h_homework_pending"] += 1; continue
                    if entry is None or entry[0] != ay_name:
                        ensure_pending(domain, fp, "homework_record", r, "该学年无学籍事实", src); counts["h_homework_pending"] += 1; continue
                    ident = identity_for(r["student_id"], "homework_record", r)
                    if mapped(domain, fp, "homework_record", r, "homework_submission", "homework", src) is None:
                        year_id = entry[1]
                        cid = admin_class(year_id, entry[2], entry[3])
                        ensure_enrollment(cid, ident)
                        seq_key = (r["date"], r["subject"], cid, ident)
                        seq = hw_seq.get(seq_key, 0); hw_seq[seq_key] = seq + 1
                        token = f"migration:h:{r['date']}:{r['subject']}:{seq}:{cid}"
                        aid = ensure_assignment(domain, cid, year_id, r["subject"], "legacy", r["date"], token)
                        remark, content = r["remark"] or None, r["content"] or None
                        evaluation = f"{remark}|{content}" if remark and content else (remark or content)
                        sub = ensure_submission(aid, ident, "excused" if r["remark"] else "missing", evaluation)
                        map_to(domain, fp, "homework_record", r, "homework_submission", sub, "homework", src)
                    counts["h_homework"] += 1
                # H special_record → 域内档案：身份只解析已投影身份（名册先行），
                # 绝不为「仅有档案记录」的学号新建幽灵身份；解析不出 → pending。
                for r in src.execute("SELECT * FROM special_record ORDER BY id"):
                    ident = aliases.get(r["student_id"])
                    if ident is None:
                        ensure_pending(domain, fp, "special_record", r, "身份无域内事实", src); counts["h_note_pending"] += 1; continue
                    if mapped(domain, fp, "special_record", r, "ws_student_note", "note", src) is None:
                        dst.execute("INSERT INTO ws_student_note(data_domain,person_id,date,category,content,follow_up_done,source) VALUES (?,?,?,?,?,0,?)",
                                    (domain, ident, r["date"], "其他", f"[{r['type']}] {r['note'] or ''}", f"migration:{tag}"))
                        note_id = one("SELECT last_insert_rowid()")[0]
                        map_to(domain, fp, "special_record", r, "ws_student_note", note_id, "note", src)
                    counts["h_note"] += 1
            else:
                teacher = src.execute("SELECT subject FROM teacher ORDER BY id LIMIT 1").fetchone(); current = teacher[0] if teacher else None
                # C2：旧教学版班级是「延续实体」（无学年维度、跨年使用）。原实例保持
                # 基线学年（active_grade 推导）；另为每班建下一学年延续实例（同学科/
                # 标签/排序），源行映射 kind='class' 仍指原实例，延续实例记
                # kind='class_continuation'，成员整体复制（valid_from=延续学年起点）。
                classes = {}
                class_chains = {}
                for r in src.execute("SELECT * FROM teaching_class"):
                    delta = active_grade - int(r["grade"])
                    orig_name = f"{int(anchor_year[:4]) + delta}-{int(anchor_year[:4]) + delta + 1}"
                    cont_name = f"{int(anchor_year[:4]) + delta + 1}-{int(anchor_year[:4]) + delta + 2}"
                    orig_ay = shifted_year(anchor_year, delta); cont_ay = shifted_year(anchor_year, delta + 1)
                    sort_order = r["sort_order"] if "sort_order" in r.keys() else 0
                    cid = teaching_class(orig_ay, r["subject"], r["label"], sort_order)
                    classes[r["id"]] = cid; map_to(domain, fp, "teaching_class", r, "teaching_class", cid, "class", src)
                    cont_cid = teaching_class(cont_ay, r["subject"], r["label"], sort_order)
                    map_to(domain, fp, "teaching_class", r, "teaching_class", cont_cid, "class_continuation", src)
                    counts["class_continuation"] += 1
                    class_chains[r["id"]] = (orig_name, orig_ay, cid, cont_name, cont_ay, cont_cid)
                # 1c：成员班映射按**身份**建（每成员行 identity_for 解析后的 ws 身份 →
                # 最小教学班，原实例学年）。g1- 死号前缀剥离绝不代表同人（旧教学版
                # migrate_student_ids.py：跨届撞号死号命名空间），但 student_alias
                # 证明的同人链必须接通——字符串直配会漏掉别名链（run2 仅 7 行命中）。
                ident_min_tc = {}
                member_tcs = defaultdict(list)
                for r in src.execute("SELECT * FROM teaching_class_member"):
                    if r["teaching_class_id"] not in classes: continue
                    ident = identity_for(r["student_id"], "teaching_class_member", r); mid = ensure_member(classes[r["teaching_class_id"]], ident, r["source"])
                    map_to(domain, fp, "teaching_class_member", r, "teaching_class_member", mid, "member", src); counts["t_member"] += 1
                    cont_mid = ensure_member(class_chains[r["teaching_class_id"]][5], ident, "migration:t-continuation")
                    map_to(domain, fp, "teaching_class_member", r, "teaching_class_member", cont_mid, "member_continuation", src)
                    counts["member_continuation"] += 1
                    member_tcs[r["student_id"]].append(r["teaching_class_id"])
                    prev = ident_min_tc.get(ident)
                    if prev is None or r["teaching_class_id"] < prev:
                        ident_min_tc[ident] = r["teaching_class_id"]
                counts["t_hw_multi_member"] += sum(1 for tcs in member_tcs.values() if len(tcs) > 1)
                # C1：物理成绩 class_ref 由成员事实定位（按身份，同身份多条成员行取
                # 教学班 id 最小）；身份不在成员映射 → class_ref=NULL 并计数
                # score_class_ref_null。源 class_num 是行政班号（6班），绝不用它
                # 新造 label='6' 教学班。
                for r in src.execute("SELECT * FROM subject_score"):
                    if not current or r["subject"] != current: continue
                    ex = exams.get(r["exam_id"])
                    if not ex: continue
                    ay = exam_year(ex["exam_date"]); exact, precision = parsed_exam_date(ex["exam_date"]); ident = identity_for(r["student_id"], "subject_score", r)
                    src_tc = ident_min_tc.get(ident)
                    cid = None
                    if src_tc is None:
                        counts["score_class_ref_null"] += 1
                    else:
                        cid = classes[src_tc]; mid = ensure_member(cid, ident, "legacy_score")
                        map_to(domain, fp, "subject_score", r, "teaching_class", cid, "score_class", src); map_to(domain, fp, "subject_score", r, "teaching_class_member", mid, "score_member", src)
                    dst.execute("INSERT OR IGNORE INTO score_fact(data_domain,academic_year_id,exam_name,exam_date,source_exam_date,exam_date_precision,class_ref_id,identity_id,subject,total_type,subject_key,total_key,score,grade_score,source,data_revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("teaching", ay, ex["name"], exact, ex["exam_date"], precision, cid, ident, current, None, current, "", r["raw_score"], r["grade_score"], "migration:t", 1))
                    fact = one("SELECT id FROM score_fact WHERE data_domain='teaching' AND academic_year_id=? AND exam_name=? AND identity_id=? AND subject_key=? AND total_key=''", (ay, ex["name"], ident, current))[0]
                    map_to(domain, fp, "subject_score", r, "score_fact", fact, "score", src); counts["t_score"] += 1
                # T HomeworkRecord：Q08 语义——assignment.subject=教师任教学科，
                # homework_type=旧 subject 原值（作业种类，含 化学/日常作业 等）。
                # 教学班同样按身份映射解析（别名链接通即命中）；批次学年用「学期
                # 覆盖优先」（2026-08 落暑假学期 → AY2025-2026 原实例；09-01 起
                # 落合并学期 → AY2026-2027 延续实例），应用按学年读取才能看到
                # 9 月的当前作业。身份不在成员映射（无别名链即无成员事实）/教师
                # 学科缺失/日期学年无对应实例 → pending_import_row；pending 行
                # 绝不新建身份。
                status_map = {"缺交": "missing", "已交": "submitted", "请假": "excused"}
                hw_seq = {}
                for r in src.execute("SELECT * FROM homework_record ORDER BY id"):
                    if current is None:
                        ensure_pending(domain, fp, "homework_record", r, "教师任教学科缺失，不猜", src); counts["t_homework_pending"] += 1; continue
                    ident = aliases.get(r["student_id"])
                    src_tc = ident_min_tc.get(ident) if ident is not None else None
                    if src_tc is None:
                        ensure_pending(domain, fp, "homework_record", r, "无教学班成员事实", src); counts["t_homework_pending"] += 1; continue
                    hw_ay = homework_year_name(r["date"])
                    if not hw_ay:
                        ensure_pending(domain, fp, "homework_record", r, "作业日期无法推导学年", src); counts["t_homework_pending"] += 1; continue
                    chain = class_chains[src_tc]
                    if hw_ay not in (chain[0], chain[3]):
                        ensure_pending(domain, fp, "homework_record", r, "作业日期学年无对应教学班实例", src); counts["t_homework_pending"] += 1; continue
                    if mapped(domain, fp, "homework_record", r, "homework_submission", "homework", src) is None:
                        cid = chain[2] if hw_ay == chain[0] else chain[5]
                        year_id = chain[1] if hw_ay == chain[0] else chain[4]
                        seq_key = (r["date"], r["subject"], cid, ident)
                        seq = hw_seq.get(seq_key, 0); hw_seq[seq_key] = seq + 1
                        token = f"migration:t:{r['date']}:{r['subject']}:{seq}:{cid}"
                        aid = ensure_assignment(domain, cid, year_id, current, r["subject"], r["date"], token)
                        sub = ensure_submission(aid, ident, status_map.get(r["submission_status"], "unknown"), r["evaluation"])
                        map_to(domain, fp, "homework_record", r, "homework_submission", sub, "homework", src)
                    counts["t_homework"] += 1
                # T special_record → 域内档案：同样只解析已投影身份（别名/成员/成绩），
                # 孤儿学号 → pending_import_row，归档保全。
                for r in src.execute("SELECT * FROM special_record ORDER BY id"):
                    ident = aliases.get(r["student_id"])
                    if ident is None:
                        ensure_pending(domain, fp, "special_record", r, "身份无域内事实", src); counts["t_note_pending"] += 1; continue
                    if mapped(domain, fp, "special_record", r, "ws_student_note", "note", src) is None:
                        dst.execute("INSERT INTO ws_student_note(data_domain,person_id,date,category,content,follow_up_done,source) VALUES (?,?,?,?,?,0,?)",
                                    (domain, ident, r["date"], "其他", f"[{r['type']}] {r['note'] or ''}", f"migration:{tag}"))
                        note_id = one("SELECT last_insert_rowid()")[0]
                        map_to(domain, fp, "special_record", r, "ws_student_note", note_id, "note", src)
                    counts["t_note"] += 1
        fk=list(dst.execute("PRAGMA foreign_key_check"));
        if fk: raise ValueError(f"workspace projection FK violations: {len(fk)}")
        dst.commit(); return dict(counts)
    except Exception:
        dst.rollback(); raise
    finally:
        dst.close()
        for _,_,src in srcs: src.close()


def backfill_display_names(h_path: Path, t_path: Path, target_db: Path) -> dict[str, Any]:
    """UX01 安全补齐：对已迁移目标库中 display_name 仍为空的 ws_student_identity，
    用来源快照可证明的姓名（同域 student_id + name 列）填回。

    红线：绝不覆盖非空姓名；同身份多个不同姓名 → 跳过并列入 candidates 供人工
    裁决，绝不按姓名并人；H/T 两域各补各的身份，绝不跨域同名归并。除
    ws_student_identity.display_name 外零写入。来源只读（mode=ro）。
    """
    dst = sqlite3.connect(target_db); dst.row_factory = sqlite3.Row
    result: dict[str, Any] = {"filled": 0, "already_named": 0, "no_evidence": 0,
                              "name_mismatch": 0, "candidates": []}
    try:
        for domain, path in (("homeroom", h_path), ("teaching", t_path)):
            src = source_db(path); src.row_factory = sqlite3.Row
            try:
                # 域内证据：student_id → 来源姓名集合（凡带两列的源表都算证据）
                names_by_sid: dict[str, set[str]] = defaultdict(set)
                for table in sorted(tables(src)):
                    cols = {r[1] for r in src.execute(f"PRAGMA table_info({_q(table)})")}
                    if "student_id" not in cols or "name" not in cols:
                        continue
                    for row in src.execute(f"SELECT student_id, name FROM {_q(table)}"):
                        if row["student_id"] and isinstance(row["name"], str) and row["name"].strip():
                            names_by_sid[row["student_id"]].add(row["name"].strip())
                names_by_ident: dict[int, set[str]] = defaultdict(set)
                for alias in dst.execute(
                    "SELECT identity_id, alias_value FROM ws_student_alias WHERE data_domain=?",
                    (domain,),
                ).fetchall():
                    names_by_ident[alias["identity_id"]].update(names_by_sid.get(alias["alias_value"], ()))
                for ident, names in sorted(names_by_ident.items()):
                    row = dst.execute(
                        "SELECT display_name FROM ws_student_identity WHERE id=? AND data_domain=?",
                        (ident, domain),
                    ).fetchone()
                    if row is None:
                        continue
                    if row["display_name"]:
                        if names and row["display_name"] not in names:
                            result["name_mismatch"] += 1
                        else:
                            result["already_named"] += 1
                        continue
                    if len(names) == 1:
                        dst.execute(
                            "UPDATE ws_student_identity SET display_name=? WHERE id=? AND data_domain=? AND display_name IS NULL",
                            (next(iter(names)), ident, domain),
                        )
                        result["filled"] += 1
                    elif len(names) > 1:
                        result["candidates"].append({"identity_id": ident, "data_domain": domain, "names": sorted(names)})
                    else:
                        result["no_evidence"] += 1
            finally:
                src.close()
        dst.commit()
        return result
    except Exception:
        dst.rollback()
        raise
    finally:
        dst.close()


def apply_confirmed_links(target_db: Path, decisions_payload: dict[str, Any] | None) -> dict[str, int]:
    """Apply only explicit, snapshot-bound class links and one-to-one student pairs."""
    links = (decisions_payload or {}).get("links", [])
    counts = Counter()
    if not links:
        return {}
    db = sqlite3.connect(target_db); db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")

        def one(sql, args=()):
            return db.execute(sql, args).fetchone()

        def identity_by_alias(domain: str, alias_value: str, expected_name: str) -> int:
            rows = db.execute(
                "SELECT DISTINCT i.id,i.display_name FROM ws_student_alias a "
                "JOIN ws_student_identity i ON i.id=a.identity_id "
                "WHERE a.data_domain=? AND a.alias_value=? AND i.data_domain=?",
                (domain, alias_value, domain),
            ).fetchall()
            if len(rows) != 1 or rows[0]["display_name"] != expected_name:
                raise ValueError("confirmed pair alias/name is not uniquely resolvable")
            return int(rows[0]["id"])

        for spec in links:
            year_name = spec.get("academic_year")
            admin = spec.get("admin_class", {})
            teaching = spec.get("teaching_class", {})
            pairs = spec.get("pairs", [])
            if not isinstance(pairs, list) or not pairs:
                raise ValueError("confirmed link must contain pairs")
            year = one("SELECT id,start_date FROM academic_year WHERE name=?", (year_name,))
            if year is None:
                raise ValueError("confirmed link academic year not found")
            ac = one(
                "SELECT id FROM administrative_class WHERE academic_year_id=? AND grade=? AND class_num=?",
                (year["id"], admin.get("grade"), admin.get("class_num")),
            )
            tc = one(
                "SELECT id FROM teaching_class WHERE academic_year_id=? AND subject=? AND label=?",
                (year["id"], teaching.get("subject"), teaching.get("label")),
            )
            if ac is None or tc is None:
                raise ValueError("confirmed link class not found")
            subject = teaching.get("subject")
            existing = one(
                "SELECT id FROM homeroom_teaching_link WHERE admin_class_id=? AND teaching_class_id=? AND academic_year_id=? AND subject=?",
                (ac["id"], tc["id"], year["id"], subject),
            )
            if existing is None:
                db.execute(
                    "INSERT INTO homeroom_teaching_link(admin_class_id,teaching_class_id,academic_year_id,subject,valid_from,share_categories,status,version) VALUES (?,?,?,?,?,'roster,current_subject_score','active',1)",
                    (ac["id"], tc["id"], year["id"], subject, year["start_date"]),
                )
                link_id = one("SELECT last_insert_rowid()")[0]
                counts["links_created"] += 1
            else:
                link_id = existing["id"]
            seen_h, seen_t = set(), set()
            for pair in pairs:
                common_name = pair.get("expected_name")
                h_name = pair.get("homeroom_expected_name", common_name)
                t_name = pair.get("teaching_expected_name", common_name)
                if not all(isinstance(v, str) and v for v in (h_name, t_name)):
                    raise ValueError("confirmed pair expected names are required")
                h_id = identity_by_alias("homeroom", pair.get("homeroom_alias"), h_name)
                t_id = identity_by_alias("teaching", pair.get("teaching_alias"), t_name)
                if h_id in seen_h or t_id in seen_t:
                    raise ValueError("confirmed pairs must be one-to-one")
                seen_h.add(h_id); seen_t.add(t_id)
                h_member = one("SELECT 1 FROM enrollment WHERE admin_class_id=? AND identity_id=? AND status='active'", (ac["id"], h_id))
                t_member = one("SELECT 1 FROM teaching_class_member WHERE teaching_class_id=? AND identity_id=?", (tc["id"], t_id))
                if h_member is None or t_member is None:
                    raise ValueError("confirmed pair is outside linked class membership")
                prior_h = one("SELECT teaching_identity_id FROM linked_student WHERE link_id=? AND homeroom_identity_id=?", (link_id, h_id))
                prior_t = one("SELECT homeroom_identity_id FROM linked_student WHERE link_id=? AND teaching_identity_id=?", (link_id, t_id))
                if (prior_h and prior_h[0] != t_id) or (prior_t and prior_t[0] != h_id):
                    raise ValueError("confirmed pair conflicts with existing link")
                if prior_h is None:
                    db.execute(
                        "INSERT INTO linked_student(link_id,homeroom_identity_id,teaching_identity_id,confirm_basis) VALUES (?,?,?,?)",
                        (link_id, h_id, t_id, spec.get("confirm_basis") or "manual_confirm:p8"),
                    )
                    counts["pairs_created"] += 1
                else:
                    counts["pairs_existing"] += 1
        db.commit()
        return dict(counts)
    except Exception:
        db.rollback(); raise
    finally:
        db.close()


def build_projection_audit(h_path: Path, t_path: Path, target_db: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """逐表分类审计：每张非零来源表给出 rows/projected/archive_only/reason。

    projected 按 source_projection_map ∪ source_map 的去重 source_pk 计数；
    未投影/部分投影表的 reason 取 PROJECTION_REASONS（承接演练 NOT_MIGRATED
    口径的真实迁移语境），未登记且未投影的表给出保守提示，绝不静默。
    """
    audit: dict[str, dict[str, dict[str, Any]]] = {}
    target_db = Path(target_db)
    dst = sqlite3.connect(f"file:{quote(str(target_db.resolve()))}?mode=ro", uri=True)
    try:
        for tag, path in (("h", h_path), ("t", t_path)):
            fp = f"{tag}:{sha256(path)}"
            src = source_db(path)
            try:
                entry: dict[str, dict[str, Any]] = {}
                for table in sorted(tables(src)):
                    rows = src.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    if rows == 0:
                        continue
                    projected = dst.execute(
                        "SELECT COUNT(*) FROM (SELECT DISTINCT source_pk FROM source_projection_map WHERE source_fingerprint=? AND source_table=?"
                        " UNION SELECT DISTINCT source_pk FROM source_map WHERE source_fingerprint=? AND source_table=?)",
                        (fp, table, fp, table)).fetchone()[0]
                    pending = dst.execute("SELECT COUNT(*) FROM pending_import_row WHERE source_fingerprint=? AND source_table=?", (fp, table)).fetchone()[0]
                    reason = PROJECTION_REASONS.get((tag, table))
                    if reason is None and pending:
                        reason = f"{pending} 行入待核实区（pending_import_row），余量归档可追溯"
                    if projected + pending < rows and reason is None:
                        reason = "未登记投影去向：余量已归档待核实"
                    entry[table] = {"rows": rows, "projected": projected, "archive_only": rows - projected, "reason": reason}
                audit[tag] = entry
            finally:
                src.close()
    finally:
        dst.close()
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--homeroom-db", required=True, type=Path)
    parser.add_argument("--teaching-db", required=True, type=Path)
    parser.add_argument("--target-root", required=True, type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--resume", action="store_true", help="only resume the identical marked snapshot")
    parser.add_argument("--project-business", action="store_true", help="mirror legacy H before conservative ws projection")
    parser.add_argument("--assets-root", type=Path, help="local isolated H/T assets root; copied with digest manifest")
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--backfill-display-names", action="store_true", help="fill NULL ws_student_identity.display_name from provable source names on an existing migrated target; no other writes")
    args = parser.parse_args()
    target = args.target_root.resolve()
    if args.backfill_display_names:
        # R02：补齐必须绑定目标迁移所用的源快照——marker 摘要、目标库迁移身份
        # 与两份源文件摘要全部核对通过才允许写入；任何校验失败零写入退出。
        # 边界检查与正式运行同规则：源不得位于 target-root 内。
        for source in (args.homeroom_db.resolve(), args.teaching_db.resolve()):
            if source == target or target in source.parents:
                print(json.dumps({"ok": False, "error": "source path must not be inside target-root"}, ensure_ascii=False))
                return 2
        db_path = target / "data" / "db.sqlite"
        marker_path = target / ".p8-real-marker.json"
        if not db_path.is_file() or not marker_path.is_file():
            print(json.dumps({"ok": False, "error": "backfill requires an existing migrated target (data/db.sqlite + .p8-real-marker.json)"}, ensure_ascii=False))
            return 2
        try:
            manifest = preflight(args.homeroom_db, args.teaching_db, None)
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, sqlite3.DatabaseError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 2
        expected_sources = {"homeroom": manifest["homeroom"]["sha256"], "teaching": manifest["teaching"]["sha256"]}
        if marker.get("sources") != expected_sources:
            print(json.dumps({"ok": False, "error": "source snapshot digest mismatch: target marker does not match the provided sources"}, ensure_ascii=False))
            return 2
        # 目标迁移身份：marker 的 run_token 必须是目标库里已完成的一次迁移
        try:
            check = sqlite3.connect("file:" + quote(str(db_path.resolve())) + "?mode=ro", uri=True)
            try:
                run_row = check.execute("SELECT status FROM migration_run WHERE run_token=?", (marker.get("run_token"),)).fetchone()
            finally:
                check.close()
        except sqlite3.DatabaseError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 2
        if run_row is None or run_row[0] != "completed":
            print(json.dumps({"ok": False, "error": "target database is not a completed migration run of this marker"}, ensure_ascii=False))
            return 2
        try:
            backfill = backfill_display_names(args.homeroom_db, args.teaching_db, db_path)
        except (OSError, sqlite3.DatabaseError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps({"ok": True, "backfill_display_names": backfill}, ensure_ascii=False, sort_keys=True))
        return 0
    # Reject sources inside target before preflight: it prevents a target from becoming a source.
    for source in (args.homeroom_db.resolve(), args.teaching_db.resolve()):
        if source == target or target in source.parents:
            raise SystemExit("source path must not be inside target-root")
    try:
        manifest = preflight(args.homeroom_db, args.teaching_db, args.decisions)
    except (OSError, sqlite3.DatabaseError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    if args.assets_root and not args.assets_root.resolve().is_dir():
        print(json.dumps({"ok": False, "error": "assets-root is not a directory"}, ensure_ascii=False))
        return 2
    if args.preflight_only:
        print(json.dumps({"ok": True, "manifest": manifest}, ensure_ascii=False, sort_keys=True))
        return 0
    decisions_payload = json.loads(args.decisions.read_text(encoding="utf-8")) if args.decisions else None
    # No filesystem target mutation occurs until all checks above have succeeded. A normal
    # run only accepts a brand-new root; resume requires a marker bound to these hashes.
    marker = target / ".p8-real-marker.json"
    if target.exists():
        entries = list(target.iterdir())
        if not args.resume and entries:
            print(json.dumps({"ok": False, "error": "target-root must be a new empty directory; use --resume only for its identical marker"}, ensure_ascii=False))
            return 2
        if args.resume:
            try:
                old = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                print(json.dumps({"ok": False, "error": "--resume requires target marker"}, ensure_ascii=False))
                return 2
            if old.get("run_token") != manifest["run_token"] or old.get("sources") != {"homeroom": manifest["homeroom"]["sha256"], "teaching": manifest["teaching"]["sha256"]}:
                print(json.dumps({"ok": False, "error": "--resume source snapshot mismatch"}, ensure_ascii=False))
                return 2
    elif args.resume:
        print(json.dumps({"ok": False, "error": "--resume target-root does not exist"}, ensure_ascii=False))
        return 2
    target.mkdir(parents=True, exist_ok=True)
    (target / "data").mkdir(exist_ok=True)
    marker.write_text(json.dumps({"run_token": manifest["run_token"], "sources": {"homeroom": manifest["homeroom"]["sha256"], "teaching": manifest["teaching"]["sha256"]}}, ensure_ascii=False), encoding="utf-8")
    report_dir = target / "reports"
    report_dir.mkdir(exist_ok=True)
    (report_dir / "preflight.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    asset_manifest = []
    if args.assets_root:
        assets_root = args.assets_root.resolve()
        for path in sorted(p for p in assets_root.rglob("*") if p.is_file()):
            rel = path.relative_to(assets_root)
            dest = target / "assets" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest); os.chmod(dest, 0o600)
            asset_manifest.append({"domain": rel.parts[0] if rel.parts else "unknown", "relative_path": hashlib.sha256(str(rel).encode()).hexdigest(), "size": dest.stat().st_size, "sha256": sha256(dest), "status": "copied"})
        (report_dir / "assets_manifest.json").write_text(json.dumps(asset_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.environ["EXAM_TRACKER_DIR"] = str(target / "data")
    os.environ["EXAM_TRACKER_BACKUP_DIR"] = str(target / "backups")
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from app.db.schema import ensure_app_schema
    from app.db.models import SessionLocal
    from app.db import workspace_models as wm
    ensure_app_schema()
    legacy_counts = {}
    if args.project_business and not args.resume:
        # Fresh target only (or the exact resume marker); a failed mirror rolls back
        # rather than leaving a partially usable H application.
        legacy_counts = mirror_homeroom_legacy(args.homeroom_db, target / "data" / "db.sqlite")
        workspace_counts = project_workspace_facts(
            args.homeroom_db,
            args.teaching_db,
            target / "data" / "db.sqlite",
            decisions_payload,
        )
        workspace_counts.update(apply_confirmed_links(target / "data" / "db.sqlite", decisions_payload))
    else:
        workspace_counts = {}
    # A run is idempotent on the combined snapshot digest.  Business projection is deliberately
    # conservative: it is completed only where dates/identity/class scope are provable; every
    # other source row has a durable archive destination rather than an invisible loss.
    db = SessionLocal()
    try:
        run = db.query(wm.MigrationRun).filter_by(run_token=manifest["run_token"]).one_or_none()
        if run and run.status == "completed":
            print(json.dumps({"ok": True, "run_token": run.run_token, "added": 0, "status": "already_completed"}))
            return 0
        if run is None:
            run = wm.MigrationRun(run_token=manifest["run_token"], kind="p8-real", status="running")
            db.add(run); db.flush()
        counts = Counter()
        for domain, tag, path, fp in (("homeroom", "h", args.homeroom_db, manifest["homeroom"]["sha256"]), ("teaching", "t", args.teaching_db, manifest["teaching"]["sha256"])):
            tagged_fp = f"{tag}:{fp}"
            conn = source_db(path); conn.row_factory = sqlite3.Row
            try:
                orphan_rows = fk_rowids(conn)
                for table in sorted(tables(conn)):
                    pk = primary_key_columns(conn, table)
                    for row in source_rows(conn, table, pk):
                        key = stable_source_key(row, pk)
                        exists = db.query(wm.SourceArchiveRecord.id).filter_by(source_fingerprint=tagged_fp, source_table=table, source_pk=key).first()
                        if exists:
                            continue
                        reason = "source_preserved_pending_projection"
                        if table == "upload": reason = "upload_metadata_original_unverified"
                        if (table, int(row["__p8_rowid__"])) in orphan_rows:
                            reason = "foreign_key_orphan_pending_review"
                        payload = dict(row); payload.pop("__p8_rowid__", None)
                        db.add(wm.SourceArchiveRecord(source_fingerprint=tagged_fp, source_table=table, source_pk=key, data_domain=domain, archive_reason=reason, payload_json=json.dumps(payload, ensure_ascii=False, default=str)))
                        counts[domain + ":archived"] += 1
                db.flush()
            finally:
                conn.close()
        run.status = "completed"; run.stats_json = json.dumps({"manifest": manifest, "counts": counts, "legacy_h": legacy_counts, "workspace": workspace_counts}, ensure_ascii=False)
        db.commit()
        audit = build_projection_audit(args.homeroom_db, args.teaching_db, target / "data" / "db.sqlite")
        (report_dir / "projection_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "run_token": run.run_token, "added": dict(counts), "status": "completed", "workspace": workspace_counts}, ensure_ascii=False))
    except Exception:
        db.rollback(); raise
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
