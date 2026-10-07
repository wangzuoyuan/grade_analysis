"""P1-B3 学生与班级变化分解契约用例（docs/diagnosis-roadmap/p1-contracts.md §5 + §8）。

在 tests/v1/conftest.py 合成样本（v1_seed）之上 ORM 直种两场考试
（2026一模 → 2026二模）的变化场景，全部合成姓名（秦氏家族）。覆盖 §8
要求：缺考、缺科、百分位方向与单位、同分、跨学年、范围隔离（教学域
不读全科）、稀疏历史；另覆盖：转入/转出/缺考的 excluded 计数、基期
percentile_bin 与学校段位分组、CLASS_GROUP_MIN_SIZE 小样本聚合抑制、
下钻 person_id 名单、direction_note 方向解释字段、禁止因果字段。

方向语义（契约 §5 强制）：percentile_change = 本次百分位 − 上次百分位，
单位百分点，负值 = 相对位置上升；rank_change = 本次名次 − 上次名次，
负值 = 名次数值变小 = 相对位置上升。
"""

from datetime import date
from types import SimpleNamespace
from urllib.parse import quote

import pytest

API = "/api/v1"

EXAM_FROM = "2026一模"  # 2026-03-10
EXAM_TO = "2026二模"  # 2026-05-20
EXAM_OTHER_CLASS = "2026他班考"  # H9 的考试：本班无行 → 200 空可比
EXAM_MISSING = "2026不存在"  # 域内完全不存在 → 404
EXAM_OLD = "2025旧学年考"  # 旧学年 2024-2025 的考试 → 跨学年 404

STUDENT_URL = f"{API}/homeroom/diagnosis/changes/student"
CLASS_URL = f"{API}/homeroom/diagnosis/changes/class"
T_STUDENT_URL = f"{API}/teaching/diagnosis/changes/student"
T_CLASS_URL = f"{API}/teaching/diagnosis/changes/class"


