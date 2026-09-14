from app.ingest.filename_parser import parse_filename


def test_parse_grade2_second_semester_midterm_sample():
    parsed = parse_filename("高二2025学年第二学期期中考试学生成绩明细表.xlsx")

    assert parsed["grade"] == 2
    assert parsed["semester"] == "下"
    assert parsed["exam_type"] == "期中"
    assert parsed["sort_key"] == "2026-04"
    assert parsed["canonical_name"] == "高二第二学期期中考试"


def test_parse_cohort_school_year_grade1_sample():
    parsed = parse_filename("2024级2024学年第二学期期中考试班级均分表.xlsx")

    assert parsed["grade"] == 1
    assert parsed["semester"] == "下"
    assert parsed["exam_type"] == "期中"
    assert parsed["sort_key"] == "2025-04"
    assert parsed["canonical_name"] == "高一第二学期期中考试"


def test_parse_cohort_school_year_grade2_sample():
    parsed = parse_filename("2024级2025学年第一学期期末考试.xlsx")

    assert parsed["grade"] == 2
    assert parsed["semester"] == "上"
    assert parsed["exam_type"] == "期末"
    assert parsed["sort_key"] == "2026-01"
    assert parsed["canonical_name"] == "高二第一学期期末考试"


def test_parse_monthly_exam_keeps_month_in_canonical_name():
    parsed = parse_filename("2025级2025学年第一学期10月月考成绩明细表.xlsx")

    assert parsed["grade"] == 1
    assert parsed["semester"] == "上"
    assert parsed["exam_type"] == "月考"
    assert parsed["sort_key"] == "2025-10"
    assert parsed["canonical_name"] == "高一第一学期10月月考"
