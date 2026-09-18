from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .config import Settings
from .models import Quote, QuoteLine, utcnow

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#6b7280")
RULE = colors.HexColor("#d9dde3")
ACCENT = colors.HexColor("#0f4c81")
SHADE = colors.HexColor("#f3f5f8")


def fmt_qty(quantity: Decimal) -> str:
    text = f"{Decimal(quantity):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def availability_label(line: QuoteLine) -> str:
    if line.stock_status == "in_stock":
        return f"In stock, {line.lead_time_days} d"
    if line.stock_status == "partial":
        return f"Partial stock, {line.lead_time_days} d"
    if line.stock_status == "backorder":
        return f"{line.lead_time_days} days"
    return "To be confirmed"


def _para(text: str | None, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text or "").replace("\n", "<br/>"), style)


def render_quote_pdf(quote: Quote, settings: Settings) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=20 * mm,
        title=f"Quotation {quote.number}",
        author=settings.company_name,
    )
    base = getSampleStyleSheet()["Normal"]
    body = ParagraphStyle("body", parent=base, fontName="Helvetica", fontSize=9, leading=12, textColor=INK)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10.5)
    muted = ParagraphStyle("muted", parent=small, textColor=MUTED)
    label = ParagraphStyle("label", parent=muted, fontName="Helvetica-Bold", fontSize=7.5)
    right = ParagraphStyle("right", parent=body, alignment=TA_RIGHT)
    title = ParagraphStyle("title", parent=right, fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=ACCENT)

    is_draft = quote.status not in ("approved", "sent")
    created = quote.created_at or utcnow()
    story: list = []

    company = Paragraph(
        f"<font size=13><b>{escape(settings.company_name)}</b></font><br/>"
        f"{escape(settings.company_address.replace(chr(92) + 'n', chr(10))).replace(chr(10), '<br/>')}<br/>"
        f"{escape(settings.company_email)}",
        body,
    )
    meta = Paragraph(
        f"Quote no. <b>{escape(quote.number or '')}</b><br/>"
        f"Date: {created:%d %b %Y}<br/>"
        f"Valid until: {quote.valid_until:%d %b %Y}" if quote.valid_until else "",
        right,
    )
    header = Table(
        [[company, [Paragraph("QUOTATION" + (" &nbsp;<font color='#b45309'>DRAFT</font>" if is_draft else ""), title), meta]]],
        colWidths=[95 * mm, 79 * mm],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [header, Spacer(1, 8 * mm)]

    customer = quote.customer
    bill_lines = [customer.name, customer.company, customer.email, customer.billing_address] if customer else []
    bill = "\n".join(x for x in bill_lines if x)
    ship = quote.shipping_address or "Same as billing address"
    if quote.requested_delivery:
        ship += f"\nRequested delivery: {quote.requested_delivery}"
    parties = Table(
        [[Paragraph("BILL TO", label), Paragraph("SHIP TO", label)], [_para(bill, body), _para(ship, body)]],
        colWidths=[87 * mm, 87 * mm],
    )
    parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, 0), 2)]))
    story += [parties, Spacer(1, 7 * mm)]

    rows = [["#", "SKU", "Description", "Qty", "Unit price", "Disc.", "Availability", "Amount"]]
    for line in quote.lines:
        rows.append(
            [
                str(line.position),
                _para(line.sku or "-", small),
                _para(line.description, small),
                _para(f"{fmt_qty(line.quantity)} {line.unit or ''}".strip(), small),
                f"{line.unit_price:,.2f}",
                f"{line.discount_pct:g}%" if line.discount_pct else "",
                _para(availability_label(line), small),
                f"{line.line_total:,.2f}",
            ]
        )
    items = Table(rows, colWidths=[7 * mm, 27 * mm, 52 * mm, 15 * mm, 19 * mm, 11 * mm, 21 * mm, 22 * mm], repeatRows=1)
    items.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
                ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
                ("TEXTCOLOR", (0, 1), (-1, -1), INK),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
                ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SHADE]),
                ("ALIGN", (4, 0), (5, -1), "RIGHT"),
                ("ALIGN", (7, 0), (7, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story += [items, Spacer(1, 4 * mm)]

    cur = quote.currency
    totals_rows = [["Subtotal", f"{cur} {quote.subtotal:,.2f}"]]
    if quote.discount_total:
        totals_rows.append(["Discount", f"- {cur} {quote.discount_total:,.2f}"])
    totals_rows.append(["Shipping", "Free" if not quote.shipping_total else f"{cur} {quote.shipping_total:,.2f}"])
    totals_rows.append([f"Tax ({quote.tax_rate * 100:.2f}%)", f"{cur} {quote.tax_total:,.2f}"])
    totals_rows.append(["Total", f"{cur} {quote.total:,.2f}"])
    totals = Table(totals_rows, colWidths=[40 * mm, 36 * mm], hAlign="RIGHT")
    totals.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
                ("TEXTCOLOR", (0, 0), (-1, -1), INK),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 11),
                ("LINEABOVE", (0, -1), (-1, -1), 0.8, INK),
                ("TOPPADDING", (0, -1), (-1, -1), 6),
            ]
        )
    )
    story += [totals, Spacer(1, 10 * mm)]

    terms = [
        f"Payment terms: {customer.payment_terms if customer else 'Prepayment'}.",
        f"Prices in {cur}. This quotation is valid until {quote.valid_until:%d %B %Y}." if quote.valid_until else f"Prices in {cur}.",
        "Lead times are estimated working days from order confirmation and subject to stock at time of order.",
        "To accept, reply to this email quoting the quotation number or send a purchase order.",
    ]
    story += [Paragraph("TERMS", label), Spacer(1, 1.5 * mm), _para("\n".join(terms), muted)]

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 10 * mm, f"{settings.company_name}  ·  Quotation {quote.number}")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
