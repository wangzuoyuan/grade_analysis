"""同名批量确认（confirm-batch / undo）安全口径集成测试。

镜像 test_rollover.py 的隔离方式（模块级 EXAM_TRACKER_DIR + 自建 seed）。
覆盖：安全批量成功（含 roster-only 学号）、多候选拒绝/显式选择、已占用
候选拒绝、批内 g1/g2 重复、越权班级、非同名候选、事务回滚（部分成功为 0）、
仅撤销本批新增链接（提交前已有关联不受影响）。
"""

import os
import tempfile

# 必须在 import app.main 之前把 EXAM_TRACKER_DIR 钉到临时目录，使
# app.paths / app.db.models 的 engine 绑定隔离库。
_TMP = tempfile.mkdtemp(prefix="rollover_batch_test_")
os.environ["EXAM_TRACKER_DIR"] = os.path.join(_TMP, "examdata")
os.makedirs(os.environ["EXAM_TRACKER_DIR"], exist_ok=True)

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.models import (
    SessionLocal,
    Exam,
    SubjectScore,
    TotalScore,
    Teacher,
    ClassRoster,
    StudentAlias,
    StudentIdentity,
    RolloverConfirmBatch,
)
from app.analysis.identity import (
    aliases_of,
    ensure_identity,
    identity_of,
    link_aliases,
)


@pytest.fixture(scope="module")
def client():
    seed()
    return TestClient(app)


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _seed_exam(eid, name, grade, exam_date):
    s = SessionLocal()
    s.add(
        Exam(
            id=eid,
            name=name,
            grade=grade,
            semester="上",
            exam_date=exam_date,
            exam_type="月考",
        )
    )
    s.commit()
    s.close()


def _seed_subject(exam_id, student_id, name, class_num):
    s = SessionLocal()
    s.add(
        SubjectScore(
            exam_id=exam_id,
            student_id=student_id,
            name=name,
            class_num=class_num,
            subject="语文",
            raw_score=80.0,
        )
    )
    s.commit()
    s.close()


