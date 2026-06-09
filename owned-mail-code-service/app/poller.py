"""Background polling of all mailboxes, plus on-demand single-mailbox polling
and code lookup used by the /api/code endpoint.
"""
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, Tuple

from . import database
from .code_extractor import extract_from_parts
from .config import settings
from .imap_client import fetch_recent_raw
from .mail_parser import parse_message
from .microsoft_oauth import get_access_token
from .models import Account

log = logging.getLogger(__name__)

# Per-account locks ensure the same mailbox is never polled concurrently by the
# background thread and an on-demand /api/code request.
_account_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

_stop_event = threading.Event()
_thread: Optional[threading.Thread] = None
_imap_gate = threading.Lock()
_last_imap_poll_at = 0.0
_active_code_requests = 0
_code_requests_guard = threading.Lock()


def _imap_wait_turn() -> None:
    """Throttle global IMAP fetches across all mailboxes."""
    min_gap = max(0, settings.IMAP_MIN_INTERVAL_SECONDS)
    if min_gap <= 0:
        return
    wait = (_last_imap_poll_at + min_gap) - time.monotonic()
    if wait > 0:
        time.sleep(wait)


@contextmanager
def code_request_scope() -> Iterator[None]:
    """Mark that a /api/code caller is actively waiting for a mailbox."""
    global _active_code_requests
    with _code_requests_guard:
        _active_code_requests += 1
    try:
        yield
    finally:
        with _code_requests_guard:
            _active_code_requests = max(0, _active_code_requests - 1)


def has_active_code_requests() -> bool:
    with _code_requests_guard:
        return _active_code_requests > 0


def _lock_for(email: str) -> threading.Lock:
    with _locks_guard:
        lock = _account_locks.get(email)
        if lock is None:
            lock = threading.Lock()
            _account_locks[email] = lock
        return lock


def _fetch_and_store(account: Account, access_token: str) -> int:
    raws = fetch_recent_raw(account.email, access_token,
                            limit=settings.LATEST_MAIL_LIMIT)
    stored = 0
    for raw in raws:
        try:
            parsed = parse_message(raw)
        except Exception as exc:
            log.warning("failed to parse a message for %s: %s", account.email, exc)
            continue
        code = extract_from_parts(parsed.subject, parsed.text_body,
                                  parsed.html_body, settings.DEFAULT_CODE_PATTERN)
        inserted = database.insert_message(
            account.email, parsed.message_id, parsed.from_addr, parsed.subject,
            parsed.text_body, parsed.html_body, parsed.date_raw, code,
        )
        if inserted:
            stored += 1
    return stored


def poll_account(account: Account) -> Tuple[bool, Optional[str]]:
    """Poll one mailbox. Returns (ok, error_message)."""
    global _last_imap_poll_at
    lock = _lock_for(account.email)
    with lock:
        with _imap_gate:
            _imap_wait_turn()
            try:
                token = get_access_token(account)
                try:
                    stored = _fetch_and_store(account, token)
                except Exception:
                    # The cached token may be stale; force a refresh and retry once.
                    token = get_access_token(account, force=True)
                    stored = _fetch_and_store(account, token)
                database.update_poll_status(account.email, int(time.time()), "")
                log.info("polled %s: %d new message(s)", account.email, stored)
                return True, None
            except Exception as exc:
                message = str(exc)
                database.update_poll_status(account.email, int(time.time()), message)
                log.warning("poll failed for %s: %s", account.email, message)
                return False, message
            finally:
                _last_imap_poll_at = time.monotonic()


def poll_email(email: str) -> Tuple[bool, Optional[str]]:
    account = database.get_account(email)
    if not account:
        return False, "account not found"
    return poll_account(account)


def _active_emails() -> Optional[set]:
    """Lowercased emails currently listed in ``accounts.txt`` (the ACTIVE set).

    Returns ``None`` if the file can't be read -- then we fall back to polling
    every account in the DB (previous behaviour). Re-read each cycle so that when
    the orchestrator (project B) moves a done/failed account out of accounts.txt,
    the poller stops polling it on the very next cycle (no restart needed).
    """
    path = settings.ACCOUNTS_FILE
    if not path or not os.path.exists(path):
        return None
    try:
        out = set()
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                email = line.split("----", 1)[0].strip()
                if email:
                    out.add(email.lower())
        return out
    except OSError:
        return None


def poll_all() -> dict:
    accounts = database.get_accounts()
    # Only poll mailboxes still listed in accounts.txt (skip already-imported /
    # failed ones that project B moved into the archive files). This keeps the
    # OAuth/IMAP load proportional to the ACTIVE pool, not the whole DB history.
    active = _active_emails()
    if active is not None:
        before = len(accounts)
        accounts = [a for a in accounts if a.email.lower() in active]
        if before != len(accounts):
            log.debug("poll scope: %d/%d accounts active (accounts.txt)", len(accounts), before)
    ok = 0
    failed = []
    for account in accounts:
        if has_active_code_requests():
            log.debug("poll cycle paused: /api/code request is active")
            break
        success, _ = poll_account(account)
        if success:
            ok += 1
        else:
            failed.append(account.email)
    return {"ok": ok, "failed": failed}


