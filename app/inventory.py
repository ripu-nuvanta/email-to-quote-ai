"""Stock and lead-time lookup, either from the local DB or from another system's API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

import httpx

from .config import Settings
from .models import Product

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Availability:
    status: str  # in_stock | partial | backorder | unknown
    on_hand: int | None
    lead_time_days: int | None


UNKNOWN = Availability("unknown", None, None)


class InventoryService(Protocol):
    def check(self, product: Product, quantity: Decimal) -> Availability: ...


def classify(on_hand: int, quantity: Decimal, in_stock_days: int, backorder_days: int) -> Availability:
    if on_hand >= quantity:
        return Availability("in_stock", on_hand, in_stock_days)
    return Availability("partial" if on_hand > 0 else "backorder", on_hand, backorder_days)


class DatabaseInventory:
    def check(self, product: Product, quantity: Decimal) -> Availability:
        inv = product.inventory
        if inv is None:
            return UNKNOWN
        return classify(inv.on_hand, quantity, inv.lead_time_days_in_stock, inv.lead_time_days_backorder)


class HttpInventory:
    """Calls GET {base_url}/availability?sku=..&quantity=.. expecting
    {"on_hand": int, "lead_time_days": int} (optionally "status"). Failures degrade to "unknown"
    so the line is flagged for the salesperson instead of blocking the quote."""

    def __init__(self, base_url: str, api_key: str = "", timeout: float = 5.0, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout)
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def check(self, product: Product, quantity: Decimal) -> Availability:
        try:
            response = self.client.get(
                f"{self.base_url}/availability",
                params={"sku": product.sku, "quantity": str(quantity)},
                headers=self.headers,
            )
            response.raise_for_status()
            data = response.json()
            on_hand, lead = int(data["on_hand"]), int(data["lead_time_days"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            log.warning("Inventory API lookup failed for %s", product.sku, exc_info=True)
            return UNKNOWN
        status = data.get("status") or classify(on_hand, quantity, lead, lead).status
        return Availability(status, on_hand, lead)


def make_inventory(settings: Settings) -> InventoryService:
    if settings.inventory_api_url:
        return HttpInventory(settings.inventory_api_url, settings.inventory_api_key, settings.inventory_api_timeout)
    return DatabaseInventory()
