"""R6 回归：从空关联全经 API 建立学生映射（契约 v2 §1.2.1/§1.2.2）。

前置（用例 a）：删除 seed 的 LinkedStudent 两行并 cancel link，之后
preview→confirm 复活空 link——修复前该流程 linked_count=0 且无任何后端
入口可建映射。复活后 valid_from=当天，样本考试（2025-11-06）早于共享
下限，故 a) 同时经 share-scope 端点显式授权历史日期，后续用例才能看到
成绩投影（§1.2.2：显式授权可早于 valid_from）。
"""

API = "/api/v1"
SUBJECT = "物理"
EXAM_E1_DATE = "2025-11-06"


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _linked_count(seed):
    from app.db import workspace_models as wm

    db = _db()
    try:
        return (
            db.query(wm.LinkedStudent).filter_by(link_id=seed.link_id).count()
        )
    finally:
        db.close()


def _homeroom_students(client, seed):
    r = client.get(
        f"{API}/homeroom/students",
        params={"academic_year_id": seed.ay_id, "class_id": seed.h6_id},
    )
    assert r.status_code == 200
    return {s["person_id"]: s for s in r.json()["students"]}


def test_r6_revive_empty_link_and_history_grant(client, v1_seed):
    """删除既有配对并 cancel 后，新 preview→confirm 复活空 link；
    GET students pairs 为空；显式授权历史日期使成绩共享可用。"""
    from app.db import workspace_models as wm

    db = _db()
    try:
        deleted = (
            db.query(wm.LinkedStudent)
            .filter_by(link_id=v1_seed.link_id)
            .delete()
        )
        assert deleted == 2  # seed 的两条显式配对（前置，非被测行为）
        db.commit()
    finally:
        db.close()

    cancel = client.post(f"{API}/shared/links/{v1_seed.link_id}/cancel")
    assert cancel.status_code == 200

    preview = client.post(
        f"{API}/shared/links/preview",
        json={
            "admin_class_id": v1_seed.h6_id,
            "teaching_class_id": v1_seed.t6_id,
            "academic_year_id": v1_seed.ay_id,
            "subject": SUBJECT,
        },
    )
    assert preview.status_code == 200
    confirmed = client.post(
        f"{API}/shared/links/confirm", json={"token": preview.json()["token"]}
    )
    assert confirmed.status_code == 200
    body = confirmed.json()
    assert body["link_id"] == v1_seed.link_id  # 复活原行
    assert body["version"] >= 2
    assert body["linked_count"] == 0  # 空关联起点

    listed = client.get(f"{API}/shared/links/{v1_seed.link_id}/students")
    assert listed.status_code == 200
    assert listed.json() == {"link_id": v1_seed.link_id, "pairs": []}

    # 复活后 valid_from=当天：显式授权历史日期，否则 §1.2.2 历史门阻断
    grant = client.post(
        f"{API}/shared/links/{v1_seed.link_id}/share-scope",
        json={"share_history_from": EXAM_E1_DATE},
    )
    assert grant.status_code == 200
    assert grant.json()["share_history_from"] == EXAM_E1_DATE
    assert grant.json()["link_id"] == v1_seed.link_id

    # link 不存在 → 404
    missing = client.get(f"{API}/shared/links/999999/students")
    assert missing.status_code == 404
    assert missing.json()["error"] == "resource_out_of_scope"


