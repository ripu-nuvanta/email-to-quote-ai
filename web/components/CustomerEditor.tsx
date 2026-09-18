"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, money, send } from "@/lib/api";
import type { CustomerListItem, CustomerPrice, Product } from "@/lib/types";
import { useSession } from "./Session";

const EMPTY_PRICE = { sku: "", unit_price: "", min_qty: "1", valid_from: "", valid_until: "", note: "" };

interface Props {
  customer: CustomerListItem;
  products: Product[];
  onSaved: (customer: CustomerListItem) => void;
  onPricesChanged: () => void;
}

export function CustomerEditor({ customer, products, onSaved, onPricesChanged }: Props) {
  const { token } = useSession();
  const [form, setForm] = useState({
    name: customer.name,
    company: customer.company ?? "",
    tax_region: customer.tax_region ?? "",
    discount_pct: String(Number(customer.discount_pct)),
    payment_terms: customer.payment_terms,
    billing_address: customer.billing_address ?? "",
    is_verified: customer.is_verified,
  });
  const [prices, setPrices] = useState<CustomerPrice[]>([]);
  const [draft, setDraft] = useState(EMPTY_PRICE);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const loadPrices = useCallback(async () => {
    try {
      setPrices(await api<CustomerPrice[]>(`/api/customers/${customer.id}/prices`, token));
    } catch (e) {
      setMessage({ tone: "error", text: (e as Error).message });
    }
  }, [customer.id, token]);

  useEffect(() => {
    void loadPrices();
  }, [loadPrices]);

  async function attempt(action: () => Promise<void>, success: string) {
    setBusy(true);
    setMessage(null);
    try {
      await action();
      setMessage({ tone: "ok", text: success });
    } catch (e) {
      setMessage({ tone: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  const saveCustomer = (event: FormEvent) => {
    event.preventDefault();
    void attempt(async () => {
      const updated = await api<CustomerListItem>(
        `/api/customers/${customer.id}`,
        token,
        send(
          {
            ...form,
            company: form.company.trim() || null,
            tax_region: form.tax_region.trim() || null,
            billing_address: form.billing_address.trim() || null,
          },
          "PATCH",
        ),
      );
      onSaved(updated);
    }, "Customer saved.");
  };

  const addPrice = (event: FormEvent) => {
    event.preventDefault();
    void attempt(async () => {
      await api<CustomerPrice>(
        `/api/customers/${customer.id}/prices`,
        token,
        send(
          {
            sku: draft.sku.trim(),
            unit_price: draft.unit_price.trim(),
            min_qty: Number(draft.min_qty) || 1,
            valid_from: draft.valid_from || null,
            valid_until: draft.valid_until || null,
            note: draft.note.trim() || null,
          },
          "PUT",
        ),
      );
      setDraft(EMPTY_PRICE);
      await loadPrices();
      onPricesChanged();
    }, "Contract price saved. New quotes for this customer will use it.");
  };

  const removePrice = (price: CustomerPrice) => {
    if (!window.confirm(`Remove the contract price for ${price.sku}?`)) return;
    void attempt(async () => {
      await api<void>(`/api/customers/${customer.id}/prices/${price.id}`, token, { method: "DELETE" });
      await loadPrices();
      onPricesChanged();
    }, "Contract price removed.");
  };

  const selectedProduct = products.find((product) => product.sku === draft.sku.trim());

  return (
    <>
      {message ? <div className={`alert ${message.tone}`}>{message.text}</div> : null}

      <form className="card" onSubmit={saveCustomer}>
        <div className="row between">
          <div>
            <h2>{customer.company ?? customer.name}</h2>
            <div className="muted">{customer.email}</div>
          </div>
          <label className="check">
            <input type="checkbox" checked={form.is_verified} onChange={(e) => setForm({ ...form, is_verified: e.target.checked })} />
            Verified customer
          </label>
        </div>
        <div className="form-grid spaced">
          <label className="field">
            Contact name
            <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
          <label className="field">
            Company
            <input value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} />
          </label>
          <label className="field">
            Tax region (e.g. US-CA)
            <input value={form.tax_region} onChange={(e) => setForm({ ...form, tax_region: e.target.value })} />
          </label>
          <label className="field">
            General discount %
            <input inputMode="decimal" value={form.discount_pct} onChange={(e) => setForm({ ...form, discount_pct: e.target.value })} />
          </label>
          <label className="field">
            Payment terms
            <input required value={form.payment_terms} onChange={(e) => setForm({ ...form, payment_terms: e.target.value })} />
          </label>
        </div>
        <label className="field spaced">
          Billing address
          <textarea rows={3} value={form.billing_address} onChange={(e) => setForm({ ...form, billing_address: e.target.value })} />
        </label>
        <div className="row end spaced">
          <button type="submit" className="primary" disabled={busy}>
            Save customer
          </button>
        </div>
      </form>

      <div className="card">
        <h3>Contract prices</h3>
        <p className="muted small">
          These prices replace the list price for this customer (and verified colleagues at the same company). The general discount is not added on top.
        </p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>SKU</th>
                <th>Product</th>
                <th className="num">From qty</th>
                <th className="num">Contract price</th>
                <th className="num">List price</th>
                <th>Valid</th>
                <th>Note</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {prices.length === 0 ? (
                <tr>
                  <td colSpan={8} className="muted">
                    No contract prices yet.
                  </td>
                </tr>
              ) : null}
              {prices.map((price) => (
                <tr key={price.id}>
                  <td>
                    <strong>{price.sku}</strong>
                  </td>
                  <td>{price.product_name}</td>
                  <td className="num">{price.min_qty}</td>
                  <td className="num">{money(price.unit_price)}</td>
                  <td className="num muted">{money(price.list_price)}</td>
                  <td className="small">{price.valid_from || price.valid_until ? `${price.valid_from ?? "…"} → ${price.valid_until ?? "…"}` : "Always"}</td>
                  <td className="small">{price.note ?? ""}</td>
                  <td>
                    <button type="button" className="danger" disabled={busy} onClick={() => removePrice(price)} title="Remove">
                      ✕
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <form className="spaced" onSubmit={addPrice}>
          <datalist id={`customer-products-${customer.id}`}>
            {products.map((product) => (
              <option key={product.sku} value={product.sku}>
                {product.name}
              </option>
            ))}
          </datalist>
          <div className="form-grid">
            <label className="field">
              SKU
              <input required list={`customer-products-${customer.id}`} value={draft.sku} onChange={(e) => setDraft({ ...draft, sku: e.target.value })} />
            </label>
            <label className="field">
              Contract price {selectedProduct ? `(list ${money(selectedProduct.base_price)})` : ""}
              <input required inputMode="decimal" value={draft.unit_price} onChange={(e) => setDraft({ ...draft, unit_price: e.target.value })} />
            </label>
            <label className="field">
              From quantity
              <input inputMode="numeric" value={draft.min_qty} onChange={(e) => setDraft({ ...draft, min_qty: e.target.value })} />
            </label>
            <label className="field">
              Valid from
              <input type="date" value={draft.valid_from} onChange={(e) => setDraft({ ...draft, valid_from: e.target.value })} />
            </label>
            <label className="field">
              Valid until
              <input type="date" value={draft.valid_until} onChange={(e) => setDraft({ ...draft, valid_until: e.target.value })} />
            </label>
            <label className="field">
              Note
              <input value={draft.note} onChange={(e) => setDraft({ ...draft, note: e.target.value })} placeholder="e.g. 2026 contract" />
            </label>
          </div>
          <div className="row end spaced">
            <button type="submit" disabled={busy || !draft.sku.trim() || !draft.unit_price.trim()}>
              Save contract price
            </button>
          </div>
        </form>
      </div>
    </>
  );
}
