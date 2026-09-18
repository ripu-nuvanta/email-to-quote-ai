from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# --- LLM extraction contract (sent to OpenAI as a Structured Outputs JSON schema) ---

class ExtractedItem(BaseModel):
    product_description: str = Field(
        description="The product exactly as the customer described it, including size, colour and spec details."
    )
    sku: str | None = Field(
        description="Part number / SKU / catalogue code only if the customer explicitly wrote one; otherwise null. Never invent one."
    )
    quantity: float = Field(description="Requested quantity. Use 0 if the customer did not state a quantity.")
    unit: str | None = Field(description="Unit as written (e.g. 'boxes', 'pcs', 'm'), or null.")
    notes: str | None = Field(description="Item-specific remarks such as brand preference or accepted alternatives, or null.")


class ExtractedQuoteRequest(BaseModel):
    is_quote_request: bool = Field(description="True only if the customer asks for prices or a quotation for products.")
    customer_name: str | None = Field(
        description="Name of the customer requesting the quote. If the email was forwarded by a colleague, the original customer's name. Null if unknown."
    )
    company: str | None = Field(description="The customer's company name, or null.")
    customer_email: str | None = Field(
        description="Email address of the customer requesting the quote. If the email was forwarded by a colleague, the original customer's address from the forwarded content. Null if not written anywhere."
    )
    shipping_address: str | None = Field(description="Delivery address as written, or null.")
    requested_delivery: str | None = Field(description="Requested delivery date or timeframe as written, or null.")
    items: list[ExtractedItem]
    notes: str | None = Field(
        description="Other requirements, including any prices, discounts or terms the customer proposes (non-binding), or null."
    )


class QuoteChange(BaseModel):
    action: Literal["add", "remove", "change_quantity", "replace"] = Field(
        description="add = a product not on the quote yet; remove = no longer wanted; change_quantity = same product, new amount; replace = a different product instead of a line"
    )
    line_number: int | None = Field(description="Number of the current quote line this change refers to, from the list provided; null for add")
    existing_item: str | None = Field(description="The current quote item the customer refers to, in their words; null for add")
    new_product: str | None = Field(description="For add or replace: the product they want, in their words (keep size, colour, model); otherwise null")
    new_sku: str | None = Field(description="SKU / part number of the new product only if the customer wrote one; otherwise null. Never invent one.")
    quantity: float | None = Field(description="New quantity if stated; null keeps the current quantity")
    note: str | None = Field(description="Detail about this change worth passing to the salesperson, or null")


class FollowUpChanges(BaseModel):
    is_change_request: bool = Field(description="True only if the customer asks to add, remove, swap or change the quantity of products")
    changes: list[QuoteChange]
    summary: str = Field(description="One short plain-English sentence describing what the customer asked for")


# --- Inbound ---

