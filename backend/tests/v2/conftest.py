"""P2-C4 合成样本（契约 docs/diagnosis-roadmap/p2-contracts.md §5 用例共享）。

在 tests/v1/conftest.py 的 v1_seed 之上补种专门服务 C4 的场景（ORM 直种，
不走导入链路）。全部姓名合成，与 P1 样本同风格（秦X）：

- 秦档/秦门/秦语/秦钥/秦锁/秦考/秦务/秦试/秦止（班主任域，高二6班）；
  秦理·T（教学域 T6）。各人独立，避免「同人同科未关闭干预」跨用例串扰。
- 考试 C4E1（60 天前）为基线场；秦考另有 C4E2（7 天前）主三门缺考行
  （score=NULL 无名次），其余用例的新可比考试在用例内补种（对应真实
  时序：干预创建后才有新考试）。

fixture 命名 p2c4_seed，模块级（每个测试模块从空 schema 重建，互不影响）。
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

# v1 合成样本与 client fixture 原样复用（pytest 从本 conftest 命名空间解析）
from tests.v1.conftest import *  # noqa: F401,F403

EXAM_C4_BASE = "C4基线考"
EXAM_C4_MISSING = "C4缺考考"

NAME_DANG = "秦档"
NAME_MEN = "秦门"
NAME_YU = "秦语"
NAME_YUE = "秦钥"
NAME_SUO = "秦锁"
NAME_KAO = "秦考"
NAME_WU2 = "秦务"
NAME_SHI = "秦试"
NAME_ZHI = "秦止"
NAME_LI_T = "秦理·T"
NAME_SHU_T = "秦数·T"


def _d(days_ago: int) -> date:
    return date.today() - timedelta(days=days_ago)


@pytest.fixture(scope="module")
def p2c4_seed(v1_seed):
    """C4 专用场景：9 名班主任域学生 + 1 名教学域学生 + 基线场成绩。"""
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    s = v1_seed
    db = SessionLocal()

    def person(name, seat):
        ident = wm.WsStudentIdentity(data_domain="homeroom", display_name=name)
        db.add(ident)
        db.flush()
        db.add(
            wm.Enrollment(
                admin_class_id=s.h6_id,
                identity_id=ident.id,
                status="active",
                valid_from=date(2025, 9, 1),
                seat_no=seat,
            )
        )
        return ident

    def person_t(name, cls_id):
        ident = wm.WsStudentIdentity(data_domain="teaching", display_name=name)
        db.add(ident)
        db.flush()
        db.add(
            wm.TeachingClassMember(
                teaching_class_id=cls_id,
                identity_id=ident.id,
                valid_from=date(2025, 9, 1),
            )
        )
        return ident

    dang = person(NAME_DANG, 21)
    men = person(NAME_MEN, 22)
    yu = person(NAME_YU, 23)
    yue = person(NAME_YUE, 24)
    suo = person(NAME_SUO, 25)
    kao = person(NAME_KAO, 26)
    wu2 = person(NAME_WU2, 27)
    shi = person(NAME_SHI, 28)
    zhi = person(NAME_ZHI, 29)
    li_t = person_t(NAME_LI_T, s.t6_id)
    shu_t = person_t(NAME_SHU_T, s.t6_id)
    db.flush()

    def fact(domain, cls_id, ident_id, exam, exam_date, subject=None, total_type=None,
             score=None, pct=None, xueji=None, grade_rank=None, grade_score=None):
        f = wm.ScoreFact()
        f.data_domain = domain
        f.academic_year_id = s.ay_id
        f.exam_name = exam
        f.exam_date = exam_date
        f.class_ref_id = cls_id
        f.identity_id = ident_id
        f.subject = subject
        f.total_type = total_type
        f.score = score
        f.grade_percentile = pct
        f.xueji_rank = xueji
        f.grade_rank = grade_rank
        f.grade_score = grade_score
        f.source = "p2-c4-test"
        db.add(f)
        return f

    # ── 基线场 C4E1（60 天前）：各人主三门名次 / 秦语的语文百分位 / 秦理·T 等级分 ──
    base_day = _d(60)
    fact("homeroom", s.h6_id, dang.id, EXAM_C4_BASE, base_day, None, "主三门", 274.0,
         pct=0.31, xueji=210, grade_rank=221)
    fact("homeroom", s.h6_id, men.id, EXAM_C4_BASE, base_day, None, "主三门", 275.0,
         pct=0.30, xueji=200, grade_rank=211)
    fact("homeroom", s.h6_id, yu.id, EXAM_C4_BASE, base_day, "语文", None, 80.0, pct=0.50)
    fact("homeroom", s.h6_id, yue.id, EXAM_C4_BASE, base_day, None, "主三门", 231.0,
         pct=0.66, xueji=300, grade_rank=311)
    fact("homeroom", s.h6_id, suo.id, EXAM_C4_BASE, base_day, None, "主三门", 229.0,
         pct=0.67, xueji=310, grade_rank=321)
    fact("homeroom", s.h6_id, kao.id, EXAM_C4_BASE, base_day, None, "主三门", 221.0,
         pct=0.70, xueji=400, grade_rank=411)
    fact("homeroom", s.h6_id, shi.id, EXAM_C4_BASE, base_day, None, "主三门", 272.0,
         pct=0.33, xueji=220, grade_rank=231)
    fact("homeroom", s.h6_id, zhi.id, EXAM_C4_BASE, base_day, None, "主三门", 271.0,
         pct=0.34, xueji=230, grade_rank=241)
    # 秦务（wu2）零成绩：no_baseline 场景
    fact("teaching", s.t6_id, li_t.id, EXAM_C4_BASE, base_day, "物理", None, 88.0,
         pct=0.30, grade_score=82.0)
    # 秦数·T 专供作用域守卫用例（不被其他用例改动，断言确定性）
    fact("teaching", s.t6_id, shu_t.id, EXAM_C4_BASE, base_day, "物理", None, 84.0,
         pct=0.36, grade_score=78.0)

    # ── 秦考的 C4E2（7 天前）：主三门缺考行（有行、无分无名次→不可比） ──
    fact("homeroom", s.h6_id, kao.id, EXAM_C4_MISSING, _d(7), None, "主三门", None)

    db.commit()
    yield SimpleNamespace(
        seed=s,
        today=date.today(),
        dang_id=dang.id,
        men_id=men.id,
        yu_id=yu.id,
        yue_id=yue.id,
        suo_id=suo.id,
        kao_id=kao.id,
        wu2_id=wu2.id,
        shi_id=shi.id,
        zhi_id=zhi.id,
        li_t_id=li_t.id,
        shu_t_id=shu_t.id,
        exam_base=EXAM_C4_BASE,
        exam_missing=EXAM_C4_MISSING,
    )
    db.close()


# 供用例构造日期（ISO 字符串）
def iso(days_ago: int) -> str:
    return _d(days_ago).isoformat()
