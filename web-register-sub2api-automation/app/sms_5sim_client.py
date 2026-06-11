"""5sim SMS provider implementation.

User (authenticated, Bearer token) endpoints::

    GET /v1/user/profile
    GET /v1/user/buy/activation/{country}/{operator}/{product}[?maxPrice=]
    GET /v1/user/check/{id}
    GET /v1/user/finish/{id}
    GET /v1/user/cancel/{id}

Guest (no auth) endpoints used for product/operator discovery::

    GET /v1/guest/products/{country}/{operator}      -> {product: {Category, Qty, Price}}
    GET /v1/guest/prices?country=&product=           -> {country: {product: {operator: {cost, count, rate}}}}

The service ``product`` is NOT hardcoded -- it is supplied by configuration or
per-request override. The ``operator`` is, by default, auto-selected by a
strategy (highest success rate, cheapest, most available) using the guest
prices endpoint; a fallback operator is used when no candidate passes the
filters.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests

from .config import proxies_for
from .sms_provider_base import COUNTRY_INFO, SmsError, SmsOrder, SmsProvider, SmsTimeout, extract_code

log = logging.getLogger(__name__)

_TIMEOUT = 30

# Operator-selection strategies.
STRATEGY_HIGHEST_SUCCESS = "highest_success"
STRATEGY_LOWEST_PRICE = "lowest_price"
STRATEGY_MOST_AVAILABLE = "most_available"
STRATEGY_MANUAL = "manual"


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class FiveSimProvider(SmsProvider):
    name = "5sim"

    def __init__(
        self,
        token: str,
        base: str = "https://5sim.net",
        country: str = "argentina",
        operator: str = "any",
        product: str = "",
        strategy: str = STRATEGY_HIGHEST_SUCCESS,
        operator_fallback: str = "any",
        min_success_rate: float = 0.0,
        min_count: int = 1,
        exclude_operators: Optional[List[str]] = None,
        max_price: Optional[float] = None,
        code_pattern: str = r"\b\d{6}\b",
        poll_interval: int = 5,
    ) -> None:
        self.token = token
        self.base = base.rstrip("/")
        self.country = (country or "").strip()
        self.operator = (operator or "any").strip()
        self.product = (product or "").strip()
        self.strategy = (strategy or STRATEGY_HIGHEST_SUCCESS).strip().lower()
        self.operator_fallback = (operator_fallback or "any").strip()
        self.min_success_rate = min_success_rate or 0.0
        self.min_count = min_count if min_count is not None else 1
        self.exclude_operators = {o.strip().lower() for o in (exclude_operators or []) if o.strip()}
        self.max_price = max_price
        self.code_pattern = code_pattern
        self.poll_interval = max(1, poll_interval)

    # ------------------------------------------------------------------ #
    # HTTP
    # ------------------------------------------------------------------ #
    def _headers(self, auth: bool = True) -> dict:
        headers = {"Accept": "application/json"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get(self, path: str, params: Optional[dict] = None, auth: bool = True) -> Any:
        url = f"{self.base}{path}"
        try:
            resp = requests.get(
                url, headers=self._headers(auth), params=params, timeout=_TIMEOUT, proxies=proxies_for(url)
            )
        except requests.RequestException as exc:
            raise SmsError(f"5sim request failed ({path}): {exc}") from exc

        if resp.status_code == 401:
            raise SmsError("5sim unauthorized (check FIVESIM_TOKEN)")
        if resp.status_code >= 400:
            raise SmsError(f"5sim HTTP {resp.status_code} on {path}: {resp.text[:200]}")

        text = (resp.text or "").strip()
        # 5sim signals some conditions as HTTP 200 with a plain-text body.
        if text and text[0] not in "{[":
            raise SmsError(f"5sim returned: {text}")
        try:
            return resp.json()
        except ValueError as exc:
            raise SmsError(f"5sim returned non-JSON on {path}: {text[:200]}") from exc

    # ------------------------------------------------------------------ #
    # account
    # ------------------------------------------------------------------ #
    def check_profile(self) -> dict:
        body = self._get("/v1/user/profile")
        if not isinstance(body, dict):
            return {"raw": body}
        return {
            "provider": self.name,
            "id": body.get("id"),
            "email": body.get("email"),
            "balance": body.get("balance"),
            "rating": body.get("rating"),
            "frozen_balance": body.get("frozen_balance"),
        }

    # ------------------------------------------------------------------ #
    # discovery (guest endpoints, no auth required)
    # ------------------------------------------------------------------ #
    def list_products(self, country: Optional[str] = None, operator: Optional[str] = None) -> dict:
        """Return services available for a country/operator.

        Note: the 5sim products endpoint returns price + qty but NOT success
        rate; ``rate`` is best-effort enriched from the guest prices endpoint
        (max operator rate per product) and may be ``None`` if unavailable.
        """
        country = (country or self.country).strip()
        operator = (operator or self.operator or "any").strip()
        if not country:
            raise SmsError("country is required to list 5sim products")

        body = self._get(f"/v1/guest/products/{country}/{operator}", auth=False)
        rate_map = self._best_rate_per_product(country)

        products: List[dict] = []
        if isinstance(body, dict):
            for product_name, info in body.items():
                if not isinstance(info, dict):
                    continue
                products.append({
                    "product": product_name,
                    "price": _to_float(info.get("Price")),
                    "count": _to_int(info.get("Qty")),
                    "rate": rate_map.get(product_name),
                    "category": info.get("Category"),
                })
        products.sort(key=lambda p: ((p["rate"] or 0), (p["count"] or 0)), reverse=True)
        return {"country": country, "operator": operator, "products": products, "raw": body}

    def _best_rate_per_product(self, country: str) -> Dict[str, float]:
        """Best-effort: {product: best operator rate} for a whole country."""
        try:
            body = self._get("/v1/guest/prices", params={"country": country}, auth=False)
        except SmsError:
            return {}
        out: Dict[str, float] = {}
        cnode = body.get(country) if isinstance(body, dict) else None
        if not isinstance(cnode, dict):
            return {}
        for product_name, operators in cnode.items():
            if not isinstance(operators, dict):
                continue
            rates = [_to_float(op.get("rate")) for op in operators.values() if isinstance(op, dict)]
            rates = [r for r in rates if r is not None]
            if rates:
                out[product_name] = max(rates)
        return out

    def list_operators(self, country: str, product: str) -> List[dict]:
        """Return operator candidates for a country+product from guest prices."""
        country = (country or "").strip()
        product = (product or "").strip()
        if not country or not product:
            raise SmsError("country and product are required to list 5sim operators")

        body = self._get("/v1/guest/prices", params={"country": country, "product": product}, auth=False)

        # Navigate country -> product -> {operator: {cost, count, rate}} defensively.
        operators: Dict[str, Any] = {}
        if isinstance(body, dict):
            cnode = body.get(country)
            if not isinstance(cnode, dict) and len(body) == 1:
                cnode = next(iter(body.values()))
            if isinstance(cnode, dict):
                pnode = cnode.get(product)
                if not isinstance(pnode, dict) and len(cnode) == 1:
                    pnode = next(iter(cnode.values()))
                if isinstance(pnode, dict):
                    operators = pnode

        candidates: List[dict] = []
        for op_name, info in operators.items():
            if not isinstance(info, dict):
                continue
            candidates.append({
                "operator": op_name,
                "price": _to_float(info.get("cost")),
                "success_rate": _to_float(info.get("rate")),
                "count": _to_int(info.get("count")),
            })
        return candidates

    def _candidate_passes_filters(self, candidate: dict) -> bool:
        if candidate["operator"].lower() in self.exclude_operators:
            return False
        if (candidate["count"] or 0) < self.min_count:
            return False
        if (candidate["success_rate"] or 0) < self.min_success_rate:
            return False
        if self.max_price is not None and candidate["price"] is not None and candidate["price"] > self.max_price:
            return False
        return True

    def _filter_and_sort(self, candidates: List[dict], strategy: str) -> List[dict]:
        filtered = [c for c in candidates if self._candidate_passes_filters(c)]

        def rate_key(c):
            return c["success_rate"] or 0

        def count_key(c):
            return c["count"] or 0

        def price_key(c):
            return c["price"] if c["price"] is not None else float("inf")

        if strategy == STRATEGY_LOWEST_PRICE:
            filtered.sort(key=lambda c: (price_key(c), -rate_key(c)))
        elif strategy == STRATEGY_MOST_AVAILABLE:
            filtered.sort(key=lambda c: (-count_key(c), -rate_key(c)))
        else:  # highest_success (default)
            filtered.sort(key=lambda c: (-rate_key(c), -count_key(c), price_key(c)))
        return filtered

    def select_best_operator(
        self,
        country: Optional[str] = None,
        product: Optional[str] = None,
        strategy: Optional[str] = None,
    ) -> dict:
        """Preview the operator selection. Does NOT buy / does NOT charge."""
        country = (country or self.country).strip()
        product = (product or self.product).strip()
        strategy = (strategy or self.strategy or STRATEGY_HIGHEST_SUCCESS).strip().lower()
        if not country:
            raise SmsError("country is required to select a 5sim operator")
        if not product:
            raise SmsError("product is required to select a 5sim operator")

        candidates = self.list_operators(country, product)
        ranked = self._filter_and_sort(candidates, strategy)

        if strategy == STRATEGY_MANUAL:
            chosen = next((c for c in candidates if c["operator"].lower() == self.operator.lower()), None)
            if chosen and self._candidate_passes_filters(chosen):
                selected = chosen
            else:
                fallback_operator = (self.operator_fallback or "any").strip() or "any"
                fallback = next((c for c in candidates if c["operator"].lower() == fallback_operator.lower()), None)
                if fallback and self._candidate_passes_filters(fallback):
                    selected = fallback
                else:
                    selected = {
                        "operator": fallback_operator if fallback_operator.lower() != self.operator.lower() else self.operator,
                        "price": None,
                        "success_rate": None,
                        "count": None,
                    }
        elif ranked:
            selected = ranked[0]
        else:
            # nothing passed the filters -> fall back
            selected = {"operator": self.operator_fallback, "price": None, "success_rate": None, "count": None}

        return {
            "country": country,
            "product": product,
            "strategy": strategy,
            "selected": selected,
            "candidates": ranked if ranked else candidates,
        }

    # ------------------------------------------------------------------ #
    # buy / wait / finish / cancel
    # ------------------------------------------------------------------ #
    def find_best_country(self, product: str) -> str:
        """Return the country (slug) with the highest delivery rate for ``product``.

        Considers only countries present in COUNTRY_INFO (so the OAuth phone form
        can actually select them) that currently have stock. Best = highest
        operator success rate, tie-broken by total available count.
        """
        body = self._get("/v1/guest/prices", params={"product": product}, auth=False)
        # The product-only filter is product-first: {product: {country: {operator: {...}}}}.
        countries = {}
        if isinstance(body, dict):
            pnode = body.get(product)
            if isinstance(pnode, dict):
                countries = pnode
            else:  # fallback: {country: {product: {operator: {...}}}}
                for slug, node in body.items():
                    if isinstance(node, dict) and isinstance(node.get(product), dict):
                        countries[slug] = node[product]

        best_key = None
        best_slug = None
        for slug, ops in countries.items():
            if slug not in COUNTRY_INFO or not isinstance(ops, dict):
                continue
            rates, total = [], 0
            for op_name, info in ops.items():
                if not isinstance(info, dict):
                    continue
                if op_name.lower() in self.exclude_operators:
                    continue
                count = _to_int(info.get("count")) or 0
                if count < self.min_count:
                    continue
                rates.append(_to_float(info.get("rate")) or 0.0)
                total += count
            if not rates:
                continue
            key = (max(rates), total)
            if best_key is None or key > best_key:
                best_key, best_slug = key, slug
        if not best_slug:
            raise SmsError(f"5sim: no country with stock for product '{product}'")
        log.info("5sim auto-selected country=%s for product=%s (rate=%.2f, count=%s)",
                 best_slug, product, best_key[0], best_key[1])
        return best_slug

    def ensure_country(self) -> str:
        """Resolve the country to use (auto-pick the best one if not configured)."""
        if not self.country:
            if not self.product:
                raise SmsError("FIVESIM_PRODUCT 未配置，无法自动选择国家")
            self.country = self.find_best_country(self.product)
        return self.country

    def country_for_form(self) -> Optional[str]:
        # Resolve (auto if needed) so the web form picks the SAME country we buy.
        try:
            return self.ensure_country()
        except SmsError:
            return self.country or None

    def _resolve_operator(self) -> dict:
        """Return the operator dict to use for buying (manual or auto)."""
        try:
            result = self.select_best_operator(self.country, self.product, self.strategy)
            return result["selected"]
        except SmsError as exc:
            log.warning("5sim operator auto-select failed (%s); falling back to %s", exc, self.operator_fallback)
            return {"operator": self.operator_fallback or "any", "price": None, "success_rate": None}

    def buy_number(self) -> SmsOrder:
        if not self.product:
            raise SmsError("FIVESIM_PRODUCT 未配置，且请求中没有传 fivesim_product")
        # Resolve the country (auto-pick the best-rate country if not configured).
        self.ensure_country()

        selected = self._resolve_operator()
        operator = selected.get("operator") or "any"

        def _do_buy(op):
            path = f"/v1/user/buy/activation/{self.country}/{op}/{self.product}"
            params = {}
            # maxPrice only applies when operator == 'any'
            if self.max_price is not None and op == "any":
                params["maxPrice"] = self.max_price
            log.info("5sim buying: country=%s operator=%s product=%s", self.country, op, self.product)
            return self._get(path, params=params or None)

        # Try the chosen operator. If it has no stock, use the configured
        # fallback operator. This lets a manual preference like virtual62 fall
        # back to virtual34 without waiting for the outer retry loop to hit the
        # same unavailable operator repeatedly. For auto strategies with no
        # explicit fallback, the historical fallback remains "any".
        try:
            body = _do_buy(operator)
        except SmsError as exc:
            msg = str(exc).lower()
            stock_issue = ("no free phones" in msg or "out of stock" in msg or "no product" in msg)
            fallback_operator = (self.operator_fallback or "any").strip() or "any"
            if operator != "any" and fallback_operator.lower() != operator.lower() and stock_issue:
                log.warning(
                    "5sim operator=%s unavailable (%s); retrying operator=%s",
                    operator, str(exc)[:50], fallback_operator,
                )
                first_exc = exc
                operator = fallback_operator
                selected = {"operator": fallback_operator, "price": None, "success_rate": None}
                try:
                    body = _do_buy(operator)
                except SmsError as fallback_exc:
                    raise SmsError(
                        f"5sim buy failed on operator={selected['operator']} after "
                        f"primary failed ({first_exc}); fallback failed ({fallback_exc})"
                    ) from fallback_exc
            else:
                raise
        if not isinstance(body, dict) or body.get("id") is None:
            raise SmsError(f"5sim buy returned no order id: {body}")

        order_id = str(body.get("id"))
        phone = str(body.get("phone") or "")
        if not phone:
            raise SmsError(f"5sim buy returned no phone: {body}")

        log.info("5sim bought number order_id=%s phone=%s operator=%s", order_id, phone, operator)
        return SmsOrder(
            provider=self.name,
            order_id=order_id,
            phone_number=phone,
            token=None,
            raw=body,
            country=self.country,
            product=self.product,
            operator=operator,
            strategy=self.strategy,
            price=_to_float(selected.get("price")),
            success_rate=_to_float(selected.get("success_rate")),
        )

    def wait_code(self, order: SmsOrder, timeout: int) -> str:
        deadline = time.monotonic() + timeout
        last_status = None
        while time.monotonic() < deadline:
            body = self._get(f"/v1/user/check/{order.order_id}")
            if isinstance(body, dict):
                last_status = body.get("status")
                for sms in body.get("sms") or []:
                    text = sms.get("text") or ""
                    candidate = (sms.get("code") or "").strip()
                    # Spec: extract via SMS_CODE_PATTERN; fall back to 5sim's own code field.
                    code = extract_code(text, self.code_pattern)
                    if not code and candidate:
                        code = extract_code(candidate, self.code_pattern) or candidate
                    if code:
                        log.info("5sim received code for order_id=%s", order.order_id)
                        return code
            time.sleep(self.poll_interval)
        raise SmsTimeout(
            f"5sim no SMS code within {timeout}s (order_id={order.order_id}, last_status={last_status})"
        )

    def finish_order(self, order: SmsOrder) -> None:
        try:
            self._get(f"/v1/user/finish/{order.order_id}")
            log.info("5sim finished order_id=%s", order.order_id)
        except SmsError as exc:
            raise SmsError(f"5sim finish failed: {exc}") from exc

    def cancel_order(self, order: SmsOrder) -> None:
        try:
            self._get(f"/v1/user/cancel/{order.order_id}")
            log.info("5sim cancelled order_id=%s", order.order_id)
        except SmsError as exc:
            raise SmsError(f"5sim cancel failed: {exc}") from exc
