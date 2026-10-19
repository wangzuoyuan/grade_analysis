from pathlib import Path

from openpyxl import Workbook

from app.ingest.excel_parser import parse_excel_grade23
from app.ingest.excel_parser import parse_excel_grade1_student_scores
from app.ingest.excel_parser import is_grade23_rank_band_sheet, is_grade23_student_sheet


def make_grade23_student_workbook(path: Path):
    workbook = Workbook()
    ws = workbook.active
    ws.title = "学生成绩明细"
    ws.cell(2, 1, "学号")
    ws.cell(2, 2, "班级")
    ws.cell(2, 3, "学籍")
    ws.cell(2, 4, "姓名")
    ws.cell(2, 20, "+3总分")
    ws.cell(2, 21, "主三门")
    ws.cell(2, 24, "3+3总分")

    # 高二/高三学生成绩表是固定列位解析：5-7 为语数英原始分，
    # 8-19 为选考科目原始/等级分，20-26 为总分/百分位/排名，27-29 为语数英百分位。
    ws.append([])
    ws.append([
        "7240101", "01", "1", "卞幻",
        97, 108, 120,
        48, 52,
        50, 55,
        None, None,
        None, None,
        None, None,
        None, None,
        174,
        325.5, "25.08%", 283,
        499.5, "30.01%", 291,
        "75.08%", "40.12%", "35.45%",
    ])
    ws.append([
        "7240102", "01", "1", "学生乙",
        90, 100, 110,
        None, None,
        45, 50,
        None, None,
        None, None,
        None, None,
        None, None,
        95,
        300, "40.00%", 350,
        395, "45.00%", 420,
        "60.00%", "55.00%", "50.00%",
    ])
    workbook.save(path)


def test_parse_grade23_sample_totals(tmp_path):
    sample = tmp_path / "高二2025学年第二学期期中考试学生成绩明细表.xlsx"
    make_grade23_student_workbook(sample)
    result = parse_excel_grade23(str(sample), grade=2)

    assert result["kind"] == "student_scores"
    assert len(result["students"]) == 2

    totals = {
        row["total_type"]: row
        for row in result["total_scores"]
        if row["student_id"] == "7240101"
    }
    assert totals["主三门"]["total_score"] == 325.5
    assert totals["+3"]["total_score"] == 174
    assert totals["3+3"]["total_score"] == 499.5
    assert totals["3+3"]["xueji_rank"] == 291


def test_parse_grade23_sample_subjects(tmp_path):
    sample = tmp_path / "高二2025学年第二学期期中考试学生成绩明细表.xlsx"
    make_grade23_student_workbook(sample)
    result = parse_excel_grade23(str(sample), grade=2)

    subjects = {
        row["subject"]: row
        for row in result["subject_scores"]
        if row["student_id"] == "7240101"
    }
    assert subjects["语文"]["raw_score"] == 97
    assert subjects["语文"]["grade_percentile"] == 0.7508
    assert subjects["物理"]["raw_score"] == 48
    assert subjects["物理"]["grade_score"] == 52
    assert "生物" not in subjects


def test_parse_grade1_percentiles(tmp_path):
    workbook = Workbook()
    ws = workbook.active
    ws.title = "学生成绩明细"
    ws.append(["学生成绩（在籍）"])
    ws.append(["学号", "班级", "学籍", "姓名", "语文", None, "数学", None, "主三门", None, None, None])
    ws.append([None, None, None, None, "分数", "年级百分位", "分数", "年级百分位", "总分", "年级百分位", "学籍排名", "年级排名"])
    ws.merge_cells("A2:A3")
    ws.merge_cells("B2:B3")
    ws.merge_cells("C2:C3")
    ws.merge_cells("D2:D3")
    ws.merge_cells("E2:F2")
    ws.merge_cells("G2:H2")
    ws.merge_cells("I2:L2")
    ws.append(["7240101", "01", "1", "卞幻", 92, "45.01%", 106, "66.84%", 306, "47.88%", 283, 283])
    path = tmp_path / "2024级2024学年第二学期期中考试.xlsx"
    workbook.save(path)

    result = parse_excel_grade1_student_scores(path)

    subjects = {
        row["subject"]: row
        for row in result["subject_scores"]
        if row["student_id"] == "7240101"
    }
    totals = {
        row["total_type"]: row
        for row in result["total_scores"]
        if row["student_id"] == "7240101"
    }
    assert subjects["语文"]["grade_percentile"] == 0.4501
    assert subjects["数学"]["grade_percentile"] == 0.6684
    assert totals["主三门"]["grade_percentile"] == 0.4788
    assert totals["主三门"]["xueji_rank"] == 283
    assert totals["主三门"]["grade_rank"] == 283


