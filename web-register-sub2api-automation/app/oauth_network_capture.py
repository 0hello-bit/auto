"""Capture the OAuth ``/auth/callback?code=...&state=...`` from the auth page.

The callback target is the Sub2API redirect URI (e.g. ``http://localhost:1455``)
which usually is NOT served by anything, so the browser navigation to it will
fail to load. We therefore must NOT rely on the page successfully navigating --
instead we listen on the network (``request`` / ``response`` / ``framenavigated``)
so the callback URL is captured the moment the browser merely *attempts* it.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)


def extract_query_param(url: str, key: str) -> str:
    try:
        qs = parse_qs(urlparse(url).query)
    except Exception:
        return ""
    return qs.get(key, [""])[0]


def extract_code_state(url: str) -> Optional[dict]:
    """Return ``{code, state, callback_url}`` if ``url`` carries an OAuth code."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    code = qs.get("code", [""])[0]
    state = qs.get("state", [""])[0]
    if code:
        return {"code": code, "state": state, "callback_url": url}
    return None


class OAuthCallbackCapture:
    """Listens on a page/context and records the first OAuth callback seen."""

    def __init__(self, expected_state: Optional[str] = None) -> None:
        self.expected_state = expected_state
        self.code: Optional[str] = None
        self.state: Optional[str] = None
        self.callback_url: Optional[str] = None
        self.captured: bool = False
        self.event: asyncio.Event = asyncio.Event()

    def reset(self, expected_state: Optional[str] = None) -> None:
        """Re-arm the capture for a fresh OAuth flow (new state).

        Used when the import regenerates the Sub2API auth_url (e.g. a second
        phone-verification page appears). Listeners stay attached to the same
        page/context; only the expected state and captured result are reset.
        """
        self.expected_state = expected_state
        self.code = None
        self.state = None
        self.callback_url = None
        self.captured = False
        self.event = asyncio.Event()

    # ------------------------------------------------------------------ #
    def consider(self, url: Optional[str]) -> None:
        if self.captured or not url:
            return
        if "/auth/callback" not in url or "code=" not in url:
            return
        result = extract_code_state(url)
        if not result:
            return
        # The capture is attached to the WHOLE browser context, which may also
        # see callbacks from OTHER OAuth flows (a previous/foreign/manually-opened
        # authorize in the same Chrome). When we know our expected state, accept
        # ONLY the callback whose state matches it -- otherwise we would grab a
        # foreign code/state that fails exchange (observed: state mismatch).
        if self.expected_state and result.get("state") and result["state"] != self.expected_state:
            log.info("Ignoring OAuth callback with non-matching state (foreign/concurrent flow)")
            return
        self.code = result["code"]
        self.state = result["state"]
        self.callback_url = result["callback_url"]
        self.captured = True
        # Avoid logging the full code; just signal capture.
        log.info("Captured OAuth callback (code/state present)")
        self.event.set()

    def state_matches(self) -> bool:
        return bool(self.captured) and (self.state == self.expected_state)

    # ------------------------------------------------------------------ #
    def attach_page(self, page) -> None:
        """Attach network listeners to a single page."""
        page.on("request", lambda request: self.consider(request.url))
        page.on("response", lambda response: self.consider(response.url))
        page.on("requestfailed", lambda request: self.consider(request.url))
        page.on("framenavigated", lambda frame: self.consider(frame.url))

    def attach_context(self, context) -> None:
        """Attach to all current and future pages in a browser context."""
        for page in context.pages:
            self.attach_page(page)
        context.on("page", self.attach_page)

    async def wait(self, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for a callback. Returns ``captured``."""
        try:
            await asyncio.wait_for(self.event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        return self.captured