def seed():
    """数据布局：高一班 6 / 高二班 3（教师绑定 G1->6, G2->3）。

    同名候选（G1 -> G2 class 3）：
      陈一 g1_101 -> g2_201（唯一候选，严格安全）
      王五 g1_103(class6) + g1_104(class5) -> g2_202（多候选）
      赵六 g1_105（seed 时已 manual 链给他人）-> g2_204（候选被占用）
      吴九 g1_107 -> g2_207 / g2_208（一个候选被两行共享，批内重复）
      郑一 g1_130 -> g2_230（撤销测试）
      钱九 g1_160 -> TMP-2-3-钱九（仅花名册的 roster-only 学号）
    另有：g2_910 外班生（class 9，越权）、g2_900/g1_900 预链（manual，
    用于验证撤销不破坏既有关联）。
    """
    s = SessionLocal()
    for m in (SubjectScore, TotalScore, Exam, Teacher, ClassRoster,
              StudentAlias, StudentIdentity, RolloverConfirmBatch):
        s.query(m).delete()
    s.commit()
    s.close()

    _seed_exam(101, "高一期末", 1, "2024-06")
    _seed_exam(201, "高二开学测", 2, "2025-09")

    # ── G1（class 6 为主）──
    _seed_subject(101, "g1_101", "陈一", 6)
    _seed_subject(101, "g1_103", "王五", 6)
    _seed_subject(101, "g1_104", "王五", 5)   # 多候选：跨班的同名
    _seed_subject(101, "g1_105", "赵六", 6)   # seed 后 manual 链给他人
    _seed_subject(101, "g1_107", "吴九", 6)
    _seed_subject(101, "g1_130", "郑一", 6)
    _seed_subject(101, "g1_140", "孙七", 6)   # 非「郑一」同名，用于候选核验拒绝
    _seed_subject(101, "g1_150", "冯八", 6)
    _seed_subject(101, "g1_152", "冯 八", 7)  # 姓名带空格：规范化后仍是同名
    _seed_subject(101, "g1_160", "钱九", 6)   # roster-only g2 的候选
    _seed_subject(101, "g1_900", "预链", 6)

    # ── G2（class 3 为主）──
    _seed_subject(201, "g2_201", "陈一", 3)
    _seed_subject(201, "g2_202", "王五", 3)
    _seed_subject(201, "g2_204", "赵六", 3)
    _seed_subject(201, "g2_207", "吴九", 3)
    _seed_subject(201, "g2_208", "吴九", 3)
    _seed_subject(201, "g2_230", "郑一", 3)
    _seed_subject(201, "g2_250", "冯八", 3)
    _seed_subject(201, "g2_900", "预链", 3)
    _seed_subject(201, "g2_910", "外班生", 9)  # 越权班级

    # 主三门（候选信息展示用）
    for sid in ("g1_101", "g1_103", "g1_104"):
        s = SessionLocal()
        s.add(TotalScore(exam_id=101, student_id=sid,
                         total_type="主三门", total_score=240.0, xueji_rank=10))
        s.commit()
        s.close()

    # roster-only 高二学生（仅有花名册、尚无成绩）：TMP- 临时学号
    s = SessionLocal()
    s.merge(ClassRoster(student_id="TMP-2-3-钱九", name="钱九",
                        grade=2, class_num=3, excluded=0))
    s.commit()
    s.close()

    # 提交前已存在的关联（撤销绝不能碰）：
    #   - g1_105 赵六 已 manual 链到「别的身份」-> 候选被占用
    #   - g1_900 + g2_900 预链 -> 既有 identity，undo 后必须原样保留
    s = SessionLocal()
    other_iid = ensure_identity(s, display_name="赵六他哥")
    link_aliases(s, other_iid, [("g1_105", 1)], "manual")
    pre_iid = ensure_identity(s, display_name="预链")
    link_aliases(s, pre_iid, [("g1_900", 1), ("g2_900", 2)], "manual")
    s.commit()
    s.close()

    s = SessionLocal()
    s.merge(Teacher(id=1, target_class_high1=6, target_class_high2=3))
    s.commit()
    s.close()


# ─────────────────────────── 成功路径 ───────────────────────────


