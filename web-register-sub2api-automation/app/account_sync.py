"""Sync the mailbox pool file (``emails.txt``) from project A's ``accounts.txt``.

Project A (``owned-mail-code-service``) holds the authoritative mailbox list in
``accounts.txt`` (``email----password----client_id----refresh_token`` per line).
This module extracts just the email from every account line and rewrites
``emails.txt`` with only the mailboxes that still need onboarding -- i.e. those
NOT already imported into Sub2API and not locally ``used`` / ``unavailable`` /
``deferred`` -- preserving the source order. So ``emails.txt`` only ever lists
mailboxes that still have to be processed now.
"""
from __future__ import annotations

import logging
from typing import Dict, List

from . import account_archive, database, email_pool
from .config import config

log = logging.getLogger(__name__)

SEP = "----"

_HEADER = (
    "# 邮箱池 / Email pool  (auto-generated -- do NOT edit by hand)\n"
    "# 由 /api/emails/sync 从 owned-mail-code-service/accounts.txt 自动生成。\n"
    "# 只保留「还未接入 Sub2API」的邮箱，每行一个；重跑同步即可刷新。\n"
    "# Generated from accounts.txt; only mailboxes NOT yet imported into Sub2API are kept.\n"
)


class AccountSyncError(RuntimeError):
    pass


def _emails_from_accounts_file() -> List[str]:
    """Ordered, de-duplicated emails parsed from the accounts source file."""
    path = config.accounts_source_path
    if not path.exists():
        raise AccountSyncError(
            f"accounts source file not found: {path} "
            f"(set ACCOUNTS_SOURCE_FILE to your owned-mail-code-service/accounts.txt)"
        )
    out: List[str] = []
    seen = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        email = line.split(SEP, 1)[0].strip()
        if not email or "@" not in email:
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(email)
    return out


def sync_emails_file() -> Dict:
    """Rewrite ``emails.txt`` from the accounts source file.

    Returns a report: how many emails were in the source, which were kept, and
    which were skipped (with the reason: already imported / used / unavailable /
    deferred). Best-effort about Sub2API: if it is unreachable, no email is
    skipped on the "already imported" rule (the set comes back empty).
    """
    accounts_emails = _emails_from_accounts_file()
    imported = email_pool._sub2api_imported_emails()  # lowercased set; {} on error

    kept: List[str] = []
    skipped: List[Dict[str, str]] = []
    for email in accounts_emails:
        status = (database.get_email_status(email) or {}).get("status")
        reason = None
        if email.lower() in imported:
            reason = "imported_into_sub2api"
        elif status == "used":
            reason = "used"
        elif status == "unavailable":
            reason = "unavailable"
        elif status == "deferred":
            reason = "deferred"

        if reason:
            skipped.append({"email": email, "reason": reason})
            # Keep local bookkeeping consistent with Sub2API.
            if reason == "imported_into_sub2api" and status != "used":
                database.set_email_status(email, "used", last_error="already imported into Sub2API")
            # Also keep project A's accounts.txt as the active set. Account
            # lines that should not be polled/issued any more are archived with
            # their full credentials, while registered/failed/fresh mailboxes
            # remain available for resume/retry.
            if reason in ("imported_into_sub2api", "used"):
                account_archive.archive_account(email, "imported")
            elif reason == "deferred":
                account_archive.archive_account(email, "deferred")
            elif reason == "unavailable":
                account_archive.archive_account(email, "failed")
            continue
        kept.append(email)

    config.emails_path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(kept)
    config.emails_path.write_text(_HEADER + body + ("\n" if kept else ""), encoding="utf-8")

    log.info(
        "synced emails.txt: %d/%d kept, %d skipped (source=%s)",
        len(kept), len(accounts_emails), len(skipped), config.accounts_source_path,
    )
    return {
        "source": str(config.accounts_source_path),
        "emails_file": str(config.emails_path),
        "total_in_source": len(accounts_emails),
        "kept_count": len(kept),
        "kept": kept,
        "skipped_count": len(skipped),
        "skipped": skipped,
        "sub2api_reachable": bool(imported),
    }
