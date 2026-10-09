"""成绩分析WebApp测试套件

测试策略：
- API 测试验证端点可访问且返回正确 JSON 结构。
- conftest 在导入应用前设置临时数据目录，所有写入仅发生在隔离数据库。
"""

import pytest
from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture
def client():
    return TestClient(app)

# === API 测试 ===

def test_health(client):
    """Step 6: 健康检查端点"""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True

def test_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["message"] == "学情追踪 API"

def test_teacher_get(client):
    """Step 6: 获取班主任信息"""
    response = client.get("/api/teacher")
    assert response.status_code == 200
    data = response.json()
    assert "target_class_high1" in data or "id" in data

def test_bind_class(client):
    """Step 6: 绑定班级"""
    response = client.post("/api/teacher/bind-class", json={"class_num": 4, "grade": 1})
    assert response.status_code == 200
    assert response.json()["ok"] is True

def test_list_exams(client):
    """Step 6: 列出考试"""
    response = client.get("/api/exams")
    assert response.status_code == 200
    assert "exams" in response.json()

def test_chat_config_endpoint(client):
    response = client.get("/api/chat/config")
    assert response.status_code == 200
    data = response.json()
    assert {"provider", "model", "configured", "base_url_configured"} <= set(data)
    assert "api_key" not in data

def test_exam_not_found(client):
    """Step 6: 考试不存在返回404"""
    response = client.get("/api/exams/99999")
    assert response.status_code == 404

def test_exam_detail_includes_dashboard_payload(client):
    """考试详情返回页面需要的统计、学生明细和分数段字段"""
    exams_response = client.get("/api/exams")
    assert exams_response.status_code == 200
    exams = exams_response.json()["exams"]
    if not exams:
        pytest.skip("no exams in local tracker database")

    response = client.get(f"/api/exams/{exams[0]['id']}")
    assert response.status_code == 200
    data = response.json()

    assert {"max_total", "min_total", "rank_min", "rank_max"} <= set(data["stats"])
    assert isinstance(data["students"], list)
    assert isinstance(data["rank_bands"], list)
    if data["students"]:
        student = data["students"][0]
        assert {
            "student_id",
            "name",
            "xueji",
            "subject_scores",
            "subject_percentiles",
            "total_scores",
            "total_score",
            "grade_rank",
        } <= set(student)

def test_focus_list(client):
    """Step 6: 重点关注名单"""
    response = client.get("/api/focus-list/1")
    assert response.status_code == 200
    assert "focus_list" in response.json()

def test_class_compare(client):
    """Step 6: 班级对比"""
    response = client.get("/api/class/compare")
    assert response.status_code == 200
    assert "exams" in response.json()

def test_student_not_found(client):
    """Step 6: 学生不存在返回404"""
    response = client.get("/api/students/NOTEXIST123")
    assert response.status_code == 404

def test_student_detail_includes_plus_three_subjects(client):
    """学生详情需要返回加三学科，供历次考试明细表展示。"""
    from app.db.models import SessionLocal, SubjectScore
    from sqlalchemy import or_

    db = SessionLocal()
    row = db.query(SubjectScore).filter(
        SubjectScore.subject.notin_(["语文", "数学", "英语"]),
        or_(SubjectScore.raw_score.isnot(None), SubjectScore.grade_score.isnot(None)),
    ).first()
    db.close()
    if row is None:
        pytest.skip("no plus-three subject scores in local tracker database")

    response = client.get(f"/api/students/{row.student_id}")
    assert response.status_code == 200
    subjects = {s["subject"] for s in response.json()["subject_trend"]}
    assert row.subject in subjects

def test_student_detail_excludes_subject_trend_without_scores(client):
    """无原始分/等级分的单科行不能进入趋势线数据。"""
    response = client.get("/api/students/9900615")
    if response.status_code == 404:
        pytest.skip("local database does not contain regression student 9900615")
    assert response.status_code == 200

    invalid_rows = [
        row
        for row in response.json()["subject_trend"]
        if row["exam_date"] == "2025-09" and row["subject"] in {"物理", "化学", "生物", "政治", "历史", "地理"}
    ]
    assert invalid_rows == []

def test_student_detail_includes_grade1_five_total_trend(client):
    """高一学生详情需要返回五门总分趋势，供个人页展示语数英物化总分和排名。"""
    from app.db.models import SessionLocal, TotalScore

    db = SessionLocal()
    row = db.query(TotalScore).filter(TotalScore.total_type == "五门").first()
    db.close()
    if row is None:
        pytest.skip("no grade1 five-total scores in local tracker database")

    response = client.get(f"/api/students/{row.student_id}")
    assert response.status_code == 200
    five_trend = response.json()["five_trend"]
    assert any(item["exam_id"] == row.exam_id for item in five_trend)

def test_subject_weakness(client):
    """Step 6: 单科薄弱"""
    response = client.get("/api/subject-weakness/1")
    assert response.status_code == 200
    assert "subject_weakness" in response.json()


def test_rank_metrics_options(client):
    response = client.get("/api/rank-metrics?grade=1&mode=frequency")
    assert response.status_code == 200
    data = response.json()
    assert "metrics" in data
    assert any(item["value"] == "subject:语文" for item in data["metrics"])
    assert any(item["value"] == "total:主三门" for item in data["metrics"])


def test_rank_range_endpoint(client):
    from app.db.models import SessionLocal, TotalScore

    db = SessionLocal()
    row = db.query(TotalScore).filter(TotalScore.total_type == "主三门").first()
    db.close()
    if row is None:
        pytest.skip("no total scores in local tracker database")

    response = client.get(
        f"/api/rank-range?exam_id={row.exam_id}&metric=total:主三门&rank_min=1&rank_max=9999"
    )
    assert response.status_code == 200
    data = response.json()
    assert {"metric", "metric_label", "rows"} <= set(data)


def test_rank_frequency_endpoint(client):
    from app.db.models import SessionLocal, Exam

    db = SessionLocal()
    exam = db.query(Exam).first()
    db.close()
    if exam is None:
        pytest.skip("no exams in local tracker database")

    response = client.get(f"/api/rank-frequency?grade={exam.grade}&metric=total:主三门&recent_count=2")
    assert response.status_code == 200
    data = response.json()
    assert {"bins", "rows", "exams"} <= set(data)


def test_grade_score_frequency_bins_are_exact_scores():
    """高二/高三+3等级分频次按精确等级分统计，不按分数段归并。"""
    # P0-A1：等级分箱是共享定义（新旧两条排名频次路径同一份）
    from app.analysis.definitions import GRADE_SCORE_BINS, grade_score_bin as _grade_score_bin

    assert [label for _key, label, _score, _separator in GRADE_SCORE_BINS] == [
        "70分",
        "67分",
        "64分",
        "61分",
        "58分",
        "55分",
        "52分",
        "49分",
        "46分",
        "43分",
        "40分",
    ]
    assert [score for _key, _label, score, separator in GRADE_SCORE_BINS if separator] == [67, 58, 49, 43]
    assert _grade_score_bin(67) == "g67"
    assert _grade_score_bin(66) is None


def test_ingest_uploads_get(client):
    """Step 6: 上传列表"""
    response = client.get("/api/uploads")
    assert response.status_code == 200
    assert "uploads" in response.json()
