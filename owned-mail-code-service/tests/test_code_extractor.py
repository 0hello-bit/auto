from app.code_extractor import extract_code, extract_from_parts


def test_default_pattern_matches_6_digits():
    assert extract_code("Your code is 123456 thanks") == "123456"


def test_default_pattern_matches_4_digits():
    assert extract_code("PIN: 1234") == "1234"


def test_default_pattern_rejects_too_short_and_too_long_runs():
    assert extract_code("abc 123 xyz") is None        # only 3 digits
    assert extract_code("id 123456789 done") is None   # 9-digit bounded run


def test_custom_pattern_six_digits():
    assert extract_code("code 1234 and 567890", r"\b\d{6}\b") == "567890"


def test_invalid_pattern_returns_none():
    assert extract_code("123456", r"(\d{6}") is None  # broken regex -> None


def test_extract_from_parts_checks_subject_then_body_then_html():
    assert extract_from_parts("code 111111", "222222", "333333", r"\b\d{6}\b") == "111111"
    assert extract_from_parts("no code here", "body 444444", "", r"\b\d{6}\b") == "444444"
    assert extract_from_parts("nope", "nope", "html 555555", r"\b\d{6}\b") == "555555"
