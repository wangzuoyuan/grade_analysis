"""P7 合成双库迁移演练：源库构造器（H=班主任版 legacy / T=教学版）。

范围声明：本脚本只构造**合成演练源库**（全部样本虚构，放 .test-data/ 忽略
目录），不是真实生产双库的任何导出。红线自检：
- 不 import 本仓 app.*（零 engine 副作用），建表/造数全部走 sqlite3 原生 DDL；
- 输出目录默认 <repo>/.test-data/migration-rehearsal/sources/，绝不触碰
  ~/.exam-tracker 与 .sources/。

H 源表结构对照 backend/app/db/models.py（legacy 模型）；
T 源表结构对照 .sources/teaching/backend/app/db/models.py（f9ab60f，只读
参照，绝不 import——其模块底部有 create_all 副作用）。

输出：<out>/h.sqlite、<out>/t.sqlite、<out>/../sources_manifest.json
（两库 sha256 + 逐表行数）。重跑先清空输出目录。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys

# ── 仓库根定位：scripts/migration/ 的上两级，供默认输出目录解析 ──
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_OUT = os.path.join(REPO_ROOT, ".test-data", "migration-rehearsal", "sources")
# 路径保护统一（ 交付边界裁决）：--out 必须位于演练根之内——本脚本会
# rmtree 清空输出目录，越界等于对任意目录执行删除，必须 exit 2 拒绝。
REHEARSAL_ROOT = os.path.realpath(
    os.path.join(REPO_ROOT, ".test-data", "migration-rehearsal")
)

# ─────────────────────────────────────────────────────────────
# H 源 DDL：逐列对照本仓 backend/app/db/models.py 的 legacy 模型。
# 顺序与约束（UNIQUE/INDEX/FK）照抄，保证演练读写与真实 legacy 库同构。
# ─────────────────────────────────────────────────────────────
H_DDL = [
    """CREATE TABLE teacher (
        id INTEGER PRIMARY KEY, name TEXT, school TEXT,
        target_class_high1 INTEGER, target_class_high2 INTEGER, target_class_high3 INTEGER,
        created_at DATETIME)""",
    """CREATE TABLE exam (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, grade INTEGER NOT NULL,
        semester TEXT NOT NULL, exam_date TEXT, exam_type TEXT NOT NULL,
        source_files JSON DEFAULT '[]', created_at DATETIME)""",
    """CREATE TABLE upload (
        id INTEGER PRIMARY KEY, exam_id INTEGER REFERENCES exam(id),
        file_path TEXT NOT NULL, file_hash TEXT, kind TEXT NOT NULL, mime TEXT NOT NULL,
        parsed_ok INTEGER DEFAULT 0, parse_log JSON, uploaded_at DATETIME)""",
    """CREATE TABLE subject_score (
        id INTEGER PRIMARY KEY, exam_id INTEGER NOT NULL REFERENCES exam(id),
        student_id TEXT NOT NULL, class_num INTEGER, xueji INTEGER, name TEXT,
        subject TEXT NOT NULL, raw_score REAL, grade_score REAL, grade_percentile REAL)""",
    """CREATE INDEX idx_subject_exam_student ON subject_score (exam_id, student_id)""",
    """CREATE INDEX idx_subject_student_subject ON subject_score (student_id, subject)""",
    """CREATE TABLE total_score (
        id INTEGER PRIMARY KEY, exam_id INTEGER NOT NULL REFERENCES exam(id),
        student_id TEXT NOT NULL, total_type TEXT NOT NULL, total_score REAL,
        grade_percentile REAL, xueji_rank INTEGER, grade_rank INTEGER)""",
    """CREATE INDEX idx_total_exam_type ON total_score (exam_id, total_type)""",
    """CREATE INDEX idx_total_student_type ON total_score (student_id, total_type)""",
    """CREATE TABLE class_average (
        id INTEGER PRIMARY KEY, exam_id INTEGER NOT NULL REFERENCES exam(id),
        class_type TEXT, class_num INTEGER NOT NULL, teacher_name TEXT,
        subject_averages JSON DEFAULT '{}', total_averages JSON DEFAULT '{}')""",
    """CREATE TABLE analysis_config (
        id INTEGER PRIMARY KEY, high_score_max INTEGER NOT NULL,
        critical_min INTEGER NOT NULL, critical_max INTEGER NOT NULL,
        weak_min INTEGER NOT NULL, updated_at DATETIME)""",
    # 班主任版花名册：主键真实学号，带在班状态 status（转班/毕业不删行）
    """CREATE TABLE class_roster (
        student_id TEXT PRIMARY KEY, name TEXT NOT NULL, class_num INTEGER,
        grade INTEGER, seat_no INTEGER, gender TEXT,
        excluded INTEGER NOT NULL DEFAULT 0, status TEXT)""",
    """CREATE INDEX idx_roster_class ON class_roster (class_num)""",
    """CREATE INDEX idx_roster_name ON class_roster (name)""",
    """CREATE INDEX idx_roster_grade ON class_roster (grade)""",
    """CREATE TABLE homework_record (
        id INTEGER PRIMARY KEY, student_id TEXT NOT NULL REFERENCES class_roster(student_id),
        date TEXT NOT NULL, subject TEXT NOT NULL, content TEXT, remark TEXT)""",
    """CREATE INDEX idx_hw_student_date ON homework_record (student_id, date)""",
    """CREATE INDEX idx_hw_date_subject ON homework_record (date, subject)""",
    """CREATE TABLE homework_collection (
        id INTEGER PRIMARY KEY, date TEXT NOT NULL, subject TEXT NOT NULL,
        grade INTEGER NOT NULL, class_num INTEGER NOT NULL,
        UNIQUE (date, subject, grade, class_num))""",
    """CREATE TABLE special_record (
        id INTEGER PRIMARY KEY, student_id TEXT NOT NULL REFERENCES class_roster(student_id),
        date TEXT NOT NULL, type TEXT NOT NULL, note TEXT)""",
    """CREATE TABLE homework_setting (key TEXT PRIMARY KEY, value TEXT)""",
    """CREATE TABLE homework_semester (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, start_date TEXT NOT NULL,
        end_date TEXT NOT NULL, is_current INTEGER NOT NULL DEFAULT 0, created_at DATETIME,
        UNIQUE (name, start_date, end_date))""",
    """CREATE TABLE student_identity (
        id INTEGER PRIMARY KEY, display_name TEXT, gender TEXT, ext_key TEXT, note TEXT,
        created_at DATETIME)""",
    """CREATE TABLE student_alias (
        id INTEGER PRIMARY KEY, identity_id INTEGER NOT NULL REFERENCES student_identity(id),
        student_id TEXT NOT NULL, grade INTEGER, link_source TEXT NOT NULL DEFAULT 'name_confirmed',
        created_at DATETIME, UNIQUE (student_id))""",
    """CREATE TABLE rollover_confirm_batch (
        id TEXT PRIMARY KEY, grade INTEGER NOT NULL, class_num INTEGER NOT NULL,
        created_at DATETIME, undone INTEGER NOT NULL DEFAULT 0,
        payload JSON, created_aliases JSON, created_identities JSON)""",
    """CREATE TABLE roster_import_batch (
        id TEXT PRIMARY KEY, grade INTEGER NOT NULL, class_num INTEGER NOT NULL,
        created_at DATETIME, undone INTEGER NOT NULL DEFAULT 0,
        payload JSON, created_rows JSON, replaced_rows JSON, repaired_rows JSON,
        renamed JSON, summary JSON)""",
    """CREATE TABLE imported_history (
        id INTEGER PRIMARY KEY, identity_id INTEGER NOT NULL REFERENCES student_identity(id),
        grade INTEGER NOT NULL DEFAULT 1, exam_label TEXT, exam_seq INTEGER,
        kind TEXT NOT NULL, subject TEXT, total_type TEXT, raw_score REAL,
        grade_score REAL, grade_percentile REAL, xueji_rank INTEGER)""",
    """CREATE TABLE student_note (
        id INTEGER PRIMARY KEY, student_id TEXT NOT NULL, date TEXT NOT NULL,
        category TEXT NOT NULL, content TEXT NOT NULL, follow_up TEXT,
        follow_up_done INTEGER NOT NULL DEFAULT 0, created_at DATETIME)""",
    """CREATE TABLE student_change_log (
        id INTEGER PRIMARY KEY, op_type TEXT NOT NULL, identity_id INTEGER, student_id TEXT,
        before_summary JSON, after_summary JSON, detail JSON,
        grade INTEGER, class_num INTEGER, created_at DATETIME)""",
]

# ─────────────────────────────────────────────────────────────
# T 源 DDL：逐列对照 .sources/teaching/backend/app/db/models.py（只读参照）。
# 与 H 的差异点：teacher 多 current_teaching_class_id/subject；subject_score/
# class_average/class_roster 多 class_label；homework_record 多
# submission_status/evaluation/时间戳；class_roster 无 grade/status；
# 多 teaching_class/teaching_class_member 两表。
# ─────────────────────────────────────────────────────────────
T_DDL = [
    """CREATE TABLE teacher (
        id INTEGER PRIMARY KEY, name TEXT, school TEXT,
        target_class_high1 INTEGER, target_class_high2 INTEGER, target_class_high3 INTEGER,
        current_teaching_class_id INTEGER, subject TEXT, created_at DATETIME)""",
    """CREATE TABLE exam (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, grade INTEGER NOT NULL,
        semester TEXT NOT NULL, exam_date TEXT, exam_type TEXT NOT NULL,
        source_files JSON DEFAULT '[]', created_at DATETIME)""",
    """CREATE TABLE upload (
        id INTEGER PRIMARY KEY, exam_id INTEGER REFERENCES exam(id),
        file_path TEXT NOT NULL, file_hash TEXT, kind TEXT NOT NULL, mime TEXT NOT NULL,
        parsed_ok INTEGER DEFAULT 0, parse_log JSON, uploaded_at DATETIME)""",
    """CREATE TABLE subject_score (
        id INTEGER PRIMARY KEY, exam_id INTEGER NOT NULL REFERENCES exam(id),
        student_id TEXT NOT NULL, class_num INTEGER, class_label TEXT, xueji INTEGER,
        name TEXT, subject TEXT NOT NULL, raw_score REAL, grade_score REAL,
        grade_percentile REAL)""",
    """CREATE INDEX idx_subject_exam_student ON subject_score (exam_id, student_id)""",
    """CREATE INDEX idx_subject_student_subject ON subject_score (student_id, subject)""",
    """CREATE INDEX idx_subject_class_label ON subject_score (exam_id, class_label)""",
    """CREATE TABLE total_score (
        id INTEGER PRIMARY KEY, exam_id INTEGER NOT NULL REFERENCES exam(id),
        student_id TEXT NOT NULL, total_type TEXT NOT NULL, total_score REAL,
        grade_percentile REAL, xueji_rank INTEGER, grade_rank INTEGER)""",
    """CREATE TABLE class_average (
        id INTEGER PRIMARY KEY, exam_id INTEGER NOT NULL REFERENCES exam(id),
        class_type TEXT, class_num INTEGER NOT NULL, class_label TEXT,
        teacher_name TEXT, subject_averages JSON DEFAULT '{}', total_averages JSON DEFAULT '{}')""",
    """CREATE TABLE analysis_config (
        id INTEGER PRIMARY KEY, high_score_max INTEGER NOT NULL,
        critical_min INTEGER NOT NULL, critical_max INTEGER NOT NULL,
        weak_min INTEGER NOT NULL, updated_at DATETIME)""",
    # 教学版花名册：无 grade/status 列；多教学班标签 class_label
    """CREATE TABLE class_roster (
        student_id TEXT PRIMARY KEY, name TEXT NOT NULL, class_num INTEGER,
        class_label TEXT, seat_no INTEGER, gender TEXT,
        excluded INTEGER NOT NULL DEFAULT 0)""",
    """CREATE INDEX idx_roster_class ON class_roster (class_num)""",
    """CREATE INDEX idx_roster_name ON class_roster (name)""",
    """CREATE TABLE homework_record (
        id INTEGER PRIMARY KEY, student_id TEXT NOT NULL REFERENCES class_roster(student_id),
        date TEXT NOT NULL, subject TEXT NOT NULL, content TEXT, remark TEXT,
        submission_status TEXT NOT NULL DEFAULT '缺交', evaluation TEXT,
        created_at DATETIME, updated_at DATETIME)""",
    """CREATE INDEX idx_hw_student_date ON homework_record (student_id, date)""",
    """CREATE INDEX idx_hw_date_subject ON homework_record (date, subject)""",
    """CREATE TABLE special_record (
        id INTEGER PRIMARY KEY, student_id TEXT NOT NULL REFERENCES class_roster(student_id),
        date TEXT NOT NULL, type TEXT NOT NULL, note TEXT)""",
    """CREATE TABLE homework_setting (key TEXT PRIMARY KEY, value TEXT)""",
    """CREATE TABLE homework_semester (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, start_date TEXT NOT NULL,
        end_date TEXT NOT NULL, is_current INTEGER NOT NULL DEFAULT 0, created_at DATETIME,
        UNIQUE (name, start_date, end_date))""",
    """CREATE TABLE student_identity (
        id INTEGER PRIMARY KEY, display_name TEXT, ext_key TEXT, gender TEXT, note TEXT,
        created_at DATETIME)""",
    """CREATE TABLE student_alias (
        id INTEGER PRIMARY KEY, identity_id INTEGER NOT NULL REFERENCES student_identity(id),
        student_id TEXT NOT NULL, grade INTEGER, link_source TEXT NOT NULL DEFAULT 'name_confirmed',
        created_at DATETIME, UNIQUE (student_id))""",
    # 教学班：grade+label 唯一，label 对数字班与走班名一视同仁
    """CREATE TABLE teaching_class (
        id INTEGER PRIMARY KEY, grade INTEGER NOT NULL, label TEXT NOT NULL,
        subject TEXT, kind TEXT NOT NULL DEFAULT '教学', note TEXT,
        sort_order INTEGER NOT NULL DEFAULT 0, created_at DATETIME,
        UNIQUE (grade, label))""",
    """CREATE TABLE teaching_class_member (
        id INTEGER PRIMARY KEY, teaching_class_id INTEGER NOT NULL REFERENCES teaching_class(id),
        student_id TEXT NOT NULL, name TEXT, source TEXT NOT NULL DEFAULT 'manual',
        created_at DATETIME, UNIQUE (teaching_class_id, student_id))""",
]

# ─────────────────────────────────────────────────────────────
# 合成样本常量（沿用 tests/v1 的虚构命名风格：秦甲、秦乙…）。
# 学号编码 2024xxxx=2024 级；两个学年 = 2024-2025（高一）+ 2025-2026（高二）。
# ─────────────────────────────────────────────────────────────

# H 身份组：H identity_id -> (display_name, [(student_id, grade, link_source)])
H_IDENTITIES = {
    1: ("秦甲", [("20240601", 1, "name_confirmed"), ("20250601", 2, "name_confirmed")]),
    2: ("秦乙", [("20240602", 1, "name_confirmed"), ("20250602", 2, "name_confirmed"),
                 ("20250702", 2, "manual")]),  # 转班改号：同一人多学号
    3: ("秦丙", [("20240603", 1, "name_confirmed")]),          # 无成绩成员
    4: ("秦丁", [("20240604", 1, "name_confirmed")]),          # 同名不同人 A（6 班）
    5: ("秦丁", [("20240704", 1, "manual")]),                  # 同名不同人 B（7 班）
    6: ("秦壬", [("_anon:秦壬", 1, "manual")]),                # 仅姓名无学号成员（占位号）
    7: ("秦子", [("TMP20250999", 1, "manual")]),               # TMP 前缀临时学号
    8: ("秦戊", [("G1::20230101", 2, "crosswalk")]),           # G1:: 前缀（跨届命名空间）
    9: ("秦亥", [("g1-20230105", 2, "crosswalk")]),            # g1- 前缀（更旧命名空间）
    10: ("秦戌", [("20230101", 2, "name_confirmed")]),         # 复用后的裸学号（与 8 同源不同人）
    11: ("秦乾", [("20240609", 1, "name_confirmed")]),         # 撞号在册人（见坤）
}

# H 考试：(id, name, grade, semester, exam_date, exam_type)
H_EXAMS = [
    (1, "2024期中", 1, "上", "2024-11-14", "期中"),
    (2, "2024期末", 1, "下", "2025-01-16", "期末"),
    (3, "2025期中", 2, "上", "2025-11-06", "期中"),
    (4, "2025期末", 2, "下", "2026-01-15", "期末"),
]

# H 成绩：(id, exam_id, student_id, class_num, name, subject, raw_score, grade_score)
# - 甲高一物理 90（T 侧 91 → 冲突样本）；乙高一物理 84（T 侧 84 → 一致样本）
# - 秦坤与秦乾同学号 20240609 不同名（录入口径事故 → 撞号隔离样本）
# - 高二甲/乙带 grade_score（等级分逐字段保留验证）；
#   110 丁B 缺考 raw_score=NULL（已映射行的缺考 NULL 不转 0 样本；
#   TMP 子的缺考行走隔离路径，不进业务表）
H_SUBJECT_SCORES = [
    (101, 1, "20240601", 6, "秦甲", "语文", 92.0, None),
    (102, 1, "20240601", 6, "秦甲", "数学", 88.0, None),
    (103, 1, "20240601", 6, "秦甲", "英语", 90.0, None),
    (104, 1, "20240601", 6, "秦甲", "物理", 90.0, None),
    (105, 1, "20240602", 6, "秦乙", "语文", 76.0, None),
    (106, 1, "20240602", 6, "秦乙", "数学", 81.0, None),
    (107, 1, "20240602", 6, "秦乙", "英语", 79.0, None),
    (108, 1, "20240602", 6, "秦乙", "物理", 84.0, None),
    (109, 1, "20240604", 6, "秦丁", "物理", 70.0, None),
    (110, 1, "20240704", 7, "秦丁", "物理", None, None),
    (111, 1, "_anon:秦壬", 6, "秦壬", "物理", 55.0, None),
    (112, 1, "20240609", 6, "秦乾", "物理", 60.0, None),
    (113, 1, "20240609", 7, "秦坤", "物理", 62.0, None),
    (114, 2, "TMP20250999", 6, "秦子", "物理", None, None),
    (115, 3, "20250601", 6, "秦甲", "语文", 94.0, 96.0),
    (116, 3, "20250601", 6, "秦甲", "数学", 91.0, 93.0),
    (117, 3, "20250601", 6, "秦甲", "英语", 93.0, 95.0),
    (118, 3, "20250601", 6, "秦甲", "物理", 86.0, 88.0),
    (119, 3, "20250702", 7, "秦乙", "语文", 78.0, 80.0),
    (120, 3, "20250702", 7, "秦乙", "数学", 83.0, 85.0),
    (121, 3, "20250702", 7, "秦乙", "英语", 80.0, 82.0),
    (122, 3, "20250702", 7, "秦乙", "物理", 85.0, 87.0),
    (123, 3, "G1::20230101", 6, "秦戊", "物理", 80.0, 82.0),
    (124, 3, "20230101", 3, "秦戌", "物理", 88.0, 90.0),
]

# H 总分：(id, exam_id, student_id, total_type, total_score, xueji_rank, grade_rank)
H_TOTAL_SCORES = [
    (201, 1, "20240601", "主三门", 270.0, 3, 12),
    (202, 1, "20240602", "主三门", 236.0, 15, 60),
    (203, 3, "20250601", "主三门", 278.0, 2, 8),
]

# H 花名册：(student_id, name, class_num, grade, seat_no, status)
H_ROSTER = [
    ("20240601", "秦甲", 6, 1, 1, "active"),
    ("20240602", "秦乙", 6, 1, 2, "active"),
    ("20240603", "秦丙", 6, 1, 3, "active"),
    ("20240604", "秦丁", 6, 1, 4, "active"),
    ("20240704", "秦丁", 7, 1, 1, "active"),
    ("_anon:秦壬", "秦壬", 6, 1, 5, "active"),
    ("TMP20250999", "秦子", 6, 1, 6, "active"),
    ("20240609", "秦乾", 6, 1, 7, "active"),
    ("20250601", "秦甲", 6, 2, 1, "active"),
    ("20250602", "秦乙", 6, 2, 2, "transferred"),   # 转班离班行（不删）
    ("20250702", "秦乙", 7, 2, 2, "active"),        # 转入班行 → 花名册多行
    ("G1::20230101", "秦戊", 6, 2, 8, "active"),
    ("g1-20230105", "秦亥", 6, 2, 9, "active"),
    ("20230101", "秦戌", 3, 2, 1, "active"),
]

# H 作业缺交：(id, student_id, date, subject, content, remark)
# 甲同日 2024-10-20 两份数学（不盲目压成一次作业）；甲第二行带请假 remark。
H_HOMEWORK = [
    (301, "20240601", "2024-10-20", "数学", "必修一第三章习题", None),
    (302, "20240601", "2024-10-20", "数学", "错题重做", "运动会请假"),
    (303, "20240602", "2024-10-21", "物理", "卷二", None),
    (304, "_anon:秦壬", "2024-10-22", "物理", "练习册P30", None),
    (305, "20250601", "2025-11-10", "物理", "期中订正", None),
]

# H 撤销快照一：换届向导「写入名册」（高一 6 班，2024-09-01）
H_ROSTER_IMPORT_BATCH = (
    "rbatch-20240901", 1, 6,
    json.dumps([
        {"student_id": "20240601", "name": "秦甲"},
        {"student_id": "20240602", "name": "秦乙"},
        {"student_id": "20240603", "name": "秦丙"},
        {"student_id": "20240604", "name": "秦丁"},
    ]),
    json.dumps([
        {"student_id": "20240601", "name": "秦甲", "class_num": 6, "grade": 1},
        {"student_id": "20240602", "name": "秦乙", "class_num": 6, "grade": 1},
        {"student_id": "20240603", "name": "秦丙", "class_num": 6, "grade": 1},
        {"student_id": "20240604", "name": "秦丁", "class_num": 6, "grade": 1},
    ]),
    json.dumps([
        {"old": {"student_id": "20240101", "name": "占位"}, "new_student_id": "20240602",
         "moved_record_ids": [], "alias_actions": []},
    ]),
    json.dumps([]),
    json.dumps({}),   # renamed：本批无届命名空间迁移
    json.dumps({"created": 4, "updated": 0, "replaced": 1, "repaired": 0, "total": 5}),
)

# H 撤销快照二：高二 6 班导入（renamed 正是 G1:: 前缀的来历）
H_ROSTER_IMPORT_BATCH_B = (
    "rbatch-20250901", 2, 6,
    json.dumps([{"student_id": "20230101", "name": "秦戊"}]),
    json.dumps([{"student_id": "G1::20230101", "name": "秦戊", "class_num": 6, "grade": 2}]),
    json.dumps([]),
    json.dumps([]),
    json.dumps({"20230101": "G1::20230101"}),   # 届命名空间迁移：腾出裸学号
    json.dumps({"created": 1, "updated": 0, "replaced": 0, "repaired": 0, "total": 1}),
)

# H 撤销快照三：换届「同名批量确认」（高二 6 班）
# 第三项引用 20240699/20230699——源库无此学号（引用已漂移）→ undo 审计 read-only 样本。
H_ROLLOVER_BATCH = (
    "cbatch-20250901", 2, 6,
    json.dumps([
        {"g2_student_id": "20250601", "name": "秦甲", "decision": "confirm", "g1_student_id": "20240601"},
        {"g2_student_id": "20250602", "name": "秦乙", "decision": "confirm", "g1_student_id": "20240602"},
        {"g2_student_id": "20240699", "name": "秦隐", "decision": "confirm", "g1_student_id": "20230699"},
    ]),
    json.dumps([
        {"student_id": "20250601", "identity_id": 1},
        {"student_id": "20250602", "identity_id": 2},
    ]),
    json.dumps([]),
)

# H 手工历史成绩（identity 维度，与全年级排名完全隔离）：
# (id, identity_id, grade, exam_label, exam_seq, kind, subject, total_type, raw_score)
H_IMPORTED_HISTORY = [
    (401, 1, 1, "入学摸底", 1, "subject", "数学", None, 75.0),
    (402, 2, 1, "入学摸底", 1, "total", None, "主三门", 210.0),
]

# H 班主任私密档案（Q05 对账母集样本：非零 student_note 必须显式分类，
# 不允许再出现「表有源行、对账零记录」的静默丢失）。
H_STUDENT_NOTES = [
    (501, "20240601", "2024-10-15", "谈话", "物理学习状态谈话", "两周后回访", 0),
    (502, "20240602", "2024-11-01", "家长沟通", "电话沟通月考情况", None, 1),
]

# ─────────────────────────────────────────────────────────────
# T 源样本：两学年同名教学班「6」（高一行政 / 高二走班）；
# teacher.subject=物理；成绩含被旧链路丢弃的语文列与停写总分行。
# ─────────────────────────────────────────────────────────────

# T 身份组（T 库自己的身份链，与 H 完全独立，绝不因此归并）
T_IDENTITIES = {
    1: ("秦甲", [("20240601", 1), ("20250601", 2)]),
    2: ("秦乙", [("20240602", 1)]),
}

T_TEACHER = (1, "合成教学老师", "合成中学", None, None, None, 2, "物理")

# T 教学班：(id, grade, label, subject, kind, sort_order)——同名班「6」跨学年
T_TEACHING_CLASSES = [
    (1, 1, "6", "物理", "行政", 1),
    (2, 2, "6", "物理", "教学", 1),
]

# T 成员：(id, teaching_class_id, student_id, name, source)——标签+来源
T_MEMBERS = [
    (501, 1, "20240601", "秦甲", "parser"),
    (502, 1, "20240602", "秦乙", "parser"),
    (503, 1, "20240605", "秦春", "manual"),
    (504, 1, "_anon:夏", "夏", "manual"),          # 仅姓名占位成员
    (505, 2, "20250601", "秦甲", "roster"),
    (506, 2, "20250603", "秦庚", "manual"),
]

# T 花名册（作业模块外键依赖；教学版无 grade/status 列）
T_ROSTER = [
    ("20240601", "秦甲", 6, "6", 1),
    ("20240602", "秦乙", 6, "6", 2),
    ("20240605", "秦春", 6, "6", 3),
    ("_anon:夏", "夏", 6, "6", 4),
    ("20250601", "秦甲", 6, "6", 1),
    ("20250603", "秦庚", 6, "6", 5),
]

# T 考试：与 H 同场同名（同考试名+同日期+同类型）→ 冲突比对基础
T_EXAMS = [
    (1, "2024期中", 1, "上", "2024-11-14", "期中"),
    (2, "2025期中", 2, "上", "2025-11-06", "期中"),
]

# T 成绩：(id, exam_id, student_id, class_label, name, subject, raw_score, grade_score)
# 物理 5 行（甲高一 91 与 H 的 90 → 冲突；乙 84 一致；夏缺考 NULL）；
# 语文 1 行 + total 1 行 = 被旧链路丢弃/停写列（迁移不恢复，计 lost_columns）。
T_SUBJECT_SCORES = [
    (601, 1, "20240601", "6", "秦甲", "物理", 91.0, None),
    (602, 1, "20240602", "6", "秦乙", "物理", 84.0, None),
    (603, 1, "20240605", "6", "秦春", "物理", 72.0, None),
    (604, 1, "_anon:夏", "6", "夏", "物理", None, None),
    (605, 1, "20240601", "6", "秦甲", "语文", 88.0, None),   # 丢弃学科列
    (606, 2, "20250601", "6", "秦甲", "物理", 86.0, 88.0),
]

# T 总分（停写遗留行 → lost_columns）
T_TOTAL_SCORES = [
    (701, 1, "20240601", "主三门", 275.0, None, None),
]

# T 侧显式不迁移样本（Q05：非零配置/班均表必须进入对账第四类，不许静默）。
T_CLASS_AVERAGE = (1, 1, "平行", 6, "6", "合成教学老师", '{"物理": 78.0}', "{}")
T_ANALYSIS_CONFIG = (1, 80, 400, 500, 501, "2024-09-01 08:00:00")
T_HOMEWORK_SEMESTER = (1, "2024-2025学年上", "2024-09-01", "2025-01-31", 1,
                       "2024-09-01 08:00:00")

# T 作业：(id, student_id, date, subject, content, submission_status, evaluation)
# Q08 真实语义：旧 subject 列存的是**作业种类**（校本作业/周末作业/试卷订正…，
# 见 .sources/teaching/backend/app/homework/parser.py 头部注释），任教学科只
# 来自 Teacher 配置——绝不在样本里把 subject 造为学科名掩盖语义。
# 同日同种两行（甲缺交/乙已交）→ 一个批次两个提交状态；evaluation 保留验证。
T_HOMEWORK = [
    (801, "20240601", "2024-10-11", "校本作业", "限时练1", "缺交", None),
    (802, "20240602", "2024-10-11", "校本作业", "限时练1", "已交", "已订正"),
    (803, "20240602", "2024-10-18", "周末作业", "错题重做", "缺交", None),
    (804, "20240601", "2024-10-25", "试卷订正", "月考卷订正", "已交", None),
]


def _exec_many(conn: sqlite3.Connection, sql: str, rows) -> None:
    conn.executemany(sql, rows)


def build_h(path: str) -> dict:
    """构造 H 源库（班主任版 legacy 表结构 + 边界样本），返回逐表行数。"""
    conn = sqlite3.connect(path)
    try:
        for ddl in H_DDL:
            conn.execute(ddl)
        conn.execute(
            "INSERT INTO teacher VALUES (1,'合成班主任','合成中学',NULL,6,NULL,'2024-09-01 08:00:00')"
        )
        for row in H_EXAMS:
            conn.execute(
                "INSERT INTO exam VALUES (?,?,?,?,?,?,?,?)",
                (*row, json.dumps(["synthetic.xlsx"]), "2024-09-01 08:00:00"),
            )
        _exec_many(
            conn,
            "INSERT INTO subject_score VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(i, e, s, c, None, n, sub, raw, g, None) for (i, e, s, c, n, sub, raw, g) in H_SUBJECT_SCORES],
        )
        _exec_many(
            conn, "INSERT INTO total_score VALUES (?,?,?,?,?,?,?,?)",
            [(i, e, s, t, v, None, xr, gr) for (i, e, s, t, v, xr, gr) in H_TOTAL_SCORES],
        )
        conn.execute(
            "INSERT INTO class_average VALUES (1,1,'平行',6,'合成班主任','{\"物理\": 70.5}','{\"主三门\": 250.0}')"
        )
        conn.execute(
            "INSERT INTO analysis_config VALUES (1, 80, 400, 500, 501, '2024-09-01 08:00:00')"
        )
        _exec_many(
            conn, "INSERT INTO class_roster VALUES (?,?,?,?,?,?,?,?)",
            [(s, n, c, g, seat, None, 0, st) for (s, n, c, g, seat, st) in H_ROSTER],
        )
        _exec_many(
            conn, "INSERT INTO homework_record VALUES (?,?,?,?,?,?)",
            [(i, s, d, sub, c, r) for (i, s, d, sub, c, r) in H_HOMEWORK],
        )
        # 全交台账：建表不造行（目标模型无对应表，报告注明未纳入口径）
        conn.execute(
            "INSERT INTO homework_setting VALUES ('semester_start','2024-09-01')"
        )
        conn.execute(
            "INSERT INTO homework_semester VALUES (1,'2024-2025学年上','2024-09-01','2025-01-31',1,'2024-09-01 08:00:00')"
        )
        for ident_id, (name, aliases) in H_IDENTITIES.items():
            conn.execute(
                "INSERT INTO student_identity VALUES (?,?,?,?,?,?)",
                (ident_id, name, None, None, None, "2024-09-01 08:00:00"),
            )
            for sid, grade, source in aliases:
                conn.execute(
                    "INSERT INTO student_alias (identity_id, student_id, grade, link_source, created_at)"
                    " VALUES (?,?,?,?,?)",
                    (ident_id, sid, grade, source, "2024-09-01 08:00:00"),
                )
        conn.execute(
            "INSERT INTO roster_import_batch (id, grade, class_num, payload, created_rows,"
            " replaced_rows, repaired_rows, renamed, summary, created_at, undone)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (*H_ROSTER_IMPORT_BATCH, "2024-09-01 08:00:00", 0),
        )
        conn.execute(
            "INSERT INTO roster_import_batch (id, grade, class_num, payload, created_rows,"
            " replaced_rows, repaired_rows, renamed, summary, created_at, undone)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (*H_ROSTER_IMPORT_BATCH_B, "2025-09-01 08:00:00", 0),
        )
        conn.execute(
            "INSERT INTO rollover_confirm_batch (id, grade, class_num, payload,"
            " created_aliases, created_identities, created_at, undone)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (*H_ROLLOVER_BATCH, "2025-09-01 08:00:00", 0),
        )
        _exec_many(
            conn, "INSERT INTO imported_history VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(i, ident, g, lbl, seq, kind, sub, tt, raw, None, None, None)
             for (i, ident, g, lbl, seq, kind, sub, tt, raw) in H_IMPORTED_HISTORY],
        )
        _exec_many(
            conn, "INSERT INTO student_note VALUES (?,?,?,?,?,?,?,?)",
            [(i, s, dt, cat, c, fu, done, "2024-10-15 08:00:00")
             for (i, s, dt, cat, c, fu, done) in H_STUDENT_NOTES],
        )
        conn.commit()
        return _table_row_counts(conn)
    finally:
        conn.close()


def build_t(path: str) -> dict:
    """构造 T 源库（教学版表结构），返回逐表行数。"""
    conn = sqlite3.connect(path)
    try:
        for ddl in T_DDL:
            conn.execute(ddl)
        conn.execute(
            "INSERT INTO teacher VALUES (?,?,?,?,?,?,?,?,?)",
            (*T_TEACHER, "2024-09-01 08:00:00"),
        )
        for row in T_EXAMS:
            conn.execute(
                "INSERT INTO exam VALUES (?,?,?,?,?,?,?,?)",
                (*row, json.dumps(["synthetic-t.xlsx"]), "2024-09-01 08:00:00"),
            )
        _exec_many(
            conn,
            "INSERT INTO subject_score VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(i, e, s, None, lbl, None, n, sub, raw, g, None)
             for (i, e, s, lbl, n, sub, raw, g) in T_SUBJECT_SCORES],
        )
        _exec_many(
            conn, "INSERT INTO total_score VALUES (?,?,?,?,?,?,?,?)",
            [(i, e, s, t, v, None, xr, gr) for (i, e, s, t, v, xr, gr) in T_TOTAL_SCORES],
        )
        # Q05：T 侧非零班均/阈值/学期配置（对账第四类「显式不迁移」样本）
        conn.execute("INSERT INTO class_average VALUES (?,?,?,?,?,?,?,?)", T_CLASS_AVERAGE)
        conn.execute("INSERT INTO analysis_config VALUES (?,?,?,?,?,?)", T_ANALYSIS_CONFIG)
        conn.execute(
            "INSERT INTO homework_semester VALUES (?,?,?,?,?,?)", T_HOMEWORK_SEMESTER,
        )
        for ident_id, (name, aliases) in T_IDENTITIES.items():
            conn.execute(
                "INSERT INTO student_identity VALUES (?,?,?,?,?,?)",
                (ident_id, name, None, None, None, "2024-09-01 08:00:00"),
            )
            for sid, grade in aliases:
                conn.execute(
                    "INSERT INTO student_alias (identity_id, student_id, grade, link_source, created_at)"
                    " VALUES (?,?,?,?,?)",
                    (ident_id, sid, grade, "name_confirmed", "2024-09-01 08:00:00"),
                )
        _exec_many(
            conn, "INSERT INTO class_roster VALUES (?,?,?,?,?,?,?)",
            [(s, n, c, lbl, seat, None, 0) for (s, n, c, lbl, seat) in T_ROSTER],
        )
        _exec_many(
            conn, "INSERT INTO homework_record VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(i, s, d, sub, c, None, st, ev, "2024-10-11 08:00:00", "2024-10-11 08:00:00")
             for (i, s, d, sub, c, st, ev) in T_HOMEWORK],
        )
        _exec_many(
            conn, "INSERT INTO teaching_class VALUES (?,?,?,?,?,?,?,?)",
            [(i, g, lbl, sub, kind, None, so, "2024-09-01 08:00:00")
             for (i, g, lbl, sub, kind, so) in T_TEACHING_CLASSES],
        )
        _exec_many(
            conn,
            "INSERT INTO teaching_class_member (id, teaching_class_id, student_id, name, source, created_at)"
            " VALUES (?,?,?,?,?,?)",
            [(i, tc, s, n, src, "2024-09-01 08:00:00")
             for (i, tc, s, n, src) in T_MEMBERS],
        )
        conn.commit()
        return _table_row_counts(conn)
    finally:
        conn.close()


def _table_row_counts(conn: sqlite3.Connection) -> dict:
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in sorted(tables)}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="P7 合成双库演练：构造 H/T 源库")
    parser.add_argument("--out", default=DEFAULT_OUT, help="输出目录（必须在演练根 .test-data/migration-rehearsal 内）")
    args = parser.parse_args()

    out = os.path.realpath(os.path.abspath(args.out))
    # rmtree 前断言：越界（演练根之外，含 ~/.exam-tracker、.sources/）直接拒绝
    if out != REHEARSAL_ROOT and not out.startswith(REHEARSAL_ROOT + os.sep):
        print(
            f"[红线] --out={out} 不在演练根 {REHEARSAL_ROOT} 之内，拒绝执行（本脚本会清空输出目录）",
            file=sys.stderr,
        )
        return 2
    if os.path.exists(out):
        shutil.rmtree(out)  # 幂等：重跑先清空输出目录
    os.makedirs(out, exist_ok=True)

    h_path = os.path.join(out, "h.sqlite")
    t_path = os.path.join(out, "t.sqlite")
    h_rows = build_h(h_path)
    t_rows = build_t(t_path)

    manifest = {
        "scope": "synthetic-rehearsal",  # 如实声明：合成演练源，非真实生产库
        "built_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "sources": {
            "h": {"path": os.path.relpath(h_path, os.path.dirname(out)),
                  "sha256": sha256_file(h_path), "rows": h_rows},
            "t": {"path": os.path.relpath(t_path, os.path.dirname(out)),
                  "sha256": sha256_file(t_path), "rows": t_rows},
        },
    }
    manifest_path = os.path.join(os.path.dirname(out), "sources_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(json.dumps({"h": h_path, "t": t_path, "manifest": manifest_path}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