@pytest.fixture(scope="module")
def b3_seed(v1_seed):
    """在 v1_seed 基础上补种变化分解场景（ORM 直种）。

    H6 成员与角色（全部合成）：
    - 甲乙丙：两场完整数据（甲进步、乙退步且二模缺数学、丙临界段）
    - 秦丁：2026-04-01 转出（仅一模有数据）→ excluded.transferred_out
    - 秦戊：2026-04-15 转入（仅二模有数据）→ excluded.transferred_in
    - 秦己：两场均在册、二模缺考（score=NULL 行）→ excluded.missing_exam
    - 秦辛/秦壬：一模同百分位同分箱（0.45）、同名次 200（壬仅 grade_rank）
    - 秦未：一模百分位与名次均未导入（可比但不进分箱）→ ungrouped
    - 秦申：稀疏历史——仅一模一条数学行，二模无任何行 → excluded.missing_exam
    - 秦庚：H9 学生 + 2026他班考（空可比样本）
    T6（物理教学班）甲T/乙T/丁T 两场完整数据（丁T 二模与乙T 同分 84）。
    """
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    s = v1_seed
    db = SessionLocal()
    from_date = date(2026, 3, 10)
    to_date = date(2026, 5, 20)

    def identity(domain, name):
        obj = wm.WsStudentIdentity(data_domain=domain, display_name=name)
        db.add(obj)
        return obj

    def enroll(cls_id, ident, valid_from="2025-09-01", valid_to=None):
        db.add(
            wm.Enrollment(
                admin_class_id=cls_id,
                identity_id=ident.id,
                status="active",
                valid_from=date.fromisoformat(valid_from),
                valid_to=date.fromisoformat(valid_to) if valid_to else None,
            )
        )

    ding_h = identity("homeroom", "秦丁")
    wu_h = identity("homeroom", "秦戊")
    ji_h = identity("homeroom", "秦己")
    xin_h = identity("homeroom", "秦辛")
    ren_h = identity("homeroom", "秦壬")
    wei_h = identity("homeroom", "秦未")
    shen_h = identity("homeroom", "秦申")
    geng_h = identity("homeroom", "秦庚")
    db.flush()

    enroll(s.h6_id, ding_h, valid_to="2026-04-01")  # 一模后转出
    enroll(s.h6_id, wu_h, valid_from="2026-04-15")  # 二模前转入
    for ident in (ji_h, xin_h, ren_h, wei_h, shen_h):
        enroll(s.h6_id, ident)
    enroll(s.h9_id, geng_h)
    db.flush()

    def fact(domain, cls_id, ident_id, exam, exam_date, subject, total_type,
             score, percentile=None, xueji=None, grade_rank=None, grade_score=None):
        f = wm.ScoreFact(
            data_domain=domain,
            academic_year_id=s.ay_id,
            exam_name=exam,
            exam_date=exam_date,
            class_ref_id=cls_id,
            identity_id=ident_id,
            subject=subject,
            total_type=total_type,
            score=score,
            grade_percentile=percentile,
            xueji_rank=xueji,
            grade_rank=grade_rank,
            grade_score=grade_score,
            source="p1-b3-test",
        )
        db.add(f)
        return f

    def total(domain, cls_id, ident_id, exam, exam_date, score, pct, xueji=None, grade_rank=None):
        return fact(domain, cls_id, ident_id, exam, exam_date, None, "主三门",
                    score, pct, xueji, grade_rank)

    # ── 甲：两场全科+总分，进步（rank 42→30，百分位全面上升）──
    h, jia = s.h6_id, s.jia_h_id
    total("homeroom", h, jia, EXAM_FROM, from_date, 620.0, 0.18, xueji=42)
    total("homeroom", h, jia, EXAM_TO, to_date, 640.0, 0.12, xueji=30)
    fact("homeroom", h, jia, EXAM_FROM, from_date, "语文", None, 110.0, percentile=0.30)
    fact("homeroom", h, jia, EXAM_TO, to_date, "语文", None, 115.0, percentile=0.22)
    fact("homeroom", h, jia, EXAM_FROM, from_date, "数学", None, 130.0, percentile=0.10)
    fact("homeroom", h, jia, EXAM_TO, to_date, "数学", None, 135.0, percentile=0.06)
    fact("homeroom", h, jia, EXAM_FROM, from_date, "英语", None, 125.0, percentile=0.25)
    fact("homeroom", h, jia, EXAM_TO, to_date, "英语", None, 120.0, percentile=0.31)
    fact("homeroom", h, jia, EXAM_FROM, from_date, "物理", None, 90.0, percentile=0.15, grade_score=67)
    fact("homeroom", h, jia, EXAM_TO, to_date, "物理", None, 95.0, percentile=0.10, grade_score=70)

    # ── 乙：退步（rank 350→380），二模缺数学（缺科样本）──
    yi = s.yi_h_id
    total("homeroom", h, yi, EXAM_FROM, from_date, 520.0, 0.50, xueji=350)
    total("homeroom", h, yi, EXAM_TO, to_date, 530.0, 0.55, xueji=380)
    fact("homeroom", h, yi, EXAM_FROM, from_date, "语文", None, 95.0, percentile=0.48)
    fact("homeroom", h, yi, EXAM_TO, to_date, "语文", None, 98.0, percentile=0.52)
    fact("homeroom", h, yi, EXAM_FROM, from_date, "数学", None, 100.0, percentile=0.60)
    fact("homeroom", h, yi, EXAM_FROM, from_date, "物理", None, 84.0, percentile=0.58, grade_score=55)
    fact("homeroom", h, yi, EXAM_TO, to_date, "物理", None, 80.0, percentile=0.62, grade_score=52)

    # ── 丙：临界段（rank 450→430），只种数学 ──
    bing = s.bing_h_id
    total("homeroom", h, bing, EXAM_FROM, from_date, 430.0, 0.82, xueji=450)
    total("homeroom", h, bing, EXAM_TO, to_date, 445.0, 0.78, xueji=430)
    fact("homeroom", h, bing, EXAM_FROM, from_date, "数学", None, 70.0, percentile=0.85)
    fact("homeroom", h, bing, EXAM_TO, to_date, "数学", None, 75.0, percentile=0.80)

    # ── 丁：转出样本，仅一模 ──
    total("homeroom", h, ding_h.id, EXAM_FROM, from_date, 480.0, 0.72, xueji=300)

    # ── 戊：转入样本，仅二模 ──
    total("homeroom", h, wu_h.id, EXAM_TO, to_date, 510.0, 0.60, xueji=320)

    # ── 己：缺考样本（二模行存在但 score=NULL，绝不转 0）──
    total("homeroom", h, ji_h.id, EXAM_FROM, from_date, 400.0, 0.90, xueji=520)
    total("homeroom", h, ji_h.id, EXAM_TO, to_date, None, None, xueji=None)
    fact("homeroom", h, ji_h.id, EXAM_FROM, from_date, "数学", None, 40.0, percentile=0.88)
    fact("homeroom", h, ji_h.id, EXAM_TO, to_date, "数学", None, None)

    # ── 辛/壬：同分同箱样本（一模同为 0.45 / 名次 200；壬只有 grade_rank）──
    total("homeroom", h, xin_h.id, EXAM_FROM, from_date, 500.0, 0.45, xueji=200)
    total("homeroom", h, xin_h.id, EXAM_TO, to_date, 505.0, 0.45, xueji=200)
    fact("homeroom", h, xin_h.id, EXAM_FROM, from_date, "数学", None, 118.0, percentile=0.45)
    fact("homeroom", h, xin_h.id, EXAM_TO, to_date, "数学", None, 118.0, percentile=0.45)
    total("homeroom", h, ren_h.id, EXAM_FROM, from_date, 500.0, 0.45, grade_rank=200)
    total("homeroom", h, ren_h.id, EXAM_TO, to_date, 510.0, 0.44, xueji=205)

    # ── 未：可比但一模百分位/名次均未导入（分箱 ungrouped 样本）──
    total("homeroom", h, wei_h.id, EXAM_FROM, from_date, 460.0, None)
    total("homeroom", h, wei_h.id, EXAM_TO, to_date, 470.0, 0.75, xueji=470)

    # ── 申：稀疏历史——仅一模一条数学行 ──
    fact("homeroom", h, shen_h.id, EXAM_FROM, from_date, "数学", None, 55.0, percentile=0.86)

    # ── 他班空可比样本：H9 的考试 ──
    fact("homeroom", s.h9_id, geng_h.id, EXAM_OTHER_CLASS, date(2026, 6, 1), "数学", None, 60.0, percentile=0.70)

    # ── teaching 域 T6 物理：甲T/乙T/丁T 两场（丁T 二模与乙T 同分 84）──
    t6 = s.t6_id
    for ident, (s1, p1, g1), (s2, p2, g2) in (
        (s.jia_t_id, (90.0, 0.15, 67), (82.0, 0.10, 70)),
        (s.yi_t_id, (84.0, 0.18, 61), (84.0, 0.50, 58)),
        (s.ding_t_id, (55.0, 0.12, 40), (84.0, 0.80, 61)),
    ):
        fact("teaching", t6, ident, EXAM_FROM, from_date, "物理", None, s1, percentile=p1, grade_score=g1)
        fact("teaching", t6, ident, EXAM_TO, to_date, "物理", None, s2, percentile=p2, grade_score=g2)

    # ── 旧学年考试（跨学年 404 样本）──
    ay2 = wm.AcademicYear(name="2024-2025", start_date=date(2024, 9, 1), end_date=date(2025, 7, 15))
    db.add(ay2)
    db.flush()
    old_cls = wm.AdministrativeClass(academic_year_id=ay2.id, grade=1, class_num=6, label="高一6班")
    db.add(old_cls)
    db.flush()
    # 旧学年事实必须挂在 ay2（跨学年 404 样本），不能走默认 ay_id 的 fact 助手
    db.add(
        wm.ScoreFact(
            data_domain="homeroom",
            academic_year_id=ay2.id,
            exam_name=EXAM_OLD,
            exam_date=date(2025, 1, 10),
            class_ref_id=old_cls.id,
            identity_id=s.jia_h_id,
            subject="数学",
            total_type=None,
            score=120.0,
            grade_percentile=0.15,
            source="p1-b3-test",
        )
    )

    db.commit()
    yield SimpleNamespace(
        jia_h_id=s.jia_h_id,
        yi_h_id=s.yi_h_id,
        ji_h_id=ji_h.id,
        xin_h_id=xin_h.id,
        ren_h_id=ren_h.id,
        wei_h_id=wei_h.id,
        shen_h_id=shen_h.id,
        geng_h_id=geng_h.id,
        jia_t_id=s.jia_t_id,
        t6_id=s.t6_id,
        h6_id=s.h6_id,
        ay_id=s.ay_id,
        ay2_id=ay2.id,
    )
    db.close()


