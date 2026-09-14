"""F02 回归：preview 绑定 link_id/link_version/link_status（契约 v2.1 §1.2）。

缺陷背景：active 关联上 preview → cancel → 用这个【未消费】旧 token
confirm → 200 且复活已取消关联（快照没有关联状态/版本，校验 pending +
成员相同识别不了"取消发生在预览之后"）。

修复语义：
- preview 在目标班对已有 link（任意状态）时把 link_id/link_version/
  link_status 写入快照；confirm 校验绑定与当前库一致——已取消或版本
  变化（含 share-scope 修改递增 version）→ 409 link_version_conflict
  零写入，detail 提示重新预览；快照无绑定（全新配对）走原逻辑。
- 配对候选集合（审核边界裁决）：homeroom_only/teaching_only 扣除
  已确认 LinkedStudent 的成员，不再把已配对的甲乙列进"未配对"候选。

本文件用例按定义顺序共享 v1_seed 的模块级数据（isolated_module_schema
只按模块隔离）；前面的用例保持 link 行存在且走向稳态，最后一个用例
才做删除 link 的破坏性操作。
"""

API = "/api/v1"
SUBJECT = "物理"


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _preview_token(client, seed):
    r = client.post(
        f"{API}/shared/links/preview",
        json={
            "admin_class_id": seed.h6_id,
            "teaching_class_id": seed.t6_id,
            "academic_year_id": seed.ay_id,
            "subject": SUBJECT,
        },
    )
    assert r.status_code == 200
    return r.json()["token"]


def _confirm(client, token):
    return client.post(f"{API}/shared/links/confirm", json={"token": token})


def _link_state(seed):
    from app.db import workspace_models as wm

    db = _db()
    try:
        link = db.get(wm.HomeroomTeachingLink, seed.link_id)
        return link.status, link.version, link.cancelled_at is not None
    finally:
        db.close()


def _batch_status(token):
    from app.db import workspace_models as wm

    db = _db()
    try:
        return db.query(wm.ImportBatch).filter_by(token=token).one().status
    finally:
        db.close()


def _snapshot_of(token):
    from app.db import workspace_models as wm

    db = _db()
    try:
        import json

        raw = db.query(wm.ImportBatch).filter_by(token=token).one().scope_json
        return json.loads(raw or "{}")
    finally:
        db.close()


def test_f02_preview_candidates_exclude_paired(client, v1_seed):
    """候选集合：已配对甲乙在 both（计数=2），不再出现在
    homeroom_only/teaching_only；only 与 both 计数一致覆盖全名册。"""
    r = client.post(
        f"{API}/shared/links/preview",
        json={
            "admin_class_id": v1_seed.h6_id,
            "teaching_class_id": v1_seed.t6_id,
            "academic_year_id": v1_seed.ay_id,
            "subject": SUBJECT,
        },
    )
    assert r.status_code == 200
    diff = r.json()["roster_diff"]
    assert {b["person_id"] for b in diff["both"]} == {v1_seed.jia_h_id, v1_seed.yi_h_id}
    assert {b["person_id"] for b in diff["homeroom_only"]} == {v1_seed.bing_h_id}
    assert {b["person_id"] for b in diff["teaching_only"]} == {v1_seed.ding_t_id}
    # 计数一致：both + only = 当期名册（H/T 各 3 人）
    assert len(diff["both"]) == 2
    assert len(diff["both"]) + len(diff["homeroom_only"]) == 3
    assert len(diff["both"]) + len(diff["teaching_only"]) == 3


def test_f02_unconsumed_old_token_cannot_revive_cancelled(client, v1_seed):
    """preview（active 上）→ cancel → 未消费旧 token confirm → 409 零写入，
    link 保持 cancelled；取消之后的新 preview → confirm 才能复活
    （version+1）。"""
    old_token = _preview_token(client, v1_seed)
    assert _snapshot_of(old_token)["link_status"] == "active"

    cancel = client.post(f"{API}/shared/links/{v1_seed.link_id}/cancel")
    assert cancel.status_code == 200
    assert _link_state(v1_seed) == ("cancelled", 2, True)

    r = _confirm(client, old_token)
    assert r.status_code == 409
    assert r.json()["error"] == "link_version_conflict"
    assert "重新预览" in r.json()["detail"]
    # 零写入：link 状态/版本原地不动，旧 token 也未被消费
    assert _link_state(v1_seed) == ("cancelled", 2, True)
    assert _batch_status(old_token) == "pending"

    new_token = _preview_token(client, v1_seed)
    assert _snapshot_of(new_token)["link_status"] == "cancelled"
    ok = _confirm(client, new_token)
    assert ok.status_code == 200
    body = ok.json()
    assert body["link_id"] == v1_seed.link_id
    assert body["version"] == 3  # 取消后 version 2 的 +1
    assert body["linked_count"] == 2
    assert _link_state(v1_seed) == ("active", 3, False)


