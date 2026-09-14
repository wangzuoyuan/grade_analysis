"""Q10 跨域冲突规范值确认（契约 docs/contracts/p3-imports-analysis.md §1.6 v2.3）。

- GET /shared/links/{id}/score-conflicts：与读接口同一门（§1.4.1 考试时点
  成员交集 + 历史授权下限）；被门挡住的事实不构成冲突；
- POST /shared/links/{id}/canonical-scores：按"来源侧"选择（v2.3/V04）——
  选 teaching 91 / 选 homeroom 90 双向确认、非法侧 422、伪造人/科/考试
  422 零写入、缺考 NULL 冲突可选任一侧（选缺考侧 → 两域同置 NULL、
  stats 按缺考口径变化；选实分侧 → 两域同置实分）、已一致的重复提交
  skipped、link cancelled 409。

样本（conftest v1_seed，模块内顺序执行、状态向前演化）：
E1「2025期中」甲 H 物理 90 / T 物理 91；乙 H 物理 84 / T 物理 85。
"""

from datetime import date

API = "/api/v1"
EXAM = "2025期中"
EXAM_DATE = "2025-11-06"


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _phys_fact(db, seed, domain, ident_id):
    from app.db import workspace_models as wm

    return (
        db.query(wm.ScoreFact)
        .filter_by(
            data_domain=domain,
            academic_year_id=seed.ay_id,
            exam_name=EXAM,
            identity_id=ident_id,
            subject="物理",
        )
        .one()
    )


def _set_phys_score(seed, domain, ident_id, score):
    """直接改单域物理分（造 NULL 缺考冲突 / 重建样本），不改 revision。"""
    db = _db()
    try:
        fact = _phys_fact(db, seed, domain, ident_id)
        fact.score = score
        db.commit()
    finally:
        db.close()


def _conflicts(client, seed):
    r = client.get(f"{API}/shared/links/{seed.link_id}/score-conflicts")
    assert r.status_code == 200, r.text
    return r.json()["conflicts"]


def _post(client, seed, person_id, side, subject="物理", exam_name=EXAM):
    return client.post(
        f"{API}/shared/links/{seed.link_id}/canonical-scores",
        json={
            "resolutions": [
                {
                    "person_id": person_id,
                    "subject": subject,
                    "exam_name": exam_name,
                    "canonical_side": side,
                }
            ]
        },
    )


def _scores_rows(client, seed, mode, exam_name=EXAM):
    r = client.get(
        f"{API}/scores",
        params={"mode": mode, "academic_year_id": seed.ay_id, "exam_name": exam_name},
    )
    assert r.status_code == 200, r.text
    return r.json()["rows"]


def _jia_phys_h(rows, seed):
    hit = [
        r
        for r in rows
        if r["person_id"] == seed.jia_h_id and r.get("subject") == "物理"
    ]
    assert len(hit) == 1
    return hit[0]


def _phys_stats(client, seed, mode="homeroom", exam_name=EXAM):
    """物理学科计数（valid_count/missing_count 按缺考口径：NULL 不计有效）。"""
    r = client.get(
        f"{API}/{mode}/analysis/exams/{exam_name}/stats",
        params={"academic_year_id": seed.ay_id},
    )
    assert r.status_code == 200, r.text
    hit = [s for s in r.json()["subjects"] if s["subject"] == "物理"]
    assert len(hit) == 1
    return hit[0]


def test_conflict_list_enumerates_and_respects_history_gate(client, v1_seed):
    """清单条目形状与值（90/91、84/85）；share_history_from 晚于考试日 →
    被门挡住 → 0 条；撤销授权后冲突复现。未关联的丙永不出现。"""
    seed = v1_seed
    by_pid = {c["person_id"]: c for c in _conflicts(client, seed)}
    jia = by_pid[seed.jia_h_id]
    assert jia["homeroom_score"] == 90.0
    assert jia["teaching_score"] == 91.0
    assert jia["subject"] == "物理"
    assert jia["exam_name"] == EXAM
    assert jia["exam_date"] == EXAM_DATE
    assert jia["name"] == "秦甲"
    assert by_pid[seed.yi_h_id]["homeroom_score"] == 84.0
    assert by_pid[seed.yi_h_id]["teaching_score"] == 85.0
    assert seed.bing_h_id not in by_pid
    assert len(by_pid) == 2

    db = _db()
    try:
        from app.db import workspace_models as wm

        link = db.get(wm.HomeroomTeachingLink, seed.link_id)
        link.share_history_from = date(2025, 12, 1)  # 晚于 E1 的 2025-11-06
        db.commit()
        assert _conflicts(client, seed) == []
    finally:
        link = db.get(wm.HomeroomTeachingLink, seed.link_id)
        link.share_history_from = None
        db.commit()
        db.close()
    assert len(_conflicts(client, seed)) == 2