def test_confirm_batch_success_link_and_new(client, db):
    """唯一候选 link（服务端自动补 g1）+ 多候选行判「新学生」，全部落库。"""
    r = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 3,
            "items": [
                {"g2_student_id": "g2_201", "decision": "link"},
                {"g2_student_id": "g2_202", "decision": "new"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["linked"] == 1
    assert body["new_students"] == 1
    assert body["grade"] == 2 and body["class_num"] == 3

    # 陈一：g2 与 g1 同一 identity
    assert identity_of(db, "g2_201") == identity_of(db, "g1_101")
    iid_link = identity_of(db, "g2_201")
    alias_map = {a.student_id: a.grade for a in aliases_of(db, iid_link)}
    assert alias_map == {"g1_101": 1, "g2_201": 2}

    # 王五：新学生 -> 独立 identity，只挂 g2 学号
    iid_new = identity_of(db, "g2_202")
    assert iid_new is not None and iid_new != iid_link
    assert {a.student_id for a in aliases_of(db, iid_new)} == {"g2_202"}

    # 批次快照记录了本批实际新建的 alias / identity（供 undo）
    batch = db.query(RolloverConfirmBatch).filter_by(id=body["batch_id"]).one()
    recorded = {rec["student_id"] for rec in batch.created_aliases}
    assert recorded == {"g2_201", "g1_101", "g2_202"}
    assert sorted(batch.created_identities) == sorted([iid_link, iid_new])
    assert batch.undone == 0

    # 结果逐行回显（姓名取服务端库内真相）
    results = {x["g2_student_id"]: x for x in body["results"]}
    assert results["g2_201"]["status"] == "linked"
    assert results["g2_201"]["name"] == "陈一"
    assert results["g2_202"]["status"] == "new"


def test_confirm_batch_accepts_roster_only_g2(client, db):
    """仅花名册（TMP- 临时学号）的高二学生也能确认：归属按 roster 判定。"""
    r = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 3,
            "items": [
                {"g2_student_id": "TMP-2-3-钱九", "decision": "link",
                 "g1_student_id": "g1_160"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert identity_of(db, "TMP-2-3-钱九") == identity_of(db, "g1_160")


def test_confirm_batch_accepts_normalized_same_name_pick(client, db):
    """显式指定的候选姓名带空格：规范化后一致即认可（冯 八 ≡ 冯八）。"""
    r = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 3,
            "items": [
                {"g2_student_id": "g2_250", "decision": "link",
                 "g1_student_id": "g1_152"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert identity_of(db, "g2_250") == identity_of(db, "g1_152")


# ─────────────────────────── 拒绝路径（整批 422，零落库） ───────────────────────────


def _batch_count(db):
    return db.query(RolloverConfirmBatch).count()


def test_confirm_batch_multi_candidate_row_rejects_without_pick(client, db):
    """同名候选多于一个且未显式选择 -> 拒绝（绝不自动猜）；显式选择后通过。"""
    # 构造真正的多候选行：g1_151 冯八（class 8）与 g1_150 构成两个候选
    _seed_subject(101, "g1_151", "冯八", 8)
    _seed_subject(201, "g2_260", "冯八", 3)

    r = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 3,
            "items": [{"g2_student_id": "g2_260", "decision": "link"}],
        },
    )
    assert r.status_code == 422, r.text
    assert "必须明确选择其一" in r.json()["detail"]
    assert identity_of(db, "g2_260") is None

    # 显式选择第二个候选（跨班同名）-> 成功
    r2 = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 3,
            "items": [
                {"g2_student_id": "g2_260", "decision": "link",
                 "g1_student_id": "g1_151"},
            ],
        },
    )
    assert r2.status_code == 200, r2.text
    assert identity_of(db, "g2_260") == identity_of(db, "g1_151")


def test_confirm_batch_rejects_occupied_candidate_and_rolls_back(client, db):
    """候选已被关联到别人 -> 整批 422，同批的正常行也零落库（无部分成功）。"""
    batches_before = _batch_count(db)
    identities_before = db.query(StudentIdentity).count()
    r = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 3,
            "items": [
                {"g2_student_id": "g2_230", "decision": "link",
                 "g1_student_id": "g1_130"},  # 正常行
                {"g2_student_id": "g2_204", "decision": "link"},  # 候选 g1_105 被占用
            ],
        },
    )
    assert r.status_code == 422, r.text
    assert "已被关联到其他学生" in r.json()["detail"]

    # 事务回滚：正常行也没有写库，批次快照与 identity 数量不变
    db.expire_all()
    assert identity_of(db, "g2_230") is None
    assert identity_of(db, "g1_130") is None
    assert identity_of(db, "g2_204") is None
    assert _batch_count(db) == batches_before
    assert db.query(StudentIdentity).count() == identities_before


def test_confirm_batch_rejects_g1_not_same_name(client, db):
    """显式指定的 g1 不是本行学生的同名候选 -> 拒绝。"""
    r = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 3,
            "items": [
                {"g2_student_id": "g2_230", "decision": "link",
                 "g1_student_id": "g1_140"},
            ],
        },
    )
    assert r.status_code == 422
    assert "不是「郑一」在高1的同名候选" in r.json()["detail"]


def test_confirm_batch_rejects_duplicate_g1_in_batch(client, db):
    """批内两行重复使用同一高一学号 -> 整批拒绝。"""
    r = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 3,
            "items": [
                {"g2_student_id": "g2_207", "decision": "link",
                 "g1_student_id": "g1_107"},
                {"g2_student_id": "g2_208", "decision": "link",
                 "g1_student_id": "g1_107"},
            ],
        },
    )
    assert r.status_code == 422, r.text
    assert "重复使用" in r.json()["detail"]
    assert identity_of(db, "g2_207") is None
    assert identity_of(db, "g2_208") is None


