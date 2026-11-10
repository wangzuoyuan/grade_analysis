"""作业列表端点 N+1 批量预取：等价性 + 查询数守卫（2026-11-09 审查 P2-3）。

优化内容（app/api/homework.py）：
- homework_assignments_list 对分页后的 assignment_id 集合一次 IN 预取
  逐人行（_submissions_by_assignment），migration 空快照批次按域一次
  预取 legacy 名单（_legacy_expected_ids_batch），行装配路径
  （_list_item_of → _projected_submissions/_projected_expected）不再
  每批一查；详情/看板/预警/诊断等既有调用方保持原逐批行为。

本文件验证两件事：
1. 等价性：把两个批量函数 monkeypatch 成空映射即可完整回退旧的逐批
   查询路径（.get(a.id) → None → 函数内部走原查询）。同一数据集下，
   批量路径与回退路径对多种查询参数（全量/分页/学科过滤/双域/跨域
   投影）产出**逐字节相同**的响应体。
2. 查询数守卫：列表请求内 homework_submission 的 IN 预取恰好 1 次、
   enrollment / teaching_class_member 的 legacy 名单恢复各域至多 1 次
   ——回退路径下这些断言会失败，防止 N+1 复发。
"""

import json
from datetime import date

import pytest

from app.db import workspace_models as wm


API = "/api/v1/homework"


@pytest.fixture(scope="module")
def dataset(v1_seed):
    """混合批次数据集（普通快照批 / legacy migration 批 / 跨域投影批）。

    视角覆盖：
    - H6 班主任视图可见：A/B/C/D（直读）+ E/F（T6 跨域物理投影）；
    - T6 教学视图可见：E/F（直读）+ A/B（H6 跨域物理投影）；
    - t_empty 教学视图可见：G（直读空名册 legacy）+ A/B（跨域投影）。
    同日跨域请假映射（A 的 excused → E 的读域计数）覆盖 _day_exception_flags
    与装配路径的联动。
    """
    from app.db.models import SessionLocal

    db = SessionLocal()
    try:
        # seed link 默认 share_categories 不含作业类共享——打开它，
        # E/F（teaching 批）才对 H6 班主任视图可见（跨域投影路径）。
        link = db.get(wm.HomeroomTeachingLink, v1_seed.link_id)
        link.share_categories = (
            "roster,current_subject_score,current_subject_homework"
        )
        created = {}

        def batch(token, domain, class_id, subject, hw_type, day, expected,
                  rows=()):
            a = wm.HomeworkAssignment(
                data_domain=domain, class_ref_id=class_id,
                academic_year_id=v1_seed.ay_id, subject=subject,
                homework_type=hw_type, assigned_date=date.fromisoformat(day),
                batch_token=token,
                expected_members_json=(
                    expected if isinstance(expected, str)
                    else json.dumps(expected)
                ),
                status="active",
            )
            db.add(a)
            db.flush()
            for person_id, status, evaluation in rows:
                db.add(wm.HomeworkSubmission(
                    assignment_id=a.id, person_id=person_id,
                    submission_status=status, evaluation=evaluation,
                ))
            created[token] = a.id
            return a

        # A 普通 homeroom 物理：甲默认交、乙请假、丙缺交
        batch("p2-3:h:normal-1", "homeroom", v1_seed.h6_id, "物理", "练习册",
              "2025-12-01", v1_seed.h_person_ids,
              [(v1_seed.bing_h_id, "missing", None),
               (v1_seed.yi_h_id, "excused", None)])
        # B 普通 homeroom 物理：甲缺交+负面评价、丙已交+出勤异常（已交行注记）
        batch("p2-3:h:normal-2", "homeroom", v1_seed.h6_id, "物理", "练习册",
              "2025-12-02",
              [v1_seed.jia_h_id, v1_seed.bing_h_id],
              [(v1_seed.jia_h_id, "missing", "作业乱"),
               (v1_seed.bing_h_id, "submitted", "迟到")])
        # C legacy homeroom 化学：空快照 → Enrollment 名单恢复（全默认已交）
        batch("migration:h:p2-3-legacy-1", "homeroom", v1_seed.h6_id, "化学",
              "旧系统", "2025-12-03", "[]")
        # D legacy homeroom 数学：空快照 → 名单恢复 + 甲缺交例外行
        batch("migration:h:p2-3-legacy-2", "homeroom", v1_seed.h6_id, "数学",
              "旧系统", "2025-12-04", "[]",
              [(v1_seed.jia_h_id, "missing", None)])
        # E 普通 teaching 物理（T6，经 link 对 H6 可见）：丁请假（不在
        #   LinkedStudent 交集，跨域读被投影剔除）；与 A 同日 → A 的乙
        #   请假经按天映射修正 E 的读域计数。
        batch("p2-3:t:normal-1", "teaching", v1_seed.t6_id, "物理", "分层",
              "2025-12-01", v1_seed.t6_person_ids,
              [(v1_seed.ding_t_id, "excused", None)])
        # F legacy teaching 物理（T6，经 link 对 H6 可见）：TeachingClassMember
        #   名单恢复 + 乙(T)缺交（跨域读映射成乙(H)缺交）。
        batch("migration:t:p2-3-legacy-1", "teaching", v1_seed.t6_id, "物理",
              "旧系统", "2025-12-05", "[]",
              [(v1_seed.yi_t_id, "missing", None)])
        # G legacy teaching 物理（t_empty，无成员）：空名册恢复 → 空快照
        #   rate null + rate_unavailable 口径。
        batch("migration:t:p2-3-empty", "teaching", v1_seed.t_empty_id, "物理",
              "旧系统", "2025-12-06", "[]")

        db.commit()
        yield created
        db.rollback()
    finally:
        db.close()


