"""2026-10-18 审查修复 3：诊断域考试标识 exam_key/exam_name 混用（后端部分）。

复现基线（probe.py 3A/3B）：WorkspaceExamEvent 事实进入诊断后，
``_group_exams`` 的分组 key 是 ``event:<id>``，而报告/复查/回看仍把 key 当
人类可读考试名使用——传真实考试名 422、不传名输出 ``event:123``、锚点
截断拿 key 与名字做字典序比较误删锚点考试本身、时间线恒空、偏科回看
恒「暂不可比」。

新契约（docs/contracts/exam-events.md「读取」）：
- 对外 exam_name 字段一律人类可读考试名，另附稳定 exam_key（无事件事实
  exam_key==exam_name）；
- 入参（exam_name/anchor_exam/from_exam/to_exam）双收 exam_key 与唯一
  考试名，解析到同一分组 bucket；报错 available 清单「名称 (key)」双标；
- 时间线截断先归一到 key 再比较，锚点本身保留；
- 回看内部过滤一律按 key。

仅使用独立合成数据与合成姓名（秦甲/秦乙等，tests/v1/conftest.v1_seed）。
"""

from datetime import date

import pytest


def _add_event_exam(db, s, name, day, *, rank, pct, physics_pct=None, person_id=None):
    """造一场事件事实考试：主三门总分行（+可选物理单科行），返回 event_id。"""
    from app.db import workspace_models as wm

    event = wm.WorkspaceExamEvent(
        academic_year_id=s.ay_id, grade=2, exam_name=name
    )
    db.add(event)
    db.flush()
    pid = person_id if person_id is not None else s.jia_h_id
    db.add(
        wm.ScoreFact(
            data_domain="homeroom", academic_year_id=s.ay_id, class_ref_id=s.h6_id,
            identity_id=pid, exam_name=name, exam_event_id=event.id, exam_date=day,
            subject="总分", total_type="主三门", score=240.0 + rank % 7,
            xueji_rank=rank, grade_percentile=pct, source="fix03-test",
        )
    )
    if physics_pct is not None:
        db.add(
            wm.ScoreFact(
                data_domain="homeroom", academic_year_id=s.ay_id, class_ref_id=s.h6_id,
                identity_id=pid, exam_name=name, exam_event_id=event.id, exam_date=day,
                subject="物理", total_type=None, score=60.0,
                grade_percentile=physics_pct, source="fix03-test",
            )
        )
    db.flush()
    return event.id


@pytest.fixture(scope="module")
def event_seed(client, v1_seed):
    """两场事件考试（12 月 rank 300 pct .5 / 1 月 rank 100 pct .5），
    主三门百分位 0.05（年级前 5%）、物理单科 0.5（差 45 个百分点）→ 连续 2 场弱于总体 = 明显偏科。

    module 级：v1_seed 同为模块级，重复执行会造出同名多场事件（名称定位
    按契约必须拒绝），故整模块只种一次、用例只读。"""
    from app.db.models import SessionLocal

    s = v1_seed
    db = SessionLocal()
    e1 = _add_event_exam(db, s, "12月合成考", date(2025, 12, 5),
                         rank=300, pct=0.05, physics_pct=0.5)
    e2 = _add_event_exam(db, s, "1月合成考", date(2026, 1, 10),
                         rank=100, pct=0.05, physics_pct=0.5)
    db.commit()
    db.close()
    yield s, {"e1": e1, "e2": e2}


def _ctx(db, s):
    from app.core.context import resolve_workspace_context

    return resolve_workspace_context(
        db, 1, "homeroom", {"academic_year_id": s.ay_id, "class_id": s.h6_id}
    )


# ── 3A：诊断报告按真实考试名 / exam_key 双收，输出人类可读名 ──────────────


