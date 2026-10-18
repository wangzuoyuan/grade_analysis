"""2026-10-18 审查修复 12：诊断体系在缺考学生与任课视角上的口径缺陷。

四子项（核实说明 §12 / remediation plan 第 12 行）：
- 12.1 全缺考被判有效考试：valid_exam_count 只计有有效成绩证据的考试；
  两场全缺考 → classify_student 进 insufficient_data（沿用既有结构与措辞）。
- 12.2 教学域总分口径不适用：教学独立来源只存任教学科、绝无总分行，
  段位/趋势/稳定性/偏科等总分依赖指标明确标 not_computable
  （teaching_no_total），学生类型/action 首页相应标不可计算；
  **班主任域行为完全不变**（sections 无增量键、仍按总分口径判型）。
  绝不为凑指标放开教学侧读总分/全科。
- 12.3 多学科教学路由漏 subject 参数：changes/attention/correlation 的
  教学端点补 Optional subject 并透传（多学科教师传 subject 不再 422）。
- 12.4 旧相关性接口拒绝共享考试：homeroom 分支补 ctx.link_id 的对侧域
  可读考试判定（与 changes._ensure_exams_readable 同一规则；存在性门
  放行后实际取数仍走 readable_facts 统一权限门，绝不跳过权限检查）。

仅使用独立合成数据与合成姓名（秦甲/秦乙等，tests/v1/conftest.v1_seed）。
"""

from datetime import date

import pytest


def _add_event(db, s, name, grade=2):
    from app.db import workspace_models as wm

    event = wm.WorkspaceExamEvent(academic_year_id=s.ay_id, grade=grade, exam_name=name)
    db.add(event)
    db.flush()
    return event


def _fact(db, s, *, domain, class_id, person_id, name, event_id, day,
          subject=None, total_type=None, score=None, rank=None, pct=None):
    from app.db import workspace_models as wm

    db.add(
        wm.ScoreFact(
            data_domain=domain, academic_year_id=s.ay_id, class_ref_id=class_id,
            identity_id=person_id, exam_name=name, exam_event_id=event_id,
            exam_date=day, subject=subject, total_type=total_type, score=score,
            xueji_rank=rank, grade_percentile=pct, source="fix12-test",
        )
    )


@pytest.fixture(scope="module")
def seed12(client, v1_seed):
    """module 级合成补充（v1_seed 之上，只种一次）：
    - 全缺考生 秦全缺（H6 新成员）：两场事件考试全部 score=None；
    - 教学域第二场物理事件考（12.2 凑足教学侧有效考试数）；
    - 化学教学班（12.3 多学科样本）；
    - 教学侧独有考试「教学侧投影考」（12.4 班主任经 link 可读）。"""
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    s = v1_seed
    db = SessionLocal()

    # 12.1 全缺考生
    absent = wm.WsStudentIdentity(data_domain="homeroom", display_name="秦全缺")
    db.add(absent)
    db.flush()
    db.add(wm.Enrollment(
        admin_class_id=s.h6_id, identity_id=absent.id, status="active",
        valid_from=date(2025, 9, 1), seat_no=9,
    ))
    for name, day in (("合成缺考一", date(2026, 1, 10)), ("合成缺考二", date(2026, 2, 11))):
        ev = _add_event(db, s, name)
        _fact(db, s, domain="homeroom", class_id=s.h6_id, person_id=absent.id,
              name=name, event_id=ev.id, day=day,
              subject="总分", total_type="主三门", score=None)

    # 12.2 教学域第二场物理（jia_t 已有 E1 物理 91 分，再补一场凑足 2 场）
    ev_t = _add_event(db, s, "教学期中考")
    _fact(db, s, domain="teaching", class_id=s.t6_id, person_id=s.jia_t_id,
          name="教学期中考", event_id=ev_t.id, day=date(2025, 12, 1),
          subject="物理", total_type=None, score=88.0)
    # 班主任域对照：甲·H 两场有效主三门 → 可正常判型
    for name, day, rank in (("班主任一考", date(2025, 12, 1), 200),
                            ("班主任二考", date(2026, 1, 5), 150)):
        ev_h = _add_event(db, s, name)
        _fact(db, s, domain="homeroom", class_id=s.h6_id, person_id=s.jia_h_id,
              name=name, event_id=ev_h.id, day=day,
              subject="总分", total_type="主三门", score=250.0,
              rank=rank, pct=0.3)

    # 12.3 多学科：同学年再加化学教学班
    db.add(wm.TeachingClass(
        academic_year_id=s.ay_id, subject="化学", label="高二合成化学班", grade=2,
    ))

    # 12.4 教学侧独有考试（H6 域完全无同名事实）
    ev_only = _add_event(db, s, "教学侧投影考")
    _fact(db, s, domain="teaching", class_id=s.t6_id, person_id=s.jia_t_id,
          name="教学侧投影考", event_id=ev_only.id, day=date(2026, 2, 20),
          subject="物理", total_type=None, score=90.0)

    db.commit()
    db.close()
    yield s


