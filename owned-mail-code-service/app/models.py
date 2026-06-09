"""Internal data classes and API request models."""
from dataclasses import dataclass

from pydantic import BaseModel


@dataclass
class Account:
    """A mailbox account. `password` is stored for reference/compat only;
    authentication uses the OAuth2 refresh_token, not the password."""

    email: str
    password: str
    client_id: str
    refresh_token: str


class ImportRequest(BaseModel):
    text: str


class CheckRequest(BaseModel):
    """POST /api/accounts/check - probe one mailbox's health (OAuth + IMAP)."""

    email: str


class CodeRequest(BaseModel):
    email: str
    timeout: int = 180
    # Empty -> resolved to settings.DEFAULT_CODE_PATTERN by the endpoint.
    pattern: str = ""
    subject_keyword: str = ""
    from_keyword: str = ""
    # Only accept codes from messages first seen at/after this epoch second.
    # 0 (default) = no freshness filter (return newest matching stored code).
    since: int = 0
