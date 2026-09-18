"use client";

import { useEffect, useState } from "react";
import { api, ApiError, dateTime, money, qty, send } from "@/lib/api";
import type { LineEdit, Product, Quote, QuoteLine } from "@/lib/types";
import { ApproveDialog, type ApproveOptions } from "./ApproveDialog";
import { Conversation } from "./Conversation";
import { useSession } from "./Session";
import { StatusBadge } from "./StatusBadge";

const PRICE_SOURCES: Record<string, [label: string, tone: string]> = {
  customer_price: ["Contract price", "ok"],
  list_price: ["List price", ""],
  manual: ["Manual price", "info"],
};

function StockBadge({ line }: { line: QuoteLine }) {
  switch (line.stock_status) {
    case "in_stock":
      return <span className="badge ok">In stock · {line.lead_time_days}d</span>;
    case "partial":
      return <span className="badge warn">{line.on_hand} on hand · {line.lead_time_days}d</span>;
    case "backorder":
      return <span className="badge bad">Backorder · {line.lead_time_days}d</span>;
    default:
      return <span className="badge">Unknown</span>;
  }
}

function shippingText(quote: Quote): string {
  const service = quote.shipping_service ?? "Shipping";
  switch (quote.shipping_source) {
    case "api":
      return `${service} · live rate${quote.shipping_transit_days != null ? ` · ${quote.shipping_transit_days} days in transit` : ""}`;
    case "manual":
      return "Set manually";
    case "rules":
    case "rules_fallback":
      return `${service} · standard rate`;
    default:
      return service;
  }
}

interface Props {
  quote: Quote;
  products: Product[];
  onChange: (quote: Quote) => void;
  onOpenQuote: (id: number) => void;
  onFollowUp: () => void;
}