def _hctx(db, s):
    from app.core.context import resolve_workspace_context

    return resolve_workspace_context(
        db, 1, "homeroom", {"academic_year_id": s.ay_id, "class_id": s.h6_id}
    )


def _tctx(db, s, subject="物理"):
    """教学域上下文：seed12 已造出物理+化学双学科，必须显式 subject（与
    12.3 修复后的前端行为一致）。"""
    from app.api.students_mgmt import _teaching_ctx

    return _teaching_ctx(db, s.ay_id, None, None, subject)


# ── 12.1 全缺考 → insufficient_data ────────────────────────────────────────


def test_all_absent_student_is_insufficient_data(client, seed12):
    """两场考试全部 score=None：修复前 valid_exam_count=len(ordered)=2 且
    types 优先读 data_quality → classification_status=classified（误报）；
    修复后有效考试数=0 → insufficient_data（沿用既有结构与措辞）。"""
    s = seed12
    from app.db.models import SessionLocal
    from app.db import workspace_models as wm
    from app.diagnosis.features import student_features
    from app.diagnosis.types import classify_student

    db = SessionLocal()
    absent = (
        db.query(wm.WsStudentIdentity)
        .filter_by(data_domain="homeroom", display_name="秦全缺")
        .one()
    )
    ctx = _hctx(db, s)
    f = student_features(db, ctx, absent.id, s.ay_id)
    assert f["data_quality"]["valid_exam_count"] == 0  # 修复前为 2
    assert any("只有缺考登记" in note for note in f["data_quality"]["notes"])
    types = classify_student(f)
    assert types["classification_status"] == "insufficient_data"  # 修复前 classified
    assert types["main_type"] is None
    assert any(e["type"] == "数据不足" and "不足 2 场" in e["basis"] for e in types["evidence"])
    db.close()


def test_partially_absent_counts_only_valid_exams(client, seed12):
    """甲·H（期中+两场班主任事件考有效）：缺考场不计入，但有效场照常计数
    ——既有「有行即有效」的普通学生不受 12.1 影响（缺考不转 0）。"""
    s = seed12
    from app.db.models import SessionLocal
    from app.diagnosis.features import student_features

    db = SessionLocal()
    ctx = _hctx(db, s)
    f = student_features(db, ctx, s.jia_h_id, s.ay_id)
    assert f["data_quality"]["valid_exam_count"] >= 2
    db.close()


# ── 12.2 教学域总分依赖指标 not_computable；班主任域不变 ──────────────────


def test_teaching_total_based_indicators_not_computable(client, seed12):
    """教学域（jia_t 两场有效物理）：trend/stability/imbalance/段位主三门
    全部标 teaching_no_total（口径不适用，不是「数据没导」）；学生类型
    not_computable；绝不为凑指标读总分/全科。"""
    s = seed12
    from app.db.models import SessionLocal
    from app.diagnosis.features import student_features
    from app.diagnosis.types import classify_student

    db = SessionLocal()
    ctx = _tctx(db, s)
    f = student_features(db, ctx, s.jia_t_id, s.ay_id)
    ind = f["indicators"]
    for name in ("trend", "stability", "imbalance"):
        assert ind[name]["status"] == "not_computable"
        assert ind[name]["missing_reason"] == "teaching_no_total"
    assert ind["current_level"]["main3"]["missing_reason"] == "teaching_no_total"
    assert any("教学域只存任教学科" in note for note in f["data_quality"]["notes"])
    # 单科事实照常可读（subjects 是教学域合法数据，不受口径门影响）
    assert ind["current_level"]["subjects"]

    types = classify_student(f)
    assert types["classification_status"] == "not_computable"
    assert types["main_type"] is None  # 无作业数据时不判作业风险
    assert any(e["type"] == "类型不可计算" for e in types["evidence"])
    db.close()


