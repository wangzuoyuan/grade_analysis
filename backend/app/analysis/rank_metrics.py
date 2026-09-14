from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Optional


BASE_SUBJECTS = ["语文", "数学", "英语"]
ELECTIVE_SUBJECTS = ["物理", "化学", "生物", "政治", "历史", "地理"]
ALL_SUBJECTS = BASE_SUBJECTS + ELECTIVE_SUBJECTS
PERCENTILE_BINS = [
    ("p0_20", "前20%", 0, 0.2),
    ("p20_40", "20%-40%", 0.2, 0.4),
    ("p40_60", "40%-60%", 0.4, 0.6),
    ("p60_80", "60%-80%", 0.6, 0.8),
    ("p80_100", "后20%", 0.8, 1.0),
]
GRADE_SCORE_VALUES = [70, 67, 64, 61, 58, 55, 52, 49, 46, 43, 40]
GRADE_SCORE_SEPARATOR_AFTER = {67, 58, 49, 43}
GRADE_SCORE_BINS = [
    (f"g{score}", f"{score}分", score, score in GRADE_SCORE_SEPARATOR_AFTER)
    for score in GRADE_SCORE_VALUES
]


def rank_metric_options(grade: int, mode: str = "frequency") -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    if grade == 1:
        options.extend(
            {"value": f"subject:{subject}", "label": subject, "kind": "subject_percentile"}
            for subject in ALL_SUBJECTS
        )
        options.extend(
            {"value": f"total:{total_type}", "label": f"{total_type}总分", "kind": "total_rank"}
            for total_type in ["主三门", "五门"]
        )
        return options

    options.extend(
        {"value": f"subject:{subject}", "label": subject, "kind": "subject_percentile"}
        for subject in BASE_SUBJECTS
    )
    if mode == "frequency":
        options.extend(
            {"value": f"subject_grade:{subject}", "label": f"{subject}等级分", "kind": "subject_grade_score"}
            for subject in ELECTIVE_SUBJECTS
        )
    options.extend(
        {"value": f"total:{total_type}", "label": f"{total_type}总分", "kind": "total_rank"}
        for total_type in ["主三门", "3+3"]
    )
    return options


def _metric_meta(grade: int, metric: str, mode: str) -> dict[str, str]:
    for option in rank_metric_options(grade, mode):
        if option["value"] == metric:
            source, key = metric.split(":", 1)
            return {**option, "source": source, "key": key}
    raise ValueError("该年级不支持此排名指标")


