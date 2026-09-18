"""Outbound email adapters: console (.eml files), Gmail API, Microsoft Graph."""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

import httpx

from .config import Settings
from .models import utcnow

log = logging.getLogger(__name__)


class MailError(Exception):
    pass


@dataclass
class OutgoingEmail:
    to: str
    subject: str
    body_text: str
    attachments: list[tuple[str, bytes, str]] = field(default_factory=list)  # (filename, data, mime type)
    cc: list[str] = field(default_factory=list)
    in_reply_to: str | None = None  # RFC 5322 Message-ID of the customer's email, for threading
    thread_id: str | None = None  # provider thread id (Gmail)


class Mailer(Protocol):
    def send(self, message: OutgoingEmail) -> str: ...


def build_mime(message: OutgoingEmail, sender: str) -> EmailMessage:
    mime = EmailMessage()
    mime["From"] = sender
    mime["To"] = message.to
    if message.cc:
        mime["Cc"] = ", ".join(message.cc)
    mime["Subject"] = message.subject
    if message.in_reply_to and message.in_reply_to.startswith("<"):
        mime["In-Reply-To"] = message.in_reply_to
        mime["References"] = message.in_reply_to
    mime.set_content(message.body_text)
    for filename, data, mime_type in message.attachments:
        maintype, _, subtype = mime_type.partition("/")
        mime.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return mime


class ConsoleMailer:
    """Development mailer: writes each message to OUTBOX_DIR as an .eml file."""

    def __init__(self, outbox_dir: Path, sender: str):
        self.outbox_dir = Path(outbox_dir)
        self.sender = sender

    def send(self, message: OutgoingEmail) -> str:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", message.subject)[:60].strip("-")
        path = self.outbox_dir.resolve() / f"{utcnow():%Y%m%dT%H%M%S%f}-{slug}.eml"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bytes(build_mime(message, self.sender)))
        except OSError as exc:
            raise MailError(f"Could not write {path}: {exc}") from exc
        log.info("Email to %s written to %s", message.to, path)
        return str(path)


class GmailMailer:
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

    def __init__(self, client_id: str, client_secret: str, refresh_token: str, sender: str, http: httpx.Client | None = None):
        self.client_id, self.client_secret, self.refresh_token = client_id, client_secret, refresh_token
        self.sender = sender
        self.http = http or httpx.Client(timeout=20)

    def send(self, message: OutgoingEmail) -> str:
        try:
            token = self.http.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            token.raise_for_status()
            raw = base64.urlsafe_b64encode(bytes(build_mime(message, self.sender))).decode()
            payload = {"raw": raw} | ({"threadId": message.thread_id} if message.thread_id else {})
            response = self.http.post(
                self.SEND_URL, json=payload, headers={"Authorization": f"Bearer {token.json()['access_token']}"}
            )
            response.raise_for_status()
            return response.json()["id"]
        except (httpx.HTTPError, KeyError) as exc:
            raise MailError(f"Gmail send failed: {exc}") from exc


class GraphMailer:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str, sender: str, http: httpx.Client | None = None):
        self.tenant_id, self.client_id, self.client_secret = tenant_id, client_id, client_secret
        self.sender = sender
        self.http = http or httpx.Client(timeout=20)

    def send(self, message: OutgoingEmail) -> str:
        try:
            token = self.http.post(
                f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                    "grant_type": "client_credentials",
                },
            )
            token.raise_for_status()
            payload = {
                "message": {
                    "subject": message.subject,
                    "body": {"contentType": "Text", "content": message.body_text},
                    "toRecipients": [{"emailAddress": {"address": message.to}}],
                    "ccRecipients": [{"emailAddress": {"address": address}} for address in message.cc],
                    "attachments": [
                        {
                            "@odata.type": "#microsoft.graph.fileAttachment",
                            "name": filename,
                            "contentType": mime_type,
                            "contentBytes": base64.b64encode(data).decode(),
                        }
                        for filename, data, mime_type in message.attachments
                    ],
                },
                "saveToSentItems": True,
            }
            response = self.http.post(
                f"https://graph.microsoft.com/v1.0/users/{self.sender}/sendMail",
                json=payload,
                headers={"Authorization": f"Bearer {token.json()['access_token']}"},
            )
            response.raise_for_status()
            return f"graph:{response.status_code}"
        except (httpx.HTTPError, KeyError) as exc:
            raise MailError(f"Microsoft Graph send failed: {exc}") from exc


def make_mailer(settings: Settings) -> Mailer:
    if settings.mail_provider == "gmail":
        return GmailMailer(settings.gmail_client_id, settings.gmail_client_secret, settings.gmail_refresh_token, settings.mail_sender)
    if settings.mail_provider == "graph":
        return GraphMailer(settings.graph_tenant_id, settings.graph_client_id, settings.graph_client_secret, settings.mail_sender)
    return ConsoleMailer(settings.outbox_dir, settings.mail_sender)
