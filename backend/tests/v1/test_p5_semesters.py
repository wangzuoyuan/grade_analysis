"""P5 H06：学期管理（契约 docs/contracts/p5-homework.md §5）。

- auto：该学年无学期行时按学年日期二分推导（id=null，绝不猜今天属于哪
  学期之外的内容）；有行即 auto=false。
- 冲突（同学年重名/日期重叠/start>end）一律 422 不 500；学年不存在 404。
- 设当前学期：切换清零旧 current，重复设置 422。
- restore-auto：删手工行回自动推导，返回 before/after 对比。

注意模块内测试共享 DB，用例顺序即状态推进顺序（auto → 手工 → 冲突 →
编辑 → 当前 → 还原）。
"""

from datetime import date

from app.db import workspace_models as wm


def _get(client, ay_id):
    return client.get("/api/v1/homework/semesters", params={"academic_year_id": ay_id})


def test_h06_auto_derivation_bisects_academic_year(client, v1_seed):
    """无行时按学年日期二分推导上/下学期：id=null、首尾贴合学年、
    两段连续（下学期.start = 上学期.end + 1 天）、至多一个 current。"""
    resp = _get(client, v1_seed.ay_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["auto"] is True
    assert body["academic_year_name"] == "2025-2026"
    assert [s["name"] for s in body["semesters"]] == ["上学期", "下学期"]
    first, second = body["semesters"]
    assert first["id"] is None and second["id"] is None
    assert first["start_date"] == "2025-09-01"
    assert second["end_date"] == "2026-07-15"
    # 二分点：317 天的中点 158 天 → 2026-02-06 / 2026-02-07
    assert first["end_date"] == "2026-02-06"
    assert second["start_date"] == "2026-02-07"
    assert all(s["mode"] == "auto" for s in body["semesters"])
    assert sum(1 for s in body["semesters"] if s["is_current"]) <= 1


def test_h06_create_manual_turns_auto_off(client, v1_seed):
    """落一行手工学期后 auto=false，返回表内实际值。"""
    resp = client.post(
        "/api/v1/homework/semesters",
        json={
            "academic_year_id": v1_seed.ay_id,
            "name": "秋季学期",
            "start_date": "2025-09-01",
            "end_date": "2026-01-25",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["auto"] is False
    assert len(body["semesters"]) == 1
    row = body["semesters"][0]
    assert row["name"] == "秋季学期"
    assert row["mode"] == "manual"
    assert row["is_current"] is False

    listing = _get(client, v1_seed.ay_id).json()
    assert listing["auto"] is False
    assert [s["name"] for s in listing["semesters"]] == ["秋季学期"]


def test_h06_create_conflicts_rejected(client, v1_seed):
    """同学年重名 / 日期重叠 / start>end → 422；学年不存在 → 404。"""
    base = {"academic_year_id": v1_seed.ay_id}

    dup = client.post(
        "/api/v1/homework/semesters",
        json={**base, "name": "秋季学期",
              "start_date": "2026-02-01", "end_date": "2026-06-01"},
    )
    assert dup.status_code == 422, dup.text
    assert dup.json()["error"] == "invalid_scope_param"

    overlap = client.post(
        "/api/v1/homework/semesters",
        json={**base, "name": "第一学段",
              "start_date": "2026-01-10", "end_date": "2026-03-01"},
    )
    assert overlap.status_code == 422, overlap.text

    inverted = client.post(
        "/api/v1/homework/semesters",
        json={**base, "name": "倒置学期",
              "start_date": "2026-05-01", "end_date": "2026-04-01"},
    )
    assert inverted.status_code == 422, inverted.text

    missing_year = client.post(
        "/api/v1/homework/semesters",
        json={**base, "academic_year_id": 99999, "name": "幽灵学期",
              "start_date": "2025-09-01", "end_date": "2026-01-25"},
    )
    assert missing_year.status_code == 404, missing_year.text

    # 冲突请求零写入
    listing = _get(client, v1_seed.ay_id).json()
    assert [s["name"] for s in listing["semesters"]] == ["秋季学期"]


def test_h06_update_semester_flow(client, v1_seed):
    """PUT：改日期/名称；与既有学期重叠 422、重名 422、合法更新 200。"""
    created = client.post(
        "/api/v1/homework/semesters",
        json={**{"academic_year_id": v1_seed.ay_id}, "name": "春季学期",
              "start_date": "2026-02-10", "end_date": "2026-07-10"},
    )
    assert created.status_code == 200, created.text
    spring_id = created.json()["semesters"][0]["id"]

    overlap = client.put(
        f"/api/v1/homework/semesters/{spring_id}",
        json={"start_date": "2026-01-20"},  # 与秋季学期 [.., 2026-01-25] 重叠
    )
    assert overlap.status_code == 422, overlap.text
    assert overlap.json()["error"] == "invalid_scope_param"

    dup_name = client.put(
        f"/api/v1/homework/semesters/{spring_id}", json={"name": "秋季学期"}
    )
    assert dup_name.status_code == 422, dup_name.text

    ok = client.put(
        f"/api/v1/homework/semesters/{spring_id}",
        json={"start_date": "2026-02-08", "end_date": "2026-07-12"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["semesters"][0]["start_date"] == "2026-02-08"
    assert ok.json()["semesters"][0]["mode"] == "manual"

    missing = client.put(
        "/api/v1/homework/semesters/999999", json={"name": "不存在"}
    )
    assert missing.status_code == 404, missing.text


def test_h06_current_semester_switch_and_repeat(client, v1_seed):
    """设当前：重复设同一条 422；切换后旧 current 清零。"""
    listing = _get(client, v1_seed.ay_id).json()
    by_name = {s["name"]: s["id"] for s in listing["semesters"]}
    autumn_id, spring_id = by_name["秋季学期"], by_name["春季学期"]

    first = client.put(f"/api/v1/homework/semesters/{autumn_id}/current")
    assert first.status_code == 200, first.text
    assert first.json()["is_current"] is True

    repeat = client.put(f"/api/v1/homework/semesters/{autumn_id}/current")
    assert repeat.status_code == 422, repeat.text
    assert repeat.json()["error"] == "invalid_scope_param"

    switch = client.put(f"/api/v1/homework/semesters/{spring_id}/current")
    assert switch.status_code == 200, switch.text

    after = _get(client, v1_seed.ay_id).json()
    flags = {s["name"]: s["is_current"] for s in after["semesters"]}
    assert flags == {"秋季学期": False, "春季学期": True}

    current = client.get("/api/v1/homework/current-semester")
    assert current.status_code == 200, current.text
    assert current.json() == {
        "id": spring_id,
        "academic_year_id": v1_seed.ay_id,
        "academic_year_name": "2025-2026",
        "name": "春季学期",
        "start_date": "2026-02-08",
        "end_date": "2026-07-12",
        "mode": "manual",
    }


def test_h06_current_semester_is_global_across_academic_years(client, v1_seed, db_session):
    """新学年设为当前后，旧学年不得保留另一个 current。"""
    new_year = wm.AcademicYear(
        name="2026-2027", start_date=date(2026, 9, 1), end_date=date(2027, 7, 15)
    )
    db_session.add(new_year)
    db_session.flush()
    new_semester = wm.WsHomeworkSemester(
        academic_year_id=new_year.id,
        name="上学期",
        start_date=date(2026, 9, 1),
        end_date=date(2027, 1, 31),
        is_current=0,
        mode="manual",
    )
    db_session.add(new_semester)
    db_session.commit()

    switched = client.put(f"/api/v1/homework/semesters/{new_semester.id}/current")
    assert switched.status_code == 200, switched.text
    assert (
        db_session.query(wm.WsHomeworkSemester)
        .filter(wm.WsHomeworkSemester.is_current == 1)
        .count()
        == 1
    )
    current = client.get("/api/v1/homework/current-semester").json()
    assert current["academic_year_id"] == new_year.id
    assert current["id"] == new_semester.id
    db_session.query(wm.WsHomeworkSemester).filter(
        wm.WsHomeworkSemester.id == new_semester.id
    ).delete(synchronize_session=False)
    db_session.commit()


def test_h06_restore_auto_returns_before_after(client, v1_seed, db_session):
    """restore-auto：删手工行回自动推导（before/after 对比）；
    两行都还原后 GET 回到 auto=true；还原后的 id 再 PUT → 404。"""
    listing = _get(client, v1_seed.ay_id).json()
    ids = [s["id"] for s in listing["semesters"]]

    first = client.post(f"/api/v1/homework/semesters/{ids[0]}/restore-auto")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["restored"] is True
    assert body["before"]["name"] in {"秋季学期", "春季学期"}
    assert [s["name"] for s in body["after"]] == ["上学期", "下学期"]

    # 还剩一行手工数据 → 仍 auto=false
    still_manual = _get(client, v1_seed.ay_id).json()
    assert still_manual["auto"] is False

    second = client.post(f"/api/v1/homework/semesters/{ids[1]}/restore-auto")
    assert second.status_code == 200, second.text

    back_to_auto = _get(client, v1_seed.ay_id).json()
    assert back_to_auto["auto"] is True
    assert all(s["id"] is None for s in back_to_auto["semesters"])

    gone = client.put(
        f"/api/v1/homework/semesters/{ids[0]}", json={"name": "已删除"}
    )
    assert gone.status_code == 404, gone.text

    # 表已清空：DB 里不应残留任何学期行（物理删除仅限手工配置行）
    left = db_session.query(wm.WsHomeworkSemester).count()
    assert left == 0
