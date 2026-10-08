"""P2-C2 教师行动首页测试（契约 docs/diagnosis-roadmap/p2-contracts.md §3/§6）。

在 tests/v1/conftest.py 合成样本（v1_seed）之上 ORM 直种行动首页场景：
- 排序（契约 §3 冻结）：综合风险 > 持续下滑 > 临界下滑 > 作业风险（次标签）
  > 短期下滑；同级按 evidence 数、再按最近名次变化幅度 |rank_change|；
- 摘要同源：priority/types/sections 全部由 B1 class_features + B2
  classify_student 派生（与单生 features/types 端点逐字段一致），本模块
  断言理由中的数字 = B1 端点输出值（不另算第二口径）；
- follow_ups 只读既有字段：open_n = Σ B1 每生 open_follow_ups；
  due_this_week = 未关闭跟进中档案日期落在「本周」（as_of-6 天起、含当日，
  沿用 analysis.py weekly-focus 约定）的条数，已关闭不计、绝不依赖 C4 新列；
- 缺失纪律：数据不足且无作业风险的学生不进优先关注（不凑数）；教学域
  无总分/名次 → 结构计数如实为 0；T8 无作业无档案 → 全零空态；
- 服务层学年错配 422；显式未知学年 404。
样本全部合成（秦庚/秦恒/秦辛/秦午/秦未/秦申 及 v1_seed 既有秦甲/秦乙/秦丙；
教学域沿用 甲·T/丁·T/戊·T/己·T）。
"""

import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

API = "/api/v1"

EXAM_MID = "2025期中"  # v1_seed 原有（2025-11-06）
EXAM_YIMO = "2026一模"  # 本模块补种（today-40）
EXAM_ERMO = "2026二模"  # 本模块补种（today-10）