def _get(client, url, **params):
    return client.get(url, params=params)


def _subject(response, name):
    return next(item for item in response["subjects"] if item["subject"] == name)


# ────────────────────── 学生分解：homeroom 域 ──────────────────────────────


def test_student_homeroom_changes_direction_and_units(client, b3_seed):
    """百分位方向与单位（§8）：percentile_change=百分点、负值=相对位置上升；
    主三门名次变化走 resolve_year_rank（学籍名次）；等级分变化。"""
    r = _get(client, STUDENT_URL, person_id=b3_seed.jia_h_id, from_exam=EXAM_FROM, to_exam=EXAM_TO)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["person_id"] == b3_seed.jia_h_id
    assert body["calc_version"] == "p1-v1"
    assert body["status"] == "ok" and body["missing_reason"] is None
    assert body["from_exam_date"] == "2026-03-10" and body["to_exam_date"] == "2026-05-20"
    # 方向语义文字解释字段（§5 强制）
    assert "百分点" in body["direction_note"] and "负值" in body["direction_note"]
    assert "相对位置上升" in body["direction_note"]

    shuxue = _subject(body, "数学")
    assert shuxue["percentile_change"] == -4.0  # 0.06 − 0.10 = −4 个百分点（上升）
    assert shuxue["from"]["percentile"] == 0.10 and shuxue["to"]["percentile"] == 0.06
    yuwen = _subject(body, "语文")
    assert yuwen["percentile_change"] == -8.0
    yingyu = _subject(body, "英语")
    assert yingyu["percentile_change"] == 6.0  # 正值 = 相对位置下降
    wuli = _subject(body, "物理")
    assert wuli["percentile_change"] == -5.0
    assert wuli["grade_score_change"] == 3.0  # 70 − 67
    # 单位红线：是百分点（−4.0），不是 0–1 分数差（−0.04）
    assert abs(shuxue["percentile_change"]) > 1

    # 主三门名次变化：42 → 30，本次−上次 = −12（负 = 名次数值变小 = 上升）
    assert body["main3"]["from"]["rank"] == 42 and body["main3"]["to"]["rank"] == 30
    assert body["main3"]["from"]["rank_basis"] == "school"
    assert body["main3"]["rank_change"] == -12

    # top_movers：|变化| 前 3 科（8.0 / 6.0 / 5.0；数学 4.0 落榜）
    movers = [(m["subject"], m["percentile_change"], m["direction"]) for m in body["top_movers"]]
    assert movers == [
        ("语文", -8.0, "相对位置上升"),
        ("英语", 6.0, "相对位置下降"),
        ("物理", -5.0, "相对位置上升"),
    ]
    # 禁止因果断言字段：响应键里不允许出现归因/因果类字段
    blob = str(body)
    for banned in ("因为", "由于", "导致", "归因", "原因归"):
        assert banned not in blob


