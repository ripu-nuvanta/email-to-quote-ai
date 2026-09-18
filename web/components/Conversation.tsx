"use client";

import { useState } from "react";
import { dateTime } from "@/lib/api";
import type { AttachmentReport, ConversationEntry, Quote } from "@/lib/types";
import { FileTypeIcon, ReplyIcon } from "./Icons";

const OUTCOMES: Record<string, [label: string, tone: string]> = {
  updated: ["Quote updated", "info"],
  revision_created: ["Revision created", "info"],
  no_changes: ["No product changes", ""],
};
const PREVIEW_LINES = 18;

export function attachmentStatus(attachment: AttachmentReport, aiReading: boolean): [string, string] {
  if (attachment.method === "text") return ["Read", "ok"];
  if (attachment.method === "ai") return aiReading ? ["Read by AI", "ok"] : ["Not read: needs OpenAI key", "warn"];
  if (attachment.method === "error") return ["Could not open", "bad"];
  return ["Not supported", "bad"];
}

function initials(entry: ConversationEntry): string {
  const source = entry.from_name?.trim() || entry.from_email.replace(/@.*/, "");
  const words = source.split(/[\s._-]+/).filter(Boolean);
  return ((words[0]?.[0] ?? "?") + (words[1]?.[0] ?? "")).toUpperCase();
}

function describeChange(change: Record<string, string | null>): string {
  switch (change.action) {
    case "removed":
      return `Removed ${change.item}`;
    case "added":
      return `Added ${change.quantity} × ${change.item}`;
    case "quantity_changed":
      return `${change.item}: quantity ${change.from} → ${change.to}`;
    case "replaced":
      return `Replaced ${change.from_item} with ${change.quantity} × ${change.item}`;
    default:
      return `Couldn't apply “${change.item}” (${(change.result ?? "unclear").replaceAll("_", " ")}), please check`;
  }
}

function Message({ entry, number, aiReading }: { entry: ConversationEntry; number: number; aiReading: boolean }) {
  const lines = entry.body.split("\n");
  const [expanded, setExpanded] = useState(lines.length <= PREVIEW_LINES);
  const [outcome, tone] = entry.outcome ? (OUTCOMES[entry.outcome] ?? [entry.outcome, ""]) : ["", ""];
  return (
    <article className="thread-entry">
      <header className="thread-head">
        <span className="avatar">{initials(entry)}</span>
        <div className="stack grow" style={{ gap: 0 }}>
          <strong>{entry.from_name || entry.from_email}</strong>
          <span className="sub">
            {entry.from_name ? `${entry.from_email} · ` : ""}
            {entry.received_at ? dateTime(entry.received_at) : ""}
          </span>
        </div>
        <span className={entry.kind === "request" ? "badge" : "badge info"}>{entry.kind === "request" ? "Original request" : `Follow-up ${number}`}</span>
      </header>
      <div className="thread-subject">{entry.subject || "(no subject)"}</div>
      <pre className="email thread-body">{(expanded ? entry.body : lines.slice(0, PREVIEW_LINES).join("\n")) || "(empty message)"}</pre>
      {!expanded ? (
        <button type="button" className="link thread-more" onClick={() => setExpanded(true)}>
          Show full email
        </button>
      ) : null}
      {entry.attachments.length ? (
        <div className="thread-files">
          <ul className="file-chips">
            {entry.attachments.map((attachment, index) => {
              const [label, labelTone] = attachmentStatus(attachment, aiReading);
              return (
                <li key={`${attachment.filename}-${index}`} className="file-chip" title={attachment.note ?? undefined}>
                  <FileTypeIcon name={attachment.filename} />
                  <span className="file-name">{attachment.filename}</span>
                  <span className={`badge ${labelTone}`}>{label}</span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
      {entry.kind === "follow_up" ? (
        <div className="ai-summary">
          <div className="row between">
            <strong>What the customer asked for</strong>
            {outcome ? (
              <span className={`badge ${tone}`}>
                {outcome}
                {entry.outcome === "revision_created" ? ` · ${entry.quote_number}` : ""}
              </span>
            ) : null}
          </div>
          <span>{entry.summary}</span>
          {entry.changes?.length ? (
            <ul>
              {entry.changes.map((change, index) => (
                <li key={index}>{describeChange(change)}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

interface Props {
  quote: Quote;
  aiReading: boolean;
  onFollowUp: () => void;
}

export function Conversation({ quote, aiReading, onFollowUp }: Props) {
  let followUpNumber = 0;
  return (
    <div className="card">
      <div className="row between">
        <div className="row gap-s">
          <h3 className="flush">Email conversation</h3>
          <span className="muted small">
            {quote.conversation.length} message{quote.conversation.length === 1 ? "" : "s"}
          </span>
        </div>
        {quote.status !== "ignored" ? (
          <button type="button" className="icon-button" onClick={onFollowUp}>
            <ReplyIcon /> Customer follow-up
          </button>
        ) : null}
      </div>
      <div className="thread spaced">
        {quote.conversation.map((entry, index) => {
          if (entry.kind === "follow_up") followUpNumber += 1;
          return <Message key={`${entry.kind}-${index}`} entry={entry} number={followUpNumber} aiReading={aiReading} />;
        })}
      </div>
    </div>
  );
}
