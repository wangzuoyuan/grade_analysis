"""旧版 /api 排名指标实现（/rank-metrics、/rank-range、/rank-frequency）。

P0-A1 起所有概念判定（指标选项、分箱、班内名次）一律委托
app.analysis.definitions 的共享实现；本模块只负责旧库（SubjectScore/
TotalScore/Exam）取数与旧响应组装，保证与新 /api/v1 路径同一概念输出
同一数值。

名次语义（v2.1/P0-A1）：总分类指标只用真实学籍/年级名次（xueji_rank/
grade_rank）；单科没有真实年级名次时名次不可得——绝不按「百分位 × 人数」
推算名次（历史实现 _percentile_to_rank 已删除，见
docs/diagnosis-roadmap/p0-definitions.md §名次区间）。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

from app.analysis import definitions as defs


def rank_metric_options(grade: int, mode: str = "frequency") -> list[dict[str, str]]:
    """排名指标选项（共享定义委托，新 /api/v1 同一口径）。"""
    return defs.metric_options(grade, mode)


def _metric_meta(grade: int, metric: str, mode: str) -> dict[str, str]:
    return defs.metric_meta(grade, metric, mode)


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
    """班内名次：共享定义 min_ranks（同分同名次，NULL 不参与）。"""
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
        class_ranks = defs.min_ranks([(student_id, value) for student_id, value in items])
        for student_id, _value in items:
            rank = class_ranks.get(student_id)
            if rank is not None:
                ranks[(exam_id, student_id)] = rank
    return ranks


def rank_range_filter(
    exam_id: int,
    metric: str,
    rank_min: int,
    rank_max: int,
    class_num: Optional[int] = None,
) -> dict[str, Any]:
    """单场考试按指标与年级名次区间筛选学生。

    名次语义：只用真实名次。总分类取 TotalScore.xueji_rank/grade_rank；
    单科旧成绩库（SubjectScore）没有年级名次列，真实名次不可得 → 该科
    名单为空并在 metric_note 标注（绝不按百分位推算名次）。"""
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
                # 真实名次：学籍名次优先，其次年级名次；皆缺 → 名次不可得
                year_rank = defs.resolve_year_rank(row.xueji_rank, row.grade_rank)
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
            for row in subject_rows:
                profile = profiles.get((row.exam_id, row.student_id), {})
                if class_num is not None and profile.get("class_num") != class_num:
                    continue
                # 单科仅当来源提供真实年级名次时才可筛（旧库无该列，恒为 None）；
                # 名次不可得的学生不进结果，百分位仍可在排名频次里查。
                year_rank = defs.resolve_year_rank(
                    getattr(row, "xueji_rank", None), getattr(row, "grade_rank", None)
                )
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
            "metric_note": (
                "只按真实学籍/年级名次筛选；名次不可得的学生不进名单"
                "（不按百分位推算名次），可靠百分位见排名频次的百分位分箱。"
            ),
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
                rank = defs.resolve_year_rank(row.xueji_rank, row.grade_rank)
                bin_key = defs.rank_bin(rank)
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
            bins = [
                {"key": key, "label": f"{key.split('_')[0][1:]}-{key.split('_')[1]}名次数"}
                for key in sorted_rank_bins
            ]
        elif meta["kind"] == "subject_grade_score":
            source_rows = (
                db.query(SubjectScore)
                .filter(SubjectScore.exam_id.in_(selected_ids), SubjectScore.subject == meta["key"])
                .all()
            )
            bins = [
                {"key": f"g{score}", "label": f"{score}分", "separator_after": score in defs.GRADE_SCORE_SEPARATOR_AFTER}
                for score in defs.GRADE_SCORE_VALUES
            ]
            for row in source_rows:
                profile = profiles.get((row.exam_id, row.student_id), {})
                if class_num is not None and profile.get("class_num") != class_num:
                    continue
                bin_key = defs.grade_score_bin(row.grade_score)
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
            bins = [{"key": key, "label": label} for key, label, _, _ in defs.PERCENTILE_BINS]
            for row in source_rows:
                profile = profiles.get((row.exam_id, row.student_id), {})
                if class_num is not None and profile.get("class_num") != class_num:
                    continue
                # 百分位缺失（含缺考行）不入任何箱，绝不残留上次百分位
                bin_key = defs.percentile_bin(row.grade_percentile)
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