VIEWS = [
    # （说明， params）
    ("班主任 H6 全量（直读 + 跨域投影 + 同日请假映射）",
     {"mode": "homeroom", "class_id": None}),
    ("班主任 H6 学科过滤 + 分页",
     {"mode": "homeroom", "class_id": None, "subject": "物理",
      "limit": 3, "offset": 1}),
    ("教学 T6 全量（直读 + 跨域投影）",
     {"mode": "teaching", "teaching_class_id": None}),
    ("教学 t_empty（空名册 legacy + 跨域投影）",
     {"mode": "teaching", "teaching_class_id": "empty"}),
]


def _params(v1_seed, view):
    mode = view["mode"]
    if mode == "homeroom":
        return {"mode": "homeroom", "class_id": v1_seed.h6_id, **{
            k: v for k, v in view.items() if k in ("subject", "limit", "offset")
        }}
    class_key = "teaching_class_id"
    return {"mode": "teaching",
            class_key: v1_seed.t_empty_id if view[class_key] == "empty"
            else v1_seed.t6_id,
            **{k: v for k, v in view.items()
               if k in ("subject", "limit", "offset")}}


def _disable_batch_prefetch(monkeypatch):
    """把两个批量预取函数替换成空映射：端点 .get(a.id) 恒为 None，
    装配路径完整回退到旧的逐批查询。"""
    from app.api import homework as hw

    monkeypatch.setattr(hw, "_submissions_by_assignment", lambda db, ids: {})
    monkeypatch.setattr(hw, "_legacy_expected_ids_batch", lambda db, asg: {})


