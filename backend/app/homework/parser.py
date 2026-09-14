"""作业录入文本解析与学科归类。

移植自原「作业跟踪」Flask 应用 app.py 的 SUBJECT_GROUPS /
normalize_subject / parse_homework_item / is_subject_item，逻辑保持不变，
仅独立成模块供 router 与对话工具复用。
"""

import re

# 学科归类表：原始文本含任一关键词即归到对应规范学科名。
SUBJECT_GROUPS = [
    ("语文", ["语文"]),
    ("数学", ["数学"]),
    ("英语", ["英语"]),
    ("物理", ["物理"]),
    ("化学", ["化学"]),
    ("生物", ["生物"]),
    ("历史", ["历史"]),
    ("地理", ["地理"]),
    ("政治", ["政治", "道法", "道德与法治"]),
    ("全科", ["全科"]),
]

# 录入文本里「学生/学科 : 列表」的分隔符
NAME_SPLIT_RE = re.compile(r"[，,；;、\s]+")
COLON_SPLIT_RE = re.compile(r"[:：]")


def normalize_subject(subject):
    """把原始学科文本归一化到规范学科名；识别不到则原样返回。"""
    if not subject:
        return "未分类"

    normalized = str(subject).strip().replace(" ", "")
    if not normalized:
        return "未分类"

    for canonical_name, keywords in SUBJECT_GROUPS:
        if any(keyword in normalized for keyword in keywords):
            return canonical_name

    return normalized


def parse_homework_item(item):
    """解析单个作业项，返回 (subject, content, remark)。

    - '请假'     → ('全科', None, '请假')
    - '英语粉书' → ('英语', '英语粉书', None)
    - '数学'     → ('数学', None, None)
    无法识别学科时，原样作为学科名返回。
    """
    item = item.strip()
    if not item:
        return None

    if item == "请假":
        return ("全科", None, "请假")

    for canonical_name, keywords in SUBJECT_GROUPS:
        for keyword in keywords:
            if keyword in item:
                content = item if item != keyword else None
                return (canonical_name, content, None)

    return (item, None, None)


def is_subject_item(item):
    """item 是否包含可识别的学科关键词（用于区分缺交 vs 特殊情况）。"""
    item = item.strip()
    for _, keywords in SUBJECT_GROUPS:
        for keyword in keywords:
            if keyword in item:
                return True
    return False


# 「全班都交了」的表达：右侧整体匹配才算（如「数学：全交」「英语：齐了」）
_FULL_SUBMIT_TOKENS = {"全交", "齐", "全齐", "交齐", "都交", "全交齐", "都交齐"}


def is_full_submission(text):
    """右侧列表是否为「全交/齐」类表达（忽略 了/嘛/吗/标点/空白）。"""
    normalized = re.sub(r"[了嘛吗！!。，,、\s]", "", str(text or ""))
    return normalized in _FULL_SUBMIT_TOKENS


def split_names(raw):
    """把「陈晓帆、顾小满 沈知远」这类列表拆成姓名数组。"""
    return [n.strip() for n in NAME_SPLIT_RE.split(raw) if n.strip()]


def split_colon(line):
    """按中英文冒号切一次，返回 (左, 右) 或 None（格式错误）。"""
    parts = COLON_SPLIT_RE.split(line, maxsplit=1)
    if len(parts) < 2:
        return None
    return parts[0].strip(), parts[1].strip()
