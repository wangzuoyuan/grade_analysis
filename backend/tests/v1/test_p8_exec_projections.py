"""P8 exec 业务投影测试：学期并集、H/T 作业批次、special_record 档案、审计与幂等。

所有样本虚构（2031+ 学年），直接调用 run_real_migration.project_workspace_facts，
目标库为 tests/conftest.py 的隔离 EXAM_TRACKER_DIR。语义基准：
run_rehearsal.py stage_import_homeroom/stage_import_teaching 作业段 + 用户
2026-09-12 裁决（学期并集；special_record 不建幽灵身份）。H 侧表结构与真实
快照一致（CLI 端到端用例会走 legacy 镜像，列不齐会被拒）。
"""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from app.db import workspace_models  # noqa: F401  顶层导入保证 ws_* 表建出

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SCRIPT = os.path.join(REPO, "scripts", "migration", "run_real_migration.py")


@pytest.fixture(autouse=True)
def fresh_schema():
    """每个用例从空 schema 开始：学期/批次有唯一键，禁止跨用例残留。"""
    from app.db.models import Base, engine

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def _load_module():
    spec = importlib.util.spec_from_file_location("p8_exec_real", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _target_db():
    from app.db.models import engine

    return engine.url.database


H_SHAPE = """
CREATE TABLE exam (id INTEGER NOT NULL, name VARCHAR NOT NULL, grade INTEGER NOT NULL, semester VARCHAR NOT NULL, exam_date VARCHAR, exam_type VARCHAR NOT NULL, source_files JSON, created_at DATETIME, PRIMARY KEY (id));
CREATE TABLE student_identity (id INTEGER NOT NULL, display_name VARCHAR, gender VARCHAR, ext_key VARCHAR, note VARCHAR, created_at DATETIME, PRIMARY KEY (id));
CREATE TABLE student_alias (id INTEGER NOT NULL, identity_id INTEGER NOT NULL, student_id VARCHAR NOT NULL, grade INTEGER, link_source VARCHAR NOT NULL, created_at DATETIME, PRIMARY KEY (id), CONSTRAINT uq_alias_student UNIQUE (student_id), FOREIGN KEY(identity_id) REFERENCES student_identity (id));
CREATE TABLE class_roster (student_id VARCHAR NOT NULL, name VARCHAR, class_num INTEGER, seat_no INTEGER, gender VARCHAR, excluded INTEGER, grade INTEGER, status VARCHAR(20), PRIMARY KEY (student_id));
CREATE TABLE subject_score (id INTEGER NOT NULL, exam_id INTEGER NOT NULL, student_id VARCHAR NOT NULL, class_num INTEGER, xueji INTEGER, name VARCHAR, subject VARCHAR NOT NULL, raw_score FLOAT, grade_score FLOAT, grade_percentile FLOAT, PRIMARY KEY (id), FOREIGN KEY(exam_id) REFERENCES exam (id));
CREATE TABLE total_score (id INTEGER NOT NULL, exam_id INTEGER NOT NULL, student_id VARCHAR NOT NULL, total_type VARCHAR NOT NULL, total_score FLOAT, grade_percentile FLOAT, xueji_rank INTEGER, grade_rank INTEGER, PRIMARY KEY (id), FOREIGN KEY(exam_id) REFERENCES exam (id));
CREATE TABLE homework_record (id INTEGER NOT NULL, student_id VARCHAR NOT NULL, date VARCHAR NOT NULL, subject VARCHAR NOT NULL, content VARCHAR, remark VARCHAR, PRIMARY KEY (id), FOREIGN KEY(student_id) REFERENCES class_roster (student_id));
CREATE TABLE special_record (id INTEGER NOT NULL, student_id VARCHAR NOT NULL, date VARCHAR NOT NULL, type VARCHAR NOT NULL, note VARCHAR, PRIMARY KEY (id), FOREIGN KEY(student_id) REFERENCES class_roster (student_id));
CREATE TABLE homework_semester (id INTEGER NOT NULL, name VARCHAR NOT NULL, start_date VARCHAR NOT NULL, end_date VARCHAR NOT NULL, is_current INTEGER NOT NULL, created_at DATETIME, PRIMARY KEY (id));
CREATE TABLE homework_setting (key VARCHAR NOT NULL, value VARCHAR, PRIMARY KEY (key));
CREATE TABLE homework_collection (id INTEGER NOT NULL, date VARCHAR NOT NULL, subject VARCHAR NOT NULL, grade INTEGER NOT NULL, class_num INTEGER NOT NULL, PRIMARY KEY (id));
CREATE TABLE teacher (id INTEGER NOT NULL, name VARCHAR, school VARCHAR, target_class_high1 INTEGER, target_class_high2 INTEGER, target_class_high3 INTEGER, created_at DATETIME, PRIMARY KEY (id));
"""

T_SHAPE = """
CREATE TABLE exam (id INTEGER PRIMARY KEY, name TEXT, grade INTEGER, exam_date TEXT);
CREATE TABLE student_identity (id INTEGER PRIMARY KEY, display_name TEXT);
CREATE TABLE student_alias (id INTEGER PRIMARY KEY, identity_id INTEGER, student_id TEXT);
CREATE TABLE class_roster (student_id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE subject_score (id INTEGER PRIMARY KEY, exam_id INTEGER, student_id TEXT, class_num INTEGER, subject TEXT, raw_score REAL, grade_score REAL);
CREATE TABLE total_score (id INTEGER PRIMARY KEY, exam_id INTEGER, student_id TEXT, total_type TEXT, total_score REAL);
CREATE TABLE homework_record (id INTEGER PRIMARY KEY, student_id TEXT, date TEXT, subject TEXT, content TEXT, remark TEXT, submission_status TEXT NOT NULL, evaluation TEXT);
CREATE TABLE special_record (id INTEGER PRIMARY KEY, student_id TEXT, date TEXT, type TEXT, note TEXT);
CREATE TABLE homework_semester (id INTEGER PRIMARY KEY, name TEXT, start_date TEXT, end_date TEXT, is_current INTEGER);
CREATE TABLE homework_setting (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE teaching_class (id INTEGER PRIMARY KEY, grade INTEGER, label TEXT, subject TEXT);
CREATE TABLE teaching_class_member (id INTEGER PRIMARY KEY, teaching_class_id INTEGER REFERENCES teaching_class(id), student_id TEXT, source TEXT, name TEXT);
CREATE TABLE teacher (id INTEGER PRIMARY KEY, subject TEXT);
"""


def _seed_homeroom(path):
    db = sqlite3.connect(path)
    db.executescript(H_SHAPE)
    # 锚定学年 2032-2033（current 学期 2032-09-01 起），active_grade=2。
    db.execute("INSERT INTO homework_semester VALUES (1,'2032-2033学年第一学期','2032-09-01','2033-01-20',1,NULL)")
    db.execute("INSERT INTO homework_setting VALUES ('active_grade','2')")
    db.execute("INSERT INTO student_identity VALUES (1,'甲',NULL,NULL,NULL,NULL)")
    db.execute("INSERT INTO student_alias VALUES (1,1,'s1',2,'fixture',NULL)")
    # s2/s3 是上学年（2031-2032，grade1）学籍行；ghost-h 无任何学籍/别名/成绩事实。
    db.execute("INSERT INTO class_roster VALUES ('s1','甲',3,1,'M',0,2,'active')")
    db.execute("INSERT INTO class_roster VALUES ('s2','乙',3,2,'F',0,1,'active')")
    db.execute("INSERT INTO class_roster VALUES ('s3','丙',3,3,'M',0,1,'active')")
    # 1e 总分回填三分支：s2 有同考科目行（分支一）；s3 只有考试学年学籍行
    # （分支二）；s1 学籍在 2032-2033、与考试学年不符（分支三 → NULL）。
    db.execute("INSERT INTO exam VALUES (1,'旧考试',1,'下学期','2032-04-15','monthly',NULL,NULL)")
    db.execute("INSERT INTO subject_score VALUES (1,1,'s2',3,NULL,'乙','数学',80,80,NULL)")
    db.executemany("INSERT INTO total_score VALUES (?,?,?,?,?,?,?,?)", [
        (1, 1, "s2", "主三门", 240, None, None, None),
        (2, 1, "s3", "主三门", 250, None, None, None),
        (3, 1, "s1", "主三门", 260, None, None, None)])
    # 同日同科同人两行：出现序展开为两个独立批次，绝不压成一次。
    # content/remark 组合覆盖 evaluation 四象限；s2 跨学年行 pending。
    db.executemany("INSERT INTO homework_record VALUES (?,?,?,?,?,?)", [
        (1, "s1", "2032-09-10", "数学", None, None),
        (2, "s1", "2032-09-10", "数学", "漏做", None),
        (3, "s1", "2032-09-11", "物理", None, "病假"),
        (4, "s1", "2032-09-11", "物理", "补交", "病假"),
        (5, "s2", "2032-03-05", "化学", None, None),  # 2032-03 → AY2031-2032，命中 s2 学籍
        (6, "s2", "2032-09-12", "化学", None, None),  # AY2032-2033，s2 无该学年学籍 → pending
    ])
    # H 源 FK 完整（镜像强制 FK）；幽灵学号场景在 T 侧覆盖。
    db.executemany("INSERT INTO special_record VALUES (?,?,?,?,?)", [
        (1, "s1", "2032-09-16", "请假", None),
        (2, "s2", "2032-03-07", "比赛", "区赛"),
    ])
    db.execute("INSERT INTO homework_collection VALUES (1,'2032-09-09','化学',2,3)")
    db.commit(); db.close()


def _seed_teaching(path):
    db = sqlite3.connect(path)
    db.executescript(T_SHAPE)
    db.executemany("INSERT INTO homework_semester VALUES (?,?,?,?,?)", [
        (1, "2031学年暑假", "2032-07-01", "2032-08-31", 0),
        (2, "2031学年第二学期", "2032-03-01", "2032-06-30", 0),
        (3, "2032学年第一学期", "2032-09-01", "2033-01-21", 1),
    ])
    db.execute("INSERT INTO homework_setting VALUES ('active_grade','1')")
    db.execute("INSERT INTO teacher VALUES (1,'物理')")
    db.execute("INSERT INTO class_roster VALUES ('m1','丙')")  # T 名册不投影，审计需登记理由
    db.execute("INSERT INTO exam VALUES (1,'旧教学考试',2,'2032-04')")
    db.executemany("INSERT INTO teaching_class VALUES (?,?,?,?)", [
        (1, 2, "物A1", "物理"), (2, 2, "物B3", "物理")])
    db.executemany("INSERT INTO teaching_class_member VALUES (?,?,?,?,?)", [
        (1, 1, "m1", "manual", "丙"), (2, 2, "m1", "manual", "丙"),  # 双成员行 → 取最小教学班
        (3, 2, "m2", "manual", "丁")])
    # class_num=6 是行政班号：绝不据此造 label='6' 教学班（C1）。
    # m1 有成员事实 → class_ref=物A1；t-score-only 无成员事实 → class_ref=NULL。
    db.executemany("INSERT INTO subject_score VALUES (?,?,?,?,?,?,?)", [
        (1, 1, "t-score-only", 6, "物理", 88, 88),
        (2, 1, "m1", 6, "物理", 92, 92)])
    db.executemany("INSERT INTO homework_record VALUES (?,?,?,?,?,?,?,?)", [
        (1, "m1", "2032-03-01", "日常作业", None, None, "缺交", "字迹潦草"),
        (2, "m2", "2032-03-02", "化学", None, None, "已交", None),
        (3, "m2", "2032-03-03", "没带", None, None, "忘带", None),
        (4, "nom", "2032-03-04", "物理", None, None, "缺交", None),
        (5, "m2", "2032-09-05", "数学", None, None, "缺交", None),  # 学期覆盖 → 延续实例
        (6, "m2", "2032-08-15", "英语", None, None, "缺交", None),  # 八月被暑假学期覆盖 → 原实例（month>=8 会误判）
        (7, "m1", "2032-01-10", "语文", None, None, "缺交", None),  # 学期外缝隙 → 回退归上学年 → 原实例
    ])
    db.executemany("INSERT INTO special_record VALUES (?,?,?,?,?)", [
        (1, "m1", "2032-03-05", "晚到", None),
        (2, "ghost-t", "2032-03-06", "比赛", None),
    ])
    db.commit(); db.close()


def _seed_teaching_chains(path):
    """1c 红线夹具：g1- 是跨届撞号死号命名空间——前缀剥离≠同人，
    但 student_alias 证明的同人链必须接通成员班。"""
    db = sqlite3.connect(path)
    db.executescript(T_SHAPE)
    db.execute("INSERT INTO homework_semester VALUES (1,'2032学年第一学期','2032-09-01','2033-01-21',1)")
    db.execute("INSERT INTO homework_setting VALUES ('active_grade','1')")
    db.execute("INSERT INTO teacher VALUES (1,'物理')")
    db.execute("INSERT INTO exam VALUES (1,'旧考试',2,'2032-04')")
    db.execute("INSERT INTO teaching_class VALUES (1,2,'物A1','物理')")
    db.execute("INSERT INTO student_identity VALUES (1,'乙')")
    # 成员现号与链上死号同 identity（别名链证明同人）。
    db.executemany("INSERT INTO student_alias VALUES (?,?,?)", [
        (1, 1, "9900602"), (2, 1, "g1-9900602")])
    db.execute("INSERT INTO teaching_class_member VALUES (1,1,'9900602','manual','乙')")
    # 物理成绩：链上死号应接通成员班；无链死号 g1-9900603 与现号 9900603
    # 是两个不同的学生，绝不并成一个身份。
    db.executemany("INSERT INTO subject_score VALUES (?,?,?,?,?,?,?)", [
        (1, 1, "g1-9900602", 6, "物理", 91, 91),
        (2, 1, "g1-9900603", 6, "物理", 92, 92),
        (3, 1, "9900603", 6, "物理", 93, 93)])
    db.executemany("INSERT INTO homework_record VALUES (?,?,?,?,?,?,?,?)", [
        (1, "g1-9900602", "2032-03-01", "物理", None, None, "缺交", None),
        (2, "g1-9900603", "2032-03-02", "物理", None, None, "缺交", None)])
    db.commit(); db.close()


@pytest.fixture
def seeded_sources(tmp_path):
    h, t = tmp_path / "h.sqlite", tmp_path / "t.sqlite"
    _seed_homeroom(h); _seed_teaching(t)
    return h, t


def _submission_by_source_row(db, data_domain):
    return {row[0]: dict(row) for row in db.execute(
        "SELECT CAST(json_extract(m.source_pk,'$[0][1]') AS INTEGER) AS src_id, s.id, s.submission_status, s.evaluation,"
        " a.subject, a.homework_type, a.assigned_date, a.batch_token, a.expected_members_json, y.name AS ay_name"
        " FROM source_projection_map m JOIN homework_submission s ON s.id=m.target_id"
        " JOIN homework_assignment a ON a.id=s.assignment_id JOIN academic_year y ON y.id=a.academic_year_id"
        " WHERE m.source_table='homework_record' AND m.data_domain=? AND m.target_table='homework_submission'", (data_domain,))}


def test_semester_union_merges_overlap_and_keeps_single_current(seeded_sources):
    module = _load_module()
    h, t = seeded_sources
    module.project_workspace_facts(h, t, _target_db())
    db = sqlite3.connect(_target_db()); db.row_factory = sqlite3.Row
    rows = db.execute("SELECT s.name, s.start_date, s.end_date, s.is_current, s.mode, y.name AS ay FROM ws_homework_semester s JOIN academic_year y ON y.id=s.academic_year_id ORDER BY y.name, s.start_date").fetchall()
    assert len(rows) == 3  # 暑假 + 第二学期（同学年不重叠各成行）+ 合并行
    merged = [r for r in rows if r["ay"] == "2032-2033"]
    assert len(merged) == 1
    assert (merged[0]["name"], merged[0]["start_date"], merged[0]["end_date"]) == ("2032学年第一学期", "2032-09-01", "2033-01-21")
    assert merged[0]["is_current"] == 1 and merged[0]["mode"] == "manual"
    assert sum(int(r["is_current"]) for r in rows) == 1
    h_maps = db.execute("SELECT source_pk, projection_kind FROM source_projection_map WHERE source_table='homework_semester' AND data_domain='homeroom'").fetchall()
    assert [(m["source_pk"], m["projection_kind"]) for m in h_maps] == [('[["id",1]]', "semester_merged")]  # H 行被合并，不静默丢弃
    t_kinds = {m["source_pk"]: m["projection_kind"] for m in db.execute("SELECT source_pk, projection_kind FROM source_projection_map WHERE source_table='homework_semester' AND data_domain='teaching'")}
    assert t_kinds == {'[["id",1]]': "semester", '[["id",2]]': "semester", '[["id",3]]': "semester"}
    db.close()


def test_homeroom_homework_batch_seq_statuses_and_missing_enrollment(seeded_sources):
    module = _load_module()
    h, t = seeded_sources
    counts = module.project_workspace_facts(h, t, _target_db())
    db = sqlite3.connect(_target_db()); db.row_factory = sqlite3.Row
    assert counts["h_homework"] == 5 and counts["h_homework_pending"] == 1
    subs = _submission_by_source_row(db, "homeroom")
    assert len(subs) == 5
    # H 源无作业种类：legacy 占位；expected_members 绝不推断其余人已交。
    assert all(s["homework_type"] == "legacy" and s["expected_members_json"] == "[]" for s in subs.values())
    assert all(s["subject"] in ("数学", "物理", "化学") for s in subs.values())
    assert subs[1]["submission_status"] == "missing" and subs[1]["evaluation"] is None
    assert subs[2]["submission_status"] == "missing" and subs[2]["evaluation"] == "漏做"  # content 保留
    assert subs[3]["submission_status"] == "excused" and subs[3]["evaluation"] == "病假"
    assert subs[4]["submission_status"] == "excused" and subs[4]["evaluation"] == "病假|补交"
    # 同日同科同人两行 → 两个独立批次（token 出现序不同）。
    assert subs[1]["batch_token"] != subs[2]["batch_token"]
    assert subs[1]["batch_token"].startswith("migration:h:2032-09-10:数学:0:")
    assert subs[2]["batch_token"].startswith("migration:h:2032-09-10:数学:1:")
    # 行 6：2032-09-12 属 2032-2033 学年，s2 只有 2031-2032 学籍 → pending 不投影。
    pending = db.execute("SELECT reason FROM pending_import_row WHERE source_table='homework_record' AND data_domain='homeroom'").fetchall()
    assert len(pending) == 1 and pending[0]["reason"] == "该学年无学籍事实"
    # 学年班级由学籍行定位：行 5 落在 2031-2032 学年班级。
    old = db.execute("SELECT y.name FROM homework_assignment a JOIN academic_year y ON y.id=a.academic_year_id WHERE a.batch_token LIKE 'migration:h:2032-03-05:%'").fetchone()
    assert old["name"] == "2031-2032"
    db.close()


def test_teaching_homework_type_source_and_member_determinism(seeded_sources):
    module = _load_module()
    h, t = seeded_sources
    counts = module.project_workspace_facts(h, t, _target_db())
    db = sqlite3.connect(_target_db()); db.row_factory = sqlite3.Row
    assert counts["t_homework"] == 6 and counts["t_homework_pending"] == 1
    assert counts["t_hw_multi_member"] == 1
    assert counts["class_continuation"] == 2 and counts["member_continuation"] == 3
    assert counts["score_class_ref_null"] == 1
    subs = _submission_by_source_row(db, "teaching")
    assert len(subs) == 6
    # assignment.subject=教师学科（物理），homework_type=旧 subject 原值（作业种类）。
    assert all(s["subject"] == "物理" for s in subs.values())
    assert subs[1]["homework_type"] == "日常作业"
    assert subs[2]["homework_type"] == "化学"
    # 状态映射：缺交→missing，已交→submitted，其他→unknown；evaluation 原值。
    assert (subs[1]["submission_status"], subs[1]["evaluation"]) == ("missing", "字迹潦草")
    assert subs[2]["submission_status"] == "submitted"
    assert subs[3]["submission_status"] == "unknown"
    # 双成员行取 teaching_class_id 最小（物A1 原实例），token 带目标教学班。
    tc1 = db.execute("SELECT id FROM teaching_class WHERE label='物A1' AND academic_year_id=(SELECT id FROM academic_year WHERE name='2031-2032')").fetchone()[0]
    assert subs[1]["batch_token"].endswith(f":{tc1}")
    # 1d 学期覆盖优先：2032-08-15 被「暑假」窗口（2032-07-01~08-31）覆盖 →
    # AY2031-2032 原实例（month>=8 规则会误判成新学年）。
    assert subs[6]["assigned_date"] == "2032-08-15" and subs[6]["ay_name"] == "2031-2032"
    # 2032-01-10 在所有学期窗口之外 → 回退归上学年（month<9）→ AY2031-2032 原实例。
    assert subs[7]["assigned_date"] == "2032-01-10" and subs[7]["ay_name"] == "2031-2032"
    # C2：2032-09-05 被「第一学期」窗口覆盖 → AY2032-2033 延续实例（同学年才能读到）。
    assert subs[5]["assigned_date"] == "2032-09-05" and subs[5]["ay_name"] == "2032-2033"
    cont = db.execute("SELECT a.id, a.academic_year_id, tc.label FROM homework_assignment a JOIN teaching_class tc ON tc.id=a.class_ref_id WHERE a.batch_token LIKE 'migration:t:2032-09-05:%'").fetchone()
    ay_2032 = db.execute("SELECT id FROM academic_year WHERE name='2032-2033'").fetchone()[0]
    assert cont["academic_year_id"] == ay_2032 and cont["label"] == "物B3"
    pending = db.execute("SELECT reason FROM pending_import_row WHERE source_table='homework_record' AND reason LIKE '%教学班%'").fetchall()
    assert len(pending) == 1 and pending[0]["reason"] == "无教学班成员事实"
    db.close()


def test_teaching_class_instances_c1_no_fake_class_and_member_continuation(seeded_sources):
    module = _load_module()
    h, t = seeded_sources
    counts = module.project_workspace_facts(h, t, _target_db())
    db = sqlite3.connect(_target_db()); db.row_factory = sqlite3.Row
    # 目标教学班 = 2 原实例（2031-2032）+ 2 延续实例（2032-2033），无 label='6' 假班。
    rows = db.execute("SELECT tc.label, y.name, COUNT(*) c FROM teaching_class tc JOIN academic_year y ON y.id=tc.academic_year_id GROUP BY tc.label, y.name ORDER BY y.name, tc.label").fetchall()
    assert [(r["label"], r["name"], r["c"]) for r in rows] == [("物A1", "2031-2032", 1), ("物B3", "2031-2032", 1), ("物A1", "2032-2033", 1), ("物B3", "2032-2033", 1)]
    assert db.execute("SELECT COUNT(*) FROM teaching_class WHERE label='6'").fetchone()[0] == 0
    # 物理成绩 class_ref 只指向真实班或 NULL（C1）：m1→物A1 原实例；t-score-only→NULL。
    tc1 = db.execute("SELECT id FROM teaching_class WHERE label='物A1' AND academic_year_id=(SELECT id FROM academic_year WHERE name='2031-2032')").fetchone()[0]
    facts = {r["class_ref_id"]: r["c"] for r in db.execute("SELECT class_ref_id, COUNT(*) c FROM score_fact WHERE data_domain='teaching' GROUP BY class_ref_id")}
    assert facts == {tc1: 1, None: 1}
    assert counts["score_class_ref_null"] == 1
    # 延续成员：3 个 (班,人) 对，valid_from=延续学年起点，source 标注延续迁移。
    cont_members = db.execute(
        "SELECT m.source, m.valid_from, COUNT(*) c FROM teaching_class_member m JOIN teaching_class tc ON tc.id=m.teaching_class_id"
        " WHERE tc.academic_year_id=(SELECT id FROM academic_year WHERE name='2032-2033') GROUP BY m.source, m.valid_from").fetchall()
    assert [(r["source"], r["valid_from"], r["c"]) for r in cont_members] == [("migration:t-continuation", "2032-09-01", 3)]
    db.close()


def test_special_record_projects_notes_and_pending_orphans_without_ghost_identities(seeded_sources):
    module = _load_module()
    h, t = seeded_sources
    module.project_workspace_facts(h, t, _target_db())
    db = sqlite3.connect(_target_db()); db.row_factory = sqlite3.Row
    notes = db.execute("SELECT data_domain, source, category, content, follow_up FROM ws_student_note ORDER BY data_domain, content").fetchall()
    assert len(notes) == 3  # H 两条（note 可为空/非空）+ T 一条
    h_notes = [n for n in notes if n["data_domain"] == "homeroom"]
    t_note = [n for n in notes if n["data_domain"] == "teaching"][0]
    assert [(n["content"], n["category"], n["source"]) for n in h_notes] == [("[比赛] 区赛", "其他", "migration:h"), ("[请假] ", "其他", "migration:h")]
    assert h_notes[0]["follow_up"] is None
    assert (t_note["source"], t_note["content"]) == ("migration:t", "[晚到] ")
    # T 孤儿学号 pending；绝不为其新建「仅档案存在」的幽灵身份（H 源 FK 完整无此形态）。
    pend = {r["data_domain"]: r["reason"] for r in db.execute("SELECT p.data_domain, p.reason FROM pending_import_row p WHERE p.source_table='special_record'")}
    assert pend == {"teaching": "身份无域内事实"}
    ghosts = db.execute("SELECT COUNT(*) FROM ws_student_alias WHERE alias_value IN ('ghost-h','ghost-t')").fetchone()[0]
    assert ghosts == 0
    db.close()


def test_identity_chain_links_member_class_but_dead_number_stays_independent(tmp_path):
    """1c 红线：别名链证明的同人接通成员班；无链死号绝不并入现号。"""
    module = _load_module()
    h, t = tmp_path / "h.sqlite", tmp_path / "t.sqlite"
    _seed_homeroom(h); _seed_teaching_chains(t)
    counts = module.project_workspace_facts(h, t, _target_db())
    db = sqlite3.connect(_target_db()); db.row_factory = sqlite3.Row
    # 身份关系：链上死号 = 成员同 identity；无链死号与现号各自独立（三身份互异）。
    ident = {r["alias_value"]: r["identity_id"] for r in db.execute(
        "SELECT alias_value, identity_id FROM ws_student_alias WHERE data_domain='teaching'")}
    assert ident["g1-9900602"] == ident["9900602"]
    assert len({ident["9900602"], ident["g1-9900603"], ident["9900603"]}) == 3
    # 物理成绩 class_ref：链上死号 → 物A1 原实例；无链死号/现号 → NULL。
    tc1 = db.execute("SELECT id FROM teaching_class WHERE label='物A1' AND academic_year_id=(SELECT id FROM academic_year WHERE name='2031-2032')").fetchone()[0]
    facts = {r["identity_id"]: r["class_ref_id"] for r in db.execute(
        "SELECT identity_id, class_ref_id FROM score_fact WHERE data_domain='teaching'")}
    assert facts[ident["g1-9900602"]] == tc1
    assert facts[ident["g1-9900603"]] is None and facts[ident["9900603"]] is None
    assert counts["score_class_ref_null"] == 2 and counts["t_score"] == 3
    # 作业同样按身份解析：链上死号投影到成员班；无链死号 pending 且不建新身份。
    assert counts["t_homework"] == 1 and counts["t_homework_pending"] == 1
    sub = db.execute("SELECT a.class_ref_id FROM homework_submission s JOIN homework_assignment a ON a.id=s.assignment_id").fetchone()
    assert sub["class_ref_id"] == tc1
    pend = db.execute("SELECT reason FROM pending_import_row WHERE source_table='homework_record' AND data_domain='teaching'").fetchall()
    assert [p["reason"] for p in pend] == ["无教学班成员事实"]
    # T 域身份总数 = 1 个投影身份 + 2 个 score-only（g1-9900603、9900603），
    # pending 的死号作业没有再新造身份。
    assert db.execute("SELECT COUNT(*) FROM ws_student_identity WHERE data_domain='teaching'").fetchone()[0] == 3
    db.close()


def test_snapshot_bound_manual_decisions_resolve_alias_and_create_confirmed_pairs(tmp_path):
    """人工裁决只接通指定域内别名，并逐项验证班级成员后建立跨域配对。"""
    module = _load_module()
    h, t = tmp_path / "h.sqlite", tmp_path / "t.sqlite"
    _seed_homeroom(h); _seed_teaching_chains(t)
    db = sqlite3.connect(t)
    db.execute(
        "INSERT INTO homework_record VALUES (3,'_anon:confirmed','2032-03-03','日常作业',NULL,NULL,'缺交',NULL)"
    )
    db.commit(); db.close()
    decisions = {
        "identity_resolutions": [{
            "domain": "teaching",
            "source_table": "homework_record",
            "source_alias": "_anon:confirmed",
            "target_alias": "9900602",
            "expected_name": "乙",
        }],
        "links": [{
            "academic_year": "2032-2033",
            "admin_class": {"grade": 2, "class_num": 3},
            "teaching_class": {"label": "物A1", "subject": "物理"},
            "confirm_basis": "manual_confirm:fixture",
            "pairs": [{
                "homeroom_alias": "s1",
                "teaching_alias": "9900602",
                "expected_name": "甲",
            }],
        }],
    }
    # 配对两端可有不同名字；分别核验应使用各域期望名，故补充显式域名。
    decisions["links"][0]["pairs"][0].update({
        "homeroom_expected_name": "甲", "teaching_expected_name": "乙",
    })
    counts = module.project_workspace_facts(h, t, _target_db(), decisions)
    link_counts = module.apply_confirmed_links(_target_db(), decisions)
    db = sqlite3.connect(_target_db()); db.row_factory = sqlite3.Row
    assert counts["identity_resolution"] == 1
    target_identity = db.execute(
        "SELECT identity_id FROM ws_student_alias WHERE data_domain='teaching' AND alias_value='9900602'"
    ).fetchone()[0]
    assert db.execute(
        "SELECT identity_id FROM ws_student_alias WHERE data_domain='teaching' AND alias_value='_anon:confirmed'"
    ).fetchone()[0] == target_identity
    assert db.execute(
        "SELECT COUNT(*) FROM source_projection_map WHERE data_domain='teaching' AND source_table='homework_record' AND source_pk='[[\"id\",3]]'"
    ).fetchone()[0] == 1
    assert link_counts == {"links_created": 1, "pairs_created": 1}
    linked = db.execute(
        "SELECT l.confirm_basis FROM linked_student l JOIN homeroom_teaching_link h ON h.id=l.link_id"
    ).fetchone()
    assert linked["confirm_basis"] == "manual_confirm:fixture"
    db.close()


def test_homeroom_total_score_class_ref_backfill_three_branches(seeded_sources):
    """1e：H total_score 无 class_num，class_ref 按可证明证据回填三分支。"""
    module = _load_module()
    h, t = seeded_sources
    counts = module.project_workspace_facts(h, t, _target_db())
    db = sqlite3.connect(_target_db()); db.row_factory = sqlite3.Row
    assert counts["h_score"] == 4  # 1 科目 + 3 总分
    assert counts["total_class_ref_null"] == 1
    totals = {r["alias_value"]: (f"{r['ay']}|{r['grade']}|{r['cls']}" if r["cls"] else None) for r in db.execute(
        "SELECT a.alias_value, ac.grade, ac.class_num cls, y.name ay FROM score_fact f"
        " JOIN ws_student_alias a ON a.identity_id=f.identity_id AND a.data_domain='homeroom'"
        " LEFT JOIN administrative_class ac ON ac.id=f.class_ref_id"
        " LEFT JOIN academic_year y ON y.id=ac.academic_year_id"
        " WHERE f.data_domain='homeroom' AND f.total_type='主三门'")}
    assert totals["s2"] == "2031-2032|1|3"   # 分支一：同考科目行 class_num
    assert totals["s3"] == "2031-2032|1|3"   # 分支二：考试学年名册学籍行
    assert totals["s1"] is None               # 分支三：无可证明证据 → NULL 计数
    # 科目行自身班级不受回填影响。
    subj = db.execute("SELECT y.name FROM score_fact f JOIN administrative_class ac ON ac.id=f.class_ref_id JOIN academic_year y ON y.id=ac.academic_year_id WHERE f.data_domain='homeroom' AND f.subject='数学'").fetchone()
    assert subj["name"] == "2031-2032"
    db.close()


def test_projection_idempotent_on_second_run(seeded_sources):
    module = _load_module()
    h, t = seeded_sources
    first = module.project_workspace_facts(h, t, _target_db())
    db = sqlite3.connect(_target_db())
    watched = ("source_projection_map", "homework_assignment", "homework_submission", "ws_student_note",
               "ws_homework_semester", "pending_import_row", "ws_student_identity", "enrollment", "score_fact")
    baseline = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in watched}
    db.close()
    second = module.project_workspace_facts(h, t, _target_db())
    db = sqlite3.connect(_target_db())
    after = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in watched}
    assert after == baseline
    assert first == second
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("SELECT COUNT(*) FROM pragma_foreign_key_check").fetchone()[0] == 0
    db.close()