def _normalize_percentile(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    number = float(value)
    if number > 1:
        number = number / 100
    return max(0, min(number, 1))


def _percentile_bin(value: Optional[float]) -> Optional[str]:
    pct = _normalize_percentile(value)
    if pct is None:
        return None
    for key, _, lower, upper in PERCENTILE_BINS:
        if pct <= upper and (pct > lower or lower == 0):
            return key
    return PERCENTILE_BINS[-1][0]


def _grade_score_bin(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    score = int(round(float(value)))
    if score in GRADE_SCORE_VALUES:
        return f"g{score}"
    return None


def _rank_bin(rank: Optional[int]) -> Optional[str]:
    if rank is None or rank < 1:
        return None
    start = ((int(rank) - 1) // 40) * 40 + 1
    return f"r{start}_{start + 39}"


def _rank_bin_label(key: str) -> str:
    start, end = key.removeprefix("r").split("_")
    return f"{start}-{end}名次数"


def _parse_exam_ids(exam_ids: Optional[str | list[int]]) -> list[int]:
    if not exam_ids:
        return []
    if isinstance(exam_ids, list):
        return [int(value) for value in exam_ids]
    return [int(part) for part in str(exam_ids).split(",") if part.strip()]


def _profiles_for_exams(db, exam_ids: list[int]) -> dict[tuple[int, str], dict[str, Any]]:
    from app.db.models import SubjectScore

    rows = db.query(SubjectScore).filter(SubjectScore.exam_id.in_(exam_ids)).all()
    profiles: dict[tuple[int, str], dict[str, Any]] = {}
    class_counters: dict[tuple[int, str], Counter] = defaultdict(Counter)
    for row in rows:
        key = (row.exam_id, row.student_id)
        profile = profiles.setdefault(
            key,
            {"student_id": row.student_id, "name": row.name or row.student_id, "class_num": row.class_num},
        )
        if row.name:
            profile["name"] = row.name
        if row.class_num is not None:
            class_counters[key][row.class_num] += 1
    for key, counter in class_counters.items():
        if counter:
            profiles[key]["class_num"] = counter.most_common(1)[0][0]
    return profiles


def _ranked_class_scores(rows: list[Any], value_attr: str, profiles: dict[tuple[int, str], dict[str, Any]]) -> dict[tuple[int, str], int]:
    grouped: dict[tuple[int, int], list[tuple[str, float]]] = defaultdict(list)
    for row in rows:
        value = getattr(row, value_attr)
        if value is None:
            continue
        profile = profiles.get((row.exam_id, row.student_id), {})
        class_num = profile.get("class_num")
        if class_num is None:
            continue
        grouped[(row.exam_id, int(class_num))].append((row.student_id, float(value)))

    ranks: dict[tuple[int, str], int] = {}
    for (exam_id, _class_num), items in grouped.items():
        values = [value for _, value in items]
        for student_id, value in items:
            ranks[(exam_id, student_id)] = sum(1 for peer in values if peer > value) + 1
    return ranks


def _cohort_sizes(db, exam_ids: list[int]) -> dict[int, int]:
    from app.db.models import TotalScore

    result: dict[int, int] = {}
    rows = (
        db.query(TotalScore)
        .filter(TotalScore.exam_id.in_(exam_ids), TotalScore.total_type == "主三门")
        .all()
    )
    grouped: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        rank = row.xueji_rank or row.grade_rank
        if rank is not None:
            grouped[row.exam_id].append(rank)
    for exam_id, ranks in grouped.items():
        if ranks:
            result[exam_id] = max(ranks)
    return result


def _percentile_to_rank(percentile: Optional[float], cohort_size: Optional[int]) -> Optional[int]:
    pct = _normalize_percentile(percentile)
    if pct is None or not cohort_size:
        return None
    return max(1, int(math.ceil(pct * cohort_size)))


def rank_range_filter(
    exam_id: int,
    metric: str,
    rank_min: int,
    rank_max: int,
    class_num: Optional[int] = None,
) -> dict[str, Any]:
    from app.db.models import Exam, SessionLocal, SubjectScore, TotalScore

    db = SessionLocal()
    try:
        exam = db.query(Exam).filter(Exam.id == exam_id).first()
        if not exam:
            raise ValueError("考试不存在")
        meta = _metric_meta(exam.grade, metric, "range")
        rank_min = max(1, int(rank_min))
        rank_max = int(rank_max)
        if rank_max < rank_min:
            raise ValueError("排名区间上界不能小于下界")

        profiles = _profiles_for_exams(db, [exam_id])
        rows: list[dict[str, Any]] = []

        if meta["kind"] == "total_rank":
            total_rows = (
                db.query(TotalScore)
                .filter(TotalScore.exam_id == exam_id, TotalScore.total_type == meta["key"])
                .all()
            )
            class_ranks = _ranked_class_scores(total_rows, "total_score", profiles)
            for row in total_rows:
                profile = profiles.get((row.exam_id, row.student_id), {})
                if class_num is not None and profile.get("class_num") != class_num:
                    continue
                year_rank = row.xueji_rank or row.grade_rank
                if year_rank is None or not (rank_min <= year_rank <= rank_max):
                    continue
                rows.append(
                    {
                        "student_id": row.student_id,
                        "name": profile.get("name") or row.student_id,
                        "class_num": profile.get("class_num"),
                        "score": row.total_score,
                        "class_rank": class_ranks.get((row.exam_id, row.student_id)),
                        "year_rank": year_rank,
                    }
                )
        else:
            subject_rows = (
                db.query(SubjectScore)
                .filter(SubjectScore.exam_id == exam_id, SubjectScore.subject == meta["key"])
                .all()
            )
            class_ranks = _ranked_class_scores(subject_rows, "raw_score", profiles)
            cohort_size = _cohort_sizes(db, [exam_id]).get(exam_id) or len(subject_rows)
            for row in subject_rows:
                profile = profiles.get((row.exam_id, row.student_id), {})
                if class_num is not None and profile.get("class_num") != class_num:
                    continue
                year_rank = _percentile_to_rank(row.grade_percentile, cohort_size)
                if year_rank is None or not (rank_min <= year_rank <= rank_max):
                    continue
                rows.append(
                    {
                        "student_id": row.student_id,
                        "name": profile.get("name") or row.name or row.student_id,
                        "class_num": profile.get("class_num"),
                        "score": row.raw_score,
                        "class_rank": class_ranks.get((row.exam_id, row.student_id)),
                        "year_rank": year_rank,
                    }
                )

        rows.sort(key=lambda row: (row["year_rank"] is None, row["year_rank"] or 10**9, row["student_id"]))
        return {
            "exam": {"id": exam.id, "name": exam.name, "grade": exam.grade, "exam_date": exam.exam_date},
            "metric": metric,
            "metric_label": meta["label"],
            "metric_kind": meta["kind"],
            "rank_min": rank_min,
            "rank_max": rank_max,
            "class_num": class_num,
            "rows": rows,
            "metric_note": "总分按已有学籍/年级排名筛选；单科按年级百分位换算年级排名后筛选。",
        }
    finally:
        db.close()


def rank_frequency_stats(
    grade: int,
    metric: str,
    exam_ids: Optional[str | list[int]] = None,
    class_num: Optional[int] = None,
    recent_count: int = 5,
) -> dict[str, Any]:
    from app.db.models import Exam, SessionLocal, SubjectScore, TotalScore

    db = SessionLocal()
    try:
        meta = _metric_meta(grade, metric, "frequency")
        parsed_exam_ids = _parse_exam_ids(exam_ids)
        if parsed_exam_ids:
            exams = (
                db.query(Exam)
                .filter(Exam.grade == grade, Exam.id.in_(parsed_exam_ids))
                .order_by(Exam.exam_date, Exam.id)
                .all()
            )
        else:
            all_exams = db.query(Exam).filter(Exam.grade == grade).order_by(Exam.exam_date, Exam.id).all()
            exams = all_exams[-max(1, min(int(recent_count or 5), 12)) :]
        selected_ids = [exam.id for exam in exams]
        profiles = _profiles_for_exams(db, selected_ids)
        student_rows: dict[str, dict[str, Any]] = {}
        rank_bin_keys: set[str] = set()

        if meta["kind"] == "total_rank":
            source_rows = (
                db.query(TotalScore)
                .filter(TotalScore.exam_id.in_(selected_ids), TotalScore.total_type == meta["key"])
                .all()
            )
            for row in source_rows:
                profile = profiles.get((row.exam_id, row.student_id), {})
                if class_num is not None and profile.get("class_num") != class_num:
                    continue
                rank = row.xueji_rank or row.grade_rank
                bin_key = _rank_bin(rank)
                if not bin_key:
                    continue
                rank_bin_keys.add(bin_key)
                entry = student_rows.setdefault(
                    row.student_id,
                    {
                        "student_id": row.student_id,
                        "name": profile.get("name") or row.student_id,
                        "class_num": profile.get("class_num"),
                        "total_count": 0,
                    },
                )
                entry[bin_key] = entry.get(bin_key, 0) + 1
                entry["total_count"] += 1
            sorted_rank_bins = sorted(
                rank_bin_keys,
                key=lambda key: int(key.split("_")[0].removeprefix("r")),
            )
            bins = [{"key": key, "label": _rank_bin_label(key)} for key in sorted_rank_bins]
        elif meta["kind"] == "subject_grade_score":
            source_rows = (
                db.query(SubjectScore)
                .filter(SubjectScore.exam_id.in_(selected_ids), SubjectScore.subject == meta["key"])
                .all()
            )
            bins = [
                {"key": key, "label": label, "separator_after": separator_after}
                for key, label, _score, separator_after in GRADE_SCORE_BINS
            ]
            for row in source_rows:
                profile = profiles.get((row.exam_id, row.student_id), {})
                if class_num is not None and profile.get("class_num") != class_num:
                    continue
                bin_key = _grade_score_bin(row.grade_score)
                if not bin_key:
                    continue
                entry = student_rows.setdefault(
                    row.student_id,
                    {
                        "student_id": row.student_id,
                        "name": profile.get("name") or row.name or row.student_id,
                        "class_num": profile.get("class_num"),
                        "total_count": 0,
                    },
                )
                entry[bin_key] = entry.get(bin_key, 0) + 1
                entry["total_count"] += 1
        else:
            source_rows = (
                db.query(SubjectScore)
                .filter(SubjectScore.exam_id.in_(selected_ids), SubjectScore.subject == meta["key"])
                .all()
            )
            bins = [{"key": key, "label": label} for key, label, _, _ in PERCENTILE_BINS]
            for row in source_rows:
                profile = profiles.get((row.exam_id, row.student_id), {})
                if class_num is not None and profile.get("class_num") != class_num:
                    continue
                bin_key = _percentile_bin(row.grade_percentile)
                if not bin_key:
                    continue
                entry = student_rows.setdefault(
                    row.student_id,
                    {
                        "student_id": row.student_id,
                        "name": profile.get("name") or row.name or row.student_id,
                        "class_num": profile.get("class_num"),
                        "total_count": 0,
                    },
                )
                entry[bin_key] = entry.get(bin_key, 0) + 1
                entry["total_count"] += 1

        rows = []
        for entry in student_rows.values():
            for bin_info in bins:
                entry.setdefault(bin_info["key"], 0)
            rows.append(entry)
        rows.sort(
            key=lambda row: (
                -sum((index + 1) * row.get(bin_info["key"], 0) for index, bin_info in enumerate(bins)),
                row["student_id"],
            )
        )

        return {
            "grade": grade,
            "metric": metric,
            "metric_label": meta["label"],
            "metric_kind": meta["kind"],
            "class_num": class_num,
            "exams": [{"id": exam.id, "name": exam.name, "exam_date": exam.exam_date} for exam in exams],
            "bins": bins,
            "rows": rows,
            "metric_note": "单科按年级百分位五等分；+3选科按70/67/64/61/58/55/52/49/46/43/40精确等级分统计；总分按40名一档统计。",
        }
    finally:
        db.close()
