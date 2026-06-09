"""Unified SMS provider abstraction.

The registration / import workers depend ONLY on :class:`SmsProvider` and
:class:`SmsOrder` -- never on a concrete platform. The concrete platform is
chosen at runtime by :mod:`app.sms_provider_factory` based on ``SMS_PROVIDER``.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional


class SmsError(RuntimeError):
    """Generic SMS provider error."""


class SmsTimeout(SmsError):
    """Raised when no SMS code arrives within the timeout."""


@dataclass
class SmsOrder:
    """A unified SMS order returned by any provider.

    The trailing fields are optional selection metadata (mainly populated by
    5sim); providers that have no such concept (e.g. 62-US) simply leave them
    ``None``.
    """

    provider: str
    order_id: str
    phone_number: str
    token: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None
    # selection metadata (optional)
    country: Optional[str] = None
    product: Optional[str] = None
    operator: Optional[str] = None
    strategy: Optional[str] = None
    price: Optional[float] = None
    success_rate: Optional[float] = None


class SmsProvider(ABC):
    """Abstract base every concrete SMS platform must implement."""

    #: short provider key, e.g. "62us" or "5sim"
    name: str = "base"

    @abstractmethod
    def check_profile(self) -> dict:
        """Return account profile / balance / status as a dict."""

    @abstractmethod
    def buy_number(self) -> SmsOrder:
        """Order a phone number and return a unified :class:`SmsOrder`."""

    @abstractmethod
    def wait_code(self, order: SmsOrder, timeout: int) -> str:
        """Poll for the incoming SMS and return the extracted verification code.

        Raises :class:`SmsTimeout` if no code arrives within ``timeout`` seconds.
        """

    @abstractmethod
    def finish_order(self, order: SmsOrder) -> None:
        """Mark the order as completed (no-op if the platform lacks the concept)."""

    @abstractmethod
    def cancel_order(self, order: SmsOrder) -> None:
        """Cancel / release the order (no-op if the platform lacks the concept)."""

    def country_for_form(self) -> Optional[str]:
        """Country key to select on a phone-verification form.

        Returns the country the bought number belongs to (so the web form's
        country can be set to match). ``None`` means "leave the form default".
        Overridden by 5sim (which may auto-resolve the best country).
        """
        return None


# 5sim country slug -> phone-form display (zh / en) + dial code. Used both to
# auto-pick the best country and to select the matching option on the OAuth
# phone form. The dial code is the most reliable cross-language match.
COUNTRY_INFO: Dict[str, Dict[str, str]] = {
    "argentina": {"zh": "阿根廷", "en": "Argentina", "dial": "+54"},
    "usa": {"zh": "美国", "en": "United States", "dial": "+1"},
    "england": {"zh": "英国", "en": "United Kingdom", "dial": "+44"},
    "canada": {"zh": "加拿大", "en": "Canada", "dial": "+1"},
    "russia": {"zh": "俄罗斯", "en": "Russia", "dial": "+7"},
    "kazakhstan": {"zh": "哈萨克斯坦", "en": "Kazakhstan", "dial": "+7"},
    "ukraine": {"zh": "乌克兰", "en": "Ukraine", "dial": "+380"},
    "indonesia": {"zh": "印度尼西亚", "en": "Indonesia", "dial": "+62"},
    "philippines": {"zh": "菲律宾", "en": "Philippines", "dial": "+63"},
    "vietnam": {"zh": "越南", "en": "Vietnam", "dial": "+84"},
    "india": {"zh": "印度", "en": "India", "dial": "+91"},
    "brazil": {"zh": "巴西", "en": "Brazil", "dial": "+55"},
    "france": {"zh": "法国", "en": "France", "dial": "+33"},
    "germany": {"zh": "德国", "en": "Germany", "dial": "+49"},
    "netherlands": {"zh": "荷兰", "en": "Netherlands", "dial": "+31"},
    "poland": {"zh": "波兰", "en": "Poland", "dial": "+48"},
    "malaysia": {"zh": "马来西亚", "en": "Malaysia", "dial": "+60"},
    "thailand": {"zh": "泰国", "en": "Thailand", "dial": "+66"},
    "mexico": {"zh": "墨西哥", "en": "Mexico", "dial": "+52"},
    "colombia": {"zh": "哥伦比亚", "en": "Colombia", "dial": "+57"},
    "southafrica": {"zh": "南非", "en": "South Africa", "dial": "+27"},
    "nigeria": {"zh": "尼日利亚", "en": "Nigeria", "dial": "+234"},
    "spain": {"zh": "西班牙", "en": "Spain", "dial": "+34"},
    "italy": {"zh": "意大利", "en": "Italy", "dial": "+39"},
    "turkey": {"zh": "土耳其", "en": "Turkey", "dial": "+90"},
    "romania": {"zh": "罗马尼亚", "en": "Romania", "dial": "+40"},
    "portugal": {"zh": "葡萄牙", "en": "Portugal", "dial": "+351"},
    "hongkong": {"zh": "香港", "en": "Hong Kong", "dial": "+852"},
    "bangladesh": {"zh": "孟加拉国", "en": "Bangladesh", "dial": "+880"},
    "pakistan": {"zh": "巴基斯坦", "en": "Pakistan", "dial": "+92"},
    "egypt": {"zh": "埃及", "en": "Egypt", "dial": "+20"},
    "kenya": {"zh": "肯尼亚", "en": "Kenya", "dial": "+254"},
    "cambodia": {"zh": "柬埔寨", "en": "Cambodia", "dial": "+855"},
    "myanmar": {"zh": "缅甸", "en": "Myanmar", "dial": "+95"},
    "israel": {"zh": "以色列", "en": "Israel", "dial": "+972"},
    "georgia": {"zh": "格鲁吉亚", "en": "Georgia", "dial": "+995"},
    "uzbekistan": {"zh": "乌兹别克斯坦", "en": "Uzbekistan", "dial": "+998"},
}


def extract_code(text: Optional[str], pattern: str) -> Optional[str]:
    """Extract the first match of ``pattern`` from ``text`` (defaults to 6 digits).

    If the pattern contains a capture group, the first group is returned,
    otherwise the whole match is returned.
    """
    if not text:
        return None
    try:
        match = re.search(pattern, text)
    except re.error:
        match = re.search(r"\b\d{6}\b", text)
    if not match:
        return None
    if match.groups():
        return match.group(1)
    return match.group(0)