def test_report_accepts_real_exam_name_and_key(client, event_seed):
    s, events = event_seed
    params = {"person_id": s.jia_h_id, "academic_year_id": s.ay_id}
    by_name = client.get(
        "/api/v1/homeroom/diagnosis/report",
        params={**params, "exam_name": "1月合成考"},
    )
    assert by_name.status_code == 200, by_name.text
    body = by_name.json()
    # 展示字段是人类可读考试名；稳定定位键另附 exam_key（增量字段）
    assert body["exam_name"] == "1月合成考"
    assert body["exam_key"] == f"event:{events['e2']}"
    assert body["subject_performance"]["exam_name"] == "1月合成考"
    assert body["subject_performance"]["exam_key"] == f"event:{events['e2']}"
    ls = body["learning_state"]
    assert ls["latest_exam_name"] != body["exam_key"]  # 不再输出 event:123
    assert not str(ls["latest_exam_name"]).startswith("event:")

    # 传 exam_key 定位到同一 bucket，报告逐字段一致（exam_name_source=explicit）
    by_key = client.get(
        "/api/v1/homeroom/diagnosis/report",
        params={**params, "exam_name": f"event:{events['e1']}"},
    )
    assert by_key.status_code == 200, by_key.text
    assert by_key.json()["exam_name"] == "12月合成考"
    assert by_key.json()["exam_key"] == f"event:{events['e1']}"

    # 缺省=最近一场：1月合成考（2026-01-10）晚于期中，输出人类可读名
    # 而非 event:123
    default = client.get("/api/v1/homeroom/diagnosis/report", params=params)
    assert default.status_code == 200
    assert default.json()["exam_name"] == "1月合成考"
    assert default.json()["exam_key"] == f"event:{events['e2']}"


def test_report_unknown_exam_422_lists_dual_labels(client, event_seed):
    s, _ = event_seed
    resp = client.get(
        "/api/v1/homeroom/diagnosis/report",
        params={
            "person_id": s.jia_h_id, "academic_year_id": s.ay_id,
            "exam_name": "不存在的考试",
        },
    )
    assert resp.status_code == 422
    available = resp.json()["available_exams"]  # details 顶层并入错误体
    # 「名称 (key)」双标；同名多场时只有 exam_key 能唯一定位
    assert any(item.startswith("12月合成考 (event:") for item in available)
    assert any(item.startswith("1月合成考 (event:") for item in available)
    assert any(item.startswith("2025期中 (") for item in available)  # legacy 事实


# ── 3B：锚点截断先归一 key，锚点考试本身保留 ──────────────────────────────


def test_truncate_at_anchor_by_name_keeps_anchor_exam(client, event_seed):
    s, events = event_seed
    from app.api import _queries as q
    from app.db.models import SessionLocal
    from app.diagnosis.features import (
        _group_exams, _ordered_exam_names, _truncate_at_anchor,
    )

    db = SessionLocal()
    ctx = _ctx(db, s)
    entries = q.readable_facts(db, ctx, member_ids=[s.jia_h_id])
    exams = _group_exams(entries, db)
    ordered = _ordered_exam_names(exams)
    # 修复前：anchor_exam in exams 为 False（key 是 event:N）→ 回退分支拿
    # 'event:2' 与 '1月合成考' 做字典序比较（'e' > '1'）→ 锚点考试被误删。
    retained = _truncate_at_anchor(
        exams, ordered, "1月合成考", (True, "2026-01-10", "1月合成考")
    )
    assert f"event:{events['e2']}" in retained  # 锚点本身保留（截到含锚点）
    assert retained[-1] == f"event:{events['e2']}"
    db.close()


def test_class_features_anchor_payload_carries_exam_key(client, event_seed):
    s, events = event_seed
    from app.db.models import SessionLocal
    from app.diagnosis.features import class_features_at_anchors

    db = SessionLocal()
    ctx = _ctx(db, s)
    payloads = class_features_at_anchors(db, ctx, s.ay_id, ["12月合成考"])
    anchor = payloads["12月合成考"]["anchor"]
    assert anchor["exam_name"] == "12月合成考"
    assert anchor["exam_key"] == f"event:{events['e1']}"
    db.close()


# ── 指标时间线：exam_name 人类可读 + exam_key 稳定键 ──────────────────────


