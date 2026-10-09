"""长期分段（名次区间）配置 API 测试（P0-A3）。

AnalysisConfig 是全局单行配置（id=1），只含四个名次阈值数字，不涉及任何
学生个人信息；用例全部使用合成数值与合成姓名（「合成学生甲」等），不使用
真实学生数据。

覆盖：
- GET 无记录时回落学校默认三段（高分 1–80、临界 400–500、薄弱 501+）；
- PUT 保存自定义阈值并持久化；非法载荷 400 且不改写现值；
- DELETE 恢复默认（删除自定义行，读取侧回落默认值）；
- 临时查询区间（rank-range / rank-frequency / band-trend / 考试详情）均为
  只读路径，任意区间查询不得写改长期配置（隔离证明）。
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

DEFAULTS = {
    "high_score_max": 80,
    "critical_min": 400,
    "critical_max": 500,
    "weak_min": 501,
}
CUSTOM = {
    "high_score_max": 100,
    "critical_min": 350,
    "critical_max": 399,
    "weak_min": 600,
}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _config_starts_from_defaults():
    """每个用例前删掉自定义行（从默认态出发），用例后再清一次，
    用例之间互不依赖执行顺序。"""
    _delete_config_row()
    yield
    _delete_config_row()


def _delete_config_row():
    from app.db.models import AnalysisConfig, SessionLocal

    db = SessionLocal()
    try:
        row = db.query(AnalysisConfig).filter(AnalysisConfig.id == 1).first()
        if row:
            db.delete(row)
            db.commit()
    finally:
        db.close()


def _seed_synthetic_exam():
    """建一场合成考试：3 名合成学生、主三门总分与学籍名次（5/450/600），
    正好分别落入默认三段，便于查询类端点返回非空结果。"""
    from app.db.models import Exam, SessionLocal, SubjectScore, TotalScore

    db = SessionLocal()
    try:
        exam = Exam(
            name="P0A3合成月考",
            grade=1,
            semester="上",
            exam_date="2026-09-01",
            exam_type="月考",
            source_files=[],
        )
        db.add(exam)
        db.flush()
        rows = [
            ("T900001", "合成学生甲", 1, 5, 660.0),
            ("T900002", "合成学生乙", 2, 450, 470.0),
            ("T900003", "合成学生丙", 2, 600, 380.0),
        ]
        for sid, name, class_num, rank, score in rows:
            db.add(
                SubjectScore(
                    exam_id=exam.id,
                    student_id=sid,
                    class_num=class_num,
                    name=name,
                    subject="语文",
                    raw_score=score / 3,
                )
            )
            db.add(
                TotalScore(
                    exam_id=exam.id,
                    student_id=sid,
                    total_type="主三门",
                    total_score=score,
                    xueji_rank=rank,
                    grade_rank=rank,
                )
            )
        db.commit()
        return exam.id
    finally:
        db.close()


def test_get_returns_school_default_three_bands_without_row(client):
    """无自定义记录时：返回学校默认三段，且 defaults/is_default 一并给出。"""
    res = client.get("/api/analysis-config")
    assert res.status_code == 200
    data = res.json()
    assert {k: data[k] for k in DEFAULTS} == DEFAULTS
    assert data["defaults"] == DEFAULTS
    assert data["is_default"] is True


def test_put_saves_custom_long_term_bands(client):
    """PUT 写入长期配置并持久化：GET 回读一致，is_default 变 False。"""
    res = client.put("/api/analysis-config", json=CUSTOM)
    assert res.status_code == 200
    data = res.json()
    assert {k: data[k] for k in CUSTOM} == CUSTOM
    assert data["is_default"] is False

    readback = client.get("/api/analysis-config").json()
    assert {k: readback[k] for k in CUSTOM} == CUSTOM
    # 默认值清单不受自定义影响：学校默认三段保留为默认值
    assert readback["defaults"] == DEFAULTS


def test_put_same_as_default_reports_is_default(client):
    res = client.put("/api/analysis-config", json=DEFAULTS)
    assert res.status_code == 200
    assert res.json()["is_default"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {**CUSTOM, "high_score_max": 0},          # 非正数
        {**CUSTOM, "weak_min": -1},               # 非正数
        {**CUSTOM, "critical_min": 500, "critical_max": 400},  # 下界大于上界
    ],
)
def test_put_rejects_invalid_payload_without_touching_config(client, payload):
    before = client.get("/api/analysis-config").json()
    res = client.put("/api/analysis-config", json=payload)
    assert res.status_code == 400
    after = client.get("/api/analysis-config").json()
    assert {k: after[k] for k in DEFAULTS} == {k: before[k] for k in DEFAULTS}
    assert after["is_default"] is True


def test_delete_restores_school_default_bands(client):
    """恢复默认：DELETE 删除自定义行，读取侧回落默认三段。"""
    assert client.put("/api/analysis-config", json=CUSTOM).status_code == 200

    res = client.delete("/api/analysis-config")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert {k: body[k] for k in DEFAULTS} == DEFAULTS

    readback = client.get("/api/analysis-config").json()
    assert {k: readback[k] for k in DEFAULTS} == DEFAULTS
    assert readback["is_default"] is True

    from app.db.models import AnalysisConfig, SessionLocal

    db = SessionLocal()
    try:
        assert db.query(AnalysisConfig).filter(AnalysisConfig.id == 1).first() is None
    finally:
        db.close()


def test_delete_is_idempotent_without_row(client):
    res = client.delete("/api/analysis-config")
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert res.json()["is_default"] is True


def test_temporary_rank_range_query_does_not_write_config(client):
    """隔离证明：临时任意区间查询（含越过默认三段的极端区间）不写改长期配置。"""
    exam_id = _seed_synthetic_exam()
    before = client.get("/api/analysis-config").json()

    res = client.get(
        "/api/rank-range",
        params={"exam_id": exam_id, "metric": "total:主三门", "rank_min": 1, "rank_max": 999999},
    )
    assert res.status_code == 200
    assert len(res.json()["rows"]) == 3  # 任意大区间只影响本次筛选结果

    after = client.get("/api/analysis-config").json()
    assert {k: after[k] for k in DEFAULTS} == {k: before[k] for k in DEFAULTS}
    assert after["is_default"] is True


def test_temporary_rank_frequency_query_does_not_write_config(client):
    exam_id = _seed_synthetic_exam()
    before = client.get("/api/analysis-config").json()

    res = client.get(
        "/api/rank-frequency",
        params={"grade": 1, "metric": "total:主三门", "exam_ids": str(exam_id)},
    )
    assert res.status_code == 200

    after = client.get("/api/analysis-config").json()
    assert {k: after[k] for k in DEFAULTS} == {k: before[k] for k in DEFAULTS}


def test_band_consumers_read_config_without_writing(client):
    """消费端（band-trend / 考试详情）只读配置：按当前长期分段计算，
    不回写、不漂移。"""
    exam_id = _seed_synthetic_exam()
    assert client.put("/api/analysis-config", json=CUSTOM).status_code == 200

    trend = client.get("/api/band-trend", params={"grade": 1}).json()
    assert trend["band_config"] == CUSTOM  # 计算即时采用新阈值

    detail = client.get(f"/api/exams/{exam_id}").json()
    assert detail["band_config"] == CUSTOM

    readback = client.get("/api/analysis-config").json()
    assert {k: readback[k] for k in CUSTOM} == CUSTOM  # 消费端没有改写配置
