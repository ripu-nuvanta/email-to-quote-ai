"""Quote lifecycle: email -> reading -> matching -> pricing -> PDF -> approval -> send."""

from __future__ import annotations

import base64
import hmac
import logging
import re
import secrets
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import and_, or_, select
from sqlalchemy import update as sql_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from statistics import median

from . import guardrails
from .catalog import MatchCandidate, ProductMatcher, normalize
from .config import Settings
from .documents import EmailContent, find_forwarded_sender, read_email
from .errors import Conflict, NotFound
from .extraction import Extractor
from .inventory import InventoryService
from .mail import Mailer, OutgoingEmail
from .models import Customer, CustomerPrice, Product, Quote, QuoteEvent, QuoteLine, QuoteMessage, TaxRate, utcnow
from .notify import Notifier
from .pdf import render_quote_pdf
from .pricing import ZERO, calculate_totals, contract_unit_price, line_amount, money, tier_unit_price
from .schemas import (
    ApproveRequest,
    ClaimResponse,
    ExtractedItem,
    ExtractedQuoteRequest,
    InboundEmail,
    QuoteChange,
    QuoteUpdate,
    RejectRequest,
)
from .shipping import ShippingRequest, ShippingService

log = logging.getLogger(__name__)

AMBIGUITY_MARGIN = 0.05
FREE_MAIL_DOMAINS = frozenset(
    {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "icloud.com", "aol.com", "proton.me", "protonmail.com"}
)
SYSTEM = "system"
NEW_CUSTOMER_NOTE = "New customer: verify tax region, payment terms and credit before approving."
MISSING_CUSTOMER_NOTE = (
    "Forwarded by {sender}, but the original customer's email address could not be found. Set the customer before approving."
)
_EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_QUOTE_NUMBER = re.compile(r"\bQ-\d{4}-\d{5}(?:-R\d+)?\b")


