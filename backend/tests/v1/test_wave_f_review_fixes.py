"""Wave F 回归测试：Codex 2026-09-29 复审（Wave E 之后）四项发现的正式化。

来源：.test-data/codex-review-followup/test_edge_probes.py（审查期定向
复现）转正 + 主控补充用例。覆盖（编号对应复审反馈）：

1.  历史报告缺考残留（report.py 非最新考试分支）：score=NULL 行内残留
    百分位绝不输出、缺考总分行绝不产出偏科差；
2.  PATCH 改档案 date（start_date 为空 → 锚点随 date 移动）同样触发
    基线重取：补录干预改早开始日后，干预后成绩不再冒充基线；
3.  手填百分位基线单位错配：API 入口拒绝越界值（percentile 0–1 小数、
    rank ≥ 1 → 422），已入库脏数据（直连改库）由复查对照兜底
    pending/baseline_incomparable，绝不按错误单位算 change；
4.  月精度锚点整组回退当前时点（features.py）：成绩时间线不截断到
    历史场、行为窗口用 scope.as_of——绝不混合「历史成绩 + 当前行为」。

样本全部合成；测试数据目录由 conftest 隔离（EXAM_TRACKER_DIR）。
"""

from datetime import date

import pytest

from app.db import workspace_models as wm
from app.db.models import Base, SessionLocal, engine
from app.api.students_mgmt import _homeroom_ctx, patch_note
from app.api.students_mgmt_schemas import NotePatchRequest

API = "/api/v1"


@pytest.fixture(scope="module", autouse=True)
def isolated_module_schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def probe(v1_seed):
    db = SessionLocal()
    yield db, v1_seed
    db.rollback()
    db.close()


def add_fact(db, s, name, day, score=250, rank=300, pct=0.3, subject=None,
             month=None):
    f = wm.ScoreFact(
        data_domain="homeroom", academic_year_id=s.ay_id, class_ref_id=s.h6_id,
        identity_id=s.jia_h_id, exam_name=name, exam_date=day, score=score,
        xueji_rank=rank, grade_percentile=pct,
        total_type=None if subject else "主三门", subject=subject,
        source="wave-f-test",
        source_exam_date=month, exam_date_precision="month" if month else None,
    )
    db.add(f)
    db.flush()
    return f


# 用例顺序约定：本文件 probe fixture 只 rollback，但 patch_note/create_note
# 路由内部会 db.commit()——唯一写成绩又走 commit 的用例（#2 改 date 重取）
# 必须排在最后，其余用例的合成成绩才不会泄入后续时间线。


def hctx(db, s):
    return _homeroom_ctx(db, s.ay_id, s.h6_id, None)


# ── 1. 历史报告缺考残留（复审 #2） ──────────────────────────────────


def test_historical_report_absent_residue_not_used(probe):
    from app.diagnosis.report import student_report

    db, s = probe
    # 一月场登记缺考：总分行与语文行 score=NULL 但行内残留名次/百分位
    add_fact(db, s, "缺考场", date(2026, 1, 1), score=None, rank=50, pct=0.1)
    add_fact(db, s, "缺考场", date(2026, 1, 1), score=None, pct=0.8, subject="语文")
    add_fact(db, s, "最近场", date(2026, 2, 1))
    report = student_report(db, hctx(db, s), s.jia_h_id, s.ay_id, exam_name="缺考场")
    performance = report["subject_performance"]
    chinese = next(
        subj for subj in performance["subjects"] if subj["subject"] == "语文"
    )
    assert chinese["percentile"] is None and chinese["grade_score"] is None, chinese
    # 缺考总分行（残留 pct=0.1）绝不产出偏科基准
    assert performance["imbalance"]["status"] == "not_computable", performance


# ── 2. 改档案 date 触发基线重取（复审 #3，commit 用例：排文件末尾） ──


# ── 3. 手填百分位基线单位错配（复审 #4） ────────────────────────────


