"""Centralized browser / context acquisition for all workers.

Two modes (``BROWSER_MODE``):

- ``playwright`` (default): launch Playwright's bundled Chromium (original behavior).
- ``cdp``: connect to a user-started local Chrome/Edge via CDP
  (``connect_over_cdp``). The local browser is launched MANUALLY by the user with
  a dedicated ``--user-data-dir`` (so it never touches the daily-browser profile)
  and ``--remote-debugging-port``.

``CDP_CONTEXT_POLICY``:

- ``incognito`` (default & recommended): every task gets a fresh ``new_context()``
  with NO pre-existing cookies; the context is closed at task end, clearing
  cookies / localStorage / sessionStorage.
- ``first``: reuse ``browser.contexts[0]`` to keep an existing logged-in profile;
  the shared context is preserved (not closed) across tasks.

By default the local Chrome process is NOT closed (only the task context is).
Set ``CDP_CLOSE_BROWSER=true`` to also close it.

ALL workers must obtain browser/context here and never call ``chromium.launch`` /
``connect_over_cdp`` directly.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from typing import Optional, Tuple

from playwright.async_api import async_playwright

from .config import config

log = logging.getLogger(__name__)

_cdp_endpoint_pool: Optional[asyncio.Queue] = None
_cdp_endpoint_pool_key: Tuple[str, ...] = ()
_cdp_endpoint_pool_guard = asyncio.Lock()
_visible_incognito_context_ids = set()
_OPENAI_ORIGINS = (
    "https://chatgpt.com",
    "https://auth.openai.com",
    "https://auth0.openai.com",
    "https://platform.openai.com",
    "https://openai.com",
    "http://localhost:1455",
    "http://127.0.0.1:1455",
)


def _uses_cdp(settings) -> bool:
    return (settings.browser_mode or "playwright").lower() == "cdp"


def _configured_cdp_endpoints(settings) -> Tuple[str, ...]:
    endpoints = list(getattr(settings, "cdp_endpoints", None) or [])
    if not endpoints:
        endpoints = [settings.cdp_endpoint]
    cleaned = tuple(endpoint.strip() for endpoint in endpoints if endpoint and endpoint.strip())
    return cleaned or (settings.cdp_endpoint,)


async def _acquire_cdp_endpoint(settings) -> Optional[str]:
    """Lease one local Chrome CDP endpoint for this browser session."""
    if not _uses_cdp(settings):
        return None

    global _cdp_endpoint_pool, _cdp_endpoint_pool_key
    endpoints = _configured_cdp_endpoints(settings)
    async with _cdp_endpoint_pool_guard:
        if _cdp_endpoint_pool is None or _cdp_endpoint_pool_key != endpoints:
            pool: asyncio.Queue = asyncio.Queue()
            for endpoint in endpoints:
                pool.put_nowait(endpoint)
            _cdp_endpoint_pool = pool
            _cdp_endpoint_pool_key = endpoints

    assert _cdp_endpoint_pool is not None
    endpoint = await _cdp_endpoint_pool.get()
    log.info("Leased CDP endpoint %s", endpoint)
    return endpoint


def _release_cdp_endpoint(endpoint: Optional[str]) -> None:
    if endpoint is None or _cdp_endpoint_pool is None:
        return
    try:
        _cdp_endpoint_pool.put_nowait(endpoint)
        log.info("Released CDP endpoint %s", endpoint)
    except Exception:
        pass


async def _endpoint_is_reachable(playwright, endpoint: str) -> bool:
    try:
        browser = await playwright.chromium.connect_over_cdp(endpoint)
        await browser.close()
        return True
    except Exception:
        return False


async def _acquire_reachable_cdp_endpoint(playwright, settings) -> Optional[str]:
    if not _uses_cdp(settings):
        return None
    endpoints = _configured_cdp_endpoints(settings)
    for _ in range(max(1, len(endpoints))):
        endpoint = await _acquire_cdp_endpoint(settings)
        if await _endpoint_is_reachable(playwright, endpoint):
            return endpoint
        log.warning("CDP endpoint unavailable; skipping for now: %s", endpoint)
        _release_cdp_endpoint(endpoint)
    return await _acquire_cdp_endpoint(settings)


async def reset_context_storage(context) -> None:
    """Clear OpenAI/ChatGPT cookies and storage for a reused context."""
    try:
        await context.clear_cookies()
    except Exception:
        pass
    try:
        await context.clear_permissions()
    except Exception:
        pass

    origins = set(_OPENAI_ORIGINS)
    try:
        state = await context.storage_state()
        origins.update(item.get("origin") for item in state.get("origins", []) if item.get("origin"))
    except Exception:
        pass
    for page in context.pages:
        try:
            parsed = urlparse(page.url or "")
            if parsed.scheme and parsed.netloc:
                origins.add(f"{parsed.scheme}://{parsed.netloc}")
        except Exception:
            pass

    page = next((pg for pg in context.pages if not pg.is_closed()), None)
    if page is None:
        try:
            page = await context.new_page()
        except Exception:
            page = None
    if page is None:
        return

    try:
        client = await context.new_cdp_session(page)
        for method in ("Network.clearBrowserCookies", "Network.clearBrowserCache"):
            try:
                await client.send(method)
            except Exception:
                pass
        for origin in origins:
            try:
                await client.send("Storage.clearDataForOrigin", {"origin": origin, "storageTypes": "all"})
            except Exception:
                pass
        try:
            await client.detach()
        except Exception:
            pass
    except Exception:
        pass


async def _reset_visible_incognito_context(context) -> None:
    """Best-effort cleanup for a reused visible Chrome incognito window."""
    await reset_context_storage(context)

    pages = list(context.pages)
    keep = next(
        (
            page for page in pages
            if not page.is_closed() and (page.url == "about:blank" or not page.url)
        ),
        None,
    )
    if keep is None:
        keep = next((page for page in pages if not page.is_closed()), None)
    if keep is None:
        try:
            keep = await context.new_page()
        except Exception:
            keep = None

    for page in pages:
        if page is keep:
            continue
        try:
            if not page.is_closed():
                await page.close()
        except Exception:
            pass

    if keep is not None:
        try:
            if not keep.is_closed():
                await keep.goto("about:blank", wait_until="domcontentloaded", timeout=3000)
        except Exception:
            pass


async def claim_page(context):
    """Return a page for a task without forcing Chrome to open a regular window.

    For local Chrome started with ``--incognito``, CDP ``context.new_page()`` can
    create a normal-looking window. Reusing the existing incognito ``about:blank``
    page keeps automation inside the visible incognito window.
    """
    if id(context) in _visible_incognito_context_ids:
        for page in context.pages:
            try:
                if not page.is_closed() and (page.url == "about:blank" or not page.url):
                    return page
            except Exception:
                continue
        for page in context.pages:
            try:
                if not page.is_closed():
                    await page.goto("about:blank", wait_until="domcontentloaded", timeout=3000)
                    return page
            except Exception:
                continue
    return await context.new_page()


async def get_browser_and_context(
    playwright,
    settings=None,
    headless: Optional[bool] = None,
    cdp_endpoint: Optional[str] = None,
) -> Tuple:
    """Return ``(browser, context)`` per ``BROWSER_MODE`` / ``CDP_CONTEXT_POLICY``.

    ``headless`` (playwright mode only) overrides ``settings.headless`` when given.
    """
    settings = settings or config
    mode = (settings.browser_mode or "playwright").lower()

    if mode == "cdp":
        endpoint = cdp_endpoint or settings.cdp_endpoint
        browser = await playwright.chromium.connect_over_cdp(endpoint)
        policy = (settings.cdp_context_policy or "incognito").lower()
        reuse_visible_incognito = False
        if policy == "incognito":
            if browser.contexts:
                # Reuse the visible incognito window created by run-all.ps1
                # (--incognito). CDP-created new_context() instances are isolated,
                # but Chrome's UI may show them as regular windows.
                context = browser.contexts[0]
                reuse_visible_incognito = True
                _visible_incognito_context_ids.add(id(context))
            else:
                context = await browser.new_context()
        elif policy == "first":
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
        else:
            raise ValueError(f"Unsupported CDP_CONTEXT_POLICY: {settings.cdp_context_policy!r}")
        log.info("CDP connected (%s, policy=%s)", endpoint, policy)
        return browser, context, reuse_visible_incognito

    if mode == "playwright":
        hl = settings.headless if headless is None else headless
        browser = await playwright.chromium.launch(headless=hl)
        context = await browser.new_context()
        return browser, context, False

    raise ValueError(f"Unsupported BROWSER_MODE: {settings.browser_mode!r}")


async def _safe_close(closer) -> None:
    try:
        await closer()
    except Exception:  # already closed / disconnected -- never raise from cleanup
        pass


async def cleanup_browser(browser, context, settings=None, reuse_visible_incognito: bool = False) -> None:
    """Close the task context (and the browser only when appropriate).

    - playwright mode: close context + browser.
    - cdp incognito: preserve the visible incognito window, but clear cookies,
      storage, permissions and stale pages between tasks.
    - cdp first: PRESERVE the reused shared context (keeps login state); keep the
      local Chrome unless ``CDP_CLOSE_BROWSER=true``.
    """
    settings = settings or config
    mode = (settings.browser_mode or "playwright").lower()
    policy = (settings.cdp_context_policy or "incognito").lower()

    reused_first = (mode == "cdp" and policy == "first")
    visible_incognito = (
        mode == "cdp"
        and policy == "incognito"
        and reuse_visible_incognito
    )
    if visible_incognito:
        await _reset_visible_incognito_context(context)
    elif not reused_first:
        await _safe_close(context.close)

    if mode == "playwright":
        await _safe_close(browser.close)
    elif mode == "cdp" and settings.cdp_close_browser:
        await _safe_close(browser.close)


@asynccontextmanager
async def browser_session(trace_name: str, headless: Optional[bool] = None):
    """Async context manager yielding a ready ``context``.

    Wraps :func:`get_browser_and_context` + :func:`cleanup_browser`, and adds the
    default timeout + a best-effort Playwright trace saved to
    ``traces/<trace_name>.zip``. The SAME context is used for the registration
    and authorization pages in the combined flow.
    """
    leased_endpoint = None
    async with async_playwright() as pw:
        leased_endpoint = await _acquire_reachable_cdp_endpoint(pw, config)
        try:
            browser, context, reuse_visible_incognito = await get_browser_and_context(
                pw,
                config,
                headless=headless,
                cdp_endpoint=leased_endpoint,
            )
            try:
                context.set_default_timeout(config.browser_timeout_ms)
            except Exception:
                pass
            if reuse_visible_incognito:
                await _reset_visible_incognito_context(context)
            traced = False
            try:
                try:
                    await context.tracing.start(screenshots=True, snapshots=True, sources=False)
                    traced = True
                except Exception:
                    traced = False
                yield context
            finally:
                if traced:
                    try:
                        config.trace_path.mkdir(parents=True, exist_ok=True)
                        await context.tracing.stop(path=str(config.trace_path / f"{trace_name}.zip"))
                    except Exception:
                        pass
                await cleanup_browser(browser, context, config, reuse_visible_incognito=reuse_visible_incognito)
        finally:
            _release_cdp_endpoint(leased_endpoint)
