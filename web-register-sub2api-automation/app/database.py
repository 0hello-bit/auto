"""SQLite persistence layer.

A fresh short-lived connection is opened per operation. This keeps things
thread-safe (the Playwright workers run in the asyncio loop while blocking
HTTP/DB calls are dispatched via ``asyncio.to_thread``) without sharing a
single connection across threads.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional

from .config import config

# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS register_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL,
    email TEXT NOT NULL,
    name TEXT,
    age INTEGER,
    status TEXT NOT NULL,
    email_verification_code TEXT,
    phone_number TEXT,
    sms_provider TEXT,
    sms_country TEXT,
    sms_product TEXT,
    sms_operator TEXT,
    sms_operator_strategy TEXT,
    sms_order_id TEXT,
    sms_price REAL,
    sms_success_rate REAL,
    sms_verification_code TEXT,
    error_message TEXT,
    screenshot_path TEXT,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    finished_at INTEGER
);

CREATE TABLE IF NOT EXISTS sub2api_import_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT UNIQUE NOT NULL,
    register_job_id TEXT,
    email TEXT,
    sub2api_session_id TEXT,
    auth_url TEXT,
    expected_state TEXT,
    callback_url TEXT,
    code TEXT,
    state TEXT,
    group_ids TEXT,
    concurrency INTEGER,
    priority INTEGER,
    status TEXT NOT NULL,
    sub2api_account_id INTEGER,
    error_message TEXT,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    finished_at INTEGER
);

CREATE TABLE IF NOT EXISTS registered_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    age INTEGER,
    phone_number TEXT,
    sms_provider TEXT,
    sms_order_id TEXT,
    source_register_job_id TEXT,
    source_import_job_id TEXT,
    sub2api_account_id INTEGER,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS email_usage (
    email TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    last_job_id TEXT,
    last_error TEXT,
    updated_at INTEGER NOT NULL
);
"""

# Columns allowed to be updated via update_* helpers (job_id is the key).
_REGISTER_COLUMNS = {
    "url", "email", "name", "age", "status", "email_verification_code",
    "phone_number", "sms_provider", "sms_country", "sms_product", "sms_operator",
    "sms_operator_strategy", "sms_order_id", "sms_price", "sms_success_rate",
    "sms_verification_code", "error_message", "screenshot_path",
    "started_at", "finished_at",
}

# Columns added after the initial release; applied to existing DBs on startup.
_REGISTER_MIGRATIONS = {
    "sms_country": "TEXT",
    "sms_product": "TEXT",
    "sms_operator": "TEXT",
    "sms_operator_strategy": "TEXT",
    "sms_price": "REAL",
    "sms_success_rate": "REAL",
}
_IMPORT_COLUMNS = {
    "register_job_id", "email", "sub2api_session_id", "auth_url",
    "expected_state", "callback_url", "code", "state", "group_ids",
    "concurrency", "priority", "status", "sub2api_account_id",
    "error_message", "started_at", "finished_at",
}


def now() -> int:
    return int(time.time())


@contextmanager
def _connect():
    conn = sqlite3.connect(str(config.db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    config.ensure_dirs()
    with _connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial release to existing tables."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(register_jobs)").fetchall()}
    for column, col_type in _REGISTER_MIGRATIONS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE register_jobs ADD COLUMN {column} {col_type}")


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


# --------------------------------------------------------------------------- #
# register_jobs
# --------------------------------------------------------------------------- #
def create_register_job(
    job_id: str,
    url: str,
    email: str,
    name: Optional[str] = None,
    age: Optional[int] = None,
    status: str = "pending",
) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO register_jobs (job_id, url, email, name, age, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (job_id, url, email, name, age, status, now()),
        )


def update_register_job(job_id: str, **fields: Any) -> None:
    _update("register_jobs", _REGISTER_COLUMNS, job_id, fields)


def get_register_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM register_jobs WHERE job_id = ?", (job_id,)).fetchone()
    return _row_to_dict(row)


# --------------------------------------------------------------------------- #
# sub2api_import_jobs
# --------------------------------------------------------------------------- #
def create_import_job(
    job_id: str,
    email: Optional[str] = None,
    register_job_id: Optional[str] = None,
    group_ids: Optional[Iterable[int]] = None,
    concurrency: Optional[int] = None,
    priority: Optional[int] = None,
    status: str = "pending",
) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO sub2api_import_jobs
                 (job_id, register_job_id, email, group_ids, concurrency, priority, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                register_job_id,
                email,
                json.dumps(list(group_ids)) if group_ids is not None else None,
                concurrency,
                priority,
                status,
                now(),
            ),
        )


