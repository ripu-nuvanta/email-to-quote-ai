"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, fileToAttachment, send } from "@/lib/api";
import type { EmailTemplate } from "@/lib/followups";
import type { IngestResult } from "@/lib/types";
import { FileTypeIcon, MailIcon, PaperclipIcon, ReplyIcon } from "./Icons";
import { useSession } from "./Session";

export type ComposerMode = "email" | "attachments" | "reply";

export interface ComposerDefaults {
  fromEmail: string;
  fromName: string;
  subject: string;
  body: string;
  inReplyTo?: string | null;
  templates?: EmailTemplate[];
}

interface Props {
  mode: ComposerMode;
  mailbox: string;
  defaults: ComposerDefaults;
  onClose: () => void;
  onDelivered: (result: IngestResult) => void;
}

const ACCEPTED = ".pdf,.xlsx,.xlsm,.xls,.docx,.doc,.csv,.txt,.html,.eml,.png,.jpg,.jpeg,.webp,.gif,.heic";
const TITLES: Record<ComposerMode, string> = {
  email: "New email",
  attachments: "New email with attachments",
  reply: "Customer follow-up",
};

function fileSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** A mail-client style window. Delivers the email through the same pipeline as incoming mail. */
export function EmailComposer({ mode, mailbox, defaults, onClose, onDelivered }: Props) {
  const { token } = useSession();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [fromEmail, setFromEmail] = useState(defaults.fromEmail);
  const [fromName, setFromName] = useState(defaults.fromName);
  const [subject, setSubject] = useState(defaults.subject);
  const [body, setBody] = useState(defaults.body);
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  function addFiles(list: FileList | File[]) {
    const incoming = Array.from(list);
    setFiles((current) => [...current, ...incoming.filter((file) => !current.some((f) => f.name === file.name && f.size === file.size))]);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const attachments = await Promise.all(files.map(fileToAttachment));
      const result = await api<IngestResult>(
        "/api/dev/simulate-email",
        token,
        send({
          from_email: fromEmail.trim(),
          from_name: fromName.trim() || null,
          subject,
          body_text: body,
          attachments,
          in_reply_to: defaults.inReplyTo ?? null,
        }),
      );
      onDelivered(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const canSend = Boolean(fromEmail.trim()) && (mode === "attachments" ? files.length > 0 : Boolean(body.trim()) || files.length > 0);
  const dropzone = (large: boolean) => (
    <div
      className={`dropzone${large ? "" : " compact"}${dragging ? " dragging" : ""}`}
      role="button"
      tabIndex={0}
      onClick={() => fileInput.current?.click()}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && fileInput.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        addFiles(e.dataTransfer.files);
      }}
    >
      <PaperclipIcon size={large ? 22 : 16} />
      <strong>{large ? "Drop files here or click to browse" : "Attach files"}</strong>
      {large ? <span className="sub">Excel, Word, PDF, scanned documents and photos</span> : null}
    </div>
  );

  return (
    <dialog
      ref={dialogRef}
      className="dialog mail-window"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <form onSubmit={submit}>
        <div className="mail-titlebar">
          <span className="row gap-s">
            {mode === "reply" ? <ReplyIcon /> : <MailIcon />}
            <strong>{TITLES[mode]}</strong>
          </span>
          <span className="connected-pill">
            <span className="status-dot" />
            {mailbox}
          </span>
        </div>

        <div className="mail-fields">
          <div className="mail-field">
            <span>From</span>
            <input aria-label="Sender name" placeholder="Name" value={fromName} onChange={(e) => setFromName(e.target.value)} />
            <input aria-label="Sender email" type="email" required placeholder="name@company.com" value={fromEmail} onChange={(e) => setFromEmail(e.target.value)} />
          </div>
          <div className="mail-field">
            <span>To</span>
            <span className="mail-static">{mailbox}</span>
          </div>
          <div className="mail-field">
            <span>Subject</span>
            <input aria-label="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
          </div>
        </div>

        {mode === "reply" && defaults.templates?.length ? (
          <div className="mail-section template-chips">
            <span className="muted small">Examples:</span>
            {defaults.templates.map((template) => (
              <button key={template.label} type="button" className="chip" onClick={() => setBody(template.body)}>
                {template.label}
              </button>
            ))}
          </div>
        ) : null}

        {mode === "attachments" ? dropzone(true) : null}

        <textarea
          className="mail-body"
          aria-label="Message"
          rows={mode === "attachments" ? 6 : 12}
          value={body}
          placeholder={mode === "reply" ? "Write the customer's reply, or pick an example above." : "Write the message…"}
          onChange={(e) => setBody(e.target.value)}
        />

        {files.length ? (
          <div className="mail-section">
            <ul className="file-chips">
              {files.map((file, index) => (
                <li key={`${file.name}-${index}`} className="file-chip">
                  <FileTypeIcon name={file.name} />
                  <span className="file-name">{file.name}</span>
                  <span className="sub">{fileSize(file.size)}</span>
                  <button type="button" className="link danger" aria-label={`Remove ${file.name}`} onClick={() => setFiles(files.filter((_, i) => i !== index))}>
                    ✕
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {error ? <p className="alert error mail-error">{error}</p> : null}

        <div className="mail-actions">
          {mode !== "attachments" ? dropzone(false) : null}
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            accept={ACCEPTED}
            onChange={(e) => {
              if (e.target.files) addFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <span className="grow" />
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary" disabled={busy || !canSend}>
            {busy ? "Delivering…" : mode === "reply" ? "Deliver reply" : "Deliver to inbox"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
