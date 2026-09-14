"""R4 回归：确认 token 生命周期（契约 v2 §1.2 confirm 校验 1/3 + §1.5）。

- 已消费（confirmed）token 再次提交 → 409，不得再次触发任何状态变化，
  含「preview→confirm→cancel 后用旧 token 复活」场景；
- preview 快照成员集合与 confirm 时点双侧名册不一致 → 409 零写入；
- 被取消的 link 只能经【新 preview token】复活（version 递增）；
- imports/confirm 同一规则（§1.5：已消费 / 成员漂移 → 409）。

本文件用例按定义顺序共享 v1_seed 的模块级数据（isolated_module_schema
只按模块隔离）；改动成员/状态的用例在收尾恢复或经 API 走到稳态。
"""

from datetime import date, timedelta

API = "/api/v1"
SUBJECT = "物理"


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _preview_link_token(client, seed):
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


def _link(db, seed):
    from app.db import workspace_models as wm

    link = db.get(wm.HomeroomTeachingLink, seed.link_id)
    assert link is not None
    return link


def _yi_enrollment(db, seed):
    from app.db import workspace_models as wm

    return (
        db.query(wm.Enrollment)
        .filter_by(admin_class_id=seed.h6_id, identity_id=seed.yi_h_id)
        .one()
    )


def test_r4_consumed_token_cannot_revive_cancelled_link(client, v1_seed):
    """preview→confirm→cancel→同 token 再 confirm → 409 且 link 保持
    cancelled（旧 token 不得复活已取消关联）。"""
    token = _preview_link_token(client, v1_seed)
    ok = client.post(f"{API}/shared/links/confirm", json={"token": token})
    assert ok.status_code == 200

    cancel = client.post(f"{API}/shared/links/{v1_seed.link_id}/cancel")
    assert cancel.status_code == 200

    db = _db()
    try:
        link = _link(db, v1_seed)
        assert link.status == "cancelled"
        version_cancelled = link.version
    finally:
        db.close()

    again = client.post(f"{API}/shared/links/confirm", json={"token": token})
    assert again.status_code == 409
    assert again.json()["error"] == "link_version_conflict"

    db = _db()
    try:
        link = _link(db, v1_seed)
        assert link.status == "cancelled"  # 不复活
        assert link.version == version_cancelled  # 不再触发任何状态变化
        assert link.cancelled_at is not None
    finally:
        db.close()


def test_r4_consumed_token_rejected_even_for_active_link(client, v1_seed):
    """preview→confirm（复活上一用例取消的 link）→ 同 token 再 confirm →
    409（已消费）；link 保持 active 且 version 不变。"""
    token = _preview_link_token(client, v1_seed)
    ok = client.post(f"{API}/shared/links/confirm", json={"token": token})
    assert ok.status_code == 200
    confirmed_version = ok.json()["version"]

    again = client.post(f"{API}/shared/links/confirm", json={"token": token})
    assert again.status_code == 409
    assert again.json()["error"] == "link_version_conflict"

    db = _db()
    try:
        link = _link(db, v1_seed)
        assert link.status == "active"
        assert link.version == confirmed_version  # 已消费 token 零副作用
    finally:
        db.close()


def test_r4_member_drift_rejected_with_zero_write(client, v1_seed):
    """preview 后乙退出行政班（valid_to=昨天）→ confirm 409
    link_version_conflict，detail 注明重新预览，零写入。"""
    token = _preview_link_token(client, v1_seed)

    db = _db()
    try:
        _yi_enrollment(db, v1_seed).valid_to = date.today() - timedelta(days=1)
        db.commit()
        link = _link(db, v1_seed)
        status_before = link.status
        version_before = link.version
    finally:
        db.close()

    r = client.post(f"{API}/shared/links/confirm", json={"token": token})
    assert r.status_code == 409
    assert r.json()["error"] == "link_version_conflict"
    assert "重新预览" in r.json().get("detail", "")

    db = _db()
    try:
        from app.db import workspace_models as wm

        link = _link(db, v1_seed)
        assert link.status == status_before
        assert link.version == version_before  # 拒绝路径零写入
        # token 未被消费，台账保持 pending
        batch = db.query(wm.ImportBatch).filter_by(token=token).one()
        assert batch.status == "pending"
    finally:
        # 恢复成员，避免污染同模块后续用例
        _yi_enrollment(db, v1_seed).valid_to = None
        db.commit()
        db.close()


def test_r4_imports_confirm_consumed_and_member_drift(client, v1_seed):
    """imports/confirm（§1.5）：已消费 token → 409；preview 后成员漂移 →
    409 且台账保持 pending。"""
    prev = client.post(
        f"{API}/imports/preview",
        json={
            "mode": "homeroom",
            "files": [{"filename": "r4-e1.xlsx", "content_digest": "r4d01"}],
        },
    )
    assert prev.status_code == 200
    token = prev.json()["token"]
    ok = client.post(f"{API}/imports/confirm", json={"token": token})
    assert ok.status_code == 200
    again = client.post(f"{API}/imports/confirm", json={"token": token})
    assert again.status_code == 409
    assert again.json()["error"] == "link_version_conflict"

    prev2 = client.post(
        f"{API}/imports/preview",
        json={
            "mode": "homeroom",
            "files": [{"filename": "r4-e1.xlsx", "content_digest": "r4d02"}],
        },
    )
    assert prev2.status_code == 200
    token2 = prev2.json()["token"]

    db = _db()
    try:
        _yi_enrollment(db, v1_seed).valid_to = date.today() - timedelta(days=1)
        db.commit()
    finally:
        db.close()

    r = client.post(f"{API}/imports/confirm", json={"token": token2})
    assert r.status_code == 409
    assert r.json()["error"] == "link_version_conflict"

    db = _db()
    try:
        from app.db import workspace_models as wm

        batch = db.query(wm.ImportBatch).filter_by(token=token2).one()
        assert batch.status == "pending"  # 拒绝路径零写入
    finally:
        _yi_enrollment(db, v1_seed).valid_to = None
        db.commit()
        db.close()


def test_r4_cancelled_link_revives_via_new_preview_token(client, v1_seed):
    """cancel 后经【新 preview token】confirm 可复活：version 递增、
    status 回到 active（复活路径只认新 token）。"""
    cancel = client.post(f"{API}/shared/links/{v1_seed.link_id}/cancel")
    assert cancel.status_code == 200

    db = _db()
    try:
        version_cancelled = _link(db, v1_seed).version
    finally:
        db.close()

    token = _preview_link_token(client, v1_seed)
    r = client.post(f"{API}/shared/links/confirm", json={"token": token})
    assert r.status_code == 200
    body = r.json()
    assert body["link_id"] == v1_seed.link_id  # 复活原行（唯一键占位）
    assert body["version"] == version_cancelled + 1
    assert body["linked_count"] == 2  # 既确认配对保留

    db = _db()
    try:
        link = _link(db, v1_seed)
        assert link.status == "active"
        assert link.cancelled_at is None
    finally:
        db.close()
