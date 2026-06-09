"""SQLite persistence layer.

A fresh connection is opened per operation, which keeps the module safe to use
from both the FastAPI request threadpool and the background poller thread.
WAL mode + a busy timeout handle concurrent writers gracefully.
"""
import logging
import os
import sqlite3
import threading
import time
from email.utils import parsedate_to_datetime
from typing import List, Optional

from .config import settings
from .models import Account

log = logging.getLogger(__name__)

_init_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    email TEXT PRIMARY KEY,
    password TEXT,
    client_id TEXT,
    refresh_token TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    message_id TEXT,
    from_addr TEXT,
    subject TEXT,
    body TEXT,
    html_body TEXT,
    date_raw TEXT,
    message_ts INTEGER,
    code TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(email, message_id)
);

CREATE TABLE IF NOT EXISTS poll_status (
    email TEXT PRIMARY KEY,
    last_poll_at INTEGER,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_email_created
    ON messages(email, created_at DESC, id DESC);
"""

_MIGRATIONS = {
    "messages": {
        "message_ts": "INTEGER",
    },
}


def _parse_message_ts(date_raw: str) -> int:
    """Best-effort RFC822 Date header parser.

    Returns 0 when the header is missing or unparsable. Callers can then fall
    back to created_at, but freshness checks should prefer message_ts whenever
    it exists so an old email first fetched today is not treated as a new code.
    """
    if not date_raw:
        return 0
    try:
        return int(parsedate_to_datetime(date_raw).timestamp())
    except Exception:
        return 0


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    with _init_lock:
        directory = os.path.dirname(os.path.abspath(settings.DB_FILE))
        if directory:
            os.makedirs(directory, exist_ok=True)
        conn = _connect()
        try:
            conn.executescript(SCHEMA)
            _migrate(conn)
            _ensure_indexes(conn)
            _backfill_message_ts(conn)
            conn.commit()
        finally:
            conn.close()
        log.info("database initialised at %s", settings.DB_FILE)


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _MIGRATIONS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for column, col_type in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def _ensure_indexes(conn: sqlite3.Connection) -> None:
    # Created after migrations so existing databases get message_ts before this
    # index references it.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_email_message_ts "
        "ON messages(email, message_ts DESC, id DESC)"
    )


def _backfill_message_ts(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT id, date_raw FROM messages "
        "WHERE (message_ts IS NULL OR message_ts = 0) AND date_raw IS NOT NULL AND date_raw != ''"
    ).fetchall()
    for row in rows:
        message_ts = _parse_message_ts(row["date_raw"])
        if message_ts:
            conn.execute("UPDATE messages SET message_ts=? WHERE id=?", (message_ts, row["id"]))


# --------------------------------------------------------------------------- #
# accounts
# --------------------------------------------------------------------------- #
def upsert_account(account: Account) -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO accounts (email, password, client_id, refresh_token, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                password=excluded.password,
                client_id=excluded.client_id,
                refresh_token=excluded.refresh_token
            """,
            (account.email, account.password, account.client_id,
             account.refresh_token, int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()


def update_refresh_token(email: str, refresh_token: str) -> None:
    conn = _connect()
    try:
        conn.execute("UPDATE accounts SET refresh_token=? WHERE email=?",
                     (refresh_token, email))
        conn.commit()
    finally:
        conn.close()


def _row_to_account(row: sqlite3.Row) -> Account:
    return Account(
        email=row["email"],
        password=row["password"] or "",
        client_id=row["client_id"] or "",
        refresh_token=row["refresh_token"] or "",
    )


def get_accounts() -> List[Account]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM accounts ORDER BY email").fetchall()
        return [_row_to_account(r) for r in rows]
    finally:
        conn.close()


def get_account(email: str) -> Optional[Account]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
        return _row_to_account(row) if row else None
    finally:
        conn.close()


def list_accounts_public() -> List[dict]:
    """Account list WITHOUT password / refresh_token / access_token."""
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT a.email          AS email,
                   a.client_id      AS client_id,
                   a.refresh_token  AS refresh_token,
                   p.last_poll_at   AS last_poll_at,
                   p.last_error     AS last_error
            FROM accounts a
            LEFT JOIN poll_status p ON p.email = a.email
            ORDER BY a.email
            """
        ).fetchall()
        return [{
            "email": r["email"],
            "client_id": r["client_id"] or "",
            "has_refresh_token": bool(r["refresh_token"]),
            "last_poll_at": r["last_poll_at"],
            "last_error": r["last_error"] or "",
        } for r in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# messages
# --------------------------------------------------------------------------- #
def insert_message(email, message_id, from_addr, subject, body, html_body,
                   date_raw, code) -> bool:
    """Insert a message; returns True if newly inserted, False if a duplicate."""
    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO messages
                (email, message_id, from_addr, subject, body, html_body,
                 date_raw, message_ts, code, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (email, message_id, from_addr, subject, body, html_body,
             date_raw, _parse_message_ts(date_raw), code, int(time.time())),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_recent_messages(email: str, limit: int = 30) -> List[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM messages
            WHERE email=?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (email, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# poll_status
# --------------------------------------------------------------------------- #
def update_poll_status(email: str, last_poll_at: int, last_error: str = "") -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO poll_status (email, last_poll_at, last_error)
            VALUES (?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                last_poll_at=excluded.last_poll_at,
                last_error=excluded.last_error
            """,
            (email, last_poll_at, last_error),
        )
        conn.commit()
    finally:
        conn.close()


def get_poll_status(email: str) -> Optional[dict]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM poll_status WHERE email=?", (email,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