def test_create_manual_baseline_out_of_range_422(client, probe):
    db, s = probe
    r = client.post(
        f"{API}/homeroom/students/{s.jia_h_id}/notes",
        json={
            "date": "2026-03-01", "category": "谈话", "content": "wave-f synthetic",
            "problem": "wave-f synthetic", "target_metric": "subject:语文",
            "baseline_value": {"value": 50},  # 50 显然不是 0–1 小数
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"] == "invalid_scope_param"
    assert "percentile" in str(r.json()["detail"])


def test_patch_manual_baseline_out_of_range_422(client, probe):
    db, s = probe
    note = wm.WsStudentNote(
        data_domain="homeroom", person_id=s.jia_h_id, date=date(2026, 3, 1),
        category="谈话", content="wave-f synthetic", problem="wave-f synthetic",
        status="open", target_metric="subject:语文",
    )
    db.add(note)
    db.flush()
    db.commit()
    r = client.patch(
        f"{API}/homeroom/notes/{note.id}",
        json={"baseline_value": {"value": 40}},
    )
    assert r.status_code == 422, r.text
    assert "percentile" in str(r.json()["detail"])


def test_review_contrast_dirty_baseline_falls_back_pending(probe):
    """已入库的脏基线（绕过 API 直改库，如历史数据）由复查对照兜底：
    绝不把 50 当 0–1 口径算出 change=-49.6。"""
    from app.diagnosis.review import review_contrast

    db, s = probe
    add_fact(db, s, "四月场", date(2026, 4, 1), pct=0.4, subject="语文")
    note = wm.WsStudentNote(
        data_domain="homeroom", person_id=s.jia_h_id,
        date=date(2026, 3, 1), start_date=date(2026, 3, 1),
        category="谈话", content="wave-f synthetic", problem="wave-f synthetic",
        status="open", target_metric="subject:语文",
        baseline_value={"metric": "subject:语文", "value": 50, "source": "teacher"},
    )
    db.add(note)
    db.flush()
    contrast = review_contrast(db, hctx(db, s), note)
    assert contrast["status"] == "pending", contrast
    assert contrast["reason"] == "baseline_incomparable", contrast


def test_review_contrast_legal_percentile_baseline_ready(probe):
    """合法手填（0–1 小数，前端按百分数转换后提交）→ 正常对照：
    0.5 → 0.4 = −0.1（前 50% → 前 40%，相对位置上升）。"""
    from app.diagnosis.review import review_contrast

    db, s = probe
    add_fact(db, s, "四月场", date(2026, 4, 1), pct=0.4, subject="语文")
    note = wm.WsStudentNote(
        data_domain="homeroom", person_id=s.jia_h_id,
        date=date(2026, 3, 1), start_date=date(2026, 3, 1),
        category="谈话", content="wave-f synthetic", problem="wave-f synthetic",
        status="open", target_metric="subject:语文",
        baseline_value={"metric": "subject:语文", "unit": "percentile",
                        "value": 0.5, "source": "teacher"},
    )
    db.add(note)
    db.flush()
    contrast = review_contrast(db, hctx(db, s), note)
    assert contrast["status"] == "ready", contrast
    assert contrast["change"]["value"] == -0.1, contrast


# ── 4. 月精度锚点整组回退当前时点（复审 #5） ────────────────────────


def test_month_anchor_fallback_uses_current_features(probe):
    from app.diagnosis.features import class_features

    db, s = probe
    add_fact(db, s, "一月月精度场", None, rank=100, month="2026-01")
    add_fact(db, s, "二月精确场", date(2026, 2, 1), rank=500)
    payload = class_features(db, hctx(db, s), s.ay_id, anchor_exam="一月月精度场")
    # 回退必须整组一致：当前水平取最新场（500），而非截断历史场（100）
    assert payload["anchor"]["timepoint_basis"] == "current_time_point", payload["anchor"]
    student = next(
        row for row in payload["students"] if row["person_id"] == s.jia_h_id
    )
    main3 = student["features"]["indicators"]["current_level"]["main3"]
    assert main3["rank"] == 500, main3


# ── 2. 改档案 date 触发基线重取（复审 #3）——commit 用例，排末尾 ────


def test_patch_note_date_recaptures_baseline(probe):
    from app.diagnosis.review import review_contrast

    db, s = probe
    add_fact(db, s, "一月场", date(2026, 1, 1), rank=300)
    add_fact(db, s, "三月场", date(2026, 3, 1), rank=100)
    note = wm.WsStudentNote(
        data_domain="homeroom", person_id=s.jia_h_id, date=date(2026, 4, 1),
        category="谈话", content="wave-f synthetic", problem="wave-f synthetic",
        status="open", target_metric="total:主三门",
        baseline_value={"metric": "total:主三门", "unit": "rank", "value": 100,
                        "exam_name": "三月场", "exam_date": "2026-03-01",
                        "source": "auto"},
    )
    db.add(note)
    db.flush()

    # start_date 为空：锚点=档案 date。4/1 改到 2/1 后，三月成绩（干预后）
    # 不再早于锚点 → 必须重取一月场（干预前），否则复查与同场比较恒为 0。
    patched = patch_note(
        mode="homeroom", note_id=note.id,
        req=NotePatchRequest(date="2026-02-01"),
        academic_year_id=s.ay_id, class_id=s.h6_id, teaching_class_id=None,
        term_id=None, subject=None, db=db,
    )
    assert patched.baseline_value["exam_name"] == "一月场", patched.baseline_value
    contrast = review_contrast(db, hctx(db, s), note)
    assert contrast["status"] == "ready", contrast
    assert contrast["baseline"]["value"] == 300.0, contrast
    assert contrast["change"]["value"] == -200.0, contrast  # 100 − 300


# ── 3. 名次手填基线整数校验（2026-09-30 Codex 复审 #3）──────────────


def test_create_rank_baseline_fraction_422(client, probe):
    """名次手填基线非整数（1.5）在写入前拦下：422 invalid_scope_param。"""
    db, s = probe
    r = client.post(
        f"{API}/homeroom/students/{s.jia_h_id}/notes",
        json={
            "date": "2026-03-01", "category": "谈话", "content": "codex-3 synthetic",
            "problem": "codex-3 synthetic", "target_metric": "total:主三门",
            "baseline_value": {"value": 1.5},  # 名次为整数，1.5 不可入库
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"] == "invalid_scope_param"
    assert "整数" in str(r.json()["detail"]), r.json()["detail"]


def test_numeric_baseline_value_rejects_fractional_rank():
    """纯函数口径：rank 单位仅接受 ≥1 的整数；1.5 → None（绝不参与对照）。"""
    from app.diagnosis.review import _numeric_baseline_value

    assert _numeric_baseline_value({"value": 1.5, "unit": "rank"}, "rank") is None
    assert _numeric_baseline_value({"value": 300, "unit": "rank"}, "rank") == 300.0
    assert _numeric_baseline_value({"value": 300.0, "unit": "rank"}, "rank") == 300.0


def test_review_contrast_fractional_rank_baseline_pending(probe):
    """已入库的脏名次基线（历史遗留 1.5）由复查对照兜底：
    pending/baseline_incomparable，绝不拿 1.5 硬算 change。"""
    from app.diagnosis.review import review_contrast

    db, s = probe
    # 考试名与文件内其它用例错开：前序 commit 用例的成绩会泄入本用例，
    # 同名会撞唯一约束（文件头注释钉死的顺序约定）。
    add_fact(db, s, "丙场", date(2026, 1, 15), rank=300)
    note = wm.WsStudentNote(
        data_domain="homeroom", person_id=s.jia_h_id,
        date=date(2026, 2, 1), start_date=date(2026, 2, 1),
        category="谈话", content="codex-3 synthetic", problem="codex-3 synthetic",
        status="open", target_metric="total:主三门",
        baseline_value={"metric": "total:主三门", "unit": "rank", "value": 1.5,
                        "source": "teacher"},
    )
    db.add(note)
    db.flush()
    contrast = review_contrast(db, hctx(db, s), note)
    assert contrast["status"] == "pending", contrast
    assert contrast["reason"] == "baseline_incomparable", contrast
