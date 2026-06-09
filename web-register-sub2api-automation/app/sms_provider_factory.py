"""Factory that selects the concrete SMS provider from configuration.

Business code (the workers) call :func:`build_sms_provider` and receive a
:class:`~app.sms_provider_base.SmsProvider` -- they never import a concrete
client. The provider is chosen by ``SMS_PROVIDER`` (overridable per request),
and 5sim's country/product/operator/strategy may also be overridden per request.
"""
from __future__ import annotations

from typing import List, Optional

from .config import AVAILABLE_SMS_PROVIDERS, config
from .sms_5sim_client import FiveSimProvider
from .sms_62us_client import Us62Provider
from .sms_provider_base import SmsError, SmsProvider


def available_providers() -> List[str]:
    return list(AVAILABLE_SMS_PROVIDERS)


def build_5sim_provider(
    *,
    country: Optional[str] = None,
    product: Optional[str] = None,
    operator: Optional[str] = None,
    strategy: Optional[str] = None,
    max_price: Optional[float] = None,
    require_country: bool = False,
    require_product: bool = True,
) -> FiveSimProvider:
    """Build a :class:`FiveSimProvider`, merging request overrides over .env.

    ``require_product`` keeps the explicit "product missing" error. ``country``
    may be empty: the provider then auto-picks the highest-delivery-rate country
    for the product at buy time (see ``FiveSimProvider.ensure_country``).
    """
    if not config.fivesim_token:
        raise SmsError("SMS_PROVIDER=5sim 但 FIVESIM_TOKEN 未配置")

    eff_country = (country or config.fivesim_country or "").strip()
    eff_product = (product or config.fivesim_product or "").strip()
    eff_operator = (operator or config.fivesim_operator or "any").strip()
    eff_strategy = (strategy or config.fivesim_operator_strategy or "highest_success").strip().lower()
    eff_max_price = max_price if max_price is not None else config.fivesim_max_price

    if require_product and not eff_product:
        raise SmsError("FIVESIM_PRODUCT 未配置，且请求中没有传 fivesim_product")
    if require_country and not eff_country:
        raise SmsError("FIVESIM_COUNTRY 未配置，且请求中没有传 fivesim_country")

    return FiveSimProvider(
        token=config.fivesim_token,
        base=config.fivesim_base,
        country=eff_country,
        operator=eff_operator,
        product=eff_product,
        strategy=eff_strategy,
        operator_fallback=config.fivesim_operator_fallback,
        min_success_rate=config.fivesim_min_success_rate,
        min_count=config.fivesim_min_count,
        exclude_operators=config.fivesim_exclude_operators,
        max_price=eff_max_price,
        code_pattern=config.sms_code_pattern,
        poll_interval=config.sms_poll_interval_seconds,
    )


def build_sms_provider(
    provider_name: Optional[str] = None,
    *,
    fivesim_country: Optional[str] = None,
    fivesim_product: Optional[str] = None,
    fivesim_operator: Optional[str] = None,
    fivesim_operator_strategy: Optional[str] = None,
    fivesim_max_price: Optional[float] = None,
) -> SmsProvider:
    """Return a configured :class:`SmsProvider` for ``provider_name``.

    ``provider_name`` falls back to ``SMS_PROVIDER`` from the environment.
    Raises :class:`SmsError` with an explicit message when the selected
    provider is misconfigured.
    """
    name = (provider_name or config.sms_provider or "").strip().lower()

    if name == "62us":
        if not config.us62_api_key:
            raise SmsError("SMS_PROVIDER=62us 但 US62_API_KEY 未配置")
        if not config.us62_goods_id:
            raise SmsError("SMS_PROVIDER=62us 但 US62_GOODS_ID 未配置")
        return Us62Provider(
            api_key=config.us62_api_key,
            base=config.us62_base,
            goods_id=config.us62_goods_id,
            code_pattern=config.sms_code_pattern,
            poll_interval=config.sms_poll_interval_seconds,
        )

    if name == "5sim":
        return build_5sim_provider(
            country=fivesim_country,
            product=fivesim_product,
            operator=fivesim_operator,
            strategy=fivesim_operator_strategy,
            max_price=fivesim_max_price,
        )

    raise SmsError(
        f"未知的 SMS_PROVIDER='{name}'，可用值: {', '.join(AVAILABLE_SMS_PROVIDERS)}"
    )
