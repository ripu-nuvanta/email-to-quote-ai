"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, dateTime, mailboxFor, money } from "@/lib/api";
import { followUpTemplates } from "@/lib/followups";
import type { IngestResult, Product, Quote, QuoteSummary } from "@/lib/types";
import { EmailComposer, type ComposerDefaults, type ComposerMode } from "./EmailComposer";
import { MailIcon, PaperclipIcon } from "./Icons";
import { QuoteDetail } from "./QuoteDetail";
import { useSession } from "./Session";
import { StatusBadge } from "./StatusBadge";

const FILTERS: [value: string, label: string][] = [
  ["pending_approval", "Pending"],
  ["approved", "Approved"],
  ["sending", "Sending"],
  ["sent", "Sent"],
  ["rejected", "Rejected"],
  ["ignored", "Ignored"],
  ["", "All"],
];

const EMAIL_DEFAULTS: ComposerDefaults = {
  fromEmail: "jordan.lee@brightline-construction.com",
  fromName: "Jordan Lee",
  subject: "Quote request - Oakland site",
  body: `Hi team,

Could you send us a quote for the following for our Oakland site:

- 60 boxes nitrile gloves size L
- 25 x hard hats (white)
- Cat6 cable 305m box x 8
- 12 pcs cordless drill 18V
- FS-HB-M8-50 x 40
- 40 boxes M8 hex nuts
- 15 x safety vests

We need delivery by Oct 3. Ship to 1200 Harbor Way, Oakland, CA 94607.

Thanks,
Jordan`,
};

const ATTACHMENT_DEFAULTS: ComposerDefaults = {
  fromEmail: "mgonzalez@summitridgebuilders.com",
  fromName: "Maria Gonzalez",
  subject: "PR-0418 Aspen Ridge Ph2",
  body: "Hi,\n\nPurchase request for Aspen Ridge Phase 2 attached. Please quote with lead times.\n\nThanks,\nMaria",
};

interface ComposerState {
  mode: ComposerMode;
  defaults: ComposerDefaults;
  fromQuote?: Quote;
}

export function QuotesWorkspace() {
  const { token, config } = useSession();
  const router = useRouter();
  const params = useSearchParams();
  const selectedId = Number(params.get("quote")) || null;
  const mailbox = mailboxFor(config?.company_name);

  const [filter, setFilter] = useState("pending_approval");
  const [quotes, setQuotes] = useState<QuoteSummary[]>([]);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [products, setProducts] = useState<Product[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [composer, setComposer] = useState<ComposerState | null>(null);

  const loadList = useCallback(async () => {
    try {
      setQuotes(await api<QuoteSummary[]>(`/api/quotes${filter ? `?status=${filter}` : ""}`, token));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [filter, token]);

  useEffect(() => {
    void loadList();
    const timer = window.setInterval(loadList, 20_000);
    return () => window.clearInterval(timer);
  }, [loadList]);

  useEffect(() => {
    api<Product[]>("/api/products", token)
      .then(setProducts)
      .catch(() => setProducts([]));
  }, [token]);

  useEffect(() => {
    if (!selectedId) {
      setQuote(null);
      return;
    }
    let cancelled = false;
    api<Quote>(`/api/quotes/${selectedId}`, token)
      .then((value) => {
        if (!cancelled) {
          setQuote(value);
          setError(null);
        }
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId, token, reloadKey]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 9000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const openQuote = (id: number) => {
    if (id === selectedId) setReloadKey((key) => key + 1);
    else router.replace(`/?quote=${id}`, { scroll: false });
  };

  const onQuoteChanged = (updated: Quote) => {
    setQuote(updated);
    void loadList();
  };

  const startFollowUp = (current: Quote) => {
    const original = current.conversation[0];
    setComposer({
      mode: "reply",
      fromQuote: current,
      defaults: {
        fromEmail: current.customer?.email ?? current.source_from,
        fromName: current.customer?.name ?? "",
        subject: `Re: ${(original?.subject ?? current.source_subject).replace(/^((re|fw|fwd):\s*)+/i, "")}`,
        body: "",
        inReplyTo: current.source_message_id,
        templates: followUpTemplates(current),
      },
    });
  };

  const onDelivered = (result: IngestResult) => {
    const from = composer?.fromQuote;
    setComposer(null);
    if (from) {
      setNotice(
        result.quote_id === from.id
          ? `The customer's reply was added to ${result.number}.`
          : `${from.number} was already ${from.status.replace("_", " ")}, so the requested changes were made in a new revision: ${result.number}.`,
      );
    } else {
      setNotice(result.status === "ignored" ? "Email received. It isn't a quote request, so it was filed under Ignored." : `Email received: quote ${result.number} created.`);
    }
    setFilter(result.status === "ignored" ? "ignored" : "pending_approval");
    openQuote(result.quote_id);
    void loadList();
  };

  return (
    <div className="workspace">
      <aside className="sidebar">
        <div className="sidebar-head">
          <div className="filters">
            {FILTERS.map(([value, label]) => (
              <button key={label} type="button" className={filter === value ? "chip active" : "chip"} onClick={() => setFilter(value)}>
                {label}
              </button>
            ))}
          </div>
          {config?.dev_tools ? (
            <div className="mailbox">
              <button type="button" className="mailbox-button" onClick={() => setComposer({ mode: "email", defaults: EMAIL_DEFAULTS })}>
                <MailIcon size={18} />
                <span className="mailbox-text">
                  <strong>Connected email</strong>
                  <span className="sub">
                    <span className="status-dot" /> {mailbox}
                  </span>
                </span>
              </button>
              <button type="button" className="icon-button" onClick={() => setComposer({ mode: "attachments", defaults: ATTACHMENT_DEFAULTS })}>
                <PaperclipIcon /> Attachments
              </button>
            </div>
          ) : null}
        </div>
        {quotes.length === 0 ? (
          <p className="muted pad">No quotes here.</p>
        ) : (
          <ul className="item-list">
            {quotes.map((item) => (
              <li key={item.id}>
                <button type="button" className={item.id === selectedId ? "list-item active" : "list-item"} onClick={() => openQuote(item.id)}>
                  <span className="row between">
                    <strong>{item.number}</strong>
                    <span>{money(item.total, item.currency)}</span>
                  </span>
                  <span className="sub">{item.customer_name ?? item.source_from}</span>
                  <span className="row gap-s">
                    <StatusBadge status={item.status} />
                    {item.needs_review_count ? <span className="badge warn">{item.needs_review_count} to review</span> : null}
                    {item.revision ? <span className="badge info">R{item.revision}</span> : null}
                    {item.reply_count ? (
                      <span className="badge">
                        ↩ {item.reply_count} repl{item.reply_count === 1 ? "y" : "ies"}
                      </span>
                    ) : null}
                    <span className="sub">{dateTime(item.created_at)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>

      <section className="detail">
        {notice ? <div className="alert ok">{notice}</div> : null}
        {error ? <div className="alert error">{error}</div> : null}
        {quote ? (
          <QuoteDetail key={quote.id} quote={quote} products={products} onChange={onQuoteChanged} onOpenQuote={openQuote} onFollowUp={() => startFollowUp(quote)} />
        ) : (
          <div className="empty">Select a quote on the left.</div>
        )}
      </section>

      {composer ? <EmailComposer mode={composer.mode} mailbox={mailbox} defaults={composer.defaults} onClose={() => setComposer(null)} onDelivered={onDelivered} /> : null}
    </div>
  );
}
