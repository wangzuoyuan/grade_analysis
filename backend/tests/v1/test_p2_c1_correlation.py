"""P2-C1 作业×成绩相关性（契约 docs/diagnosis-roadmap/p2-contracts.md §2 + §6 门禁）。

覆盖：
- 统计单元：纯 Python Pearson / Spearman（含并列秩平均）数值正确性；
- 窗口语义：[考试日−window_days, 考试日) 不含考试当日与考后作业、不含窗前批次；
- 例外登记口径：忘带/请假/出勤不计入分子分母；应交快照为空批次整批剔除
  （分母未知绝不默认已交）；应交名单内无行=已交（既有例外登记默认）；
- 分层：该场学校段位（AnalysisConfig 出厂阈值：高分 1–80 / 临界 400–500 /
  薄弱 ≥501）+ 全班；n<8 / 零方差 / 分母未知占比>50% / 段位不可用 →
  不可计算 + 原因，r/rho 绝不编造；
- 响应必含：window_days、窗口起止日、sample（各层 n 与排除数）、note
  （方向与指标含义 + 不构成因果或提分保证）、calc_version="p2-v1"；
- 教学域：metric 钉任教学科（传其他值 422）、无总分行段位层如实不可用；
- 结构性不可算：考试日期缺失 → status not_computable + exam_date_missing。

口径隔离说明：conftest 每模块重建 schema、db_session 为函数级——本模块用
test_p6 的「_SEEDED 守卫 + 每用例自播种」模式。各用例的批次用**不同
homework_type / subject / 考试日期**隔离（作业批次按考试日期开窗可见，
同一 DB 内重叠窗口的批次会互相进入分母，故必须错峰）。样本全部合成
（P2C1* / 秦甲秦乙秦丙 家族）。
"""

import json
from datetime import date

import pytest

EXAM_WINDOW = "P2C1窗口考"      # 2025-12-01，批次 type=P2C1窗口
EXAM_EXCEPTION = "P2C1例外考"   # 2025-12-01，批次 type=P2C1例外
EXAM_SUBJECT = "P2C1科目考"     # 2025-12-01，批次语文/物理各一
EXAM_LAYER = "P2C1分层考"       # 2025-12-05，批次 type=P2C1分层
EXAM_ZEROVAR = "P2C1零方差考"   # 2025-12-05，批次 type=P2C1零方差
EXAM_SHARE = "P2C1占比考"       # 2025-12-06，批次 type=P2C1占比
EXAM_NORANK = "P2C1无名次考"    # 2025-12-20（窗口与其他批次错峰）
EXAM_NODATE = "P2C1无日期考"

