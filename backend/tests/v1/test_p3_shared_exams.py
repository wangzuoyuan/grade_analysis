"""P3 shared/exams + S05 + 新建学生链路（契约 p3-imports-analysis.md §1.4）。

- 两场考试导入后 /shared/exams 列表正确（日期降序、row_count、subjects
  并集；total_type 行不计入 subjects）。
- S05：T8（无 link）的 teaching 域考试不进 homeroom /scores 与
  /shared/exams（域隔离天然成立，断言即可）。
- 新建学生链路：文件里新学号 → confirm 后 identity+alias+教学班成员行
  建立；第二次 preview 同文件 known_students 计入。

造表 helper 与 test_p3_imports_e01_e02_e05.py 同源复制（conftest 不在
本任务允许清单）。
"""

from pathlib import Path

import pytest

API = "/api/v1"
SUBJECT = "物理"

EXAM_S1 = "P3S1"
EXAM_S2 = "P3S2"

_HEADERS = {
    1: "学号", 2: "班级", 3: "学籍", 4: "姓名",
    5: "语文", 6: "数学", 7: "英语",
    8: "物理", 9: "物理等级分",
    20: "+3总分", 21: "主三门总分", 22: "主三门百分位", 23: "主三门学籍排名",
    24: "3+3总分", 25: "3+3百分位", 26: "3+3学籍排名",
    27: "语文百分位", 28: "数学百分位", 29: "英语百分位",
}


def _student_row(
    sid, name, *, class_num=6, chinese=None, math=None, english=None,
    physics=None, physics_grade=None, main3=None, teaching_label=None,
):
    data = {1: sid, 2: class_num, 3: 1, 4: name}
    if chinese is not None:
        data[5] = chinese
    if math is not None:
        data[6] = math
    if english is not None:
        data[7] = english
    if physics is not None:
        data[8] = physics
    if physics_grade is not None:
        data[9] = physics_grade
    if main3 is not None:
        data[21] = main3
    if teaching_label is not None:
        data[30] = teaching_label
    return data


def _write_grade23_xlsx(path: Path, rows, teaching_col: bool = False) -> Path:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    headers = dict(_HEADERS)
    if teaching_col:
        headers[30] = "教学班"
    for col, text in headers.items():
        ws.cell(row=2, column=col, value=text)
    for offset, data in enumerate(rows, start=4):
        for col, val in data.items():
            if val is not None:
                ws.cell(row=offset, column=col, value=val)
    wb.save(str(path))
    return path