@pytest.fixture(scope="module")
def p2c2_seed(v1_seed):
    """在 v1_seed 基础上补种行动首页场景（ORM 直种，不走导入链路）。

    班主任域 H6 新增六名合成学生，各自定向命中 B2 规则（阈值语义在 B2，
    本处只构造满足其输入的 B1 字段）：
    - 秦庚：薄弱段（560≥501）+ 作业风险（30 天缺交 3）→ 命中 ≥2 风险面
      （综合风险型次标签）；另 direction=退步/streak=1 → 短期下滑主类型。
      排序键 = 综合风险（最优先）。
    - 秦恒：名次 100→150→210 连退 2 次 → 持续下滑主类型（非临界段）。
    - 秦辛：临界段（460∈[400,500]）+ 退步、streak=1 → 短期下滑主类型 +
      临界下滑次标签；排序键 = 临界下滑。
    - 秦午：退步 streak=1（短期下滑主）+ 作业风险（30 天缺交 4）次标签；
      排序键 = 作业风险；evidence 2 条、|rank_change|=30。
    - 秦未：仅 1 场（insufficient_data）+ 作业风险次标签；排序键 = 作业
      风险；evidence 2 条、无名次变化（幅度 0）→ 同级排在秦午之后。
    - 秦申：120→70 进步（improving 样本）、高分段但稳定性数据不足 →
      无任何类型命中，不进优先关注。
    作业（H6 数学线，缺行=默认已交）：庚 d1..d3 缺、午 d1..d4 缺、
    未 d1..d3 缺，其余成员无例外行。
    档案：庚 d20 未关闭 + d40 已关闭；午 d0（今日）未关闭；未 d2 未关闭
    + d1 已关闭（本周已关闭不进 due_this_week）。
    """
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    s = v1_seed
    db = SessionLocal()
    today = date.today()

    def d(days_ago: int) -> date:
        return today - timedelta(days=days_ago)

    def fact(domain, ay_id, cls_id, ident_id, exam, exam_date, subject=None,
             total_type=None, score=None, pct=None, xueji=None, grade_rank=None):
        f = wm.ScoreFact()
        f.data_domain = domain
        f.academic_year_id = ay_id
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
        f.source = "p2-c2-test"
        db.add(f)
        return f

    def main3(ident, exam, exam_date, rank, pct):
        fact("homeroom", s.ay_id, s.h6_id, ident, exam, exam_date, None, "主三门",
             200.0, pct=pct, xueji=rank, grade_rank=rank + 5)

    def student(name, seat):
        ident = wm.WsStudentIdentity(data_domain="homeroom", display_name=name)
        db.add(ident)
        db.flush()
        db.add(wm.Enrollment(
            admin_class_id=s.h6_id, identity_id=ident.id, status="active",
            valid_from=date(2025, 9, 1), seat_no=seat,
        ))
        return ident.id

    geng = student("秦庚", 6)
    heng = student("秦恒", 7)
    xin = student("秦辛", 8)
    wu = student("秦午", 9)
    wei = student("秦未", 10)
    shen = student("秦申", 11)

    # 秦庚：薄弱段 + 作业风险 → 综合风险面 ×2；近期退步未成连击
    main3(geng, EXAM_YIMO, d(40), 520, 0.70)
    main3(geng, EXAM_ERMO, d(10), 560, 0.74)
    # 秦恒：连续退步 2 次（100→150→210）
    main3(heng, EXAM_MID, date(2025, 11, 6), 100, 0.10)
    main3(heng, EXAM_YIMO, d(40), 150, 0.16)
    main3(heng, EXAM_ERMO, d(10), 210, 0.22)
    # 秦辛：临界段 + 退步 streak=1 → 临界下滑次标签
    main3(xin, EXAM_MID, date(2025, 11, 6), 420, 0.62)
    main3(xin, EXAM_YIMO, d(40), 410, 0.60)
    main3(xin, EXAM_ERMO, d(10), 460, 0.66)
    # 秦午：短期下滑 + 作业风险（次标签，排序键）
    main3(wu, EXAM_MID, date(2025, 11, 6), 300, 0.48)
    main3(wu, EXAM_ERMO, d(10), 330, 0.52)
    # 秦未：insufficient（仅 1 场）+ 作业风险次标签
    main3(wei, EXAM_ERMO, d(10), 300, 0.48)
    # 秦申：进步 + 高分段（稳定性数据不足 → 无类型命中）
    main3(shen, EXAM_MID, date(2025, 11, 6), 120, 0.14)
    main3(shen, EXAM_ERMO, d(10), 70, 0.08)

    # ── 作业（H6 数学线）：缺行=默认已交 ──
    expected_h6 = list(s.h_person_ids) + [geng, heng, xin, wu, wei, shen]

    def assignment(on_date, token):
        db.add(wm.HomeworkAssignment(
            data_domain="homeroom", class_ref_id=s.h6_id, academic_year_id=s.ay_id,
            subject="数学", homework_type="练习册", assigned_date=on_date,
            batch_token=token, expected_members_json=json.dumps(expected_h6),
            status="active",
        ))

    for i in (1, 2, 3, 4):
        assignment(d(i), f"p2c2-m{i}")
    db.flush()
    rows = (
        db.query(wm.HomeworkAssignment)
        .filter(wm.HomeworkAssignment.batch_token.like("p2c2-m%"))
        .all()
    )
    by_day = {row.assigned_date: row for row in rows}
    for pid in (geng, wei):  # d1..d3 缺（d4 无行=默认已交 → 连缺 3 天）
        for i in (1, 2, 3):
            db.add(wm.HomeworkSubmission(
                assignment_id=by_day[d(i)].id, person_id=pid, submission_status="missing",
            ))
    for i in (1, 2, 3, 4):  # 午：d1..d4 连缺 4 天
        db.add(wm.HomeworkSubmission(
            assignment_id=by_day[d(i)].id, person_id=wu, submission_status="missing",
        ))

    # ── 档案（homeroom 域，follow_up 既有字段） ──
    def note(pid, on_date, category, content, follow_up=None, done=0):
        db.add(wm.WsStudentNote(
            data_domain="homeroom", person_id=pid, date=on_date,
            category=category, content=content, follow_up=follow_up,
            follow_up_done=done, source=None,
        ))

    note(geng, d(20), "谈话", "作业连续缺交，已谈", follow_up="每周检查作业本", done=0)
    note(geng, d(40), "家访", "暑期状态回访", follow_up="已电话确认", done=1)
    note(wu, d(0), "谈话", "今晨未交作业", follow_up="明早补交数学", done=0)
    note(wei, d(2), "谈话", "三科作业均拖欠", follow_up="本周补齐", done=0)
    note(wei, d(1), "谈话", "数学已补", follow_up="当日完成", done=1)

    # ── 教学（T6 物理）：甲·T 连缺 2 天 + 1 条本周未关闭跟进 ──
    expected_t6 = list(s.t6_person_ids)
    for i in (1, 2):
        db.add(wm.HomeworkAssignment(
            data_domain="teaching", class_ref_id=s.t6_id, academic_year_id=s.ay_id,
            subject="物理", homework_type="练习册", assigned_date=d(i),
            batch_token=f"p2c2-t6-p{i}", expected_members_json=json.dumps(expected_t6),
            status="active",
        ))
    db.flush()
    t6_rows = {
        row.batch_token: row
        for row in db.query(wm.HomeworkAssignment)
        .filter(wm.HomeworkAssignment.batch_token.like("p2c2-t6-p%"))
        .all()
    }
    for i in (1, 2):
        db.add(wm.HomeworkSubmission(
            assignment_id=t6_rows[f"p2c2-t6-p{i}"].id, person_id=s.jia_t_id,
            submission_status="missing",
        ))
    db.add(wm.WsStudentNote(
        data_domain="teaching", person_id=s.jia_t_id, date=d(3), category="谈话",
        content="物理作业连缺", follow_up="当面提醒", follow_up_done=0, source=None,
    ))

    db.commit()
    yield SimpleNamespace(
        seed=s,
        geng_id=geng,
        heng_id=heng,
        xin_id=xin,
        wu_id=wu,
        wei_id=wei,
        shen_id=shen,
        today=today,
    )
    db.close()


