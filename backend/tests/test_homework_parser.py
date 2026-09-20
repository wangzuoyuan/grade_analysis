from app.homework.parser import (
    is_full_submission,
    is_subject_item,
    normalize_subject,
    parse_homework_item,
    split_colon,
    split_names,
)


def test_normalize_subject_groups():
    assert normalize_subject("英语粉书") == "英语"
    assert normalize_subject("道法") == "政治"
    assert normalize_subject("数学") == "数学"
    assert normalize_subject("") == "未分类"
    # 识别不到的原样返回
    assert normalize_subject("周记") == "周记"


def test_parse_homework_item():
    assert parse_homework_item("请假") == ("全科", None, "请假")
    assert parse_homework_item("英语粉书") == ("英语", "英语粉书", None)
    assert parse_homework_item("数学") == ("数学", None, None)
    assert parse_homework_item("") is None


def test_is_subject_item():
    assert is_subject_item("英语作文") is True
    assert is_subject_item("迟到") is False
    assert is_subject_item("请假") is False


def test_split_helpers():
    assert split_colon("陈晓帆：英语、数学") == ("陈晓帆", "英语、数学")
    assert split_colon("没有冒号") is None
    assert split_names("陈晓帆、顾小满 沈知远，叶青禾") == ["陈晓帆", "顾小满", "沈知远", "叶青禾"]


def test_is_full_submission():
    assert is_full_submission("全交") is True
    assert is_full_submission("齐") is True
    assert is_full_submission("齐了") is True
    assert is_full_submission("全齐") is True
    assert is_full_submission("交齐！") is True
    assert is_full_submission("都交齐") is True
    # 整体匹配才算：混了姓名不是全交
    assert is_full_submission("全交、陈晓帆") is False
    assert is_full_submission("陈晓帆") is False
    assert is_full_submission("") is False


def test_evaluation_tone_poor_marker_rule():
    """单字「差」只在独立成段或「差：细节」打头时算负面；藏在词内不算。"""
    from app.api.homework import _evaluation_tone

    assert _evaluation_tone("差") == "negative"
    assert _evaluation_tone("差：最后两题没写完") == "negative"
    assert _evaluation_tone("差:潦草") == "negative"
    assert _evaluation_tone("不认真") == "negative"
    assert _evaluation_tone("不合格") == "negative"
    assert _evaluation_tone("优秀") == "neutral"
    assert _evaluation_tone("误差分析") == "neutral"
    assert _evaluation_tone("差错订正") == "neutral"
    assert _evaluation_tone("练习册") == "neutral"
    assert _evaluation_tone("迟到｜差") == "negative"
    assert _evaluation_tone("迟到｜误差分析") == "neutral"