export function QuoteDetail({ quote, products, onChange, onOpenQuote, onFollowUp }: Props) {
  const { token, name, config } = useSession();
  const [edits, setEdits] = useState<Record<number, LineEdit>>({});
  const [version, setVersion] = useState(0); // remounts the line inputs after each save
  const [shipping, setShipping] = useState(Number(quote.shipping_total).toFixed(2));
  const [newSku, setNewSku] = useState("");
  const [newQty, setNewQty] = useState("1");
  const [customerEmail, setCustomerEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [approving, setApproving] = useState(false);

  useEffect(() => setShipping(Number(quote.shipping_total).toFixed(2)), [quote.shipping_total]);
  useEffect(() => () => {
    if (pdfUrl) URL.revokeObjectURL(pdfUrl);
  }, [pdfUrl]);
  useEffect(() => {
    setEdits({});
    setVersion((v) => v + 1);
  }, [quote]);

  const editable = quote.status === "pending_approval";
  const flagged = quote.lines.filter((line) => line.needs_review);
  const shippingChanged = editable && Number(shipping) !== Number(quote.shipping_total);
  const dirty = Object.keys(edits).length > 0 || shippingChanged;
  const listId = `products-${quote.id}`;

  function actor(): string {
    const value = name.trim();
    if (!value) throw new ApiError("Enter your name at the top of the page first.", 400);
    return value;
  }

  async function run(action: () => Promise<Quote>): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      const updated = await action();
      setEdits({});
      setVersion((v) => v + 1);
      setPdfUrl(null);
      onChange(updated);
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  const patch = (body: Record<string, unknown>) =>
    run(() => api<Quote>(`/api/quotes/${quote.id}`, token, send({ actor: actor(), ...body }, "PATCH")));

  function editLine(line: QuoteLine, field: keyof LineEdit, value: string) {
    setEdits((current) => {
      const entry: LineEdit = { ...(current[line.id] ?? { id: line.id }) };
      if (value === "") delete entry[field];
      else (entry as Record<string, string | number>)[field] = value;
      return { ...current, [line.id]: entry };
    });
  }

  const confirmLine = (line: QuoteLine) => setEdits((current) => ({ ...current, [line.id]: current[line.id] ?? { id: line.id } }));

  function save() {
    const body: Record<string, unknown> = { lines: Object.values(edits) };
    if (shippingChanged) body.shipping_total = shipping;
    return patch(body);
  }

  async function addLine() {
    const ok = await patch({ lines: [...Object.values(edits), { product_sku: newSku.trim(), quantity: newQty.trim() || "1" }] });
    if (ok) {
      setNewSku("");
      setNewQty("1");
    }
  }

  function removeLine(line: QuoteLine) {
    if (window.confirm(`Remove line ${line.position} (${line.sku ?? line.requested_text})?`)) {
      void patch({ lines: [{ id: line.id, remove: true }] });
    }
  }

  async function setCustomer() {
    if (await patch({ customer_email: customerEmail.trim() })) setCustomerEmail("");
  }

  async function previewPdf() {
    setError(null);
    try {
      const blob = await api<Blob>(`/api/quotes/${quote.id}/pdf`, token);
      setPdfUrl(URL.createObjectURL(blob));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function reject() {
    const reason = window.prompt("Why are you rejecting this quote?");
    if (reason?.trim()) {
      void run(() => api<Quote>(`/api/quotes/${quote.id}/reject`, token, send({ approver: actor(), reason: reason.trim() })));
    }
  }

  async function approve(options: ApproveOptions) {
    const ok = await run(() =>
      api<Quote>(
        `/api/quotes/${quote.id}/approve`,
        token,
        send({ approver: actor(), send: options.send, cover_message: options.coverMessage || null, confirm_flagged_lines: options.confirmFlagged }),
      ),
    );
    if (ok) setApproving(false);
  }

  const sendNow = () => run(() => api<Quote>(`/api/quotes/${quote.id}/send`, token, send({ actor: actor() })));
  const reload = () => run(() => api<Quote>(`/api/quotes/${quote.id}`, token));

  return (
    <>
      {error ? <div className="alert error">{error}</div> : null}

      {/* Title and actions */}
      <div className="card">
        <div className="row between">
          <div className="row gap">
            <h2>{quote.number}</h2>
            <StatusBadge status={quote.status} />
            {quote.revision ? <span className="badge info">Revision {quote.revision}</span> : null}
            <span className="muted">Valid until {quote.valid_until ?? "—"}</span>
          </div>
          <div className="row">
            <button type="button" onClick={previewPdf}>
              {pdfUrl ? "Refresh PDF" : "Preview PDF"}
            </button>
            {editable ? (
              <>
                <button type="button" className="danger" disabled={busy} onClick={reject}>
                  Reject
                </button>
                <button type="button" className="primary" disabled={busy || dirty} title={dirty ? "Save your changes first" : undefined} onClick={() => setApproving(true)}>
                  Approve…
                </button>
              </>
            ) : null}
            {quote.status === "approved" ? (
              <button type="button" className="primary" disabled={busy} onClick={sendNow}>
                {config?.send_mode === "n8n" ? "Send via n8n" : "Send to customer"}
              </button>
            ) : null}
            {quote.status === "sending" ? (
              <button type="button" disabled={busy} onClick={reload}>
                Refresh
              </button>
            ) : null}
          </div>
        </div>

        {quote.revision_of_id ? (
          <p className="muted small spaced">
            Revision of{" "}
            <button type="button" className="link" onClick={() => onOpenQuote(quote.revision_of_id!)}>
              {quote.revision_of_number}
            </button>
            , created from the customer&apos;s follow-up email.
          </p>
        ) : null}
        {quote.latest_revision_id ? (
          <p className="alert warn">
            The customer asked for changes after this quote. Latest version:{" "}
            <button type="button" className="link" onClick={() => onOpenQuote(quote.latest_revision_id!)}>
              {quote.latest_revision_number}
            </button>
          </p>
        ) : null}
        {quote.status === "sending" ? <p className="alert warn">This quote is being sent{config?.send_mode === "n8n" ? " by n8n" : ""}.</p> : null}
        {quote.status === "approved" && config?.send_mode === "n8n" ? (
          <p className="alert warn">Approved and handed to n8n. It changes to “Sent” when n8n reports back.</p>
        ) : null}
        {quote.rejection_reason ? <p className="alert error">Rejected: {quote.rejection_reason}</p> : null}
        {quote.internal_notes ? <p className="alert warn">{quote.internal_notes}</p> : null}
      </div>

      <Conversation quote={quote} aiReading={Boolean(config?.ai_reading)} onFollowUp={onFollowUp} />

      {/* Customer and delivery */}
      <div className="card">
        <div className="grid-2 flush-top">
          <div className="stack">
            <h3>Customer</h3>
            {quote.customer ? (
              <div>
                <strong>{quote.customer.name}</strong>
                {quote.customer.company ? ` · ${quote.customer.company}` : ""}{" "}
                {!quote.customer.is_verified ? <span className="badge warn">New / unverified</span> : null}
                <div>{quote.customer.email}</div>
                <div className="muted small">
                  Terms {quote.customer.payment_terms} · discount {Number(quote.customer.discount_pct)}% · tax region {quote.customer.tax_region ?? "unknown"}
                </div>
              </div>
            ) : (
              <p className="alert error">No customer yet. Enter the customer’s email address below.</p>
            )}
            {quote.forwarded_by ? <div className="muted small">Forwarded by {quote.forwarded_by} (copied on the quote email)</div> : null}
            {editable ? (
              <form
                className="row"
                onSubmit={(event) => {
                  event.preventDefault();
                  void setCustomer();
                }}
              >
                <input type="email" className="grow" placeholder={quote.customer ? "Change to another customer email" : "customer@company.com"} value={customerEmail} onChange={(e) => setCustomerEmail(e.target.value)} />
                <button type="submit" disabled={busy || !customerEmail.trim()}>
                  {quote.customer ? "Change" : "Set customer"}
                </button>
              </form>
            ) : null}
          </div>
          <div className="stack">
            <h3>Delivery</h3>
            <div style={{ whiteSpace: "pre-wrap" }}>{quote.shipping_address ?? <span className="muted">No address given</span>}</div>
            <div className="muted small">Requested delivery: {quote.requested_delivery ?? "—"}</div>
            {quote.customer_notes ? (
              <div className="small">
                <span className="muted">Customer notes (not binding):</span> {quote.customer_notes}
              </div>
            ) : null}
          </div>
        </div>
      </div>

      {/* Products */}
      <div className="card">
        <div className="row between">
          <h3>Products</h3>
          {editable && flagged.length ? (
            <span className="badge warn">
              {flagged.length} line{flagged.length > 1 ? "s" : ""} to review
            </span>
          ) : null}
        </div>
        {editable && flagged.length ? <p className="muted small">Fix the highlighted lines or click Confirm, then Save changes.</p> : null}

        <datalist id={listId}>
          {products.map((product) => (
            <option key={product.sku} value={product.sku}>
              {product.name}
            </option>
          ))}
        </datalist>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Product</th>
                <th className="num">Qty</th>
                <th className="num">Unit price</th>
                <th className="num">Disc %</th>
                <th>Stock</th>
                <th className="num">Amount</th>
                {editable ? <th /> : null}
              </tr>
            </thead>
            <tbody>
              {quote.lines.length === 0 ? (
                <tr>
                  <td colSpan={editable ? 8 : 7} className="muted">
                    No products yet{editable ? ". Add them below." : "."}
                  </td>
                </tr>
              ) : null}
              {quote.lines.map((line) => {
                const edit = edits[line.id];
                const [priceLabel, priceTone] = PRICE_SOURCES[line.price_source ?? ""] ?? ["", ""];
                return (
                  <tr key={`${line.id}-${version}`} className={line.needs_review && !edit ? "flagged" : undefined}>
                    <td>{line.position}</td>
                    <td>
                      {editable ? (
                        <input className="sku-input" list={listId} defaultValue={line.sku ?? ""} placeholder="Choose SKU" onChange={(e) => editLine(line, "product_sku", e.target.value.trim())} />
                      ) : (
                        <strong>{line.sku ?? "—"}</strong>
                      )}
                      <div>{line.description}</div>
                      <div className="sub">
                        Asked for: “{line.requested_text}” · {line.match_method ?? "no match"}
                        {line.match_confidence != null ? ` ${Math.round(line.match_confidence * 100)}%` : ""}
                      </div>
                      {line.review_reason ? <div className="reason">⚠ {line.review_reason}</div> : null}
                    </td>
                    <td className="num">
                      {editable ? (
                        <input className="num-input" inputMode="decimal" defaultValue={qty(line.quantity)} onChange={(e) => editLine(line, "quantity", e.target.value.trim())} />
                      ) : (
                        qty(line.quantity)
                      )}{" "}
                      <span className="sub">{line.unit ?? ""}</span>
                    </td>
                    <td className="num">
                      {editable ? (
                        <input className="num-input" inputMode="decimal" defaultValue={Number(line.unit_price).toFixed(2)} onChange={(e) => editLine(line, "unit_price", e.target.value.trim())} />
                      ) : (
                        money(line.unit_price)
                      )}
                      {priceLabel ? (
                        <div>
                          <span className={`badge ${priceTone}`}>{priceLabel}</span>
                        </div>
                      ) : null}
                    </td>
                    <td className="num">
                      {editable ? (
                        <input className="num-input" style={{ width: 64 }} inputMode="decimal" defaultValue={String(Number(line.discount_pct))} onChange={(e) => editLine(line, "discount_pct", e.target.value.trim())} />
                      ) : (
                        Number(line.discount_pct)
                      )}
                    </td>
                    <td>
                      <StockBadge line={line} />
                    </td>
                    <td className="num">{money(line.line_total)}</td>
                    {editable ? (
                      <td>
                        <div className="row gap-s">
                          {line.needs_review ? (
                            <button type="button" disabled={Boolean(edit)} onClick={() => confirmLine(line)}>
                              {edit ? "✓ Confirmed" : "Confirm"}
                            </button>
                          ) : null}
                          <button type="button" className="danger" title="Remove line" disabled={busy} onClick={() => removeLine(line)}>
                            ✕
                          </button>
                        </div>
                      </td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {editable ? (
          <div className="row gap spaced">
            <input className="sku-input" list={listId} placeholder="Add product (SKU)" value={newSku} onChange={(e) => setNewSku(e.target.value)} />
            <input className="num-input" style={{ width: 70 }} inputMode="decimal" placeholder="Qty" value={newQty} onChange={(e) => setNewQty(e.target.value)} />
            <button type="button" disabled={busy || !newSku.trim()} onClick={addLine}>
              Add line
            </button>
            <span className="grow" />
            <button type="button" className="primary" disabled={busy || !dirty} onClick={save}>
              Save changes
            </button>
          </div>
        ) : null}

        <div className="row top gap spaced">
          <div className="stack small">
            <strong>Shipping</strong>
            <span>{shippingText(quote)}</span>
            {quote.shipping_source === "rules_fallback" ? <span className="badge warn">Shipping API unavailable, standard rate used</span> : null}
            {editable ? (
              <div className="row gap-s">
                <label className="row gap-s">
                  Override
                  <input className="num-input" inputMode="decimal" value={shipping} onChange={(e) => setShipping(e.target.value)} />
                </label>
                {quote.shipping_manual ? (
                  <button type="button" disabled={busy} onClick={() => patch({ reset_shipping: true })}>
                    Use automatic
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
          <div className="totals">
            <div>
              <span>Subtotal</span>
              <span>{money(quote.subtotal, quote.currency)}</span>
            </div>
            <div>
              <span>Discount</span>
              <span>− {money(quote.discount_total, quote.currency)}</span>
            </div>
            <div>
              <span>Shipping</span>
              <span>{money(quote.shipping_total, quote.currency)}</span>
            </div>
            <div>
              <span>Tax ({(Number(quote.tax_rate) * 100).toFixed(2)}%)</span>
              <span>{money(quote.tax_total, quote.currency)}</span>
            </div>
            <div className="grand">
              <span>Total</span>
              <span>{money(quote.total, quote.currency)}</span>
            </div>
          </div>
        </div>
      </div>

      {pdfUrl ? (
        <div className="card">
          <iframe className="pdf" src={pdfUrl} title={`Quotation ${quote.number}`} />
        </div>
      ) : null}

      <div className="card">
        <h3>History</h3>
        <ul className="events">
          {quote.events.map((event, index) => (
            <li key={`${event.at}-${index}`}>
              <span className="muted">{dateTime(event.at)}</span> · <strong>{event.event.replaceAll("_", " ")}</strong> by {event.actor}{" "}
              {event.detail ? <span className="muted">{JSON.stringify(event.detail)}</span> : null}
            </li>
          ))}
        </ul>
      </div>

      {approving ? <ApproveDialog quote={quote} sendMode={config?.send_mode ?? "backend"} busy={busy} error={error} onCancel={() => setApproving(false)} onApprove={approve} /> : null}
    </>
  );
}