def _get(client, path, **params):
    return client.get(f"{API}{path}", params=params)


# ────────────── 响应形状与优先关注排序 ──────────────


def test_action_summary_shape_and_priority_order(client, p2c2_seed):
    """契约 §3 冻结形状 + 排序：综合风险 > 持续下滑 > 临界下滑 > 作业风险
    （次标签，同级按 |rank_change| 大者在前）> 短期下滑。"""
    s = p2c2_seed.seed
    resp = _get(client, "/homeroom/diagnosis/action-summary", academic_year_id=s.ay_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert set(body) == {"calc_version", "as_of", "priority_persons", "class_types", "sections"}
    assert body["calc_version"] == "p2-v1"
    assert body["as_of"] == p2c2_seed.today.isoformat()
    assert set(body["sections"]) == {"trend_changes", "structure", "homework", "follow_ups"}

    persons = body["priority_persons"]
    # 秦庚(综合) > 秦恒(持续) > 秦辛(临界) > 秦午(作业, 幅度30) > 秦未(作业, 幅度0)
    assert [p["person_id"] for p in persons] == [
        p2c2_seed.geng_id, p2c2_seed.heng_id, p2c2_seed.xin_id,
        p2c2_seed.wu_id, p2c2_seed.wei_id,
    ]
    for entry in persons:
        # name 为契约形状的追加键（前端「姓名+理由+直达学生页」所需）
        assert set(entry) == {"person_id", "name", "reasons", "evidence_ref"}
        assert entry["evidence_ref"] == {"types": True, "features": True}
        assert entry["reasons"], "每条优先关注必须有理由（可追溯）"

    by_id = {p["person_id"]: p for p in persons}
    assert by_id[p2c2_seed.geng_id]["name"] == "秦庚"
    assert by_id[p2c2_seed.geng_id]["reasons"] == [
        "综合风险型", "短期下滑型", "近 30 天缺交 3 次", "连续缺交 3 天",
    ]
    assert by_id[p2c2_seed.heng_id]["reasons"] == ["持续下滑型", "连续退步 2 次"]
    assert by_id[p2c2_seed.xin_id]["reasons"] == ["临界下滑型", "短期下滑型"]
    assert by_id[p2c2_seed.wu_id]["reasons"] == [
        "短期下滑型", "近 30 天缺交 4 次", "连续缺交 4 天",
    ]
    assert by_id[p2c2_seed.wei_id]["reasons"] == [
        "近 30 天缺交 3 次", "连续缺交 3 天",
    ]
    # 数据不足且无作业风险的学生（秦甲/乙/丙/申）不进优先关注
    assert p2c2_seed.shen_id not in by_id
    assert s.jia_h_id not in by_id


def test_action_summary_sections(client, p2c2_seed):
    """四段摘要：趋势/结构/作业/待办（B1 输出的直接计数）。"""
    s = p2c2_seed.seed
    body = _get(client, "/homeroom/diagnosis/action-summary",
                academic_year_id=s.ay_id).json()
    sections = body["sections"]

    # 趋势：秦申 进步；庚/恒/辛/午 退步；甲乙丙/未 数据不足（不进桶）
    assert sections["trend_changes"] == {"improving_n": 1, "declining_n": 4}
    # 结构：最近一场段位——秦申 高分段(70)、秦辛 临界(460)、秦庚 薄弱(560)
    assert sections["structure"] == {
        "band_counts": {"high_score": 1, "critical": 1, "weak": 1}
    }
    # 作业：庚/午/未 三人命中作业风险型（B2 判定）；缺交合计 3+4+3
    assert sections["homework"] == {"risk_n": 3, "missing_30d_total": 10, "stats_excluded_n": 0}
    # 待办：未关闭 庚1+午1+未1=3；本周(今日-6起含当日) 午(d0)+未(d2)=2，
    # 庚(d20) 出窗、已关闭两条不计
    assert sections["follow_ups"] == {"open_n": 3, "due_this_week": 2}


def test_action_summary_homogeneous_with_b1_b2(client, p2c2_seed):
    """同源红线：理由数字 = B1 单生 features 端点输出；类型 = B2 types 端点
    判定（摘要绝不另算第二口径）。"""
    s = p2c2_seed.seed
    body = _get(client, "/homeroom/diagnosis/action-summary",
                academic_year_id=s.ay_id).json()
    geng = next(
        p for p in body["priority_persons"] if p["person_id"] == p2c2_seed.geng_id
    )

    features = _get(client, "/homeroom/diagnosis/features",
                    person_id=p2c2_seed.geng_id, academic_year_id=s.ay_id).json()
    hw = features["indicators"]["homework_behavior"]
    assert hw["missing_30d"] == 3
    assert hw["current_streak_days"] == 3
    assert "近 30 天缺交 3 次" in geng["reasons"]
    assert "连续缺交 3 天" in geng["reasons"]
    # 薄弱段出自同一 features 输出（structure 计数的依据）
    assert features["indicators"]["current_level"]["bands"]["weak"] is True

    types = _get(client, "/homeroom/diagnosis/types",
                 person_id=p2c2_seed.geng_id, academic_year_id=s.ay_id).json()
    class_types = {row["person_id"]: row["types"] for row in body["class_types"]}
    assert len(class_types) == len(body["class_types"])
    assert class_types[p2c2_seed.geng_id] == types
    assert "综合风险型" in ([types["main_type"]] + types["secondary_tags"])
    assert "综合风险型" in geng["reasons"]


# ────────────── 作用域隔离与空态 ──────────────


def test_teaching_domain_summary(client, p2c2_seed):
    """教学域：仅任教学科——无总分/名次 → 结构计数如实全 0；作业风险与
    待办仍按本域口径输出（甲·T 连缺 2 天 + 本周未关闭跟进）。"""
    s = p2c2_seed.seed
    resp = _get(client, "/teaching/diagnosis/action-summary", academic_year_id=s.ay_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["calc_version"] == "p2-v1"
    assert body["sections"]["structure"] == {
        "band_counts": {"high_score": 0, "critical": 0, "weak": 0}
    }
    assert body["sections"]["trend_changes"] == {"improving_n": 0, "declining_n": 0}
    assert body["sections"]["homework"] == {"risk_n": 1, "missing_30d_total": 2, "stats_excluded_n": 0}
    assert body["sections"]["follow_ups"] == {"open_n": 1, "due_this_week": 1}

    persons = body["priority_persons"]
    assert [p["person_id"] for p in persons] == [s.jia_t_id]
    assert persons[0]["name"] == "秦甲·T"
    assert persons[0]["reasons"] == ["连续缺交 2 天"]  # 30 天缺交 2 < 3 → 只报连缺
    assert persons[0]["evidence_ref"] == {"types": True, "features": True}


def test_teaching_empty_scope_all_zero(client, p2c2_seed):
    """T8（戊·T/己·T）：无作业无档案无类型命中 → 优先关注空、四摘要全零
    （空态如实输出 0/空列表，不编造）。"""
    s = p2c2_seed.seed
    resp = _get(client, "/teaching/diagnosis/action-summary",
                teaching_class_id=s.t8_id, academic_year_id=s.ay_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["priority_persons"] == []
    assert body["sections"] == {
        "trend_changes": {"improving_n": 0, "declining_n": 0},
        "structure": {"band_counts": {"high_score": 0, "critical": 0, "weak": 0}},
        "homework": {"risk_n": 0, "missing_30d_total": 0, "stats_excluded_n": 0},
        "follow_ups": {"open_n": 0, "due_this_week": 0},
    }


def test_scope_guards(client, p2c2_seed):
    """显式未知学年 404；服务层学年错配 422（InvalidScopeParam）。"""
    s = p2c2_seed.seed
    assert _get(client, "/homeroom/diagnosis/action-summary",
                academic_year_id=999999).status_code == 404
    assert _get(client, "/teaching/diagnosis/action-summary",
                academic_year_id=999999).status_code == 404

    from app.core.context import resolve_workspace_context
    from app.core.errors import InvalidScopeParam
    from app.db.models import SessionLocal
    from app.diagnosis.action import action_summary

    db = SessionLocal()
    try:
        ctx = resolve_workspace_context(db, 1, "homeroom", {"academic_year_id": s.ay_id})
        with pytest.raises(InvalidScopeParam):
            action_summary(db, ctx, 999999)
        # 服务函数与端点同源：同一 ctx 直调输出与 HTTP 响应逐字段一致
        http = _get(client, "/homeroom/diagnosis/action-summary",
                    academic_year_id=s.ay_id).json()
        assert action_summary(db, ctx, s.ay_id) == http
    finally:
        db.close()
