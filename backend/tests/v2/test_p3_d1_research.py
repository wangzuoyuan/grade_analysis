"""P3-D1 教研统计端点测试（契约 docs/diagnosis-roadmap/p3-contracts.md §2/§4）。

覆盖（契约 §4 门禁清单）：
- 缺考排除：秦缺 P3二中主三门缺考行（score=NULL）→ excluded.missing_exam；
- 空队列：无成员类型队列 → 200 + 零计数 + status=empty（绝不 500/编造）；
- 跨学年 404：不存在的 academic_year_id → 404 resource_out_of_scope；
- 教学域隔离：teaching 队列只含教学域建档，homeroom 人员绝不出现；
  教学域 total:主三门 不可得 → 如实排除（绝不跨域取数）；
- 干预队列含 pending 复查：review_summary ready/pending 条数（C4 同源）；
- limitations 固定文案存在（回顾性队列、无对照组、不构成因果结论）；
- 聚合与 B3 单生分解一致（同源断言：research 的每人变化 ==
  /changes/student 端点的同指标变化）。

样本全部合成（秦甲/秦乙/秦丙/秦进/秦研/秦缺/秦离 + 教学域 秦甲·T/秦乙·T）。
在 tests/v2/conftest.py 的 v1_seed 之上补种 P3 场景（ORM 直种）。
"""

from datetime import date, timedelta

import pytest

API = "/api/v1"

EXAM_P3_ONE = "P3一中"    # 2025-10-20（仅秦进）
EXAM_P3_MID = "P3二中"    # 2026-01-20
EXAM_P3_FINAL = "P3期末"  # 2026-04-15

METRIC_MAIN3 = "total:主三门"
# 高二教学域：物理为选考等级分口径（高二 subject:物理 非指标选项，高二/三
# 频次模式走 subject_grade:*——与 tests/v2/test_p2_c4_review.py 同一裁决）
METRIC_PHYS = "subject_grade:物理"

NAME_JIN = "秦进"   # 持续进步型（连续进步 2 次）
NAME_YAN = "秦研"   # 短期下滑型（近方向退步未成连击）
NAME_QUE = "秦缺"   # P3二中缺考（排除样本）
NAME_LI = "秦离"    # 建档后离班（transferred_out 样本）


def _d(days_offset: int) -> date:
    return date.today() + timedelta(days=days_offset)


