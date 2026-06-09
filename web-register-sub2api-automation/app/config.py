"""Configuration loading.

All configuration comes from environment variables (optionally populated from a
local ``.env`` file via python-dotenv). Secrets (API keys / tokens) are read
from the environment only and are never written to logs in full -- use
:func:`mask_secret` whenever a secret-ish value must be referenced in a log.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

# Load .env located at the project root (parent of the app/ package).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


# --------------------------------------------------------------------------- #
# parsing helpers
# --------------------------------------------------------------------------- #
def _get(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


def _get_bool(name: str, default: bool = False) -> bool:
    raw = _get(name, str(default)).lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _get_int(name: str, default: int) -> int:
    raw = _get(name, "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _get_optional_int(name: str) -> Optional[int]:
    raw = _get(name, "")
    if raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _get_float(name: str, default: float) -> float:
    raw = _get(name, "")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _get_optional_float(name: str) -> Optional[float]:
    raw = _get(name, "")
    if raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _get_str_list(name: str, default: str = "") -> List[str]:
    raw = _get(name, default)
    result: List[str] = []
    for piece in raw.replace(";", ",").split(","):
        piece = piece.strip()
        if piece:
            result.append(piece)
    return result


def _get_int_list(name: str, default: str = "") -> List[int]:
    raw = _get(name, default)
    result: List[int] = []
    for piece in raw.replace(";", ",").split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            result.append(int(piece))
        except ValueError:
            continue
    return result


def mask_secret(value: Optional[str]) -> str:
    """Return a log-safe representation of a secret-ish value.

    Never returns more than the first/last couple of characters so that full
    API keys / tokens never end up in logs.
    """
    if not value:
        return "<empty>"
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}***{value[-2:]}"


# --------------------------------------------------------------------------- #
# config object
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    # this service
    api_key: str = field(default_factory=lambda: _get("API_KEY", "change-this-register-service-key"))
    host: str = field(default_factory=lambda: _get("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _get_int("PORT", 5060))

    # target web project
    register_url: str = field(default_factory=lambda: _get("REGISTER_URL", ""))

    # browser
    headless: bool = field(default_factory=lambda: _get_bool("HEADLESS", False))
    browser_timeout_ms: int = field(default_factory=lambda: _get_int("BROWSER_TIMEOUT_MS", 60000))
    code_timeout_seconds: int = field(default_factory=lambda: _get_int("CODE_TIMEOUT_SECONDS", 180))

    # browser mode: "playwright" (bundled Chromium) or "cdp" (local Chrome via CDP)
    browser_mode: str = field(default_factory=lambda: _get("BROWSER_MODE", "playwright").lower())
    cdp_endpoint: str = field(default_factory=lambda: _get("CDP_ENDPOINT", "http://127.0.0.1:9222"))
    cdp_context_policy: str = field(default_factory=lambda: _get("CDP_CONTEXT_POLICY", "incognito").lower())
    cdp_close_browser: bool = field(default_factory=lambda: _get_bool("CDP_CLOSE_BROWSER", False))

    # files & db
    emails_file: str = field(default_factory=lambda: _get("EMAILS_FILE", "emails.txt"))
    db_file: str = field(default_factory=lambda: _get("DB_FILE", "data/register_jobs.db"))
    screenshot_dir: str = field(default_factory=lambda: _get("SCREENSHOT_DIR", "screenshots"))
    trace_dir: str = field(default_factory=lambda: _get("TRACE_DIR", "traces"))
    # Source mailbox file (project A's accounts.txt) used to (re)populate emails.txt.
    # Default points at the sibling owned-mail-code-service/accounts.txt.
    accounts_source_file: str = field(default_factory=lambda: _get(
        "ACCOUNTS_SOURCE_FILE", "../owned-mail-code-service/accounts.txt"))
    # Archive files: a fully-imported ('used') account line is moved out of
    # accounts.txt into accounts_imported_file; a permanently-failed ('unavailable')
    # one into accounts_failed_file; a temporarily-unusable mailbox ('deferred')
    # one into accounts_deferred_file. Keeps accounts.txt = the ACTIVE set so
    # project A only polls live mailboxes.
    accounts_imported_file: str = field(default_factory=lambda: _get(
        "ACCOUNTS_IMPORTED_FILE", "../owned-mail-code-service/accounts_imported.txt"))
    accounts_failed_file: str = field(default_factory=lambda: _get(
        "ACCOUNTS_FAILED_FILE", "../owned-mail-code-service/accounts_failed.txt"))
    accounts_deferred_file: str = field(default_factory=lambda: _get(
        "ACCOUNTS_DEFERRED_FILE", "../owned-mail-code-service/accounts_deferred.txt"))

    # project A: mail code service
    mail_code_service_base: str = field(default_factory=lambda: _get("MAIL_CODE_SERVICE_BASE", "http://127.0.0.1:5050"))
    mail_code_service_api_key: str = field(default_factory=lambda: _get("MAIL_CODE_SERVICE_API_KEY", ""))
    # Email verification-code extraction (sent to project A so it returns the RIGHT code).
    # Default targets ChatGPT/OpenAI login codes; capture group (\d{6}) -> the 6 digits.
    email_code_pattern: str = field(default_factory=lambda: _get(
        "EMAIL_CODE_PATTERN",
        r"(?s)(?:验证码|登录代码|verification code|login code)[^0-9]{0,300}(\d{6})",
    ))
    email_code_subject_keyword: str = field(default_factory=lambda: _get("EMAIL_CODE_SUBJECT_KEYWORD", "ChatGPT"))
    email_code_from_keyword: str = field(default_factory=lambda: _get("EMAIL_CODE_FROM_KEYWORD", "openai"))

    # sms master switch
    enable_sms: bool = field(default_factory=lambda: _get_bool("ENABLE_SMS", True))
    sms_provider: str = field(default_factory=lambda: _get("SMS_PROVIDER", "62us").lower())
    sms_code_pattern: str = field(default_factory=lambda: _get("SMS_CODE_PATTERN", r"\b\d{6}\b"))
    sms_timeout_seconds: int = field(default_factory=lambda: _get_int("SMS_TIMEOUT_SECONDS", 180))
    sms_poll_interval_seconds: int = field(default_factory=lambda: _get_int("SMS_POLL_INTERVAL_SECONDS", 5))
    sms_auto_finish_order: bool = field(default_factory=lambda: _get_bool("SMS_AUTO_FINISH_ORDER", True))
    sms_auto_cancel_on_fail: bool = field(default_factory=lambda: _get_bool("SMS_AUTO_CANCEL_ON_FAIL", True))
    # OpenAI sometimes asks for phone verification a SECOND time in one OAuth flow,
    # shows a "查看你的手机" gate after consent, or the flow regresses to the OAuth
    # start after a failed phone attempt. In all those cases the import abandons
    # the page and reopens a FRESH Sub2API auth_url instead of getting stuck. This
    # caps how many times the whole OAuth flow is regenerated (each regen = a fresh
    # phone attempt, so keep it >= a few to ride out 5sim's ~40%/number rate).
    oauth_max_attempts: int = field(default_factory=lambda: _get_int("OAUTH_MAX_ATTEMPTS", 5))
    # Batch parallelism: how many mailboxes the auto batch processes concurrently.
    # Each gets its own incognito context; all egress via the same proxy IP, so keep
    # this modest to avoid OpenAI/Cloudflare anti-abuse on simultaneous registrations.
    batch_parallelism: int = field(default_factory=lambda: _get_int("BATCH_PARALLELISM", 3))

    # 62-US
    us62_base: str = field(default_factory=lambda: _get("US62_BASE", "https://api.62-us.com"))
    us62_api_key: str = field(default_factory=lambda: _get("US62_API_KEY", ""))
    us62_goods_id: str = field(default_factory=lambda: _get("US62_GOODS_ID", ""))

    # 5sim
    fivesim_base: str = field(default_factory=lambda: _get("FIVESIM_BASE", "https://5sim.net"))
    fivesim_token: str = field(default_factory=lambda: _get("FIVESIM_TOKEN", ""))
    fivesim_country: str = field(default_factory=lambda: _get("FIVESIM_COUNTRY", "argentina"))
    fivesim_product: str = field(default_factory=lambda: _get("FIVESIM_PRODUCT", ""))
    fivesim_operator: str = field(default_factory=lambda: _get("FIVESIM_OPERATOR", "any"))
    fivesim_operator_strategy: str = field(default_factory=lambda: _get("FIVESIM_OPERATOR_STRATEGY", "highest_success").lower())
    fivesim_operator_fallback: str = field(default_factory=lambda: _get("FIVESIM_OPERATOR_FALLBACK", "any"))
    fivesim_min_success_rate: float = field(default_factory=lambda: _get_float("FIVESIM_MIN_SUCCESS_RATE", 0.0))
    fivesim_min_count: int = field(default_factory=lambda: _get_int("FIVESIM_MIN_COUNT", 1))
    fivesim_exclude_operators: List[str] = field(default_factory=lambda: _get_str_list("FIVESIM_EXCLUDE_OPERATORS", ""))
    fivesim_max_price: Optional[float] = field(default_factory=lambda: _get_optional_float("FIVESIM_MAX_PRICE"))

    # Sub2API
    sub2api_base: str = field(default_factory=lambda: _get("SUB2API_BASE", "http://127.0.0.1:8080"))
    sub2api_admin_api_key: str = field(default_factory=lambda: _get("SUB2API_ADMIN_API_KEY", ""))
    # weishaw/sub2api uses JWT admin login (email + password), not x-api-key.
    sub2api_admin_email: str = field(default_factory=lambda: _get("SUB2API_ADMIN_EMAIL", ""))
    sub2api_admin_password: str = field(default_factory=lambda: _get("SUB2API_ADMIN_PASSWORD", ""))
    sub2api_platform: str = field(default_factory=lambda: _get("SUB2API_PLATFORM", "openai").lower())
    sub2api_account_type: str = field(default_factory=lambda: _get("SUB2API_ACCOUNT_TYPE", "oauth").lower())
    sub2api_redirect_uri: str = field(default_factory=lambda: _get("SUB2API_REDIRECT_URI", "http://localhost:1455/auth/callback"))
    sub2api_default_group_ids: List[int] = field(default_factory=lambda: _get_int_list("SUB2API_DEFAULT_GROUP_IDS", "1"))
    sub2api_default_concurrency: int = field(default_factory=lambda: _get_int("SUB2API_DEFAULT_CONCURRENCY", 10))
    sub2api_default_priority: int = field(default_factory=lambda: _get_int("SUB2API_DEFAULT_PRIORITY", 1))

    # random identity
    min_age: int = field(default_factory=lambda: _get_int("MIN_AGE", 18))
    max_age: int = field(default_factory=lambda: _get_int("MAX_AGE", 45))

    # registration selectors
    free_register_selector: str = field(default_factory=lambda: _get("FREE_REGISTER_SELECTOR", 'button:has-text("免费注册")'))
    email_input_selector: str = field(default_factory=lambda: _get("EMAIL_INPUT_SELECTOR", 'input[placeholder="电子邮件地址"]'))
    email_continue_selector: str = field(default_factory=lambda: _get("EMAIL_CONTINUE_SELECTOR", 'button:has-text("继续")'))
    email_code_input_selector: str = field(default_factory=lambda: _get("EMAIL_CODE_INPUT_SELECTOR", 'input[name="code"], input[autocomplete="one-time-code"], input:visible'))
    email_code_continue_selector: str = field(default_factory=lambda: _get("EMAIL_CODE_CONTINUE_SELECTOR", 'button:has-text("继续")'))
    name_input_selector: str = field(default_factory=lambda: _get("NAME_INPUT_SELECTOR", 'input[placeholder="姓名"], input[name="name"]'))
    age_input_selector: str = field(default_factory=lambda: _get("AGE_INPUT_SELECTOR", 'input[placeholder="年龄"], input[name="age"]'))
    complete_button_selector: str = field(default_factory=lambda: _get("COMPLETE_BUTTON_SELECTOR", 'button:has-text("完成帐户创建"), button:has-text("完成账户创建"), [role="button"]:has-text("完成帐户创建"), [role="button"]:has-text("完成账户创建"), button:has-text("户创建"), [role="button"]:has-text("户创建"), button:has-text("创建账户"), button:has-text("Create account")'))

    # authorization selectors
    auth_continue_selector: str = field(default_factory=lambda: _get("AUTH_CONTINUE_SELECTOR", 'button[type="submit"]:has-text("继续"), button:has-text("继续"), [role="button"]:has-text("继续"), button:has-text("Continue"), [role="button"]:has-text("Continue"), button:has-text("授权"), button:has-text("Authorize"), button:has-text("Allow")'))
    phone_input_selector: str = field(default_factory=lambda: _get("PHONE_INPUT_SELECTOR", 'input[placeholder="电话号码"], input[name="phone"], input[type="tel"]'))
    phone_continue_selector: str = field(default_factory=lambda: _get("PHONE_CONTINUE_SELECTOR", 'button[type="submit"]:has-text("继续"), button:has-text("继续"), button:has-text("发送验证码")'))
    sms_code_input_selector: str = field(default_factory=lambda: _get("SMS_CODE_INPUT_SELECTOR", 'input[name="code"], input[placeholder="代码"], input[placeholder="验证码"], input[name="sms_code"], input[autocomplete="one-time-code"]'))
    sms_code_continue_selector: str = field(default_factory=lambda: _get("SMS_CODE_CONTINUE_SELECTOR", 'button[type="submit"]:has-text("继续"), button:has-text("继续"), button:has-text("完成验证"), button:has-text("验证")'))

    # phone-step country selector (OAuth phone verification): pick the country
    # that matches the SMS provider's country (default form value is US).
    phone_country_select_selector: str = field(default_factory=lambda: _get("PHONE_COUNTRY_SELECT_SELECTOR", "select"))
    phone_country_trigger_selector: str = field(default_factory=lambda: _get("PHONE_COUNTRY_TRIGGER_SELECTOR", 'button[aria-haspopup="listbox"], button[role="combobox"], [data-testid*="country"], [aria-label*="国家"], [aria-label*="ountry"]'))
    phone_country_search_selector: str = field(default_factory=lambda: _get("PHONE_COUNTRY_SEARCH_SELECTOR", 'input[type="search"], input[placeholder*="搜索"], input[placeholder*="Search"]'))
    phone_strip_country_code: bool = field(default_factory=lambda: _get_bool("PHONE_STRIP_COUNTRY_CODE", True))
    # Some countries' numbers can't use Text Message -> switch to WhatsApp then continue.
    phone_text_message_selector: str = field(default_factory=lambda: _get(
        "PHONE_TEXT_MESSAGE_SELECTOR",
        'button:has-text("短信"), [role="button"]:has-text("短信"), label:has-text("短信"), [role="radio"]:has-text("短信"), '
        'button:has-text("短消息"), [role="button"]:has-text("短消息"), label:has-text("短消息"), [role="radio"]:has-text("短消息"), '
        'button:has-text("文本消息"), [role="button"]:has-text("文本消息"), label:has-text("文本消息"), [role="radio"]:has-text("文本消息"), '
        'button:has-text("Text Message"), [role="button"]:has-text("Text Message"), label:has-text("Text Message"), [role="radio"]:has-text("Text Message"), '
        'button:has-text("SMS"), [role="button"]:has-text("SMS"), label:has-text("SMS"), [role="radio"]:has-text("SMS")'
    ))
    phone_whatsapp_selector: str = field(default_factory=lambda: _get("PHONE_WHATSAPP_SELECTOR", 'button:has-text("WhatsApp"), [role="radio"]:has-text("WhatsApp"), label:has-text("WhatsApp"), [role="button"]:has-text("WhatsApp")'))
    # Whether the current SMS order can receive a WhatsApp one-time code.
    # The default 5sim "openai" SMS order only receives regular SMS, NOT WhatsApp,
    # so when False (default) a WhatsApp-only phone page must NOT wait for an SMS
    # code (it would just time out): instead cancel the order and buy a new number.
    phone_allow_whatsapp_fallback: bool = field(default_factory=lambda: _get_bool("PHONE_ALLOW_WHATSAPP_FALLBACK", False))
    # Phone-verification retry: buy a fresh number if one is rejected (e.g. "已被验证过").
    sms_max_phone_attempts: int = field(default_factory=lambda: _get_int("SMS_MAX_PHONE_ATTEMPTS", 3))
    phone_error_keywords: List[str] = field(default_factory=lambda: _get_str_list(
        "PHONE_ERROR_KEYWORDS",
        "已被验证,已验证过,已被使用,已使用,无法验证,无效,验证失败,请重试,try another,already been used,already verified,too many",
    ))
    phone_back_selector: str = field(default_factory=lambda: _get("PHONE_BACK_SELECTOR", 'button:has-text("返回"), button:has-text("上一步"), button:has-text("更换"), [aria-label*="返回"], [aria-label*="ack"]'))

    # success detection (optional)
    success_url_keyword: str = field(default_factory=lambda: _get("SUCCESS_URL_KEYWORD", ""))
    success_text_keyword: str = field(default_factory=lambda: _get("SUCCESS_TEXT_KEYWORD", ""))

    # ----- derived paths -----
    def abs_path(self, relative: str) -> Path:
        """Resolve a possibly-relative path against the project root."""
        p = Path(relative)
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    @property
    def db_path(self) -> Path:
        return self.abs_path(self.db_file)

    @property
    def emails_path(self) -> Path:
        return self.abs_path(self.emails_file)

    @property
    def accounts_source_path(self) -> Path:
        return self.abs_path(self.accounts_source_file)

    @property
    def accounts_imported_path(self) -> Path:
        return self.abs_path(self.accounts_imported_file)

    @property
    def accounts_failed_path(self) -> Path:
        return self.abs_path(self.accounts_failed_file)

    @property
    def accounts_deferred_path(self) -> Path:
        return self.abs_path(self.accounts_deferred_file)

    @property
    def screenshot_path(self) -> Path:
        return self.abs_path(self.screenshot_dir)

    @property
    def trace_path(self) -> Path:
        return self.abs_path(self.trace_dir)

    def ensure_dirs(self) -> None:
        """Make sure all runtime directories exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.screenshot_path.mkdir(parents=True, exist_ok=True)
        self.trace_path.mkdir(parents=True, exist_ok=True)
        for path in (
            self.accounts_imported_path,
            self.accounts_failed_path,
            self.accounts_deferred_path,
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)


# Singleton-ish config instance imported across the app.
config = Config()

# Available SMS providers (used by /api/sms/providers and the factory).
AVAILABLE_SMS_PROVIDERS: List[str] = ["62us", "5sim"]

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def is_local_url(url: str) -> bool:
    """True if ``url`` points at the local machine."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in _LOCAL_HOSTS


def proxies_for(url: str) -> Optional[Dict[str, Optional[str]]]:
    """Return a ``requests`` ``proxies`` mapping for ``url``.

    Local services (project A mail-code service, Sub2API) must NOT be routed
    through an HTTP(S)_PROXY (a common cause of silent localhost failures when
    a system proxy like Clash/V2Ray is active). External hosts (5sim / 62-US)
    return ``None`` so requests keeps honoring the environment proxy, which a
    user behind a regional proxy typically needs to reach them.
    """
    if is_local_url(url):
        return {"http": None, "https": None}
    return None
