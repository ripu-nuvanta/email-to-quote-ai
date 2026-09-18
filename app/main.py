"""FastAPI app. Run with:  uvicorn app.main:create_app --factory --reload"""

from __future__ import annotations

import hmac
import logging
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import customers
from .config import Settings, get_settings
from .db import Base, make_engine, make_session_factory
from .errors import Conflict, NotFound
from .extraction import ExtractionError, Extractor, make_extractor
from .inventory import InventoryService, make_inventory
from .mail import Mailer, MailError, make_mailer
from .migrate import ensure_schema
from .models import Product
from .notify import Notifier
from .schemas import (
    AppConfig,
    ApproveRequest,
    ClaimResponse,
    CustomerListItem,
    CustomerPriceIn,
    CustomerPriceOut,
    CustomerUpdate,
    FailedReport,
    InboundEmail,
    IngestResult,
    ProductOut,
    QuoteOut,
    QuoteSummary,
    QuoteUpdate,
    RejectRequest,
    SendRequest,
    SentReport,
    SimulatedEmail,
)
from .seed import seed_demo_extras, seed_if_empty
from .service import QuoteService
from .shipping import ShippingService, make_shipping

log = logging.getLogger(__name__)

NOT_BUILT_PAGE = """<!doctype html><meta charset="utf-8"><title>Quote approvals</title>
<body style="font:15px/1.5 system-ui,sans-serif;max-width:640px;margin:48px auto;padding:0 16px;color:#1f2933">
<h1 style="font-size:22px">The approval app isn't built yet</h1>
<p>The API is running. To build the approval screen, open a second PowerShell window in the
<code>quote-automation</code> folder and run:</p>
<pre style="background:#f3f4f6;padding:12px;border-radius:6px">cd web
npm install
npm run build</pre>
<p>Then refresh this page. API documentation: <a href="/docs">/docs</a></p></body>"""


class WebApp(StaticFiles):
    """Serves the exported Next.js approval app, or a short help page until it has been built."""

    def _built(self) -> bool:
        return (Path(str(self.directory)) / "index.html").is_file()

    async def check_config(self) -> None:
        if self._built():  # the build folder may not exist yet; get_response shows the help page instead
            await super().check_config()

    async def get_response(self, path: str, scope):
        if not self._built():
            return HTMLResponse(NOT_BUILT_PAGE, status_code=503)
        directory = Path(str(self.directory))
        alternative = self._prefetch_file(path)
        if alternative and not (directory / path).is_file() and (directory / alternative).is_file():
            path = alternative
        return await super().get_response(path, scope)

    @staticmethod
    def _prefetch_file(path: str) -> str | None:
        """Next.js requests page prefetch data as '__next.<segment>.__PAGE__.txt' but exports it as
        '__next.<segment>/__PAGE__.txt'; map one to the other so background prefetching works."""
        folder, _, name = path.replace("\\", "/").rpartition("/")
        suffix = ".__PAGE__.txt"
        if not (name.startswith("__next.") and name.endswith(suffix) and len(name) > len("__next") + len(suffix)):
            return None
        candidate = f"{name[: -len(suffix)]}/__PAGE__.txt"
        return f"{folder}/{candidate}" if folder else candidate


def _matches(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided.encode(), expected.encode())