@pytest.fixture(scope="module")
def p3d1_seed(v1_seed):
    """P3-D1 场景：三名 v1 学生补两场带名次考试 + 四名新学生 + 干预建档。"""
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    s = v1_seed
    db = SessionLocal()

    def person(name, seat, *, valid_to=None):
        ident = wm.WsStudentIdentity(data_domain="homeroom", display_name=name)
        db.add(ident)
        db.flush()
        db.add(
            wm.Enrollment(
                admin_class_id=s.h6_id,
                identity_id=ident.id,
                status="active",
                valid_from=date(2025, 9, 1),
                valid_to=valid_to,
                seat_no=seat,
            )
        )
        return ident

    jin = person(NAME_JIN, 31)
    yan = person(NAME_YAN, 32)
    que = person(NAME_QUE, 33)
    li = person(NAME_LI, 34, valid_to=date(2026, 2, 15))  # 已离班
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
        f.source = "p3-d1-test"
        db.add(f)
        return f

    def main3(ident_id, exam, exam_date, score, xueji, grade_rank, pct):
        fact("homeroom", s.h6_id, ident_id, exam, exam_date, None, "主三门",
             score, pct=pct, xueji=xueji, grade_rank=grade_rank)

    # ── 成绩（学籍名次为本测试断言的唯一名次口径）──
    # 秦进：350 → 250 → 200（连续进步 ≥2 → 持续进步型；结局变化 −50 进步）
    main3(jin.id, EXAM_P3_ONE, date(2025, 10, 20), 246.0, 350, 360, 0.55)
    main3(jin.id, EXAM_P3_MID, date(2026, 1, 20), 256.0, 250, 260, 0.60)
    main3(jin.id, EXAM_P3_FINAL, date(2026, 4, 15), 260.0, 200, 210, 0.64)
    # 秦甲（v1）：200 → 100（结局变化 −100 进步）
    main3(s.jia_h_id, EXAM_P3_MID, date(2026, 1, 20), 250.0, 200, 210, 0.80)
    main3(s.jia_h_id, EXAM_P3_FINAL, date(2026, 4, 15), 280.0, 100, 110, 0.90)
    # 秦乙（v1）：100 → 90（|−10| < 阈值 20 → 持平）
    main3(s.yi_h_id, EXAM_P3_MID, date(2026, 1, 20), 270.0, 100, 110, 0.30)
    main3(s.yi_h_id, EXAM_P3_FINAL, date(2026, 4, 15), 275.0, 90, 100, 0.29)
    # 秦丙（v1）：300 → 150（结局变化 −150 进步）
    main3(s.bing_h_id, EXAM_P3_MID, date(2026, 1, 20), 235.0, 300, 310, 0.66)
    main3(s.bing_h_id, EXAM_P3_FINAL, date(2026, 4, 15), 265.0, 150, 160, 0.74)
    # 秦研：100 → 180（+80 退步；短期下滑型）
    main3(yan.id, EXAM_P3_MID, date(2026, 1, 20), 275.0, 100, 110, 0.30)
    main3(yan.id, EXAM_P3_FINAL, date(2026, 4, 15), 230.0, 180, 190, 0.40)
    # 秦缺：P3二中缺考（有行无分无名次）；P3期末 250
    fact("homeroom", s.h6_id, que.id, EXAM_P3_MID, date(2026, 1, 20), None, "主三门", None)
    main3(que.id, EXAM_P3_FINAL, date(2026, 4, 15), 245.0, 250, 260, 0.45)

    # ── 教学域 T6：物理等级分（82→79 = −3.0 等级分，|−3|<20 → 持平）──
    def phys(ident_id, exam, exam_date, score, pct, grade_score):
        fact("teaching", s.t6_id, ident_id, exam, exam_date, "物理", None, score,
             pct=pct, grade_score=grade_score)

    phys(s.jia_t_id, EXAM_P3_MID, date(2026, 1, 20), 80.0, 0.50, 82.0)
    phys(s.jia_t_id, EXAM_P3_FINAL, date(2026, 4, 15), 85.0, 0.35, 79.0)
    phys(s.yi_t_id, EXAM_P3_MID, date(2026, 1, 20), 88.0, 0.60, 88.0)
    phys(s.yi_t_id, EXAM_P3_FINAL, date(2026, 4, 15), 90.0, 0.40, 92.0)

    # ── 干预建档（C4 扩展列；ORM 直种保证用例内时序确定）──
    def note(person_id, *, problem, status="open", target_metric=METRIC_MAIN3,
             baseline=None, start=None, review=None, subject_scope=None, date_=date(2026, 2, 1)):
        row = wm.WsStudentNote(
            data_domain="homeroom",
            person_id=person_id,
            date=date_,
            category="谈话",
            content="合成：P3-D1 干预建档",
            problem=problem,
            subject_scope=subject_scope,
            measures="每周面批",
            target_metric=target_metric,
            baseline_value=baseline,
            start_date=start,
            review_date=review,
            status=status,
        )
        db.add(row)
        return row

    # ready（新可比考试 + 已到期）：基线 P3二中 200 名 → P3期末 100 名
    note(s.jia_h_id, problem="名次下滑", status="open",
         baseline={"value": 200.0, "unit": "rank", "exam_name": EXAM_P3_MID,
                   "exam_date": "2026-01-20"},
         start=date(2026, 1, 21), review=_d(-30))
    # pending/not_due_yet：开始日晚于最后一场且未到复查日
    note(s.yi_h_id, problem="名次下滑", status="open",
         baseline={"value": 90.0, "unit": "rank", "exam_name": EXAM_P3_FINAL},
         start=date(2026, 5, 10), review=_d(30))
    # pending/no_target_metric（done 状态：关闭干预同样进复查摘要计数）
    note(s.bing_h_id, problem="名次下滑", status="done", target_metric=None)
    # ready（未到期但已有新可比考试）：基线 P3二中 100 名 → P3期末 180 名
    note(yan.id, problem="作业波动", subject_scope="数学",
         baseline={"value": 100.0, "unit": "rank", "exam_name": EXAM_P3_MID,
                   "exam_date": "2026-01-20"},
         start=date(2026, 1, 21), review=_d(30), date_=date(2026, 1, 25))
    # pending/no_baseline：零基线
    note(que.id, problem="名次下滑", status="open")
    # 已离班学生的建档（不进 in-scope 成员与复查摘要，只进 transferred_out）
    note(li.id, problem="离班跟进", status="open")

    # 教学域建档：秦甲·T（教学域干预队列唯一成员；等级分基线）
    db.add(
        wm.WsStudentNote(
            data_domain="teaching",
            person_id=s.jia_t_id,
            date=date(2026, 2, 1),
            category="谈话",
            content="合成：教学域干预",
            problem="物理波动",
            subject_scope="物理",
            target_metric=METRIC_PHYS,
            baseline_value={"value": 82.0, "unit": "grade_score", "exam_name": EXAM_P3_MID,
                            "exam_date": "2026-01-20"},
            start_date=date(2026, 1, 21),
            review_date=_d(-30),
            status="open",
        )
    )

    db.commit()
    yield SimpleNamespaceP3(s, jin, yan, que, li)
    db.close()