class QuoteService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        extractor: Extractor,
        inventory: InventoryService,
        mailer: Mailer,
        shipping: ShippingService,
        notifier: Notifier | None = None,
    ):
        self.session = session
        self.settings = settings
        self.extractor = extractor
        self.inventory = inventory
        self.mailer = mailer
        self.shipping = shipping
        self.notifier = notifier

    # ------------------------------------------------------------------ queries

    def get(self, quote_id: int) -> Quote:
        quote = self.session.get(Quote, quote_id)
        if quote is None:
            raise NotFound(f"Quote {quote_id} not found")
        return quote

    def list(self, status: str | None = None, limit: int = 200) -> list[Quote]:
        query = select(Quote).order_by(Quote.created_at.desc(), Quote.id.desc()).limit(limit)
        if status:
            query = query.where(Quote.status == status)
        return list(self.session.scalars(query))

    def pdf_bytes(self, quote: Quote) -> bytes:
        if quote.pdf_path and Path(quote.pdf_path).is_file():
            return Path(quote.pdf_path).read_bytes()
        return render_quote_pdf(quote, self.settings)

    # ------------------------------------------------------------------ ingestion

    def ingest_email(self, email: InboundEmail) -> Quote:
        existing = self._by_message_id(email.message_id)
        if existing is not None:
            log.info("Email %s already processed as %s", email.message_id, existing.number)
            return existing
        known_reply = self.session.scalar(select(QuoteMessage).where(QuoteMessage.message_id == email.message_id))
        if known_reply is not None:
            return known_reply.quote
        thread_quote = self._find_thread_quote(email)
        if thread_quote is not None and self._may_follow_up(email, thread_quote):
            return self._ingest_follow_up(email, thread_quote)

        content = read_email(email, self.settings.max_attachment_mb * 1024 * 1024)
        request = self.extractor.extract(email, content)
        quote = Quote(
            status="received",
            source_message_id=email.message_id,
            source_thread_id=email.thread_id,
            source_provider_id=email.provider_message_id,
            source_from=email.from_email.strip().lower(),
            source_subject=email.subject[:998],
            source_body=content.body,
            attachments=content.report,
            extraction=request.model_dump(mode="json"),
            currency=self.settings.currency,
            subtotal=ZERO,
            discount_total=ZERO,
            shipping_total=ZERO,
            shipping_manual=False,
            tax_rate=Decimal("0"),
            tax_total=ZERO,
            total=ZERO,
            valid_until=date.today() + timedelta(days=self.settings.quote_validity_days),
        )
        self.session.add(quote)
        self._log(
            quote,
            SYSTEM,
            "received",
            {"from": quote.source_from, "items_extracted": len(request.items), "attachments": len(content.report)},
        )
        try:
            self.session.flush()
        except IntegrityError:  # the same message raced in through another worker
            self.session.rollback()
            existing = self._by_message_id(email.message_id)
            if existing is not None:
                return existing
            raise
        quote.number = f"Q-{quote.created_at:%Y}-{quote.id:05d}"

        unread = self._unread_attachments(content)
        # Without AI file reading, an unreadable attachment may be the whole request: let a person decide.
        maybe_request = request.is_quote_request or bool(unread and not self._reads_files)
        if not maybe_request or (not request.items and not unread):
            quote.status = "ignored"
            self._log(quote, SYSTEM, "ignored", {"reason": "not a quote request" if not maybe_request else "no products found"})
            self.session.commit()
            return quote

        customer, forwarded_by = self._resolve_identity(email, request, content)
        quote.customer = customer
        quote.forwarded_by = forwarded_by
        notes: list[str] = []
        if forwarded_by:
            notes.append(f"Forwarded by {forwarded_by}." if customer else MISSING_CUSTOMER_NOTE.format(sender=forwarded_by))
        if customer is not None and not customer.is_verified:
            notes.append(NEW_CUSTOMER_NOTE)
        if unread:
            notes.append(f"Could not read attachment(s): {', '.join(unread)}. Check them manually.")
        if not request.items:
            notes.append("No products could be read from this email. Add lines manually or reject it.")
        quote.internal_notes = " ".join(notes) or None
        quote.shipping_address = request.shipping_address
        quote.requested_delivery = request.requested_delivery
        quote.customer_notes = request.notes
        quote.tax_rate = self._tax_rate(customer)

        matcher = ProductMatcher(self.session.scalars(select(Product).where(Product.active.is_(True))))
        for position, item in enumerate(request.items, start=1):
            quote.lines.append(self._build_line(position, item, matcher, customer, source=content))
        self._recalculate(quote)
        quote.status = "pending_approval"
        self._log(
            quote,
            SYSTEM,
            "priced",
            {
                "lines": len(quote.lines),
                "needs_review": quote.needs_review_count,
                "shipping": quote.shipping_source,
                "total": str(quote.total),
            },
        )
        pdf = self._render_pdf(quote)
        self.session.commit()

        if self.notifier is not None:
            self.notifier.quote_ready(quote, pdf)
        return quote

    def _by_message_id(self, message_id: str) -> Quote | None:
        return self.session.scalar(select(Quote).where(Quote.source_message_id == message_id))

    # ------------------------------------------------------------------ follow-ups
    #
    # A reply in an existing conversation (In-Reply-To / References / thread id, or the quote number in the
    # subject) from the customer or a colleague is a follow-up, not a new request. The AI only describes the
    # requested changes; products are matched against the catalogue and priced from the database as usual,
    # and every changed line is flagged so a salesperson confirms it before anything is sent.

    def _find_thread_quote(self, email: InboundEmail) -> Quote | None:
        referenced = [value for value in [email.in_reply_to, *email.references] if value]
        quote = None
        if referenced:
            quote = self.session.scalar(select(Quote).where(Quote.source_message_id.in_(referenced)).order_by(Quote.id))
            if quote is None:
                message = self.session.scalar(select(QuoteMessage).where(QuoteMessage.message_id.in_(referenced)))
                quote = message.quote if message is not None else None
        if quote is None and email.thread_id:
            quote = self.session.scalar(select(Quote).where(Quote.source_thread_id == email.thread_id).order_by(Quote.id))
        if quote is None and (number := _QUOTE_NUMBER.search(email.subject or "")):
            quote = self.session.scalar(select(Quote).where(Quote.number == number.group(0)))
        if quote is None or quote.status in ("received", "ignored"):
            return None
        return quote.latest_revision or quote

    def _may_follow_up(self, email: InboundEmail, quote: Quote) -> bool:
        """Only the customer, their colleagues (same company domain) or our own staff can change a quote by replying."""
        sender = email.from_email.strip().lower()
        domain = sender.rpartition("@")[2]
        known = {quote.source_from, quote.forwarded_by, quote.customer.email if quote.customer is not None else None}
        if sender in known or domain in self.settings.internal_domains:
            return True
        customer_domain = quote.customer.email_domain if quote.customer is not None else None
        return bool(customer_domain) and domain == customer_domain and domain not in FREE_MAIL_DOMAINS

    def _ingest_follow_up(self, email: InboundEmail, quote: Quote) -> Quote:
        content = read_email(email, self.settings.max_attachment_mb * 1024 * 1024)
        request = self.extractor.extract_changes(email, content, self._lines_for_ai(quote))
        wants_changes = request.is_change_request and bool(request.changes)
        target, outcome = quote, "no_changes"
        if wants_changes:
            if quote.status == "pending_approval":
                outcome = "updated"
            else:  # already approved, sent or rejected: keep it as it was and work on a new revision
                target, outcome = self._create_revision(quote, email), "revision_created"

        message = QuoteMessage(
            message_id=email.message_id,
            received_at=utcnow(),
            from_email=email.from_email.strip().lower(),
            from_name=email.from_name,
            subject=email.subject[:998],
            body=content.body,
            attachments=content.report,
            summary=request.summary,
            outcome=outcome,
        )
        target.messages.append(message)
        notes = []
        if unread := self._unread_attachments(content):
            notes.append(f"Could not read attachment(s) in the customer's reply: {', '.join(unread)}.")
        pdf = None
        if wants_changes:
            applied, problems = self._apply_changes(target, request.changes, content)
            message.changes = applied
            notes += problems
            self._recalculate(target)
            self._log(target, SYSTEM, "follow_up_applied", {"from": message.from_email, "summary": request.summary, "changes": applied})
            pdf = self._render_pdf(target)
        else:
            notes.append(f"Customer replied: {request.summary}")
            self._log(target, SYSTEM, "customer_replied", {"from": message.from_email, "summary": request.summary})
        target.internal_notes = " ".join(note for note in [target.internal_notes, *notes] if note) or None
        try:
            self.session.commit()
        except IntegrityError:  # the same reply raced in through another worker
            self.session.rollback()
            known = self.session.scalar(select(QuoteMessage).where(QuoteMessage.message_id == email.message_id))
            if known is not None:
                return known.quote
            raise
        if pdf is not None and self.notifier is not None:
            self.notifier.quote_ready(target, pdf)
        return target

    @staticmethod
    def _lines_for_ai(quote: Quote) -> list[str]:
        return [
            f"{line.position}. {line.sku or 'no SKU'}: {line.description} | quantity {line.quantity.normalize():f} {line.unit or ''}".rstrip()
            for line in quote.lines
        ]

    def _create_revision(self, quote: Quote, email: InboundEmail) -> Quote:
        root = quote
        while root.revision_of is not None:
            root = root.revision_of
        number = (quote.revision or 0) + 1
        revision = Quote(
            number=f"{root.number}-R{number}",
            revision=number,
            revision_of=quote,
            status="pending_approval",
            customer=quote.customer,
            source_message_id=email.message_id,
            source_thread_id=email.thread_id or quote.source_thread_id,
            source_provider_id=email.provider_message_id or quote.source_provider_id,
            source_from=quote.source_from,
            source_subject=quote.source_subject,
            source_body=quote.source_body,
            forwarded_by=quote.forwarded_by,
            attachments=quote.attachments,
            extraction=quote.extraction,
            currency=quote.currency,
            subtotal=ZERO,
            discount_total=ZERO,
            shipping_total=quote.shipping_total,
            shipping_manual=quote.shipping_manual,
            shipping_service=quote.shipping_service,
            tax_rate=quote.tax_rate,
            tax_total=ZERO,
            total=ZERO,
            valid_until=date.today() + timedelta(days=self.settings.quote_validity_days),
            requested_delivery=quote.requested_delivery,
            shipping_address=quote.shipping_address,
            customer_notes=quote.customer_notes,
            internal_notes=f"Revision {number} of {quote.number}, requested by the customer.",
        )
        self.session.add(revision)
        for line in quote.lines:
            copy = QuoteLine(
                position=line.position, product=line.product, product_id=line.product_id, requested_text=line.requested_text,
                requested_sku=line.requested_sku, sku=line.sku, description=line.description, unit=line.unit,
                quantity=line.quantity, unit_price=line.unit_price, price_overridden=line.price_overridden,
                price_source=line.price_source, discount_pct=line.discount_pct, line_total=line.line_total,
                weight_kg=line.weight_kg, match_method=line.match_method, match_confidence=line.match_confidence,
                alternatives=list(line.alternatives or []), stock_status=line.stock_status, on_hand=line.on_hand,
                lead_time_days=line.lead_time_days, needs_review=False, review_reason=None,
            )
            if copy.product is not None:
                self._refresh_price_and_stock(copy, quote.customer, [])  # today's prices and stock
            revision.lines.append(copy)
        self._log(quote, SYSTEM, "revision_requested", {"revision": revision.number, "from": email.from_email})
        self._log(revision, SYSTEM, "received", {"from": email.from_email, "revision_of": quote.number})
        return revision

    @staticmethod
    def _quantity(value: float | None) -> Decimal | None:
        if value is None or value <= 0:
            return None
        return Decimal(str(value)).quantize(Decimal("0.001"))

    def _line_for_change(self, quote: Quote, change: QuoteChange) -> QuoteLine | None:
        if change.line_number:
            for line in quote.lines:
                if line.position == change.line_number:
                    return line
        if not change.existing_item:
            return None
        priced = [line for line in quote.lines if line.product is not None]
        if priced:
            candidates = ProductMatcher({line.product.sku: line.product for line in priced}.values()).match(change.existing_item)
            if candidates and (candidates[0].method == "sku" or candidates[0].score >= 0.6):
                return next(line for line in priced if line.sku == candidates[0].product.sku)
        wanted = normalize(change.existing_item)
        return next((line for line in quote.lines if line.product is None and wanted and wanted in normalize(line.requested_text)), None)

    def _apply_changes(
        self, quote: Quote, changes: list[QuoteChange], source: EmailContent | None = None
    ) -> tuple[list[dict], list[str]]:
        matcher = ProductMatcher(self.session.scalars(select(Product).where(Product.active.is_(True))))
        applied: list[dict] = []
        problems: list[str] = []
        for index, change in enumerate(changes):
            line = self._line_for_change(quote, change)
            reference = change.existing_item or (f"line {change.line_number}" if change.line_number else "an item")
            if change.action in ("remove", "change_quantity") and line is None:
                problems.append(f"Customer asked to {change.action.replace('_', ' ')} '{reference}', which isn't on the quote: check their reply.")
                applied.append({"action": change.action, "result": "not_found", "item": reference})
                continue

            if change.action == "remove":
                quote.lines.remove(line)
                applied.append({"action": "removed", "sku": line.sku, "item": line.description})
            elif change.action == "change_quantity":
                quantity = self._quantity(change.quantity)
                if quantity is None:
                    problems.append(f"Customer asked to change the quantity of '{reference}' but gave no new quantity: check their reply.")
                    applied.append({"action": change.action, "result": "no_quantity", "item": line.description})
                    continue
                previous = line.quantity
                line.quantity = quantity
                reasons = [f"Customer changed quantity from {previous.normalize():f} to {quantity.normalize():f}"]
                if previous > 0 and (quantity >= previous * 10 or quantity * 10 <= previous):
                    reasons.append("Big change (10× or more): check it isn't a typo")
                if source is not None and not guardrails.quantity_in_text(change.quantity, source.full_text):
                    reasons.append(f"Couldn't find {quantity.normalize():f} in the customer's reply: check the quantity")
                if line.product is not None:
                    self._refresh_price_and_stock(line, quote.customer, reasons)
                    reasons += guardrails.quantity_problems(quantity, line.product, self._usual_quantity(quote.customer, line.product), self.settings)
                self._set_review(line, reasons)
                applied.append({"action": "quantity_changed", "sku": line.sku, "item": line.description,
                                "from": f"{previous.normalize():f}", "to": f"{quantity.normalize():f}"})
            else:  # add or replace
                wanted = change.new_product or change.new_sku
                if not wanted:
                    problems.append(f"Customer asked to {change.action} '{reference}' but the new product wasn't clear: check their reply.")
                    applied.append({"action": change.action, "result": "unclear", "item": reference})
                    continue
                quantity = self._quantity(change.quantity) or (line.quantity if line is not None else None)
                item = ExtractedItem(product_description=wanted, sku=change.new_sku, quantity=float(quantity) if quantity else 0,
                                     unit=None, notes=change.note)
                new_line = self._build_line(1000 + index, item, matcher, quote.customer, source=source)
                replacing = change.action == "replace" and line is not None
                first_reason = f"Customer asked for this instead of {line.description}" if replacing else "Added from the customer's follow-up"
                self._set_review(new_line, [first_reason, *([new_line.review_reason] if new_line.review_reason else [])])
                if replacing:
                    position = quote.lines.index(line)
                    quote.lines.remove(line)
                    quote.lines.insert(position, new_line)
                    applied.append({"action": "replaced", "from_sku": line.sku, "from_item": line.description, "sku": new_line.sku,
                                    "item": new_line.description, "quantity": f"{new_line.quantity.normalize():f}"})
                else:
                    if change.action == "replace":
                        problems.append(f"Customer asked to replace '{reference}', which isn't on the quote, so the new product was added instead.")
                    quote.lines.append(new_line)
                    applied.append({"action": "added", "sku": new_line.sku, "item": new_line.description,
                                    "quantity": f"{new_line.quantity.normalize():f}"})
        return applied, problems

    @property
    def _reads_files(self) -> bool:
        return bool(getattr(self.extractor, "reads_files", False))

    def _unread_attachments(self, content: EmailContent) -> list[str]:
        return [
            entry["filename"]
            for entry in content.report
            if entry["method"] in ("unsupported", "error") or (entry["method"] == "ai" and not self._reads_files)
        ]

    def _resolve_identity(
        self, email: InboundEmail, request: ExtractedQuoteRequest, content: EmailContent
    ) -> tuple[Customer | None, str | None]:
        """Who is the quote for? The sender, unless a colleague forwarded the customer's email."""
        sender = email.from_email.strip().lower()
        internal = self.settings.internal_domains
        if sender.rpartition("@")[2] not in internal:
            return self._find_or_create_customer(sender, request.customer_name or email.from_name, request.company), None

        address, name = (request.customer_email or "").strip().lower(), request.customer_name
        if not _EMAIL.fullmatch(address) or address.rpartition("@")[2] in internal:
            found = find_forwarded_sender(content.full_text, internal)
            if found is None:
                return None, sender
            name, address = found
        return self._find_or_create_customer(address, name, request.company), sender

    def _find_or_create_customer(self, address: str, name: str | None, company: str | None) -> Customer:
        address = address.strip().lower()
        customer = self.session.scalar(select(Customer).where(Customer.email == address))
        if customer is not None:
            return customer

        domain = address.rpartition("@")[2]
        account = None
        if domain not in FREE_MAIL_DOMAINS:
            account = self.session.scalar(
                select(Customer).where(Customer.email_domain == domain, Customer.is_verified.is_(True)).order_by(Customer.id)
            )
        customer = Customer(
            name=name or address,
            email=address,
            email_domain=domain,
            company=company or (account.company if account else None),
            tax_region=account.tax_region if account else None,
            discount_pct=account.discount_pct if account else Decimal("0"),
            payment_terms=account.payment_terms if account else "Prepayment",
            billing_address=account.billing_address if account else None,
            is_verified=False,
        )
        self.session.add(customer)
        self.session.flush()
        return customer

    def _tax_rate(self, customer: Customer | None) -> Decimal:
        if customer is not None and customer.tax_region:
            rate = self.session.get(TaxRate, customer.tax_region)
            if rate is not None:
                return rate.rate
        return self.settings.default_tax_rate

    # ------------------------------------------------------------------ lines

    def _build_line(
        self,
        position: int,
        item: ExtractedItem,
        matcher: ProductMatcher,
        customer: Customer | None,
        source: EmailContent | None = None,
    ) -> QuoteLine:
        reasons: list[str] = []
        if item.quantity > 0:
            quantity = Decimal(str(item.quantity)).quantize(Decimal("0.001"))
        else:
            quantity = Decimal("1")
            reasons.append("Quantity not stated, defaulted to 1")

        line = QuoteLine(
            position=position,
            requested_text=item.product_description,
            requested_sku=item.sku,
            description=item.product_description,
            unit=item.unit,
            quantity=quantity,
            unit_price=ZERO,
            price_overridden=False,
            discount_pct=Decimal("0"),
            line_total=ZERO,
            weight_kg=Decimal("0"),
            needs_review=False,
        )
        candidates = matcher.match(item.product_description, item.sku)
        line.alternatives = [c.as_dict() for c in candidates]
        product, line.match_confidence, line.match_method = self._pick_product(item, candidates, reasons)
        if product is not None:
            self._assign_product(line, product)
            self._refresh_price_and_stock(line, customer, reasons)
        reasons += self._guardrail_reasons(item, line, product, matcher, customer, source)
        line.discount_pct = self._default_discount(line, customer)
        if item.notes:
            reasons.append(f"Customer note: {item.notes}")
        self._set_review(line, reasons)
        return line

    def _pick_product(
        self, item: ExtractedItem, candidates: list[MatchCandidate], reasons: list[str]
    ) -> tuple[Product | None, float, str | None]:
        if not candidates:
            reasons.append("No catalog match")
            return None, 0.0, None
        top = candidates[0]
        if top.method == "sku":
            return top.product, 1.0, "sku"
        if item.sku:
            reasons.append(f"SKU {item.sku} not found in catalog")

        runner_up = candidates[1].score if len(candidates) > 1 else 0.0
        confident = top.score >= self.settings.match_confidence_threshold
        if confident and top.score - runner_up >= AMBIGUITY_MARGIN:
            return top.product, top.score, "fuzzy"

        chosen_sku = self.extractor.choose_product(item.product_description, [c.as_dict() for c in candidates])
        if chosen_sku:
            chosen = next(c for c in candidates if c.product.sku == chosen_sku)
            reasons.append("Several similar products; AI picked the closest, please confirm")
            return chosen.product, chosen.score, "ai"

        if top.score >= self.settings.min_match_score:
            close = [c.product.sku for c in candidates[1:] if top.score - c.score < 0.15]
            if confident:
                reasons.append("Ambiguous match, also similar: " + ", ".join(close))
            else:
                reasons.append(f"Low-confidence match ({top.score:.0%})" + (f"; alternatives: {', '.join(close)}" if close else ""))
            return top.product, top.score, "fuzzy"

        reasons.append("No confident catalog match")
        return None, top.score, None

    def _guardrail_reasons(
        self,
        item: ExtractedItem,
        line: QuoteLine,
        product: Product | None,
        matcher: ProductMatcher,
        customer: Customer | None,
        source: EmailContent | None,
    ) -> list[str]:
        """Rule-based checks on a new line (see guardrails.py). They only add review reasons."""
        reasons = []
        if source is not None and (problem := guardrails.grounding_problem(item, source)):
            reasons.append(problem)
        if product is not None:
            if line.match_method == "sku" and (problem := guardrails.sku_description_conflict(item, product, matcher)):
                reasons.append(problem)
            if problem := guardrails.unit_problem(item, product):
                reasons.append(problem)
            reasons += guardrails.quantity_problems(line.quantity, product, self._usual_quantity(customer, product), self.settings)
        return reasons

    def _usual_quantity(self, customer: Customer | None, product: Product) -> Decimal | None:
        """Median quantity of this product on the customer's approved or sent quotes."""
        if customer is None or customer.id is None or product.id is None:
            return None
        quantities = list(
            self.session.scalars(
                select(QuoteLine.quantity)
                .join(Quote, QuoteLine.quote_id == Quote.id)
                .where(
                    Quote.customer_id == customer.id,
                    QuoteLine.product_id == product.id,
                    Quote.status.in_(("approved", "sending", "sent")),
                )
                .order_by(QuoteLine.id.desc())
                .limit(20)
            )
        )
        return Decimal(median(quantities)) if quantities else None

    def _assign_product(self, line: QuoteLine, product: Product) -> None:
        line.product = product
        line.product_id = product.id
        line.sku = product.sku
        line.description = product.name
        line.unit = product.unit
        line.weight_kg = product.weight_kg or Decimal("0")

    def _refresh_price_and_stock(self, line: QuoteLine, customer: Customer | None, reasons: list[str]) -> None:
        product = line.product
        if line.price_overridden:
            line.price_source = "manual"
        else:
            line.unit_price, line.price_source = self._unit_price(product, line.quantity, customer)
        availability = self.inventory.check(product, line.quantity)
        line.stock_status = availability.status
        line.on_hand = availability.on_hand
        line.lead_time_days = availability.lead_time_days
        if availability.status == "unknown":
            reasons.append("Stock / lead time unavailable, confirm manually")

    def _unit_price(self, product: Product, quantity: Decimal, customer: Customer | None) -> tuple[Decimal, str]:
        if customer is not None:
            special = contract_unit_price(self._customer_prices(customer, product.id), quantity, date.today())
            if special is not None:
                return special, "customer_price"
        return tier_unit_price(product, quantity), "list_price"

    def _customer_prices(self, customer: Customer, product_id: int) -> list[CustomerPrice]:
        """Prices agreed with this customer, or with verified colleagues at the same company domain."""
        query = (
            select(CustomerPrice)
            .join(Customer, CustomerPrice.customer_id == Customer.id)
            .where(CustomerPrice.product_id == product_id)
        )
        own = CustomerPrice.customer_id == customer.id
        if customer.email_domain and customer.email_domain not in FREE_MAIL_DOMAINS:
            query = query.where(or_(own, and_(Customer.email_domain == customer.email_domain, Customer.is_verified.is_(True))))
        else:
            query = query.where(own)
        return list(self.session.scalars(query).unique())

    @staticmethod
    def _default_discount(line: QuoteLine, customer: Customer | None) -> Decimal:
        # Contract prices are already net: the customer's general discount is not added on top.
        if customer is None or line.price_source == "customer_price":
            return Decimal("0")
        return customer.discount_pct or Decimal("0")

    @staticmethod
    def _set_review(line: QuoteLine, reasons: list[str]) -> None:
        line.needs_review = bool(reasons)
        line.review_reason = "; ".join(reasons) or None

    def _recalculate(self, quote: Quote) -> None:
        for position, line in enumerate(quote.lines, start=1):
            line.position = position
            line.line_total = line_amount(line.quantity, line.unit_price, line.discount_pct)

        if quote.shipping_manual:
            quote.shipping_source = "manual"
            quote.shipping_transit_days = None
        else:
            net = sum((line.line_total for line in quote.lines), ZERO)
            estimate = self.shipping.quote(
                ShippingRequest(
                    weight_kg=sum((line.quantity * (line.weight_kg or 0) for line in quote.lines), Decimal(0)),
                    order_value=net,
                    currency=quote.currency,
                    destination=quote.shipping_address,
                    items=[
                        {"sku": line.sku, "quantity": str(line.quantity), "weight_kg": str(line.weight_kg or 0)}
                        for line in quote.lines
                        if line.sku
                    ],
                )
            )
            quote.shipping_total = estimate.amount
            quote.shipping_service = estimate.service
            quote.shipping_source = estimate.source
            quote.shipping_transit_days = estimate.transit_days

        totals = calculate_totals(quote.lines, tax_rate=quote.tax_rate, shipping_total=quote.shipping_total)
        quote.subtotal = totals.subtotal
        quote.discount_total = totals.discount_total
        quote.shipping_total = totals.shipping_total
        quote.tax_total = totals.tax_total
        quote.total = totals.total

    def _product_by_sku(self, sku: str) -> Product:
        product = self.session.scalar(select(Product).where(Product.sku == sku.strip(), Product.active.is_(True)))
        if product is None:
            raise Conflict(f"Unknown or inactive SKU: {sku}")
        return product

    # ------------------------------------------------------------------ salesperson actions

    def update(self, quote_id: int, update: QuoteUpdate) -> Quote:
        quote = self.get(quote_id)
        self._require_status(quote, {"pending_approval"}, "edit")
        changes: list[dict] = []
        if update.customer_email:
            self._change_customer(quote, update.customer_email, changes)
        by_id = {line.id: line for line in quote.lines}

        for edit in update.lines:
            if edit.id is None:
                if not edit.product_sku:
                    raise Conflict("A new line needs product_sku")
                product = self._product_by_sku(edit.product_sku)
                line = QuoteLine(
                    position=len(quote.lines) + 1,
                    requested_text="(added by salesperson)",
                    quantity=edit.quantity or Decimal("1"),
                    unit_price=money(edit.unit_price) if edit.unit_price is not None else ZERO,
                    price_overridden=edit.unit_price is not None,
                    discount_pct=Decimal("0"),
                    line_total=ZERO,
                    weight_kg=Decimal("0"),
                    match_method="manual",
                    match_confidence=1.0,
                    alternatives=[],
                )
                reasons: list[str] = []
                self._assign_product(line, product)
                self._refresh_price_and_stock(line, quote.customer, reasons)
                line.discount_pct = edit.discount_pct if edit.discount_pct is not None else self._default_discount(line, quote.customer)
                self._set_review(line, reasons)
                quote.lines.append(line)
                changes.append({"action": "added", "sku": product.sku, "quantity": str(line.quantity)})
                continue

            line = by_id.get(edit.id)
            if line is None:
                raise Conflict(f"Line {edit.id} is not on quote {quote.number}")
            if edit.remove:
                quote.lines.remove(line)
                changes.append({"action": "removed", "line": edit.id, "sku": line.sku})
                continue

            diff: dict = {}
            reasons = []
            product_changed = bool(edit.product_sku) and edit.product_sku != line.sku
            quantity_changed = edit.quantity is not None and edit.quantity != line.quantity
            if quantity_changed:
                diff["quantity"] = [str(line.quantity), str(edit.quantity)]
                line.quantity = edit.quantity
            if product_changed:
                product = self._product_by_sku(edit.product_sku)
                diff["sku"] = [line.sku, product.sku]
                self._assign_product(line, product)
                line.match_method, line.match_confidence = "manual", 1.0
            if edit.unit_price is not None and money(edit.unit_price) != line.unit_price:
                diff["unit_price"] = [str(line.unit_price), str(money(edit.unit_price))]
                line.unit_price = money(edit.unit_price)
                line.price_overridden = True
                line.price_source = "manual"
            if line.product is not None and (product_changed or quantity_changed):
                self._refresh_price_and_stock(line, quote.customer, reasons)
            if edit.discount_pct is not None and edit.discount_pct != line.discount_pct:
                diff["discount_pct"] = [str(line.discount_pct), str(edit.discount_pct)]
                line.discount_pct = edit.discount_pct
            if edit.description is not None and edit.description != line.description:
                diff["description"] = [line.description, edit.description]
                line.description = edit.description
            if line.product is None:
                reasons.append("No catalog product selected")
            # Any explicit edit (even an empty one) means the salesperson has looked at this line.
            self._set_review(line, reasons)
            changes.append({"action": "updated" if diff else "reviewed", "line": line.id, **diff})

        if update.reset_shipping:
            quote.shipping_manual = False
            changes.append({"action": "shipping_reset"})
        elif update.shipping_total is not None:
            quote.shipping_manual = True
            quote.shipping_service = "Manual"
            quote.shipping_total = money(update.shipping_total)
            changes.append({"action": "shipping_set", "value": str(quote.shipping_total)})
        if update.internal_notes is not None:
            quote.internal_notes = update.internal_notes

        self._recalculate(quote)
        self._log(quote, update.actor, "edited", {"changes": changes, "total": str(quote.total)})
        self._render_pdf(quote)
        self.session.commit()
        return quote

    def _change_customer(self, quote: Quote, address: str, changes: list[dict]) -> None:
        address = address.strip().lower()
        if not _EMAIL.fullmatch(address):
            raise Conflict("Enter a valid customer email address")
        if address.rpartition("@")[2] in self.settings.internal_domains:
            raise Conflict("That address belongs to your own company; enter the customer's address")
        previous = quote.customer.email if quote.customer else None
        if previous == address:
            return

        customer = self._find_or_create_customer(address, None, None)
        quote.customer = customer
        quote.tax_rate = self._tax_rate(customer)
        for line in quote.lines:
            if line.product is None:
                continue
            self._refresh_price_and_stock(line, customer, [])
            if not line.price_overridden:
                line.discount_pct = self._default_discount(line, customer)

        notes = quote.internal_notes or ""
        if quote.forwarded_by:
            notes = notes.replace(MISSING_CUSTOMER_NOTE.format(sender=quote.forwarded_by), f"Forwarded by {quote.forwarded_by}.")
        if not customer.is_verified and NEW_CUSTOMER_NOTE not in notes:
            notes = f"{notes} {NEW_CUSTOMER_NOTE}"
        quote.internal_notes = notes.strip() or None
        changes.append({"action": "customer_changed", "from": previous, "to": address})

    def approve(self, quote_id: int, request: ApproveRequest) -> Quote:
        quote = self.get(quote_id)
        self._require_status(quote, {"pending_approval"}, "approve")
        if quote.customer is None:
            raise Conflict("Set the customer's email address before approving")
        if not quote.lines:
            raise Conflict("Quote has no lines")
        unmatched = [line.position for line in quote.lines if line.product is None]
        if unmatched:
            raise Conflict("Some lines have no catalog product; fix or remove them first", unmatched)
        flagged = [line.position for line in quote.lines if line.needs_review]
        if flagged and not request.confirm_flagged_lines:
            raise Conflict("Some lines are flagged for review; edit them or confirm_flagged_lines", flagged)

        quote.status = "approved"
        quote.approved_by = request.approver
        quote.approved_at = utcnow()
        quote.cover_message = request.cover_message or None
        for line in quote.lines:
            line.needs_review = False
        self._log(quote, request.approver, "approved", {"confirmed_flagged_lines": flagged, "total": str(quote.total)})
        self._render_pdf(quote)  # re-render without the DRAFT marker
        self.session.commit()

        if request.send:
            return self.send(quote.id, request.approver)
        return quote

    def send(self, quote_id: int, actor: str, cover_message: str | None = None) -> Quote:
        quote = self.get(quote_id)
        self._require_status(quote, {"approved", "sending"}, "send")
        self._guard_sample_customer(quote)
        if cover_message:
            quote.cover_message = cover_message
        if self.settings.send_mode == "n8n":
            return self._hand_to_n8n(quote, actor)

        self._claim(quote)
        try:
            provider_id = self.mailer.send(self.outgoing_email(quote))
        except Exception as exc:
            self._release_claim(quote, actor, str(exc))
            raise
        return self._complete_send(quote, actor, provider_id, via="app")

    def reject(self, quote_id: int, request: RejectRequest) -> Quote:
        quote = self.get(quote_id)
        self._require_status(quote, {"pending_approval"}, "reject")
        quote.status = "rejected"
        quote.rejection_reason = request.reason
        self._log(quote, request.approver, "rejected", {"reason": request.reason})
        self.session.commit()
        return quote

    # ------------------------------------------------------------------ sending (app or n8n)
    #
    # Both paths go through a claim: approved -> sending is a single conditional UPDATE, so two clicks,
    # two workers or two n8n runs can never email the same quote twice. A claim that is never completed
    # (a crashed workflow) expires after SEND_CLAIM_TIMEOUT_MINUTES.

    def _hand_to_n8n(self, quote: Quote, actor: str) -> Quote:
        if self.notifier is None:
            raise Conflict("n8n sending is not configured")
        try:
            self.notifier.quote_approved(quote)
        except Exception as exc:
            self._log(quote, actor, "handoff_failed", {"error": str(exc)})
            self.session.commit()
            raise
        self._log(quote, actor, "handed_to_n8n", None)
        self.session.commit()
        return quote

    def claim_for_n8n(self, quote_id: int) -> ClaimResponse:
        quote = self.get(quote_id)
        if quote.customer is None:
            raise Conflict(f"Quote {quote.number} has no customer")
        self._guard_sample_customer(quote)
        token = self._claim(quote)
        try:
            message = self.outgoing_email(quote)
        except Exception as exc:
            self._release_claim(quote, "n8n", str(exc))
            raise
        self._log(quote, "n8n", "send_claimed", None)
        self.session.commit()
        filename, data, mime_type = message.attachments[0]
        return ClaimResponse(
            claim_token=token,
            quote_id=quote.id,
            number=quote.number,
            to=message.to,
            cc=message.cc,
            subject=message.subject,
            body_text=message.body_text,
            reply_to_provider_message_id=None if quote.forwarded_by else quote.source_provider_id,
            attachment_filename=filename,
            attachment_mime_type=mime_type,
            attachment_base64=base64.b64encode(data).decode(),
        )

    def report_sent(self, quote_id: int, claim_token: str, provider_message_id: str | None) -> Quote:
        quote = self._claimed(quote_id, claim_token)
        return self._complete_send(quote, "n8n", provider_message_id, via="n8n")

    def report_failed(self, quote_id: int, claim_token: str, error: str) -> Quote:
        quote = self._claimed(quote_id, claim_token)
        self._release_claim(quote, "n8n", error)
        return quote

    def _claim(self, quote: Quote) -> str:
        token = secrets.token_urlsafe(24)
        now = utcnow()
        stale = now - timedelta(minutes=self.settings.send_claim_timeout_minutes)
        result = self.session.execute(
            sql_update(Quote)
            .where(
                Quote.id == quote.id,
                or_(Quote.status == "approved", and_(Quote.status == "sending", Quote.send_claimed_at < stale)),
            )
            .values(status="sending", send_claim_token=token, send_claimed_at=now)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self.session.rollback()
            raise Conflict(f"Quote {quote.number} is already being sent or has been sent")
        self.session.commit()
        self.session.refresh(quote)
        return token

    def _claimed(self, quote_id: int, claim_token: str) -> Quote:
        quote = self.get(quote_id)
        valid = (
            quote.status == "sending"
            and quote.send_claim_token is not None
            and hmac.compare_digest(quote.send_claim_token.encode(), claim_token.encode())
        )
        if not valid:
            raise Conflict(f"This send claim for quote {quote.number} is no longer valid")
        return quote

    def _release_claim(self, quote: Quote, actor: str, error: str) -> None:
        quote.status = "approved"
        quote.send_claim_token = None
        quote.send_claimed_at = None
        self._log(quote, actor, "send_failed", {"error": error})
        self.session.commit()

    def _complete_send(self, quote: Quote, actor: str, provider_id: str | None, via: str) -> Quote:
        quote.status = "sent"
        quote.sent_at = utcnow()
        quote.send_claim_token = None
        self._log(quote, actor, "sent", {"to": quote.customer.email, "provider_id": provider_id, "via": via})
        self.session.commit()
        return quote

    def outgoing_email(self, quote: Quote) -> OutgoingEmail:
        subject = re.sub(r"^((re|fw|fwd):\s*)+", "", quote.source_subject or "", flags=re.I).strip()
        forwarded = bool(quote.forwarded_by)
        if forwarded:  # a new email to the customer, copying the colleague who forwarded it
            subject_line = f"Quotation {quote.number}" + (f" - {subject}" if subject else "")
        else:  # a reply in the customer's own thread
            subject_line = f"Re: {subject}" if subject else f"Quotation {quote.number}"
        return OutgoingEmail(
            to=quote.customer.email,
            cc=[quote.forwarded_by] if forwarded else [],
            subject=subject_line,
            body_text=quote.cover_message or self._default_cover(quote),
            attachments=[(f"{quote.number}.pdf", self.pdf_bytes(quote), "application/pdf")],
            in_reply_to=None if forwarded else quote.source_message_id,
            thread_id=None if forwarded else quote.source_thread_id,
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _require_status(quote: Quote, allowed: set[str], action: str) -> None:
        if quote.status not in allowed:
            raise Conflict(f"Cannot {action} quote {quote.number} in status '{quote.status}'")

    @staticmethod
    def _log(quote: Quote, actor: str, event: str, detail: dict | None = None) -> None:
        quote.events.append(QuoteEvent(actor=actor, event=event, detail=detail, at=utcnow()))

    def _render_pdf(self, quote: Quote) -> bytes:
        pdf = render_quote_pdf(quote, self.settings)
        directory = Path(self.settings.quotes_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{quote.number}.pdf"
        path.write_bytes(pdf)
        quote.pdf_path = str(path)
        return pdf

    def refresh_pdf(self, quote: Quote) -> bytes:
        """Re-render and store the quote PDF, e.g. after correcting its dates."""
        return self._render_pdf(quote)

    def _guard_sample_customer(self, quote: Quote) -> None:
        """Sample customers have realistic addresses that may belong to real companies: only let their quotes
        go to the local outbox, never out through Gmail, Outlook or n8n (unless ALLOW_SAMPLE_SENDS=true)."""
        real_delivery = self.settings.send_mode == "n8n" or self.settings.mail_provider != "console"
        if real_delivery and quote.customer is not None and quote.customer.is_sample and not self.settings.allow_sample_sends:
            raise Conflict(
                f"{quote.customer.name} is a built-in sample customer, so this quote is not emailed. "
                "To test real sending, use a quote for your own email address."
            )

    def _default_cover(self, quote: Quote) -> str:
        first_name = quote.customer.name.split()[0] if quote.customer.name and "@" not in quote.customer.name else "there"
        body = [
            f"Hi {first_name},",
            "",
            (
                f"Thank you for your reply. Please find attached our revised quotation {quote.number}, "
                if quote.revision
                else f"Thank you for your enquiry. Please find attached quotation {quote.number}, "
            )
            + f"valid until {quote.valid_until:%d %B %Y}.",
            "",
            f"Total: {quote.currency} {quote.total:,.2f} (including shipping and tax).",
        ]
        not_in_stock = [line for line in quote.lines if line.stock_status in ("partial", "backorder")]
        if not_in_stock:
            body += ["", "Please note these items are not fully in stock:"]
            body += [f"  - {line.description}: approx. {line.lead_time_days} days" for line in not_in_stock]
        body += [
            "",
            "Reply to this email to confirm the order or if you'd like any changes.",
            "",
            "Best regards,",
            quote.approved_by or "",
            self.settings.company_name,
        ]
        return "\n".join(body)