def test_student_homeroom_resolve_year_rank_fallback(client, b3_seed):
    """名次解析复用共享定义：壬一模只有 grade_rank（无学籍名次）→ 取 200；
    rank_change = 205 − 200 = +5（正 = 名次数值变大 = 相对位置下降）。"""
    r = _get(client, STUDENT_URL, person_id=b3_seed.ren_h_id, from_exam=EXAM_FROM, to_exam=EXAM_TO)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["main3"]["from"]["rank"] == 200
    assert body["main3"]["to"]["rank"] == 205
    assert body["main3"]["rank_change"] == 5


def test_student_missing_exam_null_score(client, b3_seed):
    """缺考（§8）：己二模主三门/数学行存在但 score=NULL——不转 0、不残留
    上次值，变化 null + 缺考说明；整体 not_computable。"""
    r = _get(client, STUDENT_URL, person_id=b3_seed.ji_h_id, from_exam=EXAM_FROM, to_exam=EXAM_TO)
    assert r.status_code == 200, r.text
    body = r.json()
    shuxue = _subject(body, "数学")
    assert shuxue["to"]["present"] is True and shuxue["to"]["score"] is None
    assert shuxue["percentile_change"] is None
    assert shuxue["missing_reason"] == "本次考试缺考"
    assert body["main3"]["rank_change"] is None
    assert body["main3"]["missing_reason"] == "本次考试主三门缺考"
    assert body["status"] == "not_computable"
    assert "缺考" in body["missing_reason"]


