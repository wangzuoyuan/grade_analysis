"""S03 不自动合并：同名同号跨域隔离、无 link 的教学班成员绝不入 H 侧。

样本（见 tests/v1/conftest.py）：
- 戊（T8）display_name 与甲（H6）相同「秦甲」，教学别名 2025T8-01 与
  甲的行政别名 2025H6-01 同裸号 01。T8 与 H6 无 link。
- 任何 H 侧响应（students / scores / 画像）不得出现戊、己、丁，
  也不得因同名把戊并进甲的行或反之多出一行。
"""

API = "/api/v1"
EXAM_E1 = "2025期中"


def _homeroom_students(client, seed):
    r = client.get(
        f"{API}/homeroom/students",
        params={"academic_year_id": seed.ay_id, "class_id": seed.h6_id},
    )
    assert r.status_code == 200
    return r.json()


def _homeroom_scores(client, seed):
    r = client.get(
        f"{API}/scores",
        params={"mode": "homeroom", "academic_year_id": seed.ay_id, "exam_name": EXAM_E1},
    )
    assert r.status_code == 200
    return r.json()


def test_homeroom_roster_never_merges_same_name_same_bare_sid(client, v1_seed):
    """用戊的名字/裸号在 H 名册检索：不出现、不并档、不放大人数。"""
    body = _homeroom_students(client, v1_seed)
    students = body["students"]

    assert body["metadata"]["cohort_size"] == 3
    names = [s["name"] for s in students]
    assert sorted(names) == sorted(["秦甲", "秦乙", "秦丙"])
    # 戊与甲同名：甲只出现一次，绝不因同名同号多出一行（自动合并的典型症状）
    assert names.count("秦甲") == 1

    for s in students:
        alias = s.get("alias")
        # H 名册只挂 H 域别名；戊的教学域别名（同裸号 01）绝不出现
        assert alias and alias.startswith("2025H6")
        assert alias != "2025T8-01"


def test_homeroom_side_excludes_unlinked_teaching_members(client, v1_seed):
    """T8 与 H6 无 link：H 侧 students/scores/画像均不含戊己，丁不入 H。"""
    h_person_ids = {s["person_id"] for s in _homeroom_students(client, v1_seed)["students"]}
    assert h_person_ids == set(v1_seed.h_person_ids)

    rows = _homeroom_scores(client, v1_seed)["rows"]
    row_persons = {r["person_id"] for r in rows}
    # H 侧成绩只见甲乙丙（丙为 H 域原生；甲乙经 link 的物理可投影）
    assert row_persons == h_person_ids
    for banned in (v1_seed.ding_t_id, v1_seed.wu_t_id, v1_seed.ji_t_id):
        assert banned not in row_persons

    # 即使个别行来自 teaching 域，也只允许 link 成员交集（甲乙），
    # 绝不允许无 link 的 T6-only 丁或 T8 戊己借道混入
    for r in rows:
        if r.get("source_domain") == "teaching":
            assert r["person_id"] in {v1_seed.jia_h_id, v1_seed.yi_h_id}

    # 跨域画像守卫：戊/己的教学域 person 在 H 画像端点必须 404
    for person_id in (v1_seed.wu_t_id, v1_seed.ji_t_id):
        r = client.get(f"{API}/homeroom/students/{person_id}")
        assert r.status_code == 404
        assert r.json().get("error") == "resource_out_of_scope"
