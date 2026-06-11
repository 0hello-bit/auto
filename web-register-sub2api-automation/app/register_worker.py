"""Web registration worker (Playwright Chromium, async API).

This module also hosts the shared Playwright helpers (selector resolution,
click/fill, screenshots, the browser-context session) that the import and the
combined workers reuse -- the project file layout is fixed, so these live here
rather than in a separate helper module.

All selectors come from configuration (.env). A selector value may contain
several comma-separated CSS selectors; each piece is treated as a *fallback
candidate* and tried in order until one resolves to a visible element. (Avoid
putting a literal comma inside a single ``:has-text("a, b")`` selector.)
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import List, Optional

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from . import browser_manager, database, email_pool, mail_code_client
from .config import config
from .database import now
from .mail_code_client import MailboxDeferredError, MailboxUnavailableError
from .sms_provider_base import SmsTimeout

log = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"success", "failed", "timeout"}


class PlaywrightStepError(RuntimeError):
    """A required page element never became ready."""


_NAME_INPUT_EXTRA_SELECTOR = (
    'input[placeholder="全名"], input[name="fullName"], input[name="full_name"], '
    'input[aria-label*="全名"], input[placeholder*="Full"], input[aria-label*="Full"]'
)
_BIRTH_DATE_INPUT_SELECTOR = (
    'input[placeholder="生日日期"], input[placeholder*="生日"], input[aria-label*="生日"], '
    'input[name*="birth"], input[placeholder*="Birth"], input[aria-label*="Birth"], input[type="date"]'
)
_CHATGPT_APP_MARKERS = (
    "新聊天", "搜索聊天", "库", "项目", "应用", "Codex",
    "有问题，尽管问", "你在忙什么", "What can I help with",
)


def _append_selectors(selector: str, extra: str) -> str:
    parts = [piece.strip() for piece in (selector or "").split(",") if piece.strip()]
    parts.extend(piece.strip() for piece in (extra or "").split(",") if piece.strip())
    return ", ".join(dict.fromkeys(parts))


def _birth_date_for_age(age: int, *, html_date: bool = False) -> str:
    today = dt.datetime.fromtimestamp(now()).date()
    year = today.year - int(age)
    try:
        birthday = today.replace(year=year)
    except ValueError:
        birthday = today.replace(year=year, day=28)
    return birthday.strftime("%Y-%m-%d" if html_date else "%Y/%m/%d")


async def _fill_birth_date(page, age: int, timeout_ms: int) -> str:
    loc = await resolve_locator(page, _BIRTH_DATE_INPUT_SELECTOR, timeout_ms)
    input_type = ""
    try:
        input_type = (await loc.get_attribute("type") or "").lower()
    except Exception:
        input_type = ""
    birthdate = _birth_date_for_age(age, html_date=(input_type == "date"))
    await loc.fill(birthdate)
    return birthdate


# --------------------------------------------------------------------------- #
# selector / interaction helpers
# --------------------------------------------------------------------------- #
def _split_selectors(selector: str) -> List[str]:
    return [piece.strip() for piece in selector.split(",") if piece.strip()]


async def resolve_locator(page, selector: str, timeout_ms: int, require_enabled: bool = False):
    """Return the first visible (and optionally enabled) locator for ``selector``.

    Tries each comma-separated candidate in order, polling until ``timeout_ms``.
    """
    candidates = _split_selectors(selector)
    if not candidates:
        raise PlaywrightStepError(f"empty selector: {selector!r}")

    deadline = time.monotonic() + max(0.1, timeout_ms / 1000)
    last_detail = "no match"
    while time.monotonic() < deadline:
        for cand in candidates:
            try:
                loc = page.locator(cand).first
                if await loc.count() == 0:
                    continue
                if not await loc.is_visible():
                    continue
                if require_enabled and not await loc.is_enabled():
                    continue
                return loc
            except Exception as exc:  # malformed candidate / transient DOM
                last_detail = f"{cand}: {exc}"
                continue
        await asyncio.sleep(0.25)
    raise PlaywrightStepError(f"selector not ready: {selector!r} ({last_detail})")


async def fill_field(page, selector: str, value: str, timeout_ms: int) -> None:
    loc = await resolve_locator(page, selector, timeout_ms)
    await loc.fill(value)


async def click_button(page, selector: str, timeout_ms: int) -> None:
    loc = await resolve_locator(page, selector, timeout_ms, require_enabled=True)
    await loc.click()


async def click_optional(page, selector: str, timeout_ms: int) -> bool:
    """Click if the element shows up within ``timeout_ms``; never raises."""
    try:
        loc = await resolve_locator(page, selector, timeout_ms, require_enabled=True)
        await loc.click()
        return True
    except Exception:
        return False


async def is_present(page, selector: str) -> bool:
    """True if any candidate selector currently resolves to a visible element."""
    for cand in _split_selectors(selector):
        try:
            loc = page.locator(cand).first
            if await loc.count() > 0 and await loc.is_visible():
                return True
        except Exception:
            continue
    return False


async def _page_has_any_text(page, keywords) -> bool:
    try:
        text = await page.locator("body").inner_text(timeout=2000)
    except Exception:
        return False
    lower = text.lower()
    return any(str(keyword).lower() in lower for keyword in keywords if keyword)


async def _reset_if_logged_in_start(page, url: str, register_job_id: str) -> None:
    """If the registration URL lands in an existing ChatGPT session, clear it."""
    if not (await _page_has_any_text(page, _CHATGPT_APP_MARKERS)):
        return
    if await is_present(page, config.free_register_selector) or await is_present(page, config.email_input_selector):
        return

    log.warning("[register %s] registration URL landed in logged-in ChatGPT; clearing context and retrying", register_job_id)
    try:
        await browser_manager.reset_context_storage(page.context)
    except Exception as exc:
        log.warning("[register %s] context storage reset failed: %s", register_job_id, str(exc)[:120])
    try:
        await page.goto("about:blank", wait_until="domcontentloaded")
    except Exception:
        pass
    await page.goto(url, wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=12000)
    except Exception:
        pass
    await asyncio.sleep(1.0)


async def save_screenshot(page, job_id: str, label: str = "error") -> str:
    """Best-effort full-page screenshot. Returns the path or ``""``."""
    if page is None:
        return ""
    try:
        config.screenshot_path.mkdir(parents=True, exist_ok=True)
        path = config.screenshot_path / f"{job_id}_{label}_{now()}.png"
        await page.screenshot(path=str(path), full_page=True)
        log.info("Saved screenshot %s", path)
        return str(path)
    except Exception as exc:
        log.warning("Failed to save screenshot: %s", exc)
        return ""


def status_for_exc(exc: BaseException) -> str:
    """Map an exception to a terminal job status (timeout vs failed)."""
    if isinstance(exc, (asyncio.TimeoutError, PlaywrightTimeoutError, SmsTimeout, TimeoutError)):
        return "timeout"
    return "failed"


def is_terminal(status: Optional[str]) -> bool:
    return status in _TERMINAL_STATUSES


# --------------------------------------------------------------------------- #
# browser session
# --------------------------------------------------------------------------- #
# Browser/context acquisition lives in app/browser_manager.py (supports both the
# bundled Playwright Chromium and a local Chrome over CDP). Workers use
# ``browser_manager.browser_session(trace_name, headless=...)``.


async def _wait_for_success(page) -> None:
    """Optional success detection after account creation."""
    if config.success_url_keyword:
        try:
            await page.wait_for_url(lambda u: config.success_url_keyword in u, timeout=config.browser_timeout_ms)
        except Exception:
            pass
    if config.success_text_keyword:
        try:
            await page.get_by_text(config.success_text_keyword).first.wait_for(timeout=config.browser_timeout_ms)
        except Exception:
            pass
    if not (config.success_url_keyword or config.success_text_keyword):
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# registration steps (reused by the combined worker)
# --------------------------------------------------------------------------- #
async def perform_registration(
    page,
    *,
    register_job_id: str,
    url: str,
    email: str,
    name: str,
    age: int,
    code_timeout: int,
) -> dict:
    """Drive the web email-registration form on ``page``.

    Updates the ``register_jobs`` row as it progresses. Raises on any failed
    step (caller is responsible for the terminal status + screenshot).
    """
    t = config.browser_timeout_ms
    database.update_register_job(register_job_id, status="running", started_at=now())

    log.info("[register %s] open %s", register_job_id, url)
    await page.goto(url, wait_until="domcontentloaded")
    # Let the SPA hydrate before the first click; otherwise the button is in the
    # DOM but its click handler isn't attached yet and the click is a no-op
    # (observed on chatgpt.com: the login/register modal never opened).
    try:
        await page.wait_for_load_state("networkidle", timeout=12000)
    except Exception:
        pass
    await asyncio.sleep(1.0)
    await _reset_if_logged_in_start(page, url, register_job_id)

    log.info("[register %s] click free-register", register_job_id)
    if not await is_present(page, config.free_register_selector):
        current_url = ""
        try:
            current_url = page.url
        except Exception:
            current_url = ""
        raise PlaywrightStepError(
            f"registration start page has no free-register button after reset (url={current_url})"
        )
    await click_button(page, config.free_register_selector, t)

    # The 免费注册 click can be a no-op if the SPA hasn't attached its handler yet
    # (observed on chatgpt.com: the email modal never opens, then the email input
    # is "not found" and a perfectly good email is burned as 'failed'). Re-click
    # 免费注册 until the email input actually appears (up to 3 tries).
    for i in range(3):
        if await is_present(page, config.email_input_selector):
            break
        await asyncio.sleep(2.5)
        if await is_present(page, config.email_input_selector):
            break
        log.info("[register %s] email box not open yet; re-click free-register (%d/3)", register_job_id, i + 1)
        await click_optional(page, config.free_register_selector, 5000)

    log.info("[register %s] fill email", register_job_id)
    await fill_field(page, config.email_input_selector, email, t)
    # Mark the instant the verification email is triggered, so Project A only
    # returns the freshly-sent code (send-then-poll), never a stale stored one.
    code_since = now()
    await click_button(page, config.email_continue_selector, t)

    log.info("[register %s] fetch email code from Project A (since=%s)", register_job_id, code_since)
    code = await asyncio.to_thread(
        mail_code_client.get_verification_code,
        email,
        code_timeout,
        config.email_code_pattern,
        config.email_code_subject_keyword,
        config.email_code_from_keyword,
        code_since,
    )
    database.update_register_job(register_job_id, email_verification_code=code)

    log.info("[register %s] fill email code", register_job_id)
    await fill_field(page, config.email_code_input_selector, code, t)
    await click_button(page, config.email_code_continue_selector, t)

    # The name/age + "complete account" page is OPTIONAL. In the current
    # ChatGPT flow the account is created and logged in right after the email
    # code, with no name/age page. If that page DOES appear we fill it;
    # otherwise we treat the account as created & logged in and move on.
    name_selector = _append_selectors(config.name_input_selector, _NAME_INPUT_EXTRA_SELECTOR)
    try:
        name_loc = await resolve_locator(page, name_selector, 10000)
    except PlaywrightStepError:
        name_loc = None

    if name_loc is not None:
        log.info("[register %s] fill name + age/birthdate", register_job_id)
        await name_loc.fill(name)
        try:
            await fill_field(page, config.age_input_selector, str(age), 5000)
        except PlaywrightStepError:
            birthdate = await _fill_birth_date(page, age, t)
            log.info("[register %s] age field not present; filling birthdate=%s", register_job_id, birthdate)
        log.info("[register %s] complete account creation", register_job_id)
        # Click "完成账户创建" (the black button, 操作流程.md 图四 红框③). It may be a
        # styled [role=button] (not a <button>) and/or only become enabled a beat
        # after the fields are filled, so: broadened selector + best-effort retry,
        # and treat "left the name/age page" as success (this also catches a
        # manual click). NEVER block 60s on click_button -- that wrongly failed an
        # account that WAS created (the page had already advanced to 你已准备就绪).
        for _ in range(3):
            if not await is_present(page, name_selector):
                break
            await click_optional(page, config.complete_button_selector, 5000)
            await asyncio.sleep(1.5)
        if await is_present(page, name_selector):
            raise PlaywrightStepError(
                "could not complete account creation (still on the name/age page)"
            )
        log.info("[register %s] account creation submitted (left name/age page)", register_job_id)
    else:
        log.info("[register %s] no name/age page detected; account created & logged in", register_job_id)

    await _wait_for_success(page)
    log.info("[register %s] registration steps done (logged in)", register_job_id)
    return {"email": email, "name": name, "age": age, "email_verification_code": code}


# --------------------------------------------------------------------------- #
# finalizers
# --------------------------------------------------------------------------- #
def finalize_register_success(job_id: str, email: str, name: str, age: int, record_account: bool = True) -> None:
    database.update_register_job(job_id, status="success", finished_at=now())
    # Registration succeeded but the account is NOT in Sub2API yet -> 'registered'
    # (recoverable via the import-only resume path). The email is marked 'used'
    # only after the Sub2API import succeeds (see sub2api_import_worker).
    email_pool.mark_registered(email, job_id)
    if record_account:
        database.upsert_registered_account(email=email, name=name, age=age, source_register_job_id=job_id)
    log.info("[register %s] SUCCESS (email marked 'registered')", job_id)


def finalize_register_failure(job_id: str, email: str, exc: BaseException, screenshot: str) -> None:
    msg = str(exc)[:1000]
    status = status_for_exc(exc)
    database.update_register_job(
        job_id, status=status, error_message=msg, screenshot_path=screenshot or None, finished_at=now()
    )
    if email:
        # A dead mailbox (revoked refresh_token / service-abuse mode) is
        # terminal -> 'unavailable'. A transient IMAP mailbox fault is moved to
        # 'deferred' so it leaves the active batch but is not treated as dead.
        # Any other failure is retryable -> 'failed'.
        if isinstance(exc, MailboxUnavailableError):
            email_pool.mark_unavailable(email, job_id, msg)
        elif isinstance(exc, MailboxDeferredError):
            email_pool.mark_deferred(email, job_id, msg)
        else:
            email_pool.mark_failed(email, job_id, msg)
    log.error("[register %s] %s: %s", job_id, status.upper(), msg)


# --------------------------------------------------------------------------- #
# standalone register job
# --------------------------------------------------------------------------- #
async def run_register_job(
    job_id: str,
    *,
    url: str,
    email: str,
    name: str,
    age: int,
    headless: bool,
    code_timeout: int,
) -> None:
    try:
        async with browser_manager.browser_session(f"register_{job_id}", headless=headless) as context:
            page = await browser_manager.claim_page(context)
            try:
                await perform_registration(
                    page, register_job_id=job_id, url=url, email=email, name=name, age=age, code_timeout=code_timeout
                )
            except Exception as exc:
                shot = await save_screenshot(page, job_id, "register_error")
                finalize_register_failure(job_id, email, exc, shot)
                return
            finalize_register_success(job_id, email, name, age, record_account=True)
    except Exception as exc:  # browser launch / context-level failure
        current = database.get_register_job(job_id)
        if not current or not is_terminal(current.get("status")):
            finalize_register_failure(job_id, email, exc, "")
