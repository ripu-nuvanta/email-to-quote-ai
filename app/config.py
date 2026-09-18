from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "sqlite:///./quotes.db"
    seed_demo_data: bool = True

    # AI extraction
    extractor: str = "auto"  # auto | openai | heuristic
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    match_confidence_threshold: float = 0.80
    min_match_score: float = 0.50

    # Guardrails (app/guardrails.py): flag unusually large quantities for review
    large_quantity_threshold: Decimal = Decimal("1000")
    unusual_quantity_factor: Decimal = Decimal("5")  # this many times the customer's usual order

    # Email understanding
    internal_email_domains: str = ""  # comma-separated; mail from these domains is treated as forwarded by staff
    max_attachment_mb: int = 15

    # Inventory / lead time (external system); empty = use local DB
    inventory_api_url: str = ""
    inventory_api_key: str = ""
    inventory_api_timeout: float = 5.0

    # Shipping cost: a courier / shipping platform API, or the fixed rules below
    shipping_api_url: str = ""
    shipping_api_key: str = ""
    shipping_api_timeout: float = 8.0
    shipping_rate_per_kg: Decimal = Decimal("1.50")
    free_shipping_threshold: Decimal = Decimal("2500")
    min_shipping_charge: Decimal = Decimal("15")

    # Outbound mail
    mail_provider: str = "console"  # console | gmail | graph
    outbox_dir: Path = Path("./outbox")
    mail_sender: str = "sales@example.com"
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""

    # Who sends approved quotes: this app ("backend") or n8n ("n8n")
    send_mode: str = "backend"
    n8n_quote_approved_webhook_url: str = ""
    send_claim_timeout_minutes: int = 15
    allow_sample_sends: bool = False  # True lets quotes for the built-in sample customers go out for real

    # Approval notifications
    n8n_quote_ready_webhook_url: str = ""
    sales_notify_email: str = ""

    # Security
    inbound_webhook_secret: str = "change-me-inbound"
    approver_api_token: str = "change-me-approver"
    enable_dev_endpoints: bool = True

    # Approval web app (Next.js)
    cors_origins: str = "http://localhost:3000"
    web_dist_dir: Path = PROJECT_DIR / "web" / "out"

    # Quote defaults
    public_base_url: str = "http://localhost:8000"
    quotes_dir: Path = Path("./quote_pdfs")
    company_name: str = "Acme Industrial Supply"
    company_address: str = "100 Example Street\nSpringfield, CA 90000"
    company_email: str = "sales@example.com"
    currency: str = "USD"
    quote_validity_days: int = 30
    default_tax_rate: Decimal = Decimal("0")

    @property
    def internal_domains(self) -> frozenset[str]:
        return frozenset(d.strip().lower().lstrip("@") for d in self.internal_email_domains.split(",") if d.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
