"""P1 合并工作台契约用例：合成样本与共享 fixture。

契约先行说明：
- 被测代码（app.core.context / app.core.errors / app.db.workspace_models /
  挂在 app.main 的 /api/v1 路由）由实现方按 P1 契约提供。为保证
  `pytest --collect-only` 在实现落地前仍可收集，所有 app 导入都在
  fixture / 用例体内延迟执行，测试模块顶层只 import 标准库与 pytest。
- 复用 backend/tests/conftest.py 的模块级 schema 重建隔离
  （isolated_module_schema），本文件只负责数据 seed，不引入新的数据目录。
- 样本全部虚构：学年 2025-2026、高二 6 班（行政，教师绑定班）、
  物理教学班 T6/T8，考试「2025期中」。
- 戊与甲同名（display_name 相同）、学号裸号同为 01，专用于验证
  「同名同号跨域不自动合并」。
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

# 模块导入即把 ws_* 新表注册进共享 MetaData：tests/conftest.py 的
# isolated_module_schema 会在每个测试模块前 drop/create_all，只有此处
# 顶层导入才能保证 tests/v1 独立运行时新表也被建出（tests/conftest.py
# 先于本文件被 pytest 加载并设置好隔离数据目录，导入是安全的）。
from app.db import workspace_models  # noqa: F401

# ── 合成样本常量（唯一事实来源；各用例文件按需局部复制并注明）──
AY_NAME = "2025-2026"
SUBJECT = "物理"
EXAM_E1 = "2025期中"
TOTAL_TYPE_MAIN3 = "主三门"

NAME_JIA_H = "秦甲"
NAME_JIA_T = "秦甲·T"
NAME_YI_H = "秦乙"
NAME_YI_T = "秦乙·T"
NAME_BING_H = "秦丙"
NAME_DING_T = "秦丁·T"
NAME_WU_T = "秦甲"  # 与甲同名（跨域碰撞样本）
NAME_JI_T = "秦己·T"

ALIAS_JIA_H = "2025H6-01"
ALIAS_JIA_T = "2025T6-01"
ALIAS_YI_H = "2025H6-02"
ALIAS_YI_T = "2025T6-02"
ALIAS_BING_H = "2025H6-03"
ALIAS_DING_T = "2025T6-04"
ALIAS_WU_T = "2025T8-01"  # 与甲的行政班别名同裸号 01，域不同
ALIAS_JI_T = "2025T8-02"

# homeroom 域全科（甲乙丙）+ 主三门总分；teaching 域只有物理。
# 甲乙的物理两域分值故意不同（90 vs 91 / 84 vs 85），用于检验
# shared_subject_score 确实取自 teaching 域投影。丁缺考（score=NULL）。
HOMEROOM_SUBJECT_SCORES = {
    "jia": {"语文": 88.0, "数学": 92.0, "英语": 95.0, "物理": 90.0},
    "yi": {"语文": 76.0, "数学": 81.0, "英语": 79.0, "物理": 84.0},
    "bing": {"语文": 65.0, "数学": 70.0, "英语": 72.0, "物理": 68.0},
}
TOTAL_MAIN3 = {"jia": 275.0, "yi": 236.0, "bing": 207.0}
TEACHING_PHYSICS_T6 = {"jia": 91.0, "yi": 85.0, "ding": None}
TEACHING_PHYSICS_T8 = {"wu": 77.0, "ji": 82.0}


def set_first_column(obj, candidates, value):
    """契约只冻结关键列；对次选列名做兼容探测，全部缺失则跳过该列。

    返回实际命中的列名（None 表示未写）。集成阶段如失败，优先核对本处
    的候选列名与实现差异。
    """
    columns = obj.__table__.columns
    for name in candidates:
        if name in columns:
            setattr(obj, name, value)
            return name
    return None


def set_date_column(obj, column_name, iso_value):
    """日期列类型契约未定：DateTime/Date 列写 datetime，其余按 ISO 字符串。"""
    from sqlalchemy import Date, DateTime

    if column_name not in obj.__table__.columns:
        return
    column = obj.__table__.columns[column_name]
    if isinstance(column.type, (DateTime, Date)):
        setattr(obj, column_name, datetime.fromisoformat(iso_value))
    else:
        setattr(obj, column_name, iso_value)


@pytest.fixture(scope="module")
def client():
    """TestClient 挂在 app.main；延迟导入避免实现落地前 collection 失败。"""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture(scope="module")
def v1_seed(isolated_module_schema):
    """模块级合成样本（S02–S06 共用）。

    结构：
    - 教师 id=1 绑定高二 6 班（active_grade=2，照抄既有用例造法）。
    - H6（行政，成员甲乙丙）、H9（行政，无成员，非绑定班，供越界测试）。
    - T6（物理，成员甲乙丁）、T8（物理，成员戊己）、T-empty（无成员）。
    - Link：H6↔T6 active（version=1）+ LinkedStudent 甲乙。
    - ScoreFact：E1 下 homeroom 域全科+总分、teaching 域物理（丁 NULL）。
    """
    from app.db import workspace_models as wm
    from app.db.models import HomeworkSetting, SessionLocal, Teacher

    db = SessionLocal()
    db.merge(Teacher(id=1, name="测试班主任", target_class_high2=6))
    db.merge(HomeworkSetting(key="active_grade", value="2"))

    ay = wm.AcademicYear()
    set_first_column(ay, ("name", "code", "label"), AY_NAME)
    set_date_column(ay, "start_date", "2025-09-01")
    set_date_column(ay, "end_date", "2026-07-15")
    db.add(ay)
    db.flush()

    h6 = wm.AdministrativeClass(
        academic_year_id=ay.id, grade=2, class_num=6, label="高二6班"
    )
    h9 = wm.AdministrativeClass(
        academic_year_id=ay.id, grade=2, class_num=9, label="高二9班"
    )
    db.add_all([h6, h9])
    db.flush()

    def teaching_class(label):
        tc = wm.TeachingClass(academic_year_id=ay.id, subject=SUBJECT, label=label)
        set_first_column(tc, ("teacher_id",), 1)
        db.add(tc)
        return tc

    t6 = teaching_class("高二6班(教)")
    t8 = teaching_class("高二8班(教)")
    t_empty = teaching_class("高二6班(教·空)")
    db.flush()

    def identity(domain, display_name):
        obj = wm.WsStudentIdentity()
        set_first_column(obj, ("data_domain", "domain"), domain)
        set_first_column(obj, ("display_name", "name"), display_name)
        db.add(obj)
        return obj

    jia_h = identity("homeroom", NAME_JIA_H)
    jia_t = identity("teaching", NAME_JIA_T)
    yi_h = identity("homeroom", NAME_YI_H)
    yi_t = identity("teaching", NAME_YI_T)
    bing_h = identity("homeroom", NAME_BING_H)
    ding_t = identity("teaching", NAME_DING_T)
    wu_t = identity("teaching", NAME_WU_T)
    ji_t = identity("teaching", NAME_JI_T)
    db.flush()

    def alias(ident, alias_value, domain):
        # v2.2/G06：种子 alias 一律规范标注本学年（NULL 学年登记不再默认
        # 视为本学年身份，规范的当前登记必须带学年标记）；alias_scope
        # 冗余列与学年同步（参与唯一约束，见模型注释）
        a = wm.WsStudentAlias()
        set_first_column(a, ("identity_id",), ident.id)
        set_first_column(a, ("alias", "alias_value", "student_id", "sid"), alias_value)
        set_first_column(a, ("data_domain", "domain"), domain)
        set_first_column(a, ("academic_year_id",), ay.id)
        set_first_column(a, ("alias_scope",), str(ay.id))
        set_first_column(a, ("link_source", "source"), "synthetic-test")
        db.add(a)

    alias(jia_h, ALIAS_JIA_H, "homeroom")
    alias(jia_t, ALIAS_JIA_T, "teaching")
    alias(yi_h, ALIAS_YI_H, "homeroom")
    alias(yi_t, ALIAS_YI_T, "teaching")
    alias(bing_h, ALIAS_BING_H, "homeroom")
    alias(ding_t, ALIAS_DING_T, "teaching")
    alias(wu_t, ALIAS_WU_T, "teaching")
    alias(ji_t, ALIAS_JI_T, "teaching")

    def enrollment(cls_obj, ident, seat_no):
        e = wm.Enrollment()
        set_first_column(e, ("admin_class_id",), cls_obj.id)
        set_first_column(e, ("identity_id",), ident.id)
        set_first_column(e, ("status",), "active")
        set_date_column(e, "valid_from", "2025-09-01")
        set_first_column(e, ("seat_no", "seat"), seat_no)
        db.add(e)

    enrollment(h6, jia_h, 1)
    enrollment(h6, yi_h, 2)
    enrollment(h6, bing_h, 3)

    def member(cls_obj, ident):
        m = wm.TeachingClassMember()
        set_first_column(m, ("teaching_class_id",), cls_obj.id)
        set_first_column(m, ("identity_id",), ident.id)
        set_date_column(m, "valid_from", "2025-09-01")
        db.add(m)

    member(t6, jia_t)
    member(t6, yi_t)
    member(t6, ding_t)
    member(t8, wu_t)
    member(t8, ji_t)

    link = wm.HomeroomTeachingLink()
    set_first_column(link, ("admin_class_id",), h6.id)
    set_first_column(link, ("teaching_class_id",), t6.id)
    set_first_column(link, ("academic_year_id",), ay.id)
    set_first_column(link, ("subject",), SUBJECT)
    set_first_column(link, ("status",), "active")
    set_first_column(link, ("version",), 1)
    set_date_column(link, "valid_from", "2025-09-01")
    db.add(link)
    db.flush()

    for h_ident, t_ident in ((jia_h, jia_t), (yi_h, yi_t)):
        ls = wm.LinkedStudent()
        set_first_column(ls, ("link_id",), link.id)
        set_first_column(ls, ("homeroom_identity_id",), h_ident.id)
        set_first_column(ls, ("teaching_identity_id",), t_ident.id)
        set_first_column(ls, ("confirm_basis",), "synthetic-test")
        db.add(ls)

    def score_fact(domain, cls_obj, ident, subject, total_type, score):
        f = wm.ScoreFact()
        set_first_column(f, ("data_domain", "domain"), domain)
        set_first_column(f, ("academic_year_id",), ay.id)
        set_first_column(f, ("exam_name",), EXAM_E1)
        set_date_column(f, "exam_date", "2025-11-06")
        set_first_column(f, ("class_ref_id",), cls_obj.id)
        set_first_column(f, ("identity_id", "person_id"), ident.id)
        set_first_column(f, ("subject",), subject)
        set_first_column(f, ("total_type",), total_type)
        set_first_column(f, ("score",), score)
        set_first_column(f, ("source",), "synthetic-test")
        db.add(f)

    for key, ident in (("jia", jia_h), ("yi", yi_h), ("bing", bing_h)):
        for subject, value in HOMEROOM_SUBJECT_SCORES[key].items():
            score_fact("homeroom", h6, ident, subject, None, value)
        score_fact("homeroom", h6, ident, "总分", TOTAL_TYPE_MAIN3, TOTAL_MAIN3[key])
    for key, ident in (("jia", jia_t), ("yi", yi_t), ("ding", ding_t)):
        score_fact("teaching", t6, ident, SUBJECT, None, TEACHING_PHYSICS_T6[key])
    for key, ident in (("wu", wu_t), ("ji", ji_t)):
        score_fact("teaching", t8, ident, SUBJECT, None, TEACHING_PHYSICS_T8[key])

    db.commit()

    yield SimpleNamespace(
        ay_id=ay.id,
        h6_id=h6.id,
        h9_id=h9.id,
        t6_id=t6.id,
        t8_id=t8.id,
        t_empty_id=t_empty.id,
        link_id=link.id,
        jia_h_id=jia_h.id,
        yi_h_id=yi_h.id,
        bing_h_id=bing_h.id,
        jia_t_id=jia_t.id,
        yi_t_id=yi_t.id,
        ding_t_id=ding_t.id,
        wu_t_id=wu_t.id,
        ji_t_id=ji_t.id,
        h_person_ids=[jia_h.id, yi_h.id, bing_h.id],
        t6_person_ids=[jia_t.id, yi_t.id, ding_t.id],
        t8_person_ids=[wu_t.id, ji_t.id],
    )
    db.close()
