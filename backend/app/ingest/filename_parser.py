import re

EXAM_TYPE_MAP = {
    "月考": "月考",
    "期中": "期中",
    "期末": "期末",
    "一模": "一模",
    "二模": "二模",
    "三模": "三模",
}

SEMESTER_MAP = {
    "第一学期": "上",
    "第二学期": "下",
    "上学期": "上",
    "下学期": "下",
    "高一第一学期": "上",
    "高二第一学期": "上",
    "高三第一学期": "上",
    "高一第二学期": "下",
    "高二第二学期": "下",
    "高三第二学期": "下",
}

GRADE_MAP = {
    "高一": 1,
    "高二": 2,
    "高三": 3,
}

GRADE_LABELS = {
    1: "高一",
    2: "高二",
    3: "高三",
}

SEMESTER_LABELS = {
    "上": "第一学期",
    "下": "第二学期",
}


def infer_sort_key(filename: str, semester: str | None, exam_type: str | None) -> str:
    """从文件名推断排序月份 - 学年制第二学期落在下一自然年 - """
    year_match = re.search(r"(20\d\d)学年", filename) or re.search(r"(20\d\d)", filename)
    school_year = int(year_match.group(1)) if year_match else 2024

    month_match = re.search(r"(\d+)月", filename)
    if month_match:
        month = int(month_match.group(1))
        year = school_year
        if semester == "下" and month <= 8:
            year = school_year + 1
        return f"{year}-{month:02d}"

    if semester == "上":
        month = 11 if exam_type == "期中" else 12 if exam_type == "月考" else 1
        year = school_year + 1 if month == 1 else school_year
        return f"{year}-{month:02d}"

    if semester == "下":
        month = 4 if exam_type == "期中" else 6 if exam_type == "期末" else 3
        return f"{school_year + 1}-{month:02d}"

    return f"{school_year}-01"


def infer_grade(filename: str) -> int | None:
    for grade_name, grade_num in GRADE_MAP.items():
        if grade_name in filename:
            return grade_num

    cohort_match = re.search(r"(20\d\d)级", filename)
    school_year_match = re.search(r"(20\d\d)学年", filename)
    if cohort_match and school_year_match:
        grade = int(school_year_match.group(1)) - int(cohort_match.group(1)) + 1
        if grade in GRADE_LABELS:
            return grade
    return None


def parse_filename(filename: str) -> dict:
    """从文件名提取年级,学期,考试类型,排序键,规范名"""
    result = {
        "grade": None,
        "semester": None,
        "exam_type": None,
        "sort_key": None,
        "canonical_name": filename,
    }

    result["grade"] = infer_grade(filename)

    # 提取学期
    for sem_name, sem_code in SEMESTER_MAP.items():
        if sem_name in filename:
            result["semester"] = sem_code
            break

    # 提取考试类型
    for et_name, et_code in EXAM_TYPE_MAP.items():
        if et_name in filename:
            result["exam_type"] = et_code
            break

    result["sort_key"] = infer_sort_key(filename, result["semester"], result["exam_type"])

    # 构建规范名。月考同一学期可能有多次，名称里需要保留月份，避免 9/10/12 月月考被合并。
    grade_str = GRADE_LABELS.get(result["grade"], "未知年级")
    sem_str = SEMESTER_LABELS.get(result["semester"], "未知学期")
    exam_str = result["exam_type"] or "考试"
    month_match = re.search(r"(\d+)月", filename)
    month_str = f"{int(month_match.group(1))}月" if result["exam_type"] == "月考" and month_match else ""
    suffix = "考试" if exam_str in {"期中", "期末", "一模", "二模", "三模"} else ""
    result["canonical_name"] = f"{grade_str}{sem_str}{month_str}{exam_str}{suffix}"

    return result

def canonical_exam_name(exam_type: str, grade: int, semester: str) -> str:
    """生成规范考试名"""
    grade_str = GRADE_LABELS.get(grade, "未知年级")
    sem_str = SEMESTER_LABELS.get(semester if semester in ("上", "下") else "下", "第二学期")
    suffix = "考试" if exam_type in {"期中", "期末", "一模", "二模", "三模"} else ""
    return f"{grade_str}{sem_str}{exam_type}{suffix}"


def build_exam_name(grade: int, semester: str, exam_type: str, month: int | None = None) -> str:
    """按用户手动指定的年级/学期/考试类型/月份生成规范考试名。
    月考需带月份，避免同一学期多次月考被合并（与 parse_filename 中的逻辑一致）。"""
    grade_str = GRADE_LABELS.get(grade, "未知年级")
    sem_str = SEMESTER_LABELS.get(semester if semester in ("上", "下") else "下", "第二学期")
    month_str = f"{int(month)}月" if exam_type == "月考" and month else ""
    suffix = "考试" if exam_type in {"期中", "期末", "一模", "二模", "三模"} else ""
    return f"{grade_str}{sem_str}{month_str}{exam_type}{suffix}"
