"""Email pool backed by ``emails.txt`` + the ``email_usage`` table.

If the API request supplies an explicit email it is used as-is. Otherwise the
next email from ``emails.txt`` that is not already ``in_use`` / ``used`` /
``registered`` is handed out (in file order).

Email lifecycle (status in ``email_usage``):
    (none) -> in_use            picked for a register/register+import job
    in_use -> registered        web registration succeeded (NOT yet in Sub2API)
    registered -> used          Sub2API import succeeded (fully consumed)
    in_use/registered -> used   (used == registered AND imported)
    * -> failed                 step failed BEFORE the account exists -> retryable
    * -> deferred               mailbox is temporarily unreachable; retry later
    * -> unavailable            mailbox is permanently unusable

``registered`` mailboxes already have a ChatGPT account, so they are never
re-registered; instead they are recovered via the import-only *resume* path
(see :func:`list_resumable_emails`).
"""
from __future__ import annotations

import logging
from typing import List, Optional

from . import database
from .config import config

log = logging.getLogger(__name__)


class EmailPoolError(RuntimeError):
    pass


def _read_email_file() -> List[str]:
    path = config.emails_path
    if not path.exists():
        raise EmailPoolError(
            f"emails file not found: {path} (copy emails.example.txt to {config.emails_file})"
        )
    emails: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        emails.append(line)
    return emails


def _sub2api_imported_emails() -> set:
    """Emails already imported into Sub2API (best-effort; empty set on any error)."""
    try:
        from . import sub2api_client
        return sub2api_client.existing_account_emails()
    except Exception as exc:
        log.warning("could not query Sub2API accounts (skip-already-imported is best-effort): %s", exc)
        return set()


def get_next_email(explicit: Optional[str] = None) -> str:
    """Return an email to register with.

    Emails already imported into Sub2API (by this tool or manually) are never
    reused. ``explicit`` (from the API request) takes priority but is also
    rejected if it is already a Sub2API account.
    """
    imported = _sub2api_imported_emails()

    if explicit:
        e = explicit.strip()
        if e.lower() in imported:
            raise EmailPoolError(f"邮箱 {e} 已导入 Sub2API，不再重复使用")
        return e

    emails = _read_email_file()
    if not emails:
        raise EmailPoolError("emails.txt is empty")

    unavailable = database.get_unavailable_emails()
    for email in emails:
        if email in unavailable:
            continue
        if email.strip().lower() in imported:
            # already a Sub2API account -> cache locally as 'used' and skip
            database.set_email_status(email, "used", last_error="already imported into Sub2API")
            log.info("skip %s: already imported into Sub2API", email)
            continue
        return email

    raise EmailPoolError("emails.txt 中没有可用邮箱（都在 in_use/used 或已导入 Sub2API）")


def mark_in_use(email: str, job_id: str) -> None:
    database.set_email_status(email, "in_use", last_job_id=job_id)


def mark_registered(email: str, job_id: str) -> None:
    """Web registration succeeded but the account is NOT yet in Sub2API.

    Such mailboxes are not handed out for a fresh registration, but they ARE
    recoverable via the import-only resume path (:func:`list_resumable_emails`).
    """
    database.set_email_status(email, "registered", last_job_id=job_id)


def mark_used(email: str, job_id: str) -> None:
    """Fully consumed: registered AND imported into Sub2API."""
    database.set_email_status(email, "used", last_job_id=job_id)
    # Move the account line out of accounts.txt -> accounts_imported.txt and drop
    # from emails.txt, so project A stops polling this (now done) mailbox.
    from . import account_archive
    account_archive.archive_account(email, "imported")


def mark_failed(email: str, job_id: str, error: str) -> None:
    # 'failed' emails become available again for a retry.
    database.set_email_status(email, "failed", last_job_id=job_id, last_error=(error or "")[:500])


def mark_deferred(email: str, job_id: str, error: str) -> None:
    """Temporarily unusable mailbox (e.g. Outlook IMAP says authenticated but
    not connected). It is removed from the active pool for this run, but kept
    separate from permanent failures so it can be reviewed and restored later."""
    database.set_email_status(email, "deferred", last_job_id=job_id, last_error=(error or "")[:500])
    log.warning("email %s deferred for later retry: %s", email, (error or "")[:200])
    from . import account_archive
    account_archive.archive_account(email, "deferred")


def mark_unavailable(email: str, job_id: str, error: str) -> None:
    """The mailbox can never receive a code (dead refresh_token / service-abuse
    mode). It is permanently skipped: never handed out for a fresh registration
    and never resumed. This is terminal, unlike 'failed' (which is retryable)."""
    database.set_email_status(email, "unavailable", last_job_id=job_id, last_error=(error or "")[:500])
    log.warning("email %s marked UNAVAILABLE: %s", email, (error or "")[:200])
    # Move the account line out of accounts.txt -> accounts_failed.txt and drop
    # from emails.txt, so project A stops polling this dead mailbox.
    from . import account_archive
    account_archive.archive_account(email, "failed")


def get_email_usage(email: str) -> Optional[dict]:
    """Current lifecycle row for ``email`` ({status,last_job_id,last_error,...}) or None."""
    return database.get_email_status((email or "").strip())


def is_email_used_or_imported(email: str) -> bool:
    """True if ``email`` is fully consumed: locally 'used' OR already a Sub2API account."""
    e = (email or "").strip()
    if not e:
        return False
    row = database.get_email_status(e)
    if row and row.get("status") == "used":
        return True
    return e.lower() in _sub2api_imported_emails()


def list_registered_emails(limit: Optional[int] = None) -> List[str]:
    """Resumable mailboxes (alias of :func:`list_resumable_emails`), optionally capped."""
    emails = list_resumable_emails()
    if limit is not None and limit >= 0:
        emails = emails[:limit]
    return emails


def list_resumable_emails() -> List[str]:
    """Emails registered on the site but NOT yet imported into Sub2API.

    Any ``registered`` mailbox that turns out to already be a Sub2API account
    (e.g. imported manually) is reconciled to ``used`` and excluded.
    """
    imported = _sub2api_imported_emails()
    out: List[str] = []
    for email in database.get_emails_by_status("registered"):
        if email.strip().lower() in imported:
            database.set_email_status(email, "used", last_error="already imported into Sub2API")
            log.info("reconcile %s: already imported into Sub2API -> used", email)
            continue
        out.append(email)
    return out


def list_fresh_emails() -> List[str]:
    """Emails from emails.txt that are available for a FRESH registration, in
    file order: not in_use / used / registered / unavailable / deferred, and
    not already a Sub2API account. (Mirrors :func:`get_next_email` but returns
    every match.)"""
    try:
        emails = _read_email_file()
    except EmailPoolError:
        return []
    imported = _sub2api_imported_emails()
    unavailable = database.get_unavailable_emails()
    out: List[str] = []
    for email in emails:
        if email in unavailable:
            continue
        if email.strip().lower() in imported:
            database.set_email_status(email, "used", last_error="already imported into Sub2API")
            continue
        out.append(email)
    return out
