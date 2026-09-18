from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

MONEY = Numeric(12, 2)
QTY = Numeric(12, 3)
PCT = Numeric(5, 2)
RATE = Numeric(6, 4)
WEIGHT = Numeric(10, 3)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    company: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    email_domain: Mapped[str] = mapped_column(String(255), index=True)
    tax_region: Mapped[str | None] = mapped_column(String(20))
    discount_pct: Mapped[Decimal] = mapped_column(PCT, default=Decimal("0"))
    payment_terms: Mapped[str] = mapped_column(String(50), default="Net 30")
    billing_address: Mapped[str | None] = mapped_column(Text)
    # False for contacts auto-created from an unknown sender; sales must verify terms/tax.
    is_verified: Mapped[bool] = mapped_column(Boolean, default=True)
    # Built-in sample customers: quotes for them are never emailed through Gmail, Outlook or n8n.
    is_sample: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    unit: Mapped[str] = mapped_column(String(20), default="ea")
    units_per_pack: Mapped[int | None] = mapped_column(Integer)  # e.g. 100 for a box of 100; used by the unit check
    base_price: Mapped[Decimal] = mapped_column(MONEY)
    weight_kg: Mapped[Decimal] = mapped_column(WEIGHT, default=Decimal("0"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    price_tiers: Mapped[list[PriceTier]] = relationship(
        back_populates="product", cascade="all, delete-orphan", order_by="PriceTier.min_qty", lazy="selectin"
    )
    inventory: Mapped[InventoryItem | None] = relationship(
        back_populates="product", cascade="all, delete-orphan", lazy="selectin"
    )


class PriceTier(Base):
    __tablename__ = "price_tiers"
    __table_args__ = (UniqueConstraint("product_id", "min_qty"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    min_qty: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(MONEY)

    product: Mapped[Product] = relationship(back_populates="price_tiers")


class CustomerPrice(Base):
    """A negotiated price for one customer (and other verified contacts at the same company domain).
    Takes priority over list/tier prices; the customer's general discount is not applied on top."""

    __tablename__ = "customer_prices"
    __table_args__ = (UniqueConstraint("customer_id", "product_id", "min_qty"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    min_qty: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[Decimal] = mapped_column(MONEY)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(200))

    customer: Mapped[Customer] = relationship()
    product: Mapped[Product] = relationship(lazy="joined")


class InventoryItem(Base):
    __tablename__ = "inventory"

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)
    warehouse: Mapped[str] = mapped_column(String(50), default="MAIN")
    on_hand: Mapped[int] = mapped_column(Integer, default=0)
    lead_time_days_in_stock: Mapped[int] = mapped_column(Integer, default=2)
    lead_time_days_backorder: Mapped[int] = mapped_column(Integer, default=21)

    product: Mapped[Product] = relationship(back_populates="inventory")


class TaxRate(Base):
    __tablename__ = "tax_rates"

    region: Mapped[str] = mapped_column(String(20), primary_key=True)
    label: Mapped[str] = mapped_column(String(100))
    rate: Mapped[Decimal] = mapped_column(RATE)


class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str | None] = mapped_column(String(30), unique=True)
    # received -> pending_approval -> approved -> sending -> sent ; pending_approval -> rejected ; received -> ignored
    status: Mapped[str] = mapped_column(String(30), index=True, default="received")
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    # A customer follow-up on an approved/sent quote creates a revision (Q-...-R1, R2, ...) linked to it.
    revision_of_id: Mapped[int | None] = mapped_column(ForeignKey("quotes.id"))
    revision: Mapped[int | None] = mapped_column(Integer)

    source_message_id: Mapped[str] = mapped_column(String(512), unique=True)
    source_thread_id: Mapped[str | None] = mapped_column(String(255))
    source_provider_id: Mapped[str | None] = mapped_column(String(255))  # Gmail/Graph message id, for threaded replies
    source_from: Mapped[str] = mapped_column(String(320))
    source_subject: Mapped[str] = mapped_column(String(998), default="")
    source_body: Mapped[str] = mapped_column(Text, default="")
    forwarded_by: Mapped[str | None] = mapped_column(String(320))
    attachments: Mapped[list[dict] | None] = mapped_column(JSON)  # what was read from each attachment
    extraction: Mapped[dict | None] = mapped_column(JSON)

    currency: Mapped[str] = mapped_column(String(3), default="USD")
    subtotal: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    discount_total: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    shipping_total: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    shipping_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    shipping_service: Mapped[str | None] = mapped_column(String(100))
    shipping_source: Mapped[str | None] = mapped_column(String(20))  # rules | api | rules_fallback | manual
    shipping_transit_days: Mapped[int | None] = mapped_column(Integer)
    tax_rate: Mapped[Decimal] = mapped_column(RATE, default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    valid_until: Mapped[date | None] = mapped_column(Date)

    requested_delivery: Mapped[str | None] = mapped_column(String(200))
    shipping_address: Mapped[str | None] = mapped_column(Text)
    customer_notes: Mapped[str | None] = mapped_column(Text)
    internal_notes: Mapped[str | None] = mapped_column(Text)
    cover_message: Mapped[str | None] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(String(500))

    approved_by: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    send_claim_token: Mapped[str | None] = mapped_column(String(64))
    send_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    customer: Mapped[Customer | None] = relationship(lazy="joined")
    lines: Mapped[list[QuoteLine]] = relationship(
        back_populates="quote", cascade="all, delete-orphan", order_by="QuoteLine.position", lazy="selectin"
    )
    events: Mapped[list[QuoteEvent]] = relationship(
        back_populates="quote", cascade="all, delete-orphan", order_by="QuoteEvent.id", lazy="selectin"
    )
    messages: Mapped[list[QuoteMessage]] = relationship(
        back_populates="quote", cascade="all, delete-orphan", order_by="QuoteMessage.id", lazy="selectin"
    )
    revision_of: Mapped[Quote | None] = relationship(back_populates="revisions", remote_side="Quote.id")
    revisions: Mapped[list[Quote]] = relationship(back_populates="revision_of", order_by="Quote.id")

    @property
    def needs_review_count(self) -> int:
        return sum(1 for line in self.lines if line.needs_review)

    @property
    def customer_name(self) -> str | None:
        if self.customer is None:
            return None
        return f"{self.customer.name} ({self.customer.company})" if self.customer.company else self.customer.name

    @property
    def reply_count(self) -> int:
        return len(self.messages)

    @property
    def revision_of_number(self) -> str | None:
        return self.revision_of.number if self.revision_of is not None else None

    @property
    def latest_revision(self) -> Quote | None:
        latest = self
        while latest.revisions:
            latest = latest.revisions[-1]
        return None if latest is self else latest

    @property
    def latest_revision_id(self) -> int | None:
        return self.latest_revision.id if self.latest_revision is not None else None

    @property
    def latest_revision_number(self) -> str | None:
        return self.latest_revision.number if self.latest_revision is not None else None

    @property
    def conversation(self) -> list[dict]:
        """The customer's original request, then every reply across all revisions up to this one."""
        chain = [self]
        while chain[-1].revision_of is not None:
            chain.append(chain[-1].revision_of)
        root = chain[-1]
        from_name = root.customer.name if root.customer is not None and root.customer.email == root.source_from else None
        request = {
            "kind": "request", "from_email": root.source_from, "from_name": from_name, "subject": root.source_subject,
            "body": root.source_body, "attachments": root.attachments or [], "received_at": root.created_at,
            "summary": None, "outcome": None, "changes": None, "quote_number": root.number,
        }
        replies = [
            {
                "kind": "follow_up", "from_email": message.from_email, "from_name": message.from_name, "subject": message.subject,
                "body": message.body, "attachments": message.attachments or [], "received_at": message.received_at,
                "summary": message.summary, "outcome": message.outcome, "changes": message.changes, "quote_number": quote.number,
            }
            for quote in chain
            for message in quote.messages
        ]
        replies.sort(key=lambda entry: _naive(entry["received_at"]))
        return [request, *replies]


def _naive(value: datetime | None) -> datetime:
    if value is None:
        return datetime.max
    return value.replace(tzinfo=None) if value.tzinfo else value


class QuoteLine(Base):
    __tablename__ = "quote_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))

    requested_text: Mapped[str] = mapped_column(Text, default="")
    requested_sku: Mapped[str | None] = mapped_column(String(64))
    sku: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    unit: Mapped[str | None] = mapped_column(String(20))

    quantity: Mapped[Decimal] = mapped_column(QTY)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    price_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    price_source: Mapped[str | None] = mapped_column(String(20))  # customer_price | list_price | manual
    discount_pct: Mapped[Decimal] = mapped_column(PCT, default=Decimal("0"))
    line_total: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    weight_kg: Mapped[Decimal] = mapped_column(WEIGHT, default=Decimal("0"))

    match_method: Mapped[str | None] = mapped_column(String(20))  # sku | fuzzy | ai | manual
    match_confidence: Mapped[float | None] = mapped_column(Float)
    alternatives: Mapped[list[dict]] = mapped_column(JSON, default=list)

    stock_status: Mapped[str | None] = mapped_column(String(20))  # in_stock | partial | backorder | unknown
    on_hand: Mapped[int | None] = mapped_column(Integer)
    lead_time_days: Mapped[int | None] = mapped_column(Integer)

    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reason: Mapped[str | None] = mapped_column(Text)

    quote: Mapped[Quote] = relationship(back_populates="lines")
    product: Mapped[Product | None] = relationship(lazy="joined")


class QuoteEvent(Base):
    __tablename__ = "quote_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(200))
    event: Mapped[str] = mapped_column(String(50))
    detail: Mapped[dict | None] = mapped_column(JSON)

    quote: Mapped[Quote] = relationship(back_populates="events")


class QuoteMessage(Base):
    """A customer's follow-up email in the conversation of a quote (the original request lives on the quote)."""

    __tablename__ = "quote_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[str] = mapped_column(String(512), unique=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    from_email: Mapped[str] = mapped_column(String(320))
    from_name: Mapped[str | None] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(998), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[list[dict] | None] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(30))  # updated | revision_created | no_changes
    changes: Mapped[list[dict] | None] = mapped_column(JSON)

    quote: Mapped[Quote] = relationship(back_populates="messages")