def test_confirm_teaching_value_91(client, v1_seed):
    """选教学域侧（canonical_side=teaching，v2.3）：两域 fact 均 91、
    revision 各 +1、source 追加 canonical:teaching 且保留原来源；清单不再
    含甲；两工作台读值一致且 homeroom 行无 shared_conflict；同条同侧重
    复提交 → skipped。"""
    seed = v1_seed
    db = _db()
    try:
        h_before = _phys_fact(db, seed, "homeroom", seed.jia_h_id)
        t_before = _phys_fact(db, seed, "teaching", seed.jia_t_id)
        h_rev = h_before.data_revision or 1
        t_rev = t_before.data_revision or 1
        h_src, t_src = h_before.source, t_before.source
    finally:
        db.close()

    r = _post(client, seed, seed.jia_h_id, "teaching")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resolved"] == 1
    assert body["skipped"] == 0
    assert body["facts"] == [
        {
            "person_id": seed.jia_h_id,
            "subject": "物理",
            "exam_name": EXAM,
            "score": 91.0,
            "data_revision": h_rev + 1,
        }
    ]

    db = _db()
    try:
        h = _phys_fact(db, seed, "homeroom", seed.jia_h_id)
        t = _phys_fact(db, seed, "teaching", seed.jia_t_id)
        assert h.score == 91.0 and t.score == 91.0
        assert (h.data_revision or 1) == h_rev + 1
        assert (t.data_revision or 1) == t_rev + 1
        assert "canonical:teaching" in h.source
        assert "canonical:teaching" in t.source
        # 追加而非覆盖：原来源保留在 source 前段
        assert h.source.startswith(h_src)
        assert t.source.startswith(t_src)
    finally:
        db.close()

    by_pid = {c["person_id"]: c for c in _conflicts(client, seed)}
    assert seed.jia_h_id not in by_pid
    assert seed.yi_h_id in by_pid

    jia_h = _jia_phys_h(_scores_rows(client, seed, "homeroom"), seed)
    assert jia_h["score"] == 91.0
    assert jia_h.get("shared_conflict") is None
    t_rows = _scores_rows(client, seed, "teaching")
    jia_t = [r for r in t_rows if r["person_id"] == seed.jia_t_id]
    assert len(jia_t) == 1
    assert jia_t[0]["score"] == 91.0
    assert jia_t[0]["source_domain"] == "teaching"

    # 幂等：已不构成冲突的重复提交（同侧）→ skipped，不再写 revision
    r2 = _post(client, seed, seed.jia_h_id, "teaching")
    assert r2.status_code == 200, r2.text
    assert r2.json()["resolved"] == 0
    assert r2.json()["skipped"] == 1

    # 还原甲样本 90/91（保留确认痕迹列），反向用例从干净冲突出发
    _set_phys_score(seed, "homeroom", seed.jia_h_id, 90.0)
    _set_phys_score(seed, "teaching", seed.jia_t_id, 91.0)


def test_confirm_homeroom_value_90_reverse(client, v1_seed):
    """选班主任域侧 90（反向，canonical_side=homeroom）：两域均 90、
    source 标 canonical:homeroom；teaching 侧读值同步为 90，homeroom 行
    无冲突标记。"""
    seed = v1_seed
    r = _post(client, seed, seed.jia_h_id, "homeroom")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resolved"] == 1
    assert body["facts"][0]["score"] == 90.0

    db = _db()
    try:
        h = _phys_fact(db, seed, "homeroom", seed.jia_h_id)
        t = _phys_fact(db, seed, "teaching", seed.jia_t_id)
        assert h.score == 90.0 and t.score == 90.0
        assert "canonical:homeroom" in h.source
        assert "canonical:homeroom" in t.source
    finally:
        db.close()

    by_pid = {c["person_id"]: c for c in _conflicts(client, seed)}
    assert seed.jia_h_id not in by_pid

    jia_h = _jia_phys_h(_scores_rows(client, seed, "homeroom"), seed)
    assert jia_h["score"] == 90.0
    assert jia_h.get("shared_conflict") is None
    jia_t = [
        r for r in _scores_rows(client, seed, "teaching") if r["person_id"] == seed.jia_t_id
    ]
    assert len(jia_t) == 1
    assert jia_t[0]["score"] == 90.0


def test_invalid_side_and_forged_keys_rejected_zero_write(client, v1_seed):
    """非法侧（契约 v2.3 只允许 homeroom/teaching，无"第三值"手工分）/
    伪造人·科·考试 → 422 且零写入；真实冲突保持原样。"""
    seed = v1_seed
    # 重建基准样本 90/91（模块内前面用例已把两域确认成 90），再造成
    # 新冲突 89 vs 91，使本用例断言不依赖执行历史
    _set_phys_score(seed, "homeroom", seed.jia_h_id, 90.0)
    _set_phys_score(seed, "teaching", seed.jia_t_id, 91.0)
    _set_phys_score(seed, "homeroom", seed.jia_h_id, 89.0)

    r = _post(client, seed, seed.jia_h_id, "both")  # 非两域之一 → 422
    assert r.status_code == 422, r.text
    r = _post(client, seed, seed.jia_h_id, 95.0)  # 数值不是合法侧 → 422
    assert r.status_code == 422, r.text

    r = _post(client, seed, 999999, "teaching")  # 伪造人
    assert r.status_code == 422
    r = _post(client, seed, seed.jia_h_id, "teaching", subject="数学")  # 伪造科
    assert r.status_code == 422
    r = _post(
        client, seed, seed.jia_h_id, "teaching", exam_name="不存在的考试"
    )  # 伪造考试
    assert r.status_code == 422

    db = _db()
    try:
        h = _phys_fact(db, seed, "homeroom", seed.jia_h_id)
        t = _phys_fact(db, seed, "teaching", seed.jia_t_id)
        assert h.score == 89.0 and t.score == 91.0
        assert (h.data_revision or 1) >= 1  # 未被本次失败请求推进
    finally:
        db.close()
    by_pid = {c["person_id"]: c for c in _conflicts(client, seed)}
    assert by_pid[seed.jia_h_id]["homeroom_score"] == 89.0
    assert by_pid[seed.jia_h_id]["teaching_score"] == 91.0