def _keyword_list(raw: str) -> List[str]:
    """Split a keyword string into an OR-list (comma / fullwidth-comma / ；、)."""
    return [k.strip().lower() for k in re.split(r"[,，;；、]", raw or "") if k.strip()]


# Substrings that mark a PERMANENT mailbox failure (the refresh_token is dead /
# the account is blocked), as opposed to a transient hiccup (throttling, a
# dropped connection, a slow message). When the mailbox can never authenticate
# again there is no point polling until the deadline -- /api/code surfaces this
# immediately so the caller can mark the mailbox unavailable instead of retrying.
_PERMANENT_MAILBOX_ERROR_MARKERS = (
    "invalid_grant",          # refresh_token revoked / expired
    "abuse",                  # AADSTS70000 "User account is found to be in service abuse mode"
    "aadsts70000",
    "aadsts50034",            # account does not exist
    "aadsts700016",           # application/client not found
    "unauthorized_client",
    "account is locked",
    "account is disabled",
    "consent_required",
)


def is_permanent_mailbox_error(message: Optional[str]) -> bool:
    """True if ``message`` indicates the mailbox can never authenticate again."""
    if not message:
        return False
    low = message.lower()
    return any(marker in low for marker in _PERMANENT_MAILBOX_ERROR_MARKERS)


_TRANSIENT_MAILBOX_ERROR_MARKERS = (
    "user is authenticated but not connected",
    "xoauth2 auth failed: user is authenticated but not connected",
)


def is_transient_mailbox_error(message: Optional[str]) -> bool:
    """True if IMAP/OAuth accepted auth but the mailbox is temporarily unusable."""
    if not message:
        return False
    low = message.lower()
    return any(marker in low for marker in _TRANSIENT_MAILBOX_ERROR_MARKERS)


def find_code(email: str, pattern: str, subject_keyword: str = "",
              from_keyword: str = "", limit: int = 30, since: int = 0,
              since_grace: int = 0) -> Optional[str]:
    """Look for a verification code in already-stored messages (newest first).

    ``since`` (epoch seconds): when > 0, only messages first stored at/after that
    time are considered. ``since_grace`` widens that lower bound by a small
    number of seconds for timeout recovery only. This prevents returning a
    stale code from an older attempt while still catching messages that a
    background poll stored just before the caller's send timestamp.

    ``subject_keyword`` / ``from_keyword`` may each be a single keyword OR a
    comma-separated OR-list (e.g. ``"ChatGPT,OpenAI"``): a message matches if it
    contains ANY of the listed keywords. This is important because ChatGPT signup
    codes arrive with a "ChatGPT" subject while OAuth *login* codes arrive with an
    "OpenAI" subject -- both must be accepted.
    """
    rows = database.get_recent_messages(email, limit)
    subject_kws = _keyword_list(subject_keyword)
    from_kws = _keyword_list(from_keyword)
    strict_threshold = max(0, since) if since else 0
    fallback_threshold = max(0, since - max(0, since_grace)) if since else 0
    for row in rows:
        # Prefer the message's own Date header over created_at. created_at is
        # when Project A first fetched/stored the email; an old code can be
        # fetched for the first time during a new attempt and would otherwise
        # look fresh. If Date is missing/unparseable, fall back to created_at.
        #
        # The timeout-recovery grace is ONLY safe for created_at fallback. When
        # message_ts exists, widening the threshold can re-admit an actually old
        # OpenAI code that arrived shortly before the new send click.
        message_ts = row.get("message_ts") or 0
        if message_ts:
            freshness_ts = message_ts
            threshold = strict_threshold
        else:
            freshness_ts = row.get("created_at") or 0
            threshold = fallback_threshold
        if threshold and freshness_ts < threshold:
            log.debug(
                "skip stale code candidate for %s: message_ts=%s created_at=%s threshold=%s subject=%r",
                email, row.get("message_ts"), row.get("created_at"), threshold, row.get("subject"),
            )
            continue
        subject = (row.get("subject") or "").lower()
        from_addr = (row.get("from_addr") or "").lower()
        if subject_kws and not any(kw in subject for kw in subject_kws):
            continue
        if from_kws and not any(kw in from_addr for kw in from_kws):
            continue
        code = extract_from_parts(row.get("subject") or "", row.get("body") or "",
                                  row.get("html_body") or "", pattern)
        if code:
            return code
    return None


# --------------------------------------------------------------------------- #
# background thread
# --------------------------------------------------------------------------- #
def _loop() -> None:
    log.info("background poller started (interval=%ss)", settings.POLL_INTERVAL_SECONDS)
    while not _stop_event.is_set():
        try:
            result = poll_all()
            if result["failed"]:
                log.info("poll cycle done: ok=%d failed=%d",
                         result["ok"], len(result["failed"]))
        except Exception:
            log.exception("unexpected error during poll cycle")
        _stop_event.wait(settings.POLL_INTERVAL_SECONDS)


def start_background_poller() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="mail-poller", daemon=True)
    _thread.start()


def stop_background_poller() -> None:
    _stop_event.set()
    log.info("background poller stop requested")
