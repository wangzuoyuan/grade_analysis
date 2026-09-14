"""S06 取消关联：共享立即消失，两个域的原生数据不受影响。

cancel H6↔T6 link 后：
- H 侧 students 不再有 shared_subject_score / linked_teaching_class_id；
- H 侧 scores 不再返回任何 source_domain=teaching 行；
- teaching/students 的 T6 名册仍全量（甲乙丁），域内数据不动；
- link 状态落库为 cancelled，links 列表同步。
"""

API = "/api/v1"
EXAM_E1 = "2025期中"


def _links(client, seed):
    r = client.get(f"{API}/shared/links", params={"academic_year_id": seed.ay_id})
    assert r.status_code == 200
    return r.json()["links"]


def test_cancel_link_stops_sharing_and_keeps_native_data(client, v1_seed):
    links = _links(client, v1_seed)
    ours = [l for l in links if l["id"] == v1_seed.link_id]
    assert ours, "links 列表应包含 H6↔T6 active link"
    assert ours[0]["status"] == "active"

    r = client.post(f"{API}/shared/links/{v1_seed.link_id}/cancel")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["status"] == "cancelled"

    # 状态落库
    from app.db import workspace_models as wm
    from app.db.models import SessionLocal

    db = SessionLocal()
    try:
        link = db.get(wm.HomeroomTeachingLink, v1_seed.link_id)
        assert link is not None and link.status == "cancelled"
    finally:
        db.close()

    # links 列表反映 cancelled
    links_after = _links(client, v1_seed)
    ours_after = [l for l in links_after if l["id"] == v1_seed.link_id]
    assert ours_after and ours_after[0]["status"] == "cancelled"

    # H 侧共享投影立即消失
    hr = client.get(
        f"{API}/homeroom/students",
        params={"academic_year_id": v1_seed.ay_id, "class_id": v1_seed.h6_id},
    )
    assert hr.status_code == 200
    for s in hr.json()["students"]:
        assert s.get("shared_subject_score") is None
        assert s.get("linked_teaching_class_id") is None

    # H 侧 scores 不再有 teaching 来源行，但本域全科/总分原样保留
    sr = client.get(
        f"{API}/scores",
        params={
            "mode": "homeroom",
            "academic_year_id": v1_seed.ay_id,
            "exam_name": EXAM_E1,
        },
    )
    assert sr.status_code == 200
    rows = sr.json()["rows"]
    assert all(r.get("source_domain") != "teaching" for r in rows)
    assert sum(1 for r in rows if r.get("subject") == "语文") == 3
    assert sum(1 for r in rows if r.get("total_type") == "主三门") == 3
    assert {r["person_id"] for r in rows} == set(v1_seed.h_person_ids)

    # 教学域原生数据不受 cancel 影响：T6 名册仍全量
    tr = client.get(
        f"{API}/teaching/students",
        params={
            "academic_year_id": v1_seed.ay_id,
            "teaching_class_id": v1_seed.t6_id,
        },
    )
    assert tr.status_code == 200
    tbody = tr.json()
    assert tbody["metadata"]["mode"] == "teaching"
    assert {s["person_id"] for s in tbody["students"]} == set(v1_seed.t6_person_ids)
