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

import logging
from contextlib import asynccontextmanager
from typing import Optional, Tuple

from playwright.async_api import async_playwright

from .config import config

log = logging.getLogger(__name__)


async def get_browser_and_context(playwright, settings=None, headless: Optional[bool] = None) -> Tuple:
    """Return ``(browser, context)`` per ``BROWSER_MODE`` / ``CDP_CONTEXT_POLICY``.

    ``headless`` (playwright mode only) overrides ``settings.headless`` when given.
    """
    settings = settings or config
    mode = (settings.browser_mode or "playwright").lower()

    if mode == "cdp":
        browser = await playwright.chromium.connect_over_cdp(settings.cdp_endpoint)
        policy = (settings.cdp_context_policy or "incognito").lower()
        if policy == "incognito":
            context = await browser.new_context()
        elif policy == "first":
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
        else:
            raise ValueError(f"Unsupported CDP_CONTEXT_POLICY: {settings.cdp_context_policy!r}")
        log.info("CDP connected (%s, policy=%s)", settings.cdp_endpoint, policy)
        return browser, context

    if mode == "playwright":
        hl = settings.headless if headless is None else headless
        browser = await playwright.chromium.launch(headless=hl)
        context = await browser.new_context()
        return browser, context

    raise ValueError(f"Unsupported BROWSER_MODE: {settings.browser_mode!r}")


async def _safe_close(closer) -> None:
    try:
        await closer()
    except Exception:  # already closed / disconnected -- never raise from cleanup
        pass


async def cleanup_browser(browser, context, settings=None) -> None:
    """Close the task context (and the browser only when appropriate).

    - playwright mode: close context + browser.
    - cdp incognito: close the task context (clears cookies/storage); keep the
      local Chrome running unless ``CDP_CLOSE_BROWSER=true``.
    - cdp first: PRESERVE the reused shared context (keeps login state); keep the
      local Chrome unless ``CDP_CLOSE_BROWSER=true``.
    """
    settings = settings or config
    mode = (settings.browser_mode or "playwright").lower()
    policy = (settings.cdp_context_policy or "incognito").lower()

    reused_first = (mode == "cdp" and policy == "first")
    if not reused_first:
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
    async with async_playwright() as pw:
        browser, context = await get_browser_and_context(pw, config, headless=headless)
        try:
            context.set_default_timeout(config.browser_timeout_ms)
        except Exception:
            pass
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
            await cleanup_browser(browser, context, config)