def test_r6_create_pairs_then_conflict_projection(client, v1_seed):
    """显式 POST 配对 甲(h)↔甲(t)、乙(h)↔乙(t)：created=2；GET 返回两条；
    H 名册读到 v2 冲突语义 shared_conflict（绝不静默取 teaching 值）。"""
    r = client.post(
        f"{API}/shared/links/{v1_seed.link_id}/students",
        json={
            "pairs": [
                {
                    "homeroom_person_id": v1_seed.jia_h_id,
                    "teaching_person_id": v1_seed.jia_t_id,
                },
                {
                    "homeroom_person_id": v1_seed.yi_h_id,
                    "teaching_person_id": v1_seed.yi_t_id,
                },
            ]
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 2
    assert body["skipped"] == 0
    assert len(body["pairs"]) == 2
    # confirm_basis 缺省 'manual_confirm:<今天日期>'
    for pair in body["pairs"]:
        assert pair["confirm_basis"].startswith("manual_confirm:")

    listed = client.get(f"{API}/shared/links/{v1_seed.link_id}/students")
    assert listed.status_code == 200
    pairs = listed.json()["pairs"]
    assert len(pairs) == 2
    by_h = {p["homeroom_person_id"]: p for p in pairs}
    jia = by_h[v1_seed.jia_h_id]
    assert jia["teaching_person_id"] == v1_seed.jia_t_id
    assert jia["linked_id"]
    assert jia["homeroom_name"] == "秦甲"
    assert jia["teaching_name"] == "秦甲·T"

    by_pid = _homeroom_students(client, v1_seed)
    assert by_pid[v1_seed.jia_h_id]["shared_conflict"] == {"teaching_score": 91.0}
    assert by_pid[v1_seed.jia_h_id]["shared_subject_score"] is None
    assert by_pid[v1_seed.yi_h_id]["shared_conflict"] == {"teaching_score": 85.0}
    assert by_pid[v1_seed.bing_h_id]["shared_conflict"] is None


def test_r6_repeat_post_is_idempotent_skip(client, v1_seed):
    """重复 POST 完全相同的对 → created=0 skipped=2，不报错。"""
    r = client.post(
        f"{API}/shared/links/{v1_seed.link_id}/students",
        json={
            "pairs": [
                {
                    "homeroom_person_id": v1_seed.jia_h_id,
                    "teaching_person_id": v1_seed.jia_t_id,
                },
                {
                    "homeroom_person_id": v1_seed.yi_h_id,
                    "teaching_person_id": v1_seed.yi_t_id,
                },
            ]
        },
    )
    assert r.status_code == 200
    assert r.json()["created"] == 0
    assert r.json()["skipped"] == 2
    assert len(r.json()["pairs"]) == 2
    assert _linked_count(v1_seed) == 2


def test_r6_pair_validation_rejections(client, v1_seed):
    """逐对校验失败整批零写入：越界成员 404；请求内/库中一一映射冲突 422。"""
    before = _linked_count(v1_seed)

    # 甲(h)↔己(t8 成员)：己不在该 link 教学班 → 404
    r = client.post(
        f"{API}/shared/links/{v1_seed.link_id}/students",
        json={
            "pairs": [
                {
                    "homeroom_person_id": v1_seed.jia_h_id,
                    "teaching_person_id": v1_seed.ji_t_id,
                }
            ]
        },
    )
    assert r.status_code == 404
    assert r.json()["error"] == "resource_out_of_scope"

    # 同请求内 甲(h) 配两个不同 teaching → 422
    r = client.post(
        f"{API}/shared/links/{v1_seed.link_id}/students",
        json={
            "pairs": [
                {
                    "homeroom_person_id": v1_seed.jia_h_id,
                    "teaching_person_id": v1_seed.jia_t_id,
                },
                {
                    "homeroom_person_id": v1_seed.jia_h_id,
                    "teaching_person_id": v1_seed.yi_t_id,
                },
            ]
        },
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_scope_param"

    # 库中已有 甲(h)↔甲(t)：同 h 不同 t（丁）→ 422
    r = client.post(
        f"{API}/shared/links/{v1_seed.link_id}/students",
        json={
            "pairs": [
                {
                    "homeroom_person_id": v1_seed.jia_h_id,
                    "teaching_person_id": v1_seed.ding_t_id,
                }
            ]
        },
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_scope_param"

    # 库中已有 甲(h)↔甲(t)：同 t 不同 h（丙）→ 422
    r = client.post(
        f"{API}/shared/links/{v1_seed.link_id}/students",
        json={
            "pairs": [
                {
                    "homeroom_person_id": v1_seed.bing_h_id,
                    "teaching_person_id": v1_seed.jia_t_id,
                }
            ]
        },
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_scope_param"

    # 整批校验：任一失败零写入（行数与拒绝前一致）
    assert _linked_count(v1_seed) == before

    # 空 pairs → 422（契约：一次 1..N 对）
    r = client.post(
        f"{API}/shared/links/{v1_seed.link_id}/students", json={"pairs": []}
    )
    assert r.status_code == 422


def test_r6_delete_pair_stops_sharing_immediately(client, v1_seed):
    """DELETE 撤销甲的配对：该生共享即时停止（shared_* 全无），乙不受影响。"""
    listed = client.get(f"{API}/shared/links/{v1_seed.link_id}/students")
    pairs = listed.json()["pairs"]
    jia_pair = next(
        p for p in pairs if p["homeroom_person_id"] == v1_seed.jia_h_id
    )

    r = client.delete(
        f"{API}/shared/links/{v1_seed.link_id}/students/{jia_pair['linked_id']}"
    )
    assert r.status_code == 200
    assert r.json() == {"success": True}

    by_pid = _homeroom_students(client, v1_seed)
    jia = by_pid[v1_seed.jia_h_id]
    assert jia.get("shared_conflict") is None
    assert jia.get("shared_subject_score") is None
    assert jia.get("linked_teaching_class_id") is None
    # 乙的配对不受影响
    assert by_pid[v1_seed.yi_h_id]["shared_conflict"] == {"teaching_score": 85.0}

    # 重复 DELETE 同一行 → 404；不存在的 link → 404
    again = client.delete(
        f"{API}/shared/links/{v1_seed.link_id}/students/{jia_pair['linked_id']}"
    )
    assert again.status_code == 404
    bogus = client.delete(f"{API}/shared/links/999999/students/1")
    assert bogus.status_code == 404


def test_r6_share_scope_categories_and_history(client, v1_seed):
    """share-scope：roster-only → 成绩投影字段全无；恢复后 null 历史授权
    阻断、显式日期恢复；非法类别/空列表/非法日期 → 422。"""
    link_id = v1_seed.link_id

    # 收紧为 roster-only：成绩投影（shared_subject_score/shared_conflict）全无
    r = client.post(
        f"{API}/shared/links/{link_id}/share-scope",
        json={"share_categories": ["roster"]},
    )
    assert r.status_code == 200
    assert r.json()["share_categories"] == ["roster"]
    by_pid = _homeroom_students(client, v1_seed)
    for s in by_pid.values():
        assert s.get("shared_subject_score") is None
        assert s.get("shared_conflict") is None

    # 恢复成绩共享；显式 null 撤销历史授权 → 复活后的 valid_from=当天，
    # 样本考试早于下限，再次阻断
    restore = client.post(
        f"{API}/shared/links/{link_id}/share-scope",
        json={"share_categories": ["roster", "current_subject_score"]},
    )
    assert restore.status_code == 200
    clear = client.post(
        f"{API}/shared/links/{link_id}/share-scope",
        json={"share_history_from": None},
    )
    assert clear.status_code == 200
    assert clear.json()["share_history_from"] is None
    by_pid = _homeroom_students(client, v1_seed)
    assert by_pid[v1_seed.yi_h_id].get("shared_conflict") is None

    # 重新显式授权考试当日 → 共享恢复（"正常"）
    grant = client.post(
        f"{API}/shared/links/{link_id}/share-scope",
        json={"share_history_from": EXAM_E1_DATE},
    )
    assert grant.status_code == 200
    assert grant.json()["version"] > restore.json()["version"]  # 每次变更递增
    by_pid = _homeroom_students(client, v1_seed)
    assert by_pid[v1_seed.yi_h_id]["shared_conflict"] == {"teaching_score": 85.0}

    # 非法类别 / 空列表 / 非法日期 → 422 invalid_scope_param
    bad_category = client.post(
        f"{API}/shared/links/{link_id}/share-scope",
        json={"share_categories": ["roster", "grades"]},
    )
    assert bad_category.status_code == 422
    assert bad_category.json()["error"] == "invalid_scope_param"
    empty_category = client.post(
        f"{API}/shared/links/{link_id}/share-scope",
        json={"share_categories": []},
    )
    assert empty_category.status_code == 422
    bad_date = client.post(
        f"{API}/shared/links/{link_id}/share-scope",
        json={"share_history_from": "2025/11/06"},
    )
    assert bad_date.status_code == 422
    assert bad_date.json()["error"] == "invalid_scope_param"