def test_homeroom_indicators_unchanged_by_caliber_gate(client, seed12):
    """班主任域（甲·H 两场有效主三门）：判型照常 classified、无
    teaching_no_total、action sections 不带增量状态键——12.2 只动教学域。"""
    s = seed12
    from app.db.models import SessionLocal
    from app.diagnosis.action import action_summary
    from app.diagnosis.features import student_features
    from app.diagnosis.types import classify_student

    db = SessionLocal()
    ctx = _hctx(db, s)
    f = student_features(db, ctx, s.jia_h_id, s.ay_id)
    ind = f["indicators"]
    assert ind["trend"]["status"] == "ok"
    assert ind["trend"]["missing_reason"] is None
    # 班主任域永远不出现 teaching_no_total（jia 最近一场是经 link 投影的
    # 教学侧单科考试，main3 缺行是既有投影行为，与 12.2 无关）
    assert ind["current_level"]["main3"]["missing_reason"] != "teaching_no_total"
    types = classify_student(f)
    assert types["classification_status"] == "classified"

    summary = action_summary(db, ctx, ctx.academic_year_id)
    # 班主任域 sections 保持既有键集（不携带教学域的 status/missing_reason）
    assert set(summary["sections"]["structure"]) == {"band_counts"}
    assert set(summary["sections"]["trend_changes"]) == {"improving_n", "declining_n"}
    db.close()


def test_teaching_action_summary_marks_not_computable(client, seed12):
    """教学域 action 首页：结构/趋势摘要标注 not_computable + teaching_no_total
    （0 不是「无人进退步/无临界」，是口径不适用）；作业/待办摘要不受影响。"""
    s = seed12
    from app.db.models import SessionLocal
    from app.api.students_mgmt import _teaching_ctx
    from app.diagnosis.action import action_summary

    db = SessionLocal()
    ctx = _teaching_ctx(db, s.ay_id, s.t6_id, None, "物理")
    summary = action_summary(db, ctx, ctx.academic_year_id)
    for section in ("structure", "trend_changes"):
        assert summary["sections"][section]["status"] == "not_computable"
        assert summary["sections"][section]["missing_reason"] == "teaching_no_total"
    assert "homework" in summary["sections"] and "follow_ups" in summary["sections"]
    db.close()


# ── 12.3 多学科教学路由补 subject 参数 ────────────────────────────────────


def test_multi_subject_teaching_endpoints_accept_subject(client, seed12):
    """同学年存在物理+化学两个教学班：不传 subject → 422（多学科必须显式）；
    传 subject=物理 → homework-students / changes / attention 全部 200
    （修复前这三个路由写死 subject=None，多学科必 422）。"""
    s = seed12
    base = {"academic_year_id": s.ay_id, "subject": "物理"}

    # 修复前：422 subject is required when multiple teaching subjects exist
    r = client.get("/api/v1/teaching/diagnosis/homework-students",
                   params={"academic_year_id": s.ay_id})
    assert r.status_code == 422
    assert "subject" in r.text or "学科" in r.text

    r = client.get("/api/v1/teaching/diagnosis/homework-students", params={**base})
    assert r.status_code == 200, r.text

    r = client.get("/api/v1/teaching/diagnosis/attention", params={**base})
    assert r.status_code == 200, r.text

    # 变化分解（班级/单生）：from=2025期中 → to=教学期中考（物理域两场）
    changes = {
        "from_exam": "2025期中", "to_exam": "教学期中考",
        "academic_year_id": s.ay_id, "subject": "物理",
    }
    r = client.get("/api/v1/teaching/diagnosis/changes/class", params=changes)
    assert r.status_code == 200, r.text
    r = client.get("/api/v1/teaching/diagnosis/changes/student",
                   params={**changes, "person_id": s.jia_t_id})
    assert r.status_code == 200, r.text
    # 不传 subject 的变化分解同维持 422（多学科必须显式）
    r = client.get("/api/v1/teaching/diagnosis/changes/class",
                   params={"from_exam": "2025期中", "to_exam": "教学期中考",
                           "academic_year_id": s.ay_id})
    assert r.status_code == 422


# ── 12.4 班主任可对任课侧投影考试做相关性分析 ─────────────────────────────


def test_homeroom_correlation_accepts_link_projected_exam(client, seed12):
    """教学侧独有考试「教学侧投影考」（H6 域无同名事实）：修复前
    _ensure_exam_readable 班主任分支只查本域 → 404；修复后经生效 link 的
    对侧域存在性门放行（实际取数仍走 readable_facts 统一权限门）。"""
    s = seed12
    params = {"exam_name": "教学侧投影考", "academic_year_id": s.ay_id,
              "class_id": s.h6_id, "window_days": 14}
    r = client.get("/api/v1/homeroom/diagnosis/correlation", params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    # Y=主三门分数：该考试教学侧只有物理行（经五条件门投影为单科行），
    # 无总分行 → 如实 not_computable（no_valid_exam_score），绝不被 404 挡在门外
    assert body["status"] == "not_computable"
    assert body["missing_reason"] == "no_valid_exam_score"

    # 真正不存在的考试仍然 404（存在性门不因 12.4 放松）
    r = client.get("/api/v1/homeroom/diagnosis/correlation",
                   params={**params, "exam_name": "哪都不存在的考试"})
    assert r.status_code == 404