def test_student_missing_subject_row(client, b3_seed):
    """缺科（§8）：乙二模无数学成绩行 → 该科变化 null + 缺行说明；
    其余科目照常输出。"""
    r = _get(client, STUDENT_URL, person_id=b3_seed.yi_h_id, from_exam=EXAM_FROM, to_exam=EXAM_TO)
    assert r.status_code == 200, r.text
    body = r.json()
    shuxue = _subject(body, "数学")
    assert shuxue["to"]["present"] is False
    assert shuxue["percentile_change"] is None
    assert shuxue["missing_reason"] == "本次考试无此成绩行"
    yuwen = _subject(body, "语文")
    assert yuwen["percentile_change"] == 4.0  # 0.52 − 0.48（下降）
    assert body["main3"]["rank_change"] == 30  # 380 − 350（退步）
    assert body["status"] == "ok"


def test_student_sparse_history(client, b3_seed):
    """稀疏历史（§8）：申仅一模一条数学行、二模无任何行——如实输出缺失，
    绝不残留/编造。"""
    r = _get(client, STUDENT_URL, person_id=b3_seed.shen_h_id, from_exam=EXAM_FROM, to_exam=EXAM_TO)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "not_computable"
    assert [item["subject"] for item in body["subjects"]] == ["数学"]
    shuxue = body["subjects"][0]
    assert shuxue["from"]["present"] is True and shuxue["to"]["present"] is False
    assert shuxue["percentile_change"] is None
    assert shuxue["missing_reason"] == "本次考试无此成绩行"
    assert body["main3"]["rank_change"] is None
    assert "上次考试主三门成绩行缺失" in body["main3"]["missing_reason"]
    assert body["top_movers"] == []


def test_student_same_score_zero_change(client, b3_seed):
    """同分（§8）：辛两场同百分位（0.45→0.45）同名次（200→200）
    → 变化恰为 0，方向为持平（非上升/下降）。"""
    r = _get(client, STUDENT_URL, person_id=b3_seed.xin_h_id, from_exam=EXAM_FROM, to_exam=EXAM_TO)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["main3"]["rank_change"] == 0
    assert body["subjects"][0]["percentile_change"] == 0.0