def test_parse_grade23_class_average_workbook(tmp_path):
    workbook = Workbook()
    ws = workbook.active
    ws.title = "班级均分"
    ws.append(
        [
            "班型",
            "班级",
            "班主任",
            "语文",
            "数学",
            "英语",
            "物理",
            None,
            "化学",
            None,
            "加3同均分",
            "主三门",
            "3+3总分",
        ]
    )
    ws.append([None, None, None, None, None, None, "原始", "等级", "原始", "等级", None, None, None])
    ws.append(["平行班", "01", "张老师", 101.2, 108.3, 110.4, 55.1, 61.2, 58.3, 62.4, 180.5, 319.9, 500.4])
    ws.append([None, "02", "李老师", 99.1, 103.2, 109.3, 53.1, 59.2, 57.3, 60.4, 176.5, 311.6, 488.1])
    path = tmp_path / "高二2025学年第二学期期中考试班级均分表.xlsx"
    workbook.save(path)

    result = parse_excel_grade23(str(path), grade=2)

    assert result["kind"] == "class_averages"
    assert len(result["class_averages"]) == 2
    first = result["class_averages"][0]
    assert first["class_type"] == "平行班"
    assert first["class_num"] == 1
    assert first["teacher_name"] == "张老师"
    assert first["subject_averages"]["语文"] == 101.2
    assert first["subject_averages"]["物理_等级"] == 61.2
    # 总分口径升级为「口径→指标包」dict：旧格式无子列时均分落 {均分: v}
    assert first["total_averages"]["+3"] == {"均分": 180.5}
    assert first["total_averages"]["主三门"] == {"均分": 319.9}
    assert first["total_averages"]["3+3"] == {"均分": 500.4}


def make_grade23_new_format_average_workbook(path: Path):
    # 新格式学校均分表：总分列挂 均分/及格率/高分率/低分率/标准差 子列 + 校名次顶级列
    workbook = Workbook()
    ws = workbook.active
    ws.title = "班级均分"
    ws.append(
        [
            "班级类型", "班级", "班主任", "语文", "数学", "英语", "物理", None,
            "3+3总分", None, None, None, None,
            "主三门总分", None, None, None, None,
            "3+3校名次", "主三门校名次",
        ]
    )
    ws.append(
        [None, None, None, None, None, None, "原始", "等级"]
        + ["均分", "及格率", "高分率", "低分率", "标准差"]
        + ["均分", "及格率", "高分率", "低分率", "标准差"]
        + [None, None]
    )
    ws.append(
        ["平行班", "01", "张老师", 101.2, 108.3, 110.4, 55.1, 61.2,
         499.5, 0.91, 0.12, 0.03, 45.67, 325.5, 95, 0.2, 0, 38.5, 3, 5]
    )
    ws.append(
        [None, "02", "李老师", 99.1, 103.2, 109.3, 53.1, 59.2,
         488.1, 0.85, 0.1, 0.02, 44.2, 311.6, 0.93, 0.15, 0.05, 37.9, 8, 6]
    )
    ws.merge_cells("A3:A4")  # 班级类型纵向合并：第二行靠继承
    workbook.save(path)


