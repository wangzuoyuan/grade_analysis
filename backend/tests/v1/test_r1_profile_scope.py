"""R1 回归：homeroom 个人画像只含目标本人的成绩事实。

背景（审核 R1）：homeroom_student_profile 此前取全班事实后未按
person_id 过滤，甲的画像返回甲乙丙三人成绩（语文 88/76/65 三条、总分
275/236/207）。修复后每科每场、总分只含目标人；同场冲突按 v2 §1.4.1
保留本域值并附 shared_conflict（见 test_r5）。
"""

API = "/api/v1"
EXAM_E1 = "2025期中"

# 甲（H 域）样本值，见 tests/v1/conftest.py
JIA_SCORES = {"语文": 88.0, "数学": 92.0, "英语": 95.0, "物理": 90.0}


def _profile(client, person_id):
    r = client.get(f"{API}/homeroom/students/{person_id}")
    assert r.status_code == 200
    return r.json()


def _all_exams(body):
    exams = [e for g in body["subjects"] for e in g["exams"]]
    exams += [e for g in (body.get("totals") or []) for e in g["exams"]]
    return exams


def test_homeroom_profile_subjects_only_target_person(client, v1_seed):
    """甲画像每科恰 1 条且均为甲的值，绝不出现乙丙的同科成绩。"""
    body = _profile(client, v1_seed.jia_h_id)
    by_subject = {g["subject"]: g["exams"] for g in body["subjects"]}
    assert set(by_subject) == set(JIA_SCORES)

    for subject, expected in JIA_SCORES.items():
        exams = by_subject[subject]
        assert len(exams) == 1, (subject, exams)
        assert exams[0]["score"] == expected
        assert exams[0]["exam_name"] == EXAM_E1
        assert exams[0]["source_domain"] == "homeroom"


def test_homeroom_profile_totals_only_target_person(client, v1_seed):
    """总分只含甲的 275，乙丙的 236/207 不混入。"""
    body = _profile(client, v1_seed.jia_h_id)
    totals = {g["total_type"]: g["exams"] for g in (body.get("totals") or [])}
    assert set(totals) == {"主三门"}
    assert len(totals["主三门"]) == 1
    assert totals["主三门"][0]["score"] == 275.0
    assert totals["主三门"][0]["source_domain"] == "homeroom"


def test_homeroom_profile_never_leaks_classmate_scores(client, v1_seed):
    """甲画像任何 score 字段绝不含他人值；91 只允许出现在物理条目的
    shared_conflict 提示里，不得成为一条独立成绩。"""
    body = _profile(client, v1_seed.jia_h_id)
    scores = {e["score"] for e in _all_exams(body)}
    assert scores == {88.0, 92.0, 95.0, 90.0, 275.0}
    # 乙丙全科+总分、teaching 域 91/85 都不得出现
    assert not scores & {
        76.0, 81.0, 79.0, 84.0, 236.0,
        65.0, 70.0, 72.0, 68.0, 207.0,
        91.0, 85.0,
    }

    physics = next(g for g in body["subjects"] if g["subject"] == "物理")["exams"]
    assert len(physics) == 1
    assert physics[0]["score"] == 90.0
    assert physics[0].get("shared_conflict") == {"teaching_score": 91.0}


def test_homeroom_profile_per_person_isolation(client, v1_seed):
    """乙画像同样只含乙本人值，防止只修甲路径的假绿。"""
    body = _profile(client, v1_seed.yi_h_id)
    by_subject = {g["subject"]: g["exams"] for g in body["subjects"]}
    assert by_subject["语文"][0]["score"] == 76.0
    assert by_subject["物理"][0]["score"] == 84.0
    assert by_subject["物理"][0].get("shared_conflict") == {"teaching_score": 85.0}
    totals = {g["total_type"]: g["exams"] for g in (body.get("totals") or [])}
    assert totals["主三门"][0]["score"] == 236.0
