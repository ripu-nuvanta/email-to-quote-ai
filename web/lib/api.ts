import type { Attachment } from "./types";

// Empty in the built app (same origin as FastAPI); set in .env.development for `npm run dev`.
const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public lines: number[] = [],
  ) {
    super(message);
  }
}

function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{ loc: [...], msg: "..." }]
    return detail
      .map((item) => {
        const entry = item as { loc?: unknown[]; msg?: string };
        const field = entry.loc?.slice(1).join(".");
        return field ? `${field}: ${entry.msg}` : (entry.msg ?? JSON.stringify(item));
      })
      .join("; ");
  }
  return JSON.stringify(detail);
}

export async function api<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  if (init.body) headers["Content-Type"] = "application/json";
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers: { ...headers, ...(init.headers as Record<string, string>) } });

  if (!response.ok) {
    let detail: unknown = response.statusText || `HTTP ${response.status}`;
    let lines: number[] = [];
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
      lines = body.lines ?? [];
    } catch {
      // not JSON
    }
    const message = describe(detail);
    throw new ApiError(lines.length ? `${message} (lines ${lines.join(", ")})` : message, response.status, lines);
  }
  if (response.status === 204) return undefined as T;
  const type = response.headers.get("content-type") ?? "";
  return (type.includes("application/json") ? response.json() : response.blob()) as Promise<T>;
}

export function send(body: unknown, method = "POST"): RequestInit {
  return { method, body: JSON.stringify(body) };
}

export const storage = {
  get(key: string): string {
    try {
      return window.localStorage.getItem(key) ?? "";
    } catch {
      return "";
    }
  },
  set(key: string, value: string) {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      // storage unavailable (private window): keep working without remembering
    }
  },
};

/** The mailbox shown in the email windows: saved in this browser, or derived from the company name. */
export function mailboxFor(companyName: string | undefined): string {
  const saved = storage.get("mailboxAddress");
  if (saved) return saved;
  const slug = (companyName ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "");
  return `quotes@${slug || "yourcompany"}.com`;
}

export function money(value: string | number, currency?: string): string {
  const amount = Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return currency ? `${currency} ${amount}` : amount;
}

export function qty(value: string | number): string {
  return String(Number(value));
}

export function dateTime(value: string): string {
  return new Date(value).toLocaleString();
}

export function fileToAttachment(file: File): Promise<Attachment> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = String(reader.result);
      resolve({
        filename: file.name,
        content_type: file.type || "application/octet-stream",
        content_base64: dataUrl.slice(dataUrl.indexOf(",") + 1),
      });
    };
    reader.onerror = () => reject(reader.error ?? new Error(`Could not read ${file.name}`));
    reader.readAsDataURL(file);
  });
}