class SimpleNamespaceP3:
    """种子句柄（显式属性，便于用例阅读）。"""

    def __init__(self, s, jin, yan, que, li):
        self.s = s
        self.jin_id = jin.id
        self.yan_id = yan.id
        self.que_id = que.id
        self.li_id = li.id


def _get_cohorts(client, mode="homeroom", **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(f"{API}/{mode}/diagnosis/research/cohorts?{query}")


def _get_outcome(client, cohort, from_exam, to_exam, metric, mode="homeroom", **params):
    from urllib.parse import quote

    query = "&".join(
        f"{k}={quote(str(v))}" for k, v in (
            {"cohort": cohort, "from_exam": from_exam, "to_exam": to_exam,
             "metric": metric, **params}.items()
        )
    )
    return client.get(f"{API}/{mode}/diagnosis/research/outcome?{query}")


def _assert_limitations(limitations):
    assert isinstance(limitations, list) and limitations, "limitations 必须存在"
    text = "\n".join(limitations)
    assert "回顾性队列" in text, "必须声明回顾性队列"
    assert "无对照" in text, "必须声明无对照"
    assert "因果" in text and "不构成" in text, "必须声明不构成因果结论"


# ────────────────────────── 队列清单 ──────────────────────────


@pytest.mark.usefixtures("p3d1_seed")
class TestCohortList:
    def test_interventions_cohort_groups_and_status(self, client, p3d1_seed):
        resp = _get_cohorts(client, academic_year_id=p3d1_seed.s.ay_id)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["calc_version"] == "p3-v1"
        assert body["rules_version"] == "p1-v1/p2-v1/p3-v1"
        _assert_limitations(body["limitations"])
        entry = next(c for c in body["cohorts"] if c["cohort"] == "interventions")
        # 在册建档学生：甲乙丙研缺 5 人；秦离已离班单列
        assert entry["student_count"] == 5
        assert entry["transferred_out"]["n"] == 1
        group_names = {(g["subject_scope"], g["problem"]) for g in entry["groups"]}
        assert (None, "名次下滑") in group_names
        assert ("数学", "作业波动") in group_names
        big = next(g for g in entry["groups"] if g["problem"] == "名次下滑")
        assert big["n"] == 4
        assert big["start_date"] is not None and big["end_date"] is not None  # 起止
        assert big["status_counts"]["open"] >= 2
        assert big["status_counts"]["done"] == 1

    def test_type_cohorts_listed_with_anchor_semantics(self, client, p3d1_seed):
        resp = _get_cohorts(client, academic_year_id=p3d1_seed.s.ay_id)
        body = resp.json()
        ids = {c["cohort"] for c in body["cohorts"]}
        # 考试锚点重算（2026-09-29 升级）：类型按各场考试时点判定——秦进在
        # P3期末才累计连续进步 2 次（P3一中/P3二中/2025期中 时点尚不构成
        # 持续进步型），秦研在 P3期末才有单次退步；早期锚点不冒充命中。
        assert f"type:持续进步型@{EXAM_P3_FINAL}" in ids
        assert f"type:短期下滑型@{EXAM_P3_FINAL}" in ids
        for early in ("2025期中", "P3一中", "P3二中"):
            assert f"type:持续进步型@{early}" not in ids
            assert f"type:短期下滑型@{early}" not in ids
        entry = next(c for c in body["cohorts"] if c["cohort"] == f"type:持续进步型@{EXAM_P3_FINAL}")
        assert entry["kind"] == "type"
        assert entry["student_count"] == 1
        assert entry["person_ids"] == [p3d1_seed.jin_id]
        assert entry["membership_basis"] == "exam_anchor"
        assert entry["limitation"] is None  # 日期可解析 → 无回退局限
        # 顶层固定文案说明锚点语义
        assert any("时点重算" in item for item in body["limitations"])

    def test_metric_options_and_exams_exposed(self, client, p3d1_seed):
        resp = _get_cohorts(client, academic_year_id=p3d1_seed.s.ay_id)
        body = resp.json()
        assert {e["exam_name"] for e in body["exams"]} >= {"2025期中", EXAM_P3_MID, EXAM_P3_FINAL}
        assert any(o["value"] == METRIC_MAIN3 for o in body["metric_options"])

    def test_unknown_academic_year_404(self, client):
        resp = _get_cohorts(client, academic_year_id=999999)
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"] == "resource_out_of_scope"

    def test_teaching_domain_isolation_in_listing(self, client, p3d1_seed):
        """教学域清单只含教学域建档（秦甲·T）；班主任域人员绝不出现。"""
        resp = _get_cohorts(client, mode="teaching", academic_year_id=p3d1_seed.s.ay_id)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        s = p3d1_seed.s
        homeroom_ids = {s.jia_h_id, s.yi_h_id, s.bing_h_id,
                        p3d1_seed.jin_id, p3d1_seed.yan_id, p3d1_seed.que_id, p3d1_seed.li_id}
        entry = next(c for c in body["cohorts"] if c["cohort"] == "interventions")
        assert entry["student_count"] == 1
        for group in entry["groups"]:
            assert set(group["person_ids"]).isdisjoint(homeroom_ids)
        assert entry["transferred_out"]["n"] == 0
        # 教学域无总分/名次特征 → 类型队列为空（如实为空，不借读全科）
        assert [c for c in body["cohorts"] if c["kind"] == "type"] == []


# ────────────────────────── 结局聚合 ──────────────────────────


@pytest.mark.usefixtures("p3d1_seed")
class TestOutcomeInterventions:
    def test_aggregate_counts_median_excluded_review(self, client, p3d1_seed):
        resp = _get_outcome(
            client, "interventions", EXAM_P3_MID, EXAM_P3_FINAL, METRIC_MAIN3,
            academic_year_id=p3d1_seed.s.ay_id,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["calc_version"] == "p3-v1"
        assert body["rules_version"] == "p1-v1/p2-v1/p3-v1"
        assert body["metric_unit"] == "rank"
        assert body["threshold"] == 20
        # 可比集合（两场均有效）：甲乙丙研 4 人
        assert body["comparable_n"] == 4
        assert body["improved_n"] == 2      # 甲 −100、丙 −150
        assert body["flat_n"] == 1          # 乙 −10
        assert body["declined_n"] == 1      # 研 +80
        assert body["median_change"] == -55.0  # median(−150,−100,−10,80)
        # 缺考排除（秦缺 P3二中缺考）与已离班（秦离）
        assert body["excluded"]["missing_exam"]["person_ids"] == [p3d1_seed.que_id]
        assert body["excluded"]["no_data"]["n"] == 0
        assert body["excluded"]["transferred_out"]["person_ids"] == [p3d1_seed.li_id]
        # 干预队列附 C4 复查摘要（ready 2：甲/研；pending 3：乙/丙/缺）
        summary = body["review_summary"]
        assert summary["ready_n"] == 2
        assert summary["pending_n"] == 3
        assert summary["pending_reasons"] == {
            "no_baseline": 1, "no_target_metric": 1, "not_due_yet": 1,
        }
        _assert_limitations(body["limitations"])
        assert "不构成干预效果因果结论" in body["direction_note"]
        # 明细表：person_id + 变化值（进步在前）
        assert body["students"][0]["person_id"] == p3d1_seed.s.bing_h_id
        assert body["students"][0]["change"] == -150.0
        yan_row = next(r for r in body["students"] if r["person_id"] == p3d1_seed.yan_id)
        assert yan_row["direction"] == "退步" and yan_row["change"] == 80.0

    def test_same_source_with_b3_student_endpoint(self, client, p3d1_seed):
        """同源断言：research 每人变化 == B3 /changes/student 端点同指标变化。"""
        resp = _get_outcome(
            client, "interventions", EXAM_P3_MID, EXAM_P3_FINAL, METRIC_MAIN3,
            academic_year_id=p3d1_seed.s.ay_id,
        )
        body = resp.json()
        assert body["students"], "必须有可比学生"
        for row in body["students"]:
            b3 = client.get(
                f"{API}/homeroom/diagnosis/changes/student?person_id={row['person_id']}"
                f"&from_exam={EXAM_P3_MID}&to_exam={EXAM_P3_FINAL}"
                f"&academic_year_id={p3d1_seed.s.ay_id}"
            )
            assert b3.status_code == 200, b3.text
            assert b3.json()["main3"]["rank_change"] == row["change"]

    def test_empty_type_cohort_returns_zero_aggregate(self, client, p3d1_seed):
        """空队列：无成员类型队列 → 200 零计数（绝不 500、绝不编造）。"""
        resp = _get_outcome(
            client, f"type:稳定优秀型@{EXAM_P3_FINAL}", EXAM_P3_MID, EXAM_P3_FINAL,
            METRIC_MAIN3, academic_year_id=p3d1_seed.s.ay_id,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "empty"
        assert body["comparable_n"] == 0
        assert body["improved_n"] == 0
        assert body["flat_n"] == 0
        assert body["declined_n"] == 0
        assert body["median_change"] is None
        assert body["students"] == []
        assert body["review_summary"] is None
        _assert_limitations(body["limitations"])

    def test_type_cohort_outcome_and_review_absent(self, client, p3d1_seed):
        resp = _get_outcome(
            client, f"type:持续进步型@{EXAM_P3_FINAL}", EXAM_P3_MID, EXAM_P3_FINAL,
            METRIC_MAIN3, academic_year_id=p3d1_seed.s.ay_id,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["cohort_kind"] == "type"
        assert body["type_name"] == "持续进步型"
        assert body["membership_basis"] == "exam_anchor"  # 2026-09-29：考试锚点重算
        assert body["comparable_n"] == 1
        assert body["improved_n"] == 1
        assert body["students"][0]["person_id"] == p3d1_seed.jin_id
        assert body["students"][0]["change"] == -50.0
        assert body["review_summary"] is None
        # 类型队列附锚点语义说明
        assert any("时点重算" in item for item in body["limitations"])

    def test_invalid_inputs(self, client, p3d1_seed):
        ay = p3d1_seed.s.ay_id
        bad_cohort = _get_outcome(client, "bogus", EXAM_P3_MID, EXAM_P3_FINAL, METRIC_MAIN3,
                                  academic_year_id=ay)
        assert bad_cohort.status_code == 422
        assert bad_cohort.json()["error"] == "invalid_scope_param"
        bad_type = _get_outcome(client, f"type:不存在型@{EXAM_P3_FINAL}", EXAM_P3_MID,
                                EXAM_P3_FINAL, METRIC_MAIN3, academic_year_id=ay)
        assert bad_type.status_code == 422
        bad_exam = _get_outcome(client, f"type:持续进步型@不存在的考试", EXAM_P3_MID,
                                EXAM_P3_FINAL, METRIC_MAIN3, academic_year_id=ay)
        assert bad_exam.status_code == 404
        same_exams = _get_outcome(client, "interventions", EXAM_P3_MID, EXAM_P3_MID,
                                  METRIC_MAIN3, academic_year_id=ay)
        assert same_exams.status_code == 422
        unknown_exam = _get_outcome(client, "interventions", "别的考试", EXAM_P3_FINAL,
                                    METRIC_MAIN3, academic_year_id=ay)
        assert unknown_exam.status_code == 404
        assert unknown_exam.json()["error"] == "resource_out_of_scope"
        bad_metric = _get_outcome(client, "interventions", EXAM_P3_MID, EXAM_P3_FINAL,
                                  "total:九门", academic_year_id=ay)
        assert bad_metric.status_code == 422

    def test_cross_year_outcome_404(self, client, p3d1_seed):
        resp = _get_outcome(client, "interventions", EXAM_P3_MID, EXAM_P3_FINAL,
                            METRIC_MAIN3, academic_year_id=999999)
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"] == "resource_out_of_scope"


@pytest.mark.usefixtures("p3d1_seed")
class TestTeachingDomainOutcome:
    def test_teaching_grade_score_metric(self, client, p3d1_seed):
        """教学域仅任教学科口径：物理等级分变化（82→79 = −3.0，|−3|<20 → 持平）。"""
        resp = _get_outcome(
            client, "interventions", EXAM_P3_MID, EXAM_P3_FINAL, METRIC_PHYS,
            mode="teaching", academic_year_id=p3d1_seed.s.ay_id,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["metric_unit"] == "grade_score"
        assert body["comparable_n"] == 1
        assert body["flat_n"] == 1
        assert body["students"][0]["person_id"] == p3d1_seed.s.jia_t_id
        assert body["students"][0]["change"] == -3.0
        # 教学域复查摘要（C4 同源）：秦甲·T 已有新可比考试 → ready
        assert body["review_summary"]["ready_n"] == 1
        assert body["review_summary"]["pending_n"] == 0
        _assert_limitations(body["limitations"])

    def test_focus_followups_teaching_subject_isolation(self, client, p3d1_seed):
        resp = client.get(f"{API}/teaching/diagnosis/research/follow-ups",
                          params={"academic_year_id":p3d1_seed.s.ay_id})
        assert resp.status_code == 200, resp.text
        records = resp.json()["records"]
        assert len(records) == 1
        assert records[0]["person_id"] == p3d1_seed.s.jia_t_id
        assert records[0]["contrast"]["unit"] == "grade_score"
        assert records[0]["contrast"]["baseline"]["value"] == 82

    def test_focus_followups_other_subject_note_for_same_person_excluded(self, p3d1_seed):
        from app.db.models import SessionLocal
        from app.db.workspace_models import WsStudentNote
        from app.core.context import resolve_workspace_context
        from app.diagnosis.focus import follow_ups
        with SessionLocal() as db:
            db.add(WsStudentNote(data_domain="teaching",person_id=p3d1_seed.s.jia_t_id,
                date=date(2026,2,1),category="谈话",content="虚拟数学跟进",problem="数学问题",
                subject_scope="数学",target_metric="subject:数学",status="open"))
            db.flush()
            ctx = resolve_workspace_context(db, 1, "teaching", {"academic_year_id":p3d1_seed.s.ay_id,
                "teaching_class_id":p3d1_seed.s.t6_id,"subject":"物理"})
            records = follow_ups(db, ctx)["records"]
            assert len(records) == 1 and records[0]["subject"] == "物理"
            db.rollback()

    def test_focus_followups_retains_transferred_notes_without_scores(self, client, p3d1_seed):
        resp = client.get(f"{API}/homeroom/diagnosis/research/follow-ups",
                          params={"academic_year_id":p3d1_seed.s.ay_id})
        assert resp.status_code == 200, resp.text
        records = resp.json()["records"]
        transferred = next(r for r in records if r["person_id"] == p3d1_seed.li_id)
        assert not transferred["in_current_roster"]
        assert transferred["contrast"]["reason"] == "transferred_out"
        assert "latest" not in transferred["contrast"]
        assert not any(r["person_id"] == p3d1_seed.s.jia_t_id for r in records)

    def test_teaching_main3_metric_honestly_excluded(self, client, p3d1_seed):
        """教学域无总分/名次事实 → total:主三门 全部如实排除，绝不跨域取数。"""
        resp = _get_outcome(
            client, "interventions", EXAM_P3_MID, EXAM_P3_FINAL, METRIC_MAIN3,
            mode="teaching", academic_year_id=p3d1_seed.s.ay_id,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["comparable_n"] == 0
        assert body["students"] == []
        assert body["excluded"]["missing_exam"]["n"] == 1  # 秦甲·T（有物理行、无名次可算）
        assert body["improved_n"] == 0

    def test_teaching_type_cohort_empty_outcome(self, client, p3d1_seed):
        """教学域类型队列为空：outcome 空聚合（不借读行政班全科）。"""
        resp = _get_outcome(
            client, f"type:持续进步型@{EXAM_P3_FINAL}", EXAM_P3_MID, EXAM_P3_FINAL,
            METRIC_PHYS, mode="teaching", academic_year_id=p3d1_seed.s.ay_id,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "empty"
        assert body["comparable_n"] == 0
