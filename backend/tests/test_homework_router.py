"""作业模块路由冒烟测试（使用 conftest 提供的隔离数据库）。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.models import (
    ClassRoster,
    Exam,
    HomeworkRecord,
    HomeworkSetting,
    SessionLocal,
    SpecialRecord,
    SubjectScore,
    Teacher,
    TotalScore,
)

TEST_CLASS_NUM = 4


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def bound_homeroom():
    db = SessionLocal()
    try:
        teacher = db.query(Teacher).first() or Teacher(name="测试班主任")
        teacher.target_class_high1 = TEST_CLASS_NUM
        db.add(teacher)
        db.merge(HomeworkSetting(key="active_grade", value="1"))
        db.merge(ClassRoster(student_id="TEST-001", name="测试学生", class_num=TEST_CLASS_NUM, grade=1, excluded=0))
        db.commit()
    finally:
        db.close()


def test_kpi_shape(client):
    r = client.get("/api/homework/kpi")
    assert r.status_code == 200
    body = r.json()
    assert "total_misses" in body
    assert "worst_subject" in body
    assert "top_students" in body


def test_trend_subjects_rankings(client):
    for path in ("/api/homework/trend", "/api/homework/subjects", "/api/homework/rankings"):
        r = client.get(path)
        assert r.status_code == 200, path


def test_warnings_shape(client):
    r = client.get("/api/homework/warnings")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) >= {"serious", "warning", "counts"}


def test_correlation_shape(client):
    r = client.get(f"/api/homework/correlation?class_num={TEST_CLASS_NUM}")
    assert r.status_code == 200
    body = r.json()
    assert "rows" in body and isinstance(body["rows"], list)
    assert body["y_field"] == "xueji_rank"


def test_correlation_subject_mode(client):
    r = client.get(f"/api/homework/correlation?class_num={TEST_CLASS_NUM}&subject=数学")
    assert r.status_code == 200
    body = r.json()
    assert body["subject"] == "数学"
    assert body["y_field"] == "grade_percentile"


def test_correlation_subjects_ranking(client):
    r = client.get(f"/api/homework/correlation/subjects?class_num={TEST_CLASS_NUM}")
    assert r.status_code == 200
    body = r.json()
    assert "rankings" in body
    subjects = {x["subject"] for x in body["rankings"]}
    assert "数学" in subjects
    for row in body["rankings"]:
        assert "r" in row and "n" in row


def test_warnings_have_student_id(client):
    r = client.get("/api/homework/warnings")
    assert r.status_code == 200
    body = r.json()
    for w in body["serious"] + body["warning"]:
        assert "student_id" in w


def test_toggle_excluded_roundtrip(client):
    """对某真实学生切两次 excluded，保证最终状态还原，不污染统计。"""
    roster = client.get("/api/homework/roster").json()
    assert roster, "花名册为空，先跑迁移"
    sid = roster[0]["student_id"]
    before = roster[0]["excluded"]
    r1 = client.put(f"/api/homework/roster/{sid}/toggle-excluded")
    assert r1.status_code == 200
    assert r1.json()["excluded"] != before
    r2 = client.put(f"/api/homework/roster/{sid}/toggle-excluded")
    assert r2.json()["excluded"] == before


def test_pearson_known_values():
    from app.homework.service import _pearson
    # 完全正相关
    assert _pearson([1, 2, 3, 4], [2, 4, 6, 8]) == 1.0
    # 完全负相关
    assert _pearson([1, 2, 3, 4], [8, 6, 4, 2]) == -1.0
    # 样本不足
    assert _pearson([1, 2], [2, 4]) is None
    # 零方差
    assert _pearson([1, 1, 1], [1, 2, 3]) is None


def test_semester_roundtrip(client):
    r = client.get("/api/homework/semester")
    assert r.status_code == 200
    assert "semester_start" in r.json()


def test_add_record_unknown_student_reports_error(client):
    """录入一个不存在的学生，应返回 success 但 errors 非空、added_count=0，
    不向真实统计写入脏数据。"""
    r = client.post(
        "/api/homework/records",
        json={"raw_text": "查无此人测试XYZ：数学", "date": "2026-03-02", "mode": "by_student"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["added_count"] == 0
    assert body["errors"]


def test_all_class_scoped_reads_reject_other_class(client):
    paths = (
        "/api/homework/kpi",
        "/api/homework/trend",
        "/api/homework/subjects",
        "/api/homework/rankings",
        "/api/homework/warnings",
        "/api/homework/manage/records",
        "/api/homework/roster",
        "/api/homework/semester",
        "/api/homework/correlation",
        "/api/homework/correlation/subjects",
        "/api/weekly-focus",
    )
    for path in paths:
        response = client.get(f"{path}?class_num=5")
        assert response.status_code == 409, path


def test_full_submission_breaks_streak(client, db_session):
    """回归「缺-交-缺被误判连续」：全交台账日进时间轴后应打断 streak。
    放在文件末尾：本用例会设置固定学期区间。"""
    from app.db.models import HomeworkCollection

    r = client.put("/api/homework/semester", json={
        "semester_start": "2026-03-01", "semester_end": "2026-03-31"})
    assert r.status_code == 200

    db = db_session
    db.query(HomeworkRecord).filter(HomeworkRecord.student_id == "TEST-001").delete()
    db.query(HomeworkCollection).delete()
    for d in ("2026-03-02", "2026-03-04"):
        db.add(HomeworkRecord(student_id="TEST-001", date=d, subject="数学"))
    db.commit()

    def _find(body):
        return [w for w in body["serious"] + body["warning"] if w["student_id"] == "TEST-001"]

    # 无台账（旧行为）：时间轴只见两次缺交 → 误判连续 2 次
    body = client.get("/api/homework/warnings").json()
    assert any(w["streak"] == 2 for w in _find(body))

    # 台账：03-03 全交 → streak 打断为 1，不进预警
    r = client.post("/api/homework/records",
                    json={"raw_text": "数学：全交", "date": "2026-03-03", "mode": "by_subject"})
    assert r.status_code == 200
    assert r.json()["added_count"] == 1
    body = client.get("/api/homework/warnings").json()
    assert not _find(body)

    # 幂等：同一收交日换说法重复录入不重复计数
    r1 = client.post("/api/homework/records",
                     json={"raw_text": "数学：齐了", "date": "2026-03-03", "mode": "by_subject"})
    assert r1.json()["added_count"] == 0

    # 修复后台账反向验证：删台账回到误判，重录恢复打断
    db.query(HomeworkCollection).delete()
    db.commit()
    body = client.get("/api/homework/warnings").json()
    assert any(w["streak"] == 2 for w in _find(body))


def test_same_name_input_and_writes_are_isolated_to_bound_class(client):
    db = SessionLocal()
    try:
        for model in (HomeworkRecord, SpecialRecord):
            db.query(model).filter(model.student_id.in_(["SCOPE-C4", "SCOPE-C5"])).delete()
        db.query(ClassRoster).filter(ClassRoster.student_id.in_(["SCOPE-C4", "SCOPE-C5"])).delete()
        db.add_all([
            ClassRoster(student_id="SCOPE-C4", name="同名学生", class_num=TEST_CLASS_NUM, grade=1, excluded=0),
            ClassRoster(student_id="SCOPE-C5", name="同名学生", class_num=5, grade=1, excluded=0),
        ])
        db.add(HomeworkRecord(student_id="SCOPE-C5", date="2026-03-08", subject="语文"))
        db.commit()
    finally:
        db.close()

    response = client.post(
        f"/api/homework/records?class_num={TEST_CLASS_NUM}",
        json={"raw_text": "同名学生：数学", "date": "2026-03-09", "mode": "by_student"},
    )
    assert response.status_code == 200
    assert response.json()["added_count"] == 1

    db = SessionLocal()
    try:
        own = db.query(HomeworkRecord).filter(
            HomeworkRecord.student_id == "SCOPE-C4",
            HomeworkRecord.date == "2026-03-09",
        ).one()
        other = db.query(HomeworkRecord).filter(
            HomeworkRecord.student_id == "SCOPE-C5",
            HomeworkRecord.date == "2026-03-08",
        ).one()
        other_id = other.id
        assert own.subject == "数学"
    finally:
        db.close()

    listed = client.get(
        f"/api/homework/manage/records?class_num={TEST_CLASS_NUM}&student=同名学生"
    ).json()
    assert {row["date"] for row in listed} == {"2026-03-09"}
    assert client.delete(
        f"/api/homework/manage/records/{other_id}?class_num={TEST_CLASS_NUM}"
    ).status_code == 404


def test_placeholder_names_are_unique_per_grade_and_class(client):
    db = SessionLocal()
    try:
        db.query(ClassRoster).filter(ClassRoster.student_id.in_([
            "HW-1-4-跨班占位", "HW-1-5-跨班占位"
        ])).delete()
        db.add(ClassRoster(
            student_id="HW-1-5-跨班占位",
            name="跨班占位",
            class_num=5,
            grade=1,
            excluded=0,
        ))
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/api/homework/roster",
        json={"name": "跨班占位", "class_num": TEST_CLASS_NUM, "grade": 1},
    )
    assert response.status_code == 200
    assert response.json()["student_id"] == f"HW-1-{TEST_CLASS_NUM}-跨班占位"


def test_correlation_uses_active_grade_exam_and_current_class_samples(client):
    db = SessionLocal()
    try:
        for model in (SubjectScore, TotalScore):
            db.query(model).filter(model.exam_id.in_([9101, 9201])).delete()
        db.query(Exam).filter(Exam.id.in_([9101, 9201])).delete()
        for sid in ("CORR-C4-1", "CORR-C4-2", "CORR-C5"):
            db.query(HomeworkRecord).filter(HomeworkRecord.student_id == sid).delete()
            db.query(ClassRoster).filter(ClassRoster.student_id == sid).delete()
        db.add_all([
            Exam(id=9101, name="高一当前考试", grade=1, semester="下", exam_date="2026-04-01", exam_type="月考"),
            Exam(id=9201, name="高二更新考试", grade=2, semester="下", exam_date="2026-05-01", exam_type="月考"),
            ClassRoster(student_id="CORR-C4-1", name="相关甲", class_num=TEST_CLASS_NUM, grade=1, excluded=0),
            ClassRoster(student_id="CORR-C4-2", name="相关乙", class_num=TEST_CLASS_NUM, grade=1, excluded=0),
            ClassRoster(student_id="CORR-C5", name="他班样本", class_num=5, grade=1, excluded=0),
        ])
        class_four_ids = ["TEST-001", "CORR-C4-1", "CORR-C4-2"]
        for index, sid in enumerate(class_four_ids, start=1):
            db.add(SubjectScore(
                exam_id=9101, student_id=sid, class_num=TEST_CLASS_NUM,
                name=f"样本{index}", subject="数学", raw_score=100 - index,
                grade_percentile=0.1 * index,
            ))
            db.add(TotalScore(
                exam_id=9101, student_id=sid, total_type="主三门",
                total_score=300 - index, xueji_rank=index,
            ))
        db.add(SubjectScore(
            exam_id=9101, student_id="CORR-C5", class_num=5, name="他班样本",
            subject="数学", raw_score=60, grade_percentile=0.9,
        ))
        db.add(TotalScore(
            exam_id=9201, student_id="CORR-G2", total_type="主三门",
            total_score=200, xueji_rank=99,
        ))
        db.commit()
    finally:
        db.close()

    result = client.get(
        f"/api/homework/correlation?class_num={TEST_CLASS_NUM}&subject=数学"
    ).json()
    assert result["exam_id"] == 9101
    result_ids = {row["student_id"] for row in result["rows"]}
    assert {"TEST-001", "CORR-C4-1", "CORR-C4-2"} <= result_ids
    assert "CORR-C5" not in result_ids

    ranking = client.get(
        f"/api/homework/correlation/subjects?class_num={TEST_CLASS_NUM}&exam_id=9101"
    ).json()
    math = next(row for row in ranking["rankings"] if row["subject"] == "数学")
    assert math["n"] == 3
    assert client.get(
        f"/api/homework/correlation?class_num={TEST_CLASS_NUM}&exam_id=9201"
    ).status_code == 409
