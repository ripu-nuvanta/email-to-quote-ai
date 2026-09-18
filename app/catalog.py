"""Match free-text product requests to catalogue products.

Exact SKU hits win outright. Otherwise each product (name + aliases) is scored on
token recall (how much of the request it explains), token precision (how much of the
product name was mentioned) and character similarity. In Postgres the same idea can be
pushed into SQL with pg_trgm (see db/schema.sql) once the catalogue is large.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from .models import Product

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_NUM_UNIT = re.compile(r"\b(\d+(?:\.\d+)?)\s+(mm|cm|m|kg|g|v|w|ah)\b")
_CAT = re.compile(r"\bcat\s+(\d+a?)\b")
_SKU_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)+")
_STOPWORDS = frozenset(
    {"a", "an", "and", "the", "of", "for", "with", "x", "size", "box", "boxes", "pc", "pcs", "pieces", "units", "ea", "please", "qty"}
)


def normalize(text: str) -> str:
    text = _NON_ALNUM.sub(" ", (text or "").lower())
    text = _NUM_UNIT.sub(r"\1\2", text)
    text = _CAT.sub(r"cat\1", text)
    return " ".join(text.split())


def _stem(token: str) -> str:
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> frozenset[str]:
    return frozenset(_stem(t) for t in normalize(text).split() if t not in _STOPWORDS)


def normalize_sku(code: str) -> str:
    return re.sub(r"[\s_]", "", code.upper())


@dataclass(frozen=True)
class MatchCandidate:
    product: Product
    score: float
    method: str  # "sku" | "fuzzy"

    def as_dict(self) -> dict:
        return {"sku": self.product.sku, "name": self.product.name, "score": round(self.score, 3)}


class ProductMatcher:
    def __init__(self, products: Iterable[Product]):
        self._products = list(products)
        self._by_sku = {normalize_sku(p.sku): p for p in self._products}
        self._variants = {
            p.sku: [(normalize(v), tokenize(v)) for v in [p.name, *(p.aliases or [])]] for p in self._products
        }

    def match(self, description: str, sku: str | None = None, limit: int = 3) -> list[MatchCandidate]:
        codes = ([sku] if sku else []) + _SKU_TOKEN.findall(description or "")
        for code in codes:
            product = self._by_sku.get(normalize_sku(code))
            if product is not None:
                return [MatchCandidate(product, 1.0, "sku")]

        query_norm, query_tokens = normalize(description), tokenize(description)
        if not query_tokens:
            return []
        scored = [MatchCandidate(p, self._score(query_norm, query_tokens, p), "fuzzy") for p in self._products]
        scored = sorted((c for c in scored if c.score > 0.2), key=lambda c: c.score, reverse=True)
        return scored[:limit]

    def _score(self, query_norm: str, query_tokens: frozenset[str], product: Product) -> float:
        variants = self._variants[product.sku]
        all_tokens = frozenset().union(*(tokens for _, tokens in variants))
        recall = len(query_tokens & all_tokens) / len(query_tokens)
        precision = max((len(query_tokens & t) / len(t) for _, t in variants if t), default=0.0)
        similarity = max(SequenceMatcher(None, query_norm, n).ratio() for n, _ in variants)
        return 0.55 * recall + 0.25 * precision + 0.20 * similarity

    def score(self, description: str, product: Product) -> float:
        """How well free text describes one product (0-1), using its name and aliases."""
        tokens = tokenize(description)
        if not tokens:
            return 0.0
        if product.sku not in self._variants:
            self._variants[product.sku] = [(normalize(v), tokenize(v)) for v in [product.name, *(product.aliases or [])]]
        return self._score(normalize(description), tokens, product)