def update_import_job(job_id: str, **fields: Any) -> None:
    if "group_ids" in fields and fields["group_ids"] is not None and not isinstance(fields["group_ids"], str):
        fields["group_ids"] = json.dumps(list(fields["group_ids"]))
    _update("sub2api_import_jobs", _IMPORT_COLUMNS, job_id, fields)


def get_import_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM sub2api_import_jobs WHERE job_id = ?", (job_id,)).fetchone()
    return _row_to_dict(row)


def _update(table: str, allowed: set, job_id: str, fields: Dict[str, Any]) -> None:
    columns = {k: v for k, v in fields.items() if k in allowed}
    if not columns:
        return
    assignments = ", ".join(f"{col} = ?" for col in columns)
    values = list(columns.values()) + [job_id]
    with _connect() as conn:
        conn.execute(f"UPDATE {table} SET {assignments} WHERE job_id = ?", values)


# --------------------------------------------------------------------------- #
# registered_accounts
# --------------------------------------------------------------------------- #
def upsert_registered_account(
    email: str,
    name: Optional[str] = None,
    age: Optional[int] = None,
    phone_number: Optional[str] = None,
    sms_provider: Optional[str] = None,
    sms_order_id: Optional[str] = None,
    source_register_job_id: Optional[str] = None,
    source_import_job_id: Optional[str] = None,
    sub2api_account_id: Optional[int] = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO registered_accounts
                 (email, name, age, phone_number, sms_provider, sms_order_id,
                  source_register_job_id, source_import_job_id, sub2api_account_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(email) DO UPDATE SET
                  name=excluded.name,
                  age=excluded.age,
                  phone_number=excluded.phone_number,
                  sms_provider=excluded.sms_provider,
                  sms_order_id=excluded.sms_order_id,
                  source_register_job_id=excluded.source_register_job_id,
                  source_import_job_id=excluded.source_import_job_id,
                  sub2api_account_id=excluded.sub2api_account_id""",
            (
                email, name, age, phone_number, sms_provider, sms_order_id,
                source_register_job_id, source_import_job_id, sub2api_account_id, now(),
            ),
        )


def list_accounts(limit: int = 200) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM registered_accounts ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# email_usage
# --------------------------------------------------------------------------- #
def set_email_status(
    email: str,
    status: str,
    last_job_id: Optional[str] = None,
    last_error: Optional[str] = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO email_usage (email, status, last_job_id, last_error, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(email) DO UPDATE SET
                  status=excluded.status,
                  last_job_id=excluded.last_job_id,
                  last_error=excluded.last_error,
                  updated_at=excluded.updated_at""",
            (email, status, last_job_id, last_error, now()),
        )


def get_email_status(email: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM email_usage WHERE email = ?", (email,)).fetchone()
    return _row_to_dict(row)


def get_unavailable_emails() -> set:
    """Emails that should NOT be handed out for a FRESH registration.

    ``registered`` is included: those mailboxes already have a ChatGPT account
    (registration succeeded) so re-registering them would just bounce to a login.
    They are recovered via the import-only resume path, not by re-registration.

    ``unavailable`` is included: the mailbox can never authenticate again (dead
    refresh_token / AADSTS70000 service-abuse mode), so it is permanently skipped.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT email FROM email_usage WHERE status IN ('in_use', 'used', 'registered', 'unavailable', 'deferred')"
        ).fetchall()
    return {r["email"] for r in rows}


def get_emails_by_status(*statuses: str) -> List[str]:
    """Return the emails currently in any of ``statuses`` (file order not preserved)."""
    if not statuses:
        return []
    placeholders = ",".join("?" for _ in statuses)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT email FROM email_usage WHERE status IN ({placeholders})", tuple(statuses)
        ).fetchall()
    return [r["email"] for r in rows]


def list_email_usage() -> List[Dict[str, Any]]:
    """All tracked mailboxes with their current status, newest change first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT email, status, last_job_id, last_error, updated_at "
            "FROM email_usage ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# combined job views
# --------------------------------------------------------------------------- #
def list_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    """Recent jobs from both tables, newest first, tagged with ``type``."""
    out: List[Dict[str, Any]] = []
    with _connect() as conn:
        for row in conn.execute(
            "SELECT * FROM register_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall():
            d = dict(row)
            d["type"] = "register"
            out.append(d)
        for row in conn.execute(
            "SELECT * FROM sub2api_import_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall():
            d = dict(row)
            d["type"] = "import"
            out.append(d)
    out.sort(key=lambda d: d.get("created_at") or 0, reverse=True)
    return out[:limit]


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Find a job by id in either table."""
    reg = get_register_job(job_id)
    if reg is not None:
        reg["type"] = "register"
        return reg
    imp = get_import_job(job_id)
    if imp is not None:
        imp["type"] = "import"
        return imp
    return None
