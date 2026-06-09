"""Parse mailbox account lines of the form:

    email----password----client_id----refresh_token

The refresh_token may itself contain ``----`` and other special characters, so
we MUST split on only the first three separators. Everything after the third
separator is the token, verbatim.
"""
from typing import List, Tuple

from .models import Account

SEP = "----"


def parse_line(line: str) -> Account:
    """Parse a single account line. Raises ValueError on malformed input."""
    raw = line.strip()

    # IMPORTANT: maxsplit=3 -> at most 4 parts. The 4th part keeps any further
    # "----" sequences inside the refresh_token instead of truncating it.
    parts = raw.split(SEP, 3)
    if len(parts) != 4:
        raise ValueError("expected 4 fields separated by '----'")

    email, password, client_id, refresh_token = parts
    email = email.strip()
    client_id = client_id.strip()
    # Do NOT strip the refresh_token internally; only the surrounding line was
    # stripped. This avoids mangling a token that legitimately contains chars.
    if not email or not refresh_token:
        raise ValueError("email and refresh_token are required")

    return Account(
        email=email,
        password=password,
        client_id=client_id,
        refresh_token=refresh_token,
    )


def parse_text(text: str) -> Tuple[List[Account], List[str]]:
    """Parse a multi-line blob. Returns (accounts, errors).

    Blank lines and lines starting with '#' are ignored. Duplicate emails
    within the same blob keep the last occurrence.
    """
    accounts: List[Account] = []
    errors: List[str] = []
    by_email = {}

    for idx, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            account = parse_line(stripped)
        except ValueError as exc:
            errors.append(f"line {idx}: {exc}")
            continue
        by_email[account.email.lower()] = account

    accounts = list(by_email.values())
    return accounts, errors