class Attachment(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"
    content_base64: str


class InboundEmail(BaseModel):
    message_id: str = Field(min_length=1, max_length=512, description="RFC 5322 Message-ID or provider message id")
    provider_message_id: str | None = Field(default=None, description="Gmail / Microsoft Graph message id, used to reply in the thread")
    thread_id: str | None = None
    in_reply_to: str | None = Field(default=None, description="Message-ID this email replies to; links follow-ups to their quote")
    references: list[str] = Field(default=[], description="Message-IDs of earlier emails in the same conversation")
    from_email: str = Field(min_length=3, max_length=320)
    from_name: str | None = None
    subject: str = ""
    body_text: str = ""
    body_html: str | None = None
    attachments: list[Attachment] = []


class SimulatedEmail(BaseModel):
    from_email: str = Field(min_length=3, max_length=320)
    from_name: str | None = None
    subject: str = ""
    body_text: str = ""
    body_html: str | None = None
    attachments: list[Attachment] = []
    thread_id: str | None = None
    in_reply_to: str | None = None

    @model_validator(mode="after")
    def has_content(self) -> SimulatedEmail:
        if not (self.body_text.strip() or (self.body_html or "").strip() or self.attachments):
            raise ValueError("The email needs a body or at least one attachment")
        return self


class IngestResult(BaseModel):
    quote_id: int
    number: str | None
    status: str
    needs_review_count: int
    approval_url: str


# --- Salesperson actions ---

class LineEdit(BaseModel):
    """Edit one line. An edit that only carries `id` marks the line as reviewed."""

    id: int | None = Field(default=None, description="Existing line id; omit to add a new line")
    remove: bool = False
    product_sku: str | None = None
    description: str | None = None
    quantity: Decimal | None = Field(default=None, gt=0)
    unit_price: Decimal | None = Field(default=None, ge=0)
    discount_pct: Decimal | None = Field(default=None, ge=0, le=100)


class QuoteUpdate(BaseModel):
    actor: str = Field(min_length=1)
    customer_email: str | None = Field(default=None, description="Change who the quote is for (re-prices the quote)")
    lines: list[LineEdit] = []
    shipping_total: Decimal | None = Field(default=None, ge=0)
    reset_shipping: bool = False
    internal_notes: str | None = None


class ApproveRequest(BaseModel):
    approver: str = Field(min_length=1)
    cover_message: str | None = None
    confirm_flagged_lines: bool = False
    send: bool = True


class SendRequest(BaseModel):
    actor: str = Field(min_length=1)
    cover_message: str | None = None


class RejectRequest(BaseModel):
    approver: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    company: str | None = None
    tax_region: str | None = None
    discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    payment_terms: str | None = Field(default=None, min_length=1)
    billing_address: str | None = None
    is_verified: bool | None = None


class CustomerPriceIn(BaseModel):
    sku: str = Field(min_length=1)
    unit_price: Decimal = Field(ge=0)
    min_qty: int = Field(default=1, ge=1)
    valid_from: date | None = None
    valid_until: date | None = None
    note: str | None = Field(default=None, max_length=200)


# --- n8n integration (claim -> send -> report) ---

class ClaimResponse(BaseModel):
    claim_token: str
    quote_id: int
    number: str
    to: str
    cc: list[str]
    subject: str
    body_text: str
    reply_to_provider_message_id: str | None
    attachment_filename: str
    attachment_mime_type: str
    attachment_base64: str


class SentReport(BaseModel):
    claim_token: str = Field(min_length=1)
    provider_message_id: str | None = None


class FailedReport(BaseModel):
    claim_token: str = Field(min_length=1)
    error: str = "Email step failed"


# --- Outbound ---

class AppConfig(BaseModel):
    company_name: str
    currency: str
    send_mode: str
    ai_reading: bool
    internal_email_domains: list[str]
    dev_tools: bool


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    company: str | None
    email: str
    tax_region: str | None
    discount_pct: Decimal
    payment_terms: str
    is_verified: bool


class CustomerListItem(CustomerOut):
    billing_address: str | None
    price_count: int


class CustomerPriceOut(BaseModel):
    id: int
    sku: str
    product_name: str
    unit: str
    min_qty: int
    unit_price: Decimal
    list_price: Decimal
    valid_from: date | None
    valid_until: date | None
    note: str | None


class QuoteLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    position: int
    product_id: int | None
    requested_text: str
    requested_sku: str | None
    sku: str | None
    description: str
    unit: str | None
    quantity: Decimal
    unit_price: Decimal
    price_overridden: bool
    price_source: str | None
    discount_pct: Decimal
    line_total: Decimal
    match_method: str | None
    match_confidence: float | None
    alternatives: list[dict]
    stock_status: str | None
    on_hand: int | None
    lead_time_days: int | None
    needs_review: bool
    review_reason: str | None


class QuoteEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    at: datetime
    actor: str
    event: str
    detail: dict | None


class ConversationEntry(BaseModel):
    kind: str  # request | follow_up
    from_email: str
    from_name: str | None
    subject: str
    body: str
    attachments: list[dict]
    received_at: datetime | None
    summary: str | None
    outcome: str | None  # updated | revision_created | no_changes
    changes: list[dict] | None
    quote_number: str | None


class QuoteSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str | None
    status: str
    source_from: str
    source_subject: str
    customer_name: str | None
    currency: str
    total: Decimal
    needs_review_count: int
    revision: int | None
    reply_count: int
    created_at: datetime


class QuoteOut(QuoteSummary):
    source_message_id: str
    source_body: str
    conversation: list[ConversationEntry]
    revision_of_id: int | None
    revision_of_number: str | None
    latest_revision_id: int | None
    latest_revision_number: str | None
    forwarded_by: str | None
    attachments: list[dict] | None
    customer: CustomerOut | None
    subtotal: Decimal
    discount_total: Decimal
    shipping_total: Decimal
    shipping_manual: bool
    shipping_service: str | None
    shipping_source: str | None
    shipping_transit_days: int | None
    tax_rate: Decimal
    tax_total: Decimal
    valid_until: date | None
    requested_delivery: str | None
    shipping_address: str | None
    customer_notes: str | None
    internal_notes: str | None
    cover_message: str | None
    approved_by: str | None
    approved_at: datetime | None
    rejection_reason: str | None
    sent_at: datetime | None
    lines: list[QuoteLineOut]
    events: list[QuoteEventOut]


class ProductOut(BaseModel):
    sku: str
    name: str
    unit: str
    base_price: Decimal
    on_hand: int | None
