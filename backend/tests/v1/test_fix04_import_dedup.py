"""审查问题 4 回归：均分重导/合并重导/同批双学号写入异常 + 成功反例守护。

背景（独立代码审查报告 §4 / 核实说明 §4 / probe2.py 4A_average_date、
4C_double_alias，probe3.py 4B_merged_averages、4B_merged_scores_counterexample，
修复范围以核实说明为准）：

- 4A 已导入班级均分，不同日期 + exam_choices 归入同一事件重导 → 500。
  根因：均分查重条件（exam_name==、exam_date== 等）与库唯一约束
  uq_workspace_class_average_natural_key(data_domain, academic_year_id,
  event_or_name, grade, class_num) 的自然键不一致，改日期后查重查不到
  既有行，INSERT 撞唯一约束。
- 4B 两个同名同日均分事件归并后再导 → 500（归并后两行都命中过滤条件，
  .one_or_none() 变 MultipleResultsFound）。
- 4C 同一学生新旧两个学号（同一 identity 两个 alias）同批出现 → 500
  （批内按原始 alias 去重而不是按解析后的 identity_id 去重，两行都
  INSERT 撞 uq_score_fact_natural_key）。
- 成功反例（必须保住）：普通成绩两事件归并后 revise 重导 → 200
  revised=1（probe3 实测成功路径，修复不得破坏）。
"""

from datetime import date
from pathlib import Path

from tests.v1.conftest import ALIAS_JIA_H
from tests.v1.test_f05_f06_f07 import _student_row, _upload, _write_grade23_xlsx
from tests.v1.test_p3_imports_e01_e02_e05 import _write_grade23_class_averages

API = "/api/v1"

EXAM_AV = "FIX04均分考"
EXAM_AV_MERGED = "FIX04归并均分考"
EXAM_DOUBLE = "FIX04双号考"
EXAM_SCORES = "FIX04双成绩考"


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _upload_homeroom(client, path, v1_seed, exam_name, exam_date="2025-11-10"):
    prev = _upload(
        client, path, "homeroom",
        {"class_id": v1_seed.h6_id, "exam_name": exam_name,
         "exam_date": exam_date},
    )
    assert prev.status_code == 200, prev.text
    return prev


def _confirm(client, prev, status=200, **extra):
    r = client.post(
        f"{API}/imports/confirm",
        json={"token": prev.json()["token"], **extra},
    )
    assert r.status_code == status, r.text
    return r.json()


def _event_ids(db, exam_name):
    from app.db import workspace_models as wm

    return [
        e.id
        for e in db.query(wm.WorkspaceExamEvent)
        .filter_by(exam_name=exam_name)
        .order_by(wm.WorkspaceExamEvent.id)
        .all()
    ]


