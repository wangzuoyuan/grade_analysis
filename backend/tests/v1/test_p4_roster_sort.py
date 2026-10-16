"""P4 契约用例：名册排序——按学号自然升序，无学号按姓名拼音排最后。

排序键实现于 app/api/_queries.py roster_sid_sort_key（用户口径：座号就是
学号，同一概念）：
- 有效学号 = alias 优先，缺时用座号值顶上；先剥跨届前缀（G1::/g1-）再
  自然排序（数字段按数值比较，999999 < 1234567，而非字典序）。
- 无学号者排在有学号学生之后，按姓名拼音（姓氏首字母优先）；
- person_id 终兜底。
homeroom/teaching 名册端点与教学班成员接口共用该键（接口序=页面序）。
"""

API = "/api/v1"


def _homeroom_names(client, seed):
    r = client.get(
        f"{API}/homeroom/students",
        params={"class_id": seed.h6_id, "academic_year_id": seed.ay_id},
    )
    assert r.status_code == 200
    return [s["name"] for s in r.json()["students"]]


def test_homeroom_roster_sid_natural_then_pinyin(client, v1_seed):
    # 种子甲乙丙：2025H6-01/02/03（自然序恰与旧座号序 1/2/3 一致，不回归）。
    # 追加混合样本覆盖四类口径：座号兜底、跨届前缀、位数不同、无学号拼音。
    payloads = [
        {"name": "秦六位", "alias": "999999"},
        {"name": "秦七位", "alias": "1234567"},
        {"name": "秦前缀", "alias": "G1::2025H6-00"},
        {"name": "陈无号"},
        {"name": "王无号"},
        {"name": "秦座号", "seat_no": 5},
    ]
    for payload in payloads:
        r = client.post(f"{API}/homeroom/students", json=payload)
        assert r.status_code == 200, r.text

    names = _homeroom_names(client, v1_seed)
    # 座号值 5 数值上小于 2025 → 学号组内最前；G1:: 前缀剥后按 2025H6-00
    # 参与排序；999999 < 1234567 证明数值序；无学号者（陈/王）排最后按
    # 拼音 chen < wang。
    assert names == [
        "秦座号",
        "秦前缀",
        "秦甲",
        "秦乙",
        "秦丙",
        "秦六位",
        "秦七位",
        "陈无号",
        "王无号",
    ]


def test_teaching_roster_and_members_share_sid_order(client, v1_seed):
    t8 = v1_seed.t8_id
    # 种子 T8：秦甲 2025T8-01、秦己·T 2025T8-02。追加无学号（拼音组）
    # 与小数字学号（自然序最前）样本。
    for payload in (
        {"name": "陈教无"},
        {"name": "王教无"},
        {"name": "李教号", "alias": "42"},
    ):
        r = client.post(f"{API}/teaching/classes/{t8}/members", json=payload)
        assert r.status_code == 200, r.text

    r = client.get(f"{API}/teaching/students", params={"teaching_class_id": t8})
    assert r.status_code == 200
    names = [s["name"] for s in r.json()["students"]]
    assert names == ["李教号", "秦甲", "秦己·T", "陈教无", "王教无"]

    # 班级信息页成员接口与名册端点同序
    r2 = client.get(f"{API}/teaching/classes/{t8}/members")
    assert r2.status_code == 200
    body = r2.json()
    assert [m["name"] for m in body["active"]] == names
    assert body["left"] == []
