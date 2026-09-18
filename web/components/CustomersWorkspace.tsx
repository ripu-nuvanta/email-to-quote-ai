"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { CustomerListItem, Product } from "@/lib/types";
import { CustomerEditor } from "./CustomerEditor";
import { useSession } from "./Session";

export function CustomersWorkspace() {
  const { token } = useSession();
  const [customers, setCustomers] = useState<CustomerListItem[]>([]);
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [products, setProducts] = useState<Product[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setCustomers(await api<CustomerListItem[]>(`/api/customers?q=${encodeURIComponent(search)}`, token));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [search, token]);

  useEffect(() => {
    const timer = window.setTimeout(load, 250);
    return () => window.clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    api<Product[]>("/api/products", token)
      .then(setProducts)
      .catch(() => setProducts([]));
  }, [token]);

  const selected = customers.find((customer) => customer.id === selectedId) ?? null;

  return (
    <div className="workspace">
      <aside className="sidebar">
        <div className="sidebar-head">
          <input type="search" placeholder="Search name, company or email" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        {customers.length === 0 ? (
          <p className="muted pad">No customers found.</p>
        ) : (
          <ul className="item-list">
            {customers.map((customer) => (
              <li key={customer.id}>
                <button type="button" className={customer.id === selectedId ? "list-item active" : "list-item"} onClick={() => setSelectedId(customer.id)}>
                  <span className="row between">
                    <strong>{customer.company ?? customer.name}</strong>
                    {customer.is_verified ? null : <span className="badge warn">Unverified</span>}
                  </span>
                  <span className="sub">
                    {customer.name} · {customer.email}
                  </span>
                  <span className="sub">
                    {customer.price_count} contract price{customer.price_count === 1 ? "" : "s"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>
      <section className="detail">
        {error ? <div className="alert error">{error}</div> : null}
        {selected ? (
          <CustomerEditor
            key={selected.id}
            customer={selected}
            products={products}
            onSaved={(updated) => setCustomers((list) => list.map((c) => (c.id === updated.id ? updated : c)))}
            onPricesChanged={load}
          />
        ) : (
          <div className="empty">Select a customer to check their details and manage their contract prices.</div>
        )}
      </section>
    </div>
  );
}
