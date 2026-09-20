"""P3 导入适配层：把 H（班主任版全科）/ T（教学版单科）解析器输出
规范化为两域统一的导入 rows。

设计（契约 p3-imports-analysis.md §1.1）：
- homeroom 模式按行政班年级选 H 解析器（高一两行表头 / 高二三 3+3
  固定列），产出全科 + 总分行；非本班（班级列与行政班号不符）的学生
  整人忽略并计 warnings，绝不把外班学生写进本班。
- teaching 模式用 T 解析器（teaching_excel，移植自教学版），**只保留
  任教学科列**，其他学科行丢弃并计 warnings（延续教学版"主动过滤
  其他学科"行为）；探测到教学班标签列时按目标教学班标签过滤成员。
- preview/confirm 共用本层结果：解析一次、rows 落 import_batch.scope_json，
  confirm 不再重读文件（快照即事实源，E05 原子性前提）。

规范化行结构（JSON 可序列化，直接进 scope_json）：
``{"alias": 学号, "name": 姓名, "subject": 学科|None, "total_type": 总分口径|None,
    "score": 原始分|None, "grade_score": 等级分|None}``——score None 表示
缺考（有行无分），绝不转 0；解析器层面"原始分与等级分/百分位全空"的
学科格不产生行（H/T 解析器既有语义，保持不动）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, List, Optional

from app.ingest import excel_parser as h_parser
from app.ingest import teaching_excel as t_parser
from app.ingest.filename_parser import parse_filename


@dataclass
class ParsedImportFile:
    """一个上传文件的规范化解析结果（preview 响应 items 与快照共用）。"""

    filename: str
    kind: str = "unknown"  # student_scores | class_averages | unknown
    parsed_ok: bool = False
    message: Optional[str] = None
    exam_name: Optional[str] = None
    exam_date: Optional[date] = None
    subject: Optional[str] = None  # teaching 模式恒为任教学科
    grade: Optional[int] = None
    class_label: Optional[str] = None  # teaching 模式：文件中出现的班级标签汇总
    # 学生成绩规范化行；班级均分使用下方独立集合，不落 ScoreFact。
    rows: List[dict] = field(default_factory=list)
    # 全年级班级均分行；与学生成绩分开存储，确认时落专用表。
    class_averages: List[dict] = field(default_factory=list)
    # 去重学生清单（按学号聚合；rows 的身份事实源）
    students: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _exam_from_filename(filename: str) -> tuple[Optional[str], Optional[date]]:
    """文件名推导考试名与日期：canonical_name + sort_key(YYYY-MM) 取该月 1 日。

    与 H 旧上传链路同源（filename_parser）；日期只有年月精度，取当月 1 日
    保持确定性，前端/表单可用 exam_date 覆盖。"""
    parsed = parse_filename(filename)
    exam_name = parsed.get("canonical_name") or filename
    exam_date: Optional[date] = None
    sort_key = parsed.get("sort_key") or ""
    if "-" in sort_key:
        year_text, month_text = sort_key.split("-", 1)
        try:
            exam_date = date(int(year_text), max(1, min(12, int(month_text))), 1)
        except ValueError:
            exam_date = None
    return exam_name, exam_date


def _aggregate_students(raw_students: List[dict]) -> tuple[List[dict], List[str]]:
    """按学号聚合学生：同名同号合并（计重复 warning），同学号不同名
    （H 撞号防呆——preview 警告、confirm 整文件拒绝）单独计 warning。"""
    by_alias: dict = {}
    name_conflicts: List[str] = []
    duplicates: List[str] = []
    for stu in raw_students:
        alias = (stu.get("student_id") or "").strip()
        if not alias:
            continue
        name = (stu.get("name") or "").strip()
        existing = by_alias.get(alias)
        if existing is None:
            by_alias[alias] = {
                "alias": alias,
                "name": name,
                "class_num": stu.get("class_num"),
                "class_label": stu.get("class_label"),
            }
        elif existing["name"] != name:
            # 同学号不同姓名：防两人成绩混档，确认时整文件 409
            name_conflicts.append(
                f"同学号不同姓名：{alias}（{existing['name'] or '空'} vs {name or '空'}），确认时整文件拒绝"
            )
        else:
            duplicates.append(alias)
    warnings = []
    if name_conflicts:
        warnings.extend(name_conflicts)
    if duplicates:
        counts = {alias: duplicates.count(alias) for alias in set(duplicates)}
        detail = "、".join(f"{alias}×{counts[alias] + 1}" for alias in sorted(counts))
        warnings.append(f"同文件重复学号（同名已合并）：{detail}")
    students = [by_alias[alias] for alias in sorted(by_alias)]
    return students, warnings


def _missing_count_warning(rows: List[dict]) -> List[str]:
    """缺考（score=NULL）计数：显示"—"不转 0 的导入侧提示。"""
    missing = sum(1 for row in rows if row.get("score") is None)
    return [f"缺考（空分）行 {missing} 行，按 NULL 入库不转 0"] if missing else []


def _resolved_exam(filename: str, exam_name: Optional[str], exam_date: Optional[date | str]):
    """表单覆盖优先；exam_date 接受 ISO 字符串或 date。"""
    fallback_name, fallback_date = _exam_from_filename(filename)
    if exam_name is not None and str(exam_name).strip():
        fallback_name = str(exam_name).strip()
    if exam_date is not None and str(exam_date).strip():
        raw = str(exam_date).strip()
        try:
            fallback_date = date.fromisoformat(raw)
        except ValueError:
            raise ValueError(f"exam_date 必须是 ISO 日期（YYYY-MM-DD）：{raw}")
    return fallback_name, fallback_date


def parse_homeroom_file(
    path: Path,
    filename: str,
    grade: int,
    admin_class_num: int,
    exam_name: Optional[str] = None,
    exam_date: Optional[date | str] = None,
) -> ParsedImportFile:
    """homeroom 模式解析：H 解析器全科 + 总分，非本班学生整人忽略。"""
    result = ParsedImportFile(filename=filename, grade=grade)
    result.exam_name, result.exam_date = _resolved_exam(filename, exam_name, exam_date)

    if grade == 1:
        parsed = h_parser.parse_excel_grade1(str(path))
    elif grade in (2, 3):
        parsed = h_parser.parse_excel_grade23(str(path), grade)
    else:
        result.kind = "unknown"
        result.message = f"行政班年级非法：{grade}"
        return result

    result.kind = parsed.get("kind", "unknown")
    if result.kind == "unknown":
        result.message = parsed.get("message") or "未识别为班主任版成绩明细/班级均分表"
        return result
    result.parsed_ok = True

    if result.kind == "class_averages":
        averages = parsed.get("class_averages") or []
        result.class_averages = averages
        result.message = f"班级均分表：识别到 {len(averages)} 个班级，确认后入库"
        return result

    raw_students = parsed.get("students") or []
    students, warnings = _aggregate_students(raw_students)

    # 班级过滤：班级列有值且与行政班号不符 → 整人忽略（不建身份不入班）
    in_class = {
        stu["alias"]
        for stu in students
        if stu.get("class_num") is None or stu["class_num"] == admin_class_num
    }
    dropped = sorted(stu["alias"] for stu in students if stu["alias"] not in in_class)
    if dropped:
        warnings.append(
            f"已忽略非本班（{admin_class_num} 班）学生 {len(dropped)} 人：{'、'.join(dropped)}"
        )
    students = [stu for stu in students if stu["alias"] in in_class]
    allowed = set(in_class)

    rows: List[dict] = []
    for row in parsed.get("subject_scores") or []:
        if row.get("student_id") not in allowed:
            continue
        rows.append(
            {
                "alias": row["student_id"],
                "name": row.get("name"),
                "subject": row.get("subject"),
                "total_type": None,
                "score": row.get("raw_score"),
                "grade_score": row.get("grade_score"),
            }
        )
    for row in parsed.get("total_scores") or []:
        if row.get("student_id") not in allowed:
            continue
        rows.append(
            {
                "alias": row["student_id"],
                "name": row.get("name"),
                "subject": None,
                "total_type": row.get("total_type"),
                "score": row.get("total_score"),
                "grade_score": None,
            }
        )
    warnings.extend(_missing_count_warning(rows))
    result.rows = rows
    result.students = students
    result.warnings = warnings
    return result


def parse_teaching_file(
    path: Path,
    filename: str,
    subject: str,
    teaching_class_label: Optional[str],
    exam_name: Optional[str] = None,
    exam_date: Optional[date | str] = None,
) -> ParsedImportFile:
    """teaching 模式解析：T 解析器 + 只保留任教学科列（其他学科丢弃计
    warnings）；有教学班标签列时按目标标签过滤成员。"""
    result = ParsedImportFile(filename=filename, subject=subject)
    result.exam_name, result.exam_date = _resolved_exam(filename, exam_name, exam_date)

    # 年级优先取文件名；解析不出时按表头形态探测（高一两行表头 / 高二三固定列）
    grade = parse_filename(filename).get("grade")
    if grade not in (1, 2, 3):
        grade = 1 if t_parser.is_grade1_student_sheet(path) else 2
    result.grade = grade
    parsed = (
        t_parser.parse_excel_grade1(str(path))
        if grade == 1
        else t_parser.parse_excel_grade23(str(path), grade)
    )

    result.kind = parsed.get("kind", "unknown")
    if result.kind == "unknown":
        result.message = parsed.get("message") or "未识别为教学版成绩明细/班级均分表"
        return result
    result.parsed_ok = True

    if result.kind == "class_averages":
        averages = parsed.get("class_averages") or []
        result.class_averages = averages
        result.message = f"班级均分表：识别到 {len(averages)} 个班级，确认后入库"
        return result

    raw_students = parsed.get("students") or []
    students, warnings = _aggregate_students(raw_students)

    # 教学班标签过滤：文件带标签列时只保留目标教学班标签的行（标签为空保留，
    # 兼容无标签列的旧表）；外标签学生不进成员同步、不写成绩。
    if teaching_class_label and any(stu.get("class_label") for stu in students):
        allowed = {
            stu["alias"]
            for stu in students
            if not stu.get("class_label") or stu["class_label"] == teaching_class_label
        }
        dropped = sorted(set(stu["alias"] for stu in students) - allowed)
        if dropped:
            warnings.append(
                f"已忽略非本教学班（{teaching_class_label}）标签学生 {len(dropped)} 人：{'、'.join(dropped)}"
            )
        students = [stu for stu in students if stu["alias"] in allowed]
    else:
        allowed = {stu["alias"] for stu in students}
    # class_label 展示过滤后保留的标签（外标签已被上面的过滤排除）
    labels = sorted(
        {stu["class_label"] for stu in students if stu.get("class_label")}
    )
    if labels:
        result.class_label = "、".join(labels)

    rows: List[dict] = []
    dropped_subject_rows: dict = {}
    for row in parsed.get("subject_scores") or []:
        if row.get("student_id") not in allowed:
            continue
        if row.get("subject") != subject:
            # 非任教学科：延续教学版主动过滤，只计 warning 绝不入库
            dropped_subject_rows[row.get("subject")] = (
                dropped_subject_rows.get(row.get("subject"), 0) + 1
            )
            continue
        rows.append(
            {
                "alias": row["student_id"],
                "name": row.get("name"),
                "subject": subject,
                "total_type": None,
                "score": row.get("raw_score"),
                "grade_score": row.get("grade_score"),
            }
        )
    if dropped_subject_rows:
        detail = "、".join(
            f"{name}({count} 行)" for name, count in sorted(dropped_subject_rows.items())
        )
        warnings.append(f"已忽略非任教学科列（仅保留「{subject}」）：{detail}")
    warnings.extend(_missing_count_warning(rows))
    result.rows = rows
    result.students = students
    result.warnings = warnings
    return result