def test_parse_grade23_new_format_class_average_workbook(tmp_path):
    sample = tmp_path / "高二合成名次段期初考班级均分表.xlsx"
    make_grade23_new_format_average_workbook(sample)
    result = parse_excel_grade23(str(sample), grade=2)

    assert result["kind"] == "class_averages"
    assert len(result["class_averages"]) == 2
    first, second = result["class_averages"]
    # 选考科目双列：物理_原始/物理_等级 分列入包
    assert first["subject_averages"]["物理_原始"] == 55.1
    assert first["subject_averages"]["物理_等级"] == 61.2
    # 3+3 完整指标包：及格率 0.91（比例）自动 ×100 存百分数；校名次顶级列入包
    assert first["total_averages"]["3+3"] == {
        "均分": 499.5, "及格率": 91.0, "高分率": 12.0, "低分率": 3.0,
        "标准差": 45.67, "校名次": 3,
    }
    # 主三门包：及格率 95 已是百分数原样保留；校名次同口径入包
    assert first["total_averages"]["主三门"]["均分"] == 325.5
    assert first["total_averages"]["主三门"]["及格率"] == 95.0
    assert first["total_averages"]["主三门"]["校名次"] == 5
    # 班级类型合并单元格：第二行空值继承「平行班」
    assert second["class_type"] == "平行班"
    assert second["total_averages"]["3+3"]["及格率"] == 85.0
    assert second["total_averages"]["主三门"]["校名次"] == 6


def make_grade23_rank_band_workbook(path: Path, title: str):
    # 名次段表：第 1 行合并标题，第 2 行 班级/班主任/段列，第 3 行起每班一行
    workbook = Workbook()
    ws = workbook.active
    ws.title = "名次段"
    ws.append([title])
    ws.append(["班级", "班主任", "1-40", "41-80", "81-120", "121-160"])
    ws.append(["01", "张老师", 5, 8, 12, 3])
    ws.append(["02", "李老师", 4, 9, 10, 6])
    ws.append(["合计", "—", 9, 17, 22, 9])  # 非数字班级行：应被忽略
    workbook.save(path)


def test_parse_grade23_rank_band_workbook(tmp_path):
    sample = tmp_path / "高二合成名次段期初考名次段.xlsx"
    make_grade23_rank_band_workbook(sample, "六门总分名次段表一（在籍）")
    result = parse_excel_grade23(str(sample), grade=2)

    # 标题含「六门」→ 3+3 口径；合计行非数字班号被忽略，只留两个班
    assert result["kind"] == "rank_bands"
    band = result["rank_band"]
    assert band["total_type"] == "3+3"
    assert band["title"] == "六门总分名次段表一（在籍）"
    assert len(band["classes"]) == 2
    first = band["classes"][0]
    assert first["class_num"] == 1
    assert first["teacher_name"] == "张老师"
    assert first["bands"] == [
        {"label": "1-40", "lo": 1, "hi": 40, "count": 5},
        {"label": "41-80", "lo": 41, "hi": 80, "count": 8},
        {"label": "81-120", "lo": 81, "hi": 120, "count": 12},
        {"label": "121-160", "lo": 121, "hi": 160, "count": 3},
    ]

    # 同构文件标题含「三门」→ 主三门口径
    sample_main3 = tmp_path / "高二合成名次段期初考名次段2.xlsx"
    make_grade23_rank_band_workbook(sample_main3, "三门总分名次段表一（在籍）")
    result_main3 = parse_excel_grade23(str(sample_main3), grade=3)
    assert result_main3["kind"] == "rank_bands"
    assert result_main3["rank_band"]["total_type"] == "主三门"


def test_grade23_rank_band_detection_is_exclusive(tmp_path):
    band = tmp_path / "高二合成名次段期初考名次段.xlsx"
    make_grade23_rank_band_workbook(band, "六门总分名次段表一（在籍）")
    workbook = Workbook()
    ws = workbook.active
    ws.append(["班型", "班级", "班主任", "语文", "数学", "英语", "物理", None, "加3同均分", "主三门", "3+3总分"])
    ws.append([None, None, None, None, None, None, "原始", "等级", None, None, None])
    ws.append(["平行班", "01", "张老师", 101.2, 108.3, 110.4, 55.1, 61.2, 180.5, 319.9, 500.4])
    averages = tmp_path / "高二2025学年第二学期期中考试班级均分表.xlsx"
    workbook.save(averages)

    # 判型互斥：名次段表不误判为明细表，均分表不误判为名次段表，分流各归其位
    assert is_grade23_student_sheet(band) is False
    assert is_grade23_rank_band_sheet(band) is True
    assert is_grade23_rank_band_sheet(averages) is False
    assert parse_excel_grade23(str(band), grade=2)["kind"] == "rank_bands"
    assert parse_excel_grade23(str(averages), grade=2)["kind"] == "class_averages"