_SEEDED = {"done": False}


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _get(client, path, **params):
    resp = client.get(path, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _add_batch(
    db_session,
    class_id,
    ay_id,
    token,
    assigned,
    expected_ids,
    subject="语文",
    homework_type="P2C1测试",
    rows=(),
):
    """播种一个作业批次（expected_members_json=应交快照）+ 可选逐人行。"""
    from app.db import workspace_models as wm

    a = wm.HomeworkAssignment(
        data_domain="homeroom",
        class_ref_id=class_id,
        academic_year_id=ay_id,
        subject=subject,
        homework_type=homework_type,
        assigned_date=assigned,
        batch_token=token,
        expected_members_json=json.dumps(list(expected_ids)),
    )
    db_session.add(a)
    db_session.flush()
    for pid, status, evaluation in rows:
        db_session.add(
            wm.HomeworkSubmission(
                assignment_id=a.id,
                person_id=pid,
                submission_status=status,
                evaluation=evaluation,
            )
        )
    return a


def _add_main3(db_session, class_id, ay_id, exam, exam_date, ident, score, rank=None):
    from app.db import workspace_models as wm

    db_session.add(
        wm.ScoreFact(
            data_domain="homeroom",
            academic_year_id=ay_id,
            exam_name=exam,
            exam_date=exam_date,
            class_ref_id=class_id,
            identity_id=ident,
            subject="总分",
            total_type="主三门",
            score=score,
            xueji_rank=rank,
            source="p2-c1-test",
        )
    )


def _add_students(db_session, class_id, names):
    """播种学生（身份+入学），返回 identity id 列表。"""
    from app.db import workspace_models as wm

    ids = []
    for name in names:
        ident = wm.WsStudentIdentity(data_domain="homeroom", display_name=name)
        db_session.add(ident)
        db_session.flush()
        ids.append(ident.id)
        db_session.add(
            wm.Enrollment(
                admin_class_id=class_id,
                identity_id=ident.id,
                status="active",
                valid_from=date(2025, 9, 1),
            )
        )
    return ids


def _seed_all(client, v1_seed, db_session):
    """一次播种全部用例数据（_SEEDED 守卫；conftest 每模块空库起步）。

    - 高分段 8 人（学籍名次 1–8）+ 薄弱段 8 人（501–508）；
    - P2C1分层考（12-05）：主三门总分随名次递减；每人 8 个批次（11-21..28，
      type=P2C1分层），组内学生 i 提交前 8−i 个批次 → 提交率 (8−i)/8 与
      分数完全单调一致（组内 r=rho=1）；
    - P2C1零方差考（12-05）：仅高分段有分 + 1 个全员已交批次（x 恒 1.0）；
    - P2C1占比考（12-06）：高分段 8 人有分有批次，弱段 8 人有分无批次；
    - 窗口/例外/科目/无名次考批次各按独立 type 错峰。"""
    if _SEEDED["done"]:
        return
    h6, ay = v1_seed.h6_id, v1_seed.ay_id
    jia, yi, bing = v1_seed.jia_h_id, v1_seed.yi_h_id, v1_seed.bing_h_id

    high = _add_students(db_session, h6, [f"P2C1高{i + 1}" for i in range(8)])
    weak = _add_students(db_session, h6, [f"P2C1弱{i + 1}" for i in range(8)])
    for i, pid in enumerate(high):
        _add_main3(db_session, h6, ay, EXAM_LAYER, date(2025, 12, 5), pid, 100.0 - i, rank=i + 1)
        _add_main3(db_session, h6, ay, EXAM_ZEROVAR, date(2025, 12, 5), pid, 100.0 - i, rank=i + 1)
        _add_main3(db_session, h6, ay, EXAM_SHARE, date(2025, 12, 6), pid, 100.0 - i, rank=i + 1)
    for i, pid in enumerate(weak):
        _add_main3(db_session, h6, ay, EXAM_LAYER, date(2025, 12, 5), pid, 80.0 - i, rank=501 + i)
        _add_main3(db_session, h6, ay, EXAM_SHARE, date(2025, 12, 6), pid, 80.0 - i, rank=501 + i)
    for j in range(1, 9):  # 分层考批次（组内 i 提交 j ≤ 8−i）
        assigned = date(2025, 11, 20 + j)
        _add_batch(db_session, h6, ay, f"p2c1-layer-h{j}", assigned, high,
                   homework_type="P2C1分层",
                   rows=[(pid, "submitted" if j <= 8 - i else "missing", None)
                         for i, pid in enumerate(high)])
        _add_batch(db_session, h6, ay, f"p2c1-layer-w{j}", assigned, weak,
                   homework_type="P2C1分层",
                   rows=[(pid, "submitted" if j <= 8 - i else "missing", None)
                         for i, pid in enumerate(weak)])
    _add_batch(db_session, h6, ay, "p2c1-zero-1", date(2025, 11, 25), high,
               homework_type="P2C1零方差",
               rows=[(pid, "submitted", None) for pid in high])
    for j in range(3):  # 占比考批次（11-22..24，均在 [11-22,12-06) 窗口内）
        _add_batch(db_session, h6, ay, f"p2c1-share-{j + 1}", date(2025, 11, 22 + j),
                   high, homework_type="P2C1占比",
                   rows=[(pid, "submitted" if j <= 8 - i else "missing", None)
                         for i, pid in enumerate(high)])

    # 窗口考（12-01，window_days=14 → [11-17, 12-01)）
    for pid, score in ((jia, 90.0), (yi, 80.0), (bing, 70.0)):
        _add_main3(db_session, h6, ay, EXAM_WINDOW, date(2025, 12, 1), pid, score)
    _add_batch(db_session, h6, ay, "p2c1-win-1", date(2025, 11, 16), [jia, yi, bing],
               homework_type="P2C1窗口", rows=[(jia, "submitted", None)])
    _add_batch(db_session, h6, ay, "p2c1-win-2", date(2025, 11, 17), [jia, yi],
               homework_type="P2C1窗口", rows=[(jia, "submitted", None)])
    _add_batch(db_session, h6, ay, "p2c1-win-3", date(2025, 11, 30), [jia, yi],
               homework_type="P2C1窗口", rows=[(jia, "missing", None)])
    _add_batch(db_session, h6, ay, "p2c1-win-4", date(2025, 12, 1), [jia, yi, bing],
               homework_type="P2C1窗口", rows=[(jia, "missing", None)])
    _add_batch(db_session, h6, ay, "p2c1-win-5", date(2025, 12, 5), [jia, yi, bing],
               homework_type="P2C1窗口", rows=[(jia, "submitted", None), (yi, "submitted", None)])

    # 例外考（12-01，type=P2C1例外）
    for pid, score in ((jia, 90.0), (yi, 80.0), (bing, 70.0)):
        _add_main3(db_session, h6, ay, EXAM_EXCEPTION, date(2025, 12, 1), pid, score)
    _add_batch(db_session, h6, ay, "p2c1-exc-1", date(2025, 11, 20), [jia, yi],
               homework_type="P2C1例外",
               rows=[(jia, "submitted", None), (yi, "excused", "请假")])
    _add_batch(db_session, h6, ay, "p2c1-exc-2", date(2025, 11, 21), [jia, yi],
               homework_type="P2C1例外",
               rows=[(jia, "missing", "忘带"), (yi, "missing", "没来（出勤异常）")])
    _add_batch(db_session, h6, ay, "p2c1-exc-3", date(2025, 11, 22), [jia, yi],
               homework_type="P2C1例外",
               rows=[(jia, "missing", None)])  # 乙无行 = 已交（例外登记默认）
    _add_batch(db_session, h6, ay, "p2c1-exc-4", date(2025, 11, 23), [],
               homework_type="P2C1例外", rows=[])  # 分母未知：整批剔除

    # 科目考（12-01）：语文已交 + 物理缺交，验证 subject 过滤
    _add_main3(db_session, h6, ay, EXAM_SUBJECT, date(2025, 12, 1), jia, 90.0)
    _add_batch(db_session, h6, ay, "p2c1-subj-c", date(2025, 11, 20), [jia],
               subject="语文", homework_type="P2C1科目",
               rows=[(jia, "submitted", None)])
    _add_batch(db_session, h6, ay, "p2c1-subj-p", date(2025, 11, 20), [jia],
               subject="物理", homework_type="P2C1科目",
               rows=[(jia, "missing", None)])

    # 无名次考（12-20，窗口 [12-06, 12-20) 与其他批次错峰）
    _add_main3(db_session, h6, ay, EXAM_NORANK, date(2025, 12, 20), jia, 90.0, rank=None)
    _add_main3(db_session, h6, ay, EXAM_NORANK, date(2025, 12, 20), yi, 80.0, rank=None)
    _add_batch(db_session, h6, ay, "p2c1-norank-1", date(2025, 12, 15), [jia, yi],
               homework_type="P2C1无名次",
               rows=[(jia, "submitted", None), (yi, "missing", None)])

    # 无日期考（旧年月精度数据）
    from app.db import workspace_models as wm

    db_session.add(
        wm.ScoreFact(
            data_domain="homeroom",
            academic_year_id=ay,
            exam_name=EXAM_NODATE,
            exam_date=None,
            source_exam_date="2025-11",
            exam_date_precision="month",
            class_ref_id=h6,
            identity_id=jia,
            subject="总分",
            total_type="主三门",
            score=90.0,
            source="p2-c1-test",
        )
    )
    # 完全无日期考（exam_date 与 source_exam_date 均空）：唯一的结构性不可算路径
    db_session.add(
        wm.ScoreFact(
            data_domain="homeroom",
            academic_year_id=ay,
            exam_name="P2C1空日期考",
            exam_date=None,
            source_exam_date=None,
            exam_date_precision=None,
            class_ref_id=h6,
            identity_id=jia,
            subject="总分",
            total_type="主三门",
            score=90.0,
            source="p2-c1-test",
        )
    )
    db_session.commit()
    _SEEDED["done"] = True


# ────────────────────────── 统计单元（纯函数） ──────────────────────────


def test_pearson_and_spearman_units():
    """纯 Python 实现：Pearson 完全线性 ±1、零方差 None；Spearman 教科书值、
    并列秩平均处理、单调全相关 ±1。"""
    from app.diagnosis.correlation import _pearson, _rank_average, _spearman

    assert _pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert _pearson([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)
    assert _pearson([1, 1, 1], [1, 2, 3]) is None          # x 零方差
    assert _pearson([1, 2, 3], [5, 5, 5]) is None          # y 零方差
    assert _pearson([1], [1]) is None                       # n<2

    assert _rank_average([10, 20, 20, 30]) == [1.0, 2.5, 2.5, 4.0]
    assert _rank_average([5, 5, 5]) == [2.0, 2.0, 2.0]
    # 教科书例（无并列）：rho = 1 − 6·Σd²/(n(n²−1)) = 0.8
    assert _spearman([1, 2, 3, 4, 5], [1, 3, 2, 5, 4]) == pytest.approx(0.8)
    # 并列 → 平均秩后求 Pearson（自洽等价断言）
    xs, ys = [10, 20, 20, 30], [1, 3, 2, 4]
    expected = _pearson(_rank_average(xs), _rank_average(ys))
    assert _spearman(xs, ys) == pytest.approx(expected)
    # 完全单调（并列组内 y 也持平）→ rho=±1；x 有并列但 y 在组内变动 → <1
    assert _spearman([1, 2, 2, 3], [7, 8, 8, 10]) == pytest.approx(1.0)
    assert _spearman([1, 2, 2, 3], [7, 8, 9, 10]) == pytest.approx(
        0.9486832980505138
    )
    assert _spearman([1, 2, 3, 4], [9, 7, 3, 1]) == pytest.approx(-1.0)
    assert _spearman([3, 3, 3], [1, 2, 3]) is None
    assert _spearman([1], [1]) is None


# ────────────────────────── 窗口语义 ──────────────────────────


def test_window_excludes_exam_day_post_and_before_window(client, v1_seed, db_session):
    """窗口 [考试日−14, 考试日)：窗前（11-16）、考试当日（12-01）、考后
    （12-05）批次一律不入窗。type=P2C1窗口 内：甲 11-17 已交 + 11-30 缺交
    → 0.5；乙两批无行 → 例外登记默认已交 1.0；丙只在窗外批次应交 → 无有效
    批次不入样本。n=2 <8 → 全班层 n_too_small。"""
    _seed_all(client, v1_seed, db_session)
    jia, yi = v1_seed.jia_h_id, v1_seed.yi_h_id
    data = _get(
        client, "/api/v1/homeroom/diagnosis/correlation",
        exam_name=EXAM_WINDOW, window_days=14, metric="total:主三门",
        homework_type="P2C1窗口",
    )
    assert data["calc_version"] == "p2-v1"
    assert data["window_days"] == 14
    assert data["window_start"] == "2025-11-17"
    assert data["window_end"] == "2025-11-30"
    assert data["exam_date"] == "2025-12-01"
    assert data["sample"]["exam_score_n"] == 3
    assert data["n"] == 2
    assert data["pairs"] == [
        {"person_id": jia, "name": "秦甲", "x": 0.5, "y": 90.0, "band": None},
        {"person_id": yi, "name": "秦乙", "x": 1.0, "y": 80.0, "band": None},
    ]
    assert data["sample"]["excluded_no_homework"] == 1  # 丙：窗外才有应交批次
    assert data["layers"]["all"]["reason"] == "n_too_small"
    assert data["layers"]["all"]["n"] == 2
    assert data["r"] is None and data["rho"] is None
    assert any("不可计算" in c for c in data["caveats"])


# ────────────────────────── 例外登记口径 ──────────────────────────


def test_exception_registration_semantics(client, v1_seed, db_session):
    """例外登记：忘带/请假/出勤不计入分子分母；应交名单内无行=已交；
    应交快照为空的批次整批剔除（分母未知）；不在任何应交快照的学生不入样本。"""
    _seed_all(client, v1_seed, db_session)
    jia, yi, bing = v1_seed.jia_h_id, v1_seed.yi_h_id, v1_seed.bing_h_id
    data = _get(
        client, "/api/v1/homeroom/diagnosis/correlation",
        exam_name=EXAM_EXCEPTION, window_days=14, metric="total:主三门",
        homework_type="P2C1例外",
    )
    by_pid = {p["person_id"]: p for p in data["pairs"]}
    # 甲：已交1 + 纯缺交1（忘带不计）→ 1/2；乙：请假/出勤不计，无行默认已交
    assert by_pid[jia]["x"] == 0.5
    assert by_pid[yi]["x"] == 1.0
    assert bing not in by_pid                       # 不在任何应交快照 → 不入样本
    assert data["sample"]["excluded_batches_denominator_unknown"] == 1
    assert data["sample"]["paired_n"] == 2


def test_homework_subject_filter(client, v1_seed, db_session):
    """subject 参数按学科过滤 X 侧批次；缺省算作用域内全部作业。语文/物理
    断言加 homework_type=P2C1科目 隔离本用例批次；无过滤断言则按本模块
    播种的全部在窗批次计算（甲 3 已交/3 缺交 → 0.5，与用例顺序无关）。"""
    _seed_all(client, v1_seed, db_session)
    jia = v1_seed.jia_h_id
    filtered_c = _get(
        client, "/api/v1/homeroom/diagnosis/correlation",
        exam_name=EXAM_SUBJECT, subject="语文", homework_type="P2C1科目",
        metric="total:主三门",
    )
    filtered_p = _get(
        client, "/api/v1/homeroom/diagnosis/correlation",
        exam_name=EXAM_SUBJECT, subject="物理", homework_type="P2C1科目",
        metric="total:主三门",
    )
    unfiltered = _get(
        client, "/api/v1/homeroom/diagnosis/correlation",
        exam_name=EXAM_SUBJECT, metric="total:主三门",
    )
    pair_c = {p["person_id"]: p for p in filtered_c["pairs"]}[jia]
    pair_p = {p["person_id"]: p for p in filtered_p["pairs"]}[jia]
    pair_all = {p["person_id"]: p for p in unfiltered["pairs"]}[jia]
    assert pair_c["x"] == 1.0
    assert pair_p["x"] == 0.0
    # 甲的窗口内应交批次（全类型）：窗口考(已交/缺交) + 例外考(已交/缺交)
    # + 科目考(语文已交/物理缺交)；忘带行不计、窗外批次不计
    assert pair_all["x"] == 0.5


# ────────────────────────── 分层与不可计算原因 ──────────────────────────


def test_layers_computable_full(client, v1_seed, db_session):
    """n≥8 且有方差：全班与段位层给出 r/rho；组内完全单调一致 → r=rho=1；
    两组合并的并列 x 序列 rho 仍高度单调但不必为 1；方向为正。"""
    _seed_all(client, v1_seed, db_session)
    data = _get(
        client, "/api/v1/homeroom/diagnosis/correlation",
        exam_name=EXAM_LAYER, window_days=14, metric="total:主三门",
        homework_type="P2C1分层",
    )
    assert data["status"] == "ok"
    assert data["n"] == 16
    assert data["direction"] == "submit_up_score_up"
    layers = data["layers"]
    assert layers["high_score"]["n"] == 8
    assert layers["high_score"]["r"] == 1.0
    assert layers["high_score"]["rho"] == 1.0
    assert layers["weak"]["n"] == 8
    assert layers["weak"]["r"] == 1.0
    assert layers["critical"]["n"] == 0
    assert layers["critical"]["reason"] == "n_too_small"
    assert layers["all"]["n"] == 16
    assert 0 < layers["all"]["rho"] <= 1
    assert 0 < layers["all"]["r"] <= 1
    # sample.layers 与排除计数（契约 §2.4：各层 n 与排除数）
    assert data["sample"]["layers"]["high_score"]["n"] == 8
    assert data["sample"]["layers"]["high_score"]["excluded_no_homework"] == 0
    assert data["sample"]["excluded_no_homework"] == 0
    assert data["sample"]["paired_n"] == 16
    assert data["sample"]["excluded_no_exam_score"] == 3  # 甲乙丙无该场成绩
    # 段位随 pairs 落段标注
    assert {p["band"] for p in data["pairs"]} == {"high_score", "weak"}


def test_zero_variance_layer(client, v1_seed, db_session):
    """x 恒 1.0（全员已交）→ n=8 达标但零方差 → r/rho=null + zero_variance。"""
    _seed_all(client, v1_seed, db_session)
    data = _get(
        client, "/api/v1/homeroom/diagnosis/correlation",
        exam_name=EXAM_ZEROVAR, window_days=14, metric="total:主三门",
        homework_type="P2C1零方差",
    )
    assert data["layers"]["all"]["reason"] == "zero_variance"
    assert data["layers"]["high_score"]["reason"] == "zero_variance"
    assert data["r"] is None and data["rho"] is None
    assert any("零方差" in c for c in data["caveats"])


def test_denominator_unknown_share_over_half(client, v1_seed, db_session):
    """有成绩但无有效批次的学生过半（9/17 > 50%）→ 层不可计算 +
    denominator_unknown_over_half（n=8 达标、有方差，仍按契约拒绝）。"""
    _seed_all(client, v1_seed, db_session)
    jia = v1_seed.jia_h_id
    from app.db import workspace_models as wm

    db = _db()
    try:
        db.add(
            wm.ScoreFact(
                data_domain="homeroom",
                academic_year_id=v1_seed.ay_id,
                exam_name=EXAM_SHARE,
                exam_date=date(2025, 12, 6),
                class_ref_id=v1_seed.h6_id,
                identity_id=jia,
                subject="总分",
                total_type="主三门",
                score=88.0,
                source="p2-c1-test",
            )
        )
        db.commit()
    finally:
        db.close()
    data = _get(
        client, "/api/v1/homeroom/diagnosis/correlation",
        exam_name=EXAM_SHARE, window_days=14, metric="total:主三门",
        homework_type="P2C1占比",
    )
    all_layer = data["layers"]["all"]
    assert all_layer["n"] == 8
    assert all_layer["eligible_n"] == 17
    assert all_layer["excluded_no_homework"] == 9
    assert all_layer["denominator_unknown_share"] == 0.5294
    assert all_layer["reason"] == "denominator_unknown_over_half"
    assert data["r"] is None and data["rho"] is None
    # 高分段本身全员有批次 → 正常可算
    assert data["layers"]["high_score"]["status"] == "ok"


def test_no_rank_students_only_in_all_layer(client, v1_seed, db_session):
    """主三门名次不可得的学生不落段（绝不按哨兵名次落段），只进全班层。"""
    _seed_all(client, v1_seed, db_session)
    jia, yi = v1_seed.jia_h_id, v1_seed.yi_h_id
    data = _get(
        client, "/api/v1/homeroom/diagnosis/correlation",
        exam_name=EXAM_NORANK, window_days=14, metric="total:主三门",
        homework_type="P2C1无名次",
    )
    assert [p["band"] for p in data["pairs"]] == [None, None]
    for key in ("high_score", "critical", "weak"):
        assert data["layers"][key]["n"] == 0
        assert data["layers"][key]["reason"] == "n_too_small"
        assert data["sample"]["layers"][key]["eligible_n"] == 0


# ────────────────────────── 结构性不可算与参数校验 ──────────────────────────


def test_exam_date_month_precision_approximates_window(client, v1_seed, db_session):
    """旧数据只有年月（exam_date=NULL、source_exam_date="2025-11"）→ 窗口以
    该月最后一天（2025-11-30）为锚点的近似值，caveats 显式标注
    exam_date_month_precision（主控裁决：显式近似优于永久不可计算）。"""
    _seed_all(client, v1_seed, db_session)
    data = _get(
        client, "/api/v1/homeroom/diagnosis/correlation",
        exam_name=EXAM_NODATE, window_days=14, metric="total:主三门",
    )
    assert data["missing_reason"] is None
    assert data["exam_date"] == "2025-11-30"
    assert data["window_start"] == "2025-11-16"
    assert data["window_end"] == "2025-11-29"
    assert any("exam_date_month_precision" in c for c in data["caveats"])


def test_exam_date_fully_missing_not_computable(client, v1_seed, db_session):
    """exam_date 与 source_exam_date 均空 → 唯一的结构性不可算路径：
    status not_computable + exam_date_missing，窗口起止 null，绝不伪造。"""
    _seed_all(client, v1_seed, db_session)
    data = _get(
        client, "/api/v1/homeroom/diagnosis/correlation",
        exam_name="P2C1空日期考", window_days=14, metric="total:主三门",
    )
    assert data["status"] == "not_computable"
    assert data["missing_reason"] == "exam_date_missing"
    assert data["window_start"] is None and data["window_end"] is None
    assert data["layers"]["all"]["reason"] == "exam_date_missing"
    assert any("日期" in c for c in data["caveats"])


def test_metric_and_window_validation(client, v1_seed, db_session):
    """window_days 仅 14|30；metric 按 definitions.metric_meta（高二不支持
    subject:物理，支持 total:3+3）；越界考试 404。"""
    _seed_all(client, v1_seed, db_session)
    bad_window = client.get(
        "/api/v1/homeroom/diagnosis/correlation",
        params={"exam_name": EXAM_LAYER, "window_days": 15},
    )
    assert bad_window.status_code == 422
    assert bad_window.json()["error"] == "invalid_scope_param"

    bad_metric = client.get(
        "/api/v1/homeroom/diagnosis/correlation",
        params={"exam_name": EXAM_LAYER, "metric": "subject:物理"},
    )
    assert bad_metric.status_code == 422
    assert bad_metric.json()["error"] == "invalid_scope_param"

    oos = client.get(
        "/api/v1/homeroom/diagnosis/correlation",
        params={"exam_name": "P2C1不存在的考试"},
    )
    assert oos.status_code == 404
    assert oos.json()["error"] == "resource_out_of_scope"

    # 高二合法 metric（3+3 无数据 → not_computable + 原因，绝不报错崩）
    data = _get(
        client, "/api/v1/homeroom/diagnosis/correlation",
        exam_name=EXAM_LAYER, metric="total:3+3", window_days=14,
    )
    assert data["status"] == "not_computable"
    assert data["missing_reason"] == "no_valid_exam_score"
    assert data["metric_kind"] == "total_rank"


def test_teaching_domain_pinned_metric_and_band_unavailable(client, v1_seed, db_session):
    """教学域：metric 钉任教学科（传 total → 422）；无总分行 → 段位层
    band_unavailable；X/Y 均为任教学科口径（H6 批次经 link 投影）。"""
    from app.db import workspace_models as wm

    link = (
        db_session.query(wm.HomeroomTeachingLink)
        .filter(wm.HomeroomTeachingLink.id == v1_seed.link_id)
        .one()
    )
    link.valid_from = date(2025, 9, 1)
    link.share_categories = "roster,current_subject_score,current_subject_homework"
    db_session.commit()

    payload = {
        "mode": "homeroom",
        "class_id": v1_seed.h6_id,
        "academic_year_id": v1_seed.ay_id,
        "subject": "物理",
        "homework_type": "P2C1教学",
        "assigned_date": "2025-10-20",  # 2025期中（11-06）30 天窗口内
        "input": {"kind": "detailed",
                  "rows": [{"name_or_alias": "秦甲", "status": "submitted"},
                           {"name_or_alias": "秦乙", "status": "missing"}]},
    }
    p = client.post("/api/v1/homework/preview", json=payload)
    assert p.status_code == 200, p.text
    assert client.post(
        "/api/v1/homework/confirm", json={"token": p.json()["token"]}
    ).status_code == 200

    total_metric = client.get(
        "/api/v1/teaching/diagnosis/correlation",
        params={"academic_year_id": v1_seed.ay_id,
                "teaching_class_id": v1_seed.t6_id,
                "exam_name": "2025期中", "metric": "total:主三门"},
    )
    assert total_metric.status_code == 422
    assert total_metric.json()["error"] == "invalid_scope_param"

    data = _get(
        client, "/api/v1/teaching/diagnosis/correlation",
        academic_year_id=v1_seed.ay_id,
        teaching_class_id=v1_seed.t6_id,
        exam_name="2025期中", window_days=30,
    )
    assert data["metric"] == "subject:物理"
    assert data["metric_kind"] == "subject_score"
    assert data["homework_subject"] == "物理"
    assert data["n"] == 2
    assert data["pairs"] == [
        {"person_id": v1_seed.jia_t_id, "name": "秦甲·T", "x": 1.0,
         "y": 91.0, "band": None},
        {"person_id": v1_seed.yi_t_id, "name": "秦乙·T", "x": 0.0,
         "y": 85.0, "band": None},
    ]
    for key in ("high_score", "critical", "weak"):
        assert data["layers"][key]["reason"] == "band_unavailable"
        assert data["layers"][key]["r"] is None
    assert data["layers"]["all"]["reason"] == "n_too_small"
    assert "段位" in data["note"]


# ────────────────────────── 响应形状与表述边界 ──────────────────────────


def test_note_and_direction_semantics(client, v1_seed, db_session):
    """note 必含方向与指标含义解释 + 「不构成因果或提分保证」；direction
    值域为分数口径（submit_up_score_up/down）；响应必含契约 §2.4 键集。"""
    _seed_all(client, v1_seed, db_session)
    data = _get(
        client, "/api/v1/homeroom/diagnosis/correlation",
        exam_name=EXAM_LAYER, window_days=14, metric="total:主三门",
        homework_type="P2C1分层",
    )
    assert "不构成因果" in data["note"]
    assert "提分保证" in data["note"]
    assert "提交率" in data["note"] and "Spearman" in data["note"]
    assert data["direction"] in (
        "submit_up_score_up", "submit_up_score_down", None,
    )
    assert data["calc_version"] == "p2-v1"
    for key in (
        "window_days", "window_start", "window_end", "sample", "note",
        "calc_version", "layers", "pairs", "n", "r", "rho", "direction",
        "caveats", "metadata", "metric",
    ):
        assert key in data, key
    # 建议不越界：响应不出现任何因果断言/提分承诺字段
    for forbidden in ("cause", "causal", "guarantee", "提升保证"):
        assert forbidden not in json.dumps(data, ensure_ascii=False).lower()
