import email
from decimal import Decimal as D
from email import policy
from types import SimpleNamespace

from app.documents import EmailContent, read_email
from app.extraction import HeuristicExtractor, OpenAIExtractor
from app.schemas import FollowUpChanges, InboundEmail
from tests.conftest import AUTH, get_quote, ingest, lines_by_sku


def reply(sample_email, body, message_id="<reply-1@brightline-construction.com>", **extra):
    return {
        "message_id": message_id,
        "from_email": sample_email["from_email"],
        "from_name": "Jordan Lee",
        "subject": "Re: " + sample_email["subject"],
        "body_text": body,
        "in_reply_to": sample_email["message_id"],
        **extra,
    }


def test_reply_to_pending_quote_updates_it_and_flags_every_change(client, sample_email):
    quote_id = ingest(client, sample_email)["quote_id"]
    body = ("Hi Sam, thanks for the quote.\n\nPlease remove the cordless drills. Change the hard hats to 30. "
            "Also add 5 sets drill bit set.\n\nThanks,\nJordan")
    result = ingest(client, reply(sample_email, body))
    assert result["quote_id"] == quote_id and result["status"] == "pending_approval"

    quote = get_quote(client, quote_id)
    lines = lines_by_sku(quote)
    assert "TL-DR-18V" not in lines
    assert D(lines["SF-HH-WHT"]["quantity"]) == 30 and "from 25 to 30" in lines["SF-HH-WHT"]["review_reason"]
    assert D(lines["TL-BIT-HSS-19"]["quantity"]) == 5 and "follow-up" in lines["TL-BIT-HSS-19"]["review_reason"]
    assert [line["position"] for line in quote["lines"]] == list(range(1, len(quote["lines"]) + 1))

    assert quote["reply_count"] == 1 and len(quote["conversation"]) == 2
    follow_up = quote["conversation"][1]
    assert (follow_up["kind"], follow_up["outcome"]) == ("follow_up", "updated")
    assert {change["action"] for change in follow_up["changes"]} == {"removed", "quantity_changed", "added"}
    assert quote["events"][-1]["event"] == "follow_up_applied"

    # The same reply delivered twice changes nothing.
    assert ingest(client, reply(sample_email, body))["quote_id"] == quote_id
    assert get_quote(client, quote_id)["reply_count"] == 1


def test_reply_to_sent_quote_creates_a_revision_and_keeps_the_original(client, sample_email, settings):
    quote_id = ingest(client, sample_email)["quote_id"]
    sent = client.post(f"/api/quotes/{quote_id}/approve", json={"approver": "Sam", "confirm_flagged_lines": True}, headers=AUTH).json()
    assert sent["status"] == "sent"

    result = ingest(client, reply(sample_email, "Could you swap the hard hats for 25 x hi vis vest XL? Everything else is fine."))
    assert result["quote_id"] != quote_id and result["number"] == f"{sent['number']}-R1"

    original = get_quote(client, quote_id)
    assert original["status"] == "sent" and original["latest_revision_number"] == result["number"]
    assert any(line["sku"] == "SF-HH-WHT" for line in original["lines"])

    revision = get_quote(client, result["quote_id"])
    assert (revision["status"], revision["revision"], revision["revision_of_number"]) == ("pending_approval", 1, sent["number"])
    positions = {line["sku"]: line["position"] for line in revision["lines"]}
    assert "SF-HH-WHT" not in positions and positions["SF-VEST-HV-XL"] == 2  # swapped in place
    assert lines_by_sku(revision)["SF-VEST-HV-XL"]["needs_review"]
    assert [entry["kind"] for entry in revision["conversation"]] == ["request", "follow_up"]
    assert revision["conversation"][1]["outcome"] == "revision_created"

    sent_revision = client.post(f"/api/quotes/{result['quote_id']}/approve", json={"approver": "Sam", "confirm_flagged_lines": True}, headers=AUTH).json()
    assert sent_revision["status"] == "sent"
    latest = sorted(settings.outbox_dir.glob("*.eml"))[-1]
    message = email.message_from_bytes(latest.read_bytes(), policy=policy.default)
    assert message["Subject"] == "Re: Quote request - Oakland site"
    assert [part.get_filename() for part in message.iter_attachments()] == [f"{result['number']}.pdf"]
    assert "revised quotation" in message.get_body(("plain",)).get_content()


def test_reply_without_changes_is_recorded_only(client, sample_email):
    quote_id = ingest(client, sample_email)["quote_id"]
    before = len(get_quote(client, quote_id)["lines"])
    result = ingest(client, reply(sample_email, "Thanks, this looks good. We'll send the PO on Monday."))
    quote = get_quote(client, result["quote_id"])
    assert result["quote_id"] == quote_id and len(quote["lines"]) == before
    assert quote["conversation"][1]["outcome"] == "no_changes"
    assert "Customer replied" in quote["internal_notes"]


def test_strangers_cannot_change_a_quote_by_quoting_its_number(client, sample_email):
    quote = get_quote(client, ingest(client, sample_email)["quote_id"])
    result = ingest(client, {
        "message_id": "<stranger@elsewhere>", "from_email": "someone@elsewhere-corp.com",
        "subject": f"Re: Quotation {quote['number']}", "body_text": "Please remove the hard hats.\n- 3 x 24 port patch panel",
    })
    assert result["quote_id"] != quote["id"]
    assert get_quote(client, quote["id"])["reply_count"] == 0


def test_heuristic_reads_common_ways_of_asking_for_changes():
    extractor = HeuristicExtractor()
    inbound = InboundEmail(message_id="m", from_email="a@b.com")
    body = ("Hi team,\n"
            "Instead of the white hard hats, please quote 20 x hi vis vest L.\n"
            "We don't need the drill bit sets anymore, thanks.\n"
            "- 10 x FS-WA-M8\n\n"
            "On Mon, 14 Sep 2026 at 09:12, Sales wrote:\n> Remove everything")
    result = extractor.extract_changes(inbound, EmailContent(body=body), [])
    assert [(c.action, c.existing_item, c.new_product, c.quantity) for c in result.changes] == [
        ("replace", "white hard hats", "hi vis vest L", 20.0),
        ("remove", "drill bit sets", None, None),
        ("add", None, "FS-WA-M8", 10.0),
    ]
    assert result.changes[2].new_sku == "FS-WA-M8"
    assert not extractor.extract_changes(inbound, EmailContent(body="Great, thanks! PO to follow."), []).is_change_request


def test_openai_follow_up_gets_the_current_quote_lines():
    captured = {}

    class FakeResponses:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_parsed=FollowUpChanges(is_change_request=False, changes=[], summary="Thanks only."))

    extractor = OpenAIExtractor("sk-test", "test-model", client=SimpleNamespace(responses=FakeResponses()))
    inbound = InboundEmail(message_id="m", from_email="a@b.com", body_text="Looks good")
    result = extractor.extract_changes(inbound, read_email(inbound), ["1. SF-HH-WHT: Safety Helmet, White, EN397 | quantity 25 ea"])
    assert result.summary == "Thanks only." and captured["text_format"] is FollowUpChanges
    assert "1. SF-HH-WHT: Safety Helmet" in captured["input"][1]["content"][0]["text"]
