"""Client for Project A - the email verification code service.

Project B does NOT do any IMAP / mailbox polling itself. It only calls
Project A's HTTP API to obtain the 6-digit code for a given mailbox.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

from .config import config, proxies_for

log = logging.getLogger(__name__)


class MailCodeError(RuntimeError):
    pass


class MailboxUnavailableError(MailCodeError):
    """The mailbox itself can never receive a code (its OAuth refresh_token is
    revoked / the Outlook account is in AADSTS70000 service-abuse mode), as
    reported by Project A (HTTP 409). This is distinct from a transient 408
    timeout: the caller should mark the email 'unavailable' and stop retrying
    it, rather than 'failed' (which would be handed out again)."""


class MailboxDeferredError(MailCodeError):
    """The mailbox is temporarily unreachable by IMAP/OAuth. Move it out of the
    active pool for this run, but keep it separate from permanent failures."""


# Substrings in Project A's response that mean the mailbox is permanently dead.
_UNAVAILABLE_MARKERS = (
    "mailbox unavailable",
    "mailbox_unavailable",
    "invalid_grant",
    "service abuse",
    "abuse mode",
    "aadsts70000",
)


_DEFERRED_MARKERS = (
    "mailbox deferred",
    "mailbox_deferred",
    "user is authenticated but not connected",
    "xoauth2 auth failed: user is authenticated but not connected",
)


def _looks_unavailable(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _UNAVAILABLE_MARKERS)


def _looks_deferred(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _DEFERRED_MARKERS)


def get_verification_code(
    email: str,
    timeout: int = 180,
    pattern: str = r"\b\d{6}\b",
    subject_keyword: str = "",
    from_keyword: str = "",
    since: int = 0,
) -> str:
    """Fetch the email verification code for ``email`` from Project A.

    The mail-code service blocks server-side until a matching code arrives or
    ``timeout`` seconds elapse, so the HTTP read timeout is set generously
    above ``timeout``.

    ``since`` (epoch seconds): when > 0, Project A only returns a code from an
    email first seen at/after that time -- pass the moment right before the
    site sends the email so a stale stored code is never returned.
    """
    url = f"{config.mail_code_service_base.rstrip('/')}/api/code"
    headers = {
        "x-api-key": config.mail_code_service_api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "email": email,
        "timeout": timeout,
        "pattern": pattern,
        "subject_keyword": subject_keyword,
        "from_keyword": from_keyword,
        "since": since,
    }

    log.info("Requesting email code for %s (timeout=%ss, since=%s)", email, timeout, since)
    try:
        resp = requests.post(
            url, json=payload, headers=headers, timeout=timeout + 30, proxies=proxies_for(url)
        )
    except requests.RequestException as exc:
        raise MailCodeError(f"mail-code service request failed: {exc}") from exc

    if resp.status_code != 200:
        # 409 == Project A says the mailbox can never authenticate (dead
        # refresh_token / service-abuse mode) -> a distinct, non-retryable error.
        if resp.status_code == 409 or _looks_unavailable(resp.text):
            raise MailboxUnavailableError(
                f"mailbox {email} unavailable: {resp.text[:300]}"
            )
        if resp.status_code == 423 or _looks_deferred(resp.text):
            raise MailboxDeferredError(
                f"mailbox {email} deferred: {resp.text[:300]}"
            )
        raise MailCodeError(
            f"mail-code service returned HTTP {resp.status_code}: {resp.text[:300]}"
        )

    try:
        body = resp.json()
    except ValueError as exc:
        raise MailCodeError(f"mail-code service returned non-JSON: {resp.text[:300]}") from exc

    if body.get("code") != 1:
        msg = body.get("msg") or body
        if _looks_unavailable(str(msg)):
            raise MailboxUnavailableError(f"mailbox {email} unavailable: {msg}")
        if _looks_deferred(str(msg)):
            raise MailboxDeferredError(f"mailbox {email} deferred: {msg}")
        raise MailCodeError(f"mail-code service error: {msg}")

    data = body.get("data") or {}
    code: Optional[str] = data.get("verification_code")
    if not code:
        raise MailCodeError(f"mail-code service returned no verification_code: {body}")

    log.info("Received email code for %s", email)
    return str(code).strip()
