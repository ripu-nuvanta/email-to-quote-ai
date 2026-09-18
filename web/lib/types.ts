// Shapes returned by the FastAPI backend (see app/schemas.py). Decimals arrive as strings.
export type Decimal = string;

export interface AppConfig {
  company_name: string;
  currency: string;
  send_mode: "backend" | "n8n" | string;
  ai_reading: boolean;
  internal_email_domains: string[];
  dev_tools: boolean;
}

export interface Customer {
  id: number;
  name: string;
  company: string | null;
  email: string;
  tax_region: string | null;
  discount_pct: Decimal;
  payment_terms: string;
  is_verified: boolean;
}

export interface CustomerListItem extends Customer {
  billing_address: string | null;
  price_count: number;
}

export interface CustomerPrice {
  id: number;
  sku: string;
  product_name: string;
  unit: string;
  min_qty: number;
  unit_price: Decimal;
  list_price: Decimal;
  valid_from: string | null;
  valid_until: string | null;
  note: string | null;
}

export interface Product {
  sku: string;
  name: string;
  unit: string;
  base_price: Decimal;
  on_hand: number | null;
}

export interface QuoteLine {
  id: number;
  position: number;
  product_id: number | null;
  requested_text: string;
  requested_sku: string | null;
  sku: string | null;
  description: string;
  unit: string | null;
  quantity: Decimal;
  unit_price: Decimal;
  price_overridden: boolean;
  price_source: "customer_price" | "list_price" | "manual" | null;
  discount_pct: Decimal;
  line_total: Decimal;
  match_method: string | null;
  match_confidence: number | null;
  alternatives: { sku: string; name: string; score: number }[];
  stock_status: "in_stock" | "partial" | "backorder" | "unknown" | null;
  on_hand: number | null;
  lead_time_days: number | null;
  needs_review: boolean;
  review_reason: string | null;
}

export interface QuoteEvent {
  at: string;
  actor: string;
  event: string;
  detail: Record<string, unknown> | null;
}

export interface AttachmentReport {
  filename: string;
  content_type: string;
  size_bytes: number | null;
  method: "text" | "ai" | "unsupported" | "error";
  note: string | null;
}

export interface ConversationEntry {
  kind: "request" | "follow_up";
  from_email: string;
  from_name: string | null;
  subject: string;
  body: string;
  attachments: AttachmentReport[];
  received_at: string | null;
  summary: string | null;
  outcome: "updated" | "revision_created" | "no_changes" | null;
  changes: Record<string, string | null>[] | null;
  quote_number: string | null;
}

export interface QuoteSummary {
  id: number;
  number: string | null;
  status: string;
  source_from: string;
  source_subject: string;
  customer_name: string | null;
  currency: string;
  total: Decimal;
  needs_review_count: number;
  revision: number | null;
  reply_count: number;
  created_at: string;
}

export interface Quote extends QuoteSummary {
  source_message_id: string;
  source_body: string;
  conversation: ConversationEntry[];
  revision_of_id: number | null;
  revision_of_number: string | null;
  latest_revision_id: number | null;
  latest_revision_number: string | null;
  forwarded_by: string | null;
  attachments: AttachmentReport[] | null;
  customer: Customer | null;
  subtotal: Decimal;
  discount_total: Decimal;
  shipping_total: Decimal;
  shipping_manual: boolean;
  shipping_service: string | null;
  shipping_source: "rules" | "api" | "rules_fallback" | "manual" | null;
  shipping_transit_days: number | null;
  tax_rate: Decimal;
  tax_total: Decimal;
  valid_until: string | null;
  requested_delivery: string | null;
  shipping_address: string | null;
  customer_notes: string | null;
  internal_notes: string | null;
  cover_message: string | null;
  approved_by: string | null;
  approved_at: string | null;
  rejection_reason: string | null;
  sent_at: string | null;
  lines: QuoteLine[];
  events: QuoteEvent[];
}

export interface LineEdit {
  id?: number;
  remove?: boolean;
  product_sku?: string;
  description?: string;
  quantity?: string;
  unit_price?: string;
  discount_pct?: string;
}

export interface Attachment {
  filename: string;
  content_type: string;
  content_base64: string;
}

export interface IngestResult {
  quote_id: number;
  number: string | null;
  status: string;
  needs_review_count: number;
  approval_url: string;
}
