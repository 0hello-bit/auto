"""Application configuration, loaded from environment variables / a .env file."""
import os

from dotenv import load_dotenv

# Load .env from the current working directory (if present) before reading vars.
load_dotenv()


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    """Runtime configuration. Read once at process start."""

    def __init__(self) -> None:
        # Secret required for every /api/* call.
        self.API_KEY = os.getenv("API_KEY", "")

        # Storage / data files.
        self.ACCOUNTS_FILE = os.getenv("ACCOUNTS_FILE", "accounts.txt")
        self.DB_FILE = os.getenv("DB_FILE", "mail_codes.db")

        # Polling behaviour.
        # Keep disabled by default: /api/code actively polls only the mailbox
        # currently waiting for a verification code. Scanning every account in
        # accounts.txt is useful for diagnostics, but slow/noisy with large pools.
        self.ENABLE_BACKGROUND_POLLER = _get_bool("ENABLE_BACKGROUND_POLLER", False)
        self.POLL_INTERVAL_SECONDS = _get_int("POLL_INTERVAL_SECONDS", 20)
        # On-demand poll interval used by /api/code while waiting for a code.
        # Larger = fewer IMAP connections (avoids Microsoft XOAUTH2 throttling).
        self.CODE_POLL_INTERVAL_SECONDS = _get_int("CODE_POLL_INTERVAL_SECONDS", 6)
        # When /api/code times out, retry stored messages from slightly before
        # the caller's "send code" timestamp. This catches messages that the
        # background poller stored a few seconds early without broadly allowing
        # stale codes.
        self.CODE_SINCE_GRACE_SECONDS = _get_int("CODE_SINCE_GRACE_SECONDS", 90)
        self.LATEST_MAIL_LIMIT = _get_int("LATEST_MAIL_LIMIT", 8)
        # Outlook IMAP gets flaky when multiple OAuth2 logins happen at once.
        # Keep browser/job concurrency in Project B, but serialize Project A's
        # IMAP fetches and leave a small gap between connections.
        self.IMAP_MIN_INTERVAL_SECONDS = _get_int("IMAP_MIN_INTERVAL_SECONDS", 2)

        # IMAP server.
        self.IMAP_HOST = os.getenv("IMAP_HOST", "outlook.office365.com")
        self.IMAP_PORT = _get_int("IMAP_PORT", 993)
        # Optional: route IMAP through an HTTP CONNECT proxy. ``imaplib`` ignores
        # HTTP(S)_PROXY and connects directly. On a network that can only reach
        # Outlook via a local proxy (some Clash/V2Ray nodes), the direct IMAP
        # socket on :993 is reset (SSL EOF) -- set IMAP_PROXY=http://127.0.0.1:7897
        # to tunnel IMAP through it. Default empty = direct connection.
        self.IMAP_PROXY = os.getenv("IMAP_PROXY", "")

        # Microsoft OAuth2.
        self.TOKEN_URL = os.getenv(
            "TOKEN_URL",
            "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
        )
        self.IMAP_SCOPE = os.getenv(
            "IMAP_SCOPE",
            "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
        )

        # HTTP server bind address.
        self.HOST = os.getenv("HOST", "127.0.0.1")
        self.PORT = _get_int("PORT", 5050)

        # Default verification-code regex (poller + /api/code default).
        self.DEFAULT_CODE_PATTERN = os.getenv("DEFAULT_CODE_PATTERN", r"\b\d{4,8}\b")

        # Network timeouts (seconds).
        self.HTTP_TIMEOUT = _get_int("HTTP_TIMEOUT", 30)
        self.IMAP_TIMEOUT = _get_int("IMAP_TIMEOUT", 30)


settings = Settings()
