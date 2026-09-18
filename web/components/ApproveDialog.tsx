"use client";

import { useEffect, useRef, useState } from "react";
import { money } from "@/lib/api";
import type { Quote } from "@/lib/types";

export interface ApproveOptions {
  send: boolean;
  coverMessage: string;
  confirmFlagged: boolean;
}

interface Props {
  quote: Quote;
  sendMode: string;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onApprove: (options: ApproveOptions) => void;
}

export function ApproveDialog({ quote, sendMode, busy, error, onCancel, onApprove }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const [coverMessage, setCoverMessage] = useState("");
  const [confirmFlagged, setConfirmFlagged] = useState(false);
  const flagged = quote.lines.filter((line) => line.needs_review).length;
  const blocked = busy || (flagged > 0 && !confirmFlagged);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  return (
    <dialog
      ref={ref}
      className="dialog"
      onCancel={(event) => {
        event.preventDefault();
        onCancel();
      }}
    >
      <h2>Approve {quote.number}</h2>
      <p className="muted">
        {quote.lines.length} lines · {money(quote.total, quote.currency)} → {quote.customer?.email ?? "no customer"}
        {quote.forwarded_by ? ` (copy to ${quote.forwarded_by})` : ""}
      </p>
      {error ? <p className="alert error">{error}</p> : null}
      {flagged ? (
        <label className="check">
          <input type="checkbox" checked={confirmFlagged} onChange={(e) => setConfirmFlagged(e.target.checked)} />
          I have checked the {flagged} flagged line{flagged > 1 ? "s" : ""}
        </label>
      ) : null}
      <label className="field spaced">
        Cover email (leave empty for the standard message)
        <textarea rows={7} value={coverMessage} onChange={(e) => setCoverMessage(e.target.value)} placeholder="Hi …" />
      </label>
      <div className="row end gap spaced">
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" disabled={blocked} onClick={() => onApprove({ send: false, coverMessage, confirmFlagged })}>
          Approve only
        </button>
        <button type="button" className="primary" disabled={blocked} onClick={() => onApprove({ send: true, coverMessage, confirmFlagged })}>
          {sendMode === "n8n" ? "Approve & send via n8n" : "Approve & send"}
        </button>
      </div>
    </dialog>
  );
}