def test_batch_and_peritem_paths_byte_identical(
    client, v1_seed, dataset, monkeypatch
):
    for note, view in VIEWS:
        params = _params(v1_seed, view)
        batched = client.get(f"{API}/assignments", params=params)
        assert batched.status_code == 200, (note, batched.text)

        with monkeypatch.context() as m:
            _disable_batch_prefetch(m)
            per_item = client.get(f"{API}/assignments", params=params)
        assert per_item.status_code == 200, (note, per_item.text)

        assert batched.content == per_item.content, (
            f"批量路径与逐批路径响应不一致：{note}"
        )

    # 数据集确实覆盖关键口径（保证上面的等价比较不是空转）：
    full = client.get(
        f"{API}/assignments",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id},
    ).json()
    assert full["total"] == 6
    items = {i["assignment_id"]: i for i in full["items"]}
    a = items[dataset["p2-3:h:normal-1"]]
    assert (a["expected_count"], a["submitted"], a["missing"], a["excused"]) == (
        3, 1, 1, 1)
    assert a["excused_ids"] == [v1_seed.yi_h_id]
    assert a["missing_ids"] == [v1_seed.bing_h_id]
    b = items[dataset["p2-3:h:normal-2"]]
    assert b["negative_count"] == 1 and b["attendance_count"] == 1
    # E（teaching 批次，H6 视角投影读）：丁的请假行不在交集被剔除，
    # 乙(H) 的请假来自 A 的按天映射（跨域同日事实）。
    e = items[dataset["p2-3:t:normal-1"]]
    assert (e["expected_count"], e["submitted"], e["excused"]) == (2, 1, 1)
    assert e["excused_ids"] == [v1_seed.yi_h_id]
    # C legacy：Enrollment 名单恢复后全默认已交。
    c = items[dataset["migration:h:p2-3-legacy-1"]]
    assert (c["expected_count"], c["submitted"], c["rate_unavailable"]) == (
        3, 3, False)
    # D legacy：恢复名单 3 人 + 甲缺交。
    d = items[dataset["migration:h:p2-3-legacy-2"]]
    assert (d["expected_count"], d["missing"]) == (3, 1)
    # F legacy teaching：名单恢复经 mapping 投影成 H 域 2 人，乙缺交。
    f = items[dataset["migration:t:p2-3-legacy-1"]]
    assert (f["expected_count"], f["missing"]) == (2, 1)

    empty_view = client.get(
        f"{API}/assignments",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t_empty_id},
    ).json()
    g = next(
        i for i in empty_view["items"]
        if i["assignment_id"] == dataset["migration:t:p2-3-empty"]
    )
    assert g["expected_count"] == 0 and g["rate_unavailable"] is True


def test_list_batch_prefetch_query_count(client, v1_seed, dataset):
    """页内 6 批次（含 2 个 homeroom legacy + 1 个 teaching legacy）：
    逐人行 IN 预取恰好 1 次；两个域的 legacy 名单恢复各恰好 1 次。"""
    from sqlalchemy import event

    from app.db.models import engine

    statements = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        resp = client.get(
            f"{API}/assignments",
            params={"mode": "homeroom", "class_id": v1_seed.h6_id},
        )
        assert resp.status_code == 200, resp.text
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]

    def count_of(needle):
        return sum(1 for s in selects if needle in s)

    # 页内全部批次的逐人行装配只允许一次 IN 预取；legacy 名单恢复按域
    # 收敛到一次。判别式取批量恢复特有的列 IN 形状：eligible_linked_pairs
    # 等事件时点门也会等值查 enrollment / teaching_class_member，属
    # 可见性解析的既有固定成本，不在本次优化范围。
    # 回退路径（逐批查询）下这些形状分别是 0 次（等值）与 0 次（等值），
    # 断言会失败——同时防住 N+1 复发与预取被绕过两种回归。
    assert count_of("homework_submission.assignment_id IN (") == 1
    assert count_of("enrollment.admin_class_id IN (") == 1
    assert count_of("teaching_class_member.teaching_class_id IN (") == 1
    # 逐批形状的 legacy 恢复不应再出现（等值 + valid_from 日期过滤、无 IN、
    # 无 status 过滤——作用域解析的一次性班名单查询带 status =，事件时点
    # 门 eligible_linked_pairs 带 identity_id IN，均已按形状排除）。
    legacy_equal = [
        s for s in selects
        if ("FROM enrollment" in s or "FROM teaching_class_member" in s)
        and "valid_from <=" in s
        and " IN (" not in s
        and "status =" not in s
    ]
    assert legacy_equal == []
