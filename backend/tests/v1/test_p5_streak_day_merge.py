"""P5 连续缺交按天口径重写（预警端点与画像端点共用同一算法）。

- 教学：取消「按作业种类」分线，改为（域, 班）按天单线——同日任一批次缺交
  该天计 1（同日他批次默认已交不打断）、已交清零停止、忘带/请假跳过、
  不同作业种类的缺交可跨日成链；streak_homework_type 恒 None。
- 班主任：维持按（域, 班, 学科）分线取最大，但事件链先按天合并——
  同日同学科多批次都缺只计 1 天。
- 画像端点 streaks 与预警端点 current 完全同值（_streak_lines_of 单一实现）。

模块内日期留隔离：每个教学用例以一个「全体已交」屏障日开头，阻断前序
用例遗留缺交日的串链（也顺带回归「交了清零」）。
"""

API = "/api/v1"


def _confirm(client, seed, *, mode, day, rows, homework_type, subject="物理"):
    """确认一个 detailed 批次，返回 assignment_id。rows 用 person_id 定位。"""
    payload = {
        "mode": mode,
        "academic_year_id": seed.ay_id,
        "subject": subject,
        "homework_type": homework_type,
        "assigned_date": day,
        "input": {"kind": "detailed", "rows": rows},
    }
    payload["teaching_class_id" if mode == "teaching" else "class_id"] = (
        seed.t8_id if mode == "teaching" else seed.h6_id
    )
    preview = client.post(f"{API}/homework/preview", json=payload)
    assert preview.status_code == 200, preview.text
    confirmed = client.post(
        f"{API}/homework/confirm", json={"token": preview.json()["token"]}
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()["assignment_id"]


def _warnings(client, seed, *, mode, **params):
    query = {"mode": mode, "min_missing": 1, **params}
    if mode == "teaching":
        query.update({
            "teaching_class_id": seed.t8_id,
            "academic_year_id": seed.ay_id,
        })
    else:
        query.update({"class_id": seed.h6_id, "academic_year_id": seed.ay_id})
    resp = client.get(f"{API}/homework/warnings", params=query)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _student_row(body, person_id):
    return next(s for s in body["students"] if s["person_id"] == person_id)


def _profile(client, seed, person_id, *, mode):
    query = {"mode": mode}
    if mode == "teaching":
        query.update({
            "teaching_class_id": seed.t8_id,
            "academic_year_id": seed.ay_id,
        })
    else:
        query.update({"class_id": seed.h6_id, "academic_year_id": seed.ay_id})
    resp = client.get(f"{API}/homework/students/{person_id}", params=query)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_teaching_same_day_missing_and_submitted_counts_one_day(client, v1_seed):
    """同日两种作业（练习册缺、周末卷交）→ 该天计 1，不是 2 也不被打断；
    streak_homework_type 恒 None（按天单线后不再按种类标注）。"""
    _confirm(client, v1_seed, mode="teaching", day="2025-11-20",
             homework_type="练习册",
             rows=[{"person_id": v1_seed.wu_t_id, "status": "missing"}])
    _confirm(client, v1_seed, mode="teaching", day="2025-11-20",
             homework_type="周末卷",
             rows=[{"person_id": v1_seed.wu_t_id, "status": "submitted"}])

    body = _warnings(client, v1_seed, mode="teaching")
    row = _student_row(body, v1_seed.wu_t_id)
    assert row["missing_count"] == 1
    assert row["current_streak"] == 1
    assert row["streak_basis"] == "events"
    assert row["streak_homework_type"] is None
    assert row["streak_subject"] is None


def test_teaching_submitted_day_resets_before_missing_day(client, v1_seed):
    """前一天交、后一天缺 → current=1（交了清零，不与更早缺交串链：
    2025-11-20 的缺交被 11-21 的已交日阻断）。"""
    _confirm(client, v1_seed, mode="teaching", day="2025-11-21",
             homework_type="练习册",
             rows=[{"person_id": v1_seed.wu_t_id, "status": "submitted"}])
    _confirm(client, v1_seed, mode="teaching", day="2025-11-22",
             homework_type="练习册",
             rows=[{"person_id": v1_seed.wu_t_id, "status": "missing"}])

    row = _student_row(
        _warnings(client, v1_seed, mode="teaching"), v1_seed.wu_t_id
    )
    assert row["current_streak"] == 1


def test_teaching_forgot_and_excused_days_are_skipped(client, v1_seed):
    """当天只有忘带（缺交+忘带评语）或请假 → 跳过：不计数也不打断
    （屏障日已交、纯缺、忘带、请假、纯缺 → current=2；纯缺交数只 +2，
    忘带那一次不计入纯缺交）。模块内 DB 共享：missing_count 用前后差值断言。"""
    before = _student_row(
        _warnings(client, v1_seed, mode="teaching"), v1_seed.wu_t_id
    )["missing_count"]

    _confirm(client, v1_seed, mode="teaching", day="2025-11-23",
             homework_type="练习册",
             rows=[{"person_id": v1_seed.wu_t_id, "status": "submitted"}])
    _confirm(client, v1_seed, mode="teaching", day="2025-11-24",
             homework_type="练习册",
             rows=[{"person_id": v1_seed.wu_t_id, "status": "missing"}])
    _confirm(client, v1_seed, mode="teaching", day="2025-11-25",
             homework_type="练习册",
             rows=[{"person_id": v1_seed.wu_t_id, "status": "missing",
                    "evaluation": "忘带"}])
    _confirm(client, v1_seed, mode="teaching", day="2025-11-26",
             homework_type="练习册",
             rows=[{"person_id": v1_seed.wu_t_id, "status": "excused"}])
    _confirm(client, v1_seed, mode="teaching", day="2025-11-27",
             homework_type="练习册",
             rows=[{"person_id": v1_seed.wu_t_id, "status": "missing"}])

    row = _student_row(
        _warnings(client, v1_seed, mode="teaching"), v1_seed.wu_t_id
    )
    assert row["missing_count"] == before + 2
    assert row["current_streak"] == 2


def test_teaching_missing_days_chain_across_homework_types(client, v1_seed):
    """连续两天各有缺（不同作业种类）→ 按天成链 current=2；画像端点与
    预警端点同一口径，current 相等。"""
    _confirm(client, v1_seed, mode="teaching", day="2025-11-28",
             homework_type="练习册",
             rows=[{"person_id": v1_seed.wu_t_id, "status": "submitted"}])
    _confirm(client, v1_seed, mode="teaching", day="2025-11-29",
             homework_type="练习册",
             rows=[{"person_id": v1_seed.wu_t_id, "status": "missing"}])
    _confirm(client, v1_seed, mode="teaching", day="2025-11-30",
             homework_type="周末卷",
             rows=[{"person_id": v1_seed.wu_t_id, "status": "missing"}])

    row = _student_row(
        _warnings(client, v1_seed, mode="teaching"), v1_seed.wu_t_id
    )
    profile = _profile(client, v1_seed, v1_seed.wu_t_id, mode="teaching")
    assert row["current_streak"] == 2
    assert profile["streaks"]["current_missing_streak"] == 2
    assert profile["streaks"]["streak_basis"] == "events"
    assert row["current_streak"] == profile["streaks"]["current_missing_streak"]


def test_homeroom_same_subject_two_missing_batches_same_day_count_one(
    client, v1_seed
):
    """班主任同日同学科两批次（卷子+订正）都缺 → 按天只计 1（不再按事件
    数算 2）；画像端点与预警端点 current 相等。"""
    _confirm(client, v1_seed, mode="homeroom", day="2025-12-01",
             homework_type="卷子",
             rows=[{"person_id": v1_seed.jia_h_id, "status": "missing"}])
    _confirm(client, v1_seed, mode="homeroom", day="2025-12-01",
             homework_type="订正",
             rows=[{"person_id": v1_seed.jia_h_id, "status": "missing"}])

    row = _student_row(
        _warnings(client, v1_seed, mode="homeroom"), v1_seed.jia_h_id
    )
    assert row["missing_count"] == 2
    assert row["current_streak"] == 1
    assert row["streak_subject"] == "物理"
    assert row["streak_homework_type"] is None

    profile = _profile(client, v1_seed, v1_seed.jia_h_id, mode="homeroom")
    assert profile["streaks"]["current_missing_streak"] == 1
    assert profile["streaks"]["streak_basis"] == "events"
    assert row["current_streak"] == profile["streaks"]["current_missing_streak"]