def create_app(
    settings: Settings | None = None,
    *,
    extractor: Extractor | None = None,
    inventory: InventoryService | None = None,
    mailer: Mailer | None = None,
    shipping: ShippingService | None = None,
    http: httpx.Client | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO)

    engine = make_engine(settings.database_url)
    added = ensure_schema(engine, Base.metadata)
    if added:
        log.info("Upgraded database, added columns: %s", ", ".join(added))
    SessionLocal = make_session_factory(engine)
    if settings.seed_demo_data:
        with SessionLocal() as session:
            seed_if_empty(session)
            seed_demo_extras(session)

    extractor = extractor or make_extractor(settings)
    inventory = inventory or make_inventory(settings)
    mailer = mailer or make_mailer(settings)
    shipping = shipping or make_shipping(settings)
    notifier = Notifier(settings, mailer, http)

    def get_session():
        with SessionLocal() as session:
            yield session

    def get_service(session: Session = Depends(get_session)):
        return QuoteService(session, settings, extractor, inventory, mailer, shipping, notifier)

    def require_webhook_secret(x_webhook_secret: str = Header(default="")):
        if not _matches(x_webhook_secret, settings.inbound_webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    def require_approver(authorization: str = Header(default="")):
        if not _matches(authorization.removeprefix("Bearer ").strip(), settings.approver_api_token):
            raise HTTPException(status_code=401, detail="Invalid approver token")

    app = FastAPI(title="Email to Quote Automation", version="0.2.0")
    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(NotFound)
    async def not_found(_: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(Conflict)
    async def conflict(_: Request, exc: Conflict):
        return JSONResponse(status_code=409, content={"detail": str(exc), "lines": exc.lines})

    @app.exception_handler(MailError)
    async def mail_failed(_: Request, exc: MailError):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(ExtractionError)
    async def extraction_failed(_: Request, exc: ExtractionError):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    def ingest_result(quote) -> IngestResult:
        return IngestResult(
            quote_id=quote.id,
            number=quote.number,
            status=quote.status,
            needs_review_count=quote.needs_review_count,
            approval_url=notifier.approval_url(quote),
        )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # Called by n8n (Gmail / Outlook intake workflows) for every new inbound email.
    @app.post("/api/inbound-email", response_model=IngestResult, dependencies=[Depends(require_webhook_secret)])
    def inbound_email(email: InboundEmail, svc: QuoteService = Depends(get_service)):
        return ingest_result(svc.ingest_email(email))

    # Called by the n8n "send approved quote" workflows when SEND_MODE=n8n.
    integrations = APIRouter(prefix="/api/integrations", dependencies=[Depends(require_webhook_secret)])

    @integrations.post("/quotes/{quote_id}/claim", response_model=ClaimResponse)
    def claim_quote(quote_id: int, svc: QuoteService = Depends(get_service)):
        return svc.claim_for_n8n(quote_id)

    @integrations.post("/quotes/{quote_id}/sent", response_model=QuoteSummary)
    def quote_sent(quote_id: int, report: SentReport, svc: QuoteService = Depends(get_service)):
        return QuoteSummary.model_validate(svc.report_sent(quote_id, report.claim_token, report.provider_message_id))

    @integrations.post("/quotes/{quote_id}/failed", response_model=QuoteSummary)
    def quote_send_failed(quote_id: int, report: FailedReport, svc: QuoteService = Depends(get_service)):
        return QuoteSummary.model_validate(svc.report_failed(quote_id, report.claim_token, report.error))

    # Used by the approval web app.
    api = APIRouter(prefix="/api", dependencies=[Depends(require_approver)])

    @api.get("/config", response_model=AppConfig)
    def app_config():
        return AppConfig(
            company_name=settings.company_name,
            currency=settings.currency,
            send_mode=settings.send_mode,
            ai_reading=bool(getattr(extractor, "reads_files", False)),
            internal_email_domains=sorted(settings.internal_domains),
            dev_tools=settings.enable_dev_endpoints,
        )

    # Responses are built inside the endpoint, while the database session is still open.
    @api.get("/quotes", response_model=list[QuoteSummary])
    def list_quotes(status: str | None = None, svc: QuoteService = Depends(get_service)):
        return [QuoteSummary.model_validate(quote) for quote in svc.list(status)]

    @api.get("/quotes/{quote_id}", response_model=QuoteOut)
    def get_quote(quote_id: int, svc: QuoteService = Depends(get_service)):
        return QuoteOut.model_validate(svc.get(quote_id))

    @api.patch("/quotes/{quote_id}", response_model=QuoteOut)
    def update_quote(quote_id: int, update: QuoteUpdate, svc: QuoteService = Depends(get_service)):
        return QuoteOut.model_validate(svc.update(quote_id, update))

    @api.get("/quotes/{quote_id}/pdf")
    def quote_pdf(quote_id: int, svc: QuoteService = Depends(get_service)):
        quote = svc.get(quote_id)
        return Response(
            svc.pdf_bytes(quote),
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{quote.number}.pdf"'},
        )

    @api.post("/quotes/{quote_id}/approve", response_model=QuoteOut)
    def approve_quote(quote_id: int, request: ApproveRequest, svc: QuoteService = Depends(get_service)):
        return QuoteOut.model_validate(svc.approve(quote_id, request))

    @api.post("/quotes/{quote_id}/send", response_model=QuoteOut)
    def send_quote(quote_id: int, request: SendRequest, svc: QuoteService = Depends(get_service)):
        return QuoteOut.model_validate(svc.send(quote_id, request.actor, request.cover_message))

    @api.post("/quotes/{quote_id}/reject", response_model=QuoteOut)
    def reject_quote(quote_id: int, request: RejectRequest, svc: QuoteService = Depends(get_service)):
        return QuoteOut.model_validate(svc.reject(quote_id, request))

    @api.get("/products", response_model=list[ProductOut])
    def search_products(q: str = "", session: Session = Depends(get_session)):
        query = select(Product).where(Product.active.is_(True)).order_by(Product.sku).limit(500)
        if q:
            like = f"%{q}%"
            query = query.where(or_(Product.sku.ilike(like), Product.name.ilike(like)))
        return [
            ProductOut(sku=p.sku, name=p.name, unit=p.unit, base_price=p.base_price, on_hand=p.inventory.on_hand if p.inventory else None)
            for p in session.scalars(query)
        ]

    @api.get("/customers", response_model=list[CustomerListItem])
    def list_customers(q: str = "", session: Session = Depends(get_session)):
        return customers.list_customers(session, q)

    @api.patch("/customers/{customer_id}", response_model=CustomerListItem)
    def update_customer(customer_id: int, data: CustomerUpdate, session: Session = Depends(get_session)):
        return customers.update_customer(session, customer_id, data)

    @api.get("/customers/{customer_id}/prices", response_model=list[CustomerPriceOut])
    def list_customer_prices(customer_id: int, session: Session = Depends(get_session)):
        return customers.list_prices(session, customer_id)

    @api.put("/customers/{customer_id}/prices", response_model=CustomerPriceOut)
    def save_customer_price(customer_id: int, data: CustomerPriceIn, session: Session = Depends(get_session)):
        return customers.upsert_price(session, customer_id, data)

    @api.delete("/customers/{customer_id}/prices/{price_id}", status_code=204)
    def delete_customer_price(customer_id: int, price_id: int, session: Session = Depends(get_session)):
        customers.delete_price(session, customer_id, price_id)
        return Response(status_code=204)

    if settings.enable_dev_endpoints:

        @api.post("/dev/simulate-email", response_model=IngestResult)
        def simulate_email(email: SimulatedEmail, svc: QuoteService = Depends(get_service)):
            inbound = InboundEmail(message_id=f"<sim-{uuid.uuid4()}@local>", **email.model_dump())
            return ingest_result(svc.ingest_email(inbound))

    app.include_router(integrations)
    app.include_router(api)
    app.mount("/", WebApp(directory=settings.web_dist_dir, html=True, check_dir=False), name="web")
    return app
