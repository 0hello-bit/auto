import pytest

from app.account_parser import parse_line, parse_text


def test_parse_basic():
    acc = parse_line("a@hotmail.com----pass1----cid-123----rtoken")
    assert acc.email == "a@hotmail.com"
    assert acc.password == "pass1"
    assert acc.client_id == "cid-123"
    assert acc.refresh_token == "rtoken"


def test_refresh_token_with_special_chars_is_not_truncated():
    # A token that itself contains "----" and other special characters.
    token = "0.AS-abc----def//gg==----xyz.long----token----end"
    acc = parse_line(f"u@hotmail.com----p----cid----{token}")
    assert acc.refresh_token == token  # everything after the 3rd "----" kept


def test_only_first_three_separators_are_used():
    acc = parse_line("e@x.com----pw----cid----a----b----c")
    assert acc.password == "pw"
    assert acc.client_id == "cid"
    assert acc.refresh_token == "a----b----c"


def test_invalid_line_raises():
    with pytest.raises(ValueError):
        parse_line("not-enough----fields")


def test_parse_text_skips_comments_and_collects_errors():
    text = "\n".join([
        "# a comment",
        "good@x.com----p----c----t",
        "bad-line-without-separators",
        "   ",
    ])
    accounts, errors = parse_text(text)
    assert len(accounts) == 1
    assert accounts[0].email == "good@x.com"
    assert len(errors) == 1


def test_parse_text_dedupes_by_email():
    text = "dup@x.com----p1----c1----t1\ndup@x.com----p2----c2----t2"
    accounts, _ = parse_text(text)
    assert len(accounts) == 1
    assert accounts[0].refresh_token == "t2"  # last wins