def test_metric_points_output_name_and_key(client, event_seed):
    s, events = event_seed
    from app.db.models import SessionLocal
    from app.diagnosis.review import metric_points

    db = SessionLocal()
    ctx = _ctx(db, s)
    points = metric_points(db, ctx, s.jia_h_id, "total:主三门")
    by_key = {p["exam_key"]: p for p in points}
    # 种子 legacy 期中（key=考试名）+ 两场事件考试（key=event:N）
    assert set(by_key) == {"2025期中", f"event:{events['e1']}", f"event:{events['e2']}"}
    assert by_key[f"event:{events['e1']}"]["exam_name"] == "12月合成考"
    assert by_key[f"event:{events['e2']}"]["exam_name"] == "1月合成考"
    assert by_key["2025期中"]["exam_name"] == "2025期中"
    assert all(not p["exam_name"].startswith("event:") for p in points)
    db.close()


def test_trend_last_change_shows_names_not_keys(client, event_seed):
    s, _ = event_seed
    from app.db.models import SessionLocal
    from app.diagnosis.features import student_features

    db = SessionLocal()
    ctx = _ctx(db, s)
    f = student_features(db, ctx, s.jia_h_id, s.ay_id)
    last_change = f["indicators"]["trend"]["last_change"]
    assert last_change["from"] == "12月合成考"
    assert last_change["to"] == "1月合成考"
    assert last_change["from_exam_key"].startswith("event:")
    assert last_change["to_exam_key"].startswith("event:")
    db.close()


# ── 关注回看：时间线非空（按 key 过滤）、偏科回看可判、输出双标 ────────────


def test_focus_outcome_timeline_and_imbalance_by_name_and_key(client, event_seed):
    s, events = event_seed
    from app.db.models import SessionLocal
    from app.diagnosis.focus import focus_cohorts, focus_outcome

    db = SessionLocal()
    ctx = _ctx(db, s)
    cohorts = focus_cohorts(db, ctx)
    # cohorts 的考试清单带 exam_key（增量字段），名称仍人类可读
    exams = {e["exam_name"]: e for e in cohorts["exams"]}
    assert exams["1月合成考"]["exam_key"] == f"event:{events['e2']}"

    current = next(
        c for c in cohorts["cohorts"] if c["cohort"] == "current-focus:明显偏科型"
    )
    assert s.jia_h_id in current["person_ids"]

    # 名称入参：时间线非空（修复前 p["exam_name"] 是 event:N，过滤恒空 []）
    result = focus_outcome(
        db, ctx, "current-focus:明显偏科型", "12月合成考", "1月合成考", "total:主三门"
    )
    assert result["from_exam"] == "12月合成考" and result["to_exam"] == "1月合成考"
    assert result["from_exam_key"] == f"event:{events['e1']}"
    assert result["to_exam_key"] == f"event:{events['e2']}"
    row = next(r for r in result["students"] if r["person_id"] == s.jia_h_id)
    assert row["timeline"], "时间线不得恒空"
    assert [p["exam_name"] for p in row["timeline"]] == ["12月合成考", "1月合成考"]
    assert all(not p["exam_name"].startswith("event:") for p in row["timeline"])

    # exam_key 入参解析到同一 bucket
    by_key = focus_outcome(
        db, ctx, "current-focus:明显偏科型",
        f"event:{events['e1']}", f"event:{events['e2']}", "total:主三门",
    )
    assert by_key["from_exam"] == "12月合成考"
    assert by_key["students"][0]["timeline"]

    # 偏科回看可判：修复前 buckets.get(名称) 恒 None → 基准缺失 → 恒「暂不可比」
    imbalance = focus_outcome(
        db, ctx, "current-focus:明显偏科型", "12月合成考", "1月合成考", "problem:imbalance"
    )
    irow = next(r for r in imbalance["students"] if r["person_id"] == s.jia_h_id)
    assert irow["comparable"] is True
    assert irow["imbalance"], "偏科明细不得为空"
    assert irow["imbalance"][0]["from_overall_percentile"] is not None
    assert irow["imbalance"][0]["to_overall_percentile"] is not None
    db.close()
