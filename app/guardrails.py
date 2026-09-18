"""Guardrails: rule-based checks that flag a quote line for a person to review.

They never change products, prices or quantities. They only add a clear reason to the line, so an unusual
request can't slip through approval unnoticed. No AI is involved in any of these checks.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal

from .catalog import ProductMatcher, normalize_sku, tokenize
from .config import Settings
from .documents import EmailContent
from .models import Product
from .schemas import ExtractedItem

PIECE_UNITS = frozenset({"pc", "pcs", "piece", "pieces", "unit", "units", "ea", "each", "no", "nos", "qty"})
PACK_UNITS = frozenset({"box", "boxes", "bx", "pack", "packs", "pk", "pkt", "case", "cases", "carton", "cartons", "bag", "bags"})
SET_UNITS = frozenset({"set", "sets", "kit", "kits"})
LENGTH_UNITS = {"m": 1.0, "meter": 1.0, "meters": 1.0, "metre": 1.0, "metres": 1.0, "mtr": 1.0, "ft": 0.3048, "foot": 0.3048, "feet": 0.3048}
WHOLE_UNIT_KINDS = frozenset({"piece", "pack", "set"})
PLURALS = {"box": "boxes", "set": "sets", "pack": "packs", "case": "cases", "roll": "rolls", "bag": "bags", "pair": "pairs", "kit": "kits"}

_PACK_OF = re.compile(r"\b(?:box|pack|case|bag|carton)\s+of\s+(\d+)\b", re.I)
_LENGTH_IN_NAME = re.compile(r"\b(\d+(?:\.\d+)?)\s*m\b", re.I)
_SKU_LIKE = re.compile(r"[A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)+")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _num(value) -> str:
    return f"{Decimal(str(value)).normalize():f}"


def _plural(unit: str | None, count=2) -> str:
    unit = unit or "units"
    return unit if count == 1 else PLURALS.get(unit, unit)


def unit_kind(unit: str | None) -> str | None:
    word = (unit or "").strip(" .").lower()
    if word in PIECE_UNITS:
        return "piece"
    if word in PACK_UNITS:
        return "pack"
    if word in SET_UNITS:
        return "set"
    if word in LENGTH_UNITS:
        return "length"
    return None


def pack_size(product: Product) -> int | None:
    """Pieces per box/pack: the product's units_per_pack, or read from a name like "(box of 100)"."""
    if product.units_per_pack:
        return product.units_per_pack
    match = _PACK_OF.search(product.name or "")
    return int(match.group(1)) if match else None


def pack_length_m(product: Product) -> float | None:
    """Metres per box, read from a name like "Cat6 Cable, 305 m Box"."""
    match = _LENGTH_IN_NAME.search(product.name or "")
    return float(match.group(1)) if match else None


# ------------------------------------------------------------------ 1. SKU vs description

def sku_description_conflict(item: ExtractedItem, product: Product, matcher: ProductMatcher) -> str | None:
    """The customer wrote a valid SKU, but their words describe a different product."""
    own_codes = {normalize_sku(product.sku), normalize_sku(item.sku or "")}
    words = _SKU_LIKE.sub(lambda m: " " if normalize_sku(m.group(0)) in own_codes else m.group(0), item.product_description)
    if len(tokenize(words)) < 2:  # only the SKU (or a single word) was written: nothing to compare
        return None
    described = " ".join(words.split())
    own = matcher.score(words, product)
    other = next((c for c in matcher.match(words) if c.product.sku != product.sku), None)
    if other is not None and other.score >= 0.7 and other.score - own >= 0.15:
        return (f"SKU {product.sku} is {product.name}, but the customer described “{described}”, "
                f"which looks like {other.product.sku} ({other.product.name}). Check which one they want.")
    if own < 0.35:
        return f"SKU {product.sku} is {product.name}, but the customer described “{described}”. Check the SKU."
    return None


# ------------------------------------------------------------------ 2. Is it really in the email?

def quantity_in_text(quantity: float | None, text: str) -> bool:
    if quantity is None or quantity <= 0:
        return True
    return any(abs(float(number.replace(",", "")) - quantity) < 1e-9 for number in _NUMBER.findall(text))


def grounding_problem(item: ExtractedItem, source: EmailContent) -> str | None:
    """Flag an item whose words, SKU or quantity can't be found in what the customer actually sent."""
    text = source.full_text
    words = tokenize(item.product_description)
    coverage = len(words & tokenize(text)) / len(words) if words else 1.0
    sku_found = not item.sku or normalize_sku(item.sku) in normalize_sku(text)
    quantity_found = quantity_in_text(item.quantity, text)
    if coverage >= 0.6 and sku_found and quantity_found:
        return None
    if source.ai_files:  # read from a scan or photo: there is no text to compare with
        return "Read from a scanned file or photo: check the product and quantity against the attachment."
    missing = []
    if coverage < 0.6:
        missing.append(f"“{item.product_description}”")
    if not sku_found:
        missing.append(f"SKU {item.sku}")
    if not quantity_found:
        missing.append(f"quantity {_num(item.quantity)}")
    return f"Couldn't find {' or '.join(missing)} in the email or attachments: check the customer really asked for this."


# ------------------------------------------------------------------ 3. Quantity

def quantity_problems(quantity: Decimal, product: Product, usual: Decimal | None, settings: Settings) -> list[str]:
    problems = []
    unit = product.unit or "unit"
    if quantity != quantity.to_integral_value() and unit_kind(product.unit) in WHOLE_UNIT_KINDS:
        problems.append(f"{_num(quantity)} isn't a whole number, but this is sold per {unit}")
    if usual is not None and usual > 0 and quantity >= usual * settings.unusual_quantity_factor and quantity - usual >= 10:
        problems.append(
            f"Unusually large: this customer usually orders about {_num(usual)} {_plural(unit)}, this is {quantity / usual:.0f}× that"
        )
    elif quantity >= settings.large_quantity_threshold:
        problems.append(f"Unusually large quantity ({_num(quantity)} {_plural(unit)}): check for a typo")
    return problems


# ------------------------------------------------------------------ 4. Unit

def unit_problem(item: ExtractedItem, product: Product) -> str | None:
    """The customer counts in a different unit than the product is sold in (pieces vs boxes, metres vs boxes)."""
    asked, sold = unit_kind(item.unit), unit_kind(product.unit)
    if asked is None or sold is None or asked == sold or item.quantity <= 0:
        return None
    amount, unit = _num(item.quantity), product.unit
    if asked == "piece" and sold == "pack":
        size = pack_size(product)
        if size and size > 1:
            packs = math.ceil(item.quantity / size)
            return (f"Customer asked for {amount} {item.unit}, but this is sold per {unit} of {size} "
                    f"({amount} {_plural(unit)} = {_num(item.quantity * size)} pieces). Did they mean {packs} {_plural(unit, packs)}?")
        return f"Customer asked for {amount} {item.unit}, but this is sold per {unit}: check how many they need."
    if asked == "pack" and sold == "piece":
        return f"Customer asked for {amount} {item.unit}, but this is sold per {unit}: check how many are in their {item.unit}."
    if asked == "length":
        per_pack = pack_length_m(product)
        if per_pack:
            packs = math.ceil(item.quantity * LENGTH_UNITS[item.unit.strip(" .").lower()] / per_pack)
            return (f"Customer asked for {amount} {item.unit}, but this is sold per {_num(per_pack)} m {unit}. "
                    f"Did they mean {packs} {_plural(unit, packs)}?")
    return f"Customer asked for {amount} {item.unit}, but this is sold per {unit}: check the quantity."
