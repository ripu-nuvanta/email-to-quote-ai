"""Deterministic pricing: customer contract prices, quantity tiers, discounts and tax. Decimal throughout."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

from .models import Product, QuoteLine

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def money(value: Decimal | int) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def tier_unit_price(product: Product, quantity: Decimal) -> Decimal:
    price = product.base_price
    for tier in sorted(product.price_tiers, key=lambda t: t.min_qty):
        if quantity >= tier.min_qty:
            price = tier.unit_price
    return money(price)


def contract_unit_price(prices: Iterable, quantity: Decimal, today: date) -> Decimal | None:
    """Best matching customer price: the highest quantity break reached that is valid today."""
    applicable = [
        p
        for p in prices
        if quantity >= p.min_qty
        and (p.valid_from is None or p.valid_from <= today)
        and (p.valid_until is None or p.valid_until >= today)
    ]
    if not applicable:
        return None
    best = min(applicable, key=lambda p: (-p.min_qty, p.unit_price))
    return money(best.unit_price)


def line_amount(quantity: Decimal, unit_price: Decimal, discount_pct: Decimal) -> Decimal:
    return money(quantity * unit_price * (Decimal(100) - discount_pct) / Decimal(100))


@dataclass(frozen=True)
class Totals:
    subtotal: Decimal  # before discount
    discount_total: Decimal
    shipping_total: Decimal
    tax_total: Decimal
    total: Decimal


def calculate_totals(lines: Iterable[QuoteLine], *, tax_rate: Decimal, shipping_total: Decimal) -> Totals:
    lines = list(lines)
    gross = sum((money(l.quantity * l.unit_price) for l in lines), ZERO)
    net = sum((l.line_total for l in lines), ZERO)
    shipping = money(shipping_total or 0)
    tax = money(net * tax_rate)  # shipping treated as non-taxable in this model
    return Totals(money(gross), money(gross - net), shipping, tax, money(net + shipping + tax))