def test_f02_share_scope_bump_invalidates_old_preview(client, v1_seed):
    """preview → 修改 share-scope（version+1）→ 旧 token confirm → 409
    零写入（版本绑定同样覆盖共享范围变化）。"""
    token = _preview_token(client, v1_seed)
    assert _link_state(v1_seed) == ("active", 3, False)

    scope = client.post(
        f"{API}/shared/links/{v1_seed.link_id}/share-scope",
        json={"share_categories": ["roster", "current_subject_score"]},
    )
    assert scope.status_code == 200
    assert scope.json()["version"] == 4

    r = _confirm(client, token)
    assert r.status_code == 409
    assert r.json()["error"] == "link_version_conflict"
    assert "重新预览" in r.json()["detail"]
    assert _link_state(v1_seed) == ("active", 4, False)
    assert _batch_status(token) == "pending"


def test_f02_multiple_previews_only_latest_binding_valid(client, v1_seed):
    """多个 preview 并存：preview1 → cancel → preview2 → confirm(preview2)
    成功复活；再 confirm(preview1) → 409（未消费 + 绑定漂移双重拒绝）。"""
    token1 = _preview_token(client, v1_seed)
    cancel = client.post(f"{API}/shared/links/{v1_seed.link_id}/cancel")
    assert cancel.status_code == 200
    token2 = _preview_token(client, v1_seed)

    ok = _confirm(client, token2)
    assert ok.status_code == 200
    assert ok.json()["version"] == 6  # cancel(5) 后复活 +1
    assert _link_state(v1_seed) == ("active", 6, False)

    # token1 从未消费（pending），但其绑定（active/4）与当前（active/6）
    # 不一致 → 同样 409，不得触发任何状态变化
    stale = _confirm(client, token1)
    assert stale.status_code == 409
    assert stale.json()["error"] == "link_version_conflict"
    assert _link_state(v1_seed) == ("active", 6, False)
    assert _batch_status(token1) == "pending"


def test_f02_fresh_pairing_has_no_binding(client, v1_seed):
    """全新配对（先删 link/配对再 preview）：快照无绑定，confirm 正常
    创建新 link（version=1），候选 only 恢复全名册、both 为空。"""
    from app.db import workspace_models as wm

    db = _db()
    try:
        deleted_pairs = (
            db.query(wm.LinkedStudent).filter_by(link_id=v1_seed.link_id).delete()
        )
        assert deleted_pairs == 2  # 前置清理（非被测行为）
        deleted = (
            db.query(wm.HomeroomTeachingLink)
            .filter_by(id=v1_seed.link_id)
            .delete()
        )
        assert deleted == 1
        db.commit()
    finally:
        db.close()

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
    token = preview.json()["token"]
    snapshot = _snapshot_of(token)
    # 无既有 link → 无绑定键（契约 v2.1：全新配对无此绑定）
    assert "link_id" not in snapshot
    assert "link_version" not in snapshot
    assert "link_status" not in snapshot

    diff = preview.json()["roster_diff"]
    assert diff["both"] == []
    assert {b["person_id"] for b in diff["homeroom_only"]} == set(
        v1_seed.h_person_ids
    )
    assert {b["person_id"] for b in diff["teaching_only"]} == set(
        v1_seed.t6_person_ids
    )

    ok = _confirm(client, token)
    assert ok.status_code == 200
    body = ok.json()
    # 旧行已物理删除，这里是全新行（复活路径 version 必 >1；SQLite
    # rowid 复用可能再次发出相同数值 id，故不用 id 判断新旧）
    assert body["version"] == 1
    assert body["linked_count"] == 0

    db = _db()
    try:
        link = db.get(wm.HomeroomTeachingLink, body["link_id"])
        assert link.status == "active"
        assert link.version == 1
    finally:
        db.close()