def test_confirm_batch_rejects_duplicate_g2_in_batch(client, db):
    r = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 3,
            "items": [
                {"g2_student_id": "g2_207", "decision": "link",
                 "g1_student_id": "g1_107"},
                {"g2_student_id": "g2_207", "decision": "new"},
            ],
        },
    )
    assert r.status_code == 422
    assert "与第 1 行重复" in r.json()["detail"]


def test_confirm_batch_rejects_out_of_scope_student(client, db):
    """他班成绩学号 / 完全不存在的学号 -> 拒绝。"""
    r1 = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 3,
            "items": [{"g2_student_id": "g2_910", "decision": "new"}],
        },
    )
    assert r1.status_code == 422
    assert "不一致" in r1.json()["detail"]

    r2 = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 3,
            "items": [{"g2_student_id": "g2_ghost", "decision": "new"}],
        },
    )
    assert r2.status_code == 422
    assert "不在高2" in r2.json()["detail"]


def test_confirm_batch_rejects_already_linked_g2(client, db):
    """g2 学号已有关联身份（如之前已单点确认过）-> 拒绝并提示刷新。"""
    r = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 3,
            "items": [{"g2_student_id": "g2_201", "decision": "link"}],
        },
    )
    assert r.status_code == 422
    assert "已关联跨学年身份" in r.json()["detail"]


def test_confirm_batch_rejects_scope_mismatch_and_bad_grade(client):
    """目标班与教师绑定不一致 -> 409；grade 非法 / 空批次 -> 422。"""
    r = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 9,
            "items": [{"g2_student_id": "g2_230", "decision": "new"}],
        },
    )
    assert r.status_code == 409
    assert "不一致" in r.json()["detail"]

    r2 = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 1,
            "class_num": 6,
            "items": [{"g2_student_id": "g2_230", "decision": "new"}],
        },
    )
    assert r2.status_code == 422

    r3 = client.post(
        "/api/rollover/confirm-batch",
        json={"grade": 2, "class_num": 3, "items": []},
    )
    assert r3.status_code == 422


# ─────────────────────────── 撤销：只删本批新增 ───────────────────────────


