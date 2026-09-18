from decimal import Decimal as D

from fastapi.testclient import TestClient

from app.catalog import ProductMatcher
from app.config import Settings
from app.documents import AIFile, EmailContent
from app.extraction import HeuristicExtractor
from app.guardrails import grounding_problem, quantity_problems, sku_description_conflict, unit_problem
from app.main import create_app
from app.schemas import ExtractedItem
from tests.conftest import AUTH, get_quote, ingest, lines_by_sku
from tests.test_units import catalog

JORDAN = "jordan.lee@brightline-construction.com"


def item(description, quantity, unit=None, sku=None):
    return ExtractedItem(product_description=description, sku=sku, quantity=quantity, unit=unit, notes=None)


def product(sku, products=None):
    return next(p for p in (products or catalog()) if p.sku == sku)


# ------------------------------------------------------------------ rules

def test_unit_check():
    nuts = product("FS-HN-M8")
    assert "Did they mean 5 boxes?" in unit_problem(item("M8 hex nuts", 500, "pcs"), nuts)
    assert "50000 pieces" in unit_problem(item("M8 hex nuts", 500, "pcs"), nuts)
    assert unit_problem(item("M8 hex nuts", 5, "boxes"), nuts) is None
    assert "sold per ea" in unit_problem(item("hard hats", 10, "boxes"), product("SF-HH-WHT"))
    assert "Did they mean 2 boxes?" in unit_problem(item("cat 6 cable", 600, "m"), product("EL-C6-305"))
    assert unit_problem(item("cordless drill", 12, "pcs"), product("TL-DR-18V")) is None
    assert unit_problem(item("cordless drill", 12), product("TL-DR-18V")) is None


def test_sku_vs_description_check():
    products = catalog()
    matcher, bolts = ProductMatcher(products), product("FS-HB-M8-50", products)
    conflict = sku_description_conflict(item("FS-HB-M8-50 M8 hex nuts", 40, sku="FS-HB-M8-50"), bolts, matcher)
    assert "looks like FS-HN-M8" in conflict
    assert sku_description_conflict(item("BOLT HEX M8X50 ZP 100/BX", 60, sku="FS-HB-M8-50"), bolts, matcher) is None
    assert sku_description_conflict(item("FS-HB-M8-50", 60, sku="FS-HB-M8-50"), bolts, matcher) is None


def test_is_it_really_in_the_email_check():
    content = EmailContent(body="Please quote:\n- 25 x hard hats (white)\n- FS-HB-M8-50 x 40")
    assert grounding_problem(item("hard hats (white)", 25), content) is None
    assert grounding_problem(item("FS-HB-M8-50", 40, sku="FS-HB-M8-50"), content) is None
    invented = grounding_problem(item("cordless drill 18V", 3), content)
    assert invented.startswith("Couldn't find") and "cordless drill 18V" in invented
    assert "quantity 30" in grounding_problem(item("hard hats (white)", 30), content)
    scan = EmailContent(body="See attached", ai_files=[AIFile("scan.pdf", "application/pdf", b"%PDF")])
    assert "scanned file or photo" in grounding_problem(item("hard hats", 25), scan)


def test_quantity_check():
    settings, hats = Settings(_env_file=None), product("SF-HH-WHT")
    assert quantity_problems(D(25), hats, None, settings) == []
    assert "Unusually large quantity (5000 ea)" in quantity_problems(D(5000), hats, None, settings)[0]
    assert "usually orders about 50 ea, this is 8× that" in quantity_problems(D(400), hats, D(50), settings)[0]
    assert quantity_problems(D(60), hats, D(50), settings) == []
    assert "isn't a whole number" in quantity_problems(D("2.5"), hats, None, settings)[0]


# ------------------------------------------------------------------ in real quotes

def test_guardrails_flag_lines_on_a_quote_and_leave_normal_lines_alone(client):
    result = ingest(client, {"message_id": "<g-1@brightline>", "from_email": JORDAN, "subject": "RFQ",
                             "body_text": "- 500 pcs M8 hex nuts\n- 40 x FS-HB-M8-50 M8 hex nuts\n- 25 x hard hats (white)\n- 5000 x M8 washers"})
    lines = {line["requested_text"]: line for line in get_quote(client, result["quote_id"])["lines"]}
    assert "Did they mean 5 boxes?" in lines["M8 hex nuts"]["review_reason"]
    assert "looks like FS-HN-M8" in lines["FS-HB-M8-50 M8 hex nuts"]["review_reason"]
    assert "Unusually large quantity" in lines["M8 washers"]["review_reason"]
    assert not lines["hard hats (white)"]["needs_review"]


def test_unusual_quantity_is_compared_with_the_customers_past_orders(client):
    first = ingest(client, {"message_id": "<h-1@northwind>", "from_email": "priya@northwindfacilities.com", "subject": "RFQ", "body_text": "- 50 x SF-HH-WHT"})
    assert client.post(f"/api/quotes/{first['quote_id']}/approve", json={"approver": "Sam"}, headers=AUTH).json()["status"] == "sent"
    second = ingest(client, {"message_id": "<h-2@northwind>", "from_email": "priya@northwindfacilities.com", "subject": "RFQ", "body_text": "- 400 x SF-HH-WHT"})
    [line] = get_quote(client, second["quote_id"])["lines"]
    assert line["needs_review"] and "usually orders about 50" in line["review_reason"]


class InventingExtractor(HeuristicExtractor):
    """Simulates an AI mistake: adds a product the customer never asked for."""

    def extract(self, email, content):
        request = super().extract(email, content)
        request.items.append(ExtractedItem(product_description="cordless drill 18V", sku=None, quantity=3, unit=None, notes=None))
        return request


def test_item_the_customer_never_asked_for_is_flagged(settings):
    with TestClient(create_app(settings, extractor=InventingExtractor())) as client:
        result = ingest(client, {"message_id": "<g-2@brightline>", "from_email": JORDAN, "subject": "RFQ", "body_text": "- 25 x hard hats (white)"})
        lines = lines_by_sku(get_quote(client, result["quote_id"]))
        assert lines["TL-DR-18V"]["review_reason"].startswith("Couldn't find")
        assert not lines["SF-HH-WHT"]["needs_review"]


def test_big_quantity_jump_in_a_follow_up_is_flagged(client, sample_email):
    quote_id = ingest(client, sample_email)["quote_id"]
    ingest(client, {"message_id": "<jump@brightline>", "from_email": JORDAN, "subject": "Re: " + sample_email["subject"],
                    "in_reply_to": sample_email["message_id"], "body_text": "Please change the hard hats to 250."})
    hats = lines_by_sku(get_quote(client, quote_id))["SF-HH-WHT"]
    assert "Big change (10× or more)" in hats["review_reason"]
