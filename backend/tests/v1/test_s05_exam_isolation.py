"""S05 考试隔离：E1 同场考试含 T6/T8 教学行，H 侧只见本班 + link 交集。

样本（见 tests/v1/conftest.py）：E1 下 teaching 域有 T6（甲乙丁）与
T8（戊己）物理行。homeroom 模式查询 E1 时，可见人员必须恰为甲乙丙
（丙来自 H 域全科；甲乙物理经 link 投影），绝无丁戊己；同一
（人, 科目/总分）不得因两域并存而重复计入放大人数。
"""

API = "/api/v1"
EXAM_E1 = "2025期中"


def _homeroom_person_ids(client, seed):
    r = client.get(
        f"{API}/homeroom/students",
        params={"academic_year_id": seed.ay_id, "class_id": seed.h6_id},
    )
    assert r.status_code == 200
    return {s["person_id"] for s in r.json()["students"]}


def _homeroom_score_rows(client, seed):
    r = client.get(
        f"{API}/scores",
        params={"mode": "homeroom", "academic_year_id": seed.ay_id, "exam_name": EXAM_E1},
    )
    assert r.status_code == 200
    return r.json()["rows"]


def test_homeroom_exam_rows_limited_to_own_roster_plus_link_members(client, v1_seed):
    h_person_ids = _homeroom_person_ids(client, v1_seed)
    rows = _homeroom_score_rows(client, v1_seed)

    persons = {r["person_id"] for r in rows}
    assert persons == h_person_ids
    # T6-only 丁、T8 戊己（含与甲同名的戊）绝不出现
    for banned in (v1_seed.ding_t_id, v1_seed.wu_t_id, v1_seed.ji_t_id):
        assert banned not in persons


def test_homeroom_exam_rows_not_duplicated_across_domains(client, v1_seed):
    """两域并存不重复：每人恰 4 科 + 主三门总分 = 15 行、无重复键。"""
    rows = _homeroom_score_rows(client, v1_seed)
    assert len(rows) == 15

    keys = {
        (r["person_id"], r.get("subject"), r.get("total_type")) for r in rows
    }
    assert len(keys) == 15
    assert sum(1 for r in rows if r.get("total_type") == "主三门") == 3
    assert sum(1 for r in rows if r.get("subject") == "物理") == 3

    # 教学域来源行只允许落在 link 成员（甲乙）上，且每人至多一条物理
    teaching_rows = [r for r in rows if r.get("source_domain") == "teaching"]
    for r in teaching_rows:
        assert r["person_id"] in {v1_seed.jia_h_id, v1_seed.yi_h_id}
    physics_persons = [r["person_id"] for r in rows if r.get("subject") == "物理"]
    assert len(physics_persons) == len(set(physics_persons)) == 3
