"""S02 关联班受控共享：H 名册见任教学科投影，T 名册见 H 姓名投影。

样本（见 tests/v1/conftest.py）：
- H6 成员甲乙丙；T6 成员甲乙丁；甲乙经 active link 关联。
- 甲：H 物理 90 / T 物理 91；乙：H 物理 84 / T 物理 85 —— 两域同场值
  冲突，v2 契约 §1.4.1 下不静默取 teaching 值：shared_subject_score
  不出现，改报 shared_conflict={"teaching_score": ...}。甲教学域名
  「秦甲·T」，H 名册名「秦甲」——teaching/students 中甲乙必须显示
  H 名册名。
"""

import pytest

API = "/api/v1"
EXAM_E1 = "2025期中"


def _homeroom_students(client, seed):
    r = client.get(
        f"{API}/homeroom/students",
        params={"academic_year_id": seed.ay_id, "class_id": seed.h6_id},
    )
    assert r.status_code == 200
    return r.json()


def _teaching_students(client, seed):
    r = client.get(
        f"{API}/teaching/students",
        params={"academic_year_id": seed.ay_id, "teaching_class_id": seed.t6_id},
    )
    assert r.status_code == 200
    return r.json()


def _scores(client, mode, seed):
    r = client.get(
        f"{API}/scores",
        params={"mode": mode, "academic_year_id": seed.ay_id, "exam_name": EXAM_E1},
    )
    assert r.status_code == 200
    return r.json()


def test_homeroom_students_project_shared_physics(client, v1_seed):
    body = _homeroom_students(client, v1_seed)
    meta = body["metadata"]
    assert meta["mode"] == "homeroom"
    assert meta["subject"] is None
    for key in ("scope", "cohort_size", "data_revision"):
        assert key in meta, key
    assert meta["cohort_size"] == 3

    students = body["students"]
    assert len(students) == 3
    assert {s["person_id"] for s in students} == set(v1_seed.h_person_ids)
    by_pid = {s["person_id"]: s for s in students}

    jia = by_pid[v1_seed.jia_h_id]
    assert jia["name"] == "秦甲"
    assert jia.get("alias") == "2025H6-01"
    assert jia.get("linked_teaching_class_id") == v1_seed.t6_id
    # v2 契约 §1.4.1：甲 H 物理 90 / T 物理 91 同场冲突——绝不静默取
    # teaching 值，shared_subject_score 不出现，改报冲突提示字段
    assert jia.get("shared_subject_score") is None
    assert jia.get("shared_conflict") == {"teaching_score": pytest.approx(91.0)}

    yi = by_pid[v1_seed.yi_h_id]
    assert yi.get("linked_teaching_class_id") == v1_seed.t6_id
    assert yi.get("shared_subject_score") is None
    assert yi.get("shared_conflict") == {"teaching_score": pytest.approx(85.0)}

    # 丙未关联：不得出现共享投影/冲突字段有值
    bing = by_pid[v1_seed.bing_h_id]
    assert bing.get("shared_subject_score") is None
    assert bing.get("shared_conflict") is None
    assert bing.get("linked_teaching_class_id") is None


def test_teaching_students_use_homeroom_roster_projection(client, v1_seed):
    body = _teaching_students(client, v1_seed)
    meta = body["metadata"]
    assert meta["mode"] == "teaching"
    assert meta["subject"] == "物理"
    assert meta["cohort_size"] == 3

    students = body["students"]
    assert len(students) == 3
    assert {s["person_id"] for s in students} == set(v1_seed.t6_person_ids)
    by_pid = {s["person_id"]: s for s in students}

    # 甲乙经 link 用 H 名册投影（姓名/座号）：显示「秦甲/秦乙」而非教学域名
    assert by_pid[v1_seed.jia_t_id]["name"] == "秦甲"
    assert by_pid[v1_seed.yi_t_id]["name"] == "秦乙"
    # 丁只有 teaching 身份：用教学域原始名
    assert by_pid[v1_seed.ding_t_id]["name"] == "秦丁·T"

    # 教学侧响应不得携带 total 字段
    for s in students:
        assert "total_type" not in s
        assert "total_score" not in s


def test_homeroom_scores_keep_totals_and_other_subjects(client, v1_seed):
    body = _scores(client, "homeroom", v1_seed)
    assert body["metadata"]["mode"] == "homeroom"
    rows = body["rows"]
    assert rows

    # 班主任侧保留合法全科与总分
    assert sum(1 for r in rows if r.get("total_type") == "主三门") == 3
    assert sum(1 for r in rows if r.get("subject") == "语文") == 3
    assert sum(1 for r in rows if r.get("subject") == "物理") == 3
    # 关联班任教学科可来自 teaching 域（来源标记而非复制全科）
    assert all(
        r.get("source_domain") in (None, "homeroom", "teaching") for r in rows
    )


def test_teaching_scores_subject_only_without_totals(client, v1_seed):
    body = _scores(client, "teaching", v1_seed)
    rows = body["rows"]
    assert rows, "T6 在 E1 应有物理成绩行"

    # 教学模式只可见任教学科：无语文、无总分、无 total 字段
    assert {r["subject"] for r in rows} == {"物理"}
    for r in rows:
        assert "total_type" not in r
        assert r["subject"] != "语文"

    # 只见教学域成员（T6/T8），绝无 H-only 学生丙
    persons = {r["person_id"] for r in rows}
    allowed = set(v1_seed.t6_person_ids) | set(v1_seed.t8_person_ids)
    assert persons <= allowed
    assert v1_seed.bing_h_id not in persons

    # 丁缺考：行存在、score 为 null（显示「—」），不得转 0
    ding_rows = [r for r in rows if r["person_id"] == v1_seed.ding_t_id]
    assert len(ding_rows) == 1
    assert ding_rows[0]["score"] is None