# ────────────────────── 学生分解：范围隔离与错误契约 ──────────────────────────────


def test_student_teaching_scope_isolation(client, b3_seed):
    """范围隔离（§8）：教学域只读任教学科——响应只有物理，无全科/总分；
    主三门名次 not_computable（绝不跨域取数）。"""
    r = _get(
        client, T_STUDENT_URL,
        person_id=b3_seed.jia_t_id, from_exam=EXAM_FROM, to_exam=EXAM_TO,
        teaching_class_id=b3_seed.t6_id, academic_year_id=b3_seed.ay_id,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["data_domain"] == "teaching"
    assert [item["subject"] for item in body["subjects"]] == ["物理"]
    wuli = body["subjects"][0]
    assert wuli["percentile_change"] == -5.0  # 0.10 − 0.15（上升）
    assert wuli["grade_score_change"] == 3.0
    assert body["main3"]["rank_change"] is None
    assert "教学域" in body["main3"]["missing_reason"]
    assert body["status"] == "ok"


def test_student_errors(client, b3_seed):
    """错误契约：同场 422 / 日期倒挂 422 / 域内不存在 404 / 跨学年 404 /
    他人学生 404 / 缺 person_id 422。"""
    base = dict(from_exam=EXAM_FROM, to_exam=EXAM_TO)

    r = _get(client, STUDENT_URL, person_id=b3_seed.jia_h_id, from_exam=EXAM_TO, to_exam=EXAM_TO)
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_scope_param"

    r = _get(client, STUDENT_URL, person_id=b3_seed.jia_h_id, from_exam=EXAM_TO, to_exam=EXAM_FROM)
    assert r.status_code == 422  # from 晚于 to（两场日期已知）

    r = _get(client, STUDENT_URL, person_id=b3_seed.jia_h_id, **{**base, "to_exam": EXAM_MISSING})
    assert r.status_code == 404
    assert r.json()["error"] == "resource_out_of_scope"

    # 跨学年：EXAM_OLD 属于 2024-2025 学年，当前作用域学年不可见
    r = _get(client, STUDENT_URL, person_id=b3_seed.jia_h_id, **{**base, "from_exam": EXAM_OLD})
    assert r.status_code == 404

    # 秦庚在 H9，不在绑定班 H6 作用域
    r = _get(client, STUDENT_URL, person_id=b3_seed.geng_h_id, **base)
    assert r.status_code == 404

    r = client.get(STUDENT_URL, params={"from_exam": EXAM_FROM, "to_exam": EXAM_TO})
    assert r.status_code == 422  # FastAPI 校验：person_id 必填


# ────────────────────── 班级分解：homeroom 域 ──────────────────────────────


def _group(body, key, bands=False):
    source = body["band_groups"] if bands else body["groups"]
    return next(g for g in source if g["key"] == key)


def test_class_homeroom_comparable_and_excluded(client, b3_seed):
    """可比集合=两场都有效的交集；excluded 缺考/转入/转出显式计数与名单。"""
    r = _get(client, CLASS_URL, from_exam=EXAM_FROM, to_exam=EXAM_TO)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["calc_version"] == "p1-v1"
    assert body["membership_basis"] == "exam"
    assert "交集" in body["comparable_rule"]
    assert "负值" in body["direction_note"]
    assert body["status"] == "ok"
    # 可比：甲乙丙辛壬未（丁转出/戊转入/己缺考/申稀疏均被排除）
    assert body["comparable_n"] == 6
    excluded = body["excluded"]
    assert excluded["missing_exam"]["n"] == 2  # 己（二模缺考）+ 申（二模无行）
    assert excluded["transferred_out"]["n"] == 1  # 丁
    assert excluded["transferred_in"]["n"] == 1  # 戊
    assert excluded["missing_exam"]["n"] + excluded["transferred_in"]["n"] + excluded["transferred_out"]["n"] + body["comparable_n"] == 10


def test_class_homeroom_percentile_bin_groups(client, b3_seed):
    """基期 percentile_bin 分组：p40_60 组满 3 人输出可加指标与名次变化
    描述；小样本组（<3）聚合不输出；基期百分位缺失 → ungrouped。"""
    r = _get(client, CLASS_URL, from_exam=EXAM_FROM, to_exam=EXAM_TO)
    assert r.status_code == 200, r.text
    body = r.json()

    p40 = _group(body, "p40_60")
    assert p40["label"] == "40%-60%"
    assert p40["n"] == 3 and p40["metrics_available"] is True
    assert p40["person_ids"] == sorted([b3_seed.yi_h_id, b3_seed.xin_h_id, b3_seed.ren_h_id])
    # 可加指标：总分变化均值 = (530−520 + 505−500 + 510−500) / 3 = 8.33
    assert p40["total_change_avg"] == 8.33
    assert p40["total_change_missing_reason"] is None
    # 名次类：仅变化描述（rank_change=本次−上次，负=上升），不做贡献分解
    rk = p40["rank_change"]
    assert rk["rank_basis"] == "year_rank"
    assert rk["counted_n"] == 3 and rk["improved_n"] == 0
    assert rk["declined_n"] == 2 and rk["unchanged_n"] == 1  # 乙+30、壬+5、辛 0
    assert rk["median_change"] == 5.0
    assert "不做贡献分解" in rk["note"]

    p20 = _group(body, "p0_20")  # 只有甲 1 人 < 最小样本 3
    assert p20["n"] == 1 and p20["metrics_available"] is False
    assert p20["total_change_avg"] is None
    assert "最小样本" in p20["total_change_missing_reason"]
    assert p20["rank_change"] is None
    assert b3_seed.jia_h_id in p20["person_ids"]  # 下钻名单保留

    assert body["ungrouped"]["person_ids"] == [b3_seed.wei_h_id]  # 基期百分位缺失
    assert "百分位" in body["ungrouped"]["missing_reason"]


def test_class_homeroom_band_groups(client, b3_seed):
    """学校段位分组（band_flags 共享口径）：甲 42→高分段、丙 450→临界段、
    名次不可得/不在段内 → 未落段；段内小样本聚合抑制。"""
    r = _get(client, CLASS_URL, from_exam=EXAM_FROM, to_exam=EXAM_TO)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["band_groups_missing_reason"] is None

    high = _group(body, "high_score", bands=True)
    assert "高分段" in high["label"] and high["n"] == 1
    assert high["metrics_available"] is False

    critical = _group(body, "critical", bands=True)
    assert "临界段" in critical["label"] and critical["n"] == 1

    no_band = _group(body, "no_band", bands=True)
    assert no_band["n"] == 4  # 乙(350)/辛(200)/壬(200)/未(无名次)
    assert no_band["metrics_available"] is True
    assert no_band["total_change_avg"] == 8.75  # (10+5+10+10)/4
    assert no_band["rank_change"]["counted_n"] == 3  # 未无名次不进名次统计
    assert no_band["rank_change"]["declined_n"] == 2 and no_band["rank_change"]["unchanged_n"] == 1


def test_class_homeroom_ties_and_empty_state(client, b3_seed):
    """同分同箱（辛/壬同为 0.45 → 同组）+ 他班考试空可比 200 空态。"""
    r = _get(client, CLASS_URL, from_exam=EXAM_FROM, to_exam=EXAM_TO)
    p40 = _group(r.json(), "p40_60")
    assert b3_seed.xin_h_id in p40["person_ids"] and b3_seed.ren_h_id in p40["person_ids"]

    r = _get(client, CLASS_URL, from_exam=EXAM_FROM, to_exam=EXAM_OTHER_CLASS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "not_computable"
    assert body["comparable_n"] == 0
    assert body["groups"] == [] and body["band_groups"] == []
    assert "没有可比学生交集" in body["missing_reason"]
    # 二模（from 场）在册但「他班考」无可比行：8 人计入缺考；丁已转出、戊转入不计
    assert body["excluded"]["missing_exam"]["n"] == 8
    assert body["excluded"]["transferred_out"]["n"] == 1
    assert body["excluded"]["transferred_in"]["n"] == 1


def test_class_errors(client, b3_seed):
    r = _get(client, CLASS_URL, from_exam=EXAM_TO, to_exam=EXAM_TO)
    assert r.status_code == 422
    r = _get(client, CLASS_URL, from_exam=EXAM_TO, to_exam=EXAM_FROM)
    assert r.status_code == 422
    r = _get(client, CLASS_URL, from_exam=EXAM_MISSING, to_exam=EXAM_TO)
    assert r.status_code == 404
    r = _get(client, CLASS_URL, from_exam=EXAM_OLD, to_exam=EXAM_TO)
    assert r.status_code == 404  # 跨学年考试不可见


# ────────────────────── 班级分解：teaching 域 ──────────────────────────────


def test_class_teaching_additive_and_minrank(client, b3_seed):
    """教学域班级分解：可比 3 人同落 p0_20；可加指标为任教学科分数变化
    均值（无总分，如实注明）；名次描述用组内 min_ranks（同分同名次），
    丁T 二模 84 与乙T 同分 → 同名次。"""
    r = _get(
        client, T_CLASS_URL, from_exam=EXAM_FROM, to_exam=EXAM_TO,
        teaching_class_id=b3_seed.t6_id, academic_year_id=b3_seed.ay_id,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["data_domain"] == "teaching"
    assert body["comparable_n"] == 3
    assert body["membership_basis"] == "exam"
    assert body["band_groups"] == []
    assert "教学域" in body["band_groups_missing_reason"]

    p20 = _group(body, "p0_20")
    assert p20["n"] == 3 and p20["metrics_available"] is True
    assert p20["total_change_avg"] is None
    assert "教学域" in p20["total_change_missing_reason"]
    # 分数变化均值 = (82−90 + 84−84 + 84−55) / 3 = 7.0
    assert p20["score_change_avg"] == 7.0
    # 组内 min_ranks：一模 1/2/3，二模 乙T=1、丁T=1（同分同名次）、甲T=3
    rk = p20["rank_change"]
    assert rk["rank_basis"] == "class_min_rank"
    assert rk["improved_n"] == 2 and rk["declined_n"] == 1 and rk["unchanged_n"] == 0
    assert rk["median_change"] == -1.0


# ────────────────────── 服务函数直调（scope 快照兼容） ──────────────────────────────


def test_service_accepts_scope_snapshot_dict(b3_seed):
    """服务函数接受 WorkspaceContext 或 resolve_scope_snapshot 快照 dict
    （同源机制两种载体）；两种载体结果逐字段一致。"""
    from app.api.chat_tools import resolve_scope_snapshot
    from app.core.context import resolve_workspace_context
    from app.db.models import SessionLocal
    from app.diagnosis.changes import student_change_decomposition

    db = SessionLocal()
    try:
        snapshot = resolve_scope_snapshot(db, 1, "homeroom", academic_year_id=b3_seed.ay_id)
        via_snapshot = student_change_decomposition(
            db, snapshot, b3_seed.jia_h_id, EXAM_FROM, EXAM_TO
        )
        ctx = resolve_workspace_context(db, 1, "homeroom", {"academic_year_id": b3_seed.ay_id})
        via_context = student_change_decomposition(
            db, ctx, b3_seed.jia_h_id, EXAM_FROM, EXAM_TO
        )
        assert via_snapshot == via_context
        assert via_context["main3"]["rank_change"] == -12
    finally:
        db.close()
