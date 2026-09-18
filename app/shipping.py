"""Shipping cost: live rates from a courier / shipping platform API, or fixed rules from settings."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx

from .config import Settings
from .pricing import ZERO, money

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShippingRequest:
    weight_kg: Decimal
    order_value: Decimal
    currency: str
    destination: str | None
    items: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ShippingQuote:
    amount: Decimal
    service: str | None
    transit_days: int | None
    source: str  # rules | api | rules_fallback


class ShippingService(Protocol):
    def quote(self, request: ShippingRequest) -> ShippingQuote: ...


class RulesShipping:
    """Rate per kg with a minimum charge, free above an order value."""

    def __init__(self, rate_per_kg: Decimal, free_threshold: Decimal, min_charge: Decimal):
        self.rate_per_kg = rate_per_kg
        self.free_threshold = free_threshold
        self.min_charge = min_charge

    def quote(self, request: ShippingRequest, source: str = "rules") -> ShippingQuote:
        if request.order_value <= 0 or request.order_value >= self.free_threshold:
            amount = ZERO
        else:
            amount = max(money(self.min_charge), money(request.weight_kg * self.rate_per_kg))
        return ShippingQuote(amount, "Standard", None, source)


class HttpShipping:
    """POST {base_url}/rates with weight, order value, destination and items, expecting
    {"amount": 42.5, "service": "Ground", "transit_days": 3}. Adapt quote() to your provider's API.
    On any failure it falls back to the rules and marks the quote "rules_fallback" so sales can see it."""

    def __init__(self, base_url: str, api_key: str, fallback: RulesShipping, timeout: float = 8.0, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.fallback = fallback
        self.client = client or httpx.Client(timeout=timeout)
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def quote(self, request: ShippingRequest) -> ShippingQuote:
        if request.order_value <= 0:
            return ShippingQuote(ZERO, None, None, "api")
        try:
            response = self.client.post(
                f"{self.base_url}/rates",
                json={
                    "weight_kg": str(request.weight_kg),
                    "order_value": str(request.order_value),
                    "currency": request.currency,
                    "destination": request.destination,
                    "items": request.items,
                },
                headers=self.headers,
            )
            response.raise_for_status()
            data = response.json()
            amount = money(Decimal(str(data["amount"])))
            if amount < 0:
                raise ValueError("negative shipping amount")
            transit = data.get("transit_days")
            return ShippingQuote(amount, data.get("service"), int(transit) if transit is not None else None, "api")
        except (httpx.HTTPError, ValueError, KeyError, TypeError, InvalidOperation):
            log.warning("Shipping API failed; using standard rates", exc_info=True)
            return self.fallback.quote(request, source="rules_fallback")


def make_shipping(settings: Settings) -> ShippingService:
    rules = RulesShipping(settings.shipping_rate_per_kg, settings.free_shipping_threshold, settings.min_shipping_charge)
    if settings.shipping_api_url:
        return HttpShipping(settings.shipping_api_url, settings.shipping_api_key, rules, settings.shipping_api_timeout)
    return rules
