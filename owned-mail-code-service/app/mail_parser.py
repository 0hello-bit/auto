"""Parse raw RFC822 bytes into subject / from / text body / html body / etc."""
import email
import hashlib
import logging
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.utils import parseaddr
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class ParsedMail:
    message_id: str
    from_addr: str
    subject: str
    text_body: str
    html_body: str
    date_raw: str


def _decode_header(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _decode_payload(part) -> str:
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    except Exception:
        try:
            return part.get_payload(decode=True).decode("utf-8", errors="replace")
        except Exception:
            return ""


def parse_message(raw: bytes) -> ParsedMail:
    msg = email.message_from_bytes(raw)

    subject = _decode_header(msg.get("Subject"))
    from_raw = msg.get("From", "")
    _name, addr = parseaddr(from_raw)
    from_addr = addr or _decode_header(from_raw)
    date_raw = msg.get("Date", "") or ""
    message_id = (msg.get("Message-ID") or msg.get("Message-Id") or "").strip()

    text_body = ""
    html_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            disposition = str(part.get("Content-Disposition") or "")
            if "attachment" in disposition.lower():
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain" and not text_body:
                text_body = _decode_payload(part)
            elif ctype == "text/html" and not html_body:
                html_body = _decode_payload(part)
    else:
        body = _decode_payload(msg)
        if msg.get_content_type() == "text/html":
            html_body = body
        else:
            text_body = body

    if not message_id:
        # Fallback stable id so UNIQUE(email, message_id) de-duplication still
        # works for the rare message that lacks a Message-ID header.
        digest = hashlib.md5()
        digest.update((from_addr + "|" + subject + "|" + date_raw).encode("utf-8", "replace"))
        digest.update(raw[:512])
        message_id = "gen-" + digest.hexdigest()

    return ParsedMail(
        message_id=message_id,
        from_addr=from_addr,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        date_raw=date_raw,
    )
