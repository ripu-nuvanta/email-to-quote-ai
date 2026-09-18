from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import make_engine, make_session_factory
from app.extraction import HeuristicExtractor
from app.inventory import DatabaseInventory
from app.mail import ConsoleMailer
from app.main import create_app
from app.models import Customer
from app.seed import CUSTOMERS, DEMO_REQUESTS, seed_demo_extras, seed_demo_quotes
from app.service import QuoteService
from app.shipping import make_shipping
from tests.conftest import AUTH, get_quote, ingest

INTENDED_FOR_REVIEW = {
    "arahman@lakesidefacilityservices.com",  # "nitrile gloves" without a size
    "daniel@goldengaterenovation.com",  # "safety vests" without a size
    "rachel@silverlineevents.com",  # "safety vests" without a size
}


def session_for(settings):
    return make_session_factory(make_engine(settings.database_url))()


def test_sample_customers_are_seeded_once_and_marked(client, settings):
    customers = client.get("/api/customers", headers=AUTH).json()
    assert len(customers) == len(CUSTOMERS) == 22
    assert not any(c["email"].endswith(".example") for c in customers)
    sofia = next(c for c in customers if c["email"] == "s.rossi@sunbeltcolo.com")
    assert (sofia["company"], sofia["tax_region"], sofia["price_count"]) == ("Sunbelt Colocation", "US-AZ", 2)

    with session_for(settings) as session:
        assert all(session.scalars(select(Customer.is_sample)))
        assert seed_demo_extras(session) == {"tax_rates": 0, "customers": 0, "customer_prices": 0}


def test_sample_quotes_look_real_and_go_through_the_normal_pipeline(client, settings):
    with session_for(settings) as session:
        service = QuoteService(session, settings, HeuristicExtractor(), DatabaseInventory(),
                               ConsoleMailer(settings.outbox_dir, settings.mail_sender), make_shipping(settings))
        first = [quote.number for quote in seed_demo_quotes(service)]
        assert len(first) == 20
        assert [quote.number for quote in seed_demo_quotes(service)] == first  # repeat run adds nothing

    pending = client.get("/api/quotes?status=pending_approval", headers=AUTH).json()
    assert len(pending) == 20
    received_days, flagged, contract_priced = set(), set(), 0
    now = datetime.now(timezone.utc)
    for summary, request in zip(sorted(pending, key=lambda q: q["id"]), DEMO_REQUESTS):
        quote = get_quote(client, summary["id"])
        assert quote["customer"]["email"] == request["email"]
        assert len(quote["lines"]) == len(request["items"]), request["subject"]
        assert all(line["sku"] for line in quote["lines"]), request["subject"]
        assert quote["shipping_address"] and quote["shipping_address"] in request["ship_to"]
        received = datetime.fromisoformat(quote["created_at"]).replace(tzinfo=timezone.utc)
        assert received < now and received.weekday() < 5
        received_days.add(received.date())
        if quote["needs_review_count"]:
            flagged.add(request["email"])
        contract_priced += sum(line["price_source"] == "customer_price" for line in quote["lines"])
    assert len(received_days) >= 8  # spread over two weeks, not all at once
    assert flagged == INTENDED_FOR_REVIEW
    assert contract_priced >= 10


def test_sample_customers_are_never_really_emailed(settings, sample_email):
    sent = []

    class RecordingMailer:
        def send(self, message):
            sent.append(message.to)
            return "provider-id"

    real_email = settings.model_copy(update={"mail_provider": "gmail"})
    with TestClient(create_app(real_email, mailer=RecordingMailer())) as client:  # Jordan Lee is a sample customer
        quote_id = ingest(client, sample_email)["quote_id"]
        blocked = client.post(f"/api/quotes/{quote_id}/approve", json={"approver": "Sam", "confirm_flagged_lines": True}, headers=AUTH)
        assert blocked.status_code == 409 and "sample customer" in blocked.json()["detail"]
        assert sent == [] and get_quote(client, quote_id)["status"] == "approved"

    allowed = settings.model_copy(update={"mail_provider": "gmail", "allow_sample_sends": True})
    with TestClient(create_app(allowed, mailer=RecordingMailer())) as client:
        assert client.post(f"/api/quotes/{quote_id}/send", json={"actor": "Sam"}, headers=AUTH).json()["status"] == "sent"
        assert sent == ["jordan.lee@brightline-construction.com"]
