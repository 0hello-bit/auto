"""Exchange a Microsoft OAuth2 refresh_token for an access_token.

Access tokens are cached in memory per mailbox until shortly before expiry so we
do not hit the token endpoint on every poll. Secrets are never logged in full.
"""
import logging
import threading
import time
from typing import Dict, Tuple

import requests

from .config import settings
from .models import Account

log = logging.getLogger(__name__)


class OAuthError(Exception):
    pass


# email -> (access_token, expires_at_epoch)
_token_cache: Dict[str, Tuple[str, float]] = {}
_cache_lock = threading.Lock()
_EXPIRY_BUFFER = 60  # refresh this many seconds before the real expiry


def _mask(token: str) -> str:
    """Return a non-sensitive representation of a token for logs."""
    if not token:
        return "<empty>"
    if len(token) <= 8:
        return "****"
    return f"{token[:3]}...{token[-3:]}(len={len(token)})"


def get_access_token(account: Account, force: bool = False) -> str:
    """Return a valid access token for the account, using the cache when possible."""
    now = time.time()
    if not force:
        with _cache_lock:
            cached = _token_cache.get(account.email)
            if cached and cached[1] - _EXPIRY_BUFFER > now:
                return cached[0]

    access_token, expires_in = _refresh(account)
    with _cache_lock:
        _token_cache[account.email] = (access_token, now + expires_in)
    return access_token


def invalidate(email: str) -> None:
    with _cache_lock:
        _token_cache.pop(email, None)


def _refresh(account: Account) -> Tuple[str, int]:
    data = {
        "client_id": account.client_id,
        "grant_type": "refresh_token",
        "refresh_token": account.refresh_token,
        "scope": settings.IMAP_SCOPE,
    }
    try:
        resp = requests.post(settings.TOKEN_URL, data=data, timeout=settings.HTTP_TIMEOUT)
    except requests.RequestException as exc:
        raise OAuthError(f"token request failed: {exc}") from exc

    if resp.status_code != 200:
        # Log only the safe error fields (e.g. invalid_grant), never tokens.
        detail = ""
        try:
            body = resp.json()
            detail = f"{body.get('error')}: {str(body.get('error_description', ''))[:200]}"
        except Exception:
            detail = resp.text[:200]
        log.warning(
            "oauth refresh failed for %s (status=%s): %s",
            account.email, resp.status_code, detail,
        )
        raise OAuthError(f"oauth refresh failed (status={resp.status_code}): {detail}")

    body = resp.json()
    access_token = body.get("access_token")
    if not access_token:
        raise OAuthError("oauth response did not contain access_token")
    expires_in = int(body.get("expires_in", 3600))

    # Some flows rotate the refresh_token; persist a new one if returned.
    new_rt = body.get("refresh_token")
    if new_rt and new_rt != account.refresh_token:
        account.refresh_token = new_rt
        try:
            from . import database  # local import to avoid a circular import
            database.update_refresh_token(account.email, new_rt)
            log.info("refresh_token rotated for %s", account.email)
        except Exception as exc:
            log.warning("failed to persist rotated refresh_token for %s: %s",
                        account.email, exc)

    log.debug("got access_token for %s (%s) expires_in=%s",
              account.email, _mask(access_token), expires_in)
    return access_token, expires_in
