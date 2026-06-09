"""Move processed mailbox account lines out of ``accounts.txt`` into archive files.

When a mailbox is fully imported (``used``), permanently fails (``unavailable``),
or is temporarily deferred (``deferred``),
its full account line (``email----password----client_id----refresh_token``) is
moved OUT of ``accounts.txt`` into ``accounts_imported.txt`` /
``accounts_failed.txt`` / ``accounts_deferred.txt``, and the bare email is
dropped from ``emails.txt``.

This keeps ``accounts.txt`` (and ``emails.txt``) as the ACTIVE set so project A's
poller only polls live mailboxes -- far less OAuth/IMAP load on Microsoft (which
also reduces the chance of accounts being flagged into service-abuse mode).

All operations are best-effort and never raise into the caller; writes are atomic
(temp file + ``os.replace``) so a concurrent reader (project A) never sees a
half-written file.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import List

from .config import config

log = logging.getLogger(__name__)

SEP = "----"


def _email_of(line: str) -> str:
    """First '----' field of an account line; for emails.txt (no SEP) it's the line."""
    return line.split(SEP, 1)[0].strip()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _remove_email_lines(path: Path, email: str) -> List[str]:
    """Remove non-comment lines whose first field == ``email`` (case-insensitive).

    Returns the removed lines (verbatim). Comments / blank lines are preserved.
    """
    if not path.exists():
        return []
    removed: List[str] = []
    kept: List[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s and not s.startswith("#") and _email_of(s).lower() == email.lower():
            removed.append(s)
        else:
            kept.append(raw)
    if removed:
        _atomic_write(path, "\n".join(kept) + ("\n" if kept else ""))
    return removed


def _append_unique(path: Path, lines: List[str]) -> None:
    """Append ``lines`` to ``path``, skipping any whose email is already present."""
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            s = raw.strip()
            if s and not s.startswith("#"):
                existing.add(_email_of(s).lower())
    to_add = [ln for ln in lines if _email_of(ln).lower() not in existing]
    if not to_add:
        return
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        for ln in to_add:
            fh.write(ln.rstrip("\n") + "\n")


def archive_account(email: str, kind: str) -> None:
    """Move ``email``'s account line from accounts.txt into the archive for
    ``kind`` ('imported', 'failed', or 'deferred'), and drop the email from
    emails.txt.

    Best-effort: logs and swallows any error (archiving must never break import).
    """
    email = (email or "").strip()
    if not email:
        return
    try:
        if kind == "imported":
            dest = config.accounts_imported_path
        elif kind == "deferred":
            dest = config.accounts_deferred_path
        else:
            dest = config.accounts_failed_path
        removed = _remove_email_lines(config.accounts_source_path, email)
        if removed:
            _append_unique(dest, removed)
        elif not dest.exists():
            dest.touch()
        # emails.txt lists the bare email per line -> drop it from the active pool.
        _remove_email_lines(config.emails_path, email)
        log.info("archived %s -> %s (%d account line(s) moved)", email, dest.name, len(removed))
    except Exception as exc:  # never let archiving break the import flow
        log.warning("archive_account(%s, %s) failed (ignored): %s", email, kind, str(exc)[:160])
