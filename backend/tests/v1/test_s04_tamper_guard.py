"""S04 篡改与越界守卫：非绑定班拒绝、mode 校验、preview 后篡改 confirm 409。

零写入断言：被拒绝的请求不得在任何契约表留下行数变化（快照对比）。
preview 自身允许签发 token（不计入零写入对比基线），但被篡改后的
confirm 必须 409 且零写入。
"""

API = "/api/v1"

CONTRACT_TABLES = (
    "AcademicYear",
    "Term",
    "Cohort",
    "StudentIdentity",
    "StudentAlias",
    "AdministrativeClass",
    "Enrollment",
    "TeachingClass",
    "TeachingClassMember",
    "HomeroomTeachingLink",
    "LinkedStudent",
    "SourceMap",
    "MigrationRun",
    "ScoreFact",
    "ImportBatch",
)


def _table_counts():
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    db = SessionLocal()
    try:
        return {name: db.query(getattr(wm, name)).count() for name in CONTRACT_TABLES}
    finally:
        db.close()


def test_homeroom_students_rejects_foreign_class(client, v1_seed):
    """存在但非绑定班的 class_id（H9）：404/422 拒绝，且零写入。"""
    before = _table_counts()
    r = client.get(
        f"{API}/homeroom/students",
        params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h9_id},
    )
    assert r.status_code in (404, 422)
    if r.status_code == 404:
        assert r.json().get("error") == "resource_out_of_scope"
    assert _table_counts() == before


def test_scope_invalid_mode_rejected_without_write(client, v1_seed):
    """伪造 mode（权限凭证不得来自客户端）：422 且零写入。"""
    before = _table_counts()
    r = client.get(
        f"{API}/shared/scope",
        params={
            "mode": "everything",
            "academic_year_id": v1_seed.ay_id,
            "class_id": v1_seed.h6_id,
        },
    )
    assert r.status_code == 422
    assert r.json().get("error") == "invalid_scope_param"
    assert _table_counts() == before


def test_import_confirm_rejects_tampered_link_version(client, v1_seed):
    """imports/preview 后直接改库中 link version，confirm 必须 409 且零写入。"""
    prev = client.post(
        f"{API}/imports/preview",
        json={
            "mode": "homeroom",
            "files": [{"filename": "e1-scores.xlsx", "content_digest": "deadbeef01"}],
        },
    )
    assert prev.status_code == 200
    body = prev.json()
    assert body.get("token")
    assert body.get("expires_at")
    assert body.get("items") == []
    after_preview = _table_counts()

    # 篡改：绕过 API 直接把 link version +1，模拟 preview 签发后范围变化
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    db = SessionLocal()
    try:
        link = db.get(wm.HomeroomTeachingLink, v1_seed.link_id)
        assert link is not None
        link.version = (link.version or 1) + 1
        db.commit()
    finally:
        db.close()

    conf = client.post(f"{API}/imports/confirm", json={"token": body["token"]})
    assert conf.status_code == 409
    assert conf.json().get("error") == "link_version_conflict"
    # 被拒的 confirm 零写入：所有契约表行数与 preview 后一致
    assert _table_counts() == after_preview