def test_projection_audit_classifies_tables_with_reasons(seeded_sources):
    module = _load_module()
    h, t = seeded_sources
    module.project_workspace_facts(h, t, _target_db())
    audit = module.build_projection_audit(h, t, _target_db())
    t_hw = audit["t"]["homework_record"]
    assert (t_hw["rows"], t_hw["projected"], t_hw["archive_only"]) == (7, 6, 1)
    assert t_hw["reason"] is not None and "待核实" in t_hw["reason"]
    h_coll = audit["h"]["homework_collection"]
    assert (h_coll["rows"], h_coll["projected"], h_coll["archive_only"]) == (1, 1, 0)
    assert h_coll["reason"] is None
    h_hw = audit["h"]["homework_record"]
    assert (h_hw["rows"], h_hw["projected"], h_hw["archive_only"]) == (6, 5, 1)
    h_roster = audit["h"]["class_roster"]
    assert h_roster["projected"] == h_roster["rows"] == 3 and h_roster["reason"] is None
    t_roster = audit["t"]["class_roster"]
    assert t_roster["archive_only"] == t_roster["rows"] == 1 and t_roster["reason"]


def test_cli_end_to_end_audit_report_and_resume_zero_add(seeded_sources, tmp_path):
    h, t = seeded_sources
    target = tmp_path / "target"
    command = [sys.executable, SCRIPT, "--homeroom-db", str(h), "--teaching-db", str(t),
               "--target-root", str(target), "--project-business"]
    first = subprocess.run(command, capture_output=True, text=True, cwd=REPO)
    assert first.returncode == 0, first.stderr
    payload = json.loads(first.stdout)
    assert payload["status"] == "completed"
    ws = payload["workspace"]
    assert (ws["h_homework"], ws["h_homework_pending"]) == (5, 1)
    assert (ws["t_homework"], ws["t_homework_pending"], ws["t_hw_multi_member"]) == (6, 1, 1)
    assert (ws["class_continuation"], ws["member_continuation"], ws["score_class_ref_null"]) == (2, 3, 1)
    assert (ws["h_score"], ws["total_class_ref_null"]) == (4, 1)
    assert ws["semester_merged"] == 1 and ws["semester"] == 3
    assert (ws["h_note"], ws["t_note"]) == (2, 1)
    audit_path = target / "reports" / "projection_audit.json"
    assert audit_path.is_file()
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["t"]["homework_record"]["projected"] == 6
    assert audit["h"]["homework_collection"]["projected"] == 1
    assert audit["h"]["homework_collection"]["reason"] is None
    db = sqlite3.connect(target / "data" / "db.sqlite")
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("SELECT COUNT(*) FROM pragma_foreign_key_check").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM ws_homework_semester WHERE is_current=1").fetchone()[0] == 1
    db.close()
    second = subprocess.run(command + ["--resume"], capture_output=True, text=True, cwd=REPO)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["added"] == 0
