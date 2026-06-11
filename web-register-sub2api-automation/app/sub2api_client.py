"""Client for the locally deployed Sub2API admin API (``weishaw/sub2api``).

This talks to the REAL weishaw/sub2api admin API (verified against a running
instance), which uses **JWT admin login** (email + password), not an
``x-api-key``. Success envelope is ``{"code":0,"message":"success","data":...}``
(``code == 0`` means success).

OpenAI (ChatGPT) account import is a 3-step flow:

    1. POST /api/v1/auth/login                      {email, password} -> access_token
    2. POST /api/v1/admin/openai/generate-auth-url  {redirect_uri}    -> {auth_url, session_id}
    3. POST /api/v1/admin/openai/exchange-code       {session_id, code, state} -> oauth credentials
    4. POST /api/v1/admin/accounts                   {platform:"openai", type:"oauth",
                                                      credentials, group_ids, ...} -> {id}

    GET  /api/v1/admin/groups/all -> list of groups (find the "self" group id)

``create_from_oauth`` performs steps 3 + 4 so the rest of the app keeps a single
call. Responses are parsed defensively.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from .config import config, proxies_for

log = logging.getLogger(__name__)

_TIMEOUT = 30
_SUCCESS_CODES = {0, 1, 200, "0", "1", "200", "success", "ok"}

# Fields copied from the exchange-code result into the account credentials
# (mirrors the admin UI's buildCredentials).
_CREDENTIAL_FIELDS = (
    "access_token", "refresh_token", "id_token", "expires_at", "email",
    "chatgpt_account_id", "chatgpt_user_id", "organization_id", "account_id",
)

# Cached admin JWT (shared across worker threads).
_token_lock = threading.Lock()
_token: Optional[str] = None


class Sub2ApiError(RuntimeError):
    pass


def _base() -> str:
    return config.sub2api_base.rstrip("/")


def _envelope_msg(body: Any) -> str:
    if isinstance(body, dict):
        for key in ("message", "detail", "msg", "error"):
            if body.get(key):
                return str(body[key])
        data = body.get("data")
        if isinstance(data, dict):
            for key in ("detail", "message", "error"):
                if data.get(key):
                    return str(data[key])
    return str(body)[:300]


def _check(body: Any, path: str) -> Any:
    if isinstance(body, dict) and "code" in body and body["code"] not in _SUCCESS_CODES:
        raise Sub2ApiError(f"Sub2API error on {path}: {_envelope_msg(body)}")
    return body


def _login() -> str:
    if not config.sub2api_admin_email or not config.sub2api_admin_password:
        raise Sub2ApiError(
            "Sub2API admin login not configured: set SUB2API_ADMIN_EMAIL and SUB2API_ADMIN_PASSWORD"
        )
    url = f"{_base()}/api/v1/auth/login"
    try:
        resp = requests.post(
            url,
            json={"email": config.sub2api_admin_email, "password": config.sub2api_admin_password},
            timeout=_TIMEOUT,
            proxies=proxies_for(url),
        )
    except requests.RequestException as exc:
        raise Sub2ApiError(f"Sub2API login request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise Sub2ApiError(f"Sub2API login HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise Sub2ApiError(f"Sub2API login returned non-JSON: {resp.text[:200]}") from exc
    _check(body, "/api/v1/auth/login")
    token = _pick(body, "access_token", "token", "accessToken")
    if not token:
        raise Sub2ApiError("Sub2API login returned no access_token")
    log.info("Sub2API admin login ok")
    return str(token)


def _get_token(force: bool = False) -> str:
    global _token
    with _token_lock:
        if force:
            _token = None
        if _token is None:
            _token = _login()
        return _token


def _request(method: str, path: str, *, json_body: Optional[dict] = None, params: Optional[dict] = None) -> Any:
    """Authenticated admin request with one automatic re-login on 401."""
    url = f"{_base()}{path}"
    for attempt in (1, 2):
        token = _get_token(force=(attempt == 2))
        try:
            resp = requests.request(
                method, url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                         "Accept": "application/json"},
                json=json_body, params=params, timeout=_TIMEOUT, proxies=proxies_for(url),
            )
        except requests.RequestException as exc:
            raise Sub2ApiError(f"Sub2API request failed ({method} {path}): {exc}") from exc

        if resp.status_code == 401 and attempt == 1:
            log.info("Sub2API 401 -> re-login and retry")
            continue
        if resp.status_code >= 400:
            raise Sub2ApiError(f"Sub2API HTTP {resp.status_code} on {path}: {resp.text[:300]}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise Sub2ApiError(f"Sub2API returned non-JSON on {path}: {resp.text[:300]}") from exc
        return _check(body, path)
    raise Sub2ApiError(f"Sub2API request failed after re-login ({method} {path})")


def _candidates(body: Any) -> List[dict]:
    out: List[dict] = []
    if isinstance(body, dict):
        out.append(body)
        data = body.get("data")
        if isinstance(data, dict):
            out.append(data)
    return out


def _pick(body: Any, *keys: str) -> Optional[Any]:
    for d in _candidates(body):
        for key in keys:
            if key in d and d[key] not in (None, ""):
                return d[key]
    return None


def _build_credentials(exchange_data: Any) -> Dict[str, Any]:
    src: Dict[str, Any] = {}
    if isinstance(exchange_data, dict):
        src = exchange_data.get("data") if isinstance(exchange_data.get("data"), dict) else exchange_data
    creds = {k: src[k] for k in _CREDENTIAL_FIELDS if isinstance(src, dict) and src.get(k) not in (None, "")}
    if "access_token" not in creds:
        raise Sub2ApiError(f"exchange-code returned no access_token: keys={list(src) if isinstance(src, dict) else src}")
    return creds


# --------------------------------------------------------------------------- #
# endpoints
# --------------------------------------------------------------------------- #
def generate_auth_url() -> Tuple[str, str, Any]:
    """POST /api/v1/admin/openai/generate-auth-url -> (auth_url, session_id, body)."""
    body = _request(
        "POST", "/api/v1/admin/openai/generate-auth-url",
        json_body={"redirect_uri": config.sub2api_redirect_uri},
    )
    auth_url = _pick(body, "auth_url", "authUrl", "url")
    session_id = _pick(body, "session_id", "sessionId", "session", "id")
    if not auth_url:
        raise Sub2ApiError(f"generate-auth-url returned no auth_url: {body}")
    if not session_id:
        raise Sub2ApiError(f"generate-auth-url returned no session_id: {body}")
    log.info("Sub2API generate-auth-url ok (session_id acquired)")
    return str(auth_url), str(session_id), body


def create_from_oauth(
    session_id: str,
    code: str,
    state: str,
    name: str,
    redirect_uri: Optional[str] = None,
    concurrency: Optional[int] = None,
    priority: Optional[int] = None,
    group_ids: Optional[Iterable[int]] = None,
    proxy_id: Optional[int] = None,
) -> Tuple[Optional[int], Any]:
    """Exchange the OAuth code then create the OpenAI account.

    Step 1: POST /api/v1/admin/openai/exchange-code {session_id, code, state}
    Step 2: POST /api/v1/admin/accounts {platform, type, credentials, group_ids, ...}
    Returns ``(account_id, create_body)``.
    """
    exchange = _request(
        "POST", "/api/v1/admin/openai/exchange-code",
        json_body={"session_id": session_id, "code": code, "state": state},
    )
    credentials = _build_credentials(exchange)

    payload = {
        "name": name,
        "platform": config.sub2api_platform,        # "openai"
        "type": config.sub2api_account_type,        # "oauth"
        "credentials": credentials,
        "concurrency": concurrency if concurrency is not None else config.sub2api_default_concurrency,
        "priority": priority if priority is not None else config.sub2api_default_priority,
        "group_ids": list(group_ids) if group_ids is not None else list(config.sub2api_default_group_ids),
    }
    # Attach a proxy so Sub2API doesn't forward to chatgpt.com directly (-> GFW reset).
    effective_proxy_id = proxy_id if proxy_id is not None else config.sub2api_default_proxy_id
    if effective_proxy_id is not None:
        payload["proxy_id"] = effective_proxy_id
    body = _request("POST", "/api/v1/admin/accounts", json_body=payload)
    account_id = _pick(body, "id", "account_id", "accountId")
    try:
        account_id = int(account_id) if account_id is not None else None
    except (TypeError, ValueError):
        account_id = None
    log.info("Sub2API account created (account_id=%s, group_ids=%s, proxy_id=%s)",
             account_id, payload["group_ids"], effective_proxy_id)
    return account_id, body


def get_groups() -> Any:
    """GET /api/v1/admin/groups/all -> list of groups (data unwrapped)."""
    body = _request("GET", "/api/v1/admin/groups/all")
    if isinstance(body, dict) and "data" in body:
        data = body["data"]
        # some list endpoints wrap as {items:[...]}
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
        return data
    return body


def list_accounts(page_size: int = 1000) -> List[dict]:
    """GET /api/v1/admin/accounts -> list of account dicts (data.items)."""
    body = _request("GET", "/api/v1/admin/accounts", params={"page": 1, "page_size": page_size})
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    if isinstance(data, list):
        return data
    return []


def existing_account_emails() -> set:
    """Lowercased identifiers (``credentials.email`` + ``name``) of existing accounts.

    Used to skip emails that have already been imported into Sub2API (whether by
    this tool or manually).
    """
    out: set = set()
    for acc in list_accounts():
        if not isinstance(acc, dict):
            continue
        creds = acc.get("credentials")
        if isinstance(creds, dict) and creds.get("email"):
            out.add(str(creds["email"]).strip().lower())
        if acc.get("name"):
            out.add(str(acc["name"]).strip().lower())
    return out
