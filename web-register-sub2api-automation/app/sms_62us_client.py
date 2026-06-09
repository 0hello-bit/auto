"""62-US SMS provider implementation.

API (Bearer token auth)::

    GET  /api/v1/info           account info / balance      -> check_profile()
    GET  /api/v1/goods          list goods                  -> list_goods()
    GET  /api/v1/goods/detail   goods detail                -> goods_detail()
    POST /api/v1/get            place an order               \\
    GET  /api/v1/order/tokens   number + token for an order  / -> buy_number()
    GET  /api/v1/msg            poll incoming SMS            -> wait_code()

IMPORTANT - field-mapping assumptions
-------------------------------------
62-US does not publish a public API spec (the docs live behind the account
login), so the request fields and response field names below are best-effort
guesses based on the endpoint names. Parsing is intentionally defensive
(several candidate field names are probed, both enveloped ``{code,msg,data}``
and flat responses are accepted). If your account's real API uses different
field names, adjust ``_pick(...)`` keys / the request body in this file.

The endpoint list above contains NO finish/cancel endpoint, so
:meth:`finish_order` and :meth:`cancel_order` are no-ops (see README).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterable, List, Optional

import requests

from .config import proxies_for
from .sms_provider_base import SmsError, SmsOrder, SmsProvider, SmsTimeout, extract_code

log = logging.getLogger(__name__)

_TIMEOUT = 30
_SUCCESS_CODES = {0, 1, 200, "0", "1", "200", "success", "ok"}


class Us62Provider(SmsProvider):
    name = "62us"

    def __init__(
        self,
        api_key: str,
        base: str = "https://api.62-us.com",
        goods_id: str = "",
        code_pattern: str = r"\b\d{6}\b",
        poll_interval: int = 5,
    ) -> None:
        self.api_key = api_key
        self.base = base.rstrip("/")
        self.goods_id = goods_id
        self.code_pattern = code_pattern
        self.poll_interval = max(1, poll_interval)

    # ------------------------------------------------------------------ #
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, *, params=None, json_body=None) -> Any:
        url = f"{self.base}{path}"
        try:
            resp = requests.request(
                method, url, headers=self._headers(), params=params, json=json_body,
                timeout=_TIMEOUT, proxies=proxies_for(url),
            )
        except requests.RequestException as exc:
            raise SmsError(f"62-US request failed ({method} {path}): {exc}") from exc

        if resp.status_code == 401:
            raise SmsError("62-US unauthorized (check US62_API_KEY)")
        if resp.status_code >= 400:
            raise SmsError(f"62-US HTTP {resp.status_code} on {path}: {resp.text[:200]}")

        try:
            body = resp.json()
        except ValueError as exc:
            raise SmsError(f"62-US returned non-JSON on {path}: {resp.text[:200]}") from exc

        # Surface API-level errors when an envelope clearly signals failure.
        if isinstance(body, dict) and "code" in body:
            if body["code"] not in _SUCCESS_CODES and body.get("data") is None:
                raise SmsError(f"62-US error on {path}: {body.get('msg') or body}")
        return body

    @staticmethod
    def _data(body: Any) -> Any:
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    @classmethod
    def _candidates(cls, body: Any) -> List[dict]:
        out: List[dict] = []
        if isinstance(body, dict):
            out.append(body)
        data = cls._data(body)
        if isinstance(data, dict):
            out.append(data)
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            out.append(data[0])
        return out

    @classmethod
    def _pick(cls, body: Any, *keys: str) -> Optional[Any]:
        for d in cls._candidates(body):
            for key in keys:
                if key in d and d[key] not in (None, ""):
                    return d[key]
        return None

    # ------------------------------------------------------------------ #
    def check_profile(self) -> dict:
        body = self._request("GET", "/api/v1/info")
        data = self._data(body)
        if isinstance(data, dict):
            data = dict(data)
            data.setdefault("provider", self.name)
            return data
        return {"provider": self.name, "raw": data}

    def list_goods(self) -> Any:
        return self._data(self._request("GET", "/api/v1/goods"))

    def goods_detail(self) -> Any:
        return self._data(self._request("GET", "/api/v1/goods/detail", params={"goods_id": self.goods_id}))

    def buy_number(self) -> SmsOrder:
        if not self.goods_id:
            raise SmsError("US62_GOODS_ID is not configured")

        # 1) place the order
        order_body = self._request("POST", "/api/v1/get", json_body={"goods_id": self.goods_id})
        order_id = self._pick(order_body, "order_id", "orderId", "id", "oid", "order")
        if not order_id:
            raise SmsError(f"62-US /get returned no order id: {order_body}")
        order_id = str(order_id)

        # Some deployments return the number/token directly from /get.
        phone = self._pick(order_body, "phone", "phone_number", "number", "mobile", "tel")
        token = self._pick(order_body, "token", "order_token")

        # 2) otherwise query order tokens for number + token
        if not phone:
            tokens_body = self._request(
                "GET", "/api/v1/order/tokens", params={"order_id": order_id}
            )
            phone = self._pick(tokens_body, "phone", "phone_number", "number", "mobile", "tel")
            token = token or self._pick(tokens_body, "token", "order_token")
            raw = {"get": order_body, "tokens": tokens_body}
        else:
            raw = {"get": order_body}

        if not phone:
            raise SmsError(f"62-US could not resolve a phone number for order {order_id}")

        log.info("62-US bought number order_id=%s phone=%s", order_id, phone)
        return SmsOrder(
            provider=self.name,
            order_id=order_id,
            phone_number=str(phone),
            token=str(token) if token else None,
            raw=raw,
        )

    def wait_code(self, order: SmsOrder, timeout: int) -> str:
        deadline = time.monotonic() + timeout
        params = {"order_id": order.order_id}
        if order.token:
            params["token"] = order.token

        while time.monotonic() < deadline:
            body = self._request("GET", "/api/v1/msg", params=params)
            # an explicit code field, if the API extracts it for us
            explicit = self._pick(body, "vcode", "verify_code", "verification_code", "sms_code")
            if explicit:
                code = extract_code(str(explicit), self.code_pattern) or str(explicit)
                log.info("62-US received code for order_id=%s", order.order_id)
                return code
            for text in self._iter_message_texts(self._data(body)):
                code = extract_code(text, self.code_pattern)
                if code:
                    log.info("62-US received code for order_id=%s", order.order_id)
                    return code
            time.sleep(self.poll_interval)

        raise SmsTimeout(f"62-US no SMS code within {timeout}s (order_id={order.order_id})")

    @staticmethod
    def _iter_message_texts(data: Any) -> Iterable[str]:
        """Yield candidate SMS text strings from a variety of response shapes."""
        text_keys = ("text", "content", "sms", "msg", "message", "body")

        def from_dict(d: dict) -> Iterable[str]:
            for key in text_keys:
                value = d.get(key)
                if isinstance(value, str) and value:
                    yield value

        if isinstance(data, str):
            yield data
        elif isinstance(data, dict):
            yield from from_dict(data)
            # nested list of messages
            for key in ("list", "messages", "sms", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            yield from from_dict(item)
                        elif isinstance(item, str):
                            yield item
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    yield from from_dict(item)
                elif isinstance(item, str):
                    yield item

    def finish_order(self, order: SmsOrder) -> None:
        # 62-US (per the documented endpoint list) has no finish endpoint -> no-op.
        log.info("62-US has no finish endpoint; skipping finish for order_id=%s", order.order_id)

    def cancel_order(self, order: SmsOrder) -> None:
        # 62-US (per the documented endpoint list) has no cancel endpoint -> no-op.
        log.info("62-US has no cancel endpoint; skipping cancel for order_id=%s", order.order_id)
