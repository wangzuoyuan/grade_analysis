#!/usr/bin/env python3
"""Analyze class-6 exam score workbooks for class-teacher use."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

try:
    from student_learning_html import build_learning_profile_outputs
except Exception:  # HTML output is helpful but should not block core analysis.
    build_learning_profile_outputs = None


SUBJECT_COLS = {
    "语文": (5, 6),
    "数学": (7, 8),
    "英语": (9, 10),
    "物理": (11, 12),
    "化学": (13, 14),
    "生物": (15, 16),
    "政治": (17, 18),
    "历史": (19, 20),
    "地理": (21, 22),
}

TOTAL_COLS = {
    "主三门": (23, 24, 25, 26),
    "五门": (27, 28, 29, 30),
    "九门": (31, 32, 33, 34),
}

TREND_TOTAL_TYPES = ("主三门", "五门")

CLASS_AVG_SUBJECT_COLS = {
    "语文": 4,
    "数学": 5,
    "英语": 6,
    "物理": 7,
    "化学": 8,
    "生物": 9,
    "政治": 10,
    "历史": 11,
    "地理": 12,
}

CLASS_AVG_TOTAL_COLS = {
    "主三门": (13, 14),
    "五门": (15, 16),
    "九门": (17, 18),
}

EXAM_ORDER = {
    "高一第一学期12月月考": 1,
    "高一第一学期期末考试": 2,
    "高一第二学期期中考试": 3,
}

XUEJI_LABELS = {
    "1": "闵中学籍",
    "3": "文绮学籍",
    "4": "外省市体育生和复学学生",
}

PROGRESS_RANK_THRESHOLD = 80
VOLATILITY_RANK_THRESHOLD = 120
SUBJECT_PCT_THRESHOLD = 0.10
HIGH_RANK_MAX = 80
CRITICAL_RANK_MIN = 400
CRITICAL_RANK_MAX = 500
FOCUS_CATEGORIES = ["明显进步", "明显退步", "波动风险", "临界段", "严重偏科", "稳定优秀"]


def clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip().replace("\n", " ")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isnan(value):
            return None
        return float(value)
    text = clean(value)
    if not text:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def intish(value: Any) -> int | None:
    value_num = num(value)
    if value_num is None:
        return None
    return int(round(value_num))


def is_numeric_class(value: Any) -> bool:
    text = clean(value)
    return bool(re.fullmatch(r"\d+", text))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_exam_name(path: Path) -> str:
    stem = path.stem.strip()
    if "高一第二学期4月期中考试" in stem or "高一第二学期期中考试" in stem:
        return "高一第二学期期中考试"
    suffixes = [
        "主三门总分名次分段表",
        "主三门总分名次分段",
        "三门总分名次分段表",
        "三门总分名次分段",
        "五门总分名次分段表",
        "五门总分名次分段",
        "九门总分名次分段表",
        "九门总分名次分段",
        "班级均分表",
    ]
    name = stem
    for suffix in suffixes:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.strip()


def exam_sort_key(exam_name: str) -> tuple[int, str]:
    return (EXAM_ORDER.get(exam_name, 999), exam_name)


def detect_total_type(text: str) -> str:
    if "九门" in text:
        return "九门"
    if "五门" in text:
        return "五门"
    if "三门" in text or "主三" in text:
        return "主三门"
    return "主三门"


def row_values(ws: Any, row_idx: int, max_col: int | None = None) -> list[str]:
    max_col = max_col or ws.max_column
    return [clean(ws.cell(row_idx, col).value) for col in range(1, max_col + 1)]


def classify_workbook(path: Path) -> dict[str, Any]:
    wb = load_workbook(path, data_only=True, read_only=False)
    ws = wb[wb.sheetnames[0]]
    row1 = row_values(ws, 1)
    row2 = row_values(ws, 2)
    row3 = row_values(ws, 3)
    workbook_type = "unknown"
    if len(row2) >= 4 and row2[:4] == ["学号", "班级", "学籍", "姓名"]:
        workbook_type = "student_scores"
    elif len(row1) >= 3 and row1[:3] == ["班级类型", "班级", "班主任"]:
        workbook_type = "class_averages"
    elif len(row2) >= 3 and row2[0] == "班级" and row2[1] == "班主任" and any("-" in x for x in row2[2:]):
        workbook_type = "rank_bands"
    title = row1[0] if row1 else ""
    return {
        "file": str(path),
        "file_name": path.name,
        "sha256": sha256_file(path),
        "exam": canonical_exam_name(path),
        "exam_order": EXAM_ORDER.get(canonical_exam_name(path)),
        "type": workbook_type,
        "sheets": wb.sheetnames,
        "active_sheet": ws.title,
        "rows": ws.max_row,
        "cols": ws.max_column,
        "title": title,
        "row1": row1,
        "row2": row2,
        "row3": row3,
        "warnings": [],
    }


def inspect_files(paths: list[Path]) -> dict[str, Any]:
    files = [classify_workbook(path) for path in paths]
    warnings: list[str] = []
    hashes: dict[str, list[str]] = defaultdict(list)
    by_exam_type: dict[tuple[str, str], list[str]] = defaultdict(list)
    for info in files:
        hashes[info["sha256"]].append(info["file_name"])
        by_exam_type[(info["exam"], info["type"])].append(info["file_name"])
        if info["type"] == "unknown":
            warnings.append(f"无法识别工作簿类型: {info['file_name']}")
    for digest, names in hashes.items():
        if len(names) > 1:
            warnings.append(f"发现内容完全相同的文件，疑似重复/错传: {', '.join(names)}")
    for (exam, workbook_type), names in by_exam_type.items():
        if workbook_type != "unknown" and len(names) > 1:
            warnings.append(f"同一考试和类型出现多个文件: {exam} / {workbook_type}: {', '.join(names)}")
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "files": files,
        "warnings": warnings,
    }


def parse_student_scores(path: Path, info: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    wb = load_workbook(path, data_only=True, read_only=False)
    ws = wb[wb.sheetnames[0]]
    score_long: list[dict[str, Any]] = []
    totals: list[dict[str, Any]] = []
    students: list[dict[str, Any]] = []
    exam = info["exam"]
    order = EXAM_ORDER.get(exam, 999)
    for row_idx in range(4, ws.max_row + 1):
        student_id = clean(ws.cell(row_idx, 1).value)
        if not student_id:
            continue
        class_name = clean(ws.cell(row_idx, 2).value)
        xueji = clean(ws.cell(row_idx, 3).value)
        name = clean(ws.cell(row_idx, 4).value)
        students.append({
            "exam": exam,
            "exam_order": order,
            "student_id": student_id,
            "class": class_name,
            "xueji": xueji,
            "xueji_label": XUEJI_LABELS.get(xueji, ""),
            "name": name,
            "source_file": path.name,
        })
        for subject, (score_col, pct_col) in SUBJECT_COLS.items():
            score = num(ws.cell(row_idx, score_col).value)
            pct = num(ws.cell(row_idx, pct_col).value)
            if score is None and pct is None:
                continue
            score_long.append({
                "exam": exam,
                "exam_order": order,
                "student_id": student_id,
                "class": class_name,
                "xueji": xueji,
                "name": name,
                "subject": subject,
                "score": score,
                "grade_percentile": pct,
                "source_file": path.name,
            })
        for total_type, (score_col, pct_col, xueji_rank_col, grade_rank_col) in TOTAL_COLS.items():
            score = num(ws.cell(row_idx, score_col).value)
            pct = num(ws.cell(row_idx, pct_col).value)
            xueji_rank = intish(ws.cell(row_idx, xueji_rank_col).value)
            grade_rank = intish(ws.cell(row_idx, grade_rank_col).value)
            if score is None and pct is None and xueji_rank is None and grade_rank is None:
                continue
            totals.append({
                "exam": exam,
                "exam_order": order,
                "student_id": student_id,
                "class": class_name,
                "xueji": xueji,
                "name": name,
                "total_type": total_type,
                "total_score": score,
                "grade_percentile": pct,
                "xueji_rank": xueji_rank,
                "grade_rank": grade_rank,
                "source_file": path.name,
            })
    return students, score_long, totals


def parse_class_averages(path: Path, info: dict[str, Any]) -> list[dict[str, Any]]:
    wb = load_workbook(path, data_only=True, read_only=False)
    ws = wb[wb.sheetnames[0]]
    exam = info["exam"]
    order = EXAM_ORDER.get(exam, 999)
    current_class_type = ""
    rows: list[dict[str, Any]] = []
    for row_idx in range(3, ws.max_row + 1):
        if not any(ws.cell(row_idx, col).value not in (None, "") for col in range(1, ws.max_column + 1)):
            continue
        class_type_raw = clean(ws.cell(row_idx, 1).value)
        if class_type_raw:
            current_class_type = class_type_raw
        class_name = clean(ws.cell(row_idx, 2).value)
        teacher = clean(ws.cell(row_idx, 3).value)
        is_actual = is_numeric_class(class_name)
        row: dict[str, Any] = {
            "exam": exam,
            "exam_order": order,
            "class_type": current_class_type,
            "class": class_name,
            "teacher": teacher,
            "is_actual_class": is_actual,
            "source_file": path.name,
        }
        for subject, col in CLASS_AVG_SUBJECT_COLS.items():
            row[f"{subject}_avg"] = num(ws.cell(row_idx, col).value)
        for total_type, (score_col, rank_col) in CLASS_AVG_TOTAL_COLS.items():
            row[f"{total_type}_avg"] = num(ws.cell(row_idx, score_col).value)
            row[f"{total_type}_rank"] = intish(ws.cell(row_idx, rank_col).value)
        rows.append(row)
    return rows


def parse_rank_bands(path: Path, info: dict[str, Any]) -> list[dict[str, Any]]:
    wb = load_workbook(path, data_only=True, read_only=False)
    ws = wb[wb.sheetnames[0]]
    exam = info["exam"]
    order = EXAM_ORDER.get(exam, 999)
    total_type = detect_total_type(clean(ws.cell(1, 1).value) + path.name)
    bands = [clean(ws.cell(2, col).value) for col in range(3, ws.max_column + 1)]
    rows: list[dict[str, Any]] = []
    for row_idx in range(3, ws.max_row + 1):
        class_name = clean(ws.cell(row_idx, 1).value)
        if not is_numeric_class(class_name):
            continue
        teacher = clean(ws.cell(row_idx, 2).value)
        for offset, band in enumerate(bands, start=3):
            if not band:
                continue
            rows.append({
                "exam": exam,
                "exam_order": order,
                "total_type": total_type,
                "rank_basis": "学籍排名",
                "class": class_name,
                "teacher": teacher,
                "band": band,
                "count": intish(ws.cell(row_idx, offset).value) or 0,
                "source_file": path.name,
            })
    return rows


def parse_inputs(paths: list[Path]) -> dict[str, Any]:
    inspection = inspect_files(paths)
    students: list[dict[str, Any]] = []
    score_long: list[dict[str, Any]] = []
    student_totals: list[dict[str, Any]] = []
    class_averages: list[dict[str, Any]] = []
    rank_bands: list[dict[str, Any]] = []
    warnings = list(inspection["warnings"])
    for info in inspection["files"]:
        path = Path(info["file"])
        if info["type"] == "student_scores":
            file_students, file_scores, file_totals = parse_student_scores(path, info)
            students.extend(file_students)
            score_long.extend(file_scores)
            student_totals.extend(file_totals)
        elif info["type"] == "class_averages":
            class_averages.extend(parse_class_averages(path, info))
        elif info["type"] == "rank_bands":
            rank_bands.extend(parse_rank_bands(path, info))
    for exam in sorted({row["exam"] for row in class_averages}, key=exam_sort_key):
        for total_type in TOTAL_COLS:
            actual_values = [
                row.get(f"{total_type}_avg")
                for row in class_averages
                if row["exam"] == exam and row.get("is_actual_class")
            ]
            usable = [value for value in actual_values if value not in (None, 0)]
            if actual_values and not usable:
                warnings.append(f"{exam} 的 {total_type} 在班级均分表中不可用或全为0，趋势分析将跳过。")
    return {
        "inspection": inspection,
        "warnings": warnings,
        "students": students,
        "score_long": score_long,
        "student_totals": student_totals,
        "class_averages": class_averages,
        "rank_bands": rank_bands,
    }


def most_common_target_class(students: list[dict[str, Any]]) -> str:
    counts = Counter(row["class"] for row in students if row.get("class"))
    return counts.most_common(1)[0][0] if counts else "6"


def available_exams(rows: Iterable[dict[str, Any]]) -> list[str]:
    return sorted({row["exam"] for row in rows}, key=exam_sort_key)


def latest_exam(rows: Iterable[dict[str, Any]]) -> str | None:
    exams = available_exams(rows)
    return exams[-1] if exams else None


def previous_exam_name(exams: list[str], current: str | None) -> str | None:
    if current is None or current not in exams:
        return None
    idx = exams.index(current)
    if idx == 0:
        return None
    return exams[idx - 1]


def segment_for_rank(rank: int | None) -> str:
    if rank is None:
        return ""
    if rank <= HIGH_RANK_MAX:
        return "高分段"
    if CRITICAL_RANK_MIN <= rank <= CRITICAL_RANK_MAX:
        return "临界段"
    if rank > CRITICAL_RANK_MAX:
        return "薄弱段"
    return "普通段"


def trend_label(ranks: list[int]) -> str:
    if len(ranks) < 2:
        return "数据不足"
    deltas = [prev - cur for prev, cur in zip(ranks, ranks[1:])]
    rank_range = max(ranks) - min(ranks)
    if rank_range >= VOLATILITY_RANK_THRESHOLD:
        if len(deltas) >= 2 and deltas[-1] > 0 and deltas[-2] < 0:
            return "回升且波动"
        if len(deltas) >= 2 and deltas[-1] < 0 and deltas[-2] > 0:
            return "回落且波动"
        return "波动"
    if all(delta > 0 for delta in deltas):
        return "持续进步"
    if all(delta < 0 for delta in deltas):
        return "持续退步"
    if deltas[-1] >= PROGRESS_RANK_THRESHOLD:
        return "明显回升"
    if deltas[-1] <= -PROGRESS_RANK_THRESHOLD:
        return "明显回落"
    return "稳定"


def rows_by_key(rows: list[dict[str, Any]], *keys: str) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row.get(key) for key in keys)].append(row)
    return grouped


def latest_subject_percentiles(score_long: list[dict[str, Any]], student_id: str, exam: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in score_long:
        if row["student_id"] == student_id and row["exam"] == exam and row.get("grade_percentile") is not None:
            result[row["subject"]] = float(row["grade_percentile"])
    return result


def previous_subject_percentiles(score_long: list[dict[str, Any]], student_id: str, subject: str, exam_order: int) -> tuple[str | None, float | None]:
    candidates = [
        row
        for row in score_long
        if row["student_id"] == student_id
        and row["subject"] == subject
        and row.get("grade_percentile") is not None
        and row.get("exam_order", 999) < exam_order
    ]
    if not candidates:
        return None, None
    candidates.sort(key=lambda row: row.get("exam_order", 999))
    row = candidates[-1]
    return row["exam"], float(row["grade_percentile"])


def recommended_subjects(
    score_long: list[dict[str, Any]],
    student_id: str,
    exam: str,
    total_percentile: float | None,
) -> tuple[list[str], list[str]]:
    pcts = latest_subject_percentiles(score_long, student_id, exam)
    if not pcts:
        return [], []
    weakest = sorted(pcts.items(), key=lambda item: item[1], reverse=True)[:2]
    recommended = [subject for subject, _ in weakest]
    reasons: list[str] = []
    for subject, pct in weakest:
        reason_bits = [f"{subject}百分位{pct:.3f}"]
        if total_percentile is not None and pct - total_percentile >= 0.20:
            reason_bits.append(f"比总分百分位低{pct - total_percentile:.3f}")
        exam_order = EXAM_ORDER.get(exam, 999)
        prev_exam, prev_pct = previous_subject_percentiles(score_long, student_id, subject, exam_order)
        if prev_pct is not None and pct - prev_pct >= SUBJECT_PCT_THRESHOLD:
            reason_bits.append(f"较{prev_exam}退步{pct - prev_pct:.3f}")
        reasons.append("，".join(reason_bits))
    return recommended, reasons


def serious_subjects(
    score_long: list[dict[str, Any]],
    student_id: str,
    exam: str,
    total_percentile: float | None,
) -> list[str]:
    if total_percentile is None:
        return []
    pcts = latest_subject_percentiles(score_long, student_id, exam)
    result: list[str] = []
    for subject, pct in pcts.items():
        if pct - total_percentile >= 0.20:
            result.append(subject)
    return sorted(result, key=lambda subject: pcts[subject], reverse=True)


def build_student_trends(
    student_totals: list[dict[str, Any]],
    score_long: list[dict[str, Any]],
    target_class: str,
) -> list[dict[str, Any]]:
    trends: list[dict[str, Any]] = []
    target_totals = [
        row
        for row in student_totals
        if row.get("class") == target_class
        and row.get("xueji_rank") is not None
        and row.get("total_type") in TREND_TOTAL_TYPES
    ]
    grouped = rows_by_key(target_totals, "student_id", "total_type")
    for (student_id, total_type), rows in grouped.items():
        rows.sort(key=lambda row: row.get("exam_order", 999))
        ranks = [int(row["xueji_rank"]) for row in rows if row.get("xueji_rank") is not None]
        if not ranks:
            continue
        latest = rows[-1]
        previous = rows[-2] if len(rows) >= 2 else None
        delta = None
        if previous and previous.get("xueji_rank") is not None and latest.get("xueji_rank") is not None:
            delta = int(previous["xueji_rank"]) - int(latest["xueji_rank"])
        rank_range = max(ranks) - min(ranks) if len(ranks) >= 2 else 0
        recommended, subject_reasons = recommended_subjects(
            score_long,
            student_id,
            latest["exam"],
            latest.get("grade_percentile"),
        )
        weak_subjects = serious_subjects(
            score_long,
            student_id,
            latest["exam"],
            latest.get("grade_percentile"),
        )
        trends.append({
            "student_id": student_id,
            "name": latest.get("name"),
            "class": latest.get("class"),
            "xueji": latest.get("xueji"),
            "total_type": total_type,
            "exam_count": len(rows),
            "first_exam": rows[0]["exam"],
            "latest_exam": latest["exam"],
            "previous_exam": previous["exam"] if previous else "",
            "first_xueji_rank": rows[0].get("xueji_rank"),
            "previous_xueji_rank": previous.get("xueji_rank") if previous else None,
            "latest_xueji_rank": latest.get("xueji_rank"),
            "latest_grade_rank": latest.get("grade_rank"),
            "latest_total_score": latest.get("total_score"),
            "latest_grade_percentile": latest.get("grade_percentile"),
            "adjacent_rank_delta": delta,
            "rank_range": rank_range,
            "trend_label": trend_label(ranks),
            "latest_segment": segment_for_rank(latest.get("xueji_rank")),
            "recommended_subjects": "、".join(recommended),
            "subject_reasons": "；".join(subject_reasons),
            "serious_subjects": "、".join(weak_subjects),
        })
    trends.sort(key=lambda row: (row["total_type"], row.get("latest_xueji_rank") or 99999))
    return trends


def severity_for_focus(category: str, row: dict[str, Any]) -> float:
    delta = row.get("adjacent_rank_delta")
    if category == "明显进步" and delta is not None:
        return float(delta)
    if category == "明显退步" and delta is not None:
        return float(-delta)
    if category == "波动风险":
        return float(row.get("rank_range") or 0)
    if category == "临界段":
        rank = row.get("latest_xueji_rank") or 99999
        return float(CRITICAL_RANK_MAX - abs(rank - ((CRITICAL_RANK_MIN + CRITICAL_RANK_MAX) / 2)))
    if category == "稳定优秀":
        return float(99999 - (row.get("latest_xueji_rank") or 99999))
    if category == "严重偏科":
        return float(len(clean(row.get("serious_subjects")).split("、")))
    return 0.0


def split_zh_list(value: Any) -> list[str]:
    return [part for part in clean(value).split("、") if part]


def add_unique(items: list[str], value: Any) -> None:
    text = clean(value)
    if text and text not in items:
        items.append(text)


def add_unique_many(items: list[str], values: Iterable[Any]) -> None:
    for value in values:
        add_unique(items, value)


def format_rank_change(row: dict[str, Any]) -> str:
    total_type = clean(row.get("total_type"))
    previous_exam = clean(row.get("previous_exam")) or "上次"
    previous_rank = row.get("previous_xueji_rank")
    latest_rank = row.get("latest_xueji_rank")
    delta = row.get("adjacent_rank_delta")
    delta_text = ""
    if delta is not None:
        delta_text = f"，变化{int(delta):+d}名"
    return (
        f"{total_type}: {previous_exam} {previous_rank or '-'} -> "
        f"{clean(row.get('latest_exam'))} {latest_rank or '-'}{delta_text}，"
        f"区间波动{row.get('rank_range') or 0}名，{clean(row.get('latest_segment'))}"
    )


def category_evidence(category: str, row: dict[str, Any]) -> str:
    total_type = clean(row.get("total_type"))
    if category in {"明显进步", "明显退步"}:
        return f"{category}: {format_rank_change(row)}"
    if category == "波动风险":
        return f"波动风险: {total_type} 学籍排名区间波动{row.get('rank_range') or 0}名"
    if category == "临界段":
        return f"临界段: {total_type} 最新学籍排名{row.get('latest_xueji_rank')}"
    if category == "严重偏科":
        subjects = clean(row.get("serious_subjects")) or clean(row.get("recommended_subjects"))
        return f"严重偏科: {subjects}；{clean(row.get('subject_reasons'))}"
    if category == "稳定优秀":
        return f"稳定优秀: {total_type} 最新学籍排名{row.get('latest_xueji_rank')}，波动{row.get('rank_range') or 0}名"
    return clean(row.get("trend_label"))


def triggered_categories(row: dict[str, Any]) -> list[str]:
    categories: list[str] = []
    delta = row.get("adjacent_rank_delta")
    if delta is not None and delta >= PROGRESS_RANK_THRESHOLD:
        categories.append("明显进步")
    if delta is not None and delta <= -PROGRESS_RANK_THRESHOLD:
        categories.append("明显退步")
    if row.get("exam_count", 0) >= 3 and (row.get("rank_range") or 0) >= VOLATILITY_RANK_THRESHOLD:
        categories.append("波动风险")
    if row.get("latest_segment") == "临界段":
        categories.append("临界段")
    if clean(row.get("serious_subjects")):
        categories.append("严重偏科")
    if row.get("latest_xueji_rank") is not None and row["latest_xueji_rank"] <= HIGH_RANK_MAX and (row.get("rank_range") or 0) < VOLATILITY_RANK_THRESHOLD:
        categories.append("稳定优秀")
    return categories


def new_focus_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "student_id": clean(row.get("student_id")),
        "name": row.get("name"),
        "xueji": row.get("xueji"),
        "_categories": [],
        "_total_types": [],
        "_latest_exams": [],
        "_rank_summaries": [],
        "_recommended_subjects": [],
        "_serious_subjects": [],
        "_evidence": [],
        "_priority": 0.0,
    }


def build_focus_students(trends: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in trends:
        if row.get("total_type") != "主三门":
            continue
        categories = triggered_categories(row)
        if not categories:
            continue
        student_id = clean(row.get("student_id"))
        item = merged.setdefault(student_id, new_focus_item(row))
        for category in categories:
            add_unique(item["_categories"], category)
            add_unique(item["_evidence"], category_evidence(category, row))
            item["_priority"] = max(item["_priority"], severity_for_focus(category, row))
        add_unique(item["_total_types"], row.get("total_type"))
        add_unique(item["_latest_exams"], row.get("latest_exam"))
        add_unique(item["_rank_summaries"], format_rank_change(row))
        add_unique_many(item["_recommended_subjects"], split_zh_list(row.get("recommended_subjects")))
        add_unique_many(item["_serious_subjects"], split_zh_list(row.get("serious_subjects")))

    # 五门只作为已入选学生的补充证据；不单独触发重点名单。
    for row in trends:
        if row.get("total_type") != "五门":
            continue
        student_id = clean(row.get("student_id"))
        item = merged.get(student_id)
        if item is None:
            continue
        add_unique(item["_total_types"], row.get("total_type"))
        add_unique(item["_latest_exams"], row.get("latest_exam"))
        add_unique(item["_rank_summaries"], format_rank_change(row))
        add_unique(item["_evidence"], f"五门补充: {format_rank_change(row)}")
        add_unique_many(item["_recommended_subjects"], split_zh_list(row.get("recommended_subjects")))
        add_unique_many(item["_serious_subjects"], split_zh_list(row.get("serious_subjects")))

    focus: list[dict[str, Any]] = []
    for item in merged.values():
        ordered_categories = [category for category in FOCUS_CATEGORIES if category in item["_categories"]]
        focus.append({
            "student_id": item["student_id"],
            "name": item.get("name"),
            "xueji": item.get("xueji"),
            "categories": "、".join(ordered_categories),
            "total_types": "、".join(item["_total_types"]),
            "latest_exams": "、".join(item["_latest_exams"]),
            "rank_summary": "；".join(item["_rank_summaries"]),
            "recommended_subjects": "、".join(item["_recommended_subjects"]),
            "serious_subjects": "、".join(item["_serious_subjects"]),
            "evidence": "；".join(item["_evidence"]),
            "priority_score": round(item["_priority"], 4),
        })
    focus.sort(key=lambda row: row.get("priority_score") or 0, reverse=True)
    return focus


def build_subject_communication(focus: list[dict[str, Any]], trends: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in focus:
        categories = split_zh_list(item.get("categories"))
        communication_categories = [category for category in categories if category in {"明显退步", "临界段", "严重偏科", "波动风险"}]
        if not communication_categories:
            continue
        subjects = clean(item.get("serious_subjects")) or clean(item.get("recommended_subjects"))
        for subject in [part for part in subjects.split("、") if part]:
            rows.append({
                "subject": subject,
                "categories": "、".join(communication_categories),
                "student_id": item["student_id"],
                "name": item["name"],
                "xueji": item["xueji"],
                "total_types": item["total_types"],
                "latest_exams": item["latest_exams"],
                "rank_summary": item["rank_summary"],
                "evidence": item["evidence"],
            })
    rows.sort(key=lambda row: (row["subject"], row["categories"], row["name"]))
    return rows


def build_target_rank_bands(student_totals: list[dict[str, Any]], target_class: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    grouped = rows_by_key(
        [row for row in student_totals if row.get("class") == target_class and row.get("xueji_rank") is not None],
        "exam",
        "total_type",
    )
    for (exam, total_type), items in grouped.items():
        ranks = [int(row["xueji_rank"]) for row in items]
        rows.append({
            "exam": exam,
            "exam_order": EXAM_ORDER.get(exam, 999),
            "total_type": total_type,
            "class": target_class,
            "basis": "学生明细学籍排名精确计算",
            "student_count": len(ranks),
            "high_1_80": sum(1 for rank in ranks if rank <= HIGH_RANK_MAX),
            "critical_400_500": sum(1 for rank in ranks if CRITICAL_RANK_MIN <= rank <= CRITICAL_RANK_MAX),
            "weak_after_500": sum(1 for rank in ranks if rank > CRITICAL_RANK_MAX),
        })
    rows.sort(key=lambda row: (row["exam_order"], row["total_type"]))
    return rows


def aggregate_uploaded_rank_bands(rank_bands: list[dict[str, Any]], target_class: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    grouped = rows_by_key(rank_bands, "exam", "total_type", "class")
    for (exam, total_type, class_name), items in grouped.items():
        if class_name != target_class:
            continue
        counts = {item["band"]: item["count"] for item in items}
        rows.append({
            "exam": exam,
            "exam_order": EXAM_ORDER.get(exam, 999),
            "total_type": total_type,
            "class": class_name,
            "basis": "上传名次段表原始40名一段",
            "raw_total": sum(counts.values()),
            "high_1_80": counts.get("1-40", 0) + counts.get("41-80", 0),
            "critical_raw_note": "原表为40名一段，400-500不精确；请看学生明细精确计算。",
            "band_counts": json.dumps(counts, ensure_ascii=False),
        })
    rows.sort(key=lambda row: (row["exam_order"], row["total_type"]))
    return rows


def build_xueji1_analysis(
    students: list[dict[str, Any]],
    score_long: list[dict[str, Any]],
    student_totals: list[dict[str, Any]],
    class_averages: list[dict[str, Any]],
    target_class: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    actual_class_rows = [row for row in class_averages if row.get("is_actual_class")]
    exams = available_exams(students)
    for exam in exams:
        target_student_ids = {
            row["student_id"]
            for row in students
            if row["exam"] == exam and row["class"] == target_class and row["xueji"] == "1"
        }
        if not target_student_ids:
            continue
        exam_class_rows = [row for row in actual_class_rows if row["exam"] == exam]
        target_official = next((row for row in exam_class_rows if row["class"] == target_class), None)
        for subject in SUBJECT_COLS:
            values = [
                row["score"]
                for row in score_long
                if row["exam"] == exam and row["student_id"] in target_student_ids and row["subject"] == subject and row.get("score") is not None
            ]
            if not values:
                continue
            avg = mean(values)
            parallel_comparators = [
                row.get(f"{subject}_avg")
                for row in exam_class_rows
                if row.get("class_type") == "平行班" and row.get("class") != target_class
            ]
            grade_comparators = [
                row.get(f"{subject}_avg")
                for row in exam_class_rows
                if row.get("class") != target_class
            ]
            rows.append({
                "exam": exam,
                "exam_order": EXAM_ORDER.get(exam, 999),
                "metric": subject,
                "metric_type": "科目",
                "xueji1_count": len(target_student_ids),
                "xueji1_avg": round(avg, 2),
                "official_class_avg": target_official.get(f"{subject}_avg") if target_official else None,
                "official_rank": "",
                "estimated_parallel_rank": estimated_rank(avg, parallel_comparators),
                "estimated_grade_rank": estimated_rank(avg, grade_comparators),
                "note": "本班学籍1均分与其他班现有均分比较，属于估算。",
            })
        for total_type in TREND_TOTAL_TYPES:
            values = [
                row["total_score"]
                for row in student_totals
                if row["exam"] == exam
                and row["student_id"] in target_student_ids
                and row["total_type"] == total_type
                and row.get("total_score") is not None
            ]
            if not values:
                continue
            avg = mean(values)
            official_avg_key = f"{total_type}_avg"
            official_rank_key = f"{total_type}_rank"
            parallel_comparators = [
                row.get(official_avg_key)
                for row in exam_class_rows
                if row.get("class_type") == "平行班" and row.get("class") != target_class
            ]
            grade_comparators = [
                row.get(official_avg_key)
                for row in exam_class_rows
                if row.get("class") != target_class
            ]
            if not any(value not in (None, 0) for value in parallel_comparators + grade_comparators):
                continue
            rows.append({
                "exam": exam,
                "exam_order": EXAM_ORDER.get(exam, 999),
                "metric": total_type,
                "metric_type": "总分",
                "xueji1_count": len(target_student_ids),
                "xueji1_avg": round(avg, 2),
                "official_class_avg": target_official.get(official_avg_key) if target_official else None,
                "official_rank": target_official.get(official_rank_key) if target_official else None,
                "estimated_parallel_rank": estimated_rank(avg, parallel_comparators),
                "estimated_grade_rank": estimated_rank(avg, grade_comparators),
                "note": "本班学籍1均分与其他班现有均分比较，属于估算，不是全年级精确重排。",
            })
    rows.sort(key=lambda row: (row["exam_order"], row["metric_type"], row["metric"]))
    return rows


def estimated_rank(value: float, comparators: Iterable[float | None]) -> int | None:
    usable = [float(item) for item in comparators if item not in (None, 0)]
    if not usable:
        return None
    return 1 + sum(1 for item in usable if item > value)


def build_class_overview(class_averages: list[dict[str, Any]], target_class: str) -> list[dict[str, Any]]:
    rows = [row for row in class_averages if row.get("is_actual_class") and row.get("class") == target_class]
    rows.sort(key=lambda row: row.get("exam_order", 999))
    return rows


def build_history_rows(class_overview: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in class_overview:
        for subject in SUBJECT_COLS:
            value = row.get(f"{subject}_avg")
            if value not in (None, 0):
                rows.append({
                    "exam": row["exam"],
                    "exam_order": row["exam_order"],
                    "metric_type": "科目均分",
                    "metric": subject,
                    "value": value,
                    "rank": "",
                })
        for total_type in TREND_TOTAL_TYPES:
            value = row.get(f"{total_type}_avg")
            rank = row.get(f"{total_type}_rank")
            if value not in (None, 0):
                rows.append({
                    "exam": row["exam"],
                    "exam_order": row["exam_order"],
                    "metric_type": "总分均分",
                    "metric": total_type,
                    "value": value,
                    "rank": rank,
                })
    return rows


def build_analysis(parsed: dict[str, Any], target_class: str) -> dict[str, Any]:
    students = parsed["students"]
    score_long = parsed["score_long"]
    student_totals = parsed["student_totals"]
    class_averages = parsed["class_averages"]
    rank_bands = parsed["rank_bands"]
    class_overview = build_class_overview(class_averages, target_class)
    student_trends = build_student_trends(student_totals, score_long, target_class)
    focus_students = build_focus_students(student_trends)
    subject_comm = build_subject_communication(focus_students, student_trends)
    target_rank_bands = build_target_rank_bands(student_totals, target_class)
    uploaded_rank_band_summary = aggregate_uploaded_rank_bands(rank_bands, target_class)
    xueji1_analysis = build_xueji1_analysis(students, score_long, student_totals, class_averages, target_class)
    actual_class_averages = [row for row in class_averages if row.get("is_actual_class")]
    parallel = [row for row in actual_class_averages if row.get("class_type") == "平行班"]
    exams = available_exams(students + class_averages)
    latest = latest_exam(students + class_averages)
    previous = previous_exam_name(exams, latest)
    warnings = list(parsed["warnings"])
    uploaded_count_by_exam_total = {
        (row["exam"], row["total_type"]): row.get("raw_total")
        for row in uploaded_rank_band_summary
        if row.get("raw_total") is not None
    }
    for row in target_rank_bands:
        uploaded_count = uploaded_count_by_exam_total.get((row["exam"], row["total_type"]))
        if uploaded_count is not None and uploaded_count != row.get("student_count"):
            warnings.append(
                f"{row['exam']} {row['total_type']} 目标班学生明细可计算学籍排名人数为{row.get('student_count')}，"
                f"上传名次段表人数为{uploaded_count}，请确认名次段表是否包含不同学籍口径。"
            )
    if not students:
        warnings.append("没有找到学生成绩表，学生趋势和重点名单不可用。")
    if not class_averages:
        warnings.append("没有找到班级均分表，班级对比不可用。")
    if not rank_bands:
        warnings.append("没有找到名次段表，只能用学生明细计算目标班名次段。")
    if class_overview and latest:
        latest_target = next((row for row in class_overview if row["exam"] == latest), None)
        if latest_target is None:
            warnings.append(f"最新考试 {latest} 没有目标班 {target_class} 的班级均分行。")
    return {
        "target_class": target_class,
        "exams": exams,
        "latest_exam": latest,
        "previous_exam": previous,
        "warnings": warnings,
        "students": students,
        "score_long": score_long,
        "student_totals": student_totals,
        "class_averages": class_averages,
        "class_overview": class_overview,
        "parallel_comparison": parallel,
        "grade_reference": actual_class_averages,
        "history_rows": build_history_rows(class_overview),
        "rank_bands": rank_bands,
        "target_rank_bands": target_rank_bands,
        "uploaded_rank_band_summary": uploaded_rank_band_summary,
        "xueji1_analysis": xueji1_analysis,
        "student_trends": student_trends,
        "focus_students": focus_students,
        "subject_communication": subject_comm,
    }


def json_safe(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json_safe(row.get(key, "")) for key in fieldnames})


def display_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float):
        return round(value, 4)
    return value


def add_sheet(wb: Workbook, title: str, rows: list[dict[str, Any]] | list[list[Any]]) -> None:
    ws = wb.create_sheet(title)
    if not rows:
        ws.append(["说明"])
        ws.append(["无数据"])
        return
    if isinstance(rows[0], dict):
        fieldnames: list[str] = []
        seen: set[str] = set()
        for row in rows:  # type: ignore[assignment]
            for key in row.keys():  # type: ignore[union-attr]
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
        ws.append(fieldnames)
        for row in rows:  # type: ignore[assignment]
            ws.append([display_value(row.get(key)) for key in fieldnames])  # type: ignore[union-attr]
    else:
        for row in rows:  # type: ignore[assignment]
            ws.append([display_value(value) for value in row])
    style_sheet(ws)


def style_sheet(ws: Any) -> None:
    header_fill = PatternFill("solid", fgColor="D9EAF7")
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"
    if ws.max_row > 1 and ws.max_column > 1:
        ws.auto_filter.ref = ws.dimensions
    for column_cells in ws.columns:
        max_len = 8
        col_letter = column_cells[0].column_letter
        for cell in column_cells:
            max_len = max(max_len, min(len(clean(cell.value)), 50))
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[col_letter].width = max_len + 2


def make_notes_rows(analysis: dict[str, Any]) -> list[list[Any]]:
    return [
        ["项目", "说明"],
        ["目标班级", analysis["target_class"]],
        ["考试顺序", " -> ".join(analysis["exams"])],
        ["总分进退步主指标", "学籍排名；排名数字变小为进步，变化>=80名为明显进退步。"],
        ["波动风险", "三次及以上考试中学籍排名范围>=120名。"],
        ["名次段", "高分段1-80；临界段400-500；均按学籍排名。"],
        ["未考科目", "不记为0，不参与该科或该总分类型趋势。"],
        ["学籍1估算", "本班学籍1均分与其他班现有均分比较，属于估算，不是全年级精确重排。"],
        ["数据包", "CSV/JSON保留全量明细，支持后续聊天追问。"],
    ]


def write_workbook(path: Path, analysis: dict[str, Any]) -> None:
    wb = Workbook()
    default = wb.active
    wb.remove(default)
    add_sheet(wb, "口径说明", make_notes_rows(analysis))
    add_sheet(wb, "数据质量检查", [{"warning": warning} for warning in analysis["warnings"]])
    add_sheet(wb, "班级总览", analysis["class_overview"])
    add_sheet(wb, "平行班对比", analysis["parallel_comparison"])
    add_sheet(wb, "全年级参考", analysis["grade_reference"])
    add_sheet(wb, "本班历史趋势", analysis["history_rows"])
    rank_rows = analysis["target_rank_bands"] + analysis["uploaded_rank_band_summary"]
    add_sheet(wb, "名次段分析", rank_rows)
    add_sheet(wb, "学籍1分析", analysis["xueji1_analysis"])
    add_sheet(wb, "学生趋势明细", analysis["student_trends"])
    add_sheet(wb, "重点关注名单", analysis["focus_students"])
    add_sheet(wb, "科任沟通清单", analysis["subject_communication"])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def summarize_class_change(class_overview: list[dict[str, Any]], latest: str | None, previous: str | None) -> list[str]:
    if not latest:
        return ["未识别到最新考试。"]
    latest_row = next((row for row in class_overview if row["exam"] == latest), None)
    previous_row = next((row for row in class_overview if row["exam"] == previous), None) if previous else None
    if not latest_row:
        return [f"最新考试 {latest} 未找到目标班级均分。"]
    lines: list[str] = []
    for total_type in TREND_TOTAL_TYPES:
        avg = latest_row.get(f"{total_type}_avg")
        rank = latest_row.get(f"{total_type}_rank")
        if avg in (None, 0):
            continue
        line = f"- {latest} {total_type}均分 {avg}，班级均分排名 {rank}"
        if previous_row and previous_row.get(f"{total_type}_avg") not in (None, 0):
            avg_delta = float(avg) - float(previous_row.get(f"{total_type}_avg"))
            rank_prev = previous_row.get(f"{total_type}_rank")
            if rank_prev not in (None, "") and rank not in (None, ""):
                rank_delta = int(rank_prev) - int(rank)
                line += f"，较{previous}均分变化 {avg_delta:.2f}，排名变化 {rank_delta:+d}"
            else:
                line += f"，较{previous}均分变化 {avg_delta:.2f}"
        lines.append(line)
    return lines or [f"{latest} 暂无可用总分均分。"]


def top_focus_lines(focus: list[dict[str, Any]]) -> list[str]:
    if not focus:
        return ["暂无达到默认阈值的重点名单。"]
    counts: Counter[str] = Counter()
    for row in focus:
        counts.update(split_zh_list(row.get("categories")))
    lines = [f"- 重点名单合计: {len(focus)}人"]
    lines.extend([f"- {category}: {counts[category]}人" for category in FOCUS_CATEGORIES if counts.get(category)])
    for category in ["明显进步", "明显退步", "波动风险", "临界段", "严重偏科", "稳定优秀"]:
        names = [
            f"{row['name']}({row['total_types']})"
            for row in focus
            if category in split_zh_list(row.get("categories"))
        ]
        if names:
            lines.append(f"- {category}名单: {'、'.join(names)}")
    return lines


def summarize_xueji1(rows: list[dict[str, Any]], latest: str | None) -> list[str]:
    if not latest:
        return ["无最新考试。"]
    latest_rows = [
        row
        for row in rows
        if row["exam"] == latest
        and row["metric_type"] == "总分"
        and row["metric"] in TREND_TOTAL_TYPES
    ]
    if not latest_rows:
        return ["最新考试暂无学籍1总分估算。"]
    lines = []
    for row in latest_rows:
        lines.append(
            f"- {row['metric']} 学籍1均分 {row['xueji1_avg']}，平行班估算排名 {row['estimated_parallel_rank']}，全年级估算排名 {row['estimated_grade_rank']}（估算）"
        )
    return lines


def write_summary(path: Path, analysis: dict[str, Any]) -> None:
    latest = analysis["latest_exam"]
    previous = analysis["previous_exam"]
    lines = [
        "# 成绩分析摘要",
        "",
        f"- 目标班级: {analysis['target_class']}班",
        f"- 考试范围: {' -> '.join(analysis['exams'])}",
        f"- 最新考试: {latest or '未识别'}",
        "",
        "## 数据质量",
    ]
    if analysis["warnings"]:
        lines.extend([f"- {warning}" for warning in analysis["warnings"]])
    else:
        lines.append("- 未发现关键数据质量问题。")
    lines.extend(["", "## 班级总览"])
    lines.extend(summarize_class_change(analysis["class_overview"], latest, previous))
    lines.extend(["", "## 学籍1估算"])
    lines.extend(summarize_xueji1(analysis["xueji1_analysis"], latest))
    lines.extend(["", "## 重点名单概览"])
    lines.extend(top_focus_lines(analysis["focus_students"]))
    lines.extend([
        "",
        "## 口径提醒",
        "- 总分进退步按学籍排名判断，变化>=80名为明显进退步。",
        "- 单科趋势按年级百分位判断，未考科目不参与比较。",
        "- 学籍1排名变化是本班学籍1均分与其他班现有均分比较的估算，不是精确重排。",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_data_package(out_dir: Path, analysis: dict[str, Any]) -> dict[str, str]:
    data_dir = out_dir / "data"
    outputs = {
        "score_long": data_dir / "score_long.csv",
        "student_totals": data_dir / "student_totals.csv",
        "class_averages": data_dir / "class_averages.csv",
        "rank_bands": data_dir / "rank_bands.csv",
        "student_trends": data_dir / "student_trends.csv",
        "focus_students": data_dir / "focus_students.csv",
        "subject_communication": data_dir / "subject_communication.csv",
    }
    write_csv(outputs["score_long"], analysis["score_long"])
    write_csv(outputs["student_totals"], analysis["student_totals"])
    write_csv(outputs["class_averages"], analysis["class_averages"])
    write_csv(outputs["rank_bands"], analysis["rank_bands"])
    write_csv(outputs["student_trends"], analysis["student_trends"])
    write_csv(outputs["focus_students"], analysis["focus_students"])
    write_csv(outputs["subject_communication"], analysis["subject_communication"])
    json_path = data_dir / "analysis.json"
    serializable = {
        key: value
        for key, value in analysis.items()
        if key
        in {
            "target_class",
            "exams",
            "latest_exam",
            "previous_exam",
            "warnings",
            "class_overview",
            "target_rank_bands",
            "xueji1_analysis",
            "student_trends",
            "focus_students",
            "subject_communication",
        }
    }
    json_path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    result = {name: str(path) for name, path in outputs.items()}
    result["analysis_json"] = str(json_path)
    return result


def run_analysis(paths: list[Path], out_dir: Path, target_class: str | None) -> dict[str, Any]:
    parsed = parse_inputs(paths)
    resolved_target = target_class or most_common_target_class(parsed["students"])
    analysis = build_analysis(parsed, resolved_target)
    out_dir.mkdir(parents=True, exist_ok=True)
    workbook_path = out_dir / "成绩分析.xlsx"
    summary_path = out_dir / "成绩分析摘要.md"
    write_workbook(workbook_path, analysis)
    write_summary(summary_path, analysis)
    data_outputs = write_data_package(out_dir, analysis)
    html_outputs: dict[str, str] = {}
    if build_learning_profile_outputs is not None:
        try:
            html_outputs = build_learning_profile_outputs(
                data_dir=out_dir / "data",
                out_dir=out_dir,
                target_class=resolved_target,
            )
        except Exception as exc:
            analysis["warnings"].append(f"学生学情HTML生成失败: {exc}")
    return {
        "workbook": str(workbook_path),
        "summary": str(summary_path),
        "data": data_outputs,
        "student_learning_profile": html_outputs,
        "warnings": analysis["warnings"],
        "target_class": resolved_target,
        "exams": analysis["exams"],
    }


def existing_paths(items: list[str]) -> list[Path]:
    paths = [Path(item).expanduser() for item in items]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise SystemExit("Missing input files:\n" + "\n".join(missing))
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect and analyze exam score workbooks.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect workbook structures.")
    inspect_parser.add_argument("--inputs", nargs="+", required=True, help="Input Excel workbooks.")
    inspect_parser.add_argument("--json-out", help="Optional JSON output path.")

    run_parser = subparsers.add_parser("run", help="Generate workbook, summary, and data package.")
    run_parser.add_argument("--inputs", nargs="+", required=True, help="Input Excel workbooks.")
    run_parser.add_argument("--out", required=True, help="Output directory.")
    run_parser.add_argument("--target-class", default=None, help="Target class, default auto-detects from student scores.")
    run_parser.add_argument("--json-out", help="Optional JSON output path for run manifest.")

    args = parser.parse_args()

    if args.command == "inspect":
        result = inspect_files(existing_paths(args.inputs))
        text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        if args.json_out:
            Path(args.json_out).expanduser().write_text(text + "\n", encoding="utf-8")
        print(text)
    elif args.command == "run":
        result = run_analysis(existing_paths(args.inputs), Path(args.out).expanduser(), args.target_class)
        text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        if args.json_out:
            Path(args.json_out).expanduser().write_text(text + "\n", encoding="utf-8")
        print(text)


if __name__ == "__main__":
    main()
