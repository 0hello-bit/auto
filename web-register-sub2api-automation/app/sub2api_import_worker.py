"""Sub2API OAuth import worker.

Depends ONLY on the unified SMS provider (via the factory) -- never on a
concrete SMS platform. Flow (within an already-open browser context):

    generate-auth-url -> parse expected_state -> open auth_url in a new page
    -> click continue -> (if phone required) buy number, fill, wait SMS, fill
    -> capture /auth/callback?code=&state= from the network
    -> validate state -> create-from-oauth -> record account
    -> finish SMS order (success) / cancel SMS order (failure)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Iterable, Optional

from . import browser_manager, database, email_pool, mail_code_client, register_worker, sub2api_client
from .config import config
from .database import now
from .mail_code_client import MailboxDeferredError, MailboxUnavailableError
from .oauth_network_capture import OAuthCallbackCapture, extract_query_param
from .sms_provider_base import COUNTRY_INFO, SmsError, SmsOrder, SmsProvider
from .sms_provider_factory import build_sms_provider

log = logging.getLogger(__name__)

_SMS_BUY_NUMBER_STOCK_RETRIES = 5
_SMS_BUY_NUMBER_RETRY_DELAY_SECONDS = 2.0
_SMS_STOCK_ERROR_KEYWORDS = ("no free phones", "out of stock", "no product")


# --------------------------------------------------------------------------- #
# finalizers / SMS order helpers
# --------------------------------------------------------------------------- #
def _is_sms_stock_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(keyword in msg for keyword in _SMS_STOCK_ERROR_KEYWORDS)


async def _buy_sms_number_with_stock_retries(sms_provider: SmsProvider, import_job_id: str) -> SmsOrder:
    max_attempts = 1 + _SMS_BUY_NUMBER_STOCK_RETRIES
    for buy_attempt in range(1, max_attempts + 1):
        try:
            return await asyncio.to_thread(sms_provider.buy_number)
        except SmsError as exc:
            if not _is_sms_stock_error(exc) or buy_attempt >= max_attempts:
                raise
            log.warning(
                "[import %s] SMS number stock issue while buying (%d/%d): %s; retrying",
                import_job_id, buy_attempt, max_attempts, str(exc)[:120],
            )
            await asyncio.sleep(_SMS_BUY_NUMBER_RETRY_DELAY_SECONDS)

    raise SmsError("SMS number purchase failed without returning an order")


def finalize_import_failure(
    import_job_id: str,
    register_job_id: Optional[str],
    exc: BaseException,
    screenshot: str,
) -> None:
    msg = str(exc)[:900]
    status = register_worker.status_for_exc(exc)
    if screenshot:
        msg = f"{msg} (screenshot: {screenshot})"
    database.update_import_job(import_job_id, status=status, error_message=msg[:1000], finished_at=now())
    if register_job_id and screenshot:
        database.update_register_job(register_job_id, screenshot_path=screenshot)
    log.error("[import %s] %s: %s", import_job_id, status.upper(), msg)


async def _cancel_order_safe(provider: Optional[SmsProvider], order: Optional[SmsOrder]) -> None:
    """Cancel the SMS order on failure; never overrides the original error."""
    if not (provider and order and config.sms_auto_cancel_on_fail):
        return
    try:
        await asyncio.to_thread(provider.cancel_order, order)
    except Exception as exc:
        log.warning("[sms] cancel_order failed (ignored): %s", exc)


async def _finish_order_safe(provider: Optional[SmsProvider], order: Optional[SmsOrder]) -> None:
    if not (provider and order and config.sms_auto_finish_order):
        return
    try:
        await asyncio.to_thread(provider.finish_order, order)
    except Exception as exc:
        log.warning("[sms] finish_order failed (ignored): %s", exc)


# --------------------------------------------------------------------------- #
# phone-step country selection (OAuth phone verification)
# --------------------------------------------------------------------------- #
def _national_number(phone: str, dial: Optional[str]) -> str:
    """Strip the leading dial code so the number matches the selected country."""
    p = (phone or "").strip()
    if config.phone_strip_country_code and dial and p.startswith(dial):
        return p[len(dial):]
    return p


async def _select_native_country(loc, info, import_job_id) -> bool:
    """Select the matching <option> on a native <select> by zh/en name or dial."""
    try:
        labels = await loc.locator("option").all_text_contents()
    except Exception:
        labels = []
    zh, en, dial = info["zh"], info["en"].lower(), info["dial"]
    chosen = None
    for lab in labels:
        low = (lab or "").lower()
        if zh in (lab or "") or en in low or dial in (lab or ""):
            chosen = lab
            break
    if chosen is None:
        return False
    try:
        await loc.select_option(label=chosen)
        log.info("[import %s] phone country via <select>: %s", import_job_id, chosen.strip())
        return True
    except Exception:
        return False


async def _select_phone_country(page, country_key: str, import_job_id: str) -> Optional[dict]:
    """Set the phone-step country dropdown to match the SMS country.

    The OAuth phone form defaults to US (+1); the purchased number is from
    ``country_key`` (e.g. argentina/+54), so the country must be switched first.
    Returns the matched country info (for dial-code stripping) or None.
    """
    info = COUNTRY_INFO.get((country_key or "").strip().lower())
    if not info:
        log.info("[import %s] no phone-country mapping for %r; leaving form default", import_job_id, country_key)
        return None

    # 1) native <select>: match the option by zh/en name or dial code.
    for sel in register_worker._split_selectors(config.phone_country_select_selector):
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                if await _select_native_country(loc, info, import_job_id):
                    return info
        except Exception:
            continue

    # 2) custom dropdown: open -> (optional search) -> click option
    if not await register_worker.click_optional(page, config.phone_country_trigger_selector, 5000):
        log.warning("[import %s] phone-country dropdown trigger not found; leaving form default", import_job_id)
        return info
    await asyncio.sleep(0.6)
    for sel in register_worker._split_selectors(config.phone_country_search_selector):
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.fill(info["en"])
                await asyncio.sleep(0.6)
                break
        except Exception:
            continue
    for text in (info["zh"], info["en"], info["dial"]):
        opt = f'[role="option"]:has-text("{text}"), li:has-text("{text}"), [role="button"]:has-text("{text}")'
        if await register_worker.is_present(page, opt):
            try:
                await register_worker.click_button(page, opt, 6000)
                log.info("[import %s] phone country selected: %s", import_job_id, text)
                return info
            except Exception:
                continue
    log.warning("[import %s] could not select phone country %r; leaving form default", import_job_id, country_key)
    return info


async def _select_oauth_account(page, email: Optional[str], import_job_id: str) -> bool:
    """OAuth 'choose-an-account' page: click the account card matching our email.

    The card is a <button> containing the email; ``button:has-text(<local-part>)``
    uniquely matches it. Best-effort: returns True if a card was clicked.
    """
    if not email:
        return False
    local = (email.split("@")[0] or email).strip()
    if not local:
        return False
    for cand in (
        f'button:has-text("{local}")',
        f'[role="button"]:has-text("{local}")',
        f'a:has-text("{local}")',
        f'li:has-text("{local}")',
    ):
        if await register_worker.is_present(page, cand):
            try:
                await register_worker.click_button(page, cand, 8000)
                log.info("[import %s] selected OAuth account (%s)", import_job_id, email)
                return True
            except Exception:
                continue
    log.info("[import %s] no account-picker card matched %s (maybe auto-continued)", import_job_id, email)
    return False


async def _wait_for_email_code_page(page, import_job_id: str) -> bool:
    """Wait for the email verification-code input after submitting the email.

    Robust against a slow SPA / Cloudflare: polls for the code input for up to
    ~30s and, if the email field is still showing (the submit didn't land),
    re-clicks 继续 once. Returns True if the code input became present.
    """
    deadline = time.monotonic() + 30
    reclicked = False
    otp_clicked = False
    while time.monotonic() < deadline:
        if await register_worker.is_present(page, config.email_code_input_selector):
            return True
        if not otp_clicked and await _is_password_login_page(page):
            log.info("[import %s] password page shown; switching to one-time-code login", import_job_id)
            if await _click_one_time_code_login(page, import_job_id):
                otp_clicked = True
                await asyncio.sleep(1.2)
                continue
        # Code field not here yet. If the email field still shows, the submit may
        # not have registered (button enabled late / SPA) -> re-click once.
        if not reclicked and await register_worker.is_present(page, config.email_input_selector):
            await asyncio.sleep(1.2)
            if not await register_worker.is_present(page, config.email_code_input_selector):
                await register_worker.click_optional(page, config.email_continue_selector, 3000)
                reclicked = True
        await asyncio.sleep(1.0)
    return await register_worker.is_present(page, config.email_code_input_selector)


_PASSWORD_PAGE_KEYWORDS = ("输入密码", "需要输入密码", "Enter password", "Password")
_ONE_TIME_CODE_LOGIN_SELECTORS = (
    'button:has-text("使用一次性验证码登录"), [role="button"]:has-text("使用一次性验证码登录"), '
    'a:has-text("使用一次性验证码登录"), button:has-text("一次性验证码"), '
    '[role="button"]:has-text("一次性验证码"), a:has-text("一次性验证码"), '
    'button:has-text("one-time code"), [role="button"]:has-text("one-time code"), '
    'a:has-text("one-time code"), button:has-text("email code"), '
    '[role="button"]:has-text("email code"), a:has-text("email code")'
)


async def _is_password_login_page(page) -> bool:
    has_password_input = await register_worker.is_present(
        page,
        'input[type="password"], input[name="password"], input[autocomplete="current-password"]',
    )
    return has_password_input or await _page_has_any_text(page, _PASSWORD_PAGE_KEYWORDS)


async def _click_one_time_code_login(page, import_job_id: str) -> bool:
    clicked = await register_worker.click_optional(page, _ONE_TIME_CODE_LOGIN_SELECTORS, 5000)
    if not clicked:
        log.warning("[import %s] password page detected but one-time-code login link was not clickable", import_job_id)
    return clicked


async def _handle_auth_email_login(page, email: Optional[str], import_job_id: str) -> bool:
    """Scenario 1 (接码.md 图1-2): the OAuth page asks to re-enter email + email code.

    "欢迎回来" + an email input ("电子邮件地址") -> fill the email, submit, wait for
    the "检查你的收件箱" code page, fetch the code from Project A and submit it.

    Returns True if this WAS the email-login page (handled here), so the caller
    skips the account-picker path. Raises if the code page never appears or the
    code can't be obtained -- we must never leave the code field blank and then
    let a downstream best-effort "继续" submit an empty form (the original bug).
    """
    if not email:
        return False
    if not await register_worker.is_present(page, config.email_input_selector):
        return False  # not the email-login page (likely the account picker -> Scenario 2)

    t = config.browser_timeout_ms
    log.info("[import %s] auth page requires email re-login; filling email", import_job_id)
    await register_worker.fill_field(page, config.email_input_selector, email, t)
    # Mark the instant the (re-)send is triggered so Project A returns only the
    # freshly-sent code, never a stale stored one.
    since = now()
    await register_worker.click_button(page, config.email_continue_selector, t)

    if not await _wait_for_email_code_page(page, import_job_id):
        raise register_worker.PlaywrightStepError(
            "auth email-code input never appeared after submitting the email"
        )

    log.info("[import %s] fetching auth email code from Project A (since=%s)", import_job_id, since)
    code = await asyncio.to_thread(
        mail_code_client.get_verification_code,
        email, config.code_timeout_seconds,
        config.email_code_pattern, config.email_code_subject_keyword, config.email_code_from_keyword, since,
    )
    log.info("[import %s] filling auth email code", import_job_id)
    await register_worker.fill_field(page, config.email_code_input_selector, code, t)
    await register_worker.click_button(page, config.email_code_continue_selector, t)
    await asyncio.sleep(2.0)

    # Best-effort sanity check: if we're still on the code page with an error,
    # log it loudly (the import will then surface the real failure downstream).
    if await register_worker.is_present(page, config.email_code_input_selector):
        log.warning(
            "[import %s] still on the email-code page after submitting the code "
            "(code may have been rejected)", import_job_id,
        )
    else:
        log.info("[import %s] auth email login submitted (left code page)", import_job_id)
    return True


async def _page_has_any_text(page, keywords) -> bool:
    """True if any of the keyword strings is visible on the page."""
    for kw in keywords:
        kw = (kw or "").strip()
        if not kw:
            continue
        try:
            if await page.get_by_text(kw, exact=False).count() > 0:
                return True
        except Exception:
            continue
    return False


async def _go_back_to_phone(page, import_job_id: str) -> None:
    """Return to the phone-entry page after a rejected number ("返回上一个界面")."""
    if await register_worker.click_optional(page, config.phone_back_selector, 3000):
        await asyncio.sleep(1.0)
        return
    try:
        await page.go_back()
        await asyncio.sleep(1.5)
    except Exception:
        pass


async def _return_to_phone_form(page, import_job_id: str, wait_secs: int = 8) -> bool:
    """Return from the SMS-code page to the phone form and verify it worked."""
    await _go_back_to_phone(page, import_job_id)
    for _ in range(max(1, wait_secs)):
        if await register_worker.is_present(page, config.phone_input_selector):
            log.info("[import %s] returned to phone form for a new number", import_job_id)
            return True
        if await _is_ready_or_consent_page(page):
            return False
        await asyncio.sleep(1.0)
    return await register_worker.is_present(page, config.phone_input_selector)


async def _await_code_page(page, secs: int) -> bool:
    """Wait up to ``secs`` for the SMS-code input. False on error text or timeout."""
    for _ in range(max(1, secs)):
        if await _page_has_any_text(page, config.phone_error_keywords):
            return False
        if await register_worker.is_present(page, config.sms_code_input_selector):
            return True
        await asyncio.sleep(1.0)
    return False

async def _select_phone_delivery(
    page,
    selector: str,
    label: str,
    import_job_id: str,
    keywords: Optional[Iterable[str]] = None,
) -> bool:
    """Click the Text Message/SMS/短信 or WhatsApp option if it is visible.

    Uses INSTANT presence checks (count + is_visible, no per-candidate polling):
    probing many non-existent selectors with a 2.5s poll each used to waste up to
    ~90s and make the flow look stuck. A short 2-pass retry covers a late render.
    Only real controls (button / role=button / role=radio / label) are matched --
    never div/span (which would match a huge ancestor and mis-click).
    """
    candidates: List[str] = []
    if selector:
        candidates.extend(register_worker._split_selectors(selector))
    for kw in keywords or ():
        candidates.extend([
            f'button:has-text("{kw}")',
            f'[role="radio"]:has-text("{kw}")',
            f'[role="button"]:has-text("{kw}")',
            f'label:has-text("{kw}")',
        ])

    for attempt in range(2):
        seen = set()
        for cand in candidates:
            cand = (cand or "").strip()
            if not cand or cand in seen:
                continue
            seen.add(cand)
            try:
                loc = page.locator(cand).first
                if await loc.count() > 0 and await loc.is_visible() and await loc.is_enabled():
                    await loc.click()
                    log.info("[import %s] selected phone delivery method: %s via %s", import_job_id, label, cand)
                    return True
            except Exception:
                continue
        if attempt == 0:
            await asyncio.sleep(0.5)  # allow a late-rendering option to appear

    log.info("[import %s] phone delivery method not found, skip: %s", import_job_id, label)
    return False


async def _submit_phone_and_wait_code_page(page, import_job_id: str, wait_secs: int) -> bool:
    """Click continue and check whether we entered the SMS-code page."""
    await register_worker.click_button(page, config.phone_continue_selector, config.browser_timeout_ms)
    await asyncio.sleep(2.0)

    if await _page_has_any_text(page, config.phone_error_keywords):
        return False

    return await _await_code_page(page, wait_secs)

async def _record_sms_order(import_job_id, register_job_id, order) -> None:
    log.info(
        "[import %s] sms order: provider=%s country=%s product=%s strategy=%s "
        "operator=%s price=%s success_rate=%s order_id=%s phone=%s",
        import_job_id, order.provider, order.country, order.product, order.strategy,
        order.operator, order.price, order.success_rate, order.order_id, order.phone_number,
    )
    if register_job_id:
        database.update_register_job(
            register_job_id,
            phone_number=order.phone_number, sms_provider=order.provider, sms_order_id=order.order_id,
            sms_country=order.country, sms_product=order.product, sms_operator=order.operator,
            sms_operator_strategy=order.strategy, sms_price=order.price, sms_success_rate=order.success_rate,
        )


# --------------------------------------------------------------------------- #
# phone delivery-method detection (SMS vs WhatsApp)
# --------------------------------------------------------------------------- #
# Keywords that identify a regular SMS / Text Message delivery option.
_SMS_OPTION_KEYWORDS = ("Text Message", "Text message", "SMS", "短信", "短消息", "文本消息")
# Keywords / copy that identify WhatsApp delivery (an option OR explanatory text).
_WHATSAPP_KEYWORDS = (
    "WhatsApp", "Whats App", "通过 WhatsApp", "WhatsApp 向该号码",
    "via WhatsApp", "through WhatsApp", "send a one-time code via WhatsApp",
)
_SMS_TO_WHATSAPP_FALLBACK_KEYWORDS = (
    "无法向该电话号码发送短信", "无法发送短信", "不能发送短信",
    "已切换为 WhatsApp", "切换为 WhatsApp", "继续通过 WhatsApp",
    "can't send a text message", "cannot send a text message",
    "switched to WhatsApp", "continue with WhatsApp",
)


async def _has_clickable_option(page, keywords) -> bool:
    """True if any keyword resolves to a visible *clickable control* (a button /
    radio / labelled option), as opposed to plain body text."""
    for kw in keywords:
        kw = (kw or "").strip()
        if not kw:
            continue
        for sel in (
            f'button:has-text("{kw}")',
            f'[role="radio"]:has-text("{kw}")',
            f'[role="button"]:has-text("{kw}")',
            f'label:has-text("{kw}")',
        ):
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible() and await loc.is_enabled():
                    return True
            except Exception:
                continue
    return False


async def _detect_phone_delivery_mode(page) -> str:
    """Classify how the phone-verification page delivers the code.

    Returns:
      - "selectable":    a Text Message/SMS/短信 OR WhatsApp option is clickable
                         (the user gets to choose -> we will pick SMS).
      - "whatsapp_only": no selectable option, but the page clearly states the
                         code is sent via WhatsApp.
      - "default":       cannot be classified -> follow the page's default.
    """
    sms_option = await _has_clickable_option(page, _SMS_OPTION_KEYWORDS)
    whatsapp_option = await _has_clickable_option(page, _WHATSAPP_KEYWORDS)
    if sms_option or whatsapp_option:
        return "selectable"
    # No selectable controls. Does the body text say it will use WhatsApp?
    if await _page_has_any_text(page, _WHATSAPP_KEYWORDS):
        return "whatsapp_only"
    return "default"


async def _is_sms_forced_to_whatsapp(page) -> bool:
    """True when the page says SMS cannot be used and it switched to WhatsApp."""
    return await _page_has_any_text(page, _SMS_TO_WHATSAPP_FALLBACK_KEYWORDS)


# --------------------------------------------------------------------------- #
# "ready / consent" page + generic proceed + callback-aware SMS wait
# --------------------------------------------------------------------------- #
# The final "you're all set / ready" page (and similar consent pages) appear
# right before the OAuth callback fires. There is NO phone field, NO SMS/WhatsApp
# option and NO "send code via" text -- just a 继续/Continue button. The flow must
# simply click it and let the callback fire, not wait for a code that never comes.
_READY_PAGE_KEYWORDS = (
    "你已准备就绪", "已准备就绪", "准备就绪",
    "you're all set", "you are all set", "you're ready", "you are ready",
    "all set", "setup complete", "开始使用", "开始聊天",
)

# An OAuth consent / authorize page (e.g. "同意使用 Codex" / "使用 ChatGPT 登录到 ...")
# shown BEFORE the phone step. It must be advanced with 继续/授权/Authorize first;
# otherwise the flow looks for a phone field that isn't there yet and stalls.
_CONSENT_PAGE_KEYWORDS = (
    "同意使用", "使用 ChatGPT 登录到", "登录到", "Codex",
    "请求访问", "授权", "authorize", "allow access", "grant access", "consent",
    "继续操作即表示", "并已阅读",
)


async def _is_ready_or_consent_page(page) -> bool:
    """True if the page is the final 'ready' page (account done, no phone/code needed)."""
    return await _page_has_any_text(page, _READY_PAGE_KEYWORDS)


async def _is_consent_page(page) -> bool:
    """True if the page is an OAuth consent/authorize page that must be advanced
    with 继续/授权 before the phone step (e.g. the '同意使用 Codex' page)."""
    return await _page_has_any_text(page, _CONSENT_PAGE_KEYWORDS)


# 图7: a "查看你的手机 / check your phone" code challenge that appears AFTER the
# Codex consent (图6). It asks for a code sent to a DIFFERENT number we can't read,
# so per 接码.md it must NOT be filled -- abandon the page and reopen a fresh
# Sub2API auth_url (the new flow goes email-relogin/account-pick -> callback).
# NOTE: our own first SMS-code page is also titled "查看你的手机", but that one is
# filled inside _handle_phone_verification and is never seen by the main loop;
# any "查看你的手机" reaching the main loop is this post-consent second gate.
_CHECK_PHONE_PAGE_KEYWORDS = ("查看你的手机", "查看您的手机", "check your phone")


async def _is_check_phone_page(page) -> bool:
    """True for the 图7 '查看你的手机' second-verification gate (post-consent)."""
    return await _page_has_any_text(page, _CHECK_PHONE_PAGE_KEYWORDS)


# The OAuth START page ("登录或注册" / log-in-or-create-account). It is handled
# legitimately BEFORE the main loop (email re-login / account picker). If it
# REAPPEARS during the main loop, the flow has regressed -- e.g. a failed phone
# attempt's back-navigation over-shot past the phone page back to the start. The
# clean recovery is to reopen a FRESH Sub2API auth_url (same as 图7).
_OAUTH_START_KEYWORDS = ("登录或注册", "log in or sign up", "log-in-or-create-account")


async def _is_oauth_start_page(page) -> bool:
    """True for the OAuth start page ('登录或注册') reappearing mid-import."""
    return await _page_has_any_text(page, _OAUTH_START_KEYWORDS)


async def _click_proceed(page, import_job_id: str, timeout_ms: int = 2500) -> bool:
    """Click any continue/authorize/'ready' button to advance the flow.

    Tries the configured AUTH_CONTINUE_SELECTOR first, then broader fallbacks
    (role=button, links, English 'Continue', 同意/Agree/授权/Authorize/Allow,
    下一步/完成) so the big black 继续 button on the 你已准备就绪 page and the
    consent ('同意使用 Codex') button are reliably clicked even when not a
    plain <button>.
    """
    if await register_worker.click_optional(page, config.auth_continue_selector, timeout_ms):
        return True
    extra = (
        '[role="button"]:has-text("继续"), a:has-text("继续"), div[role="button"]:has-text("继续"), '
        'button:has-text("Continue"), [role="button"]:has-text("Continue"), '
        'button:has-text("同意"), [role="button"]:has-text("同意"), button:has-text("Agree"), '
        'button:has-text("授权"), button:has-text("Authorize"), button:has-text("Allow"), '
        'button:has-text("下一步"), button:has-text("完成"), button:has-text("Done")'
    )
    return await register_worker.click_optional(page, extra, timeout_ms)


async def _wait_sms_or_callback(sms_provider, order, capture, timeout: int):
    """Wait for the SMS code, but abort the instant the OAuth callback is captured
    (e.g. the user finished verification manually, or no SMS was actually needed).

    Returns the 6-digit code, or ``None`` if the callback fired first. Re-raises
    the provider's SmsTimeout/error if no code arrived and no callback fired.
    """
    def _swallow(task):
        # Retrieve any result/exception so a cancelled background task does not
        # log "exception was never retrieved".
        if not task.cancelled():
            task.exception()

    sms_task = asyncio.ensure_future(asyncio.to_thread(sms_provider.wait_code, order, timeout))
    cap_task = asyncio.ensure_future(capture.event.wait())
    await asyncio.wait({sms_task, cap_task}, return_when=asyncio.FIRST_COMPLETED)
    if capture.captured:
        sms_task.cancel()
        sms_task.add_done_callback(_swallow)
        cap_task.cancel()
        return None
    cap_task.cancel()
    cap_task.add_done_callback(_swallow)
    # sms_task finished (code or exception) -> surface its result/exception.
    return sms_task.result()


class _RegenerateAuthUrl(Exception):
    """Raised when the OAuth flow itself must be restarted with a fresh auth_url."""


async def _close_page_for_retry(page, import_job_id: str, reason: str):
    if page is None:
        return None
    try:
        if not page.is_closed():
            await page.goto("about:blank", wait_until="domcontentloaded", timeout=3000)
            log.info("[import %s] reset OAuth page before retry (%s)", import_job_id, reason)
            return page
    except Exception as exc:
        log.warning("[import %s] could not reset OAuth page before retry: %s", import_job_id, exc)
    return page


async def _handle_phone_verification(page, *, import_job_id, register_job_id, sms_provider, capture):
    """Phone verification.

    Before the SMS-code page is reached, rejected / WhatsApp-routed numbers are
    cancelled and retried on the same phone form. Once the SMS-code page is
    reached, the existing SMS wait / code-submit recovery behavior is preserved.
    """
    t = config.browser_timeout_ms
    attempts = max(1, config.sms_max_phone_attempts)
    # Resolve the country the number will be bought from (auto-picks the highest
    # delivery-rate country if none is configured) so the web form's country can
    # be set to the SAME country.
    try:
        form_country = await asyncio.to_thread(sms_provider.country_for_form)
    except Exception as exc:
        form_country = None
        log.warning("[import %s] could not resolve form country: %s", import_job_id, str(exc)[:80])
    log.info("[import %s] phone verification: country=%s", import_job_id, form_country)

    for attempt in range(1, attempts + 1):
        # The OAuth callback may already be captured (the user finished a step
        # manually, or no phone was actually required) -> stop immediately.
        if capture.captured:
            log.info("[import %s] OAuth callback already captured; skipping phone step", import_job_id)
            return None

        database.update_import_job(import_job_id, status="waiting_phone")

        # The phone form must be present to verify. If it is NOT, the flow has
        # moved on (e.g. the 你已准备就绪 / consent page, or no phone is required):
        # click 继续 to advance and let the callback fire -- DO NOT buy a number
        # (the old code bought one anyway and then crashed filling a missing field).
        if not await register_worker.is_present(page, config.phone_input_selector):
            if await _is_ready_or_consent_page(page):
                log.info("[import %s] ready/consent page at phone step; clicking continue (no phone needed)", import_job_id)
            await _click_proceed(page, import_job_id, 4000)
            await capture.wait(1.5)
            if capture.captured:
                return None
            if not await register_worker.is_present(page, config.phone_input_selector):
                log.info("[import %s] no phone form present; phone step not required -> let callback finish", import_job_id)
                return None

        log.info("[import %s] phone verification attempt %d/%d (country=%s)",
                 import_job_id, attempt, attempts, form_country)

        country_info = await _select_phone_country(page, form_country, import_job_id)

        # 国家已经切到接码国家后，再购买订单。
        order = await _buy_sms_number_with_stock_retries(sms_provider, import_job_id)
        await _record_sms_order(import_job_id, register_job_id, order)

        phone_to_fill = _national_number(order.phone_number, (country_info or {}).get("dial"))
        log.info("[import %s] filling phone %s (from %s)", import_job_id, phone_to_fill, order.phone_number)
        await register_worker.fill_field(page, config.phone_input_selector, phone_to_fill, t)

        # ---- detect how this page delivers the code ("Send code via": Text
        # Message / WhatsApp). When present, that block is on the SAME phone page
        # (接码.md 图4). Re-check once: it can render a beat after the number fills.
        delivery_mode = await _detect_phone_delivery_mode(page)
        if delivery_mode == "default":
            await asyncio.sleep(1.2)
            delivery_mode = await _detect_phone_delivery_mode(page)
        log.info("[import %s] phone delivery mode detected: %s", import_job_id, delivery_mode)

        if await _is_sms_forced_to_whatsapp(page) and not config.phone_allow_whatsapp_fallback:
            log.warning("[import %s] SMS was forced to WhatsApp; cancel + retry with a new number", import_job_id)
            await _cancel_order_safe(sms_provider, order)
            continue

        # WhatsApp-only page + the current SMS order can't receive WhatsApp:
        # waiting for an SMS code would just time out. Stay on the phone form,
        # cancel this number, and buy another one on the next loop iteration.
        if delivery_mode == "whatsapp_only" and not config.phone_allow_whatsapp_fallback:
            log.warning(
                "[import %s] WhatsApp-only phone page but current SMS order does not "
                "support WhatsApp; cancel + retry with a new number", import_job_id,
            )
            await _cancel_order_safe(sms_provider, order)
            continue

        code_ready = False
        if delivery_mode == "selectable":
            # CASE 1 (接码.md 图4): "Send code via" with Text Message + WhatsApp.
            # Prefer Text Message; if it does not advance to the code page, switch
            # to WhatsApp -- but only when the order can receive WhatsApp codes
            # (config-gated, per bug1: the 5sim openai SMS order cannot).
            await _select_phone_delivery(
                page,
                config.phone_text_message_selector,
                "Text Message/SMS/短信",
                import_job_id,
                keywords=("短信", "短消息", "文本消息", "SMS", "Text Message", "Text message"),
            )
            code_ready = await _submit_phone_and_wait_code_page(page, import_job_id, 8)
            if (
                not code_ready
                and await _is_sms_forced_to_whatsapp(page)
                and not config.phone_allow_whatsapp_fallback
            ):
                log.warning("[import %s] Text Message switched to WhatsApp; cancel + retry with a new number", import_job_id)
                await _cancel_order_safe(sms_provider, order)
                continue
            if (
                not code_ready
                and not capture.captured
                and not await _is_ready_or_consent_page(page)
                and not await _page_has_any_text(page, config.phone_error_keywords)
            ):
                if config.phone_allow_whatsapp_fallback:
                    log.info("[import %s] Text Message did not advance; trying WhatsApp", import_job_id)
                    if await _select_phone_delivery(
                        page, config.phone_whatsapp_selector, "WhatsApp",
                        import_job_id, keywords=("WhatsApp", "Whats App"),
                    ):
                        code_ready = await _submit_phone_and_wait_code_page(page, import_job_id, 12)
                else:
                    log.warning(
                        "[import %s] Text Message did not advance and WhatsApp fallback "
                        "disabled; cancel + new number", import_job_id,
                    )
        elif delivery_mode == "whatsapp_only":
            # fallback explicitly enabled -> the page only offers WhatsApp; continue.
            log.info("[import %s] WhatsApp fallback enabled; continuing on WhatsApp-only page", import_job_id)
            code_ready = await _submit_phone_and_wait_code_page(page, import_job_id, 12)
        else:
            # CASE 2 (接码.md 图4 变体): NO "Send code via" / no Text Message / no
            # WhatsApp option -> just click 继续 and proceed. Do NOT scan for
            # delivery buttons that do not exist (that probing looked "stuck"), and
            # do NOT gate on the code page -- the only path forward is the SMS code.
            log.info("[import %s] no delivery options on phone page; clicking continue directly", import_job_id)
            await register_worker.click_button(page, config.phone_continue_selector, t)
            await asyncio.sleep(1.5)
            code_ready = True

        # The flow may have advanced past the phone step WITHOUT asking for a
        # code (no code needed, or the user finished it manually) -> the
        # 你已准备就绪/consent page is showing, or the callback already fired.
        # Treat as done: cancel the unused number, click 继续, let the callback finish.
        if capture.captured or await _is_ready_or_consent_page(page):
            log.info("[import %s] phone step done without SMS (ready page / callback captured); finishing", import_job_id)
            await _cancel_order_safe(sms_provider, order)
            await _click_proceed(page, import_job_id, 3000)
            return None

        # 号码被直接拒绝，比如"号码已被验证过"。还在手机号页时直接换号，
        # 不重开 OAuth 授权链接。
        if await _page_has_any_text(page, config.phone_error_keywords):
            log.warning("[import %s] number rejected at phone step; cancel + retry with a new number", import_job_id)
            await _cancel_order_safe(sms_provider, order)
            continue

        # 仍未进入验证码页面（常见：Text Message 不可用后页面切到 WhatsApp），
        # 则取消当前订单，在当前手机号页继续买新号并重新选择 Text Message。
        if not code_ready:
            log.warning("[import %s] code page not reached; cancel + retry with a new number", import_job_id)
            await _cancel_order_safe(sms_provider, order)
            continue

        log.info("[import %s] waiting for SMS code (timeout=%ss)", import_job_id, config.sms_timeout_seconds)
        database.update_import_job(import_job_id, status="waiting_sms")
        try:
            sms_code = await _wait_sms_or_callback(sms_provider, order, capture, config.sms_timeout_seconds)
        except Exception as exc:
            log.warning("[import %s] no SMS (%s); cancel + go back for a new number", import_job_id, str(exc)[:80])
            await _cancel_order_safe(sms_provider, order)
            if await _return_to_phone_form(page, import_job_id):
                continue
            log.warning("[import %s] could not return to phone form after SMS timeout; regenerate auth_url", import_job_id)
            raise _RegenerateAuthUrl("no SMS code in time and back-to-phone failed")
        # Callback fired while waiting (e.g. user completed it manually) -> done.
        if sms_code is None or capture.captured:
            log.info("[import %s] OAuth callback captured while waiting for SMS; finishing", import_job_id)
            return order
        if register_job_id:
            database.update_register_job(register_job_id, sms_verification_code=sms_code)

        await register_worker.fill_field(page, config.sms_code_input_selector, sms_code, t)
        await register_worker.click_button(page, config.sms_code_continue_selector, t)
        await asyncio.sleep(2.5)

        # Callback may fire right after submitting the code -> success.
        if capture.captured:
            return order

        # Success if we left the code page and there's no error text.
        still_on_code = await register_worker.is_present(page, config.sms_code_input_selector)
        has_error = await _page_has_any_text(page, config.phone_error_keywords)
        if still_on_code or has_error:
            log.warning("[import %s] code verification failed; cancel + regenerate auth_url", import_job_id)
            await _cancel_order_safe(sms_provider, order)
            raise _RegenerateAuthUrl("code verification failed")

        log.info("[import %s] phone verification SUCCESS on attempt %d", import_job_id, attempt)
        return order

    raise SmsError(f"phone verification failed after {attempts} attempts")


# --------------------------------------------------------------------------- #
# core flow
# --------------------------------------------------------------------------- #
async def perform_import(
    context,
    *,
    page=None,
    import_job_id: str,
    register_job_id: Optional[str],
    email: Optional[str],
    name: str,
    group_ids: Optional[Iterable[int]],
    concurrency: Optional[int],
    priority: Optional[int],
    enable_sms: bool,
    sms_provider_name: Optional[str],
    timeout: int,
    fivesim: Optional[dict] = None,
) -> dict:
    """Run the authorization + import flow inside ``context`` (a new page).

    On success: updates the import job to ``success``, records the account, and
    finishes the SMS order. On failure: screenshots, cancels the SMS order,
    marks the job failed/timeout, then re-raises.

    ``fivesim`` carries optional 5sim overrides (country/product/operator/
    strategy/max_price) that take precedence over the .env defaults.
    """
    page = page
    sms_provider: Optional[SmsProvider] = None
    sms_order: Optional[SmsOrder] = None
    fivesim = fivesim or {}

    try:
        # Build (and validate) the SMS provider up-front so misconfiguration
        # fails fast with an explicit message before any browser work.
        if enable_sms:
            sms_provider = build_sms_provider(
                sms_provider_name,
                fivesim_country=fivesim.get("country"),
                fivesim_product=fivesim.get("product"),
                fivesim_operator=fivesim.get("operator"),
                fivesim_operator_strategy=fivesim.get("strategy"),
                fivesim_max_price=fivesim.get("max_price"),
            )

        # One capture, re-armed per OAuth attempt (listeners attached once).
        capture = OAuthCallbackCapture(None)
        capture.attach_context(context)

        session_id = None
        oauth_max = max(1, config.oauth_max_attempts)
        for oauth_attempt in range(1, oauth_max + 1):
            if page is None or page.is_closed():
                page = await browser_manager.claim_page(context)
                capture.attach_page(page)
            else:
                capture.attach_page(page)
            if oauth_attempt == 1:
                database.update_import_job(import_job_id, status="generating_auth_url", started_at=now())
            else:
                database.update_import_job(import_job_id, status="regenerating_auth_url")

            # Fresh Sub2API authorize link (new session_id + state) each attempt.
            auth_url, session_id, _ = await asyncio.to_thread(sub2api_client.generate_auth_url)
            expected_state = extract_query_param(auth_url, "state")
            capture.reset(expected_state)
            database.update_import_job(
                import_job_id,
                sub2api_session_id=session_id,
                auth_url=auth_url,
                expected_state=expected_state,
                status="waiting_auth",
            )
            if not expected_state:
                log.warning("[import %s] auth_url has no 'state' param", import_job_id)

            log.info("[import %s] open auth_url in same context (oauth attempt %d/%d)",
                     import_job_id, oauth_attempt, oauth_max)
            try:
                await page.goto(auth_url, wait_until="domcontentloaded")
            except Exception:
                # Navigation may bounce straight to the (unservable) callback URL.
                pass
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await asyncio.sleep(1.0)

            # Scenario 1: re-enter email + email code. Scenario 2: account picker.
            handled_login = await _handle_auth_email_login(page, email, import_job_id)
            if not handled_login:
                await _select_oauth_account(page, email, import_job_id)
            await asyncio.sleep(1.0)
            await _click_proceed(page, import_job_id, 6000)

            # ---- per-attempt main loop: consent -> phone -> watch for callback ----
            deadline = time.monotonic() + timeout
            phone_done = False
            regenerate = False
            regenerate_reason = ""
            while time.monotonic() < deadline and not capture.captured:
                phone_present = await register_worker.is_present(page, config.phone_input_selector)

                # Scenario 1 re-login page ("欢迎回来" / "登录或注册") can settle LATE
                # (after the once-before-the-loop handler ran) or appear on a fresh
                # resume context. If its email box is showing, (re)handle the email
                # login HERE -- otherwise the loop just clicks 继续 on the login page
                # forever and times out (observed on a Sandy resume).
                if not phone_present and await register_worker.is_present(page, config.email_input_selector):
                    log.info("[import %s] email-login page in loop; (re)handling Scenario 1", import_job_id)
                    try:
                        await _handle_auth_email_login(page, email, import_job_id)
                    except Exception as exc:
                        log.warning("[import %s] in-loop email re-login failed (%s); regenerating auth_url",
                                    import_job_id, str(exc)[:80])
                        regenerate = True
                        regenerate_reason = f"email re-login failed: {str(exc)[:120]}"
                        break
                    await asyncio.sleep(1.0)
                    continue

                # 图7「查看你的手机」: a SECOND verification gate after the Codex
                # consent (图6). The code is sent to a number we can't read, so per
                # 接码.md: abandon and reopen a FRESH Sub2API auth_url (the new flow
                # goes email-relogin/account-pick -> callback, skipping this gate).
                if not phone_present and await _is_check_phone_page(page):
                    log.info("[import %s] '查看你的手机' gate after consent; regenerating "
                             "Sub2API auth_url (attempt %d/%d)",
                             import_job_id, oauth_attempt, oauth_max)
                    regenerate = True
                    regenerate_reason = "check-phone gate"
                    break

                # Flow regressed to the OAuth start ("登录或注册") mid-import -- e.g.
                # a failed phone attempt's back-navigation over-shot. Reopen a fresh
                # Sub2API auth_url (account-pick -> fresh phone page -> retry).
                if not phone_present and await _is_oauth_start_page(page):
                    log.info("[import %s] flow regressed to OAuth start (登录或注册); regenerating "
                             "Sub2API auth_url (attempt %d/%d)",
                             import_job_id, oauth_attempt, oauth_max)
                    regenerate = True
                    regenerate_reason = "OAuth start regression"
                    break

                # Clear an OAuth consent/authorize page first (e.g. "同意使用 Codex"):
                # it must be advanced with 继续/授权 BEFORE the phone step, otherwise
                # the flow keeps looking for a phone field that isn't there yet.
                if not phone_present and await _is_consent_page(page):
                    log.info("[import %s] consent/authorize page detected; clicking continue", import_job_id)
                    await _click_proceed(page, import_job_id, 4000)
                    await capture.wait(1.2)
                    continue

                if enable_sms and phone_present:
                    if phone_done:
                        # A SECOND phone-verification page after the first one
                        # passed: do NOT buy another number -- abandon this flow
                        # and reopen a FRESH Sub2API auth_url (once the account is
                        # verified, the new flow proceeds straight to the callback).
                        log.info("[import %s] second phone-verification page after first passed; "
                                 "regenerating Sub2API auth_url (attempt %d/%d)",
                                 import_job_id, oauth_attempt, oauth_max)
                        regenerate = True
                        regenerate_reason = "second phone page"
                        break
                    log.info("[import %s] phone required -> verify via %s", import_job_id, sms_provider.name)
                    # Retry loop: syncs the form country, buys a fresh number if one
                    # is rejected, and bails the instant the callback is captured --
                    # a captured callback must WIN over any error raised here.
                    try:
                        order = await _handle_phone_verification(
                            page,
                            import_job_id=import_job_id,
                            register_job_id=register_job_id,
                            sms_provider=sms_provider,
                            capture=capture,
                        )
                    except _RegenerateAuthUrl as exc:
                        log.info("[import %s] phone step asked to regenerate (%s); reopening "
                                 "Sub2API auth_url (attempt %d/%d)",
                                 import_job_id, str(exc)[:60], oauth_attempt, oauth_max)
                        regenerate = True
                        regenerate_reason = str(exc)[:160]
                        break
                    except Exception:
                        if capture.captured:
                            log.info("[import %s] phone step errored but callback captured; proceeding", import_job_id)
                        else:
                            raise
                    else:
                        if order is not None:
                            sms_order = order
                            phone_done = True
                        # order is None -> no phone needed right now; keep advancing.
                    continue

                # best-effort: advance any consent/"你已准备就绪" page so the callback fires.
                await _click_proceed(page, import_job_id, 1500)
                await capture.wait(1.0)

            if capture.captured:
                break
            if regenerate:
                page = await _close_page_for_retry(page, import_job_id, regenerate_reason or "regenerate")
            if regenerate and oauth_attempt < oauth_max:
                continue
            if regenerate and oauth_attempt >= oauth_max:
                raise TimeoutError(f"OAuth retry limit reached after regeneration: {regenerate_reason or 'unknown reason'}")
            break  # not captured and no regeneration left -> timeout handled below

        if not capture.captured:
            await capture.wait(2.0)
        if not capture.captured:
            raise TimeoutError("authorization callback not captured within timeout")

        # ---- state validation (capture already filters by expected_state) ----
        database.update_import_job(
            import_job_id,
            status="callback_captured",
            callback_url=capture.callback_url,
            code=capture.code,
            state=capture.state,
        )
        if capture.state != expected_state:
            raise RuntimeError("OAuth state mismatch (expected_state != captured_state)")

        # ---- create-from-oauth ----
        database.update_import_job(import_job_id, status="importing")
        account_id, _ = await asyncio.to_thread(
            sub2api_client.create_from_oauth,
            session_id,
            capture.code,
            capture.state,
            name,
            config.sub2api_redirect_uri,
            concurrency,
            priority,
            list(group_ids) if group_ids is not None else None,
        )

        database.update_import_job(
            import_job_id, status="success", sub2api_account_id=account_id, finished_at=now()
        )

        # ---- record account ----
        reg = database.get_register_job(register_job_id) if register_job_id else None
        final_email = email or (reg or {}).get("email") or name
        database.upsert_registered_account(
            email=final_email,
            name=(reg or {}).get("name") or name,
            age=(reg or {}).get("age"),
            phone_number=sms_order.phone_number if sms_order else None,
            sms_provider=sms_order.provider if sms_order else None,
            sms_order_id=sms_order.order_id if sms_order else None,
            source_register_job_id=register_job_id,
            source_import_job_id=import_job_id,
            sub2api_account_id=account_id,
        )
        # Import succeeded -> the mailbox is now fully consumed (registered AND
        # imported). This is the ONLY place an email is marked 'used'.
        if final_email and "@" in final_email:
            email_pool.mark_used(final_email, import_job_id)

        await _finish_order_safe(sms_provider, sms_order)
        log.info("[import %s] SUCCESS (account_id=%s)", import_job_id, account_id)
        return {
            "account_id": account_id,
            "phone_number": sms_order.phone_number if sms_order else None,
            "sms_provider": sms_order.provider if sms_order else None,
            "sms_order_id": sms_order.order_id if sms_order else None,
            "sms_country": sms_order.country if sms_order else None,
            "sms_product": sms_order.product if sms_order else None,
            "sms_operator": sms_order.operator if sms_order else None,
            "sms_operator_strategy": sms_order.strategy if sms_order else None,
            "sms_price": sms_order.price if sms_order else None,
            "sms_success_rate": sms_order.success_rate if sms_order else None,
        }

    except Exception as exc:
        shot = await register_worker.save_screenshot(page, import_job_id, "import_error")
        await _cancel_order_safe(sms_provider, sms_order)
        # A mailbox problem surfaced during the OAuth re-login (Scenario 1):
        # permanent auth failures are terminal, while temporary IMAP issues are
        # moved aside for later review.
        if isinstance(exc, MailboxUnavailableError) and email and "@" in email:
            email_pool.mark_unavailable(email, import_job_id, str(exc))
        elif isinstance(exc, MailboxDeferredError) and email and "@" in email:
            email_pool.mark_deferred(email, import_job_id, str(exc))
        finalize_import_failure(import_job_id, register_job_id, exc, shot)
        raise


# --------------------------------------------------------------------------- #
# standalone import job
# --------------------------------------------------------------------------- #
async def run_import_job(
    import_job_id: str,
    *,
    email: Optional[str],
    name: str,
    group_ids: Optional[Iterable[int]],
    concurrency: Optional[int],
    priority: Optional[int],
    headless: bool,
    enable_sms: bool,
    sms_provider_name: Optional[str],
    timeout: int,
    fivesim: Optional[dict] = None,
) -> None:
    try:
        async with browser_manager.browser_session(f"import_{import_job_id}", headless=headless) as context:
            await perform_import(
                context,
                import_job_id=import_job_id,
                register_job_id=None,
                email=email,
                name=name,
                group_ids=group_ids,
                concurrency=concurrency,
                priority=priority,
                enable_sms=enable_sms,
                sms_provider_name=sms_provider_name,
                timeout=timeout,
                fivesim=fivesim,
            )
    except Exception as exc:
        current = database.get_import_job(import_job_id)
        if not current or not register_worker.is_terminal(current.get("status")):
            finalize_import_failure(import_job_id, None, exc, "")
