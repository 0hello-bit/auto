"""IMAP over SSL with XOAUTH2 authentication (Outlook / Office 365).

The mailbox is opened read-only and messages are fetched with BODY.PEEK so we
never modify flags (e.g. \\Seen) on the user's mailbox.
"""
import imaplib
import logging
import socket
import ssl
import time
from typing import List, Optional
from urllib.parse import urlparse

from .config import settings

log = logging.getLogger(__name__)


class IMAPError(Exception):
    pass


class _PreconnectedIMAP4(imaplib.IMAP4):
    """IMAP4 that uses an already-connected (and TLS-wrapped) socket.

    ``imaplib`` always opens its own socket; to tunnel IMAP through an HTTP proxy
    we build the socket ourselves and hand it over via the overridden ``open``.
    """

    def __init__(self, sock, host: str, port: int, timeout: Optional[float] = None):
        self._presock = sock
        imaplib.IMAP4.__init__(self, host, port, timeout)

    def open(self, host, port, timeout=None):  # noqa: D401 - imaplib hook
        self.host = host
        self.port = port
        self.sock = self._presock
        self.file = self.sock.makefile("rb")


def _open_proxy_tunnel(proxy_url: str, host: str, port: int, timeout: int) -> socket.socket:
    """HTTP CONNECT tunnel through ``proxy_url`` to ``host:port`` (raw socket)."""
    pu = urlparse(proxy_url if "://" in proxy_url else "http://" + proxy_url)
    phost, pport = pu.hostname, (pu.port or 8080)
    s = socket.create_connection((phost, pport), timeout=timeout)
    try:
        req = (
            f"CONNECT {host}:{port} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Proxy-Connection: keep-alive\r\n\r\n"
        )
        s.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = s.recv(4096)
            if not chunk:
                break
            resp += chunk
            if len(resp) > 65536:
                break
        status_line = resp.split(b"\r\n", 1)[0] if resp else b""
        if b" 200" not in status_line:
            raise IMAPError(f"proxy CONNECT to {host}:{port} via {phost}:{pport} failed: {status_line[:80]!r}")
        return s
    except Exception:
        try:
            s.close()
        except Exception:
            pass
        raise


def _imap_ssl_via_proxy(proxy_url: str, host: str, port: int, timeout: int) -> imaplib.IMAP4:
    raw = _open_proxy_tunnel(proxy_url, host, port, timeout)
    ctx = ssl.create_default_context()
    tls = ctx.wrap_socket(raw, server_hostname=host)
    return _PreconnectedIMAP4(tls, host, port, timeout)


def _connect_with_retry(host: str, port: int, attempts: int = 3):
    """Open an IMAP4_SSL connection, retrying transient TLS/network resets.

    Outlook/Office365 intermittently drops the TLS handshake under load
    ("UNEXPECTED_EOF_WHILE_READING" / connection reset) when the IP is being
    throttled. A couple of short-backoff retries usually rides over a transient
    drop; a persistent failure (hard block) still raises after the last attempt.

    When ``IMAP_PROXY`` is configured (defaults to the HTTPS_PROXY/HTTP_PROXY
    env, e.g. a local Clash/V2Ray at 127.0.0.1:7897), IMAP is tunnelled through
    it via HTTP CONNECT -- required when the network can only reach Outlook
    through the proxy (``imaplib`` otherwise connects directly and is reset).
    """
    proxy = (settings.IMAP_PROXY or "").strip()
    last_exc: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            if proxy and proxy.lower() not in ("direct", "none", "0"):
                return _imap_ssl_via_proxy(proxy, host, port, settings.IMAP_TIMEOUT)
            return imaplib.IMAP4_SSL(host, port, timeout=settings.IMAP_TIMEOUT)
        except Exception as exc:  # SSL EOF / connection reset / timeout / proxy error
            last_exc = exc
            if attempt < attempts:
                backoff = 1.5 * attempt
                log.warning("IMAP connect attempt %d/%d failed (%s); retry in %.1fs",
                            attempt, attempts, str(exc)[:90], backoff)
                time.sleep(backoff)
    raise IMAPError(f"IMAP connect failed after {attempts} attempts: {last_exc}")


def _build_xoauth2(email: str, access_token: str) -> bytes:
    # SASL XOAUTH2 string: user=<email>^Aauth=Bearer <token>^A^A   (^A = \x01)
    return f"user={email}\x01auth=Bearer {access_token}\x01\x01".encode()


def fetch_recent_raw(email: str, access_token: str, limit: Optional[int] = None,
                     host: Optional[str] = None, port: Optional[int] = None) -> List[bytes]:
    """Return up to `limit` most recent raw RFC822 messages, OLDEST first.

    Oldest-first ordering means newer messages get larger DB ids, so a later
    "ORDER BY created_at DESC, id DESC" query reliably yields the newest mail.
    """
    limit = limit or settings.LATEST_MAIL_LIMIT
    host = host or settings.IMAP_HOST
    port = port or settings.IMAP_PORT

    try:
        conn = _connect_with_retry(host, port)
    except IMAPError:
        raise
    except Exception as exc:
        raise IMAPError(f"IMAP connect failed: {exc}") from exc

    try:
        try:
            conn.authenticate("XOAUTH2",
                              lambda _challenge: _build_xoauth2(email, access_token))
        except imaplib.IMAP4.error as exc:
            raise IMAPError(f"XOAUTH2 auth failed: {exc}") from exc

        typ, _ = conn.select("INBOX", readonly=True)
        if typ != "OK":
            raise IMAPError("could not select INBOX")

        typ, data = conn.search(None, "ALL")
        if typ != "OK":
            raise IMAPError("IMAP search failed")

        ids = data[0].split() if data and data[0] else []
        if not ids:
            return []
        recent = ids[-limit:]  # ascending sequence numbers -> oldest first

        messages: List[bytes] = []
        for num in recent:
            typ, msg_data = conn.fetch(num, "(BODY.PEEK[])")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            if isinstance(raw, (bytes, bytearray)):
                messages.append(bytes(raw))
        return messages
    finally:
        try:
            conn.logout()
        except Exception:
            pass