def test_null_missing_conflict_both_sides_and_empty_list(client, v1_seed):
    """V04 契约 v2.3：缺考 NULL 冲突（H NULL / T 91）任一侧均可确认——
    选缺考侧（homeroom）→ 两域同置 NULL、清单不再含甲、/scores 该行 score
    为 null（缺考「—」）、stats 的 valid_count-1/missing_count+1（缺考口径，
    绝不转 0）；再回填教学侧造新冲突后选 teaching → 两域 91、计数复原；
    乙（84/85）确认 homeroom 侧 → 全部冲突清零、读值无标记。"""
    seed = v1_seed
    _set_phys_score(seed, "homeroom", seed.jia_h_id, None)

    by_pid = {c["person_id"]: c for c in _conflicts(client, seed)}
    assert by_pid[seed.jia_h_id]["homeroom_score"] is None
    assert by_pid[seed.jia_h_id]["teaching_score"] == 91.0

    # 选缺考侧：NULL 是现行业务状态，不是手工改分，必须可确认为规范值
    r = _post(client, seed, seed.jia_h_id, "homeroom")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resolved"] == 1
    assert body["facts"][0]["score"] is None  # 响应回执承载 NULL 规范值

    db = _db()
    try:
        h = _phys_fact(db, seed, "homeroom", seed.jia_h_id)
        t = _phys_fact(db, seed, "teaching", seed.jia_t_id)
        assert h.score is None and t.score is None  # 两域同置缺考
        assert "canonical:homeroom" in h.source
        assert "canonical:homeroom" in t.source
    finally:
        db.close()

    by_pid = {c["person_id"]: c for c in _conflicts(client, seed)}
    assert seed.jia_h_id not in by_pid  # 两域一致（同 NULL）→ 冲突消失

    # 读接口：缺考红线——score null（前端显示「—」），不转 0
    jia_h = _jia_phys_h(_scores_rows(client, seed, "homeroom"), seed)
    assert jia_h["score"] is None
    assert jia_h.get("shared_conflict") is None

    # 缺考口径统计：甲退出有效样本（90→NULL）
    stats = _phys_stats(client, seed, "homeroom")
    assert stats["valid_count"] == 2  # 乙 84 + 丙 68
    assert stats["missing_count"] == 1  # 甲缺考

    # 反向：教学侧回填 91 造新冲突（H NULL / T 91），选实分侧 → 两域 91
    _set_phys_score(seed, "teaching", seed.jia_t_id, 91.0)
    r = _post(client, seed, seed.jia_h_id, "teaching")
    assert r.status_code == 200, r.text
    assert r.json()["facts"][0]["score"] == 91.0
    db = _db()
    try:
        h = _phys_fact(db, seed, "homeroom", seed.jia_h_id)
        t = _phys_fact(db, seed, "teaching", seed.jia_t_id)
        assert h.score == 91.0 and t.score == 91.0
    finally:
        db.close()
    stats = _phys_stats(client, seed, "homeroom")
    assert stats["valid_count"] == 3  # 甲回到有效样本
    assert stats["missing_count"] == 0

    # 乙（84/85）确认班主任域侧 → 全部冲突清零
    r2 = _post(client, seed, seed.yi_h_id, "homeroom")
    assert r2.status_code == 200, r2.text
    assert _conflicts(client, seed) == []

    jia_h = _jia_phys_h(_scores_rows(client, seed, "homeroom"), seed)
    assert jia_h["score"] == 91.0
    assert jia_h.get("shared_conflict") is None


def test_cancelled_link_rejects_conflict_endpoints(client, v1_seed):
    """link cancelled：GET/POST canonical 端点一律 409 link_version_conflict。
    （模块内顺序执行的收尾用例：取消后不再恢复。）"""
    seed = v1_seed
    r = client.post(f"{API}/shared/links/{seed.link_id}/cancel")
    assert r.status_code == 200, r.text

    r = client.get(f"{API}/shared/links/{seed.link_id}/score-conflicts")
    assert r.status_code == 409, r.text
    assert r.json()["error"] == "link_version_conflict"

    r = _post(client, seed, seed.jia_h_id, "teaching")
    assert r.status_code == 409, r.text
    assert r.json()["error"] == "link_version_conflict"
