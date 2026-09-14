"""P7 合成双库迁移演练管线（分阶段、可断点续跑、幂等）。

范围声明（如实登记，不得夸大）：本脚本处理的是 build_synthetic_sources.py
构造的**合成源库**，绝不读写 ~/.exam-tracker、绝不打开 .sources/ 下任何
文件；真实库实测属上线前现场步骤（见 deploy/SWITCHOVER.md 现场清单）。

阶段（每阶段一个事务 + MigrationRun 阶段记录，失败回滚当前阶段、重试从
上一致点继续）：
  snapshot → import_homeroom → import_teaching → link_suggest
  → conflict_report → undo_audit → verify

幂等语义（docs/planning/04-migration.md「可重复与中断恢复」）：
- 映射键 (source_fingerprint, source_table, source_pk)：命中即跳过，重复跑
  同一源摘要零新增业务行；
- run_token 由两源 sha256 派生：摘要变化即新 MigrationRun 批次，绝不复用
  旧「已迁移」标记；
- 目标业务行全部按自然键幂等 ensure（学年名/行政班唯一键/成绩事实自然键
  等），新批次下已存在的行只补映射、不覆盖值。

隔离待核实（契约 v2 §4.1 Q04）：撞号同名、占位/临时学号（_anon:/TMP）、
未知学年、教师任教学科未配置等不能安全转换的来源行**绝不写入业务表**，
原行完整落 PendingImportRow（迁移 0008）等待确认；确认走演练根声明表
pending_confirmations.json，在导入阶段按导入规则补写业务表并清除登记。

对账母集（契约 v2 §4.1 Q05）：verify 覆盖两侧源库**全部非零业务表**，
逐行四分类 mapped/shared/quarantined/not_migrated，合计=源行全集、遗漏 0；
shared 按事实（身份在声明确认对 + 学科/时期/类别在共享白名单）判定，
绝不按人整体升级。

用法：
  python run_rehearsal.py [--root DIR] [--from STAGE] [--to STAGE]
                          [--declare FILE]
环境：必须在 import app 前确定 EXAM_TRACKER_DIR；未设置时默认
  <root>/target/data（独立目录）。任何不落在 <root> 下的路径一律拒绝。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import date

# ── 仓库根与默认演练根目录 ──
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_ROOT = os.path.join(REPO_ROOT, ".test-data", "migration-rehearsal")

STAGES = [
    "snapshot",
    "import_homeroom",
    "import_teaching",
    "link_suggest",
    "conflict_report",
    "undo_audit",
    "verify",
]

# 迁移口径表（逐行分类；verify 按四分类合计对账）。凡不在本清单、也不在
# NOT_MIGRATED_REASONS 登记内的非零来源表 → verify 判「遗漏」直接失败。
MIGRATED_TABLES = {
    "h": ["exam", "student_identity", "student_alias", "class_roster",
          "subject_score", "total_score", "homework_record"],
    "t": ["exam", "student_identity", "student_alias", "teaching_class",
          "teaching_class_member", "subject_score", "homework_record"],
}

# 对账主键列（class_roster 主键是 student_id，homework_setting 是 key，
# 其余为自增 id）
RECON_PK_COL = {"class_roster": "student_id", "homework_setting": "key"}

# 显式不迁移登记（Q05 第四类）：表级整表不迁移的原因；缺登记的非零源表
# 会在 verify 判遗漏。无法转换的原行保留于源库、指纹+行数登记可追溯。
NOT_MIGRATED_REASONS = {
    ("h", "teacher"): "教师配置：合并版教师绑定是目标应用自身配置，不迁移源行",
    ("h", "class_average"): "班均=派生统计：目标按需由成绩事实重算，不迁移原行（源库可追溯）",
    ("h", "analysis_config"): "段位阈值：目标应用自身配置，不迁移",
    ("h", "homework_setting"): "作业键值配置：目标等价物由应用自管，不迁移",
    ("h", "homework_semester"): "作业学期配置：目标由学年/学期推导，不迁移原行（源库可追溯）",
    ("h", "imported_history"): "手工历史成绩：目标等价模型本批次未建，原行保留源库可追溯（不混入全年级排名）",
    ("h", "student_note"): "班主任私密档案：不自动迁移（档案隐私边界），原行保留源库可追溯",
    ("h", "roster_import_batch"): "撤销快照：undo_audit 阶段逐项审计（convertible/read-only），批次行不迁移",
    ("h", "rollover_confirm_batch"): "撤销快照：undo_audit 阶段逐项审计，批次行不迁移",
    ("t", "teacher"): "教师配置：任教学科已读取用于语义转换（assignment.subject=教师学科），行本身无目标等价模型",
    ("t", "class_average"): "班均=派生统计：目标按需由成绩事实重算，不迁移原行（源库可追溯）",
    ("t", "analysis_config"): "段位阈值：目标应用自身配置，不迁移",
    ("t", "homework_semester"): "作业学期配置：目标由学年/学期推导，不迁移原行（源库可追溯）",
    ("t", "class_roster"): "T 花名册：目标成员事实由 teaching_class_member 承载（已迁移），花名册行不重复迁移",
}

# 分类条目的 kind：shared 只可能落在 roster/score 两类（ADR-005 白名单
# roster + current_subject_score；总分/作业/别名/结构行绝不按人升级 shared）
KIND_BY_TABLE = {
    "class_roster": "roster",
    "teaching_class_member": "roster",
    "subject_score": "score",
    "total_score": "total",
    "homework_record": "homework",
    "exam": "structure",
    "teaching_class": "structure",
    "student_alias": "alias",
    "student_identity": "identity",
}

# 撤销快照表（undo_audit 逐项映射验证对象）
UNDO_TABLES = ["roster_import_batch", "rollover_confirm_batch"]

# 作业种类占位：H 源无「作业种类」概念，置 legacy 标记不伪造种类；
# T 源的旧 subject 列才是作业种类（Q08：homework_type=旧 subject 原值，
# assignment.subject=教师任教学科），同日同种多份按出现序展开独立批次。
LEGACY_HOMEWORK_TYPE = "legacy"


# ─────────────────────────────────────────────────────────────
# 路径与红线（必须在 import app 之前执行）
# ─────────────────────────────────────────────────────────────

def resolve_runtime(root: str) -> str:
    """确定并校验目标数据目录；返回实路径。红线自检失败直接退出。

    - EXAM_TRACKER_DIR 已设：必须落在演练根 <root> 之下（测试子进程用
      <root>/target/data）；未设：默认 <root>/target/data。
    - 拒绝任何解析到 ~/.exam-tracker 或 .sources/ 的路径（防环境变量污染
      导致误写真实数据/参考克隆）。
    """
    root_real = os.path.realpath(os.path.abspath(root))
    data_dir = os.environ.get("EXAM_TRACKER_DIR") or os.path.join(root_real, "target", "data")
    data_real = os.path.realpath(os.path.abspath(data_dir))
    if data_real != root_real and not data_real.startswith(root_real + os.sep):
        print(f"[红线] EXAM_TRACKER_DIR={data_real} 不在演练根 {root_real} 之下，拒绝执行", file=sys.stderr)
        raise SystemExit(2)
    home = os.path.expanduser("~")
    if data_real.startswith(os.path.join(os.path.realpath(home), ".exam-tracker")):
        print(f"[红线] 拒绝使用真实数据目录 {data_real}", file=sys.stderr)
        raise SystemExit(2)
    if ".sources" in data_real.split(os.sep):
        print(f"[红线] 拒绝在 .sources/ 下建库：{data_real}", file=sys.stderr)
        raise SystemExit(2)
    os.environ["EXAM_TRACKER_DIR"] = data_real
    os.environ.setdefault("EXAM_TRACKER_BACKUP_DIR", os.path.join(root_real, "target", "backups"))
    os.makedirs(data_real, exist_ok=True)
    return data_real


def load_app():
    """在 resolve_runtime 之后导入应用 ORM；返回阶段实现所需的命名空间。"""
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal
    from app.db.schema import ensure_app_schema

    return {"wm": wm, "SessionLocal": SessionLocal, "ensure_app_schema": ensure_app_schema}


# ─────────────────────────────────────────────────────────────
# 小工具
# ─────────────────────────────────────────────────────────────

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def open_source(path: str) -> sqlite3.Connection:
    # 源库一律只读打开（URI mode=ro）：迁移进程绝不写源。
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def is_placeholder(student_id: str | None) -> bool:
    """占位/临时学号：_anon:（仅姓名成员）与 TMP（临时号）→ 隔离待核实。"""
    if not student_id:
        return False
    return student_id.startswith("_anon:") or student_id.startswith("TMP")


def ay_name_for_date(d: date) -> str:
    """考试日期 → 学年名（8 月起算跨年学年）。"""
    y = d.year
    return f"{y}-{y + 1}" if d.month >= 8 else f"{y - 1}-{y}"


def ay_bounds(ay_name: str) -> tuple[date, date]:
    """学年名 → 标准学年起止（推导值，非源数据；报告口径注明）。"""
    y0 = int(ay_name.split("-")[0])
    return date(y0, 9, 1), date(y0 + 1, 7, 15)


def term_bounds(ay_name: str, semester: str) -> tuple[date, date]:
    """学期名（上/下）→ 标准学期起止（推导值）。"""
    y0 = int(ay_name.split("-")[0])
    if semester == "上":
        return date(y0, 9, 1), date(y0 + 1, 1, 31)
    return date(y0 + 1, 2, 1), date(y0 + 1, 7, 15)


def d(iso: str | None):
    return date.fromisoformat(iso) if iso else None


# ─────────────────────────────────────────────────────────────
# 演练上下文
# ─────────────────────────────────────────────────────────────

class Rehearsal:
    def __init__(self, root: str, declare_path: str | None):
        self.root = os.path.realpath(os.path.abspath(root))
        self.sources_dir = os.path.join(self.root, "sources")
        self.reports_dir = os.path.join(self.root, "reports")
        self.h_path = os.path.join(self.sources_dir, "h.sqlite")
        self.t_path = os.path.join(self.sources_dir, "t.sqlite")
        self.manifest_path = os.path.join(self.root, "sources_manifest.json")
        self.declare_path = declare_path
        self.h_fp = sha256_file(self.h_path)
        self.t_fp = sha256_file(self.t_path)
        # 映射键语义（04-migration）：(source_fingerprint, table, source_pk)。
        # 两源是独立库，指纹必须分源派生——否则两源同表同数值主键会互相
        # 冒认映射（如 H/T 的 student_alias id=1 是两行不同的数据）。
        self.fp_h = f"h:{self.h_fp[:32]}"
        self.fp_t = f"t:{self.t_fp[:32]}"
        # run_token 仍由两源摘要联合派生：任一源变化 → 新迁移批次
        self.run_token = f"rehearsal-{self.h_fp[:12]}-{self.t_fp[:12]}"
        os.makedirs(self.reports_dir, exist_ok=True)
        # 应用 ORM（resolve_runtime 之后才允许导入）
        app = load_app()
        self.wm = app["wm"]
        self.SessionLocal = app["SessionLocal"]
        # 全新目标库：F01 版本感知初始化（无版本表 → create_all + stamp head）
        app["ensure_app_schema"]()

    # ── 报告输出 ──
    def write_report(self, name: str, payload) -> str:
        path = os.path.join(self.reports_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        return path

    # ── 源库便捷读取 ──
    def h(self):
        return open_source(self.h_path)

    def t(self):
        return open_source(self.t_path)

    # ── MigrationRun 台账 ──
    def get_or_create_run(self, db):
        wm = self.wm
        run = db.query(wm.MigrationRun).filter_by(run_token=self.run_token).one_or_none()
        if run is None:
            # 摘要派生 token 不存在 → 新迁移批次（源摘要变化的自然结果）
            run = wm.MigrationRun(run_token=self.run_token, kind="p7-rehearsal", status="running")
            db.add(run)
            db.flush()
        return run

    def load_stats(self, run) -> dict:
        return json.loads(run.stats_json) if run.stats_json else {}

    def completed(self, db) -> list[str]:
        run = self.get_or_create_run(db)
        stats = self.load_stats(run)
        return list(stats.get("completed_stages", []))


def load_pending_confirmations(root: str) -> dict[str, set]:
    """读取演练根的待核实确认声明表 pending_confirmations.json。

    返回 {source_tag: {(source_table, source_pk)}}；文件缺失视为无确认。
    声明是演练内的人工决定记录：确认后导入阶段按导入规则补写业务表并
    清除 PendingImportRow（幂等，重跑不重复补写）。"""
    path = os.path.join(root, "pending_confirmations.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"待核实确认声明表不可解析：{path}（{exc}）") from exc
    out: dict[str, set] = {}
    for c in data.get("confirmations", []):
        if not c.get("source") or not c.get("source_table"):
            raise RuntimeError(f"确认声明缺 source/source_table：{c}")
        out.setdefault(str(c["source"]), set()).add(
            (str(c["source_table"]), str(c["source_pk"]))
        )
    return out


def get_pending(db, wm, fp: str, table: str, pk):
    return (
        db.query(wm.PendingImportRow)
        .filter_by(source_fingerprint=fp, source_table=table, source_pk=str(pk))
        .one_or_none()
    )


def ensure_pending(db, wm, fp: str, domain: str, table: str, pk, reason: str, raw_row) -> bool:
    """待核实区幂等登记：原行完整 raw_json 落 PendingImportRow，绝不写业务表。
    命中既有登记返回 False（重复跑/重入零重复）。"""
    if get_pending(db, wm, fp, table, pk) is not None:
        return False
    db.add(
        wm.PendingImportRow(
            source_fingerprint=fp, source_table=table, source_pk=str(pk),
            data_domain=domain, reason=reason,
            raw_json=json.dumps(dict(raw_row), ensure_ascii=False, default=str),
        )
    )
    db.flush()
    return True


def confirm_pending(db, wm, fp: str, table: str, pk) -> None:
    """确认导入后清除待核实登记（幂等：不存在亦无害）。"""
    db.query(wm.PendingImportRow).filter_by(
        source_fingerprint=fp, source_table=table, source_pk=str(pk)
    ).delete()


def pending_report_rows(db, wm) -> list[dict]:
    """PendingImportRow 全量 → 待核实视图（管线报告/人工查阅入口）。
    业务读路径不读本报告；只允许在迁移工具侧查阅原记录（Q04）。"""
    rows = db.query(wm.PendingImportRow).order_by(wm.PendingImportRow.id).all()
    out = []
    for p in rows:
        try:
            raw = json.loads(p.raw_json)
        except (json.JSONDecodeError, TypeError):
            raw = p.raw_json
        out.append({
            "source": "h" if p.source_fingerprint.startswith("h:") else "t",
            "source_table": p.source_table,
            "source_pk": p.source_pk,
            "data_domain": p.data_domain,
            "reason": p.reason,
            "raw": raw,
        })
    return out


# ─────────────────────────────────────────────────────────────
# 目标侧幂等 ensure（全部按自然键查重：重复跑/新批次零重复行）
# ─────────────────────────────────────────────────────────────

def ensure_academic_year(db, wm, name: str):
    ay = db.query(wm.AcademicYear).filter_by(name=name).one_or_none()
    if ay is None:
        start, end = ay_bounds(name)
        ay = wm.AcademicYear(name=name, start_date=start, end_date=end)
        db.add(ay)
        db.flush()
    return ay


def ensure_term(db, wm, ay, name: str, start, end):
    term = db.query(wm.Term).filter_by(academic_year_id=ay.id, name=name).one_or_none()
    if term is None:
        term = wm.Term(academic_year_id=ay.id, name=name, start_date=start, end_date=end)
        db.add(term)
        db.flush()
    return term


def ensure_admin_class(db, wm, ay, grade: int, class_num: int):
    cls = (
        db.query(wm.AdministrativeClass)
        .filter_by(academic_year_id=ay.id, grade=grade, class_num=class_num)
        .one_or_none()
    )
    if cls is None:
        cls = wm.AdministrativeClass(academic_year_id=ay.id, grade=grade, class_num=class_num)
        db.add(cls)
        db.flush()
    return cls


def ensure_teaching_class(db, wm, ay, subject: str, label: str):
    tc = (
        db.query(wm.TeachingClass)
        .filter_by(academic_year_id=ay.id, subject=subject, label=label)
        .one_or_none()
    )
    if tc is None:
        tc = wm.TeachingClass(academic_year_id=ay.id, subject=subject, label=label)
        db.add(tc)
        db.flush()
    return tc


def ensure_ws_identity(db, wm, domain: str, display_name: str | None):
    """按 (domain, display_name) 幂等ensure 域内身份。

    仅用于**无源主键**的派生身份（占位/撞号独立身份）；有源主键的身份走
    SourceMap 幂等（见 ensure_mapped_identity），二者不混用。
    """
    ident = (
        db.query(wm.WsStudentIdentity)
        .filter_by(data_domain=domain, display_name=display_name)
        .one_or_none()
    )
    if ident is None:
        ident = wm.WsStudentIdentity(data_domain=domain, display_name=display_name)
        db.add(ident)
        db.flush()
    return ident


def ensure_mapped_identity(db, wm, fp: str, domain: str, group_key: str, display_name: str | None):
    """有源主键（源 identity 组）的身份：SourceMap 命中即复用，绝不重建。"""
    m = (
        db.query(wm.SourceMap)
        .filter_by(source_fingerprint=fp, source_table="student_identity", source_pk=str(group_key))
        .one_or_none()
    )
    if m is not None:
        return db.get(wm.WsStudentIdentity, m.target_id), False
    ident = wm.WsStudentIdentity(data_domain=domain, display_name=display_name)
    db.add(ident)
    db.flush()
    db.add(
        wm.SourceMap(
            source_fingerprint=fp, source_table="student_identity",
            source_pk=str(group_key), target_table="ws_student_identity", target_id=ident.id,
        )
    )
    return ident, True


def ensure_ws_alias(db, wm, identity_id: int, alias_value: str, domain: str, ay, source: str):
    scope = str(ay.id) if ay is not None else "none"
    alias = (
        db.query(wm.WsStudentAlias)
        .filter_by(
            identity_id=identity_id, alias_value=alias_value,
            data_domain=domain, alias_scope=scope,
        )
        .one_or_none()
    )
    if alias is None:
        alias = wm.WsStudentAlias(
            identity_id=identity_id, alias_value=alias_value, data_domain=domain,
            source=source, academic_year_id=ay.id if ay is not None else None,
            alias_scope=scope,
        )
        db.add(alias)
        db.flush()
    return alias


def get_map(db, wm, fp: str, table: str, pk):
    return (
        db.query(wm.SourceMap)
        .filter_by(source_fingerprint=fp, source_table=table, source_pk=str(pk))
        .one_or_none()
    )


def put_map(db, wm, fp: str, table: str, pk, target_table: str, target_id: int) -> bool:
    """写来源映射；命中即 False（幂等跳过的信号）。"""
    if get_map(db, wm, fp, table, pk) is not None:
        return False
    db.add(
        wm.SourceMap(
            source_fingerprint=fp, source_table=table, source_pk=str(pk),
            target_table=target_table, target_id=target_id,
        )
    )
    return True


def ensure_score_fact(db, wm, domain, ay, exam_name, exam_date, class_ref_id, identity_id,
                      subject, total_type, score, grade_score, source):
    """成绩事实按自然键幂等：命中返回既有行（不覆盖值，交由冲突比对）。"""
    fact = (
        db.query(wm.ScoreFact)
        .filter_by(
            data_domain=domain, academic_year_id=ay.id, exam_name=exam_name,
            identity_id=identity_id, subject_key=subject or "", total_key=total_type or "",
        )
        .one_or_none()
    )
    if fact is not None:
        return fact, False
    fact = wm.ScoreFact(
        data_domain=domain, academic_year_id=ay.id, exam_name=exam_name,
        exam_date=exam_date, class_ref_id=class_ref_id, identity_id=identity_id,
        subject=subject, total_type=total_type, score=score, grade_score=grade_score,
        source=source,
    )
    db.add(fact)
    db.flush()
    return fact, True


def ensure_enrollment(db, wm, admin_class_id, identity_id, seat_no, status, valid_from, valid_to):
    e = (
        db.query(wm.Enrollment)
        .filter_by(admin_class_id=admin_class_id, identity_id=identity_id, valid_from=valid_from)
        .one_or_none()
    )
    if e is None:
        e = wm.Enrollment(
            admin_class_id=admin_class_id, identity_id=identity_id, seat_no=seat_no,
            status=status, valid_from=valid_from, valid_to=valid_to,
        )
        db.add(e)
        db.flush()
    return e


def ensure_assignment(db, wm, domain, class_ref_id, ay, subject, homework_type,
                      assigned_date, batch_token):
    a = db.query(wm.HomeworkAssignment).filter_by(batch_token=batch_token).one_or_none()
    if a is None:
        # expected_members 置空数组：源缺交行只有事实缺交者，无分母快照，
        # 绝不据此推断「其余人已交」（04-migration 步骤 8）。
        a = wm.HomeworkAssignment(
            data_domain=domain, class_ref_id=class_ref_id, academic_year_id=ay.id,
            subject=subject, homework_type=homework_type,
            assigned_date=assigned_date, batch_token=batch_token,
            expected_members_json="[]",
        )
        db.add(a)
        db.flush()
    return a


def ensure_submission(db, wm, assignment_id, person_id, status, evaluation):
    s = (
        db.query(wm.HomeworkSubmission)
        .filter_by(assignment_id=assignment_id, person_id=person_id)
        .one_or_none()
    )
    if s is None:
        s = wm.HomeworkSubmission(
            assignment_id=assignment_id, person_id=person_id,
            submission_status=status, evaluation=evaluation,
        )
        db.add(s)
        db.flush()
    return s


# ─────────────────────────────────────────────────────────────
# 阶段 1：snapshot —— 登记源摘要，与 manifest 比对
# ─────────────────────────────────────────────────────────────

def stage_snapshot(ctx: Rehearsal, db):
    manifest = json.load(open(ctx.manifest_path, encoding="utf-8"))
    for tag, path, fp in (("h", ctx.h_path, ctx.h_fp), ("t", ctx.t_path, ctx.t_fp)):
        actual = sha256_file(path)
        if actual != fp or manifest["sources"][tag]["sha256"] != actual:
            # manifest 与文件不符：源库被改动，拒绝在未知快照上演练
            raise RuntimeError(f"源库 {tag} 摘要与 manifest 不符，请重跑 build_synthetic_sources.py")
    counts = {}
    for tag, conn in (("h", ctx.h()), ("t", ctx.t())):
        try:
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            counts[tag] = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in sorted(tables)}
        finally:
            conn.close()
    return {
        "manifest_ok": True,
        "fingerprints": {"h": ctx.h_fp, "t": ctx.t_fp},
        "row_counts": counts,
        "note": "合成演练源（非真实生产库）；全部非零来源表进入 verify 四分类对账（Q05），零行表不进入母集",
    }


# ─────────────────────────────────────────────────────────────
# H 源身份/考试推导（import_homeroom 与 verify 共用）
# ─────────────────────────────────────────────────────────────

def h_grade_year_map(conn) -> dict[int, str | None]:
    """由源 exam 集合推导 年级→学年；同年级跨多学年 = 歧义 → None（不伪造）。"""
    mapping: dict[int, str | None] = {}
    for row in conn.execute("SELECT grade, exam_date FROM exam"):
        if not row["exam_date"]:
            continue
        ay = ay_name_for_date(date.fromisoformat(row["exam_date"]))
        if mapping.get(row["grade"], ay) != ay or (row["grade"] in mapping and mapping[row["grade"]] is None):
            mapping[row["grade"]] = None
        else:
            mapping[row["grade"]] = ay
    return mapping


def load_h_identity_groups(conn):
    """H 身份组：identity_id → {display_name, aliases: {student_id: alias_row}}。"""
    groups = {}
    for row in conn.execute("SELECT * FROM student_identity"):
        groups[row["id"]] = {"display_name": row["display_name"], "aliases": {}}
    for row in conn.execute("SELECT * FROM student_alias"):
        groups.setdefault(row["identity_id"], {"display_name": None, "aliases": {}})
        groups[row["identity_id"]]["aliases"][row["student_id"]] = row
    return groups


def resolve_h_identity(conn, groups, student_id: str, row_name: str | None):
    """H 学号 → (identity_key, display_name, conflict)。

    - alias 命中 → 归该身份组；行姓名与组 display_name 不符 → 撞号（同名
      不同人共号），conflict=True（各建独立身份、不合并、登记待核实）；
    - alias 未命中（只在成绩行出现的学号）→ 独立行身份。
    """
    group = None
    for gid, g in groups.items():
        if student_id in g["aliases"]:
            group = (gid, g)
            break
    if group is None:
        return f"row:{student_id}", row_name, False
    gid, g = group
    conflict = bool(row_name) and g["display_name"] is not None and row_name != g["display_name"]
    if conflict:
        return f"row:{student_id}:{row_name}", row_name, True
    return str(gid), g["display_name"], False


# ─────────────────────────────────────────────────────────────
# 阶段 2：import_homeroom —— H → ws homeroom 域
# ─────────────────────────────────────────────────────────────

def stage_import_homeroom(ctx: Rehearsal, db):
    wm = ctx.wm
    fp = ctx.fp_h  # H 源独立指纹（映射键前半）
    conn = ctx.h()
    try:
        grade_year = h_grade_year_map(conn)
        groups = load_h_identity_groups(conn)
        confirmed = load_pending_confirmations(ctx.root).get("h", set())
        classification: list[dict] = []
        stats = {"imported": 0, "skipped_mapped": 0, "quarantined": 0, "pending_review": 0}

        def entry(table, pk, category, kind, identity_id=None, identity_key=None,
                  ay_id=None, subject=None):
            classification.append({
                "table": table, "pk": str(pk), "category": category, "kind": kind,
                "identity_id": identity_id, "identity_key": identity_key,
                "ay_id": ay_id, "subject": subject,
            })

        def quarantine_reason(conflict: bool, sid: str | None, ay_known: bool):
            """逐行隔离裁决（Q04）：命中任一 → 待核实，绝不写业务表。"""
            if conflict:
                return "同名不同人共号：身份各自独立，不合并"
            if is_placeholder(sid):
                return "占位/临时学号：保留原值，隔离待核实"
            if not ay_known:
                return "无法推导学年：不伪造，待人工核实"
            return None

        def mapped_identity(key: str, display_name: str | None):
            # 身份层照常落库（含被隔离行的独立身份）：待核实的只是业务事实，
            # 确认补导时业务行必须能挂回同一身份，绝不重建/合并。
            return ensure_mapped_identity(db, wm, fp, "homeroom", key, display_name)[0]

        def record_skip(table: str, pk, target_model, id_attr: str = "identity_id"):
            """幂等重入：skip 行按**持久状态**分类——有 SourceMap 无 pending
            = 已映射（含确认后补导）；有 pending = 隔离待核实。分类字段从
            目标行反查，源侧零写入副作用。"""
            m = get_map(db, wm, fp, table, pk)
            kind = KIND_BY_TABLE.get(table)
            ay_id = subject = ident_id = None
            target = db.get(target_model, m.target_id)
            if target is not None:
                ident_id = getattr(target, id_attr, None)
                if table == "class_roster":
                    ay_id = db.get(wm.AdministrativeClass, target.admin_class_id).academic_year_id
                elif table in ("subject_score", "total_score"):
                    ay_id = target.academic_year_id
                    subject = target.subject
                elif table == "homework_record":
                    a = db.get(wm.HomeworkAssignment, target.assignment_id)
                    ay_id, subject = a.academic_year_id, a.subject
            cat = "quarantined" if get_pending(db, wm, fp, table, pk) is not None else "mapped"
            entry(table, pk, cat, kind, ident_id, None, ay_id, subject)

        # 1) exam → AcademicYear/Term（日期推导；无日期回退 年级→学年 映射）
        for row in conn.execute("SELECT * FROM exam ORDER BY id"):
            if get_map(db, wm, fp, "exam", row["id"]) is not None:
                stats["skipped_mapped"] += 1
                entry("exam", row["id"], "mapped", "structure")
                continue
            if row["exam_date"]:
                ay_name = ay_name_for_date(date.fromisoformat(row["exam_date"]))
            else:
                ay_name = grade_year.get(row["grade"])  # 推导不出 → None，不伪造
            if ay_name is None:
                raise RuntimeError(f"exam {row['id']} 无法推导学年（不伪造）")
            ay = ensure_academic_year(db, wm, ay_name)
            term = ensure_term(db, wm, ay, "上学期" if row["semester"] == "上" else "下学期",
                               *term_bounds(ay_name, row["semester"]))
            put_map(db, wm, fp, "exam", row["id"], "term", term.id)
            stats["imported"] += 1
            entry("exam", row["id"], "mapped", "structure")

        # exam_id → (ay, exam_date, grade) 供成绩行复用
        exam_index = {}
        for row in conn.execute("SELECT * FROM exam"):
            m = get_map(db, wm, fp, "exam", row["id"])
            ay = db.get(wm.Term, m.target_id).academic_year_id
            exam_index[row["id"]] = {
                "ay_id": ay, "name": row["name"],
                "date": d(row["exam_date"]) or d(str(db.query(wm.AcademicYear).get(ay).start_date)),
                "grade": row["grade"],
            }

        # 2) StudentAlias → WsStudentAlias（原值保留含前缀，绝不剥壳）
        for row in conn.execute("SELECT * FROM student_alias ORDER BY id"):
            if get_map(db, wm, fp, "student_alias", row["id"]) is not None:
                stats["skipped_mapped"] += 1
                entry("student_alias", row["id"], "mapped", "alias")
                continue
            ident = mapped_identity(str(row["identity_id"]),
                                    groups[row["identity_id"]]["display_name"])
            ay = None
            ay_name = grade_year.get(row["grade"]) if row["grade"] is not None else None
            if ay_name is not None:
                ay = ensure_academic_year(db, wm, ay_name)
            alias = ensure_ws_alias(db, wm, ident.id, row["student_id"], "homeroom", ay, "migration:h")
            put_map(db, wm, fp, "student_alias", row["id"], "ws_student_alias", alias.id)
            stats["imported"] += 1
            entry("student_alias", row["id"], "mapped", "alias")

        # 2b) student_identity 分类条目（身份层：组映射由别名导入/此处幂等补齐）
        for row in conn.execute("SELECT * FROM student_identity ORDER BY id"):
            if get_map(db, wm, fp, "student_identity", row["id"]) is None:
                ensure_mapped_identity(db, wm, fp, "homeroom", str(row["id"]),
                                       row["display_name"])
            entry("student_identity", row["id"], "mapped", "identity")

        # 3) ClassRoster → AdministrativeClass/Enrollment
        #    有效期按学年推导（valid_from=学年起、离班 valid_to=学年末——学年
        #    粒度推导值，不伪造精确离班日）；status=NULL 按在班。
        #    撞号同名/占位学号/未知学年 → PendingImportRow，绝不写业务表。
        for row in conn.execute("SELECT * FROM class_roster ORDER BY rowid"):
            if get_map(db, wm, fp, "class_roster", row["student_id"]) is not None:
                stats["skipped_mapped"] += 1
                record_skip("class_roster", row["student_id"], wm.Enrollment)
                continue
            key, display, conflict = resolve_h_identity(conn, groups, row["student_id"], row["name"])
            ident = mapped_identity(key, display)
            ay_name = grade_year.get(row["grade"]) if row["grade"] is not None else None
            reason = quarantine_reason(conflict, row["student_id"], ay_name is not None)
            if reason is not None and ("class_roster", str(row["student_id"])) not in confirmed:
                ensure_pending(db, wm, fp, "homeroom", "class_roster", row["student_id"], reason, row)
                entry("class_roster", row["student_id"], "quarantined", "roster",
                      ident.id, key)
                continue
            if ay_name is None:
                raise RuntimeError(f"roster {row['student_id']} 无法推导学年（不伪造）")
            ay = ensure_academic_year(db, wm, ay_name)
            start, end = ay_bounds(ay_name)
            cls = ensure_admin_class(db, wm, ay, row["grade"], row["class_num"] or 0)
            status = row["status"] or "active"
            valid_to = end if status in ("transferred", "graduated") else None
            enroll = ensure_enrollment(db, wm, cls.id, ident.id, row["seat_no"], status, start, valid_to)
            put_map(db, wm, fp, "class_roster", row["student_id"], "enrollment", enroll.id)
            confirm_pending(db, wm, fp, "class_roster", row["student_id"])
            stats["imported"] += 1
            entry("class_roster", row["student_id"], "mapped", "roster", ident.id, key, ay.id)

        # 4) SubjectScore → ScoreFact(source='migration:h')，全科逐字段保留
        for row in conn.execute("SELECT * FROM subject_score ORDER BY id"):
            if get_map(db, wm, fp, "subject_score", row["id"]) is not None:
                stats["skipped_mapped"] += 1
                record_skip("subject_score", row["id"], wm.ScoreFact)
                continue
            key, display, conflict = resolve_h_identity(conn, groups, row["student_id"], row["name"])
            ident = mapped_identity(key, display)
            ex = exam_index[row["exam_id"]]
            # 学年由考试结构行决定（ structural ），成绩行只裁决身份维度
            reason = quarantine_reason(conflict, row["student_id"], True)
            if reason is not None and ("subject_score", str(row["id"])) not in confirmed:
                ensure_pending(db, wm, fp, "homeroom", "subject_score", row["id"], reason, row)
                entry("subject_score", row["id"], "quarantined", "score",
                      ident.id, key, ex["ay_id"], row["subject"])
                continue
            ay = db.get(wm.AcademicYear, ex["ay_id"])
            cls = None
            if row["class_num"] is not None:
                cls = ensure_admin_class(db, wm, ay, ex["grade"], row["class_num"])
            fact, created = ensure_score_fact(
                db, wm, "homeroom", ay, ex["name"], ex["date"],
                cls.id if cls else None, ident.id,
                row["subject"], None, row["raw_score"], row["grade_score"], "migration:h",
            )
            put_map(db, wm, fp, "subject_score", row["id"], "score_fact", fact.id)
            confirm_pending(db, wm, fp, "subject_score", row["id"])
            stats["imported"] += created
            entry("subject_score", row["id"], "mapped", "score",
                  ident.id, key, ex["ay_id"], row["subject"])

        # 5) TotalScore → ScoreFact(total 口径)
        for row in conn.execute("SELECT * FROM total_score ORDER BY id"):
            if get_map(db, wm, fp, "total_score", row["id"]) is not None:
                stats["skipped_mapped"] += 1
                record_skip("total_score", row["id"], wm.ScoreFact)
                continue
            key, display, conflict = resolve_h_identity(conn, groups, row["student_id"], None)
            ident = mapped_identity(key, display)
            ex = exam_index[row["exam_id"]]
            reason = quarantine_reason(conflict, row["student_id"], True)
            if reason is not None and ("total_score", str(row["id"])) not in confirmed:
                ensure_pending(db, wm, fp, "homeroom", "total_score", row["id"], reason, row)
                entry("total_score", row["id"], "quarantined", "total",
                      ident.id, key, ex["ay_id"], None)
                continue
            ay = db.get(wm.AcademicYear, ex["ay_id"])
            fact, created = ensure_score_fact(
                db, wm, "homeroom", ay, ex["name"], ex["date"], None, ident.id,
                None, row["total_type"], row["total_score"], None, "migration:h",
            )
            put_map(db, wm, fp, "total_score", row["id"], "score_fact", fact.id)
            confirm_pending(db, wm, fp, "total_score", row["id"])
            stats["imported"] += created
            entry("total_score", row["id"], "mapped", "total",
                  ident.id, key, ex["ay_id"], None)

        # 6) HomeworkRecord → legacy 批次 + 逐人提交
        #    同日同科同班同人第 n 行 → 独立 legacy 批次（token 含 seq），绝不
        #    压成一次作业；remark 非空 → excused（请假），其余缺交 missing。
        hw_rows = list(conn.execute("SELECT * FROM homework_record ORDER BY id"))
        seq_counter: dict[tuple, int] = {}
        roster_cls = {}
        for r in conn.execute("SELECT * FROM class_roster"):
            roster_cls[r["student_id"]] = (r["grade"], r["class_num"])
        for row in hw_rows:
            if get_map(db, wm, fp, "homework_record", row["id"]) is not None:
                stats["skipped_mapped"] += 1
                record_skip("homework_record", row["id"], wm.HomeworkSubmission,
                            id_attr="person_id")
                continue
            key, display, conflict = resolve_h_identity(conn, groups, row["student_id"], None)
            ident = mapped_identity(key, display)
            grade, class_num = roster_cls.get(row["student_id"], (None, None))
            ay_name = grade_year.get(grade) if grade is not None else None
            reason = quarantine_reason(conflict, row["student_id"], ay_name is not None)
            if reason is not None and ("homework_record", str(row["id"])) not in confirmed:
                ensure_pending(db, wm, fp, "homeroom", "homework_record", row["id"], reason, row)
                entry("homework_record", row["id"], "quarantined", "homework",
                      ident.id, key, None, row["subject"])
                continue
            if ay_name is None:
                raise RuntimeError(f"homework {row['id']} 无法推导学年（不伪造）")
            ay = ensure_academic_year(db, wm, ay_name)
            cls = ensure_admin_class(db, wm, ay, grade, class_num or 0)
            seq_key = (row["date"], row["subject"], cls.id, ident.id)
            seq = seq_counter.get(seq_key, 0)
            seq_counter[seq_key] = seq + 1
            token = f"migration:h:{row['date']}:{row['subject']}:{seq}:{cls.id}"
            assignment = ensure_assignment(db, wm, "homeroom", cls.id, ay, row["subject"],
                                           LEGACY_HOMEWORK_TYPE, d(row["date"]), token)
            status = "excused" if row["remark"] else "missing"
            sub = ensure_submission(db, wm, assignment.id, ident.id, status, None)
            put_map(db, wm, fp, "homework_record", row["id"], "homework_submission", sub.id)
            confirm_pending(db, wm, fp, "homework_record", row["id"])
            stats["imported"] += 1
            entry("homework_record", row["id"], "mapped", "homework",
                  ident.id, key, ay.id, row["subject"])

        # 隔离计数以持久层为准（重入/确认后计数稳定，不靠重算）
        pending_count = db.query(wm.PendingImportRow).filter_by(source_fingerprint=fp).count()
        stats["quarantined"] = pending_count
        stats["pending_review"] = pending_count
        ctx.write_report("pending_import_rows.json", pending_report_rows(db, wm))
        ctx.write_report("classification_h.json", {
            "scope": "synthetic-rehearsal", "fingerprint": fp,
            "tables": MIGRATED_TABLES["h"], "rows": classification,
        })
        return stats
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# 阶段 3：import_teaching —— T → ws teaching 域（丢弃列只统计不恢复）
# ─────────────────────────────────────────────────────────────

def t_grade_year_map(conn) -> dict[int, str | None]:
    return h_grade_year_map(conn)  # 推导规则同 H（exam_date → 学年）


def load_t_identity_groups(conn):
    return load_h_identity_groups(conn)  # T 身份表结构与 H 同构


def resolve_t_identity(groups, student_id: str, row_name: str | None = None):
    """T 学号 → (identity_key, display_name)；alias 未命中 → 独立行身份，
    保留行姓名（与 H 口径一致，绝不落 NULL 姓名）。"""
    for gid, g in groups.items():
        if student_id in g["aliases"]:
            return str(gid), g["display_name"]
    return f"row:{student_id}", row_name


def stage_import_teaching(ctx: Rehearsal, db):
    wm = ctx.wm
    fp = ctx.fp_t  # T 源独立指纹（映射键前半）
    conn = ctx.t()
    try:
        grade_year = t_grade_year_map(conn)
        groups = load_t_identity_groups(conn)
        teacher = conn.execute("SELECT * FROM teacher ORDER BY id LIMIT 1").fetchone()
        teaching_subject = teacher["subject"] if teacher and teacher["subject"] else None
        confirmed = load_pending_confirmations(ctx.root).get("t", set())
        classification: list[dict] = []
        lost = {"non_teaching_subject_rows": 0, "non_teaching_subjects": [],
                "total_score_rows": 0, "note": "T 旧链路丢弃的其他学科列与停写总分行：不恢复，仅统计"}
        stats = {"imported": 0, "skipped_mapped": 0, "quarantined": 0,
                 "teaching_subject": teaching_subject, "lost_columns": lost}

        def entry(table, pk, category, kind, identity_id=None, identity_key=None,
                  ay_id=None, subject=None, reason=None):
            classification.append({
                "table": table, "pk": str(pk), "category": category, "kind": kind,
                "identity_id": identity_id, "identity_key": identity_key,
                "ay_id": ay_id, "subject": subject, "reason": reason,
            })

        def mapped_identity(key: str, display_name: str | None):
            return ensure_mapped_identity(db, wm, fp, "teaching", key, display_name)[0]

        def sid_identity(student_id: str, row_name: str | None = None):
            key, display = resolve_t_identity(groups, student_id, row_name)
            return mapped_identity(key, display), key

        def record_skip(table: str, pk, target_model, id_attr: str = "identity_id"):
            """幂等重入：skip 行按**持久状态**分类（同 H 侧口径）。"""
            m = get_map(db, wm, fp, table, pk)
            kind = KIND_BY_TABLE.get(table)
            ay_id = subject = ident_id = None
            target = db.get(target_model, m.target_id)
            if target is not None:
                ident_id = getattr(target, id_attr, None)
                if table == "teaching_class_member":
                    ay_id = db.get(wm.TeachingClass, target.teaching_class_id).academic_year_id
                elif table == "subject_score":
                    ay_id, subject = target.academic_year_id, target.subject
                elif table == "homework_record":
                    a = db.get(wm.HomeworkAssignment, target.assignment_id)
                    ay_id, subject = a.academic_year_id, a.homework_type
            cat = "quarantined" if get_pending(db, wm, fp, table, pk) is not None else "mapped"
            entry(table, pk, cat, kind, ident_id, None, ay_id, subject)

        # 1) exam → 学年/学期（与 H 共用同一 AcademicYear/Term，按名幂等）
        exam_index = {}
        for row in conn.execute("SELECT * FROM exam ORDER BY id"):
            if get_map(db, wm, fp, "exam", row["id"]) is not None:
                stats["skipped_mapped"] += 1
                entry("exam", row["id"], "mapped", "structure")
                continue
            ay_name = (ay_name_for_date(date.fromisoformat(row["exam_date"]))
                       if row["exam_date"] else grade_year.get(row["grade"]))
            if ay_name is None:
                raise RuntimeError(f"T exam {row['id']} 无法推导学年（不伪造）")
            ay = ensure_academic_year(db, wm, ay_name)
            term = ensure_term(db, wm, ay, "上学期" if row["semester"] == "上" else "下学期",
                               *term_bounds(ay_name, row["semester"]))
            put_map(db, wm, fp, "exam", row["id"], "term", term.id)
            stats["imported"] += 1
            entry("exam", row["id"], "mapped", "structure")
        for row in conn.execute("SELECT * FROM exam"):
            m = get_map(db, wm, fp, "exam", row["id"])
            ay_id = db.get(wm.Term, m.target_id).academic_year_id
            ay = db.query(wm.AcademicYear).get(ay_id)
            exam_index[row["id"]] = {"ay": ay, "name": row["name"],
                                     "date": d(row["exam_date"]) or ay.start_date}

        # 2) TeachingClass → TeachingClass（同名班跨学年独立：学年参与唯一键）
        for row in conn.execute("SELECT * FROM teaching_class ORDER BY id"):
            if get_map(db, wm, fp, "teaching_class", row["id"]) is not None:
                stats["skipped_mapped"] += 1
                entry("teaching_class", row["id"], "mapped", "structure")
                continue
            ay_name = grade_year.get(row["grade"])
            if ay_name is None:
                raise RuntimeError(f"T teaching_class {row['id']} 无法推导学年（不伪造）")
            ay = ensure_academic_year(db, wm, ay_name)
            tc = ensure_teaching_class(db, wm, ay, row["subject"] or teaching_subject or "未知",
                                       row["label"])
            put_map(db, wm, fp, "teaching_class", row["id"], "teaching_class", tc.id)
            stats["imported"] += 1
            entry("teaching_class", row["id"], "mapped", "structure")

        # 3) StudentAlias → WsStudentAlias（teaching 域，原值保留含前缀）
        for row in conn.execute("SELECT * FROM student_alias ORDER BY id"):
            if get_map(db, wm, fp, "student_alias", row["id"]) is not None:
                stats["skipped_mapped"] += 1
                entry("student_alias", row["id"], "mapped", "alias")
                continue
            key, display = resolve_t_identity(groups, row["student_id"])
            ident = mapped_identity(key, display)
            ay = None
            ay_name = grade_year.get(row["grade"]) if row["grade"] is not None else None
            if ay_name is not None:
                ay = ensure_academic_year(db, wm, ay_name)
            alias = ensure_ws_alias(db, wm, ident.id, row["student_id"], "teaching", ay, "migration:t")
            put_map(db, wm, fp, "student_alias", row["id"], "ws_student_alias", alias.id)
            stats["imported"] += 1
            entry("student_alias", row["id"], "mapped", "alias")

        # 3b) student_identity 分类条目（身份层，同 H 侧）
        for row in conn.execute("SELECT * FROM student_identity ORDER BY id"):
            if get_map(db, wm, fp, "student_identity", row["id"]) is None:
                ensure_mapped_identity(db, wm, fp, "teaching", str(row["id"]),
                                       row["display_name"])
            entry("student_identity", row["id"], "mapped", "identity")

        # 4) 成员（标签+来源）→ TeachingClassMember；占位学号 → 待核实
        for row in conn.execute("SELECT * FROM teaching_class_member ORDER BY id"):
            if get_map(db, wm, fp, "teaching_class_member", row["id"]) is not None:
                stats["skipped_mapped"] += 1
                record_skip("teaching_class_member", row["id"], wm.TeachingClassMember)
                continue
            ident, id_key = sid_identity(row["student_id"], row["name"])
            reason = None
            if is_placeholder(row["student_id"]):
                reason = "占位学号成员：保留原值，隔离待核实"
            if reason is not None and ("teaching_class_member", str(row["id"])) not in confirmed:
                ensure_pending(db, wm, fp, "teaching", "teaching_class_member", row["id"],
                               reason, row)
                entry("teaching_class_member", row["id"], "quarantined", "roster",
                      ident.id, id_key)
                continue
            tc_target = get_map(db, wm, fp, "teaching_class", row["teaching_class_id"])
            tc = db.get(wm.TeachingClass, tc_target.target_id)
            ay = db.query(wm.AcademicYear).get(tc.academic_year_id)
            member = (
                db.query(wm.TeachingClassMember)
                .filter_by(teaching_class_id=tc.id, identity_id=ident.id, valid_from=ay.start_date)
                .one_or_none()
            )
            if member is None:
                member = wm.TeachingClassMember(
                    teaching_class_id=tc.id, identity_id=ident.id,
                    valid_from=ay.start_date, source=row["source"] or "manual",
                )
                db.add(member)
                db.flush()
            put_map(db, wm, fp, "teaching_class_member", row["id"], "teaching_class_member", member.id)
            confirm_pending(db, wm, fp, "teaching_class_member", row["id"])
            stats["imported"] += 1
            entry("teaching_class_member", row["id"], "mapped", "roster",
                  ident.id, id_key, ay.id)

        # 5) 成绩：只导任教学科；其他学科列显式 not_migrated（lost_columns 统计）
        #    教师任教学科未配置 → 全部成绩待核实（不猜学科，Q08）。
        for row in conn.execute("SELECT * FROM subject_score ORDER BY id"):
            if teaching_subject is not None and row["subject"] != teaching_subject:
                lost["non_teaching_subject_rows"] += 1
                if row["subject"] not in lost["non_teaching_subjects"]:
                    lost["non_teaching_subjects"].append(row["subject"])
                entry("subject_score", row["id"], "not_migrated", "score",
                      subject=row["subject"],
                      reason="非任教学科成绩列：旧链路已丢弃，不恢复（见 lost_columns）")
                continue
            if get_map(db, wm, fp, "subject_score", row["id"]) is not None:
                stats["skipped_mapped"] += 1
                record_skip("subject_score", row["id"], wm.ScoreFact)
                continue
            ident, id_key = sid_identity(row["student_id"], row["name"])
            ex = exam_index[row["exam_id"]]
            reason = None
            if teaching_subject is None:
                reason = "教师任教学科未配置：无法判定任教学科成绩，不猜"
            elif is_placeholder(row["student_id"]):
                reason = "占位学号成员：保留原值，隔离待核实"
            if reason is not None and ("subject_score", str(row["id"])) not in confirmed:
                ensure_pending(db, wm, fp, "teaching", "subject_score", row["id"], reason, row)
                entry("subject_score", row["id"], "quarantined", "score",
                      ident.id, id_key, ex["ay"].id, row["subject"], reason)
                continue
            tc = None
            if row["class_label"]:
                tc = ensure_teaching_class(db, wm, ex["ay"], row["subject"], row["class_label"])
            fact, created = ensure_score_fact(
                db, wm, "teaching", ex["ay"], ex["name"], ex["date"],
                tc.id if tc else None, ident.id, row["subject"], None,
                row["raw_score"], row["grade_score"], "migration:t",
            )
            put_map(db, wm, fp, "subject_score", row["id"], "score_fact", fact.id)
            confirm_pending(db, wm, fp, "subject_score", row["id"])
            stats["imported"] += created
            entry("subject_score", row["id"], "mapped", "score",
                  ident.id, id_key, ex["ay"].id, row["subject"])
        for row in conn.execute("SELECT * FROM total_score ORDER BY id"):
            lost["total_score_rows"] += 1  # 停写遗留总分：不导入
            entry("total_score", row["id"], "not_migrated", "total",
                  reason="停写遗留总分列：旧链路已停写，不导入（见 lost_columns）")

        # M01 中断注入（测试专用）：成绩段已写、作业段未写时抛出——验证
        # 阶段级事务回滚与重跑幂等；环境变量缺省绝不触发。
        if os.environ.get("REHEARSAL_FAIL_INJECT") == "import_teaching":
            raise RuntimeError("注入的 import_teaching 中途失败（REHEARSAL_FAIL_INJECT）")

        # 6) HomeworkRecord（submission_status/evaluation 保留）→ legacy 批次
        #    Q08 语义：assignment.subject = 教师任教学科（Teacher 配置；未配置
        #    → 待核实不猜）；homework_type = 旧 subject 列原值（作业种类）。
        tcs = {r["id"]: r for r in conn.execute("SELECT * FROM teaching_class")}
        member_tc: dict[str, int] = {}
        for r in conn.execute("SELECT * FROM teaching_class_member ORDER BY id"):
            member_tc.setdefault(r["student_id"], r["teaching_class_id"])
        hw_rows = list(conn.execute("SELECT * FROM homework_record ORDER BY id"))
        status_map = {"缺交": "missing", "已交": "submitted", "请假": "excused"}
        seq_counter: dict[tuple, int] = {}
        for row in hw_rows:
            if get_map(db, wm, fp, "homework_record", row["id"]) is not None:
                stats["skipped_mapped"] += 1
                record_skip("homework_record", row["id"], wm.HomeworkSubmission,
                            id_attr="person_id")
                continue
            ident, id_key = sid_identity(row["student_id"])
            tc_src = member_tc.get(row["student_id"])
            if tc_src is None:
                raise RuntimeError(f"T homework {row['id']} 找不到成员教学班（不伪造）")
            tc_target = get_map(db, wm, fp, "teaching_class", tc_src)
            tc = db.get(wm.TeachingClass, tc_target.target_id)
            ay = db.query(wm.AcademicYear).get(tc.academic_year_id)
            reason = None
            if teaching_subject is None:
                reason = "教师任教学科未配置：作业学科不猜，待核实"
            elif is_placeholder(row["student_id"]):
                reason = "占位学号成员：保留原值，隔离待核实"
            if reason is not None and ("homework_record", str(row["id"])) not in confirmed:
                ensure_pending(db, wm, fp, "teaching", "homework_record", row["id"], reason, row)
                entry("homework_record", row["id"], "quarantined", "homework",
                      ident.id, id_key, ay.id, row["subject"], reason)
                continue
            # seq/token 以作业种类区分：同日同种多份 = 独立批次，不压成一次
            seq_key = (row["date"], row["subject"], tc.id, ident.id)
            seq = seq_counter.get(seq_key, 0)
            seq_counter[seq_key] = seq + 1
            token = f"migration:t:{row['date']}:{row['subject']}:{seq}:{tc.id}"
            assignment = ensure_assignment(db, wm, "teaching", tc.id, ay, teaching_subject,
                                           row["subject"], d(row["date"]), token)
            sub = ensure_submission(db, wm, assignment.id, ident.id,
                                    status_map.get(row["submission_status"], "unknown"),
                                    row["evaluation"])
            put_map(db, wm, fp, "homework_record", row["id"], "homework_submission", sub.id)
            confirm_pending(db, wm, fp, "homework_record", row["id"])
            stats["imported"] += 1
            entry("homework_record", row["id"], "mapped", "homework",
                  ident.id, id_key, ay.id, row["subject"])

        # 隔离计数以持久层为准（重入/确认后计数稳定，不靠重算）
        pending_count = db.query(wm.PendingImportRow).filter_by(source_fingerprint=fp).count()
        stats["quarantined"] = pending_count
        stats["pending_review"] = pending_count
        ctx.write_report("pending_import_rows.json", pending_report_rows(db, wm))
        ctx.write_report("classification_t.json", {
            "scope": "synthetic-rehearsal", "fingerprint": fp,
            "tables": MIGRATED_TABLES["t"], "rows": classification,
        })
        ctx.write_report("lost_columns.json", lost)
        return stats
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# 阶段 4：link_suggest —— 候选只建议，声明表确认才写入
# ─────────────────────────────────────────────────────────────

def stage_link_suggest(ctx: Rehearsal, db):
    wm = ctx.wm
    fp = ctx.fp_h
    declaration = json.load(open(ctx.declare_path, encoding="utf-8"))
    h_conn, t_conn = ctx.h(), ctx.t()
    try:
        # ── 候选生成（只建议，绝不自动建）──
        candidates = []
        t_classes = list(t_conn.execute("SELECT * FROM teaching_class"))
        h_roster = list(h_conn.execute("SELECT * FROM class_roster"))
        t_members = list(t_conn.execute("SELECT * FROM teaching_class_member"))
        for tc in t_classes:
            label_num = int(tc["label"]) if str(tc["label"]).isdigit() else None
            if label_num is None:
                continue
            h_sids = {r["student_id"] for r in h_roster
                      if r["grade"] == tc["grade"] and r["class_num"] == label_num}
            t_sids = {m["student_id"] for m in t_members if m["teaching_class_id"] == tc["id"]}
            candidates.append({
                "t_grade": tc["grade"], "t_label": tc["label"], "t_subject": tc["subject"],
                "h_grade": tc["grade"], "h_class_num": label_num,
                "student_overlap": sorted(h_sids & t_sids),
            })
        ctx.write_report("link_candidates.json", {"candidates": candidates,
                                                  "note": "同号同名仅作建议；写入只认声明表"})

        # ── 声明表确认 → HomeroomTeachingLink + LinkedStudent ──
        written = {"links": 0, "students": 0}
        for link in declaration.get("links", []):
            ay = db.query(wm.AcademicYear).filter_by(name=link["academic_year"]).one_or_none()
            if ay is None:
                raise RuntimeError(f"声明 link 学年 {link['academic_year']} 不在目标库（先跑导入）")
            admin = (
                db.query(wm.AdministrativeClass)
                .filter_by(academic_year_id=ay.id, grade=link["h_grade"], class_num=link["h_class_num"])
                .one_or_none()
            )
            tc = (
                db.query(wm.TeachingClass)
                .filter_by(academic_year_id=ay.id, subject=link["t_subject"], label=link["t_label"])
                .one_or_none()
            )
            if admin is None or tc is None:
                raise RuntimeError("声明 link 的班级未导入，拒绝凭空建关联")
            htl = (
                db.query(wm.HomeroomTeachingLink)
                .filter_by(admin_class_id=admin.id, teaching_class_id=tc.id,
                           academic_year_id=ay.id, subject=link["t_subject"])
                .one_or_none()
            )
            if htl is None:
                htl = wm.HomeroomTeachingLink(
                    admin_class_id=admin.id, teaching_class_id=tc.id,
                    academic_year_id=ay.id, subject=link["t_subject"],
                    valid_from=ay.start_date,
                    share_history_from=d(link.get("share_history_from")),
                )
                db.add(htl)
                db.flush()
                written["links"] += 1
            link["_id"] = htl.id
            link["_ay_id"] = ay.id

        # 学号 → 各域身份（经目标 WsStudentAlias 按域反查，绝不按同名同号自动建）
        def identity_by_alias(domain: str, alias_value: str):
            alias = (
                db.query(wm.WsStudentAlias)
                .filter_by(data_domain=domain, alias_value=alias_value)
                .one_or_none()
            )
            return alias.identity_id if alias else None

        for stu in declaration.get("students", []):
            htl = next((l for l in declaration.get("links", []) if "_id" in l), None)
            if htl is None:
                raise RuntimeError("声明 linked_student 前必须声明其所属 link")
            h_id = identity_by_alias("homeroom", stu["h_student_id"])
            t_id = identity_by_alias("teaching", stu["t_student_id"])
            if h_id is None or t_id is None:
                raise RuntimeError(f"声明学号无法解析为两域身份：{stu}")
            ls = (
                db.query(wm.LinkedStudent)
                .filter_by(link_id=htl["_id"], homeroom_identity_id=h_id, teaching_identity_id=t_id)
                .one_or_none()
            )
            if ls is None:
                # confirm_basis 如实登记「演练声明」：非人工逐人确认
                db.add(wm.LinkedStudent(
                    link_id=htl["_id"], homeroom_identity_id=h_id,
                    teaching_identity_id=t_id, confirm_basis="rehearsal-declared",
                ))
                db.flush()
                written["students"] += 1
        return {
            "candidates": len(candidates),
            "declared_links": len(declaration.get("links", [])),
            "declared_students": len(declaration.get("students", [])),
            **written,
            "auto_created": 0,
        }
    finally:
        h_conn.close()
        t_conn.close()


# ─────────────────────────────────────────────────────────────
# 阶段 5：conflict_report —— 关联班同场同科两域值比对（不静默覆盖）
# ─────────────────────────────────────────────────────────────

def stage_conflict_report(ctx: Rehearsal, db):
    wm = ctx.wm
    links = db.query(wm.HomeroomTeachingLink).filter_by(status="active").all()
    pairs = (
        db.query(wm.LinkedStudent).all()
    )
    conflicts, compared, identical = [], 0, 0
    for link in links:
        link_pairs = [p for p in pairs if p.link_id == link.id]
        for p in link_pairs:
            h_facts = (
                db.query(wm.ScoreFact)
                .filter_by(data_domain="homeroom", academic_year_id=link.academic_year_id,
                           identity_id=p.homeroom_identity_id, subject_key=link.subject)
                .all()
            )
            t_facts = (
                db.query(wm.ScoreFact)
                .filter_by(data_domain="teaching", academic_year_id=link.academic_year_id,
                           identity_id=p.teaching_identity_id, subject_key=link.subject)
                .filter(wm.ScoreFact.total_key == "")
                .all()
            )
            by_exam_h = {f.exam_name: f for f in h_facts}
            by_exam_t = {f.exam_name: f for f in t_facts}
            for exam_name in sorted(set(by_exam_h) & set(by_exam_t)):
                hv, tv = by_exam_h[exam_name].score, by_exam_t[exam_name].score
                compared += 1
                if hv != tv:
                    conflicts.append({
                        "exam_name": exam_name, "academic_year_id": link.academic_year_id,
                        "subject": link.subject,
                        "homeroom_identity_id": p.homeroom_identity_id,
                        "teaching_identity_id": p.teaching_identity_id,
                        "homeroom_value": hv, "teaching_value": tv,
                        "resolution": "kept-both-no-overwrite",  # 矛盾值列冲突，不按来源覆盖
                    })
                else:
                    identical += 1
    payload = {"compared": compared, "conflicts": conflicts, "identical": identical,
               "note": "冲突不静默覆盖：两域事实各自保留，待人工裁决"}
    ctx.write_report("conflicts.json", payload)
    return {"compared": compared, "conflicts": len(conflicts), "identical": identical}


# ─────────────────────────────────────────────────────────────
# 阶段 6：undo_audit —— H 撤销快照 JSON 逐项映射验证
# ─────────────────────────────────────────────────────────────

def _alias_exists(db, wm, alias_value: str) -> int | None:
    a = db.query(wm.WsStudentAlias).filter_by(
        data_domain="homeroom", alias_value=alias_value).one_or_none()
    return a.identity_id if a else None


def stage_undo_audit(ctx: Rehearsal, db):
    wm = ctx.wm
    conn = ctx.h()
    try:
        items = []
        for table in UNDO_TABLES:
            for batch in conn.execute(f"SELECT * FROM {table}"):
                refs: list[tuple[str, str]] = []  # (ref_type, 学号)
                payload = json.loads(batch["payload"] or "[]")
                if table == "roster_import_batch":
                    for row in json.loads(batch["created_rows"] or "[]"):
                        refs.append(("created_row", row.get("student_id")))
                    for row in json.loads(batch["replaced_rows"] or "[]"):
                        refs.append(("replaced_old", (row.get("old") or {}).get("student_id")))
                        refs.append(("replaced_new", row.get("new_student_id")))
                    renamed = json.loads(batch["renamed"] or "{}")
                    for old, new in renamed.items():
                        refs.append(("renamed_old", old))
                        refs.append(("renamed_new", new))
                else:
                    for p in payload:
                        refs.append(("g1_student_id", p.get("g1_student_id")))
                        refs.append(("g2_student_id", p.get("g2_student_id")))
                    for a in json.loads(batch["created_aliases"] or "[]"):
                        refs.append(("created_alias", a.get("student_id")))
                for ref_type, sid in refs:
                    if not sid:
                        continue
                    identity_id = _alias_exists(db, wm, sid)
                    items.append({
                        "batch_table": table, "batch_id": batch["id"], "ref_type": ref_type,
                        "student_id": sid,
                        "status": "convertible" if identity_id is not None else "read-only",
                        "resolved_identity_id": identity_id,
                    })
        summary = {
            "batches": len({(i["batch_table"], i["batch_id"]) for i in items}),
            "items": len(items),
            "convertible": sum(1 for i in items if i["status"] == "convertible"),
            "read_only": sum(1 for i in items if i["status"] == "read-only"),
            "note": "read-only=引用已漂移/无法映射：批次保留只读审计，不支持直接撤销",
        }
        ctx.write_report("undo_audit.json", {"summary": summary, "items": items})
        return summary
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# 阶段 7：verify —— 对账（四分类互斥完备 / 完整性 / 逐字段 / 条数 / 语义）
# ─────────────────────────────────────────────────────────────

def stage_verify(ctx: Rehearsal, db):
    wm = ctx.wm
    fp_h, fp_t = ctx.fp_h, ctx.fp_t
    report: dict = {"checks": {}}
    failures: list[str] = []

    # 0) shared 升级的单一事实源：active link + 声明确认身份对。
    #    shared 按**事实**判定（身份在确认对 + 学年相同 + 类别/学科在
    #    ADR-005 白名单 roster/current_subject_score 内），绝不按人整体升级。
    link_infos = []
    for link in db.query(wm.HomeroomTeachingLink).filter_by(status="active").all():
        pairs = db.query(wm.LinkedStudent).filter_by(link_id=link.id).all()
        link_infos.append({
            "ay_id": link.academic_year_id,
            "subject": link.subject,
            "h_ids": {p.homeroom_identity_id for p in pairs},
            "t_ids": {p.teaching_identity_id for p in pairs},
        })

    def fact_is_shared(row: dict, tag: str) -> bool:
        if row.get("category") != "mapped":
            return False
        if row.get("kind") not in ("roster", "score"):
            return False
        ident, ay_id = row.get("identity_id"), row.get("ay_id")
        if ident is None or ay_id is None:
            return False
        for li in link_infos:
            if ident not in (li["h_ids"] if tag == "h" else li["t_ids"]):
                continue
            if ay_id != li["ay_id"]:
                continue
            if row["kind"] == "roster" or row.get("subject") == li["subject"]:
                return True
        return False

    # 1) 四分类互斥完备：母集 = 两侧源库**全部非零业务表**（Q05）。
    #    每行去向 = mapped / shared(verify 按事实升级) / quarantined
    #    (PendingImportRow) / not_migrated(显式登记原因)，Σ = 源行全集。
    totals = {}
    for tag, conn, cls_file in (
        ("h", ctx.h(), "classification_h.json"),
        ("t", ctx.t(), "classification_t.json"),
    ):
        try:
            cls = json.load(open(os.path.join(ctx.reports_dir, cls_file), encoding="utf-8"))
            by_pk = {(r["table"], str(r["pk"])): r for r in cls["rows"]}
            fp = fp_h if tag == "h" else fp_t
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            source_rows: set = set()
            table_counts_src: dict[str, int] = {}
            for table in sorted(tables):
                pkcol = RECON_PK_COL.get(table, "id")
                rows = conn.execute(f"SELECT {pkcol} FROM {table}").fetchall()
                if not rows:
                    continue  # 零行表不进入母集
                table_counts_src[table] = len(rows)
                source_rows |= {(table, str(r[0])) for r in rows}
            cats = {"mapped": 0, "shared": 0, "quarantined": 0, "not_migrated": 0}
            per_table = {t: {"mapped": 0, "shared": 0, "quarantined": 0, "not_migrated": 0}
                         for t in table_counts_src}
            for key, row in by_pk.items():
                if key not in source_rows:
                    failures.append(f"{tag}:{key[0]}:{key[1]} 分类行不在源母集")
                    continue
                cat = row.get("category")
                if cat == "quarantined":
                    # 隔离必须有持久层登记（原行可查阅），绝不是报告标签
                    if get_pending(db, wm, fp, key[0], key[1]) is None:
                        failures.append(f"{tag}:{key[0]}:{key[1]} 隔离行缺 PendingImportRow")
                elif cat == "not_migrated":
                    if not row.get("reason"):
                        failures.append(f"{tag}:{key[0]}:{key[1]} not_migrated 缺原因")
                elif cat == "mapped":
                    if get_map(db, wm, fp, key[0], key[1]) is None:
                        failures.append(f"{tag}:{key[0]}:{key[1]} 分类 mapped 但缺 SourceMap")
                    cat = "shared" if fact_is_shared(row, tag) else "mapped"
                else:
                    failures.append(f"{tag}:{key[0]}:{key[1]} 非法分类 {cat}")
                    continue
                cats[cat] += 1
                per_table[key[0]][cat] += 1
            # 母集行未出现在逐行分类：只允许表级显式不迁移登记，否则=遗漏
            for key in sorted(source_rows - set(by_pk)):
                reason = NOT_MIGRATED_REASONS.get((tag, key[0]))
                if reason is None:
                    failures.append(f"{tag}:{key[0]}:{key[1]} 源行无分类去向（遗漏）")
                    continue
                cats["not_migrated"] += 1
                per_table[key[0]]["not_migrated"] += 1
            # PendingImportRow ↔ 隔离分类 1:1（多登/漏登都算失败）
            pendings = db.query(wm.PendingImportRow).filter_by(source_fingerprint=fp).all()
            for p in pendings:
                row = by_pk.get((p.source_table, p.source_pk))
                if row is None or row.get("category") != "quarantined":
                    failures.append(
                        f"{tag}:{p.source_table}:{p.source_pk} PendingImportRow 无隔离分类")
            if cats["quarantined"] != len(pendings):
                failures.append(
                    f"{tag} quarantined 计数 {cats['quarantined']} != PendingImportRow {len(pendings)}")
            cats["total"] = sum(cats[c] for c in
                                ("mapped", "shared", "quarantined", "not_migrated"))
            if cats["total"] != len(source_rows):
                failures.append(
                    f"{tag} 四类合计 {cats['total']} != 源非零行 {len(source_rows)}（母集不全）")
            totals[tag] = {"counts": cats, "per_table": per_table,
                           "source_rows": table_counts_src,
                           "not_migrated_reasons": {
                               t: NOT_MIGRATED_REASONS[(tag, t)]
                               for t in table_counts_src if (tag, t) in NOT_MIGRATED_REASONS
                           }}
        finally:
            conn.close()
    grand = {k: sum(totals[t]["counts"].get(k, 0) for t in totals) for k in
             ("mapped", "shared", "quarantined", "not_migrated", "total")}
    report["checks"]["classification"] = {"per_source": totals, "total": grand}

    # 2) 目标库完整性与外键
    engine = db.get_bind()
    from sqlalchemy import text as sqltext
    with engine.connect() as c:
        integrity = c.execute(sqltext("PRAGMA integrity_check")).scalar()
        fk = c.execute(sqltext("PRAGMA foreign_key_check")).fetchall()
    if integrity != "ok":
        failures.append(f"integrity_check={integrity}")
    if fk:
        failures.append(f"foreign_key_check 违例 {len(fk)} 条")
    report["checks"]["integrity"] = {"integrity_check": integrity, "fk_violations": len(fk)}

    # 3) H 全科/总分逐字段全比对（抽样=全量：合成数据量小，逐字段更严）
    field_diffs = []
    h_conn = ctx.h()
    try:
        for table, value_col, extra in (
            ("subject_score", "raw_score", "subject"),
            ("total_score", "total_score", "total_type"),
        ):
            for row in h_conn.execute(f"SELECT * FROM {table}"):
                m = get_map(db, wm, fp_h, table, row["id"])
                if m is None:
                    continue
                fact = db.get(wm.ScoreFact, m.target_id)
                key_col_val = row[extra]
                ok = fact is not None
                if ok:
                    if extra == "subject":
                        ok = fact.subject == key_col_val and fact.total_type is None
                    else:
                        ok = fact.total_type == key_col_val and fact.subject is None
                    ok = ok and fact.score == row[value_col]  # 缺考 NULL 对 NULL
                    if extra == "subject" and row["grade_score"] is not None:
                        # 等级分逐字段保留（仅 subject_score 表有该列）
                        ok = ok and fact.grade_score == row["grade_score"]
                    exam = h_conn.execute("SELECT * FROM exam WHERE id=?", (row["exam_id"],)).fetchone()
                    ok = ok and fact.exam_name == exam["name"]
                if not ok:
                    field_diffs.append({"table": table, "pk": str(row["id"])})
        report["checks"]["h_field_compare"] = {"compared_rows": sum(
            1 for t in ("subject_score", "total_score")
            for r in h_conn.execute(f"SELECT id FROM {t}")
            if get_map(db, wm, fp_h, t, r[0]) is not None), "field_diffs": field_diffs}
    finally:
        h_conn.close()
    if field_diffs:
        failures.append(f"H 逐字段比对不一致 {len(field_diffs)} 行")

    # 4) T 其他教学班只在 teaching 域
    t_rows = db.query(wm.SourceMap).filter(
        wm.SourceMap.source_fingerprint == fp_t,
        wm.SourceMap.source_table.in_(["teaching_class", "teaching_class_member"]),
    ).count()
    t_score_facts = (
        db.query(wm.ScoreFact)
        .join(wm.SourceMap, (wm.SourceMap.target_table == "score_fact")
              & (wm.SourceMap.target_id == wm.ScoreFact.id))
        .filter(wm.SourceMap.source_fingerprint == fp_t,
                wm.SourceMap.source_table == "subject_score")
        .all()
    )
    domain_bad = [f.id for f in t_score_facts if f.data_domain != "teaching"]
    if domain_bad:
        failures.append(f"T 来源成绩出现在非 teaching 域：{domain_bad}")
    report["checks"]["teaching_domain"] = {"t_structural_maps": t_rows,
                                           "t_score_facts": len(t_score_facts),
                                           "wrong_domain": domain_bad}

    # 5) 名册/缺交条数对账（母集=源行数；去向=mapped+quarantined 全量解释）
    def source_count(conn, table):
        pkcol = RECON_PK_COL.get(table, "id")
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def pending_count(fp, table):
        return db.query(wm.PendingImportRow).filter_by(
            source_fingerprint=fp, source_table=table).count()

    h_conn_recon = ctx.h()
    t_conn_recon = ctx.t()
    try:
        roster_mapped = db.query(wm.SourceMap).filter_by(
            source_fingerprint=fp_h, source_table="class_roster").count()
        enrollments = db.query(wm.SourceMap).filter_by(
            source_fingerprint=fp_h, source_table="class_roster").all()
        enroll_count = sum(
            1 for m in enrollments if db.get(wm.Enrollment, m.target_id) is not None
        )
        h_hw = db.query(wm.SourceMap).filter_by(
            source_fingerprint=fp_h, source_table="homework_record").all()
        h_subs = [db.get(wm.HomeworkSubmission, m.target_id) for m in h_hw]
        h_missing = sum(1 for s in h_subs if s and s.submission_status == "missing")
        t_hw = db.query(wm.SourceMap).filter_by(
            source_fingerprint=fp_t, source_table="homework_record").all()
        t_subs = [db.get(wm.HomeworkSubmission, m.target_id) for m in t_hw]
        recon = {
            "h_roster": {
                "source": source_count(h_conn_recon, "class_roster"),
                "enrollment": enroll_count,
                "quarantined": pending_count(fp_h, "class_roster"),
            },
            "h_homework": {
                "source_rows": source_count(h_conn_recon, "homework_record"),
                "submissions": len(h_subs),
                "quarantined": pending_count(fp_h, "homework_record"),
                "missing": h_missing,
                "excused": sum(1 for s in h_subs if s and s.submission_status == "excused"),
            },
            "t_homework": {
                "source_rows": source_count(t_conn_recon, "homework_record"),
                "submissions": len(t_subs),
                "quarantined": pending_count(fp_t, "homework_record"),
                "status_counts": {
                    st: sum(1 for s in t_subs if s and s.submission_status == st)
                    for st in ("submitted", "missing", "excused", "unknown")},
            },
            "notes": [
                "缺交条数口径：源缺交行数=mapped+quarantined；H remark 非空行映射为 excused（请假），"
                "missing 数=源无备注缺交已映射行数；T 的 submission_status 原值映射保留；不把 excused 计入缺交统计",
                "roster 有效期为学年粒度推导（学年起止），非源数据精确日",
            ],
        }
        if (recon["h_roster"]["source"]
                != recon["h_roster"]["enrollment"] + recon["h_roster"]["quarantined"]):
            failures.append("名册对账不平：源行数 != Enrollment + 待核实")
        for tag_key in ("h_homework", "t_homework"):
            r = recon[tag_key]
            if r["source_rows"] != r["submissions"] + r["quarantined"]:
                failures.append(f"{tag_key} 缺交条数对账不平（源 != 提交 + 待核实）")
        report["checks"]["reconciliation"] = recon

        # 5b) Q08 语义转换校验：teaching 域 assignment.subject=教师任教学科、
        #     homework_type ∈ 源旧 subject（作业种类）原值集合；逐字段而非只数条数。
        teacher = t_conn_recon.execute("SELECT subject FROM teacher LIMIT 1").fetchone()
        t_subject = teacher["subject"] if teacher and teacher["subject"] else None
        hw_types_src = {
            r["subject"] for r in t_conn_recon.execute("SELECT subject FROM homework_record")
        }
        hw_semantics = {"teaching_subject": t_subject, "source_homework_types": sorted(hw_types_src),
                        "assignments": 0, "bad": []}
        if t_subject is not None:
            seen_assignments = set()
            for m in db.query(wm.SourceMap).filter_by(
                    source_fingerprint=fp_t, source_table="homework_record").all():
                sub = db.get(wm.HomeworkSubmission, m.target_id)
                if sub is None or sub.assignment_id in seen_assignments:
                    continue
                seen_assignments.add(sub.assignment_id)
                a = db.get(wm.HomeworkAssignment, sub.assignment_id)
                hw_semantics["assignments"] += 1
                if a.subject != t_subject or a.homework_type not in hw_types_src:
                    hw_semantics["bad"].append({
                        "assignment_id": a.id, "subject": a.subject,
                        "homework_type": a.homework_type})
            if hw_semantics["bad"]:
                failures.append(f"T 作业语义转换错误 {len(hw_semantics['bad'])} 批（subject/种类）")
        report["checks"]["homework_semantics"] = hw_semantics
    finally:
        h_conn_recon.close()
        t_conn_recon.close()

    # 6) lost_columns / undo 摘要并入验证报告
    report["checks"]["lost_columns"] = json.load(
        open(os.path.join(ctx.reports_dir, "lost_columns.json"), encoding="utf-8"))
    report["checks"]["undo_audit"] = json.load(
        open(os.path.join(ctx.reports_dir, "undo_audit.json"), encoding="utf-8"))["summary"]
    report["failures"] = failures
    report["ok"] = not failures
    ctx.write_report("verification.json", report)
    if failures:
        raise RuntimeError("verify 未通过：" + "; ".join(failures))
    pending_total = db.query(wm.PendingImportRow).count()
    return {
        "ok": True,
        "classification_total": grand,
        "pending_import_rows": pending_total,
        "integrity": integrity,
        "fk_violations": 0,
        "h_field_diffs": 0,
        "conflicts": len(json.load(open(os.path.join(ctx.reports_dir, "conflicts.json"),
                                        encoding="utf-8"))["conflicts"]),
    }


# ─────────────────────────────────────────────────────────────
# 阶段执行器：每阶段单事务；失败回滚本阶段并在新事务落失败痕迹
# ─────────────────────────────────────────────────────────────

def run_stage(ctx: Rehearsal, name: str, fn) -> dict:
    db = ctx.SessionLocal()
    try:
        run = ctx.get_or_create_run(db)
        result = fn(ctx, db)
        stats = ctx.load_stats(run)
        stats.setdefault("stages", {})[name] = result
        done = stats.setdefault("completed_stages", [])
        if name not in done:
            done.append(name)
        run.stats_json = json.dumps(stats, ensure_ascii=False)  # 台账写回（缺此行计数丢失）
        run.status = "running"
        db.commit()
        print(f"[stage:{name}] {json.dumps(result, ensure_ascii=False)}")
        return result
    except Exception as exc:
        db.rollback()
        try:
            db2 = ctx.SessionLocal()
            run2 = ctx.get_or_create_run(db2)
            stats = ctx.load_stats(run2)
            stats.setdefault("stages", {})[name] = {"error": str(exc)}
            run2.stats_json = json.dumps(stats, ensure_ascii=False)
            run2.status = "failed"
            db2.commit()
            db2.close()
        except Exception:
            pass
        raise
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="P7 合成双库迁移演练管线")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="演练根目录")
    parser.add_argument("--from", dest="from_stage", default=None, help="起始阶段（缺省=第一个未完成阶段）")
    parser.add_argument("--to", dest="to_stage", default="verify", help="结束阶段")
    parser.add_argument("--declare", default=os.path.join(os.path.dirname(__file__), "declared_links.json"),
                        help="合成声明表（link/linked_student 只认此表）")
    args = parser.parse_args()

    # 红线校验必须先于任何 app 导入（engine 绑定 EXAM_TRACKER_DIR）
    resolve_runtime(args.root)
    ctx = Rehearsal(args.root, args.declare)

    if args.from_stage:
        start = STAGES.index(args.from_stage)
    else:
        db = ctx.SessionLocal()
        try:
            done = ctx.completed(db)
        finally:
            db.close()
        start = 0
        for i, name in enumerate(STAGES):
            if name not in done:
                start = i
                break
        else:
            start = len(STAGES) - 1  # 全部完成：重跑 verify 收尾（幂等）
    end = STAGES.index(args.to_stage)

    impls = {
        "snapshot": stage_snapshot,
        "import_homeroom": stage_import_homeroom,
        "import_teaching": stage_import_teaching,
        "link_suggest": stage_link_suggest,
        "conflict_report": stage_conflict_report,
        "undo_audit": stage_undo_audit,
        "verify": stage_verify,
    }
    for name in STAGES[start:end + 1]:
        run_stage(ctx, name, impls[name])

    # 收尾：标记本次 run 状态（completed 只代表已执行阶段全绿）
    db = ctx.SessionLocal()
    try:
        run = ctx.get_or_create_run(db)
        done = ctx.completed(db)
        run.status = "completed" if all(s in done for s in STAGES) else "partial"
        run.finished_at = None if run.status == "partial" else __import__("datetime").datetime.utcnow()
        db.commit()
        print(json.dumps({"run_token": ctx.run_token, "status": run.status}, ensure_ascii=False))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