def _upload(client, path: Path, mode: str, extra: dict | None = None):
    data = {"mode": mode}
    if extra:
        data.update({k: str(v) for k, v in extra.items()})
    with open(path, "rb") as fh:
        return client.post(
            f"{API}/imports/preview",
            data=data,
            files=[
                (
                    "files",
                    (
                        path.name,
                        fh.read(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                )
            ],
        )


def _preview_confirm(client, path: Path, mode: str, extra: dict | None = None):
    prev = _upload(client, path, mode, extra)
    assert prev.status_code == 200, prev.text
    ok = client.post(f"{API}/imports/confirm", json={"token": prev.json()["token"]})
    assert ok.status_code == 200, ok.text
    return prev.json(), ok.json()


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


@pytest.fixture()
def homeroom_files(tmp_path):
    """两场 homeroom 考试（日期不同）：甲乙丙全科+主三门，各 5 行。"""
    def build(name, chinese, physics):
        return _write_grade23_xlsx(
            tmp_path / name,
            [
                _student_row("2025H6-01", "秦甲", chinese=chinese, math=92, english=95,
                             physics=physics, physics_grade=physics + 1, main3=chinese + 92 + 95),
                _student_row("2025H6-02", "秦乙", chinese=chinese - 12, math=81, english=79,
                             physics=physics - 6, physics_grade=physics - 5, main3=chinese + 148),
                _student_row("2025H6-03", "秦丙", chinese=chinese - 23, math=70, english=72,
                             physics=physics - 22, physics_grade=physics - 21, main3=chinese + 119),
            ],
        )

    return build("P3S1.xlsx", 88, 90), build("P3S2.xlsx", 91, 93)


def test_shared_exams_homeroom_two_exams(client, v1_seed, homeroom_files):
    s1_path, s2_path = homeroom_files
    _preview_confirm(client, s1_path, "homeroom",
                     {"class_id": v1_seed.h6_id, "exam_name": EXAM_S1, "exam_date": "2025-11-20"})
    _preview_confirm(client, s2_path, "homeroom",
                     {"class_id": v1_seed.h6_id, "exam_name": EXAM_S2, "exam_date": "2025-12-15"})

    r = client.get(
        f"{API}/shared/exams",
        params={"mode": "homeroom", "class_id": v1_seed.h6_id},
    )
    assert r.status_code == 200, r.text
    exams = r.json()["exams"]
    # 含 seed 的 2025期中（exam_date=2025-11-06）共 3 场，按日期降序
    assert [e["exam_name"] for e in exams] == [EXAM_S2, EXAM_S1, "2025期中"]
    assert [e["exam_date"] for e in exams] == ["2025-12-15", "2025-11-20", "2025-11-06"]

    s2 = exams[0]
    assert s2["row_count"] == 15  # 3 人 × (4 科 + 主三门)
    assert s2["subjects"] == ["数学", "物理", "英语", "语文"]  # 学科并集，无"总分"
    # seed 的 2025期中在 H6 域是 3 人 × (4 科 + 1 总分) = 15 行；
    # T8 域同名考试的 2 行绝不混入（S05 隔离的聚合侧证明）
    seed_exam = next(e for e in exams if e["exam_name"] == "2025期中")
    assert seed_exam["row_count"] == 15


def test_shared_exams_teaching_subject_and_scope_guard(client, v1_seed):
    # 显式教学班：T8 的 2025期中 = 2 行物理，subjects 恒仅任教学科
    r = client.get(
        f"{API}/shared/exams",
        params={"mode": "teaching", "teaching_class_id": v1_seed.t8_id},
    )
    assert r.status_code == 200, r.text
    exams = r.json()["exams"]
    assert [e["exam_name"] for e in exams] == ["2025期中"]
    assert exams[0]["row_count"] == 2
    assert exams[0]["subjects"] == [SUBJECT]

    # 多教学班未显式 teaching_class_id → 并集（集成者修订：与 /scores 教学模式
    # 的"全部所教班"缺省一致；仅导入 preview/confirm 仍要求多班显式）。
    # F09 统一投影：并集范围经 T6 的 active link 反向投影可见 H6 域的
    # P3S2/P3S1（甲乙各 1 行物理）；2025期中 = T6(甲乙丁 3 行) + T8(戊己 2 行)。
    r2 = client.get(f"{API}/shared/exams", params={"mode": "teaching"})
    assert r2.status_code == 200, r2.text
    exams2 = r2.json()["exams"]
    assert [e["exam_name"] for e in exams2] == ["P3S2", "P3S1", "2025期中"]
    by_name = {e["exam_name"]: e for e in exams2}
    assert by_name["2025期中"]["row_count"] == 5
    assert by_name["P3S1"]["row_count"] == 2
    assert all(e["subjects"] == [SUBJECT] for e in exams2)


def test_s05_t8_rows_never_enter_homeroom_scores(client, v1_seed):
    """S05：seed 的 2025期中里 T8（无 link）成员戊己的行不进 homeroom /scores。

    T6 与 H6 有 active link 且甲乙已配对：其 T 域事实经投影门进入
    homeroom 响应时 person_id 映射为 H 域身份；戊己/丁（无配对或无
    link）绝不出现。"""
    r = client.get(
        f"{API}/scores", params={"mode": "homeroom", "exam_name": "2025期中"}
    )
    assert r.status_code == 200
    rows = r.json()["rows"]
    person_ids = {row["person_id"] for row in rows}
    assert person_ids <= set(v1_seed.h_person_ids)
    assert v1_seed.wu_t_id not in person_ids
    assert v1_seed.ji_t_id not in person_ids


def test_new_student_identity_pipeline(client, v1_seed, tmp_path):
    """新学号链路：confirm 建 identity+alias+教学班成员行；第二次 preview
    同文件时 known_students 计入。"""
    from app.db import workspace_models as wm

    path = _write_grade23_xlsx(
        tmp_path / "新学生.xlsx",
        [
            _student_row("2025T8-01", "秦甲", class_num=8, chinese=70, math=71,
                         english=72, physics=77, teaching_label="高二8班(教)"),
            _student_row("2025T8-77", "秦辛", class_num=8, chinese=58, math=59,
                         english=60, physics=66, teaching_label="高二8班(教)"),
        ],
        teaching_col=True,
    )
    prev_body, result = _preview_confirm(
        client, path, "teaching",
        {"teaching_class_id": v1_seed.t8_id, "exam_name": "P3新学生", "exam_date": "2025-12-01"},
    )
    item = prev_body["items"][0]
    assert item["known_students"] == 1  # 戊
    assert [s["alias"] for s in item["new_students"]] == ["2025T8-77"]
    assert result["imported"] == 2
    assert result["students_created"] == 1
    assert result["members_synced"] == 1

    db = _db()
    try:
        alias = (
            db.query(wm.WsStudentAlias)
            .filter_by(
                alias_value="2025T8-77", data_domain="teaching",
                academic_year_id=v1_seed.ay_id,
            )
            .one()
        )
        ident = db.get(wm.WsStudentIdentity, alias.identity_id)
        assert ident.data_domain == "teaching"
        assert ident.display_name == "秦辛"
        member = (
            db.query(wm.TeachingClassMember)
            .filter_by(teaching_class_id=v1_seed.t8_id, identity_id=ident.id)
            .one()
        )
        assert member.source == "import"
    finally:
        db.close()

    # 同文件再 preview：新学号已入册，known_students 计入
    again = _upload(
        client, path, "teaching",
        {"teaching_class_id": v1_seed.t8_id, "exam_name": "P3新学生", "exam_date": "2025-12-01"},
    )
    assert again.status_code == 200
    item2 = again.json()["items"][0]
    assert item2["known_students"] == 2
    assert item2["new_students"] == []
