"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";
import { useSession } from "./Session";

const NAV = [
  { href: "/", label: "Quotes", match: (path: string) => path === "/" },
  { href: "/customers/", label: "Customers & prices", match: (path: string) => path.startsWith("/customers") },
];

export function AppShell({ children }: { children: ReactNode }) {
  const { ready, token, name, config, connectionError, setToken, setName } = useSession();
  const pathname = usePathname() ?? "/";
  const [draftToken, setDraftToken] = useState("");
  const connecting = Boolean(token) && !config && !connectionError;

  let content: ReactNode;
  if (!ready || connecting) {
    content = <main className="page center muted">Connecting…</main>;
  } else if (config) {
    content = <main className="page">{children}</main>;
  } else {
    const wrongToken = connectionError === "Invalid approver token";
    content = (
      <main className="page center">
        <form
          className="card signin"
          onSubmit={(event) => {
            event.preventDefault();
            setToken(draftToken.trim());
          }}
        >
          <h1>Sign in</h1>
          <p className="muted">
            Enter the approver token from the <code>.env</code> file (the value of <code>APPROVER_API_TOKEN</code>).
          </p>
          <input type="password" value={draftToken} onChange={(e) => setDraftToken(e.target.value)} placeholder="Approver token" autoFocus />
          {connectionError ? (
            <p className="alert error">{wrongToken ? "That token isn't correct." : `Can't reach the quote API: ${connectionError}`}</p>
          ) : null}
          <button className="primary" type="submit" disabled={!draftToken.trim()}>
            Continue
          </button>
        </form>
      </main>
    );
  }

  return (
    <>
      <header className="topbar">
        <div className="brand">{config?.company_name ?? "Quote approvals"}</div>
        <nav className="nav">
          {config
            ? NAV.map((item) => (
                <Link key={item.href} href={item.href} className={item.match(pathname) ? "active" : undefined}>
                  {item.label}
                </Link>
              ))
            : null}
        </nav>
        {config ? (
          <div className="topbar-fields">
            <label className="row gap-s">
              Your name
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Needed to approve" size={16} />
            </label>
            <button type="button" className="link" onClick={() => setToken("")}>
              Sign out
            </button>
          </div>
        ) : null}
      </header>
      {content}
    </>
  );
}
