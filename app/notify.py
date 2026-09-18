"""Tell people and systems about quotes: salesperson notifications, and handing approved quotes to n8n."""

from __future__ import annotations

import logging

import httpx

from .config import Settings
from .mail import Mailer, MailError, OutgoingEmail
from .models import Quote

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, settings: Settings, mailer: Mailer, http: httpx.Client | None = None):
        self.settings = settings
        self.mailer = mailer
        self.http = http or httpx.Client(timeout=10)

    def approval_url(self, quote: Quote) -> str:
        return f"{self.settings.public_base_url.rstrip('/')}/?quote={quote.id}"

    def quote_ready(self, quote: Quote, pdf: bytes) -> None:
        # Notification failures are logged, never raised: the quote is already safely stored.
        payload = {
            "event": "quote.pending_approval",
            "quote_id": quote.id,
            "number": quote.number,
            "customer": quote.customer_name,
            "total": str(quote.total),
            "currency": quote.currency,
            "needs_review_count": quote.needs_review_count,
            "approval_url": self.approval_url(quote),
        }
        if self.settings.n8n_quote_ready_webhook_url:
            try:
                self.http.post(self.settings.n8n_quote_ready_webhook_url, json=payload).raise_for_status()
            except httpx.HTTPError:
                log.exception("n8n notification failed for quote %s", quote.number)

        if self.settings.sales_notify_email:
            flagged = f"\n{quote.needs_review_count} line(s) need review." if quote.needs_review_count else ""
            try:
                self.mailer.send(
                    OutgoingEmail(
                        to=self.settings.sales_notify_email,
                        subject=f"[Approval needed] {quote.number} - {quote.customer_name or quote.source_from} - {quote.currency} {quote.total:,.2f}",
                        body_text=f"A draft quote is ready for approval.{flagged}\n\nReview: {self.approval_url(quote)}",
                        attachments=[(f"{quote.number}-DRAFT.pdf", pdf, "application/pdf")],
                    )
                )
            except MailError:
                log.exception("Sales notification email failed for quote %s", quote.number)

    def quote_approved(self, quote: Quote) -> None:
        """Hand an approved quote to n8n, which claims it, emails it and reports back. Raises MailError."""
        url = self.settings.n8n_quote_approved_webhook_url
        if not url:
            raise MailError("SEND_MODE is n8n but N8N_QUOTE_APPROVED_WEBHOOK_URL is not set")
        payload = {
            "event": "quote.approved",
            "quote_id": quote.id,
            "number": quote.number,
            "customer_email": quote.customer.email if quote.customer else None,
            "total": str(quote.total),
            "currency": quote.currency,
        }
        try:
            self.http.post(url, json=payload).raise_for_status()
        except httpx.HTTPError as exc:
            raise MailError(f"Could not hand quote {quote.number} to n8n: {exc}") from exc