def test_undo_removes_batch_links_and_keeps_preexisting(client, db):
    """撤销删掉本批新建的 alias/identity，提交前已有的 manual 关联原样保留。"""
    pre_iid = identity_of(db, "g2_900")
    assert pre_iid is not None
    identities_before = db.query(StudentIdentity).count()

    r = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 3,
            "items": [
                {"g2_student_id": "g2_230", "decision": "link",
                 "g1_student_id": "g1_130"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    batch_id = r.json()["batch_id"]
    assert identity_of(db, "g2_230") == identity_of(db, "g1_130")

    undo = client.post(f"/api/rollover/confirm-batch/{batch_id}/undo")
    assert undo.status_code == 200, undo.text
    body = undo.json()
    assert sorted(body["removed_aliases"]) == ["g1_130", "g2_230"]

    db.expire_all()
    assert identity_of(db, "g2_230") is None
    assert identity_of(db, "g1_130") is None
    # 本批新建的 identity 已随撤销删除（无 alias / 无历史）
    assert db.query(StudentIdentity).count() == identities_before
    # 提交前已存在的关联不受影响
    assert identity_of(db, "g2_900") == pre_iid
    assert identity_of(db, "g1_900") == pre_iid
    batch = db.query(RolloverConfirmBatch).filter_by(id=batch_id).one()
    assert batch.undone == 1

    # 重复撤销 -> 409
    again = client.post(f"/api/rollover/confirm-batch/{batch_id}/undo")
    assert again.status_code == 409


def test_undo_skips_alias_already_unlinked_individually(client, db):
    """批量确认后被单行解除的学号：撤销时跳过并说明，其余照常回滚。"""
    r = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 3,
            "items": [
                {"g2_student_id": "g2_207", "decision": "link",
                 "g1_student_id": "g1_107"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    batch_id = r.json()["batch_id"]

    # 单行解除关联（保留的能力）
    single = client.delete("/api/rollover/link/g1_107")
    assert single.status_code == 200

    undo = client.post(f"/api/rollover/confirm-batch/{batch_id}/undo")
    assert undo.status_code == 200, undo.text
    body = undo.json()
    assert body["removed_aliases"] == ["g2_207"]
    skipped = {x["student_id"]: x["reason"] for x in body["skipped"]}
    assert "g1_107" in skipped
    assert identity_of(db, "g2_207") is None


def test_undo_unknown_batch_404(client):
    r = client.post("/api/rollover/confirm-batch/no-such-batch/undo")
    assert r.status_code == 404


def test_undo_rejects_scope_mismatch(client, db):
    """教师绑定已改到别的班时撤销 -> 409；恢复绑定后可撤销。"""
    r = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 3,
            "items": [{"g2_student_id": "g2_208", "decision": "new"}],
        },
    )
    assert r.status_code == 200, r.text
    batch_id = r.json()["batch_id"]

    s = SessionLocal()
    s.merge(Teacher(id=1, target_class_high1=6, target_class_high2=4))
    s.commit()
    s.close()

    blocked = client.post(f"/api/rollover/confirm-batch/{batch_id}/undo")
    assert blocked.status_code == 409
    assert "不一致" in blocked.json()["detail"]

    s = SessionLocal()
    s.merge(Teacher(id=1, target_class_high1=6, target_class_high2=3))
    s.commit()
    s.close()

    ok = client.post(f"/api/rollover/confirm-batch/{batch_id}/undo")
    assert ok.status_code == 200, ok.text
    assert ok.json()["removed_aliases"] == ["g2_208"]


# ─────────────────────────── g1 与批内 g2 撞号（与顺序无关） ───────────────────────────


def _seed_dual_sid():
    """dual_107：既在高一（吴九，class 6）又在高二（class 3）有成绩的学号，
    使「g1 == 批内某 g2」在两侧作用域校验下都能走到撞号检查。"""
    _seed_subject(101, "dual_107", "吴九", 6)
    _seed_subject(201, "dual_107", "吴九", 3)


def test_confirm_batch_rejects_g1_equal_to_later_g2(client, db):
    """正序：第 1 行的 g1 == 第 2 行的 g2 -> 整批拒绝、零落库。"""
    _seed_dual_sid()
    r = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 3,
            "items": [
                {"g2_student_id": "g2_207", "decision": "link",
                 "g1_student_id": "dual_107"},
                {"g2_student_id": "dual_107", "decision": "new"},
            ],
        },
    )
    assert r.status_code == 422, r.text
    assert "批内不能混用同一学号" in r.json()["detail"]
    db.expire_all()
    assert identity_of(db, "g2_207") is None
    assert identity_of(db, "dual_107") is None


def test_confirm_batch_rejects_g1_equal_to_earlier_g2(client, db):
    """反序：前面行的 g2 == 后面行的 g1（旧实现只查 seen_g2 漏掉的顺序）。"""
    r = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 3,
            "items": [
                {"g2_student_id": "dual_107", "decision": "new"},
                {"g2_student_id": "g2_207", "decision": "link",
                 "g1_student_id": "dual_107"},
            ],
        },
    )
    assert r.status_code == 422, r.text
    assert "批内不能混用同一学号" in r.json()["detail"]
    db.expire_all()
    assert identity_of(db, "g2_207") is None
    assert identity_of(db, "dual_107") is None


def test_confirm_batch_rejects_g1_equal_to_own_g2(client, db):
    """同行自撞：g1 == 本行 g2 -> 拒绝。"""
    r = client.post(
        "/api/rollover/confirm-batch",
        json={
            "grade": 2,
            "class_num": 3,
            "items": [
                {"g2_student_id": "dual_107", "decision": "link",
                 "g1_student_id": "dual_107"},
            ],
        },
    )
    assert r.status_code == 422, r.text
    assert "批内不能混用同一学号" in r.json()["detail"]
    assert identity_of(db, "dual_107") is None
