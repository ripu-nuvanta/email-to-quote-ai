"""Create the 20 sample quotes (one per demo customer) shown on the Quotes page.

Run from the project folder:   .venv\\Scripts\\python -m app.demo_quotes

Uses the built-in reader (no OpenAI cost), never sends email, and is safe to run again:
samples that already exist are left as they are.
"""

from __future__ import annotations

from .config import get_settings
from .db import Base, make_engine, make_session_factory
from .extraction import HeuristicExtractor
from .inventory import make_inventory
from .mail import ConsoleMailer
from .migrate import ensure_schema
from .seed import seed_demo_extras, seed_demo_quotes, seed_if_empty
from .service import QuoteService
from .shipping import make_shipping


def main() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    ensure_schema(engine, Base.metadata)
    with make_session_factory(engine)() as session:
        seed_if_empty(session)
        added = seed_demo_extras(session)
        print(f"Added {added['customers']} customers, {added['customer_prices']} contract prices, {added['tax_rates']} tax rates")
        service = QuoteService(
            session,
            settings,
            HeuristicExtractor(),
            make_inventory(settings),
            ConsoleMailer(settings.outbox_dir, settings.mail_sender),  # never used: samples are not sent
            make_shipping(settings),
        )
        for quote in seed_demo_quotes(service):
            print(f"{quote.number}  {quote.status:<17} {quote.customer_name}  {quote.currency} {quote.total:,.2f}")


if __name__ == "__main__":
    main()