def _write_class_averages_variant(path: Path, delta: float = 5.0) -> Path:
    """与 _write_grade23_class_averages 同构、数值整体偏移的均分表。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append([
        "班型", "班级", "班主任", "语文", "数学", "英语",
        "物理", None, "加3同均分", "主三门", "3+3总分",
    ])
    ws.append([None, None, None, None, None, None, "原始", "等级", None, None, None])
    ws.append(["平行班", "06", "周老师", 101.2 + delta, 108.3, 110.4, 55.1, 61.2, 180.5, 319.9, 500.4])
    ws.append([None, "07", "吴老师", 102.2 + delta, 109.3, 111.4, 56.1, 62.2, 181.5, 322.9, 504.4])
    wb.save(path)
    return path


def _merge(client, db, v1_seed, source_event_id, target_event_id, name):
    p = client.post(
        f"{API}/homeroom/exams/merge-preview",
        json={
            "academic_year_id": v1_seed.ay_id,
            "class_id": v1_seed.h6_id,
            "source_event_id": source_event_id,
            "target_event_id": target_event_id,
        },
    )
    assert p.status_code == 200, p.text
    r = client.post(
        f"{API}/homeroom/exams/merge-confirm",
        json={
            "token": p.json()["token"],
            "confirm_name": name,
            "accept_conflicts": True,
        },
    )
    assert r.status_code == 200, r.text


# ────────────────── 4A：均分改日期归入同一事件重导 ──────────────────


def test_4a_average_reimport_different_date_same_event_idempotent(
    client, v1_seed, tmp_path,
):
    """已导入班级均分后用不同日期 + exam_choices 指向同一事件重导：
    同值幂等 skipped（修复前实测 500 IntegrityError）。"""
    from app.db import workspace_models as wm

    path = _write_grade23_class_averages(tmp_path / "FIX04均分.xlsx")
    first = _upload_homeroom(client, path, v1_seed, EXAM_AV)
    _confirm(client, first)

    db = _db()
    try:
        rows = db.query(wm.WorkspaceClassAverage).filter_by(exam_name=EXAM_AV).all()
        assert len(rows) == 2
        eid = rows[0].exam_event_id
        assert eid is not None
    finally:
        db.close()

    # 不同日期 + exam_choices 归入同一事件 → 幂等 skipped，不撞唯一约束
    again = _upload_homeroom(client, path, v1_seed, EXAM_AV, exam_date="2025-11-11")
    body = _confirm(client, again, exam_choices={path.name: eid})
    assert body["skipped"] == 2
    assert body["imported"] == 0

    db = _db()
    try:
        assert (
            db.query(wm.WorkspaceClassAverage).filter_by(exam_name=EXAM_AV).count() == 2
        )
    finally:
        db.close()


# ────────────────── 4B：同名均分事件归并后重导 ──────────────────


def test_4b_merged_average_events_reimport_idempotent(client, v1_seed, tmp_path):
    """两份同名同日均分各建一事件 → 归并为一个事件 → 再次导入：
    幂等 skipped（修复前实测 500 MultipleResultsFound）。"""
    from app.db import workspace_models as wm

    path = _write_grade23_class_averages(tmp_path / "FIX04归并均分.xlsx")
    first = _upload_homeroom(client, path, v1_seed, EXAM_AV_MERGED)
    _confirm(client, first)
    second = _upload_homeroom(client, path, v1_seed, EXAM_AV_MERGED)
    _confirm(client, second, exam_choices={path.name: "new"})

    db = _db()
    try:
        ids = _event_ids(db, EXAM_AV_MERGED)
        assert len(ids) == 2
        assert (
            db.query(wm.WorkspaceClassAverage)
            .filter_by(exam_name=EXAM_AV_MERGED)
            .count() == 4
        )
    finally:
        db.close()
    _merge(client, db, v1_seed, ids[1], ids[0], EXAM_AV_MERGED)

    third = _upload_homeroom(client, path, v1_seed, EXAM_AV_MERGED)
    body = _confirm(client, third)
    assert body["skipped"] == 2
    assert body["imported"] == 0

    db = _db()
    try:
        # 归并集内两行保留（不覆盖归并时保留的其他来源记录），不新增
        assert (
            db.query(wm.WorkspaceClassAverage)
            .filter_by(exam_name=EXAM_AV_MERGED)
            .count() == 4
        )
    finally:
        db.close()


def test_4b_merged_average_reimport_different_value_revise(client, v1_seed, tmp_path):
    """归并集内重导异值：revise=true 走修订更新归并集内行（可读、不 500）；
    revise=false → 409 可读冲突。"""
    from app.db import workspace_models as wm

    path = _write_grade23_class_averages(tmp_path / "FIX04归并均分改.xlsx")
    first = _upload_homeroom(client, path, v1_seed, EXAM_AV_MERGED + "改")
    _confirm(client, first)
    second = _upload_homeroom(client, path, v1_seed, EXAM_AV_MERGED + "改")
    _confirm(client, second, exam_choices={path.name: "new"})
    db = _db()
    try:
        ids = _event_ids(db, EXAM_AV_MERGED + "改")
    finally:
        db.close()
    _merge(client, db, v1_seed, ids[1], ids[0], EXAM_AV_MERGED + "改")

    revised_path = _write_class_averages_variant(tmp_path / "FIX04归并均分改.xlsx")
    assert revised_path == path  # 同一路径复用（覆盖写，数值整体偏移）
    third = _upload_homeroom(client, path, v1_seed, EXAM_AV_MERGED + "改")
    _confirm(client, third, status=409)  # 异值 revise=false → 可读 409

    fourth = _upload_homeroom(client, path, v1_seed, EXAM_AV_MERGED + "改")
    body = _confirm(client, fourth, revise=True)
    assert body["revised"] == 4  # 2 个班 × 归并集内 2 行统一修订
    db = _db()
    try:
        rows = (
            db.query(wm.WorkspaceClassAverage)
            .filter_by(exam_name=EXAM_AV_MERGED + "改")
            .all()
        )
        assert len(rows) == 4
        by_class = {row.class_num: row.subject_averages["语文"] for row in rows}
        assert by_class == {6: 106.2, 7: 107.2}
    finally:
        db.close()


# ────────────────── 4C：同批双学号（同一 identity） ──────────────────


def _add_current_aliases(db, v1_seed, identity_id, aliases):
    from app.db import workspace_models as wm

    for a in aliases:
        db.add(
            wm.WsStudentAlias(
                identity_id=identity_id, alias_value=a,
                data_domain="homeroom", academic_year_id=v1_seed.ay_id,
                alias_scope=str(v1_seed.ay_id), source="synthetic-test",
            )
        )
    db.commit()


def test_4c_same_identity_two_aliases_same_value_merged(client, v1_seed, tmp_path):
    """同一学生（秦甲）新旧两个学号同批出现、数值相同：按解析后 identity
    去重，同值合并为一行（修复前实测 500 唯一约束崩溃）。"""
    from app.db import workspace_models as wm

    db = _db()
    try:
        jia_pid = (
            db.query(wm.WsStudentAlias)
            .filter_by(alias_value=ALIAS_JIA_H, data_domain="homeroom")
            .one()
            .identity_id
        )
        _add_current_aliases(db, v1_seed, jia_pid, ["SYN-OLD-4C", "SYN-NEW-4C"])
    finally:
        db.close()

    path = _write_grade23_xlsx(
        tmp_path / "FIX04双号.xlsx",
        [
            _student_row("SYN-OLD-4C", "秦甲", chinese=80),
            _student_row("SYN-NEW-4C", "秦甲", chinese=80),
        ],
    )
    prev = _upload_homeroom(client, path, v1_seed, EXAM_DOUBLE)
    body = _confirm(client, prev)
    assert body["imported"] >= 1

    db = _db()
    try:
        facts = (
            db.query(wm.ScoreFact)
            .filter_by(exam_name=EXAM_DOUBLE, data_domain="homeroom")
            .all()
        )
        # 同键只落一行（语文成绩落秦甲 identity，不因双学号翻倍）
        chinese = [f for f in facts if f.subject == "语文"]
        assert len(chinese) == 1
        assert chinese[0].identity_id == jia_pid
        assert chinese[0].score == 80
    finally:
        db.close()


def test_4c_same_identity_two_aliases_conflict_readable(client, v1_seed, tmp_path):
    """同一学生两个学号同批出现但数值不同 → 可读 409，列学号对与两值，
    整批拒绝零写入（revise 也不能消解批内自相矛盾）。"""
    from app.db import workspace_models as wm

    db = _db()
    try:
        jia_pid = (
            db.query(wm.WsStudentAlias)
            .filter_by(alias_value=ALIAS_JIA_H, data_domain="homeroom")
            .one()
            .identity_id
        )
        _add_current_aliases(db, v1_seed, jia_pid, ["SYN-OLD-4D", "SYN-NEW-4D"])
    finally:
        db.close()

    path = _write_grade23_xlsx(
        tmp_path / "FIX04双号冲突.xlsx",
        [
            _student_row("SYN-OLD-4D", "秦甲", chinese=80),
            _student_row("SYN-NEW-4D", "秦甲", chinese=90),
        ],
    )
    prev = _upload_homeroom(client, path, v1_seed, "FIX04双号冲突考")
    r = client.post(
        f"{API}/imports/confirm",
        json={"token": prev.json()["token"], "revise": True},
    )
    assert r.status_code == 409, r.text
    conflicts = r.json()["conflicts"]
    assert conflicts, "必须列出冲突明细"
    entry = conflicts[0]
    assert set(entry.get("aliases", [])) == {"SYN-OLD-4D", "SYN-NEW-4D"}
    assert {entry.get("existing_score"), entry.get("new_score")} == {80, 90}

    db = _db()
    try:
        assert (
            db.query(wm.ScoreFact)
            .filter_by(exam_name="FIX04双号冲突考")
            .count() == 0
        )
    finally:
        db.close()


# ────────────────── 成功反例：归并成绩 revise 重导（必须保住） ──────────────────


def test_counterexample_merged_scores_revise_reimport_succeeds(
    client, v1_seed, tmp_path,
):
    """probe3 4B_merged_scores_counterexample：普通成绩两事件归并后 revise
    重导 → 200 revised=1，两个事件编号都保留。此为实测成功的正确路径，
    修复不得破坏。"""
    from app.db import workspace_models as wm

    path = _write_grade23_xlsx(
        tmp_path / "FIX04双成绩.xlsx",
        [_student_row("SYN-MERGE-4E", "秦合并", chinese=77)],
    )
    first = _upload_homeroom(client, path, v1_seed, EXAM_SCORES)
    _confirm(client, first)
    second = _upload_homeroom(client, path, v1_seed, EXAM_SCORES)
    _confirm(client, second, exam_choices={path.name: "new"})

    db = _db()
    try:
        ids = _event_ids(db, EXAM_SCORES)
        assert len(ids) == 2
    finally:
        db.close()
    _merge(client, db, v1_seed, ids[1], ids[0], EXAM_SCORES)

    # 改分重导（revise）：200，修订一行，两事件行都保留
    path = _write_grade23_xlsx(
        path, [_student_row("SYN-MERGE-4E", "秦合并", chinese=78)],
    )
    third = _upload_homeroom(client, path, v1_seed, EXAM_SCORES)
    body = _confirm(client, third, revise=True)
    assert body["revised"] == 1

    db = _db()
    try:
        facts = (
            db.query(wm.ScoreFact)
            .filter_by(exam_name=EXAM_SCORES, data_domain="homeroom")
            .all()
        )
        assert sorted(f.score for f in facts if f.subject == "语文") == [77, 78]
        assert {f.exam_event_id for f in facts} == set(ids)  # 事件编号不被抹掉
    finally:
        db.close()
