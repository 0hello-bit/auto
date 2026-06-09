"""HTTP API routes. Every /api/* endpoint requires the x-api-key header."""
import hmac
import logging
import re
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from . import database, poller
from .account_parser import parse_text
from .code_extractor import DEFAULT_PATTERN
from .config import settings
from .imap_client import fetch_recent_raw
from .microsoft_oauth import get_access_token
from .models import CheckRequest, CodeRequest, ImportRequest

log = logging.getLogger(__name__)


def require_api_key(x_api_key: str = Header(default=None, alias="x-api-key")) -> None:
    expected = settings.API_KEY
    if (not expected or not x_api_key
            or not hmac.compare_digest(x_api_key.encode("utf-8"), expected.encode("utf-8"))):
        raise HTTPException(status_code=401, detail="unauthorized")


router = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])


@router.post("/accounts/import")
def import_accounts(req: ImportRequest):
    accounts, errors = parse_text(req.text or "")
    for account in accounts:
        database.upsert_account(account)
    return {"code": 1, "msg": "success",
            "data": {"imported": len(accounts), "errors": errors}}


@router.get("/accounts")
def list_accounts():
    return {"code": 1, "msg": "success", "data": database.list_accounts_public()}


@router.post("/poll")
def manual_poll():
    result = poller.poll_all()
    return {"code": 1, "msg": "success", "data": result}


@router.post("/accounts/check")
def check_account(req: CheckRequest):
    """Probe one mailbox's health: OAuth refresh + a small IMAP fetch.

    Returns a clear verdict so a caller can vet an account BEFORE running the
    full register/import flow (and before spending an SMS order). ``abuse`` is
    True when the refresh_token is revoked / the account is in AADSTS70000
    service-abuse mode -- such mailboxes can never receive a code.
    """
    email = (req.email or "").strip()
    if not email:
        return JSONResponse(status_code=400, content={"code": 400, "msg": "email is required"})
    account = database.get_account(email)
    if not account:
        return JSONResponse(status_code=404,
                            content={"code": 404, "msg": "account not found", "data": {"email": email}})

    data = {"email": email, "oauth_ok": False, "imap_ok": False,
            "abuse": False, "healthy": False, "detail": ""}
    try:
        token = get_access_token(account, force=True)
        data["oauth_ok"] = True
    except Exception as exc:  # OAuth refresh failed (often abuse / invalid_grant)
        msg = str(exc)
        data["detail"] = msg[:300]
        data["abuse"] = poller.is_permanent_mailbox_error(msg)
        return {"code": 1, "msg": "checked", "data": data}

    try:
        msgs = fetch_recent_raw(email, token, limit=3)
        data["imap_ok"] = True
        data["detail"] = f"{len(msgs)} recent message(s) reachable"
    except Exception as exc:  # IMAP unreachable (network/proxy) but token is fine
        data["detail"] = str(exc)[:300]

    data["healthy"] = data["oauth_ok"] and data["imap_ok"]
    return {"code": 1, "msg": "checked", "data": data}


@router.post("/code")
def get_code(req: CodeRequest):
    email = (req.email or "").strip()
    if not email:
        return JSONResponse(status_code=400,
                            content={"code": 400, "msg": "email is required"})

    account = database.get_account(email)
    if not account:
        return JSONResponse(status_code=404,
                            content={"code": 404, "msg": "account not found",
                                     "data": {"email": email}})

    pattern = req.pattern or settings.DEFAULT_CODE_PATTERN or DEFAULT_PATTERN
    try:
        re.compile(pattern)
    except re.error as exc:
        return JSONResponse(status_code=400,
                            content={"code": 400, "msg": f"invalid pattern: {exc}"})

    timeout = max(0, int(req.timeout))
    deadline = time.time() + timeout

    def _success(code: str):
        return {"code": 1, "msg": "success",
                "data": {"email": email, "verification_code": code}}

    def _find_code(since_grace: int = 0):
        return poller.find_code(
            email,
            pattern,
            req.subject_keyword,
            req.from_keyword,
            since=req.since,
            since_grace=since_grace,
        )

    with poller.code_request_scope():
        # 1) Check what is already stored.
        code = _find_code()
        if code:
            return _success(code)

        # 2) Actively poll the mailbox, retrying every few seconds until the deadline.
        #    If a poll fails with a PERMANENT auth error (refresh_token revoked /
        #    AADSTS70000 service-abuse mode), stop immediately and report it as a
        #    409 "mailbox unavailable" so the caller can mark the mailbox dead
        #    instead of waiting out the full timeout and getting an ambiguous 408.
        while time.time() < deadline:
            ok, poll_error = poller.poll_account(account)
            if not ok and poller.is_permanent_mailbox_error(poll_error):
                log.warning("mailbox %s unavailable (permanent): %s", email, poll_error)
                return JSONResponse(
                    status_code=409,
                    content={"code": 409, "msg": f"mailbox unavailable: {poll_error}",
                             "data": {"email": email, "reason": "mailbox_unavailable",
                                      "detail": poll_error}},
                )
            if not ok and poller.is_transient_mailbox_error(poll_error):
                log.warning("mailbox %s deferred (transient): %s", email, poll_error)
                return JSONResponse(
                    status_code=423,
                    content={"code": 423, "msg": f"mailbox deferred: {poll_error}",
                             "data": {"email": email, "reason": "mailbox_deferred",
                                      "detail": poll_error}},
                )
            code = _find_code()
            if code:
                return _success(code)
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(settings.CODE_POLL_INTERVAL_SECONDS, max(1, remaining)))

        grace = max(0, settings.CODE_SINCE_GRACE_SECONDS)
        if req.since and grace:
            code = _find_code(since_grace=grace)
            if code:
                log.info("code for %s found with %ss since grace after timeout", email, grace)
                return _success(code)

    return JSONResponse(status_code=408,
                        content={"code": 408, "msg": "timeout",
                                 "data": {"email": email}})


@router.get("/messages")
def list_messages(email_addr: str = Query(...)):
    rows = database.get_recent_messages(email_addr, 30)
    data = [{
        "email": r["email"],
        "from_addr": r["from_addr"],
        "subject": r["subject"],
        "code": r["code"],
        "date_raw": r["date_raw"],
        "created_at": r["created_at"],
    } for r in rows]
    return {"code": 1, "msg": "success", "data": data}
