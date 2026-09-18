from datetime import date, timedelta
from decimal import Decimal as D
from types import SimpleNamespace

import httpx

from app.catalog import ProductMatcher, normalize
from app.documents import EmailContent
from app.extraction import HeuristicExtractor
from app.inventory import HttpInventory
from app.models import PriceTier, Product
from app.pricing import calculate_totals, contract_unit_price, line_amount, tier_unit_price
from app.schemas import InboundEmail
from app.seed import PRODUCTS
from app.shipping import HttpShipping, RulesShipping, ShippingRequest


def catalog():
    return [
        Product(sku=sku, name=name, aliases=aliases, unit=unit, base_price=D(price), weight_kg=D(weight),
                price_tiers=[PriceTier(min_qty=q, unit_price=D(p)) for q, p in tiers])
        for sku, name, aliases, unit, price, weight, tiers, _ in PRODUCTS
    ]


def test_normalize_joins_units_and_cat():
    assert normalize("Cat 6 cable, 305 m") == "cat6 cable 305m"


def test_exact_sku_wins():
    [match] = ProductMatcher(catalog()).match("please quote FS-HB-M8-50")
    assert match.product.sku == "FS-HB-M8-50" and match.method == "sku"


def test_fuzzy_match_prefers_the_specified_variant():
    matches = ProductMatcher(catalog()).match("nitrile gloves size L")
    assert matches[0].product.sku == "SF-GL-NIT-L"
    assert matches[0].score - matches[1].score > 0.1


def test_unspecified_variant_is_ambiguous():
    top, second, *_ = ProductMatcher(catalog()).match("nitrile gloves")
    assert {top.product.sku, second.product.sku} == {"SF-GL-NIT-L", "SF-GL-NIT-M"}
    assert abs(top.score - second.score) < 0.05


def test_alias_match():
    assert ProductMatcher(catalog()).match("hard hats (white)")[0].product.sku == "SF-HH-WHT"


def test_tier_pricing_and_line_amount():
    bolts = next(p for p in catalog() if p.sku == "FS-HB-M8-50")
    assert tier_unit_price(bolts, D(9)) == D("18.50")
    assert tier_unit_price(bolts, D(10)) == D("16.90")
    assert tier_unit_price(bolts, D(50)) == D("15.20")
    assert line_amount(D(3), D("10.00"), D("5")) == D("28.50")


def test_contract_price_uses_best_valid_quantity_break():
    today = date(2026, 9, 15)
    prices = [
        SimpleNamespace(min_qty=1, unit_price=D("11.50"), valid_from=None, valid_until=None),
        SimpleNamespace(min_qty=50, unit_price=D("10.00"), valid_from=None, valid_until=None),
        SimpleNamespace(min_qty=1, unit_price=D("1.00"), valid_from=None, valid_until=today - timedelta(days=1)),
    ]
    assert contract_unit_price(prices, D(10), today) == D("11.50")
    assert contract_unit_price(prices, D(60), today) == D("10.00")
    assert contract_unit_price(prices[2:], D(10), today) is None


def test_totals_with_shipping_and_tax():
    lines = [SimpleNamespace(quantity=D(2), unit_price=D("100.00"), line_total=D("190.00"), weight_kg=D("4"))]
    totals = calculate_totals(lines, tax_rate=D("0.0725"), shipping_total=D("15.00"))
    assert (totals.subtotal, totals.discount_total, totals.shipping_total, totals.tax_total, totals.total) == (
        D("200.00"), D("10.00"), D("15.00"), D("13.78"), D("218.78"))


def test_shipping_rules_and_api_fallback():
    rules = RulesShipping(D("1.50"), D("2500"), D("15"))
    small = ShippingRequest(weight_kg=D("30"), order_value=D("400"), currency="USD", destination=None)
    assert rules.quote(small).amount == D("45.00")
    assert rules.quote(ShippingRequest(D("1"), D("400"), "USD", None)).amount == D("15.00")
    assert rules.quote(ShippingRequest(D("100"), D("3000"), "USD", None)).amount == D("0.00")

    ok = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"amount": 31.2, "service": "Ground", "transit_days": 4})))
    live = HttpShipping("http://ship", "key", rules, client=ok).quote(small)
    assert (live.amount, live.service, live.transit_days, live.source) == (D("31.20"), "Ground", 4, "api")

    broken = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"price": "oops"})))
    fallback = HttpShipping("http://ship", "key", rules, client=broken).quote(small)
    assert (fallback.amount, fallback.source) == (D("45.00"), "rules_fallback")


def test_heuristic_extractor_formats():
    email = InboundEmail(message_id="m1", from_email="a@b.com", body_text=(
        "Hello\n- 60 boxes nitrile gloves size L\n2. Cat6 cable 305m box x 8\nFS-HB-M8-50 x 1,000\n"
        "From: Someone <someone@example.com>\nSubject: RFQ x 5\n"
        "We need delivery by Oct 3. Ship to 1200 Harbor Way, Oakland\n"))
    content = EmailContent(body=email.body_text, attachments_text="--- Attachment: po.csv ---\nFS-HN-M8,40\nFS-WA-M8 | M8 washers | 25")
    result = HeuristicExtractor().extract(email, content)
    assert [(i.product_description, i.quantity) for i in result.items] == [
        ("nitrile gloves size L", 60), ("Cat6 cable 305m box", 8), ("FS-HB-M8-50", 1000), ("FS-HN-M8", 40), ("FS-WA-M8", 25)]
    assert result.items[2].sku == "FS-HB-M8-50"
    assert result.requested_delivery == "Oct 3"
    assert result.shipping_address.startswith("1200 Harbor Way")


def test_http_inventory_degrades_to_unknown_on_error():
    product = catalog()[0]
    failing = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    assert HttpInventory("http://inv", client=failing).check(product, D(5)).status == "unknown"

    ok = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"on_hand": 3, "lead_time_days": 9})))
    availability = HttpInventory("http://inv", client=ok).check(product, D(5))
    assert (availability.status, availability.on_hand, availability.lead_time_days) == ("partial", 3, 9)
