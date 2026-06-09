"""Extract verification codes from mail content with a configurable regex."""
import html as html_lib
import re
from typing import Optional

from .config import settings

DEFAULT_PATTERN = r"\b\d{4,8}\b"

_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style).*?>.*?</\1>")
_BR_RE = re.compile(r"(?is)<br\s*/?>")
_BLOCK_END_RE = re.compile(r"(?is)</(?:p|div|tr|td|table|section|article)>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_WS_RE = re.compile(r"\s+")


def html_to_text(raw_html: str) -> str:
    """Convert HTML source to visible-ish text before running code regex.

    This avoids matching random numbers in CSS, tracking URLs, widths,
    timestamps, and hidden markup before the real verification code.
    """
    if not raw_html:
        return ""

    text = _SCRIPT_STYLE_RE.sub(" ", raw_html)
    text = _BR_RE.sub("\n", text)
    text = _BLOCK_END_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html_lib.unescape(text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def extract_code(text: str, pattern: str = DEFAULT_PATTERN) -> Optional[str]:
    """Return a regex match.

    If the regex has a capturing group, return group(1).
    Otherwise return the whole match.
    """
    if not text:
        return None

    try:
        match = re.search(pattern, text)
    except re.error:
        return None

    if not match:
        return None

    if match.lastindex:
        return match.group(1)

    return match.group(0)


def extract_from_parts(
    subject: str,
    text_body: str,
    html_body: str,
    pattern: str = "",
) -> Optional[str]:
    """Search subject, plain text body, then visible HTML text."""
    pattern = pattern or settings.DEFAULT_CODE_PATTERN or DEFAULT_PATTERN

    visible_html = html_to_text(html_body)

    for chunk in (subject, text_body, visible_html):
        code = extract_code(chunk, pattern)
        if code:
            return code

    return None