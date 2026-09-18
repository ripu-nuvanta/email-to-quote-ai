import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
WEBHOOK = {"X-Webhook-Secret": "test-inbound"}
AUTH = {"Authorization": "Bearer test-approver"}


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        extractor="heuristic",
        mail_provider="console",
        outbox_dir=tmp_path / "outbox",
        quotes_dir=tmp_path / "pdfs",
        inbound_webhook_secret="test-inbound",
        approver_api_token="test-approver",
        internal_email_domains="acme-supply.example",
        web_dist_dir=tmp_path / "web-not-built",
        cors_origins="",
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def sample_email():
    return json.loads((SAMPLES / "quote_request.json").read_text(encoding="utf-8"))


def ingest(client, payload):
    response = client.post("/api/inbound-email", json=payload, headers=WEBHOOK)
    assert response.status_code == 200, response.text
    return response.json()


def get_quote(client, quote_id):
    return client.get(f"/api/quotes/{quote_id}", headers=AUTH).json()


def lines_by_sku(quote):
    return {line["sku"]: line for line in quote["lines"]}
